"""Build the surge landing page's character field from the Sandy replay.

  modeling/.venv/bin/python web/scripts/build_surge_hero.py

Each cell of a 150 x 50 grid over New York Harbor is sea, land, or land the
water reached, with the first replay hour the tide gauges' observed water
stood 6 inches deep there (connected bathtub, same as the replay's ground
truth) and the peak depth. Plus the hourly water level at The Battery.
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
STORM = ROOT / "modeling" / "out" / "surge" / "storms" / "sandy_2012"
DEST = ROOT / "web" / "src" / "surge" / "sandy-field.json"
COLS, ROWS = 150, 50
CELL_ASPECT = 1.6

info = json.loads((STORM / "storm.json").read_text())
gauges = json.loads((STORM / "gauges.json").read_text())
series = json.loads((STORM / "timeseries.json").read_text())
meta = json.loads((STORM / "flood.json").read_text())
px = np.asarray(Image.open(STORM / "flood.png").convert("RGBA")).astype(np.int64)
q = px[..., 0] * 256 + px[..., 1]
spill = np.where(q == 65535, np.inf, meta["spill_min_ft"] + q / 65534 * (meta["spill_max_ft"] - meta["spill_min_ft"]))
ground = np.where(np.isfinite(spill), spill - px[..., 2] * meta["gap_step_ft"], 99.0)
H, W = spill.shape

# Crop the texture to the grid's aspect, centred on the harbour.
want = COLS / (ROWS * CELL_ASPECT)
cw = W
chh = int(round(cw / want))
if chh > H:
    chh = H
    cw = int(round(chh * want))
r0, c0 = max(0, (H - chh) // 2 - int(H * 0.05)), (W - cw) // 2
fg = info["field_gauges"]
gx = np.array([g[0] for g in info["terrain"]["gauge_texels"]])
gy = np.array([g[1] for g in info["terrain"]["gauge_texels"]])
high_tide = info["high_tide_ft"]

kind, arrival, depth = [], [], []
for r in range(ROWS):
    for c in range(COLS):
        rr = slice(r0 + int(r * chh / ROWS), r0 + int((r + 1) * chh / ROWS))
        cc = slice(c0 + int(c * cw / COLS), c0 + int((c + 1) * cw / COLS))
        sp, gr = spill[rr, cc], ground[rr, cc]
        sea = (gr <= max(0.6, high_tide)).mean()
        if sea > 0.6:
            kind.append(0); arrival.append(-1); depth.append(0)
            continue
        # The cell's lowest land: where water would get in first.
        land = gr > max(0.6, high_tide)
        i = np.argmin(np.where(land, sp, np.inf))
        s, g = sp.flat[i], gr.flat[i]
        y, x = r0 + (r + 0.5) * chh / ROWS, c0 + (c + 0.5) * cw / COLS
        w = 1 / np.maximum((gx - x) ** 2 + (gy - y) ** 2, 1)
        first, peak = -1, 0.0
        for h in range(info["hours"] + 1):
            lv = [gauges[k]["observed_ft"][h] for k in fg]
            ok = np.array([v is not None for v in lv])
            if not ok.any() or not np.isfinite(s):
                continue
            level = float((w[ok] * np.array([v for v in lv if v is not None])).sum() / w[ok].sum())
            d = level - g if level >= s else 0
            if d >= 0.5 and first < 0:
                first = h
            peak = max(peak, d)
        kind.append(2 if first >= 0 else 1); arrival.append(first); depth.append(round(peak, 1))

DEST.write_text(json.dumps({
    "cols": COLS, "rows": ROWS, "start": info["start"], "peak_hour": info["peak"]["hour"],
    "kind": kind, "arrival": arrival, "depth": depth,
    "water": [None if s["water_ft"] is None else round(s["water_ft"], 2) for s in series],
    "wind": [[s["wind_dir_deg"], s["wind_mph"], s["gust_mph"]] for s in series],
    "acres": [round(s["flooded_acres"]) for s in series],
    "storm_km": [s["storm_km"] for s in series],
}, separators=(",", ":")))
print(f"{sum(k == 2 for k in kind)} flooded cells, {sum(k == 0 for k in kind)} sea -> {DEST}")
