"""FireTrace Sentinel CLI.

  python -m sentinel threat 34.18 -118.13           # live threat at a point
  python -m sentinel replay eaton_2025 34.18 -118.13 [--policy llm]
  python -m sentinel scan                           # most threatened areas now
  python -m sentinel subscribe 34.18 -118.13 --webhook http://localhost:9000/hook
  python -m sentinel run [--once] [--every 15] [--policy llm] [--no-refresh] [--dry-run]
  python -m sentinel mcp [--http 8765]
  python -m sentinel webhook-sink [--port 9000]     # local receiver that verifies signatures
"""
import argparse
import json


def main():
    ap = argparse.ArgumentParser(prog="sentinel")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("threat"); p.add_argument("lat", type=float); p.add_argument("lon", type=float)
    p.add_argument("--policy", default="rules")
    p = sub.add_parser("replay"); p.add_argument("incident"); p.add_argument("lat", type=float)
    p.add_argument("lon", type=float); p.add_argument("--policy", default="rules")
    sub.add_parser("scan")
    p = sub.add_parser("subscribe"); p.add_argument("lat", type=float); p.add_argument("lon", type=float)
    p.add_argument("--webhook"); p.add_argument("--phone"); p.add_argument("--label")
    p.add_argument("--min-level", default="watch")
    p = sub.add_parser("run"); p.add_argument("--once", action="store_true"); p.add_argument("--every", type=float, default=15)
    p.add_argument("--policy", default="rules"); p.add_argument("--no-refresh", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("mcp"); p.add_argument("--http", type=int)
    p = sub.add_parser("webhook-sink"); p.add_argument("--port", type=int, default=9000)
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
    elif a.cmd == "subscribe":
        from . import store
        print(json.dumps(store.subscribe(a.lat, a.lon, a.label, a.webhook, a.phone, a.min_level), indent=1))
    elif a.cmd == "run":
        from .live import cycle, run_forever
        kw = {"policy_name": a.policy, "refresh": not a.no_refresh, "dry_run": a.dry_run}
        if a.once:
            print(json.dumps(cycle(**kw), indent=1, default=str))
        else:
            run_forever(a.every, **kw)
    elif a.cmd == "mcp":
        from .mcp_server import serve
        serve(a.http)
    elif a.cmd == "webhook-sink":
        from .sink import serve
        serve(a.port)


if __name__ == "__main__":
    main()
