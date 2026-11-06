import pathlib
import sys
import tkinter as tk
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gui


class PortableTests(unittest.TestCase):
    def test_custom_coordinates_update_route_and_boundary_together(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.App(root)
            before = app.build_cfg(app.read_points())
            for entry in app.pt_lat:
                value = float(entry.get()) + 0.01
                entry.delete(0, 'end')
                entry.insert(0, str(value))
            after = app.build_cfg(app.read_points())
            self.assertNotEqual(before.track_band_anchors_wgs84, after.track_band_anchors_wgs84)
            self.assertNotEqual(before.boundary_wgs84, after.boundary_wgs84)
            from lepao.routes import StadiumBand, PreparedRoute, make_plan
            from lepao.sim import RunSimulator
            band = StadiumBand(after.track_band_anchors_wgs84)
            route = PreparedRoute(band, make_plan(band, after.laps))
            RunSimulator(route, None, after, logger=lambda _: None)
        finally:
            root.destroy()

    def test_auto_link_requires_explicit_coordinate_confirmation(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.App(root, auto_watch=True)
            self.assertFalse(app.var_auto_enabled.get())
            app._handle_screen('idle')
            app._handle_screen('idle')
            self.assertFalse(app.preparing)
            self.assertIsNone(app.prepared_route)
        finally:
            root.destroy()
