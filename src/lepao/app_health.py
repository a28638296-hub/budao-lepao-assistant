"""Verify sustained App startup instead of trusting an accepted launch command."""
import re
import time

from .autolink import PACKAGE, resumed_activity


def _foreground_component(dump):
    # Prefer the top-resumed field; an older mResumedActivity entry may remain
    # while another application or system page is actually on top.
    for field in ("topResumedActivity", "mResumedActivity"):
        lines = [line for line in dump.splitlines()
                 if re.match(r"\s*" + field + r"\s*[:=]", line)]
        if lines:
            match = re.search(r"([\w.]+)/([\w.$]+)", lines[-1])
            if match:
                package, activity = match.groups()
                if activity.startswith(package + "."):
                    activity = activity[len(package):]
                return package, activity
            return None
    return None


def verify_started(client, *, sleep=time.sleep, clock=time.monotonic):
    """Require one live foreground App process for at least ten seconds."""
    deadline = clock() + 30
    stable = 0
    stable_since = None
    last_pid = None
    backed_out = False
    while clock() < deadline:
        foreground = _foreground_component(
            client.adb_shell("dumpsys", "activity", "activities"))
        details = client.adb_shell("dumpsys", "activity", "top")
        if (not backed_out and foreground
                and foreground[0] == "com.android.settings"
                and "ManageAppExternalSourcesActivity" in foreground[1]
                and re.search(r"package:" + re.escape(PACKAGE) + r"(?=$|[\s}])", details)):
            # Close only this App's install-permission page; never grant it.
            client.adb_shell("input", "keyevent", "4")
            backed_out = True
            stable = 0
            stable_since = last_pid = None
            sleep(1)
            continue

        activity, block = resumed_activity(details)
        pid_match = re.search(r"\bpid=(\d+)\b", block.splitlines()[0]) if block else None
        pid = pid_match.group(1) if pid_match else None
        try:
            processes = client.adb_shell("pidof", PACKAGE).split()
        except RuntimeError:
            processes = []
        # 4.3.1 can briefly have two processes with the same name. Identify the
        # actual resumed Activity's process, not the first result from pidof.
        ready = (foreground == (PACKAGE, activity) and activity
                 and "splash" not in activity.lower() and pid
                 and all(p.isdecimal() for p in processes) and pid in processes)
        if ready:
            if pid != last_pid or stable_since is None:
                stable = 0
                stable_since = clock()
            stable += 1
        else:
            stable = 0
            stable_since = None
        last_pid = pid
        if stable >= 5 and stable_since is not None and clock() - stable_since >= 10:
            return {"foreground": PACKAGE + "/" + activity, "pid": pid,
                    "update_permission_closed": backed_out}
        sleep(1)
    raise RuntimeError(
        "乐跑未进入稳定的可操作页面，可能闪退、反复重启、卡在启动页或被其他窗口遮挡；"
        "尚未启动自动联动。请先单独打开 App 并检查当前版本与模拟器的兼容性，"
        "参阅 docs/APP_UPDATE_RECOVERY.md。")
