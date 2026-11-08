import itertools
import unittest
from unittest.mock import MagicMock

import gui
from lepao.startup_location import restore_before_open
from lepao.routes import StadiumBand


class StartupLocationTests(unittest.TestCase):
    def setUp(self):
        self.config = {"gui": {"coord_system": "gcj02"},
                       "track": {"points_as_entered": gui.DEFAULT_POINTS_GCJ}}
        anchors = [gui.to_wgs84(*p, "gcj02")[::-1] for p in gui.DEFAULT_POINTS_GCJ]
        band = StadiumBand(anchors)
        self.target = band.point(band.straight / 2, -1)
        self.client = MagicMock()
        self.client.adb_shell.side_effect = lambda *a: "package:/app.apk" if a[0] == "pm" else "Status: ok"

    def run_restore(self):
        return restore_before_open(self.client, self.config, sleep=lambda _: None,
                                   clock=itertools.count().__next__)

    def test_three_consecutive_fixes_required(self):
        good = {"enabled": True, "location": self.target}
        self.client.gps_status.side_effect = [good, {"enabled": True, "location": (0, 0)}, good, good, good]
        self.assertTrue(self.run_restore()["restored"])
        self.assertEqual(self.client.gps_status.call_count, 5)

    def test_stale_or_missing_fix_blocks_open(self):
        for point in [(0, 0), None]:
            self.client.gps_status.return_value = {"enabled": True, "location": point}
            with self.assertRaisesRegex(RuntimeError, "25 秒"):
                self.run_restore()

    def test_correction_is_applied_and_read_back_before_open(self):
        bias = (0.00001, 0.00001)
        actual = [None]
        def send(lat, lon):
            actual[0] = tuple((lat, lon)[i] + self.client.position_correction[i] + bias[i]
                              for i in (0, 1))
        self.client.set_wgs84.side_effect = send
        self.client.gps_status.side_effect = lambda: {"enabled": True, "location": actual[0]}
        report = self.run_restore()
        self.assertTrue(report["restored"])
        self.assertLess(report["error_m"], 0.01)
        self.assertGreaterEqual(self.client.gps_status.call_count, 6)
        for i in (0, 1):
            self.assertAlmostEqual(report["correction"][i], -bias[i], places=10)

    def test_existing_record_is_untouched(self):
        self.client.adb_shell.side_effect = None
        self.client.adb_shell.return_value = ".services.RunService"
        self.assertTrue(self.run_restore()["active_record"])
        self.client.set_wgs84.assert_not_called()
        self.client.ensure_gps_enabled.assert_not_called()

    def test_missing_component_fails_before_relocating(self):
        self.client.adb_shell.side_effect = lambda *a: ""
        with self.assertRaisesRegex(RuntimeError, "GPSTest"):
            self.run_restore()
        self.client.set_wgs84.assert_not_called()

    def test_first_launch_without_config_can_configure(self):
        self.config = {}
        self.assertTrue(self.run_restore()["needs_setup"])
        self.client.set_wgs84.assert_not_called()
