"""Storm surge as a hazard for the shared router (agent/routing).

What the router sees at replay hour h is what the surge sentinel sees (world.py): the latest NHC advisory
issued by then and the tide gauges observed so far, turned into each gauge's 48 h water-level forecast. Never
the observed flood, which is what the agent is graded against.

Per road point: water level = inverse-distance blend of the gauge forecasts; water reaches the point once
the level passes its spill level and stands at least 0.5 ft deep (SurgeTrace's flood test). Expected
scenario closes roads; the high scenario only warns.

Restrictions (design values, approved for the demo; check against official guidance before relying on them):
  arrival  water expected on the road less than the margin after you'd pass (2 h A -> B, 1 h Escape)
  flood    already 0.5 ft deep at departure
  bridge   tropical-storm-force wind (39 mph sustained) forecast on the bridge, from then on
  tunnel   flooding forecast at the tunnel in the next 48 h
  soft     the high-water scenario floods the road
  safe     ground 2 ft above the high scenario's peak, outside the evacuation zones ordered for the forecast
           category, and 3 mi from the forecast flooding
Leave-by is also capped by tropical-storm-force winds reaching your location: roads aren't safe after that.
"""
from datetime import datetime, timezone

import numpy as np

from routing import graph as road_graph
from routing.config import SURGE_ROADS
from routing.engine import Hazard
from routing.geo import local_km

from .config import FLOOD_DEPTH_FT, ZONE_SPILL_FT, ZONES_FOR_CATEGORY
from .world import _merc, category, levels, load_storm, state_at, terrain, world

MARGIN_ROUTE_H = 2.0
MARGIN_ESCAPE_H = 1.0
LOCAL_STREETS = False              # storm-area graphs already include residential streets
TS_WIND_MS = 39 * 0.44704          # 39 mph sustained: tropical-storm force
SAFE_PAD_FT = 2.0
SAFE_FLOOD_KM = 4.83               # 3 mi
SAFE_LEAD_H = 12.0


def _texel(info, lon, lat):
    (lon0, lat1), (lon1, _), (_, lat0) = info["terrain"]["corners"][:3]
    x0, y1 = _merc(lon0, lat1)
    x1, y0 = _merc(lon1, lat0)
    x, y = _merc(np.asarray(lon), np.asarray(lat))
    W, H = info["terrain"]["width"], info["terrain"]["height"]
    return (x - x0) / (x1 - x0) * W, (y1 - y) / (y1 - y0) * H


def _ffill(a):
    a = np.array(a, float)
    for i in range(1, len(a)):
        if not np.isfinite(a[i]):
            a[i] = a[i - 1]
    return np.where(np.isfinite(a), a, np.nanmin(a) if np.isfinite(a).any() else 0.0)


def _wind_speed(s, lat, lon):
    """Sustained wind (m/s) at many points: world.wind_at vectorised (Holland profile + half the storm motion)."""
    kx = 111.32 * np.cos(np.radians((s["lat"] + lat) / 2))
    dx, dy = (lon - s["lon"]) * kx, (lat - s["lat"]) * 110.57
    r = np.maximum(np.hypot(dx, dy), 1.0)
    x = (s["rmw_nm"] * 1.852 / r) ** 1.3
    vmax = s["vmax_kt"] * 0.514444
    v = vmax * np.sqrt(x * np.exp(1 - x))
    toward = np.radians((np.degrees(np.arctan2(dx, dy)) - 90.0 - 20.0) % 360)
    k = 0.5 * v / max(vmax, 1e-6)
    return np.hypot(v * np.sin(toward) + k * s["motion"][0] / 3.6, v * np.cos(toward) + k * s["motion"][1] / 3.6)


def _inside_r34(s, lat, lon, r34_nm):
    """Within the 34-kt wind radius of the storm centre, by quadrant (NE, SE, SW, NW)."""
    kx = 111.32 * np.cos(np.radians((s["lat"] + lat) / 2))
    dx, dy = (lon - s["lon"]) * kx, (lat - s["lat"]) * 110.57
    quad = (((np.degrees(np.arctan2(dx, dy)) + 360.0) % 360.0) // 90).astype(int)    # 0 NE, 1 SE, 2 SW, 3 NW
    return np.hypot(dx, dy) <= np.asarray(r34_nm, float)[quad] * 1.852


def _wind_arrivals(track, h0, lat, lon, horizon=48, r34_nm=None):
    """First forecast hour (replay clock) with tropical-storm-force wind at each point; inf if none.
    With NHC 34-kt radii: inside the radius. Without: the Holland profile, which spreads wind too far."""
    lat, lon = np.atleast_1d(np.asarray(lat, float)), np.atleast_1d(np.asarray(lon, float))
    out = np.full(len(lat), np.inf)
    for i in range(horizon + 1):
        s = state_at(track, h0 + i)
        if s is None:
            continue
        ts = _inside_r34(s, lat, lon, r34_nm) if r34_nm else _wind_speed(s, lat, lon) >= TS_WIND_MS
        hit = np.isinf(out) & ts
        out[hit] = h0 + i
        if not np.isinf(out).any():
            break
    return out


def _wind_arrival(track, h0, lat, lon, horizon=48, r34_nm=None):
    return float(_wind_arrivals(track, h0, lat, lon, horizon, r34_nm)[0])


class _Storm:
    def __init__(self, key, hour):
        self.key, self.hour = key, hour
        self.info = load_storm(key)[0]
        w = world(key, hour)
        self.track = (w["storm"] or {}).get("track") or []
        # Size of the tropical-storm wind field: the 34-kt radii of the latest best-track fix before the advisory
        # was issued (what forecasters had then; the replay's forecasts don't carry radii), held along the track.
        issued = (w["storm"] or {}).get("issued_h", hour)
        fixes = [p for p in self.info["best_track"] if p["t"] <= issued - 3 and (p.get("radii") or {}).get("34")]
        self.r34 = fixes[-1]["radii"]["34"] if fixes else None
        mids, highs = zip(*(levels(w["gauges"][g]) for g in self.info["field_gauges"]))
        self.mid = np.array([_ffill(m) for m in mids])         # (gauges, 49) expected level, ft NAVD88
        self.high = np.array([_ffill(m) for m in highs])
        self.gtex = np.array(self.info["terrain"]["gauge_texels"], float)
        self.spill, self.ground, _ = terrain(key)
        vmax = max((p["vmax_kt"] for p in self.track), default=0.0)
        zones = ZONES_FOR_CATEGORY.get(max(category(vmax), 0), "")
        self.zone_ft = max((ZONE_SPILL_FT[z] for z in zones), default=None)
        self.zones = zones
        self.origin = datetime.fromisoformat(self.info["start"].replace("Z", "+00:00")).astimezone(timezone.utc)

    def at(self, lon, lat):
        """spill, ground (ft) per point; spill inf where water never reaches; nan outside the mapped box."""
        c, r = _texel(self.info, lon, lat)
        r, c = r.astype(int), c.astype(int)
        inside = (r >= 0) & (r < self.spill.shape[0]) & (c >= 0) & (c < self.spill.shape[1])
        spill = np.full(len(lon), np.nan)
        ground = np.full(len(lon), np.nan)
        spill[inside] = self.spill[r[inside], c[inside]]
        ground[inside] = self.ground[r[inside], c[inside]]
        return spill, ground, inside, (c, r)

    def blended(self, texel, which):
        c, r = texel
        d2 = (self.gtex[None, :, 0] - c[:, None]) ** 2 + (self.gtex[None, :, 1] - r[:, None]) ** 2
        w = 1.0 / np.maximum(d2, 1.0)
        return (w @ which) / w.sum(axis=1, keepdims=True)              # (points, 49)

    def inside_box(self, lat, lon):
        w, s, e, n = self.info["bbox"]
        return w <= lon <= e and s <= lat <= n


def _far_from(lon, lat, wet, km, cell_km=0.5):
    """True where no `wet` point lies within ~km: wet points marked on a 0.5 km grid, spread by a box sum.
    The square box is a little stricter than a circle, which only shrinks safe ground."""
    far = np.ones(len(lon), bool)
    if not wet.any():
        return far
    x, y = local_km(lon, lat, float(lon.mean()), float(lat.mean()))
    ix, iy = np.floor((x - x.min()) / cell_km).astype(int), np.floor((y - y.min()) / cell_km).astype(int)
    grid = np.zeros((iy.max() + 1, ix.max() + 1), np.int32)
    grid[iy[wet], ix[wet]] = 1
    r = int(np.ceil(km / cell_km))
    pad = np.pad(grid, r + 1)
    s = pad.cumsum(0).cumsum(1)                         # integral image: box sums in O(1)
    h, w = grid.shape
    yy, xx = np.arange(h)[:, None] + r + 1, np.arange(w)[None, :] + r + 1
    box = s[yy + r, xx + r] - s[yy - r - 1, xx + r] - s[yy + r, xx - r - 1] + s[yy - r - 1, xx - r - 1]
    return box[iy, ix] == 0


def _conditions(st):
    def conditions(G, mask, want_safe):
        import shapely
        from shapely import STRtree

        n = len(G)
        sel = mask[G.pt_edge]
        lon, lat = G.pts[sel, 0], G.pts[sel, 1]
        eidx = G.pt_edge[sel]

        def per_edge_min(vals):
            out = np.full(n, np.inf)
            np.minimum.at(out, eidx, vals)
            return out

        def per_edge_any(m):
            return np.bincount(eidx[m], minlength=n) > 0

        spill, ground, inside, tex = st.at(lon, lat)
        # Water can reach these points at some level. Points ordinary high tide already reaches are open water
        # under a bridge or causeway deck, not road that floods.
        can = inside & np.isfinite(spill) & ~(spill <= st.info.get("high_tide_ft", 0.0))
        arr = np.full(len(lon), np.inf)
        high_floods = np.zeros(len(lon), bool)
        peak_high = np.full(len(lon), -np.inf)
        if can.any():
            ci = np.flatnonzero(can)
            texc = (tex[0][ci], tex[1][ci])
            mid, high = st.blended(texc, st.mid), st.blended(texc, st.high)
            sp, gr = spill[ci, None], ground[ci, None]
            wet = (mid >= sp) & (mid - gr >= FLOOD_DEPTH_FT)
            first = np.where(wet.any(axis=1), wet.argmax(axis=1), -1)
            arr[ci] = np.where(first >= 0, st.hour + first, np.inf)
            high_floods[ci] = ((high >= sp) & (high - gr >= FLOOD_DEPTH_FT)).any(axis=1) & (first < 0)
            peak_high[ci] = high.max(axis=1)
        arrival = per_edge_min(arr)
        flooded_now = per_edge_any(arr <= st.hour)

        # Bridges: storm winds close them from the first forecast hour with tropical-storm force.
        bridge_now = np.zeros(n, bool)
        bridges = np.flatnonzero(G.bridge & mask)
        if len(bridges):
            mid = np.array([np.mean(G.coords[e], axis=0) for e in bridges])
            t = _wind_arrivals(st.track, st.hour, mid[:, 1], mid[:, 0], r34_nm=st.r34)
            arrival[bridges] = np.minimum(arrival[bridges], t)
            bridge_now[bridges] = t <= st.hour
        tunnel = G.tunnel & mask & np.isfinite(arrival)

        exposure = per_edge_any(high_floods).astype(float)
        out = {"arrival": arrival, "hard": {"flood": flooded_now, "bridge": bridge_now, "tunnel": tunnel},
               "penalty": 0.5 * exposure, "exposure": exposure,
               "exposure_text": "The high-water scenario floods {roads}: be ready for water on the road."}
        if want_safe:
            # (a) ground well above the worst case, (b) outside the ordered evacuation zones, (c) away from flooding.
            high_ok = ~can | (ground >= peak_high + SAFE_PAD_FT)
            zone_ok = ~can | (st.zone_ft is None) | (spill >= (st.zone_ft or 0))
            far = _far_from(lon, lat, np.isfinite(arr), SAFE_FLOOD_KM)
            out["safe"] = ~per_edge_any(~(high_ok & zone_ok & far))
        return out
    return conditions


REASONS = {
    "flood": lambda cond, e: f"already under {FLOOD_DEPTH_FT:g} ft of water or more at departure (forecast)",
    "bridge": lambda cond, e: "bridge closed: tropical-storm-force winds (39 mph) forecast",
    "tunnel": lambda cond, e: "tunnel closed: flooding forecast at an entrance",
}


# ------------------------------------------------------------------ live: an active Atlantic storm

# Live NHC data has the storm's position but no forecast track, so live routing follows the official NWS
# warnings and their onset times: water from Storm Surge / Coastal Flood Warnings, tropical-storm winds from
# Hurricane / Tropical Storm Warnings. Watches only warn.
WATER_WARNINGS = ("Storm Surge Warning", "Coastal Flood Warning")
WIND_WARNINGS = ("Hurricane Warning", "Tropical Storm Warning")
SOFT_ALERTS = ("Storm Surge Watch", "Coastal Flood Watch", "Coastal Flood Advisory", "Hurricane Watch",
               "Tropical Storm Watch")
LIVE_ROAD_PAD_DEG = 0.15


class NoLiveStorm(ValueError):
    pass


class _Live:
    """The warnings in force now, on a clock starting now."""

    def __init__(self):
        import json

        from shapely.geometry import shape

        from .config import LIVE
        storms = json.loads((LIVE / "storms.json").read_text()) if (LIVE / "storms.json").exists() else []
        alerts = json.loads((LIVE / "alerts.geojson").read_text())["features"] if (LIVE / "alerts.geojson").exists() else []
        self.storms = [s for s in storms if s["id"].startswith("al")]       # Atlantic only
        self.origin = datetime.now(timezone.utc)

        def onset_h(p):
            t = p.get("onset") or p.get("effective")
            if not t:
                return 0.0
            return max(0.0, (datetime.fromisoformat(t) - self.origin).total_seconds() / 3600)

        self.water = [(shape(f["geometry"]), onset_h(f["properties"])) for f in alerts
                      if f["properties"]["event"] in WATER_WARNINGS and f.get("geometry")]
        self.wind = [(shape(f["geometry"]), onset_h(f["properties"])) for f in alerts
                     if f["properties"]["event"] in WIND_WARNINGS and f.get("geometry")]
        self.soft = [shape(f["geometry"]) for f in alerts if f["properties"]["event"] in SOFT_ALERTS and f.get("geometry")]
        if not self.storms or not (self.water or self.wind):
            raise NoLiveStorm("No active Atlantic storm with surge or hurricane warnings right now; "
                              "pick a storm replay instead.")
        import shapely
        u = shapely.unary_union([g for g, _ in self.water + self.wind])
        w, s, e, n = u.bounds
        self.bbox = (w - LIVE_ROAD_PAD_DEG, s - LIVE_ROAD_PAD_DEG, e + LIVE_ROAD_PAD_DEG, n + LIVE_ROAD_PAD_DEG)
        self.key = self.storms[0]["id"]
        self.name = self.storms[0]["name"]

    def inside(self, lat, lon):
        w, s, e, n = self.bbox
        return w <= lon <= e and s <= lat <= n

    @staticmethod
    def _first_onset(pairs, pts):
        """Earliest onset (h) of any polygon containing each point; inf where none does."""
        from shapely import STRtree
        out = np.full(len(pts), np.inf)
        if pairs:
            geoms, onsets = zip(*pairs)
            i, j = STRtree(list(geoms)).query(pts, predicate="intersects")
            np.minimum.at(out, i, np.asarray(onsets, float)[j])
        return out

    def wind_onset(self, lat, lon):
        import shapely
        return float(self._first_onset(self.wind, shapely.points([lon], [lat]))[0])


def _live_conditions(lv):
    def conditions(G, mask, want_safe):
        import shapely
        from shapely import STRtree

        n = len(G)
        sel = mask[G.pt_edge]
        lon, lat = G.pts[sel, 0], G.pts[sel, 1]
        eidx = G.pt_edge[sel]
        pts = shapely.points(lon, lat)

        def per_edge_min(vals):
            out = np.full(n, np.inf)
            np.minimum.at(out, eidx, vals)
            return out

        def per_edge_any(m):
            return np.bincount(eidx[m], minlength=n) > 0

        water = lv._first_onset(lv.water, pts)
        wind = lv._first_onset(lv.wind, pts)
        arrival = per_edge_min(water)
        bridges = G.bridge & mask
        arrival[bridges] = np.minimum(arrival[bridges], per_edge_min(wind)[bridges])
        soft = np.zeros(len(lon), bool)
        if lv.soft:
            soft[STRtree(lv.soft).query(pts, predicate="intersects")[0]] = True
        exposure = per_edge_any(soft).astype(float)
        out = {"arrival": arrival,
               "hard": {"flood": per_edge_any(water <= 0), "bridge": bridges & (per_edge_min(wind) <= 0),
                        "tunnel": G.tunnel & mask & np.isfinite(arrival)},
               "penalty": 0.5 * exposure, "exposure": exposure,
               "exposure_text": "A storm surge or flood watch covers {roads}."}
        if want_safe:
            near = np.isfinite(water) | np.isfinite(wind)
            far = _far_from(lon, lat, np.isfinite(water), SAFE_FLOOD_KM)
            out["safe"] = ~per_edge_any(near | ~far)
        return out
    return conditions


def _live_hazard(depart_h, at):
    lv = _Live()
    cap = lv.wind_onset(*at) if at else np.inf
    notes = [f"Live: {lv.name}. Roads follow the NWS warnings in force and their onset times (no live surge "
             "forecast or track here).", "Free-flow speeds; no live traffic feed.",
             "Always follow official evacuation orders and road closures."]
    hz = Hazard(kind="surge", label="live", origin=lv.origin, depart_h=float(depart_h), conditions=_live_conditions(lv),
                reasons=REASONS, region=f"live:{lv.key}", arrival_phrase=lambda iso: f"warning in effect from {iso}",
                escape_soft=(), safe_lead_h=SAFE_LEAD_H, safe_text="outside every surge, flood and hurricane warning",
                leave_by_cap_h=None if not np.isfinite(cap) else cap,
                leave_by_cap_reason="tropical-storm-force winds are warned for your area then", notes=notes,
                mapped=lv.inside, mapped_label="warning area")
    hz.live = lv
    return hz


def build(mode, depart_h, look_ahead_h=0.0, at=None, blocks=None):
    """mode "replay:<storm>" (depart_h = hours into the replay) or "live" (hours from now, active Atlantic storms)."""
    if mode == "live":
        return _live_hazard(depart_h, at)
    if not mode.startswith("replay:"):
        raise ValueError('surge mode must be "replay:<storm>" (sandy_2012, matthew_2016, irma_2017, isabel_2003) or "live"')
    key = mode.split(":", 1)[1]
    st = _Storm(key, int(round(float(depart_h))))
    cap = _wind_arrival(st.track, st.hour, *at, r34_nm=st.r34) if at else np.inf
    notes = ["Water levels come from the NHC advisory and tide gauges available at departure, not the observed flood.",
             "Free-flow speeds, slowed by modelled evacuation traffic near the water; no live traffic feed.",
             "Always follow official evacuation orders and road closures."]
    if st.zones:
        notes.append(f"Evacuation zones {', '.join(st.zones)} apply for the forecast category.")
    hz = Hazard(kind="surge", label=mode, origin=st.origin, depart_h=float(st.hour), conditions=_conditions(st),
                reasons=REASONS, region=f"surge:{key}", arrival_phrase=lambda iso: f"water expected at {iso}",
                escape_soft=(), safe_lead_h=SAFE_LEAD_H,
                safe_text="2 ft above the worst-case water, outside the ordered evacuation zones, 3 mi from flooding",
                leave_by_cap_h=None if not np.isfinite(cap) else cap,
                leave_by_cap_reason="tropical-storm-force winds reach your area then", notes=notes,
                mapped=st.inside_box, mapped_label="flood-mapped area")
    hz.storm = st
    return hz


def graph_for(hz):
    if getattr(hz, "live", None) is not None:
        # Only the warned region, downloaded the first time someone asks (in the background).
        path = SURGE_ROADS / "live" / f"{hz.live.key}.json"
        if not path.exists():
            road_graph.region_later(hz.live.bbox, path)
            raise FileNotFoundError(f"Road data for {hz.live.name}'s warning area is downloading; try again in a few "
                                    "minutes.")
        return road_graph.load(str(path))
    return road_graph.load(str(SURGE_ROADS / f"{hz.storm.key}.json"))
