"""Subscribers, consent, escalation state and the alert log, as JSON files under agent/state/.

A subscriber is a location plus delivery channels. A subscription with no
channel is polled: the subscriber's agent calls check_alerts and gets what it
has not seen yet (Meta's Muse connects this way, over MCP). Deliberately nothing about
the person: life context (kids, pets, mobility, car) stays with their personal
agent. Single-process storage; use a database before running several processes.
"""
import json
import threading
import uuid
from datetime import datetime, timezone

from . import config

_lock = threading.RLock()


def _subs_path():
    return config.STATE / "subscribers.json"


def _alerts_path():
    return config.STATE / "alerts.jsonl"


def _load():
    p = _subs_path()
    return json.loads(p.read_text()) if p.exists() else {}


def _save(subs):
    config.STATE.mkdir(parents=True, exist_ok=True)
    tmp = _subs_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(subs, indent=1))
    tmp.replace(_subs_path())


def normalize_phone(p):
    p = (p or "").strip()
    if p.startswith("+"):
        return "+" + "".join(c for c in p if c.isdigit())
    digits = "".join(c for c in p if c.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits


def subscribe(lat, lon, label=None, webhook_url=None, phone=None, min_level="watch", consent=False, email=None):
    phone = normalize_phone(phone) if phone else None
    sub = {"id": uuid.uuid4().hex[:12], "token": uuid.uuid4().hex[:16], "lat": lat, "lon": lon,
           "label": label, "webhook_url": webhook_url, "phone": phone, "email": email,
           "consent": bool(consent and phone), "min_level": min_level,
           "created": datetime.now(timezone.utc).isoformat(), "last_level": "none", "read_up_to": None,
           "stage": "none", "stage_at": None, "reminders": 0, "left_at": None,
           "situation": None, "route": None, "route_at": None, "window": None}
    with _lock:
        subs = _load()
        subs[sub["id"]] = sub
        _save(subs)
    return sub


def get(sub_id):
    return _load().get(sub_id)


def _find(key, value):
    return next((s for s in _load().values() if value and s.get(key) == value), None)


def find_by_phone(phone):
    return _find("phone", normalize_phone(phone))


def find_by_token(token):
    return _find("token", token)


def update(sub_id, **fields):
    with _lock:
        subs = _load()
        if sub_id not in subs:
            return None
        subs[sub_id].update(fields)
        _save(subs)
        return subs[sub_id]


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


def log_alert(alert):
    config.STATE.mkdir(parents=True, exist_ok=True)
    with _lock, _alerts_path().open("a") as f:
        f.write(json.dumps(alert, default=str) + "\n")


def recent_alerts(sub_id=None, limit=20):
    p = _alerts_path()
    if not p.exists():
        return []
    rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    if sub_id:
        rows = [r for r in rows if r.get("subscriber_id") == sub_id]
    return rows[-limit:]
