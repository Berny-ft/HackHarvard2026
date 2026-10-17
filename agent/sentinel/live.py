"""The always-on loop: refresh feeds, scan the region, alert subscribers.

  python -m sentinel run --every 15      # loop, minutes between cycles
  python -m sentinel run --once          # one cycle (cron / Chalk sandbox)
"""
import subprocess
import time
import uuid
from datetime import datetime, timezone

import numpy as np

from . import store
from .channels import get_channel
from .closures import active_closures, block_points
from .config import ACTIVE_WINDOW_H, REPO, ROUTE_MAX_AGE_MIN
from .escalation import minutes_since, next_action
from .messages import TEXTS
from .notify import deliver
from .policy import get_policy
from .signals import assess, level_rank
from .routing import best_route
from .snapshot import live_snapshot
from .windows import departure_window

MODELING = REPO / "modeling"


def refresh_feeds():
    """Re-run the modelling pipeline's live step (hot spots, weather, warnings)."""
    py = MODELING / ".venv" / "bin" / "python"
    r = subprocess.run([str(py if py.exists() else "python3"), "-m", "wildfire", "live"],
                       cwd=MODELING, capture_output=True, text=True, timeout=600)
    return r.returncode == 0, (r.stdout + r.stderr)[-500:]


def scan_region(snap=None, step=0.05, top=10):
    """Rule-level threat on a grid over the area with active fire nearby.

    Returns the most threatened cells, so the sentinel (and MCP clients) can say
    where in the city things are happening without anyone subscribing first.
    """
    snap = snap or live_snapshot()
    f = snap.fires
    if len(f) == 0:
        return []
    lats = np.arange(f.lat.min() - 0.15, f.lat.max() + 0.15, step)
    lons = np.arange(f.lon.min() - 0.15, f.lon.max() + 0.15, step)
    cells = []
    for la in lats:
        for lo in lons:
            s = assess(snap, float(la), float(lo), with_burn_prob=False)
            if s["rule_level"] in ("warn", "evacuate"):
                cells.append({"lat": round(float(la), 3), "lon": round(float(lo), 3), "level": s["rule_level"],
                              "eta_h": s["eta_h"], "nearest_fire_km": s["nearest_fire_km"],
                              "fire": s["nearest_fire_name"]})
    cells.sort(key=lambda c: (-level_rank(c["level"]), c["eta_h"] if c["eta_h"] is not None else 99))
    return cells[:top]


def evaluate_subscriber(sub, snap, policy, dry_run=False):
    """Assess one subscriber; on a relevant level change, log (and deliver) an alert and return it."""
    s = assess(snap, sub["lat"], sub["lon"])
    history = [{k: a[k] for k in ("as_of", "level", "message")} for a in store.recent_alerts(sub["id"], 4)]
    d = policy.decide(s, history)
    prev = sub.get("last_level", "none")
    changed = d["level"] != prev
    relevant = max(level_rank(d["level"]), level_rank(prev)) >= level_rank(sub.get("min_level", "watch"))
    if not (changed and relevant):
        return None
    alert = {"id": uuid.uuid4().hex[:12], "subscriber_id": sub["id"], "label": sub.get("label"),
             "as_of": s["as_of"], "level": d["level"], "previous": prev,
             "kind": "escalation" if level_rank(d["level"]) > level_rank(prev) else "de-escalation",
             "message": d["message"], "reasons": d.get("reasons", []), "policy": policy.name,
             "signals": {k: s[k] for k in ("lat", "lon", "nearest_fire_km", "nearest_fire_name", "fire_bearing_deg",
                                           "downwind", "eta_h", "red_flag", "weather_now")}}
    alert["delivery"] = [] if dry_run else deliver(sub, alert)
    store.log_alert(alert)
    store.set_last_level(sub["id"], d["level"])
    return alert


def _situation(s, level):
    return {"level": level, "as_of": s["as_of"], "nearest_fire_km": s["nearest_fire_km"],
            "nearest_fire_name": s["nearest_fire_name"], "nearest_fire_confirmed": s["nearest_fire_confirmed"],
            "fire_bearing_deg": s["fire_bearing_deg"], "eta_h": s["eta_h"], "downwind": s["downwind"]}


def _stale(iso, now, max_min):
    return not iso or minutes_since(iso, now) > max_min


def cycle(policy_name="rules", refresh=True, dry_run=False, channel=None, snapshot=None, now=None):
    """One pass over all subscribers. `snapshot`/`now` run it against a historical replay instead of live feeds."""
    started = now or datetime.now(timezone.utc)
    now_iso = started.isoformat()
    feeds = (True, "replay") if snapshot else refresh_feeds() if refresh else (True, "skipped")
    snap = snapshot or live_snapshot()
    policy = get_policy(policy_name)
    channel = channel or get_channel()
    # Caltrans full closures; [] if the feed is down, and for replays (today's closures are not history).
    blocks = [] if snapshot else block_points(active_closures())
    alerts, texts = [], []
    for sub in store.subscribers():
        s = assess(snap, sub["lat"], sub["lon"])
        history = [{k: a[k] for k in ("as_of", "level", "message") if k in a}
                   for a in store.recent_alerts(sub["id"], 4)]
        d = policy.decide(s, history)          # once per subscriber: the LLM policy is billed per call
        level = d["level"]
        upd = {"situation": _situation(s, level)}

        if level in ("warn", "evacuate"):
            if _stale(sub.get("route_at"), started, ROUTE_MAX_AGE_MIN):
                route = best_route(snap, sub["lat"], sub["lon"], blocks=blocks, now_iso=now_iso)
                upd["route"], upd["route_at"] = route, (now_iso if route else None)
            else:
                upd["route"] = sub.get("route")
            travel = (upd["route"] or {}).get("duration_min")
            upd["window"] = departure_window(started, s["eta_h"], travel, bool(s["nearest_fire_confirmed"]))
        else:
            upd.update(route=None, route_at=None, window=None)

        prev = sub.get("last_level", "none")
        min_rank = level_rank(sub.get("min_level", "watch"))
        if level != prev and max(level_rank(level), level_rank(prev)) >= min_rank:
            alert = {"id": uuid.uuid4().hex[:12], "subscriber_id": sub["id"], "label": sub.get("label"),
                     "as_of": s["as_of"], "level": level, "previous": prev,
                     "kind": "escalation" if level_rank(level) > level_rank(prev) else "de-escalation",
                     "message": d["message"], "reasons": d.get("reasons", []), "policy": policy.name,
                     "channel": "webhook",
                     "signals": {k: s[k] for k in ("lat", "lon", "nearest_fire_km", "nearest_fire_name", "fire_bearing_deg",
                                                   "downwind", "eta_h", "red_flag", "weather_now")}}
            alert["delivery"] = [] if dry_run else deliver(sub, alert)
            if not dry_run:
                store.log_alert(alert)
            alerts.append(alert)
        upd["last_level"] = level

        merged = {**sub, **upd}
        # A LEFT or STOP can arrive while the cycle runs; use the freshest copy.
        fresh = store.get(sub["id"]) if not dry_run else sub
        if fresh is None:
            continue
        merged["left_at"] = fresh.get("left_at") if level != "none" else merged.get("left_at")
        merged["consent"] = fresh.get("consent")
        kind, next_stage = next_action(merged, level, started)
        if kind and level_rank(level) < min_rank:
            kind = None
        if level == "none" and (merged.get("stage", "none") != "none" or merged.get("left_at")):
            upd.update(stage="none", stage_at=None, reminders=0, left_at=None)
        elif kind is None and next_stage != merged.get("stage", "none"):
            upd["stage"] = next_stage
        if kind:
            body = TEXTS[kind](merged)
            if dry_run:
                result = {"ok": True, "status": "dry-run"}
            else:
                try:
                    result = channel.send_text(sub["phone"], body)
                except Exception as e:     # a failed send must not stop the cycle
                    result = {"ok": False, "status": repr(e)}
            entry = {"id": uuid.uuid4().hex[:12], "subscriber_id": sub["id"], "as_of": s["as_of"],
                     "level": level, "channel": "text", "text_kind": kind, "kind": kind, "message": body,
                     "delivery": [result]}
            if not dry_run:
                store.log_alert(entry)
            texts.append(entry)
            upd.update(stage=next_stage, stage_at=now_iso)
            if kind == "reminder":
                upd["reminders"] = merged.get("reminders", 0) + 1
        if not dry_run:
            store.update(sub["id"], **upd)
    return {"started": now_iso, "feeds_ok": feeds[0], "fire_points": len(snap.fires),
            "subscribers": len(store.subscribers()), "road_blocks": len(blocks), "alerts": alerts, "texts": texts}


def run_forever(every_min=15, **kw):
    while True:
        try:
            r = cycle(**kw)
            print(f"{r['started']} fires={r['fire_points']} subs={r['subscribers']} alerts={len(r['alerts'])} texts={len(r['texts'])}")
        except Exception as e:  # keep the sentinel alive through a bad feed
            print(f"cycle failed: {e!r}")
        time.sleep(every_min * 60)
