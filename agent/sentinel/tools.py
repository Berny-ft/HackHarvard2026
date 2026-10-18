"""Tools the LLM policy can call before it decides.

Each tool reads the same Snapshot the signals came from, so in a replay it
only sees what was known at that hour. Answers are small dicts the model can
read; nothing here sends an alert or changes state.
"""
import json

import numpy as np

from .config import ACTIVE_WINDOW_H, MAX_LENGTH_TO_BREADTH, STALE_CREDIT_H
from .geo import angle_diff, bearing_deg, offsets_km
from .signals import directional_ros, effective_wind_mph, head_ros_kmh

COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

SPECS = [
    {"type": "function", "function": {
        "name": "fire_activity",
        "description": "How much fire is near the location and how fresh it is: satellite-confirmed vs only "
                       "reported points, and how many new satellite detections arrived in the last 3, 6 and 12 "
                       "hours. Use it to judge whether a report is real and whether the fire is growing.",
        "parameters": {"type": "object", "properties": {
            "radius_km": {"type": "number", "description": "Search radius, 1-50 km. Default 25."}}}}},
    {"type": "function", "function": {
        "name": "nearest_fire_points",
        "description": "The closest fire points to the location: distance, direction to look, hours since seen, "
                       "source (satellite, reported, perimeter) and whether the wind blows from that point "
                       "toward the location.",
        "parameters": {"type": "object", "properties": {
            "n": {"type": "integer", "description": "How many points, 1-10. Default 5."}}}}},
    {"type": "function", "function": {
        "name": "eta_at_wind",
        "description": "Modelled time for the fire to reach the location if the wind were this strong, from the "
                       "current wind direction. Use it to test the outlook's peak gusts.",
        "parameters": {"type": "object", "properties": {
            "wind_mph": {"type": "number"}, "gust_mph": {"type": "number"}},
            "required": ["wind_mph", "gust_mph"]}}},
]


def _geometry(snap, lat, lon):
    f = snap.fires
    dx, dy = offsets_km(lat, lon, f.lat, f.lon)
    dist = np.hypot(dx, dy)
    from_fire = bearing_deg(-dx, -dy)                     # bearing of the location as seen from each point
    wind_to = (snap.weather(lat, lon)["wind_dir_deg"] + 180.0) % 360.0
    return f, dist, from_fire, angle_diff(from_fire, wind_to)


def fire_activity(snap, lat, lon, radius_km=25):
    radius_km = float(np.clip(radius_km or 25, 1, 50))
    if len(snap.fires) == 0:
        return {"radius_km": radius_km, "points": 0}
    f, dist, _, _ = _geometry(snap, lat, lon)
    near = dist <= radius_km
    kind = np.array(f.kind)
    sat = near & (kind == "satellite")
    confirmed = near & (kind != "reported")
    reported = near & (kind == "reported")
    return {
        "radius_km": radius_km,
        "satellite_points": int(sat.sum()),
        "reported_only_points": int(reported.sum()),
        "new_satellite_last_3h": int((sat & (f.age_h <= 3)).sum()),
        "new_satellite_last_6h": int((sat & (f.age_h <= 6)).sum()),
        "new_satellite_last_12h": int((sat & (f.age_h <= 12)).sum()),
        "freshest_satellite_age_h": round(float(f.age_h[sat].min()), 1) if sat.any() else None,
        "nearest_confirmed_km": round(float(dist[confirmed].min()), 2) if confirmed.any() else None,
        "nearest_reported_only_km": round(float(dist[reported].min()), 2) if reported.any() else None,
    }


def nearest_fire_points(snap, lat, lon, n=5):
    n = int(np.clip(n or 5, 1, 10))
    if len(snap.fires) == 0:
        return {"points": []}
    f, dist, from_fire, theta = _geometry(snap, lat, lon)
    out = []
    for i in np.argsort(dist)[:n]:
        out.append({"km": round(float(dist[i]), 2),
                    "look_toward": COMPASS[int(((from_fire[i] + 180) % 360 + 22.5) // 45) % 8],
                    "age_h": round(float(f.age_h[i]), 1), "source": f.kind[i],
                    "wind_blows_from_it_toward_you": bool(theta[i] <= 45)})
    return {"points": out}


def eta_at_wind(snap, lat, lon, wind_mph, gust_mph):
    wx = dict(snap.weather(lat, lon), wind_mph=float(wind_mph), gust_mph=max(float(gust_mph), float(wind_mph)))
    f = snap.fires
    active = f.age_h <= ACTIVE_WINDOW_H if len(f) else np.array([], bool)
    if not active.any():
        return {"eta_h": None, "note": "no active fire points"}
    _, dist, _, theta = _geometry(snap, lat, lon)
    ros = directional_ros(head_ros_kmh(wx), effective_wind_mph(wx), theta, MAX_LENGTH_TO_BREADTH)
    eta = np.maximum(0.0, dist / ros - np.minimum(f.age_h, STALE_CREDIT_H))
    return {"wind_mph": wx["wind_mph"], "gust_mph": wx["gust_mph"],
            "head_spread_kmh": round(head_ros_kmh(wx), 2), "eta_h": round(float(eta[active].min()), 2)}


TOOLS = {"fire_activity": fire_activity, "nearest_fire_points": nearest_fire_points, "eta_at_wind": eta_at_wind}


def run(name, arguments, snap, lat, lon):
    """Run one tool call; errors go back to the model as text rather than raising."""
    try:
        args = json.loads(arguments or "{}")
        return json.dumps(TOOLS[name](snap, lat, lon, **args), default=str)
    except Exception as e:  # unknown tool, bad arguments
        return json.dumps({"error": f"{type(e).__name__}: {e}"})
