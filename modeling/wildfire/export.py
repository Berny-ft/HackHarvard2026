"""Write model outputs as GeoTIFF (analysis) + PNG overlay (web map)."""
import json

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.warp import Resampling, calculate_default_transform, reproject

from .config import OUT

# Transparent -> yellow -> orange -> red -> deep red, like the FireTrace layers.
CMAPS = {
    "fire": [
        (0.00, (255, 230, 120, 0)),
        (0.15, (255, 210, 90, 110)),
        (0.40, (250, 150, 50, 170)),
        (0.70, (225, 70, 35, 210)),
        (1.00, (140, 20, 20, 235)),
    ],
    "time": [  # early (yellow) -> late (deep red), opaque
        (0.00, (255, 225, 110, 220)),
        (0.50, (240, 120, 40, 220)),
        (1.00, (150, 25, 25, 220)),
    ],
}


def colorize(arr, vmin, vmax, cmap):
    stops = CMAPS[cmap]
    t = np.nan_to_num(np.clip((arr - vmin) / max(vmax - vmin, 1e-9), 0, 1))
    xs = [s[0] for s in stops]
    rgba = np.zeros((*arr.shape, 4), dtype=np.uint8)
    for ch in range(4):
        rgba[..., ch] = np.interp(t, xs, [s[1][ch] for s in stops]).astype(np.uint8)
    rgba[np.isnan(arr)] = 0
    return rgba


def to_web_mercator(arr, prof):
    dst_crs = "EPSG:3857"
    tr, w, h = calculate_default_transform(
        prof["crs"], dst_crs, prof["width"], prof["height"],
        *rasterio.transform.array_bounds(prof["height"], prof["width"], prof["transform"]))
    out = np.full((h, w), np.nan, dtype=np.float32)
    reproject(arr.astype(np.float32), out, src_transform=prof["transform"], src_crs=prof["crs"],
              dst_transform=tr, dst_crs=dst_crs, resampling=Resampling.nearest,
              src_nodata=np.nan, dst_nodata=np.nan)
    left, bottom, right, top = rasterio.transform.array_bounds(h, w, tr)
    to_ll = Transformer.from_crs(dst_crs, "EPSG:4326", always_xy=True)
    (lon0, lon1), (lat0, lat1) = to_ll.transform([left, right], [bottom, top])
    # Mapbox/MapLibre image-source corner order: TL, TR, BR, BL.
    corners = [[lon0, lat1], [lon1, lat1], [lon1, lat0], [lon0, lat0]]
    return out, corners


def save_overlay(arr, prof, name, vmin, vmax, cmap="fire"):
    OUT.mkdir(exist_ok=True)
    with rasterio.open(OUT / f"{name}.tif", "w", **{**prof, "dtype": "float32", "nodata": np.nan}) as dst:
        dst.write(arr.astype(np.float32), 1)
    merc, corners = to_web_mercator(arr, prof)
    Image.fromarray(colorize(merc, vmin, vmax, cmap), "RGBA").save(OUT / f"{name}.png", optimize=True)
    (OUT / f"{name}.json").write_text(json.dumps(
        {"image": f"{name}.png", "coordinates": corners, "vmin": vmin, "vmax": vmax}, indent=1))


def save_json(name, obj):
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=1, default=str))
