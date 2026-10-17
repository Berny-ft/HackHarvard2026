"""Departure window math. Informative only; nothing is enforced."""
from datetime import timedelta

DEFAULT_TRAVEL_MIN = 45
CONGESTION_FACTOR = 1.5
BUFFER_MIN = 20
MAX_ETA_H = 12      # beyond this the ETA is too uncertain to show as a clock time


def departure_window(now, eta_h, travel_min, confirmed):
    if eta_h is None or eta_h > MAX_ETA_H:
        return None
    travel = travel_min if travel_min else DEFAULT_TRAVEL_MIN
    arrival = now + timedelta(hours=eta_h)
    latest = arrival - timedelta(minutes=travel * CONGESTION_FACTOR + BUFFER_MIN)
    if confirmed and eta_h <= 3:
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
