"""Small geodesy helpers (local equirectangular is fine at county scale)."""
from functools import lru_cache

import numpy as np

from .config import MODEL_OUT

KM_PER_DEG_LAT = 110.57


def km_per_deg_lon(lat):
    return 111.32 * np.cos(np.radians(lat))


def offsets_km(lat, lon, lats, lons):
    """East/north offsets (km) from (lat, lon) to arrays of points."""
    dx = (np.asarray(lons) - lon) * km_per_deg_lon(lat)
    dy = (np.asarray(lats) - lat) * KM_PER_DEG_LAT
    return dx, dy


def bearing_deg(dx, dy):
    """Compass bearing (0 = north, 90 = east) of an east/north offset."""
    return (np.degrees(np.arctan2(dx, dy)) + 360.0) % 360.0


def angle_diff(a, b):
    """Smallest absolute difference between two compass angles, 0–180."""
    return np.abs((np.asarray(a) - b + 180.0) % 360.0 - 180.0)


@lru_cache(maxsize=1)
def _burn_raster():
    import rasterio
    from rasterio.warp import transform

    src = rasterio.open(MODEL_OUT / "burn_probability.tif")
    return src, src.read(1), transform


def burn_probability(lat, lon):
    """10-year burn likelihood from the risk model at a point (None outside the box)."""
    src, arr, transform = _burn_raster()
    xs, ys = transform("EPSG:4326", src.crs, [lon], [lat])
    r, c = src.index(xs[0], ys[0])
    if 0 <= r < arr.shape[0] and 0 <= c < arr.shape[1] and np.isfinite(arr[r, c]):
        return round(float(arr[r, c]), 3)
    return None
