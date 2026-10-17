"""Export the surge agent's output for the web app (modeling/out/surge/sentinel/).

  python -m evals.surge.run_local --policy zones
  python -m evals.surge.run_local --policy rules
  python -m evals.surge.learn                  # optional: LLM policy + learned lessons
  python -m evals.surge.export_web

replay.json: the summary table per policy (all storms, train, held out, per
storm), what calibration and the learning loop found, and every replayed
household with what the rules policy sent it and when.
"""
import json

from evals.surge.dataset import OUT
from surge_sentinel.config import SURGE_OUT

DEST = SURGE_OUT / "sentinel" / "replay.json"


def _read(name):
    p = OUT / name
    return json.loads(p.read_text()) if p.exists() else None


def main(n=60):
    summaries = {}
    for policy in ("zones", "rules"):
        res = _read(f"results_surge_{policy}_n{n}.json")
        if res:
            summaries[policy] = res["summary"]
    detail = _read(f"results_surge_rules_n{n}.json")
    if detail is None:
        raise SystemExit("Run `python -m evals.surge.run_local --policy rules` first.")
    learn = _read("surge_learn.json")
    if learn:
        # The other policies on the very same held-out sample, so the LLM rows compare like for like.
        from evals.surge.metrics import summarize
        from evals.surge.run_local import limit_rows
        from evals.surge.dataset import TRAIN
        for policy in ("zones", "rules"):
            res = _read(f"results_surge_{policy}_n{n}.json")
            if not res:
                continue
            keep = {(r["storm"], r["lat"], r["lon"]) for r in limit_rows(res["rows"], learn["limit"]) if r["storm"] not in TRAIN}
            pairs = [(r, o) for r, o in zip(res["rows"], res["outputs"]) if (r["storm"], r["lat"], r["lon"]) in keep]
            learn[f"held_out_{policy}"] = summarize([r for r, _ in pairs], [o for _, o in pairs])["all"]
    calib = _read("surge_calibration.json")

    households = []
    for r, o in zip(detail["rows"], detail["outputs"]):
        households.append({
            "storm": r["storm"], "category": r["category"], "lat": r["lat"], "lon": r["lon"],
            "ground_ft": r["ground_ft"], "spill_ft": r["spill_ft"], "arrival_h": r["observed_arrival_h"],
            "peak_depth_ft": r["peak_depth_ft"], "first": o["first_hour"],
            "alerts": [{k: a[k] for k in ("hour", "level", "message", "eta_h", "forecast_depth_ft", "high_depth_ft")}
                       for a in o["alerts"]],
        })
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps({
        "n_per_group": n, "summary": summaries, "households": households,
        "learn": learn and {k: learn.get(k) for k in ("limit", "model", "lessons", "tokens", "train_before",
                                                      "held_out_before", "held_out_after", "held_out_zones", "held_out_rules")},
        "calibration": calib and {k: calib[k] for k in ("best", "train", "held_out")},
    }, separators=(",", ":"), default=str))
    print(f"{len(households)} households, policies {list(summaries)}, learn {'yes' if learn else 'no'} -> {DEST}")


if __name__ == "__main__":
    main()
