"""Fire as a hazard for the shared router (agent/routing).

Where the fire is projected to be:
  * replay — the incident's spread simulation (arrival_hours.tif, 30 m). A model run from ignition that
    overpredicts area (see incident.json metrics), which errs toward closing roads;
  * live, or any snapshot — the sentinel's elliptical spread model (signals.py) from every active fire point,
    with each road point's nearest forecast weather.

Restrictions (thresholds in config.py; design defaults, not fitted):
  hard   perimeter   within 0.5 mi of a current fire perimeter      (a penalty in Escape)
         hotspot     within 2 mi of a satellite hot spot, last 24 h (a penalty in Escape)
         closure     a Caltrans full closure (live only)
  soft   downwind of an active fire (by gusts, fading at 15 km), inside a Red Flag Warning
  safe   for Escape: fire not projected for 12 h and 5 mi from any perimeter, hot spot or reported fire
"""
import json
from datetime import datetime, timezone

import numpy as np

from routing.engine import Hazard
from routing.geo import KM_PER_DEG_LAT, angle_diff, bearing_deg, km_per_deg_lon, local_km

from .config import (ACTIVE_WINDOW_H, ESCAPE_SAFE_KM, ESCAPE_SAFE_LEAD_H, INCIDENTS, LIVE, ROUTE_DOWNWIND_KM,
                     ROUTE_HOTSPOT_AGE_H, ROUTE_HOTSPOT_KM, ROUTE_PERIMETER_KM)
from .signals import directional_ros, effective_wind_mph, head_ros_kmh
from .snapshot import HISTORICAL_RED_FLAG, load_incident

MARGIN_ROUTE_H = 2.0            # H1 for A -> B
MARGIN_ESCAPE_H = 0.5           # H1 for Escape: leaving close to the front is still leaving
ARRIVAL_HORIZON_H = 12.0        # fire points farther than their fastest spread over (departure + this) can't matter
REGION = "socal"


# ------------------------------------------------------------------ spatial helpers

def _thin(pts, cell_km):
    """One point per grid cell (keeping the smallest value in extra columns, e.g. age)."""
    if len(pts) == 0:
        return pts
    lat0 = float(pts[:, 1].mean())
    key = np.column_stack([np.floor(pts[:, 0] * km_per_deg_lon(lat0) / cell_km),
                           np.floor(pts[:, 1] * KM_PER_DEG_LAT / cell_km)])
    order = np.lexsort([pts[:, 2]] if pts.shape[1] > 2 else [pts[:, 0]])
    _, first = np.unique(key[order], axis=0, return_index=True)
    return pts[order[first]]


def _pairs(lon, lat, fires, radius_km):
    """(sample, fire) index pairs within radius_km and the fire -> sample offset in km (spatial index)."""
    import shapely
    from shapely import STRtree
    if len(fires) == 0 or len(lon) == 0:
        z = np.zeros(0, int)
        return z, z, np.zeros(0), np.zeros(0)
    lon0, lat0 = float(np.mean(lon)), float(np.mean(lat))
    x, y = local_km(lon, lat, lon0, lat0)
    fx, fy = local_km(fires[:, 0], fires[:, 1], lon0, lat0)
    i, j = STRtree(shapely.points(fx, fy)).query(shapely.points(x, y), predicate="dwithin", distance=radius_km)
    return i, j, x[i] - fx[j], y[i] - fy[j]


def _ellipse_arrival(lon, lat, fires, wx, horizon_h):
    """Hours until the nearest active fire point reaches each location (sentinel ETA model)."""
    out = np.full(len(lon), np.inf)
    if len(lon) == 0 or len(fires) == 0:
        return out
    radius = float(np.max(head_ros_kmh(wx))) * (max(horizon_h, 0.0) + ARRIVAL_HORIZON_H)
    i, j, dx, dy = _pairs(lon, lat, _thin(fires, 0.2), min(radius, 40.0))
    if len(i) == 0:
        return out
    sub = {k: v[i] for k, v in wx.items()}
    theta = angle_diff(bearing_deg(dx, dy), (sub["wind_dir_deg"] + 180.0) % 360.0)
    ros = directional_ros(head_ros_kmh(sub), effective_wind_mph(sub), theta)
    np.minimum.at(out, i, np.hypot(dx, dy) / ros)
    return out


# ------------------------------------------------------------------ fire state

class _Fire:
    """What the conditions need, on one clock (hours from `origin`)."""

    def __init__(self, label, origin, depart_h, arrival, perimeters, hotspots, active, wind, red_flag,
                 red_flag_note=None, blocks=()):
        self.label, self.origin, self.depart_h = label, origin, depart_h
        self.arrival, self.perimeters, self.hotspots, self.active = arrival, perimeters, hotspots, active
        self.wind, self.red_flag, self.red_flag_note, self.blocks = wind, red_flag, red_flag_note, list(blocks)


def _uniform_wind(wx):
    def wind(lon, lat):
        return {k: np.full(len(lon), float(wx[k])) for k in ("wind_mph", "gust_mph", "wind_dir_deg",
                                                              "fuel_moisture_1h_pct")}
    return wind


def replay_fire(key, depart_h):
    """A past incident `depart_h` hours after ignition, using its spread simulation."""
    import rasterio
    from rasterio.warp import transform
    from shapely.geometry import shape

    inc, ts, xy, hrs = load_incident(key)
    d = INCIDENTS / key
    src = rasterio.open(d / "arrival_hours.tif")
    arr = src.read(1)

    def arrival(lon, lat, wx=None):
        xs, ys = transform("EPSG:4326", src.crs, lon, lat)
        rows, cols = rasterio.transform.rowcol(src.transform, xs, ys)
        rows, cols = np.asarray(rows), np.asarray(cols)
        ok = (rows >= 0) & (rows < arr.shape[0]) & (cols >= 0) & (cols < arr.shape[1])
        out = np.full(len(lon), np.inf)
        vals = arr[rows[ok], cols[ok]]
        out[ok] = np.where(np.isfinite(vals), vals, np.inf)
        return out

    perims = json.loads((d / "perimeters.geojson").read_text())["features"]
    burning = [f for f in perims if f["properties"]["hour"] <= depart_h]
    seen = hrs <= depart_h
    hot = np.column_stack([xy[seen], depart_h - hrs[seen]])
    hot = hot[hot[:, 2] <= ROUTE_HOTSPOT_AGE_H]
    act = hot[hot[:, 2] <= ACTIVE_WINDOW_H, :2]
    active = np.vstack([[[inc["ignition"]["lon"], inc["ignition"]["lat"]]], act])
    wx = ts[int(min(max(depart_h, 0), len(ts) - 1))]
    # Each replayed fire burned under a Red Flag Warning issued before ignition (recorded in incident.json).
    return _Fire(f"replay:{key}", datetime.fromisoformat(inc["start"]), depart_h, arrival,
                 [shape(burning[-1]["geometry"])] if burning else [], hot, active, _uniform_wind(wx),
                 lambda lon, lat: np.ones(len(lon), bool), inc.get("red_flag") or HISTORICAL_RED_FLAG)


def live_fire(depart_h, look_ahead_h=0.0, snap=None, blocks=()):
    """Current feeds (`python -m wildfire live`), clock starting now."""
    from shapely import STRtree, points
    from shapely.geometry import shape

    from .snapshot import live_snapshot

    snap = snap or live_snapshot()
    now = datetime.fromisoformat(snap.as_of) if isinstance(snap.as_of, str) else datetime.now(timezone.utc)
    f = snap.fires
    is_active = f.age_h <= ACTIVE_WINDOW_H
    active = np.column_stack([f.lon[is_active], f.lat[is_active]]) if len(f) else np.zeros((0, 2))
    sat = np.array([k == "satellite" for k in f.kind], bool) if len(f) else np.zeros(0, bool)
    hot = np.column_stack([f.lon[sat], f.lat[sat], f.age_h[sat]]) if sat.any() else np.zeros((0, 3))
    hot = hot[hot[:, 2] <= ROUTE_HOTSPOT_AGE_H]

    def _read(name, default):
        p = LIVE / name
        return json.loads(p.read_text()) if p.exists() else default

    perimeters = [shape(x["geometry"]) for x in _read("perimeters.geojson", {"features": []})["features"]
                  if x.get("geometry")]
    grid = _read("weather.json", [])
    glat = np.array([g["lat"] for g in grid]); glon = np.array([g["lon"] for g in grid])
    gwx = {k: np.array([g[k] for g in grid], float) for k in ("wind_mph", "gust_mph", "wind_dir_deg",
                                                             "fuel_moisture_1h_pct")}

    def wind(lon, lat):
        lon, lat = np.asarray(lon), np.asarray(lat)
        idx = np.empty(len(lon), int)
        for s in range(0, len(lon), 20000):
            dl = (glat[None, :] - lat[s:s + 20000, None]) ** 2
            dn = ((glon[None, :] - lon[s:s + 20000, None]) * np.cos(np.radians(lat[s:s + 20000, None]))) ** 2
            idx[s:s + 20000] = np.argmin(dl + dn, axis=1)
        return {k: a[idx] for k, a in gwx.items()}

    flags = [shape(x["geometry"]) for x in _read("red_flags.geojson", {"features": []})["features"]
             if x.get("geometry")]
    tree = STRtree(flags) if flags else None

    def red_flag(lon, lat):
        out = np.zeros(len(lon), bool)
        if tree is not None:
            out[tree.query(points(lon, lat), predicate="intersects")[0]] = True
        return out

    def arrival(lon, lat, wx):
        return _ellipse_arrival(np.asarray(lon), np.asarray(lat), active, wx, depart_h + look_ahead_h)

    return _Fire("live", now, depart_h, arrival, perimeters, hot, active, wind, red_flag, blocks=blocks)


def points_fire(snap, look_ahead_h=0.0, blocks=()):
    """Any other snapshot (e.g. tests): fire points and the snapshot's weather, no perimeters."""
    f = snap.fires
    now = datetime.fromisoformat(snap.as_of)
    is_active = f.age_h <= ACTIVE_WINDOW_H
    active = np.column_stack([f.lon[is_active], f.lat[is_active]]) if len(f) else np.zeros((0, 2))
    sat = np.array([k == "satellite" for k in f.kind], bool) if len(f) else np.zeros(0, bool)
    hot = np.column_stack([f.lon[sat], f.lat[sat], f.age_h[sat]]) if sat.any() else np.zeros((0, 3))
    hot = hot[hot[:, 2] <= ROUTE_HOTSPOT_AGE_H]
    c = (float(np.mean(f.lat)), float(np.mean(f.lon))) if len(f) else (0.0, 0.0)
    wind = _uniform_wind(snap.weather(*c))

    def arrival(lon, lat, wx):
        return _ellipse_arrival(np.asarray(lon), np.asarray(lat), active, wx, look_ahead_h)

    flag = snap.red_flag(*c)
    return _Fire(snap.source, now, 0.0, arrival, [], hot, active, wind,
                 lambda lon, lat: np.full(len(lon), bool(flag)), flag, blocks=blocks)


# ------------------------------------------------------------------ conditions

def _conditions(fire):
    def conditions(G, mask, want_safe):
        import shapely
        from shapely import STRtree

        n = len(G)
        sel = mask[G.pt_edge]
        lon, lat = G.pts[sel, 0], G.pts[sel, 1]
        eidx = G.pt_edge[sel]
        lon0, lat0 = float(lon.mean()), float(lat.mean())
        pts = shapely.points(*local_km(lon, lat, lon0, lat0))

        def per_edge_min(vals):
            out = np.full(n, np.inf)
            np.minimum.at(out, eidx, vals)
            return out

        def per_edge_any(m):
            return np.bincount(eidx[m], minlength=n) > 0

        def tree_of_points(p, cell):
            if not len(p):
                return None, p
            p = _thin(p, cell)
            return STRtree(shapely.points(*local_km(p[:, 0], p[:, 1], lon0, lat0))), p

        wx = fire.wind(lon, lat)
        arrival = per_edge_min(fire.arrival(lon, lat, wx))

        near_perim = np.zeros(len(lon), bool)
        perim_tree = None
        if fire.perimeters:
            geoms = [shapely.transform(g, lambda c: np.column_stack(local_km(c[:, 0], c[:, 1], lon0, lat0)))
                     for g in fire.perimeters]
            perim_tree = STRtree(geoms)
            near_perim[perim_tree.query(pts, predicate="dwithin", distance=ROUTE_PERIMETER_KM)[0]] = True

        cell = 0.4
        hot_age = np.full(len(lon), np.inf)
        hot_tree, hot = tree_of_points(fire.hotspots, cell)
        if hot_tree is not None:
            # Widen by half a cell diagonal so thinning can only make H3 stricter.
            hit = hot_tree.query(pts, predicate="dwithin", distance=ROUTE_HOTSPOT_KM + 0.71 * cell)
            np.minimum.at(hot_age, hit[0], hot[hit[1], 2])

        closed = np.zeros(len(lon), bool)
        if fire.blocks:
            from routing.config import CLOSURE_BLOCK_KM
            b = np.array(fire.blocks, float)            # (lat, lon) points along closed stretches
            bt = STRtree(shapely.points(*local_km(b[:, 1], b[:, 0], lon0, lat0)))
            closed[bt.query(pts, predicate="dwithin", distance=CLOSURE_BLOCK_KM)[0]] = True

        down = np.zeros(len(lon))
        i, j, dx, dy = _pairs(lon, lat, _thin(fire.active, 0.4), ROUTE_DOWNWIND_KM)
        if len(i):
            theta = angle_diff(bearing_deg(dx, dy), (wx["wind_dir_deg"][i] + 180.0) % 360.0)
            expo = np.where(theta <= 45, 1.0 - np.hypot(dx, dy) / ROUTE_DOWNWIND_KM, 0.0) \
                * np.minimum(1.0, wx["gust_mph"][i] / 50.0)
            np.maximum.at(down, i, expo)
        down_e = np.zeros(n)
        np.maximum.at(down_e, eidx, down)
        red = per_edge_any(fire.red_flag(lon, lat))

        out = {"arrival": arrival,
               "hard": {"perimeter": per_edge_any(near_perim), "hotspot": np.isfinite(per_edge_min(hot_age)),
                        "closure": per_edge_any(closed)},
               "hotspot_age": per_edge_min(hot_age),
               "penalty": 0.5 * down_e + np.where(red, 0.3, 0.0),
               "exposure": down_e, "exposure_text": "Downwind of an active fire on {roads}: expect smoke and embers."}
        if want_safe:
            # Safe ground is 5 mi from any perimeter, hot spot or reported fire (early on a fire is only a report).
            near = np.zeros(len(lon), bool)
            act_tree, _ = tree_of_points(fire.active, cell)
            for tree, pad in ((perim_tree, 0.0), (hot_tree, 0.71 * cell), (act_tree, 0.71 * cell)):
                if tree is not None:
                    near[tree.query(pts, predicate="dwithin", distance=ESCAPE_SAFE_KM + pad)[0]] = True
            out["safe"] = ~per_edge_any(near)
        return out
    return conditions


REASONS = {
    "perimeter": lambda cond, e: "within 0.5 mi of the fire perimeter",
    "hotspot": lambda cond, e: f"within 2 mi of a satellite hot spot seen {cond['hotspot_age'][e]:.0f} h before departure",
    "closure": lambda cond, e: "fully closed (Caltrans)",
}


def _hazard(fire):
    notes = ["Free-flow speeds, slowed by modelled evacuation traffic near the fire; no live traffic feed.",
             "Always follow official evacuation orders and road closures."]
    if fire.red_flag_note:
        notes.append(f"Red Flag Warning in force: {fire.red_flag_note}.")
    return Hazard(kind="fire", label=fire.label, origin=fire.origin, depart_h=fire.depart_h,
                  conditions=_conditions(fire), reasons=REASONS, region=REGION,
                  arrival_phrase=lambda iso: f"fire projected at {iso}",
                  escape_soft=("perimeter", "hotspot"), safe_lead_h=ESCAPE_SAFE_LEAD_H,
                  safe_text="fire not projected there for 12 h, and 5 mi from any perimeter, hot spot or reported fire",
                  notes=notes)


def build(mode, depart_h, look_ahead_h=0.0, blocks=None):
    """For the map and MCP tools: mode "live" (depart_h = hours from now) or "replay:<incident>"."""
    if mode == "live":
        if blocks is None:
            from routing.closures import active_closures, block_points
            blocks = block_points(active_closures())
        return _hazard(live_fire(depart_h, look_ahead_h, blocks=blocks))
    if mode.startswith("replay:"):
        return _hazard(replay_fire(mode.split(":", 1)[1], depart_h))
    raise ValueError('mode must be "live" or "replay:<incident>" (e.g. "replay:eaton_2025")')


def from_snapshot(snap, look_ahead_h=0.0, blocks=()):
    """For the alert loop and the text demo: whatever the sentinel is looking at right now."""
    if snap.source.startswith("replay:"):
        return _hazard(replay_fire(snap.source.split(":", 1)[1], float(snap.meta.get("hour", 0))))
    # Snapshot's default source is "live"; only live_snapshot() records the feed time, and only then do the
    # live weather grid, perimeters and Red Flag polygons on disk describe what this snapshot saw.
    if snap.source == "live" and "feeds_updated" in snap.meta:
        return _hazard(live_fire(0.0, look_ahead_h, snap=snap, blocks=blocks))
    return _hazard(points_fire(snap, look_ahead_h, blocks))
