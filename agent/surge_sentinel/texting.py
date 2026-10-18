"""SurgeTrace by text and phone: the same conversation as FireTrace (NOW, NEXT, REPEAT, STEPS, MAP, WINDOW, LEFT)
for a storm replayed against one address.

  python -m sentinel demo --storm sandy_2012 --lat 40.5795 --lon -73.8236 [--phone +1...] [--send] [--serve 8080]

Each replay hour the surge sentinel decides the level from what was known then (latest NHC advisory, gauges so
far); at warn and evacuate the shared router (agent/routing, hazard "surge") finds the fastest way to safe
ground and the latest departure, capped by tropical-storm-force winds. Subscribers live in the same store as
FireTrace's, so replies to the texts and calls to the line are answered by `sentinel web` unchanged.
"""
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from routing import escape_route
from routing.engine import summary_for_texts
from sentinel import config, store
from sentinel.escalation import next_action
from sentinel.messages import TEXTS
from sentinel.windows import departure_window

from .policy import get_policy
from .signals import assess, level_rank
from .world import load_storm, observed_arrival_h

# Default homes: dry at ordinary high tide, flooded by the storm's surge near its peak (observed gauges),
# so a replay walks through watch -> warn -> evacuate with a way out.
DEFAULT_ADDRESS = {"sandy_2012": (40.5760, -73.8400, "your Rockaway Park home"),      # water ~hour 69, 4.7 ft
                   "irma_2017": (25.7780, -80.1410, "your Miami Beach home"),        # ~hour 73, 2.1 ft
                   "matthew_2016": (32.0090, -80.8510, "your Tybee Island home"),    # ~hour 64, 4.6 ft
                   "isabel_2003": (36.8930, -76.2480, "your Norfolk home")}          # ~hour 75, 4.2 ft


def situation(s, level, now):
    water_at = (now + timedelta(hours=s["eta_h"])).isoformat() if s.get("eta_h") is not None else None
    return {"level": level, "hazard": "surge", "storm_name": s["storm_name"], "eta_h": s.get("eta_h"),
            "water_at": water_at, "flooding_now_ft": s.get("flooding_now_ft"),
            "forecast_peak_depth_ft": s.get("forecast_peak_depth_ft"), "high_peak_depth_ft": s.get("high_peak_depth_ft")}


def cycle(sub, key, hour, now, channel, policy_name="rules", dry_run=False):
    """One replay hour for one subscriber: level, route, window, and any text it calls for."""
    s = assess(key, hour, sub["lat"], sub["lon"])
    history = [{k: a[k] for k in ("as_of", "level", "message") if k in a} for a in store.recent_alerts(sub["id"], 4)]
    d = get_policy(policy_name).decide(s, history)
    level = d["level"]
    now_iso = now.isoformat()
    upd = {"situation": situation(s, level, now)}
    if level in ("warn", "evacuate"):
        if sub.get("route_hour") != hour:
            r = escape_route(sub["lat"], sub["lon"], hazard="surge", mode=f"replay:{key}", depart_hour=hour)
            upd["route"], upd["route_hour"] = summary_for_texts(r, now_iso), hour
        else:
            upd["route"] = sub.get("route")
        route = upd["route"] or {}
        upd["window"] = departure_window(now, s.get("eta_h"), route.get("duration_min"), True,
                                         leave_by=route.get("leave_by"), tz=config.TZ)
    else:
        upd.update(route=None, route_hour=None, window=None, route_changes=0, dir_step=0)

    merged = {**sub, **upd}
    texts = []

    def send(kind):
        body = TEXTS[kind](merged)
        result = {"ok": True, "status": "dry-run"} if dry_run else channel.send_text(sub["phone"], body)
        entry = {"id": uuid.uuid4().hex[:12], "subscriber_id": sub["id"], "as_of": s["as_of"], "level": level,
                 "channel": "text", "text_kind": kind, "kind": kind, "message": body, "delivery": [result]}
        store.log_alert(entry)
        texts.append(entry)

    kind, next_stage = next_action(merged, level, now)
    if kind and level_rank(level) < level_rank(sub.get("min_level", "watch")):
        kind = None
    if level == "none" and merged.get("stage", "none") != "none":
        upd.update(stage="none", stage_at=None, reminders=0)
    elif kind:
        send(kind)
        upd.update(stage=next_stage, stage_at=now_iso)
        if kind == "reminder":
            upd["reminders"] = merged.get("reminders", 0) + 1
    else:
        from sentinel.live import _route_changed
        if _route_changed(sub.get("route"), upd.get("route")) and merged.get("stage") in ("warned", "evacuated") \
                and merged.get("consent") and not merged.get("left_at") \
                and sub.get("route_changes", 0) < config.MAX_ROUTE_CHANGE_TEXTS:
            send("route_change")
            upd["route_changes"] = sub.get("route_changes", 0) + 1
        elif next_stage != merged.get("stage", "none"):
            upd["stage"] = next_stage
    store.update(sub["id"], **upd)
    return level, texts


def _replay_loop(key, step, delay, ch, sub_id):
    info = load_storm(key)[0]
    start = datetime.fromisoformat(info["start"].replace("Z", "+00:00")).astimezone(timezone.utc)
    last = None
    for h in range(0, info["hours"] + 1, step):
        sub = store.get(sub_id)
        if sub is None:
            return
        level, texts = cycle(sub, key, h, start + timedelta(hours=h), ch)
        if level != last or texts:
            print(f"  hour {h:>3}  level={level:<9} texts={len(texts)}", flush=True)
            last = level
        time.sleep(delay if texts else min(delay, 1.0))
    print("\nReplay finished.", flush=True)


def main(storm="sandy_2012", address=None, phone=None, send=False, serve_port=None, delay=8.0, lat=None, lon=None,
         step=2):
    import os

    from sentinel.channels import ConsoleChannel, get_channel, set_channel
    from sentinel.demo import geocode

    info = load_storm(storm)[0]
    if lat is None or lon is None:
        if address:
            lat, lon, address = geocode(address)
        else:
            lat, lon, address = DEFAULT_ADDRESS[storm]
            print(f"Using the built-in demo address: {lat:.5f}, {lon:.5f}")
    phone = phone or os.environ.get("DEMO_PHONE")
    if not phone:
        raise SystemExit("Pass --phone or set DEMO_PHONE in agent/.env.")
    state = config.ROOT / "state" / "demo"
    shutil.rmtree(state, ignore_errors=True)
    config.STATE = state
    config.MAX_REMINDERS = 2
    config.TZ = info.get("tz", "America/New_York")         # texts show the storm's local clock
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
    when = datetime.fromisoformat(info["start"].replace("Z", "+00:00")).strftime("%b %Y")
    store.update(sub["id"], address=address, sim=f"{info['name']} replay, {when}")
    arrival, peak = observed_arrival_h(storm, lat, lon)
    print(f"\n{info['name']} replay. " + (f"Water reached this address at hour {arrival:.0f} ({peak:.1f} ft at the peak)."
                                         if arrival is not None else "Water never reached this address."))
    print("Every two replay hours (the sentinel sees only the forecasts and gauges available then):\n", flush=True)
    loop = threading.Thread(target=_replay_loop, daemon=True, args=(storm, step, delay, ch, sub["id"]))
    loop.start()
    if serve_port:
        from sentinel.web import serve
        print(f"Dashboard http://127.0.0.1:{serve_port}/  (replies to the demo texts are answered here)", flush=True)
        serve(serve_port)
    else:
        loop.join()
