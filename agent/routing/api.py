"""Entry points used by the web map, the MCP tools, the CLI and the texting loop.

Hazard plug-ins live with their agents and are loaded by name, so this package never imports either
agent at module load:
  "fire"   -> sentinel.route_hazard        (LA + Ventura road graph)
  "surge"  -> surge_sentinel.route_hazard  (a road graph per storm area)

A plug-in module provides:
  build(mode, depart_h, look_ahead_h=0, **kw) -> Hazard     the map / MCP / CLI: mode names
  from_snapshot(snap, look_ahead_h=0, **kw)   -> Hazard     the alert loop: whatever the agent sees now
  graph_for(hazard)                           -> RoadGraph  (optional; default: the hazard's region)
  MARGIN_ROUTE_H, MARGIN_ESCAPE_H
"""
import importlib

from . import config, engine, graph

PLUGINS = {"fire": "sentinel.route_hazard", "surge": "surge_sentinel.route_hazard"}


def plugin(hazard):
    if hazard not in PLUGINS:
        raise ValueError(f"hazard must be one of {sorted(PLUGINS)}")
    return importlib.import_module(PLUGINS[hazard])


def _graph(p, hz, lat, lon, local_streets):
    g = p.graph_for(hz) if hasattr(p, "graph_for") else graph.region(hz.region)
    if not local_streets or not getattr(p, "LOCAL_STREETS", True):
        return g
    merged = graph.with_local_streets(g, lat, lon)
    if merged is g:
        hz.notes.insert(0, "Streets near the start are still loading; this route starts from the nearest "
                           "major road. Ask again in a minute for street-level directions.")
    return merged


def _check_risk(risk):
    if risk not in config.RISK:
        raise ValueError(f"risk must be one of {sorted(config.RISK)}")


def safe_route(from_lat, from_lon, to_lat, to_lon, hazard="fire", mode="live", depart_hour=0.0,
               margin_hours=None, risk="balanced", local_streets=True):
    """One hazard-safe driving path from A to B, or status "no_safe_path"."""
    _check_risk(risk)
    p = plugin(hazard)
    hz = p.build(mode, float(depart_hour), at=(from_lat, from_lon)) if _takes_at(p) else p.build(mode, float(depart_hour))
    g = _graph(p, hz, from_lat, from_lon, local_streets)
    margin = p.MARGIN_ROUTE_H if margin_hours is None else float(margin_hours)
    return engine.plan(g, hz, (from_lat, from_lon), (to_lat, to_lon), margin, risk)


def escape_route(lat, lon, hazard="fire", mode="live", depart_hour=0.0, margin_hours=None, risk="balanced",
                 local_streets=True):
    """Fastest route out of danger from one point, whether it gets out in time, and the latest departure."""
    _check_risk(risk)
    p = plugin(hazard)
    kw = {"at": (lat, lon)} if _takes_at(p) else {}
    hz = p.build(mode, float(depart_hour), look_ahead_h=config.LEAVE_BY_WINDOW_H, **kw)
    g = _graph(p, hz, lat, lon, local_streets)
    margin = p.MARGIN_ESCAPE_H if margin_hours is None else float(margin_hours)
    return engine.escape(g, hz, (lat, lon), margin, risk)


def subscriber_route(snap, lat, lon, now_iso=None, hazard="fire", blocks=(), graph_override=None,
                     local_streets=True, jam=None):
    """The escape route the texting layer stores on a subscriber, from what the agent sees right now.
    None if there is no way out (or no road data); the texts then fall back to plain advice."""
    p = plugin(hazard)
    hz = p.from_snapshot(snap, look_ahead_h=config.LEAVE_BY_WINDOW_H, blocks=blocks)
    try:
        g = graph_override if graph_override is not None else _graph(p, hz, lat, lon, local_streets)
    except FileNotFoundError:
        return None
    if g is None or len(g) == 0:
        return None
    r = engine.escape(g, hz, (lat, lon), p.MARGIN_ESCAPE_H, "balanced", jam=jam)
    return engine.summary_for_texts(r, now_iso) if r.get("status") == "ok" else None


def _takes_at(p):
    import inspect
    return "at" in inspect.signature(p.build).parameters
