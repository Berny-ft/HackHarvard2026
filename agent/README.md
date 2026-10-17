# FireTrace Sentinel

An always-on wildfire agent for LA + Ventura counties. It watches the same live
feeds as the FireTrace map (satellite hot spots, NIFC incidents and perimeters,
NWS Red Flag Warnings, hourly fire weather), decides who is in danger, and
**pushes** alerts: signed webhooks to people's personal agents, SMS to people.
Personal agents connect over **MCP** to pull detail and tailor the advice with
what they know about their person. The sentinel never stores that context.

```
 live feeds ──▶ Snapshot ──▶ signals (distance, downwind, ETA, wind, fuel, Red Flag, burn prob)
 (modeling/      │                 │
  out/live)      │                 ▼
                 │        policy: rules | radius | LLM (Chalk Model Gateway)
                 │                 │
                 │                 ├─▶ webhook (HMAC-signed) ─▶ personal agent
                 │                 └─▶ SMS (Twilio)
                 └──────▶ MCP server: get_threat · regional_overview · subscribe · recent_alerts · replay_threat
```

## Run it

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                      # Chalk gateway key for the LLM policy (optional)

.venv/bin/python -m sentinel threat 34.18 -118.13          # live threat at a point
.venv/bin/python -m sentinel scan                          # most threatened areas now
.venv/bin/python -m sentinel replay eaton_2025 34.18 -118.13   # what it would have sent
.venv/bin/python -m sentinel webhook-sink --port 9000 &    # stand-in personal agent
.venv/bin/python -m sentinel subscribe 34.18 -118.13 --webhook http://127.0.0.1:9000/hook
.venv/bin/python -m sentinel run --once                    # one cycle (refreshes feeds)
.venv/bin/python -m sentinel mcp                           # MCP over stdio
.venv/bin/python -m sentinel mcp --http 8765               # MCP over HTTP at /mcp
```

Claude Desktop / Claude Code MCP config:

```json
{"mcpServers": {"firetrace": {"command": "/abs/path/agent/.venv/bin/python",
                              "args": ["-m", "sentinel", "mcp"], "cwd": "/abs/path/agent"}}}
```

## How Chalk is used

| Chalk piece | What it does here |
|---|---|
| **Model Gateway** | `LLMPolicy` and the alert-quality judge call models through the gateway's OpenAI-compatible router (`sentinel/policy.py`) |
| **Evaluations** | `evals/chalk_eval.py`: one dataset, one task per policy, deterministic scorers + an LLM judge, grouped in the `firetrace-sentinel` suite |
| **Sandboxes** | `deploy/sandbox.py` runs the loop and the MCP endpoint 24/7, with state on a Chalk Volume and `RestartPolicy.ALWAYS` |

## Evaluation: replaying Eaton and Palisades (Jan 2025)

**Point in time.** At hour *h* of a replay, the agent sees only what was known
by then: the reported ignition, the satellite detections up to *h* (VIIRS +
MODIS), the PDS Red Flag Warning, and hourly weather standing in for the
forecast (`sentinel/snapshot.py`).

**Ground truth comes from neither the agent nor the spread model.** For each
location, the fire's arrival time is the first satellite detection within 750 m.
That covers 99.8–100% of destroyed homes.

Subscribers (`evals/dataset.py`, 100 per group per fire, seed 7):
- **destroyed**: CAL FIRE DINS structures recorded Destroyed (>50%). These people needed a warning.
- **undamaged**: DINS No Damage structures inside the fire area.
- **outside**: land points 2–20 km beyond the final perimeter, where the fire never arrived.

Metrics (`evals/metrics.py`):
- **warned / evacuated in time**: share of destroyed homes that got warn / evacuate before the observed arrival.
- **lead time**: observed arrival hour minus alert hour.
- **over-evacuation**: an evacuate sent to an outside point.
- **false warning**: a warn or evacuate sent to an outside point 8 km or more from the perimeter.

Results (`python -m evals.run_local --policy …`), both fires, 600 subscribers:

| Policy | Warned in time | Evacuated in time | Median warn lead | Median evac lead | Over-evac | False warn | Alerts/sub |
|---|---|---|---|---|---|---|---|
| radius (wind-blind rings, trusts every report) | 100% | 92.5% | 8.7 h | 4.4 h | 4% | 6% | 1.5 |
| rules (wind ETA + rings, confirmation cap) | 100% | 82.5% | 8.7 h | 4.4 h | 4% | 6% | 2.7 |
| llm (Chalk gateway) | *run with a gateway key* | | | | | | |

What the evals showed. These are honest results, not tuned to look good:
1. **The wind-ETA model adds little over plain distance on these two fires.** Spread
   parameters fitted on Eaton only (`evals/calibrate.py`) and scored on held-out
   Palisades end up in a regime where the distance ring decides most evacuations
   (head spread ~1.2 km/h at 40 mph effective wind, which matches observed
   Eaton growth). Satellite detections arrive hours apart, which limits what a
   front-tracking ETA can do. The Eaton fit picks a 2 km evacuate ring, which
   scores worse on Palisades (72% vs 76%), so the default is the safer 3 km.
2. **Giving full credit for "unseen travel" (treating every old detection as a
   live front) overpredicts badly.** On Eaton it pushed over-evacuation to 20% or more, so
   that credit is fitted and lands at 0.
3. **Trusting unconfirmed reports wins replays and fails live.** Live NIFC lists
   ~48 "active" incidents in the box, most of them tiny. Treating each as an
   evacuate-grade fire would spam everyone, so the rule policy caps an
   unconfirmed report at *warn*. That costs 10 points of on-time evacuations,
   because the first satellite confirmation came 1.8–2.7 h after ignition.
   This is the judgment call the LLM policy is meant to make better, for
   example trusting a fresh report under a PDS Red Flag with 60 mph gusts.

Caveats: arrival times are limited to satellite overpasses, so lead times
are approximate. Hourly reanalysis weather stands in for a forecast, which is
optimistic. Two fires is a small sample.

## Files

- `sentinel/snapshot.py`: live and point-in-time replay views of the world
- `sentinel/signals.py`: physics signals and the rule triage
- `sentinel/policy.py`: rules / radius / LLM policies, message templates
- `sentinel/replay.py`: hour-by-hour replay and observed arrival
- `sentinel/live.py`: the sentinel loop and region scan
- `sentinel/notify.py`, `sentinel/sink.py`: signed webhooks, SMS, test receiver
- `sentinel/store.py`: subscribers and alert log (JSON under `state/`)
- `sentinel/mcp_server.py`: MCP tools and the `household_plan` prompt
- `evals/`: dataset, metrics, local runner, calibration, Chalk Evaluation suite
- `evals/export_web.py`: writes `modeling/out/sentinel/` (replay results and the live scan) for the web app
- `deploy/sandbox.py`: Chalk sandbox deployment
