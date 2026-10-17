// Excerpts from agent/ — shown verbatim (trimmed) on the surge landing page.

export const LEARN = `def failures(rows, outs):
    for r, o in zip(rows, outs):
        ev = o["first_hour"]["evacuate"]
        if r["category"] == "flooded":
            lead = None if ev is None else r["observed_arrival_h"] - ev
            if lead is None or lead < LEAD_TARGET_H:
                cases.append({"truth": f"flooded {r['peak_depth_ft']} ft deep ...",
                              "error": "evacuate never sent" if ev is None
                                       else f"evacuate only {lead:.1f} h ahead", ...})
        elif r["category"] in ("spared", "high") and ev is not None:
            cases.append({"truth": "stayed dry", "error": f"told to evacuate at hour {ev}", ...})


base_train = run(train, lessons=False)
text, used = distil(failures(train, base_train))   # the LLM writes its own lessons
LESSONS.write_text(text + "\\n")
base_held = run(held, lessons=False)                # scored on storms it never saw
taught_held = run(held, lessons=True)`;

export const CHALK_EVAL = `@chalkcompute.function(**FN)
def surge_rules(storm: str, lat: float, lon: float) -> str:
    return _replay("rules", storm, lat, lon)


@chalkcompute.function(image=IMAGE)
def no_needless_evacuation(output: str, category: str):
    from surge_metrics import no_needless_evacuation as f
    return _score(f(output, category))


dataset = chalkcompute.DatasetClient().upload("surgetrace-replay-n60", rows)
ev = chalkcompute.Evaluation.create(f"surge-{policy}", dataset=dataset,
                                    task=TASKS[policy], scorers=SCORERS,
                                    suite_id=suite.id)
run = ev.run(metadata={"git_sha": sha}).wait()`;

export const GATEWAY = `class LLMPolicy:
    def __init__(self, model=MODEL, client=None, lessons=True):
        self.client = client or gateway_client()     # Chalk Model Gateway
        self.system = system_prompt() if lessons else SYSTEM

    def decide(self, s, history):
        prompt = json.dumps({"signals": _compact(s),
                             "alerts_already_sent": history[-4:]})
        resp = self.client.chat.completions.create(
            model=self.model, max_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": self.system},
                      {"role": "user", "content": prompt}])`;
