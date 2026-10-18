"""Room setup wizard: trace the real room with a finger on the touchscreen.

Steps: trace the walls -> tap the door -> place & aim each ultrasonic sensor
-> tap the Pico and the Pi -> save. The traced outline is the rough room;
everything after that (hit points, presence, person estimate) is drawn by
the sensors against it. Skipping uses a sensible default rectangle so the
UI is never blocked.

The canvas has a 0.5 m grid; the +/- buttons zoom the grid so the trace can
be drawn roughly to scale. Coordinates are meters, origin top-left, y down.
"""

from __future__ import annotations

import math
import tkinter as tk

import settings_store
from room_math import simplify, snap_to_polygon
from theme import AMBER, BG, BLUE, DIM, GREEN, GRID_A, GRID_B, MARKER, PANEL, ROOM_FILL, TEXT


STEPS = [
    "TRACE: drag a finger around the room walls, then tap Done",
    "DOOR: tap where the door is (on a wall)",
    "SENSORS: tap where each ultrasonic sensor sits",
    "SENSORS: tap in the direction it points",
    "PICO: tap where the Pico (CSI sender) sits",
    "PI: tap where the Pi sits, then Done",
]


class RoomSetup(tk.Frame):
    def __init__(self, master, settings: dict, on_done, **kwargs):
        super().__init__(master, bg=BG, **kwargs)
        self.settings = settings
        self.on_done = on_done
        self.pxm = 70.0

        self.step_label = tk.Label(self, text=STEPS[0], bg=BG, fg=TEXT,
                                   font=("Helvetica", 12, "bold"))
        self.step_label.pack(fill="x", pady=(6, 2))

        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=2)
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)

        bar = tk.Frame(self, bg=PANEL)
        bar.pack(fill="x", padx=8, pady=6)
        tk.Button(bar, text="  -  ", command=lambda: self._zoom(-10),
                  font=("Helvetica", 11)).pack(side="left")
        tk.Button(bar, text="  +  ", command=lambda: self._zoom(10),
                  font=("Helvetica", 11)).pack(side="left")
        tk.Label(bar, text="grid 0.5 m", bg=PANEL, fg=DIM,
                 font=("Helvetica", 9)).pack(side="left", padx=8)
        self.hint = tk.Label(bar, text="", bg=PANEL, fg=AMBER,
                             font=("Helvetica", 9))
        self.hint.pack(side="left", padx=8)
        tk.Button(bar, text="Clear", command=self._clear,
                  font=("Helvetica", 11)).pack(side="right", padx=4)
        self.next_btn = tk.Button(bar, text="Done", command=self._next,
                                  font=("Helvetica", 11, "bold"),
                                  state="disabled")
        self.next_btn.pack(side="right", padx=4)
        tk.Button(bar, text="Skip (default room)", command=self._skip,
                  font=("Helvetica", 10)).pack(side="right", padx=4)

        self.reset()

    # -- state ------------------------------------------------------------

    def reset(self, event=None) -> None:
        self.step = 0
        self.trace: list[tuple[float, float]] = []
        self.outline: list[tuple[float, float]] | None = None
        self.door = None
        self.sensors = []
        self.sensor_i = 0
        self.pico = None
        self.pi = None
        self.aim_from = None
        self._update()

    def start(self) -> None:
        self.reset()
        self.pack(fill="both", expand=True)

    # -- input ---------------------------------------------------------------

    def _meters(self, event):
        return (event.x / self.pxm, event.y / self.pxm)

    def _press(self, event) -> None:
        x, y = self._meters(event)
        if self.step == 0:
            self.trace = [(x, y)]
        elif self.step == 1:
            if self.outline:
                snapped = snap_to_polygon(self.outline, (x, y))
                d = math.hypot(snapped[0] - x, snapped[1] - y)
                if d < 0.7:
                    self.door = list(snapped)
                    self._advance()
                else:
                    self._hint("tap closer to a wall")
        elif self.step in (2, 3, 4, 5):
            self._place(x, y, event)

    def _drag(self, event) -> None:
        if self.step != 0 or not self.trace:
            return
        x, y = self._meters(event)
        lx, ly = self.trace[-1]
        if math.hypot(x - lx, y - ly) >= 0.04:
            self.trace.append((x, y))
            self._draw()
            if len(self.trace) >= 3:
                self.next_btn.config(state="normal")

    def _place(self, x, y, event) -> None:
        labels = [s.get("label", f"us{i+1}")
                  for i, s in enumerate(self.settings.get("sensors", []))]
        if self.step == 2:
            self.sensors.append({"x": round(x, 2), "y": round(y, 2),
                                 "heading_deg": 0})
            self.aim_from = (x, y)
            self._advance()
        elif self.step == 3:
            sx, sy = self.aim_from
            heading = math.degrees(math.atan2(y - sy, x - sx)) % 360
            self.sensors[-1]["heading_deg"] = round(heading, 1)
            self.sensors[-1]["label"] = (
                labels[self.sensor_i] if self.sensor_i < len(labels)
                else f"us{self.sensor_i + 1}")
            self.sensor_i += 1
            if self.sensor_i >= len(self.settings.get("sensors", [])):
                self._advance()
            else:
                self.step_label.config(
                    text=f"SENSORS: tap where "
                         f"'{labels[self.sensor_i]}' sits")
        elif self.step == 4:
            self.pico = [round(x, 2), round(y, 2)]
            self._advance()
        elif self.step == 5:
            self.pi = [round(x, 2), round(y, 2)]
            self.next_btn.config(state="normal")
            self._draw()

    # -- flow -----------------------------------------------------------------

    def _advance(self) -> None:
        self.step = min(self.step + 1, 5)
        self._update()

    def _next(self) -> None:
        if self.step == 0:
            if len(self.trace) < 3:
                self._hint("trace at least three points")
                return
            self.outline = simplify(self.trace, 0.08)
            self._advance()
        elif self.step in (1, 2, 3, 4):
            self._advance()
        elif self.step == 5:
            self._save()

    def _save(self) -> None:
        room = {
            "outline": [list(p) for p in (self.outline or [])],
            "door": self.door,
            "sensors": self.sensors,
            "pico": self.pico,
            "pi": self.pi,
        }
        if not room["outline"] or len(room["outline"]) < 3:
            room = settings_store.default_room(self.settings)
        settings_store.save_room(room)
        self.pack_forget()
        if self.on_done:
            self.on_done(room)

    def _skip(self) -> None:
        room = settings_store.default_room(self.settings)
        settings_store.save_room(room)
        self.pack_forget()
        if self.on_done:
            self.on_done(room)

    def _clear(self) -> None:
        self.reset()

    def _zoom(self, delta) -> None:
        self.pxm = max(30.0, min(150.0, self.pxm + delta))
        self._draw()

    def _hint(self, text) -> None:
        self.hint.config(text=text)

    def _update(self) -> None:
        text = STEPS[self.step]
        if self.step in (3,) and self.aim_from:
            text = "SENSORS: tap in the direction it points"
        self.step_label.config(text=text)
        self.next_btn.config(
            state="disabled" if self.step == 0 and len(self.trace) < 3
            else "normal")
        self._draw()

    # -- drawing ---------------------------------------------------------------

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        w = c.winfo_width() or 780
        h = c.winfo_height() or 380

        # grid + labels each meter
        gx = 0
        while gx * self.pxm < w:
            c.create_line(gx * self.pxm, 0, gx * self.pxm, h,
                          fill=GRID_A if gx % 2 else GRID_B)
            if gx and gx % 2 == 0:
                c.create_text(gx * self.pxm + 3, 4, anchor="nw",
                              text=f"{gx // 2} m", fill=DIM,
                              font=("Helvetica", 8))
            gx += 1
        gy = 0
        while gy * self.pxm < h:
            c.create_line(0, gy * self.pxm, w, gy * self.pxm,
                          fill=GRID_A if gy % 2 else GRID_B)
            gy += 1

        def P(x, y):
            return (x * self.pxm, y * self.pxm)

        if self.outline:
            pts = [P(x, y) for x, y in self.outline]
            c.create_polygon(*pts, fill=ROOM_FILL, outline=TEXT, width=2)
        elif self.trace:
            pts = [P(x, y) for x, y in self.trace]
            c.create_line(*pts, fill=BLUE, width=3)
            for x, y in self.trace[::4]:
                c.create_oval(P(x, y)[0] - 2, P(x, y)[1] - 2,
                              P(x, y)[0] + 2, P(x, y)[1] + 2,
                              fill=BLUE, outline="")
            c.create_text(w / 2, h - 14, fill=DIM,
                          text=f"{len(self.trace)} points traced",
                          font=("Helvetica", 9))
        if self.door:
            dx, dy = P(*self.door)
            c.create_text(dx, dy, text="\u25bc", fill=AMBER,
                          font=("Helvetica", 12, "bold"))
        for s in self.sensors:
            sx, sy = P(s["x"], s["y"])
            head = math.radians(s.get("heading_deg", 0))
            dx, dy = math.cos(head), math.sin(head)
            c.create_polygon(sx + dx * 9, sy + dy * 9,
                             sx - dy * 4, sy + dx * 4,
                             sx + dy * 4, sy - dx * 4,
                             fill=MARKER, outline="")
            c.create_text(sx - dx * 12, sy - dy * 12,
                          text=s.get("label", "?"), fill=TEXT,
                          font=("Helvetica", 8, "bold"))
        if self.aim_from and self.step == 3 and self.sensors:
            sx, sy = P(*self.aim_from)
            c.create_line(sx, sy, c.winfo_pointerx() - c.winfo_rootx(),
                          c.winfo_pointery() - c.winfo_rooty(),
                          fill=AMBER, dash=(4, 3))
        for pos, label, color in ((self.pico, "Pico", BLUE),
                                  (self.pi, "Pi", GREEN)):
            if pos:
                px, py = P(*pos)
                c.create_oval(px - 5, py - 5, px + 5, py + 5, fill=color,
                              outline="")
                c.create_text(px, py + 11, text=label, fill=color,
                              font=("Helvetica", 8))
