"""Satellite fire detections (NASA FIRMS archive) for scoring spread timing.

Each detection says "this ~375 m pixel was burning at time t". The spread
model should have reached that pixel by then. Needs FIRMS_MAP_KEY in .env.
"""
import io
import os

import numpy as np
import pandas as pd
import rasterio
import requests
from pyproj import Transformer

from . import live  # noqa: F401  (loads modeling/.env)
from .config import CRS, DATA

FIRMS = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/{bbox}/{days}/{date}"
# Standard-processing archives; pixel footprint at nadir (m).
SOURCES = {"VIIRS_SNPP_SP": 375, "VIIRS_NOAA20_SP": 375, "VIIRS_NOAA21_SP": 375, "MODIS_SP": 1000}


def fetch_detections(key, lon, lat, half_width_km, start, hours):
    """Detections around an incident from `start` (local) for `hours`, cached."""
    path = DATA / f"firms_{key}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    d = half_width_km / 100  # ~degrees, generous
    bbox = f"{lon - d:.3f},{lat - d:.3f},{lon + d:.3f},{lat + d:.3f}"
    t0 = pd.Timestamp(start).tz_localize("America/Los_Angeles")
    t1 = t0 + pd.Timedelta(hours=hours)
    frames = []
    for src, pixel_m in SOURCES.items():
        day = t0.tz_convert("UTC").normalize()
        while day <= t1.tz_convert("UTC"):
            n = min(10, (t1.tz_convert("UTC") - day).days + 1)
            text = requests.get(FIRMS.format(key=os.environ["FIRMS_MAP_KEY"], src=src, bbox=bbox, days=n,
                                             date=day.strftime("%Y-%m-%d")), timeout=60).text
            if text.startswith("latitude"):
                df = pd.read_csv(io.StringIO(text))
                df["source"], df["pixel_m"] = src, pixel_m
                frames.append(df)
            day += pd.Timedelta(days=n)
    df = pd.concat(frames, ignore_index=True)
    t = pd.to_datetime(df["acq_date"] + " " + df["acq_time"].astype(str).str.zfill(4), format="%Y-%m-%d %H%M", utc=True)
    df["time_local"] = t.dt.tz_convert("America/Los_Angeles").dt.tz_localize(None)
    df["hour"] = (t - t0.tz_convert("UTC")).dt.total_seconds() / 3600
    df = df[(df["hour"] >= 0) & (df["hour"] <= hours)]
    df = df[["latitude", "longitude", "time_local", "hour", "frp", "confidence", "source", "pixel_m"]].copy()
    df["confidence"] = df["confidence"].astype(str)  # VIIRS: l/n/h, MODIS: 0-100
    df.to_parquet(path)
    return df


def detection_cells(det, prof, res, real_mask, buffer_cells=3):
    """Map detections onto the grid; keep those on or next to the real burn scar."""
    to = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
    x, y = to.transform(det["longitude"].to_numpy(), det["latitude"].to_numpy())
    rows, cols = rasterio.transform.rowcol(prof["transform"], x, y)
    rows, cols = np.asarray(rows), np.asarray(cols)
    H, W = real_mask.shape
    ok = (rows >= 0) & (rows < H) & (cols >= 0) & (cols < W)
    near = np.zeros(len(det), bool)
    for dr in range(-buffer_cells, buffer_cells + 1):
        for dc in range(-buffer_cells, buffer_cells + 1):
            rr, cc = (rows + dr).clip(0, H - 1), (cols + dc).clip(0, W - 1)
            near |= ok & real_mask[rr, cc]
    keep = det[near].copy()
    keep["row"], keep["col"] = rows[near], cols[near]
    keep["half"] = np.maximum(1, (keep["pixel_m"] / 2 / res).round().astype(int))
    return keep


def timing_score(arrival, cells, tolerance_h=1.0):
    """How often the simulated fire had reached each detected pixel in time.

    For each detection, the earliest simulated arrival anywhere in its pixel
    footprint is compared to the satellite time. Returns the share that was
    on time (within `tolerance_h`) and the median lateness in hours.
    """
    if cells is None or not len(cells):
        return {"on_time": None, "median_late_h": None, "n_detections": 0}
    H, W = arrival.shape
    late = np.empty(len(cells))
    for i, (r, c, h, t) in enumerate(zip(cells["row"], cells["col"], cells["half"], cells["hour"])):
        win = arrival[max(0, r - h):min(H, r + h + 1), max(0, c - h):min(W, c + h + 1)]
        late[i] = (win.min() / 60 if win.size else np.inf) - t
    finite = np.where(np.isfinite(late), late, 999)
    return {
        "on_time": float((finite <= tolerance_h).mean()),
        "median_late_h": float(np.median(finite)),
        "n_detections": int(len(cells)),
    }


def observed_growth(cells, shape, hours):
    """Cumulative detected footprint (cells) by hour: a satellite-based area curve."""
    seen = np.zeros(shape, bool)
    by_hour = np.zeros(hours + 1)
    order = cells.sort_values("hour")
    H, W = shape
    j = 0
    rows = list(order.itertuples())
    for hr in range(hours + 1):
        while j < len(rows) and rows[j].hour <= hr:
            r, c, h = rows[j].row, rows[j].col, rows[j].half
            seen[max(0, r - h):min(H, r + h + 1), max(0, c - h):min(W, c + h + 1)] = True
            j += 1
        by_hour[hr] = seen.sum()
    return by_hour
