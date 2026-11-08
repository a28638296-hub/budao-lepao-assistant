import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import gui


class PortableConfigTests(unittest.TestCase):
    def test_fixed_folder_ignores_different_roaming_profiles(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "portable-mode.txt").touch()
            expected = os.path.join(folder, "user-data", "config.json")
            with patch.object(gui, "HERE", folder):
                for profile in ("profile-a", "profile-b"):
                    with patch.dict(os.environ, {"APPDATA": os.path.join(folder, profile)}):
                        self.assertEqual(gui.config_path(), expected)
            self.assertTrue(Path(expected).parent.is_dir())

    def test_without_marker_keeps_roaming_default(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(gui, "HERE", folder), patch.dict(os.environ, {"APPDATA": folder}):
                self.assertEqual(gui.config_path(), os.path.join(folder, "LePaoRunnerCommunity", "config.json"))
