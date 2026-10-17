"""Learn from the train storms: replay them with the LLM policy, collect what it
got wrong, and have the LLM distil that into short lessons that go into its
instructions (out/surge/lessons.md). Then score the held-out storms with and
without the lessons, so the lessons are judged on storms they never saw.

Runs locally or in a Chalk sandbox (deploy/surge_learn.py); all model calls go
through the Chalk Model Gateway.

  python -m evals.surge.learn --limit 4
"""
import argparse
import json

from evals.surge.dataset import OUT, TRAIN, load_rows
from evals.surge.metrics import LEAD_TARGET_H, summarize
from evals.surge.run_local import limit_rows
from sentinel.config import MAX_TOKENS, MODEL
from sentinel.policy import gateway_client
from surge_sentinel.policy import LESSONS, LLMPolicy
from surge_sentinel.replay import replay_subscriber

TEACHER = """You review the decisions of a storm-surge warning agent on past hurricanes. You get cases where it
was wrong: households that flooded but were told to leave late or never, and households that stayed dry but were told
to evacuate. Each case lists the agent's alerts (hour, level, the forecast it saw) and what really happened.

Write at most 8 lessons the agent should follow next time. Each lesson is one sentence, general (no storm or place
names), and refers only to signals the agent sees: spill level and ground height, expected and high-scenario depth,
hours until flooding, storm category, distance, closest approach, advisory age, water observed now. Prefer lessons
that fix the most cases without creating the opposite error. Reply with the lessons as a markdown list, nothing else."""


def failures(rows, outs):
    cases = []
    for r, o in zip(rows, outs):
        ev = o["first_hour"]["evacuate"]
        if r["category"] == "flooded":
            lead = None if ev is None else r["observed_arrival_h"] - ev
            if lead is None or lead < LEAD_TARGET_H:
                cases.append({"truth": f"flooded {r['peak_depth_ft']} ft deep from hour {r['observed_arrival_h']}",
                              "error": "evacuate never sent" if ev is None else f"evacuate only {lead:.1f} h ahead",
                              "row": r, "alerts": o["alerts"]})
        elif r["category"] in ("spared", "high") and ev is not None:
            cases.append({"truth": "stayed dry", "error": f"told to evacuate at hour {ev}", "row": r, "alerts": o["alerts"]})
    return cases


def distil(cases, max_cases=40):
    slim = [{"truth": c["truth"], "error": c["error"],
             "address": {k: c["row"][k] for k in ("ground_ft", "spill_ft")},
             "alerts": [{k: a.get(k) for k in ("hour", "level", "forecast_depth_ft", "high_depth_ft", "eta_h", "storm_km")}
                        for a in c["alerts"]]} for c in cases[:max_cases]]
    resp = gateway_client().chat.completions.create(
        model=MODEL, max_tokens=max(MAX_TOKENS, 1200),
        messages=[{"role": "system", "content": TEACHER}, {"role": "user", "content": json.dumps(slim)}])
    return (resp.choices[0].message.content or "").strip(), resp.usage.total_tokens if resp.usage else 0


def run(rows, lessons):
    outs = []
    for r in rows:
        outs.append(replay_subscriber(r["storm"], r["lat"], r["lon"], policy=LLMPolicy(lessons=lessons)))
    return outs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=4, help="households per storm/category group")
    a = ap.parse_args()
    rows = limit_rows(load_rows(), a.limit)
    train = [r for r in rows if r["storm"] in TRAIN]
    held = [r for r in rows if r["storm"] not in TRAIN]

    if LESSONS.exists():
        LESSONS.unlink()
    base_train = run(train, lessons=False)
    cases = failures(train, base_train)
    print(f"train: {len(cases)} mistakes out of {len(train)} households")
    text, used = distil(cases) if cases else ("", 0)
    LESSONS.write_text(text + "\n")
    print(text)

    base_held = run(held, lessons=False)
    taught_held = run(held, lessons=True)
    tokens = used + sum(o["llm_tokens"] for o in base_train + base_held + taught_held)
    report = {"limit": a.limit, "model": MODEL, "lessons": text, "tokens": tokens,
              "train_before": summarize(train, base_train)["all"],
              "held_out_before": summarize(held, base_held)["all"],
              "held_out_after": summarize(held, taught_held)["all"]}
    (OUT / "surge_learn.json").write_text(json.dumps(report, indent=1))
    (OUT / f"results_surge_llm_learned_l{a.limit}.json").write_text(json.dumps(
        {"rows": held, "outputs": taught_held, "summary": summarize(held, taught_held)}, default=str))
    (OUT / f"results_surge_llm_l{a.limit}.json").write_text(json.dumps(
        {"rows": train + held, "outputs": base_train + base_held, "summary": summarize(train + held, base_train + base_held)},
        default=str))
    for k in ("train_before", "held_out_before", "held_out_after"):
        s = report[k]
        print(f"{k:<16} evac 12h+ {s['evacuate_12h_ahead']}  evac in time {s['evacuate_in_time']}  "
              f"needless {s['needless_evacuation_rate']}  false {s['false_warning_rate']}")
    print(f"{tokens:,} tokens")


if __name__ == "__main__":
    main()
