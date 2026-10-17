"""Build the storm-surge replay evaluation set: simulated households at real places.

Per storm, three groups of addresses on land (ground above the highest
predicted tide), sampled from the terrain grid:
  flooded  — the gauges' observed water stood 6 inches or more deep here, and
             not already at the start of the replay. These needed a warning.
  spared   — low enough to sit in an evacuation zone (spill level 16 ft or
             less) but stayed dry. An evacuate here is a needless evacuation:
             exactly the cost of zone-wide orders.
  high     — high ground the water could never reach (spill above 16 ft).
             Any warn or evacuate here is a false warning.

  python -m evals.surge.dataset -n 60
"""
import json
import random

import numpy as np

from sentinel.config import ROOT
from surge_sentinel.world import load_storm, observed_arrival_h, terrain

STORMS = ["sandy_2012", "matthew_2016", "irma_2017", "isabel_2003"]
TRAIN = ["sandy_2012", "matthew_2016"]
OUT = ROOT / "evals" / "data"
SPARED_MAX_SPILL_FT = 16.0


def _lonlat(key, c, r):
    info = load_storm(key)[0]
    (lon0, lat1), (lon1, _), (_, lat0) = info["terrain"]["corners"][:3]
    W, H = info["terrain"]["width"], info["terrain"]["height"]
    x0 = np.radians(lon0)
    x1 = np.radians(lon1)
    y0 = np.log(np.tan(np.pi / 4 + np.radians(lat0) / 2))
    y1 = np.log(np.tan(np.pi / 4 + np.radians(lat1) / 2))
    lon = np.degrees(x0 + (c + 0.5) / W * (x1 - x0))
    lat = np.degrees(2 * np.arctan(np.exp(y1 - (r + 0.5) / H * (y1 - y0))) - np.pi / 2)
    return float(lat), float(lon)


def build(n_per_group=60, seed=7, storms=STORMS):
    rng = random.Random(seed)
    rows = []
    for key in storms:
        info = load_storm(key)[0]
        spill, ground, _ = terrain(key)
        H, W = spill.shape
        # Keep away from the box edge, where water would arrive from outside the grid.
        m = 20
        land = np.zeros_like(spill, bool)
        land[m:H - m, m:W - m] = True
        land &= ~(np.isfinite(ground) & (ground <= max(0.6, info["high_tide_ft"])))
        counts = {"flooded": 0, "spared": 0, "high": 0}
        want = {"flooded": n_per_group, "spared": n_per_group, "high": n_per_group // 2}
        tries = 0
        while any(counts[k] < want[k] for k in counts) and tries < 400 * n_per_group:
            tries += 1
            r, c = rng.randrange(m, H - m), rng.randrange(m, W - m)
            if not land[r, c]:
                continue
            sp = spill[r, c]
            lat, lon = _lonlat(key, c, r)
            if not np.isfinite(sp) or sp > SPARED_MAX_SPILL_FT:
                cat = "high"
                arr, peak = None, 0.0
            else:
                arr, peak = observed_arrival_h(key, lat, lon)
                if arr is not None and arr <= 2:
                    continue
                cat = "flooded" if arr is not None else "spared"
            if counts[cat] >= want[cat]:
                continue
            counts[cat] += 1
            rows.append({"storm": key, "category": cat, "lat": round(lat, 6), "lon": round(lon, 6),
                         "ground_ft": None if not np.isfinite(ground[r, c]) else round(float(ground[r, c]), 2),
                         "spill_ft": None if not np.isfinite(sp) else round(float(sp), 2),
                         "observed_arrival_h": arr, "peak_depth_ft": peak,
                         "role": "train" if key in TRAIN else "held_out"})
        print(f"{key}: {counts}")
    return rows


def load_rows(n=60):
    path = OUT / f"surge_households_n{n}.json"
    if not path.exists():
        OUT.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(build(n), indent=1))
    return json.loads(path.read_text())


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=60, help="households per group per storm")
    a = ap.parse_args()
    rows = build(a.n)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"surge_households_n{a.n}.json"
    path.write_text(json.dumps(rows, indent=1))
    print(f"{len(rows)} rows -> {path}")
