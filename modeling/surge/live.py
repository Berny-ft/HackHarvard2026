"""Live feeds for the East Coast: active storms, water levels, coastal warnings.

  NHC CurrentStorms.json      active Atlantic storms, position and intensity
  NHC forecast cone (GIS)     5-day cone and track for each active storm
  NOAA CO-OPS                 last 48 h observed water level + tide predictions
                              for the next 48 h at every gauge in config.LIVE_GAUGES
  NWS api.weather.gov alerts  Storm Surge / Hurricane / Tropical Storm / Coastal
                              Flood watches and warnings, with zone polygons

Writes out/surge/live/*.json and status.json.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import requests

from .config import LIVE_BBOX, LIVE_GAUGES, OUT

LIVE = OUT / "live"
TIMEOUT = 40
HEADERS = {"User-Agent": f"hackharvard-surgetrace ({os.environ.get('NWS_CONTACT', 'hackathon project')})"}
COOPS = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
EVENTS = ["Storm Surge Warning", "Storm Surge Watch", "Hurricane Warning", "Hurricane Watch",
          "Tropical Storm Warning", "Tropical Storm Watch", "Coastal Flood Warning", "Coastal Flood Advisory",
          "Coastal Flood Watch", "Coastal Flood Statement"]
STATES = ["FL", "GA", "SC", "NC", "VA", "MD", "DE", "NJ", "NY", "CT", "RI", "MA", "NH", "ME", "DC", "PA"]


def _get(url, **kw):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def fetch_storms():
    """Active Atlantic storms (al*), with their forecast track points when NHC publishes them."""
    j = _get("https://www.nhc.noaa.gov/CurrentStorms.json").json()
    out = []
    for s in j.get("activeStorms", []):
        if not s["id"].startswith("al"):
            continue
        out.append({"id": s["id"], "name": s["name"], "classification": s["classification"],
                    "vmax_kt": int(s["intensity"]), "pmin_mb": int(s["pressure"]) if s.get("pressure") else None,
                    "lat": s["latitudeNumeric"], "lon": s["longitudeNumeric"],
                    "movement_dir": s.get("movementDir"), "movement_kt": s.get("movementSpeed"),
                    "updated": s.get("lastUpdate"), "advisory": (s.get("publicAdvisory") or {}).get("url")})
    return out


def fetch_gauges():
    now = datetime.now(timezone.utc)
    begin = (now - timedelta(hours=48)).strftime("%Y%m%d %H:%M")
    end = (now + timedelta(hours=48)).strftime("%Y%m%d %H:%M")
    out = []
    for gid, name in LIVE_GAUGES.items():
        base = {"station": gid, "datum": "NAVD", "units": "english", "time_zone": "gmt", "format": "json",
                "application": "hackharvard-surgetrace"}
        try:
            obs = _get(COOPS, params={**base, "product": "water_level", "begin_date": begin,
                                      "end_date": now.strftime("%Y%m%d %H:%M")}).json()
            pred = _get(COOPS, params={**base, "product": "predictions", "interval": "h",
                                       "begin_date": begin, "end_date": end}).json()
        except requests.RequestException:
            continue
        if "data" not in obs or "predictions" not in pred:
            continue
        meta = obs["metadata"]
        o = [(r["t"], float(r["v"])) for r in obs["data"] if r["v"] and r["t"].endswith(":00")]   # 6-min -> hourly
        p = {r["t"]: float(r["v"]) for r in pred["predictions"]}
        last_t, last_v = o[-1] if o else (None, None)
        resid = [v - p[t] for t, v in o if t in p]
        out.append({
            "id": gid, "name": name, "lat": float(meta["lat"]), "lon": float(meta["lon"]),
            "latest_ft": last_v, "latest_time": last_t, "anomaly_ft": round(resid[-1], 2) if resid else None,
            "max_anomaly_48h_ft": round(max(resid), 2) if resid else None,
            "observed": [{"t": t, "v": v} for t, v in o],
            "tide": [{"t": t, "v": v} for t, v in sorted(p.items())],
            "next_high_ft": max((v for t, v in p.items() if t > (last_t or "")), default=None),
        })
    return out


def _zone_geometry(url, cache={}):
    from shapely.geometry import mapping, shape

    if url not in cache:
        try:
            g = _get(url).json().get("geometry")
            cache[url] = g and mapping(shape(g).simplify(0.004, preserve_topology=True))
        except requests.RequestException:
            cache[url] = None
    return cache[url]


def fetch_alerts():
    feats = []
    for state in STATES:
        fc = _get("https://api.weather.gov/alerts/active", params={"area": state}).json()
        for f in fc.get("features", []):
            p = f["properties"]
            if p["event"] not in EVENTS:
                continue
            props = {k: p.get(k) for k in ("event", "headline", "severity", "onset", "ends", "expires", "areaDesc")}
            props["state"] = state
            if f.get("geometry"):
                feats.append({"type": "Feature", "geometry": f["geometry"], "properties": props})
                continue
            for zone in p.get("affectedZones", [])[:40]:
                geom = _zone_geometry(zone)
                if geom:
                    feats.append({"type": "Feature", "geometry": geom, "properties": {**props, "zone": zone.rsplit("/", 1)[-1]}})
    return {"type": "FeatureCollection", "features": feats}


def run():
    LIVE.mkdir(parents=True, exist_ok=True)
    status = {"updated": datetime.now(timezone.utc).isoformat(), "bbox": LIVE_BBOX, "sources": {}}

    def step(name, fn, filename, summary):
        try:
            res = fn()
            (LIVE / filename).write_text(json.dumps(res, separators=(",", ":")))
            status["sources"][name] = {"ok": True, "summary": summary(res)}
        except Exception as e:   # one feed down must not stop the others
            status["sources"][name] = {"ok": False, "error": str(e)[:200]}
        print(f"[surge live] {name}: {status['sources'][name]}", flush=True)

    step("nhc_storms", fetch_storms, "storms.json",
         lambda r: f"{len(r)} active Atlantic storm{'s' if len(r) != 1 else ''}")
    step("water_levels", fetch_gauges, "gauges.json",
         lambda r: f"{len(r)} gauges, highest anomaly {max((g['anomaly_ft'] or 0) for g in r):+.1f} ft" if r else "none")
    step("nws_alerts", fetch_alerts, "alerts.geojson",
         lambda r: f"{len({f['properties']['headline'] for f in r['features']})} coastal alerts over {len(r['features'])} zones")
    (LIVE / "status.json").write_text(json.dumps(status, indent=1))
