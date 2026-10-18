"""The room map: the actual room traced on the touchscreen, then drawn by
the sensors themselves.

Layers, back to front:
- the finger-traced outline + 0.5 m grid (from room.json / setup wizard)
- depth evidence: every ultrasonic reading lands a hit point at
  sensor_pos + heading * distance; recent points fade slowly, and points that
  deviate from the ray's rolling baseline are drawn hot - that is the person
- live rays with their baseline tick and current hit
- the person estimate (crossing rays) or the single deviating hit
- the CSI sightline (Pico <-> Pi), pulsing with the motion score, plus the
  "room state changed" badge (a still-but-present body shifts the static
  fingerprint, docs/LOG.md lying AUC 0.99)
- the simulated person as ground truth in demo modes

In live mode everything except the trace and the sensor icons is produced by
real data; there is no furniture drawn from imagination.
"""

from __future__ import annotations

import math
import tkinter as tk
from theme import FONT, AMBER, BG, BLUE, BLUE_NEW, BLUE_OLD, DIM, GREEN, GRID_B, HOT, HOT_OLD, MARKER, MUTED_DOT, PANEL, ROOM_FILL, TEXT

EVIDENCE_S = 25.0          # how long a hit point stays on the map
EVIDENCE_STEP = 0.5        # sampling cadence for evidence points


class RoomCanvas(tk.Frame):
    def __init__(self, master, on_tap=None, **kwargs):
        super().__init__(master, bg=PANEL, **kwargs)
        self.on_tap = on_tap
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.canvas.bind("<Button-1>", self._tap)
        self.canvas.bind("<Configure>", lambda e: self._retransform())

        self.room = None
        self.scale = 80.0           # px per meter, recomputed on layout
        self.off_x = self.off_y = 0.0
        self._evidence = []          # per-sensor [(t, x, y, deviating)]
        self._last_ev = []
        self._dash = 0

    # -- geometry -----------------------------------------------------------

    def set_room(self, room: dict) -> None:
        self.room = room
        self._evidence = [[] for _ in room.get("sensors", [])]
        self._last_ev = [0.0] * len(room.get("sensors", []))
        self._retransform()
        self._draw_static()

    def _retransform(self) -> None:
        if not self.room:
            return
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 40 or h < 40:
            return
        pts = self.room["outline"]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bw = max(max(xs) - min(xs), 0.5)
        bh = max(max(ys) - min(ys), 0.5)
        self.scale = min((w - 30) / bw, (h - 34) / bh)
        self.off_x = (w - bw * self.scale) / 2 - min(xs) * self.scale
        self.off_y = (h - bh * self.scale) / 2 - min(ys) * self.scale + 6
        self._draw_static()

    def _px(self, x, y):
        return (x * self.scale + self.off_x, y * self.scale + self.off_y)

    def _meters(self, px, py):
        return ((px - self.off_x) / self.scale, (py - self.off_y) / self.scale)

    # -- static layer ---------------------------------------------------------

    def _draw_static(self) -> None:
        c = self.canvas
        c.delete("static")
        if not self.room:
            return
        c.delete("dyn")
        pts = [self._px(x, y) for x, y in self.room["outline"]]
        c.create_polygon(*pts, fill=ROOM_FILL, outline=TEXT, width=2,
                         tags=("static",))

        # 0.5 m grid clipped to the room bbox
        xs = [p[0] for p in self.room["outline"]]
        ys = [p[1] for p in self.room["outline"]]
        for gx in self._frange(min(xs), max(xs), 0.5):
            a, b = self._px(gx, min(ys)), self._px(gx, max(ys))
            c.create_line(a[0], a[1], b[0], b[1], fill=GRID_B,
                          tags=("static",))
        for gy in self._frange(min(ys), max(ys), 0.5):
            a, b = self._px(min(xs), gy), self._px(max(xs), gy)
            c.create_line(a[0], a[1], b[0], b[1], fill=GRID_B,
                          tags=("static",))

        pico = self.room.get("pico")
        if pico:
            px, py = self._px(*pico)
            c.create_oval(px - 5, py - 5, px + 5, py + 5, fill=BLUE,
                          outline="", tags=("static",))
            c.create_text(px, py + 10, text="Pico", fill=BLUE,
                          font=(FONT, 7), tags=("static",))
        pi = self.room.get("pi")
        if pi:
            px, py = self._px(*pi)
            c.create_rectangle(px - 5, py - 5, px + 5, py + 5, fill=GREEN,
                                outline="", tags=("static",))
            c.create_text(px, py + 10, text="Pi", fill=GREEN,
                          font=(FONT, 7), tags=("static",))

        for i, s in enumerate(self.room.get("sensors", [])):
            sx, sy = self._px(s["x"], s["y"])
            head = math.radians(s.get("heading_deg", 0))
            dx, dy = math.cos(head), math.sin(head)
            c.create_polygon(
                sx + dx * 9, sy + dy * 9,
                sx - dy * 4, sy + dx * 4,
                sx + dy * 4, sy - dx * 4,
                fill=MARKER, outline="", tags=("static",))
            c.create_text(sx - dx * 12, sy - dy * 12, text=s.get("label", f"us{i+1}"),
                          fill=TEXT, font=(FONT, 7, "bold"),
                          tags=("static",))

    @staticmethod
    def _frange(start, stop, step):
        n = int((stop - start) / step) + 1
        for i in range(max(0, n)):
            yield start + i * step

    # -- dynamic layer ----------------------------------------------------------

    def refresh(self, data: dict, now: float) -> None:
        c = self.canvas
        c.delete("dyn")
        if not self.room:
            c.create_text(c.winfo_width() / 2, c.winfo_height() / 2,
                          text="run Room setup from Settings",
                          fill=DIM, font=(FONT, 10), tags=("dyn",))
            return

        sensors = self.room.get("sensors", [])
        readings = data.get("readings") or []
        baselines = data.get("baselines") or []
        deviating = set(data.get("deviating") or ())

        # evidence points (the room drawn by the sensors)
        for i, s in enumerate(sensors):
            if i < len(readings) and now - self._last_ev[i] >= EVIDENCE_STEP:
                self._last_ev[i] = now
                d = readings[i]
                if d is not None and d < (data.get("max_distance_m") or 4.0):
                    heading = math.radians(s.get("heading_deg", 0))
                    x = s["x"] + math.cos(heading) * d
                    y = s["y"] + math.sin(heading) * d
                    self._evidence[i].append(
                        (now, x, y, i in deviating))
            cutoff = now - EVIDENCE_S
            ev = self._evidence[i]
            while ev and ev[0][0] < cutoff:
                ev.pop(0)

        for i, s in enumerate(sensors):
            sx, sy = self._px(s["x"], s["y"])
            heading = math.radians(s.get("heading_deg", 0))
            dx, dy = math.cos(heading), math.sin(heading)
            d = readings[i] if i < len(readings) else None
            if d is None:
                continue
            dev = i in deviating
            if d >= (data.get("max_distance_m") or 4.0):
                ex, ey = sx + dx * 24, sy + dy * 24
                c.create_line(sx, sy, ex, ey, fill=DIM, dash=(3, 3),
                             tags=("dyn",))
                c.create_text(ex + dx * 8, ey + dy * 8, text="no echo",
                              fill=DIM, font=(FONT, 7), tags=("dyn",))
                continue
            hx, hy = self._px(s["x"] + dx * d, s["y"] + dy * d)
            color = HOT if dev else MUTED_DOT
            c.create_line(sx, sy, hx, hy, fill=color, width=2, tags=("dyn",))
            c.create_oval(hx - 3, hy - 3, hx + 3, hy + 3,
                          fill=color, outline="", tags=("dyn",))
            base = baselines[i] if i < len(baselines) else None
            if base is not None and base < (data.get("max_distance_m") or 4.0):
                bx, by = self._px(s["x"] + dx * base, s["y"] + dy * base)
                c.create_line(bx - 3, by - 3, bx + 3, by + 3, fill=AMBER,
                              tags=("dyn",))
                c.create_line(bx - 3, by + 3, bx + 3, by - 3, fill=AMBER,
                              tags=("dyn",))

        for i, ev in enumerate(self._evidence):
            for t, x, y, was_dev in ev:
                px, py = self._px(x, y)
                age = (now - t) / EVIDENCE_S
                if was_dev:
                    fill = HOT if age < 0.5 else HOT_OLD
                else:
                    fill = BLUE_NEW if age < 0.4 else BLUE_OLD
                c.create_oval(px - 2, py - 2, px + 2, py + 2,
                              fill=fill, outline="", tags=("dyn",))

        # person estimate
        person = data.get("person")
        if person:
            px, py = self._px(person[0], person[1])
            c.create_oval(px - 8, py - 8, px + 8, py + 8, outline=GREEN,
                          width=2, tags=("dyn",))
            c.create_oval(px - 2, py - 2, px + 2, py + 2, fill=GREEN,
                          outline="", tags=("dyn",))
            c.create_text(px, py - 12, text="PERSON", fill=GREEN,
                          font=(FONT, 8, "bold"), tags=("dyn",))

        # sim ground truth (demo modes)
        truth = data.get("sim_truth")
        if truth and truth.get("inside"):
            px, py = self._px(truth["x"], truth["y"])
            r = 0.22 * self.scale
            c.create_oval(px - r, py - r, px + r, py + r, outline=HOT,
                          dash=(4, 3), width=2, tags=("dyn",))
            c.create_text(px, py + r + 8,
                          text="lying" if truth.get("lying") else "SIM",
                          fill=HOT, font=(FONT, 8),
                          tags=("dyn",))

        # CSI sightline + badges
        pico = self.room.get("pico")
        pi = self.room.get("pi")
        if pico and pi:
            a = self._px(*pico)
            b = self._px(*pi)
            moving = data.get("moving")
            self._dash = (self._dash + 2) % 12
            line = c.create_line(a[0], a[1], b[0], b[1], fill=BLUE,
                                 dash=(6, 6), dashoffset=self._dash,
                                 tags=("dyn",))
            if moving:
                c.itemconfigure(line, width=3)
            c.create_text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - 8,
                          text="CSI motion" if moving else "CSI quiet",
                          fill=BLUE, font=(FONT, 7), tags=("dyn",))
            if data.get("shape_changed"):
                c.create_text(c.winfo_width() - 8, 16, anchor="e",
                              text="ROOM STATE CHANGED", fill=AMBER,
                              font=(FONT, 9, "bold"), tags=("dyn",))

        # status corner
        status = data.get("status", "")
        if status:
            c.create_text(8, 8, anchor="nw", text=status, fill=TEXT,
                          font=(FONT, 9, "bold"), tags=("dyn",))

    def _tap(self, _event) -> None:
        if self.on_tap:
            self.on_tap()
