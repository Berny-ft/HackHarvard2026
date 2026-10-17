"""Decide an alert level and write the message.

RulePolicy  — thresholds on ETA/distance; the baseline and the fallback.
LLMPolicy   — an LLM (through the Chalk Model Gateway) reads the signals plus
              what it has already told this subscriber, and decides whether to
              escalate, hold or stand down, and what to say. It is only called
              when the rule triage says something is going on, which keeps cost
              bounded: most locations, most hours, are "none".
"""
import json

from .config import CHALK_ROUTER_API_KEY, CHALK_ROUTER_ENV_ID, CHALK_ROUTER_URL, LEVELS, MAX_TOKENS, MODEL
from .signals import level_rank

COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def compass(deg):
    return COMPASS[int(((deg or 0) + 22.5) // 45) % 8]


def template_message(level, s):
    if level == "none":
        return "No fire threat near you right now."
    wx = s["weather_now"]
    name, d = s["nearest_fire_name"] or "A fire", s["nearest_fire_km"]
    if d is None:
        where = "Critical fire weather in your area"
    elif d < 0.5:
        where = (f"{name} has burned within 500 m of you" if s.get("nearest_fire_confirmed")
                 else f"A fire ({name}) was reported within 500 m of you")
    else:
        where = f"{name} is {d:.1f} km to your {compass(s['fire_bearing_deg'])}"
    # Only quote an ETA when it is what drives the level; a long ETA next to
    # "leave now" (set off by distance alone) reads as a contradiction.
    eta_max = {"watch": 24, "warn": 8, "evacuate": 3}[level]
    eta = (f" and could reach you in about {max(1, round(s['eta_h']))} h"
           if s["eta_h"] is not None and s["eta_h"] <= eta_max and d is not None and d >= 0.5 else "")
    wind = f"Wind {wx['wind_mph']:.0f} mph gusting {wx['gust_mph']:.0f} from the {compass(wx['wind_dir_deg'])}."
    action = {
        "watch": "Stay alert, charge your phone and know two ways out.",
        "warn": "Get ready to leave: pack go-bags, load pets and medications, park facing out.",
        "evacuate": "Leave now. Do not wait for an official order if you feel unsafe.",
    }[level]
    return f"{where}{eta}. {wind} {action}"


class RulePolicy:
    name = "rules"

    def decide(self, s, history):
        level = s["rule_level"]
        return {"level": level, "message": template_message(level, s), "reasons": ["rule thresholds"]}


class RadiusPolicy:
    """Ablation baseline: fixed rings around any fire, blind to wind and fuel."""
    name = "radius"
    RINGS = (("evacuate", 3.0), ("warn", 8.0), ("watch", 15.0))

    def decide(self, s, history):
        d = s["nearest_fire_km"]
        level = next((lvl for lvl, r in self.RINGS if d is not None and d <= r), "none")
        return {"level": level, "message": template_message(level, s), "reasons": ["distance ring"]}


SYSTEM = """You are FireTrace Sentinel, an always-on wildfire early-warning agent for Los Angeles and Ventura counties.
For one subscriber location you receive physical threat signals (distance and bearing to the nearest fire, whether the
location is downwind, a modelled time-to-arrival, wind, fuel moisture, Red Flag status, 10-year burn probability) and
the alerts you have already sent this subscriber.

Decide the alert level:
- none: no meaningful threat.
- watch: a fire or critical fire weather in the region; stay alert.
- warn: fire could plausibly reach this location within ~8 hours; prepare to leave.
- evacuate: fire could reach this location within ~3 hours or is very close; leave now.

Principles: a missed warning can kill, a needless evacuation costs trust. Wind-driven fires under Santa Ana conditions
spot embers 1-2 km ahead of the front and the modelled ETA can be too slow, so lean toward escalating when the location
is downwind in extreme wind. Upwind or cross-wind locations far from the fire rarely need more than watch. Do not
de-escalate an evacuate unless the fire is clearly moving away. Messages go to people and to their personal agents:
be specific (distance, direction, timing), calm, under 60 words, and end with the single most important action.

Reply with JSON only: {"level": "...", "message": "...", "reasons": ["short", "reasons"]}"""


def _compact(s):
    keep = ("as_of", "nearest_fire_km", "nearest_fire_name", "nearest_fire_age_h", "nearest_fire_confirmed",
            "fire_bearing_deg", "downwind",
            "wind_alignment_deg", "eta_h", "head_ros_kmh", "active_points_within_25km", "red_flag",
            "burn_probability_10y", "weather_now", "outlook_12h", "rule_level")
    return {k: s[k] for k in keep}


class LLMPolicy:
    name = "llm"
    dry_run = False   # count the calls a run would make without making them

    def __init__(self, model=MODEL, client=None):
        self.model = model
        self.client = client or (None if self.dry_run else gateway_client())
        self.calls = 0
        self.tokens = 0

    def decide(self, s, history):
        if s["rule_level"] == "none" and not history:
            return RulePolicy().decide(s, history)
        prompt = json.dumps({"signals": _compact(s), "alerts_already_sent": history[-4:]}, default=str)
        self.calls += 1
        if self.dry_run:
            return RulePolicy().decide(s, history) | {"reasons": ["dry run"]}
        resp = self.client.chat.completions.create(
            model=self.model, max_tokens=MAX_TOKENS,   # the gateway route rejects temperature
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        )
        self.tokens += resp.usage.total_tokens if resp.usage else 0
        text = (resp.choices[0].message.content or "").strip()
        try:
            out = json.loads(text[text.index("{"):text.rindex("}") + 1])
            assert out["level"] in LEVELS
        except (ValueError, KeyError, AssertionError):
            out = RulePolicy().decide(s, history) | {"reasons": ["llm reply unparseable; rule fallback"]}
        return out


def gateway_client():
    """OpenAI-compatible client pointed at the Chalk Model Gateway."""
    from openai import OpenAI

    if not CHALK_ROUTER_API_KEY:
        raise RuntimeError("Set CHALK_ROUTER_API_KEY and CHALK_ROUTER_ENV_ID (agent/.env) to use the LLM policy.")
    return OpenAI(base_url=CHALK_ROUTER_URL, api_key=CHALK_ROUTER_API_KEY,
                  default_headers={"X-Chalk-Env-Id": CHALK_ROUTER_ENV_ID or ""})


POLICIES = {"rules": RulePolicy, "radius": RadiusPolicy, "llm": LLMPolicy}


def get_policy(name):
    return POLICIES[name]()


def escalated(prev, new):
    return prev is None or level_rank(new) > level_rank(prev)
