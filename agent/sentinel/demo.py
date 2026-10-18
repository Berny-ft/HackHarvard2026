"""Replay a past wildfire against one house and text the person as it unfolds.

  python -m sentinel demo --address "123 Main St, Altadena, CA" --incident eaton_2025
  python -m sentinel demo --address "..." --phone +15146646295 --send --serve 8080
  python -m sentinel demo --phone +15146646295 --send --call --serve 8080    # also phones you at EVACUATE

The sentinel sees only what was known at each hour of the real fire (satellite detections so far, the weather at
that hour). Texts carry a "[DEMO: ...]" tag so nobody mistakes them for a live emergency. Without --send, texts
print to the console. With --send they go out through Photon (needs photon-bridge running and PHOTON_PROJECT_ID
set), and the person's replies (NOW, WINDOW, LEFT, ...) are answered by the web server started with --serve.
With --call the person is also phoned (docs/VOICE_SETUP.md); the voice agent's questions reach the same server.
"""
import os
import shutil
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests


# Default demo house: a real Eaton Fire home CAL FIRE recorded as destroyed (from evals/data, coordinates only).
DEFAULT_HOUSE = (34.187316, -118.153195, "your Altadena home")


def geocode(address):
    """Address -> (lat, lon, display name) through OpenStreetMap Nominatim (free, no key)."""
    r = requests.get("https://nominatim.openstreetmap.org/search", timeout=15,
                     params={"q": address, "format": "json", "limit": 1},
                     headers={"User-Agent": "FireTraceSentinel-hackathon-demo"})
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise SystemExit(f"Could not find that address: {address!r}. Pass --lat and --lon instead.")
    return float(rows[0]["lat"]), float(rows[0]["lon"]), rows[0]["display_name"]


def _replay_loop(incident, step, delay, ch, sub_id, tz):
    from . import live, store
    from .snapshot import load_incident, replay_snapshot
    inc, _, _, _ = load_incident(incident)
    last_level = None
    for h in range(0, inc["hours"] + 1, step):
        snap = replay_snapshot(incident, h)
        now = datetime.fromisoformat(snap.as_of)
        if now.tzinfo is None:                       # incident clocks are Pacific local time
            now = now.replace(tzinfo=tz)
        r = live.cycle(refresh=False, channel=ch, snapshot=snap, now=now)
        sit = (store.get(sub_id) or {}).get("situation") or {}
        level = sit.get("level", "none")
        if level != last_level or r["texts"]:
            km = sit.get("nearest_fire_km")
            print(f"  hour {h:>2}  {now:%a %b %d %I:%M %p}  level={level:<9} "
                  f"nearest fire {f'{km:.1f} km' if km is not None else '-'}", flush=True)
            last_level = level
        for t in r["texts"]:
            ok = t["delivery"][0]
            print(f"    -> {t['text_kind'].upper()} text {'sent' if ok.get('ok') else 'FAILED: ' + str(ok.get('status'))}"
                  f"\n       {t['message']}", flush=True)
        for c in r["calls"]:
            ok = c["delivery"][0]
            print(f"    -> CALL {'placed, pick up' if ok.get('ok') else 'FAILED: ' + str(ok.get('status'))}"
                  f"\n       {c['message']}", flush=True)
        time.sleep(delay * (3 if r["calls"] else 1) if r["texts"] else 0.05)
    print("\nReplay finished.", flush=True)


def main(address=None, incident="eaton_2025", phone=None, send=False, serve_port=None, delay=8.0, lat=None, lon=None,
         call=False):
    from . import config, live, store
    from .channels import ConsoleChannel, get_channel, set_channel
    from .replay import observed_arrival_h
    from .snapshot import load_incident

    inc, _, _, _ = load_incident(incident)
    if address is None and (lat is None or lon is None):
        lat, lon, address = DEFAULT_HOUSE
        print(f"Using the built-in demo house: {lat:.5f}, {lon:.5f}")
    elif lat is None or lon is None:
        lat, lon, shown = geocode(address)
        print(f"Address: {shown}\n  -> {lat:.5f}, {lon:.5f}")
    phone = phone or os.environ.get("DEMO_PHONE")
    if not phone:
        raise SystemExit("Pass --phone or set DEMO_PHONE in agent/.env.")

    # Fresh state each run, kept apart from real subscribers.
    state = config.ROOT / "state" / "demo"
    shutil.rmtree(state, ignore_errors=True)
    config.STATE = state
    config.MAX_REMINDERS = 2                    # keep a real phone from being flooded during the replay

    if call:
        from .channels import voice
        if not voice.configured():
            raise SystemExit("--call needs ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID and ELEVENLABS_PHONE_NUMBER_ID in "
                             "agent/.env (docs/VOICE_SETUP.md).")
        config.CALLS = True
        print(f"Calling {phone} through the Photon number on: {', '.join(sorted(config.CALL_ON))}.")
    else:
        config.CALLS = False
    if send:
        if not config.PHOTON_PROJECT_ID:
            raise SystemExit("--send needs PHOTON_PROJECT_ID / PHOTON_PROJECT_SECRET in agent/.env "
                             "and `node bridge.mjs` running in photon-bridge/.")
        ch = get_channel()
        print(f"Texting {phone} through Photon. By using --send you confirm that number's owner agreed to the demo texts.")
    else:
        ch = ConsoleChannel()
        set_channel(ch)
        print("Console mode: texts print here and are not sent. Add --send to text the phone.")

    sub = store.subscribe(lat, lon, label="demo", phone=phone, consent=True)
    when = datetime.fromisoformat(inc["start"]).strftime("%b %Y")
    store.update(sub["id"], address=address, sim=f"{inc['name']} replay, {when}")

    from . import routing
    print("Loading the road network around the house (OpenStreetMap; first run can take a minute or two)...", flush=True)
    t0 = time.time()
    g = routing.load_graph(lat, lon)
    print(f"  {'road network ready (%d road nodes, %.0fs)' % (len(g.lat), time.time() - t0) if g else 'road data unavailable: routes will fall back to plain advice'}")

    arrival = observed_arrival_h(incident, lat, lon)
    print(f"\n{inc['name']} replay. Fire detected at this house at hour "
          f"{arrival:.0f}." if arrival is not None else f"\n{inc['name']} replay. Fire never reached this house.")
    print("Hour-by-hour (the sentinel sees only what was known by that hour):\n", flush=True)

    loop = threading.Thread(target=_replay_loop, daemon=True,
                            args=(incident, 1, delay, ch, sub["id"], ZoneInfo(config.TZ)))
    loop.start()
    if serve_port:
        from .web import serve
        print(f"Dashboard http://127.0.0.1:{serve_port}/  (replies to the demo texts are answered here)", flush=True)
        serve(serve_port)
    else:
        loop.join()
