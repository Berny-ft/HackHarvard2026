"""MCP server: how personal agents connect to the sentinel.

A personal agent knows its person (who lives there, pets, medical needs, car,
work schedule). The sentinel knows the fire. Over MCP the personal agent pulls
the threat for the places that matter to its person, subscribes them, and
turns alerts into a plan for that household. The sentinel never receives the
life context.

Subscriptions are delivered two ways: pushed (webhook, email, SMS) or polled
(the agent calls check_alerts on a schedule). Agents that cannot receive
inbound calls, like Meta's Muse, use polling.

  python -m sentinel mcp                                  # stdio (Claude Desktop / Claude Code)
  python -m sentinel mcp --http 8765 --loop               # HTTP at /mcp + the 15-min alert loop
  SENTINEL_MCP_TOKEN=... python -m sentinel mcp --http 8765 --loop   # require Authorization: Bearer
"""
import hmac
import os
import threading
from datetime import datetime, timezone
from functools import lru_cache
from time import monotonic

from mcp.server.mcpserver import MCPServer

from . import store
from .live import evaluate_subscriber, scan_region
from .policy import RulePolicy, get_policy
from .signals import assess
from .snapshot import live_snapshot, replay_snapshot

mcp = MCPServer(
    name="firetrace-sentinel",
    instructions=(
        "Wildfire early-warning for Los Angeles and Ventura counties, built on live satellite hot spots, "
        "NIFC incidents and perimeters, NWS Red Flag Warnings and hourly fire weather. Use get_threat for any "
        "location your user cares about (home, work, school, a relative's house). Levels: none < watch < warn "
        "< evacuate. Combine the threat with what you know about your user — mobility, children, pets, "
        "vehicles, medical devices — to tailor the advice; this server does not want that information. "
        "To watch a place continuously: call subscribe once (no delivery address needed), then call "
        "check_alerts with the subscription_id every 15 minutes. When it returns new alerts, tell your user "
        "right away if the level is warn or evacuate. Always defer to official evacuation orders."
    ),
)

_cache = {"t": -1e9, "snap": None}
POLICY = os.environ.get("SENTINEL_POLICY", "rules")


def _snap():
    """Live snapshot, rebuilt at most once a minute."""
    if monotonic() - _cache["t"] > 60:
        _cache.update(t=monotonic(), snap=live_snapshot())
    return _cache["snap"]


def _view(s):
    d = RulePolicy().decide(s, [])
    return {"level": d["level"], "summary": d["message"],
            "signals": {k: s[k] for k in ("as_of", "nearest_fire_km", "nearest_fire_name", "nearest_fire_confirmed",
                                          "fire_bearing_deg", "downwind", "eta_h", "head_ros_kmh", "red_flag",
                                          "burn_probability_10y", "weather_now", "outlook_12h")}}


@mcp.tool()
def get_threat(lat: float, lon: float) -> dict:
    """Current wildfire threat at a location: level, plain-language summary, and the signals behind it
    (distance/direction to the nearest fire, downwind or not, modelled hours to arrival, wind, fuel moisture,
    Red Flag Warning, 10-year burn probability)."""
    return _view(assess(_snap(), lat, lon))


@mcp.tool()
def regional_overview() -> dict:
    """What the sentinel sees across LA + Ventura right now: feed freshness, fire counts by source, active
    Red Flag Warnings, and the most threatened areas."""
    s = _snap()
    kinds = {k: s.fires.kind.count(k) for k in set(s.fires.kind)}
    return {"as_of": s.as_of, "feeds_updated": s.meta.get("feeds_updated"), "fire_points": kinds,
            "named_incidents": sorted({n for n in s.fires.name if n})[:30],
            "most_threatened_areas": scan_region(s)}


@mcp.tool()
def subscribe(lat: float, lon: float, webhook_url: str | None = None, phone: str | None = None,
              label: str | None = None, min_level: str = "watch", consent: bool = False,
              email: str | None = None) -> dict:
    """Watch a location (home, work, school) continuously. The sentinel re-checks it every 15 minutes and records
    an alert whenever the level changes (none < watch < warn < evacuate, from min_level up). Collect alerts by
    calling check_alerts(subscription_id) on a schedule; optionally also push them: webhook_url receives a signed
    JSON POST on every level change (header X-Sentinel-Signature: sha256=HMAC of the body), email receives the
    alert, and phone receives texts (with evacuation windows and routes) only if consent is true. Returns the
    subscription id and, if the place is already under threat, the first alert. Only the location and delivery
    addresses are stored."""
    sub = store.subscribe(lat, lon, label, webhook_url, phone, min_level, consent, email)
    first = evaluate_subscriber(sub, _snap(), get_policy(POLICY))
    return {"subscription_id": sub["id"], "check_every_minutes": 15,
            "first_alert": first, "current": _view(assess(_snap(), lat, lon))}


@mcp.tool()
def check_alerts(subscription_id: str) -> dict:
    """New alerts for a subscription since the last check (empty list = nothing changed), plus the current
    level. Call this on a schedule, e.g. every 15 minutes. Each alert has the level, a plain-language message,
    the reasons, and the signals (distance, direction, downwind, hours to arrival, wind, Red Flag).
    At warn or evacuate it also returns way_out: the fastest route to safe ground (turn-by-turn directions,
    minutes, latest safe departure, a map page and a Google Maps link that follows the route). Read the first
    steps to your user and send them the map; tell them to follow officers' directions over any route."""
    sub = store.get(subscription_id)
    if sub is None:
        return {"error": "unknown subscription_id; call subscribe first"}
    new = store.unread_alerts(subscription_id)
    sub = store.get(subscription_id)
    out = {"checked_at": datetime.now(timezone.utc).isoformat(), "label": sub.get("label"),
           "current_level": sub["last_level"], "new_alerts": new,
           "act_now": any(a["level"] in ("warn", "evacuate") for a in new)}
    if sub["last_level"] in ("warn", "evacuate") and sub.get("route"):
        from .messages import way_out
        out["way_out"] = way_out(sub)
    return out


@mcp.tool()
def unsubscribe(subscription_id: str) -> dict:
    """Stop alerts for a subscription."""
    return {"removed": store.unsubscribe(subscription_id)}


@mcp.tool()
def recent_alerts(subscription_id: str | None = None, limit: int = 10) -> list:
    """Alerts the sentinel has sent, newest last (optionally for one subscription), with the reasons and
    signals behind each one."""
    return store.recent_alerts(subscription_id, limit)


@mcp.tool()
def replay_threat(incident: str, hour: int, lat: float, lon: float) -> dict:
    """What the sentinel would have said at a location `hour` hours into a past fire, using only what was
    known then. incident: "eaton_2025", "palisades_2025", "woolsey_2018", "mountain_2024" or "franklin_2024".
    Useful for drills and demos."""
    return _view(assess(_replay(incident, hour), lat, lon))


@lru_cache(maxsize=64)
def _replay(incident, hour):
    return replay_snapshot(incident, hour)


def _for_agent(r):
    for k in ("closed_at_departure", "blocking", "path"):   # map geometry; too bulky for an agent's context
        r.pop(k, None)
    return r


@mcp.tool()
def safe_route(from_lat: float, from_lon: float, to_lat: float, to_lon: float, hazard: str = "fire",
               mode: str = "live", depart_hour: float = 0.0, margin_hours: float | None = None,
               risk: str = "balanced") -> dict:
    """One hazard-safe driving route from A to B, or status "no_safe_path" when none exists, with turn-by-turn
    directions, the roads avoided and why, and the smallest lead over the hazard along the way.
    hazard "fire": a road closes if the fire is projected to reach it less than margin_hours (default 2) after
    you'd pass, within 0.5 mi of a perimeter, within 2 mi of a satellite hot spot from the last 24 h, or under a
    Caltrans full closure. mode "live" (depart_hour = hours from now) or "replay:<fire>" (hours after ignition):
    eaton_2025, palisades_2025, woolsey_2018, mountain_2024, franklin_2024.
    hazard "surge": flooded roads, bridges in tropical-storm winds and flooded tunnels close; mode
    "replay:<storm>" (sandy_2012, matthew_2016, irma_2017, isabel_2003) or "live".
    risk ("cautious" | "balanced" | "fastest") weights the soft penalties."""
    from routing import safe_route as route
    return _for_agent(route(from_lat, from_lon, to_lat, to_lon, hazard, mode, depart_hour, margin_hours, risk))


@mcp.tool()
def escape_route(lat: float, lon: float, hazard: str = "fire", mode: str = "live", depart_hour: float = 0.0,
                 margin_hours: float | None = None, risk: str = "balanced") -> dict:
    """Can someone at this location get out in time? The fastest route to safe ground, how long it takes,
    turn-by-turn directions, the smallest lead over the hazard, and leave_by_time: the latest departure that
    still gets out. status: "ok" (tight=true if the lead is under 1 h) | "no_escape" | "already_safe".
    Only the hazard's arrival (and hard closures like Caltrans or flooded roads) close roads here; passing near
    a fire perimeter or hot spot is penalised, not forbidden. hazard/mode/depart_hour as in safe_route."""
    from routing import escape_route as escape
    return _for_agent(escape(lat, lon, hazard, mode, depart_hour, margin_hours, risk))


@mcp.prompt()
def household_plan(location: str, household: str) -> str:
    """Prompt for a personal agent: turn the sentinel's threat into a plan for one household."""
    return (f"Call get_threat for {location}. Then write a short, ordered plan for this household: {household}. "
            "Account for who needs help moving, pets, medications and devices, vehicles and fuel, and which "
            "route leads away from the fire (opposite its bearing, not downwind). Say when to leave, not just "
            "what to pack. If the level is warn or evacuate, lead with the single next action.")


def _require_bearer(app, token):
    """Pure-ASGI guard (keeps streaming intact): every HTTP request needs Authorization: Bearer <token>."""
    want = f"Bearer {token}".encode()

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            got = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(got, want):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b"missing or wrong bearer token"})
                return
        await app(scope, receive, send)

    return guarded


def serve(http_port=None, loop=False, every_min=15):
    if loop:
        from .live import run_forever
        threading.Thread(target=run_forever, kwargs={"every_min": every_min, "policy_name": POLICY},
                         daemon=True).start()
    if not http_port:
        mcp.run("stdio")
        return
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    # Served behind a tunnel or proxy, so the Host header is the public name; auth is the bearer token.
    app = mcp.streamable_http_app(host="0.0.0.0", transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False))
    token = os.environ.get("SENTINEL_MCP_TOKEN")
    if token:
        app = _require_bearer(app, token)
    else:
        print("WARNING: SENTINEL_MCP_TOKEN not set; the MCP endpoint is open to anyone who has the URL")
    uvicorn.run(app, host=os.environ.get("SENTINEL_MCP_HOST", "127.0.0.1"), port=http_port, log_level="warning")
