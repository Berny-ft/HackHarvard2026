"""Building footprints around each modeled fire, for the 3D replay.

Footprints come from OpenStreetMap as it stood the day before ignition
(Overpass attic query), so homes the fire destroyed are still on the map.
Each building carries the modeled arrival hour at its footprint, its CAL FIRE
DINS damage class when there is one within a few metres, and a height from
its OSM tags (or a typical height for its kind).

Writes incidents/<key>/buildings.bin + buildings.json. Binary layout
(little-endian, sections in this order, N buildings, V vertices):
  float32[N] arrival hour (-1 = the modeled fire never reaches it)
  float32[N] height, metres
  uint32[N+1] first vertex of each building's outer ring (ring not closed)
  float32[2V] vertices, metres east / south of the origin in Web Mercator
              scaled to true metres at the origin's latitude
  uint8[N]   damage: 0 no record, 1 none, 2 affected, 3 minor, 4 major, 5 destroyed

python -m wildfire.buildings [key ...]
"""
import json
import math
import sys
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import rasterio.features
import requests
from pyproj import Transformer
from scipy.ndimage import distance_transform_edt
from shapely.geometry import Polygon

from .config import CRS, DATA, INCIDENTS, OUT

OVERPASS = "https://overpass-api.de/api/interpreter"
NEAR_M = 400       # keep buildings within this distance of the modeled or real burn
DINS_M = 8         # DINS point must fall within this distance of the footprint
CHUNK_DEG = 0.06   # Overpass bbox tiles
EARTH_R = 6378137.0

DAMAGE = {"No Damage": 1, "Affected (1-9%)": 2, "Minor (10-25%)": 3, "Major (26-50%)": 4, "Destroyed (>50%)": 5}
# Typical heights (m) when OSM has neither height nor levels.
KIND_HEIGHT = {"garage": 3.0, "garages": 3.0, "shed": 2.6, "carport": 2.8, "roof": 3.5, "greenhouse": 3.0,
               "apartments": 10.0, "commercial": 8.0, "retail": 6.5, "industrial": 8.0, "warehouse": 8.0,
               "school": 7.5, "church": 11.0, "hospital": 14.0, "office": 12.0, "hotel": 14.0}
DEFAULT_HEIGHT = 5.2  # one-to-two storey house


def _overpass(bbox, date, key, i):
    """bbox = (south, west, north, east). Cached per chunk."""
    cache = DATA / f"osm_buildings_{key}_{i}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    s, w, n, e = bbox
    q = f'[out:json][timeout:180][date:"{date}"];way[building]({s},{w},{n},{e});out tags geom;'
    for attempt in range(5):
        r = requests.post(OVERPASS, data={"data": q}, timeout=240,
                          headers={"User-Agent": "FireTrace/1.0 (wildfire replay research)"})
        if r.status_code == 200:
            DATA.mkdir(exist_ok=True)
            cache.write_text(r.text)
            return r.json()
        print(f"  overpass {r.status_code}, retrying", flush=True)
        time.sleep(15 * (attempt + 1))
    r.raise_for_status()


def _height(tags):
    for k in ("height", "building:height"):
        try:
            return float(str(tags[k]).split()[0].replace("m", ""))
        except (KeyError, ValueError):
            pass
    try:
        return float(tags["building:levels"]) * 3.2 + 1.0
    except (KeyError, ValueError):
        return KIND_HEIGHT.get(tags.get("building"), DEFAULT_HEIGHT)


def run_one(key):
    d = OUT / "incidents" / key
    info = json.loads((d / "incident.json").read_text())
    hours = info["hours"]
    with rasterio.open(d / "arrival_hours.tif") as src:
        arr, tr = src.read(1), src.transform
        res = src.res[0]
    burns = np.isfinite(arr) & (arr <= hours)

    real = gpd.read_file(d / "real_perimeter.geojson").to_crs(CRS)
    real_mask = rasterio.features.rasterize([(g, 1) for g in real.geometry], out_shape=arr.shape,
                                            transform=tr, fill=0, dtype="uint8").astype(bool)
    near = distance_transform_edt(~(burns | real_mask)) * res <= NEAR_M
    rows, cols = np.nonzero(near)
    xs, ys = rasterio.transform.xy(tr, [rows.min(), rows.max()], [cols.min(), cols.max()])
    to_ll = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    lons, lats = to_ll.transform(np.array(xs), np.array(ys))
    w, e, s, n = lons.min() - 0.01, lons.max() + 0.01, lats.min() - 0.01, lats.max() + 0.01

    # OSM the day before ignition.
    date = (pd.Timestamp(info["start"]) - pd.Timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    ways = {}
    nx, ny = math.ceil((e - w) / CHUNK_DEG), math.ceil((n - s) / CHUNK_DEG)
    for j in range(ny):
        for i in range(nx):
            box = (s + j * CHUNK_DEG, w + i * CHUNK_DEG, min(n, s + (j + 1) * CHUNK_DEG), min(e, w + (i + 1) * CHUNK_DEG))
            print(f"  {key}: osm chunk {j * nx + i + 1}/{nx * ny}", flush=True)
            for el in _overpass(box, date, key, j * nx + i)["elements"]:
                if el.get("type") == "way" and len(el.get("geometry", [])) >= 4:
                    ways[el["id"]] = el

    polys, heights = [], []
    for el in ways.values():
        ring = [(p["lon"], p["lat"]) for p in el["geometry"]]
        if ring[0] != ring[-1]:
            continue
        tags = el.get("tags", {})
        if tags.get("building") in ("no", "construction", "ruins", "collapsed", "destroyed") or tags.get("location") == "underground":
            continue
        polys.append(Polygon(ring))
        heights.append(_height(tags))
    b = gpd.GeoDataFrame({"height": heights}, geometry=polys, crs="EPSG:4326").to_crs(CRS)
    b = b[b.geometry.is_valid & (b.geometry.area > 20)]

    # Keep buildings near the burn; arrival = earliest modeled arrival over centroid and corners.
    c = b.geometry.centroid
    r, cl = rasterio.transform.rowcol(tr, c.x.to_numpy(), c.y.to_numpy())
    r, cl = np.asarray(r), np.asarray(cl)
    inside = (r >= 0) & (r < arr.shape[0]) & (cl >= 0) & (cl < arr.shape[1])
    keep = np.zeros(len(b), bool)
    keep[inside] = near[r[inside], cl[inside]]
    b = b[keep].copy()

    def first_arrival(geom):
        pts = [geom.centroid.coords[0], *geom.exterior.coords[:-1]]
        best = np.inf
        for x, y in pts:
            rr, cc = rasterio.transform.rowcol(tr, x, y)
            if 0 <= rr < arr.shape[0] and 0 <= cc < arr.shape[1] and burns[rr, cc]:
                best = min(best, float(arr[rr, cc]))
        return best

    b["arrival"] = [first_arrival(g) for g in b.geometry]

    # Damage: the worst DINS record on or right next to the footprint.
    b["damage"] = 0
    sp = d / "structures.geojson"
    if sp.exists():
        pts = gpd.read_file(sp).to_crs(CRS)
        if "DAMAGE" in pts:
            pts["code"] = pts["DAMAGE"].map(DAMAGE).fillna(0).astype(int)
            pts = pts[pts["code"] > 0]
            j = gpd.sjoin_nearest(b[["geometry"]].reset_index(), pts[["code", "geometry"]], max_distance=DINS_M)
            worst = j.groupby("index")["code"].max()
            b.loc[worst.index, "damage"] = worst.to_numpy()

    # Geometry in a local metric Web Mercator frame (matches how the web map places things).
    b = b.to_crs("EPSG:4326")
    b["geometry"] = b.geometry.simplify(8e-6, preserve_topology=True)
    lon0, lat0 = (w + e) / 2, (s + n) / 2
    k = EARTH_R * math.cos(math.radians(lat0))  # metres per radian of Mercator at the origin

    def merc(lon, lat):
        return np.radians(lon), np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))

    ox, oy = merc(lon0, lat0)
    starts, verts = [0], []
    for g in b.geometry:
        g = g if g.geom_type == "Polygon" else max(g.geoms, key=lambda p: p.area)
        ring = np.asarray(g.exterior.coords)[:-1]
        if not g.exterior.is_ccw:  # counter-clockwise in lon/lat = clockwise once y points south
            ring = ring[::-1]
        mx, my = merc(ring[:, 0], ring[:, 1])
        verts.append(np.column_stack([(mx - ox) * k, -(my - oy) * k]))
        starts.append(starts[-1] + len(ring))
    verts = np.concatenate(verts).astype("<f4")
    arrival = np.where(np.isfinite(b["arrival"]), b["arrival"], -1).astype("<f4")
    height = b["height"].clip(2.5, 120).astype("<f4").to_numpy()
    damage = b["damage"].astype("u1").to_numpy()

    with open(d / "buildings.bin", "wb") as f:
        f.write(arrival.tobytes())
        f.write(height.tobytes())
        f.write(np.asarray(starts, "<u4").tobytes())
        f.write(verts.tobytes())
        f.write(damage.tobytes())
    meta = {"origin": [lon0, lat0], "count": int(len(b)), "vertices": int(len(verts)),
            "reached": int((arrival >= 0).sum()), "with_damage_record": int((damage > 0).sum()),
            "osm_date": date, "source": "OpenStreetMap contributors (ODbL), CAL FIRE DINS"}
    (d / "buildings.json").write_text(json.dumps(meta, indent=1))
    print(f"  {key}: {meta['count']} buildings, {meta['reached']} reached, "
          f"{meta['with_damage_record']} with DINS, {(d / 'buildings.bin').stat().st_size / 1e6:.1f} MB", flush=True)


def run(keys=None):
    for key in keys or INCIDENTS:
        run_one(key)


if __name__ == "__main__":
    run(sys.argv[1:] or None)
