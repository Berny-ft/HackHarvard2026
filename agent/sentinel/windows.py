"""Departure window math. Informative only; nothing is enforced.

"Earliest" is now. "Latest safe" is the router's leave-by time when it has one (the last departure whose
escape route still stays ahead of the projected hazard on every road), and otherwise an estimate from the
hazard's ETA at the house minus padded travel time.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TRAVEL_MIN = 45
CONGESTION_FACTOR = 1.5
BUFFER_MIN = 20
MAX_ETA_H = 12      # beyond this the ETA is too uncertain to show as a clock time


def _as_aware(iso, tz):
    t = datetime.fromisoformat(iso)
    return t if t.tzinfo else t.replace(tzinfo=ZoneInfo(tz))   # replay clocks are local wall time


def departure_window(now, eta_h, travel_min, confirmed, leave_by=None, tz="America/Los_Angeles"):
    if leave_by is None and (eta_h is None or eta_h > MAX_ETA_H):
        return None
    travel = travel_min if travel_min else DEFAULT_TRAVEL_MIN
    if leave_by is not None:
        latest = _as_aware(leave_by, tz)
    else:
        latest = now + timedelta(hours=eta_h) - timedelta(minutes=travel * CONGESTION_FACTOR + BUFFER_MIN)
    if confirmed and eta_h is not None and eta_h <= 3:
        conf = "high"
    elif confirmed:
        conf = "medium"
    else:
        conf = "low"
    if latest <= now:
        return {"status": "leave_now", "earliest": now.isoformat(), "latest": now.isoformat(),
                "confidence": conf, "travel_min": travel}
    return {"status": "window", "earliest": now.isoformat(), "latest": latest.isoformat(),
            "confidence": conf, "travel_min": travel}
