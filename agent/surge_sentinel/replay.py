"""Replay a past storm hour by hour for one household.

The agent sees only what was known at each hour (world.world). Ground truth is
independent of the agent and of the surge model: the observed flood arrival is
the first hour the tide gauges' observed water stands 6 inches deep at the
address (world.observed_arrival_h).
"""
from sentinel.config import LLM_RECHECK_H

from .policy import LLMPolicy, RulePolicy
from .signals import assess, level_rank
from .world import load_storm


def replay_subscriber(key, lat, lon, policy=None, hours=None, start_hour=0, **knobs):
    policy = policy or RulePolicy()
    info = load_storm(key)[0]
    hours = hours or info["hours"]
    history, alerts = [], []
    first = {lvl: None for lvl in ("watch", "warn", "evacuate")}
    current, last_rule, last_call = "none", None, -99

    for h in range(start_hour, hours + 1):
        s = assess(key, h, lat, lon, **knobs)
        # The LLM is asked when the rule triage changes (and, with SENTINEL_LLM_RECHECK_H, every N hours
        # while something is going on); rules decide every hour.
        busy = s["rule_level"] != "none" or current != "none"
        if (not isinstance(policy, LLMPolicy) or s["rule_level"] != last_rule
                or (LLM_RECHECK_H and busy and h - last_call >= LLM_RECHECK_H)):
            d = policy.decide(s, history)
            last_call = h
        else:
            d = None
        last_rule = s["rule_level"]
        if d and d["level"] != current:
            alert = {"hour": h, "as_of": s["as_of"], "level": d["level"], "previous": current,
                     "message": d["message"], "reasons": d.get("reasons", []), "eta_h": s["eta_h"],
                     "forecast_depth_ft": s["forecast_peak_depth_ft"], "high_depth_ft": s["high_peak_depth_ft"],
                     "storm_km": s["storm"]["distance_km"] if s["storm"] else None}
            alerts.append(alert)
            history.append({k: alert[k] for k in ("as_of", "level", "message")})
            current = d["level"]
            for lvl in first:
                if first[lvl] is None and level_rank(current) >= level_rank(lvl):
                    first[lvl] = h

    return {"first_hour": first, "alerts": alerts, "policy": policy.name,
            "llm_calls": getattr(policy, "calls", 0), "llm_tokens": getattr(policy, "tokens", 0)}
