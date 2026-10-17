"""Build the replay evaluation set: simulated subscribers at real places.

Per incident, three groups of subscriber locations:
  destroyed  — CAL FIRE DINS structures recorded Destroyed (>50%). These people
               needed a warning before the fire reached them.
  undamaged  — DINS structures inside the fire area with No Damage. Warning them
               is still right (the fire came close); they test for over-alerting
               only loosely.
  burned     — for fires without damage records (the showcase fires): random
               points inside the final perimeter that satellites saw burn.
  outside    — random land points 2–20 km beyond the final perimeter, where the
               fire never arrived. An evacuate here is over-evacuation; a warn
               8 km or more out is a false warning.

Each row carries the ground truth the scorers need, so the same table works for
the local runner and for a Chalk Evaluation.
"""
import json
import random

import numpy as np
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

from sentinel.config import INCIDENTS, ROOT
from sentinel.geo import burn_probability, km_per_deg_lon, KM_PER_DEG_LAT
from sentinel.replay import observed_arrival_h

FIRES = ["eaton_2025", "palisades_2025"]          # train: the sentinel is calibrated on these
SHOWCASE = ["woolsey_2018", "mountain_2024", "franklin_2024"]   # held out, never tuned on
OUT = ROOT / "evals" / "data"


def _perimeter_km(key):
    g = json.loads((INCIDENTS / key / "real_perimeter.geojson").read_text())["features"][0]["geometry"]
    poly = shape(g)
    lat0 = poly.centroid.y
    kx = km_per_deg_lon(lat0)
    to_km = lambda x, y, z=None: (np.asarray(x) * kx, np.asarray(y) * KM_PER_DEG_LAT)
    return poly, shp_transform(to_km, poly), kx


def build(n_per_group=100, seed=7, fires=FIRES):
    rng = random.Random(seed)
    rows = []
    for key in fires:
        poly, poly_km, kx = _perimeter_km(key)
        from shapely.geometry import Point

        def dist_km(lat, lon):
            return round(poly_km.distance(Point(lon * kx, lat * KM_PER_DEG_LAT)), 2)

        minx, miny, maxx, maxy = poly.bounds
        damage = INCIDENTS / key / "structures.geojson"
        if damage.exists():
            feats = json.loads(damage.read_text())["features"]
            groups = {
                "destroyed": [f for f in feats if f["properties"]["DAMAGE"] == "Destroyed (>50%)"],
                "undamaged": [f for f in feats if f["properties"]["DAMAGE"] == "No Damage"],
            }
            for cat, pool in groups.items():
                for f in rng.sample(pool, min(n_per_group, len(pool))):
                    lon, lat = f["geometry"]["coordinates"]
                    rows.append(_row(key, cat, lat, lon, f["properties"]["DAMAGE"], dist_km(lat, lon)))
        else:
            n, tries = 0, 0
            while n < n_per_group and tries < 200 * n_per_group:
                tries += 1
                lat, lon = rng.uniform(miny, maxy), rng.uniform(minx, maxx)
                if not poly.contains(Point(lon, lat)):
                    continue
                row = _row(key, "burned", lat, lon, None, 0.0)
                if row["observed_arrival_h"] is not None and row["burn_probability_10y"] is not None:
                    rows.append(row)
                    n += 1

        pad = 0.25  # degrees, ~20–25 km
        n = 0
        while n < n_per_group:
            lat, lon = rng.uniform(miny - pad, maxy + pad), rng.uniform(minx - pad, maxx + pad)
            d = dist_km(lat, lon)
            if 2 <= d <= 20 and burn_probability(lat, lon) is not None:   # on land, in the model box
                rows.append(_row(key, "outside", lat, lon, None, d))
                n += 1
    return rows


def _row(key, cat, lat, lon, damage, dist):
    arr = observed_arrival_h(key, lat, lon) if cat != "outside" else None
    return {"incident": key, "category": cat, "lat": round(lat, 6), "lon": round(lon, 6),
            "damage": damage, "dist_to_perimeter_km": dist, "observed_arrival_h": arr,
            "burn_probability_10y": burn_probability(lat, lon)}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=100, help="locations per group per fire")
    ap.add_argument("--showcase", action="store_true", help="the held-out fires instead of the train fires")
    a = ap.parse_args()
    rows = build(a.n, fires=SHOWCASE if a.showcase else FIRES)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / (f"replay_showcase_n{a.n}.json" if a.showcase else f"replay_subscribers_n{a.n}.json")
    path.write_text(json.dumps(rows, indent=1))
    print(f"{len(rows)} rows -> {path}")
