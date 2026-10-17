"""Region, grid and incident settings shared by every pipeline step."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "out"

# Los Angeles + Ventura counties (lon/lat).
BBOX_LONLAT = (-119.5, 33.7, -117.6, 34.85)

# California Albers: equal-area, metres. All rasters live on this CRS.
CRS = "EPSG:3310"
REGION_RES = 150  # metres per cell for region-wide features / risk model
INCIDENT_RES = 30  # metres per cell for spread simulation (native LANDFIRE)

# Fire history split for the risk model: features use fires up to
# HISTORY_END, the label is "burned in (HISTORY_END, LABEL_END]".
TRAIN_HISTORY_END, TRAIN_LABEL_END = 2004, 2014
TEST_HISTORY_END, TEST_LABEL_END = 2014, 2025

# Ignition points are approximate public origin locations, not official.
# role "train": used to calibrate the spread model (and the sentinel).
# role "showcase": simulated with the parameters learned on the train fires
# only, so every score on them is out of sample.
INCIDENTS = {
    "eaton_2025": {
        "name": "Eaton Fire",
        "frap_name": "EATON",
        "year": 2025,
        "ignition_lonlat": (-118.0905, 34.1910),
        "start": "2025-01-07T18:18",  # local time (America/Los_Angeles)
        "hours": 72,
        "half_width_km": 9,
        "role": "train",
        "red_flag": "PDS Red Flag Warning (NWS Los Angeles/Oxnard), Jan 7-10 2025",
    },
    "palisades_2025": {
        "name": "Palisades Fire",
        "frap_name": "PALISADES",
        "year": 2025,
        "ignition_lonlat": (-118.5430, 34.0720),
        "start": "2025-01-07T10:30",
        "hours": 72,
        "half_width_km": 12,
        "role": "train",
        "red_flag": "PDS Red Flag Warning (NWS Los Angeles/Oxnard), Jan 7-10 2025",
    },
    "woolsey_2018": {
        "name": "Woolsey Fire",
        "frap_name": "WOOLSEY",
        "year": 2018,
        "ignition_lonlat": (-118.6969, 34.2357),
        "start": "2018-11-08T14:24",
        "hours": 72,
        "half_width_km": 36,
        "res": 60,
        "role": "showcase",
        "red_flag": "Red Flag Warning (NWS Los Angeles/Oxnard), Santa Ana winds, Nov 8-9 2018",
    },
    "mountain_2024": {
        "name": "Mountain Fire",
        "frap_name": "MOUNTAIN",
        "year": 2024,
        "ignition_lonlat": (-118.9580, 34.3100),
        "start": "2024-11-06T08:51",
        "hours": 48,
        "half_width_km": 18,
        "res": 30,
        "role": "showcase",
        "red_flag": "PDS Red Flag Warning (NWS Los Angeles/Oxnard), Santa Ana winds, Nov 6 2024",
    },
    "franklin_2024": {
        "name": "Franklin Fire",
        "frap_name": "FRANKLIN",
        "year": 2024,
        "ignition_lonlat": (-118.6955, 34.0735),
        "start": "2024-12-09T22:50",
        "hours": 48,
        "half_width_km": 8,
        "res": 30,
        "role": "showcase",
        "red_flag": "Red Flag Warning (NWS Los Angeles/Oxnard), Santa Ana winds, Dec 9-10 2024",
    },
    "bobcat_2020": {
        "name": "Bobcat Fire",
        "frap_name": "BOBCAT",
        "year": 2020,
        "ignition_lonlat": (-117.9690, 34.2440),
        "start": "2020-09-06T12:20",
        "hours": 240,
        "half_width_km": 22,
        "res": 60,
        "live_woody": 0.70,
        "role": "unused",   # 10-day, not wind-driven; not in the default runs
    },
}


def keys_with(role):
    return [k for k, v in INCIDENTS.items() if v.get("role") == role]
