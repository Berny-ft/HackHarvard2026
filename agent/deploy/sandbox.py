"""Run the sentinel 24/7 in a Chalk sandbox: alert loop + MCP endpoint.

  chalk login && chalk environment <env-id>
  python deploy/sandbox.py            # create (or replace) the sandbox
  python deploy/sandbox.py --stop

The sandbox refreshes the live feeds with the modelling pipeline every cycle,
keeps subscribers and the alert log on a Chalk Volume (so restarts lose
nothing), and serves the MCP tools over streamable HTTP on port 8765.
Secrets come from the Chalk environment: CHALK_ROUTER_API_KEY (LLM policy),
FIRMS_MAP_KEY (satellite hot spots), SENTINEL_WEBHOOK_SECRET, and optionally
TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM for SMS.
"""
import argparse
import os
import sys

from chalkcompute import Image, RestartPolicy, Sandbox, SandboxClient, Secret, Volume

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
REPO = os.path.dirname(AGENT)
NAME = "firetrace-sentinel"
POLICY = os.environ.get("SENTINEL_POLICY", "llm")

image = (
    Image.debian_slim("3.12")
    .pip_install_from_requirements(os.path.join(REPO, "modeling", "requirements.txt"))
    .pip_install_from_requirements(os.path.join(AGENT, "requirements.txt"))
    .add_local_dir(os.path.join(REPO, "modeling", "wildfire"), "/app/modeling/wildfire", exclude=["__pycache__"])
    .add_local_dir(os.path.join(REPO, "modeling", "out"), "/app/modeling/out",
                   exclude=["incidents/*/*.tif", "*.png"])
    .add_local_dir(os.path.join(AGENT, "sentinel"), "/app/agent/sentinel", exclude=["__pycache__"])
    .env({"PYTHONPATH": "/app/agent", "SENTINEL_STATE": "/state", "SENTINEL_MCP_HOST": "0.0.0.0",
          "FIRETRACE_OUT": "/app/modeling/out"})
    .workdir("/app/agent")
)

secrets = [Secret.from_chalk_env(k) for k in ("CHALK_ROUTER_API_KEY", "FIRMS_MAP_KEY", "SENTINEL_WEBHOOK_SECRET")]
if os.environ.get("TWILIO_ACCOUNT_SID"):
    secrets += [Secret.from_chalk_env(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stop", action="store_true")
    a = ap.parse_args()

    for info in SandboxClient().list():
        if info.name == NAME:
            Sandbox(sandbox_id=info.id).terminate()
            print(f"terminated {info.id}")
    if a.stop:
        return

    Volume("firetrace-state")  # created if missing
    sb = Sandbox(
        image=image, name=NAME, cpu="1", memory="4Gi", port=8765,
        env={"CHALK_ROUTER_ENV_ID": os.environ.get("CHALK_ROUTER_ENV_ID", ""),
             "SENTINEL_MODEL": os.environ.get("SENTINEL_MODEL", "openai/gpt-6.1-sol")}, secrets=secrets,
        volumes=[("firetrace-state", "/state")],
        restart_policy=RestartPolicy.ALWAYS,
        entrypoint=["sh", "-c", f"python -m sentinel mcp --http 8765 & "
                                f"python -m sentinel run --every 15 --policy {POLICY}"],
    ).run()
    print(f"sandbox {NAME} running; MCP at port 8765 (/mcp)")
    print(sb)


if __name__ == "__main__":
    sys.exit(main())
