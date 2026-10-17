"""Outbound phone calls over Photon Voice. Photon supplies the phone number and SIP trunk; ElevenLabs runs the
voice agent and places the call (POST /v1/convai/sip-trunk/outbound-call). The agent answers questions by calling
this app's POST /photon/voice. Setup steps: docs/VOICE_SETUP.md.

Needs in agent/.env: ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID, ELEVENLABS_PHONE_NUMBER_ID."""
import os

import requests

API = os.environ.get("ELEVENLABS_API_URL", "https://api.elevenlabs.io")


def _cfg():
    return (os.environ.get("ELEVENLABS_API_KEY"), os.environ.get("ELEVENLABS_AGENT_ID"),
            os.environ.get("ELEVENLABS_PHONE_NUMBER_ID"))


def configured():
    return all(_cfg())


def place_call(to, message):
    key, agent, number = _cfg()
    if not (key and agent and number):
        return {"ok": False, "status": "Set ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID and ELEVENLABS_PHONE_NUMBER_ID "
                                       "in agent/.env (see docs/VOICE_SETUP.md; needs a dedicated Photon number)"}
    r = requests.post(f"{API}/v1/convai/sip-trunk/outbound-call", headers={"xi-api-key": key}, timeout=30, json={
        "agent_id": agent, "agent_phone_number_id": number, "to_number": to,
        "conversation_initiation_client_data": {"conversation_config_override": {"agent": {"first_message": message}}},
    })
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.ok and body.get("success", True):
        return {"ok": True, "status": body.get("message", "calling"), "conversation_id": body.get("conversation_id")}
    return {"ok": False, "status": f"{r.status_code} {body.get('detail') or body.get('message') or r.text[:200]}"}
