"""Local API behind the web map's alert demo.

The map plays the fire; on every hour it calls POST /api/demo/step and the
sentinel decides, at that hour, whether your address needs an alert. Emails go
out on a background thread so playback never waits on SMTP; the map polls
GET /api/demo/state to show each alert and whether its email was sent.

  python -m sentinel demo-server            # http://127.0.0.1:8787, proxied by `npm run dev`
"""
import threading

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .demo import DemoSession, default_location

SESSION: DemoSession | None = None


async def config(request: Request):
    """?incident=<key> switches the demo to that fire (same address, same recipient)."""
    global SESSION
    want = request.query_params.get("incident")
    if want and want != SESSION.incident:
        try:
            SESSION = DemoSession(SESSION.lat, SESSION.lon, SESSION.label, want, SESSION.policy.name, SESSION.email)
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


app = Starlette(routes=[
    Route("/api/demo/config", config),
    Route("/api/demo/state", state),
    Route("/api/demo/step", step, methods=["POST"]),
    Route("/api/demo/reset", reset, methods=["POST"]),
])


def serve(port=8787, incident="eaton_2025", policy="rules", address=None, lat=None, lon=None, email=None):
    global SESSION
    import uvicorn

    from .demo import geocode
    if lat is not None and lon is not None:
        label = address or f"{lat:.4f}, {lon:.4f}"
    elif address:
        lat, lon = geocode(address)
        label = address
    else:
        loc = default_location()
        if not loc:
            raise SystemExit("set SENTINEL_DEMO_ADDRESS (and _LAT/_LON) in agent/.env or pass --address")
        lat, lon, label = loc
    SESSION = DemoSession(lat, lon, label, incident, policy, email)
    c = SESSION.config()
    print(f"Alert demo: {c['fire']} replayed toward {label}; emails to {c['email']}. "
          f"Open the map, pick {incident.split('_')[0].title()}, press 'Alert demo'.")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
