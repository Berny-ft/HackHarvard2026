"""Export agent output for the web app (modeling/out/sentinel/).

  python -m evals.run_local --policy radius
  python -m evals.run_local --policy rules
  python -m evals.export_web            # replay.json, plus scan.json from the current live feeds

replay.json: the summary table per policy, and every replayed household with
what the rules policy sent it and when (drawn on the incident map).
scan.json: where the sentinel would alert right now (drawn on the live map).
"""
import json

from evals.dataset import OUT
from sentinel.config import MODEL_OUT

POLICIES = ("radius", "rules", "llm")
DEST = MODEL_OUT / "sentinel" / "replay.json"


def main(n=100):
    summaries, detail = {}, None
    for policy in POLICIES:
        path = OUT / f"results_{policy}_n{n}.json"
        if not path.exists():
            continue
        res = json.loads(path.read_text())
        summaries[policy] = res["summary"]
        if policy == "rules":
            detail = res
    if detail is None:
        raise SystemExit("Run `python -m evals.run_local --policy rules` first.")

    households = []
    for r, o in zip(detail["rows"], detail["outputs"]):
        households.append({
            "incident": r["incident"], "category": r["category"],
            "lat": r["lat"], "lon": r["lon"], "dist_km": r["dist_to_perimeter_km"],
            "arrival_h": r["observed_arrival_h"], "first": o["first_hour"],
            "alerts": [{k: a[k] for k in ("hour", "level", "message", "nearest_fire_km", "eta_h", "downwind")}
                       for a in o["alerts"]],
        })
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps({"n_per_group": n, "summary": summaries, "households": households},
                               separators=(",", ":"), default=str))
    print(f"{len(households)} households, policies {list(summaries)} -> {DEST}")

    from sentinel.live import scan_region
    status = json.loads((MODEL_OUT / "live" / "status.json").read_text())
    cells = scan_region(top=40)
    (DEST.parent / "scan.json").write_text(json.dumps({"updated": status["updated"], "cells": cells}, indent=1))
    print(f"{len(cells)} threatened cells -> {DEST.parent / 'scan.json'}")


if __name__ == "__main__":
    main()
