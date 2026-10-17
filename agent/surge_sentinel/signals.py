"""Turn the world at one hour into explainable flood signals for one address.

The water level at the address is the inverse-distance blend of the gauge
forecasts; water reaches the address once that level passes its spill level
(the lowest water that can flow there from the sea), and stands level minus
ground deep. Two scenarios: the expected forecast and a high one (see config).
"""
from functools import lru_cache

import numpy as np

from .config import (EVACUATE_H, FLOOD_DEPTH_FT, HIGH_SURGE_FACTOR, HIGH_SURGE_PAD_FT, LEVELS,
                     WARN_H, WATCH_H, WATCH_KM, WATCH_SPILL_FT)
from .world import (_offset_km, blend, category, gauge_weights, ground_at, levels, load_storm, observed_depth, state_at,
                    wind_at, world)

COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def compass(deg):
    return COMPASS[int(((deg or 0) + 22.5) // 45) % 8]


def _first(depth, thresh):
    i = np.flatnonzero(depth >= thresh)
    return None if len(i) == 0 else int(i[0])


def assess(key, hour, lat, lon, high_factor=HIGH_SURGE_FACTOR, high_pad_ft=HIGH_SURGE_PAD_FT,
           evacuate_h=EVACUATE_H, warn_h=WARN_H):
    w = world(key, hour)
    info = load_storm(key)[0]
    spill, ground = ground_at(key, lat, lon)
    wts = gauge_weights(key, lat, lon)
    gids = info["field_gauges"]
    lv = [levels(w["gauges"][g], high_factor, high_pad_ft) for g in gids]
    mid = _blend_series([m for m, _ in lv], wts)
    high = _blend_series([h for _, h in lv], wts)
    now_level = blend([w["gauges"][g]["now_ft"] if w["gauges"][g]["now_ft"] is not None else np.nan for g in gids], wts)

    def depth(level):
        if spill is None:
            return np.zeros_like(level)
        return np.where(np.nan_to_num(level, nan=-99) >= spill, np.nan_to_num(level, nan=-99) - ground, 0.0).clip(0)

    d_mid, d_high = depth(mid), depth(high)
    out = {
        "lat": lat, "lon": lon, "as_of": w["as_of"], "hour": hour, "storm_name": info["name"],
        "ground_ft": None if ground is None else round(ground, 1),
        "spill_ft": None if spill is None else round(spill, 1),     # water this high reaches the address
        "water_now_ft": None if not np.isfinite(now_level) else round(now_level, 2),
        "surge_now_ft": round(float(np.mean([w["gauges"][g]["surge_now_ft"] for g in gids])), 2),
        "flooding_now_ft": _flooding_now(key, hour, lat, lon),
        "forecast_peak_water_ft": None if not np.isfinite(mid).any() else round(float(np.nanmax(mid)), 2),
        "forecast_peak_depth_ft": round(float(d_mid.max()), 2),
        "high_peak_depth_ft": round(float(d_high.max()), 2),
        "eta_h": _first(d_mid, FLOOD_DEPTH_FT),
        "eta_high_h": _first(d_high, FLOOD_DEPTH_FT),
        "peak_in_h": int(np.nanargmax(mid)) if np.isfinite(mid).any() else None,
        "storm": None,
    }

    out["storm"] = _storm_signals(key, hour, lat, lon)
    out["rule_level"] = rule_level(out, evacuate_h, warn_h)
    return out


@lru_cache(maxsize=200_000)
def _flooding_now(key, hour, lat, lon):
    spill, ground = ground_at(key, lat, lon)
    return round(observed_depth(key, hour, lat, lon, spill, ground), 2)


@lru_cache(maxsize=200_000)
def _storm_signals(key, hour, lat, lon):
    """Where the storm is and will pass relative to the address (independent of the policy's knobs)."""
    st = world(key, hour)["storm"]
    if not (st and st["now"]):
        return None
    now = st["now"]
    dx, dy = _offset_km(lat, lon, now["lat"], now["lon"])
    # Closest approach along the forecast track over the next 72 h.
    best = (np.inf, None, None)
    peak_wind = 0.0
    for t in range(hour, hour + 73):
        s = state_at(st["track"], t)
        if s is None:
            continue
        d = float(np.hypot(*_offset_km(lat, lon, s["lat"], s["lon"])))
        if d < best[0]:
            best = (d, t - hour, s)
        if t <= hour + 48:
            peak_wind = max(peak_wind, wind_at(s, lat, lon)[0])
    return {
        "category": category(now["vmax_kt"]), "vmax_mph": round(now["vmax_kt"] * 1.15078),
        "distance_km": round(float(np.hypot(dx, dy))), "bearing": compass(np.degrees(np.arctan2(dx, dy)) % 360),
        "closest_km": None if best[1] is None else round(best[0]), "closest_in_h": best[1],
        "category_at_closest": None if best[2] is None else category(best[2]["vmax_kt"]),
        "peak_wind_mph_48h": round(peak_wind * 2.23694),
        "advisory_age_h": st["advisory_age_h"],
    }


def _blend_series(series, w):
    m = np.vstack(series)
    ok = np.isfinite(m)
    ww = np.where(ok, w[:, None], 0.0)
    with np.errstate(invalid="ignore"):
        return np.where(ok.any(0), (ww * np.nan_to_num(m)).sum(0) / ww.sum(0), np.nan)


def rule_level(s, evacuate_h=EVACUATE_H, warn_h=WARN_H):
    def within(eta, h):
        return eta is not None and eta <= h

    if s["flooding_now_ft"] >= FLOOD_DEPTH_FT or within(s["eta_h"], evacuate_h):
        return "evacuate"
    if within(s["eta_h"], warn_h):
        return "warn"
    st = s["storm"]
    if (st and st["category_at_closest"] is not None and st["category_at_closest"] >= 0 and st["closest_km"] <= WATCH_KM
            and st["closest_in_h"] <= WATCH_H and s["spill_ft"] is not None and s["spill_ft"] <= WATCH_SPILL_FT):
        return "watch"
    return "none"


def level_rank(level):
    return LEVELS.index(level)
