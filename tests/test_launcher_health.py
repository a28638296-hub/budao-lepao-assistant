import pathlib
import sys
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import launch


class LauncherHealthTests(unittest.TestCase):
    def invoke(self, client, report=None, health_error=None, restore_error=None):
        manager = Mock(returncode=0, stdout='{"is_android_started": true}')
        with patch("launch.subprocess.run", return_value=manager), \
             patch("launch.MuMuLocationClient", return_value=client), \
             patch("launch.Path.is_file", return_value=False), \
             patch("launch.restore_before_open", return_value=report or {"restored": True},
                   side_effect=restore_error), \
             patch("launch.verify_started", side_effect=health_error) as verify:
            launch.open_target({"root": "C:/MuMu", "index": 1})
            return verify

    def test_first_setup_without_saved_coordinates_still_checks_the_app(self):
        client = Mock()
        client.adb_shell.side_effect = ["1", "Status: ok", ""]
        verify = self.invoke(client, {"restored": False, "needs_setup": True})
        verify.assert_called_once_with(client)
        self.assertEqual(client.adb_shell.call_args,
                         call("am", "force-stop", "com.android.gpstest.osmdroid"))

    def test_accepted_launch_followed_by_crash_is_reported_and_diagnostic_closed(self):
        client = Mock()
        client.adb_shell.side_effect = ["1", "Status: ok", ""]
        with self.assertRaisesRegex(RuntimeError, "not stable"):
            self.invoke(client, health_error=RuntimeError("not stable"))
        self.assertEqual(client.adb_shell.call_args,
                         call("am", "force-stop", "com.android.gpstest.osmdroid"))

    def test_failed_launch_is_not_masked_by_cleanup_failure(self):
        client = Mock()
        client.adb_shell.side_effect = ["1", "Error: not found", RuntimeError("cleanup failed")]
        with self.assertRaisesRegex(RuntimeError, "乐跑未成功打开"):
            self.invoke(client)

    def test_failed_gps_check_also_closes_diagnostic(self):
        client = Mock()
        client.adb_shell.side_effect = ["1", ""]
        with self.assertRaisesRegex(RuntimeError, "GPS timeout"):
            self.invoke(client, restore_error=RuntimeError("GPS timeout"))
        self.assertEqual(client.adb_shell.call_args,
                         call("am", "force-stop", "com.android.gpstest.osmdroid"))

    def test_active_record_bypasses_app_start_and_diagnostic_cleanup(self):
        client = Mock()
        client.adb_shell.return_value = "1"
        verify = self.invoke(client, {"active_record": True, "restored": False})
        verify.assert_not_called()
        client.adb_shell.assert_called_once_with("getprop", "sys.boot_completed")


if __name__ == "__main__":
    unittest.main()
