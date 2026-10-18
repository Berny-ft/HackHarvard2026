"""Deliver alerts: signed webhooks for agents, email for people. Phone texts go through channels/.

Email is the channel that reaches personal agents without an API: an agent
that watches its person's inbox (Meta's Muse, for one) sees the alert and acts
on it with what it knows about the household.

Webhook body is JSON; the X-Sentinel-Signature header is
"sha256=" + HMAC-SHA256(SENTINEL_WEBHOOK_SECRET, raw body), so a receiving
agent can verify the alert came from the sentinel before acting on it.
"""
import hashlib
import hmac
import json
import smtplib
from email.message import EmailMessage

import requests

from .config import (ALERT_EMAIL, SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER, TWILIO_FROM,
                     TWILIO_SID, TWILIO_TOKEN, WEBHOOK_SECRET)


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


def render_email(alert):
    from .messages import way_out_lines
    """Subject + plain-text body. The subject alone should tell an agent what to do."""
    sig = alert.get("signals", {})
    where = alert.get("label") or f"{sig.get('lat', '')}, {sig.get('lon', '')}"
    subject = f"FireTrace {alert['level'].upper()}: {where}"
    if alert.get("kind") == "fire-arrived":
        subject = f"FireTrace FIRE AT YOUR ADDRESS: {where}"
    elif sig.get("nearest_fire_name"):
        subject += f" ({sig['nearest_fire_name']} fire, {sig['nearest_fire_km']:.1f} km)"
    wx = sig.get("weather_now") or {}
    lines = [
        alert["message"], "",
        *way_out_lines(alert.get("way_out")),
        f"Level: {alert['level']} (was {alert.get('previous', 'none')})",
        f"Location: {where}",
        f"Nearest fire: {sig.get('nearest_fire_name') or 'unnamed'}, {sig.get('nearest_fire_km')} km, "
        f"bearing {sig.get('fire_bearing_deg')} deg, {'downwind' if sig.get('downwind') else 'not downwind'}",
        f"Modelled hours to arrival: {sig.get('eta_h')}",
        f"Wind: {wx.get('wind_mph')} mph gusting {wx.get('gust_mph')} from {wx.get('wind_dir_deg')} deg",
        f"Red Flag Warning: {sig.get('red_flag') or 'none'}",
        f"As of: {alert.get('as_of')}", "",
        "Why: " + "; ".join(alert.get("reasons", [])), "",
        "FireTrace Sentinel is an automated early-warning agent. Always follow official evacuation orders.",
        "Machine-readable alert:", json.dumps(alert, default=str),
    ]
    return subject, "\n".join(lines)


def send_email(to, alert):
    subject, body = render_email(alert)
    if not (SMTP_USER and SMTP_PASSWORD):
        print(f"[email disabled] to {to}: {subject}")
        return {"channel": "email", "ok": False, "status": "SMTP not configured"}
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = SMTP_FROM, to, subject
    msg["X-Sentinel-Level"] = alert["level"]
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(msg)
    return {"channel": "email", "ok": True, "status": "sent"}


def deliver(sub, alert):
    results = []
    channels = ((send_webhook, sub.get("webhook_url")), (send_email, sub.get("email") or ALERT_EMAIL))
    for fn, target in channels:
        if not target:
            continue
        try:
            results.append(fn(target, alert))
        except (requests.RequestException, smtplib.SMTPException, OSError) as e:
            results.append({"channel": fn.__name__, "ok": False, "status": str(e)})
    return results
