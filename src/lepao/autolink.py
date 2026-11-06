"""Read-only LePao screen detection and one-shot run coordination."""
import re
import threading
import xml.etree.ElementTree as ET

PACKAGE = "com.lptiyu.tanke"


def classify_run_views(block):
    """4.2.6 TextProgressBar draws its text, so accessibility text is empty.

    Observed on-device: running has a visible pause bar and GONE stop bar;
    paused has both bars visible. Use the view tree without waiting for idle.
    """
    states = {}
    ancestors = []
    for line in block.splitlines():
        match = re.match(r"(\s*)[\w.$]+\{\S+ ([VIG])\S* .*", line)
        if not match:
            continue
        depth, visible = len(match[1]), match[2] == "V"
        while ancestors and ancestors[-1][0] >= depth:
            ancestors.pop()
        visible = visible and all(v for _, v in ancestors)
        ancestors.append((depth, visible))
        rid = re.search(r"app:id/(\w+)", line)
        if rid:
            states[rid[1]] = (match[2], visible)
            if "countdown" in rid[1].lower().replace("_", "") and visible:
                return "unknown"
    control = states.get("tv_start_pause_continue_run")
    stop = states.get("tv_stoprun")
    if control and control[1] and stop:
        if stop[1]:
            return "paused"
        if stop[0] == "G":
            return "running"
    return "unknown"


def classify_screen(xml, activity, services=""):
    if activity not in (".activities.main.MainActivity", ".activities.school_run.MapRunActivity"):
        return "unknown"
    root = ET.fromstring(xml)
    nodes = [n for n in root.iter("node") if n.get("package") == PACKAGE
             and n.get("enabled", "true") == "true"]
    if any("countdown" in n.get("resource-id", "").lower()
           and n.get("text", "").strip() in {"1", "2", "3", "GO", "Go"} for n in nodes):
        return "unknown"
    controls = [n for n in nodes if n.get("resource-id") == PACKAGE + ":id/tv_start_pause_continue_run"]
    labels = {re.sub(r"\s+", "", n.get(k, "")) for n in controls for k in ("text", "content-desc")}
    if labels & {"继续跑", "继续跑步", "继续运动", "继续乐跑", "继续"}:
        return "paused"
    if labels & {"长按暂停", "暂停", "暂停跑步", "暂停运动", "暂停乐跑"}:
        return "running"
    if any(n.get("resource-id") == PACKAGE + ":id/tv_enterRun"
           and n.get("text") == "开始乐跑" for n in nodes) and "com.lptiyu.tanke/.services.RunService" not in services and "com.lptiyu.tanke/com.lptiyu.tanke.services.RunService" not in services:
        return "idle"
    return "unknown"


class ScreenObserver:
    def __init__(self, client):
        self.client = client
        self.lock = threading.Lock()

    def read(self):
        # Never save screen contents or inspect login screens.
        with self.lock:
            dump = self.client.adb_shell("dumpsys", "activity", "top")
            activity = None
            active_block = ""
            for block in re.split(r"(?m)^\s*ACTIVITY ", dump)[1:]:
                match = re.match(r"com\.lptiyu\.tanke/(\S+)", block)
                if match and re.search(r"mResumed=true\s+mStopped=false", block):
                    activity = match[1]
                    active_block = block
            if activity not in (".activities.main.MainActivity", ".activities.school_run.MapRunActivity"):
                return "unknown"
            if activity == ".activities.school_run.MapRunActivity":
                # The clock refreshes continuously: `uiautomator dump` waits for
                # idle and times out here even though the recording is active.
                return classify_run_views(active_block)
            path = "/sdcard/codex-lepao-watch.xml"
            result = self.client.adb_shell("uiautomator", "dump", path)
            if "dumped to" not in result:
                raise RuntimeError("暂时无法读取乐跑界面")
            xml = self.client.adb_shell("cat", path)
            services = self.client.adb_shell("dumpsys", "activity", "services", PACKAGE)
            return classify_screen(xml, activity, services)


class AutoLink:
    """Only a confirmed idle screen rearms a completed/stopped recording."""
    def __init__(self):
        self.idle_samples = 0
        self.consumed = False

    def observe(self, state, *, ready, busy, preparing=False):
        self.idle_samples = self.idle_samples + 1 if state == "idle" else 0
        if state == "idle" and self.idle_samples >= 2:
            if busy:
                return "stop"
            self.consumed = False
            if not ready and not preparing:
                return "prepare"
        if state == "running" and not busy and not preparing and ready and not self.consumed:
            self.consumed = True
            return "start"
        return None
