"""Spoken replies to "Are you okay?": yes / no / "I need help".

The mic reaches the UI only as the rolling WAV (sound/rolling_audio.py), so
this listens to that file: each time it is republished, the new tail is fed
to an offline Vosk recognizer restricted to a tiny grammar. A reply is
reported once as "ok" or "help" through the callback, then listening stops
until it is armed again.

Audio from while the speaker was talking is dropped, so the prompt itself
("Are you okay?") is never mistaken for an answer. Vosk is optional: without
it, or without a model, the listener stays disabled and the buttons still
work. Model: https://alphacephei.com/vosk/models (vosk-model-small-en-us)
unpacked to ui/speech_model/ or pointed to by the "speech_model" setting.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import wave
from pathlib import Path

import numpy as np

from feeds import WAV_CANDIDATES, _first_existing

log = logging.getLogger("speech")

DEFAULT_MODEL = Path(__file__).resolve().parent / "speech_model"

# Words the recognizer may produce; everything else becomes [unk].
GRAMMAR = ["yes", "yeah", "yep", "no", "nope", "help", "i", "am", "need",
           "okay", "ok", "fine", "alright", "not", "[unk]"]

_OK_WORDS = {"yes", "yeah", "yep", "okay", "ok", "fine", "alright"}
_HELP_WORDS = {"no", "nope", "help"}
_NEGATIONS = {"no", "nope", "not"}

MARGIN_S = 0.3        # ignore this long after the speaker stops (room tail)
MAX_CHUNK_S = 6.0


def classify(text: str) -> str | None:
    """'ok', 'help' or None for the recognised words.

    Help wins over ok, so "no", "not okay" and "I need help" all ask for
    help; only an unqualified yes / okay / fine counts as ok.
    """
    words = text.lower().replace("'", "").split()
    if "help" in words or any(w in _NEGATIONS for w in words):
        return "help"
    if any(w in _OK_WORDS for w in words):
        return "ok"
    return None


def read_tail(path, seconds: float):
    """(int16 PCM bytes, sample rate, mtime) for the last `seconds` of a WAV."""
    mtime = os.stat(path).st_mtime
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        total = wav.getnframes()
        keep = min(total, int(rate * seconds))
        wav.setpos(total - keep)
        return wav.readframes(keep), rate, mtime


class SpeechListener:
    """Background thread; arm() it when a question is asked.

    `is_speaking()` reports whether the voice is currently playing and
    `on_result(label, text)` is called from the listener thread.
    """

    def __init__(self, settings: dict, is_speaking, on_result,
                 interval: float = 0.25):
        self.settings = settings
        self.is_speaking = is_speaking
        self.on_result = on_result
        self.interval = interval
        self.available = False
        self.status = "off"
        self._armed = threading.Event()
        self._stop = threading.Event()
        self._armed_at = 0.0
        self._thread = None
        if settings.get("speech_enabled", True):
            self._thread = threading.Thread(
                target=self._run, name="speech", daemon=True)
            self._thread.start()

    # -- public -----------------------------------------------------------

    def arm(self) -> None:
        self._armed_at = time.time()
        self._armed.set()

    def disarm(self) -> None:
        self._armed.clear()

    def close(self) -> None:
        self._stop.set()

    # -- worker -----------------------------------------------------------

    def _load(self):
        model_dir = Path(self.settings.get("speech_model") or DEFAULT_MODEL)
        try:
            import vosk
        except ImportError:
            self.status = "vosk not installed (pip install vosk)"
            return None
        if not model_dir.is_dir():
            self.status = f"no speech model at {model_dir}"
            return None
        vosk.SetLogLevel(-1)
        self.status = "loading model"
        model = vosk.Model(str(model_dir))
        self.available = True
        self.status = "ready"
        return vosk, model

    def _recognizer(self, vosk, model, rate):
        return vosk.KaldiRecognizer(model, rate, json.dumps(GRAMMAR))

    def _run(self) -> None:
        loaded = self._load()
        if loaded is None:
            log.info("speech disabled: %s", self.status)
            return
        vosk, model = loaded
        rec = None
        rate = None
        last_mtime = 0.0
        idle_since = 0.0           # wall time the speaker last went quiet

        while not self._stop.wait(self.interval):
            if not self._armed.is_set():
                rec = None
                continue
            if self.is_speaking():
                idle_since = time.time()
                rec = None
                continue
            path = _first_existing(WAV_CANDIDATES, self.settings.get("wav_path"))
            if path is None:
                continue
            try:
                mtime = os.stat(path).st_mtime
                if mtime <= last_mtime:
                    continue
                since = max(last_mtime, idle_since + MARGIN_S, self._armed_at)
                seconds = min(mtime - since, MAX_CHUNK_S)
                last_mtime = mtime
                if seconds < 0.2:
                    continue
                pcm, wav_rate, _ = read_tail(path, seconds)
            except (OSError, EOFError, wave.Error) as exc:
                log.debug("speech read failed: %s", exc)
                continue
            if rec is None or wav_rate != rate:
                rate = wav_rate
                rec = self._recognizer(vosk, model, rate)
            self._feed(rec, pcm)

    def _feed(self, rec, pcm: bytes) -> None:
        if rec.AcceptWaveform(pcm):
            text = json.loads(rec.Result()).get("text", "")
        else:
            text = json.loads(rec.PartialResult()).get("partial", "")
        label = classify(text)
        if label is not None and self._armed.is_set():
            self._armed.clear()
            log.info("heard %r -> %s", text, label)
            self.on_result(label, text)
