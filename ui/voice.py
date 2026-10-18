"""Voice prompts: cached Gemini WAVs first, espeak-ng / macOS say behind.

Phrases are keyed so playback is instant: Gemini pre-generates each phrase
into ui/voice_cache/<key>.wav (see gemini_tts.py); whatever is missing gets
spoken by the system fallback, and the better voice swaps in once generated.

Everything runs on one worker thread so prompts never overlap; an alarm
(priority 0) jumps the queue and interrupts the current phrase.
"""

from __future__ import annotations

import logging
import queue
import shutil
import shlex
import subprocess
import sys
import threading

from training import PHRASES as TRAIN_PHRASES

log = logging.getLogger("voice")

# key -> (text, urgency)  urgency shapes the Gemini `style` hint
PHRASES: dict[str, str] = {
    "ready": "DownLink is ready.",
    "checkin": "Are you okay? If you can hear me, tap the green button.",
    "checkin_reminder": "Are you okay? Tap the green button.",
    "checkin_say": "Are you okay? Say yes, no, or I need help.",
    "checkin_reminder_say": "Are you okay? Say yes, or no, or I need help.",
    "reprompt": "Sorry, I didn't catch that. Are you okay?",
    "im_ok": "Okay, glad you're alright.",
    "alert": "Emergency alert. Tap the white button if you do not need help.",
    "alert_reminder": "Emergency alert. Tap the white button if you do not need help.",
    "helpsent": "Help is being contacted. Do not move if you are hurt.",
    "count_5": "Five.",
    "count_4": "Four.",
    "count_3": "Three.",
    "count_2": "Two.",
    "count_1": "One.",
    "calibrating": "Calibrating room.",
    "calibrated": "Calibration complete.",
    "calibration_failed": "Calibration failed. No sensor data.",
}

PHRASES.update(TRAIN_PHRASES)

URGENT = {"alert", "alert_reminder", "helpsent"}


class Speaker:
    """Serialised speech with interrupt support."""

    def __init__(self, cache_dir, muted=False, voice="Kore"):
        self.cache_dir = cache_dir
        self.voice = voice
        self.muted = muted
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._proc_lock = threading.Lock()
        self._active = False
        self._thread = threading.Thread(
            target=self._worker, name="voice", daemon=True)
        self._thread.start()

    # -- public -----------------------------------------------------------

    def speak(self, key: str, text: str | None = None, priority: int = 1) -> None:
        """Queue a phrase. priority 0 = alarm: interrupts and jumps the queue."""
        if self.muted:
            return
        text = text or PHRASES.get(key, key)
        self._queue.put((priority, key, text))

    def interrupt_and_speak(self, key: str, text: str | None = None) -> None:
        """Alarm path: cut whatever is playing and say this now."""
        if self.muted:
            return
        with self._proc_lock:
            self._kill_current()
        self._queue = type(self._queue)()       # drop queued phrases
        self.speak(key, text, priority=0)

    def busy(self) -> bool:
        """True while a phrase is playing or queued."""
        return self._active or not self._queue.empty()

    def stop(self) -> None:
        """Silence now (state cancelled)."""
        with self._proc_lock:
            self._kill_current()
        self._queue = type(self._queue)()

    def close(self) -> None:
        self._stop.set()
        self.stop()

    # -- worker ------------------------------------------------------------

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                _prio, key, text = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            self._active = True
            try:
                self._say(key, text)
            finally:
                self._active = False

    def _say(self, key: str, text: str) -> None:
        wav = self._cached(key)
        if wav is not None:
            if sys.platform == "darwin":
                cmd = ["afplay", str(wav)]
            elif shutil.which("pw-play"):   # through PipeWire so a connected Bluetooth speaker is used
                cmd = ["pw-play", str(wav)]
            else:
                cmd = ["aplay", "-q", str(wav)]
        else:
            cmd = self._fallback_cmd(text)
        if cmd is None:
            log.info("voice muted/unavailable: %s", text)
            return
        try:
            with self._proc_lock:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
                self._proc = proc
            proc.wait()
        except OSError as exc:
            log.warning("voice playback failed: %s", exc)
        finally:
            with self._proc_lock:
                self._proc = None

    def _cached(self, key: str):
        """Gemini cache files are <voice>_<key>.wav; plain <key>.wav also
        accepted so hand-dropped clips work."""
        if self.cache_dir is None:
            return None
        for name in (f"{self.voice}_{key}.wav", f"{key}.wav"):
            path = self.cache_dir / name
            if path.exists():
                return path
        return None

    def _fallback_cmd(self, text: str):
        if shutil.which("espeak-ng") and shutil.which("pw-play"):
            return ["sh", "-c", "espeak-ng -v en-us -s 155 --stdout " + shlex.quote(text) + " | pw-play -"]
        if shutil.which("espeak-ng"):
            return ["espeak-ng", "-v", "en-us", "-s", "155", text]
        if sys.platform == "darwin" and shutil.which("say"):
            return ["say", text]
        return None

    def _kill_current(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
