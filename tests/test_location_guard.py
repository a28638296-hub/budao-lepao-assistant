import json
import pathlib
import random
import sys
import unittest
from unittest.mock import Mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lepao.routes import StadiumBand, PreparedRoute, make_plan
from lepao.location_guard import IdleLocationGuard, confirmed_start, require_inner_fix, settle_start


class LocationGuardTests(unittest.TestCase):
    def test_stationary_bias_is_corrected_without_relaxing_boundary(self):
        self.observer.read.return_value = "idle"
        self.client.position_correction = (0, 0)
        target = self.route.position_at(0)
        bias = (0.00001, 0.00001)
        self.client.gps_status.side_effect = lambda: {"enabled": True, "location": tuple(
            target[i] + self.client.position_correction[i] + bias[i] for i in (0, 1))}
        result = settle_start(self.client, self.observer, self.route, sleep=lambda _: None)
        self.assertTrue(confirmed_start(result, self.route))
        self.assertAlmostEqual(self.client.position_correction[0], -bias[0])

    def test_correction_never_uses_distant_fix_or_active_record(self):
        self.client.position_correction = (0, 0)
        self.observer.read.return_value = "idle"
        with self.assertRaises(RuntimeError):
            settle_start(self.client, self.observer, self.route, sleep=lambda _: None)
        self.assertEqual(self.client.position_correction, (0, 0))
        self.client.reset_mock()
        self.observer.read.return_value = "running"
        with self.assertRaises(RuntimeError):
            settle_start(self.client, self.observer, self.route, sleep=lambda _: None)
        self.client.set_wgs84.assert_not_called()

    def setUp(self):
        cfg = json.loads((ROOT / "config.example.json").read_text(encoding="utf8"))
        self.band = StadiumBand(cfg["run"]["track_band_anchors_wgs84"])
        self.route = PreparedRoute(self.band, make_plan(self.band, 2, rng=random.Random(2)))
        self.client, self.observer = Mock(), Mock()
        self.client.gps_status.return_value = {"enabled": True, "location": (23, 113)}

    def test_reopening_with_wrong_fix_restores_same_prepared_start(self):
        self.observer.read.return_value = "idle"
        guard = IdleLocationGuard()
        guard.check(self.client, self.observer, self.route, 0)
        guard.check(self.client, self.observer, self.route, 1)
        self.client.set_wgs84.assert_called_once_with(*self.route.position_at(0))
        guard.check(self.client, self.observer, self.route, 6)
        self.assertEqual(self.client.set_wgs84.call_count, 2)

    def test_never_jump_after_page_changes_to_recording(self):
        for state in ("running", "paused", "unknown"):
            self.client.reset_mock()
            self.observer.read.return_value = state
            IdleLocationGuard().check(self.client, self.observer, self.route)
            self.client.set_wgs84.assert_not_called()

    def test_close_to_start_but_outside_inner_lane_is_rejected(self):
        phase = self.route.plan["start_phase_m"]
        for offset in (0.5, -3):
            gps = {"enabled": True, "location": self.band.point(phase, offset)}
            self.assertFalse(confirmed_start(gps, self.route))
            with self.assertRaises(RuntimeError): require_inner_fix(gps, self.band)
        self.assertTrue(confirmed_start({"enabled": True, "location": self.route.position_at(0)}, self.route))

    def test_no_fix_is_not_reported_as_ready(self):
        for gps in ({"enabled": False, "location": self.route.position_at(0)},
                    {"enabled": True, "location": None}):
            self.assertFalse(confirmed_start(gps, self.route))
            with self.assertRaises(RuntimeError): require_inner_fix(gps, self.band)

    def test_runtime_stops_on_outside_readback(self):
        from test_campus_boundary import Client, Clock
        from lepao.sim import RunConfig, RunSimulator
        from unittest.mock import patch
        cfg = RunConfig(laps=2, enable_steps=False, start_delay_s=0, hide_window=False,
                        track_band_anchors_wgs84=json.loads((ROOT / "config.example.json").read_text())["run"]["track_band_anchors_wgs84"],
                        gps_noise_m=0, lap_line_drift_m=0)
        client, clock = Client(), Clock()
        client.gps_status = lambda: {"enabled": True, "location": (23, 113)}
        sim = RunSimulator(self.route, client, cfg, logger=lambda _: None)
        with patch("lepao.sim.time.time", clock.time), patch("lepao.sim.time.sleep", clock.sleep):
            with self.assertRaisesRegex(RuntimeError, "离开内圈"):
                sim.run()
        self.assertLess(len(client.points), 10)
