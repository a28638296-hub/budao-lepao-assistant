"""Prepare a continuous run inside an annular stadium, with local route history."""
import json
import errno
import math
import os
import random
import time
import uuid
from pathlib import Path

from .geo import haversine, meters_per_degree
from .track import Track


class StadiumBand:
    def __init__(self, anchors, inner_offset=-2.0, outer_offset=0.75):
        if len(anchors) != 6:
            raise ValueError("跑道环带需要六个控制点")
        self.origin = tuple(sum(p[i] for p in anchors) / 6 for i in (0, 1))
        self.scale = meters_per_degree(self.origin[0])
        p = [self.xy(v) for v in anchors]
        self.north = tuple((p[1][i] + p[5][i]) / 2 for i in (0, 1))
        self.south = tuple((p[2][i] + p[4][i]) / 2 for i in (0, 1))
        self.straight = math.dist(self.north, self.south)
        if self.straight < 5:
            raise ValueError("跑道中心轴无效")
        self.up = tuple((self.north[i] - self.south[i]) / self.straight for i in (0, 1))
        self.right = (self.up[1], -self.up[0])
        self.radius = min(math.dist(p[1], p[5]) / 2, math.dist(p[2], p[4]) / 2,
                          sum((p[0][i] - self.north[i]) * self.up[i] for i in (0, 1)),
                          sum((self.south[i] - p[3][i]) * self.up[i] for i in (0, 1)))
        if self.radius < 5 or not (-self.radius < inner_offset < outer_offset):
            raise ValueError("跑道内外边界无效")
        self.inner, self.outer = self.radius + inner_offset, self.radius + outer_offset
        self.perimeter = 2 * self.straight + 2 * math.pi * self.radius

    def xy(self, p):
        return ((p[1] - self.origin[1]) * self.scale[1], (p[0] - self.origin[0]) * self.scale[0])

    def location(self, xy):
        return (self.origin[0] + xy[1] / self.scale[0], self.origin[1] + xy[0] / self.scale[1])

    def radial_distance(self, p):
        q = self.xy(p)
        t = sum((q[i] - self.south[i]) * self.up[i] for i in (0, 1))
        t = min(self.straight, max(0.0, t))
        return math.dist(q, tuple(self.south[i] + t * self.up[i] for i in (0, 1)))

    def phase_at(self, point):
        """Nearest perimeter phase, used to continue from an observed inner fix."""
        q = self.xy(point)
        t = sum((q[i] - self.south[i]) * self.up[i] for i in (0, 1))
        u = sum((q[i] - self.south[i]) * self.right[i] for i in (0, 1))
        arc = math.pi * self.radius
        if t < 0:
            return self.straight + math.atan2(-t, u) * self.radius
        if t > self.straight:
            return 2*self.straight + arc + math.atan2(t-self.straight, -u)*self.radius
        return self.straight-t if u >= 0 else self.straight+arc+t

    def require_point(self, p):
        d = self.radial_distance(p)
        if not math.isfinite(d) or not self.inner <= d <= self.outer:
            raise ValueError("定位离开跑道环带（进入草坪或超出外沿），已停止下发")

    def require_segment(self, a, b):
        # A valid pair of endpoints can still form a shortcut through the grass.
        n = max(1, math.ceil(haversine(*a, *b) / 0.5))
        for j in range(n + 1):
            t = j / n
            self.require_point(tuple(a[i] * (1 - t) + b[i] * t for i in (0, 1)))

    def point(self, distance, offset):
        s = distance % self.perimeter
        arc = math.pi * self.radius
        r = self.radius + offset
        if s < self.straight:
            centre = tuple(self.north[i] - s * self.up[i] for i in (0, 1))
            normal = self.right
        elif s < self.straight + arc:
            theta = (s - self.straight) / self.radius
            centre = self.south
            normal = tuple(self.right[i] * math.cos(theta) - self.up[i] * math.sin(theta) for i in (0, 1))
        elif s < 2 * self.straight + arc:
            t = s - self.straight - arc
            centre = tuple(self.south[i] + t * self.up[i] for i in (0, 1))
            normal = tuple(-v for v in self.right)
        else:
            theta = (s - 2 * self.straight - arc) / self.radius
            centre = self.north
            normal = tuple(-self.right[i] * math.cos(theta) + self.up[i] * math.sin(theta) for i in (0, 1))
        return self.location(tuple(centre[i] + r * normal[i] for i in (0, 1)))


def read_history(path):
    p = Path(path)
    if not p.exists():
        # Recover a completed staging file left by a failed Windows rename.
        p = p.with_suffix(".tmp")
        if not p.exists():
            return []
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("路线历史文件格式不正确，请保留文件并检查")
    return data


def save_plan(path, plan, status):
    records = read_history(path)
    item = dict(plan, status=status)
    records = [r for r in records if r.get("id") != plan["id"]] + [item]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_suffix(".tmp")
    payload = json.dumps(records, ensure_ascii=False, indent=2)
    temp.write_text(payload, encoding="utf-8")
    try:
        temp.replace(p)
    except OSError as exc:
        if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
            raise
        # Some Windows encrypted/redirected folders reject even same-folder
        # renames. Retain the old file and staging file before a flushed copy.
        old = p.read_bytes() if p.exists() else None
        if old is not None:
            p.with_suffix(".backup.json").write_bytes(old)
        try:
            with p.open("w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            if json.loads(p.read_text(encoding="utf-8")) != records:
                raise OSError("路线历史写入校验失败")
        except Exception:
            if old is not None:
                p.write_bytes(old)
            raise


def make_plan(band, laps, history=(), rng=None):
    rng = rng or random.SystemRandom()
    if not 0 < laps <= 200:
        raise ValueError("圈数应为 0～200")
    recent = [r["start"] for r in history[-8:] if "start" in r]
    choices = []
    for _ in range(128):
        start = rng.uniform(0, band.perimeter)
        p = band.point(start, -1.0)
        separation = min((haversine(*p, *old) for old in recent), default=999)
        choices.append((separation, start))
    separated = [c for c in choices if c[0] >= 20]
    phase = rng.choice(separated)[1] if separated else max(choices)[1]
    # Entire generated route stays inside the reference oval, including waves.
    lo, hi = rng.uniform(-1.75, -1.65), rng.uniform(-0.8, -0.7)
    if rng.choice((True, False)):
        lo, hi = hi, lo
    plan = dict(id=uuid.uuid4().hex, created_at=time.time(), laps=laps, start_phase_m=phase,
                offset_start_m=lo, offset_end_m=hi, wave_phase=rng.uniform(0, 2 * math.pi),
                wave_phase2=rng.uniform(0, 2 * math.pi), direction=-1,
                route_profile="inner_counterclockwise_v2")
    plan["start"] = list(band.point(phase, route_offset(plan, phase, 0, band.perimeter)))
    return plan


def route_offset(plan, phase, progress, perimeter):
    # The same small wave repeats each lap; a monotone radial drift separates laps.
    theta = 2 * math.pi * phase / perimeter
    return (plan["offset_start_m"] + (plan["offset_end_m"] - plan["offset_start_m"]) * progress
            + 0.06 * math.sin(2 * theta + plan["wave_phase"])
            + 0.04 * math.sin(3 * theta + plan["wave_phase2"]))


class PreparedRoute(Track):
    def __init__(self, band, plan):
        # Negative phase travels north on the east straight: counterclockwise.
        if plan.get("direction") != -1 or plan.get("route_profile") != "inner_counterclockwise_v2":
            raise ValueError("旧路线不符合逆时针内圈设置，请重新准备")
        self.plan = plan
        self.band = band
        distance = plan["laps"] * band.perimeter
        count = max(2, math.ceil(distance / 0.8))
        pts = []
        for j in range(count + 1):
            progress = j / count
            phase = plan["start_phase_m"] + plan["direction"] * distance * progress
            point = band.point(phase, route_offset(plan, phase, progress, band.perimeter))
            band.require_point(point)
            if pts:
                band.require_segment(pts[-1], point)
            pts.append(point)
        super().__init__(pts, closed=False, corner_radius_m=0)
        self.total_distance_m = self.length
        self.lap_length_m = self.length / plan["laps"]
