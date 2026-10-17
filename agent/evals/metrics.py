"""Per-row scores and aggregate metrics, shared by the local runner and Chalk scorers.

Every per-row score is in [0, 1] (Chalk's scorer contract) or None when the
row is not applicable to that metric.
"""
import json

import numpy as np

LEAD_TARGET_H = 3.0     # a warning this far ahead of arrival earns full lead credit


def _first(output, level):
    out = json.loads(output) if isinstance(output, str) else output
    return out["first_hour"][level]


def timely(output, category, observed_arrival_h, level="warn"):
    """1 if a destroyed home got `level` (or higher) before the fire was observed there."""
    if category != "destroyed" or observed_arrival_h is None:
        return None
    h = _first(output, level)
    return float(h is not None and h < observed_arrival_h)


def lead_time_h(output, category, observed_arrival_h, level="warn"):
    if category != "destroyed" or observed_arrival_h is None:
        return None
    h = _first(output, level)
    return None if h is None else observed_arrival_h - h


def lead_score(output, category, observed_arrival_h, level="warn"):
    lt = lead_time_h(output, category, observed_arrival_h, level)
    if lt is None:
        return None if category != "destroyed" or observed_arrival_h is None else 0.0
    return float(np.clip(lt / LEAD_TARGET_H, 0.0, 1.0))


FALSE_WARN_KM = 8.0


def no_over_evacuation(output, category):
    """Outside points (fire never arrived): 0 if the agent said evacuate."""
    if category != "outside":
        return None
    return float(_first(output, "evacuate") is None)


def no_false_warning(output, category, dist_to_perimeter_km):
    """Outside points 8 km or more from the final perimeter: 0 for any warn or evacuate."""
    if category != "outside" or dist_to_perimeter_km < FALSE_WARN_KM:
        return None
    return float(_first(output, "warn") is None)


def summarize(rows, outputs):
    """Aggregate table for a set of rows and task outputs, per incident and overall."""
    res = {}
    for scope in sorted({r["incident"] for r in rows}) + ["all"]:
        sel = [(r, o) for r, o in zip(rows, outputs) if scope == "all" or r["incident"] == scope]
        des = [(r, o) for r, o in sel if r["category"] == "destroyed" and r["observed_arrival_h"] is not None]
        out = [(r, o) for r, o in sel if r["category"] == "outside"]
        lw = [lead_time_h(o, r["category"], r["observed_arrival_h"], "warn") for r, o in des]
        le = [lead_time_h(o, r["category"], r["observed_arrival_h"], "evacuate") for r, o in des]
        res[scope] = {
            "destroyed_n": len(des),
            "warned_in_time": _mean(timely(o, r["category"], r["observed_arrival_h"], "warn") for r, o in des),
            "evacuate_in_time": _mean(timely(o, r["category"], r["observed_arrival_h"], "evacuate") for r, o in des),
            "median_warn_lead_h": _median(lw),
            "median_evacuate_lead_h": _median(le),
            "outside_n": len(out),
            "over_evacuation_rate": _rate(no_over_evacuation(o, r["category"]) for r, o in out),
            "false_warning_rate": _rate(no_false_warning(o, r["category"], r["dist_to_perimeter_km"]) for r, o in out),
            "alerts_per_subscriber": _mean(len(o["alerts"]) for _, o in sel),
        }
    return res


def _rate(oks):
    m = _mean(oks)
    return None if m is None else round(1 - m, 3)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(float(np.mean(xs)), 3) if xs else None


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(float(np.median(xs)), 2) if xs else None
