"""All texts to people. Official county orders always take precedence."""
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config
from .policy import compass


def clock(iso, tz=None):
    t = datetime.fromisoformat(iso).astimezone(ZoneInfo(tz or config.TZ))
    return t.strftime("%I:%M %p").lstrip("0")


def clock_rel(iso, ref_iso):
    """Clock time, with the weekday when it falls on a different day than ref (so overnight windows read clearly)."""
    t, r = (datetime.fromisoformat(x).astimezone(ZoneInfo(config.TZ)) for x in (iso, ref_iso))
    return (t.strftime("%a ") if t.date() != r.date() else "") + clock(iso)


def product(sub_or_sit):
    sit = sub_or_sit.get("situation", sub_or_sit) or {}
    return "SurgeTrace" if sit.get("hazard") == "surge" else "FireTrace"


def _surge_where(sit):
    """'Hurricane Sandy: water expected 2.5 ft deep at your address around 9:00 PM.'"""
    name = sit.get("storm_name") or "The storm"
    now = sit.get("flooding_now_ft") or 0
    if now >= 0.5:
        return f"{name}: water is already about {now:.1f} ft deep at your address"
    depth, when = sit.get("forecast_peak_depth_ft") or 0, sit.get("water_at")
    if depth >= 0.5 and when:
        return f"{name}: water expected up to {depth:.1f} ft deep at your address, from about {clock(when)}"
    high = sit.get("high_peak_depth_ft") or 0
    if high >= 0.5:
        return f"{name}: in the high-water scenario your address floods up to {high:.1f} ft deep"
    return f"{name} is forecast to bring storm surge to your area"


def where(sit):
    if sit.get("hazard") == "surge":
        return _surge_where(sit)
    d = sit.get("nearest_fire_km")
    name = sit.get("nearest_fire_name") or "A fire"
    if d is None:
        return "Critical fire weather in your area"
    if d < 0.5:
        return f"{name} is within 500 m of you"
    return f"{name} is {d:.1f} km to your {compass(sit.get('fire_bearing_deg'))}"


def contact_line():
    call = f" or call {config.PHOTON_NUMBER}" if config.PHOTON_NUMBER else ""
    return f"Text NOW for your route, NEXT for step-by-step, LEFT once you're out{call}."


# ------------------------------------------------------------------ directions, for texts and for speech

_SHORT = [("Turn left onto ", "Left on "), ("Turn right onto ", "Right on "), ("Slight left onto ", "Bear left on "),
          ("Slight right onto ", "Bear right on "), ("Sharp left onto ", "Sharp left on "),
          ("Sharp right onto ", "Sharp right on "), ("Continue onto ", "Continue on "),
          (" Avenue", " Ave"), (" Street", " St"), (" Boulevard", " Blvd"), (" Drive", " Dr"), (" Road", " Rd"),
          (" Freeway", " Fwy"), (" Highway", " Hwy"), (" Parkway", " Pkwy"), (" Canyon", " Cyn"),
          ("North ", "N "), ("South ", "S "), ("East ", "E "), ("West ", "W ")]
_ROUTE_WORDS = {"I": "Interstate", "US": "U.S. Route", "CA": "California", "SR": "State Route", "NJ": "New Jersey",
                "NY": "New York", "FL": "Florida", "GA": "Georgia", "VA": "Virginia", "SC": "South Carolina"}


def _short(text):
    for a, b in _SHORT:
        text = text.replace(a, b)
    return text


def _spoken(text):
    """Route numbers read the way a person says them: 'I-210' -> 'Interstate 210'."""
    import re
    return re.sub(r"\b(I|US|CA|SR|NJ|NY|FL|GA|VA|SC)-(\d+)\b", lambda m: f"{_ROUTE_WORDS[m.group(1)]} {m.group(2)}",
                  text).replace("(", ", ").replace(")", "")


def _miles(mi, spoken=False):
    if mi < 0.15:
        return "a short way"
    if spoken:
        if mi < 0.4:
            return "about a quarter mile"
        if mi < 0.7:
            return "about half a mile"
        if mi < 1.3:
            return "about a mile"
        return f"about {round(mi)} miles"
    return f"{mi:.1f} mi"


def step_line(step, spoken=False):
    """One direction. Text: 'Left on Marengo Ave (0.7 mi)'. Speech: 'Turn left onto Marengo Avenue, for about
    half a mile.'"""
    if step["maneuver"] == "arrive":
        return "You've reached safe ground." if spoken else step["instruction"]
    if spoken:
        return f"{_spoken(step['instruction'])}, for {_miles(step['mi'], True)}."
    return f"{_short(step['instruction'])} ({_miles(step['mi'])})"


def steps_list(route, first=None, start=0):
    steps = (route or {}).get("directions") or []
    picked = steps[start:start + first] if first else steps[start:]
    return " ".join(f"{start + i + 1}) {step_line(s)}" for i, s in enumerate(picked))


def _noun(route):
    return "water" if (route or {}).get("hazard") == "surge" else "fire"


def _leave_by_line(sub):
    w = sub.get("window")
    if not w:
        return ""
    if w["status"] == "leave_now":
        return " Leave now."
    return f" Leave by {clock(w['latest'])} at the latest."


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
    parts = [f"{sim_prefix(sub)}{product(sub)} WARNING{_at(sub)}: {where(sub['situation'])}."]
    line = _window_line(sub.get("window"))
    if line:
        parts.append(line)
    parts.append("Official orders from your county take precedence. We will message you again on any change.")
    parts.append(contact_line())
    return " ".join(parts)


def evacuate_text(sub):
    r = sub.get("route")
    way = (f" Your way out ({round(r['duration_min'])} min): {steps_list(r, first=4)} Map: {map_link(sub)}."
           if r and r.get("directions") else "")
    return f"{sim_prefix(sub)}{product(sub)} EVACUATE{_at(sub)}: {where(sub['situation'])}. Leave now.{way} {contact_line()}"


def route_change_text(sub):
    r = sub["route"]
    return (f"{sim_prefix(sub)}{product(sub)}: your way out has changed. Now {r['summary']}, about "
            f"{round(r['duration_min'])} min: {steps_list(r, first=3)} Text NOW for the full route, NEXT for "
            f"step-by-step. Map: {map_link(sub)}")


def reminder_text(sub):
    sit = sub["situation"]
    return f"{sim_prefix(sub)}Reminder ({sit['level'].upper()}){_at(sub)}: {where(sit)}. {contact_line()}"


TEXTS = {"warn": warn_text, "evacuate": evacuate_text, "reminder": reminder_text, "route_change": route_change_text}


def route_reply(sub, spoken=False):
    """NOW: the summary, the leave-by time and the first steps (one step when spoken)."""
    r = sub.get("route")
    if not r:
        noun = "water" if (sub.get("situation") or {}).get("hazard") == "surge" else "fire"
        return (f"No live route available. Head away from the {noun}, follow officers' directions, "
                "and check official sources. Emergency: call 911.")
    noun = _noun(r)
    jam = ", including modelled evacuation traffic" if r.get("congestion_modelled") else ""
    if spoken:
        first = (r.get("directions") or [None])[0]
        return (f"Your fastest way out takes about {round(r['duration_min'])} minutes{jam}, {_spoken(r['summary'])}, "
                f"to a {r['dest_label']}.{_leave_by_line(sub)} Step 1: {step_line(first, True) if first else ''} "
                "Say next for the next step, or repeat to hear it again. I can also text you the map.")
    risk = (f" The {noun} may reach parts of this route; follow officers' directions." if r.get("fire_risk") else "")
    steps = f" {steps_list(r, first=4)}" if r.get("directions") else ""
    more = " Text NEXT for step-by-step, STEPS for all of them." if len(r.get("directions") or []) > 5 else ""
    return (f"Route as of {clock(r['computed_at'])}: {r['summary']} to a {r['dest_label']}, about "
            f"{round(r['duration_min'])} min{jam} ({r['distance_km']:.0f} km), planned to stay ahead of the {noun}."
            f"{_leave_by_line(sub)}{steps}{more} Map: {map_link(sub)}.{risk} Check for closures and officers.")


def steps_reply(sub, spoken=False):
    r = sub.get("route")
    if not r or not r.get("directions"):
        return route_reply(sub, spoken)
    if spoken:
        first = r["directions"][:3]
        return ("Here are the first steps. " + " ".join(f"Step {i + 1}: {step_line(s, True)}" for i, s in enumerate(first))
                + " Say next to keep going.")
    return f"All steps ({round(r['duration_min'])} min): {steps_list(r)} Map: {map_link(sub)}"


def step_reply(sub, index, spoken=False, changed=False):
    """One step (NEXT / REPEAT). `index` is 0-based."""
    r = sub.get("route")
    steps = (r or {}).get("directions") or []
    if not steps:
        return route_reply(sub, spoken)
    lead = "Your route changed since you last asked. " if changed else ""
    if index >= len(steps):
        return lead + ("That was the last step. You should be on safe ground. Text LEFT once you're out."
                       if not spoken else "That was the last step. You should be on safe ground. Say I've left once you're out.")
    s = steps[index]
    if spoken:
        tail = " Say next for the next step." if index < len(steps) - 1 else ""
        return f"{lead}Step {index + 1} of {len(steps)}: {step_line(s, True)}{tail}"
    tail = " NEXT for the next one." if index < len(steps) - 1 else ""
    return f"{lead}Step {index + 1}/{len(steps)}: {step_line(s)}{tail}"


def way_out(sub, tz=None):
    """The stored escape route, trimmed for agents (check_alerts) and emails: what to say and what to send."""
    r, w = sub.get("route"), sub.get("window") or {}
    if not r:
        return None
    leave_by = (w.get("latest") if w.get("status") == "window" else "now") if w else r.get("leave_by")
    return {"summary": f"{r['summary']} to a {r['dest_label']}", "minutes": round(r["duration_min"]),
            "leave_by": leave_by, "steps": [s["instruction"] for s in r.get("directions") or []],
            "map_page": map_link(sub) if sub.get("token") else None, "google_maps_url": r.get("google_maps_url"),
            "computed_at": r.get("computed_at"), "tz": tz}


def way_out_lines(wo):
    """Email block for a way out (plain text)."""
    if not wo:
        return []
    when = ("Leave now." if wo["leave_by"] == "now" else
            f"Leave by {clock(wo['leave_by'], wo.get('tz'))} at the latest." if wo.get("leave_by") else "")
    steps = [f"  {i + 1}. {s}" for i, s in enumerate(wo["steps"])]
    return ([f"YOUR WAY OUT: {wo['summary']}, about {wo['minutes']} min. {when}".rstrip()] + steps +
            ([f"Map: {wo['map_page']}"] if wo.get("map_page") else [])
            + ([f"Google Maps (follows this route): {wo['google_maps_url']}"] if wo.get("google_maps_url") else [])
            + [""])


def map_reply(sub):
    r = sub.get("route")
    gm = f" Google Maps (follows this route): {r['google_maps_url']}" if r and r.get("google_maps_url") else ""
    return f"Your route map: {map_link(sub)}.{gm}"


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
    return ("NOW: your route. NEXT / REPEAT: step-by-step directions. STEPS: all directions. MAP: the route map. "
            "LEFT: you've left. STATUS: current level. WINDOW: when to leave. STOP: unsubscribe. "
            "Emergency: call 911.")


def freeform_reply(sub):
    return f"{status_reply(sub)} For anything else, call 911 in an emergency."
