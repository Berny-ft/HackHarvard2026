"""FireTrace Sentinel CLI.

  python -m sentinel threat 34.18 -118.13           # live threat at a point
  python -m sentinel replay eaton_2025 34.18 -118.13 [--policy llm]
  python -m sentinel scan                           # most threatened areas now
  python -m sentinel route 34.20 -118.13 34.05 -118.25 [--mode replay:eaton_2025 --depart 6] [--hazard surge]
  python -m sentinel escape 34.19 -118.13 [--mode replay:eaton_2025 --depart 6] [--hazard surge]
  python -m sentinel subscribe 34.18 -118.13 --webhook http://localhost:9000/hook
  python -m sentinel subscribe 34.18 -118.13 --phone 818-555-0100 --consent
  python -m sentinel run [--once] [--every 15] [--policy llm] [--no-refresh] [--dry-run]
  python -m sentinel mcp [--http 8765] [--loop]
  python -m sentinel alert-demo [--address "..."] [--seconds-per-hour 3] [--policy llm]   # emails each alert
  python -m sentinel demo-server                    # API for the web map's "Alert demo"
  python -m sentinel webhook-sink [--port 9000]     # local receiver that verifies signatures
  python -m sentinel demo --address "..." [--phone +1...] [--send] [--serve 8080]   # replay a past fire at one house
  python -m sentinel web [--port 8080]              # inbound texts/voice, route page, dashboard
"""
import argparse
import json
import os


def main():
    ap = argparse.ArgumentParser(prog="sentinel")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("threat"); p.add_argument("lat", type=float); p.add_argument("lon", type=float)
    p.add_argument("--policy", default="rules")
    p = sub.add_parser("replay"); p.add_argument("incident"); p.add_argument("lat", type=float)
    p.add_argument("lon", type=float); p.add_argument("--policy", default="rules")
    sub.add_parser("scan")
    for name, pts in (("route", ("from_lat", "from_lon", "to_lat", "to_lon")), ("escape", ("lat", "lon"))):
        p = sub.add_parser(name)
        for k in pts:
            p.add_argument(k, type=float)
        p.add_argument("--hazard", default="fire", choices=["fire", "surge"])
        p.add_argument("--mode", default="live"); p.add_argument("--depart", type=float, default=0.0)
        p.add_argument("--margin", type=float); p.add_argument("--risk", default="balanced")
    p = sub.add_parser("subscribe"); p.add_argument("lat", type=float); p.add_argument("lon", type=float)
    p.add_argument("--webhook"); p.add_argument("--phone"); p.add_argument("--email"); p.add_argument("--label")
    p.add_argument("--min-level", default="watch"); p.add_argument("--consent", action="store_true")
    p = sub.add_parser("run"); p.add_argument("--once", action="store_true"); p.add_argument("--every", type=float, default=15)
    p.add_argument("--policy", default="rules"); p.add_argument("--no-refresh", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("mcp"); p.add_argument("--http", type=int)
    p.add_argument("--loop", action="store_true", help="also run the alert loop in the background")
    p.add_argument("--every", type=float, default=15)
    p = sub.add_parser("alert-demo", help="replay a real fire moved to your address, emailing each alert")
    p.add_argument("--address"); p.add_argument("--lat", type=float); p.add_argument("--lon", type=float)
    p.add_argument("--incident", default="eaton_2025", help="eaton_2025, palisades_2025, woolsey_2018, mountain_2024, franklin_2024")
    p.add_argument("--seconds-per-hour", type=float, default=3.0); p.add_argument("--policy", default="rules")
    p.add_argument("--email", help="recipient (default SENTINEL_ALERT_EMAIL)")
    p = sub.add_parser("demo-server", help="API for the web map's alert demo")
    p.add_argument("--port", type=int, default=8787); p.add_argument("--address")
    p.add_argument("--lat", type=float); p.add_argument("--lon", type=float)
    p.add_argument("--incident", default="eaton_2025", help="eaton_2025, palisades_2025, woolsey_2018, mountain_2024, franklin_2024")
    p.add_argument("--policy", default="rules"); p.add_argument("--email")
    p = sub.add_parser("webhook-sink"); p.add_argument("--port", type=int, default=9000)
    p = sub.add_parser("demo"); p.add_argument("--address")
    p.add_argument("--incident", default="eaton_2025"); p.add_argument("--phone")
    p.add_argument("--storm", help="replay a storm instead (SurgeTrace): sandy_2012, matthew_2016, irma_2017, isabel_2003")
    p.add_argument("--lat", type=float); p.add_argument("--lon", type=float)
    p.add_argument("--send", action="store_true", help="text the phone through Photon (default: print only)")
    p.add_argument("--serve", type=int, metavar="PORT"); p.add_argument("--delay", type=float, default=8.0)
    p = sub.add_parser("web"); p.add_argument("--port", type=int, default=8080)
    p = sub.add_parser("call", help="phone the number now (Photon voice)"); p.add_argument("--phone")
    p.add_argument("--message", default="This is a FireTrace Sentinel test call. A wildfire watch is in effect near your home.")
    a = ap.parse_args()

    if a.cmd == "threat":
        from .policy import get_policy
        from .signals import assess
        from .snapshot import live_snapshot
        s = assess(live_snapshot(), a.lat, a.lon)
        print(json.dumps({"decision": get_policy(a.policy).decide(s, []), "signals": s}, indent=1, default=str))
    elif a.cmd == "replay":
        from .policy import get_policy
        from .replay import observed_arrival_h, replay_subscriber
        r = replay_subscriber(a.incident, a.lat, a.lon, policy=get_policy(a.policy))
        arr = observed_arrival_h(a.incident, a.lat, a.lon)
        print(f"{a.incident}: fire observed here at hour {arr:.1f}" if arr is not None
              else f"{a.incident}: fire never observed within 750 m")
        for al in r["alerts"]:
            print(f"  h{al['hour']:>3}  {al['level']:<9} {al['message']}")
    elif a.cmd == "scan":
        from .live import scan_region
        print(json.dumps(scan_region(), indent=1))
    elif a.cmd in ("route", "escape"):
        from routing import escape_route, safe_route
        kw = {"hazard": a.hazard, "mode": a.mode, "depart_hour": a.depart, "margin_hours": a.margin, "risk": a.risk}
        r = (safe_route(a.from_lat, a.from_lon, a.to_lat, a.to_lon, **kw)
             if a.cmd == "route" else escape_route(a.lat, a.lon, **kw))
        r.pop("closed_at_departure", None)
        r.pop("blocking", None)
        if r.get("path"):
            r["path"] = f"<LineString, {len(r['path']['geometry']['coordinates'])} points>"
        print(json.dumps(r, indent=1))
    elif a.cmd == "subscribe":
        from . import store
        print(json.dumps(store.subscribe(a.lat, a.lon, a.label, a.webhook, a.phone, a.min_level, a.consent, a.email),
                         indent=1))
    elif a.cmd == "run":
        from .live import cycle, run_forever
        kw = {"policy_name": a.policy, "refresh": not a.no_refresh, "dry_run": a.dry_run}
        if a.once:
            print(json.dumps(cycle(**kw), indent=1, default=str))
        else:
            run_forever(a.every, **kw)
    elif a.cmd == "mcp":
        from .mcp_server import serve
        serve(a.http, loop=a.loop, every_min=a.every)
    elif a.cmd == "alert-demo":
        from .alert_demo import default_location, geocode, run
        if a.address:
            (lat, lon), label = geocode(a.address), a.address
        elif a.lat is not None and a.lon is not None:
            lat, lon, label = a.lat, a.lon, f"{a.lat:.4f}, {a.lon:.4f}"
        elif loc := default_location():
            lat, lon, label = loc
        else:
            ap.error("alert-demo needs --address, --lat/--lon, or SENTINEL_DEMO_ADDRESS in agent/.env")
        run(lat, lon, label, a.incident, a.seconds_per_hour, a.policy, a.email)
    elif a.cmd == "demo-server":
        from .demo_server import serve as demo_serve
        demo_serve(a.port, a.incident, a.policy, a.address, a.lat, a.lon, a.email)
    elif a.cmd == "demo" and a.storm:
        from surge_sentinel.texting import main as surge_demo
        surge_demo(a.storm, a.address, a.phone, a.send, a.serve, a.delay, a.lat, a.lon)
    elif a.cmd == "demo":
        from .demo import main as demo
        demo(a.address, a.incident, a.phone, a.send, a.serve, a.delay, a.lat, a.lon)
    elif a.cmd == "call":
        from .channels.voice import place_call
        from .store import normalize_phone
        phone = a.phone or os.environ.get("DEMO_PHONE")
        if not phone:
            raise SystemExit("Pass --phone or set DEMO_PHONE in agent/.env.")
        print(place_call(normalize_phone(phone), a.message))
    elif a.cmd == "web":
        from .web import serve
        serve(a.port)
    elif a.cmd == "webhook-sink":
        from .sink import serve
        serve(a.port)


if __name__ == "__main__":
    main()
