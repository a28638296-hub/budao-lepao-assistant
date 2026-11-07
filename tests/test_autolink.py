import pathlib
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from xml.sax.saxutils import quoteattr

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from lepao.autolink import AutoLink, ScreenObserver, classify_screen, classify_run_views, PACKAGE

MAIN = ".activities.main.MainActivity"
RUN = ".activities.school_run.MapRunActivity"


def screen(*pairs):
    return '<hierarchy>' + ''.join(
        '<node package="%s" resource-id="%s:id/%s" text=%s enabled="true" />' %
        (PACKAGE, PACKAGE, name, quoteattr(label)) for name, label in pairs) + '</hierarchy>'


class AutoLinkTests(unittest.TestCase):
    def test_prestart_map_with_visible_back_is_idle_unless_service_running(self):
        block = (ROOT / 'tests/fixtures/prestart-view-hierarchy.txt').read_text(encoding='utf8')
        self.assertEqual(classify_run_views(block), 'idle')
        dump = 'ACTIVITY com.lptiyu.tanke/' + RUN + ' token\n mResumed=true mStopped=false\n' + block
        client = Mock()
        client.adb_shell.side_effect = [dump, '']
        self.assertEqual(ScreenObserver(client).read(), 'idle')
        client.adb_shell.side_effect = [dump, 'com.lptiyu.tanke/.services.RunService']
        self.assertEqual(ScreenObserver(client).read(), 'unknown')
    def test_real_textprogressbar_views_without_accessibility_text(self):
        for filename, expected in [("run-view-hierarchy.txt", "running"),
                                   ("paused-view-hierarchy.txt", "paused")]:
            raw = (ROOT / "tests/fixtures" / filename).read_text(encoding="utf8")
            # The diagnostic artifact contains only named views. Isolate the
            # complete control subtree rather than invent missing ancestors.
            lines = raw.splitlines()
            start = next(i for i, l in enumerate(lines) if "app:id/ll_run_control}" in l)
            depth = len(lines[start]) - len(lines[start].lstrip())
            end = next(i for i in range(start+1, len(lines))
                       if len(lines[i]) - len(lines[i].lstrip()) <= depth)
            block = '\n'.join(lines[start:end])
            self.assertEqual(classify_run_views(block), expected)
            client = Mock()
            client.adb_shell.return_value = 'ACTIVITY com.lptiyu.tanke/' + RUN + ' token\n mResumed=true mStopped=false\n' + block
            self.assertEqual(ScreenObserver(client).read(), expected)
            client.adb_shell.assert_called_once_with('dumpsys', 'activity', 'top')
            self.assertEqual(classify_run_views('android.View{p G.E...... ........ 0,0-0,0}\n' + block), 'unknown')
            self.assertEqual(classify_run_views(block + '\nandroid.View{p V.E...... ........ 0,0-10,10 app:id/view_mask_count_down}'), 'unknown')

    def test_known_426_controls_and_countdown(self):
        self.assertEqual(classify_screen(screen(("tv_enterRun", "开始乐跑")), MAIN), "idle")
        self.assertEqual(classify_screen(screen(("tv_start_pause_continue_run", "长按暂停")), RUN), "running")
        self.assertEqual(classify_screen(screen(("tv_start_pause_continue_run", "继 续 跑")), RUN), "paused")
        self.assertEqual(classify_screen(screen(("tv_start_pause_continue_run", "长按暂停"),
                                               ("countdown_textview", "3")), RUN), "unknown")

    def test_media_ads_login_and_active_service_do_not_arm(self):
        self.assertEqual(classify_screen(screen(("ad_pause", "暂停")), MAIN), "unknown")
        self.assertEqual(classify_screen(screen(("tv_enterRun", "开始乐跑")), ".LoginActivity"), "unknown")
        self.assertEqual(classify_screen(screen(("tv_enterRun", "开始乐跑")), MAIN,
                         "com.lptiyu.tanke/com.lptiyu.tanke.services.RunService"), "unknown")

    def test_start_once_until_recording_has_ended(self):
        link = AutoLink()
        def observe(state, ready=False, busy=False):
            return link.observe(state, ready=ready, busy=busy)
        self.assertIsNone(observe("running"))  # Opened mid-recording: no teleport/start.
        self.assertIsNone(observe("idle"))
        self.assertEqual(observe("idle"), "prepare")
        self.assertIsNone(observe("idle", ready=True))
        self.assertEqual(observe("running", ready=True), "start")
        self.assertIsNone(observe("running", ready=True))
        self.assertIsNone(observe("unknown", ready=True))
        self.assertIsNone(observe("running", ready=True))  # Stop/completion cannot restart.
        self.assertIsNone(observe("idle", busy=True))
        self.assertEqual(observe("idle", busy=True), "stop")
        self.assertEqual(observe("idle"), "prepare")
        self.assertEqual(observe("running", ready=True), "start")

    def test_preparation_and_pause_do_not_start(self):
        link = AutoLink()
        self.assertIsNone(link.observe("running", ready=True, busy=False, preparing=True))
        self.assertIsNone(link.observe("paused", ready=True, busy=False))
        self.assertEqual(link.observe("running", ready=True, busy=False), "start")

    def test_observer_never_reads_login_or_stale_dump(self):
        client = Mock()
        client.adb_shell.return_value = "ACTIVITY com.lptiyu.tanke/.LoginActivity abc\n mResumed=true mStopped=false"
        self.assertEqual(ScreenObserver(client).read(), "unknown")
        self.assertEqual(client.adb_shell.call_count, 1)
        client.adb_shell.side_effect = ["ACTIVITY com.lptiyu.tanke/" + MAIN + " abc\n mResumed=true mStopped=false",
                                      "ERROR: could not get idle state"]
        with self.assertRaises(RuntimeError):
            ScreenObserver(client).read()

    def test_gui_auto_event_starts_without_button_and_without_relocation(self):
        import tkinter as tk
        import gui
        root = tk.Tk()
        root.withdraw()
        app = gui.App(root)
        try:
            app.prepared_route = Mock()
            app.prepared_key = (app.read_points(), float(app.var_laps.get()))
            with patch.object(app, "on_start") as start, patch.object(app, "on_prepare") as prepare:
                app._handle_screen("running")
                app._handle_screen("running")
                start.assert_called_once_with(automatic=True)
                prepare.assert_not_called()
        finally:
            root.destroy()

    def test_preparation_rechecks_idle_before_changing_location(self):
        import tkinter as tk
        import gui
        root = tk.Tk()
        root.withdraw()
        app = gui.App(root)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                app.route_history_path = str(pathlib.Path(tmp) / "history.json")
                app.client = Mock()
                app.observer = Mock()
                app.observer.read.return_value = "running"
                class ImmediateThread:
                    def __init__(self, target, **kw): self.target = target
                    def start(self): self.target()
                with patch.object(gui.threading, "Thread", ImmediateThread):
                    app.on_prepare(automatic=True)
                app.client.set_wgs84.assert_not_called()
                self.assertIsNone(app.prepared_route)
        finally:
            root.destroy()

    def test_cold_home_prepares_once_then_waits_for_fix_and_auto_starts(self):
        import tkinter as tk
        import gui
        root = tk.Tk()
        root.withdraw()
        app = gui.App(root)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                app.route_history_path = str(pathlib.Path(tmp) / "history.json")
                app.client = Mock()
                app.client.gps_status.return_value = {"enabled": True, "location": None, "satellites": None}
                app.observer = Mock()
                app.observer.read.return_value = "idle"
                class ImmediateThread:
                    def __init__(self, target, **kw): self.target = target
                    def start(self): self.target()
                with patch.object(gui.threading, "Thread", ImmediateThread):
                    app._handle_screen("idle")
                    app._handle_screen("idle")
                while not app.msgq.empty():
                    kind, payload = app.msgq.get_nowait()
                    if kind == "prepared":
                        app._finish_prepare(*payload)
                route = app.prepared_route
                self.assertIsNotNone(route)
                app.client.set_wgs84.assert_called_once_with(*route.position_at(0))
                with patch.object(gui.threading, "Thread") as thread, patch.object(app, "on_save", return_value=True):
                    app._handle_screen("running")
                    thread.assert_not_called()
                    self.assertFalse(app.autolink.consumed)
                    app.client.gps_status.return_value = {"enabled": True, "location": route.position_at(0), "satellites": 7}
                    app._handle_screen("running")
                    thread.return_value.start.assert_called_once()
                app.client.set_wgs84.assert_called_once()
                self.assertIsNone(app.prepared_route)
                self.assertTrue(app.autolink.consumed)
        finally:
            root.destroy()

    def test_paused_simulator_does_not_send_locations_or_catch_up(self):
        from lepao.sim import RunConfig, RunSimulator
        from lepao.track import Track
        from test_campus_boundary import Client, Clock
        clock, client = Clock(), Client()
        cfg = RunConfig(enable_steps=False, hide_window=False, start_delay_s=0,
                        laps=0.03, gps_noise_m=0, lap_line_drift_m=0, corner_radius_m=0,
                        warmup_s=0, traffic_events=False)
        route = Track([(22.75, 108.45), (22.751, 108.45), (22.75, 108.451)], corner_radius_m=0)
        sim = RunSimulator(route, client, cfg, logger=lambda _: None)
        def paused():
            if clock.now < 1010:
                self.assertEqual(client.points, [])
                return True
            return False
        with patch("lepao.sim.time.time", clock.time), patch("lepao.sim.time.sleep", clock.sleep):
            stats = sim.run(pause_flag=paused)
        self.assertLess(stats.distance_m, route.length * cfg.laps + 5)
        self.assertGreaterEqual(stats.started_at, 1010)
        self.assertGreater(len(client.points), 0)


if __name__ == "__main__":
    unittest.main()
