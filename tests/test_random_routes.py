from unittest.mock import Mock
import json
import math
import pathlib
import random
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lepao.geo import haversine
from lepao.routes import StadiumBand, PreparedRoute, make_plan, read_history, save_plan, route_offset


class RandomRouteTests(unittest.TestCase):
    def setUp(self):
        cfg = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.band = StadiumBand(cfg["run"]["track_band_anchors_wgs84"])

    def test_random_starts_and_persistent_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "history.json"
            plans = []
            rng = random.Random(125)
            for _ in range(5):
                plan = make_plan(self.band, 5.6, read_history(path), rng)
                save_plan(path, plan, "prepared")
                save_plan(path, plan, "started")
                plans.append(plan)
            self.assertEqual(len(read_history(path)), 5)
            for a, b in zip(plans, plans[1:]):
                self.assertGreater(haversine(*a["start"], *b["start"]), 15)
                self.assertNotEqual(a["wave_phase"], b["wave_phase"])

    def test_all_new_routes_are_geometrically_counterclockwise(self):
        for seed in range(20):
            plan = make_plan(self.band, 1, rng=random.Random(seed))
            self.assertEqual(plan["direction"], -1)
            route = PreparedRoute(self.band, plan)
            xy = [self.band.xy(p) for p in route.path]
            signed_area = sum(a[0] * b[1] - b[0] * a[1]
                              for a, b in zip(xy, xy[1:] + xy[:1])) / 2
            self.assertGreater(signed_area, 100)  # East/north axes: positive is counterclockwise.
        # Independent cardinal-direction check: east side moves north.
        a = self.band.point(self.band.straight / 2, 0)
        b = self.band.point(self.band.straight / 2 - 2, 0)
        self.assertGreater(b[0], a[0])
        plan["direction"] = 1
        with self.assertRaisesRegex(ValueError, "逆时针"):
            PreparedRoute(self.band, plan)

    def test_laps_are_separated_without_jumps_or_grass_crossings(self):
        for seed in range(8):
            plan = make_plan(self.band, 5.6, rng=random.Random(seed))
            route = PreparedRoute(self.band, plan)
            self.assertFalse(route.closed)
            self.assertEqual(route.position_at(route.length + 10), route.path[-1])
            self.assertLess(haversine(*route.position_at(0), *plan["start"]), 0.001)
            for a, b in zip(route.path, route.path[1:]):
                self.assertLess(haversine(*a, *b), 1.1)
                self.band.require_segment(a, b)
                offset = self.band.radial_distance(a) - self.band.radius
                self.assertGreaterEqual(offset, -1.31)
                self.assertLessEqual(offset, -0.69)
            # At the same position around the oval, successive laps have distinct radii.
            for phase in range(0, 360, 15):
                d = self.band.perimeter * phase / 360
                offsets = [route_offset(plan, d, (i + phase / 360) / 5.6, self.band.perimeter)
                           for i in range(5)]
                self.assertTrue(all(abs(b - a) > 0.07 for a, b in zip(offsets, offsets[1:])))

    def test_inner_grass_and_shortcut_are_rejected(self):
        middle = self.band.location(tuple((a + b) / 2 for a, b in zip(self.band.north, self.band.south)))
        with self.assertRaisesRegex(ValueError, "草坪"):
            self.band.require_point(middle)
        a = self.band.point(0, 0)
        b = self.band.point(self.band.perimeter / 2, 0)
        self.band.require_point(a)
        self.band.require_point(b)
        with self.assertRaisesRegex(ValueError, "草坪"):
            self.band.require_segment(a, b)

    def test_later_laps_tolerate_small_readback_residual_without_widening_band(self):
        from lepao.location_guard import require_inner_fix
        from lepao.geo import offset_meters
        # Exercise the entire run, including inward drift on its last laps.
        for seed in range(8):
            route = PreparedRoute(self.band, make_plan(self.band, 5, rng=random.Random(seed)))
            for point in route.path[::5]:
                for angle in range(0, 360, 45):
                    a = math.radians(angle)
                    shifted = offset_meters(*point, 0.6 * math.cos(a), 0.6 * math.sin(a))
                    rounded = tuple(round(v, 6) for v in shifted)
                    require_inner_fix({"enabled": True, "location": rounded}, self.band)

    def test_full_prepared_run_uses_total_length_once(self):
        from unittest.mock import patch
        from lepao.sim import RunConfig, RunSimulator
        from test_campus_boundary import Client, Clock
        cfg = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))["run"]
        cfg = RunConfig(**{k: v for k, v in cfg.items() if k in RunConfig.__dataclass_fields__})
        cfg.laps, cfg.enable_steps, cfg.start_delay_s = 5.6, False, 0
        route = PreparedRoute(self.band, make_plan(self.band, cfg.laps, rng=random.Random(33)))
        client, clock = Client(), Clock()
        sim = RunSimulator(route, client, cfg, logger=lambda _: None)
        with patch("lepao.sim.time.time", clock.time), patch("lepao.sim.time.sleep", clock.sleep):
            stats = sim.run()
        self.assertLess(abs(stats.distance_m - route.length), 5)
        self.assertLess(haversine(*client.points[-1], *route.path[-1]), 0.01)
        for a, b in zip(client.points, client.points[1:]):
            self.band.require_segment(a, b)
        self.assertGreaterEqual(stats.min_speed, 1000 / 540)
        self.assertLessEqual(stats.max_speed, 1000 / 360)

    def test_gui_preparation_is_required_and_start_does_not_relocate(self):
        import tkinter as tk
        from unittest.mock import patch
        sys.path.insert(0, str(ROOT))
        import gui

        class LocationClient:
            def __init__(self):
                self.points = []
            def set_wgs84(self, *point):
                self.points.append(point)
            def kick_location(self):
                return True
            def ensure_gps_enabled(self):
                return True
            def gps_status(self):
                return {"enabled": True, "location": self.points[-1], "satellites": 7}

        root = tk.Tk()
        root.withdraw()
        app = gui.App(root)
        client = LocationClient()
        app.client = client
        try:
            with tempfile.TemporaryDirectory() as tmp:
                app.route_history_path = str(pathlib.Path(tmp) / "history.json")
                with patch.object(gui.messagebox, "showerror") as error:
                    app.on_start()
                    self.assertTrue(error.called)
                    self.assertEqual(client.points, [])
                app.observer = Mock()
                app.observer.read.return_value = "idle"
                from lepao.location_guard import settle_start
                with patch.object(gui, "settle_start", side_effect=lambda c,o,r,cancel: settle_start(c,o,r,cancel,sleep=lambda _: None)):
                    app.on_prepare()
                self.assertEqual(len(client.points), 2)
                prepared = app.prepared_route
                self.assertEqual(client.points[0], prepared.position_at(0))
                app.on_check()
                self.assertEqual(len(client.points), 2)
                with patch.object(gui.threading, "Thread") as thread, patch.object(app, "on_save", return_value=True):
                    app.on_start()
                    thread.return_value.start.assert_called_once()
                self.assertEqual(len(client.points), 2)  # No second random start / teleport.
                self.assertIsNone(app.prepared_route)
                self.assertEqual(read_history(app.route_history_path)[-1]["status"], "started")
        finally:
            root.destroy()

    def test_history_cross_volume_fallback_preserves_previous_records(self):
        import errno
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "history.json"
            first = make_plan(self.band, 5.6, rng=random.Random(1))
            save_plan(path, first, "prepared")
            second = make_plan(self.band, 5.6, read_history(path), rng=random.Random(2))
            with patch.object(pathlib.Path, "replace", side_effect=OSError(errno.EXDEV, "cross volume")):
                save_plan(path, second, "prepared")
                self.assertEqual(len(read_history(path)), 2)
                save_plan(path, second, "started")
            self.assertEqual(len(read_history(path)), 2)
            self.assertEqual(read_history(path)[-1]["status"], "started")
            self.assertTrue(path.with_suffix(".backup.json").exists())

    def test_history_staging_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "history.json"
            previous = make_plan(self.band, 5.6, rng=random.Random(3))
            path.with_suffix(".tmp").write_text(json.dumps([previous]), encoding="utf-8")
            self.assertEqual(read_history(path)[0]["id"], previous["id"])
            save_plan(path, previous, "prepared")
            self.assertEqual(len(read_history(path)), 1)


if __name__ == "__main__":
    unittest.main()
