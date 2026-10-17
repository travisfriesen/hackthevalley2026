"""Simulated person for demos and development without hardware.

A deterministic little agent that lives in the traced room: walks between
waypoints, pauses, falls, lies still, enters and leaves. It drives the
simulated ultrasonic distances (real ray casting against the traced walls)
and tells the simulated CSI source what activity to emit.

In sim/replay mode the room canvas also draws this person as ground truth,
so the demo shows sensors reacting to a known event.
"""

from __future__ import annotations

import math
import random
import threading
import time

from room_math import PERSON_RADIUS_M, ray_hit


def point_in_polygon(poly, p) -> bool:
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            cross = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < cross:
                inside = not inside
    return inside


class SimRoom:
    SPEED_WALK = 0.75          # m/s
    FALL_ACTION_S = 1.2         # "burst" duration right after falling

    def __init__(self, room: dict, max_distance_m: float = 4.0):
        self.room = room
        self.outline = [tuple(p) for p in room["outline"]]
        self.sensors = room.get("sensors", [])
        self.max_distance_m = max_distance_m
        self.lock = threading.RLock()          # fall() calls enter() re-entrantly
        self._rng = random.Random(2026)

        self.inside = False
        self.lying = False
        self.x = 0.0
        self.y = 0.0
        self._waypoint = None
        self._pause_until = 0.0
        self._fall_at = None      # wall time of the fall for the CSI burst
        self._last_step = time.monotonic()

    # -- demo controls (UI buttons / tests) ---------------------------------

    def enter(self) -> None:
        with self.lock:
            door = self.room.get("door") or [
                (self.outline[0][0] + self.outline[2][0]) / 2, 0
            ]
            self.x, self.y = float(door[0]), float(door[1])
            self.inside = True
            self.lying = False
            self._fall_at = None
            self._waypoint = self._pick_waypoint()

    def leave(self) -> None:
        with self.lock:
            door = self.room.get("door") or [
                (self.outline[0][0] + self.outline[2][0]) / 2, 0
            ]
            self.x, self.y = float(door[0]), float(door[1])
            self.inside = False
            self.lying = False
            self._waypoint = None

    def walk(self) -> None:
        with self.lock:
            if not self.inside:
                self.enter()
            self.lying = False
            self._waypoint = self._pick_waypoint()

    def fall(self) -> None:
        """Scripted incident: walk to the middle, collapse, lie still."""
        with self.lock:
            if not self.inside:
                self.enter()
            cx = sum(p[0] for p in self.outline) / len(self.outline)
            cy = sum(p[1] for p in self.outline) / len(self.outline)
            self._waypoint = (cx, cy)
            self._fall_on_arrival = True

    _fall_on_arrival = False

    # -- simulation ----------------------------------------------------------

    def step(self, now: float) -> None:
        with self.lock:
            dt = max(0.0, min(0.5, now - self._last_step))
            self._last_step = now
            if not self.inside or self.lying or self._waypoint is None:
                return
            if now < self._pause_until:
                return
            wx, wy = self._waypoint
            dx, dy = wx - self.x, wy - self.y
            dist = math.hypot(dx, dy)
            if dist < 0.05:
                if getattr(self, "_fall_on_arrival", False):
                    self.lying = True
                    self._fall_at = now
                    self._fall_on_arrival = False
                    self._waypoint = None
                else:
                    self._pause_until = now + self._rng.uniform(1.0, 4.0)
                    self._waypoint = self._pick_waypoint()
                return
            speed = self.SPEED_WALK * (2.0 if getattr(
                self, "_fall_on_arrival", False) else 1.0)
            step = min(dist, speed * dt)
            self.x += dx / dist * step
            self.y += dy / dist * step

    def _pick_waypoint(self):
        xs = [p[0] for p in self.outline]
        ys = [p[1] for p in self.outline]
        for _ in range(40):
            p = (self._rng.uniform(min(xs) + 0.3, max(xs) - 0.3),
                 self._rng.uniform(min(ys) + 0.3, max(ys) - 0.3))
            if point_in_polygon(self.outline, p):
                return p
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    # -- sensor views ----------------------------------------------------------

    def activity(self) -> str:
        """What the (simulated) CSI link should look like right now."""
        with self.lock:
            if not self.inside:
                return "empty"
            if self.lying:
                if (self._fall_at is not None
                        and time.monotonic() - self._fall_at < self.FALL_ACTION_S):
                    return "fall"
                return "lying"
            if self._waypoint is not None and time.monotonic() >= self._pause_until:
                return "walking"
            return "lying"          # standing/sitting still: no CSI motion

    def distances(self, max_m: float | None = None) -> list[float | None]:
        """Ray-cast per-sensor distances exactly like the real HC-SR04s see."""
        with self.lock:
            circles = [(self.x, self.y, PERSON_RADIUS_M)] if self.inside else []
            out = []
            limit = max_m or self.max_distance_m
            for s in self.sensors:
                heading = math.radians(s.get("heading_deg", 0))
                d = ray_hit(s["x"], s["y"], heading,
                            [self.outline], circles, limit)
                noise = self._rng.uniform(-0.01, 0.01)
                out.append(min(limit, max(0.05, d + noise)))
            return out

    def ground_truth(self):
        with self.lock:
            return {
                "inside": self.inside,
                "x": self.x,
                "y": self.y,
                "lying": self.lying,
            }
