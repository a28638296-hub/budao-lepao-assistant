"""跑道建模：N 个顶点 -> 闭合路径 -> 按里程插值。"""
import math

from .geo import haversine, meters_per_degree


def stadium_waypoints(anchors, margin_m=0.0, arc_segments=64, straight_segments=48):
    """Six ordered anchors -> two tangent semicircles and two straight sides.

    Order: north tip, NE tangent, SE tangent, south tip, SW tangent, NW tangent.
    A positive margin expands the radius for a separate outer boundary.
    """
    if len(anchors) != 6 or arc_segments < 4 or straight_segments < 1:
        raise ValueError("操场环形路线需要按顺序填写六个控制点")
    lat0 = sum(p[0] for p in anchors) / 6
    lon0 = sum(p[1] for p in anchors) / 6
    mlat, mlon = meters_per_degree(lat0)
    p = [((lon - lon0) * mlon, (lat - lat0) * mlat) for lat, lon in anchors]
    north = tuple((p[1][i] + p[5][i]) / 2 for i in (0, 1))
    south = tuple((p[2][i] + p[4][i]) / 2 for i in (0, 1))
    length = math.dist(north, south)
    if length < 5:
        raise ValueError("操场两端控制点间距过小")
    up = tuple((north[i] - south[i]) / length for i in (0, 1))
    right = (up[1], -up[0])
    radii = [math.dist(p[1], p[5]) / 2, math.dist(p[2], p[4]) / 2,
             sum((p[0][i] - north[i]) * up[i] for i in (0, 1)),
             sum((south[i] - p[3][i]) * up[i] for i in (0, 1))]
    radius = min(radii) + margin_m
    if not math.isfinite(radius) or radius < 5:
        raise ValueError("操场控制点顺序或弯道半径有误")
    xy = []
    for j in range(arc_segments + 1):
        theta = -math.pi / 2 + math.pi * j / arc_segments
        xy.append(tuple(north[i] + radius * (up[i] * math.cos(theta) + right[i] * math.sin(theta))
                        for i in (0, 1)))
    for j in range(1, straight_segments):
        t = j / straight_segments
        xy.append(tuple(north[i] * (1 - t) + south[i] * t + radius * right[i] for i in (0, 1)))
    for j in range(arc_segments + 1):
        theta = math.pi / 2 - math.pi * j / arc_segments
        xy.append(tuple(south[i] + radius * (-up[i] * math.cos(theta) + right[i] * math.sin(theta))
                        for i in (0, 1)))
    for j in range(1, straight_segments):
        t = j / straight_segments
        xy.append(tuple(south[i] * (1 - t) + north[i] * t - radius * right[i] for i in (0, 1)))
    # Start at the north tip, then run clockwise. Track closes the final segment.
    first = arc_segments // 2
    xy = xy[first:] + xy[:first]
    return [[lat0 + y / mlat, lon0 + x / mlon] for x, y in xy]


class ConvexBoundary:
    """Constrain output to a surveyed convex area; coordinates are WGS-84."""

    def __init__(self, points):
        self.origin = tuple(points[0]) if points else (0, 0)
        self.scale = meters_per_degree(self.origin[0])
        self.points = [self._xy(p) for p in points]
        if len(self.points) > 1 and self.points[0] == self.points[-1]:
            self.points.pop()
        if len(self.points) < 3:
            raise ValueError("操场边界至少需要三个点")
        edges = list(zip(self.points, self.points[1:] + self.points[:1]))
        area = sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)
        if not math.isfinite(area) or abs(area) < 1:
            raise ValueError("操场边界无效")
        self.sign = 1 if area > 0 else -1
        self.edges = edges
        if any(not self.contains_xy(p) for p in self.points):
            raise ValueError("操场边界必须是按顺序排列的凸多边形")

    def _xy(self, point):
        lat, lon = point
        return ((lon - self.origin[1]) * self.scale[1],
                (lat - self.origin[0]) * self.scale[0])

    def contains_xy(self, p):
        if not all(math.isfinite(v) for v in p):
            return False
        for a, b in self.edges:
            dx, dy = b[0] - a[0], b[1] - a[1]
            cross = dx * (p[1] - a[1]) - dy * (p[0] - a[0])
            # The UI rounds coordinates to 6 decimal places (under 0.1 m).
            if self.sign * cross < -0.15 * math.hypot(dx, dy):
                return False
        return True

    def contains(self, point):
        return self.contains_xy(self._xy(point))

    def require_inside(self, point):
        if not self.contains(point):
            raise ValueError("定位超出南宁学院操场限定区域，已停止下发；请检查跑道坐标和坐标系")


class Track:
    def __init__(self, waypoints, closed=True, corner_radius_m=6.0, corner_segments=6):
        pts = [(float(p[0]), float(p[1])) for p in waypoints]
        if len(pts) < 2:
            raise ValueError("至少需要 2 个顶点")
        if closed and pts[0] != pts[-1]:
            pts.append(pts[0])
        self.raw = pts
        self.closed = closed
        path = self._round_corners(pts, corner_radius_m, corner_segments) if corner_radius_m > 0 else pts
        self.path = self._dedupe(path)
        self.cum = [0.0]
        for i in range(1, len(self.path)):
            a, b = self.path[i - 1], self.path[i]
            self.cum.append(self.cum[-1] + haversine(a[0], a[1], b[0], b[1]))
        self.length = self.cum[-1]
        if self.length <= 0:
            raise ValueError("路径长度为 0")

    # ---------- geometry helpers ----------
    @staticmethod
    def _dedupe(pts, eps=1e-9):
        out = [pts[0]]
        for p in pts[1:]:
            if abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
                out.append(p)
        return out

    @staticmethod
    def _round_corners(pts, radius_m, segments):
        """用二次贝塞尔把每个拐角抹圆，跑出来更像真实轨迹。"""
        if len(pts) < 4:
            return pts
        core = pts[:-1] if pts[0] == pts[-1] else pts
        n = len(core)
        out = []
        for i in range(n):
            p_prev = core[(i - 1) % n]
            p_cur = core[i]
            p_next = core[(i + 1) % n]
            d1 = haversine(p_cur[0], p_cur[1], p_prev[0], p_prev[1])
            d2 = haversine(p_cur[0], p_cur[1], p_next[0], p_next[1])
            r = min(radius_m, d1 / 2.5 if d1 > 0 else radius_m, d2 / 2.5 if d2 > 0 else radius_m)
            if r <= 0.5:
                out.append(p_cur)
                continue
            a = Track._toward(p_cur, p_prev, r, d1)
            b = Track._toward(p_cur, p_next, r, d2)
            for k in range(segments + 1):
                t = k / segments
                lat = (1 - t) ** 2 * a[0] + 2 * (1 - t) * t * p_cur[0] + t * t * b[0]
                lon = (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * p_cur[1] + t * t * b[1]
                out.append((lat, lon))
        out.append(out[0])
        return out

    @staticmethod
    def _toward(src, dst, dist, total):
        if total <= 0:
            return src
        t = min(1.0, dist / total)
        return (src[0] + (dst[0] - src[0]) * t, src[1] + (dst[1] - src[1]) * t)

    # ---------- sampling ----------
    def position_at(self, distance_m):
        """按里程取点，自动循环。"""
        d = distance_m % self.length if self.closed else max(0.0, min(distance_m, self.length))
        lo, hi = 0, len(self.cum) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= d:
                lo = mid
            else:
                hi = mid
        seg = self.cum[hi] - self.cum[lo]
        t = 0.0 if seg <= 0 else (d - self.cum[lo]) / seg
        a, b = self.path[lo], self.path[hi]
        return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t

    def bearing_at(self, distance_m, probe_m=6.0):
        a = self.position_at(distance_m)
        b = self.position_at(distance_m + probe_m)
        mlat, mlon = meters_per_degree(a[0])
        dn = (b[0] - a[0]) * mlat
        de = (b[1] - a[1]) * mlon
        if dn == 0 and de == 0:
            return 0.0
        return (math.degrees(math.atan2(de, dn)) + 360.0) % 360.0

    def bbox(self):
        lats = [p[0] for p in self.path]
        lons = [p[1] for p in self.path]
        return min(lats), min(lons), max(lats), max(lons)
