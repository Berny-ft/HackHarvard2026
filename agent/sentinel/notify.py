"""Deliver alerts: signed webhooks for agents, SMS for people.

Webhook body is JSON; the X-Sentinel-Signature header is
"sha256=" + HMAC-SHA256(SENTINEL_WEBHOOK_SECRET, raw body), so a receiving
agent can verify the alert came from the sentinel before acting on it.
"""
import hashlib
import hmac
import json

import requests

from .config import TWILIO_FROM, TWILIO_SID, TWILIO_TOKEN, WEBHOOK_SECRET


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def send_webhook(url, alert):
    body = json.dumps(alert, default=str).encode()
    r = requests.post(url, data=body, timeout=10, headers={
        "Content-Type": "application/json", "X-Sentinel-Signature": sign(body),
        "X-Sentinel-Event": f"alert.{alert['level']}"})
    return {"channel": "webhook", "ok": r.ok, "status": r.status_code}


def send_sms(phone, text):
    if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM):
        print(f"[sms disabled] to {phone}: {text}")
        return {"channel": "sms", "ok": False, "status": "twilio not configured"}
    r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
                      auth=(TWILIO_SID, TWILIO_TOKEN), timeout=10,
                      data={"From": TWILIO_FROM, "To": phone, "Body": text[:1500]})
    return {"channel": "sms", "ok": r.ok, "status": r.status_code}


def deliver(sub, alert):
    results = []
    for fn, target in ((send_webhook, sub.get("webhook_url")), (send_sms, sub.get("phone"))):
        if not target:
            continue
        try:
            arg = alert if fn is send_webhook else f"FireTrace {alert['level'].upper()}: {alert['message']}"
            results.append(fn(target, arg))
        except requests.RequestException as e:
            results.append({"channel": fn.__name__, "ok": False, "status": str(e)})
    return results
