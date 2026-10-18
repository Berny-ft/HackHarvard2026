"""Local API behind the web map's alert demo.

The map plays the fire; on every hour it calls POST /api/demo/step and the
sentinel decides, at that hour, whether your address needs an alert. Emails go
out on a background thread so playback never waits on SMTP; the map polls
GET /api/demo/state to show each alert and whether its email was sent.

  python -m sentinel demo-server            # http://127.0.0.1:8787, proxied by `npm run dev`

With DEMO_PHONE and the ElevenLabs keys set, the evacuate alert also phones that
number; the voice agent's webhook tool posts to /photon/voice here, and each
question and answer shows up in the map's alert card.

The surge map's demo uses /api/surge/*: POST config with a storm and any
address the user clicks, then the same step/state/reset cycle.
"""
import os
import threading

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .alert_demo import DemoSession, default_location

SESSION: DemoSession | None = None
SURGE = None          # surge_sentinel.demo.SurgeDemoSession, created when the map picks an address
POLICY = "rules"


async def config(request: Request):
    """?incident=<key> switches the demo to that fire (same address, same recipient)."""
    global SESSION
    if SESSION is None:
        return JSONResponse({"error": "no demo address; set SENTINEL_DEMO_ADDRESS in agent/.env"}, status_code=503)
    want = request.query_params.get("incident")
    if want and want != SESSION.incident:
        try:
            SESSION = DemoSession(SESSION.lat, SESSION.lon, SESSION.label, want, SESSION.policy.name, SESSION.email,
                                  SESSION.phone)
        except (FileNotFoundError, SystemExit) as e:
            return JSONResponse({"error": f"no replay for {want}: {e}"}, status_code=404)
    return JSONResponse(SESSION.config())


async def state(request: Request):
    return JSONResponse(SESSION.state())


async def step(request: Request):
    hour = int((await request.json())["hour"])
    for alert in SESSION.step_to(hour):
        threading.Thread(target=SESSION.send, args=(alert,), daemon=True).start()
    return JSONResponse(SESSION.state())


async def reset(request: Request):
    with SESSION._lock:
        SESSION.reset()
    return JSONResponse(SESSION.state())


async def photon_voice(request: Request):
    """The voice agent's webhook tool: {"from", "transcript"} -> {"reply", "briefing"} for the demo phone."""
    from .store import normalize_phone
    from .web import UNKNOWN, _read

    data, err = await _read(request)
    if err:
        return err
    if SESSION is None or not SESSION.phone or normalize_phone(data.get("from", "")) != SESSION.phone:
        return JSONResponse({"reply": UNKNOWN, "briefing": UNKNOWN})
    reply, brief = SESSION.answer(data.get("transcript", ""))
    return JSONResponse({"reply": reply, "briefing": brief})


# ------------------------------------------------------------- surge demo

async def surge_config(request: Request):
    """POST {storm, lat, lon, label?}: watch this address through this storm."""
    global SURGE
    from surge_sentinel.demo import SurgeDemoSession

    b = await request.json()
    try:
        SURGE = SurgeDemoSession(b["storm"], float(b["lat"]), float(b["lon"]), b.get("label"), POLICY)
    except FileNotFoundError as e:
        return JSONResponse({"error": f"no replay for {b.get('storm')}: {e}"}, status_code=404)
    return JSONResponse(SURGE.config())


def _surge_ready():
    return SURGE is not None or JSONResponse({"error": "pick an address first"}, status_code=409)


async def surge_state(request: Request):
    if (r := _surge_ready()) is not True:
        return r
    return JSONResponse(SURGE.state())


async def surge_step(request: Request):
    if (r := _surge_ready()) is not True:
        return r
    hour = int((await request.json())["hour"])
    for alert in SURGE.step_to(hour):
        threading.Thread(target=SURGE.send, args=(alert,), daemon=True).start()
    return JSONResponse(SURGE.state())


async def surge_reset(request: Request):
    if (r := _surge_ready()) is not True:
        return r
    with SURGE._lock:
        SURGE.reset()
    return JSONResponse(SURGE.state())


class _SinglePageApp(StaticFiles):
    """Built web app; paths that are not files (/map, /surge/map) get index.html."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


routes = [
    Route("/api/demo/config", config),
    Route("/api/demo/state", state),
    Route("/api/demo/step", step, methods=["POST"]),
    Route("/api/demo/reset", reset, methods=["POST"]),
    Route("/photon/voice", photon_voice, methods=["POST"]),
    Route("/api/surge/config", surge_config, methods=["POST"]),
    Route("/api/surge/state", surge_state),
    Route("/api/surge/step", surge_step, methods=["POST"]),
    Route("/api/surge/reset", surge_reset, methods=["POST"]),
]
async def site_config(request: Request):
    """The Mapbox token for the maps, from the MAPBOX_TOKEN secret, so it can change without a rebuild."""
    import json
    return Response(f"window.MAPBOX_TOKEN = {json.dumps(os.environ.get('MAPBOX_TOKEN', ''))};\n",
                    media_type="application/javascript", headers={"Cache-Control": "no-store"})


# Hosted (deploy/web_sandbox.py): also serve the built site and the model outputs it reads.
if os.environ.get("WEB_DIST"):
    from .config import MODEL_OUT
    routes += [Route("/config.js", site_config),
               Mount("/data", StaticFiles(directory=MODEL_OUT)),
               Mount("/", _SinglePageApp(directory=os.environ["WEB_DIST"], html=True))]
app = Starlette(routes=routes)


def serve(port=8787, incident="eaton_2025", policy="rules", address=None, lat=None, lon=None, email=None):
    global SESSION, POLICY
    POLICY = policy
    import uvicorn

    from .alert_demo import geocode
    from .config import CALL_ON
    if lat is not None and lon is not None:
        label = address or f"{lat:.4f}, {lon:.4f}"
    elif address:
        lat, lon = geocode(address)
        label = address
    else:
        loc = default_location()
        lat, lon, label = loc if loc else (None, None, None)
    if lat is not None:
        SESSION = DemoSession(lat, lon, label, incident, policy, email)
        c = SESSION.config()
        calls = f"; calls {c['phone']} on {', '.join(sorted(CALL_ON))}" if c["calls"] else ""
        print(f"Fire alert demo: {c['fire']} replayed toward {label}; emails to {c['email']}{calls}. "
              f"Open the map, pick {incident.split('_')[0].title()}, press 'Alert demo'.")
    else:
        print("Fire alert demo off (set SENTINEL_DEMO_ADDRESS in agent/.env to enable it).")
    print("Surge alert demo: open /surge/map, press 'Alert demo' and click any address.")
    uvicorn.run(app, host=os.environ.get("SENTINEL_DEMO_HOST", "127.0.0.1"), port=port, log_level="warning")
