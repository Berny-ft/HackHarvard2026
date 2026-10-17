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
6. **Agent logic.** Give the agent a webhook tool: `POST https://<public url>/photon/voice` (public URL = tunnel to this app,
   e.g. `ngrok http 8787`), header `X-Photon-Secret: <PHOTON_WEBHOOK_SECRET>`, JSON body
   `{"from": "+15146646295", "transcript": "<what the caller said>"}`. The reply is `{"reply": "..."}`; the agent speaks it.
7. **agent/.env.** `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID` (the imported number's id).
8. **Call.** `python -m sentinel call --phone 5146646295`, or "Call my phone" on http://127.0.0.1:8787/test.
   Calling in works too: dial the Photon number.
