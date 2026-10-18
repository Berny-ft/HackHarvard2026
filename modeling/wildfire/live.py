"""Live feeds: forecast weather, Red Flag Warnings, smoke, satellite hot spots,
current perimeters, active incidents, and (cached) power lines + roads.

Writes JSON/GeoJSON to out/live/ for the web app. Static layers (fuels,
terrain, fire history) come from the main pipeline.

FIRMS needs a free key in modeling/.env:  FIRMS_MAP_KEY=...
"""
import csv
import io
import json
import os
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

from .config import BBOX_LONLAT, DATA, OUT, ROOT
from .fireweather import TIER_NAMES, dead_fuel_moisture_1h_pct, santa_ana_level

WEST, SOUTH, EAST, NORTH = BBOX_LONLAT
LIVE = OUT / "live"
TIMEOUT = 30
HOURLY = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m",
          "wind_gusts_10m", "precipitation", "vapour_pressure_deficit", "soil_moisture_0_to_1cm"]


def load_env(path=ROOT / ".env"):
    """Minimal .env reader so keys don't need to be exported by hand."""
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
# api.weather.gov asks for a contact in the User-Agent; set NWS_CONTACT in .env.
HEADERS = {"User-Agent": f"hackharvard-firetrace ({os.environ.get('NWS_CONTACT', 'hackathon project')})"}


def _get(url, **kw):
    r = requests.get(url, timeout=kw.pop("timeout", TIMEOUT), **kw)
    r.raise_for_status()
    return r


def grid_points(step=0.25):
    lats = np.arange(SOUTH, NORTH + 1e-9, step)
    lons = np.arange(WEST, EAST + 1e-9, step)
    return [(round(float(la), 3), round(float(lo), 3)) for la in lats for lo in lons]


# ---------------------------------------------------------------- weather

def fetch_forecast(points, forecast_hours=48, past_hours=0):
    """Current conditions + hourly forecast for many points in one request (°F, mph)."""
    data = _get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": ",".join(str(p[0]) for p in points),
        "longitude": ",".join(str(p[1]) for p in points),
        "current": ",".join(HOURLY[:6]),
        "hourly": ",".join(HOURLY),
        "forecast_hours": forecast_hours,
        "past_hours": past_hours,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "America/Los_Angeles",
    }).json()
    return data if isinstance(data, list) else [data]


def point_forecast(lon, lat, hours=72, spinup=120):
    """Hourly weather for one point, shaped like the spread model's input.

    Includes `spinup` past hours so 10-h/100-h fuel moisture can equilibrate.
    """
    loc = fetch_forecast([(lat, lon)], forecast_hours=hours + 1, past_hours=spinup)[0]
    df = pd.DataFrame(loc["hourly"])
    df["time"] = pd.to_datetime(df["time"])
    return df


def weather_grid():
    pts = grid_points()
    rows = []
    for loc in fetch_forecast(pts):
        c, h = loc["current"], loc["hourly"]
        if None in (c.get("temperature_2m"), c.get("relative_humidity_2m"),
                    c.get("wind_gusts_10m"), c.get("wind_direction_10m")):
            continue
        hl = santa_ana_level(np.array(h["wind_direction_10m"], float),
                             np.array(h["wind_gusts_10m"], float),
                             np.array(h["relative_humidity_2m"], float))
        fm = dead_fuel_moisture_1h_pct(h["temperature_2m"], h["relative_humidity_2m"])
        peak = int(np.argmax(hl)) if hl.max() > 0 else int(np.argmin(fm))
        level_now = int(santa_ana_level(c["wind_direction_10m"], c["wind_gusts_10m"], c["relative_humidity_2m"]))
        rows.append({
            "lat": loc["latitude"], "lon": loc["longitude"],
            "temp_f": c["temperature_2m"], "rh_pct": c["relative_humidity_2m"],
            "wind_mph": c["wind_speed_10m"], "gust_mph": c["wind_gusts_10m"],
            "wind_dir_deg": c["wind_direction_10m"],
            "fuel_moisture_1h_pct": float(dead_fuel_moisture_1h_pct(c["temperature_2m"], c["relative_humidity_2m"])),
            "santa_ana_now": TIER_NAMES[level_now],
            "santa_ana_level_now": level_now,
            "santa_ana_peak_48h": TIER_NAMES[int(hl.max())],
            "santa_ana_level_peak_48h": int(hl.max()),
            "worst_hour": h["time"][peak],
            "min_fuel_moisture_48h_pct": float(fm.min()),
            "max_gust_48h_mph": float(np.nanmax(np.array(h["wind_gusts_10m"], float))),
        })
    return rows


def fetch_air_quality():
    pts = grid_points(step=0.5)
    data = _get("https://air-quality-api.open-meteo.com/v1/air-quality", params={
        "latitude": ",".join(str(p[0]) for p in pts),
        "longitude": ",".join(str(p[1]) for p in pts),
        "current": "pm2_5,us_aqi",
        "timezone": "America/Los_Angeles",
    }).json()
    data = data if isinstance(data, list) else [data]
    return [{"lat": d["latitude"], "lon": d["longitude"], **d.get("current", {})} for d in data]


# ---------------------------------------------------------------- alerts + fires

def _zone_geometry(url, cache={}):
    if url not in cache:
        cache[url] = _get(url, headers={**HEADERS, "Accept": "application/geo+json"}).json().get("geometry")
    return cache[url]


def fetch_red_flag_warnings():
    """Active NWS Red Flag Warnings touching LA or Ventura counties (GeoJSON).

    Warnings are issued per fire-weather zone and usually carry no geometry,
    so each affected zone's polygon is fetched and attached.
    """
    fc = _get("https://api.weather.gov/alerts/active", params={"area": "CA", "event": "Red Flag Warning"},
              headers={**HEADERS, "Accept": "application/geo+json"}).json()
    feats = []
    for f in fc.get("features", []):
        p = f["properties"]
        if not any(c in p.get("areaDesc", "") for c in ("Los Angeles", "Ventura", "Santa Monica", "Santa Clarita")):
            continue
        props = {k: p.get(k) for k in ("headline", "areaDesc", "onset", "ends", "severity", "description")}
        if f.get("geometry"):
            feats.append({"type": "Feature", "geometry": f["geometry"], "properties": props})
            continue
        for zone in p.get("affectedZones", []):
            geom = _zone_geometry(zone)
            if geom:
                feats.append({"type": "Feature", "geometry": geom, "properties": {**props, "zone": zone.rsplit("/", 1)[-1]}})
    return {"type": "FeatureCollection", "features": feats}


def fetch_firms_hotspots(days=2, sources=("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")):
    """Satellite hot spots (GeoJSON). Needs FIRMS_MAP_KEY."""
    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        raise RuntimeError("no FIRMS_MAP_KEY in modeling/.env")
    feats = []
    for src in sources:
        text = _get(f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/"
                    f"{WEST},{SOUTH},{EAST},{NORTH}/{days}").text
        if text.lstrip().startswith(("Invalid", "<")):
            raise RuntimeError(f"FIRMS rejected the request: {text[:120]}")
        for r in csv.DictReader(io.StringIO(text)):
            t = f"{r['acq_date']}T{r['acq_time'].zfill(4)[:2]}:{r['acq_time'].zfill(4)[2:]}:00Z"
            feats.append({"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [float(r["longitude"]), float(r["latitude"])]},
                          "properties": {"time_utc": t, "frp_mw": float(r.get("frp") or 0),
                                         "confidence": r.get("confidence"), "source": src,
                                         "day_night": r.get("daynight")}})
    return {"type": "FeatureCollection", "features": feats}


def _arcgis_geojson(url):
    return _get(url, params={
        "where": "1=1", "geometry": f"{WEST},{SOUTH},{EAST},{NORTH}",
        "geometryType": "esriGeometryEnvelope", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "outSR": 4326, "f": "geojson",
    }).json()


def fetch_current_perimeters():
    return _arcgis_geojson("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
                           "WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query")


def fetch_current_incidents():
    """Active incident points: NIFC WFIGS (stable) first, CAL FIRE site feed as extra."""
    fc = _arcgis_geojson("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
                         "WFIGS_Incident_Locations_Current/FeatureServer/0/query")
    out = []
    for f in fc.get("features", []):
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        out.append({"name": p.get("IncidentName"), "lon": lon, "lat": lat,
                    "acres": p.get("IncidentSize"), "contained_pct": p.get("PercentContained"),
                    "discovered": p.get("FireDiscoveryDateTime"), "cause": p.get("FireCause"),
                    "source": "NIFC WFIGS"})
    try:  # undocumented website endpoint: nice to have, may change
        items = _get("https://www.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=false", headers=HEADERS).json()
        seen = {(o["name"] or "").lower() for o in out}
        for i in items:
            lat, lon = i.get("Latitude"), i.get("Longitude")
            if lat and lon and SOUTH <= lat <= NORTH and WEST <= lon <= EAST and i.get("Name", "").lower() not in seen:
                out.append({"name": i.get("Name"), "lon": lon, "lat": lat, "acres": i.get("AcresBurned"),
                            "contained_pct": i.get("PercentContained"), "discovered": i.get("Started"),
                            "url": i.get("Url"), "source": "CAL FIRE"})
    except Exception as e:
        print(f"  (CAL FIRE feed skipped: {e})")
    return out


# ---------------------------------------------------------------- static-ish infra

def fetch_osm_infrastructure(force=False):
    """Power lines + major roads from OpenStreetMap, cached as GeoJSON (slow)."""
    path = DATA / "osm_infra.geojson"
    if path.exists() and not force:
        return json.loads(path.read_text())
    q = f"""[out:json][timeout:180];
    (way["power"="line"]({SOUTH},{WEST},{NORTH},{EAST});
     way["highway"~"^(motorway|trunk|primary|secondary)$"]({SOUTH},{WEST},{NORTH},{EAST}););
    out geom;"""
    els = requests.post("https://overpass-api.de/api/interpreter", data={"data": q},
                        headers=HEADERS, timeout=240).json()["elements"]
    feats = [{"type": "Feature",
              "geometry": {"type": "LineString", "coordinates": [[g["lon"], g["lat"]] for g in e["geometry"]]},
              "properties": {"kind": "power_line" if "power" in e["tags"] else "road",
                             "class": e["tags"].get("highway") or e["tags"].get("voltage"),
                             "name": e["tags"].get("name")}}
             for e in els if e.get("geometry")]
    fc = {"type": "FeatureCollection", "features": feats}
    path.write_text(json.dumps(fc))
    return fc


# Drivable classes for fire-safe routing; ramps keep freeway interchanges connected.
# The graph is read by agent/routing (graph.py), which mirrors these speeds for
# streets it adds near a start point.
ROUTE_CLASSES = "motorway|trunk|primary|secondary|tertiary"
ALL_CLASSES = ROUTE_CLASSES + "|unclassified|residential"      # small areas (storm boxes) can afford streets
DEFAULT_MPH = {"motorway": 65, "trunk": 55, "primary": 40, "secondary": 35, "tertiary": 30,
               "unclassified": 25, "residential": 20}
ROADS_GRAPH = DATA / "roads_graph.json"


def _speed_mph(tags, cls):
    m = tags.get("maxspeed", "").split()
    if m and m[0].isdigit():
        return int(m[0]) if "km/h" not in tags["maxspeed"] else round(int(m[0]) * 0.621)
    return DEFAULT_MPH.get(cls.removesuffix("_link"), 30) * (0.6 if cls.endswith("_link") else 1)


def fetch_road_graph(force=False, bbox=BBOX_LONLAT, path=ROADS_GRAPH, classes=ROUTE_CLASSES, grid=(4, 5),
                     tiles_dir=None):
    """Road network from OpenStreetMap as a routing graph (slow, cached).

    Nodes are OSM junctions and way ends (with their OSM ids, so the router can
    join extra streets to them); each edge keeps its polyline, name and route
    number so the router can test it against hazard rasters, draw the chosen
    path and say "Merge onto I-210".
    """
    if path.exists() and not force:
        return json.loads(path.read_text())
    west, south, east, north = bbox
    # Overpass drops whole-box responses for big areas, so fetch in tiles (cached,
    # so a rerun resumes after the public server's frequent 502/504s) and merge ways by id.
    tiles = tiles_dir or DATA / "road_tiles"
    tiles.mkdir(parents=True, exist_ok=True)
    by_id = {}
    lats, lons = np.linspace(south, north, grid[0]), np.linspace(west, east, grid[1])
    for s, n in zip(lats[:-1], lats[1:]):
        for w_, e in zip(lons[:-1], lons[1:]):
            tile = tiles / f"{s:.3f}_{w_:.3f}.json"
            if not tile.exists():
                q = f"""[out:json][timeout:180];
                way["highway"~"^({classes})(_link)?$"]({s},{w_},{n},{e});
                out body geom qt;"""
                for attempt in range(5):
                    r = requests.post("https://overpass-api.de/api/interpreter", data={"data": q},
                                      headers=HEADERS, timeout=300)
                    if r.ok and r.content:
                        break
                    print(f"  [roads] tile {s:.2f},{w_:.2f}: HTTP {r.status_code}, retrying")
                    time.sleep(15 * (attempt + 1))
                r.raise_for_status()
                tile.write_bytes(r.content)
            for el in json.loads(tile.read_bytes())["elements"]:
                if el.get("geometry"):
                    by_id[el["id"]] = el
            print(f"  [roads] tile {s:.2f},{w_:.2f}: {len(by_id)} ways so far")
    ways = list(by_id.values())
    uses = {}
    for w in ways:
        for i, n in enumerate(w["nodes"]):
            uses[n] = uses.get(n, 0) + (2 if i in (0, len(w["nodes"]) - 1) else 1)
    index, nodes, ids, edges = {}, [], [], []

    def node(nid, g):
        if nid not in index:
            index[nid] = len(nodes)
            nodes.append([round(g["lon"], 6), round(g["lat"], 6)])
            ids.append(nid)
        return index[nid]

    for w in ways:
        t = w["tags"]
        cls = t["highway"]
        ow = t.get("oneway", "yes" if cls in ("motorway", "motorway_link") or t.get("junction") == "roundabout"
                   else "no")
        mph = _speed_mph(t, cls)
        start = 0
        for i in range(1, len(w["nodes"])):
            if uses[w["nodes"][i]] < 2 and i < len(w["nodes"]) - 1:
                continue
            seg = w["geometry"][start:i + 1]
            if ow == "-1":
                seg = seg[::-1]
            a, b = (start, i) if ow != "-1" else (i, start)
            edges.append({"u": node(w["nodes"][a], w["geometry"][a]), "v": node(w["nodes"][b], w["geometry"][b]),
                          "oneway": ow in ("yes", "true", "1", "-1"), "class": cls, "mph": mph,
                          "name": t.get("name"), "ref": (t.get("ref") or "").split(";")[0].strip() or None,
                          "bridge": t.get("bridge", "no") not in ("no", "") or None,
                          "tunnel": t.get("tunnel", "no") in ("yes", "building_passage") or None,
                          "coords": [[round(g["lon"], 6), round(g["lat"], 6)] for g in seg]})
            start = i
    graph = {"source": "OpenStreetMap via Overpass", "fetched": datetime.now(timezone.utc).isoformat(),
             "bbox": list(bbox), "nodes": nodes, "ids": ids, "edges": edges}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph))
    return graph


# ---------------------------------------------------------------- main

def run(include_osm=False):
    LIVE.mkdir(parents=True, exist_ok=True)
    status = {"updated": datetime.now(timezone.utc).isoformat(), "sources": {}}

    def step(name, fn, filename, summary):
        try:
            result = fn()
            (LIVE / filename).write_text(json.dumps(result))
            status["sources"][name] = {"ok": True, "summary": summary(result)}
        except Exception as e:
            status["sources"][name] = {"ok": False, "error": str(e)[:200]}
        s = status["sources"][name]
        print(f"  [{'ok' if s['ok'] else 'fail'}] {name}: {s.get('summary') or s.get('error')}")

    print("[live]")
    step("weather", weather_grid, "weather.json",
         lambda r: f"{len(r)} points, min 1-h fuel moisture {min(p['fuel_moisture_1h_pct'] for p in r)}%, "
                   f"Santa Ana now at {sum(p['santa_ana_level_now'] > 0 for p in r)}, "
                   f"forecast within 48h at {sum(p['santa_ana_level_peak_48h'] > 0 for p in r)}")
    step("red_flags", fetch_red_flag_warnings, "red_flags.geojson",
         lambda r: f"{len({f['properties']['headline'] for f in r['features']})} active over {len(r['features'])} zones")
    step("air_quality", fetch_air_quality, "air_quality.json",
         lambda r: f"max US AQI {max((p.get('us_aqi') or 0) for p in r)}")
    step("hotspots", fetch_firms_hotspots, "hotspots.geojson", lambda r: f"{len(r['features'])} in last 48h")
    step("perimeters", fetch_current_perimeters, "perimeters.geojson", lambda r: f"{len(r.get('features', []))} current")
    step("incidents", fetch_current_incidents, "incidents.json", lambda r: f"{len(r)} active in box")
    if include_osm:
        # Full roads + lines stay in data/; the web app only needs power lines.
        step("power_lines", lambda: {"type": "FeatureCollection", "features": [
            f for f in fetch_osm_infrastructure()["features"] if f["properties"]["kind"] == "power_line"]},
             "power_lines.geojson", lambda r: f"{len(r['features'])} power lines")
    (LIVE / "status.json").write_text(json.dumps(status, indent=1))
