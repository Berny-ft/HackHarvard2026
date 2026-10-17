"""Build each storm's replay: flood terrain, gauges, forecasts, hourly series.

Flooding is a connected bathtub: a cell floods when the water level reaches
its *spill elevation*, the lowest level at which water can flow to it from the
sea over the terrain (priority flood from open water). Depth is level minus
ground. The level over the box at each hour is the inverse-distance blend of
the tide gauges' observed water level. That reconstruction is the ground truth
the agent is graded against; the surge model never touches it.

Per storm, in out/surge/storms/<key>/:
  storm.json        metadata, best track, model scores, flood stats
  gauges.json       hourly observed level, tide, observed and modelled surge
  advisories.geojson every NHC official forecast: track points + cone polygon
  timeseries.json   hourly series for the charts
  flood.png/.json   spill elevation + ground, packed for the GPU (see pack_terrain)
  extent.geojson    outline of the peak flood
"""
import heapq
import json
from datetime import datetime, timedelta

import numpy as np
from PIL import Image
from pyproj import Transformer

from .config import FLOOD_DEPTH_FT, GAUGES, OUT, STORMS, WATER_FT
from .fetch import fetch_dem, fetch_weather
from .model import fitted_params, hindcast_scores, load_storm, observed_surge, run_surge, track_points

SPILL_MIN, SPILL_MAX = -10.0, 40.0
_from_merc = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
_to_merc = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
# NHC cone radii (nm) by forecast hour, 2010s-2020s average track errors.
CONE_NM = {0: 0, 3: 8, 12: 26, 24: 41, 36: 55, 48: 69, 60: 86, 72: 103, 96: 151, 120: 196}


def category(vmax_kt):
    for c, v in ((5, 137), (4, 113), (3, 96), (2, 83), (1, 64)):
        if vmax_kt >= v:
            return c
    return 0 if vmax_kt >= 34 else -1


# ---------------------------------------------------------------- terrain

def spill_elevation(dem):
    """Lowest water level (ft) that reaches each cell from open water (priority flood, 8-connected)."""
    H, W = dem.shape
    ground = np.where(np.isfinite(dem), dem, 999.0)
    spill = np.full(dem.shape, np.inf)
    seeds = np.argwhere(ground <= WATER_FT)
    heap = [(float(ground[r, c]), int(r), int(c)) for r, c in seeds]
    for lvl, r, c in heap:
        spill[r, c] = lvl
    heapq.heapify(heap)
    nb = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    flat_g, flat_s = ground.ravel(), spill.ravel()
    while heap:
        lvl, r, c = heapq.heappop(heap)
        if lvl > flat_s[r * W + c] or lvl > SPILL_MAX:
            continue
        for dr, dc in nb:
            rr, cc = r + dr, c + dc
            if 0 <= rr < H and 0 <= cc < W:
                i = rr * W + cc
                v = max(lvl, flat_g[i])
                if v < flat_s[i]:
                    flat_s[i] = v
                    heapq.heappush(heap, (v, rr, cc))
    return spill


def pack_terrain(spill, dem, name, grid):
    """flood.png: R,G = spill elevation, 16 bits over [-10, 40] ft (65535 = water never gets there);
    B = spill minus ground in 0.05 ft steps (0-12.75 ft); A = 255 so browsers never premultiply."""
    ok = np.isfinite(spill) & (spill <= SPILL_MAX)
    q = np.where(ok, np.round((np.clip(spill, SPILL_MIN, SPILL_MAX) - SPILL_MIN) / (SPILL_MAX - SPILL_MIN) * 65534),
                 65535).astype(np.uint16)
    gap = np.where(ok, np.clip(spill - np.nan_to_num(dem, nan=0.0), 0, 12.75), 0)
    b = np.round(gap / 0.05).astype(np.uint8)
    rgba = np.stack([(q >> 8).astype(np.uint8), (q & 255).astype(np.uint8), b, np.full(q.shape, 255, np.uint8)], -1)
    Image.fromarray(rgba, "RGBA").save(name.with_suffix(".png"), optimize=True)
    x0, y1, res, W, H = grid["x0"], grid["y1"], grid["res"], grid["W"], grid["H"]
    (lon0, lon1), (lat1, lat0) = _from_merc.transform([x0, x0 + W * res], [y1, y1 - H * res])
    corners = [[lon0, lat1], [lon1, lat1], [lon1, lat0], [lon0, lat0]]
    name.with_suffix(".json").write_text(json.dumps({
        "image": name.with_suffix(".png").name, "coordinates": corners, "width": W, "height": H,
        "res_m": res, "spill_min_ft": SPILL_MIN, "spill_max_ft": SPILL_MAX, "gap_step_ft": 0.05}, indent=1))
    return corners


def decode_terrain(png_path):
    """Inverse of pack_terrain: (spill ft with inf = never, ground ft)."""
    px = np.asarray(Image.open(png_path).convert("RGBA")).astype(np.int64)
    q = px[..., 0] * 256 + px[..., 1]
    spill = np.where(q == 65535, np.inf, SPILL_MIN + q / 65534 * (SPILL_MAX - SPILL_MIN))
    ground = np.where(np.isfinite(spill), spill - px[..., 2] * 0.05, np.nan)
    return spill, ground


# ---------------------------------------------------------------- water field

def field_gauges(key):
    """Gauges that shape the water surface over the box: inside it or within ~40 km."""
    lon0, lat0, lon1, lat1 = STORMS[key]["bbox"]
    out = []
    for gid in STORMS[key]["gauges"]:
        g = GAUGES[gid]
        dx = max(lon0 - g["lon"], 0, g["lon"] - lon1) * 111.32 * np.cos(np.radians(g["lat"]))
        dy = max(lat0 - g["lat"], 0, g["lat"] - lat1) * 110.57
        if np.hypot(dx, dy) <= 40:
            out.append(gid)
    return out


def idw_weights(px, py, gx, gy):
    """Inverse-distance-squared weights (rows: points, cols: gauges), in texel units."""
    d2 = (px[:, None] - gx[None, :]) ** 2 + (py[:, None] - gy[None, :]) ** 2
    w = 1.0 / np.maximum(d2, 1.0)
    return w


def level_field(levels, w):
    """Blend gauge levels (nan = gauge offline) with weights w -> level per point."""
    lv = np.asarray(levels, float)
    ok = np.isfinite(lv)
    if not ok.any():
        return np.full(w.shape[0], np.nan)
    ww = w[:, ok]
    return (ww * lv[ok]).sum(1) / ww.sum(1)


def gauge_texels(gids, grid):
    xs, ys = _to_merc.transform([GAUGES[g]["lon"] for g in gids], [GAUGES[g]["lat"] for g in gids])
    return (np.array(xs) - grid["x0"]) / grid["res"], (grid["y1"] - np.array(ys)) / grid["res"]


# ---------------------------------------------------------------- forecasts

def cone(points):
    from shapely.geometry import Point
    from shapely.ops import unary_union

    taus = sorted(CONE_NM)
    shapes = []
    prev = None
    for p in points:
        if p["tau"] > 120:
            continue
        rad = np.interp(p["tau"], taus, [CONE_NM[t] for t in taus]) * 1.852 / 111.0
        c = Point(p["lon"], p["lat"]).buffer(max(rad, 0.02), 32)
        shapes.append(c if prev is None else unary_union([prev, c]).convex_hull)
        prev = c
    return unary_union(shapes).simplify(0.01) if shapes else None


def advisories_geojson(atcf, start):
    from shapely.geometry import mapping

    feats = []
    for adv in atcf["advisories"]:
        issued = (datetime.strptime(adv["dtg"], "%Y%m%d%H") + timedelta(hours=3) - start).total_seconds() / 3600
        pts = [p for p in adv["points"] if p["tau"] != 3 or True]
        c = cone(pts)
        props = {"issued_h": issued, "dtg": adv["dtg"],
                 "points": [{"tau": p["tau"], "lat": p["lat"], "lon": p["lon"], "vmax_kt": p["vmax_kt"],
                             "cat": category(p["vmax_kt"])} for p in pts]}
        if c is not None:
            feats.append({"type": "Feature", "geometry": mapping(c), "properties": props})
    return {"type": "FeatureCollection", "features": feats}


# ---------------------------------------------------------------- build

def build(key):
    s, start, atcf, best, gauges = load_storm(key)
    params = fitted_params()
    d = OUT / "storms" / key
    d.mkdir(parents=True, exist_ok=True)
    hours = s["hours"]
    print(f"[surge replays] {key}: terrain", flush=True)

    dem, grid = fetch_dem(key)
    spill = spill_elevation(dem)
    corners = pack_terrain(spill, dem, d / "flood", grid)
    spill, ground = decode_terrain(d / "flood.png")      # exactly what the web and the agent see

    # Gauges: observed, tide, observed surge, hindcast surge.
    hind = hindcast_scores(key, params)
    fg = field_gauges(key)
    gout = {}
    for gid, g in gauges.items():
        surge, bg = observed_surge(g)
        gout[gid] = {"id": gid, "name": g["name"], "lat": g["lat"], "lon": g["lon"], "field": gid in fg,
                     "push_to": g["push_to"], "shelf_km": g["shelf_km"], "background_ft": round(bg, 2),
                     "observed_ft": g["observed_ft"], "tide_ft": g["tide_ft"],
                     "surge_ft": [None if not np.isfinite(v) else round(float(v), 2) for v in surge],
                     "model_surge_ft": hind.get(gid, {}).get("sim")}
    (d / "gauges.json").write_text(json.dumps(gout, separators=(",", ":")))

    # Observed water surface over the box, hour by hour.
    gx, gy = gauge_texels(fg, grid)
    # Land is ground above the highest predicted tide: marsh that floods every spring tide isn't news.
    high_tide = max(max(v for v in gauges[g]["tide_ft"] if v is not None) for g in fg)
    land = np.isfinite(spill) & (ground > max(WATER_FT, high_tide))
    rows, cols = np.nonzero(np.isfinite(spill))
    w = idw_weights(cols + 0.5, rows + 0.5, gx, gy)
    sp, gr = spill[rows, cols], ground[rows, cols]
    ld = land[rows, cols]
    cell_acres = grid["res"] ** 2 * np.cos(np.radians((s["bbox"][1] + s["bbox"][3]) / 2)) ** 2 / 4046.86
    peak_depth = np.zeros(len(sp))
    flooded_acres, levels_primary = [], []
    for h in range(hours + 1):
        lv = level_field([gauges[g]["observed_ft"][h] if gauges[g]["observed_ft"][h] is not None else np.nan
                          for g in fg], w)
        wet = (lv >= sp) & (lv - gr >= FLOOD_DEPTH_FT) & ld
        peak_depth = np.maximum(peak_depth, np.where(wet, lv - gr, 0))
        flooded_acres.append(float(wet.sum() * cell_acres))
        levels_primary.append(gauges[fg[0]]["observed_ft"][h])
    extent = _extent(rows, cols, peak_depth > 0, grid)
    (d / "extent.geojson").write_text(json.dumps(extent))

    adv = advisories_geojson(atcf, start)
    (d / "advisories.geojson").write_text(json.dumps(adv, separators=(",", ":")))

    # Hourly series for the charts.
    wx = fetch_weather(key)
    lon0, lat0, lon1, lat1 = s["bbox"]
    cx, cy = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    from .model import state_at, _offset_km
    series = []
    for h in range(hours + 1):
        st = state_at(best, h)
        dist = float(np.hypot(*_offset_km(st["lat"], st["lon"], cy, cx))) if st else None
        p = gout[fg[0]]
        series.append({
            "hour": h, "time": (start + timedelta(hours=h)).isoformat() + "Z",
            "water_ft": p["observed_ft"][h], "tide_ft": p["tide_ft"][h], "surge_ft": p["surge_ft"][h],
            "model_surge_ft": (p["model_surge_ft"] or [None] * (hours + 1))[h],
            "flooded_acres": round(flooded_acres[h], 1),
            "storm_km": None if dist is None else round(dist, 1),
            "storm_vmax_mph": None if st is None else round(st["vmax_kt"] * 1.15078),
            "storm_pmin_mb": None if st is None else round(st["pmin_mb"]),
            "storm_cat": None if st is None else category(st["vmax_kt"]),
            "storm_lat": None if st is None else round(st["lat"], 3), "storm_lon": None if st is None else round(st["lon"], 3),
            "wind_mph": wx[h]["wind_speed_10m"], "gust_mph": wx[h]["wind_gusts_10m"], "wind_dir_deg": wx[h]["wind_direction_10m"],
            "rain_in": wx[h]["precipitation"], "pressure_mb": wx[h]["pressure_msl"],
        })
    (d / "timeseries.json").write_text(json.dumps(series))

    track = [{"t": round(p["t"], 2), "lat": p["lat"], "lon": p["lon"], "vmax_kt": p["vmax_kt"], "pmin_mb": round(p["pmin_mb"]),
              "rmw_nm": p["rmw_nm"], "cat": category(p["vmax_kt"]), "radii": p["radii"]} for p in best]
    peak_h = int(np.nanargmax([x if x is not None else -99 for x in levels_primary]))
    info = {
        "key": key, "name": s["name"], "place": s["place"], "year": s["year"], "role": s["role"], "tz": s["tz"],
        "start": start.isoformat() + "Z", "hours": hours, "bbox": s["bbox"], "camera": s.get("camera", {}),
        "center": {"lon": cx, "lat": cy}, "field_gauges": fg, "high_tide_ft": round(high_tide, 2), "flood_depth_ft": FLOOD_DEPTH_FT,
        "terrain": {"corners": corners, "width": grid["W"], "height": grid["H"],
                    "gauge_texels": [[round(float(x), 2), round(float(y), 2)] for x, y in zip(gx, gy)]},
        "peak": {"hour": peak_h, "water_ft": levels_primary[peak_h], "gauge": GAUGES[fg[0]]["name"],
                 "flooded_acres": round(max(flooded_acres)), "max_depth_ft": round(float(peak_depth.max()), 1)},
        "model": {"params": params, "gauges": {gid: {k: v for k, v in r.items() if k != "sim"} for gid, r in hind.items()}},
        "best_track": track,
    }
    (d / "storm.json").write_text(json.dumps(info, indent=1))
    print(f"  peak {info['peak']['water_ft']} ft at {info['peak']['gauge']} (hour {peak_h}), "
          f"{info['peak']['flooded_acres']:,} acres flooded, {len(adv['features'])} advisories")


def _extent(rows, cols, wet, grid):
    """Outline of every cell that flooded at some hour, as lon/lat polygons."""
    import rasterio.features
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union
    from rasterio.transform import from_origin

    m = np.zeros((grid["H"], grid["W"]), np.uint8)
    m[rows[wet], cols[wet]] = 1
    k = 3   # coarsen for a light outline
    H, W = (grid["H"] // k) * k, (grid["W"] // k) * k
    mc = m[:H, :W].reshape(H // k, k, W // k, k).max(axis=(1, 3))
    tr = from_origin(grid["x0"], grid["y1"], grid["res"] * k, grid["res"] * k)
    polys = [shape(g) for g, v in rasterio.features.shapes(mc, mask=mc > 0, transform=tr) if v]
    geom = unary_union(polys).simplify(grid["res"] * 1.5)
    from shapely.ops import transform as shp_transform
    geom = shp_transform(lambda x, y, z=None: _from_merc.transform(x, y), geom)
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": mapping(geom), "properties": {}}]}


def run():
    index = []
    for key in STORMS:
        build(key)
        info = json.loads((OUT / "storms" / key / "storm.json").read_text())
        index.append({k: info[k] for k in ("key", "name", "place", "year", "role", "start", "hours", "center", "peak")})
    (OUT / "storms.json").write_text(json.dumps(index, indent=1))
