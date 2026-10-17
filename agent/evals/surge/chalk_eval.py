"""The storm-surge replay as a Chalk Evaluation suite (surgetrace-sentinel).

One dataset (households at real places in four storms, with gauge-observed
flood arrival), one task per policy (official zones baseline, address rules,
LLM through the Chalk Model Gateway, LLM with learned lessons), and the same
scorers as the local runner. Chalk pins every version, so a policy change is
compared run against run in the Evaluations dashboard.

  chalk login && chalk environment <env-id>
  python -m evals.surge.chalk_eval --policies zones rules
  SENTINEL_EVAL_LLM=1 python -m evals.surge.chalk_eval --policies llm llm_learned --limit 4
"""
import os

import chalkcompute

import sentinel.config  # noqa: F401  (loads agent/.env before the configs below read os.environ)

AGENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SURGE = os.path.join(os.path.dirname(AGENT), "modeling", "out", "surge")
WITH_LLM = os.environ.get("SENTINEL_EVAL_LLM") == "1"


def _image():
    return (
        chalkcompute.Image.debian_slim("3.12")
        .pip_install(["numpy", "pillow", "openai", "pydantic", "requests"])
        .add_local_dir(os.path.join(AGENT, "sentinel"), "/app/sentinel", exclude=["__pycache__"])
        .add_local_dir(os.path.join(AGENT, "surge_sentinel"), "/app/surge_sentinel", exclude=["__pycache__"])
        .add_local_dir(os.path.join(SURGE, "storms"), "/app/out/surge/storms", exclude=["extent.geojson"])
        .add_local_file(os.path.join(SURGE, "surge_model.json"), "/app/out/surge/surge_model.json")
        .add_local_file(os.path.join(AGENT, "evals", "surge", "metrics.py"), "/app/surge_metrics.py")
        .env({"FIRETRACE_OUT": "/app/out", "PYTHONPATH": "/app", "SENTINEL_STATE": "/tmp/state"})
        .workdir("/app")
    )


IMAGE = _image() if os.path.isdir(os.path.join(AGENT, "surge_sentinel")) and os.path.isdir(SURGE) else None
FN = dict(image=IMAGE, cpu="1", memory="2Gi", call_timeout=900.0)


def _replay(policy, storm, lat, lon, lessons=None):
    import json

    from surge_sentinel.policy import LLMPolicy, get_policy
    from surge_sentinel.replay import replay_subscriber

    p = get_policy(policy) if policy != "llm_learned" else LLMPolicy(lessons=True)
    if lessons is not None and policy == "llm_learned":
        p.system += f"\n\nLessons from replaying past storms:\n{lessons}"
    return json.dumps(replay_subscriber(storm, lat, lon, policy=p), default=str)


@chalkcompute.function(**FN)
def surge_zones(storm: str, lat: float, lon: float) -> str:
    return _replay("zones", storm, lat, lon)


@chalkcompute.function(**FN)
def surge_rules(storm: str, lat: float, lon: float) -> str:
    return _replay("rules", storm, lat, lon)


TASKS = {"zones": surge_zones, "rules": surge_rules}

if WITH_LLM:
    LLM_FN = dict(secrets=[chalkcompute.Secret.from_chalk_env("CHALK_ROUTER_API_KEY")],
                  env={"CHALK_ROUTER_ENV_ID": os.environ.get("CHALK_ROUTER_ENV_ID", ""),
                       "SENTINEL_MODEL": os.environ.get("SENTINEL_MODEL", "openai/gpt-6.1-sol")})
    LESSONS = os.path.join(SURGE, "lessons.md")
    LESSON_TEXT = open(LESSONS).read() if os.path.exists(LESSONS) else ""

    @chalkcompute.function(**FN, **LLM_FN)
    def surge_llm(storm: str, lat: float, lon: float) -> str:
        return _replay("llm", storm, lat, lon)

    @chalkcompute.function(**FN, **LLM_FN)
    def surge_llm_learned(storm: str, lat: float, lon: float) -> str:
        return _replay("llm_learned", storm, lat, lon, LESSON_TEXT)

    TASKS |= {"llm": surge_llm, "llm_learned": surge_llm_learned}


def _score(x):
    return [] if x is None else [chalkcompute.EvaluationScorerResult(score=float(x))]


def _arrival(v):
    # The dataset stores "never flooded" as -1: an all-null column in a batch is dropped before it reaches a scorer.
    return None if v is None or v < 0 else v


@chalkcompute.function(image=IMAGE)
def warned_in_time(output: str, category: str, observed_arrival_h: float) -> list[chalkcompute.EvaluationScorerResult]:
    from surge_metrics import timely
    return _score(timely(output, category, _arrival(observed_arrival_h), "warn"))


@chalkcompute.function(image=IMAGE)
def evacuate_in_time(output: str, category: str, observed_arrival_h: float) -> list[chalkcompute.EvaluationScorerResult]:
    from surge_metrics import timely
    return _score(timely(output, category, _arrival(observed_arrival_h), "evacuate"))


@chalkcompute.function(image=IMAGE)
def evacuate_lead(output: str, category: str, observed_arrival_h: float) -> list[chalkcompute.EvaluationScorerResult]:
    """Evacuate lead time, full credit 12 h ahead of the water."""
    from surge_metrics import lead_score
    return _score(lead_score(output, category, _arrival(observed_arrival_h), "evacuate"))


@chalkcompute.function(image=IMAGE)
def no_needless_evacuation(output: str, category: str) -> list[chalkcompute.EvaluationScorerResult]:
    from surge_metrics import no_needless_evacuation as f
    return _score(f(output, category))


@chalkcompute.function(image=IMAGE)
def no_false_warning(output: str, category: str) -> list[chalkcompute.EvaluationScorerResult]:
    from surge_metrics import no_false_warning as f
    return _score(f(output, category))


SCORERS = [warned_in_time, evacuate_in_time, evacuate_lead, no_needless_evacuation, no_false_warning]
SCORER_NAMES = ["warned_in_time", "evacuate_in_time", "evacuate_lead", "no_needless_evacuation", "no_false_warning"]


def main():
    import argparse
    import json
    import subprocess

    from evals.chalk_eval import _mean, _scores
    from evals.surge.dataset import load_rows
    from evals.surge.run_local import limit_rows

    ap = argparse.ArgumentParser()
    ap.add_argument("--policies", nargs="+", default=["zones", "rules"], choices=list(TASKS))
    ap.add_argument("--limit", type=int, help="first N rows of each storm/category group")
    a = ap.parse_args()

    rows = [{k: r[k] for k in ("storm", "category", "lat", "lon", "observed_arrival_h", "role")} for r in load_rows()]
    if a.limit:
        rows = limit_rows(rows, a.limit)
    for r in rows:
        r["observed_arrival_h"] = -1.0 if r["observed_arrival_h"] is None else float(r["observed_arrival_h"])
    rows.sort(key=lambda r: r["category"] != "flooded")
    dataset = chalkcompute.DatasetClient().upload("surgetrace-replay-n60" + (f"-l{a.limit}" if a.limit else ""), rows)
    suite = next((s for s in chalkcompute.EvaluationSuite.list_all() if s.name == "surgetrace-sentinel"), None) \
        or chalkcompute.EvaluationSuite.create("surgetrace-sentinel")
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    for policy in a.policies:
        ev = chalkcompute.Evaluation.create(f"surge-{policy}", dataset=dataset, task=TASKS[policy], scorers=SCORERS,
                                            suite_id=suite.id, metadata={"policy": policy, "rows": len(rows)})
        run = ev.run(metadata={"git_sha": sha}).wait()
        table = chalkcompute.DatasetClient().read(run.result_dataset)
        cols = [c for c in table.column_names if c.startswith("scorer_") and not c.endswith("_error")]
        means = {SCORER_NAMES[i]: _mean(_scores(table[c].to_pylist())) for i, c in enumerate(cols)}
        scored = {SCORER_NAMES[i]: sum(bool(x) for x in table[c].to_pylist()) for i, c in enumerate(cols)}
        print("rows scored per scorer:", scored)
        errors = sum(e is not None for e in table["output_error"].to_pylist())
        print(policy, run.status, f"task errors {errors}/{table.num_rows}", json.dumps(means))


if __name__ == "__main__":
    main()
