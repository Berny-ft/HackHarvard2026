"""Storms, regions and gauges for the storm-surge pipeline (US East Coast).

Units are US: feet (NAVD88) for water and ground, knots and mph for wind,
nautical miles for storm radii, as NHC and NOAA CO-OPS publish them.

Each storm replays a box around the place it flooded worst. Ground truth for
"did this spot flood, and when" comes from NOAA tide gauges (observed water
level), never from the surge model. `role` works like the fire pipeline:
"train" storms calibrate the surge model and the agent, "showcase" storms are
only ever scored with what the train storms taught.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "surge"
OUT = ROOT / "out" / "surge"

# Grid for the flood texture: Web Mercator metres, so the texture's corners are
# exact for the map. ~40 m cells; boxes are 35-55 km on a side.
MERC_RES = 40.0
MAX_PX = 1600

# Water at least this deep counts as "flooded" (6 inches: enough to stall a car
# and flood a ground floor with a threshold).
FLOOD_DEPTH_FT = 0.5
# Ground at or below this (NAVD88 ft) is open water in the hydro-flattened DEM.
WATER_FT = 0.6

# Gauges: NOAA CO-OPS station id, name, position, the compass direction wind
# must blow *toward* to pile water onto this shore, and an approximate shelf
# width (km to the 20 m isobath) from NOAA charts. Wide shallow shelves and
# funnelled bays amplify wind setup; Miami's narrow shelf is why its surge is small.
GAUGES = {
    "8518750": {"name": "The Battery, NY", "lat": 40.7006, "lon": -74.0142, "push_to": 320, "shelf_km": 45},
    "8516945": {"name": "Kings Point, NY", "lat": 40.8103, "lon": -73.7649, "push_to": 270, "shelf_km": 55},
    "8531680": {"name": "Sandy Hook, NJ", "lat": 40.4669, "lon": -74.0094, "push_to": 300, "shelf_km": 40},
    "8670870": {"name": "Fort Pulaski, GA", "lat": 32.0367, "lon": -80.9017, "push_to": 270, "shelf_km": 110},
    "8665530": {"name": "Charleston, SC", "lat": 32.7808, "lon": -79.9236, "push_to": 300, "shelf_km": 90},
    "8723214": {"name": "Virginia Key, FL", "lat": 25.7314, "lon": -80.1618, "push_to": 280, "shelf_km": 4},
    "8722670": {"name": "Lake Worth Pier, FL", "lat": 26.6128, "lon": -80.0342, "push_to": 270, "shelf_km": 3},
    "8638610": {"name": "Sewells Point, VA", "lat": 36.9467, "lon": -76.3300, "push_to": 290, "shelf_km": 70},
    "8632200": {"name": "Kiptopeke, VA", "lat": 37.1652, "lon": -75.9884, "push_to": 320, "shelf_km": 60},
}

STORMS = {
    "sandy_2012": {
        "name": "Hurricane Sandy",
        "atcf": "al182012",
        "year": 2012,
        "place": "New York Harbor",
        "bbox": (-74.27, 40.50, -73.72, 40.90),          # lon0, lat0, lon1, lat1
        "start": "2012-10-27T00:00",                      # UTC; replay hour 0
        "hours": 96,
        "gauges": ["8518750", "8516945", "8531680"],
        "role": "train",
        "tz": "America/New_York",
        "camera": {"bearing": -20},
    },
    "matthew_2016": {
        "name": "Hurricane Matthew",
        "atcf": "al142016",
        "year": 2016,
        "place": "Savannah and Tybee Island",
        "bbox": (-81.20, 31.86, -80.78, 32.20),
        "start": "2016-10-05T00:00",
        "hours": 120,
        "gauges": ["8670870", "8665530"],
        "role": "train",
        "tz": "America/New_York",
        "camera": {"bearing": 30},
    },
    "irma_2017": {
        "name": "Hurricane Irma",
        "atcf": "al112017",
        "year": 2017,
        "place": "Miami and Biscayne Bay",
        "bbox": (-80.36, 25.60, -80.08, 25.98),
        "start": "2017-09-07T12:00",
        "hours": 96,
        "gauges": ["8723214", "8722670"],
        "role": "showcase",
        "tz": "America/New_York",
        "camera": {"bearing": -10},
    },
    "isabel_2003": {
        "name": "Hurricane Isabel",
        "atcf": "al132003",
        "year": 2003,
        "place": "Norfolk and Hampton Roads",
        "bbox": (-76.45, 36.78, -76.00, 37.10),
        "start": "2003-09-15T12:00",
        "hours": 96,
        "gauges": ["8638610", "8632200"],
        "role": "showcase",
        "tz": "America/New_York",
        "camera": {"bearing": 20},
    },
}

# Live view: every NOAA water-level gauge we watch from the Keys to Maine.
LIVE_GAUGES = {
    "8724580": "Key West, FL", "8723214": "Virginia Key, FL", "8722670": "Lake Worth Pier, FL",
    "8721604": "Trident Pier, FL", "8720218": "Mayport, FL", "8670870": "Fort Pulaski, GA",
    "8665530": "Charleston, SC", "8658163": "Wrightsville Beach, NC", "8651370": "Duck, NC",
    "8638610": "Sewells Point, VA", "8575512": "Annapolis, MD", "8594900": "Washington, DC",
    "8557380": "Lewes, DE", "8534720": "Atlantic City, NJ", "8531680": "Sandy Hook, NJ",
    "8518750": "The Battery, NY", "8516945": "Kings Point, NY", "8467150": "Bridgeport, CT",
    "8461490": "New London, CT", "8452660": "Newport, RI", "8447930": "Woods Hole, MA",
    "8443970": "Boston, MA", "8418150": "Portland, ME",
}
LIVE_BBOX = (-82.5, 24.3, -66.5, 45.5)
