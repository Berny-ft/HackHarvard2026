"""Host the web app in a Chalk sandbox: the built site, /data, and the alert-demo API.

  cd web && npm run build            # only when the site's code changes
  python deploy/web_sandbox.py       # create (or replace) the sandbox
  python deploy/web_sandbox.py --stop

One process serves everything: `sentinel demo-server` answers /api/demo/* and
/api/surge/*, and with WEB_DIST set it also serves the built site and
modeling/out as /data. It is a separate sandbox from the agent, so
redeploying the site never changes the MCP URL registered in the gateway.
Secrets: MAPBOX_TOKEN for the maps (edit it in the Chalk UI, then redeploy;
no rebuild needed), SMTP_USER / SMTP_PASSWORD / SENTINEL_ALERT_EMAIL for the demo emails
and SENTINEL_DEMO_ADDRESS / _LAT / _LON for the fire demo's address.
Calls on evacuate: ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID / ELEVENLABS_PHONE_NUMBER_ID,
PHOTON_WEBHOOK_SECRET (the agent's webhook tool sends it to /photon/voice) and DEMO_PHONE,
the number the demo rings (docs/VOICE_SETUP.md).
"""
import argparse
import os
import sys

from chalkcompute import Image, RestartPolicy, Sandbox, Secret

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
REPO = os.path.dirname(AGENT)
DIST = os.path.join(REPO, "web", "dist")
NAME = "firetrace-web"
PORT = 8080
sys.path.insert(0, AGENT)
import sentinel.config  # noqa: E402,F401  (loads agent/.env: which optional secrets exist)

image = (
    Image.debian_slim("3.12")
    .apt_install(["libexpat1"])   # rasterio's GDAL needs it; the slim image leaves it out
    .pip_install_from_requirements(os.path.join(AGENT, "requirements.txt"))
    .add_local_dir(DIST, "/app/web", exclude=["data", "data/*"])   # /data is served from modeling/out
    .add_local_dir(os.path.join(REPO, "modeling", "out"), "/app/modeling/out", exclude=["*.tif", "**/*.tif"])
    .add_local_dir(os.path.join(AGENT, "sentinel"), "/app/agent/sentinel", exclude=["__pycache__"])
    .add_local_dir(os.path.join(AGENT, "surge_sentinel"), "/app/agent/surge_sentinel", exclude=["__pycache__"])
    .env({"PYTHONPATH": "/app/agent", "FIRETRACE_OUT": "/app/modeling/out", "SENTINEL_STATE": "/tmp/state",
          "WEB_DIST": "/app/web", "SENTINEL_DEMO_HOST": "0.0.0.0"})
    .workdir("/app/agent")
)

secrets = [Secret.from_chalk_env("MAPBOX_TOKEN")]   # served to the maps as /config.js; change it in the Chalk UI
if os.environ.get("SMTP_USER"):   # demo emails
    secrets += [Secret.from_chalk_env(k) for k in ("SMTP_USER", "SMTP_PASSWORD", "SENTINEL_ALERT_EMAIL")]
if os.environ.get("SENTINEL_DEMO_LAT"):   # the fire demo's address
    secrets += [Secret.from_chalk_env(k) for k in ("SENTINEL_DEMO_ADDRESS", "SENTINEL_DEMO_LAT", "SENTINEL_DEMO_LON")]
if os.environ.get("ELEVENLABS_API_KEY"):   # phone calls on evacuate
    secrets += [Secret.from_chalk_env(k) for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID",
                                                   "ELEVENLABS_PHONE_NUMBER_ID", "PHOTON_WEBHOOK_SECRET", "DEMO_PHONE")]


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
    if not os.path.isfile(os.path.join(DIST, "index.html")):
        sys.exit("web/dist is missing: run `npm run build` in web/ first")

    Sandbox(
        image=image, name=NAME, cpu="1", memory="2Gi", port=PORT, secrets=secrets,
        restart_policy=RestartPolicy.ALWAYS,
        entrypoint=["python", "-m", "sentinel", "demo-server", "--port", str(PORT)],
    ).run()
    print(f"sandbox {NAME} running; site on port {PORT}")


if __name__ == "__main__":
    sys.exit(main())
