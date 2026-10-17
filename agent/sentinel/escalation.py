"""Text sequence state machine: warn once, evacuate once, then reminders."""
from datetime import datetime

from . import config


def minutes_since(iso, now):
    if not iso:
        return float("inf")
    return (now - datetime.fromisoformat(iso)).total_seconds() / 60.0


def next_action(sub, level, now):
    """-> (text_kind | None, next_stage). Pure; no I/O."""
    stage = sub.get("stage", "none")
    if level == "none":
        return None, "none"
    if not sub.get("consent") or not sub.get("phone") or sub.get("left_at"):
        return None, stage
    if stage == "none" and level == "warn":
        return "warn", "warned"
    if stage in ("none", "warned") and level == "evacuate":
        return "evacuate", "evacuated"
    if (stage == "evacuated" and level == "evacuate"
            and minutes_since(sub.get("stage_at"), now) >= config.REMINDER_EVERY_MIN
            and sub.get("reminders", 0) < config.MAX_REMINDERS):
        return "reminder", "evacuated"
    return None, stage
