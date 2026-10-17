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
from .config import REPO
from .notify import deliver
from .policy import get_policy
from .signals import assess, level_rank
from .snapshot import live_snapshot

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


def cycle(policy_name="rules", refresh=True, dry_run=False):
    started = datetime.now(timezone.utc)
    feeds = refresh_feeds() if refresh else (True, "skipped")
    snap = live_snapshot()
    policy = get_policy(policy_name)
    sent = [a for sub in store.subscribers() if (a := evaluate_subscriber(sub, snap, policy, dry_run))]
    return {"started": started.isoformat(), "feeds_ok": feeds[0], "fire_points": len(snap.fires),
            "subscribers": len(store.subscribers()), "alerts": sent}


def run_forever(every_min=15, **kw):
    while True:
        try:
            r = cycle(**kw)
            print(f"{r['started']} fires={r['fire_points']} subs={r['subscribers']} alerts={len(r['alerts'])}")
        except Exception as e:  # keep the sentinel alive through a bad feed
            print(f"cycle failed: {e!r}")
        time.sleep(every_min * 60)
