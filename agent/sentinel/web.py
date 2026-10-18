"""Inbound text and voice, the route page, and dashboard data.

  python -m sentinel web --port 8080
"""
import hashlib
import hmac
import html
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from . import config, conversation, messages, store
from .channels import get_channel

UNKNOWN = "This number isn't signed up."


def _signed_ok(body: bytes, header: str, shared: str = "") -> bool:
    secret = config.PHOTON_WEBHOOK_SECRET
    if secret and shared and hmac.compare_digest(shared, secret):    # voice-agent tools can't compute an HMAC
        return True
    if not secret:
        return get_channel().name == "console"      # unsigned only while developing
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header or "", want)


async def _read(request: Request):
    body = await request.body()
    if not _signed_ok(body, request.headers.get("X-Photon-Signature", ""), request.headers.get("X-Photon-Secret", "")):
        return None, JSONResponse({"error": "bad signature"}, status_code=401)
    try:
        return json.loads(body or b"{}"), None
    except ValueError:
        return None, JSONResponse({"error": "bad json"}, status_code=400)


def _now():
    return datetime.now(timezone.utc).isoformat()


_LOG = []


def _log(kind, direction, who, text):
    _LOG.append({"t": _now(), "kind": kind, "dir": direction, "who": who, "text": text})
    del _LOG[:-200]


TEST_HTML = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Photon test</title>
<style>body{font:15px system-ui;max-width:880px;margin:24px auto;padding:0 16px;color:#222}
h1{font-size:20px}section{border:1px solid #ddd;border-radius:10px;padding:14px 16px;margin:16px 0}
h2{font-size:16px;margin:0 0 8px}input,button,select{font:inherit;padding:7px 10px;margin:3px 0}
input[type=text]{width:100%;box-sizing:border-box}button{cursor:pointer}small{color:#666}
.log{background:#fafafa;border:1px solid #eee;border-radius:8px;height:230px;overflow:auto;padding:8px;font:13px ui-monospace,monospace}
.in{color:#0a5}.out{color:#06c}.error{color:#c00}</style>
<h1>Photon test console</h1>
<small>Line <b id=line></b> &rarr; phone <b id=phone></b>. Shared line: <b>you must text the line first</b> before the app can text you.</small>

<section><h2>1. Texting</h2>
<small>Send an arbitrary text to your phone from the line. Replies you text back show up below and get a real agent reply.</small>
<input type=text id=tbody placeholder="Message to send to your phone" value="Test text from FireTrace Sentinel">
<button onclick=sendText()>Send text to my phone</button> <button onclick=sendAlert()>Send sample alert</button>
<div class=log id=tlog></div></section>

<section><h2>2. Calling (voice)</h2>
<small id=vstatus></small><br>
<small>Real phone calls need a dedicated Photon number + ElevenLabs SIP agent (docs/VOICE_SETUP.md). This is a local test call: your mic &rarr; transcript &rarr; agent reply spoken back (the same logic as POST /photon/voice). Use Chrome/Edge.</small>
<button onclick=callPhone()>&#9742; Call my phone</button> <small>(real call via Photon voice)</small><br>
<button id=callbtn onclick=toggleCall()>&#128222; Start call (use mic)</button> <small id=callstate></small><br>
<input type=text id=vbody placeholder="Or type what the caller says, e.g. 'is my house safe?' or 'I've left'">
<button onclick=sendVoice()>Say it (typed turn)</button>
<div class=log id=vlog></div></section>
<script>
const $=id=>document.getElementById(id);
async function j(u,b){const r=await fetch(u,b?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(b)}:{});return r.json()}
function render(el,items){el.innerHTML=items.map(m=>`<div class="${m.dir}">${m.t.slice(11,19)} ${m.dir==='in'?'&larr; you':m.dir==='out'?'&rarr; app':'! error'}: ${m.text.replace(/</g,'&lt;')}</div>`).join('');el.scrollTop=el.scrollHeight}
async function poll(){const d=await j('/test/log');render($('tlog'),d.log.filter(m=>m.kind==='text'));render($('vlog'),d.log.filter(m=>m.kind==='voice'))}
async function sendText(){const d=await j('/test/send',{body:$('tbody').value});if(!d.ok)alert('Send failed: '+d.status);poll()}
async function sendAlert(){const d=await j('/test/send',{alert:true});if(!d.ok)alert('Send failed: '+d.status);poll()}
async function callPhone(){const d=await j('/test/call',{});alert(d.ok?'Calling your phone... pick up.':'Call failed: '+d.status);poll()}
function speak(t,done){speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(t);u.onend=done;speechSynthesis.speak(u)}
async function turn(said,loud){const d=await j('/test/voice',{transcript:said});poll();if(loud&&d.reply)speak(d.reply,()=>{if(rec)listen()});else if(rec)listen()}
async function sendVoice(){const v=$('vbody').value;$('vbody').value='';if(v)await turn(v,false)}
let rec=null;const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
function listen(){if(!rec)return;$('callstate').textContent='listening... speak now';try{rec.start()}catch(e){}}
function toggleCall(){if(rec){const r=rec;rec=null;r.abort();speechSynthesis.cancel();$('callbtn').innerHTML='&#128222; Start call (use mic)';$('callstate').textContent='call ended';return}
if(!SR){alert('Mic speech recognition needs Chrome or Edge. Use the typed box instead.');return}
rec=new SR();rec.lang='en-US';rec.interimResults=false;
rec.onresult=e=>{const said=e.results[0][0].transcript;$('callstate').textContent='heard: '+said;turn(said,true)};
rec.onerror=e=>{$('callstate').textContent='mic error: '+e.error+(e.error==='not-allowed'?' (allow the microphone)':'');if(e.error==='not-allowed'||e.error==='service-not-allowed'){rec=null;$('callbtn').innerHTML='&#128222; Start call (use mic)'}};
rec.onend=()=>{};
$('callbtn').innerHTML='&#9209; End call';speak('FireTrace Sentinel. How can I help?',listen)}
(async()=>{const c=await j('/test/config');$('line').textContent=c.line;$('phone').textContent=c.phone;$('vstatus').textContent=c.voice;poll();setInterval(poll,2000)})()
</script>"""


def _test_phone():
    return os.environ.get("DEMO_PHONE") or "+15146646295"


async def test_page(request: Request):
    return HTMLResponse(TEST_HTML)


async def test_config(request: Request):
    voice = "Voice status: no Photon voice profile/line configured (checked via API)."
    try:
        import requests
        r = requests.get(f"https://api.photon.codes/v1/projects/{config.PHOTON_PROJECT_ID}/platforms/voice/profiles",
                         headers={"Authorization": f"Bearer {config.PHOTON_API_KEY}"}, timeout=8)
        n = len(r.json().get("profiles", []))
        voice = f"Voice status: {n} Photon voice profile(s) configured." + ("" if n else " Real calls are not available yet.")
    except Exception:
        pass
    return JSONResponse({"line": config.PHOTON_NUMBER or "+16289988619", "phone": _test_phone(), "voice": voice})


async def test_log(request: Request):
    return JSONResponse({"log": _LOG})


async def test_send(request: Request):
    data = await request.json()
    phone = store.normalize_phone(_test_phone())
    body = data.get("body", "")
    if data.get("alert"):
        sub = store.find_by_phone(phone)
        body = ("FireTrace test alert: this is a sample message from your Sentinel agent. "
                "Reply STATUS for your fire status." if not sub else
                conversation.respond(sub, "status", _now()))
    if not body.strip():
        return JSONResponse({"ok": False, "status": "empty"})
    try:
        res = get_channel().send_text(phone, body)
    except Exception as e:
        res = {"ok": False, "status": str(e)}
    _log("text", "out" if res.get("ok") else "error", phone, body if res.get("ok") else str(res.get("status")))
    return JSONResponse(res)


async def test_call(request: Request):
    from .channels import voice as vc
    phone = store.normalize_phone(_test_phone())
    sub = store.find_by_phone(phone)
    sit = (sub or {}).get("situation") or {}
    msg = (messages.call_opening(sub) if sit.get("level", "none") != "none" else
           conversation.respond(sub, "status", _now()) if sub else "This is a FireTrace Sentinel test call.")
    res = vc.place_call(phone, msg, messages.briefing(sub) if sub else None)
    _log("voice", "out" if res.get("ok") else "error", phone,
         f"CALL {res.get('status')}: {msg}" if res.get("ok") else str(res.get("status")))
    return JSONResponse(res)


async def test_voice(request: Request):
    data = await request.json()
    phone = _test_phone()
    sub = store.find_by_phone(phone)
    said = data.get("transcript", "")
    _log("voice", "in", phone, said)
    reply = conversation.respond(sub, said, _now()) if sub else UNKNOWN
    _log("voice", "out", phone, reply)
    return JSONResponse({"ok": True, "reply": reply})


async def inbound(request: Request):
    data, err = await _read(request)
    if err:
        return err
    sender = data.get("from", "")
    sub = store.find_by_phone(sender)
    reply = conversation.respond(sub, data.get("text", ""), _now()) if sub else UNKNOWN
    _log("text", "in", sender, data.get("text", ""))
    try:
        res = get_channel().send_text(store.normalize_phone(sender), reply)
    except Exception as e:
        _log("text", "error", sender, str(e))
        return JSONResponse({"ok": False}, status_code=502)
    _log("text", "out" if res.get("ok") else "error", sender, reply if res.get("ok") else str(res.get("status")))
    return JSONResponse({"ok": True} if res.get("ok") else {"ok": False}, status_code=200 if res.get("ok") else 502)


async def voice(request: Request):
    data, err = await _read(request)
    if err:
        return err
    sub = store.find_by_phone(data.get("from", ""))
    text = data.get("transcript", "")
    if not sub:
        reply = UNKNOWN
    elif conversation.classify(text) == "FREEFORM":     # "why?", "how bad is it?": the agent answers from the facts
        reply = messages.briefing(sub)
    else:
        reply = conversation.respond(sub, text, _now())
    _log("voice", "in", data.get("from", ""), text)
    return JSONResponse({"reply": reply, "briefing": messages.briefing(sub) if sub else UNKNOWN})


async def route_page(request: Request):
    sub = store.find_by_token(request.path_params["token"])
    if not sub:
        return HTMLResponse("Not found", status_code=404)
    e = html.escape
    sit = sub.get("situation") or {}
    level = sit.get("level", "none")
    rows = [f"<h1>FireTrace: {e(level.upper())}</h1>"]
    if level != "none":
        rows.append(f"<p>{e(messages.where(sit))}.</p>")
    r = sub.get("route")
    if r:
        o, d = r["origin"], r["dest"]
        wp = "|".join(f"{a:.5f},{b:.5f}" for a, b in r.get("waypoints", []))
        gm = (f"https://www.google.com/maps/dir/?api=1&origin={o[0]},{o[1]}&destination={d[0]},{d[1]}"
              f"&travelmode=driving" + (f"&waypoints={wp}" if wp else ""))
        rows.append(f"<p>{e(r['summary'])}, about {round(r['duration_min'])} min "
                    f"({r['distance_km']:.0f} km) to a {e(r['dest_label'])}, planned to stay ahead of the fire, "
                    f"as of {e(messages.clock(r['computed_at']))}.</p>"
                    + ("<p><b>Fire may reach parts of this route. It is the least-bad option; follow officers.</b></p>"
                       if r.get("fire_risk") else "")
                    + f'<p><a href="{e(gm, quote=True)}">Open in Google Maps</a></p>')
    else:
        rows.append("<p>No live route right now. Head away from the fire and follow officers.</p>")
    w = sub.get("window")
    if w and w["status"] == "window":
        rows.append(f"<p>Planning window: {e(messages.clock(w['earliest']))} to {e(messages.clock(w['latest']))} "
                    f"({e(w['confidence'])} confidence). Estimate, not an order.</p>")
    elif w:
        rows.append("<p>There is no safe later window. Leave now.</p>")
    rows.append(f"<p>{e(messages.contact_line())} Official county orders take precedence.</p>")
    page = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>FireTrace route</title><body style="font:16px system-ui;max-width:32rem;margin:1rem auto;'
            f'padding:0 1rem">{"".join(rows)}</body>')
    return HTMLResponse(page)


def _dash_ok(request: Request) -> bool:
    tok = config.DASH_TOKEN
    return not tok or hmac.compare_digest(request.query_params.get("token", ""), tok)


def _redact(text):
    return re.sub(r"https?://\S+", "[link]", text or "")


async def dashboard(request: Request):
    if not _dash_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    subs = store.subscribers()
    levels = Counter((s.get("situation") or {}).get("level", "none") for s in subs)
    stages = Counter(s.get("stage", "none") for s in subs)
    alerts = store.recent_alerts(limit=20)
    live = config.LIVE / "meta.json"
    return JSONResponse({
        "subscribers": len(subs), "levels": dict(levels), "stages": dict(stages),
        "left": sum(1 for s in subs if s.get("left_at")),
        "evacuated_not_left_max_reminders": sum(
            1 for s in subs if s.get("stage") == "evacuated" and not s.get("left_at")
            and s.get("reminders", 0) >= config.MAX_REMINDERS),
        "consented_for_text": sum(1 for s in subs if s.get("consent")),
        "feeds_updated": datetime.fromtimestamp(live.stat().st_mtime, timezone.utc).isoformat()
        if live.exists() else None,
        "recent_alerts": [{**{k: a.get(k) for k in ("as_of", "level", "channel", "text_kind", "kind")},
                           "message": _redact(a.get("message"))} for a in alerts]})


DASH_HTML = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FireTrace Sentinel</title>
<style>
:root{--bg:#0e1116;--fg:#e8eaed;--mut:#9aa0a6;--card:#171b22;--none:#3a7d44;--watch:#c9a227;--warn:#e07b25;--evacuate:#d93a2f}
@media (prefers-color-scheme:light){:root{--bg:#f6f7f9;--fg:#15181d;--mut:#5f6368;--card:#fff}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif}
main{max-width:64rem;margin:0 auto;padding:1rem}
h1{font-size:1.3rem;margin:.2rem 0 1rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));gap:.7rem}
.card{background:var(--card);border-radius:10px;padding:.8rem}
.n{font-size:1.8rem;font-weight:700}.l{color:var(--mut);font-size:.8rem;text-transform:uppercase}
.b{border-left:5px solid var(--c)}
ul{list-style:none;padding:0}li{background:var(--card);border-left:5px solid var(--c);border-radius:8px;padding:.6rem .8rem;margin:.5rem 0}
small{color:var(--mut)}
</style>
<main><h1>FireTrace Sentinel <small id="upd"></small></h1>
<div class="grid" id="tiles"></div><h2>Recent messages</h2><ul id="feed"></ul></main>
<script>
const C={none:"--none",watch:"--watch",warn:"--warn",evacuate:"--evacuate"};
const q=location.search;
function tile(label,val,lvl){const d=document.createElement("div");d.className="card"+(lvl?" b":"");
 if(lvl)d.style.setProperty("--c","var("+C[lvl]+")");
 const n=document.createElement("div");n.className="n";n.textContent=val;
 const l=document.createElement("div");l.className="l";l.textContent=label;d.append(n,l);return d}
async function load(){
 const r=await fetch("/dashboard.json"+q);if(!r.ok){document.getElementById("upd").textContent="(unauthorized)";return}
 const d=await r.json();const t=document.getElementById("tiles");t.replaceChildren(
  tile("subscribers",d.subscribers),tile("texting enabled",d.consented_for_text),
  ...["none","watch","warn","evacuate"].map(k=>tile(k,(d.levels||{})[k]||0,k)),
  tile("left",d.left),tile("unreached (max reminders)",d.evacuated_not_left_max_reminders));
 const f=document.getElementById("feed");f.replaceChildren(...(d.recent_alerts||[]).slice().reverse().map(a=>{
  const li=document.createElement("li");li.style.setProperty("--c","var("+(C[a.level]||"--none")+")");
  const s=document.createElement("small");s.textContent=(a.channel||"")+" · "+(a.text_kind||a.kind||"")+" · "+(a.as_of||"");
  const m=document.createElement("div");m.textContent=a.message||"";li.append(s,m);return li}));
 document.getElementById("upd").textContent=d.feeds_updated?"· feeds "+d.feeds_updated:"";
}
load();setInterval(load,5000);
</script>"""

SIGNUP_HTML = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FireTrace alerts</title>
<body style="font:16px system-ui;max-width:32rem;margin:1rem auto;padding:0 1rem">
<h1>Get FireTrace wildfire texts</h1>
<p>We text you when a wildfire threatens your location in Los Angeles or Ventura County, send your live
evacuation route when you reply NOW, and stop when you reply LEFT. Estimates only. Official county orders
always take precedence. In an emergency call 911.</p>
<form method="post" action="/signup">
<p><label>Mobile number<br><input name="phone" type="tel" required autocomplete="tel" style="width:100%;font-size:1rem"></label></p>
<p><label>Latitude<br><input id="lat" name="lat" required inputmode="decimal" style="width:100%;font-size:1rem"></label></p>
<p><label>Longitude<br><input id="lon" name="lon" required inputmode="decimal" style="width:100%;font-size:1rem"></label></p>
<p><button type="button" onclick="navigator.geolocation.getCurrentPosition(p=>{lat.value=p.coords.latitude.toFixed(5);lon.value=p.coords.longitude.toFixed(5)})">Use my location</button></p>
<p><label><input type="checkbox" name="consent" value="yes" required>
I agree to receive automated wildfire alert texts at this number about this location. Message frequency varies
during an incident. Reply STOP to cancel at any time. Message and data rates may apply.</label></p>
<p><button type="submit" style="font-size:1rem">Sign up</button></p></form>"""


async def home(request: Request):
    return HTMLResponse(DASH_HTML)


async def signup_form(request: Request):
    return HTMLResponse(SIGNUP_HTML)


async def signup(request: Request):
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8", "replace")).items()}
    try:
        lat, lon = float(form["lat"]), float(form["lon"])
        assert 32.5 <= lat <= 35.5 and -121.0 <= lon <= -116.5          # LA + Ventura area
        phone = store.normalize_phone(form["phone"])
        assert 11 <= len(phone) <= 16
    except (KeyError, ValueError, AssertionError):
        return HTMLResponse("Check your number and location (Los Angeles / Ventura area only).", status_code=400)
    if form.get("consent") != "yes":
        return HTMLResponse("Consent is required to receive texts.", status_code=400)
    # Double opt-in: texts stay off until the number's owner replies YES, and an
    # existing subscriber's location can't be changed from the web form.
    if not store.find_by_phone(phone):
        s = store.subscribe(lat, lon, phone=phone, consent=False)
        store.update(s["id"], pending_consent=True)
        try:
            get_channel().send_text(phone, "FireTrace: reply YES to get wildfire alert texts for this location. "
                                           "Reply STOP to cancel. Msg&data rates may apply. Call 911 in an emergency.")
        except Exception:
            pass
    return HTMLResponse("Almost done: we sent a text to that number. Reply YES to confirm.")


app = Starlette(routes=[
    Route("/", home),
    Route("/signup", signup_form, methods=["GET"]),
    Route("/signup", signup, methods=["POST"]),
    Route("/test", test_page),
    Route("/test/config", test_config),
    Route("/test/log", test_log),
    Route("/test/send", test_send, methods=["POST"]),
    Route("/test/voice", test_voice, methods=["POST"]),
    Route("/test/call", test_call, methods=["POST"]),
    Route("/photon/inbound", inbound, methods=["POST"]),
    Route("/photon/voice", voice, methods=["POST"]),
    Route("/route/{token}", route_page),
    Route("/dashboard.json", dashboard),
])


def serve(port=8080):
    import uvicorn
    uvicorn.run(app, host=config.WEB_HOST, port=port)
