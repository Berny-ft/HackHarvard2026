// Excerpts from agent/ — shown verbatim (trimmed) on the landing page.

export const GATEWAY = `def gateway_client():
    """OpenAI-compatible client pointed at the Chalk Model Gateway."""
    from openai import OpenAI

    return OpenAI(base_url=CHALK_ROUTER_URL, api_key=CHALK_ROUTER_API_KEY,
                  default_headers={"X-Chalk-Env-Id": CHALK_ROUTER_ENV_ID})


class LLMPolicy:
    def decide(self, s, history):
        if s["rule_level"] == "none" and not history:
            return RulePolicy().decide(s, history)      # most hours cost nothing
        prompt = json.dumps({"signals": _compact(s),
                             "alerts_already_sent": history[-4:]})
        resp = self.client.chat.completions.create(
            model=self.model, max_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": prompt}])`;

export const EVALS = `@chalkcompute.function(**FN)
def sentinel_rules(incident: str, lat: float, lon: float,
                   burn_probability_10y: float | None) -> str:
    return _replay("rules", incident, lat, lon, burn_probability_10y)


@chalkcompute.function(image=IMAGE)
def evacuate_in_time(output: str, category: str,
                     observed_arrival_h: float | None):
    from evals_metrics import timely
    return _score(timely(output, category, observed_arrival_h, "evacuate"))


dataset = chalkcompute.DatasetClient().upload("firetrace-replay-n100", rows)
ev = chalkcompute.Evaluation.create(f"sentinel-{policy}", dataset=dataset,
                                    task=TASKS[policy], scorers=SCORERS,
                                    suite_id=suite.id)
run = ev.run(metadata={"git_sha": sha}).wait()`;

export const SANDBOX = `Volume("firetrace-state")  # created if missing
sb = Sandbox(
    image=image, name="firetrace-sentinel", cpu="1", memory="4Gi", port=8765,
    secrets=[Secret.from_chalk_env(k) for k in
             ("CHALK_ROUTER_API_KEY", "FIRMS_MAP_KEY", "SENTINEL_WEBHOOK_SECRET")],
    volumes=[("firetrace-state", "/state")],
    restart_policy=RestartPolicy.ALWAYS,
    entrypoint=["sh", "-c", "python -m sentinel mcp --http 8765 & "
                            "python -m sentinel run --every 15 --policy llm"],
).run()`;
