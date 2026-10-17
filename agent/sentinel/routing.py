"""Evacuation routing on OpenStreetMap roads, planned against where the fire is going.

No third-party routing or traffic service. The router:
  * loads the road network around the house from OpenStreetMap (Overpass), cached on disk;
  * predicts when the fire front reaches every road node with the same wind/fuel spread model the alert levels
    use (signals.py), from the satellite detections in the snapshot;
  * runs a time-dependent shortest-path search: a road segment is only usable if the person could drive through
    it BEFORE the fire is projected to get there (with a safety margin), and segments closer to the front cost more;
  * slows roads inside the threatened zone by a modelled evacuation-congestion factor, and removes roads
    Caltrans reports fully closed.

Known limits (also in docs/IMPLEMENTATION_PLAN.md): there is no live traffic-speed feed, so jams are a model of
evacuation load, not a measurement; the spread model is crude; destinations are "safe road nodes", not shelters.
"""
import heapq
import json
import math
import time

import numpy as np
import requests

from . import config
from .config import ACTIVE_WINDOW_H
from .geo import angle_diff
from .signals import directional_ros, effective_wind_mph, head_ros_kmh

R_EARTH = 6371.0
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter")
MAJOR = "motorway|trunk|primary|secondary|tertiary"
MAJOR_RADIUS_M = 12000
MINOR_RADIUS_M = 1500           # residential streets only near the house, to keep the download small

# Free-flow speed (km/h) and modelled evacuation congestion (fraction of free-flow speed that remains when the
# area is being evacuated). The congestion numbers are placeholders to tune; there is no live speed feed.
SPEED = {"motorway": 100, "trunk": 80, "primary": 65, "secondary": 55, "tertiary": 48,
         "unclassified": 40, "residential": 30, "living_street": 15}
EVAC_SPEED_FACTOR = {"motorway": 0.60, "trunk": 0.55, "primary": 0.50, "secondary": 0.45, "tertiary": 0.40,
                     "unclassified": 0.35, "residential": 0.30, "living_street": 0.30}
CONGESTION_ZONE_H = 6.0         # roads the fire could reach within this long are assumed jammed
SAFE_ETA_H = 24.0               # a destination must be this far (in fire-time) from the front
MIN_ESCAPE_KM = 5.0             # ...and at least this far from the house
MARGIN_H = 0.25                 # required slack between our arrival at a node and the fire's
RISK_ZONE_H = 3.0               # nodes the fire reaches sooner than this carry a cost penalty
RISK_WEIGHT = 2.0
BLOCK_KM = 0.25                 # nodes this close to a Caltrans full closure are impassable
MAX_POPS = 400_000


def km_between(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(a))


def _bearing(lat1, lon1, lat2, lon2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


# ------------------------------------------------------------------ road graph

class Graph:
    """Directed road graph. adj[u] = [(v, km, class, label), ...]."""

    def __init__(self, lat, lon, adj):
        self.lat, self.lon, self.adj = np.asarray(lat), np.asarray(lon), adj


def _label(tags):
    ref = (tags.get("ref") or "").split(";")[0].strip()
    return ref or tags.get("name") or ""


def build_graph(elements):
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in elements if e["type"] == "node"}
    index, lat, lon = {}, [], []
    adj = []

    def idx(nid):
        if nid not in index:
            index[nid] = len(lat)
            lat.append(nodes[nid][0]); lon.append(nodes[nid][1]); adj.append([])
        return index[nid]

    for w in elements:
        if w["type"] != "way":
            continue
        t = w.get("tags", {})
        hw = (t.get("highway") or "").replace("_link", "")
        if hw not in SPEED or t.get("access") in ("private", "no") or t.get("area") == "yes":
            continue
        ow = t.get("oneway")
        forward = ow in ("yes", "true", "1") or t.get("junction") == "roundabout" or (hw == "motorway" and ow != "no")
        backward = ow == "-1"
        label = _label(t)
        ids = [n for n in w["nodes"] if n in nodes]
        for a, b in zip(ids, ids[1:]):
            km = km_between(*nodes[a], *nodes[b])
            u, v = idx(a), idx(b)
            if not backward:
                adj[u].append((v, km, hw, label))
            if not forward:
                adj[v].append((u, km, hw, label))
    return Graph(lat, lon, adj)


def _query(lat, lon):
    return (f'[out:json][timeout:90];('
            f'way["highway"~"^({MAJOR})(_link)?$"](around:{MAJOR_RADIUS_M},{lat},{lon});'
            f'way["highway"~"^(unclassified|residential)$"](around:{MINOR_RADIUS_M},{lat},{lon}););'
            f'(._;>;);out body qt;')


_graphs, _failed = {}, {}
FAIL_COOLDOWN_S = 600


def _cache_path(lat, lon):
    return config.ROOT / "state" / "osm" / f"{round(lat, 2):.2f}_{round(lon, 2):.2f}.json"


def load_graph(lat, lon, fetch=None, attempts=3):
    """Road graph around (lat, lon). Disk-cached; the first download can take a minute or two.
    Returns None if the road data can't be fetched (callers fall back to plain advice)."""
    key = _cache_path(lat, lon)
    if key in _graphs:
        return _graphs[key]
    if key.exists():
        elements = json.loads(key.read_text())
    else:
        if time.monotonic() - _failed.get(key, -1e9) < FAIL_COOLDOWN_S:
            return None
        elements = None
        for i in range(attempts):
            for url in OVERPASS:
                try:
                    if fetch:
                        elements = fetch(lat, lon)
                    else:
                        r = requests.post(url, data={"data": _query(lat, lon)}, timeout=150,
                                          headers={"User-Agent": "FireTraceSentinel-hackathon/1.0"})
                        r.raise_for_status()
                        elements = r.json()["elements"]
                    break
                except Exception:
                    elements = None
            if elements:
                break
            time.sleep(0 if fetch else 5 * (i + 1))
        if not elements:
            _failed[key] = time.monotonic()
            return None
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_text(json.dumps(elements))
    _graphs[key] = build_graph(elements)
    return _graphs[key]


# ------------------------------------------------------------------ fire timing

def fire_eta_hours(snap, origin, lats, lons):
    """Hours until the projected fire front reaches each (lat, lon), from the snapshot's active fire points.
    Same ellipse spread model as signals.assess; inf where there is no active fire."""
    f = snap.fires
    out = np.full(len(lats), np.inf)
    act = f.age_h <= ACTIVE_WINDOW_H
    if not act.any():
        return out
    flat, flon = np.asarray(f.lat)[act], np.asarray(f.lon)[act]
    # thin to one point per ~250 m cell so the node x fire matrix stays small
    cell = np.unique(np.round(flat / 0.0023).astype(int) * 100003 + np.round(flon / 0.0028).astype(int),
                     return_index=True)[1]
    flat, flon = flat[cell], flon[cell]
    wx = snap.weather(*origin)
    head, wind = head_ros_kmh(wx), effective_wind_mph(wx)
    wind_to = (wx["wind_dir_deg"] + 180.0) % 360.0
    kx, ky = 111.32 * math.cos(math.radians(origin[0])), 110.57
    for s in range(0, len(lats), 2000):
        la, lo = np.asarray(lats[s:s + 2000])[:, None], np.asarray(lons[s:s + 2000])[:, None]
        dx, dy = (flon[None, :] - lo) * kx, (flat[None, :] - la) * ky           # node -> fire
        dist = np.hypot(dx, dy)
        from_fire = (np.degrees(np.arctan2(-dx, -dy)) + 360.0) % 360.0           # bearing of node seen from fire
        ros = directional_ros(head, wind, angle_diff(from_fire, wind_to))
        out[s:s + 2000] = (dist / np.maximum(ros, 1e-3)).min(axis=1)
    return out


# ------------------------------------------------------------------ search

def _search(g, src, eta, blocked, zone_jam, margin):
    n = len(g.lat)
    best = {src: 0.0}
    prev = {}
    heap = [(0.0, 0.0, src)]
    pops = 0
    while heap and pops < MAX_POPS:
        prio, t, u = heapq.heappop(heap)
        if best.get(u, 1e18) < t - 1e-12:
            continue
        pops += 1
        if (u != src and eta[u] >= SAFE_ETA_H and
                km_between(g.lat[src], g.lon[src], g.lat[u], g.lon[u]) >= MIN_ESCAPE_KM):
            return u, t, prev
        for v, km, cls, label in g.adj[u]:
            if blocked[v]:
                continue
            speed = SPEED[cls] * (EVAC_SPEED_FACTOR[cls] if zone_jam and eta[u] < CONGESTION_ZONE_H else 1.0)
            dt = km / speed
            tv = t + dt
            if tv + margin > eta[v]:
                continue
            risk = max(0.0, (RISK_ZONE_H - eta[v]) / RISK_ZONE_H)
            pv = prio + dt * (1.0 + RISK_WEIGHT * risk)
            if tv < best.get(v, 1e18) - 1e-12:
                best[v] = tv
                prev[v] = (u, km, label)
                heapq.heappush(heap, (pv, tv, v))
    return None, None, prev


def _compass(deg):
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int(((deg or 0) + 22.5) // 45) % 8]


def best_route(snap, lat, lon, blocks=None, now_iso=None, graph=None, zone_jam=None):
    """Fastest route to a safe node that stays ahead of the fire. None if no road data or no way out.

    blocks: [(lat, lon)] points of full road closures. zone_jam: model evacuation congestion (default: on when
    the house itself is inside the fire's 6-hour zone).
    """
    g = graph or load_graph(lat, lon)
    if g is None or len(g.lat) == 0:
        return None
    src = int(np.argmin((g.lat - lat) ** 2 + ((g.lon - lon) * math.cos(math.radians(lat))) ** 2))
    eta = fire_eta_hours(snap, (lat, lon), g.lat, g.lon)
    blocked = np.zeros(len(g.lat), dtype=bool)
    for bl, bo in (blocks or []):
        if abs(bl - lat) < 0.5 and abs(bo - lon) < 0.5:
            blocked |= (np.hypot((g.lat - bl) * 110.57, (g.lon - bo) * 111.32 * math.cos(math.radians(bl)))
                        <= BLOCK_KM)
    blocked[src] = False
    if zone_jam is None:
        zone_jam = bool(eta[src] < CONGESTION_ZONE_H)
    # Strict first: every segment passable before the fire. If that's impossible, take the least-bad way out
    # and say so.
    dest, t, prev = _search(g, src, eta, blocked, zone_jam, MARGIN_H)
    risky = False
    if dest is None:
        dest, t, prev = _search(g, src, eta, blocked, zone_jam, -1e9)
        risky = True
    if dest is None:
        return None
    path, dist_by_label, node, km_total = [dest], {}, dest, 0.0
    while node != src:
        u, km, label = prev[node]
        km_total += km
        if label:
            dist_by_label[label] = dist_by_label.get(label, 0.0) + km
        path.append(u)
        node = u
    path.reverse()
    roads = [k for k, _ in sorted(dist_by_label.items(), key=lambda kv: -kv[1])[:2]]
    picks = ([path[i] for i in np.unique(np.linspace(1, len(path) - 2, 8).astype(int))]
             if len(path) > 2 else [])
    brg = _bearing(lat, lon, g.lat[dest], g.lon[dest])
    return {"summary": ("via " + ", ".join(roads)) if roads else "away from the fire",
            "duration_min": t * 60.0, "distance_km": km_total, "origin": [lat, lon],
            "dest": [float(g.lat[dest]), float(g.lon[dest])],
            "dest_label": f"safe area {km_between(lat, lon, g.lat[dest], g.lon[dest]):.0f} km {_compass(brg)}",
            "waypoints": [[float(g.lat[p]), float(g.lon[p])] for p in picks],
            "fire_risk": risky, "congestion_modelled": bool(zone_jam), "computed_at": now_iso}
