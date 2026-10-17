"""Run the storm-surge replay evaluation locally (no Chalk account needed).

  python -m evals.surge.run_local                      # rule policy, every household
  python -m evals.surge.run_local --policy zones       # the official-zones baseline
  python -m evals.surge.run_local --policy llm --limit 10
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor

from evals.surge.dataset import OUT, load_rows
from evals.surge.metrics import summarize
from surge_sentinel.policy import get_policy
from surge_sentinel.replay import replay_subscriber


def run(rows, policy_name="rules", workers=8):
    def one(r):
        return replay_subscriber(r["storm"], r["lat"], r["lon"], policy=get_policy(policy_name))

    with ThreadPoolExecutor(workers if policy_name == "llm" else 1) as ex:
        return list(ex.map(one, rows))


def limit_rows(rows, limit):
    seen = {}
    return [r for r in rows if seen.setdefault((r["storm"], r["category"]), []).append(1) or
            len(seen[(r["storm"], r["category"])]) <= limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", default="rules", choices=["zones", "rules", "llm"])
    ap.add_argument("-n", type=int, default=60)
    ap.add_argument("--limit", type=int, help="first N rows of each storm/category group")
    ap.add_argument("--storms", nargs="+", help="only these storms")
    ap.add_argument("--dry-run", action="store_true", help="LLM policy: count calls, make none")
    a = ap.parse_args()

    rows = load_rows(a.n)
    if a.storms:
        rows = [r for r in rows if r["storm"] in a.storms]
    if a.limit:
        rows = limit_rows(rows, a.limit)
    if a.dry_run:
        from surge_sentinel.policy import LLMPolicy
        LLMPolicy.dry_run = True
    t = time.time()
    outputs = run(rows, a.policy)
    summary = summarize(rows, outputs)
    print(json.dumps(summary, indent=1))
    print(f"{len(rows)} households, {time.time() - t:.0f}s, {sum(o['llm_calls'] for o in outputs)} LLM calls, "
          f"{sum(o.get('llm_tokens', 0) for o in outputs):,} tokens{' (dry run)' if a.dry_run else ''}")
    if a.dry_run:
        return
    tag = "".join(f"_{s}" for s in a.storms or []) + (f"_l{a.limit}" if a.limit else "")
    path = OUT / f"results_surge_{a.policy}_n{a.n}{tag}.json"
    path.write_text(json.dumps({"summary": summary, "rows": rows, "outputs": outputs}, indent=1, default=str))
    print(f"-> {path}")


if __name__ == "__main__":
    main()
