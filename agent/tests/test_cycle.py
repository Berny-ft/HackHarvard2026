import json
import os
import tempfile
import unittest

# Must precede any `sentinel` import, or tests write to the real state folder.
os.environ["SENTINEL_STATE"] = tempfile.mkdtemp(prefix="sentinel-cycle-test-")
os.environ.pop("PHOTON_WEBHOOK_SECRET", None)
os.environ.pop("PHOTON_PROJECT_ID", None)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import numpy as np
    from sentinel import config, live, store
    from sentinel.channels import ConsoleChannel, set_channel
    from sentinel.snapshot import Fires, Snapshot
    HAVE = True
except ImportError:
    HAVE = False

LAT, LON = 34.18, -118.13


def snapshot(fire_km):
    """One confirmed fire `fire_km` north of the test point, strong wind blowing at the point."""
    if fire_km is None:
        fires = Fires(np.array([]), np.array([]), np.array([]), [], [])
    else:
        fires = Fires(np.array([LAT + fire_km / 111.0]), np.array([LON]), np.array([0.5]), ["satellite"], ["Eaton"])
    wx = {"temp_f": 85, "rh_pct": 8, "wind_mph": 30, "gust_mph": 50, "wind_dir_deg": 0,
          "fuel_moisture_1h_pct": 3, "santa_ana_level": 2}
    look = {"peak_santa_ana_level": 2}
    return Snapshot(as_of="2026-10-17T20:00:00+00:00", fires=fires, weather=lambda a, b: wx,
                    outlook=lambda a, b: look, red_flag=lambda a, b: "Red Flag Warning")


@unittest.skipUnless(HAVE, "numpy/starlette not installed")
class TestCycle(unittest.TestCase):
    def setUp(self):
        for s in store.subscribers():
            store.unsubscribe(s["id"])
        store._alerts_path().unlink(missing_ok=True)
        self.ch = ConsoleChannel()
        set_channel(self.ch)
        self.fire_km = 1.5
        self._orig = live.live_snapshot
        self._closures = live.active_closures
        live.active_closures = lambda: []           # no network in tests
        self._route = live.best_route
        live.best_route = lambda *a, **k: None      # no road download in tests
        live.live_snapshot = lambda: snapshot(self.fire_km)
        self._every = config.REMINDER_EVERY_MIN
        config.REMINDER_EVERY_MIN = 0           # reminders eligible every cycle

    def tearDown(self):
        live.live_snapshot = self._orig
        live.active_closures = self._closures
        live.best_route = self._route
        config.REMINDER_EVERY_MIN = self._every

    def run_cycle(self, **kw):
        return live.cycle(refresh=False, channel=self.ch, **kw)

    def test_evacuate_once_then_reminder_then_left(self):
        s = store.subscribe(LAT, LON, phone="818-555-0100", consent=True)
        r = self.run_cycle()
        self.assertEqual([t["text_kind"] for t in r["texts"]], ["evacuate"])
        r = self.run_cycle()
        self.assertEqual([t["text_kind"] for t in r["texts"]], ["reminder"])
        store.update(s["id"], left_at="2026-10-17T20:10:00+00:00")
        r = self.run_cycle()
        self.assertEqual(r["texts"], [])

    def test_threat_clearing_rearms(self):
        s = store.subscribe(LAT, LON, phone="818-555-0100", consent=True)
        self.run_cycle()
        store.update(s["id"], left_at="2026-10-17T20:10:00+00:00")
        self.fire_km = None
        self.run_cycle()
        after = store.get(s["id"])
        self.assertEqual(after["stage"], "none")
        self.assertIsNone(after["left_at"])
        self.assertEqual(after["reminders"], 0)

    def test_left_during_cycle_blocks_text(self):
        s = store.subscribe(LAT, LON, phone="818-555-0100", consent=True)
        orig = live.assess

        def assess_then_left(*a, **k):
            out = orig(*a, **k)
            store.update(s["id"], left_at="2026-10-17T20:00:00+00:00")   # reply lands mid-cycle
            return out
        live.assess = assess_then_left
        try:
            r = self.run_cycle()
        finally:
            live.assess = orig
        self.assertEqual(r["texts"], [])

    def test_min_level_blocks_low_text(self):
        store.subscribe(LAT, LON, phone="818-555-0100", consent=True, min_level="evacuate")
        self.fire_km = 6.0          # warn-range, below min_level
        r = self.run_cycle()
        self.assertEqual(r["texts"], [])

    def test_no_consent_no_text(self):
        s = store.subscribe(LAT, LON, phone="818-555-0100", consent=False)
        r = self.run_cycle()
        self.assertEqual(r["texts"], [])
        self.assertEqual(store.get(s["id"])["stage"], "none")
        self.assertEqual(self.ch.sent, [])

    def test_call_on_evacuate_once(self):
        from sentinel.channels import voice
        placed = []
        orig = voice.place_call, voice.configured, config.CALLS
        voice.place_call = lambda to, msg, brief=None: placed.append((to, msg, brief)) or {"ok": True, "status": "calling"}
        voice.configured = lambda: True
        config.CALLS = True
        try:
            store.subscribe(LAT, LON, phone="818-555-0100", consent=True)
            r = self.run_cycle()
            self.assertEqual(len(r["calls"]), 1)
            to, msg, brief = placed[0]
            self.assertEqual(to, "+18185550100")
            self.assertIn("leave now", msg)
            self.assertIn("Why this level", brief)
            self.assertIn("wind 30 mph", brief)
            r = self.run_cycle()                 # reminder text, no second call
            self.assertEqual(r["calls"], [])
            self.assertEqual(len(placed), 1)
        finally:
            voice.place_call, voice.configured, config.CALLS = orig

    def test_no_call_when_off(self):
        store.subscribe(LAT, LON, phone="818-555-0100", consent=True)
        self.assertEqual(self.run_cycle()["calls"], [])

    def test_dry_run_writes_nothing(self):
        s = store.subscribe(LAT, LON, phone="818-555-0100", consent=True)
        before = json.dumps(store.get(s["id"]), sort_keys=True)
        self.run_cycle(dry_run=True)
        self.assertEqual(json.dumps(store.get(s["id"]), sort_keys=True), before)
        self.assertEqual(self.ch.sent, [])
        self.assertEqual(store.recent_alerts(), [])


try:
    from starlette.testclient import TestClient
    HAVE_WEB = HAVE
except ImportError:
    HAVE_WEB = False


@unittest.skipUnless(HAVE_WEB, "starlette/httpx not installed")
class TestWeb(unittest.TestCase):
    def setUp(self):
        from sentinel import web
        self.web = web
        self.ch = ConsoleChannel()
        set_channel(self.ch)
        self.client = TestClient(web.app)
        self.sub = store.subscribe(LAT, LON, label="secret-home", phone="818-555-0199", consent=True)
        store.update(self.sub["id"], situation={"level": "evacuate", "nearest_fire_km": 2.0,
                     "nearest_fire_name": "Eaton", "fire_bearing_deg": 0},
                     route={"summary": "via <b>I-5</b>", "duration_min": 20, "distance_km": 15,
                            "origin": [34.18, -118.13], "dest": [34.0, -118.1], "dest_label": "x",
                            "computed_at": "2026-10-17T20:00:00+00:00"})

    def test_now_texted_back(self):
        r = self.client.post("/photon/inbound", json={"from": "+18185550199", "text": "NOW"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Route", self.ch.sent[-1][1])

    def test_unknown_number(self):
        self.client.post("/photon/inbound", json={"from": "+19995550000", "text": "NOW"})
        self.assertIn("isn't signed up", self.ch.sent[-1][1])

    def test_voice(self):
        r = self.client.post("/photon/voice", json={"from": "+18185550199", "transcript": "status"})
        self.assertIn("EVACUATE", r.json()["reply"])

    def test_voice_why_gets_briefing(self):
        r = self.client.post("/photon/voice", json={"from": "+18185550199", "transcript": "why do I have to leave?"})
        reply = r.json()["reply"]
        self.assertIn("Alert level: EVACUATE", reply)
        self.assertIn("Route", reply)

    def test_route_page(self):
        self.assertEqual(self.client.get("/route/nope").status_code, 404)
        r = self.client.get(f"/route/{self.sub['token']}")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("<b>I-5", r.text)
        self.assertIn("&lt;b&gt;I-5", r.text)

    def test_dashboard_private(self):
        body = self.client.get("/dashboard.json").text
        self.assertNotIn("818", body)
        self.assertNotIn("secret-home", body)

    def test_signup_is_double_opt_in(self):
        form = {"phone": "818-555-0155", "lat": "34.18", "lon": "-118.13", "consent": "yes"}
        r = self.client.post("/signup", data=form)
        self.assertEqual(r.status_code, 200)
        s = store.find_by_phone("+18185550155")
        self.assertFalse(s["consent"])                      # no texts until YES
        self.assertIn("reply YES", self.ch.sent[-1][1])
        self.client.post("/photon/inbound", json={"from": "+18185550155", "text": "YES"})
        self.assertTrue(store.get(s["id"])["consent"])

    def test_signup_rejects_without_consent_or_outside_area(self):
        base = {"phone": "818-555-0166", "lat": "34.18", "lon": "-118.13"}
        self.assertEqual(self.client.post("/signup", data=base).status_code, 400)
        far = {**base, "lat": "40.7", "lon": "-74.0", "consent": "yes"}
        self.assertEqual(self.client.post("/signup", data=far).status_code, 400)
        self.assertIsNone(store.find_by_phone("+18185550166"))

    def test_signup_cannot_take_over_existing_number(self):
        form = {"phone": "818-555-0199", "lat": "34.5", "lon": "-118.5", "consent": "yes"}
        self.client.post("/signup", data=form)
        self.assertEqual(store.get(self.sub["id"])["lat"], LAT)

    def test_dashboard_redacts_links_and_honors_token(self):
        store.log_alert({"as_of": "x", "level": "evacuate", "channel": "text", "kind": "evacuate",
                         "message": "Map: http://localhost:8080/route/abcdef0123456789 ok"})
        body = self.client.get("/dashboard.json").text
        self.assertNotIn("abcdef0123456789", body)
        self.web.config.DASH_TOKEN = "t0k"
        try:
            self.assertEqual(self.client.get("/dashboard.json").status_code, 401)
            self.assertEqual(self.client.get("/dashboard.json?token=t0k").status_code, 200)
        finally:
            self.web.config.DASH_TOKEN = None

    def test_home_page_serves(self):
        self.assertIn("FireTrace Sentinel", self.client.get("/").text)

    def test_bad_signature(self):
        self.web.config.PHOTON_WEBHOOK_SECRET = "s3cret"
        try:
            r = self.client.post("/photon/inbound", json={"from": "+18185550199", "text": "NOW"},
                                 headers={"X-Photon-Signature": "bad"})
            self.assertEqual(r.status_code, 401)
        finally:
            self.web.config.PHOTON_WEBHOOK_SECRET = None


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE_WEB, "starlette/httpx not installed")
class TestDemoCall(unittest.TestCase):
    """The map's alert demo phones DEMO_PHONE on evacuate and answers the voice agent from the replay."""

    def setUp(self):
        from sentinel import alert_demo, demo_server
        from sentinel.channels import voice
        self.voice, self.demo_server = voice, demo_server
        self.orig = voice.place_call, voice.configured, demo_server.SESSION
        self.placed = []
        voice.place_call = lambda to, msg, brief=None: (self.placed.append((to, msg, brief))
                                                         or {"ok": True, "status": "calling", "conversation_id": "c1"})
        voice.configured = lambda: True
        lat, lon = alert_demo.anchor("eaton_2025")
        demo_server.SESSION = alert_demo.DemoSession(lat, lon, "test home", email="", phone="514-664-6295")
        demo_server.SESSION.email = None
        self.texts = []
        texts = self.texts

        class FakeTexter:
            def send_text(self, to, body):
                texts.append((to, body))
                return {"ok": True, "status": "sent"}
        demo_server.SESSION.texter = FakeTexter()
        self.client = TestClient(demo_server.app)

    def tearDown(self):
        self.voice.place_call, self.voice.configured, self.demo_server.SESSION = self.orig

    def test_evacuate_calls_once_and_answers(self):
        for h in range(10):
            self.client.post("/api/demo/step", json={"hour": h})
        session = self.demo_server.SESSION
        for a in session.events:     # the server sends on threads; run them inline here
            if a.get("text") == "queued":
                session.send_text(a)
            if a.get("call") == "queued":
                session.place_call(a)
        self.assertEqual(len(self.texts), 1)                 # the first warning, not again at evacuate
        self.assertEqual(self.texts[0][0], "+15146646295")
        self.assertIn("WARNING", self.texts[0][1])
        self.assertEqual([a.get("text") for a in session.events][0], "sent")
        self.assertEqual(len(self.placed), 1)
        self.assertEqual(self.placed[0][0], "+15146646295")
        self.assertIn("leave now", self.placed[0][1])
        auth = {"X-Photon-Secret": config.PHOTON_WEBHOOK_SECRET or ""}
        r = self.client.post("/photon/voice", headers=auth, json={"from": "+15146646295", "transcript": "why?"}).json()
        self.assertIn("Alert level: EVACUATE", r["reply"])
        r = self.client.post("/photon/voice", headers=auth, json={"from": "+19995550000", "transcript": "why?"}).json()
        self.assertIn("isn't signed up", r["reply"])
        call = self.client.get("/api/demo/state").json()["call"]
        self.assertEqual(call["status"], "placed")
        self.assertEqual(call["phone"], "••• 6295")
        self.assertEqual(len(call["turns"]), 1)
