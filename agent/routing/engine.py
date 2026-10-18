"""The hazard-neutral router: one path from A to B, or the fastest way out from A.

A hazard plug-in (fire, storm surge) describes the danger on every road; this
module turns that into a route. The search is time-aware: it tracks when you
reach each road and compares it with when the hazard is projected to get there.

What a hazard supplies (see Hazard):
  arrival      when the hazard reaches each road (hours on the hazard's clock)
  hard         named closures, e.g. near a fire perimeter, a flooded road, a bridge in high wind
  penalty      soft extra cost (downwind, Red Flag, ...), already weighted
  safe         roads that count as safe ground for Escape (with safe_lead_h of lead)

Rules shared by every hazard:
  * A road is closed if the hazard reaches it less than `margin` hours after you would pass it.
  * Hard closures close a road; in Escape, those listed in `escape_soft` become penalties instead,
    because near the danger every road would otherwise be closed.
  * Thin margins cost more (fading out 3 h past the margin); risk scales every soft penalty.
  * Modelled evacuation traffic slows roads the hazard could reach within 6 h, when the start is
    inside that zone (no live speed feed: this is a model of evacuation load).
  * No path means "no_safe_path" / "no_escape", never a least-bad route.

Every result explains itself (roads the plain fastest route would have used and why they were
closed), carries turn-by-turn directions, and a Google Maps link that follows the route.
"""
import heapq
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import quote

import numpy as np

from . import config
from .geo import KM_PER_DEG_LAT, bearing_deg, compass, km_per_deg_lon

MI_PER_KM = 0.621371
MAJOR = ("motorway", "trunk")


@dataclass
class Hazard:
    kind: str                    # "fire" | "surge"
    label: str                   # "live", "replay:eaton_2025", ...
    origin: datetime             # hour 0 of the hazard's clock
    depart_h: float              # departure on that clock
    conditions: callable         # (G, edge_mask, want_safe) -> dict, see module docstring
    reasons: dict                # hard code -> fn(cond, e) -> sentence fragment
    arrival_phrase: callable     # (arrival_iso) -> "fire projected at 9:40 PM"
    region: str = "socal"
    escape_soft: tuple = ()      # hard codes that become penalties in Escape
    safe_lead_h: float = 12.0
    safe_text: str = ""
    leave_by_cap_h: float | None = None   # latest departure the hazard allows (e.g. storm winds arrive)
    leave_by_cap_reason: str = ""
    notes: list = field(default_factory=list)
    mapped: callable = None      # (lat, lon) -> bool, where the hazard has data; None = everywhere
    mapped_label: str = ""       # "flood-mapped area"


# ----------------------------------------------------------------- search

def _speed_factor(G, cond, depart_h, jam):
    """Travel-time multiplier per edge for modelled evacuation traffic."""
    f = np.ones(len(G))
    if not jam:
        return f
    zone = cond["arrival"] - depart_h < config.CONGESTION_ZONE_H
    slow = np.array([config.EVAC_SPEED_FACTOR.get(c.removesuffix("_link"), 0.5) for c in G.cls])
    f[zone] = 1.0 / slow[zone]
    return f


def _closed_mask(cond, codes):
    m = np.zeros(len(cond["arrival"]), bool)
    for c in codes:
        m |= cond["hard"][c]
    return m


def _search(G, src, dst, mask, depart_h, cond=None, margin=2.0, w=1.0, hard=(), soft=(), goal=None,
            factor=None):
    """Dijkstra on (penalised) time. With `cond`, `hard` closures and the margin prune edges as the search
    reaches them; without it, this is the plain fastest route. `goal` = (safe_until per node, lead) stops at
    the first node reached at time t with safe_until - t >= lead. Returns [(edge, reversed, enter_h, exit_h)]."""
    hours = (G.hours * (factor if factor is not None else 1.0)).tolist()
    ok = mask.tolist()
    if cond is not None:
        arrival = cond["arrival"].tolist()
        closed = _closed_mask(cond, hard).tolist()
        softm = _closed_mask(cond, soft).tolist()
        pen = cond["penalty"].tolist()
    if goal is not None:
        safe_until, lead = goal[0].tolist(), goal[1]
        dst = None
    best, t_at, prev = {src: 0.0}, {src: depart_h}, {}
    heap = [(0.0, src)]
    while heap:
        c, a = heapq.heappop(heap)
        if c > best.get(a, np.inf):
            continue
        if a == dst or (goal is not None and safe_until[a] - t_at[a] >= lead):
            dst = a
            break
        t = t_at[a]
        for e, b, rev in G.out[a]:
            if not ok[e]:
                continue
            t_exit = t + hours[e]
            cost = hours[e]
            if cond is not None:
                if closed[e]:
                    continue
                slack = arrival[e] - t_exit - margin
                if slack < 0:
                    continue
                p = 0.5 * max(0.0, 1.0 - slack / 3.0) + pen[e] + (1.0 if softm[e] else 0.0)
                cost *= 1.0 + w * p
            if c + cost < best.get(b, np.inf):
                best[b], t_at[b], prev[b] = c + cost, t_exit, (a, e, rev)
                heapq.heappush(heap, (c + cost, b))
    if dst is None or (dst not in prev and src != dst):
        return None
    path, b = [], dst
    while b != src:
        a, e, rev = prev[b]
        path.append((e, rev, t_at[a], t_at[b]))
        b = a
    return path[::-1]


def _snap(G, lat, lon, usable):
    d = np.hypot((G.nodes[:, 0] - lon) * km_per_deg_lon(lat), (G.nodes[:, 1] - lat) * KM_PER_DEG_LAT)
    d[~usable] = np.inf
    i = int(np.argmin(d))
    return i, float(d[i])


def box_mask(G, a, b, pad_min_km=15.0):
    """Edges inside the A-B box padded by half the trip length (at least 15 km)."""
    (la1, lo1), (la2, lo2) = a, b
    trip = np.hypot((lo2 - lo1) * km_per_deg_lon((la1 + la2) / 2), (la2 - la1) * KM_PER_DEG_LAT)
    pad = max(pad_min_km, 0.5 * trip)
    plat, plon = pad / KM_PER_DEG_LAT, pad / km_per_deg_lon((la1 + la2) / 2)
    first = G.nodes[G.u]
    return ((first[:, 0] >= min(lo1, lo2) - plon) & (first[:, 0] <= max(lo1, lo2) + plon) &
            (first[:, 1] >= min(la1, la2) - plat) & (first[:, 1] <= max(la1, la2) + plat))


def circle_mask(G, lat, lon, km):
    first = G.nodes[G.u]
    return np.hypot((first[:, 0] - lon) * km_per_deg_lon(lat), (first[:, 1] - lat) * KM_PER_DEG_LAT) <= km


# ---------------------------------------------------------------- explain

def road_label(G, e):
    """What a driver reads: 'Foothill Freeway (I-210)', 'Lake Avenue', 'freeway ramp'."""
    name, ref, cls = G.name[e], G.ref[e], G.cls[e]
    if name and ref and cls.removesuffix("_link") in MAJOR:
        return f"{name} ({ref})"
    if name or ref:
        return name or ref
    if cls == "motorway_link":
        return "freeway ramp"
    if cls.endswith("_link"):
        return "ramp"
    return "unnamed road" if cls in ("residential", "unclassified", "living_street") else f"{cls} road"


def _short_label(G, e):
    """For summaries: the route number when there is one ('I-210'), else the name."""
    return G.ref[e] or G.name[e] or road_label(G, e)


def why_closed(G, hz, cond, e, exit_h, margin, fmt, escape=False):
    """(code, reason) if edge e is closed when you'd leave it at exit_h, else None."""
    for code, m in cond["hard"].items():
        if m[e] and not (escape and code in hz.escape_soft):
            return code, hz.reasons[code](cond, e)
    arr = cond["arrival"][e]
    if arr - exit_h < margin:
        if arr <= hz.depart_h:
            return "arrival", f"already reached at departure ({hz.arrival_phrase(fmt(arr))})"
        return "arrival", (f"{hz.arrival_phrase(fmt(arr))}, you'd pass at {fmt(exit_h)} "
                           f"({max(0.0, arr - exit_h) * 60:.0f} min ahead; needs {margin:g} h)")
    return None


def avoided(G, hz, cond, plain, chosen_edges, margin, fmt, escape=False):
    """Closed stretches of the plain fastest route: one list entry per road, plus their geometry."""
    roads = {}
    for e, _, _, exit_h in plain or []:
        if e in chosen_edges:
            continue
        why = why_closed(G, hz, cond, e, exit_h, margin, fmt, escape)
        if why:
            r = roads.setdefault(road_label(G, e), {"restriction": why[0], "reason": why[1], "coords": []})
            r["coords"].append(G.coords[e])
    items = list(roads.items())[:10]
    blocking = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"road": road, "restriction": r["restriction"]},
         "geometry": {"type": "MultiLineString", "coordinates": r["coords"]}} for road, r in items]}
    return [{"road": road, "reason": r["reason"]} for road, r in items], blocking


def _line(G, path):
    coords = []
    for e, rev, _, _ in path:
        c = G.coords[e][::-1] if rev else G.coords[e]
        coords += c if not coords else c[1:]
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": {}}


def closed_at_departure(G, hz, cond, mask, margin, escape=False, limit=6000):
    """Roads closed if you were on them right at departure, for the map, by restriction."""
    feats, taken = [], np.zeros(len(G), bool)
    layers = [(c, m) for c, m in cond["hard"].items() if not (escape and c in hz.escape_soft)]
    layers.append(("arrival", cond["arrival"] - hz.depart_h < margin))
    for code, m in layers:
        idx = np.flatnonzero(m & mask & ~taken)[:limit]
        taken |= m
        if len(idx):
            feats.append({"type": "Feature", "properties": {"restriction": code, "roads": int(len(idx))},
                          "geometry": {"type": "MultiLineString", "coordinates": [G.coords[i] for i in idx]}})
    return {"type": "FeatureCollection", "features": feats}


# ------------------------------------------------------------- directions

def _heading(coords, from_end=False, span_km=0.05):
    pts = coords[::-1] if from_end else coords
    x0, y0 = pts[0]
    for x, y in pts[1:]:
        dx, dy = (x - x0) * km_per_deg_lon(y0), (y - y0) * KM_PER_DEG_LAT
        if np.hypot(dx, dy) >= span_km or [x, y] == list(pts[-1]):
            b = float(bearing_deg(dx, dy))
            return (b + 180.0) % 360.0 if from_end else b
    return 0.0


def _base_name(road):
    for p in ("North ", "South ", "East ", "West "):
        if road.startswith(p):
            return road[len(p):]
    return road


def _turn(prev, leg):
    return (_heading(leg["coords"]) - _heading(prev["coords"], from_end=True) + 540.0) % 360.0 - 180.0


def _merge_leg(leg, other):
    leg["coords"] += other["coords"][1:]
    leg["km"] += other["km"]
    leg["exit"] = other["exit"]
    leg["lead"] = min(leg["lead"], other["lead"])


def directions(G, cond, path, fmt, arrive_text):
    """Turn-by-turn steps. Consecutive segments on one road form a leg; the turn between legs comes from
    the change in heading. Short unnamed stretches and short straight connectors fold into the leg before."""
    raw = []
    for e, rev, enter, exit_ in path:
        c = G.coords[e][::-1] if rev else G.coords[e]
        road, cls = road_label(G, e), G.cls[e]
        leg = {"road": road, "cls": cls, "coords": list(c), "km": G.km[e], "enter": enter, "exit": exit_,
               "lead": cond["arrival"][e] - exit_, "start": c[0]}
        unnamed = not (G.name[e] or G.ref[e]) and not cls.endswith("_link")
        if raw and (_base_name(raw[-1]["road"]) == _base_name(road) or (unnamed and G.km[e] < 0.3)):
            _merge_leg(raw[-1], leg)
        else:
            raw.append(leg)
    legs = []
    for leg in raw:
        if (legs and leg["km"] < 0.32 and not leg["cls"].endswith("_link")
                and not legs[-1]["cls"].endswith("_link") and abs(_turn(legs[-1], leg)) < 20):
            _merge_leg(legs[-1], leg)
        elif legs and _base_name(legs[-1]["road"]) == _base_name(leg["road"]):
            _merge_leg(legs[-1], leg)
        else:
            legs.append(leg)
    steps = []
    for i, leg in enumerate(legs):
        nxt = next((l["road"] for l in legs[i + 1:] if not l["cls"].endswith("_link")), None)
        if i == 0:
            text, man = f"Head {compass(_heading(leg['coords']))} on {leg['road']}", "depart"
        else:
            prev = legs[i - 1]
            turn = _turn(prev, leg)
            side, a = ("right" if turn > 0 else "left"), abs(turn)
            if leg["cls"].endswith("_link"):
                verb = "Take the exit" if prev["cls"].removesuffix("_link") in MAJOR else "Take the ramp"
                text, man = (f"{verb} toward {nxt}" if nxt else verb), (f"ramp-{side}" if a >= 20 else "ramp")
            elif prev["cls"].endswith("_link") and leg["cls"] in MAJOR:
                text, man = f"Merge onto {leg['road']}", "merge"
            elif a < 20:
                text, man = f"Continue onto {leg['road']}", "straight"
            elif a < 45:
                text, man = f"Slight {side} onto {leg['road']}", f"slight-{side}"
            elif a < 135:
                text, man = f"Turn {side} onto {leg['road']}", f"turn-{side}"
            elif a < 170:
                text, man = f"Sharp {side} onto {leg['road']}", f"sharp-{side}"
            else:
                text, man = f"Make a U-turn onto {leg['road']}", "uturn"
        steps.append({"instruction": text, "maneuver": man, "road": leg["road"],
                      "mi": round(leg["km"] * MI_PER_KM, 1), "minutes": round((leg["exit"] - leg["enter"]) * 60, 1),
                      "at_time": fmt(leg["enter"]), "start": [leg["start"][1], leg["start"][0]],
                      "lead_h": round(float(leg["lead"]), 1) if np.isfinite(leg["lead"]) else None})
    if legs:
        end = legs[-1]["coords"][-1]
        steps.append({"instruction": arrive_text, "maneuver": "arrive", "road": None, "mi": 0, "minutes": 0,
                      "at_time": fmt(legs[-1]["exit"]), "start": [end[1], end[0]], "lead_h": None})
    return steps


def google_maps_url(steps, origin, dest):
    """A Google Maps link that follows the safe route: turn points as waypoints (Google takes up to 9),
    so Google doesn't re-route through the danger."""
    turns = [s["start"] for s in steps[1:-1]]
    if len(turns) > 9:
        turns = [turns[i] for i in np.unique(np.linspace(0, len(turns) - 1, 9).astype(int))]
    url = (f"https://www.google.com/maps/dir/?api=1&origin={origin[0]:.5f},{origin[1]:.5f}"
           f"&destination={dest[0]:.5f},{dest[1]:.5f}&travelmode=driving")
    if turns:
        url += "&waypoints=" + quote("|".join(f"{a:.5f},{b:.5f}" for a, b in turns), safe=",")
    return url


def path_stats(G, hz, cond, path, margin, fmt, arrive_text, mention_near=False):
    """Distance, time, smallest lead over the hazard, legs, directions, warnings and a Maps link."""
    edges = [p[0] for p in path]
    exits = np.array([p[3] for p in path]) if path else np.array([hz.depart_h])
    margins = cond["arrival"][edges] - exits if path else np.array([])
    finite = np.isfinite(margins)
    min_m = float(margins[finite].min()) if finite.any() else None
    tight = {}
    for (e, _, _, _), m in zip(path, margins):
        if np.isfinite(m) and m < margin + 1.0:
            tight.setdefault(road_label(G, e), round(float(m), 1))
    exposed = list(dict.fromkeys(road_label(G, e) for e in edges if cond.get("exposure") is not None
                                 and cond["exposure"][e] > 0.2))
    near = list(dict.fromkeys(road_label(G, e) for e in edges if any(m[e] for m in cond["hard"].values())))

    def names(rs):
        return ", ".join(rs[:3]) + (f" and {len(rs) - 3} more roads" if len(rs) > 3 else "")

    steps = directions(G, cond, path, fmt, arrive_text)
    line = _line(G, path) if path else None
    coords = line["geometry"]["coordinates"] if line else []
    return {
        "path": line,
        "distance_mi": round(float(G.km[edges].sum()) * MI_PER_KM, 1),
        "eta_minutes": round(float(exits[-1] - hz.depart_h) * 60),
        "arrive_time": fmt(exits[-1]),
        "min_margin_hours": round(min_m, 1) if min_m is not None else None,
        "roads": _road_list(G, path),
        "directions": steps,
        "google_maps_url": google_maps_url(steps, coords[0][::-1], coords[-1][::-1]) if coords else None,
        "warnings": ([f"Thin margin on {r}: {hz.kind} projected {m:g} h after you pass." for r, m in tight.items()]
                     + ([f"Passes close to the {hz.kind} on {names(near)}."] if near and mention_near else [])
                     + ([cond["exposure_text"].format(roads=names(exposed))] if exposed else [])),
    }


def _road_list(G, path):
    legs = []
    for e, _, enter, _ in path:
        road = road_label(G, e)
        if legs and legs[-1]["road"] == road:
            legs[-1]["mi"] += G.km[e] * MI_PER_KM
        else:
            legs.append({"road": road, "mi": G.km[e] * MI_PER_KM, "enter_hour": round(enter, 2)})
    return [leg | {"mi": round(leg["mi"], 1)} for leg in legs if leg["mi"] >= 0.05]


def _base(G, hz, fmt, extra):
    return {"hazard": hz.kind, "mode": hz.label, "depart_hour": hz.depart_h, "depart_time": fmt(hz.depart_h),
            "notes": list(hz.notes)} | extra


def _formatter(hz):
    def fmt(h):
        return (hz.origin + timedelta(hours=float(h))).isoformat(timespec="minutes")
    return fmt


# ---------------------------------------------------------------- A -> B

def plan(G, hz, a, b, margin, risk="balanced", jam=None):
    """One hazard-safe path from a=(lat, lon) to b, or status "no_safe_path"."""
    w = config.RISK[risk]
    fmt = _formatter(hz)
    mask = box_mask(G, a, b)
    usable = np.zeros(len(G.nodes), bool)
    usable[G.u[mask]] = usable[G.v[mask]] = True
    src, d_src = _snap(G, *a, usable)
    dst, d_dst = _snap(G, *b, usable)
    base = _base(G, hz, fmt, {
        "restrictions": {"margin_hours": margin, "risk": risk, "hard": list(hz.reasons)},
        "snapped": {"from": G.nodes[src].tolist()[::-1], "from_km": round(d_src, 2),
                    "to": G.nodes[dst].tolist()[::-1], "to_km": round(d_dst, 2)}})
    for label, d in (("Start", d_src), ("Destination", d_dst)):
        if d > config.MAX_SNAP_KM:
            return base | {"status": "error", "message": f"{label} is {d:.1f} km from the nearest road."}
    if src == dst:
        return base | {"status": "error", "message": "Start and destination snap to the same road junction."}

    cond = hz.conditions(G, mask, False)
    jam = bool(cond["arrival"][[e for e, _, _ in G.out[src]]].min(initial=np.inf) - hz.depart_h
               < config.CONGESTION_ZONE_H) if jam is None else jam
    factor = _speed_factor(G, cond, hz.depart_h, jam)
    hard = tuple(cond["hard"])
    plain = _search(G, src, dst, mask, hz.depart_h, factor=factor)
    path = _search(G, src, dst, mask, hz.depart_h, cond, margin, w, hard=hard, factor=factor)
    closed_map = closed_at_departure(G, hz, cond, mask, margin)
    base["congestion_modelled"] = jam
    if path is None:
        blocked, blocking = avoided(G, hz, cond, plain, set(), margin, fmt)
        start_cut = all(why_closed(G, hz, cond, e, hz.depart_h + G.hours[e], margin, fmt)
                        for e, _, _ in G.out[src] if mask[e])
        why = ("The start is cut off: every road leaving it is closed." if start_cut else
               "Every route between A and B crosses a closed road." if plain else
               "A and B are not connected by roads in this area.")
        return base | {"status": "no_safe_path", "message": why, "blocked_by": blocked,
                       "blocking": blocking, "closed_at_departure": closed_map}
    blocked, blocking = avoided(G, hz, cond, plain, {p[0] for p in path}, margin, fmt)
    stats = path_stats(G, hz, cond, path, margin, fmt, "Arrive at your destination")
    plain_h = sum(G.hours[p[0]] * factor[p[0]] for p in plain) if plain else None
    return base | {"status": "ok"} | stats | {
        "detour_minutes": round(stats["eta_minutes"] - plain_h * 60) if plain_h is not None else None,
        "avoided": blocked, "blocking": blocking, "closed_at_departure": closed_map}


# ----------------------------------------------------------------- Escape

ESCAPE_SEARCH_KM = 30.0
ESCAPE_TIGHT_H = 1.0


def timed_points(G, path):
    """[(lon, lat, hour you pass it)] along a path, times interpolated within each segment (for evaluation)."""
    out = []
    for e, rev, enter, exit_ in path:
        c = G.coords[e][::-1] if rev else G.coords[e]
        for k, (x, y) in enumerate(c):
            out.append((x, y, enter + (exit_ - enter) * k / max(len(c) - 1, 1)))
    return out


def escape(G, hz, a, margin, risk="balanced", jam=None, search_km=ESCAPE_SEARCH_KM, timed=False):
    """Fastest route to safe ground from a=(lat, lon), whether it gets out in time, and the latest departure
    that still does. status: "ok" (tight when the lead is under 1 h) | "no_escape" | "already_safe" | "error"."""
    w = config.RISK[risk]
    fmt = _formatter(hz)
    mask = circle_mask(G, *a, search_km)
    usable = np.zeros(len(G.nodes), bool)
    usable[G.u[mask]] = usable[G.v[mask]] = True
    src, d_src = _snap(G, *a, usable)
    base = _base(G, hz, fmt, {
        "kind": "escape",
        "restrictions": {"margin_hours": margin, "risk": risk, "safe_zone": hz.safe_text,
                         "soft_in_escape": list(hz.escape_soft)},
        "snapped": {"from": G.nodes[src].tolist()[::-1], "from_km": round(d_src, 2)}})
    if d_src > config.MAX_SNAP_KM:
        return base | {"status": "error", "message": f"Start is {d_src:.1f} km from the nearest road."}

    cond = hz.conditions(G, mask, True)
    # A node is safe until the latest hazard arrival on any adjoining road that counts as safe ground.
    safe_until = np.full(len(G.nodes), -np.inf)
    ok_edge = mask & cond["safe"]
    for ends in (G.u, G.v):
        np.maximum.at(safe_until, ends[ok_edge], cond["arrival"][ok_edge])
    goal = (safe_until, hz.safe_lead_h)
    out_edges = [e for e, _, _ in G.out[src] if mask[e]]
    jam = bool(out_edges and cond["arrival"][out_edges].min() - hz.depart_h < config.CONGESTION_ZONE_H) \
        if jam is None else jam
    factor = _speed_factor(G, cond, hz.depart_h, jam)
    hard = tuple(c for c in cond["hard"] if c not in hz.escape_soft)
    soft = tuple(c for c in cond["hard"] if c in hz.escape_soft)
    base["congestion_modelled"] = jam

    def run(t0):
        return _search(G, src, None, mask, t0, cond, margin, w, hard=hard, soft=soft, goal=goal, factor=factor)

    closed_map = closed_at_departure(G, hz, cond, mask, margin, escape=True)
    if safe_until[src] - hz.depart_h >= hz.safe_lead_h:
        return base | {"status": "already_safe", "message": "You're already outside the danger zone.",
                       "closed_at_departure": closed_map}
    path = run(hz.depart_h)
    plain = _search(G, src, None, mask, hz.depart_h, goal=goal, factor=factor)
    if timed:   # evaluation: both routes on the clock, to check against what really happened
        base["timed"] = {"route": timed_points(G, path) if path else None,
                         "plain": timed_points(G, plain) if plain else None}
    if path is None:
        blocked, blocking = avoided(G, hz, cond, plain, set(), margin, fmt, escape=True)
        return base | {
            "status": "no_escape",
            "message": (f"Every way out is closed: the {hz.kind} is projected to reach each road first."
                        if plain else f"No road reaches safe ground within {search_km:g} km."),
            "blocked_by": blocked, "blocking": blocking, "closed_at_departure": closed_map}

    stats = path_stats(G, hz, cond, path, margin, fmt, "Arrive at the safe zone", mention_near=True)
    trip_h = stats["eta_minutes"] / 60
    leave_by = _leave_by(run, hz.depart_h, cap=None if hz.leave_by_cap_h is None else hz.leave_by_cap_h - trip_h)
    end = path[-1]
    safe_node = G.u[end[0]] if end[1] else G.v[end[0]]
    safe_lat, safe_lon = float(G.nodes[safe_node][1]), float(G.nodes[safe_node][0])
    beyond = None
    if hz.mapped is not None and not hz.mapped(safe_lat, safe_lon):
        # Where the route crosses out of the area the hazard has data for: drawn fading, and said in the steps.
        coords = stats["path"]["geometry"]["coordinates"]
        k = next((i for i, (x, y) in enumerate(coords) if not hz.mapped(y, x)), len(coords) - 1)
        beyond = {"type": "Feature", "properties": {},
                  "geometry": {"type": "LineString", "coordinates": coords[max(k - 1, 0):]}}
        stats["directions"][-1]["instruction"] = f"Leave the {hz.mapped_label}: safe ground beyond it"
    blocked, blocking = avoided(G, hz, cond, plain, {p[0] for p in path}, margin, fmt, escape=True)
    tight = stats["min_margin_hours"] is not None and stats["min_margin_hours"] < ESCAPE_TIGHT_H
    late = False
    if leave_by == "no_deadline":
        lb, lb_msg = None, f"Escape stays open for at least {config.LEAVE_BY_WINDOW_H:g} h."
    else:
        lb = leave_by
        capped = hz.leave_by_cap_h is not None and abs(lb - (hz.leave_by_cap_h - trip_h)) < 0.2
        late = lb < hz.depart_h
        if late:
            lb_msg = (f"Leave now: {hz.leave_by_cap_reason.replace(' then', '')} before you'd reach safe ground."
                      if capped else "Leave now.")
        else:
            lb_msg = f"Leave by {fmt(lb)} to still get out" + (f" ({hz.leave_by_cap_reason})." if capped else ".")
    if late:
        verdict = "A way out is still open, but there's no time to wait."
    else:
        verdict = "You can get out, but with little time to spare." if tight else "You can get out in time."
    return base | {"status": "ok", "tight": tight or late} | stats | {
        "message": verdict + " " + lb_msg,
        "safe_point": [safe_lat, safe_lon],
        "beyond_mapped_area": beyond,
        "leave_by_hour": round(lb, 2) if lb is not None else None,
        "leave_by_time": fmt(lb) if lb is not None else None,
        "avoided": blocked, "blocking": blocking, "closed_at_departure": closed_map}


def _leave_by(run, depart_h, cap=None, step_h=1 / 6):
    """Latest departure (10-min resolution) that still escapes, or "no_deadline" inside the window.
    Uses what is known at departure; later departures only change when you reach each road.
    `cap` is a hazard-imposed latest departure (e.g. storm winds reaching the roads)."""
    window = depart_h + config.LEAVE_BY_WINDOW_H
    hi = window if cap is None else min(window, cap)
    if hi <= depart_h:
        return hi                         # the hazard's own deadline has already passed
    if run(hi) is not None:
        return "no_deadline" if hi == window else hi
    lo = depart_h
    while hi - lo > step_h:
        mid = (lo + hi) / 2
        if run(mid) is not None:
            lo = mid
        else:
            hi = mid
    return lo


# --------------------------------------------------------- for messaging

def summary_for_texts(r, now_iso=None):
    """The route record the texting layer stores on a subscriber (sub["route"]), from an escape result.
    Keeps the fields messages.py and the route page already read, and adds the directions."""
    if r.get("status") != "ok":
        return None
    coords = r["path"]["geometry"]["coordinates"]
    origin, dest = coords[0][::-1], coords[-1][::-1]
    by_road = {}
    for leg in r["roads"]:
        key = leg["road"].split(" (")[-1].rstrip(")") if "(" in leg["road"] else leg["road"]
        by_road[key] = by_road.get(key, 0) + leg["mi"]
    top = [k for k, _ in sorted(by_road.items(), key=lambda kv: -kv[1]) if k not in ("unnamed road", "ramp",
                                                                                      "freeway ramp")][:2]
    dist_km = r["distance_mi"] / MI_PER_KM
    from .geo import km_between
    brg = float(bearing_deg((dest[1] - origin[1]) * km_per_deg_lon(origin[0]), (dest[0] - origin[0]) * KM_PER_DEG_LAT))
    turns = [s["start"] for s in r["directions"][1:-1]][:8]
    return {"summary": ("via " + ", ".join(top)) if top else "away from the danger",
            "duration_min": float(r["eta_minutes"]), "distance_km": dist_km, "origin": origin, "dest": dest,
            "dest_label": f"safe area {km_between(*origin, *dest):.0f} km {compass(brg)}", "dest_bearing": round(brg),
            "waypoints": turns, "fire_risk": False, "congestion_modelled": r.get("congestion_modelled", False),
            "computed_at": now_iso, "hazard": r.get("hazard"), "directions": r["directions"],
            "path": r["path"], "leave_by": r.get("leave_by_time"), "tight": r.get("tight", False),
            "min_lead_h": r.get("min_margin_hours"), "avoided": r.get("avoided", []),
            "warnings": r.get("warnings", []), "google_maps_url": r.get("google_maps_url"),
            "safe_point": r.get("safe_point")}
