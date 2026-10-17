"""Build the landing-page character field from the Eaton Fire data (stdlib only).

  python3 web/scripts/build_hero.py

Each cell of a 150 x 50 grid gets the first hour a satellite saw fire there
(VIIRS/MODIS detections, dilated by one cell) and how many DINS-inspected
structures stand in it, split by destroyed / not destroyed.
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INC = ROOT / "modeling" / "out" / "incidents" / "eaton_2025"
DEST = ROOT / "web" / "src" / "landing" / "eaton-field.json"

COLS, ROWS = 150, 50
CELL_ASPECT = 1.6                      # character cell height / width on screen
LON0, LON1 = -118.43, -117.95
km_per_lon = 111.32 * math.cos(math.radians(34.19))
cell_w_km = (LON1 - LON0) * km_per_lon / COLS
lat_span = cell_w_km * CELL_ASPECT * ROWS / 110.57
LAT1 = 34.19 + lat_span * 0.45         # fire sits a little below the vertical middle
LAT0 = LAT1 - lat_span


def cell(lon, lat):
    c = int((lon - LON0) / (LON1 - LON0) * COLS)
    r = int((LAT1 - lat) / (LAT1 - LAT0) * ROWS)
    return (r, c) if 0 <= c < COLS and 0 <= r < ROWS else None


arrival = [[None] * COLS for _ in range(ROWS)]
for f in json.loads((INC / "detections.geojson").read_text())["features"]:
    rc = cell(*f["geometry"]["coordinates"][:2])
    if not rc:
        continue
    h = f["properties"]["hour"]
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            r, c = rc[0] + dr, rc[1] + dc
            if 0 <= r < ROWS and 0 <= c < COLS:
                hh = round(h + (0.6 if dr or dc else 0), 1)
                if arrival[r][c] is None or hh < arrival[r][c]:
                    arrival[r][c] = hh

destroyed = [[0] * COLS for _ in range(ROWS)]
standing = [[0] * COLS for _ in range(ROWS)]
for f in json.loads((INC / "structures.geojson").read_text())["features"]:
    rc = cell(*f["geometry"]["coordinates"][:2])
    if rc:
        grid = destroyed if f["properties"]["DAMAGE"].startswith("Destroyed") else standing
        grid[rc[0]][rc[1]] += 1

series = json.loads((INC / "timeseries.json").read_text())
DEST.parent.mkdir(parents=True, exist_ok=True)
DEST.write_text(json.dumps({
    "cols": COLS, "rows": ROWS, "start": series[0]["time"],
    "arrival": [-1 if a is None else a for row in arrival for a in row],
    "destroyed": [v for row in destroyed for v in row],
    "standing": [v for row in standing for v in row],
    "wind": [[s["wind_dir_deg"], s["wind_mph"], s["gust_mph"]] for s in series],
    "acres": [round(s.get("observed_acres") or 0) for s in series],
}, separators=(",", ":")))
lit = sum(a is not None for row in arrival for a in row)
print(f"{lit} burning cells, {sum(map(sum, destroyed))} destroyed -> {DEST}")
