"""Road graphs for the router.

Prebuilt graphs (major roads for a whole region, from `python -m wildfire roads`
or `python -m surge roads`) load once and are cached. Residential streets near a
start point come from OpenStreetMap on demand and are joined to the prebuilt
graph by shared OSM node ids; if that download fails the router carries on with
the major roads alone.

Every edge keeps its polyline: the router samples it every 100 m to test it
against a hazard, draws it on the map, and reads turns off it for directions.
"""
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import requests

from . import config
from .geo import KM_PER_DEG_LAT, km_per_deg_lon

KMH_PER_MPH = 1.609344
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter")
HEADERS = {"User-Agent": "FireTrace-hackathon/1.0"}


@dataclass
class RoadGraph:
    nodes: np.ndarray        # (n, 2) lon, lat
    ids: list                # OSM node id per node (None for synthetic nodes)
    u: np.ndarray            # edge start node
    v: np.ndarray            # edge end node
    hours: np.ndarray        # free-flow travel time
    km: np.ndarray
    name: list
    ref: list                # route number, e.g. "I-210"
    cls: list
    coords: list             # polyline per edge, u -> v
    pts: np.ndarray          # (m, 2) sample points along every edge
    pt_edge: np.ndarray      # edge index of each sample
    out: list                # node -> [(edge, to_node, reversed)]
    edges: list              # the raw edge dicts (kept so local streets can be appended)
    bridge: np.ndarray       # OSM bridge=* (storm winds close these)
    tunnel: np.ndarray       # OSM tunnel=yes (flooding closes these)

    def __len__(self):
        return len(self.u)


def _fmt_ref(ref):
    """'I 210' -> 'I-210', 'CA 2' -> 'CA-2'; OSM lists several refs with ';'."""
    if not ref:
        return None
    r = ref.split(";")[0].strip()
    parts = r.split()
    return "-".join(parts) if len(parts) == 2 and parts[1][:1].isdigit() else r


def _densify(edges):
    """Sample points every <= SAMPLE_KM along a list of edges, in one vectorised pass."""
    S, T, eid = [], [], []
    for i, e in enumerate(edges):
        c = e["coords"]
        S += c[:-1]; T += c[1:]; eid += [i] * (len(c) - 1)
    if not S:
        return np.zeros(len(edges)), np.zeros((0, 2)), np.zeros(0, int)
    S, T, eid = np.array(S, float), np.array(T, float), np.array(eid)
    seg = np.hypot((T[:, 0] - S[:, 0]) * km_per_deg_lon(S[:, 1]), (T[:, 1] - S[:, 1]) * KM_PER_DEG_LAT)
    km = np.bincount(eid, seg, minlength=len(edges))
    n = np.maximum(1, np.ceil(seg / config.SAMPLE_KM)).astype(int)
    rep = np.repeat(np.arange(len(seg)), n)
    frac = (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)) / n[rep]
    pts = S[rep] + (T[rep] - S[rep]) * frac[:, None]
    last = np.array([e["coords"][-1] for e in edges], float)
    return km, np.vstack([pts, last]), np.concatenate([eid[rep], np.arange(len(edges))])


def assemble(nodes, ids, edges, base=None):
    """Build a RoadGraph. With `base`, only the new edges (appended after base.edges) are densified."""
    nodes = np.asarray(nodes, float).reshape(-1, 2)
    start = len(base.edges) if base is not None else 0
    new = edges[start:]
    km_new, pts_new, pe_new = _densify(new)
    if base is not None:
        km = np.concatenate([base.km, km_new])
        pts = np.vstack([base.pts, pts_new]) if len(pts_new) else base.pts
        pt_edge = np.concatenate([base.pt_edge, pe_new + start])
        out = [list(o) for o in base.out] + [[] for _ in range(len(nodes) - len(base.out))]
    else:
        km, pts, pt_edge, out = km_new, pts_new, pe_new, [[] for _ in range(len(nodes))]
    for i, e in enumerate(new, start):
        out[e["u"]].append((i, e["v"], False))
        if not e["oneway"]:
            out[e["v"]].append((i, e["u"], True))
    mph = np.array([e["mph"] for e in edges], float)
    return RoadGraph(nodes, list(ids), np.array([e["u"] for e in edges], int), np.array([e["v"] for e in edges], int),
                     km / (mph * KMH_PER_MPH), km, [e.get("name") for e in edges],
                     [_fmt_ref(e.get("ref")) for e in edges], [e["class"] for e in edges],
                     [e["coords"] for e in edges], pts, pt_edge, out, edges,
                     np.array([bool(e.get("bridge")) for e in edges]), np.array([bool(e.get("tunnel")) for e in edges]))


@lru_cache(maxsize=8)
def load(path):
    """A prebuilt graph file (cached for the life of the process)."""
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{p} missing: build it with `python -m wildfire roads` (or `python -m surge roads`)")
    g = json.loads(p.read_text())
    return assemble(g["nodes"], g.get("ids") or [None] * len(g["nodes"]), g["edges"])


def region(name):
    return load(str(config.REGIONS[name]))


# ---------------------------------------------------------------- from raw OSM

def _speed(tags, cls):
    m = (tags.get("maxspeed") or "").split()
    if m and m[0].isdigit():
        return int(m[0]) if "km/h" not in tags["maxspeed"] else round(int(m[0]) * 0.621)
    return config.DEFAULT_MPH.get(cls.removesuffix("_link"), 25) * (0.6 if cls.endswith("_link") else 1)


def edges_from_osm(elements, index=None, nodes=None, ids=None):
    """Overpass elements (nodes + ways, `out body` with `>`) -> edges split at junctions.
    `index`/`nodes`/`ids` extend an existing node table (OSM id -> node index)."""
    index = {} if index is None else index
    nodes = [] if nodes is None else nodes
    ids = [] if ids is None else ids
    where = {e["id"]: (e["lon"], e["lat"]) for e in elements if e["type"] == "node"}
    ways = [w for w in elements if w["type"] == "way"]
    uses = {}
    for w in ways:
        ns = [n for n in w["nodes"] if n in where]
        for i, n in enumerate(ns):
            uses[n] = uses.get(n, 0) + (2 if i in (0, len(ns) - 1) else 1)
    for n in index:                       # nodes already in the graph are junctions by definition
        uses[n] = uses.get(n, 0) + 2

    def node(nid):
        if nid not in index:
            index[nid] = len(nodes)
            nodes.append([round(where[nid][0], 6), round(where[nid][1], 6)])
            ids.append(nid)
        return index[nid]

    edges = []
    for w in ways:
        t = w.get("tags", {})
        cls = t.get("highway") or ""
        if cls.removesuffix("_link") not in config.DEFAULT_MPH or t.get("access") in ("private", "no") \
                or t.get("area") == "yes":
            continue
        ns = [n for n in w["nodes"] if n in where]
        if len(ns) < 2:
            continue
        ow = t.get("oneway", "yes" if cls in ("motorway", "motorway_link") or t.get("junction") == "roundabout"
                   else "no")
        start = 0
        for i in range(1, len(ns)):
            if uses[ns[i]] < 2 and i < len(ns) - 1:
                continue
            seg = ns[start:i + 1]
            if ow == "-1":
                seg = seg[::-1]
            edges.append({"u": node(seg[0]), "v": node(seg[-1]), "oneway": ow in ("yes", "true", "1", "-1"),
                          "class": cls, "mph": _speed(t, cls), "name": t.get("name"), "ref": t.get("ref"),
                          "bridge": t.get("bridge", "no") not in ("no", ""), "tunnel": t.get("tunnel") == "yes",
                          "coords": [[round(where[n][0], 6), round(where[n][1], 6)] for n in seg]})
            start = i
    return edges, index, nodes, ids


def from_osm(elements):
    """A standalone graph from Overpass elements (tests, or anywhere without a prebuilt graph)."""
    edges, _, nodes, ids = edges_from_osm(elements)
    return assemble(nodes, ids, edges)


# ---------------------------------------------------------------- a region on demand

MAJOR_CLASSES = "motorway|trunk|primary|secondary|tertiary"
_region_inflight = set()


def fetch_region(bbox, path, classes=MAJOR_CLASSES, tile_deg=0.5):
    """Download a region's roads from Overpass in tiles and save a graph file (slow: minutes for a big area).
    Used for a live storm's warning area; tiles that fail are retried once, then skipped."""
    import math
    w, s, e, n = bbox
    nx, ny = max(1, math.ceil((e - w) / tile_deg)), max(1, math.ceil((n - s) / tile_deg))
    elements = {}
    for i in range(nx):
        for j in range(ny):
            tw, ts = w + i * (e - w) / nx, s + j * (n - s) / ny
            te, tn = tw + (e - w) / nx, ts + (n - s) / ny
            q = (f'[out:json][timeout:180];way["highway"~"^({classes})(_link)?$"]'
                 f'({ts},{tw},{tn},{te});(._;>;);out body qt;')
            for _ in range(2):
                try:
                    r = requests.post(OVERPASS[0], data={"data": q}, headers=HEADERS, timeout=200)
                    r.raise_for_status()
                    for el in r.json()["elements"]:
                        elements[(el["type"], el["id"])] = el
                    break
                except Exception:
                    time.sleep(10)
    edges, _, nodes, ids = edges_from_osm(list(elements.values()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source": "OpenStreetMap via Overpass", "bbox": list(bbox), "nodes": nodes,
                                "ids": ids, "edges": edges}))
    return path


def region_later(bbox, path, classes=MAJOR_CLASSES):
    """fetch_region on a background thread (once per path)."""
    import threading
    if path in _region_inflight or path.exists():
        return
    _region_inflight.add(path)

    def run():
        try:
            fetch_region(bbox, path, classes)
        finally:
            _region_inflight.discard(path)
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------- local streets

_local = OrderedDict()            # (base id, key) -> merged graph, a few kept
_failed = {}


def _local_cache(lat, lon):
    return config.STATE / "osm_local" / f"{lat:.2f}_{lon:.2f}.json"


def local_streets(lat, lon, fetch=None):
    """Residential streets within LOCAL_STREETS_KM of a point: cached on disk; None if unavailable."""
    path = _local_cache(lat, lon)
    if path.exists():
        return json.loads(path.read_bytes())
    if time.monotonic() - _failed.get(path, -1e9) < config.LOCAL_STREETS_COOLDOWN_S:
        return None
    q = (f'[out:json][timeout:{config.LOCAL_STREETS_TIMEOUT_S}];'
         f'way["highway"~"^(unclassified|residential|living_street)$"]'
         f'(around:{config.LOCAL_STREETS_KM * 1000:.0f},{lat},{lon});(._;>;);out body qt;')
    try:
        if fetch:
            elements = fetch(lat, lon)
        else:
            elements = None
            for url in OVERPASS:
                try:
                    r = requests.post(url, data={"data": q}, headers=HEADERS,
                                      timeout=config.LOCAL_STREETS_TIMEOUT_S + 5)
                    r.raise_for_status()
                    elements = r.json()["elements"]
                    break
                except Exception:
                    continue
        if not elements:
            raise RuntimeError("no streets")
    except Exception:
        _failed[path] = time.monotonic()
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(elements))
    return elements


_inflight = set()


def _fetch_later(lat, lon):
    """Download a spot's streets on a background thread, so the next route there can use them."""
    import threading
    spot = (lat, lon)
    if spot in _inflight:
        return
    _inflight.add(spot)

    def run():
        try:
            local_streets(lat, lon)
        finally:
            _inflight.discard(spot)
    threading.Thread(target=run, daemon=True).start()


def with_local_streets(base, lat, lon, fetch=None, wait=False):
    """The base graph plus residential streets around (lat, lon), joined at shared OSM nodes.

    Streets come from the disk cache. A spot seen for the first time is fetched in the background
    (Overpass can take 30 s or fail) and this call returns the base graph straight away, unless
    wait=True (setup steps, tests). Falls back to the base graph if the streets don't connect."""
    key = (id(base), round(lat, 2), round(lon, 2))
    if key in _local:
        _local.move_to_end(key)
        return _local[key]
    spot = (round(lat, 2), round(lon, 2))
    if not wait and fetch is None and not _local_cache(*spot).exists():
        _fetch_later(*spot)
        return base
    elements = local_streets(*spot, fetch)
    if not elements:
        return base
    index = {nid: i for i, nid in enumerate(base.ids) if nid is not None}
    nodes, ids = base.nodes.tolist(), list(base.ids)
    n_base = len(base.nodes)
    new_edges, index, nodes, ids = edges_from_osm(elements, index, nodes, ids)
    if not new_edges or not any(e["u"] < n_base or e["v"] < n_base for e in new_edges):
        return base                       # streets that never touch the major roads would strand the search
    g = assemble(nodes, ids, base.edges + new_edges, base=base)
    _local[key] = g
    while len(_local) > 6:
        _local.popitem(last=False)
    return g
