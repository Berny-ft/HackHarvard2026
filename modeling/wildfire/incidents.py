"""Run the spread model on real incidents, score it, and export the timeline.

Calibration is done on one fire and tested on another (both 2025 Santa Ana
fires), so the reported score is not fitted to the fire it describes.
"""
import json
from itertools import product

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize, shapes
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from . import fetch, observations
from .config import CRS, DATA, INCIDENT_RES, INCIDENTS, OUT
from .export import save_overlay
from .features import load_perimeters, load_raster
from .spread import simulate

SPINUP_H = 120
M2_PER_ACRE = 4046.86
CHAIN_M = 20.1168

_to_albers = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)


def load_incident(key):
    cfg = INCIDENTS[key]
    res = cfg.get("res", INCIDENT_RES)
    lon, lat = cfg["ignition_lonlat"]
    bounds = fetch.bounds_around(lon, lat, cfg["half_width_km"] * 1000, res)
    fetch.fetch_terrain_and_fuels(bounds, res, key)
    dem, prof = load_raster(DATA / f"{key}_dem.tif")
    fbfm, _ = load_raster(DATA / f"{key}_fbfm40.tif")
    cc, _ = load_raster(DATA / f"{key}_cc.tif")

    start = pd.Timestamp(cfg["start"]).floor("h")
    wx = fetch.fetch_hourly_weather(
        key, lon, lat,
        (start - pd.Timedelta(hours=SPINUP_H)).strftime("%Y-%m-%d"),
        (start + pd.Timedelta(hours=cfg["hours"] + 24)).strftime("%Y-%m-%d"))
    i0 = int(np.searchsorted(wx["time"].to_numpy(), (start - pd.Timedelta(hours=SPINUP_H)).to_datetime64()))
    wx = wx.iloc[i0:].reset_index(drop=True)

    x, y = _to_albers.transform(lon, lat)
    row, col = rasterio.transform.rowcol(prof["transform"], x, y)
    perims = load_perimeters()
    real = perims[(perims["FIRE_NAME"].str.upper() == cfg["frap_name"]) & (perims["year"] == cfg["year"])]
    real_geom = unary_union(real.geometry.to_list())
    real_mask = rasterize([(real_geom, 1)], out_shape=dem.shape,
                          transform=prof["transform"], fill=0, dtype="uint8").astype(bool)
    det = observations.fetch_detections(key, lon, lat, cfg["half_width_km"], start, cfg["hours"])
    cells = observations.detection_cells(det, prof, res, real_mask)
    # Satellite footprint curve, rescaled so its end matches the mapped burn
    # scar (pixel footprints overstate area).
    obs = observations.observed_growth(cells, real_mask.shape, cfg["hours"])
    obs_cells = obs * real_mask.sum() / max(1, obs[-1])
    return dict(obs_cells=obs_cells, cfg=cfg, res=res, dem=dem, fbfm=fbfm, cc=cc, prof=prof, wx=wx, start=start,
                ignition_rc=(row, col), real_geom=real_geom, real_mask=real_mask,
                detections=det, det_cells=cells)


def run_sim(inc, **params):
    cfg = inc["cfg"]
    kw = dict(live_herb=cfg.get("live_herb", 0.30), live_woody=cfg.get("live_woody", 0.60))
    kw.update(params)
    return simulate(inc["dem"], inc["fbfm"], inc["cc"], inc["res"], inc["ignition_rc"],
                    inc["wx"], cfg["hours"], spinup=SPINUP_H, **kw)


def score(arrival, inc):
    """Final-shape overlap with the real perimeter + timing vs satellite detections."""
    real_mask = inc["real_mask"]
    sim = np.isfinite(arrival)
    inter = (sim & real_mask).sum()
    out = {
        "sorensen": float(2 * inter / max(1, sim.sum() + real_mask.sum())),
        "iou": float(inter / max(1, (sim | real_mask).sum())),
        "sim_over_real_area": float(sim.sum() / max(1, real_mask.sum())),
        **observations.timing_score(arrival, inc.get("det_cells")),
    }
    # Growth timing: hourly simulated area vs satellite-observed area, symmetric
    # in log space (too fast is as wrong as too slow). Skip the first 3 hours.
    hours = len(inc["obs_cells"]) - 1
    sim_cells = np.array([(arrival <= h * 60).sum() for h in range(hours + 1)], float)
    pad = 50 * M2_PER_ACRE / inc["res"] ** 2  # 50 acres
    err = np.abs(np.log((sim_cells[3:] + pad) / (inc["obs_cells"][3:] + pad)))
    out["growth_log_error"] = float(err.mean())
    out["growth_skill"] = float(np.exp(-err.mean()))
    # Objective: equal weight on "right place" and "right speed".
    out["skill"] = 0.5 * out["sorensen"] + 0.5 * out["growth_skill"]
    return out


GRID = list(product([0.75, 1.0, 1.5], [0.1, 0.3], [0.0, 1.0, 2.0, 4.0], [0.5, 1.0], [0.0, 1.0, 2.0, 4.0]))
PARAM_NAMES = ("adjust", "urban_factor", "hold_ros", "gust_weight", "spot")


def calibrate(incs):
    """Score every parameter set on every incident."""
    table = {}
    for vals in GRID:
        params = dict(zip(PARAM_NAMES, vals))
        table[vals] = {k: score(run_sim(inc, **params)[0], inc) for k, inc in incs.items()}
        print("    " + " ".join(f"{n}={v:<4}" for n, v in params.items()) + " | " +
              " ".join(f"{k}: skill={s['skill']:.3f} sor={s['sorensen']:.3f} growth={s['growth_skill']:.2f}"
                       for k, s in table[vals].items()), flush=True)
    return table


# ---------------------------------------------------------------- export

def _isochrones(arrival, prof, res, hours, step=1):
    to_ll = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    feats = []
    for h in range(step, hours + 1, step):
        mask = (arrival <= h * 60).astype("uint8")
        if not mask.any():
            continue
        polys = [shape(g) for g, v in shapes(mask, mask=mask.astype(bool), transform=prof["transform"]) if v == 1]
        geom = unary_union(polys).simplify(res * 0.75)
        geom = gpd.GeoSeries([geom], crs=CRS).to_crs("EPSG:4326").iloc[0]
        feats.append({"type": "Feature", "properties": {"hour": h, "acres": float(mask.sum() * res * res / M2_PER_ACRE)},
                      "geometry": mapping(geom)})
    return {"type": "FeatureCollection", "features": feats}


def _timeseries(arrival, moist, wind, wx, start, hours, res):
    rows = []
    burned_prev = 0
    for h in range(hours + 1):
        burned = int((arrival <= h * 60).sum())
        area_m2 = burned * res * res
        # Head-fire proxy: growth in the "radius" of an equal-area circle.
        r_now = np.sqrt(area_m2 / np.pi)
        r_prev = np.sqrt(burned_prev * res * res / np.pi)
        w = wx.iloc[SPINUP_H + h] if SPINUP_H + h < len(wx) else wx.iloc[-1]
        m = moist.iloc[min(h, len(moist) - 1)]
        rows.append({
            "hour": h,
            "time": (start + pd.Timedelta(hours=h)).isoformat(),
            "acres": round(area_m2 / M2_PER_ACRE, 1),
            "growth_acres_per_hr": round((burned - burned_prev) * res * res / M2_PER_ACRE, 1),
            "spread_chains_per_hr": round((r_now - r_prev) / CHAIN_M, 1) if h else 0.0,
            "wind_mph": round(float(w["wind_speed_10m"]), 1),
            "gust_mph": round(float(w["wind_gusts_10m"]), 1),
            "wind_dir_deg": int(w["wind_direction_10m"]),
            "temp_f": round(float(w["temperature_2m"]), 1),
            "rh_pct": int(w["relative_humidity_2m"]),
            "fuel_moisture_1h_pct": round(float(m["m1"]) * 100, 1),
        })
        burned_prev = burned
    return rows


def _damage(key, arrival, prof, start):
    name = key.split("_")[0]
    path = DATA / f"dins_{name}_2025.geojson"
    if not path.exists():
        return None
    pts = gpd.read_file(path).to_crs(CRS)
    rows, cols = rasterio.transform.rowcol(prof["transform"], pts.geometry.x, pts.geometry.y)
    rows, cols = np.asarray(rows), np.asarray(cols)
    H, W = arrival.shape
    inside = (rows >= 0) & (rows < H) & (cols >= 0) & (cols < W)
    t = np.full(len(pts), np.inf)
    t[inside] = arrival[rows[inside], cols[inside]]
    # Structures sit in "urban" cells, so check the 3x3 neighbourhood too.
    for dr, dc in product((-1, 0, 1), repeat=2):
        rr, cc = (rows + dr).clip(0, H - 1), (cols + dc).clip(0, W - 1)
        t = np.minimum(t, np.where(inside, arrival[rr, cc], np.inf))
    destroyed = pts["DAMAGE"].str.startswith("Destroyed").to_numpy()
    reached = np.isfinite(t)
    pts = pts.to_crs("EPSG:4326")
    out = pts[["DAMAGE", "STRUCTURETYPE", "geometry"]].copy()
    out["sim_arrival_hr"] = np.where(reached, np.round(t / 60, 2), None)
    return out, {
        "structures": int(len(pts)),
        "destroyed": int(destroyed.sum()),
        "destroyed_reached_by_sim": float((destroyed & reached).sum() / max(1, destroyed.sum())),
        "undamaged_reached_by_sim": float((~destroyed & reached).sum() / max(1, (~destroyed).sum())),
    }


def export(key, inc, arrival, moist, wind, params, metrics):
    d = OUT / "incidents" / key
    d.mkdir(parents=True, exist_ok=True)
    cfg, res, prof = inc["cfg"], inc["res"], inc["prof"]
    hours = cfg["hours"]
    hrs = np.where(np.isfinite(arrival), arrival / 60, np.nan).astype(np.float32)
    with rasterio.open(d / "arrival_hours.tif", "w", **{**prof, "dtype": "float32", "nodata": np.nan}) as dst:
        dst.write(hrs, 1)
    save_overlay(hrs, prof, f"incidents/{key}/spread", vmin=0, vmax=hours, cmap="time")
    (d / "perimeters.geojson").write_text(json.dumps(_isochrones(arrival, prof, res, hours)))
    (d / "real_perimeter.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"source": "CAL FIRE FRAP"},
        "geometry": mapping(gpd.GeoSeries([inc["real_geom"]], crs=CRS).to_crs("EPSG:4326").iloc[0])}]}))
    series = _timeseries(arrival, moist, wind, inc["wx"], inc["start"], hours, res)
    observed = inc["obs_cells"] * res * res / M2_PER_ACRE
    for row, acres in zip(series, observed):
        row["observed_acres"] = round(float(acres), 1)
    det = inc["detections"]
    (d / "detections.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [float(r.longitude), float(r.latitude)]},
        "properties": {"hour": round(float(r.hour), 2), "time": r.time_local.isoformat(),
                       "frp_mw": float(r.frp), "source": r.source},
    } for r in det.itertuples()]}))
    dmg = _damage(key, arrival, prof, inc["start"])
    if dmg:
        pts, dmg_metrics = dmg
        pts.to_file(d / "structures.geojson", driver="GeoJSON")
        metrics = {**metrics, "damage": dmg_metrics}
    lon, lat = cfg["ignition_lonlat"]
    summary = {
        "key": key, "name": cfg["name"], "start": inc["start"].isoformat(), "hours": hours,
        "ignition": {"lon": lon, "lat": lat, "note": "approximate public origin"},
        "resolution_m": res, "params": params, "metrics": metrics,
        "final_sim_acres": series[-1]["acres"],
        "real_acres": float(inc["real_mask"].sum() * res * res / M2_PER_ACRE),
    }
    (d / "incident.json").write_text(json.dumps(summary, indent=1))
    (d / "timeseries.json").write_text(json.dumps(series))
    print(json.dumps(summary, indent=1))


def run(keys=("eaton_2025", "palisades_2025")):
    """Grid-search parameters on all fires; report leave-one-fire-out scores.

    The exported simulation uses the parameters with the best mean score
    across fires. Each incident also records its out-of-sample score: the
    score it gets with parameters chosen only on the *other* fires.
    """
    incs = {k: load_incident(k) for k in keys}
    print(f"[spread] calibrating on {', '.join(keys)}")
    table = calibrate(incs)

    def best(on):
        vals = max(table, key=lambda v: np.mean([table[v][k]["skill"] for k in on]))
        return dict(zip(PARAM_NAMES, vals)), vals

    shipped, shipped_vals = best(keys)
    print(f"  shipped params {shipped}")
    for key, inc in incs.items():
        others = [k for k in keys if k != key]
        loo_params, loo_vals = best(others) if others else (shipped, shipped_vals)
        arrival, moist, wind = run_sim(inc, **shipped)
        metrics = {
            **table[shipped_vals][key],
            "out_of_sample_sorensen": table[loo_vals][key]["sorensen"] if others else None,
            "out_of_sample_on_time": table[loo_vals][key]["on_time"] if others else None,
            "out_of_sample_growth_skill": table[loo_vals][key]["growth_skill"] if others else None,
            "out_of_sample_skill": table[loo_vals][key]["skill"] if others else None,
            "out_of_sample_params": loo_params if others else None,
        }
        export(key, inc, arrival, moist, wind, shipped, metrics)
