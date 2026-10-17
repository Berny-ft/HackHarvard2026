"""Download raw inputs for each storm. Every source is public and needs no key.

  NHC ATCF archive   best track (b-deck) and every official forecast (a-deck OFCL)
  NOAA CO-OPS        hourly observed water level and tide predictions, NAVD88 ft
  USGS 3DEP          ground elevation on a Web Mercator grid
  Open-Meteo         hourly wind, gusts, rain and pressure over the box
"""
import gzip
import io
import json
import time
from datetime import datetime, timedelta

import numpy as np
import rasterio
import requests
from pyproj import Transformer

from .config import DATA, GAUGES, MAX_PX, MERC_RES, STORMS

ATCF = "https://ftp.nhc.noaa.gov/atcf/archive/{year}/{deck}{id}.dat.gz"
COOPS = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
DEM_URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
M_TO_FT = 3.28084

session = requests.Session()
session.headers["User-Agent"] = "hackharvard-surgetrace (hackathon project)"
_to_merc = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


def _get(url, params=None, tries=4, timeout=180):
    for i in range(tries):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(65)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(2 ** i)


def _cached(path, fn):
    if path.exists():
        return json.loads(path.read_text())
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = fn()
    path.write_text(json.dumps(obj))
    return obj


# ---------------------------------------------------------------- NHC ATCF

def _latlon(s):
    v = int(s[:-1]) / 10
    return -v if s[-1] in "SW" else v


def _num(s):
    s = s.strip()
    return int(s) if s.lstrip("-").isdigit() else 0


def parse_deck(text, tech=None):
    """ATCF a/b-deck rows -> {(dtg, tau): record}, merging the 34/50/64 kt radii lines."""
    out = {}
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 12 or (tech and f[4] != tech):
            continue
        key = (f[2], int(f[5]))
        r = out.setdefault(key, {
            "dtg": f[2], "tau": int(f[5]), "lat": _latlon(f[6]), "lon": _latlon(f[7]),
            "vmax_kt": _num(f[8]), "pmin_mb": _num(f[9]) or None, "type": f[10],
            "rmw_nm": (_num(f[19]) or None) if len(f) > 19 else None, "radii": {},
        })
        if len(f) > 16 and f[11] in ("34", "50", "64") and any(_num(x) for x in f[13:17]):
            r["radii"][f[11]] = [_num(x) for x in f[13:17]]   # NE, SE, SW, NW (nm)
        if r["rmw_nm"] is None and len(f) > 19 and _num(f[19]):
            r["rmw_nm"] = _num(f[19])
        if r["pmin_mb"] is None and _num(f[9]):
            r["pmin_mb"] = _num(f[9])
    return out


def fetch_atcf(key):
    s = STORMS[key]
    sid = s["atcf"]

    def get(deck):
        r = _get(ATCF.format(year=s["year"], deck=deck, id=sid))
        return gzip.decompress(r.content).decode("latin-1")

    def build():
        best = parse_deck(get("b"))
        fc = parse_deck(get("a"), tech="OFCL")
        track = sorted((v for v in best.values() if v["tau"] == 0), key=lambda r: r["dtg"])
        advisories = {}
        for v in fc.values():
            advisories.setdefault(v["dtg"], []).append(v)
        adv = [{"dtg": d, "points": sorted(p, key=lambda r: r["tau"])} for d, p in sorted(advisories.items())]
        return {"best_track": track, "advisories": adv}

    return _cached(DATA / key / "atcf.json", build)


# ---------------------------------------------------------------- CO-OPS

def _coops(station, product, begin, end):
    params = {"begin_date": begin, "end_date": end, "station": station, "product": product, "datum": "NAVD",
              "units": "english", "time_zone": "gmt", "format": "json", "application": "hackharvard-surgetrace"}
    if product == "predictions":
        params["interval"] = "h"
    j = _get(COOPS, params, timeout=90).json()
    rows = j.get("data") or j.get("predictions") or []
    return {r["t"]: (float(r["v"]) if r["v"] not in ("", None) else None) for r in rows}


def fetch_gauges(key):
    s = STORMS[key]
    t0 = datetime.fromisoformat(s["start"])
    t1 = t0 + timedelta(hours=s["hours"] + 24)
    begin, end = t0.strftime("%Y%m%d %H:%M"), t1.strftime("%Y%m%d %H:%M")

    def build():
        out = {}
        for gid in s["gauges"]:
            obs, pred = _coops(gid, "hourly_height", begin, end), _coops(gid, "predictions", begin, end)
            times = [(t0 + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M") for h in range(s["hours"] + 1)]
            out[gid] = {**GAUGES[gid], "id": gid, "observed_ft": [obs.get(t) for t in times],
                        "tide_ft": [pred.get(t) for t in times]}
        return out

    return _cached(DATA / key / "gauges.json", build)


# ---------------------------------------------------------------- terrain

def merc_grid(bbox, res=MERC_RES, max_px=MAX_PX):
    lon0, lat0, lon1, lat1 = bbox
    (x0, x1), (y0, y1) = _to_merc.transform([lon0, lon1], [lat0, lat1])
    res = max(res, (x1 - x0) / max_px, (y1 - y0) / max_px)
    W, H = int(np.ceil((x1 - x0) / res)), int(np.ceil((y1 - y0) / res))
    return {"x0": x0, "y1": y0 + H * res, "res": res, "W": W, "H": H}


def fetch_dem(key):
    """3DEP bare-earth elevation (NAVD88), resampled to our Mercator grid, in feet."""
    path = DATA / key / "dem_ft.npy"
    g = merc_grid(STORMS[key]["bbox"])
    if path.exists():
        return np.load(path), g
    tile = 2000
    out = np.full((g["H"], g["W"]), np.nan, np.float32)
    for r0 in range(0, g["H"], tile):
        for c0 in range(0, g["W"], tile):
            h, w = min(tile, g["H"] - r0), min(tile, g["W"] - c0)
            bx0, by1 = g["x0"] + c0 * g["res"], g["y1"] - r0 * g["res"]
            r = _get(DEM_URL, {"bbox": f"{bx0},{by1 - h * g['res']},{bx0 + w * g['res']},{by1}", "bboxSR": 3857,
                               "imageSR": 3857, "size": f"{w},{h}", "format": "tiff", "pixelType": "F32",
                               "interpolation": "RSP_BilinearInterpolation", "f": "image"})
            if "tiff" not in r.headers.get("content-type", ""):
                raise RuntimeError(f"3DEP returned {r.headers.get('content-type')}: {r.text[:200]}")
            with rasterio.open(io.BytesIO(r.content)) as src:
                arr = src.read(1).astype(np.float32)
                if src.nodata is not None:
                    arr[arr == src.nodata] = np.nan
            arr[arr < -1000] = np.nan
            out[r0:r0 + h, c0:c0 + w] = arr[:h, :w] * M_TO_FT
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, out)
    return out, g


# ---------------------------------------------------------------- weather

def fetch_weather(key):
    s = STORMS[key]
    lon0, lat0, lon1, lat1 = s["bbox"]
    t0 = datetime.fromisoformat(s["start"])
    t1 = t0 + timedelta(hours=s["hours"])

    def build():
        j = _get(METEO_URL, {
            "latitude": (lat0 + lat1) / 2, "longitude": (lon0 + lon1) / 2,
            "start_date": t0.date().isoformat(), "end_date": (t1 + timedelta(days=1)).date().isoformat(),
            "hourly": "wind_speed_10m,wind_gusts_10m,wind_direction_10m,precipitation,pressure_msl",
            "wind_speed_unit": "mph", "precipitation_unit": "inch", "timezone": "GMT",
        }).json()["hourly"]
        idx = {t: i for i, t in enumerate(j["time"])}
        rows = []
        for h in range(s["hours"] + 1):
            i = idx.get((t0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M"))
            rows.append({k: (j[k][i] if i is not None else None) for k in
                         ("wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "precipitation", "pressure_msl")})
        return rows

    return _cached(DATA / key / "weather.json", build)


def run():
    for key in STORMS:
        print(f"[surge fetch] {key}", flush=True)
        a = fetch_atcf(key)
        print(f"  ATCF: {len(a['best_track'])} best-track fixes, {len(a['advisories'])} official forecasts")
        g = fetch_gauges(key)
        for gid, v in g.items():
            obs = [x for x in v["observed_ft"] if x is not None]
            print(f"  {v['name']}: {len(obs)} obs, peak {max(obs) if obs else float('nan'):.2f} ft NAVD88")
        dem, grid = fetch_dem(key)
        print(f"  DEM {grid['W']}x{grid['H']} @ {grid['res']:.0f} m, {np.isfinite(dem).mean():.0%} valid")
        fetch_weather(key)
