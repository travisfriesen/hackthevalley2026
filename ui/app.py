"""App: threads, feeds, escalation and screens glued together.

Tick loop (10 Hz, Tk main thread):
  1. step the simulated room (if demo mode) and sample its sensors
  2. pull the CSI snapshot; presence from ultrasonic baselines
  3. feed the escalation machine (presence, motion, sound events), tick it
  4. schedule voice prompts for the current state
  5. refresh the visible screen

Background threads never touch widgets: CSI feed, ultrasonic gpio loop,
audio level, sound-event CSV watcher, voice playback, Gemini pregeneration.
"""

from __future__ import annotations

import glob
import json
import logging
import math
import threading
import time
from collections import deque
from pathlib import Path

import settings_store
from csi_stream import CsiProcessor, LiveSource, ReplaySource, SimSource
from escalation import Config, Escalation, State
from feeds import AudioLevelFeed, SoundEventFeed, UltraFeed
from gemini_tts import start_pregeneration
from room_math import PresenceEstimator, ray_intersection, ray_point
from room_sim import SimRoom
from screens import Alert, CheckIn, Dashboard, HelpSent, Settings, TrainingScreen
from recorder import TakeRecorder
from speech import SpeechListener
from training import Training
from voice import Speaker

import reply as reply_mod        # sound/speech, put on sys.path by speech.py

log = logging.getLogger("app")

REPO_ROOT = Path(__file__).resolve().parent.parent


class App:
    def __init__(self, root, settings: dict, fullscreen: bool):
        self.root = root
        self.settings = settings
        self.fullscreen = fullscreen
        self.now = time.monotonic()

        # -- state holders
        self.room = settings_store.load_room(settings)
        self.sim = SimRoom(self.room, settings.get("max_distance_m", 4.0))
        self.processor = CsiProcessor(window_s=settings.get("motion_window_s", 2.0),
                                      factor=settings.get("motion_factor", 1.5))
        self.ultra = UltraFeed(settings, sim=self.sim)
        self.audio = AudioLevelFeed(settings)
        self.events_feed = SoundEventFeed(settings)
        self.presence = PresenceEstimator(
            deviation_cm=settings.get("presence_dev_cm", 30.0),
            enter_s=settings.get("presence_enter_s", 1.5),
            exit_s=settings.get("presence_exit_s", 5.0),
            max_distance_m=settings.get("max_distance_m", 4.0))
        self._load_calibration()

        self.speaker = Speaker(settings_store.VOICE_CACHE,
                               muted=settings.get("voice_muted", False),
                               voice=settings.get("gemini_voice", "Kore"))
        self._speech_results: deque = deque()
        self.speech = SpeechListener(settings, self.speaker.busy,
                                     lambda label, text:
                                     self._speech_results.append(
                                         ("local", label, text)))
        self._spoke_spans: list = []   # [[start, end], ...] wall times the voice was talking
        self._checkin_wall = 0.0
        self._last_reprompt = 0.0
        self._tts_thread = None
        self._tts_stop = None
        self.regen_voice()

        # -- escalation machine
        self.escalation = Escalation(
            self._config(), on_transition=self._on_transition,
            log=self._log_transition)
        self._checkin_entered = None
        self._checkin_repeat_k = 0
        self._last_alert_voice = 0.0
        self._last_sound_trigger = 0.0
        self._seen_sound_events = set()

        # -- ui events log + activity feed
        self._log_path = REPO_ROOT / "ui_events.jsonl"
        self.activity = deque(maxlen=10)

        # -- screens
        self.dashboard = Dashboard(root, self)
        self.checkin = CheckIn(root, self)
        self.alert = Alert(root, self)
        self.helpsent = HelpSent(root, self)
        self.settings_screen = Settings(root, self)
        self.training_screen = TrainingScreen(root, self)
        self.screens = {"dashboard": self.dashboard, "checkin": self.checkin,
                        "alert": self.alert, "helpsent": self.helpsent,
                        "settings": self.settings_screen,
                        "training": self.training_screen}

        from room_setup import RoomSetup
        self.room_setup = RoomSetup(root, settings, on_done=self._room_saved)
        self.room_zoom = False
        self.dashboard.room.set_room(self.room)

        # -- csi feed
        self.csi_mode = settings.get("csi_mode", "live")
        self.csi_started_at = 0.0
        self._csi_dead_noted = False
        self.csi_stop: threading.Event | None = None
        self.csi_thread = None
        self.takes = []
        self.take_i = 0
        self._load_takes()
        self._start_csi()

        # -- data dict refreshed each tick for the screens
        self.d = {
            "clock": "", "presence": False, "noise_dbfs": None,
            "mic_fresh": False, "cal_text": None, "motion": {}, "readings": [],
            "baselines": [], "deviating": set(), "events": [],
            "state": {}, "csi_mode": self.csi_mode, "still_for": None,
            "max_distance_m": settings.get("max_distance_m", 4.0),
            "person": None, "sim_truth": None,
        }
        self.motion_history: deque = deque(maxlen=120)   # (t, score)
        self._last_history_t = 0.0
        self._prev_calibrating = False
        self._cal_start_at = None        # monotonic time the countdown ends
        self._cal_last_count = None      # last countdown number spoken
        self._last_tick_error = -999.0
        self.training = Training(
            position_s=settings.get("training_position_s", 10.0),
            record_s=settings.get("training_seconds", 30.0))
        self.recorder: TakeRecorder | None = None

        self.show("dashboard")
        self.speaker.speak("ready")

        self.root.after(100, self._tick_loop)

    def _tick_loop(self) -> None:
        """The single timer loop. tick() itself does the work only, so tests
        can drive ticks without stacking up extra after() loops."""
        try:
            self.tick()
        except Exception:                # noqa: BLE001
            # one bad tick must never stop the loop: this UI is the safety net
            now = time.monotonic()
            if now - self._last_tick_error > 5.0:
                self._last_tick_error = now
                log.exception("tick failed (continuing)")
        self.root.after(100, self._tick_loop)

    # == helpers ==============================================================

    def _config(self) -> Config:
        s = self.settings
        return Config(still_seconds=s.get("still_seconds", 45),
                      checkin_seconds=s.get("checkin_seconds", 30),
                      alert_grace_seconds=s.get("alert_grace_seconds", 10),
                      rearm_seconds=s.get("rearm_seconds", 120))

    def _load_takes(self) -> None:
        pattern = self.settings.get("replay_path", "")
        if not pattern:
            return
        directory = Path(pattern).parent
        self.takes = sorted(glob.glob(str(directory / "*.pcap")))

    def _start_csi(self) -> None:
        if self.csi_stop is not None:
            self.csi_stop.set()
        mode = self.csi_mode
        mac = self.settings.get("csi_mac") or None
        if mode == "live":
            source = LiveSource(self.settings.get(
                "tcpdump_cmd", ["tcpdump", "-i", "wlan0", "-w", "-", "-U"]),
                mac=mac)
        elif mode == "replay":
            path = self.settings.get("replay_path")
            if not path or not Path(path).exists():
                raise SystemExit(f"replay pcap {path!r} not found")
            else:
                source = ReplaySource(path,
                                      speed=self.settings.get("replay_speed", 1.0),
                                      loop=self.settings.get("replay_loop", True))
        else:
            source = SimSource(self.sim.activity)

        self.csi_stop = threading.Event()
        self.csi_started_at = time.monotonic()
        push = self.processor.push
        self.csi_thread = threading.Thread(
            target=source.run, args=(push, self.csi_stop),
            name="csi-feed", daemon=True)
        self.csi_thread.start()

    def _log(self, kind: str, **fields) -> None:
        record = {"t": time.time(), "kind": kind}
        record.update(fields)
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            pass

    def _log_transition(self, now, frm, to, reason) -> None:
        self._log("transition", state=to.value, frm=frm.value, reason=reason)
        stamp = time.strftime("%H:%M:%S")
        text = {"check_in": "check-in shown",
                "alert": "alert", "help_sent": "help sent",
                "active": "all clear", "still": "quiet",
                "away": "room empty"}.get(to.value, to.value)
        self.activity.appendleft((stamp, f"{text} ({reason})"))

    # == transitions ==========================================================

    def _on_transition(self, now, frm, to, reason) -> None:
        if to is not State.CHECK_IN:
            self.speech.disarm()
        if to is State.CHECK_IN:
            self._checkin_entered = now
            self._checkin_repeat_k = 1
            self._checkin_wall = time.time()
            self.speech.arm()
            self.speaker.speak("checkin_say" if self.voice_reply_ready() else "checkin")
            self.show("checkin")
        elif to is State.ALERT:
            self._last_alert_voice = now
            self.speaker.interrupt_and_speak("alert")
            self.show("alert")
        elif to is State.HELP_SENT:
            self.helpsent.set_entered(now)
            self.speaker.interrupt_and_speak("helpsent")
            self.show("helpsent")
        elif to in (State.ACTIVE, State.STILL, State.AWAY):
            # only when a help screen is being left: presence changes on the
            # dashboard must not repaint it (the flashing) or cut off speech
            if frm in (State.CHECK_IN, State.ALERT, State.HELP_SENT):
                self.speaker.stop()
                self.show("dashboard")

    # == screen management =====================================================

    def show(self, name) -> None:
        for key, screen in self.screens.items():
            screen.pack_forget()
        self.screens[name].pack(fill="both", expand=True)
        self._screen = name

    def show_dashboard(self) -> None:
        self.show("dashboard")

    def show_settings(self) -> None:
        self.show("settings")

    def toggle_room_zoom(self) -> None:
        room = self.dashboard.room
        room.pack_forget()
        if self.room_zoom:
            room.pack(in_=self.dashboard.left, fill="both", expand=True,
                      before=self.dashboard.waterfall)
        else:
            room.pack(in_=self.root, fill="both", expand=True)
            room.lift()
        self.room_zoom = not self.room_zoom

    def run_room_setup(self) -> None:
        self.room_setup.start()

    def _room_saved(self, room) -> None:
        self.room = room
        self.sim = SimRoom(self.room, self.settings.get("max_distance_m", 4.0))
        self.ultra.sim = self.sim
        self.presence = PresenceEstimator(
            deviation_cm=self.settings.get("presence_dev_cm", 30.0),
            enter_s=self.settings.get("presence_enter_s", 1.5),
            exit_s=self.settings.get("presence_exit_s", 5.0),
            max_distance_m=self.settings.get("max_distance_m", 4.0))
        self.dashboard.room.set_room(room)
        self.presence.unfreeze()
        self._save_calibration()       # the old room's ultrasonic baseline is void
        if self.csi_mode == "sim":
            self._start_csi()          # SimSource holds the old sim.activity
        self.activity.appendleft(
            (time.strftime("%H:%M:%S"), "room map updated"))

    # == actions (buttons) ======================================================

    def manual_help(self) -> None:
        self._log("manual_help")
        self.escalation.request_help(self.now)

    def checkin_ok(self) -> None:
        self._log("checkin_ok")
        self.escalation.im_ok(self.now)

    def _note_own_voice(self, wall: float) -> None:
        """Remember when this device was speaking, as [start, end] wall times."""
        if self.speaker.busy():
            if self._spoke_spans and wall - self._spoke_spans[-1][1] < 0.6:
                self._spoke_spans[-1][1] = wall          # same utterance (or a short gap)
            else:
                self._spoke_spans.append([wall, wall])
                del self._spoke_spans[:-40]

    def voice_reply_ready(self) -> bool:
        """True when something is listening for a spoken answer."""
        return self.speech.available or self.events_feed.speech_server_alive()

    def _drain_speech(self) -> None:
        """Act on spoken answers during a check-in.

        Replies from the GPU worker (Whisper + Claude) understand free-form
        answers. While that worker is alive, the quick offline listener may
        only escalate; it cannot cancel, so "yes, but my hip hurts" is not
        cut short by the "yes". Without the worker it decides alone.
        """
        self._note_own_voice(time.time())
        for row in self.events_feed.drain_replies():
            reply = reply_mod.parse_reply(row["top_classes"], row["unix_time_approx"])
            if reply is not None:
                self._speech_results.append(("remote", reply.label, reply))
        while self._speech_results:
            source, label, heard = self._speech_results.popleft()
            if self.escalation.machine.state is not State.CHECK_IN:
                log.info("ignored %s reply %r: not in a check-in", source,
                         getattr(heard, "text", heard))
                continue
            if source == "remote":
                # keep only the words spoken while this device was silent: the
                # mic hears the question, and an answer often follows it closely
                said = reply_mod.without_own_voice(
                    heard, self._spoke_spans, floor=self._checkin_wall,
                    lag=self.settings.get("speech_audio_lag_s", 0.5))
                if said is None:
                    log.info("ignored reply %r (%s): the device's own voice or before the question",
                             heard.text, label)
                    continue
                if said is not heard:
                    log.info("reply %r -> %r after removing the device's own voice",
                             heard.text, said.text)
                heard, label = said, said.label
                text, why = heard.text, heard.reason
            else:
                if label == "ok" and self.events_feed.speech_server_alive():
                    self.speech.arm()          # let the richer answer decide
                    continue
                text, why = heard, ""
            self._log("checkin_voice", source=source, heard=text, result=label, why=why)
            self.activity.appendleft((time.strftime("%H:%M:%S"),
                                      f'heard "{text}"' + (f" - {why}" if why else "")))
            if label == "ok":
                self.escalation.im_ok(self.now)
            elif label == "help":
                self.escalation.request_help(self.now)
            else:
                if time.time() - self._last_reprompt > 8.0:
                    self._last_reprompt = time.time()
                    self.speaker.speak("reprompt")
                self.speech.arm()

    def checkin_help(self) -> None:
        self._log("checkin_need_help")
        self.escalation.request_help(self.now)

    def alert_cancel(self) -> None:
        self._log("alert_cancelled")
        self.escalation.im_ok(self.now)

    def helpsent_clear(self) -> None:
        self._log("all_clear")
        self.escalation.all_clear(self.now)

    def demo_walk(self) -> None:
        self.sim.walk()
        self._log("demo", action="walk")

    def demo_fall(self) -> None:
        self.sim.fall()
        self._log("demo", action="fall")

    def demo_enter(self) -> None:
        self.sim.enter()
        self._log("demo", action="enter")

    def demo_leave(self) -> None:
        self.sim.leave()
        self._log("demo", action="leave")

    def next_take(self) -> None:
        if not self.takes:
            return
        self.take_i = (self.take_i + 1) % len(self.takes)
        self.settings["replay_path"] = self.takes[self.take_i]
        self._log("replay", take=self.takes[self.take_i])
        if self.csi_mode == "replay":
            self._start_csi()
        else:
            self.csi_mode = "replay"
            self._start_csi()

    CAL_COUNTDOWN_S = 5.0
    CAL_SECONDS = 10.0

    def start_calibration(self) -> None:
        """Button: count down so the person can leave, then calibrate."""
        if self._cal_start_at is not None or self.processor.calibrating(self.now):
            return
        self._cal_start_at = self.now + self.CAL_COUNTDOWN_S
        self._cal_last_count = None
        self.activity.appendleft(
            (time.strftime("%H:%M:%S"),
             f"calibrating in {self.CAL_COUNTDOWN_S:.0f} s: leave the room"))
        self.show("dashboard")

    def _calibration_tick(self, now) -> str | None:
        """Start the real calibration when the countdown ends; returns the
        banner text for the dashboard (None when idle)."""
        if self._cal_start_at is not None:
            if now < self._cal_start_at:
                n = math.ceil(self._cal_start_at - now)
                if n != self._cal_last_count:      # say each number once
                    self._cal_last_count = n
                    self.speaker.interrupt_and_speak(f"count_{n}")
                return f"Leave the room\nCalibrating in {n}"
            self._cal_start_at = None
            self.processor.calibrate(now, self.CAL_SECONDS)
            self.speaker.interrupt_and_speak("calibrating")
            self.activity.appendleft(
                (time.strftime("%H:%M:%S"),
                 f"calibrating empty room ({self.CAL_SECONDS:.0f} s)"))
        if self.processor.calibrating(now):
            left = max(0.0, self.processor.cal_until - now)
            return f"Calibrating room\n{math.ceil(left)} s"
        return None

    def regen_voice(self) -> None:
        if self._tts_stop is not None:
            self._tts_stop.set()
        self._tts_thread, self._tts_stop = start_pregeneration(
            self.settings, settings_store.VOICE_CACHE,
            settings_store.gemini_api_key(self.settings))

    def apply_settings(self, settings: dict) -> None:
        self.settings = settings
        settings_store.save(settings)
        self.escalation.machine.config = self._config()
        self.processor.factor = settings.get("motion_factor", 1.5)
        self.speaker.muted = settings.get("voice_muted", False)
        self.speaker.voice = settings.get("gemini_voice", "Kore")
        self.presence.deviation_m = (
            settings.get("presence_dev_cm", 30.0) / 100.0)
        self.regen_voice()
        self._log("settings_saved")

    # == tick ====================================================================

    def tick(self) -> None:
        self.now = now = time.monotonic()
        wall = time.time()

        # 1. simulated room + sensors
        self._drain_speech()
        self.sim.step(now)
        self.ultra.poll_sim()

        # 2. CSI snapshot + motion
        cal_text = self._calibration_tick(now)
        snap = self.processor.snapshot(now)
        self._finish_calibration(snap)
        alive = snap["alive"]
        if alive and now - self._last_history_t >= 0.5:
            self._last_history_t = now
            if snap["motion_score"] is not None:
                self.motion_history.append((now, snap["motion_score"]))
            else:
                self.motion_history.append((now, 0.0))

        # live CSI dead: say so once, never substitute simulated data
        if (self.csi_mode == "live" and not alive
                and not self._csi_dead_noted
                and now - self.csi_started_at
                > self.settings.get("live_dead_s", 5.0) + 3.0):
            self._csi_dead_noted = True
            log.warning("live CSI silent; waiting for packets (no sim fallback)")
            self.activity.appendleft(
                (time.strftime("%H:%M:%S"), "live CSI silent"))
        elif alive:
            self._csi_dead_noted = False

        # 3. presence from ultrasonic baselines
        readings = (self.ultra.latest[1]
                    if self.ultra.latest else [])
        present = bool(self.presence.update(wall, readings)) if readings else (
            self.presence.present)
        # a dead CSI feed cannot claim the room is still
        moving = snap["moving"] if alive else True

        # 4. new sound events
        for event in self.events_feed.drain():
            key = (event.get("unix_time_approx"),
                   event.get("rise_db"), event.get("top_classes"))
            self.activity.appendleft((
                time.strftime("%H:%M:%S", time.localtime(event["unix_time_approx"])),
                f"SOUND {event.get('status', '')} "
                + (f"rise {event['rise_db']}dB" if event.get("rise_db")
                   else str(event.get("top_classes", ""))[:30])))
            if key in self._seen_sound_events:
                continue
            self._seen_sound_events.add(key)
            if event.get("rise_db"):
                continue        # loud-sound heuristic row (speech, doors...): shown in the feed, never a fall on its own
            if self.training.active:
                continue        # someone lying on the floor on purpose
            if now - self._last_sound_trigger > 8.0:
                self._last_sound_trigger = now
                label = (event.get("top_classes") or "loud sound")[:40]
                self.escalation.sound_candidate(now, label)

        # 5. drive the machine (held while a guided training is running)
        self._training_tick(now, wall, readings)
        if not self.training.active:
            self.escalation.set_presence(now, present)
            self.escalation.set_motion(now, bool(moving))
            self.escalation.tick(now)
        state = self.escalation.snapshot(now)

        # 6. voice scheduling
        self._voice_tick(now, state)

        # 7. data for the screens
        still_for = None
        m = self.escalation.machine
        if m.state is State.STILL and m.still_since is not None:
            still_for = now - m.still_since
        self.d.update({
            "clock": time.strftime("%H:%M:%S"),
            "cal_text": cal_text,
            "presence": present,
            "noise_dbfs": self.audio.dbfs,
            "mic_fresh": self.audio.fresh(),
            "motion": snap,
            "readings": readings,
            "baselines": [self.presence.baseline(i)
                          for i in range(len(readings) or 2)],
            "deviating": set(self.presence.deviating),
            "events": list(self.activity)[:5],
            "state": state,
            "csi_mode": self.csi_mode,
            "still_for": still_for,
            "person": self._person_estimate(readings),
            "sim_truth": (self.sim.ground_truth()
                          if self.csi_mode in ("sim", "replay")
                          or self.ultra.mode == "sim" else None),
        })

        # the banner follows the calibration, whichever screen is up (the map
        # is a root-level widget, so it is placed there too)
        self.dashboard.set_banner(cal_text if self._screen == "dashboard" else None)

        screen = self.screens.get(self._screen)
        if screen is not None:
            screen.refresh(self)
        # rescheduling happens once, in _tick_loop

    # == guided training ========================================================

    def start_training(self) -> None:
        if self.escalation.machine.state not in (State.AWAY, State.ACTIVE, State.STILL):
            return                       # not in the middle of a check-in or alert
        base = Path(self.settings.get("training_dir")
                    or Path.home() / "captures" / "training")
        out = base / time.strftime("session_%Y%m%d_%H%M%S")
        tcpdump = (self.settings.get("tcpdump_cmd")
                   if self.csi_mode == "live" else None)
        self.recorder = TakeRecorder(
            out, tcpdump, self.settings.get("max_distance_m", 4.0))
        self.training.start(self.now)
        self.activity.appendleft(
            (time.strftime("%H:%M:%S"), f"guided training -> {out}"))
        self._log("training_started", dir=str(out))
        self.show("training")

    def stop_training(self) -> None:
        self._run_training(self.training.stop(self.now))
        self.speaker.stop()
        self._log("training_stopped")
        self.show("settings")

    def skip_training_take(self) -> None:
        self._run_training(self.training.skip(self.now))

    def _training_tick(self, now, wall, readings) -> None:
        if not self.training.active:
            return
        self._run_training(self.training.tick(now))
        if self.recorder is not None and self.recorder.recording:
            self.recorder.sample(wall, readings)

    def _run_training(self, actions) -> None:
        for action in actions:
            kind = action[0]
            if kind == "speak":
                self.speaker.interrupt_and_speak(action[1])
            elif kind == "record_start" and self.recorder is not None:
                self.recorder.start(action[1])
                self.activity.appendleft(
                    (time.strftime("%H:%M:%S"), f"recording {action[1]} ({action[2]:.0f} s)"))
            elif kind == "record_stop" and self.recorder is not None:
                self.recorder.stop()
            elif kind == "finished":
                self._log("training_finished")
                self.show("settings")

    def _finish_calibration(self, snap) -> None:
        calibrating = snap.get("calibrating")
        was_calibrating, self._prev_calibrating = self._prev_calibrating, calibrating
        if not (was_calibrating and not calibrating):
            return
        self.processor.finish_calibration()
        # the room was empty during calibration: lock the ultrasonic
        # structure baselines in as well
        self.presence.freeze_recent(time.time())
        motion_ok = self.processor.threshold is not None
        ultra_ok = self.presence.export_frozen() is not None
        parts = []
        if motion_ok:
            parts.append(f"motion threshold {self.processor.threshold:.3f}")
        if ultra_ok:
            parts.append("ultrasonic")
        stamp = time.strftime("%H:%M:%S")
        if motion_ok or ultra_ok:
            self._save_calibration()
            self.speaker.interrupt_and_speak("calibrated")
            self.activity.appendleft((stamp, "empty-room baseline set (" + ", ".join(parts) + ")"))
        else:
            self.speaker.interrupt_and_speak("calibration_failed")
            self.activity.appendleft((stamp, "calibration failed: no sensor data"))

    def _save_calibration(self) -> None:
        try:
            settings_store.save_calibration({
                "motion": self.processor.export_calibration(),
                "ultrasonic": self.presence.export_frozen(),
                "saved_at": time.time()})
        except OSError as exc:
            log.warning("could not save calibration: %s", exc)

    def _load_calibration(self) -> None:
        """Restore the last calibration, so a restart keeps it."""
        data = settings_store.load_calibration()
        try:
            if data.get("motion"):
                self.processor.import_calibration(data["motion"])
            frozen = data.get("ultrasonic")
            if frozen and len(frozen) == len(self.settings.get("sensors", [])):
                self.presence.import_frozen(frozen)
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("ignoring unreadable calibration: %s", exc)

    def _person_estimate(self, readings):
        dev = sorted(self.presence.deviating)
        sensors = self.room.get("sensors", [])
        if len(dev) >= 2 and len(readings) >= 2:
            i, j = dev[0], dev[1]
            if i < len(sensors) and j < len(sensors):
                point = ray_intersection(sensors[i], readings[i],
                                         sensors[j], readings[j])
                if point is not None:
                    return point
        if len(dev) == 1 and dev[0] < len(sensors) and readings:
            return ray_point(sensors[dev[0]], readings[dev[0]])
        return None

    def _voice_tick(self, now, state) -> None:
        value = state["state"]
        if value == "check_in" and self._checkin_entered is not None:
            repeat = self.settings.get("voice_repeat_seconds", 10)
            due = self._checkin_entered + self._checkin_repeat_k * repeat
            if now >= due:
                self._checkin_repeat_k += 1
                self.speaker.speak("checkin_reminder_say"
                                   if self.voice_reply_ready() else "checkin_reminder")
        elif value == "alert":
            if now - self._last_alert_voice >= 4.0:
                self._last_alert_voice = now
                self.speaker.speak("alert_reminder")

    # == room view data ==========================================================

    def room_data(self) -> dict:
        d = self.d
        status = "PERSON IN ROOM" if d["presence"] else "ROOM EMPTY"
        if self.csi_mode == "replay":
            name = Path(self.settings.get("replay_path", "")).name
            status += f"  |  replay {name}"
        elif self.csi_mode == "sim":
            status += "  |  simulated"
        if self.escalation.machine.state is State.STILL and d.get("still_for") is not None:
            status += f"  |  still {d['still_for']:.0f} s"
        return {
            "readings": d["readings"],
            "baselines": d["baselines"],
            "deviating": d["deviating"],
            "person": d["person"],
            "moving": d["motion"].get("moving"),
            "shape_changed": d["motion"].get("shape_changed"),
            "sim_truth": d["sim_truth"],
            "max_distance_m": d["max_distance_m"],
            "status": status,
        }

    def close(self) -> None:
        if self.recorder is not None:
            self.recorder.stop()
        if self.csi_stop is not None:
            self.csi_stop.set()
        if self._tts_stop is not None:
            self._tts_stop.set()
        self.speaker.close()
        self.ultra.close()
        self.audio.close()
        self.speech.close()
        self.events_feed.close()
