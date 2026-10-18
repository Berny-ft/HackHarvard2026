"""Demo: a real fire, moved to your address, with real alert emails.

Replays a past incident hour by hour, shifted so that your address sits where a
home the fire actually destroyed stood (same distance and direction from the
ignition). Weather, spread and satellite detections are the real ones; only the
map position moves. The sentinel watches your address and emails an alert on
every level change, then a final notice when the fire was observed at the home.

Two ways to run it:
  python -m sentinel alert-demo              # in the terminal
  python -m sentinel demo-server          # for the web map: play the fire, emails follow
"""
import json
import os
import threading
import time
import uuid
from dataclasses import replace

import requests

from .config import ALERT_EMAIL, INCIDENTS
from .notify import deliver
from .policy import get_policy
from .replay import observed_arrival_h
from .signals import assess, level_rank
from .snapshot import Fires, load_incident, replay_snapshot

# A destroyed home per fire whose household was warned well before the fire came.
ANCHORS = {"eaton_2025": (34.18, -118.13), "palisades_2025": (34.045, -118.53)}


def incidents():
    """Fires with a replay on disk."""
    return sorted(p.name for p in INCIDENTS.iterdir() if (p / "incident.json").exists())


def anchor(incident):
    """The place your address stands in for. Hand-picked for the train fires; for others, the
    satellite-observed burning spot (5-12 h in) where the rule policy gave the longest evacuation lead.
    Cached next to the incident."""
    if incident in ANCHORS:
        return ANCHORS[incident]
    path = INCIDENTS / incident / "demo_anchor.json"
    if path.exists():
        a = json.loads(path.read_text())
        return a["lat"], a["lon"]
    from .replay import replay_subscriber
    feats = json.loads((INCIDENTS / incident / "detections.geojson").read_text())["features"]
    cands = [f for f in feats if 5 <= f["properties"]["hour"] <= 12][::max(1, len(feats) // 400)][:40]
    best, best_lead = None, -1e9
    for f in cands:
        lon, lat = f["geometry"]["coordinates"]
        arr = observed_arrival_h(incident, lat, lon)
        r = replay_subscriber(incident, lat, lon)
        ev, wa = r["first_hour"]["evacuate"], r["first_hour"]["warn"]
        if arr is None or ev is None or wa is None or wa >= ev:
            continue
        lead = arr - ev
        if lead > best_lead:
            best, best_lead = (lat, lon), lead
    if best is None:
        raise SystemExit(f"no usable demo anchor for {incident}")
    path.write_text(json.dumps({"lat": best[0], "lon": best[1], "evacuate_lead_h": best_lead}))
    return best


def geocode(address):
    r = requests.get("https://nominatim.openstreetmap.org/search", timeout=15,
                     params={"q": address, "format": "json", "limit": 1},
                     headers={"User-Agent": "hackharvard-firetrace-demo"})
    r.raise_for_status()
    hits = r.json()
    if not hits:
        raise SystemExit(f"could not find {address!r}; pass --lat/--lon instead")
    return float(hits[0]["lat"]), float(hits[0]["lon"])


def default_location():
    """The demo address from agent/.env: (lat, lon, label)."""
    label = os.environ.get("SENTINEL_DEMO_ADDRESS")
    if os.environ.get("SENTINEL_DEMO_LAT") and os.environ.get("SENTINEL_DEMO_LON"):
        lat, lon = float(os.environ["SENTINEL_DEMO_LAT"]), float(os.environ["SENTINEL_DEMO_LON"])
        return lat, lon, label or f"{lat:.4f}, {lon:.4f}"
    if label:
        return (*geocode(label), label)
    return None


def shifted_snapshot(incident, hour, dlat, dlon):
    s = replay_snapshot(incident, hour)
    f = s.fires
    fires = Fires(f.lat + dlat, f.lon + dlon, f.age_h, f.kind, [f"{n} (replay)" for n in f.name])
    return replace(s, fires=fires)


class DemoSession:
    """The sentinel watching one address through a shifted replay. Thread-safe."""

    def __init__(self, lat, lon, label, incident="eaton_2025", policy_name="rules", email=None):
        self.lat, self.lon, self.label, self.incident = lat, lon, label, incident
        self.email = email or ALERT_EMAIL
        alat, alon = anchor(incident)
        self.dlat, self.dlon = lat - alat, lon - alon
        self.arrival = observed_arrival_h(incident, alat, alon)
        self.info = load_incident(incident)[0]
        self.policy = get_policy(policy_name)
        self.sub = {"id": "demo", "lat": lat, "lon": lon, "label": label, "email": self.email}
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        self.hour, self.current, self.history, self.events, self.now = -1, "none", [], [], None

    def config(self):
        return {"label": self.label, "lat": self.lat, "lon": self.lon, "incident": self.incident,
                "fire": self.info["name"], "start": self.info["start"], "dlat": self.dlat, "dlon": self.dlon,
                "arrival_h": self.arrival, "email": self.email, "policy": self.policy.name}

    def step_to(self, hour):
        """Advance to `hour` (rewinding resets). Returns the alerts this step produced."""
        with self._lock:
            if hour < self.hour:
                self.reset()
            new = []
            for h in range(self.hour + 1, hour + 1):
                snap = shifted_snapshot(self.incident, h, self.dlat, self.dlon)
                s = assess(snap, self.lat, self.lon)
                d = self.policy.decide(s, self.history, snap=snap)
                self.now = {"hour": h, "level": d["level"], "nearest_fire_km": s["nearest_fire_km"],
                            "eta_h": s["eta_h"], "downwind": s["downwind"]}
                if d["level"] != self.current and level_rank(d["level"]) >= level_rank("watch"):
                    a = self._alert(s, d, h)
                    self.history.append({k: a[k] for k in ("as_of", "level", "message")})
                    self.current = d["level"]
                    new.append(a)
                if h + 1 > self.arrival >= h:
                    first = self.events[0]["hour"] if self.events else (new[0]["hour"] if new else None)
                    s["nearest_fire_km"] = 0.0
                    msg = (f"Fire has reached your address (observed by satellite at hour {self.arrival:.1f} of the "
                           f"replay). Your first warning went out at hour {first}. If you are still there, leave "
                           f"immediately or shelter in a cleared, non-flammable area and call 911.")
                    new.append(self._alert(s, {"level": "evacuate", "message": msg, "reasons": [
                        "satellite fire detection within 750 m of the address"]}, self.arrival, kind="fire-arrived"))
            self.events.extend(new)
            self.hour = max(self.hour, hour)
            return new

    def state(self):
        with self._lock:
            return {"hour": self.hour, "now": self.now, "current": self.current,
                    "alerts": [{k: a[k] for k in ("id", "hour", "level", "kind", "message", "email")}
                               for a in self.events]}

    def send(self, alert):
        """Deliver one alert by email and record the outcome on it."""
        alert["email"] = "sending"
        res = deliver(self.sub, alert) if self.email else []
        ok = any(r.get("ok") for r in res)
        alert["email"] = "sent" if ok else ("not sent: " + (res[0]["status"] if res else "no recipient"))
        return alert["email"]

    def _alert(self, s, d, hour, kind=None):
        return {"id": uuid.uuid4().hex[:12], "subscriber_id": "demo", "label": f"[DEMO] {self.label}",
                "hour": hour, "as_of": f"demo replay hour {hour:g}", "level": d["level"], "previous": self.current,
                "kind": kind or ("escalation" if level_rank(d["level"]) > level_rank(self.current) else "de-escalation"),
                "message": d["message"], "reasons": d.get("reasons", []), "policy": self.policy.name,
                "email": "queued",
                "signals": {k: s[k] for k in ("lat", "lon", "nearest_fire_km", "nearest_fire_name", "fire_bearing_deg",
                                              "downwind", "eta_h", "red_flag", "weather_now")}}


def run(lat, lon, label, incident="eaton_2025", seconds_per_hour=3.0, policy_name="rules", email=None):
    """Terminal demo."""
    d = DemoSession(lat, lon, label, incident, policy_name, email)
    print(f"\nDEMO: the {d.info['name']} ({d.info['start'][:10]}) replayed toward {label}")
    print(f"Alerts go to {d.email or '(no SENTINEL_ALERT_EMAIL set)'}; the real fire reached the matching home "
          f"at hour {d.arrival:.1f}.\n")
    for h in range(int(d.arrival) + 2):
        new = d.step_to(h)
        n = d.now
        fire = f"{n['nearest_fire_km']:.1f} km" if n["nearest_fire_km"] is not None else "-"
        eta = f"{n['eta_h']:.1f} h" if n["eta_h"] is not None else "-"
        print(f"  hour {h:>2}  fire {fire:>8}  ETA {eta:>7}  {n['level'].upper()}")
        for a in new:
            status = d.send(a)
            print(f"           -> {a['level'].upper()} alert ({a['kind']}): "
                  f"{'EMAIL SENT' if status == 'sent' else 'email ' + status}")
        time.sleep(seconds_per_hour)
    print("\nDemo finished.")
