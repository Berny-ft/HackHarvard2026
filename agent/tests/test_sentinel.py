import os
import tempfile
import unittest

# Must precede any `sentinel` import, or tests write to the real state folder.
os.environ["SENTINEL_STATE"] = tempfile.mkdtemp(prefix="sentinel-test-")
os.environ.pop("PHOTON_WEBHOOK_SECRET", None)
os.environ.pop("PHOTON_PROJECT_ID", None)

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel import conversation, escalation, messages, routing, store, windows  # noqa: E402
from sentinel import config  # noqa: E402
from sentinel.channels import ConsoleChannel, set_channel  # noqa: E402

NOW = datetime(2026, 10, 17, 20, 0, tzinfo=timezone.utc)


def sub(**kw):
    base = {"consent": True, "phone": "+18185550100", "stage": "none", "stage_at": None,
            "reminders": 0, "left_at": None}
    return {**base, **kw}


class TestEscalation(unittest.TestCase):
    def test_no_threat_no_text(self):
        self.assertEqual(escalation.next_action(sub(stage="warned"), "none", NOW), (None, "none"))

    def test_warn_once(self):
        self.assertEqual(escalation.next_action(sub(), "warn", NOW), ("warn", "warned"))
        self.assertEqual(escalation.next_action(sub(stage="warned"), "warn", NOW), (None, "warned"))

    def test_evacuate_upgrades_from_warn(self):
        self.assertEqual(escalation.next_action(sub(stage="warned"), "evacuate", NOW), ("evacuate", "evacuated"))

    def test_reminder_interval_and_cap(self):
        recent = (NOW - timedelta(minutes=2)).isoformat()
        old = (NOW - timedelta(minutes=config.REMINDER_EVERY_MIN + 1)).isoformat()
        self.assertEqual(escalation.next_action(sub(stage="evacuated", stage_at=recent), "evacuate", NOW)[0], None)
        self.assertEqual(escalation.next_action(sub(stage="evacuated", stage_at=old), "evacuate", NOW)[0], "reminder")
        capped = sub(stage="evacuated", stage_at=old, reminders=config.MAX_REMINDERS)
        self.assertIsNone(escalation.next_action(capped, "evacuate", NOW)[0])

    def test_left_stops_everything(self):
        s = sub(stage="evacuated", left_at=NOW.isoformat())
        self.assertIsNone(escalation.next_action(s, "evacuate", NOW)[0])

    def test_no_consent_no_text(self):
        self.assertIsNone(escalation.next_action(sub(consent=False), "evacuate", NOW)[0])


class TestClassify(unittest.TestCase):
    def test_intents(self):
        cases = {"NOW": "NOW", "route": "NOW", "I'm leaving now": "NOW", "LEFT": "LEFT", "I've left": "LEFT",
                 "window": "WINDOW", "When should I leave?": "WINDOW", "status": "STATUS", "STOP": "STOP",
                 "Unsubscribe": "STOP", "?": "HELP", "help": "HELP", "not leaving": "FREEFORM",
                 "what about my dog": "FREEFORM"}
        for text, want in cases.items():
            self.assertEqual(conversation.classify(text), want, text)


class TestWindows(unittest.TestCase):
    def test_latest_departure(self):
        w = windows.departure_window(NOW, 5.0, 40, True)
        self.assertEqual(w["status"], "window")
        want = NOW + timedelta(hours=5) - timedelta(minutes=40 * 1.5 + 20)
        self.assertEqual(datetime.fromisoformat(w["latest"]), want)

    def test_leave_now_and_confidence(self):
        w = windows.departure_window(NOW, 1.0, 60, True)
        self.assertEqual(w["status"], "leave_now")
        self.assertEqual(w["confidence"], "high")
        self.assertEqual(windows.departure_window(NOW, 6.0, 30, True)["confidence"], "medium")
        self.assertEqual(windows.departure_window(NOW, 6.0, 30, False)["confidence"], "low")

    def test_far_eta_gives_no_window(self):
        self.assertIsNone(windows.departure_window(NOW, 40.0, 30, True))

    def test_no_eta(self):
        self.assertIsNone(windows.departure_window(NOW, None, 30, True))


SIT = {"level": "evacuate", "nearest_fire_km": 2.0, "nearest_fire_name": "Eaton", "fire_bearing_deg": 45}


class TestMessages(unittest.TestCase):
    def test_evacuate_text(self):
        t = messages.evacuate_text({"situation": SIT})
        self.assertIn("Leave now", t)
        self.assertIn("NOW", t)
        self.assertIn("NE", t)

    def test_warn_window_is_estimate(self):
        w = windows.departure_window(NOW, 5.0, 40, True)
        t = messages.warn_text({"situation": {**SIT, "level": "warn"}, "window": w})
        self.assertIn("Estimate only", t)
        self.assertIn("not an order", t)

    def test_route_reply_links_token(self):
        r = {"summary": "via I-210", "duration_min": 31.2, "distance_km": 18.0, "origin": [1, 1], "dest": [2, 2],
             "dest_label": "x", "computed_at": NOW.isoformat()}
        t = messages.route_reply({"route": r, "token": "abc123"})
        self.assertIn("/route/abc123", t)
        self.assertIn("31 min", t)

    def test_warn_never_says_leave_now(self):
        w = windows.departure_window(NOW, 0.5, 40, True)          # leave_now window at warn level
        t = messages.warn_text({"situation": {**SIT, "level": "warn"}, "window": w})
        self.assertNotIn("leave now", t.lower())
        r = messages.window_reply({"situation": {**SIT, "level": "warn"}, "window": w})
        self.assertNotIn("leave now", r.lower())

    def test_route_fallback(self):
        self.assertIn("away from the fire", messages.route_reply({"route": None, "token": "t"}))


def grid_elements(lat0=34.0, lon0=-118.0, n=30, step=0.01):
    """A square street grid centred on (lat0, lon0), as Overpass-style elements."""
    els, nid = [], {}
    for i in range(n):
        for j in range(n):
            nid[i, j] = len(nid) + 1
            els.append({"type": "node", "id": nid[i, j], "lat": lat0 + (i - n // 2) * step,
                        "lon": lon0 + (j - n // 2) * step})
    for i in range(n):
        els.append({"type": "way", "id": 1000 + i, "nodes": [nid[i, j] for j in range(n)],
                    "tags": {"highway": "primary", "name": f"Row {i}"}})
        els.append({"type": "way", "id": 2000 + i, "nodes": [nid[j, i] for j in range(n)],
                    "tags": {"highway": "primary", "ref": f"SR-{i}"}})
    return els


def fire_snapshot(points, wind_dir_deg):
    import numpy as np
    from sentinel.snapshot import Fires, Snapshot
    pts = np.array(points)
    fires = Fires(pts[:, 0], pts[:, 1], np.full(len(pts), 0.5), ["satellite"] * len(pts), ["Test Fire"] * len(pts))
    wx = {"temp_f": 85, "rh_pct": 8, "wind_mph": 25, "gust_mph": 40, "wind_dir_deg": wind_dir_deg,
          "fuel_moisture_1h_pct": 4, "santa_ana_level": 2}
    return Snapshot(as_of=NOW.isoformat(), fires=fires, weather=lambda a, b: wx,
                    outlook=lambda a, b: {"peak_santa_ana_level": 2}, red_flag=lambda a, b: None)


NORTH_FIRE = [(34.03, -118.0)]          # 3 km north of the house


class TestRouting(unittest.TestCase):
    def setUp(self):
        self.g = routing.build_graph(grid_elements())

    def route(self, snap, **kw):
        return routing.best_route(snap, 34.0, -118.0, now_iso=NOW.isoformat(), graph=self.g, **kw)

    def test_km_between(self):
        self.assertAlmostEqual(routing.km_between(34, -118, 35, -118), 111.2, delta=0.5)

    def test_route_ends_somewhere_the_fire_wont_reach(self):
        snap = fire_snapshot(NORTH_FIRE, 0)                 # wind pushes the fire south, toward the house
        r = self.route(snap)
        self.assertIsNotNone(r)
        self.assertFalse(r["fire_risk"])
        eta = routing.fire_eta_hours(snap, (34.0, -118.0), [r["dest"][0]], [r["dest"][1]])
        self.assertGreaterEqual(eta[0], routing.SAFE_ETA_H)
        self.assertGreaterEqual(routing.km_between(34.0, -118.0, *r["dest"]), routing.MIN_ESCAPE_KM)
        self.assertTrue(r["summary"].startswith("via "))
        self.assertEqual(r["origin"], [34.0, -118.0])

    def test_fire_downwind_of_route_start_is_not_driven_toward(self):
        snap = fire_snapshot(NORTH_FIRE, 0)
        r = self.route(snap)
        self.assertLessEqual(r["dest"][0], 34.0 + 1e-6)     # never heads north into the fire

    def test_search_never_uses_a_node_after_the_fire_gets_there(self):
        snap = fire_snapshot(NORTH_FIRE, 0)
        eta = routing.fire_eta_hours(snap, (34.0, -118.0), self.g.lat, self.g.lon)
        src = int(((self.g.lat - 34.0) ** 2 + (self.g.lon + 118.0) ** 2).argmin())
        dest, t, prev = routing._search(self.g, src, eta, [False] * len(eta), True, routing.MARGIN_H)
        self.assertIsNotNone(dest)
        self.assertGreaterEqual(eta[dest], routing.SAFE_ETA_H)
        self.assertGreater(eta[dest], t + routing.MARGIN_H)

    def test_closure_is_avoided(self):
        snap = fire_snapshot(NORTH_FIRE, 180)
        free = self.route(snap)
        blocks = [tuple(p) for p in free["waypoints"][:3]]
        detour = self.route(snap, blocks=blocks)
        self.assertIsNotNone(detour)
        for wl, wo in detour["waypoints"]:
            for bl, bo in blocks:
                self.assertGreater(routing.km_between(wl, wo, bl, bo), routing.BLOCK_KM)

    def test_surrounded_house_flags_risk(self):
        ring = [(34.0 + di * 0.01, -118.0 + dj * 0.01) for di in (-1, 0, 1) for dj in (-1, 0, 1) if di or dj]
        r = self.route(fire_snapshot(ring, 0))
        self.assertTrue(r is None or r["fire_risk"])

    def test_evacuation_traffic_slows_the_route(self):
        snap = fire_snapshot(NORTH_FIRE, 0)
        fast = self.route(snap, zone_jam=False)
        slow = self.route(snap, zone_jam=True)
        self.assertGreater(slow["duration_min"], fast["duration_min"])
        self.assertTrue(slow["congestion_modelled"])

    def test_no_road_data_returns_none(self):
        def boom(lat, lon):
            raise RuntimeError("overpass down")
        self.assertIsNone(routing.load_graph(12.34, 56.78, fetch=boom, attempts=1))
        self.assertIsNone(routing.best_route(fire_snapshot(NORTH_FIRE, 0), 34.0, -118.0,
                                             graph=routing.Graph([], [], [])))


class TestStoreAndReplies(unittest.TestCase):
    def test_phone_normalization(self):
        self.assertEqual(store.normalize_phone("(818) 555-0100"), "+18185550100")
        self.assertEqual(store.normalize_phone("1-818-555-0100"), "+18185550100")
        self.assertEqual(store.normalize_phone("+447700900123"), "+447700900123")

    def test_find_and_replies(self):
        s = store.subscribe(34.1, -118.1, phone="818-555-0111", consent=True)
        self.assertEqual(store.find_by_phone("+18185550111")["id"], s["id"])
        self.assertEqual(store.find_by_token(s["token"])["id"], s["id"])
        conversation.respond(s, "LEFT", NOW.isoformat())
        self.assertTrue(store.get(s["id"])["left_at"])
        conversation.respond(s, "STOP", NOW.isoformat())
        self.assertFalse(store.get(s["id"])["consent"])

    def test_now_does_not_set_left(self):
        s = store.subscribe(34.1, -118.1, phone="818-555-0122", consent=True)
        s = store.update(s["id"], situation=SIT, route={
            "summary": "via I-5", "duration_min": 25, "distance_km": 20, "origin": [1, 1], "dest": [2, 2],
            "dest_label": "x", "computed_at": NOW.isoformat()})
        reply = conversation.respond(s, "NOW", NOW.isoformat())
        self.assertIn("via I-5", reply)
        self.assertIsNone(store.get(s["id"])["left_at"])


if __name__ == "__main__":
    unittest.main()
