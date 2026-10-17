"""MCP server: how personal agents connect to the sentinel.

A personal agent knows its person (who lives there, pets, medical needs, car,
work schedule). The sentinel knows the fire. Over MCP the personal agent pulls
the threat for the places that matter to its person, subscribes a webhook so
the sentinel pushes escalations, and turns them into a plan for that household.
The sentinel never receives the life context.

  python -m sentinel mcp                 # stdio (Claude Desktop / Claude Code)
  python -m sentinel mcp --http 8765     # streamable HTTP at /mcp
"""
import os
from functools import lru_cache
from time import monotonic

from mcp.server.mcpserver import MCPServer

from . import store
from .live import scan_region
from .policy import RulePolicy
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
        "Subscribe a webhook to be pushed escalations. Always defer to official evacuation orders."
    ),
)

_cache = {"t": -1e9, "snap": None}


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
              label: str | None = None, min_level: str = "watch") -> dict:
    """Have the sentinel push alerts for a location. webhook_url receives a signed JSON POST on every level
    change (header X-Sentinel-Signature: sha256=HMAC of the body); phone receives SMS. Returns the
    subscription id. Only the location and delivery address are stored."""
    sub = store.subscribe(lat, lon, label, webhook_url, phone, min_level)
    return {"subscription_id": sub["id"], "current": _view(assess(_snap(), lat, lon))}


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
    known then. incident: "eaton_2025" or "palisades_2025". Useful for drills and demos."""
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


def serve(http_port=None):
    if http_port:
        mcp.run("streamable-http", host=os.environ.get("SENTINEL_MCP_HOST", "127.0.0.1"), port=http_port)
    else:
        mcp.run("stdio")
