import json
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
from lepao.mumu import MuMuGateway, find_adb
from lepao.autolink import resumed_activity, classify_run_views


def output(text='', error='', code=0):
    return SimpleNamespace(stdout=text, stderr=error, returncode=code)


class AndroidCompatibilityTests(unittest.TestCase):
    def client(self):
        client = MuMuGateway.__new__(MuMuGateway)
        client.adb_path = __file__
        client.vm_index = 7
        client.manager = Mock(return_value=json.dumps({'7': {'index': '7', 'adb_port': 17000}}))
        return client

    def test_adb_discovery_accepts_future_version_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            p = pathlib.Path(folder, 'nx_device/99.0/shell/adb.exe')
            p.parent.mkdir(parents=True)
            p.touch()
            self.assertEqual(find_adb([folder]), str(p))
            self.assertIsNone(find_adb([str(p.parent / 'missing')]))

    def test_reconnect_refreshes_selected_instance_endpoint(self):
        client = self.client()
        client._adb_serial = '127.0.0.1:16000'
        client._adb = Mock(side_effect=[output(error="device '127.0.0.1:16000' not found", code=1),
                                       output('connected'), output('35')])
        self.assertEqual(client.adb_shell('getprop', 'ro.build.version.sdk'), '35')
        client.manager.assert_called_once_with('info', '-v', '7')
        self.assertEqual(client._adb.call_args_list[1].args, ('connect', '127.0.0.1:17000'))
        self.assertEqual(client._adb.call_args_list[2].args[:2], ('-s', '127.0.0.1:17000'))

    def test_permission_failure_is_not_retried_or_redirected(self):
        client = self.client()
        client._adb_serial = '127.0.0.1:17000'
        client._adb = Mock(return_value=output(error='SecurityException: Permission denial', code=1))
        with self.assertRaisesRegex(RuntimeError, 'Permission denial'):
            client.adb_shell('cmd', 'location')
        client._adb.assert_called_once()
        client.manager.assert_not_called()

    def test_android15_dump_shutdown_failure_requires_exact_success_marker(self):
        client = self.client()
        client._adb_serial = '127.0.0.1:17000'
        client._adb = Mock(return_value=output('UI hierchary dumped to: /sdcard/probe.xml', code=139))
        self.assertIn('dumped to:', client.adb_shell('uiautomator', 'dump', '/sdcard/probe.xml'))
        with self.assertRaises(RuntimeError):
            client.adb_shell('uiautomator', 'dump', '/sdcard/different.xml')
        client._adb.return_value = output('ERROR: could not get idle state', code=1)
        with self.assertRaises(RuntimeError):
            client.adb_shell('uiautomator', 'dump', '/sdcard/probe.xml')

    def test_missing_location_subcommand_uses_verified_secure_setting(self):
        client = self.client()
        client.adb_shell = Mock(side_effect=['Unknown command', '3'])
        self.assertTrue(client.location_enabled())
        client.adb_shell = Mock(side_effect=['Unknown command', 'null'])
        with self.assertRaises(RuntimeError):
            client.location_enabled()

    def test_enable_fallback_is_read_back_not_assumed_success(self):
        client = self.client()
        client.adb_shell = Mock(return_value='')
        client.location_enabled = Mock(side_effect=[False, True])
        client.ensure_gps_enabled()
        client.adb_shell.assert_any_call('settings', 'put', 'secure', 'location_mode', '3')
        client.location_enabled = Mock(side_effect=[False, False])
        with self.assertRaises(RuntimeError):
            client.ensure_gps_enabled()

    def test_gps_dump_spelling_variations(self):
        for label in ['last location=', 'lastLocation=', 'Last Location: ']:
            client = self.client()
            client.adb_shell = Mock(side_effect=['true', label + 'Location[gps 22.5, 108.4 hAcc=1 satellites=8]'])
            self.assertEqual(client.gps_status(), {'enabled': True, 'location': (22.5, 108.4), 'satellites': 8})

    def test_lifecycle_and_component_formats(self):
        for component in ['.activities.main.MainActivity', 'com.lptiyu.tanke.activities.main.MainActivity']:
            for state in ['mResumed=true mStopped=false', 'mStopped = false\n mResumed = true']:
                dump = 'ACTIVITY com.lptiyu.tanke/' + component + ' token userId=0\n' + state
                self.assertEqual(resumed_activity(dump)[0], '.activities.main.MainActivity')
        dump += '\nACTIVITY other.pkg/.Main token\nmResumed=true mStopped=false'
        self.assertIsNone(resumed_activity(dump)[0])

    def test_package_qualified_view_ids(self):
        views = ('android.View{a V.E...... ........ 0,0-10,10 com.lptiyu.tanke:id/tv_start_pause_continue_run}\n'
                 'android.View{b G.E...... ........ 0,0-0,0 com.lptiyu.tanke:id/tv_stoprun}')
        self.assertEqual(classify_run_views(views), 'running')

    def test_diagnostics_have_no_android_version_upper_limit(self):
        for sdk in [31, 32, 33, 34, 35, 36, 37]:
            client = self.client()
            client.adb_shell = Mock(side_effect=['release', str(sdk), 'x86_64,arm64-v8a', 'versionName=4.2.6', 'true'])
            self.assertTrue(client.device_info()['android_12_or_newer'])
