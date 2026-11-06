import json
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lepao.geo import offset_meters
from lepao.sim import RunConfig, RunSimulator
from lepao.track import ConvexBoundary, Track, stadium_waypoints


class Client:
    def __init__(self):
        self.points = []
        self.visible = False

    def set_wgs84(self, lat, lon):
        self.points.append((lat, lon))

    def unpark_window(self):
        return True

    def show_window(self):
        self.visible = True
        return True

    def park_window(self):
        raise AssertionError("Visible mode must not hide the emulator")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, duration):
        self.now += max(0, duration)


class CampusBoundaryTests(unittest.TestCase):
    def setUp(self):
        data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.points = data["track"]["waypoints_wgs84"]
        self.cfg = RunConfig(**{k: v for k, v in data["run"].items()
                               if k in RunConfig.__dataclass_fields__})
        self.cfg.enable_steps = False  # All checks are offline.
        self.cfg.start_delay_s = 0
        self.boundary = ConvexBoundary(self.cfg.boundary_wgs84)

    def test_entire_route_is_inside_for_both_polygon_orders(self):
        route = Track(self.points, corner_radius_m=self.cfg.corner_radius_m)
        reverse = ConvexBoundary(list(reversed(self.cfg.boundary_wgs84)))
        for i in range(10000):
            point = route.position_at(route.length * i / 10000)
            self.assertTrue(self.boundary.contains(point))
            self.assertTrue(reverse.contains(point))
        self.assertFalse(self.boundary.contains((float("nan"), 108)))

    def test_full_run_stays_inside_and_visible(self):
        self.assertEqual(self.cfg.gps_noise_m, 0)
        self.assertEqual(self.cfg.lap_line_drift_m, 0)
        self.assertFalse(self.cfg.hide_window)
        client, clock = Client(), Clock()
        sim = RunSimulator(Track(self.points), client, self.cfg, logger=lambda _: None)
        with patch("lepao.sim.time.time", clock.time), patch("lepao.sim.time.sleep", clock.sleep):
            sim.run()
        self.assertTrue(client.visible)
        self.assertGreater(len(client.points), 500)
        self.assertTrue(all(self.boundary.contains(p) for p in client.points))
        self.assertGreaterEqual(sim.stats.min_speed, 1000 / (9 * 60))
        self.assertLessEqual(sim.stats.max_speed, 1000 / (6 * 60))

    def test_pace_bounds_cover_start_finish_and_large_jitter(self):
        self.cfg.warmup_s = 60
        self.cfg.cooldown_s = 60
        self.cfg.speed_jitter_pct = 100
        sim = RunSimulator(Track(self.points), Client(), self.cfg, logger=lambda _: None)
        for t in range(1000):
            speed = sim._speed_mps(1, t, 1000 - t)
            self.assertGreaterEqual(1000 / speed / 60, 6 - 1e-10)
            self.assertLessEqual(1000 / speed / 60, 9 + 1e-10)

    def test_stadium_uses_semicircles_and_straights(self):
        # Independent ideal stadium: 40 m radius, 90 m between bend centres.
        from lepao.geo import meters_per_degree
        import math
        origin = (22.75, 108.45)
        mlat, mlon = meters_per_degree(origin[0])
        anchors = [[origin[0] + n / mlat, origin[1] + e / mlon]
                   for e, n in [(0, 85), (40, 45), (40, -45), (0, -85), (-40, -45), (-40, 45)]]
        points = stadium_waypoints(anchors)
        self.assertGreater(len(points), 200)
        for lat, lon in points:
            e, n = (lon - origin[1]) * mlon, (lat - origin[0]) * mlat
            if n > 45:
                self.assertAlmostEqual(math.hypot(e, n - 45), 40, places=5)
            elif n < -45:
                self.assertAlmostEqual(math.hypot(e, n + 45), 40, places=5)
            else:
                self.assertAlmostEqual(abs(e), 40, places=5)

    def test_variable_pace_is_smooth_nonconstant_and_bounded(self):
        import random
        rng = random.Random(20261105)
        self.assertTrue(self.cfg.vary_pace)
        self.assertEqual(self.cfg.speed_jitter_pct, 0)
        for start_pace in (6, 7.5, 9):
            self.cfg.speed_kmh = 60 / start_pace
            with patch("lepao.sim.random.uniform", rng.uniform), patch("lepao.sim.random.choice", rng.choice):
                sim = RunSimulator(Track(self.points), Client(), self.cfg, logger=lambda _: None)
                paces = [1000 / (60 * sim._speed_mps(1, t, 10000)) for t in range(1200)]
            self.assertGreater(max(paces[150:]) - min(paces[150:]), 0.8)
            self.assertTrue(all(6 - 1e-9 <= p <= 9 + 1e-9 for p in paces))
            self.assertLess(max(abs(b - a) for a, b in zip(paces, paces[1:])), 0.1)
            # Both acceleration and deceleration must actually occur.
            self.assertTrue(any(b - a > 0.005 for a, b in zip(paces, paces[1:])))
            self.assertTrue(any(a - b > 0.005 for a, b in zip(paces, paces[1:])))

    def test_random_targets_do_not_force_alternating_directions(self):
        sim = RunSimulator(Track(self.points), Client(), self.cfg, logger=lambda _: None)
        # Independent random choices may slow down twice in a row, then speed up.
        with patch("lepao.sim.random.uniform", side_effect=[60, 7.8, 90, 8.2, 75, 8.6, 65, 6.4]):
            targets, durations = [], []
            for t in (0, 60, 150, 225):
                sim._varying_pace(t)
                targets.append(sim._pace_to)
                durations.append(sim._pace_end_s - sim._pace_start_s)
        self.assertEqual(targets, [7.8, 8.2, 8.6, 6.4])
        self.assertEqual(durations, [60, 90, 75, 65])

    def test_outside_route_is_rejected_before_run(self):
        outside = [offset_meters(*p, 1000, 0) for p in self.points]
        with self.assertRaisesRegex(ValueError, "超出"):
            RunSimulator(Track(outside), Client(), self.cfg)

    def test_outside_output_is_never_sent(self):
        client = Client()
        sim = RunSimulator(Track(self.points), client, self.cfg, logger=lambda _: None)
        with patch("lepao.sim.offset_meters", return_value=(31.49, 120.28)):
            with self.assertRaisesRegex(ValueError, "超出"):
                sim.run()
        self.assertEqual(client.points, [])


if __name__ == "__main__":
    unittest.main()
