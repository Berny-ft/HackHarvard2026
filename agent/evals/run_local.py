"""Run the replay evaluation locally (no Chalk account needed).

  python -m evals.run_local                    # rule policy, all rows
  python -m evals.run_local --policy llm -n 30 # LLM policy on 30 rows per group
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor

from evals.dataset import FIRES, OUT, SHOWCASE, build
from evals.metrics import summarize
from sentinel.policy import get_policy
from sentinel.replay import replay_subscriber


def load_rows(n, showcase=False):
    path = OUT / (f"replay_showcase_n{n}.json" if showcase else f"replay_subscribers_n{n}.json")
    if not path.exists():
        OUT.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(build(n, fires=SHOWCASE if showcase else FIRES), indent=1))
    rows = json.loads(path.read_text())
    if rows and "burn_probability_10y" not in rows[0]:   # datasets built before the column existed
        from sentinel.geo import burn_probability
        for r in rows:
            r["burn_probability_10y"] = burn_probability(r["lat"], r["lon"])
        path.write_text(json.dumps(rows, indent=1))
    return rows


def run(rows, policy_name="rules", workers=8, **physics):
    def one(r):
        return replay_subscriber(r["incident"], r["lat"], r["lon"], policy=get_policy(policy_name),
                                 burn_prob=r.get("burn_probability_10y", "lookup"), **physics)

    with ThreadPoolExecutor(workers if policy_name == "llm" else 1) as ex:
        return list(ex.map(one, rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", default="rules", choices=["rules", "radius", "llm"])
    ap.add_argument("-n", type=int, default=100, help="dataset size (locations per group per fire)")
    ap.add_argument("--limit", type=int, help="use only the first N rows of each incident/category group")
    ap.add_argument("--dry-run", action="store_true", help="LLM policy: count calls, make none")
    ap.add_argument("--showcase", action="store_true", help="held-out fires (Woolsey, Mountain, Franklin)")
    a = ap.parse_args()

    rows = load_rows(a.n, a.showcase)
    if a.limit:
        seen = {}
        rows = [r for r in rows if seen.setdefault((r["incident"], r["category"]), []).append(1) or
                len(seen[(r["incident"], r["category"])]) <= a.limit]
    if a.dry_run:
        from sentinel.policy import LLMPolicy
        LLMPolicy.dry_run = True
    t = time.time()
    outputs = run(rows, a.policy)
    summary = summarize(rows, outputs)
    print(json.dumps(summary, indent=1))
    print(f"{len(rows)} subscribers, {time.time() - t:.0f}s, "
          f"{sum(o['llm_calls'] for o in outputs)} LLM calls, "
          f"{sum(o.get('llm_tokens', 0) for o in outputs):,} tokens{' (dry run)' if a.dry_run else ''}")
    if a.dry_run:
        return
    tag = "_showcase" if a.showcase else ""
    path = OUT / f"results_{a.policy}{tag}_n{a.n}{'_l' + str(a.limit) if a.limit else ''}.json"
    path.write_text(json.dumps({"summary": summary, "rows": rows, "outputs": outputs}, indent=1, default=str))
    print(f"-> {path}")


if __name__ == "__main__":
    main()
