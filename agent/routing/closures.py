"""Road closures from Caltrans' public lane-closure feed (District 7 = Los Angeles + Ventura).

Only closures in force right now that stop all traffic (Full / One-Way Traffic on the mainline) are treated as
blocks. Lane closures slow traffic, which Google's live-traffic times already reflect.
"""
import time

import requests

URL = "https://cwwp2.dot.ca.gov/data/d7/lcs/lcsStatusD07.json"
CACHE_S = 300
MAX_SPAN_KM = 5.0       # beyond this, begin/end coordinates are not trusted to lie on one straight road
STEP_KM = 0.1          # spacing of points along a closed stretch
_cache = {"t": -1e9, "rows": []}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse(data, now_epoch):
    """Active full mainline closures as [{lat, lon, end_lat, end_lon, route, where, type}]."""
    out = []
    for row in data:
        lcs = row.get("lcs") or {}
        c = lcs.get("closure") or {}
        if c.get("typeOfClosure") not in ("Full", "One-Way Traffic") or c.get("facility") != "Mainline":
            continue
        t = c.get("closureTimestamp") or {}
        start, end = _f(t.get("closureStartEpoch")), _f(t.get("closureEndEpoch"))
        indefinite = str(t.get("isClosureEndIndefinite")).lower() == "true"
        if start is None or start > now_epoch or not (indefinite or (end is not None and end >= now_epoch)):
            continue
        loc = lcs.get("location") or {}
        b, e = loc.get("begin") or {}, loc.get("end") or {}
        lat, lon = _f(b.get("beginLatitude")), _f(b.get("beginLongitude"))
        if lat is None or lon is None:
            continue
        out.append({"lat": lat, "lon": lon,
                    "end_lat": _f(e.get("endLatitude")) or lat, "end_lon": _f(e.get("endLongitude")) or lon,
                    "route": b.get("beginRoute"), "where": b.get("beginLocationName"),
                    "type": c.get("typeOfClosure")})
    return out


def block_points(closures):
    """Points every ~100 m along each closed stretch, for the route clearance check."""
    from .geo import km_between
    pts = []
    for c in closures:
        length = km_between(c["lat"], c["lon"], c["end_lat"], c["end_lon"])
        if length > MAX_SPAN_KM:         # a straight line across a long span would invent road; keep the endpoints
            pts += [(c["lat"], c["lon"]), (c["end_lat"], c["end_lon"])]
            continue
        n = max(1, int(length / STEP_KM))
        for i in range(n + 1):
            f = i / n
            pts.append((c["lat"] + (c["end_lat"] - c["lat"]) * f, c["lon"] + (c["end_lon"] - c["lon"]) * f))
    return pts


def active_closures(fetch=None, now_epoch=None):
    """Cached for 5 minutes. A failed fetch returns the last good list (or []), never raises."""
    now_epoch = now_epoch or time.time()
    if fetch is None and time.monotonic() - _cache["t"] < CACHE_S:
        return _cache["rows"]
    try:
        data = fetch() if fetch else requests.get(URL, timeout=6).json()["data"]
        rows = parse(data, now_epoch)
    except Exception:
        return _cache["rows"]
    if fetch is None:
        _cache.update(t=time.monotonic(), rows=rows)
    return rows
