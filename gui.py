#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""步道乐跑 · MuMu 后台代跑助手（图形界面）。"""
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

def app_dir():
    """打包成 exe 后以 exe 所在目录为工作目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = app_dir()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from lepao.geo import bd09_to_wgs84, gcj02_to_wgs84, haversine  # noqa: E402
from lepao.mumu import MuMuLocationClient, discover_mumu  # noqa: E402
from lepao.sensors import cadence_for_speed  # noqa: E402
from lepao.sim import RunConfig, RunSimulator  # noqa: E402
from lepao.track import Track, stadium_waypoints  # noqa: E402
from lepao.routes import StadiumBand, PreparedRoute, make_plan, read_history, save_plan  # noqa: E402
from lepao.autolink import AutoLink, ScreenObserver  # noqa: E402
from lepao.location_guard import IdleLocationGuard, confirmed_start

HERE = APP_DIR
CFG_PATH = os.path.join(HERE, "config.json")
_MUTEX = None

def _appdata_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "LePaoRunnerCommunity")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def portable_config_path():
    return os.path.join(HERE, "config.json")


def config_path():
    """正式存档固定放 %APPDATA%\\LePaoRunner\\config.json。

    这样重新打包 exe、换目录、甚至删掉 dist 都不会丢配置；
    如果 exe 同目录存在 config.json，则把它当作便携覆盖（优先读取，保存时同步更新）。
    """
    return os.path.join(_appdata_dir(), "config.json")



def acquire_single_instance():
    """单实例锁：防止同时开两份导致步数脉冲翻倍（曾因此把 App 摇白屏）。"""
    global _MUTEX
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        _MUTEX = k32.CreateMutexW(None, False, "Local\\\\LePaoRunner_SingleInstance_v2")
        ERROR_ALREADY_EXISTS = 183
        if k32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        return True
    except Exception:
        return True

# 本机默认：南宁学院田径场 6 个边界近似点（GCJ-02）。
# 点位依据见 deployment/track-map/selected-points.json。
DEFAULT_POINTS_GCJ = [
    [22.7529525, 108.4513986],
    [22.7525766, 108.4517419],
    [22.7517752, 108.4516668],
    [22.7514486, 108.4512591],
    [22.7518345, 108.4509158],
    [22.7526359, 108.4509909],
]

SYS_OPTIONS = {
    "高德/腾讯地图 (GCJ-02)": "gcj02",
    "百度地图 (BD-09)": "bd09",
    "手机GPS/谷歌地球 (WGS-84)": "wgs84",
}


def to_wgs84(lat, lon, kind):
    if kind == "gcj02":
        return gcj02_to_wgs84(lon, lat)
    if kind == "bd09":
        return bd09_to_wgs84(lon, lat)
    return lon, lat


class App:
    def __init__(self, root, auto_watch=False):
        self.root = root
        self.sim = None
        self.worker = None
        self.stop_flag = threading.Event()
        self.msgq = queue.Queue()
        self.disc = discover_mumu()
        self.client = None
        self.prepared_route = None
        self.prepared_key = None
        self.auto_watch = auto_watch
        self.autolink = AutoLink()
        self.observer = None
        self.preparing = False
        self.closing = threading.Event()
        self.pause_flag = threading.Event()
        self.auto_retry_at = 0.0
        self.last_screen = None
        self.last_confirmed_at = time.monotonic()
        self.route_history_path = os.path.join(_appdata_dir(), "route_history.json")
        self._build()
        self.root.after(120, self._drain)
        self._log("环境探测：")
        self._log("  MuMu 目录   : %s" % (self.disc.get("root") or "未找到"))
        self._log("  MuMuManager : %s" % (self.disc.get("manager") or "未找到"))
        self._log("  adb         : %s" % (self.disc.get("adb") or "未找到"))
        self._log("  网关端口    : %s" % self.disc.get("gateway_port"))
        if not self.disc.get("manager"):
            self._log("警告：没找到 MuMuManager.exe，请确认已安装 MuMu 模拟器。")
        if self.auto_watch:
            self._log("自动联动已开启：乐跑首页自动准备，正式开始记录后自动运行。")
            self.root.after(500, self._poll_screen)

    # ---------- UI ----------
    def _build(self):
        r = self.root
        r.title("步道乐跑 · 社区版 v2.2.1")
        width = min(760, r.winfo_screenwidth() - 60)
        height = min(900, r.winfo_screenheight() - 100)
        r.geometry("%dx%d+20+20" % (width, height))
        r.minsize(min(620, width), min(480, height))
        canvas = tk.Canvas(r, highlightthickness=0)
        scroll = ttk.Scrollbar(r, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        content = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        r = content

        pad = dict(padx=10, pady=4)
        head = ttk.Label(r, text="操场环形路线：北端 → 西北 → 西南 → 南端 → 东南 → 东北",
                         font=("Microsoft YaHei UI", 10, "bold"))
        head.pack(anchor="w", **pad)

        frm = ttk.Frame(r)
        frm.pack(fill="x", **pad)
        ttk.Label(frm, text="纬度", width=8, anchor="center").grid(row=0, column=1)
        ttk.Label(frm, text="经度", width=16, anchor="center").grid(row=0, column=2)
        self.pt_lat, self.pt_lon = [], []
        for i in range(6):
            ttk.Label(frm, text="点 %d" % (i + 1), width=6).grid(row=i + 1, column=0, pady=2)
            e1 = ttk.Entry(frm, width=16)
            e2 = ttk.Entry(frm, width=20)
            e1.insert(0, "%.6f" % DEFAULT_POINTS_GCJ[i][0])
            e2.insert(0, "%.6f" % DEFAULT_POINTS_GCJ[i][1])
            e1.grid(row=i + 1, column=1, padx=6, pady=2)
            e2.grid(row=i + 1, column=2, padx=6, pady=2)
            self.pt_lat.append(e1)
            self.pt_lon.append(e2)

        cs = ttk.Frame(r)
        cs.pack(fill="x", **pad)
        ttk.Label(cs, text="坐标系").pack(side="left")
        self.csys = ttk.Combobox(cs, values=list(SYS_OPTIONS.keys()), state="readonly", width=26)
        self.csys.current(0)
        self.csys.pack(side="left", padx=8)
        ttk.Label(cs, text="（按你复制坐标的来源选）").pack(side="left")

        opt = ttk.LabelFrame(r, text="跑步参数")
        opt.pack(fill="x", **pad)
        g = ttk.Frame(opt)
        g.pack(fill="x", padx=8, pady=6)

        ttk.Label(g, text="起始配速 分钟/公里（6～9）").grid(row=0, column=0, sticky="w", pady=4)
        self.var_speed = tk.StringVar(value="7.5")
        ttk.Entry(g, textvariable=self.var_speed, width=10).grid(row=0, column=1, padx=8)

        ttk.Label(g, text="跑几圈").grid(row=0, column=2, sticky="w", padx=(16, 0))
        self.var_laps = tk.StringVar(value="5")
        ttk.Entry(g, textvariable=self.var_laps, width=8).grid(row=0, column=3, padx=8)

        ttk.Label(g, text="下发频率 Hz").grid(row=1, column=0, sticky="w", pady=4)
        self.var_hz = tk.StringVar(value="1.0")
        ttk.Entry(g, textvariable=self.var_hz, width=10).grid(row=1, column=1, padx=8)

        ttk.Label(g, text="启动方式" if self.auto_watch else "开始前等待 秒").grid(row=1, column=2, sticky="w", padx=(16, 0))
        self.var_delay = tk.StringVar(value="5")
        if self.auto_watch:
            ttk.Label(g, text="跟随乐跑").grid(row=1, column=3, padx=8)
        else:
            ttk.Entry(g, textvariable=self.var_delay, width=8).grid(row=1, column=3, padx=8)

        ttk.Label(g, text="步频 步/分（0=自动,上限84）").grid(row=2, column=0, sticky="w", pady=4)
        self.var_cad = tk.StringVar(value="0")
        ttk.Entry(g, textvariable=self.var_cad, width=10).grid(row=2, column=1, padx=8)

        self.var_steps = tk.BooleanVar(value=True)
        ttk.Checkbutton(g, text="模拟步数/运动状态（强烈建议开启）",
                        variable=self.var_steps).grid(row=2, column=2, columnspan=2, sticky="w", padx=(16, 0))
        self.var_hide = tk.BooleanVar(value=False)
        ttk.Checkbutton(g, text="后台运行：把模拟器窗口移到屏幕外",
                        variable=self.var_hide).grid(row=3, column=2, columnspan=2, sticky="w", padx=(16, 0))
        self.var_kick = tk.BooleanVar(value=False)
        ttk.Checkbutton(g, text="保底刷新（轨迹不动时才勾）", variable=self.var_kick).grid(row=4, column=2, columnspan=2, sticky="w", padx=(16, 0))
        ttk.Label(opt, text="逆时针 · 跑道内侧分圈移线 · 内外边界保护").pack(anchor="w", padx=8, pady=4)
        ttk.Label(opt, text="随机配速 6～9 分钟/公里 · 随机间隔 60～100 秒 · 平滑过渡").pack(anchor="w", padx=8, pady=2)

        btns = ttk.Frame(r)
        btns.pack(fill="x", **pad)
        self.btn_prepare = ttk.Button(btns, text="准备随机起点", command=self.on_prepare)
        self.btn_start = ttk.Button(btns, text="开始代跑", command=self.on_start, state="disabled")
        if not self.auto_watch:
            self.btn_prepare.pack(side="left", ipadx=4, ipady=8)
            self.btn_start.pack(side="left", padx=6, ipadx=4, ipady=8)
        else:
            ttk.Label(btns, text="自动跟随乐跑", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left", padx=6)
        self.btn_stop = ttk.Button(btns, text="■  停止", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4, ipadx=4, ipady=8)
        ttk.Button(btns, text="自检", command=self.on_check).pack(side="left", padx=8, ipady=8)
        ttk.Button(btns, text="保存默认", command=self.on_save).pack(side="left", padx=8, ipady=8)
        self.var_auto_enabled = tk.BooleanVar(value=False)
        if self.auto_watch:
            ttk.Checkbutton(r, text="启用自动联动（核对坐标后勾选，点保存默认可记住；示例为南宁学院）", variable=self.var_auto_enabled).pack(anchor="w", padx=10)
        self.route_status = tk.StringVar(value="自动联动：打开乐跑首页，起点就绪后直接点“开始乐跑”" if self.auto_watch else
                                        "先结束上一条乐跑记录 → 准备起点 → 乐跑开始记录 → 开始代跑")
        ttk.Label(r, textvariable=self.route_status, wraplength=650).pack(anchor="w", padx=10, pady=4)

        btns2 = ttk.Frame(r)
        btns2.pack(fill="x", **pad)
        ttk.Button(btns2, text="隐藏模拟器（后台）", command=self.on_hide_mumu).pack(side="left", ipadx=10, ipady=5)
        ttk.Button(btns2, text="显示模拟器（看进度）", command=self.on_show_mumu).pack(side="left", padx=8, ipadx=10, ipady=5)
        self.var_pause_when_shown = tk.BooleanVar(value=False)
        ttk.Checkbutton(btns2, text="查看时暂停摇一摇（窗口不抖）",
                        variable=self.var_pause_when_shown).pack(side="left", padx=(16, 0))

        self.pb = ttk.Progressbar(r, mode="determinate", maximum=100)
        self.pb.pack(fill="x", padx=12, pady=(2, 6))

        box = ttk.LabelFrame(r, text="运行日志")
        box.pack(fill="both", expand=True, **pad)
        self.txt = tk.Text(box, height=14, wrap="word", font=("Consolas", 9))
        self.txt.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, command=self.txt.yview)
        sb.pack(side="right", fill="y")
        self.txt.config(yscrollcommand=sb.set)

        self._apply_config(self._load_config())
        if not self.auto_watch:
            self.root.bind("<Return>", lambda e: self.on_start())
        self.root.bind("<Escape>", lambda e: self.on_stop())
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- helpers ----------
    def get_client(self):
        if self.client is None:
            self.client = MuMuLocationClient(gateway_port=self.disc.get("gateway_port"),
                                             vm_index=self.disc.get("vm_index", 0),
                                             manager_path=self.disc.get("manager"),
                                             adb_path=self.disc.get("adb"))
        return self.client

    def _log(self, msg):
        self.msgq.put(("log", "[%s] %s" % (time.strftime("%H:%M:%S"), msg)))

    def _drain(self):
        try:
            while True:
                kind, payload = self.msgq.get_nowait()
                if kind == "log":
                    self.txt.insert("end", payload + "\n")
                    self.txt.see("end")
                elif kind == "progress":
                    self.pb["value"] = payload
                elif kind == "screen":
                    self._handle_screen(*payload)
                    if not self.closing.is_set():
                        self.root.after(1000, self._poll_screen)
                elif kind == "prepared":
                    self._finish_prepare(*payload)
                elif kind == "done":
                    self.btn_start["state"] = "disabled"
                    self.btn_prepare["state"] = "normal"
                    self.btn_stop["state"] = "disabled"
                    self.pb["value"] = 0
                    self.route_status.set("本次已停止；请在乐跑结束记录，返回首页后将自动准备下次起点" if self.auto_watch else
                                          "本次已停止；先在乐跑结束记录，下次重新准备随机起点")
        except queue.Empty:
            pass
        self.root.after(150, self._drain)

    def _get_observer(self):
        if self.observer is None:
            self.observer = ScreenObserver(self.get_client())
        return self.observer

    def _poll_screen(self):
        if self.closing.is_set():
            return
        if not self.var_auto_enabled.get():
            if self.worker and self.worker.is_alive():
                self.on_stop()
            self.root.after(1000, self._poll_screen)
            return
        observer = self._get_observer()
        route = self.prepared_route
        if not hasattr(self, "idle_location_guard"):
            self.idle_location_guard = IdleLocationGuard()
        def poll():
            try:
                state, error = observer.read(), None
                if state == "idle" and not self.preparing and not (self.worker and self.worker.is_alive()):
                    self.idle_location_guard.check(self.get_client(), observer, route)
            except Exception as exc:
                state, error = "unknown", str(exc)
            self.msgq.put(("screen", (state, error)))
        threading.Thread(target=poll, daemon=True).start()

    def _handle_screen(self, state, error=None):
        if self.closing.is_set():
            return
        if self.auto_watch and not self.var_auto_enabled.get():
            return
        busy = bool(self.worker and self.worker.is_alive())
        if state in ("running", "paused", "idle"):
            self.last_confirmed_at = time.monotonic()
        if state != self.last_screen:
            self._log("乐跑状态：" + {"idle": "首页待跑", "running": "记录中", "paused": "暂停中",
                                       "unknown": "等待可识别的乐跑页面"}[state])
            self.last_screen = state
        if busy:
            if state == "paused" or (state == "unknown" and time.monotonic() - self.last_confirmed_at > 15):
                self.pause_flag.set()
                self.route_status.set("已暂停移动；返回乐跑跑步页面并继续后自动恢复")
            elif state == "running":
                self.pause_flag.clear()
                self.route_status.set("已自动运行：逆时针 · 随机配速 6～9 分钟/公里")
        if self.prepared_route is not None and state == "idle":
            try:
                if self.prepared_key != (self.read_points(), float(self.var_laps.get())):
                    self.prepared_route = None
                    self.prepared_key = None
            except ValueError:
                return
        action = self.autolink.observe(state, ready=self.prepared_route is not None,
                                       busy=busy, preparing=self.preparing)
        if action == "prepare" and time.monotonic() >= self.auto_retry_at:
            self.on_prepare(automatic=True)
        elif action == "start":
            self.on_start(automatic=True)
        elif action == "stop":
            self.on_stop()
        elif state == "running" and not busy and not self.preparing and not self.autolink.consumed:
            self.route_status.set("此记录开始前尚未准备起点；请结束记录并返回首页，等待自动准备")
        elif error and not busy and not self.preparing:
            self.route_status.set("等待模拟器连接：" + error[:100])

    def read_anchors(self):
        kind = SYS_OPTIONS[self.csys.get()]
        pts = []
        for i in range(6):
            try:
                lat = float(self.pt_lat[i].get().strip())
                lon = float(self.pt_lon[i].get().strip())
            except ValueError:
                raise ValueError("点 %d 的经纬度不是数字" % (i + 1))
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("点 %d 超出经纬度范围" % (i + 1))
            lo, la = to_wgs84(lat, lon, kind)
            pts.append([la, lo])
        return pts

    def read_points(self):
        return stadium_waypoints(self.read_anchors())

    def build_cfg(self, points):
        pace = float(self.var_speed.get())
        laps = float(self.var_laps.get())
        hz = float(self.var_hz.get())
        delay = float(self.var_delay.get() or "0")
        cad = int(float(self.var_cad.get()))
        if not (6.0 <= pace <= 9.0):
            raise ValueError("目标配速请填 6～9 分钟/公里（例如 7.5 表示 7分30秒/公里）")
        speed = 60.0 / pace
        if laps <= 0 or laps > 200:
            raise ValueError("圈数请填 0~200")
        return RunConfig(
            speed_kmh=speed, laps=laps, update_hz=max(0.2, min(5.0, hz)),
            start_delay_s=max(0.0, delay), cadence=max(0, cad),
            enable_steps=self.var_steps.get(), hide_window=self.var_hide.get(),
            kick_every_s=20.0 if self.var_kick.get() else 0.0,
            gps_noise_m=0.0, lap_line_drift_m=0.0,
            corner_radius_m=0.0, warmup_s=0.0, cooldown_s=0.0, traffic_events=False,
            min_pace_min_km=6.0, max_pace_min_km=9.0, vary_pace=True,
            speed_jitter_pct=0.0, pace_transition_min_s=60.0, pace_transition_max_s=100.0,
            track_band_anchors_wgs84=self.read_anchors(),
            boundary_wgs84=stadium_waypoints(
                self.read_anchors(), margin_m=5.0),
        )

    # ---------- actions ----------

    # ---------- 配置读写 ----------
    def _load_config(self):
        # %APPDATA% 为主存档（重新打包/换目录都不丢），exe 同目录作为回退
        cands = [config_path(), portable_config_path()]
        for p in cands:
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self.cfg_path = p
                self._log("已载入配置：%s" % p)
                return data
            except Exception:
                continue
        self.cfg_path = config_path()
        self._log("未找到历史配置，使用默认值（保存位置：%s）" % self.cfg_path)
        return {}

    def _apply_config(self, cfg):
        gui = cfg.get("gui", {}) or {}
        # Only a saved explicit confirmation may enable the next launch.
        self.var_auto_enabled.set(bool(gui.get("auto_link_confirmed", False)) and
                                  bool(cfg.get("track", {}).get("points_as_entered")))
        kind = gui.get("coord_system")
        if kind:
            for label, val in SYS_OPTIONS.items():
                if val == kind:
                    self.csys.set(label)
                    break
        track = cfg.get("track", {}) or {}
        pts = track.get("points_as_entered") or track.get("waypoints_wgs84")
        if isinstance(pts, list) and len(pts) >= 6:
            for i in range(6):
                la, lo = float(pts[i][0]), float(pts[i][1])
                self.pt_lat[i].delete(0, "end")
                self.pt_lat[i].insert(0, "%.6f" % la)
                self.pt_lon[i].delete(0, "end")
                self.pt_lon[i].insert(0, "%.6f" % lo)
        run = cfg.get("run", {}) or {}
        if run.get("speed_kmh", 0) > 0:
            self.var_speed.set("%g" % (60.0 / run["speed_kmh"]))
        pairs = ((self.var_laps, "laps", "%g"),
                 (self.var_hz, "update_hz", "%g"), (self.var_delay, "start_delay_s", "%g"),
                 (self.var_cad, "cadence", "%d"))
        for var, key, fmt in pairs:
            if key in run and run[key] is not None:
                var.set(fmt % float(run[key]) if fmt != "%d" else "%d" % int(run[key]))
        for var, key in ((self.var_steps, "enable_steps"), (self.var_hide, "hide_window"),
                         (self.var_kick, "kick_enabled"), (self.var_pause_when_shown, "pause_when_shown")):
            if key in run:
                var.set(bool(run[key]))

    # ---------- 模拟器窗口 显示 / 隐藏 ----------
    def _feeder(self):
        return getattr(self.sim, "feeder", None) if self.sim else None

    def on_hide_mumu(self):
        try:
            cli = self.get_client()
            if cli.park_window():
                self._log("已把模拟器窗口移到屏幕外（后台运行）")
                f = self._feeder()
                if f is not None:
                    f.resume()
            else:
                self._log("隐藏失败：没找到模拟器窗口（MuMu 是否在运行？）")
        except Exception as exc:  # noqa: BLE001
            self._log("隐藏失败：%s" % exc)

    def on_show_mumu(self):
        try:
            cli = self.get_client()
            ok = cli.unpark_window()
            if not ok:
                try:
                    cli.manager("control", "-v", str(cli.vm_index), "show_window")
                    cli.set_not_topmost()
                    ok = True
                except Exception:
                    ok = False
            if ok:
                self._log("已把模拟器窗口放回屏幕")
                if self.var_pause_when_shown.get():
                    f = self._feeder()
                    if f is not None:
                        f.pause()
                        self._log("已暂停摇一摇（窗口不抖）；点「隐藏模拟器」会自动恢复")
            else:
                self._log("显示失败：没找到模拟器窗口")
        except Exception as exc:  # noqa: BLE001
            self._log("显示失败：%s" % exc)

    def on_check(self):
        try:
            points = self.read_points()
        except ValueError as exc:
            messagebox.showerror("参数有误", str(exc))
            return
        track = Track(points, corner_radius_m=0.0)
        cad = cadence_for_speed(60.0 / float(self.var_speed.get()))
        self._log("自检：跑道周长 %.1f m，共 %d 个顶点" % (track.length, len(points)))
        self._log("自检：预计步频 %d 步/分" % cad)

        self._log("自检为只读检查，不改变当前位置，也不发送步数")
        def device_check():
            try:
                info = self.get_client().device_info()
                self._log("设备：Android %s / API %s | 架构 %s | App %s | 位置开关 %s" %
                          (info["android"], info["sdk"], info["abi"], info["app_version"] or "未安装",
                           "已开启" if info["location_enabled"] else "未开启"))
                if not info["android_12_or_newer"]:
                    self._log("此项目面向 Android 12 及以上；当前系统低于该范围")
                self._log("设备接口可读；App 启动和跑步页面兼容性仍需单独确认")
            except Exception as exc:
                self._log("设备自检失败：%s" % exc)
        threading.Thread(target=device_check, daemon=True).start()

    def on_prepare(self, automatic=False):
        if self.preparing or (self.worker and self.worker.is_alive()):
            return
        self.prepared_route = None
        self.btn_start["state"] = "disabled"
        try:
            points = self.read_points()
            cfg = self.build_cfg(points)
        except Exception as exc:
            self._finish_prepare(None, None, None, exc, automatic)
            return
        self.preparing = True
        self.route_status.set("正在自动准备随机起点并确认 GPS，请稍候…" if automatic else "正在准备起点…")
        cli = self.get_client()
        observer = self._get_observer() if automatic else None

        def prepare():
            try:
                band = StadiumBand(cfg.track_band_anchors_wgs84)
                RunSimulator(Track(points, corner_radius_m=0), None, cfg, logger=lambda _: None)
                history = read_history(self.route_history_path)
                plan = make_plan(band, cfg.laps, history or [{"start": points[0]}])
                route = PreparedRoute(band, plan)
                RunSimulator(route, None, cfg, logger=lambda _: None)
                save_plan(self.route_history_path, plan, "reserved")
                cli.ensure_gps_enabled()
                # Recheck immediately before relocating. Never move an existing recording
                # to a new start merely because the assistant has just been opened.
                if self.closing.is_set() or (observer and observer.read() != "idle"):
                    raise RuntimeError("乐跑已离开待跑首页，取消移动起点；返回首页后自动重试")
                cli.address = "南宁学院田径场－本次随机起点"
                cli.set_wgs84(*route.position_at(0))
                cli.kick_location()
                deadline = time.monotonic() + 10
                while True:
                    if self.closing.is_set():
                        raise RuntimeError("助手已关闭，取消准备")
                    gps = cli.gps_status()
                    if confirmed_start(gps, route):
                        break
                    if automatic and gps["enabled"]:
                        # On a cold launch, the home screen may have no GPS listener.
                        # Keep this acknowledged start; verify the fix before motion.
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("GPS 尚未确认新起点定位，请保持乐跑首页打开")
                    time.sleep(0.5)
                save_plan(self.route_history_path, plan, "prepared")
                return route, (points, cfg.laps), gps, None, automatic
            except Exception as exc:
                return None, None, None, exc, automatic

        if automatic:
            def work():
                self.msgq.put(("prepared", prepare()))
            threading.Thread(target=work, daemon=True).start()
        else:
            self._finish_prepare(*prepare())

    def _finish_prepare(self, route, key, gps, error, automatic):
        self.preparing = False
        if self.closing.is_set():
            return
        if error:
            self.auto_retry_at = time.monotonic() + 10
            self.route_status.set("准备未完成，将自动重试：" + str(error) if automatic else "准备失败，请检查后重试")
            self._log("准备失败：%s" % error)
            if not automatic:
                messagebox.showerror("准备路线失败", str(error))
            return
        self.prepared_route, self.prepared_key = route, key
        self.btn_start["state"] = "normal"
        self.route_status.set("实际 GPS 已确认内圈起点" if confirmed_start(gps, route) else
                              "起点已下发；等待 App 请求 GPS，尚未确认实际位置")
        if confirmed_start(gps, route):
            self._log("GPS 已开启，起点误差 %.1f 米，卫星数 %s" %
                      (haversine(*gps["location"], *route.position_at(0)), gps.get("satellites")))
        else:
            self._log("起点已下发、GPS 已开启；乐跑申请定位后自动确认起点，再开始移动。")
        self._log("已准备逆时针路线：起点随机，内侧每圈平滑错开，预计 %.2f km" % (route.length / 1000))

    def on_save(self, silent=False):
        """把界面上的所有内容存盘；下次打开自动读回。"""
        try:
            points = self.read_points()
            cfg = self.build_cfg(points)
            entered = [[float(self.pt_lat[i].get().strip()), float(self.pt_lon[i].get().strip())]
                       for i in range(6)]
        except ValueError as exc:
            if not silent:
                messagebox.showerror("参数有误", str(exc))
            return False
        cfg.kick_enabled = bool(self.var_kick.get())
        cfg.pause_when_shown = bool(self.var_pause_when_shown.get())
        cfg.coord_system = SYS_OPTIONS[self.csys.get()]
        data = {
            "gui": {"coord_system": SYS_OPTIONS[self.csys.get()],
                    "auto_link_confirmed": self.var_auto_enabled.get()},
            "mumu": {"vm_index": self.disc.get("vm_index", 0),
                     "gateway_port": self.disc.get("gateway_port"), "prefer_http": True},
            "run": cfg.__dict__,
            "track": {"points_as_entered": entered, "waypoints_wgs84": points},
        }
        target = config_path()
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        # 如果 exe 旁边有便携 config.json，一起更新，保持两份一致
        portable = portable_config_path()
        if portable != target:
            try:
                with open(portable, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
            except Exception:
                pass
        self.cfg_path = target
        self._log("已保存配置：%s" % target)
        if not silent:
            messagebox.showinfo("已保存", "配置已保存到：\n%s\n\n下次打开会自动载入。" % target)
        return True

    def on_start(self, automatic=False):
        if self.worker and self.worker.is_alive():
            return
        try:
            points = self.read_points()
            cfg = self.build_cfg(points)
            if self.prepared_route is None or self.prepared_key != (points, cfg.laps):
                raise ValueError("请先在乐跑尚未开始记录时点击“①准备随机起点”；修改圈数或坐标后需重新准备")
            track = self.prepared_route
            if automatic:
                cfg.start_delay_s = 0.0
            RunSimulator(track, None, cfg, logger=lambda _: None)
            self.get_client().ensure_gps_enabled()
            gps = self.get_client().gps_status()
            if not confirmed_start(gps, track):
                if automatic:
                    self.autolink.consumed = False
                    self.route_status.set("等待实际 GPS 落在内圈起点（误差≤5米）；未确认前不移动")
                    return
                raise ValueError("实际 GPS 尚未确认在内圈起点，请回首页恢复定位")
            save_plan(self.route_history_path, track.plan, "started")
        except (ValueError, OSError, RuntimeError) as exc:
            if automatic:
                self.route_status.set("自动启动失败：" + str(exc))
                self._log("自动启动失败：" + str(exc))
            else:
                messagebox.showerror("参数有误", str(exc))
            return

        try:
            self.on_save(silent=True)
        except OSError as exc:
            self._log("默认配置保存失败，本次仍使用界面参数：%s" % exc)
        self.stop_flag.clear()
        self.pause_flag.clear()
        self.autolink.consumed = True
        self.prepared_route = None
        self.prepared_key = None
        self.btn_prepare["state"] = "disabled"
        self.btn_start["state"] = "disabled"
        self.btn_stop["state"] = "normal"
        self._log("=" * 52)
        self._log("检测到乐跑记录中，已自动开始运行。" if automatic else "开始代跑，请确认 App 已经点了「开始乐跑」。")
        self.route_status.set("已自动运行：逆时针 · 随机配速 6～9 分钟/公里" if automatic else "正在运行")

        def work():
            route_status = "failed"
            try:
                cli = self.get_client()
                sim = RunSimulator(track, cli, cfg, logger=self._log)
                self.sim = sim
                total = getattr(track, "total_distance_m", cfg.laps * track.length)

                def stop_flag():
                    if self.stop_flag.is_set():
                        return True
                    if total > 0:
                        self.msgq.put(("progress", min(100.0, sim.stats.distance_m / total * 100.0)))
                    return False

                sim.run(stop_flag=stop_flag, pause_flag=self.pause_flag.is_set)
                route_status = "stopped" if self.stop_flag.is_set() else "finished"
            except Exception as exc:  # noqa: BLE001
                self._log("运行出错：%s" % exc)
            finally:
                try:
                    save_plan(self.route_history_path, track.plan, route_status)
                except Exception as exc:
                    self._log("路线历史更新失败：%s" % exc)
                self.msgq.put(("done", None))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def on_stop(self):
        self.autolink.consumed = True
        if self.stop_flag.is_set():
            return
        self.stop_flag.set()
        self._log("正在停止…")

    def on_close(self):
        self.closing.set()
        try:
            self.on_save(silent=True)      # 关窗前自动存一份
        except Exception:
            pass
        self.stop_flag.set()
        time.sleep(0.2)
        self.root.destroy()



def selftest(out_path):
    """打包后自检：不弹界面，跑一小段并把结果写入文件（用于验证 exe 可用）。"""
    lines = []

    def log(msg):
        lines.append("[%s] %s" % (time.strftime("%H:%M:%S"), msg))
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
        except Exception:
            pass

    try:
        disc = discover_mumu()
        log("root=%s" % disc.get("root"))
        log("manager=%s" % disc.get("manager"))
        log("adb=%s" % disc.get("adb"))
        log("gateway_port=%s" % disc.get("gateway_port"))
        pts = [list(to_wgs84(la, lo, "gcj02")[::-1]) for la, lo in DEFAULT_POINTS_GCJ]
        track = Track(pts, corner_radius_m=6.0)
        log("track perimeter=%.1f m" % track.length)
        cfg = RunConfig(speed_kmh=9.0, laps=0.015, start_delay_s=0.0,
                        enable_steps=True, hide_window=True, log_every_s=3.0)
        cli = MuMuLocationClient(gateway_port=disc.get("gateway_port"),
                                 manager_path=disc.get("manager"), adb_path=disc.get("adb"))
        sim = RunSimulator(track, cli, cfg, logger=log)
        sim.run()
        log("RESULT points=%d errors=%d steps=%d distance=%.1f m"
            % (sim.stats.points, sim.stats.errors, sim.stats.steps, sim.stats.distance_m))
        log("SELFTEST_OK")
    except Exception as exc:  # noqa: BLE001
        import traceback
        log("SELFTEST_FAIL %s\n%s" % (exc, traceback.format_exc()))

def main():
    if not acquire_single_instance():
        r = tk.Tk()
        r.withdraw()
        messagebox.showwarning("已在运行", "本程序已经开着一份了。\n\n同时运行两份会让步数脉冲翻倍，导致模拟器/App 异常，请先关掉那一份。")
        r.destroy()
        return
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except Exception:
        pass
    App(root, auto_watch=True)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--dumpconfig":
        import tkinter as _tk
        _r = _tk.Tk()
        _r.withdraw()
        _a = App(_r)
        with open(sys.argv[2], "w", encoding="utf-8") as fh:
            fh.write("config=%s\n" % getattr(_a, "cfg_path", ""))
            fh.write("csys=%s\n" % _a.csys.get())
            for _i in range(6):
                fh.write("p%d=%s,%s\n" % (_i + 1, _a.pt_lat[_i].get(), _a.pt_lon[_i].get()))
            fh.write("speed=%s laps=%s hz=%s delay=%s cad=%s\n" % (
                _a.var_speed.get(), _a.var_laps.get(), _a.var_hz.get(),
                _a.var_delay.get(), _a.var_cad.get()))
        _r.destroy()
        raise SystemExit(0)
    if len(sys.argv) >= 3 and sys.argv[1] == "--configpath":
        with open(sys.argv[2], "w", encoding="utf-8") as fh:
            fh.write("portable=%s\n" % portable_config_path())
            fh.write("config=%s\n" % config_path())
            fh.write("exists=%s\n" % os.path.exists(config_path()))
        raise SystemExit(0)
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        selftest(sys.argv[2])
        raise SystemExit(0)
    try:
        main()
    except Exception:
        import traceback
        detail = traceback.format_exc()
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("启动失败", detail[-1500:])
            r.destroy()
        except Exception:
            pass
        raise
