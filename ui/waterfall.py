"""CSI waterfall (what the radio "sees") + the motion strip that drives state.

Left half of the dashboard. The heatmap is the same view as the phase-0
analysis plots: usable subcarriers on y, time scrolling left, colour = gain
normalised amplitude. Under it, the motion score line with the calibrated
threshold - walking visibly lifts the line above the bar, live.
"""

from __future__ import annotations

import tkinter as tk

import numpy as np

BG = "#0a0f18"
PANEL = "#131c2b"
TEXT = "#e8eef7"
DIM = "#7d8ea3"
GREEN = "#2ea360"
RED = "#cf4a4a"
AMBER = "#d9a013"

# 32-step viridis-ish ramp, interpolated from 5 anchors (no matplotlib dep)
_ANCHORS = [
    (0x44, 0x01, 0x54), (0x3b, 0x52, 0x8b), (0x21, 0x91, 0x8c),
    (0x5e, 0xc9, 0x62), (0xfd, 0xe7, 0x25),
]


def _color_list(steps=32):
    out = []
    n = len(_ANCHORS) - 1
    for i in range(steps):
        f = i / (steps - 1) * n
        k = min(int(f), n - 1)
        t = f - k
        a, b = _ANCHORS[k], _ANCHORS[k + 1]
        out.append("#%02x%02x%02x" % tuple(
            round(a[j] + (b[j] - a[j]) * t) for j in range(3)))
    return out


class Waterfall(tk.Frame):
    """Two stacked canvases: heatmap + motion strip. refresh() is cheap when
    nothing new has arrived (called at 10 Hz, draws at 4 / 0.5 Hz)."""

    def __init__(self, master, col_w=4, hist_cols=104, rows=26, **kwargs):
        super().__init__(master, bg=PANEL, **kwargs)
        self.col_w = col_w
        self.hist_cols = hist_cols
        self.rows = rows
        self.colors = _color_list()
        self.palette = np.array(
            [[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in self.colors], np.uint8)

        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x", padx=6, pady=(4, 0))
        self.title = tk.Label(
            head, text="CSI  SUBCARRIERS", bg=PANEL, fg=TEXT,
            font=("Helvetica", 9, "bold"))
        self.title.pack(side="left")
        self.hz_label = tk.Label(head, text="offline", bg=PANEL, fg=DIM,
                                 font=("Helvetica", 9))
        self.hz_label.pack(side="right")

        self.heat = tk.Canvas(self, bg=BG, highlightthickness=0, height=112)
        self.heat.pack(fill="both", expand=True, padx=6, pady=3)
        self.strip = tk.Canvas(self, bg=BG, highlightthickness=0, height=56)
        self.strip.pack(fill="x", padx=6, pady=(0, 5))

        self._img = None              # one PhotoImage; a pixel buffer is shifted left per column
        self._buf = None
        self._last_col_t = 0.0
        self._last_strip_t = 0.0
        self._strip_xlim = 55.0       # seconds shown on the strip
        self.heat.bind("<Configure>", lambda e: self._resized())

    # -- heatmap -------------------------------------------------------------

    def _resized(self) -> None:
        w, h = self.heat.winfo_width(), self.heat.winfo_height()
        if w < 20 or h < 20:
            return
        self._buf = np.zeros((h, w, 3), np.uint8)
        self._buf[:] = [0x0a, 0x0f, 0x18]  # BG
        if self._img is None:
            self._img = tk.PhotoImage(width=w, height=h)
            self.heat.create_image(0, 0, image=self._img, anchor="nw")
        self._show()

    def _show(self) -> None:
        h, w, _ = self._buf.shape
        self._img.configure(data=b"P6 %d %d 255\n" % (w, h) + self._buf.tobytes())

    def _new_column(self, column, now) -> None:
        if column is None or self._buf is None:
            return
        h = self._buf.shape[0]
        rows = min(self.rows, column.size)
        vals = column
        if column.size % rows:
            vals = column[:rows * (column.size // rows + 1)]
        vals = vals[: (vals.size // rows) * rows].reshape(rows, -1).mean(1)
        shade = (np.clip(vals / 1.6, 0.0, 1.0) * 31.999).astype(int)
        col = self.palette[shade][np.arange(h) * rows // h]  # stretch rows to canvas height
        self._buf[:, :-self.col_w] = self._buf[:, self.col_w:]  # scroll left
        self._buf[:, -self.col_w:] = col[:, None, :]
        self._show()

    # -- motion strip ----------------------------------------------------------

    def _draw_strip(self, history, threshold, baseline, moving, label) -> None:
        w = self.strip.winfo_width()
        h = self.strip.winfo_height()
        if w < 20 or h < 20:
            return
        self.strip.delete("ms")
        t1 = history[-1][0] if history else 0.0
        t0 = t1 - self._strip_xlim
        peak = max(0.02, max((s for _t, s in history), default=0.05))

        def xy(t, s):
            return ((t - t0) / self._strip_xlim * w,
                    h - 6 - (s / (peak * 1.35)) * (h - 14))

        if threshold is not None:
            ty = h - 6 - (threshold / (peak * 1.35)) * (h - 14)
            self.strip.create_line(
                0, ty, w, ty, fill=AMBER, dash=(4, 3), tags=("ms",))
            self.strip.create_text(
                w - 4, ty - 7, anchor="e", fill=AMBER,
                font=("Helvetica", 8), text="threshold", tags=("ms",))
        if baseline is not None:
            by = h - 6 - (baseline / (peak * 1.35)) * (h - 14)
            self.strip.create_line(
                0, by, w, by, fill=DIM, dash=(2, 4), tags=("ms",))

        if len(history) >= 2:
            pts = []
            for t, s in history:
                if t >= t0:
                    x, y = xy(t, s)
                    pts.extend((x, y))
            self.strip.create_line(
                *pts, fill="#6fb3ff" if moving else GREEN,
                width=2, tags=("ms",), smooth=True)

        self.strip.create_text(
            4, 4, anchor="nw", fill=TEXT, font=("Helvetica", 9, "bold"),
            text=label, tags=("ms",))

    # -- entry point -----------------------------------------------------------

    def refresh(self, snap, history, now) -> None:
        """history: list[(t, score)] sampled by the App every 0.5 s."""
        alive = snap.get("alive")
        if snap.get("column") is not None and now - self._last_col_t >= 0.25:
            self._last_col_t = now
            self._new_column(snap["column"], now)
        if snap.get("calibrating"):
            hz = "calibrating..."
        elif alive:
            hz = f"{snap['hz']:.0f} Hz"
        else:
            hz = "offline"
        self.hz_label.config(text=hz, fg=TEXT if alive else DIM)

        if now - self._last_strip_t >= 0.5:
            self._last_strip_t = now
            score = snap.get("motion_score")
            moving = snap.get("moving")
            if snap.get("calibrating"):
                state = "calibrating"
            elif moving:
                state = "MOVING"
            elif score is not None:
                state = "STILL"
            else:
                state = "no signal"
            label = state if score is None else f"{state}  {score:.3f}"
            self._draw_strip(history, snap.get("threshold"),
                             snap.get("baseline"), bool(moving), label)
