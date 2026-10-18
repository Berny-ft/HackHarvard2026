"""Safest direction to leave in: a minimal take on the escape router (origin/direction, routing/engine.py).

The full router searches the road graph; this only picks the compass heading. For each of 16 headings it walks
out from the home (1-8 km) and compares, at every step, when the fire would get there (the same wind-driven
ellipse as signals.assess) with when a car leaving now would. The heading with the most time to spare at its
tightest point wins: away from the fire and out of the wind's path. No roads, so the texts say "head <dir>",
not a route.
"""
import numpy as np

from .config import ACTIVE_WINDOW_H, MAX_LENGTH_TO_BREADTH, ROS_KMH_PER_MPH, STALE_CREDIT_H
from .geo import KM_PER_DEG_LAT, angle_diff, bearing_deg, km_per_deg_lon
from .signals import directional_ros, effective_wind_mph, head_ros_kmh

HEADINGS = np.arange(0, 360, 22.5)
STEPS_KM = np.array([0.5, 1, 2, 3, 4, 6, 8])
SLACK_H = 6.0             # spare time beyond this doesn't make a heading any safer
DRIVE_KMH = 25.0          # surface streets in an evacuation, not the speed limit
WORDS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]


def compass_word(deg):
    return WORDS[int(round(deg / 45.0)) % 8]


def safest_heading(snap, lat, lon):
    """-> {"bearing", "direction", "spare_h"} or None when no active fire is near enough to matter."""
    f = snap.fires
    active = f.age_h <= ACTIVE_WINDOW_H
    if not active.any():
        return None
    wx = snap.weather(lat, lon)
    head, wind = head_ros_kmh(wx, ROS_KMH_PER_MPH), effective_wind_mph(wx)
    wind_to = (wx["wind_dir_deg"] + 180.0) % 360.0
    flat, flon, age = f.lat[active], f.lon[active], np.minimum(f.age_h[active], STALE_CREDIT_H)

    rad = np.radians(HEADINGS)[:, None]
    py = lat + np.cos(rad) * STEPS_KM / KM_PER_DEG_LAT              # (heading, step) points along each ray
    px = lon + np.sin(rad) * STEPS_KM / km_per_deg_lon(lat)
    dx = (px[..., None] - flon) * km_per_deg_lon(lat)                # fire -> point, (heading, step, fire)
    dy = (py[..., None] - flat) * KM_PER_DEG_LAT
    theta = angle_diff(bearing_deg(dx, dy), wind_to)
    ros = directional_ros(head, wind, theta, MAX_LENGTH_TO_BREADTH)
    fire_h = np.maximum(0.0, np.hypot(dx, dy) / ros - age).min(axis=2)
    spare = (fire_h - STEPS_KM / DRIVE_KMH).min(axis=1)              # tightest point on each heading
    # Past a few hours of slack every heading is "safe"; then prefer straight away from the nearest fire.
    near = int(np.argmin(np.hypot((flon - lon) * km_per_deg_lon(lat), (flat - lat) * KM_PER_DEG_LAT)))
    away = (bearing_deg((flon[near] - lon) * km_per_deg_lon(lat), (flat[near] - lat) * KM_PER_DEG_LAT) + 180) % 360
    best = int(np.argmax(np.minimum(spare, SLACK_H) + angle_diff(HEADINGS, away) * -1e-3))
    return {"bearing": float(HEADINGS[best]), "direction": compass_word(HEADINGS[best]),
            "spare_h": round(float(spare[best]), 2)}
