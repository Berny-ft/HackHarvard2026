"""Settings for the shared router. Hazard-specific thresholds live with each hazard plug-in
(sentinel/route_hazard.py for fire, surge_sentinel/route_hazard.py for storm surge)."""
import os
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]          # agent/
REPO = AGENT.parent
DATA = Path(os.environ.get("FIRETRACE_ROAD_DATA", REPO / "modeling" / "data"))
STATE = Path(os.environ.get("SENTINEL_STATE", AGENT / "state"))

# Prebuilt road graphs (`python -m wildfire roads`, `python -m surge roads`), by region name.
REGIONS = {"socal": DATA / "roads_graph.json"}
SURGE_ROADS = DATA / "surge_roads"                  # <storm>.json, one graph per storm area (`python -m surge roads`)

SAMPLE_KM = 0.1             # roads are tested against the hazard every 100 m
MAX_SNAP_KM = 3.0           # start and destination must be this close to a road
RISK = {"cautious": 2.0, "balanced": 1.0, "fastest": 0.0}    # soft-penalty weight
LEAVE_BY_WINDOW_H = 24.0    # how far ahead escape looks for the latest departure that still gets out

# Free-flow speed (mph) by road class when OSM has no maxspeed; ramps run at 60 %.
DEFAULT_MPH = {"motorway": 65, "trunk": 55, "primary": 40, "secondary": 35, "tertiary": 30,
               "unclassified": 25, "residential": 20, "living_street": 10}

# Modelled evacuation traffic (no live speed feed): roads the hazard could reach within
# CONGESTION_ZONE_H run at this fraction of free-flow speed while the area empties.
# Placeholders from the texting router, to tune.
EVAC_SPEED_FACTOR = {"motorway": 0.60, "trunk": 0.55, "primary": 0.50, "secondary": 0.45, "tertiary": 0.40,
                     "unclassified": 0.35, "residential": 0.30, "living_street": 0.30}
CONGESTION_ZONE_H = 6.0

# Streets near the start point, fetched on demand (the prebuilt graphs hold major roads only).
LOCAL_STREETS_KM = 1.5
LOCAL_STREETS_TIMEOUT_S = 25
LOCAL_STREETS_COOLDOWN_S = 600      # after a failed download, don't retry the same spot for this long

# Full road closures (Caltrans) block roads within this distance.
CLOSURE_BLOCK_KM = 0.25
