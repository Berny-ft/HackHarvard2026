"""Small geodesy helpers (local equirectangular is fine at county scale)."""
import math

import numpy as np

KM_PER_DEG_LAT = 110.57
COMPASS8 = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]


def km_per_deg_lon(lat):
    return 111.32 * np.cos(np.radians(lat))


def local_km(lon, lat, lon0, lat0):
    """East/north km of points from a reference point."""
    return (np.asarray(lon) - lon0) * km_per_deg_lon(lat0), (np.asarray(lat) - lat0) * KM_PER_DEG_LAT


def bearing_deg(dx, dy):
    """Compass bearing (0 = north, 90 = east) of an east/north offset."""
    return (np.degrees(np.arctan2(dx, dy)) + 360.0) % 360.0


def angle_diff(a, b):
    """Smallest absolute difference between two compass angles, 0-180."""
    return np.abs((np.asarray(a) - b + 180.0) % 360.0 - 180.0)


def km_between(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def compass(deg):
    return COMPASS8[round((deg or 0) / 45) % 8]
