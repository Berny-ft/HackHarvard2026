"""Intent parsing and deterministic replies, shared by text and voice. No LLM."""
import re

from . import messages, store

INTENTS = {
    "NOW": {"now", "route", "directions", "leaving now", "im leaving now", "i am leaving now",
            "leaving", "im leaving", "i am leaving"},
    "LEFT": {"left", "i left", "ive left", "i have left", "im out", "i am out", "out"},
    "WINDOW": {"window", "when", "when should i leave", "when do i leave", "time"},
    "STATUS": {"status", "level", "update", "info", "whats happening", "what is happening"},
    "YES": {"yes", "y", "confirm"},
    "STOP": {"stop", "unsubscribe", "cancel", "quit", "end", "stopall"},
    "HELP": {"help"},
}


def classify(text):
    t = (text or "").strip()
    if t == "?":
        return "HELP"
    t = t.lower().replace("'", "").replace("’", "")
    t = re.sub(r"\s+", " ", re.sub(r"[^a-z]+", " ", t)).strip()
    for intent, words in INTENTS.items():
        if t in words:
            return intent
    return "FREEFORM"


def respond(sub, text, now_iso):
    reply = _respond(sub, text, now_iso)
    return messages.sim_prefix(sub) + reply


def _respond(sub, text, now_iso):
    intent = classify(text)
    if intent == "STOP":
        store.update(sub["id"], consent=False, pending_consent=False, unsubscribed_at=now_iso)
        return messages.stop_reply()
    if intent == "YES" and sub.get("pending_consent"):
        store.update(sub["id"], consent=True, pending_consent=False)
        return messages.confirm_reply()
    if intent == "HELP":
        return messages.help_reply()
    if intent == "LEFT":
        store.update(sub["id"], left_at=now_iso)
        return messages.left_reply()
    if intent == "NOW":
        level = (sub.get("situation") or {}).get("level", "none")
        return messages.route_reply(sub) if level != "none" else messages.status_reply(sub)
    if intent == "WINDOW":
        return messages.window_reply(sub)
    if intent == "STATUS":
        return messages.status_reply(sub)
    return messages.freeform_reply(sub)
