"""The replay evaluation as a Chalk Evaluation suite.

One dataset (simulated subscribers at real Eaton/Palisades locations, with
satellite-observed arrival times), one task per policy (radius baseline, wind
rules, LLM via the Chalk Model Gateway), and the same scorers as the local
runner, plus an LLM judge on the alert messages. Chalk pins every version, so
policy changes are compared run against run in the Evaluations dashboard.

  chalk login && chalk environment <env-id>
  python -m evals.chalk_eval --policies radius rules        # no LLM needed
  SENTINEL_EVAL_LLM=1 python -m evals.chalk_eval --policies rules llm --n 30

The LLM task and the alert-quality judge are only defined with SENTINEL_EVAL_LLM=1,
since they need models connected in the Model Gateway.

Chalk imports this module inside each deployed function, so top-level code only
defines functions; the sentinel package and incident data ship in the image.
"""
import os

import chalkcompute
from pydantic import BaseModel, Field

import sentinel.config  # noqa: F401  (loads agent/.env before the function configs below read os.environ)

AGENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(os.path.dirname(AGENT), "modeling", "out")
JUDGE_MODEL = os.environ.get("SENTINEL_JUDGE_MODEL", "openai/gpt-6.1-sol")
WITH_LLM = os.environ.get("SENTINEL_EVAL_LLM") == "1"

# Only the files the replay reads: per-incident timeseries, detections and
# metadata. Burn probability is precomputed into the dataset, so the image
# needs no GDAL/rasterio.
def _image():
    return (
        chalkcompute.Image.debian_slim("3.12")
        .pip_install(["numpy", "shapely", "openai", "pydantic", "requests"])
        .add_local_dir(os.path.join(AGENT, "sentinel"), "/app/sentinel", exclude=["__pycache__"])
        .add_local_dir(os.path.join(OUT, "incidents"), "/app/out/incidents",
                       exclude=["*.tif", "*.png", "structures.geojson", "perimeters.geojson", "spread.json"])
        .add_local_file(os.path.join(AGENT, "evals", "metrics.py"), "/app/evals_metrics.py")   # scorers' logic
        .env({"FIRETRACE_OUT": "/app/out", "PYTHONPATH": "/app", "SENTINEL_STATE": "/tmp/state",
              "SENTINEL_EVAL_LLM": "1" if WITH_LLM else "0"})
        .workdir("/app")
    )


# Remote workers re-import this module but don't have the local source tree;
# they run inside the already-built image, so they skip building it.
IMAGE = _image() if os.path.isdir(os.path.join(AGENT, "sentinel")) and os.path.isdir(OUT) else None

FN = dict(image=IMAGE, cpu="1", memory="2Gi", call_timeout=900.0)


def _replay(policy, incident, lat, lon, burn_prob):
    import json

    from sentinel.policy import get_policy
    from sentinel.replay import replay_subscriber

    return json.dumps(replay_subscriber(incident, lat, lon, policy=get_policy(policy), burn_prob=burn_prob),
                      default=str)


@chalkcompute.function(**FN)
def sentinel_radius(incident: str, lat: float, lon: float, burn_probability_10y: float | None) -> str:
    return _replay("radius", incident, lat, lon, burn_probability_10y)


@chalkcompute.function(**FN)
def sentinel_rules(incident: str, lat: float, lon: float, burn_probability_10y: float | None) -> str:
    return _replay("rules", incident, lat, lon, burn_probability_10y)


TASKS = {"radius": sentinel_radius, "rules": sentinel_rules}

if WITH_LLM:
    LLM_FN = dict(secrets=[chalkcompute.Secret.from_chalk_env("CHALK_ROUTER_API_KEY")],
                  env={"CHALK_ROUTER_ENV_ID": os.environ.get("CHALK_ROUTER_ENV_ID", ""),
                       "SENTINEL_MODEL": os.environ.get("SENTINEL_MODEL", "openai/gpt-6.1-sol")})

    @chalkcompute.function(**FN, **LLM_FN)
    def sentinel_llm(incident: str, lat: float, lon: float, burn_probability_10y: float | None) -> str:
        return _replay("llm", incident, lat, lon, burn_probability_10y)

    TASKS["llm"] = sentinel_llm


# ----------------------------------------------------------------- scorers
# Each returns [] (no score) on rows the metric does not apply to.

def _score(x):
    return [] if x is None else [chalkcompute.EvaluationScorerResult(score=float(x))]


@chalkcompute.function(image=IMAGE)
def warned_in_time(output: str, category: str, observed_arrival_h: float | None) -> list[chalkcompute.EvaluationScorerResult]:
    from evals_metrics import timely
    return _score(timely(output, category, observed_arrival_h, "warn"))


@chalkcompute.function(image=IMAGE)
def evacuate_in_time(output: str, category: str, observed_arrival_h: float | None) -> list[chalkcompute.EvaluationScorerResult]:
    from evals_metrics import timely
    return _score(timely(output, category, observed_arrival_h, "evacuate"))


@chalkcompute.function(image=IMAGE)
def evacuate_lead(output: str, category: str, observed_arrival_h: float | None) -> list[chalkcompute.EvaluationScorerResult]:
    """Evacuate lead time, full credit at 3 h ahead of the observed arrival."""
    from evals_metrics import lead_score
    return _score(lead_score(output, category, observed_arrival_h, "evacuate"))


@chalkcompute.function(image=IMAGE)
def no_over_evacuation(output: str, category: str) -> list[chalkcompute.EvaluationScorerResult]:
    from evals_metrics import no_over_evacuation as f
    return _score(f(output, category))


@chalkcompute.function(image=IMAGE)
def no_false_warning(output: str, category: str, dist_to_perimeter_km: float) -> list[chalkcompute.EvaluationScorerResult]:
    from evals_metrics import no_false_warning as f
    return _score(f(output, category, dist_to_perimeter_km))


class AlertQuality(BaseModel):
    score: float = Field(ge=0, le=1, description="Overall: would a frightened resident know what to do and when?")
    specific: int = Field(ge=0, le=2, description="States distance, direction and timing of the threat.")
    actionable: int = Field(ge=0, le=2, description="Ends with one clear next action matched to the level.")
    consistent: int = Field(ge=0, le=2, description="No contradictions (e.g. 'leave now' next to a 24 h ETA), "
                                                    "level and wording agree, escalations read as a sequence.")
    calm: int = Field(ge=0, le=2, description="Urgent where needed but not alarmist; under ~60 words each.")
    reason: str


SCORERS = [warned_in_time, evacuate_in_time, evacuate_lead, no_over_evacuation, no_false_warning]
SCORER_NAMES = ["warned_in_time", "evacuate_in_time", "evacuate_lead", "no_over_evacuation", "no_false_warning"]

JUDGE_INSTRUCTIONS = (
    "You grade the alert messages a wildfire early-warning agent sent one household during a past fire. "
    "The input is JSON; read the 'alerts' list (hour, level, message, eta_h, nearest_fire_km). If there are no "
    "alerts, score 1.0. Reply with JSON only, matching this schema: ")

if WITH_LLM:
    # Built-in llm_judge cannot send the X-Chalk-Env-Id header the HackHarvard
    # router key needs, so the judge is a scorer that uses the sentinel's client.
    @chalkcompute.function(image=IMAGE, call_timeout=300.0, **LLM_FN)
    def alert_quality(output: str) -> list[chalkcompute.EvaluationScorerResult]:
        import json

        from sentinel.policy import gateway_client

        resp = gateway_client().chat.completions.create(
            model=os.environ.get("SENTINEL_JUDGE_MODEL", "openai/gpt-6.1-sol"), max_tokens=2000,
            messages=[{"role": "system", "content": JUDGE_INSTRUCTIONS + json.dumps(AlertQuality.model_json_schema())},
                      {"role": "user", "content": output}])
        text = resp.choices[0].message.content or ""
        grade = AlertQuality.model_validate_json(text[text.index("{"):text.rindex("}") + 1])
        return [chalkcompute.EvaluationScorerResult(score=grade.score, metadata=grade.model_dump())]

    SCORERS.append(alert_quality)
    SCORER_NAMES.append("alert_quality")


def main():
    import argparse
    import json
    import subprocess

    from evals.run_local import load_rows

    ap = argparse.ArgumentParser()
    ap.add_argument("--policies", nargs="+", default=["radius", "rules"], choices=list(TASKS))
    ap.add_argument("--limit", type=int, help="first N rows of each incident/category group")
    ap.add_argument("--n", type=int, default=100, help="dataset: locations per group per fire")
    a = ap.parse_args()

    rows = load_rows(a.n)
    if a.limit:
        seen = {}
        rows = [r for r in rows if len(seen.setdefault((r["incident"], r["category"]), [None] * 0)) < a.limit
                and not seen[(r["incident"], r["category"])].append(r)]
    dataset = chalkcompute.DatasetClient().upload(f"firetrace-replay-n{a.n}" + (f"-l{a.limit}" if a.limit else ""), rows)
    suite = next((s for s in chalkcompute.EvaluationSuite.list_all() if s.name == "firetrace-sentinel"), None) \
        or chalkcompute.EvaluationSuite.create("firetrace-sentinel")
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()

    for policy in a.policies:
        ev = chalkcompute.Evaluation.create(f"sentinel-{policy}", dataset=dataset, task=TASKS[policy],
                                            scorers=SCORERS, suite_id=suite.id,
                                            metadata={"policy": policy, "rows": len(rows)})
        run = ev.run(metadata={"git_sha": sha}).wait()
        table = chalkcompute.DatasetClient().read(run.result_dataset)
        # Score columns are scorer_<id>, in the order the scorers were passed.
        cols = [c for c in table.column_names if c.startswith("scorer_") and not c.endswith("_error")]
        means = {SCORER_NAMES[i]: _mean(_scores(table[c].to_pylist()))
                 for i, (fn, c) in enumerate(zip(SCORERS, cols))}
        errors = sum(e is not None for e in table["output_error"].to_pylist())
        print(policy, run.status, f"task errors {errors}/{table.num_rows}", json.dumps(means))


def _scores(cells):
    """A scorer cell is a number, a struct with a score, or a list of those (empty = not applicable)."""
    out = []
    for c in cells:
        for x in (c if isinstance(c, list) else [c]):
            v = x.get("score") if isinstance(x, dict) else x
            if v is not None:
                out.append(v)
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


if __name__ == "__main__":
    main()
