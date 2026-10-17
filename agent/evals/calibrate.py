"""Fit the spread parameters and evacuate ring on one fire, report on the other.

  python -m evals.calibrate --train eaton_2025

Objective (higher is better), on the training fire's rule-policy replay:
  evacuate_in_time - over_evacuation_rate - false_warning_rate
A missed evacuation and a needless one cost the same here; the false-warning
term keeps the agent from simply widening every ring.
"""
import argparse
import itertools
import json

from evals.metrics import summarize
from evals.run_local import load_rows, run
from evals.dataset import OUT

K_GRID = [0.03, 0.05, 0.07, 0.09, 0.12]
LB_GRID = [1.5, 3.0, 8.0]
STALE_GRID = [0.0, 1.0]
EVAC_KM_GRID = [1.5, 2.0, 3.0, 4.0]


def objective(m):
    return m["evacuate_in_time"] - m["over_evacuation_rate"] - m["false_warning_rate"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="eaton_2025")
    ap.add_argument("-n", type=int, default=100)
    a = ap.parse_args()

    rows = load_rows(a.n)
    train = [r for r in rows if r["incident"] == a.train]
    test = [r for r in rows if r["incident"] != a.train]

    grid = []
    for k, lb, st, ek in itertools.product(K_GRID, LB_GRID, STALE_GRID, EVAC_KM_GRID):
        m = summarize(train, run(train, "rules", k=k, max_lb=lb, stale_h=st, evac_km=ek))["all"]
        grid.append({"k": k, "max_lb": lb, "stale_h": st, "evac_km": ek, "objective": round(objective(m), 3), **m})
        print(f"k={k:<5} lb={lb:<4} stale={st:<4} evac_km={ek:<4} obj={grid[-1]['objective']:+.3f} evac={m['evacuate_in_time']} "
              f"overevac={m['over_evacuation_rate']} falsewarn={m['false_warning_rate']}")

    best = max(grid, key=lambda g: g["objective"])
    params = {p: best[p] for p in ("k", "max_lb", "stale_h", "evac_km")}
    held = summarize(test, run(test, "rules", **params))["all"]
    out = {"train": a.train, "best": params,
           "train_metrics": best, "held_out_metrics": held, "held_out_objective": round(objective(held), 3),
           "grid": grid}
    path = OUT / f"calibration_{a.train}.json"
    path.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("best", "held_out_metrics", "held_out_objective")}, indent=1))
    print(f"-> {path}")


if __name__ == "__main__":
    main()
