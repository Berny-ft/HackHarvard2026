"""Run the sentinel 24/7 in a Chalk sandbox: alert loop + MCP endpoint.

  chalk login && chalk environment <env-id>
  python deploy/sandbox.py            # create (or replace) the sandbox
  python deploy/sandbox.py --stop

The sandbox refreshes the live feeds with the modelling pipeline every cycle,
keeps subscribers and the alert log on a Chalk Volume (so restarts lose
nothing), and serves the MCP tools over streamable HTTP on port 8765.
Secrets come from the Chalk environment: CHALK_ROUTER_API_KEY (LLM policy),
FIRMS_MAP_KEY (satellite hot spots), SENTINEL_WEBHOOK_SECRET, and optionally
TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM for SMS,
SMTP_USER / SMTP_PASSWORD / SENTINEL_ALERT_EMAIL for email, and SENTINEL_MCP_TOKEN, the
bearer token the MCP endpoint requires.
"""
import argparse
import os
import sys

from chalkcompute import Image, RestartPolicy, Sandbox, Secret, VolumeClient

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
REPO = os.path.dirname(AGENT)
NAME = "firetrace-sentinel"
sys.path.insert(0, AGENT)
import sentinel.config  # noqa: E402,F401  (loads agent/.env: router env id, model, which optional secrets exist)

POLICY = os.environ.get("SENTINEL_POLICY", "llm")

image = (
    Image.debian_slim("3.12")
    .apt_install(["libexpat1"])   # rasterio's GDAL needs it; the slim image leaves it out
    .pip_install_from_requirements(os.path.join(REPO, "modeling", "requirements.txt"))
    .pip_install_from_requirements(os.path.join(AGENT, "requirements.txt"))
    .add_local_dir(os.path.join(REPO, "modeling", "wildfire"), "/app/modeling/wildfire", exclude=["__pycache__"])
    .add_local_dir(os.path.join(REPO, "modeling", "out"), "/app/modeling/out",
                   exclude=["incidents/*/*.tif", "*.png"])
    .add_local_dir(os.path.join(AGENT, "sentinel"), "/app/agent/sentinel", exclude=["__pycache__"])
    .env({"PYTHONPATH": "/app/agent", "SENTINEL_STATE": "/state", "SENTINEL_MCP_HOST": "0.0.0.0",
          "FIRETRACE_OUT": "/app/modeling/out",
          "FEED_MAX_SEG": "536"})   # the sandbox network drops full-size packets from some feeds; see wildfire/live.py
    .workdir("/app/agent")
)

secrets = [Secret.from_chalk_env(k) for k in ("CHALK_ROUTER_API_KEY", "FIRMS_MAP_KEY", "SENTINEL_WEBHOOK_SECRET",
                                              "SENTINEL_MCP_TOKEN")]
if os.environ.get("TWILIO_ACCOUNT_SID"):
    secrets += [Secret.from_chalk_env(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")]
if os.environ.get("SMTP_USER"):   # email alerts
    secrets += [Secret.from_chalk_env(k) for k in ("SMTP_USER", "SMTP_PASSWORD", "SENTINEL_ALERT_EMAIL")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stop", action="store_true")
    a = ap.parse_args()

    for info in Sandbox.list_all():
        if info.name == NAME and info.status not in ("terminated", "failed"):   # the list keeps old ones
            Sandbox.from_id(info.id).terminate()
            print(f"terminated {info.id}")
    if a.stop:
        return

    volumes = VolumeClient.from_env()
    if not volumes.exists("firetrace-state"):
        volumes.create("firetrace-state")
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
