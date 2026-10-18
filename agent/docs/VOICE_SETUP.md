# Phone calls over Photon Voice

Shared iMessage lines cannot take or place calls. Voice needs a Photon number that is a dedicated iMessage line,
an SMS number or a WhatsApp number. The agent itself runs on ElevenLabs, connected to Photon over SIP.

1. **Photon number.** Provision a dedicated line in the Photon beta dashboard (billable; check the plan first).
2. **Voice profile.** Voice -> Profiles -> Set up default -> SIP -> Create profile. Assign it to the number (Voice -> Lines).
3. **Inbound.** In the profile's Inbound calls: destination `sips:sip.rtc.elevenlabs.io:5061`, transport TLS, media encryption Auto.
4. **Outbound.** In the profile's Outbound calls: SHA-256 -> Enable outbound. Save the username/password (shown once).
5. **ElevenLabs.** Create and publish an agent. Phone Numbers -> Import number -> From SIP Trunk with the Photon number:
   inbound encryption Allowed; outbound address `sip.photon.codes:5061`, TLS, encryption Allowed, the Photon outbound
   username and password. Assign the agent to the number.
6. **Agent prompt.** Each call carries two dynamic variables: `{{fire_briefing}}` (level, distance, direction, ETA,
   wind, why, departure window, route) and `{{caller_phone}}`. Put the briefing in the agent's system prompt, e.g.:

   > You are FireTrace, calling someone whose home is threatened by a wildfire. Facts, as of the start of the call:
   > {{fire_briefing}}
   > If the level is EVACUATE, your job is to get them to leave now: say so first, keep answers to one or two
   > sentences, and repeat the route when asked which way to go. Explain why using only the facts above or from the
   > fire_status tool; never invent distances, times or roads. Official county orders take precedence. If they are
   > trapped or in danger, tell them to hang up and call 911.

   In Security, allow the **first message** override (the app sets the opening line per call).
7. **Live updates.** Give the agent a webhook tool `fire_status`: `POST https://<public url>/photon/voice` (public
   URL = tunnel to `python -m sentinel web --port 8080`, e.g. `ngrok http 8080`), header
   `X-Photon-Secret: <PHOTON_WEBHOOK_SECRET>`, JSON body `{"from": "{{caller_phone}}", "transcript": "<what the caller
   said>"}`. The reply is `{"reply": "...", "briefing": "..."}`: `reply` answers the question (route, when to leave,
   "I've left"), `briefing` is the latest situation. Tell the agent to call it when asked for an update or a route.
8. **agent/.env.** `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID` (the imported number's id),
   `PHOTON_WEBHOOK_SECRET`.
9. **Test call.** `python -m sentinel call --phone 5146646295`, or "Call my phone" on http://127.0.0.1:8080/test.
   Calling in works too: dial the Photon number.

## Calls on alerts

The alert loop phones a subscriber when their text sequence reaches a level in `SENTINEL_CALL_ON` (default
`evacuate`; `warn,evacuate` calls at both). Calls are off unless `SENTINEL_CALLS=1`. Each level calls at most once;
reminders stay texts. The Eaton replay demo turns calls on with `--call`:

    python -m sentinel demo --phone +15146646295 --send --call --serve 8080

The house sits where a destroyed Eaton home stood; as the replayed fire closes in you get the WARNING text, then the
EVACUATE text and a call. Questions during the call hit the same server, so answers follow the replay as it runs.

## Hosted on Chalk (the map's alert demo)

The `firetrace-web` sandbox (`deploy/web_sandbox.py`) runs the map's alert demo. With the keys below it phones
`DEMO_PHONE` when the replay reaches EVACUATE, and the alert card on the map shows the call and every question the
agent brings back.

1. In the Chalk environment, add the secrets `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`,
   `ELEVENLABS_PHONE_NUMBER_ID`, `PHOTON_WEBHOOK_SECRET` and `DEMO_PHONE` (the phone the demo rings).
2. Put the same names in `agent/.env` (any value works locally; the deploy script only checks that
   `ELEVENLABS_API_KEY` exists to decide whether to pass them), then `python deploy/web_sandbox.py`.
3. The public URL contains the sandbox id, so it changes on every deploy. The deploy script prints it and
   repoints the agent's `fire_status` webhook tool at `<url>/photon/voice` (the tool must exist; header
   `X-Photon-Secret`). No tunnel needed.
4. Open the map, pick Eaton 2025, press Alert demo, play. The phone rings at the EVACUATE alert (once per run;
   replays within a minute don't ring again).
