"""Read back positions before motion; restore an idle start after App reopening."""
import time
from .geo import haversine


def require_inner_fix(gps, band):
    point = gps.get("location")
    if not gps.get("enabled") or not point:
        raise RuntimeError("实际 GPS 不可用，停止移动；返回首页重新准备")
    if not band.inner <= band.radial_distance(point) <= band.radius:
        raise RuntimeError("实际 GPS 离开内圈跑道，停止移动；返回首页重新定位")


def confirmed_start(gps, route):
    point = gps.get("location")
    if not gps.get("enabled") or not point:
        return False
    band = route.band
    try:
        require_inner_fix(gps, band)
    except RuntimeError:
        return False
    return haversine(*point, *route.position_at(0)) <= 5


class IdleLocationGuard:
    def __init__(self):
        self.last_attempt = float("-inf")

    def check(self, client, observer, route, now=None):
        now = time.monotonic() if now is None else now
        if route is None or now - self.last_attempt < 5:
            return
        self.last_attempt = now
        if confirmed_start(client.gps_status(), route):
            return
        # Recheck after reading GPS: never relocate a running/paused record.
        if observer.read() != "idle":
            return
        client.ensure_gps_enabled()
        client.set_wgs84(*route.position_at(0))
