"""Deliver surge alerts: the same channels as the wildfire sentinel (signed
webhook, email, SMS), with a flood-specific email."""
import json
import smtplib
from email.message import EmailMessage

import requests

from sentinel.config import ALERT_EMAIL, SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER
from sentinel.notify import send_sms, send_webhook


def render_email(alert):
    from sentinel.messages import way_out_lines
    """Subject + plain-text body. The subject alone should tell an agent what to do."""
    sig = alert.get("signals", {})
    where = alert.get("label") or f"{sig.get('lat', '')}, {sig.get('lon', '')}"
    subject = f"SurgeTrace {alert['level'].upper()}: {where}"
    if alert.get("kind") == "water-arrived":
        subject = f"SurgeTrace WATER AT YOUR ADDRESS: {where}"
    elif sig.get("eta_h") is not None:
        subject += f" (flooding forecast in {sig['eta_h']} h)"
    st = sig.get("storm") or {}
    lines = [
        alert["message"], "",
        *way_out_lines(alert.get("way_out")),
        f"Level: {alert['level']} (was {alert.get('previous', 'none')})",
        f"Location: {where}",
        f"Ground: {sig.get('ground_ft')} ft NAVD88; seawater reaches it at {sig.get('spill_ft')} ft",
        f"Forecast flooding in: {sig.get('eta_h')} h; expected depth {sig.get('forecast_peak_depth_ft')} ft, "
        f"high scenario {sig.get('high_peak_depth_ft')} ft",
        f"Water at the nearest gauges now: {sig.get('water_now_ft')} ft ({sig.get('surge_now_ft')} ft above the tide)",
        f"Storm: category {st.get('category')}, {st.get('vmax_mph')} mph, {st.get('distance_km')} km to the {st.get('bearing')}; "
        f"closest approach {st.get('closest_km')} km in {st.get('closest_in_h')} h",
        f"As of: {alert.get('as_of')}", "",
        "Why: " + "; ".join(alert.get("reasons", [])), "",
        "SurgeTrace Sentinel is an automated early-warning agent. Always follow official evacuation orders.",
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
    for fn, target in ((send_webhook, sub.get("webhook_url")), (send_email, sub.get("email") or ALERT_EMAIL),
                       (send_sms, sub.get("phone"))):
        if not target:
            continue
        try:
            arg = f"SurgeTrace {alert['level'].upper()}: {alert['message']}" if fn is send_sms else alert
            results.append(fn(target, arg))
        except (requests.RequestException, smtplib.SMTPException, OSError) as e:
            results.append({"channel": fn.__name__, "ok": False, "status": str(e)})
    return results
