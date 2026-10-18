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

from routing import engine, geo, graph as road_graph, subscriber_route  # noqa: E402
from routing.config import CLOSURE_BLOCK_KM  # noqa: E402
from sentinel import conversation, escalation, messages, store, windows  # noqa: E402
from sentinel.route_hazard import MARGIN_ESCAPE_H, from_snapshot  # noqa: E402
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


class TestDirectionsConversation(unittest.TestCase):
    """NOW / NEXT / REPEAT / STEPS / MAP walk through a stored route, by text and by voice."""

    STEPS = [{"instruction": "Head west on East Altadena Drive", "maneuver": "depart", "mi": 0.7},
             {"instruction": "Turn left onto Marengo Avenue", "maneuver": "turn-left", "mi": 0.7},
             {"instruction": "Merge onto Foothill Freeway (I-210)", "maneuver": "merge", "mi": 3.2},
             {"instruction": "Arrive at the safe zone", "maneuver": "arrive", "mi": 0}]

    def setUp(self):
        s = store.subscribe(34.19, -118.13, phone="+18185550199", consent=True)
        route = {"summary": "via I-210", "dest_label": "safe area 8 km west", "duration_min": 8, "distance_km": 10,
                 "computed_at": NOW.isoformat(), "directions": self.STEPS, "hazard": "fire",
                 "google_maps_url": "https://www.google.com/maps/dir/?api=1&x"}
        store.update(s["id"], situation={"level": "evacuate", "nearest_fire_km": 3.0, "nearest_fire_name": "Test",
                                         "fire_bearing_deg": 90}, route=route)
        self.id = s["id"]

    def say(self, text, channel="text"):
        return conversation.respond(store.get(self.id), text, NOW.isoformat(), channel)

    def test_intents(self):
        for text, want in {"next": "NEXT", "then what": "NEXT", "repeat": "REPEAT", "what was that": "REPEAT",
                           "directions": "STEPS", "map": "MAP", "how do I get out": "NOW"}.items():
            self.assertEqual(conversation.classify(text), want, text)

    def test_text_walkthrough(self):
        self.assertIn("1) Head west on E Altadena Dr (0.7 mi)", self.say("NOW"))
        self.assertIn("Arrive at the safe zone", self.say("STEPS"))
        store.update(self.id, dir_step=1)
        self.assertIn("Step 2/4: Left on Marengo Ave", self.say("NEXT"))
        self.assertIn("Step 2/4", self.say("REPEAT"))
        self.assertIn("google.com/maps", self.say("MAP"))

    def test_voice_reads_one_step_in_full_words(self):
        first = self.say("how do I get out", "voice")
        self.assertIn("Step 1: Head west on East Altadena Drive", first)
        self.assertIn("Interstate 210", first)
        nxt = self.say("next", "voice")
        self.assertIn("Step 2 of 4: Turn left onto Marengo Avenue", nxt)
        self.assertNotIn(" Ave ", nxt)

    def test_check_alerts_hands_agents_the_way_out(self):
        from sentinel.mcp_server import check_alerts
        store.update(self.id, last_level="evacuate", window={"status": "leave_now"})
        r = check_alerts(self.id)
        self.assertEqual(r["way_out"]["steps"][0], "Head west on East Altadena Drive")
        self.assertEqual(r["way_out"]["leave_by"], "now")
        self.assertIn("/route/", r["way_out"]["map_page"])

    def test_route_change_resets_the_steps(self):
        self.say("NOW")
        r = store.get(self.id)["route"]
        store.update(self.id, route=dict(r, computed_at=(NOW + timedelta(minutes=15)).isoformat()))
        self.assertTrue(self.say("NEXT").startswith("Your route changed"))


class TestRouteChange(unittest.TestCase):
    def test_reordered_roads_and_small_shifts_are_not_news(self):
        from sentinel.live import _route_changed
        a = {"summary": "via West Woodbury Road, I-210", "dest_bearing": 270}
        self.assertFalse(_route_changed(a, {"summary": "via I-210, West Woodbury Road", "dest_bearing": 300}))
        self.assertTrue(_route_changed(a, {"summary": "via Huntington Drive, I-210", "dest_bearing": 270}))
        self.assertTrue(_route_changed(a, {"summary": "via West Woodbury Road, I-210", "dest_bearing": 180}))


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
    """The shared router (agent/routing) with fire as the hazard, on a synthetic street grid."""

    def setUp(self):
        self.g = road_graph.from_osm(grid_elements())

    def route(self, snap, **kw):
        return subscriber_route(snap, 34.0, -118.0, now_iso=NOW.isoformat(), graph_override=self.g, **kw)

    def escape(self, snap, blocks=(), jam=None, margin=MARGIN_ESCAPE_H):
        hz = from_snapshot(snap, look_ahead_h=24, blocks=blocks)
        return engine.escape(self.g, hz, (34.0, -118.0), margin, jam=jam)

    def test_km_between(self):
        self.assertAlmostEqual(geo.km_between(34, -118, 35, -118), 111.2, delta=0.5)

    def test_route_ends_somewhere_the_fire_wont_reach(self):
        snap = fire_snapshot(NORTH_FIRE, 0)                 # wind pushes the fire south, toward the house
        r = self.route(snap)
        self.assertIsNotNone(r)
        self.assertFalse(r["fire_risk"])
        self.assertTrue(r["summary"].startswith("via "))
        self.assertEqual(r["origin"], [34.0, -118.0])
        # Safe ground is 5 mi from the fire.
        self.assertGreaterEqual(geo.km_between(*NORTH_FIRE[0], *r["dest"]), config.ESCAPE_SAFE_KM - 0.5)

    def test_route_carries_directions_and_a_maps_link(self):
        r = self.route(fire_snapshot(NORTH_FIRE, 0))
        self.assertEqual(r["directions"][0]["maneuver"], "depart")
        self.assertEqual(r["directions"][-1]["instruction"], "Arrive at the safe zone")
        self.assertTrue(r["google_maps_url"].startswith("https://www.google.com/maps/dir/?api=1"))
        self.assertTrue(any("SR-" in s["instruction"] or "Row" in s["instruction"] for s in r["directions"]))

    def test_fire_downwind_of_route_start_is_not_driven_toward(self):
        r = self.route(fire_snapshot(NORTH_FIRE, 0))
        self.assertLessEqual(r["dest"][0], 34.0 + 1e-6)     # never heads north into the fire

    def test_search_never_uses_a_road_after_the_fire_gets_there(self):
        r = self.escape(fire_snapshot(NORTH_FIRE, 0))
        self.assertEqual(r["status"], "ok")
        self.assertGreaterEqual(r["min_margin_hours"] or 1e9, MARGIN_ESCAPE_H)

    def test_closure_is_avoided(self):
        snap = fire_snapshot(NORTH_FIRE, 180)
        free = self.escape(snap)
        line = free["path"]["geometry"]["coordinates"]
        blocks = [(lat, lon) for lon, lat in line[len(line) // 3: len(line) // 3 + 3]]
        detour = self.escape(snap, blocks=blocks)
        self.assertEqual(detour["status"], "ok")
        for lon, lat in detour["path"]["geometry"]["coordinates"]:
            for bl, bo in blocks:
                self.assertGreater(geo.km_between(lat, lon, bl, bo), CLOSURE_BLOCK_KM * 0.9)

    def test_surrounded_house_has_no_escape(self):
        ring = [(34.0 + di * 0.01, -118.0 + dj * 0.01) for di in (-1, 0, 1) for dj in (-1, 0, 1) if di or dj]
        self.assertIsNone(self.route(fire_snapshot(ring, 0)))   # no least-bad route: texts fall back to advice

    def test_evacuation_traffic_slows_roads_in_the_fire_zone(self):
        snap = fire_snapshot(NORTH_FIRE, 0)
        hz = from_snapshot(snap, look_ahead_h=24)
        mask = engine.circle_mask(self.g, 34.0, -118.0, 30)
        cond = hz.conditions(self.g, mask, True)
        factor = engine._speed_factor(self.g, cond, hz.depart_h, True)
        house = int(((self.g.nodes[:, 1] - 34.0) ** 2 + (self.g.nodes[:, 0] + 118.0) ** 2).argmin())
        self.assertTrue(any(factor[e] > 1 for e, _, _ in self.g.out[house]))   # the house's own street is jammed
        fast, slow = self.escape(snap, jam=False), self.escape(snap, jam=True)
        self.assertGreaterEqual(slow["eta_minutes"], fast["eta_minutes"])     # it may route around the jam
        self.assertTrue(slow["congestion_modelled"])

    def test_no_road_data(self):
        def boom(lat, lon):
            raise RuntimeError("overpass down")
        self.assertIsNone(road_graph.local_streets(12.34, 56.78, fetch=boom))
        empty = road_graph.from_osm([])
        self.assertIsNone(subscriber_route(fire_snapshot(NORTH_FIRE, 0), 34.0, -118.0, graph_override=empty))


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


class TestSurgeRouting(unittest.TestCase):
    """Storm surge as a routing hazard (surge_sentinel/route_hazard.py) on a street grid over Miami, Irma 2017."""

    @classmethod
    def setUpClass(cls):
        import numpy as np
        from surge_sentinel import route_hazard as srh
        cls.np, cls.srh = np, srh
        cls.g = road_graph.from_osm(grid_elements(lat0=25.80, lon0=-80.20, n=30, step=0.01))

    def conditions(self, hour):
        hz = self.srh.build("replay:irma_2017", hour, at=(25.79, -80.14))
        return hz, hz.conditions(self.g, self.np.ones(len(self.g), bool), True)

    def test_nothing_is_flooded_before_the_storm(self):
        _, cond = self.conditions(0)
        self.assertEqual(int(cond["hard"]["flood"].sum()), 0)       # open water under causeways doesn't count

    def test_forecast_water_reaches_roads_before_the_peak(self):
        _, cond = self.conditions(36)
        arr = cond["arrival"][self.np.isfinite(cond["arrival"])]
        self.assertGreater(len(arr), 0)
        self.assertTrue((arr >= 36).all())                          # never before departure

    def test_wind_caps_the_leave_by_time(self):
        hz, _ = self.conditions(36)
        self.assertIsNotNone(hz.leave_by_cap_h)                      # Irma's 34-kt winds reach Miami in the forecast
        self.assertGreater(hz.leave_by_cap_h, 36)                    # ...after departure, not already

    def test_safe_ground_respects_the_evacuation_zones(self):
        hz, cond = self.conditions(36)
        self.assertTrue(hz.storm.zones)                              # a major hurricane orders zones
        self.assertFalse(cond["safe"].all())


class TestLiveSurgeRouting(unittest.TestCase):
    """Live surge routing follows NWS warnings, and only for active Atlantic storms."""

    def setUp(self):
        import json
        import tempfile
        from pathlib import Path
        import surge_sentinel.config as sc
        self.sc, self.old = sc, sc.LIVE
        self.dir = Path(tempfile.mkdtemp())
        sc.LIVE = self.dir
        self.json = json

    def tearDown(self):
        self.sc.LIVE = self.old

    def write(self, storms, alerts):
        (self.dir / "storms.json").write_text(self.json.dumps(storms))
        (self.dir / "alerts.geojson").write_text(self.json.dumps({"type": "FeatureCollection", "features": alerts}))

    def test_no_atlantic_storm_means_no_live_route(self):
        from surge_sentinel import route_hazard as srh
        self.write([{"id": "ep212026", "name": "Trudy"}], [])                 # Eastern Pacific only
        with self.assertRaises(srh.NoLiveStorm):
            srh.build("live", 0, at=(40.6, -73.8))

    def test_warnings_set_the_clock(self):
        from surge_sentinel import route_hazard as srh
        onset = (NOW.replace(year=2099)).isoformat()                          # far future: lots of lead
        sq = {"type": "Polygon", "coordinates": [[[-74, 40.5], [-73.7, 40.5], [-73.7, 40.7], [-74, 40.7], [-74, 40.5]]]}
        self.write([{"id": "al992026", "name": "Test"}],
                   [{"type": "Feature", "geometry": sq, "properties": {"event": "Hurricane Warning", "onset": onset}}])
        hz = srh.build("live", 0, at=(40.6, -73.8))
        self.assertGreater(hz.leave_by_cap_h, 1000)                           # winds warned from the onset, not now
        self.assertFalse(hz.mapped(10.0, 10.0))
