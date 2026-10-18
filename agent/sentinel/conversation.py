"""Intent parsing and deterministic replies, shared by text and voice. No LLM.

Directions are a conversation: NOW gives the summary and the first steps (one step on a call), NEXT walks
through the route one step at a time, REPEAT says the current step again, STEPS lists them all, MAP sends the
map. The step a person is on is stored with them and resets when the route is recomputed (the reply then
says the route changed).
"""
import re

from . import messages, store

INTENTS = {
    "NOW": {"now", "route", "leaving now", "im leaving now", "i am leaving now", "leaving", "im leaving",
            "i am leaving", "how do i get out", "way out", "get me out", "escape", "what is my route",
            "whats my route", "my route"},
    "STEPS": {"steps", "directions", "all steps", "all directions", "list", "full route", "give me directions",
              "show directions", "the directions"},
    "NEXT": {"next", "next step", "then", "and then", "then what", "what next", "whats next", "what now",
             "continue", "go on", "ok", "okay", "done", "got it", "n"},
    "REPEAT": {"repeat", "again", "say again", "say that again", "what was that", "repeat that", "pardon",
               "sorry", "huh", "r"},
    "MAP": {"map", "link", "show me", "show me the map", "send the map", "send me the map", "text me the map",
            "google maps"},
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


def respond(sub, text, now_iso, channel="text"):
    reply = _respond(sub, text, now_iso, spoken=channel == "voice")
    return messages.sim_prefix(sub) + reply


def _route_key(sub):
    return (sub.get("route") or {}).get("computed_at")


def _cursor(sub):
    """(next step index, whether the route changed since the person last asked)."""
    same = sub.get("dir_for") == _route_key(sub)
    return (sub.get("dir_step", 0) if same else 0), (not same and sub.get("dir_for") is not None)


def _respond(sub, text, now_iso, spoken=False):
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
    level = (sub.get("situation") or {}).get("level", "none")
    if intent in ("NOW", "STEPS", "NEXT", "REPEAT", "MAP") and level == "none":
        return messages.status_reply(sub)
    if intent == "NOW":
        # The summary covers step 1 when spoken; the text lists the first four.
        shown = 1 if spoken else 4
        store.update(sub["id"], dir_step=shown, dir_for=_route_key(sub))
        return messages.route_reply(sub, spoken)
    if intent == "STEPS":
        store.update(sub["id"], dir_step=3 if spoken else len((sub.get("route") or {}).get("directions") or []),
                     dir_for=_route_key(sub))
        return messages.steps_reply(sub, spoken)
    if intent == "NEXT":
        i, changed = _cursor(sub)
        store.update(sub["id"], dir_step=i + 1, dir_for=_route_key(sub))
        return messages.step_reply(sub, i, spoken, changed)
    if intent == "REPEAT":
        i, changed = _cursor(sub)
        return messages.step_reply(sub, max(0, i - 1), spoken, changed)
    if intent == "MAP":
        if spoken:
            return "I've texted you the map. It shows the route and follows it in Google Maps."
        return messages.map_reply(sub)
    if intent == "WINDOW":
        return messages.window_reply(sub)
    if intent == "STATUS":
        return messages.status_reply(sub)
    return messages.freeform_reply(sub)
