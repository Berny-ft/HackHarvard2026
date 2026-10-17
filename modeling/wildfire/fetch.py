"""Download raw inputs: fire perimeters, terrain, fuels, damage points, weather.

Every source here is public and needs no API key.
"""
import io
import json
import time

import numpy as np
import pandas as pd
import rasterio
import requests
from pyproj import Transformer
from rasterio.transform import from_origin

from .config import BBOX_LONLAT, CRS, DATA, REGION_RES

FRAP_URL = (
    "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/"
    "California_Historic_Fire_Perimeters/FeatureServer/0/query"
)
DINS_URL = (
    "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/"
    "DINS_2025_{name}_Public_View/FeatureServer/0/query"
)
DEM_URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
LANDFIRE_URL = "https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2024/LF2024_{layer}_CONUS/ImageServer/exportImage"
METEO_URL = "https://archive-api.open-meteo.com/v1/archive"

_to_albers = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
session = requests.Session()


def _get(url, params, tries=4, timeout=180):
    for i in range(tries):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code == 429:  # Open-Meteo minutely limit
                time.sleep(65)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(2 ** i)


# ---------------------------------------------------------------- grids

def snap_bounds(xmin, ymin, xmax, ymax, res):
    return (
        np.floor(xmin / res) * res,
        np.floor(ymin / res) * res,
        np.ceil(xmax / res) * res,
        np.ceil(ymax / res) * res,
    )


def region_bounds(res=REGION_RES):
    lon0, lat0, lon1, lat1 = BBOX_LONLAT
    xs, ys = _to_albers.transform([lon0, lon1, lon0, lon1], [lat0, lat0, lat1, lat1])
    return snap_bounds(min(xs), min(ys), max(xs), max(ys), res)


def bounds_around(lon, lat, half_width_m, res):
    x, y = _to_albers.transform(lon, lat)
    return snap_bounds(x - half_width_m, y - half_width_m, x + half_width_m, y + half_width_m, res)


def grid_profile(bounds, res):
    xmin, ymin, xmax, ymax = bounds
    width, height = int(round((xmax - xmin) / res)), int(round((ymax - ymin) / res))
    return {
        "driver": "GTiff",
        "crs": CRS,
        "transform": from_origin(xmin, ymax, res, res),
        "width": width,
        "height": height,
        "count": 1,
        "compress": "deflate",
    }


# ---------------------------------------------------------------- rasters

def _export_image(url, bounds, res, interpolation, pixel_type=None):
    """Fetch an ArcGIS ImageServer export on our exact grid, tiling if needed."""
    prof = grid_profile(bounds, res)
    W, H = prof["width"], prof["height"]
    tile = 4000
    out = None
    xmin, _, _, ymax = bounds
    for r0 in range(0, H, tile):
        for c0 in range(0, W, tile):
            h, w = min(tile, H - r0), min(tile, W - c0)
            bx0, by1 = xmin + c0 * res, ymax - r0 * res
            params = {
                "bbox": f"{bx0},{by1 - h * res},{bx0 + w * res},{by1}",
                "bboxSR": 3310,
                "imageSR": 3310,
                "size": f"{w},{h}",
                "format": "tiff",
                "interpolation": interpolation,
                "f": "image",
            }
            if pixel_type:
                params["pixelType"] = pixel_type
            r = _get(url, params)
            if "tiff" not in r.headers.get("content-type", ""):
                raise RuntimeError(f"{url} returned {r.headers.get('content-type')}: {r.text[:300]}")
            with rasterio.open(io.BytesIO(r.content)) as src:
                arr = src.read(1)
                nodata = src.nodata
            if out is None:
                out = np.zeros((H, W), dtype=arr.dtype)
            if nodata is not None and np.issubdtype(arr.dtype, np.floating):
                arr = np.where(arr == nodata, np.nan, arr)
            out[r0:r0 + h, c0:c0 + w] = arr[:h, :w]
    return out, prof


def save_raster(path, arr, prof):
    prof = {**prof, "dtype": arr.dtype.name}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr, 1)


def fetch_terrain_and_fuels(bounds, res, prefix):
    """Writes {prefix}_dem.tif, _fbfm40.tif, _cc.tif (canopy cover %)."""
    jobs = [
        ("dem", DEM_URL, "RSP_BilinearInterpolation", "F32"),
        ("fbfm40", LANDFIRE_URL.format(layer="FBFM40"), "RSP_NearestNeighbor", None),
        ("cc", LANDFIRE_URL.format(layer="CC"), "RSP_NearestNeighbor", None),
    ]
    for key, url, interp, ptype in jobs:
        path = DATA / f"{prefix}_{key}.tif"
        if path.exists():
            print(f"  have {path.name}")
            continue
        print(f"  fetching {path.name} ...")
        arr, prof = _export_image(url, bounds, res, interp, ptype)
        save_raster(path, arr, prof)


# ---------------------------------------------------------------- vectors

def _paged_geojson(url, where, out_fields="*", page=2000, extra=None):
    feats, offset = [], 0
    lon0, lat0, lon1, lat1 = BBOX_LONLAT
    while True:
        params = {
            "where": where,
            "geometry": f"{lon0},{lat0},{lon1},{lat1}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": out_fields,
            "outSR": 4326,
            "resultOffset": offset,
            "resultRecordCount": page,
            "f": "geojson",
            **(extra or {}),
        }
        batch = _get(url, params).json().get("features", [])
        feats += batch
        if len(batch) < page:
            break
        offset += page
    return {"type": "FeatureCollection", "features": feats}


def fetch_perimeters():
    path = DATA / "frap_perimeters.geojson"
    if path.exists():
        print(f"  have {path.name}")
        return
    print("  fetching CAL FIRE FRAP perimeters ...")
    fc = _paged_geojson(FRAP_URL, "1=1", extra={"geometryPrecision": 5})
    path.write_text(json.dumps(fc))
    print(f"  {len(fc['features'])} perimeters")


def fetch_dins(name):
    path = DATA / f"dins_{name.lower()}_2025.geojson"
    if path.exists():
        print(f"  have {path.name}")
        return
    print(f"  fetching DINS damage points for {name} ...")
    fc = _paged_geojson(DINS_URL.format(name=name), "1=1")
    path.write_text(json.dumps(fc))
    print(f"  {len(fc['features'])} structures")


# ---------------------------------------------------------------- weather

DAILY_VARS = [
    "temperature_2m_max",
    "relative_humidity_2m_min",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "precipitation_sum",
]
HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "precipitation",
]


def weather_points(n=3):
    lon0, lat0, lon1, lat1 = BBOX_LONLAT
    lons = np.linspace(lon0, lon1, n + 2)[1:-1]
    lats = np.linspace(lat0, lat1, n + 2)[1:-1]
    return [(round(x, 3), round(y, 3)) for y in lats for x in lons]


def fetch_daily_weather(start="2000-01-01", end="2025-12-31"):
    """Daily weather at a 3x3 lattice of points, for fire-weather patterns."""
    path = DATA / "weather_daily.parquet"
    if path.exists():
        print(f"  have {path.name}")
        return
    frames = []
    for i, (lon, lat) in enumerate(weather_points()):
        part = DATA / f".weather_daily_{i}.parquet"
        if part.exists():
            frames.append(pd.read_parquet(part))
            continue
        print(f"  daily weather point {i + 1}/9 ...")
        d = _get(METEO_URL, {
            "latitude": lat, "longitude": lon,
            "start_date": start, "end_date": end,
            "daily": ",".join(DAILY_VARS),
            "timezone": "America/Los_Angeles",
            "wind_speed_unit": "mph",
            "temperature_unit": "fahrenheit",
        }).json()["daily"]
        df = pd.DataFrame(d)
        df["point"], df["lon"], df["lat"] = i, lon, lat
        df.to_parquet(part)
        frames.append(df)
        time.sleep(8)
    df = pd.concat(frames)
    df["time"] = pd.to_datetime(df["time"])
    df.to_parquet(path)
    for part in DATA.glob(".weather_daily_*.parquet"):
        part.unlink()


def fetch_hourly_weather(key, lon, lat, start, end):
    path = DATA / f"weather_{key}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    d = _get(METEO_URL, {
        "latitude": lat, "longitude": lon,
        "start_date": start, "end_date": end,
        "hourly": ",".join(HOURLY_VARS),
        "timezone": "America/Los_Angeles",
        "wind_speed_unit": "mph",
        "temperature_unit": "fahrenheit",
    }).json()["hourly"]
    df = pd.DataFrame(d)
    df["time"] = pd.to_datetime(df["time"])
    df.to_parquet(path)
    return df


def run():
    DATA.mkdir(exist_ok=True)
    print("[fetch] fire perimeters")
    fetch_perimeters()
    print("[fetch] damage inspection (DINS)")
    for name in ("Eaton", "Palisades"):
        try:
            fetch_dins(name)
        except Exception as e:  # damage data is nice-to-have
            print(f"  skipped {name}: {e}")
    print("[fetch] region terrain + fuels")
    fetch_terrain_and_fuels(region_bounds(), REGION_RES, "region")
    print("[fetch] daily weather")
    fetch_daily_weather()
