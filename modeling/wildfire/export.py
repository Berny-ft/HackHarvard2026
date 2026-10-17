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


def save_arrival_texture(hrs, prof, name, hours, res_m):
    """Pack fire arrival time into a PNG the web app decodes on the GPU.

    R,G: arrival hour as 16 bits over [0, hours] (65535 = never burns within the replay).
    B:   spread rate, 0-255 on a sqrt scale up to 3 km/h (head fire vs backing fire).
    A:   255 so browsers never premultiply the data.
    """
    # Rate of spread from the arrival-time gradient on the native (metric) grid, in km/h.
    burns = np.isfinite(hrs) & (hrs <= hours)
    gy, gx = np.gradient(np.where(burns, hrs, hours * 1.5), res_m)
    grad = np.hypot(gx, gy)
    ros = np.where(burns & (grad > 0), 1e-3 / np.maximum(grad, 1e-9), 0)
    merc, corners = to_web_mercator(np.where(burns, hrs, np.nan), prof)
    ros, _ = to_web_mercator(ros, prof)
    burns = np.isfinite(merc)
    q = np.where(burns, np.round(np.clip(merc, 0, hours) / hours * 65534), 65535).astype(np.uint16)
    ros = np.nan_to_num(ros)
    b = np.round(np.sqrt(np.clip(ros / 3.0, 0, 1)) * 255).astype(np.uint8)
    rgba = np.stack([(q >> 8).astype(np.uint8), (q & 255).astype(np.uint8), b,
                     np.full(q.shape, 255, np.uint8)], axis=-1)
    Image.fromarray(rgba, "RGBA").save(OUT / f"{name}.png", optimize=True)
    (OUT / f"{name}.json").write_text(json.dumps(
        {"image": f"{name}.png", "coordinates": corners, "hours": hours,
         "width": int(rgba.shape[1]), "height": int(rgba.shape[0])}, indent=1))


def save_json(name, obj):
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=1, default=str))
