"""Read back positions before motion; restore an idle start after App reopening."""
import time
import statistics
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


def settle_start(client, observer, route, cancelled=lambda: False, sleep=time.sleep):
    """Calibrate small emulator conversion bias while stationary, never in a record.

    Correction stays on this client only, with a hard 3 m cap. Large/stale fixes
    cannot become a correction, and read-back boundary checks remain unchanged.
    """
    target = route.position_at(0)
    gps = {}
    for attempt in range(4):
        if cancelled() or observer.read() != "idle":
            raise RuntimeError("已离开待跑页面，取消起点校正；请返回首页")
        client.set_wgs84(*target)
        samples = []
        for _ in range(3):
            sleep(1)
            if cancelled():
                raise RuntimeError("助手已关闭，取消校正")
            gps = client.gps_status()
            point = gps.get("location")
            if gps.get("enabled") and point and haversine(*point, *target) < 5:
                samples.append(point)
        if len(samples) != 3:
            continue
        centre = tuple(statistics.median(p[i] for p in samples) for i in (0, 1))
        if max(haversine(*p, *centre) for p in samples) > 0.5:
            continue
        if haversine(*centre, *target) < 0.35 and confirmed_start(gps, route):
            return gps
        if attempt == 3:
            break
        correction = tuple(client.position_correction[i] + target[i] - centre[i] for i in (0, 1))
        corrected = tuple(target[i] + correction[i] for i in (0, 1))
        if haversine(*target, *corrected) > 3:
            raise RuntimeError("定位补偿超过 3 米，停止校正；请核对跑道坐标和 GPS")
        if observer.read() != "idle":
            raise RuntimeError("乐跑已经开始，取消起点校正")
        client.position_correction = correction
    raise RuntimeError("实际 GPS 尚未稳定落在起点，请保持待跑页面后重新准备")


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
