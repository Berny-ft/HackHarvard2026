"""Turn a Snapshot into explainable threat signals for one location.

Spread is an elliptical head-fire model: rate of spread scales with wind
(sustained + half the gust excess, as in the Rothermel calibration) and is
damped by 1-h dead fuel moisture. The ellipse's length-to-breadth ratio grows
with wind, so a home 5 km downwind can be hours closer than one 2 km upwind.

Fire points are only seen at satellite overpasses, so the front has kept moving
since each was observed: ETA from now is travel time minus the point's age,
capped at stale_h (most old points are burned-out interior, not front).
"""
import numpy as np

from .config import (ACTIVE_WINDOW_H, LEVELS, MAX_LENGTH_TO_BREADTH, ROS_KMH_PER_MPH, STALE_CREDIT_H,
                     THRESHOLDS)
from .geo import angle_diff, bearing_deg, burn_probability, offsets_km


def effective_wind_mph(wx):
    return wx["wind_mph"] + 0.5 * max(0.0, wx["gust_mph"] - wx["wind_mph"])


def head_ros_kmh(wx, k=ROS_KMH_PER_MPH):
    damp = float(np.clip(1.0 - (wx["fuel_moisture_1h_pct"] - 4.0) / 16.0, 0.15, 1.0))
    return max(0.05, k * effective_wind_mph(wx) * damp)


def directional_ros(head, wind_mph, theta_deg, max_lb=MAX_LENGTH_TO_BREADTH):
    """Ellipse ROS at angle theta from the head direction."""
    lb = min(max_lb, 1.0 + 0.1 * wind_mph)
    e = np.sqrt(1.0 - 1.0 / lb ** 2)
    return head * (1.0 - e) / (1.0 - e * np.cos(np.radians(theta_deg)))


def rule_level(eta_h, dist_km, pre_fire_watch=False, evac_km=None):
    for lvl in ("evacuate", "warn", "watch"):
        max_eta, max_d = THRESHOLDS[lvl]
        if lvl == "evacuate" and evac_km is not None:
            max_d = evac_km
        if (eta_h is not None and eta_h <= max_eta) or (dist_km is not None and dist_km <= max_d):
            return lvl
    return "watch" if pre_fire_watch else "none"


def assess(snap, lat, lon, k=ROS_KMH_PER_MPH, max_lb=MAX_LENGTH_TO_BREADTH, stale_h=STALE_CREDIT_H,
           evac_km=None, with_burn_prob=True):
    wx = snap.weather(lat, lon)
    look = snap.outlook(lat, lon)
    flag = snap.red_flag(lat, lon)
    bp = burn_probability(lat, lon) if with_burn_prob else None
    out = {
        "lat": lat, "lon": lon, "as_of": snap.as_of, "source": snap.source,
        "weather_now": wx, "outlook_12h": look, "red_flag": flag, "burn_probability_10y": bp,
        "nearest_fire_km": None, "nearest_fire_name": None, "nearest_fire_age_h": None, "nearest_fire_confirmed": None,
        "fire_bearing_deg": None, "downwind": None, "wind_alignment_deg": None,
        "eta_h": None, "head_ros_kmh": round(head_ros_kmh(wx, k), 2),
        "active_points_within_25km": 0,
    }

    f = snap.fires
    pre_fire = bool(flag) and look["peak_santa_ana_level"] >= 1 and (bp or 0) >= 0.3
    if len(f) == 0:
        out["rule_level"] = rule_level(None, None, pre_fire)
        return out

    dx, dy = offsets_km(lat, lon, f.lat, f.lon)
    dist = np.hypot(dx, dy)
    i = int(np.argmin(dist))
    # Bearing of this location as seen from each fire point.
    from_fire = bearing_deg(-dx, -dy)
    wind_to = (wx["wind_dir_deg"] + 180.0) % 360.0
    theta = angle_diff(from_fire, wind_to)

    active = f.age_h <= ACTIVE_WINDOW_H
    confirmed = np.array([k_ != "reported" for k_ in f.kind])
    out.update({
        "nearest_fire_km": round(float(dist[i]), 2),
        "nearest_fire_name": f.name[i],
        "nearest_fire_age_h": round(float(f.age_h[i]), 1),
        "nearest_fire_confirmed": bool(confirmed[i]),
        "fire_bearing_deg": round(float((from_fire[i] + 180) % 360)),   # direction to look
        "active_points_within_25km": int((active & (dist <= 25)).sum()),
    })
    ros = directional_ros(head_ros_kmh(wx, k), effective_wind_mph(wx), theta, max_lb)
    eta = np.maximum(0.0, dist / ros - np.minimum(f.age_h, stale_h))

    # A 911/NIFC report nobody has confirmed (satellite or mapped perimeter) can
    # raise the level to warn but not evacuate: most reported starts stay small.
    level = rule_level(None, None, pre_fire)
    for mask, cap in ((active & confirmed, "evacuate"), (active & ~confirmed, "warn")):
        if not mask.any():
            continue
        j = np.flatnonzero(mask)[int(np.argmin(eta[mask]))]
        lvl = rule_level(float(eta[j]), float(dist[mask].min()), pre_fire, evac_km)
        lvl = LEVELS[min(level_rank(lvl), level_rank(cap))]
        if level_rank(lvl) > level_rank(level) or out["eta_h"] is None:
            out.update({"eta_h": round(float(eta[j]), 2), "wind_alignment_deg": round(float(theta[j])),
                        "downwind": bool(theta[j] <= 45)})
        level = max(level, lvl, key=level_rank)
    out["rule_level"] = level
    return out


def level_rank(level):
    return LEVELS.index(level)
