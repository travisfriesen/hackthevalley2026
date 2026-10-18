"""The five touchscreen screens: Dashboard, CheckIn, Alert, HelpSent, Settings.

All screens are flat Tk frames at 800x480 with oversized touch targets. Each
has a refresh(app) the App calls at 10 Hz when visible; screens throttle
their own expensive redraws internally.
"""

from __future__ import annotations

import tkinter as tk

BG = "#0d1421"
PANEL = "#131c2b"
PANEL2 = "#18233a"
TEXT = "#e8eef7"
DIM = "#7d8ea3"
GREEN = "#2ea360"
GREEN_D = "#1f6b40"
RED = "#cf4a4a"
RED_D = "#8f2430"
AMBER = "#d9a013"
BLUE = "#5aa2ff"
HOT = "#ff8a4a"

BIG = ("Helvetica", 15, "bold")
MED = ("Helvetica", 11)
SMALL = ("Helvetica", 9)


class Screen(tk.Frame):
    def __init__(self, master, **kwargs):
        kwargs.setdefault("bg", BG)
        super().__init__(master, **kwargs)

    def refresh(self, app) -> None:
        pass


def big_button(master, text, bg, fg, command, height=6, size=18):
    return tk.Button(
        master, text=text, bg=bg, fg=fg, activebackground=bg,
        activeforeground=fg, relief="flat", bd=0, cursor="hand2",
        font=("Helvetica", size, "bold"),
        command=command)


class Pill(tk.Label):
    def __init__(self, master, width=9):
        super().__init__(master, width=width, bg=PANEL2, fg=DIM,
                         font=("Helvetica", 9, "bold"), padx=6)

    def set(self, text, color):
        self.config(text=text, fg=color)


# == Dashboard ==============================================================

class Dashboard(Screen):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app

        # -- top bar
        top = tk.Frame(self, bg=PANEL, height=44)
        top.pack(fill="x")
        tk.Button(top, text="\u2699", bg=PANEL, fg=TEXT, bd=0,
                  font=("Helvetica", 18), cursor="hand2",
                  command=app.show_settings).pack(side="left", padx=(8, 6))
        self.pill_presence = Pill(top, 11)
        self.pill_presence.pack(side="left", padx=3)
        self.pill_mic = Pill(top, 8)
        self.pill_mic.pack(side="left", padx=3)
        self.pill_csi = Pill(top, 9)
        self.pill_csi.pack(side="left", padx=3)
        self.pill_mode = Pill(top, 8)
        self.pill_mode.pack(side="left", padx=3)
        self.clock = tk.Label(top, text="", bg=PANEL, fg=TEXT,
                              font=("Helvetica", 15, "bold"))
        self.clock.pack(side="right", padx=10)

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

        self.motion = tk.Canvas(right, bg=PANEL, height=58,
                                highlightthickness=0)
        self.motion.pack(fill="x", pady=(0, 4))
        self.ultra = tk.Canvas(right, bg=PANEL, height=76,
                               highlightthickness=0)
        self.ultra.pack(fill="x", pady=(0, 4))
        self.noise = tk.Canvas(right, bg=PANEL, height=56,
                               highlightthickness=0)
        self.noise.pack(fill="x", pady=(0, 4))

        self.events = tk.Text(right, height=4, bg=PANEL, fg=TEXT,
                              font=("Helvetica", 8), relief="flat",
                              state="disabled", wrap="none")
        self.events.pack(fill="x", pady=(0, 4))

        self.demo = tk.Frame(right, bg=BG)
        # demo buttons only packed when the app says the mode is simulated
        for label, cmd in (("WALK", app.demo_walk), ("FALL", app.demo_fall),
                           ("IN", app.demo_enter), ("OUT", app.demo_leave)):
            tk.Button(self.demo, text=label, bg=PANEL2, fg=TEXT, bd=0,
                      font=("Helvetica", 9, "bold"), width=6,
                      cursor="hand2", command=cmd).pack(side="left", padx=2)
        self.take_btn = tk.Button(self.demo, text="TAKE \u25b6", bg=PANEL2,
                                  fg=BLUE, bd=0, font=("Helvetica", 9, "bold"),
                                  width=8, cursor="hand2",
                                  command=app.next_take)
        self.take_btn.pack(side="left", padx=2)

        self.help = big_button(right, "I NEED HELP", RED, "white",
                                app.manual_help, size=16)
        self.help.pack(fill="x", side="bottom", ipady=10)

    def refresh(self, app) -> None:
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
        c.create_text(8, 8, anchor="nw", fill=TEXT, font=SMALL,
                      text="CSI MOTION")
        if score is None:
            c.create_text(w - 8, 8, anchor="ne", fill=DIM, font=SMALL,
                          text="no signal")
            return
        if snap.get("calibrating"):
            c.create_text(w - 8, 8, anchor="ne", fill=AMBER, font=SMALL,
                          text="calibrating...")
            return
        if threshold:
            tx = 8 + threshold / peak * (w - 20)
            c.create_line(tx, 22, tx, 54, fill=AMBER, dash=(3, 3))
            c.create_text(tx + 3, 22, anchor="nw", fill=AMBER, font=SMALL,
                          text="threshold")
        x = 8 + min(1.0, score / peak) * (w - 20)
        c.create_rectangle(8, 34, w - 12, 48, fill="#1d2942", outline="")
        c.create_rectangle(8, 34, x, 48,
                           fill=GREEN if snap.get("moving") else BLUE,
                           outline="")
        moving = snap.get("moving")
        still_for = app.d.get("still_for")
        label = ("MOVING" if moving else
                 f"STILL {still_for:.0f} s" if still_for is not None else "STILL")
        c.create_text(8, 54, anchor="sw", fill=TEXT, font=MED,
                      text=f"{label}    score {score:.3f}")

    def _draw_ultra(self, app) -> None:
        c = self.ultra
        c.delete("all")
        w = c.winfo_width() or 300
        c.create_text(8, 8, anchor="nw", fill=TEXT, font=SMALL,
                      text="ULTRASONIC DEPTH")
        d = app.d
        readings = d["readings"]
        baselines = d["baselines"]
        max_d = d["max_distance_m"]
        for i in range(min(2, len(readings) or 2)):
            r = readings[i] if i < len(readings) else None
            base = baselines[i] if i < len(baselines) else None
            y = 22 + i * 26
            if r is None:
                c.create_text(8, y + 6, fill=DIM, font=SMALL,
                              text=f"us{i+1}  --")
                continue
            c.create_text(8, y + 6, fill=TEXT, font=SMALL,
                          text=f"us{i+1}  {r:4.2f} m")
            if base:
                bx = 70 + base / max_d * (w - 92)
                c.create_line(bx, y - 1, bx, y + 14, fill=AMBER)
            x = 70 + min(1.0, r / max_d) * (w - 92)
            dev = i in d["deviating"]
            c.create_rectangle(70, y, w - 14, y + 13, fill="#1d2942",
                               outline="")
            c.create_rectangle(70, y, 70 + max(2.0, x - 70), y + 13,
                               fill=HOT if dev else BLUE, outline="")

    def _draw_noise(self, app) -> None:
        c = self.noise
        c.delete("all")
        w = c.winfo_width() or 300
        d = app.d
        db = d["noise_dbfs"]
        c.create_text(8, 8, anchor="nw", fill=TEXT, font=SMALL,
                      text="ROOM NOISE")
        c.create_rectangle(8, 26, w - 14, 42, fill="#1d2942", outline="")
        if db is None:
            c.create_text(w - 8, 8, anchor="ne", fill=DIM, font=SMALL,
                          text="recorder not running")
            return
        frac = (db + 60.0) / 60.0
        c.create_rectangle(8, 26, 8 + max(2.0, frac * (w - 22)), 42,
                           fill=GREEN if db < -30 else (AMBER if db < -18
                                                       else RED),
                           outline="")
        c.create_text(w - 8, 34, anchor="e", fill=TEXT, font=SMALL,
                      text=f"{db:4.0f} dBFS")
        c.create_text(8, 48, anchor="sw", fill=DIM, font=SMALL,
                      text="last 0.5 s of the rolling WAV")

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
                 font=("Helvetica", 32, "bold")).pack(pady=(36, 4))
        self.reason = tk.Label(self, text="", bg=BG, fg=AMBER,
                               font=("Helvetica", 13), wraplength=740)
        self.reason.pack(pady=(0, 8))

        mid = tk.Frame(self, bg=BG)
        mid.pack(pady=(4, 10))
        self.arc = tk.Canvas(mid, width=170, height=170, bg=BG,
                             highlightthickness=0)
        self.arc.pack(side="left", padx=18)
        txt = tk.Frame(mid, bg=BG)
        txt.pack(side="left", padx=6)
        tk.Label(txt, text="respond within", bg=BG, fg=DIM,
                 font=("Helvetica", 12)).pack()
        self.seconds = tk.Label(txt, text="", bg=BG, fg=TEXT,
                                font=("Helvetica", 64, "bold"))
        self.seconds.pack()
        tk.Label(txt, text="seconds", bg=BG, fg=DIM,
                 font=("Helvetica", 12)).pack()

        self.listen = tk.Label(self, text="", bg=BG, fg=DIM,
                               font=("Helvetica", 12))
        self.listen.pack(side="bottom")
        btns = tk.Frame(self, bg=BG)
        btns.pack(side="bottom", pady=16)
        big_button(btns, "I'M OK", GREEN, "white",
                   app.checkin_ok, size=24).pack(
            side="left", padx=8, ipadx=40, ipady=16)
        big_button(btns, "I NEED HELP", RED, "white",
                   app.checkin_help, size=24).pack(
            side="left", padx=8, ipadx=40, ipady=16)

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
            if app.speech.available else "")
        c = self.arc
        c.delete("all")
        frac = max(0.0, min(1.0, remaining / max(1.0, self.total)))
        color = GREEN if frac > 0.5 else (AMBER if frac > 0.25 else RED)
        c.create_oval(10, 10, 160, 160, outline="#223049", width=16)
        if frac > 0.005:
            c.create_arc(10, 10, 160, 160, start=90, extent=360 * frac,
                         style="arc", outline=color, width=16)


# == Alert ==================================================================

class Alert(Screen):
    def __init__(self, master, app):
        super().__init__(master, bg=RED)
        tk.Label(self, text="EMERGENCY ALERT", bg=RED, fg="white",
                 font=("Helvetica", 34, "bold")).pack(pady=(40, 2))
        tk.Label(self, text="Tap CANCEL if you do not need help",
                 bg=RED, fg="#ffe3e3",
                 font=("Helvetica", 15)).pack(pady=2)
        self.seconds = tk.Label(self, text="", bg=RED, fg="white",
                                font=("Helvetica", 72, "bold"))
        self.seconds.pack(pady=(6, 2))
        self.reason = tk.Label(self, text="", bg=RED, fg="#ffd9d9",
                               font=("Helvetica", 10), wraplength=700)
        self.reason.pack(pady=(2, 6))
        big_button(self, "I'M OK  \u2014  CANCEL ALERT", "white", RED_D,
                   app.alert_cancel, size=22).pack(
            side="bottom", pady=18, ipadx=60, ipady=14)
        self._flash = False

    def refresh(self, app) -> None:
        d = app.d
        remaining = d["state"].get("remaining") or 0.0
        self.seconds.config(text=f"{remaining:.0f}")
        self.reason.config(text=d["state"].get("reason") or "")
        self._flash = not self._flash
        color = RED if self._flash else RED_D
        self.config(bg=color)
        for child in self.winfo_children():
            if isinstance(child, tk.Button):
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
                              font=("Helvetica", 26, "bold"))
        self.title.pack(pady=(38, 4))
        self.contact = tk.Label(self, text="", bg=BG, fg=AMBER,
                                font=("Helvetica", 14))
        self.contact.pack(pady=(0, 8))
        self.steps = tk.Canvas(self, bg=PANEL, height=150, width=420,
                               highlightthickness=0)
        self.steps.pack(pady=(4, 8))
        self.clear = big_button(self, "ALL CLEAR", GREEN, "white",
                                app.helpsent_clear, size=22)
        tk.Label(self, text="simulated call \u2014 demo",
                 bg=BG, fg=DIM, font=("Helvetica", 9)).pack(side="bottom",
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
                              font=("Helvetica", 12, "bold"))
                done += 1
            elif elapsed >= t0:
                c.create_oval(24, y - 10, 44, y + 10, fill=AMBER,
                              outline="")
            else:
                c.create_oval(24, y - 10, 44, y + 10, fill="#243350",
                              outline="")
            c.create_text(56, y, anchor="w", fill=TEXT, font=MED, text=text)
        if done >= len(self.STAGES):
            self.title.config(text="HELP IS ON THE WAY", fg=GREEN)
            self.clear.pack(side="bottom", pady=12, ipadx=60, ipady=12)
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
                 font=("Helvetica", 18, "bold")).pack(pady=(10, 2))

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
            tk.Button(row, text=" \u2212 ", bg=PANEL2, fg=TEXT, bd=0,
                      font=("Helvetica", 11),
                      command=lambda k=key: self._step(k, -1)).pack(
                side="left")
            tk.Label(row, textvariable=var, bg=PANEL, fg=AMBER, width=8,
                     font=("Helvetica", 10, "bold")).pack(side="left")
            tk.Button(row, text=" + ", bg=PANEL2, fg=TEXT, bd=0,
                      font=("Helvetica", 11),
                      command=lambda k=key: self._step(k, +1)).pack(
                side="left")
            tk.Label(row, text=unit, bg=PANEL, fg=DIM,
                     font=SMALL).pack(side="left", padx=4)

        right = tk.Frame(body, bg=BG)
        right.pack(side="right", fill="both", expand=True)
        r = 0

        def row(label, widget):
            nonlocal r
            frame = tk.Frame(right, bg=PANEL)
            frame.pack(fill="x", pady=3)
            tk.Label(frame, text=label, bg=PANEL, fg=TEXT, font=SMALL,
                     width=12, anchor="w").pack(side="left", padx=6)
            widget.pack(side="left", padx=4)
            r += 1

        self.var_mute = tk.BooleanVar(value=self.draft["voice_muted"])
        row("Mute voice", tk.Checkbutton(
            right, variable=self.var_mute, bg=PANEL, fg=TEXT,
            activebackground=PANEL, bd=0))

        self.var_voice = tk.StringVar(value=self.draft["gemini_voice"])
        row("Voice", tk.OptionMenu(right, self.var_voice, *GEMINI_VOICES))
        self.var_key = tk.StringVar(value=self.draft.get("gemini_api_key", ""))
        row("API key", tk.Entry(right, textvariable=self.var_key, width=20,
                                show="\u2022"))
        self.var_name = tk.StringVar(value=self.draft["contact_name"])
        row("Contact", tk.Entry(right, textvariable=self.var_name, width=20))
        self.var_phone = tk.StringVar(value=self.draft["contact_phone"])
        row("Phone", tk.Entry(right, textvariable=self.var_phone, width=20))
        self.var_mac = tk.StringVar(value=self.draft["csi_mac"])
        row("Pico MAC", tk.Entry(right, textvariable=self.var_mac, width=20))

        btns = tk.Frame(right, bg=BG)
        btns.pack(fill="x", pady=6)
        tk.Button(btns, text="Calibrate empty room", bg=PANEL2, fg=TEXT,
                  bd=0, font=SMALL, cursor="hand2",
                  command=app.start_calibration).pack(
            side="left", padx=2, ipadx=4, ipady=4)
        tk.Button(btns, text="Room setup", bg=PANEL2, fg=TEXT, bd=0,
                  font=SMALL, cursor="hand2",
                  command=app.run_room_setup).pack(
            side="left", padx=2, ipadx=4, ipady=4)
        tk.Button(btns, text="Regenerate voice", bg=PANEL2, fg=TEXT, bd=0,
                  font=SMALL, cursor="hand2",
                  command=app.regen_voice).pack(
            side="left", padx=2, ipadx=4, ipady=4)

        btns2 = tk.Frame(self, bg=BG)
        btns2.pack(fill="x", pady=6)
        tk.Button(btns2, text="TEST ALERT", bg=RED_D, fg="white", bd=0,
                  font=("Helvetica", 11, "bold"), cursor="hand2",
                  command=app.manual_help).pack(side="left", padx=8, ipady=6)
        tk.Button(btns2, text="Save & Back", bg=GREEN, fg="white", bd=0,
                  font=("Helvetica", 11, "bold"), cursor="hand2",
                  command=self._save).pack(side="right", padx=8, ipady=6)

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
