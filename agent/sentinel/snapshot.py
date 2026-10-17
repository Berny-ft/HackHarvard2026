"""What the agent can see at one moment: fire locations, weather, warnings.

The same Snapshot interface is built two ways:
  * live    — from the modelling pipeline's out/live/ feeds;
  * replay  — from a past incident at hour h, using only data available by h
              (satellite detections up to h, the reported ignition, and the
              hourly weather as a stand-in forecast). This point-in-time cut is
              what makes the replay evaluation honest.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import numpy as np

from .config import ACTIVE_WINDOW_H, INCIDENTS, LIVE

OFFSHORE_FROM_DEG = (340, 110)  # mirrors modeling/wildfire/fireweather.py
SA_TIERS = {1: (25, 20), 2: (40, 15)}


def santa_ana_level(wind_dir_deg, gust_mph, rh_pct):
    d = wind_dir_deg % 360
    off = d >= OFFSHORE_FROM_DEG[0] or d <= OFFSHORE_FROM_DEG[1]
    level = 0
    for lvl, (gust, rh) in SA_TIERS.items():
        if off and gust_mph >= gust and rh_pct <= rh:
            level = lvl
    return level


@dataclass
class Fires:
    lat: np.ndarray
    lon: np.ndarray
    age_h: np.ndarray            # hours since seen
    kind: list                   # "satellite" | "reported" | "perimeter"
    name: list                   # incident name when known

    def __len__(self):
        return len(self.lat)


@dataclass
class Snapshot:
    as_of: str
    fires: Fires
    weather: callable                     # (lat, lon) -> dict of current conditions
    outlook: callable                     # (lat, lon) -> dict of next-12h extremes
    red_flag: callable                    # (lat, lon) -> headline or None
    source: str = "live"
    meta: dict = field(default_factory=dict)


def _wx_record(r):
    return {
        "temp_f": r["temp_f"], "rh_pct": r["rh_pct"], "wind_mph": r["wind_mph"],
        "gust_mph": r["gust_mph"], "wind_dir_deg": r["wind_dir_deg"],
        "fuel_moisture_1h_pct": r["fuel_moisture_1h_pct"],
        "santa_ana_level": santa_ana_level(r["wind_dir_deg"], r["gust_mph"], r["rh_pct"]),
    }


# ------------------------------------------------------------------ replay

# Both 2025 fires burned under an NWS "Particularly Dangerous Situation" Red Flag
# Warning issued before ignition, so the replay treats it as in force throughout.
HISTORICAL_RED_FLAG = "PDS Red Flag Warning (NWS Los Angeles/Oxnard), Jan 7-10 2025"


@lru_cache(maxsize=8)
def load_incident(key):
    d = INCIDENTS / key
    inc = json.loads((d / "incident.json").read_text())
    ts = json.loads((d / "timeseries.json").read_text())
    det = json.loads((d / "detections.geojson").read_text())["features"]
    xy = np.array([f["geometry"]["coordinates"] for f in det])
    hrs = np.array([f["properties"]["hour"] for f in det])
    order = np.argsort(hrs)
    return inc, ts, xy[order], hrs[order]


def replay_snapshot(key, hour):
    """Everything known `hour` hours after ignition of incident `key`."""
    inc, ts, xy, hrs = load_incident(key)
    seen = hrs <= hour
    ign = inc["ignition"]
    lat = np.concatenate([[ign["lat"]], xy[seen, 1]])
    lon = np.concatenate([[ign["lon"]], xy[seen, 0]])
    age = np.concatenate([[hour], hour - hrs[seen]])
    # Detections are kept as burned area once seen; the active window only
    # limits how recent a point must be to count as a moving front.
    fires = Fires(lat, lon, age, ["reported"] + ["satellite"] * int(seen.sum()),
                  [inc["name"]] * len(lat))

    h = int(min(hour, len(ts) - 1))
    now = _wx_record(ts[h])
    ahead = ts[h + 1:h + 13] or [ts[h]]
    look = {
        "max_gust_mph": max(r["gust_mph"] for r in ahead),
        "min_fuel_moisture_1h_pct": min(r["fuel_moisture_1h_pct"] for r in ahead),
        "peak_santa_ana_level": max(santa_ana_level(r["wind_dir_deg"], r["gust_mph"], r["rh_pct"]) for r in ahead),
        "wind_dir_deg_ahead": _mean_dir([r["wind_dir_deg"] for r in ahead]),
    }
    as_of = (datetime.fromisoformat(inc["start"]) + timedelta(hours=hour)).isoformat()
    return Snapshot(as_of, fires, lambda la, lo: now, lambda la, lo: look,
                    lambda la, lo: HISTORICAL_RED_FLAG, source=f"replay:{key}",
                    meta={"hour": hour, "incident": inc["name"]})


def _mean_dir(degs):
    r = np.radians(degs)
    return round(float((np.degrees(np.arctan2(np.sin(r).mean(), np.cos(r).mean())) + 360) % 360))


# -------------------------------------------------------------------- live

def _read(name, default):
    p = LIVE / name
    return json.loads(p.read_text()) if p.exists() else default


def live_snapshot(now=None):
    """Snapshot from the latest `python -m wildfire live` run."""
    from shapely.geometry import Point, shape

    now = now or datetime.now(timezone.utc)
    lat, lon, age, kind, name = [], [], [], [], []

    for f in _read("hotspots.geojson", {"features": []})["features"]:
        t = datetime.fromisoformat(f["properties"]["time_utc"].replace("Z", "+00:00"))
        lo, la = f["geometry"]["coordinates"]
        lat.append(la); lon.append(lo); age.append((now - t).total_seconds() / 3600)
        kind.append("satellite"); name.append(None)

    for inc in _read("incidents.json", []):
        a = (now.timestamp() * 1000 - inc["discovered"]) / 3.6e6
        if inc.get("contained_pct") is not None and inc["contained_pct"] >= 100:
            continue
        if a <= 72:  # recently reported; older incident points say little about a front
            lat.append(inc["lat"]); lon.append(inc["lon"]); age.append(a)
            kind.append("reported"); name.append(inc["name"])

    for f in _read("perimeters.geojson", {"features": []})["features"]:
        g = shape(f["geometry"])
        ring = (g.geoms[0] if g.geom_type == "MultiPolygon" else g).exterior.coords
        for lo, la in list(ring)[::max(1, len(ring) // 200)]:
            lat.append(la); lon.append(lo); age.append(0.0)
            kind.append("perimeter"); name.append(f["properties"].get("poly_IncidentName"))

    fires = Fires(np.array(lat), np.array(lon), np.array(age), kind, name)

    grid = _read("weather.json", [])
    glat = np.array([g["lat"] for g in grid]); glon = np.array([g["lon"] for g in grid])

    def nearest(la, lo):
        return grid[int(np.argmin((glat - la) ** 2 + ((glon - lo) * np.cos(np.radians(la))) ** 2))]

    def weather(la, lo):
        g = nearest(la, lo)
        return {k: g[k] for k in ("temp_f", "rh_pct", "wind_mph", "gust_mph", "wind_dir_deg",
                                  "fuel_moisture_1h_pct")} | {"santa_ana_level": g["santa_ana_level_now"]}

    def outlook(la, lo):
        g = nearest(la, lo)
        return {"max_gust_mph": g["max_gust_48h_mph"], "min_fuel_moisture_1h_pct": g["min_fuel_moisture_48h_pct"],
                "peak_santa_ana_level": g["santa_ana_level_peak_48h"], "worst_hour": g["worst_hour"],
                "wind_dir_deg_ahead": g["wind_dir_deg"]}

    flags = [(shape(f["geometry"]), f["properties"]["headline"])
             for f in _read("red_flags.geojson", {"features": []})["features"] if f.get("geometry")]

    def red_flag(la, lo):
        p = Point(lo, la)
        return next((h for g, h in flags if g.contains(p)), None)

    status = _read("status.json", {})
    return Snapshot(now.isoformat(), fires, weather, outlook, red_flag, source="live",
                    meta={"feeds_updated": status.get("updated")})
