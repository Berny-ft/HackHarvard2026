"""Were the escape routes actually safe? Escape routes checked against what really happened.

For people who needed to leave (homes the fire destroyed or satellites saw burn; homes the storm surge
flooded), the router plans an escape from what was known `lead` hours before the hazard actually reached
them: 2 h for fire, 6 h for surge. Each route is then driven on the real clock and checked against the
observations the agent never saw:

  fire   a satellite fire detection within 750 m (the replay evaluation's arrival rule) at or before the
         moment you'd pass that point
  surge  observed water at least 0.5 ft deep (tide gauges, bathtub terrain) in the hour you'd pass it

Baseline: the plain fastest route to the same safe ground, ignoring the hazard. Reported per hazard:
  route_given     the router found a way out (otherwise it said "no escape", or "already safe")
  clear_in_reality  of the routes given, the share the real hazard never touched before you passed
  min_margin_h    median of each route's smallest real lead over the hazard

  python -m evals.route_eval [--n 25] [--hazard fire|surge|both]
Runs locally; evals/chalk_route_eval.py runs the same cases as a Chalk Evaluation.
"""
import argparse
import json
import random
from statistics import median

import numpy as np

from routing import api, engine

from sentinel.config import ROOT

OUT = ROOT / "evals" / "data"
FIRE_ROWS = [("replay_subscribers_n100.json", ("destroyed",)), ("replay_showcase_n50.json", ("burned",))]
SURGE_ROWS = "surge_households_n60.json"
LEAD_H = {"fire": 2.0, "surge": 6.0}


def cases(hazard, n, seed=7):
    """Up to n people per fire / storm who needed to leave, with their observed arrival hour."""
    rnd = random.Random(seed)
    out = []
    if hazard == "fire":
        for f, cats in FIRE_ROWS:
            rows = [r for r in json.loads((OUT / f).read_text()) if r["category"] in cats
                    and r.get("observed_arrival_h") is not None and r["observed_arrival_h"] >= LEAD_H["fire"]]
            for key in sorted({r["incident"] for r in rows}):
                group = [r for r in rows if r["incident"] == key]
                out += [{"hazard": "fire", "event": key, "lat": r["lat"], "lon": r["lon"],
                         "arrival_h": r["observed_arrival_h"]} for r in rnd.sample(group, min(n, len(group)))]
    else:
        rows = [r for r in json.loads((OUT / SURGE_ROWS).read_text()) if r["category"] == "flooded"
                and r.get("observed_arrival_h") is not None and r["observed_arrival_h"] >= LEAD_H["surge"]]
        for key in sorted({r["storm"] for r in rows}):
            group = [r for r in rows if r["storm"] == key]
            out += [{"hazard": "surge", "event": key, "lat": r["lat"], "lon": r["lon"],
                     "arrival_h": r["observed_arrival_h"]} for r in rnd.sample(group, min(n, len(group)))]
    return out


# ------------------------------------------------------------------ ground truth along a route

def _fire_truth(event):
    """Observed fire arrival hour at points: earliest satellite detection within 750 m."""
    import shapely
    from shapely import STRtree

    from routing.geo import local_km
    from sentinel.replay import ARRIVAL_RADIUS_KM
    from sentinel.snapshot import load_incident
    _, _, xy, hrs = load_incident(event)
    lon0, lat0 = float(xy[:, 0].mean()), float(xy[:, 1].mean())
    tree = STRtree(shapely.points(*local_km(xy[:, 0], xy[:, 1], lon0, lat0)))

    def observed(lon, lat):
        out = np.full(len(lon), np.inf)
        i, j = tree.query(shapely.points(*local_km(lon, lat, lon0, lat0)), predicate="dwithin",
                          distance=ARRIVAL_RADIUS_KM)
        np.minimum.at(out, i, hrs[j])
        return out
    return observed


def _surge_truth(event):
    """Observed hour water first stands 0.5 ft deep at points (gauges + terrain), from the replay hour on."""
    from surge_sentinel.config import FLOOD_DEPTH_FT
    from surge_sentinel.world import ground_at, load_storm, observed_depth
    info = load_storm(event)[0]
    hours, high_tide = info["hours"], info.get("high_tide_ft", 0.0)

    def observed(lon, lat):
        out = np.full(len(lon), np.inf)
        for k, (x, y) in enumerate(zip(lon, lat)):
            spill, ground = ground_at(event, y, x)
            if spill is None or spill <= high_tide:      # open water under a bridge or causeway, not road
                continue
            for h in range(hours + 1):
                if observed_depth(event, h, y, x, spill, ground) >= FLOOD_DEPTH_FT:
                    out[k] = h
                    break
        return out
    return observed


def score(points, observed, step=3):
    """(clear, smallest real lead in hours) for [(lon, lat, t)], sampling every `step`-th point."""
    if not points:
        return None, None
    pts = np.array(points[::step] + [points[-1]])
    obs = observed(pts[:, 0], pts[:, 1])
    lead = obs - pts[:, 2]
    finite = np.isfinite(lead)
    return bool((lead > 0).all()), (float(lead[finite].min()) if finite.any() else None)


# ------------------------------------------------------------------ one case

def run_case(c, truth_cache={}):
    p = api.plugin(c["hazard"])
    depart = max(0.0, c["arrival_h"] - LEAD_H[c["hazard"]])
    hz = p.build(f"replay:{c['event']}", depart, look_ahead_h=24, **({"at": (c["lat"], c["lon"])}
                                                                  if c["hazard"] == "surge" else {}))
    g = p.graph_for(hz) if hasattr(p, "graph_for") else api.graph.region(hz.region)
    r = engine.escape(g, hz, (c["lat"], c["lon"]), p.MARGIN_ESCAPE_H, timed=True)
    key = (c["hazard"], c["event"])
    if key not in truth_cache:
        truth_cache[key] = (_fire_truth if c["hazard"] == "fire" else _surge_truth)(c["event"])
    observed = truth_cache[key]
    timed = r.get("timed") or {}
    ours = score(timed.get("route"), observed) if r["status"] == "ok" else (None, None)
    plain = score(timed.get("plain"), observed)
    return {**c, "depart_h": depart, "status": r["status"], "eta_min": r.get("eta_minutes"),
            "ours_clear": ours[0], "ours_margin_h": ours[1], "plain_clear": plain[0], "plain_margin_h": plain[1]}


def summarize(results):
    def pct(xs):
        xs = [x for x in xs if x is not None]
        return round(100 * sum(xs) / len(xs), 1) if xs else None

    def med(xs):
        xs = [x for x in xs if x is not None]
        return round(median(xs), 2) if xs else None

    out = {}
    for hz in sorted({r["hazard"] for r in results}):
        rs = [r for r in results if r["hazard"] == hz]
        given = [r for r in rs if r["status"] == "ok"]
        both = [r for r in given if r["plain_clear"] is not None]
        out[hz] = {
            "cases": len(rs), "lead_h": LEAD_H[hz],
            "route_given_pct": pct([r["status"] == "ok" for r in rs]),
            "no_escape_pct": pct([r["status"] == "no_escape" for r in rs]),
            "already_safe_pct": pct([r["status"] == "already_safe" for r in rs]),
            "ours_clear_in_reality_pct": pct([r["ours_clear"] for r in given]),
            "plain_clear_in_reality_pct": pct([r["plain_clear"] for r in both]),
            "plain_hit_but_ours_clear": sum(1 for r in both if r["ours_clear"] and not r["plain_clear"]),
            "ours_hit_but_plain_clear": sum(1 for r in both if not r["ours_clear"] and r["plain_clear"]),
            "ours_min_margin_h_median": med([r["ours_margin_h"] for r in given]),
            "plain_min_margin_h_median": med([r["plain_margin_h"] for r in both]),
            "by_event": {ev: {"cases": len(g), "ours_clear_pct": pct([r["ours_clear"] for r in g if r["status"] == "ok"]),
                              "plain_clear_pct": pct([r["plain_clear"] for r in g])}
                         for ev in sorted({r["event"] for r in rs}) for g in [[r for r in rs if r["event"] == ev]]},
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25, help="people per fire / storm")
    ap.add_argument("--hazard", default="both", choices=["fire", "surge", "both"])
    a = ap.parse_args()
    results = []
    for hz in (["fire", "surge"] if a.hazard == "both" else [a.hazard]):
        cs = cases(hz, a.n)
        for i, c in enumerate(cs, 1):
            try:
                results.append(run_case(c))
            except Exception as e:                       # a case that can't run is reported, not fatal
                results.append({**c, "status": f"error: {e}", "ours_clear": None, "plain_clear": None,
                                "ours_margin_h": None, "plain_margin_h": None})
            if i % 10 == 0:
                print(f"  {hz}: {i}/{len(cs)}", flush=True)
    summary = summarize(results)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "route_eval.json").write_text(json.dumps({"summary": summary, "cases": results}, indent=1, default=str))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
