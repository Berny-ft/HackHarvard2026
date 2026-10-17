"""What the agent can see at one hour of a storm, and the forecast it makes from it.

Point in time, like the wildfire replay:
  * the latest NHC official forecast issued by then (advisories go out ~3 h
    after their synoptic time), never the best track;
  * tide gauge water levels observed up to then (real-time gauges);
  * astronomical tide predictions (published years ahead);
  * the ground: elevation and spill level from the terrain texture.

The surge physics mirrors modeling/surge/model.py (Holland wind and pressure
profiles, onshore wind setup, lagged response) with the parameters fitted on
the train storms. Each gauge's forecast starts from the surge it observes now.
"""
import json
from datetime import datetime, timedelta
from functools import lru_cache

import numpy as np
from PIL import Image

from .config import FLOOD_DEPTH_FT, HIGH_SURGE_FACTOR, HIGH_SURGE_PAD_FT, HORIZON_H, STORMS, SURGE_OUT

KT_TO_MS, NM_TO_KM, FT_PER_HPA, P_AMBIENT, HOLLAND_B, INFLOW_DEG = 0.514444, 1.852, 0.0328, 1012.0, 1.3, 20.0
DEFAULT_RMW_NM = 25.0


# ------------------------------------------------------------------ data

@lru_cache(maxsize=8)
def load_storm(key):
    d = STORMS / key
    info = json.loads((d / "storm.json").read_text())
    gauges = json.loads((d / "gauges.json").read_text())
    adv = json.loads((d / "advisories.geojson").read_text())["features"]
    series = json.loads((d / "timeseries.json").read_text())
    return info, gauges, adv, series


@lru_cache(maxsize=8)
def terrain(key):
    """(spill ft, ground ft, info): spill inf = water never reaches; see modeling/surge/incidents.pack_terrain."""
    d = STORMS / key
    meta = json.loads((d / "flood.json").read_text())
    px = np.asarray(Image.open(d / "flood.png").convert("RGBA")).astype(np.int64)
    q = px[..., 0] * 256 + px[..., 1]
    lo, hi = meta["spill_min_ft"], meta["spill_max_ft"]
    spill = np.where(q == 65535, np.inf, lo + q / 65534 * (hi - lo))
    ground = np.where(np.isfinite(spill), spill - px[..., 2] * meta["gap_step_ft"], np.nan)
    return spill, ground, meta


def params():
    p = SURGE_OUT / "surge_model.json"
    return json.loads(p.read_text())["params"] if p.exists() else {"k_wind": 0.008, "shelf_exp": 0.0, "tau_h": 3.0}


def _merc(lon, lat):
    x = np.radians(lon) * 6378137.0
    y = np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) * 6378137.0
    return x, y


def texel(key, lat, lon):
    """(col, row) float texel of a point in the storm's terrain grid."""
    info = load_storm(key)[0]
    (lon0, lat1), (lon1, _), (_, lat0) = info["terrain"]["corners"][:3]
    x0, y1 = _merc(lon0, lat1)
    x1, y0 = _merc(lon1, lat0)
    x, y = _merc(lon, lat)
    W, H = info["terrain"]["width"], info["terrain"]["height"]
    return (x - x0) / (x1 - x0) * W, (y1 - y) / (y1 - y0) * H


def ground_at(key, lat, lon):
    """(spill ft or None if water never gets there, ground ft or None)."""
    spill, ground, _ = terrain(key)
    c, r = texel(key, lat, lon)
    r, c = int(r), int(c)
    if not (0 <= r < spill.shape[0] and 0 <= c < spill.shape[1]):
        return None, None
    s = spill[r, c]
    return (None, None) if not np.isfinite(s) else (float(s), float(ground[r, c]))


def gauge_weights(key, lat, lon):
    info = load_storm(key)[0]
    c, r = texel(key, lat, lon)
    g = np.array(info["terrain"]["gauge_texels"])
    d2 = (g[:, 0] - c) ** 2 + (g[:, 1] - r) ** 2
    return 1.0 / np.maximum(d2, 1.0)


def blend(levels, w):
    lv = np.asarray(levels, float)
    ok = np.isfinite(lv)
    return float((w[ok] * lv[ok]).sum() / w[ok].sum()) if ok.any() else float("nan")


# ------------------------------------------------------------------ physics (mirrors modeling/surge/model.py)

def _offset_km(lat0, lon0, lat1, lon1):
    kx = 111.32 * np.cos(np.radians((lat0 + lat1) / 2))
    return (lon1 - lon0) * kx, (lat1 - lat0) * 110.57


def pressure_from_wind(vmax_kt):
    return P_AMBIENT - (max(vmax_kt, 15.0) / 6.7) ** (1 / 0.644)


def state_at(pts, t):
    if not pts or t < pts[0]["t"] - 1e-6 or t > pts[-1]["t"] + 1e-6:
        return None
    for a, b in zip(pts, pts[1:]):
        if a["t"] <= t <= b["t"]:
            f = 0.0 if b["t"] == a["t"] else (t - a["t"]) / (b["t"] - a["t"])
            s = {k: a[k] + f * (b[k] - a[k]) for k in ("lat", "lon", "vmax_kt", "pmin_mb", "rmw_nm")}
            dt = max(b["t"] - a["t"], 1e-6)
            mx, my = _offset_km(a["lat"], a["lon"], b["lat"], b["lon"])
            s["motion"] = (mx / dt, my / dt)
            return s
    p = pts[-1]
    return {**{k: p[k] for k in ("lat", "lon", "vmax_kt", "pmin_mb", "rmw_nm")}, "motion": (0.0, 0.0)}


def wind_at(s, lat, lon):
    dx, dy = _offset_km(s["lat"], s["lon"], lat, lon)
    r = max(np.hypot(dx, dy), 1.0)
    x = (s["rmw_nm"] * NM_TO_KM / r) ** HOLLAND_B
    vmax = s["vmax_kt"] * KT_TO_MS
    v = vmax * np.sqrt(x * np.exp(1 - x))
    toward = (np.degrees(np.arctan2(dx, dy)) - 90.0 - INFLOW_DEG) % 360
    u, w = v * np.sin(np.radians(toward)), v * np.cos(np.radians(toward))
    k = 0.5 * v / max(vmax, 1e-6)
    u += k * s["motion"][0] / 3.6
    w += k * s["motion"][1] / 3.6
    deficit = (P_AMBIENT - s["pmin_mb"]) * (1 - np.exp(-x))
    return float(np.hypot(u, w)), float(np.degrees(np.arctan2(u, w)) % 360), float(r), float(max(deficit, 0.0))


def forcing(s, g, p):
    if s is None:
        return 0.0
    speed, toward, r, deficit = wind_at(s, g["lat"], g["lon"])
    if r > 1500:
        return 0.0
    onshore = speed * np.cos(np.radians(toward - g["push_to"]))
    return float(FT_PER_HPA * deficit + p["k_wind"] * (g["shelf_km"] / 50.0) ** p["shelf_exp"] * onshore * abs(onshore))


def category(vmax_kt):
    for c, v in ((5, 137), (4, 113), (3, 96), (2, 83), (1, 64)):
        if vmax_kt >= v:
            return c
    return 0 if vmax_kt >= 34 else -1


# ------------------------------------------------------------------ the world at hour h

def latest_advisory(key, hour):
    adv = [a for a in load_storm(key)[2] if a["properties"]["issued_h"] <= hour]
    return adv[-1] if adv else None


def forecast_track(key, adv):
    """Official forecast -> track points (t = hours since replay start). RMW persists from the
    most recent best-track fix before the forecast was issued (what forecasters had then)."""
    info = load_storm(key)[0]
    issued = adv["properties"]["issued_h"]
    known = [p for p in info["best_track"] if p["t"] <= issued - 3 and p.get("rmw_nm")]
    rmw = known[-1]["rmw_nm"] if known else DEFAULT_RMW_NM
    t0 = issued - 3
    pts = [{"t": t0 + p["tau"], "lat": p["lat"], "lon": p["lon"], "vmax_kt": p["vmax_kt"],
            "pmin_mb": pressure_from_wind(p["vmax_kt"]), "rmw_nm": rmw} for p in adv["properties"]["points"]]
    pts.sort(key=lambda p: p["t"])
    out = []
    for p in pts:   # ATCF repeats taus across radii lines; keep one per time
        if not out or p["t"] > out[-1]["t"]:
            out.append(p)
    return out


@lru_cache(maxsize=4096)
def world(key, hour):
    """Everything the sentinel knows `hour` hours into the replay, and the gauge forecasts it implies."""
    info, gauges, _, series = load_storm(key)
    p = params()
    adv = latest_advisory(key, hour)
    pts = forecast_track(key, adv) if adv else []
    a = 1 - np.exp(-1.0 / p["tau_h"])
    fc = {}
    for gid in info["field_gauges"]:
        g = gauges[gid]
        obs = [v for v in g["observed_ft"][:hour + 1]]
        last = next(((i, v) for i, v in reversed(list(enumerate(obs))) if v is not None), (None, None))
        tide = np.array([np.nan if v is None else v for v in g["tide_ft"]], float)
        bg = g["background_ft"]
        s0 = (last[1] - tide[last[0]] - bg) if last[0] is not None else 0.0
        base, mid, lag0 = [], [], []
        cur, f0 = s0, 0.0
        n = len(tide)
        for i in range(HORIZON_H + 1):
            t = hour + i
            if i > 0:
                f = forcing(state_at(pts, t), g, p)
                cur += (f - cur) * a
                f0 += (f - f0) * a          # the storm's own surge, relaxed from zero
            td = tide[t] if t < n and np.isfinite(tide[t]) else np.nan
            base.append(td + bg)
            mid.append(cur)
            lag0.append(f0)
        fc[gid] = {"now_ft": obs[-1] if obs and obs[-1] is not None else last[1],
                   "surge_now_ft": round(float(s0), 2), "base": np.array(base), "surge": np.array(mid),
                   "storm_surge": np.array(lag0)}

    storm = None
    s_now = state_at(pts, hour) if pts else None
    if adv is not None:
        storm = {"issued_h": adv["properties"]["issued_h"], "advisory_age_h": round(hour - adv["properties"]["issued_h"], 1),
                 "now": s_now, "track": pts, "cone": adv["geometry"]}
    as_of = (datetime.fromisoformat(info["start"].rstrip("Z")) + timedelta(hours=hour)).isoformat() + "Z"
    return {"key": key, "hour": hour, "as_of": as_of, "name": info["name"], "storm": storm, "gauges": fc}


def levels(fc, high_factor=HIGH_SURGE_FACTOR, high_pad_ft=HIGH_SURGE_PAD_FT):
    """Expected and high-scenario water level (ft NAVD88) over the horizon for one gauge forecast.
    High: the storm's surge scaled up (and padded) on top of the expected one."""
    ss = np.maximum(fc["storm_surge"], 0)
    mid = fc["base"] + fc["surge"]
    high = mid + (high_factor - 1) * ss + high_pad_ft * (ss > 0.3)
    return mid, high


def observed_depth(key, hour, lat, lon, spill=None, ground=None):
    """Water depth at a point from the gauges' observed levels at `hour` (bathtub), ft; 0 if dry."""
    info, gauges, _, _ = load_storm(key)
    if spill is None:
        spill, ground = ground_at(key, lat, lon)
    if spill is None:
        return 0.0
    w = gauge_weights(key, lat, lon)
    lv = blend([gauges[g]["observed_ft"][hour] if hour < len(gauges[g]["observed_ft"]) and
                gauges[g]["observed_ft"][hour] is not None else np.nan for g in info["field_gauges"]], w)
    return float(lv - ground) if np.isfinite(lv) and lv >= spill and lv - ground > 0 else 0.0


def observed_arrival_h(key, lat, lon, depth_ft=FLOOD_DEPTH_FT):
    """Ground truth: first (fractional) hour the observed water stands `depth_ft` deep here; None if never.
    Also returns the peak observed depth."""
    info = load_storm(key)[0]
    spill, ground = ground_at(key, lat, lon)
    if spill is None:
        return None, 0.0
    d = [observed_depth(key, h, lat, lon, spill, ground) for h in range(info["hours"] + 1)]
    peak = max(d)
    for h in range(1, len(d)):
        if d[h] >= depth_ft:
            prev = d[h - 1] if d[h - 1] < depth_ft else depth_ft
            return round(h - 1 + (depth_ft - prev) / max(d[h] - prev, 1e-6), 2), round(peak, 2)
    return (0.0 if d and d[0] >= depth_ft else None), round(peak, 2)
