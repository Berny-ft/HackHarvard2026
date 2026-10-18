"""All texts to people. Official county orders always take precedence."""
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config
from .policy import compass


def clock(iso):
    t = datetime.fromisoformat(iso).astimezone(ZoneInfo(config.TZ))
    return t.strftime("%I:%M %p").lstrip("0")


def clock_rel(iso, ref_iso):
    """Clock time, with the weekday when it falls on a different day than ref (so overnight windows read clearly)."""
    t, r = (datetime.fromisoformat(x).astimezone(ZoneInfo(config.TZ)) for x in (iso, ref_iso))
    return (t.strftime("%a ") if t.date() != r.date() else "") + clock(iso)


def where(sit):
    d = sit.get("nearest_fire_km")
    name = sit.get("nearest_fire_name") or "A fire"
    if d is None:
        return "Critical fire weather in your area"
    if d < 0.5:
        return f"{name} is within 500 m of you"
    return f"{name} is {d:.1f} km to your {compass(sit.get('fire_bearing_deg'))}"


def contact_line():
    call = f" or call {config.PHOTON_NUMBER}" if config.PHOTON_NUMBER else ""
    return f"Text NOW for your route, LEFT once you're out{call}."


def map_link(sub):
    return f"{config.PUBLIC_URL}/route/{sub['token']}"


def _window_line(w):
    if not w:
        return None
    if w["status"] == "leave_now":
        return "There is no safe later window. Be ready to leave immediately."
    return (f"Planning window: leave between {clock(w['earliest'])} and {clock_rel(w['latest'], w['earliest'])}. "
            f"Estimate only ({w['confidence']} confidence), not an order.")


def sim_prefix(sub):
    return f"[DEMO: {sub['sim']}] " if sub.get("sim") else ""


def _at(sub):
    return f" at {sub['address']}" if sub.get("address") else ""


def warn_text(sub):
    parts = [f"{sim_prefix(sub)}FireTrace WARNING{_at(sub)}: {where(sub['situation'])}."]
    line = _window_line(sub.get("window"))
    if line:
        parts.append(line)
    parts.append("Official orders from your county take precedence. We will message you again on any change.")
    parts.append(contact_line())
    return " ".join(parts)


def evacuate_text(sub):
    return f"{sim_prefix(sub)}FireTrace EVACUATE{_at(sub)}: {where(sub['situation'])}. Leave now. {contact_line()}"


def reminder_text(sub):
    sit = sub["situation"]
    return f"{sim_prefix(sub)}Reminder ({sit['level'].upper()}){_at(sub)}: {where(sit)}. {contact_line()}"


TEXTS = {"warn": warn_text, "evacuate": evacuate_text, "reminder": reminder_text}


def route_reply(sub):
    r = sub.get("route")
    if not r:
        return ("No live route available. Head away from the fire, follow officers' directions, "
                "and check official sources. Emergency: call 911.")
    risk = (" Fire may reach parts of this route; it is the least-bad option, follow officers' directions."
            if r.get("fire_risk") else "")
    jam = " includes modelled evacuation traffic," if r.get("congestion_modelled") else ""
    return (f"Route as of {clock(r['computed_at'])}: {r['summary']} to a {r['dest_label']}, about "
            f"{round(r['duration_min'])} min{jam} ({r['distance_km']:.0f} km), planned to stay ahead of the fire. "
            f"Map: {map_link(sub)}.{risk} Check for closures and officers.")


def window_reply(sub):
    w = sub.get("window")
    level = (sub.get("situation") or {}).get("level", "none")
    if not w:
        return ("No departure time can be estimated: no fire is projected to reach you soon. "
                "We'll update you if that changes. Text STATUS for the latest.")
    if w["status"] == "leave_now":
        return ("There is no safe later window. Leave now." if level == "evacuate"
                else "There is no safe later window. Be ready to leave immediately.")
    if level == "evacuate":
        return (f"You are at EVACUATE level: leave now. Latest estimated safe departure is {clock(w['latest'])} "
                f"({w['confidence']} confidence estimate, not an order). Text NOW for your route.")
    return (f"Leave between {clock(w['earliest'])} and {clock_rel(w['latest'], w['earliest'])}. {w['confidence']} confidence "
            "estimate, not an order. Official orders take precedence.")


def status_reply(sub):
    sit = sub.get("situation") or {}
    if sit.get("level", "none") == "none":
        return "No active wildfire threat near you right now. We'll text you if that changes."
    return f"Current level: {sit['level'].upper()}. {where(sit)}. {contact_line()}"


def left_reply():
    return "Got it. We'll stop reminders. Text NOW anytime for your route."


def confirm_reply():
    return "Confirmed. You'll get FireTrace wildfire texts. Reply HELP for commands, STOP to cancel."


def stop_reply():
    return "You're unsubscribed from FireTrace texts. Text us again to sign up."


def help_reply():
    return ("NOW: your route. LEFT: you've left. STATUS: current level. WINDOW: when to leave. "
            "STOP: unsubscribe. Emergency: call 911.")


def call_opening(sub):
    """First thing the voice agent says when the person picks up."""
    sit = sub.get("situation") or {}
    demo = f"This is a demo call: {sub['sim']}. " if sub.get("sim") else ""
    act = "You should leave now." if sit.get("level") == "evacuate" else "Get ready to leave."
    return (f"{demo}This is FireTrace calling about a wildfire near your home. {where(sit)}. {act} "
            "Ask me why, when to leave, or which way to go.")


DEMO_LEVEL = {"watch": "WATCH", "warn": "WARNING", "evacuate": "EVACUATE"}


def demo_alert_text(sub, alert, calling=False):
    """The alert demo's text: the alert as the sentinel worded it, and what comes next."""
    nxt = ("We're calling you now." if calling else
           "Leave now." if alert["level"] == "evacuate" else "We'll call you if the fire gets closer.")
    return (f"{sim_prefix(sub)}FireTrace {DEMO_LEVEL.get(alert['level'], alert['level'].upper())}{_at(sub)}: "
            f"{alert['message']} {nxt} Official orders take precedence; call 911 in an emergency.")


def briefing(sub):
    """Everything the voice agent may tell the caller, in plain sentences, so it never has to guess."""
    sit = sub.get("situation") or {}
    if sit.get("level", "none") == "none":
        return status_reply(sub)
    lines = [f"{sim_prefix(sub)}Alert level: {sit['level'].upper()}{_at(sub)}.", f"{where(sit)}."]
    if sit.get("eta_h") is not None:
        lines.append(f"Model estimate of fire arrival at the home: about {sit['eta_h']:.1f} hours.")
    if sit.get("downwind"):
        lines.append("The wind is blowing from the fire toward the home.")
    wx = sit.get("weather") or {}
    if wx.get("wind_mph") is not None:
        lines.append(f"Weather: wind {wx['wind_mph']:.0f} mph gusting {wx.get('gust_mph') or 0:.0f} mph "
                     f"from the {compass(wx.get('wind_dir_deg'))}, humidity {wx.get('rh_pct', 0):.0f}%.")
    if sit.get("reasons"):
        lines.append("Why this level: " + "; ".join(sit["reasons"]) + ".")
    line = _window_line(sub.get("window"))
    if line:
        lines.append(line)
    lines.append(route_reply(sub))
    lines.append("Official orders from the county take precedence. Emergency: call 911.")
    return " ".join(lines)


def freeform_reply(sub):
    return f"{status_reply(sub)} For anything else, call 911 in an emergency."
