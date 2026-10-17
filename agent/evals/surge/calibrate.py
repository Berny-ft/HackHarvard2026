"""Fit the rule policy's knobs on the train storms only (Sandy, Matthew), then
score the held-out storms (Irma, Isabel) with them.

Objective, equal weights: evacuated 12 h or more ahead (flooded homes),
minus needless evacuations (low but dry), minus false warnings (high ground).

  python -m evals.surge.calibrate
"""
import itertools
import json

from evals.surge.dataset import OUT, TRAIN, load_rows
from evals.surge.metrics import summarize
from evals.surge.run_local import limit_rows
from surge_sentinel.replay import replay_subscriber

GRID = {
    "high_factor": [1.0, 1.3, 1.6],
    "high_pad_ft": [0.0, 0.5],
    "evacuate_h": [12.0, 18.0, 24.0],
    "warn_h": [24.0, 36.0],
}


def score(summary):
    s = summary
    return (s["evacuate_12h_ahead"] or 0) - (s["needless_evacuation_rate"] or 0) - (s["false_warning_rate"] or 0)


def evaluate(rows, knobs):
    outs = [replay_subscriber(r["storm"], r["lat"], r["lon"], **knobs) for r in rows]
    return summarize(rows, outs)


def main(limit=30):
    rows = limit_rows(load_rows(), limit)
    train = [r for r in rows if r["storm"] in TRAIN]
    held = [r for r in rows if r["storm"] not in TRAIN]
    results = []
    for vals in itertools.product(*GRID.values()):
        knobs = dict(zip(GRID, vals))
        s = evaluate(train, knobs)["all"]
        results.append((score(s), knobs, s))
    results.sort(key=lambda x: -x[0])
    best_score, best, train_s = results[0]
    held_s = evaluate(held, best)["all"]
    print("best on train:", json.dumps(best), f"score {best_score:.3f}")
    for name, s in (("train", train_s), ("held out", held_s)):
        print(f"  {name:<9} evac 12h+ {s['evacuate_12h_ahead']}  evac in time {s['evacuate_in_time']}  warned {s['warned_in_time']}  "
              f"needless {s['needless_evacuation_rate']}  false {s['false_warning_rate']}")
    (OUT / "surge_calibration.json").write_text(json.dumps(
        {"best": best, "train": train_s, "held_out": held_s,
         "top": [{"score": round(sc, 3), "knobs": k} for sc, k, _ in results[:10]]}, indent=1))


if __name__ == "__main__":
    main()
