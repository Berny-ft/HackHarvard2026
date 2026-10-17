"""Per-row scores and aggregate metrics for the surge replay, shared by the
local runner and the Chalk scorers. Per-row scores are in [0, 1] or None
when the row is not applicable.
"""
import json

import numpy as np

# Surge evacuation has to beat the water *and* the flooded roads: 12 h ahead earns full credit.
LEAD_TARGET_H = 12.0


def _out(output):
    return json.loads(output) if isinstance(output, str) else output


def _first(output, level):
    return _out(output)["first_hour"][level]


def timely(output, category, observed_arrival_h, level="warn"):
    """1 if a household that flooded got `level` (or higher) before the water arrived."""
    if category != "flooded" or observed_arrival_h is None:
        return None
    h = _first(output, level)
    return float(h is not None and h < observed_arrival_h)


def lead_time_h(output, category, observed_arrival_h, level="warn"):
    if category != "flooded" or observed_arrival_h is None:
        return None
    h = _first(output, level)
    return None if h is None else observed_arrival_h - h


def lead_score(output, category, observed_arrival_h, level="evacuate"):
    if category != "flooded" or observed_arrival_h is None:
        return None
    lt = lead_time_h(output, category, observed_arrival_h, level)
    return 0.0 if lt is None else float(np.clip(lt / LEAD_TARGET_H, 0.0, 1.0))


def no_needless_evacuation(output, category):
    """Low-lying addresses that stayed dry: 0 if the agent said evacuate."""
    if category != "spared":
        return None
    return float(_first(output, "evacuate") is None)


def no_false_warning(output, category):
    """High ground the water could never reach: 0 for any warn or evacuate."""
    if category != "high":
        return None
    return float(_first(output, "warn") is None)


def summarize(rows, outputs):
    res = {}
    scopes = sorted({r["storm"] for r in rows}) + ["train", "held_out", "all"]
    for scope in scopes:
        sel = [(r, o) for r, o in zip(rows, outputs)
               if scope == "all" or r["storm"] == scope or r.get("role") == scope]
        if not sel:
            continue
        fl = [(r, o) for r, o in sel if r["category"] == "flooded"]
        sp = [(r, o) for r, o in sel if r["category"] == "spared"]
        hi = [(r, o) for r, o in sel if r["category"] == "high"]
        res[scope] = {
            "flooded_n": len(fl), "spared_n": len(sp), "high_n": len(hi),
            "warned_in_time": _mean(timely(o, r["category"], r["observed_arrival_h"], "warn") for r, o in fl),
            "evacuate_in_time": _mean(timely(o, r["category"], r["observed_arrival_h"], "evacuate") for r, o in fl),
            "evacuate_12h_ahead": _mean(float((lt := lead_time_h(o, r["category"], r["observed_arrival_h"], "evacuate"))
                                              is not None and lt >= LEAD_TARGET_H) for r, o in fl),
            "median_warn_lead_h": _median(lead_time_h(o, r["category"], r["observed_arrival_h"], "warn") for r, o in fl),
            "median_evacuate_lead_h": _median(lead_time_h(o, r["category"], r["observed_arrival_h"], "evacuate") for r, o in fl),
            "needless_evacuation_rate": _rate(no_needless_evacuation(o, r["category"]) for r, o in sp),
            "false_warning_rate": _rate(no_false_warning(o, r["category"]) for r, o in hi),
            "alerts_per_household": _mean(len(_out(o)["alerts"]) for _, o in sel),
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
