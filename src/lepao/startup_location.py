"""Verify the saved location before exposing the App to MuMu's cached fix."""
import time

from .autolink import PACKAGE, ScreenObserver, resumed_activity
from .geo import bd09_to_wgs84, gcj02_to_wgs84, haversine
from .routes import StadiumBand


def restore_before_open(client, config, *, sleep=time.sleep, clock=time.monotonic):
    services = client.adb_shell("dumpsys", "activity", "services", PACKAGE)
    if ".services.RunService" in services:
        return {"active_record": True, "restored": False}
    activity, _ = resumed_activity(client.adb_shell("dumpsys", "activity", "top"))
    if activity == ".activities.school_run.MapRunActivity" and ScreenObserver(client).read() != "idle":
        raise RuntimeError("现有跑步页面状态未确认，保留位置；请先返回首页")
    points = config.get("track", {}).get("points_as_entered")
    if not points:
        return {"restored": False, "needs_setup": True}
    kind = config.get("gui", {}).get("coord_system")
    if kind not in ("gcj02", "bd09", "wgs84"):
        raise ValueError("保存的坐标系无效，请重新设置")
    anchors = []
    for lat, lon in points:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("保存的坐标越界")
        if kind == "gcj02":
            lon, lat = gcj02_to_wgs84(lon, lat)
        elif kind == "bd09":
            lon, lat = bd09_to_wgs84(lon, lat)
        anchors.append((lat, lon))
    band = StadiumBand(anchors)
    target = band.point(band.straight / 2, -1)
    component = "com.android.gpstest.osmdroid/com.android.gpstest.ui.MainActivity"
    if not client.adb_shell("pm", "path", "com.android.gpstest.osmdroid").startswith("package:"):
        raise RuntimeError("请先在所选 Android 12 实例安装 GPSTest 的 osmdroid 版本并授予精确定位权限，详见安装教程")
    client.ensure_gps_enabled()
    result = client.adb_shell("am", "start", "-W", "-n", component)
    if "Error" in result or "Status: ok" not in result:
        raise RuntimeError("GPS 诊断组件未成功打开：" + result)
    deadline, matches = clock() + 25, 0
    while clock() < deadline:
        client.set_wgs84(*target)
        sleep(1)
        gps = client.gps_status()
        point = gps.get("location")
        error = haversine(*point, *target) if point else None
        matches = matches + 1 if gps.get("enabled") and error is not None and error < 3 else 0
        if matches >= 3:
            return {"restored": True, "error_m": error,
                    "correction": tuple(target[i] - point[i] for i in (0, 1))}
    raise RuntimeError("25 秒内未确认实际 GPS 回到保存的目标位置，暂不打开乐跑；请检查诊断组件定位权限")
