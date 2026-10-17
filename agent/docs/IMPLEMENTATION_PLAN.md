# FireTrace Sentinel: Texting, Replies, Routes and Departure Windows

**Implementation plan and handoff.** Written so another AI coding agent (or a new developer) can rebuild or extend this without the original conversation. Everything here reflects the code as it stands in `agent/` after the latest build. Where something is unverified or unfinished, it says so.

---

## 0. How to use this document

1. Read sections 1 to 3 first. They explain what is being built and the decisions that are already locked. Do not reopen them without asking.
2. Section 5 is the module-by-module spec. Section 8 is the build order, with a "done when" check for each step.
3. Section 6 is the Photon contract. Photon's SDK details were not available when this was written, so that part is a stub with a list of things to confirm.
4. Section 7 is the test plan. Run the tests before and after every change.

Base commit: `d49b7d5` ("Redesign web app and add landing page with replay evaluation"). The changes described here are uncommitted in the working tree.

---

## 1. Product in one paragraph

FireTrace Sentinel watches live wildfire feeds for Los Angeles and Ventura counties (satellite hot spots, NIFC incidents and perimeters, NWS Red Flag Warnings, hourly fire weather). For each subscribed location, it decides a threat level: `none`, `watch`, `warn`, or `evacuate`. It then talks to the person by text, using Photon as the messaging channel. People can text back to ask for their live route, confirm they have left, or ask for their departure window. Calls are inbound only: a person can call the number to ask questions, and the agent answers. **The agent never places a call.**

---

## 2. Locked decisions

These come from the product conversation. Treat them as fixed unless the product owner changes them.

| # | Decision | Consequence in code |
|---|---|---|
| D1 | Messaging channel is **Photon**. No Twilio. | `channels/photon.py` is the production adapter. Twilio code is removed from `notify.py`. Twilio config keys remain in `config.py` but are unused. |
| D2 | **No outbound calls.** The person calls the agent to ask questions. | No call-placing code. Escalation is text only. |
| D3 | Texts go to people only with **consent**. | `subscribers.consent` must be true before any text. STOP clears it. |
| D4 | Text sequence: **warn** once (with planning window), **evacuate** once ("leave now" plus how to get the route), then **reminders** while evacuate holds. | `escalation.next_action()`. |
| D5 | **NOW** = request the current best route (fire data plus live traffic). It does **not** mean the person has left. | `conversation.respond()` returns the cached route and changes no departure state. |
| D6 | **LEFT** = the only text that records departure. It stops reminders. | Sets `left_at`. Voice "I've left" maps to the same intent. |
| D7 | Departure windows are **informative only**. Shown as "earliest reasonable" to "latest safe" departure, with a confidence level and a "not an order" note. Nothing is enforced. | `windows.departure_window()`. |
| D8 | Official orders from the county always take precedence. The app never impersonates an official source, and the text says so. | Message wording in `messages.py`. |
| D9 | Safety-relevant replies (route, window, status, departure) are **deterministic**, read from stored state. An LLM is not used for them. | `conversation.py` has no LLM call. Free-form questions get the status line and "call 911 in an emergency." |
| D10 | Escalation does not run forever. It stops when LEFT is recorded, when the level drops, or at the reminder cap. | `MAX_REMINDERS`. |

Known open product question: if someone never replies, repeat texts are the only mitigation, since there are no outbound calls. This is accepted for now.

---

## 3. Architecture

```
                         every SENTINEL_CYCLE (e.g. 5 min)
 modeling/out/live/*.json ──► live_snapshot() ──► assess(snap, lat, lon) per subscriber
   (refreshed by                                        │
    `python -m wildfire live`)                          ▼
                                             policy.decide()  →  level
                                                        │
              ┌─────────────────────────────────────────┼──────────────────────────────────┐
              ▼                                         ▼                                  ▼
   level warn/evacuate?                    level changed? → webhook (signed)     escalation.next_action()
   routing.best_route()  (Google Routes)                                              │
   windows.departure_window()                                                         ▼
              │                                                         channel.send_text(phone, text)
              └──────────────► store.update(subscriber)  ◄───────────────────────────┘
                                         ▲
  inbound text/voice (Photon) ──► web.py ──► conversation.respond() ──► reply via channel
  person opens route link      ──► web.py GET /route/{token}
  operator                     ──► web.py GET /dashboard.json
```

Components are separated so that the channel, the routing provider, and the policy can each be replaced without touching the others.

---

## 4. Repository layout

Existing modules that were **not** changed (reuse them, do not rewrite):
- `agent/sentinel/snapshot.py`: builds the `Snapshot` (fires, weather, outlook, red flag) from live files or replay data.
- `agent/sentinel/signals.py`: `assess()` computes distance, bearing, ETA, `rule_level`. Needs numpy.
- `agent/sentinel/policy.py`: `RulePolicy`, `RadiusPolicy`, `LLMPolicy`. `template_message()` still produces the webhook alert text.
- `agent/sentinel/replay.py`, `agent/sentinel/sink.py`, `agent/sentinel/geo.py`, `agent/evals/*`, `agent/deploy/sandbox.py`.

Changed or new:

| Path | Status | Purpose |
|---|---|---|
| `agent/sentinel/config.py` | changed (appended) | New env-driven settings (section 5.1) |
| `agent/sentinel/store.py` | rewritten | Subscribers, consent, escalation state, alert log (section 5.3) |
| `agent/sentinel/escalation.py` | new | Text sequence state machine (5.4) |
| `agent/sentinel/windows.py` | new | Departure window math (5.5) |
| `agent/sentinel/routing.py` | new | Live-traffic routes, fire clearance filter (5.6) |
| `agent/sentinel/messages.py` | new | All texts to people (5.7) |
| `agent/sentinel/conversation.py` | new | Intent parsing and replies, shared by text and voice (5.8) |
| `agent/sentinel/channels/__init__.py` | new | Channel interface, console channel, `get_channel()` (5.2) |
| `agent/sentinel/channels/photon.py` | new, stub | Photon adapter (section 6) |
| `agent/sentinel/live.py` | rewritten | The cycle (5.9). `refresh_feeds`, `scan_region` kept as they were |
| `agent/sentinel/notify.py` | rewritten | Signed webhooks only; Twilio removed (5.10) |
| `agent/sentinel/web.py` | new | Inbound text and voice, route page, dashboard data (5.11) |
| `agent/sentinel/__main__.py` | changed | New `web` command, `subscribe --consent` (5.12) |
| `agent/sentinel/mcp_server.py` | changed | `subscribe` tool gains `consent` (5.13) |
| `agent/requirements.txt` | changed | Adds `starlette`, `uvicorn`, `tzdata` |
| `agent/.env.example` | changed | Documents the new keys |
| `agent/tests/test_sentinel.py`, `agent/tests/test_cycle.py` | new | 33 tests (section 7) |
| `agent/docs/IMPLEMENTATION_PLAN.md` | new | This document |

---

## 5. Module specifications

### 5.1 `config.py`

All values come from environment variables, with defaults. `load_env()` reads `agent/.env` first, without overriding variables already set.

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `PHOTON_API_KEY` | none | channels | Enables the Photon channel when set together with `PHOTON_NUMBER` |
| `PHOTON_NUMBER` | none | channels, messages | The number people text and call. Appears in the contact line |
| `PHOTON_WEBHOOK_SECRET` | none | web | HMAC key for inbound requests. If unset, inbound is accepted only while the console channel is active |
| `SENTINEL_REMINDER_MIN` | `10` | escalation | Minutes between evacuate reminders |
| `SENTINEL_MAX_REMINDERS` | `6` | escalation, dashboard | Reminder cap per evacuation |
| `GOOGLE_MAPS_API_KEY` | none | routing | Enables live-traffic routes. Without it, no route is cached |
| `SENTINEL_ROUTE_MAX_AGE_MIN` | `5` | live | A route older than this is recomputed |
| `SENTINEL_FIRE_CLEARANCE_KM` | `2.0` | routing | Minimum distance from a route to any active fire point |
| `SENTINEL_PUBLIC_URL` | `http://localhost:8080` | messages | Base URL for route links in texts |
| `SENTINEL_TZ` | `America/Los_Angeles` | messages | Timezone for clock times shown to people |
| `SENTINEL_WEB_HOST` | `127.0.0.1` | web | Bind address for `python -m sentinel web` |
| `SENTINEL_STATE` | `agent/state` | store | Folder for `subscribers.json` and `alerts.jsonl` |
| `SENTINEL_WEBHOOK_SECRET` | `dev-secret` | notify | Signs webhooks to personal agents. **Must be set in production** |

Existing settings still in use: `THRESHOLDS`, `ACTIVE_WINDOW_H` (12 h), `LEVELS` (`none, watch, warn, evacuate`), `REPO`, `LIVE`, the Chalk settings, `SENTINEL_EVAC_KM` (3 km). `TWILIO_*` remain defined but are unused and can be deleted.

### 5.2 `channels/`

**Interface.** Anything that sends texts needs:
- `name` (string, for logs and tests)
- `send_text(to: str, body: str) -> dict` returning at least `{"ok": bool, "status": str}`. It may raise. Callers catch exceptions.

**`channels/__init__.py`**
- `ConsoleChannel`: records sends in `self.sent` and prints `[text -> <to>] <body>`. Name `"console"`.
- `get_channel()`: returns a singleton. `PhotonChannel` when `PHOTON_API_KEY` and `PHOTON_NUMBER` are set, otherwise `ConsoleChannel`.
- `set_channel(ch)`: overrides the singleton. Used by tests.

**`channels/photon.py`** (stub). `PhotonChannel(api_key, number)` with `send_text` raising `NotImplementedError`. A deployment with Photon keys set but the adapter unfinished fails loudly on the first send. See section 6.

### 5.3 `store.py`

Storage: `SUBS = STATE/subscribers.json` (a dict keyed by subscriber id), `ALERTS = STATE/alerts.jsonl` (append-only). One in-process `threading.Lock`. Writes go to a `.tmp` file, then `replace()`. This is single-process storage. Replace it with a database before running more than one process (section 9).

**Subscriber record.** Every field, with its meaning:

| Field | Set by | Meaning |
|---|---|---|
| `id` | subscribe | 12-hex id |
| `token` | subscribe | 16-hex capability token for the route link `/route/{token}` |
| `lat`, `lon` | subscribe | Location. Only this is stored about the place |
| `label` | subscribe | Optional operator label. Never exposed by the dashboard |
| `webhook_url` | subscribe | Personal-agent endpoint (optional) |
| `phone` | subscribe | E.164 after normalization (optional) |
| `consent` | subscribe, STOP | `True` only if a phone and consent were given. STOP sets `False` |
| `min_level` | subscribe | Lowest level that triggers webhooks and texts. Default `watch` |
| `created` | subscribe | ISO timestamp |
| `last_level` | cycle | Level at the previous cycle. Drives webhook de-duplication |
| `stage` | cycle | `none`, `warned`, or `evacuated` |
| `stage_at` | cycle | When the last text in the current stage went out |
| `reminders` | cycle | Reminders sent in this evacuation |
| `left_at` | LEFT reply | When the person said they had left. Cleared when the threat clears |
| `situation` | cycle | Snapshot of the last assessment: `level`, `as_of`, `nearest_fire_km`, `nearest_fire_name`, `nearest_fire_confirmed`, `fire_bearing_deg`, `eta_h`, `downwind` |
| `route` | cycle | Last route: `summary`, `duration_min`, `distance_km`, `origin` [lat, lon], `dest` [lat, lon], `dest_label`, `computed_at` |
| `route_at` | cycle | When `route` was computed |
| `window` | cycle | Last departure window (section 5.5) |
| `unsubscribed_at` | STOP | Audit timestamp |

**Functions:** `normalize_phone(p)` (10 digits becomes `+1` plus digits; 11 digits starting with 1 becomes `+` plus digits; anything with a leading `+` is kept; other input gets a `+` prefix), `subscribe(lat, lon, label, webhook_url, phone, min_level, consent)` (requires a webhook or a phone), `get(id)`, `find_by_phone(phone)`, `find_by_token(token)`, `update(id, **fields)`, `unsubscribe(id)`, `subscribers()`, `log_alert(dict)`, `recent_alerts(sub_id, limit)`.

### 5.4 `escalation.py`

Pure function, no I/O.

```
next_action(sub, level, now) -> (text_kind | None, next_stage)
```

| Current stage | Level | Condition | Result |
|---|---|---|---|
| any | `none` | | `(None, "none")` |
| any | any | no consent or no phone, or `left_at` set | `(None, current stage)` |
| `none` | `warn` | | `("warn", "warned")` |
| `none` or `warned` | `evacuate` | | `("evacuate", "evacuated")` |
| `evacuated` | `evacuate` | minutes since `stage_at` ≥ `REMINDER_EVERY_MIN` and `reminders` < `MAX_REMINDERS` | `("reminder", "evacuated")` |
| anything else | | | `(None, current stage)` |

`minutes_since(iso, now)` returns infinity when `iso` is empty, so the first reminder is never blocked by a missing timestamp.

### 5.5 `windows.py`

```
departure_window(now, eta_h, travel_min, confirmed) -> dict | None
```

- Returns `None` when `eta_h` is `None`.
- `travel = travel_min` if given, otherwise `DEFAULT_TRAVEL_MIN = 45`.
- `arrival = now + eta_h hours`.
- `latest = arrival − (travel × CONGESTION_FACTOR + BUFFER_MIN)`, with `CONGESTION_FACTOR = 1.5` and `BUFFER_MIN = 20`.
- `status = "leave_now"` if `latest ≤ now`. In that case `earliest` and `latest` are both `now`. Otherwise `status = "window"`.
- `confidence`: `high` if confirmed and `eta_h ≤ 3`. `medium` if confirmed. `low` otherwise.
- Returns `{status, earliest, latest, confidence, travel_min}`, with datetimes as ISO strings.

The ETA comes from `signals.assess()`, which is an hours-scale estimate. Do not show windows beyond a few hours as precise. The factor and buffer are placeholders to tune with the replay evaluation.

### 5.6 `routing.py`

Constants: `ROUTES_URL = https://routes.googleapis.com/directions/v2:computeRoutes`, `FIELDS = routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline,routes.description`, `AWAY_KM = 12.0`.

**Functions**

- `km_between(lat1, lon1, lat2, lon2)`: haversine, km.
- `offset(lat, lon, bearing_deg, km)`: point at a bearing and distance.
- `away_points(lat, lon, fire_bearing_deg, km=12)`: three points about 12 km away, on the side opposite the fire, at bearings `away−40`, `away`, `away+40`, where `away = (fire_bearing_deg + 180) % 360`. Each entry is `(lat, lon, label)`.
- `decode_polyline(encoded)`: standard Google encoded-polyline decoding, returning `(lat, lon)` pairs.
- `clearance_km(path, fire_pts)`: smallest distance from the sampled route to any fire point. Samples at most 80 points. Pre-filters fire points to a bounding box padded by 0.03° (about 3 km). Returns infinity if there are no fire points nearby.
- `fetch_routes(origin, dest)`: POST to the Routes API with body `travelMode: DRIVE`, `routingPreference: TRAFFIC_AWARE_OPTIMAL`, `computeAlternativeRoutes: true`. Headers: `X-Goog-Api-Key`, `X-Goog-FieldMask`. Returns the `routes` list.
- `best_route(lat, lon, fire_bearing_deg, fire_pts, now_iso, fetch=None) -> dict | None`:
  1. If no `fetch` is given and there is no key, return `None`.
  2. For each of the three away points, fetch routes. A failed fetch is skipped, not raised.
  3. For each route: decode the polyline. Skip if the polyline or duration is missing. Reject if `clearance_km < FIRE_CLEARANCE_KM` (2 km).
  4. Keep the accepted route with the shortest `duration_min`.
  5. Return `{summary, duration_min, distance_km, origin, dest, dest_label, computed_at}`, or `None`.

**Known limits.** The clearance check is a proxy. It does not model when the fire reaches each road. Destinations are generic points, not shelters. Road closures are not used. Google's terms on caching and redistributing route results should be checked before production use.

### 5.7 `messages.py`

Helpers: `compass(deg)`, `clock(iso) → "2:05 PM"` (in `SENTINEL_TZ`), `where(sit)`, `contact_line()`, `map_link(sub) → {PUBLIC_URL}/route/{token}`.

`where(sit)`:
- no distance: `Critical fire weather in your area`
- under 0.5 km: `<fire> is within 500 m of you`
- otherwise: `<fire> is <d> km to your <compass>`, with `<fire>` defaulting to "A fire".

`contact_line()` returns: `Text NOW for your route, LEFT once you're out or call <PHOTON_NUMBER>.` (the "or call" part only when the number is set).

**Texts sent by the sentinel** (exact templates):

- **warn**: `FireTrace WARNING: <where>.` then, if the window is `window`: `Planning window: leave between <earliest> and <latest>. Estimate only (<confidence> confidence), not an order.`; if `leave_now`: `There is no safe later window. Plan to leave now.` Then `Official orders from your county take precedence. We will message you again on any change.` Then the contact line.
- **evacuate**: `FireTrace EVACUATE: <where>. Leave now. <contact line>`
- **reminder**: `Reminder (<LEVEL>): <where>. <contact line>`

**Replies** (returned by `conversation.respond`):
- `route_reply`: with a route, `Route, live traffic as of <time>: <summary>, about <n> min (<km> km). Map: <link>. Check for closures and officers.` Without one: a fallback that says to head away from the fire and follow officers.
- `window_reply`: `No departure window...` when none; `There is no safe later window. Leave now.` for `leave_now`; otherwise the window with confidence and "not an order".
- `status_reply`: `No active wildfire threat near you right now...` at `none`, otherwise `Current level: <LEVEL>. <where>. <contact line>`.
- `left_reply`: `Got it. We'll stop reminders. Text NOW anytime for your route.`
- `stop_reply`: `You're unsubscribed from FireTrace texts. Text us again to sign up.`
- `help_reply`: lists NOW, LEFT, STATUS, WINDOW, STOP, and "Emergency: call 911."
- `freeform_reply`: the status line plus "For anything else, call 911 in an emergency."

### 5.8 `conversation.py`

```
respond(sub, text, now_iso) -> str
```

Intent parsing:
1. If the trimmed text is `?`, the intent is HELP.
2. Otherwise normalize: lowercase, remove apostrophes, replace other non-letters with spaces, collapse spaces.
3. Exact match against the sets below. Matching is on the whole message, not substrings, so "not leaving" falls through to FREEFORM.
4. The word `help` also means HELP.

| Intent | Accepted messages |
|---|---|
| NOW | now, route, directions, leaving now, im leaving now, i am leaving now, leaving, im leaving, i am leaving |
| LEFT | left, i left, ive left, i have left, im out, i am out, out |
| WINDOW | window, when, when should i leave, when do i leave, time |
| STATUS | status, level, update, info, whats happening, what is happening |
| STOP | stop, unsubscribe, cancel, quit, end, stopall |
| HELP | `?`, help |
| FREEFORM | anything else |

Behavior:
- **STOP**: `consent=False`, `unsubscribed_at=now`. Reply `stop_reply`.
- **HELP**: `help_reply`, no state change.
- **LEFT**: `left_at=now`. Reply `left_reply`.
- **NOW**: if the level is not `none`, reply `route_reply` (cached route, with its time). Otherwise reply `status_reply`. **No state change.**
- **WINDOW**: `window_reply`.
- **STATUS**: `status_reply`.
- **FREEFORM**: `freeform_reply`.

The same function serves SMS and voice. The voice path sends the transcript through `respond()` and speaks the reply.

### 5.9 `live.py`: the cycle

```
cycle(policy_name="rules", refresh=True, dry_run=False, channel=None) -> dict
```

1. Optionally run `refresh_feeds()` (runs `python -m wildfire live` in `modeling/`).
2. `snap = live_snapshot()`. `policy = get_policy(policy_name)`. `channel = channel or get_channel()`.
3. `fire_pts = active_fire_points(snap)`: fire points with age ≤ `ACTIVE_WINDOW_H`, as `(lat, lon)` tuples.
4. For each subscriber:
   a. `s = assess(snap, lat, lon)`. `history` = the last four alerts for this subscriber.
   b. `decision = policy.decide(s, history)` (called **once**; the LLM policy would otherwise be billed twice). `level = decision["level"]`.
   c. `upd = {"situation": _situation(s, level)}`.
   d. If level is `warn` or `evacuate`: if `route_at` is older than `ROUTE_MAX_AGE_MIN` (or missing), compute `best_route(...)` and store `route` and `route_at` (`route_at` is cleared if no route). Then `window = departure_window(now, eta_h, travel_min, confirmed)`, stored in `upd`.
      Otherwise, clear `route`, `route_at` and `window`.
   e. **Webhook.** If `level != last_level` and the higher of old and new ranks is at least `min_level`: build an alert dict, call `deliver(sub, alert)` (unless dry run), and log it.
   f. Set `upd["last_level"] = level`.
   g. **Texts.** `kind, next_stage = next_action(merged, level, now)` where `merged = sub + upd`.
      - If `kind` is set but the level is below `min_level`, cancel it.
      - If the level is `none` and the stage is not `none` (or `left_at` is set): reset to `stage=none`, `stage_at=None`, `reminders=0`, `left_at=None`. This re-arms the person for the next threat.
      - Otherwise, if no `kind` and the stage would change, set `stage`.
      - If `kind`: build the text (`warn`, `evacuate`, or `reminder`), send it through `channel` (or record a dry-run result), log it with `channel: "text"`, set `stage=next_stage` and `stage_at=now`, and increment `reminders` for a reminder.
   h. Unless dry run, `store.update(id, **upd)`.
5. Return `{started, feeds_ok, fire_points, subscribers, alerts, texts}`.

Invariants:
- A failed send is recorded in the log entry's `delivery` (`ok: false`). It does not stop the cycle.
- Dry run changes nothing: no store writes, no logs, no sends.
- A person without consent is still assessed and their route and window are still computed. Nothing is sent, and the stage does not advance.

`run_forever(every_min, **kw)` repeats `cycle` and keeps running after an exception.

### 5.10 `notify.py`

- `sign(body) = "sha256=" + HMAC-SHA256(SENTINEL_WEBHOOK_SECRET, body)`
- `send_webhook(url, alert)`: POST JSON with headers `X-Sentinel-Signature` and `X-Sentinel-Event: alert.<level>`.
- `deliver(sub, alert)`: webhook only. Returns `[]` if there is no URL. Texts are not sent here.

`sink.py` (a local test receiver) still imports `sign` from this module.

### 5.11 `web.py`

Starlette app. Run with `python -m sentinel web --port 8080`. `uvicorn` binds to `SENTINEL_WEB_HOST`.

**Inbound verification.** `X-Photon-Signature` must equal the hex HMAC-SHA256 of the raw request body using `PHOTON_WEBHOOK_SECRET`, compared in constant time. The header name is a placeholder until Photon's signing scheme is confirmed. If no secret is configured, unsigned requests are accepted only while the console channel is active.

| Method and path | Body | Response | Behavior |
|---|---|---|---|
| `POST /photon/inbound` | `{"from": "+1…", "text": "NOW"}` | `{"ok": true}` (502 if the send fails) | Looks up the sender by phone, runs `respond()`, sends the reply through the channel. Unknown numbers get "This number isn't signed up." |
| `POST /photon/voice` | `{"from": "+1…", "transcript": "…"}` | `{"reply": "…"}` | Same logic. The caller hears the reply |
| `GET /route/{token}` | | HTML | Mobile-width page: level, fire description, route summary and minutes, "Open in Google Maps" link (built from the stored origin and destination), planning window, contact line. Every value is HTML-escaped. 404 for an unknown token |
| `GET /dashboard.json` | | JSON | See below |

`/dashboard.json` fields: `subscribers`, `levels` (count per level), `stages` (count per stage), `left`, `evacuated_not_left_max_reminders`, `consented_for_text`, `feeds_updated`, `recent_alerts` (last 20: `as_of`, `level`, `channel`, `text_kind`, `kind`, `message`). It never includes phone numbers or labels.

### 5.12 `__main__.py` (CLI)

- `threat lat lon [--policy]`: one assessment, printed as JSON.
- `replay incident lat lon [--policy]`: replay of a past incident.
- `scan`: most threatened cells.
- `subscribe lat lon [--webhook] [--phone] [--label] [--min-level watch] [--consent]`.
- `run [--once] [--every 15] [--policy rules] [--no-refresh] [--dry-run]`.
- `mcp [--http 8765]`.
- `webhook-sink [--port 9000]`.
- `web [--port 8080]`.

For the live demo, use `run --every 5`.

### 5.13 `mcp_server.py`

The `subscribe` tool gains `consent: bool = False`. It is passed to `store.subscribe`. A personal agent may subscribe a phone only when it has consent. Other MCP tools are unchanged.

---

## 6. Photon integration contract

**Status.** Photon 2.0 is the required platform: the project must be built on Photon 2.0 and live on at least one Photon channel. Photon provides a phone number, several channels (iMessage, SMS, RCS, WhatsApp, Telegram, email, phone calls), and voice agents that answer and place calls. Pricing is unknown beyond a $100 credit for messaging-agent builders, reported by RuntimeWire on October 15, 2026 (conditions unknown). The hackathon guidelines page could not be read.

**What `channels/photon.py` must implement**

1. `send_text(to, body)`: send through Photon from `PHOTON_NUMBER`. Return `{"ok": bool, "status": str}`. Raise on transport errors.
2. Inbound text: Photon posts each message to `POST /photon/inbound`. Adapt its payload to `{"from", "text"}`, or change `web.py` to read Photon's real field names. Verify the signature using Photon's scheme, replacing the `X-Photon-Signature` placeholder.
3. Inbound voice: a call to `PHOTON_NUMBER` should be answered by an agent that sends the caller's speech to `POST /photon/voice` and speaks the `reply`. Confirm whether Photon exposes per-turn transcripts or only a full call webhook.
4. Channel choice: Photon may pick iMessage, SMS, or RCS automatically. The sentinel does not need its own fallback logic if Photon does this. Confirm.
5. Number provisioning and any registration needed to text US numbers from a Photon number.
6. Rate limits and quotas per message. A large fire could produce thousands of texts in minutes.
7. Concurrent inbound calls during a fire. Confirm capacity.

**Verify before the demo:** every item above, plus the workshop schedule. The blurb said the build workshop was Friday 4 to 5 PM, and today's date is Saturday, October 17, 2026.

---

## 7. Testing plan

### 7.1 Run

From `agent/`:

```
python -m unittest discover -s tests -v
```

Requirements: `numpy` for `test_cycle.py`, `starlette` and `httpx` for the web tests. Tests skip cleanly when a dependency is missing.

Both test modules set `SENTINEL_STATE` to a temporary folder **before** importing `sentinel`. This matters. Importing first reads the real state path, and the tests will write to it. This bug occurred during the build and is fixed. Keep the order.

### 7.2 Current results

**33 tests, all passing.**

| Group | Count | What it checks |
|---|---|---|
| `TestEscalation` | 6 | No threat means no text. Warn once. Evacuate upgrades from warn. Reminders respect the interval and the cap. LEFT stops everything. No consent means no text |
| `TestClassify` | 1 (14 cases) | Every intent, including "not leaving" falling through to FREEFORM |
| `TestWindows` | 3 | Latest-departure formula. Confidence levels. No ETA means no window |
| `TestMessages` | 4 | Evacuate text has the action and the next step. Warn text shows the window as an estimate. Route reply links to the token page. Fallback when there is no route |
| `TestRouting` | 6 | Polyline round trip. Haversine distance. Away points lie opposite the fire. A route through the fire is rejected even when fastest. No key returns None. A failed fetch is skipped |
| `TestStoreAndReplies` | 3 | Phone normalization and lookup. LEFT recorded, STOP clears consent. NOW returns the cached route and does **not** set `left_at` |
| `TestWeb` | 6 | NOW is texted back. Unknown number. Voice returns a reply. Route page 404 and HTML escaping. Dashboard contains no phone number or label. Bad signature returns 401 |
| `TestCycle` | 4 | Evacuate once, then no repeat, then one reminder, then LEFT stops reminders. Threat clearing re-arms the person. No consent means no text. Dry run writes nothing |

### 7.3 Real-feed dry run

```
python -c "from sentinel.live import cycle; r = cycle(refresh=False, dry_run=True); print(len(r['texts']))"
```

Run with a temporary `SENTINEL_STATE`. On the build date it reported 363 active fire points and no sends. Needs `shapely` and `rasterio`. `burn_probability` reads `modeling/out/burn_probability.tif`.

### 7.4 Not yet tested

- The Photon adapter (not implemented).
- The Google Routes call (only with a fake fetch).
- Real inbound messages from a phone.
- Concurrency under load.
- The route page on a phone browser.
- Replay of a historical fire through the new text sequence. The existing replay harness (`python -m sentinel replay eaton_2025 …`) does not yet use the escalation logic.

### 7.5 Drill plan (before any demo)

1. Five team phones, each subscribed with `--consent`, at real addresses.
2. Replace the fire snapshot with a fixture that places a fire 2 km from each phone.
3. Run `run --every 1` and confirm: one warn text (or evacuate), a route link that opens, NOW returns a route, LEFT stops reminders, and the dashboard counts match.
4. Confirm: STOP removes the phone from further texts, and the webhook still works for an agent subscription.
5. Confirm: a bad signature to `/photon/inbound` is rejected.

### 7.6 Acceptance criteria

- No text is sent without consent.
- No text is sent that says "leave now" to someone whose level is below evacuate.
- NOW never sets `left_at`. Only LEFT (or "I've left" by voice) does.
- A reminder is never sent sooner than `REMINDER_EVERY_MIN` after the previous text.
- Reminders stop at `MAX_REMINDERS`, at LEFT, and when the level drops to none.
- The dashboard never exposes a phone number or a label.
- Every route link is protected by an unguessable token. No route link is sent to anyone who did not subscribe.
- All 33 tests pass.

---

## 8. Build order for another agent

Each step lists its "done when" check. Do them in order; each depends on the one before.

1. **Read the existing code**: `snapshot.py`, `signals.py`, `policy.py`, `live.py` (original), `notify.py` (original), `store.py` (original), `mcp_server.py`. *Done when* you can explain `assess()` and `Snapshot` in your own words.
2. **Config.** Add the settings in 5.1. *Done when* `python -c "import sentinel.config"` runs without error.
3. **Store.** Rewrite `store.py` with the fields in 5.3. *Done when* a subscriber can be created, found by phone and token, updated, and survives a restart.
4. **Escalation.** Write `escalation.py` (5.4). *Done when* the first six `TestEscalation` tests pass.
5. **Windows.** Write `windows.py` (5.5). *Done when* `TestWindows` passes.
6. **Messages.** Write `messages.py` (5.7). *Done when* `TestMessages` passes.
7. **Channels.** Write `channels/` (5.2) with the console channel. Leave Photon as the stub. *Done when* `get_channel()` returns the console channel with no keys set.
8. **Conversation.** Write `conversation.py` (5.8). *Done when* `TestClassify` and `TestStoreAndReplies` pass.
9. **Routing.** Write `routing.py` (5.6). *Done when* `TestRouting` passes, with and without a key.
10. **Notify.** Reduce `notify.py` to signed webhooks (5.10). Remove Twilio. *Done when* nothing imports `send_sms`.
11. **Cycle.** Rewrite `live.py` (5.9). Keep `refresh_feeds` and `scan_region` unchanged. *Done when* `TestCycle` passes and the real-feed dry run runs.
12. **Web.** Write `web.py` (5.11). *Done when* `TestWeb` passes.
13. **CLI and MCP.** Update `__main__.py` and `mcp_server.py` (5.12, 5.13). *Done when* `python -m sentinel --help` lists `web` and `subscribe` shows `--consent`.
14. **Photon adapter.** Implement section 6 once the SDK docs are confirmed. *Done when* a test message reaches a phone and an inbound reply returns through `/photon/inbound`.
15. **Drill.** Run section 7.5 with real phones.
16. **Dashboard UI** (optional for the demo). A page that reads `/dashboard.json`.

---

## 9. Known gaps, risks and open decisions

**Gaps (in order of importance for a real event)**

1. **Photon adapter not implemented.** The product cannot send a text yet.
2. **No signup page.** Subscribers come only from the CLI or the MCP tool. People need to see the consent language before they agree.
3. **Safe destinations are not real.** Routes go to generic points about 12 km away, opposite the fire. Replace them with the official shelter or evacuation-point list.
4. **No road closures.** The Caltrans closure feed is not used.
5. **The fire filter is a proxy.** It rejects routes near current fire points. It does not predict when the fire reaches a road. The spread model in `modeling/wildfire/spread.py` could support this later.
6. **Storage is one JSON file with a lock.** One process only. Use a database before scaling.
7. **No authentication on `/dashboard.json`.** It shows counts and recent message text, and recent messages include route tokens. Restrict access before deployment.
8. **Route tokens are bearer links.** Anyone with the link sees that person's route and origin. Consider expiry.
9. **Unreached people.** Without outbound calls, repeat texts are the only mitigation. Consider a second channel per person if Photon supports it.
10. **Replay not updated.** The historical replay does not use the escalation sequence, so the evaluation numbers in the README do not cover texts or reminders.
11. **Routing cost and terms.** Routes are computed per subscriber per cycle while they are under threat. Check Google's pricing, quotas, and terms on storing and sending route results.
12. **Unused settings.** The `TWILIO_*` keys and the old `template_message` path in `policy.py` (still used for webhook text) should be cleaned up later.

**Open product decisions**

- Should the window appear in evacuate messages too, or only in warn? (Currently only warn.)
- Should people be told the official zone name (LA County or Ventura County zones) instead of a generic grid? Official zones are better for matching against orders.
- Does the voice agent need a free-form LLM answer for questions the deterministic replies do not cover? (Currently no.)
- Should "LEFT" also be accepted by voice as "I've left"? (Currently yes, through the same parser.)

---

## 10. Environment and dependencies

- **Python**: 3.10 or newer (the code uses `str | None` and `zoneinfo`). The deploy image uses 3.12. The repo's `__pycache__` files are from 3.13.
- **Packages**: see `agent/requirements.txt` (`numpy`, `rasterio`, `shapely`, `requests`, `pyarrow`, `pydantic`, `openai`, `mcp`, `chalkcompute`, `starlette`, `uvicorn`, `tzdata`). Tests also use `httpx`.
- **Windows**: `zoneinfo` needs `tzdata`. Run commands from `agent/`. Use the venv's `Scripts\python.exe`.
- **Environment file**: `agent/.env` (not committed). Keys are listed in `agent/.env.example`. Never commit real keys.
- **Feeds**: `modeling/out/live/*.json`, produced by `python -m wildfire live` in `modeling/`.

---

## 11. Kickoff prompt for another coding agent

Paste this with the repository attached:

> You are continuing the FireTrace Sentinel build in `agent/`. Read `agent/docs/IMPLEMENTATION_PLAN.md` first, in full. The decisions in section 2 are fixed. Work in the order in section 8, and run `python -m unittest discover -s tests -v` from `agent/` after each step. Do not add outbound calls. Do not use Twilio. Do not use an LLM for route, window, status, or departure replies. Keep the cycle calling the policy once per subscriber. Do not commit or push unless asked. Before changing anything in `store.py`, confirm the test state folder is still set before any `sentinel` import. When you finish a step, report the test count and anything you could not verify.
