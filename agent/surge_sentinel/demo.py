"""Alert demo for the surge map: pick any address, replay a storm for it.

Unlike the wildfire demo nothing moves: the storm, the water and the address
are all real. The sentinel watches the address hour by hour with only the
forecasts and gauge readings available then, and emails each alert; a final
notice goes out when the gauges show water at the address.
"""
import threading
import uuid

from sentinel.config import ALERT_EMAIL

from .notify import deliver
from .policy import get_policy
from .signals import assess, level_rank
from .world import ground_at, load_storm, observed_arrival_h


class SurgeDemoSession:
    """The sentinel watching one address through one storm. Thread-safe."""

    def __init__(self, storm, lat, lon, label=None, policy_name="rules", email=None):
        self.storm, self.lat, self.lon = storm, lat, lon
        self.label = label or f"{lat:.4f}, {lon:.4f}"
        self.email = email or ALERT_EMAIL
        self.info = load_storm(storm)[0]
        self.arrival, self.peak = observed_arrival_h(storm, lat, lon)
        self.spill, self.ground = ground_at(storm, lat, lon)
        self.policy = get_policy(policy_name)
        self.sub = {"id": "surge-demo", "lat": lat, "lon": lon, "label": self.label, "email": self.email}
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        self.hour, self.current, self.history, self.events, self.now = -1, "none", [], [], None

    def config(self):
        return {"label": self.label, "lat": self.lat, "lon": self.lon, "storm": self.storm, "name": self.info["name"],
                "start": self.info["start"], "arrival_h": self.arrival, "peak_depth_ft": self.peak,
                "ground_ft": self.ground, "spill_ft": self.spill, "email": self.email, "policy": self.policy.name}

    def step_to(self, hour):
        """Advance to `hour` (rewinding resets). Returns the alerts this step produced."""
        with self._lock:
            hour = min(hour, self.info["hours"])
            if hour < self.hour:
                self.reset()
            new = []
            for h in range(self.hour + 1, hour + 1):
                s = assess(self.storm, h, self.lat, self.lon)
                d = self.policy.decide(s, self.history)
                self.now = {"hour": h, "level": d["level"], "eta_h": s["eta_h"], "flooding_now_ft": s["flooding_now_ft"],
                            "forecast_peak_depth_ft": s["forecast_peak_depth_ft"], "high_peak_depth_ft": s["high_peak_depth_ft"]}
                if d["level"] != self.current and (level_rank(d["level"]) >= level_rank("watch") or self.current != "none"):
                    a = self._alert(s, d, h)
                    if d["level"] in ("warn", "evacuate"):
                        a["way_out"] = self._way_out(h)
                    self.history.append({k: a[k] for k in ("as_of", "level", "message")})
                    self.current = d["level"]
                    new.append(a)
                if self.arrival is not None and h + 1 > self.arrival >= h:
                    first = next((e["hour"] for e in self.events + new if e["kind"] != "water-arrived"), None)
                    msg = (f"Seawater has reached your address (6 inches deep by hour {self.arrival:.1f} of the replay, "
                           f"{self.peak:.1f} ft at the peak). Your first alert went out at hour {first}. If you are still "
                           f"there, get to the highest floor and call 911 if the water keeps rising.")
                    new.append(self._alert(s, {"level": "evacuate", "message": msg, "reasons": [
                        "tide gauges show water at this address"]}, self.arrival, kind="water-arrived"))
            self.events.extend(new)
            self.hour = max(self.hour, hour)
            return new

    def state(self):
        with self._lock:
            return {"hour": self.hour, "now": self.now, "current": self.current,
                    "alerts": [{k: a[k] for k in ("id", "hour", "level", "kind", "message", "email")} for a in self.events]}

    def send(self, alert):
        alert["email"] = "sending"
        res = deliver(self.sub, alert) if self.email else []
        ok = any(r.get("ok") for r in res)
        alert["email"] = "sent" if ok else ("not sent: " + (res[0]["status"] if res else "no recipient"))
        return alert["email"]

    def _way_out(self, hour):
        """Escape route at this replay hour for the email (nothing is moved in the surge demo, so it's real)."""
        from routing import escape_route
        from routing.engine import summary_for_texts
        from sentinel.messages import way_out
        try:
            r = escape_route(self.lat, self.lon, hazard="surge", mode=f"replay:{self.storm}", depart_hour=hour)
        except FileNotFoundError:            # no road graph built for this storm
            return None
        return way_out({"route": summary_for_texts(r)}, tz=self.info.get("tz")) if r.get("status") == "ok" else None

    def _alert(self, s, d, hour, kind=None):
        return {"id": uuid.uuid4().hex[:12], "subscriber_id": "surge-demo", "label": f"[DEMO] {self.label}",
                "hour": hour, "as_of": f"{self.info['name']} replay hour {hour:g} ({s['as_of']})", "level": d["level"],
                "previous": self.current,
                "kind": kind or ("escalation" if level_rank(d["level"]) > level_rank(self.current) else "de-escalation"),
                "message": d["message"], "reasons": d.get("reasons", []), "policy": self.policy.name, "email": "queued",
                "signals": {k: s[k] for k in ("lat", "lon", "ground_ft", "spill_ft", "water_now_ft", "surge_now_ft",
                                              "eta_h", "forecast_peak_depth_ft", "high_peak_depth_ft", "storm")}}
