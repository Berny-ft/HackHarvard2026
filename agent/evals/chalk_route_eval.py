"""The route evaluation (evals/route_eval.py) as a Chalk Evaluation: were the escape routes actually safe?

One dataset row per person who needed to leave (fire: destroyed / burned homes; surge: flooded homes), one
task (plan the escape `lead` hours before the hazard really arrived and drive it on the real clock), and
scorers for our route and the plain fastest route against what really happened.

  chalk login && chalk environment <env-id>
  python -m evals.chalk_route_eval --n 20

Chalk imports this module inside each deployed function, so top-level code only defines functions; the
agent packages, replay data and road graphs ship in the image.
"""
import os

import chalkcompute

import sentinel.config  # noqa: F401  (loads agent/.env)

AGENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(AGENT)
OUT = os.path.join(REPO, "modeling", "out")
ROADS = os.path.join(REPO, "modeling", "data")


def _image():
    return (
        chalkcompute.Image.debian_slim("3.12")
        .pip_install(["numpy", "shapely", "rasterio", "pillow", "requests", "pydantic"])
        .add_local_dir(os.path.join(AGENT, "sentinel"), "/app/sentinel", exclude=["__pycache__"])
        .add_local_dir(os.path.join(AGENT, "surge_sentinel"), "/app/surge_sentinel", exclude=["__pycache__"])
        .add_local_dir(os.path.join(AGENT, "routing"), "/app/routing", exclude=["__pycache__"])
        .add_local_dir(os.path.join(AGENT, "evals"), "/app/evals", exclude=["__pycache__", "data"])
        .add_local_dir(os.path.join(OUT, "incidents"), "/app/out/incidents",
                       exclude=["*.png", "structures.geojson", "spread.json", "buildings.bin"])
        .add_local_dir(os.path.join(OUT, "surge"), "/app/out/surge", exclude=["live"])
        .add_local_file(os.path.join(ROADS, "roads_graph.json"), "/app/roads/roads_graph.json")
        .add_local_dir(os.path.join(ROADS, "surge_roads"), "/app/roads/surge_roads", exclude=["live"])
        .env({"FIRETRACE_OUT": "/app/out", "FIRETRACE_ROAD_DATA": "/app/roads", "PYTHONPATH": "/app",
              "SENTINEL_STATE": "/tmp/state"})
        .workdir("/app")
    )


IMAGE = _image() if os.path.isdir(os.path.join(AGENT, "routing")) and os.path.isdir(ROADS) else None
FN = dict(image=IMAGE, cpu="1", memory="4Gi", call_timeout=900.0)


@chalkcompute.function(**FN)
def escape_vs_reality(hazard: str, event: str, lat: float, lon: float, arrival_h: float) -> str:
    import json

    from evals.route_eval import run_case
    return json.dumps(run_case({"hazard": hazard, "event": event, "lat": lat, "lon": lon, "arrival_h": arrival_h}),
                      default=str)


def _score(output, key, as_bool=True):
    import json
    v = json.loads(output).get(key)
    return [] if v is None else [chalkcompute.EvaluationScorerResult(score=float(v) if not as_bool else float(bool(v)))]


@chalkcompute.function(image=IMAGE)
def route_given(output: str) -> list[chalkcompute.EvaluationScorerResult]:
    import json
    return [chalkcompute.EvaluationScorerResult(score=float(json.loads(output)["status"] == "ok"))]


@chalkcompute.function(image=IMAGE)
def ours_clear_in_reality(output: str) -> list[chalkcompute.EvaluationScorerResult]:
    return _score(output, "ours_clear")


@chalkcompute.function(image=IMAGE)
def plain_clear_in_reality(output: str) -> list[chalkcompute.EvaluationScorerResult]:
    return _score(output, "plain_clear")


@chalkcompute.function(image=IMAGE)
def ours_min_margin_h(output: str) -> list[chalkcompute.EvaluationScorerResult]:
    return _score(output, "ours_margin_h", as_bool=False)


SCORERS = [route_given, ours_clear_in_reality, plain_clear_in_reality, ours_min_margin_h]


def main():
    import argparse
    import subprocess

    from evals.route_eval import cases

    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="people per fire / storm")
    a = ap.parse_args()
    rows = cases("fire", a.n) + cases("surge", a.n)
    dataset = chalkcompute.DatasetClient().upload(f"firetrace-routes-n{a.n}", rows)
    suite = next((s for s in chalkcompute.EvaluationSuite.list_all() if s.name == "firetrace-routing"), None) \
        or chalkcompute.EvaluationSuite.create("firetrace-routing")
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    ev = chalkcompute.Evaluation.create("escape-vs-reality", dataset=dataset, task=escape_vs_reality,
                                        scorers=SCORERS, suite_id=suite.id, metadata={"rows": len(rows)})
    run = ev.run(metadata={"git_sha": sha}).wait()
    print("escape-vs-reality", run.status, "results in", run.result_dataset)


if __name__ == "__main__":
    main()
