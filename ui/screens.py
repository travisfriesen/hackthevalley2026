"""The touchscreen screens: Dashboard, CheckIn, Alert, HelpSent, Settings, Training.

All screens are flat Tk frames at 800x480 with oversized touch targets. Each
has a refresh(app) the App calls at 10 Hz when visible; screens throttle
their own expensive redraws internally.
"""

from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path

from theme import FONT, AMBER, BG, BLUE, DIM, GREEN, GREEN_D, HOT, ON_RED_1, ON_RED_2, PANEL, PANEL2, RED, RED_D, TEXT, TRACK

ASSETS = Path(__file__).resolve().parent / "assets"


BIG = (FONT, 15, "bold")
MED = (FONT, 11)
SMALL = (FONT, 9)


class TouchButton(tk.Label):
    """A button that honours bg/fg on every platform (macOS Tk ignores them on tk.Button).

    Dims while pressed; fires on release if the finger is still over it.
    """

    def __init__(self, master, command=None, activebackground=None,
                 activeforeground=None, **kwargs):
        kwargs.setdefault("cursor", "hand2")
        super().__init__(master, **kwargs)
        self._command = command
        self._bg = kwargs.get("bg")
        self._down = False
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)

    def _press(self, _event) -> None:
        self._down = True
        r, g, b = (c * 80 // 100 // 257 for c in self.winfo_rgb(self._bg))
        self.config(bg=f"#{r:02x}{g:02x}{b:02x}")

    def _release(self, event) -> None:
        self.config(bg=self._bg)
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        if self._down and inside and self._command:
            self._command()
        self._down = False


class Screen(tk.Frame):
    def __init__(self, master, **kwargs):
        kwargs.setdefault("bg", BG)
        super().__init__(master, **kwargs)

    def refresh(self, app) -> None:
        pass


def big_button(master, text, bg, fg, command, height=6, size=18):
    return TouchButton(
        master, text=text, bg=bg, fg=fg, activebackground=bg,
        activeforeground=fg, relief="flat", bd=0, cursor="hand2",
        font=(FONT, size, "bold"),
        command=command)


class Pill(tk.Label):
    def __init__(self, master, width=9):
        super().__init__(master, width=width, bg=PANEL2, fg=DIM,
                         font=(FONT, 9, "bold"), padx=6)

    def set(self, text, color):
        self.config(text=text, fg=color)


# == Dashboard ==============================================================

class Dashboard(Screen):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app

        # -- top bar
        top = tk.Frame(self, bg=PANEL, height=56)
        top.pack(fill="x")
        TouchButton(top, text="\u25c0  HOME", bg=GREEN, fg="white", bd=0,
                  activebackground=GREEN_D, activeforeground="white",
                  font=(FONT, 14, "bold"), cursor="hand2",
                  command=app.show_home).pack(side="left", padx=(6, 4),
                                              ipadx=14, ipady=8)
        TouchButton(top, text="\u2699", bg=PANEL2, fg=TEXT, bd=0,
                  font=(FONT, 22), cursor="hand2",
                  command=app.show_settings).pack(side="left", padx=(0, 6),
                                                  ipadx=14, ipady=2)
        self._logo = tk.PhotoImage(file=str(ASSETS / "logo_icon.png"))
        tk.Label(top, image=self._logo, bg=PANEL).pack(side="left", padx=(0, 2))
        tk.Label(top, text="DownLink", bg=PANEL, fg=TEXT,
                 font=(FONT, 14, "bold")).pack(side="left", padx=(0, 10))
        self.pill_presence = Pill(top, 11)
        self.pill_presence.pack(side="left", padx=3)
        self.pill_mic = Pill(top, 8)
        self.pill_mic.pack(side="left", padx=3)
        self.pill_csi = Pill(top, 9)
        self.pill_csi.pack(side="left", padx=3)
        self.pill_mode = Pill(top, 8)
        self.pill_mode.pack(side="left", padx=3)
        self.clock = tk.Label(top, text="", bg=PANEL, fg=TEXT,
                              font=(FONT, 15, "bold"))
        self.clock.pack(side="right", padx=10)
        # calibration countdown, big enough to read from across the room. A
        # child of the root window, like the room map (which is repacked over
        # the dashboard): as a child of the dashboard it sat behind the map.
        self.cal_banner = tk.Label(self.winfo_toplevel(), bg=AMBER, fg=BG,
                                   justify="center",
                                   font=(FONT, 44, "bold"))

        # -- left column: room map + CSI waterfall
        # the room canvas's master is the root window so it can be repacked
        # over the whole screen when zoomed (Tk can only pack `in_` a window
        # that is the parent or a descendant of the parent)
        left = tk.Frame(self, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=(6, 3))
        self.left = left
        from room_view import RoomCanvas
        from waterfall import Waterfall
        self.room = RoomCanvas(self.winfo_toplevel(), on_tap=app.toggle_room_zoom)
        self.room.pack(in_=left, fill="both", expand=True)
        self.waterfall = Waterfall(left)
        self.waterfall.pack(fill="x", pady=(4, 0))

        # -- right column: metrics + events + help
        right = tk.Frame(self, bg=BG, width=322)
        right.pack(side="right", fill="y", padx=(3, 6))
        right.pack_propagate(False)

        self.motion = tk.Canvas(right, bg=PANEL, height=80,
                                highlightthickness=0)
        self.motion.pack(fill="x", pady=(0, 4))
        self.ultra = tk.Canvas(right, bg=PANEL, height=90,
                               highlightthickness=0)
        self.ultra.pack(fill="x", pady=(0, 4))
        self.noise = tk.Canvas(right, bg=PANEL, height=56,
                               highlightthickness=0)
        self.noise.pack(fill="x", pady=(0, 4))

        self.events = tk.Text(right, height=3, bg=PANEL, fg=TEXT,
                              font=(FONT, 8), relief="flat", bd=0,
                              highlightthickness=0,
                              state="disabled", wrap="none")
        self.events.pack(fill="x", pady=(0, 4))

        self.demo = tk.Frame(right, bg=BG)
        # demo buttons only packed when the app says the mode is simulated
        for label, cmd in (("WALK", app.demo_walk), ("FALL", app.demo_fall),
                           ("IN", app.demo_enter), ("OUT", app.demo_leave)):
            TouchButton(self.demo, text=label, bg=PANEL2, fg=TEXT, bd=0,
                      font=(FONT, 9, "bold"), width=6,
                      cursor="hand2", command=cmd).pack(side="left", padx=2)
        self.take_btn = TouchButton(self.demo, text="TAKE \u25b6", bg=PANEL2,
                                  fg=BLUE, bd=0, font=(FONT, 9, "bold"),
                                  width=8, cursor="hand2",
                                  command=app.next_take)
        self.take_btn.pack(side="left", padx=2)

        self.help = big_button(right, "I NEED HELP", RED, "white",
                                app.manual_help, size=22)
        self.help.pack(fill="x", side="bottom", ipady=26)

    def set_banner(self, text) -> None:
        """Show the calibration banner over everything, or hide it (None)."""
        if text:
            if self.cal_banner.cget("text") != text:
                self.cal_banner.config(text=text)
            self.cal_banner.place(relx=0.5, rely=0.5, anchor="center",
                                  relwidth=0.8, relheight=0.5)
            self.cal_banner.lift()
        else:
            self.cal_banner.place_forget()

    # Redraw at 5 Hz while the logic still ticks at 10 Hz: on the Pi (Tk -> Xwayland)
    # the redraw rate drives CPU use and the lag, and bars/maps look fine at 5 Hz.
    DRAW_INTERVAL = 0.2

    def refresh(self, app) -> None:
        if app.now - getattr(self, "_last_draw", -1.0) < self.DRAW_INTERVAL:
            return
        self._last_draw = app.now
        d = app.d
        self.clock.config(text=d["clock"])
        self.pill_presence.set(
            "PERSON IN ROOM" if d["presence"] else "ROOM EMPTY",
            GREEN if d["presence"] else DIM)
        if d["noise_dbfs"] is None:
            self.pill_mic.set("MIC OFF", DIM)
        elif d["mic_fresh"]:
            self.pill_mic.set("MIC OK", GREEN)
        else:
            self.pill_mic.set("MIC STALE", AMBER)
        mode = d["csi_mode"]
        self.pill_mode.set({"live": "LIVE", "replay": "REPLAY",
                            "sim": "SIM"}[mode], GREEN if mode == "live" else BLUE)
        if d["motion"]["alive"]:
            self.pill_csi.set(f"CSI {d['motion']['hz']:.0f} Hz", GREEN)
        else:
            self.pill_csi.set("CSI OFFLINE", DIM)

        self._draw_motion(app)
        self._draw_ultra(app)
        self._draw_noise(app)
        self._draw_events(app)

        if mode in ("sim", "replay"):
            if not getattr(self, "_demo_visible", False):
                self._demo_visible = True
                self.demo.pack(fill="x", pady=(0, 4), side="bottom")
        elif getattr(self, "_demo_visible", False):
            self._demo_visible = False
            self.demo.pack_forget()

        self.room.refresh(app.room_data(), app.now)
        self.waterfall.refresh(d["motion"], app.motion_history, app.now)

    def _draw_motion(self, app) -> None:
        c = self.motion
        c.delete("all")
        w = c.winfo_width() or 300
        snap = app.d["motion"]
        score = snap.get("motion_score")
        threshold = snap.get("threshold")
        baseline = snap.get("baseline")
        peak = max(0.06, (score or 0), (threshold or 0) * 1.3,
                  (baseline or 0) * 1.4)
        c.create_text(8, 6, anchor="nw", fill=TEXT, font=SMALL,
                      text="CSI MOTION")
        if score is None:
            c.create_text(w - 8, 6, anchor="ne", fill=DIM, font=SMALL,
                          text="no signal")
            return
        if snap.get("calibrating"):
            c.create_text(w - 8, 6, anchor="ne", fill=AMBER, font=SMALL,
                          text="calibrating...")
            return
        if threshold:
            tx = 8 + threshold / peak * (w - 20)
            c.create_line(tx, 26, tx, 50, fill=AMBER, dash=(3, 3))
            c.create_text(w - 8, 6, anchor="ne", fill=AMBER, font=SMALL,
                          text="- - threshold")
        x = 8 + min(1.0, score / peak) * (w - 20)
        c.create_rectangle(8, 30, w - 12, 46, fill=TRACK, outline="")
        c.create_rectangle(8, 30, x, 46,
                           fill=GREEN if snap.get("moving") else BLUE,
                           outline="")
        moving = snap.get("moving")
        still_for = app.d.get("still_for")
        label = ("MOVING" if moving else
                 f"STILL {still_for:.0f} s" if still_for is not None else "STILL")
        c.create_text(8, 54, anchor="nw", fill=TEXT, font=MED,
                      text=f"{label}    score {score:.3f}")

    def _draw_ultra(self, app) -> None:
        c = self.ultra
        c.delete("all")
        w = c.winfo_width() or 300
        c.create_text(8, 6, anchor="nw", fill=TEXT, font=SMALL,
                      text="ULTRASONIC DEPTH")
        d = app.d
        readings = d["readings"]
        baselines = d["baselines"]
        max_d = d["max_distance_m"]
        x0 = 104                        # bars start right of the "us1  3.86 m" label
        for i in range(min(2, len(readings) or 2)):
            r = readings[i] if i < len(readings) else None
            base = baselines[i] if i < len(baselines) else None
            y = 30 + i * 28
            if r is None:
                c.create_text(8, y + 7, anchor="w", fill=DIM, font=SMALL,
                              text=f"us{i+1}  --")
                continue
            c.create_text(8, y + 7, anchor="w", fill=TEXT, font=SMALL,
                          text=f"us{i+1}  {r:4.2f} m")
            if base:
                bx = x0 + base / max_d * (w - x0 - 22)
                c.create_line(bx, y - 2, bx, y + 16, fill=AMBER)
            x = x0 + min(1.0, r / max_d) * (w - x0 - 22)
            dev = i in d["deviating"]
            c.create_rectangle(x0, y, w - 14, y + 14, fill=TRACK,
                               outline="")
            c.create_rectangle(x0, y, max(x0 + 2.0, x), y + 14,
                               fill=HOT if dev else BLUE, outline="")

    def _draw_noise(self, app) -> None:
        c = self.noise
        c.delete("all")
        w = c.winfo_width() or 300
        d = app.d
        db = d["noise_dbfs"]
        c.create_text(8, 6, anchor="nw", fill=TEXT, font=SMALL,
                      text="ROOM NOISE")
        c.create_rectangle(8, 30, w - 14, 46, fill=TRACK, outline="")
        if db is None:
            c.create_text(w - 8, 6, anchor="ne", fill=DIM, font=SMALL,
                          text="recorder not running")
            return
        frac = (db + 60.0) / 60.0
        c.create_rectangle(8, 30, 8 + max(2.0, frac * (w - 22)), 46,
                           fill=GREEN if db < -30 else (AMBER if db < -18
                                                       else RED),
                           outline="")
        c.create_text(w - 8, 6, anchor="ne", fill=TEXT, font=SMALL,
                      text=f"{db:.0f} dBFS")

    def _draw_events(self, app) -> None:
        sig = tuple(app.d["events"])
        if getattr(self, "_events_sig", None) == sig:
            return
        self._events_sig = sig
        self.events.config(state="normal")
        self.events.delete("1.0", "end")
        for stamp, text in app.d["events"]:
            self.events.insert("end", f"{stamp}  {text}\n")
        self.events.config(state="disabled")


# == Check-in ================================================================

class CheckIn(Screen):
    def __init__(self, master, app):
        super().__init__(master)
        self.total = 30.0
        tk.Label(self, text="ARE YOU OKAY?", bg=BG, fg=TEXT,
                 font=(FONT, 32, "bold")).pack(pady=(36, 4))
        self.reason = tk.Label(self, text="", bg=BG, fg=AMBER,
                               font=(FONT, 13), wraplength=740)
        self.reason.pack(pady=(0, 8))

        mid = tk.Frame(self, bg=BG)
        mid.pack(pady=(4, 10))
        self.arc = tk.Canvas(mid, width=170, height=170, bg=BG,
                             highlightthickness=0)
        self.arc.pack(side="left", padx=18)
        txt = tk.Frame(mid, bg=BG)
        txt.pack(side="left", padx=6)
        tk.Label(txt, text="respond within", bg=BG, fg=DIM,
                 font=(FONT, 12)).pack()
        self.seconds = tk.Label(txt, text="", bg=BG, fg=TEXT,
                                font=(FONT, 64, "bold"))
        self.seconds.pack()
        tk.Label(txt, text="seconds", bg=BG, fg=DIM,
                 font=(FONT, 12)).pack()

        self.listen = tk.Label(self, text="", bg=BG, fg=DIM,
                               font=(FONT, 12))
        self.listen.pack(side="bottom")
        btns = tk.Frame(self, bg=BG)
        btns.pack(side="bottom", pady=16)
        big_button(btns, "I'M OK", GREEN, "white",
                   app.checkin_ok, size=28).pack(
            side="left", padx=10, ipadx=44, ipady=30)
        big_button(btns, "I NEED HELP", RED, "white",
                   app.checkin_help, size=28).pack(
            side="left", padx=10, ipadx=44, ipady=30)

    def refresh(self, app) -> None:
        d = app.d
        self.total = app.settings["checkin_seconds"]
        remaining = d["state"].get("remaining") or 0.0
        self.seconds.config(
            text=f"{remaining:.0f}",
            fg=TEXT if remaining > 10 else RED)
        self.reason.config(text=d["state"].get("reason") or "")
        self.listen.config(
            text='or say "yes", "no" or "I need help"'
            if app.voice_reply_ready() else "")
        c = self.arc
        c.delete("all")
        frac = max(0.0, min(1.0, remaining / max(1.0, self.total)))
        color = GREEN if frac > 0.5 else (AMBER if frac > 0.25 else RED)
        c.create_oval(10, 10, 160, 160, outline=TRACK, width=16)
        if frac > 0.005:
            c.create_arc(10, 10, 160, 160, start=90, extent=360 * frac,
                         style="arc", outline=color, width=16)


# == Alert ==================================================================

class Alert(Screen):
    def __init__(self, master, app):
        super().__init__(master, bg=RED)
        tk.Label(self, text="EMERGENCY ALERT", bg=RED, fg="white",
                 font=(FONT, 34, "bold")).pack(pady=(40, 2))
        tk.Label(self, text="Tap CANCEL if you do not need help",
                 bg=RED, fg=ON_RED_1,
                 font=(FONT, 15)).pack(pady=2)
        self.seconds = tk.Label(self, text="", bg=RED, fg="white",
                                font=(FONT, 72, "bold"))
        self.seconds.pack(pady=(6, 2))
        self.reason = tk.Label(self, text="", bg=RED, fg=ON_RED_2,
                               font=(FONT, 10), wraplength=700)
        self.reason.pack(pady=(2, 6))
        big_button(self, "I'M OK  \u2014  CANCEL ALERT", "white", RED_D,
                   app.alert_cancel, size=22).pack(
            side="bottom", pady=18, ipadx=60, ipady=26)

    PULSE_PERIOD_S = 3.0        # slow, smooth swell: never a flash (<< 3 per second)

    @classmethod
    def _pulse_color(cls, now) -> str:
        """Ease between RED and RED_D on a sine wave."""
        k = (1 - math.cos(2 * math.pi * now / cls.PULSE_PERIOD_S)) / 2
        a, b = (int(RED[i:i + 2], 16) for i in (1, 3, 5)), (int(RED_D[i:i + 2], 16) for i in (1, 3, 5))
        return "#" + "".join(f"{round(x + (y - x) * k):02x}" for x, y in zip(a, b))

    def refresh(self, app) -> None:
        d = app.d
        remaining = d["state"].get("remaining") or 0.0
        self.seconds.config(text=f"{remaining:.0f}")
        self.reason.config(text=d["state"].get("reason") or "")
        color = self._pulse_color(app.now)
        self.config(bg=color)
        for child in self.winfo_children():
            if isinstance(child, TouchButton):
                continue          # keep the white cancel button readable
            try:
                child.config(bg=color)
            except tk.TclError:
                pass


# == HelpSent ================================================================

class HelpSent(Screen):
    STAGES = ["Calling trusted contact...", "Location shared",
              "Voice message sent"]
    STAGE_S = 2.6

    def __init__(self, master, app):
        super().__init__(master)
        self.entered = None
        self.title = tk.Label(self, text="HELP IS BEING CONTACTED",
                              bg=BG, fg=TEXT,
                              font=(FONT, 26, "bold"))
        self.title.pack(pady=(38, 4))
        self.contact = tk.Label(self, text="", bg=BG, fg=AMBER,
                                font=(FONT, 14))
        self.contact.pack(pady=(0, 8))
        self.steps = tk.Canvas(self, bg=PANEL, height=150, width=420,
                               highlightthickness=0)
        self.steps.pack(pady=(4, 8))
        self.clear = big_button(self, "ALL CLEAR", GREEN, "white",
                                app.helpsent_clear, size=22)
        tk.Label(self, text="simulated call \u2014 demo",
                 bg=BG, fg=DIM, font=(FONT, 9)).pack(side="bottom",
                                                            pady=4)

    def set_entered(self, now) -> None:
        self.entered = now

    def refresh(self, app) -> None:
        if self.entered is None:
            self.set_entered(app.now)
        elapsed = app.now - self.entered
        s = app.settings
        self.contact.config(
            text=f"{s['contact_name']}  \u2022  {s['contact_phone']}")
        c = self.steps
        c.delete("all")
        done = 0
        for i, text in enumerate(self.STAGES):
            t0 = i * self.STAGE_S
            y = 30 + i * 38
            if elapsed >= t0 + self.STAGE_S:
                c.create_oval(24, y - 10, 44, y + 10, fill=GREEN,
                              outline="")
                c.create_text(34, y, text="\u2713", fill="white",
                              font=(FONT, 12, "bold"))
                done += 1
            elif elapsed >= t0:
                c.create_oval(24, y - 10, 44, y + 10, fill=AMBER,
                              outline="")
            else:
                c.create_oval(24, y - 10, 44, y + 10, fill=TRACK,
                              outline="")
            c.create_text(56, y, anchor="w", fill=TEXT, font=MED, text=text)
        if done >= len(self.STAGES):
            self.title.config(text="HELP IS ON THE WAY", fg=GREEN)
            self.clear.pack(side="bottom", pady=12, ipadx=60, ipady=26)
        else:
            self.title.config(text="HELP IS BEING CONTACTED", fg=TEXT)


# == Settings ================================================================

GEMINI_VOICES = [
    "Kore", "Charon", "Sulafat", "Puck", "Zephyr", "Leda", "Fenrir",
    "Orus", "Aoede", "Callirrhoe", "Autonoe", "Iapetus", "Umbriel",
    "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
    "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux",
    "Pulcherrima", "Achird", "Zubenelgenubi", "Vindemiatrix",
    "Sadachbia", "Sadaltager", "Enceladus",
]

STEEPERS = [
    ("Check-in countdown", "checkin_seconds", 10, 120, 5, "s"),
    ("Stillness triggers help", "still_seconds", 20, 300, 5, "s"),
    ("Alert cancel grace", "alert_grace_seconds", 5, 30, 1, "s"),
    ("Voice repeat", "voice_repeat_seconds", 5, 30, 1, "s"),
    ("Motion factor", "motion_factor", 1.1, 2.5, 0.1, "x"),
    ("Presence deviation", "presence_dev_cm", 10, 80, 5, "cm"),
]


class Settings(Screen):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.draft = dict(app.settings)

        tk.Label(self, text="SETTINGS", bg=BG, fg=TEXT,
                 font=(FONT, 18, "bold")).pack(pady=(10, 2))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14)

        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        self._stepper_vars = {}
        for label, key, lo, hi, step, unit in STEEPERS:
            row = tk.Frame(left, bg=PANEL)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=PANEL, fg=TEXT, font=SMALL,
                     width=22, anchor="w").pack(side="left", padx=6)
            var = tk.StringVar()
            self._stepper_vars[key] = (var, lo, hi, step, unit)
            TouchButton(row, text="\u2212", bg=PANEL2, fg=TEXT, bd=0,
                      font=(FONT, 16, "bold"),
                      command=lambda k=key: self._step(k, -1)).pack(
                side="left", ipadx=12, ipady=2)
            tk.Label(row, textvariable=var, bg=PANEL, fg=AMBER, width=8,
                     font=(FONT, 10, "bold")).pack(side="left")
            TouchButton(row, text="+", bg=PANEL2, fg=TEXT, bd=0,
                      font=(FONT, 16, "bold"),
                      command=lambda k=key: self._step(k, +1)).pack(
                side="left", ipadx=12, ipady=2)
            tk.Label(row, text=unit, bg=PANEL, fg=DIM,
                     font=SMALL).pack(side="left", padx=4)

        right = tk.Frame(body, bg=BG)
        right.pack(side="right", fill="both", expand=True)

        def row(label, make):
            """One settings row; `make(frame)` builds the control inside it."""
            frame = tk.Frame(right, bg=PANEL)
            frame.pack(fill="x", pady=3)
            tk.Label(frame, text=label, bg=PANEL, fg=TEXT, font=SMALL,
                     width=10, anchor="w").pack(side="left", padx=6, pady=8)
            make(frame).pack(side="left", padx=4)

        def entry(var, show=None):
            return lambda f: tk.Entry(
                f, textvariable=var, width=18, show=show or "", font=MED,
                bg="white", fg=TEXT, insertbackground=TEXT, relief="flat",
                highlightthickness=1, highlightbackground=TRACK,
                highlightcolor=BLUE)

        self.var_mute = tk.BooleanVar(value=self.draft["voice_muted"])
        row("Mute voice", lambda f: tk.Checkbutton(
            f, variable=self.var_mute, bg=PANEL, fg=TEXT,
            activebackground=PANEL, bd=0, highlightthickness=0))
        self.var_voice = tk.StringVar(value=self.draft["gemini_voice"])
        row("Voice", lambda f: tk.OptionMenu(f, self.var_voice, *GEMINI_VOICES))
        self.var_key = tk.StringVar(value=self.draft.get("gemini_api_key", ""))
        row("API key", entry(self.var_key, show="\u2022"))
        self.var_name = tk.StringVar(value=self.draft["contact_name"])
        row("Contact", entry(self.var_name))
        self.var_phone = tk.StringVar(value=self.draft["contact_phone"])
        row("Phone", entry(self.var_phone))
        self.var_mac = tk.StringVar(value=self.draft["csi_mac"])
        row("Pico MAC", entry(self.var_mac))

        btns2 = tk.Frame(self, bg=BG)
        btns2.pack(fill="x", pady=6)
        TouchButton(btns2, text="TEST ALERT", bg=RED_D, fg="white", bd=0,
                  font=(FONT, 11, "bold"), cursor="hand2",
                  command=app.manual_help).pack(side="left", padx=8, ipady=12, ipadx=6)
        # in the bottom bar, not the crowded right column, where they were
        # squeezed to nothing on the 7" screen
        for text, command in (("Calibrate empty room", app.start_calibration),
                              ("Guided training", app.start_training),
                              ("Room setup", app.run_room_setup),
                              ("Regenerate voice", app.regen_voice)):
            TouchButton(btns2, text=text, bg=PANEL2, fg=TEXT, bd=0, font=SMALL,
                      cursor="hand2", command=command).pack(
                side="left", padx=2, ipadx=4, ipady=12)
        TouchButton(btns2, text="Save & Back", bg=GREEN, fg="white", bd=0,
                  font=(FONT, 13, "bold"), cursor="hand2",
                  command=self._save).pack(side="right", padx=8, ipady=12,
                                           ipadx=8)

        self._refresh_steppers()

    def _refresh_steppers(self) -> None:
        for key, (var, lo, hi, step, unit) in self._stepper_vars.items():
            value = self.draft[key]
            text = f"{value:g}" if isinstance(value, float) else str(value)
            var.set(text)

    def _step(self, key, sign) -> None:
        _var, lo, hi, step, _unit = self._stepper_vars[key]
        value = self.draft[key] + sign * step
        value = round(min(hi, max(lo, value)), 2)
        self.draft[key] = value
        self._refresh_steppers()

    def _save(self) -> None:
        s = self.draft
        s["voice_muted"] = self.var_mute.get()
        s["gemini_voice"] = self.var_voice.get()
        s["gemini_api_key"] = self.var_key.get().strip()
        s["contact_name"] = self.var_name.get().strip() or "Contact"
        s["contact_phone"] = self.var_phone.get().strip()
        s["csi_mac"] = self.var_mac.get().strip()
        self.app.apply_settings(s)
        self.app.show_dashboard()

    def refresh(self, app) -> None:
        pass


# == Guided training =========================================================

class TrainingScreen(Screen):
    """What to do now, and the countdown: the spoken instructions on screen."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        tk.Label(self, text="GUIDED TRAINING", bg=BG, fg=TEXT,
                 font=(FONT, 18, "bold")).pack(pady=(14, 0))
        self.step = tk.Label(self, text="", bg=BG, fg=DIM, font=MED)
        self.step.pack()
        self.label = tk.Label(self, text="", bg=BG, fg=AMBER,
                              font=(FONT, 34, "bold"))
        self.label.pack(pady=(10, 0))
        self.text = tk.Label(self, text="", bg=BG, fg=TEXT, wraplength=720,
                             font=(FONT, 16))
        self.text.pack(pady=(6, 0))
        self.big = tk.Label(self, text="", bg=BG, fg=TEXT,
                            font=(FONT, 64, "bold"))
        self.big.pack(expand=True)
        self.note = tk.Label(self, text="", bg=BG, fg=DIM, font=MED)
        self.note.pack()
        btns = tk.Frame(self, bg=BG)
        btns.pack(side="bottom", pady=14)
        big_button(btns, "SKIP TAKE", PANEL2, TEXT, app.skip_training_take,
                   size=16).pack(side="left", padx=8, ipadx=24, ipady=10)
        big_button(btns, "STOP", RED_D, "white", app.stop_training,
                   size=16).pack(side="left", padx=8, ipadx=40, ipady=10)

    def refresh(self, app) -> None:
        snap = app.training.snapshot(app.now)
        if not snap["active"]:
            return
        phase, left = snap["phase"], snap["remaining"]
        self.step.config(text=f"Take {snap['index'] + 1} of {snap['total']}")
        self.label.config(text=snap["label"].upper())
        self.text.config(text=snap["text"].removeprefix("Next: ").capitalize())
        if phase == "instruct":
            self.big.config(text=f"{left:.0f}", fg=TEXT)
            self.note.config(text="get into position, recording starts after the countdown")
        elif phase == "count":
            self.big.config(text=f"{max(1, round(left + 0.49))}", fg=AMBER)
            self.note.config(text="get ready")
        elif phase == "record":
            self.big.config(text=f"{left:.0f}", fg=RED)
            self.note.config(text="RECORDING")
        else:
            self.big.config(text="done", fg=GREEN)
            self.note.config(text="next take coming up")
