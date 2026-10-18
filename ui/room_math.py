"""Room geometry in meters, y pointing down (screen-like).

Everything the room map draws comes through here: traced-wall ray casting
for the simulated ultrasonics, hit-point placement for real readings, person
position estimates from crossing rays, and presence detection from baseline
deviation. Pure functions / small classes, no Tk.
"""

from __future__ import annotations

import math
import statistics
from collections import deque

EPS = 1e-9
PERSON_RADIUS_M = 0.22


# -- polyline helpers -------------------------------------------------------

def simplify(points: list[tuple[float, float]],
             tolerance: float = 0.08) -> list[tuple[float, float]]:
    """Douglas-Peucker light: drop points closer than tolerance to the line
    between their neighbours, so a finger trace becomes a tidy polygon."""
    if len(points) < 3:
        return list(points)
    out = [points[0]]
    for i in range(1, len(points) - 1):
        ax, ay = out[-1]
        bx, by = points[i + 1]
        px, py = points[i]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < EPS:
            dist = math.hypot(px - ax, py - ay)
        else:
            dist = abs(dx * (ay - py) - dy * (ax - px)) / length
        if dist >= tolerance:
            out.append((px, py))
    out.append(points[-1])
    return out


def point_on_segment(a, b, p):
    """Snap p to the nearest point on segment a-b."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 < EPS:
        return a
    t = ((px - ax) * dx + (py - ay) * dy) / length2
    t = max(0.0, min(1.0, t))
    return (ax + t * dx, ay + t * dy)


def snap_to_polygon(poly, p):
    """Nearest point on any polygon edge (for placing the door on a wall)."""
    best, best_d = None, float("inf")
    for i in range(len(poly)):
        q = point_on_segment(poly[i], poly[(i + 1) % len(poly)], p)
        d = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2
        if d < best_d:
            best, best_d = q, d
    return best


# -- ray casting (the ultrasonic "poor man's lidar") -------------------------

def ray_hit(x0, y0, heading_rad, walls, circles, max_m):
    """Distance (m) from (x0,y0) along heading to the nearest wall or circle.

    `walls` is a list of polygons (each a list of (x,y)); `circles` is a list
    of (cx, cy, r). Returns max_m when nothing is hit (HC-SR04 "no echo").
    """
    dx, dy = math.cos(heading_rad), math.sin(heading_rad)
    best = max_m

    for poly in walls:
        n = len(poly)
        for i in range(n):
            ax, ay = poly[i]
            bx, by = poly[(i + 1) % n]
            # solve p = o + t*d  on segment a + u*(b-a), 0<=u<=1, t>0
            ex, ey = bx - ax, by - ay
            denom = dx * ey - dy * ex
            if abs(denom) < EPS:
                continue
            t = ((ax - x0) * ey - (ay - y0) * ex) / denom
            u = ((ax - x0) * dy - (ay - y0) * dx) / denom
            if EPS < t < best and -EPS <= u <= 1 + EPS:
                best = t

    for cx, cy, r in circles:
        # |o + t*d - c| = r  -> quadratic in t
        fx, fy = x0 - cx, y0 - cy
        b = fx * dx + fy * dy
        c = fx * fx + fy * fy - r * r
        disc = b * b - c
        if disc < 0:
            continue
        t = -b - math.sqrt(disc)
        if EPS < t < best:
            best = t

    return best


def ray_point(sensor, distance_m):
    """Map position of an ultrasonic hit: sensor pos + heading * distance."""
    x, y, heading = sensor["x"], sensor["y"], math.radians(sensor["heading_deg"])
    return (x + math.cos(heading) * distance_m,
            y + math.sin(heading) * distance_m)


def ray_intersection(s1, d1, s2, d2):
    """Where two rays cross (person position when both sensors see them),
    or None when the rays point away from each other. Facing, co-linear rays
    (sensors aimed across the room at each other) meet between the hits."""
    x1, y1, h1 = s1["x"], s1["y"], math.radians(s1["heading_deg"])
    x2, y2, h2 = s2["x"], s2["y"], math.radians(s2["heading_deg"])
    dx1, dy1 = math.cos(h1), math.sin(h1)
    dx2, dy2 = math.cos(h2), math.sin(h2)
    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < 1e-6:
        # parallel rays only meet when they share a line and face each other
        cross = (x2 - x1) * dy1 - (y2 - y1) * dx1
        if abs(cross) > 1e-3:
            return None
        p1 = (x1 + d1 * dx1, y1 + d1 * dy1)
        p2 = (x2 + d2 * dx2, y2 + d2 * dy2)
        facing = ((p2[0] - x1) * dx1 + (p2[1] - y1) * dy1 > 0
                  and (p1[0] - x2) * dx2 + (p1[1] - y2) * dy2 > 0)
        if facing:
            return ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        return None
    t = ((x2 - x1) * dy2 - (y2 - y1) * dx2) / denom
    u = ((x2 - x1) * dy1 - (y2 - y1) * dx1) / denom
    if t < 0 or u < 0:
        return None
    # both rays should reach roughly the same distance, else it's a lucky cross
    if abs(t - u) > max(d1, d2) * 0.6 + 0.2:
        return None
    return (x1 + t * dx1, y1 + t * dy1)


# -- presence from baseline deviation ----------------------------------------

class PresenceEstimator:
    """Ultrasonic presence: a reading deviating from its rolling-median
    baseline means something (person/furniture) entered the beam.

    `enter` requires a sustained deviation (filters people walking past the
    sensor); `exit` requires sustained quiet. Readings above max_distance_m
    are "no echo" and ignored.
    """

    def __init__(self, deviation_cm=30.0, enter_s=1.5, exit_s=5.0,
                 max_distance_m=4.0, baseline_s=120.0):
        self.deviation_m = deviation_cm / 100.0
        self.enter_s = enter_s
        self.exit_s = exit_s
        self.max_distance_m = max_distance_m
        self.baseline_s = baseline_s
        self._history = {}      # sensor index -> deque[(t, d)]
        self._dev_since = {}    # sensor index -> t when deviation started
        self._quiet_since = {}  # single "all" key -> t when quiet began
        self._frozen = []       # calibrated structure baselines, per sensor
        self.present = False
        self.deviating = set()

    def _samples(self, idx):
        if idx not in self._history:
            self._history[idx] = deque(maxlen=4096)
        return self._history[idx]

    def freeze_recent(self, now: float, seconds: float = 5.0) -> None:
        """Lock the current readings in as the room's structure baseline
        (call during empty-room calibration). Frozen baselines stop a person
        who stays in one place from being absorbed into the rolling median."""
        frozen = []
        for idx in range(max(len(self._history), 1)):
            samples = [d for t, d in self._samples(idx) if t >= now - seconds]
            frozen.append(statistics.median(samples) if len(samples) >= 10
                         else None)
        if frozen and any(b is not None for b in frozen):
            self._frozen = frozen

    def unfreeze(self) -> None:
        self._frozen = []

    def export_frozen(self) -> list | None:
        """Per-sensor frozen baselines (meters, None where unknown), or None."""
        return list(self._frozen) if self._frozen else None

    def import_frozen(self, frozen: list) -> None:
        self._frozen = [None if b is None else float(b) for b in frozen]

    def baseline(self, idx) -> float | None:
        if self._frozen and idx < len(self._frozen):
            if self._frozen[idx] is not None:
                return self._frozen[idx]
        samples = [d for _, d in self._samples(idx)]
        return statistics.median(samples) if len(samples) >= 20 else None

    def update(self, now: float, readings: list[float | None]) -> bool:
        """Feed the latest per-sensor distances (meters); returns presence."""
        self.deviating = set()
        for idx, d in enumerate(readings):
            if d is None or d >= self.max_distance_m:
                continue
            samples = self._samples(idx)
            samples.append((now, d))
            cutoff = now - self.baseline_s
            while samples and samples[0][0] < cutoff:
                samples.popleft()
            base = self.baseline(idx)
            if base is None:
                continue
            if abs(d - base) >= self.deviation_m:
                self.deviating.add(idx)
                started = self._dev_since.setdefault(idx, now)
                if now - started >= self.enter_s:
                    self.present = True
            else:
                self._dev_since.pop(idx, None)

        if self.present and not self.deviating:
            quiet = self._quiet_since.setdefault("all", now)
            if now - quiet >= self.exit_s:
                self.present = False
                self._quiet_since.clear()
        else:
            self._quiet_since.clear()
        return self.present
