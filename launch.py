"""Portable community launcher; machine settings are kept outside the release."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import gui
from lepao.mumu import discover_mumu, MuMuLocationClient

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
                                     "direction": route.plan["direction"], "device_mutations": 0}), encoding="utf-8")
        root.destroy()
        return
    settings = configure()
    if not settings:
        return
    os.environ["MUMU_HOME"] = settings["root"]
    os.environ["MUMU_VM_INDEX"] = str(settings["index"])
    open_target(settings)
    gui.main()


def open_target(settings):
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
        command("control", "-v", index, "launch")
    deadline = time.monotonic() + 90
    client = MuMuLocationClient(vm_index=settings["index"], manager_path=manager)
    last_error = "设备未就绪"
    while time.monotonic() < deadline:
        try:
            if command("info", "-v", index).get("is_android_started"):
                if client.adb_shell("getprop", "sys.boot_completed").strip() == "1":
                    if "com.lptiyu.tanke/" not in client.adb_shell("dumpsys", "activity", "top"):
                        client.adb_shell("am", "start", "-n", "com.lptiyu.tanke/.activities.splash.SplashActivity")
                    return
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError("等待目标实例启动超时：" + last_error)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("启动失败", str(exc))
        root.destroy()
