"""Decide a flood alert level and write the message.

ZonePolicy  — the generic official approach, as a baseline: evacuation zones
              by ground height, ordered by the storm's category at closest
              approach, the same for every address in a zone.
RulePolicy  — this address's own forecast flood depth and timing (signals.py).
LLMPolicy   — an LLM through the Chalk Model Gateway reads the same signals
              plus what it already told this household, and decides. Optional
              "lessons" (learned on the train storms, evals/surge/learn.py)
              are added to its instructions.
"""
import json

from sentinel.config import MAX_TOKENS, MODEL
from sentinel.policy import gateway_client

from .config import LEVELS, SURGE_OUT, ZONE_SPILL_FT, ZONES_FOR_CATEGORY
from .signals import level_rank

LESSONS = SURGE_OUT / "lessons.md"
CAT = {-1: "a depression", 0: "a tropical storm", 1: "a Category 1 hurricane", 2: "a Category 2 hurricane",
       3: "a Category 3 hurricane", 4: "a Category 4 hurricane", 5: "a Category 5 hurricane"}


def _when(h):
    if h is None:
        return ""
    if h <= 1:
        return "within the hour"
    return f"in about {round(h)} hours"


def _depths(s):
    lo, hi = s["forecast_peak_depth_ft"], s["high_peak_depth_ft"]
    if lo >= 0.5 and hi - lo >= 1:
        return f"about {lo:.0f} ft of seawater, possibly {hi:.0f} ft,"
    return f"about {max(lo, hi if lo < 0.5 else lo):.0f} ft of seawater"


def template_message(level, s, previous="none"):
    st = s["storm"]
    name = s["storm_name"].replace("Hurricane ", "")
    if s["flooding_now_ft"] >= 0.5:
        return (f"Seawater is about {s['flooding_now_ft']:.1f} ft deep at your address now. Move to the highest floor "
                f"you can reach, stay out of the water, and call 911 if you are trapped.")
    if level_rank(level) < level_rank(previous):
        if level == "none":
            return f"The flood threat from {name} has ended at your address. Stay out of standing water; it may hide debris and live wires."
        return (f"{name}'s surge has peaked here and the water is going down. Don't return or drive through flooded "
                f"streets until officials say roads are clear.")
    if level == "none":
        return "No flood threat at your address right now."
    storm = (f"{name} is {CAT.get(st['category'], 'a storm')} {st['distance_km']} km to your {st['bearing']}"
             if st else f"{name}'s surge is rising")
    if level == "evacuate":
        eta = s["eta_h"]
        return (f"{storm}. Storm surge could put {_depths(s)} at your address {_when(eta)}. "
                f"Leave now for higher ground inland, before roads flood.")
    if level == "warn":
        return (f"{storm}. Storm surge could flood your address {_when(s['eta_h'])}, with {_depths(s)} at the peak. "
                f"Get ready to leave: pack medications and documents, "
                f"fuel the car and move it to higher ground.")
    return (f"{storm}, forecast to pass within {st['closest_km']} km in about {st['closest_in_h']} hours. Your address is "
            f"low enough to flood in a strong surge. Know your route to higher ground and keep your phone charged.")


class RulePolicy:
    name = "rules"

    def decide(self, s, history):
        level = s["rule_level"]
        prev = history[-1]["level"] if history else "none"
        return {"level": level, "message": template_message(level, s, prev), "reasons": ["forecast flood depth at this address"]}


class ZonePolicy:
    """Generic official baseline: every address in the zone gets the same order."""
    name = "zones"

    def decide(self, s, history):
        st, spill = s["storm"], s["spill_ft"]
        level = "none"
        if st and spill is not None and st["closest_km"] is not None:
            zones = ZONES_FOR_CATEGORY.get(max(st["category_at_closest"], 0), "")
            in_zone = any(spill <= ZONE_SPILL_FT[z] for z in zones)
            if in_zone and st["closest_km"] <= 150 and st["closest_in_h"] <= 36:
                level = "evacuate"
            elif in_zone and st["closest_km"] <= 250 and st["closest_in_h"] <= 72:
                level = "warn"
            elif st["category_at_closest"] >= 0 and st["closest_km"] <= 400 and st["closest_in_h"] <= 72 and spill <= 20:
                level = "watch"
        if s["flooding_now_ft"] >= 0.5:
            level = "evacuate"
        msg = {
            "evacuate": "Mandatory evacuation ordered for your evacuation zone. Leave now.",
            "warn": "Your evacuation zone may be ordered to evacuate. Prepare to leave.",
            "watch": "A hurricane threatens the coast. Know your evacuation zone.",
            "none": "No evacuation order for your zone.",
        }[level]
        return {"level": level, "message": msg, "reasons": ["evacuation zone by elevation and storm category"]}


SYSTEM = """You are SurgeTrace Sentinel, an always-on storm-surge early-warning agent for the US East Coast.
For one household you receive: the ground height and spill level of the address (the water level, ft NAVD88, at
which seawater can reach it), the water level observed now at nearby tide gauges, and a forecast built from the
latest NHC official advisory: expected and high-scenario flood depth at the address and when it would start, the
storm's category, distance and closest approach. You also see the alerts you already sent this household.

Decide the alert level:
- none: no meaningful flood threat to this address.
- watch: a tropical storm or hurricane threatens the region and the address is low enough to flood.
- warn: the address could plausibly flood within ~36 hours; get ready to leave.
- evacuate: the address is likely to flood within ~24 hours, or is flooding now; leave now.

Principles: surge drowns more people than wind, and roads to higher ground flood hours before the peak, so an evacuate
must come early (12+ hours) to be useful. But a needless evacuation of an address that stays dry costs trust and
clogs roads for those who must leave. Forecast tracks shift by 50-100 km at two days out, which can double or halve the
surge at a bay; weigh the high scenario more when the storm is strong, large, or aimed so the address is right of track.
Addresses whose spill level is well above even the high-scenario water should not be told to evacuate. Once you have
said evacuate, hold it until the peak water has passed. Messages go to people and their personal agents: specific
(expected depth, timing), calm, under 60 words, ending with the single most important action.

Reply with JSON only: {"level": "...", "message": "...", "reasons": ["short", "reasons"]}"""


def system_prompt():
    lessons = LESSONS.read_text().strip() if LESSONS.exists() else ""
    return SYSTEM + (f"\n\nLessons from replaying past storms:\n{lessons}" if lessons else "")


def _compact(s):
    keep = ("as_of", "ground_ft", "spill_ft", "water_now_ft", "surge_now_ft", "flooding_now_ft",
            "forecast_peak_water_ft", "forecast_peak_depth_ft", "high_peak_depth_ft", "eta_h", "eta_high_h",
            "peak_in_h", "storm", "rule_level")
    return {k: s[k] for k in keep}


class LLMPolicy:
    name = "llm"
    dry_run = False

    def __init__(self, model=MODEL, client=None, lessons=True):
        self.model = model
        self.client = client or (None if self.dry_run else gateway_client())
        self.system = system_prompt() if lessons else SYSTEM
        self.calls = 0
        self.tokens = 0

    def decide(self, s, history):
        if s["rule_level"] == "none" and not history and not (s["storm"] and s["spill_ft"] is not None
                                                                and s["high_peak_depth_ft"] > 0):
            return RulePolicy().decide(s, history)
        self.calls += 1
        if self.dry_run:
            return RulePolicy().decide(s, history) | {"reasons": ["dry run"]}
        prompt = json.dumps({"signals": _compact(s), "alerts_already_sent": history[-4:]}, default=str)
        resp = self.client.chat.completions.create(
            model=self.model, max_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": self.system}, {"role": "user", "content": prompt}],
        )
        self.tokens += resp.usage.total_tokens if resp.usage else 0
        text = (resp.choices[0].message.content or "").strip()
        try:
            out = json.loads(text[text.index("{"):text.rindex("}") + 1])
            assert out["level"] in LEVELS
        except (ValueError, KeyError, AssertionError):
            out = RulePolicy().decide(s, history) | {"reasons": ["llm reply unparseable; rule fallback"]}
        return out


POLICIES = {"zones": ZonePolicy, "rules": RulePolicy, "llm": LLMPolicy}


def get_policy(name):
    return POLICIES[name]()


def escalated(prev, new):
    return prev is None or level_rank(new) > level_rank(prev)
