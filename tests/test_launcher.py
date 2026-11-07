import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import launch


class LauncherTests(unittest.TestCase):
    def test_launcher_history_does_not_count_as_foreground_app(self):
        client = Mock()
        client.adb_shell.side_effect = [
            '1',
            'ACTIVITY app.lawnchair/.Launcher token\nmResumed=true mStopped=false\n'
            'history: com.lptiyu.tanke/.activities.main.MainActivity',
            'Status: ok\nComplete',
        ]
        result = Mock(returncode=0, stdout='{"is_android_started": true}')
        with patch('launch.subprocess.run', return_value=result), patch('launch.MuMuLocationClient', return_value=client):
            launch.open_target({'root': 'C:/MuMu', 'index': 1})
        client.adb_shell.assert_called_with('am', 'start', '-W', '-n', 'com.lptiyu.tanke/.activities.splash.SplashActivity')
