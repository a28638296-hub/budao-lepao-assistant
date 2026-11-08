"""Portable community launcher; machine settings are kept outside the release."""
import json
import os
import subprocess
import sys
import time
import threading
import queue
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import gui
from lepao.mumu import discover_mumu, MuMuLocationClient
from lepao.startup_location import restore_before_open

SETTINGS = Path(gui._appdata_dir()) / "launcher.json"


def configure():
    try:
        saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if saved and "--setup" not in sys.argv and Path(saved.get("root", ""), "nx_main", "MuMuManager.exe").is_file():
        return saved
    root = tk.Tk()
    root.title("首次设置 · LePao Runner Community")
    root.geometry("640x270")
    folder = tk.StringVar(value=saved.get("root") or discover_mumu().get("root") or "")
    instance = tk.StringVar(value=str(saved.get("index", 0)))
    result = {}
    ttk.Label(root, text="选择 MuMu 安装目录和实例编号；请先在 MuMu 多开器启动目标实例。").pack(pady=14)
    row = ttk.Frame(root)
    row.pack(fill="x", padx=15)
    ttk.Entry(row, textvariable=folder, width=60).pack(side="left", fill="x", expand=True)
    ttk.Button(row, text="浏览", command=lambda: folder.set(filedialog.askdirectory() or folder.get())).pack(side="left")
    ttk.Label(root, text="实例编号（主实例通常为 0，多开实例请查询编号）").pack(pady=(15, 4))
    ttk.Entry(root, textvariable=instance, width=10).pack()
    def inspect():
        try:
            p = Path(folder.get()) / "nx_main" / "MuMuManager.exe"
            out = subprocess.run([str(p), "info", "-v", "all"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=15,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            data = json.loads(out.stdout)
            items = data if isinstance(data, list) else ([data] if "index" in data else data.values())
            messagebox.showinfo("实例编号", "\n".join("%s：%s · Android %s" %
                               (i.get("index"), i.get("name"), i.get("android_version", "未知")) for i in items))
        except Exception as exc:
            messagebox.showerror("查询失败", str(exc))
    def save():
        try:
            idx = int(instance.get())
            if idx < 0 or not (Path(folder.get()) / "nx_main" / "MuMuManager.exe").is_file():
                raise ValueError("请选择包含 nx_main/MuMuManager.exe 的目录及非负实例编号")
            result.update(root=folder.get(), index=idx)
            SETTINGS.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            root.destroy()
        except Exception as exc:
            messagebox.showerror("设置有误", str(exc))
    buttons = ttk.Frame(root)
    buttons.pack(pady=16)
    ttk.Button(buttons, text="查询实例编号", command=inspect).pack(side="left", padx=8)
    ttk.Button(buttons, text="保存并打开助手", command=save).pack(side="left", padx=8)
    root.mainloop()
    return result


def main():
    if "--smoke-test" in sys.argv:
        root = tk.Tk()
        root.withdraw()
        app = gui.App(root, auto_watch=False)
        cfg = app.build_cfg(app.read_points())
        from lepao.routes import StadiumBand, PreparedRoute, make_plan
        band = StadiumBand(cfg.track_band_anchors_wgs84)
        route = PreparedRoute(band, make_plan(band, cfg.laps))
        output = Path(sys.argv[sys.argv.index("--smoke-test") + 1])
        output.write_text(json.dumps({"ok": True, "route_points": len(route.path),
                                     "direction": route.plan["direction"], "device_mutations": 0,
                                     "data_dir": gui._appdata_dir(), "version": "2.3.0"}), encoding="utf-8")
        root.destroy()
        return
    if not gui.acquire_single_instance():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("助手已打开", "助手已经运行，请使用现有窗口。", parent=root)
        root.destroy()
        return
    settings = configure()
    if not settings:
        return
    os.environ["MUMU_HOME"] = settings["root"]
    os.environ["MUMU_VM_INDEX"] = str(settings["index"])
    root = tk.Tk()
    root.title("步道乐跑 v2.3.0 · 正在启动")
    root.geometry("620x190")
    status = tk.StringVar(value="正在连接所选 MuMu 实例…")
    ttk.Label(root, textvariable=status, wraplength=580).pack(padx=20, pady=20)
    bar = ttk.Progressbar(root, mode="indeterminate")
    bar.pack(fill="x", padx=20)
    bar.start()
    elapsed = tk.StringVar()
    ttk.Label(root, textvariable=elapsed).pack(pady=12)
    messages = queue.Queue()
    cancelled = threading.Event()
    started = time.monotonic()
    def progress(message):
        if cancelled.is_set():
            raise RuntimeError("启动已取消")
        messages.put(("status", message))
    def work():
        try:
            open_target(settings, progress)
            messages.put(("ready", None))
        except Exception as exc:
            messages.put(("error", str(exc)))
    def close():
        cancelled.set()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", close)
    def poll():
        elapsed.set("已等待 %d 秒；请勿重复打开助手" % (time.monotonic() - started))
        try:
            while True:
                kind, value = messages.get_nowait()
                if kind == "status":
                    status.set(value)
                elif kind == "error":
                    bar.stop()
                    messagebox.showerror("启动未完成", value, parent=root)
                    close()
                    return
                elif kind == "ready":
                    bar.stop()
                    for widget in root.winfo_children():
                        widget.destroy()
                    root.call("tk", "scaling", 1.2)
                    gui.App(root, auto_watch=True)
                    return
        except queue.Empty:
            pass
        root.after(150, poll)
    threading.Thread(target=work, daemon=True).start()
    root.after(100, poll)
    root.mainloop()


def open_target(settings, progress=lambda message: None):
    """Launch the selected device, wait for ADB, then open the App."""
    manager = str(Path(settings["root"], "nx_main", "MuMuManager.exe"))
    def command(*args):
        result = subprocess.run([manager, *args], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        return json.loads(result.stdout)
    index = str(settings["index"])
    info = command("info", "-v", index)
    if not info.get("is_android_started"):
        progress("正在启动所选实例，首次启动可能需要 1～2 分钟…")
        command("control", "-v", index, "launch")
    progress("等待 Android 启动与 ADB 连接…")
    deadline = time.monotonic() + 90
    client = MuMuLocationClient(vm_index=settings["index"], manager_path=manager)
    last_error = "设备未就绪"
    while time.monotonic() < deadline:
        try:
            if command("info", "-v", index).get("is_android_started"):
                if client.adb_shell("getprop", "sys.boot_completed").strip() == "1":
                    break
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
        time.sleep(1)
    else:
        raise RuntimeError("等待目标实例启动超时：" + last_error)
    config = {}
    for path in (gui.config_path(), gui.portable_config_path()):
        if Path(path).is_file():
            config = json.loads(Path(path).read_text(encoding="utf-8"))
            break
    progress("正在读取并校正实际 GPS；可能暂时显示 GPSTest…")
    report = restore_before_open(client, config)
    gui.STARTUP_POSITION_CORRECTION = report.get("correction", (0.0, 0.0))
    if report.get("restored"):
        gui.STARTUP_VERIFIED_AT = time.monotonic()
    if report.get("active_record"):
        return
    progress("正在打开步道乐跑…")
    result = client.adb_shell("am", "start", "-W", "-n", "com.lptiyu.tanke/.activities.splash.SplashActivity")
    if "Error" in result or "Status: ok" not in result:
        raise RuntimeError("乐跑未成功打开：" + result)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("启动失败", str(exc))
        root.destroy()
