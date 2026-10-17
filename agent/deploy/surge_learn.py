"""Run the surge learning loop in a Chalk sandbox.

The sandbox replays the train storms with the LLM policy, has the LLM distil
its mistakes into lessons, scores the held-out storms with and without them,
and writes lessons + report to a Chalk Volume. Every model call goes through
the Chalk Model Gateway, so it runs on the hackathon credits, not a laptop.

  chalk login && chalk environment <env-id>
  python deploy/surge_learn.py --limit 6
"""
import argparse
import os
import sys

from chalkcompute import Image, RestartPolicy, Sandbox, Secret, Volume

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
REPO = os.path.dirname(AGENT)
SURGE = os.path.join(REPO, "modeling", "out", "surge")

image = (
    Image.debian_slim("3.12")
    .pip_install(["numpy", "pillow", "openai", "pydantic", "requests", "shapely", "rasterio", "mcp>=2"])
    .add_local_dir(os.path.join(AGENT, "sentinel"), "/app/agent/sentinel", exclude=["__pycache__"])
    .add_local_dir(os.path.join(AGENT, "surge_sentinel"), "/app/agent/surge_sentinel", exclude=["__pycache__"])
    .add_local_dir(os.path.join(AGENT, "evals"), "/app/agent/evals", exclude=["__pycache__", "data/results_*"])
    .add_local_dir(os.path.join(SURGE, "storms"), "/app/modeling/out/surge/storms")
    .add_local_file(os.path.join(SURGE, "surge_model.json"), "/app/modeling/out/surge/surge_model.json")
    .env({"PYTHONPATH": "/app/agent", "FIRETRACE_OUT": "/app/modeling/out"})
    .workdir("/app/agent")
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=6, help="households per storm/category group")
    a = ap.parse_args()
    Volume("surgetrace-learn")  # created if missing
    cmd = (f"python -m evals.surge.learn --limit {a.limit} && "
           "cp /app/modeling/out/surge/lessons.md evals/data/surge_learn.json /out/")
    sb = Sandbox(
        image=image, name="surgetrace-learn", cpu="2", memory="4Gi",
        env={"CHALK_ROUTER_ENV_ID": os.environ.get("CHALK_ROUTER_ENV_ID", ""),
             "SENTINEL_MODEL": os.environ.get("SENTINEL_MODEL", "openai/gpt-6.1-sol")},
        secrets=[Secret.from_chalk_env("CHALK_ROUTER_API_KEY")],
        volumes=[("surgetrace-learn", "/out")], restart_policy=RestartPolicy.NEVER,
        entrypoint=["sh", "-c", cmd],
    ).run()
    print(f"learning sandbox started: {sb}. Lessons and report land on the surgetrace-learn volume.")


if __name__ == "__main__":
    sys.exit(main())
