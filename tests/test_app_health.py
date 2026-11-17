import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from lepao.app_health import verify_started
from lepao.autolink import PACKAGE

MAIN = ".activities.main.MainActivity"
SPLASH = ".activities.splash.SplashActivity"


class Device:
    def __init__(self, snapshot=lambda now: {}, install_prompt=None):
        self.snapshot = snapshot
        self.install_prompt = install_prompt
        self.now = 0
        self.back_count = 0

    def sleep(self, seconds):
        self.now += seconds

    def clock(self):
        return self.now

    def adb_shell(self, *args):
        state = {"activity": MAIN, "pid": "100", "processes": "100", "resumed": True}
        state.update(self.snapshot(self.now))
        if args == ("input", "keyevent", "4"):
            self.back_count += 1
            return ""
        if self.install_prompt and not self.back_count:
            if args[-1] == "activities":
                return "topResumedActivity=com.android.settings/.Settings$ManageAppExternalSourcesActivity"
            if args[-1] == "top":
                return "Intent { dat=package:" + self.install_prompt + " }"
        if args == ("pidof", PACKAGE):
            if state["processes"] is None:
                raise RuntimeError("No process")
            return state["processes"]
        if args[-1] == "activities":
            return state.get("task_dump", "topResumedActivity=ActivityRecord{token u0 "
                             + PACKAGE + "/" + state["activity"] + " t1}")
        if args[-1] == "top":
            activity = state.get("detail_activity", state["activity"])
            pid = " pid=" + state["pid"] if state["pid"] else ""
            dump = ("ACTIVITY " + PACKAGE + "/" + activity + " token" + pid + "\n"
                    + (" mResumed=true mStopped=false\n" if state["resumed"]
                       else " mResumed=false mStopped=true\n"))
            return dump + state.get("overlay", "")
        raise AssertionError("Unexpected device command: " + repr(args))


class AppHealthTests(unittest.TestCase):
    def run_check(self, device):
        return verify_started(device, sleep=device.sleep, clock=device.clock)

    def test_stable_main_and_login_pages_wait_ten_seconds(self):
        for activity in (MAIN, PACKAGE + MAIN, ".activities.login.LoginActivity"):
            with self.subTest(activity=activity):
                device = Device(lambda now: {"activity": activity})
                result = self.run_check(device)
                self.assertEqual(result["pid"], "100")
                self.assertEqual(device.now, 10)

    def test_changing_secondary_pids_do_not_reset_the_foreground_process(self):
        device = Device(lambda now: {"processes": f"{now + 200} 100" if now < 8 else "100"})
        result = self.run_check(device)
        self.assertEqual(result["pid"], "100")
        self.assertEqual(device.now, 10)

    def test_main_process_restart_needs_ten_new_seconds(self):
        device = Device(lambda now: {"pid": "100" if now < 8 else "101",
                                     "processes": "100 201" if now < 8 else "101 202"})
        self.assertEqual(self.run_check(device)["pid"], "101")
        self.assertEqual(device.now, 18)

    def test_continuous_restarts_do_not_pass(self):
        device = Device(lambda now: {"pid": str(100 + now // 4),
                                     "processes": str(100 + now // 4)})
        with self.assertRaises(RuntimeError):
            self.run_check(device)
        self.assertEqual(device.now, 30)

    def test_live_task_history_splash_and_missing_process_are_rejected(self):
        cases = {
            "splash": {"activity": SPLASH},
            "stale_activity": {"resumed": False},
            "no_process": {"processes": None},
            "dead_foreground_with_live_child": {"processes": "999"},
            "missing_activity_pid": {"pid": None},
            "different_activity": {"detail_activity": SPLASH},
            "different_foreground": {"task_dump": "topResumedActivity=app.lawnchair/.Launcher\n"
                                     "mResumedActivity=" + PACKAGE + "/" + MAIN},
            "resumed_overlay": {"overlay": "ACTIVITY com.android.gpstest.osmdroid/.MainActivity x pid=200\n"
                                  " mResumed=true mStopped=false\n"},
        }
        for name, state in cases.items():
            with self.subTest(case=name):
                device = Device(lambda now: state)
                with self.assertRaises(RuntimeError):
                    self.run_check(device)
                self.assertEqual(device.now, 30)

    def test_legacy_resumed_field_and_full_component_name(self):
        device = Device(lambda now: {"task_dump": "mResumedActivity: ActivityRecord{x u0 "
                                     + PACKAGE + "/" + PACKAGE + MAIN + " t1}"})
        self.assertEqual(self.run_check(device)["foreground"], PACKAGE + "/" + MAIN)

    def test_only_matching_app_install_permission_page_is_closed(self):
        device = Device(install_prompt=PACKAGE)
        result = self.run_check(device)
        self.assertTrue(result["update_permission_closed"])
        self.assertEqual(device.back_count, 1)
        self.assertEqual(device.now, 11)
        for package in ("com.example.other", PACKAGE + ".other"):
            with self.subTest(package=package):
                device = Device(install_prompt=package)
                with self.assertRaises(RuntimeError):
                    self.run_check(device)
                self.assertEqual(device.back_count, 0)


if __name__ == "__main__":
    unittest.main()
