"""Subscribers and the alert log, as JSON files under agent/state/.

A subscriber is a location plus delivery channels. A subscription with no
channel is polled: the subscriber's agent calls check_alerts and gets what it
has not seen yet (Meta's Muse connects this way, over MCP). Deliberately nothing about
the person: life context (kids, pets, mobility, car) stays with their personal
agent, which pulls detail from the MCP tools and tailors the advice itself.
"""
import json
import threading
import uuid
from datetime import datetime, timezone

from .config import STATE

_lock = threading.Lock()
SUBS = STATE / "subscribers.json"
ALERTS = STATE / "alerts.jsonl"


def _load():
    return json.loads(SUBS.read_text()) if SUBS.exists() else {}


def _save(subs):
    STATE.mkdir(parents=True, exist_ok=True)
    SUBS.write_text(json.dumps(subs, indent=1))


def subscribe(lat, lon, label=None, webhook_url=None, phone=None, min_level="watch", email=None):
    sub = {"id": uuid.uuid4().hex[:12], "lat": lat, "lon": lon, "label": label,
           "webhook_url": webhook_url, "phone": phone, "email": email, "min_level": min_level,
           "created": datetime.now(timezone.utc).isoformat(), "last_level": "none", "read_up_to": None}
    with _lock:
        subs = _load()
        subs[sub["id"]] = sub
        _save(subs)
    return sub


def unsubscribe(sub_id):
    with _lock:
        subs = _load()
        found = subs.pop(sub_id, None) is not None
        _save(subs)
    return found


def get(sub_id):
    return _load().get(sub_id)


def unread_alerts(sub_id):
    """Alerts for a subscription since its last check, and mark them read."""
    with _lock:
        subs = _load()
        sub = subs.get(sub_id)
        if sub is None:
            return None
        rows = recent_alerts(sub_id, limit=10_000)
        seen = sub.get("read_up_to")
        ids = [r["id"] for r in rows]
        new = rows[ids.index(seen) + 1:] if seen in ids else rows
        if rows:
            sub["read_up_to"] = rows[-1]["id"]
            _save(subs)
    return new


def subscribers():
    return list(_load().values())


def set_last_level(sub_id, level):
    with _lock:
        subs = _load()
        if sub_id in subs:
            subs[sub_id]["last_level"] = level
            _save(subs)


def log_alert(alert):
    STATE.mkdir(parents=True, exist_ok=True)
    with _lock, ALERTS.open("a") as f:
        f.write(json.dumps(alert, default=str) + "\n")


def recent_alerts(sub_id=None, limit=20):
    if not ALERTS.exists():
        return []
    rows = [json.loads(line) for line in ALERTS.read_text().splitlines() if line.strip()]
    if sub_id:
        rows = [r for r in rows if r.get("subscriber_id") == sub_id]
    return rows[-limit:]
