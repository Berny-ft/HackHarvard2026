"""Replay a past incident hour by hour for one subscriber location.

The agent only sees what was known at each hour (see snapshot.replay_snapshot).
Ground truth is independent of the agent and of the spread model: the observed
arrival at a location is the first satellite fire detection within 750 m.
"""
from functools import lru_cache

import numpy as np

from .config import LLM_RECHECK_H
from .geo import burn_probability, offsets_km
from .policy import LLMPolicy, RulePolicy
from .signals import assess, level_rank
from .snapshot import load_incident, replay_snapshot

ARRIVAL_RADIUS_KM = 0.75


@lru_cache(maxsize=256)
def _snap(key, hour):
    return replay_snapshot(key, hour)


def observed_arrival_h(key, lat, lon, radius_km=ARRIVAL_RADIUS_KM):
    _, _, xy, hrs = load_incident(key)
    dx, dy = offsets_km(lat, lon, xy[:, 1], xy[:, 0])
    hit = np.hypot(dx, dy) <= radius_km
    return float(hrs[hit].min()) if hit.any() else None


def replay_subscriber(key, lat, lon, policy=None, hours=None, burn_prob="lookup", **physics):
    policy = policy or RulePolicy()
    inc, ts, _, _ = load_incident(key)
    hours = hours or inc["hours"]
    bp = burn_probability(lat, lon) if burn_prob == "lookup" else burn_prob
    history, alerts = [], []
    first = {lvl: None for lvl in ("watch", "warn", "evacuate")}
    current, last_rule, last_call = "none", None, -99

    for h in range(hours + 1):
        s = assess(_snap(key, h), lat, lon, with_burn_prob=False, **physics)
        s["burn_probability_10y"] = bp

        rule_changed = s["rule_level"] != last_rule
        busy = s["rule_level"] != "none" or current != "none"
        if not isinstance(policy, LLMPolicy) or rule_changed or (LLM_RECHECK_H and busy and h - last_call >= LLM_RECHECK_H):
            d = policy.decide(s, history)
            last_call = h
        else:
            d = None
        last_rule = s["rule_level"]

        if d and d["level"] != current:
            alert = {"hour": h, "as_of": s["as_of"], "level": d["level"], "previous": current,
                     "message": d["message"], "reasons": d.get("reasons", []),
                     "eta_h": s["eta_h"], "nearest_fire_km": s["nearest_fire_km"], "downwind": s["downwind"]}
            alerts.append(alert)
            history.append({k_: alert[k_] for k_ in ("as_of", "level", "message")})
            current = d["level"]
            for lvl in first:
                if first[lvl] is None and level_rank(current) >= level_rank(lvl):
                    first[lvl] = h

    return {"first_hour": first, "alerts": alerts, "policy": policy.name,
            "llm_calls": getattr(policy, "calls", 0), "llm_tokens": getattr(policy, "tokens", 0)}
