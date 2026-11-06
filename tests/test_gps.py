import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from lepao.mumu import MuMuGateway


class GpsTests(unittest.TestCase):
    def test_status_reports_fix_and_satellites(self):
        client = MuMuGateway.__new__(MuMuGateway)
        client.adb_shell = Mock(side_effect=["true", "last location=Location[gps 22.755425,108.446961 hAcc=1 {Bundle[{satellites=7}]}]"])
        status = client.gps_status()
        self.assertTrue(status["enabled"])
        self.assertEqual(status["location"], (22.755425, 108.446961))
        self.assertEqual(status["satellites"], 7)

    def test_refresh_restores_gps_even_when_disable_fails(self):
        client = MuMuGateway.__new__(MuMuGateway)
        client.adb_shell = Mock(side_effect=RuntimeError("connection closed"))
        client.ensure_gps_enabled = Mock()
        with self.assertRaises(RuntimeError):
            client.kick_location()
        client.ensure_gps_enabled.assert_called_once()

    def test_failed_enable_is_not_reported_as_success(self):
        client = MuMuGateway.__new__(MuMuGateway)
        client.adb_shell = Mock(return_value="")
        client.ensure_gps_enabled = Mock(side_effect=RuntimeError("GPS unavailable"))
        with patch("lepao.mumu.time.sleep"), self.assertRaises(RuntimeError):
            client.kick_location()


if __name__ == "__main__":
    unittest.main()
