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
    the reasons, and the signals (distance, direction, downwind, hours to arrival, wind, Red Flag)."""
    sub = store.get(subscription_id)
    if sub is None:
        return {"error": "unknown subscription_id; call subscribe first"}
    new = store.unread_alerts(subscription_id)
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "label": sub.get("label"),
            "current_level": store.get(subscription_id)["last_level"], "new_alerts": new,
            "act_now": any(a["level"] in ("warn", "evacuate") for a in new)}


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
