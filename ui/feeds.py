"""Feeds that move sensor data into the UI: ultrasonic, audio level, sound events.

All feeds are passive data holders polled by the App tick; background work
happens in their own threads and never touches Tk widgets.
"""

from __future__ import annotations

import csv
import logging
import os
import threading
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np

log = logging.getLogger("feeds")

WAV_CANDIDATES = [
    Path("/dev/shm/rolling_audio.wav"),
    Path("rolling_audio.wav"),
    Path("sound/rolling_audio.wav"),
    Path("../sound/rolling_audio.wav"),
]

CSV_CANDIDATES = [
    Path("fall_events.csv"),
    Path("/dev/shm/fall_events.csv"),
    Path("sound/fall_events.csv"),
    Path("../sound/fall_events.csv"),
]


# Where scripts/pi/collect.sh, label.py and the ultrasonic archive all write.
CONTINUOUS_DIR = str(Path.home() / "captures" / "continuous")


def _first_existing(candidates, override=None):
    if override:
        p = Path(override)
        if p.exists():
            return p
    for p in candidates:
        if p.exists():
            return p
    return None


class UltraFeed:
    """Per-sensor distances (meters). gpiozero on the Pi; the simulated room
    when sensors are absent or `--us sim`. Falls back to sim automatically."""

    def __init__(self, settings: dict, sim=None):
        self.settings = settings
        self.sim = sim
        self.mode = settings.get("ultra_mode", "gpio")
        self.latest: tuple[float, list] | None = None
        self.samples: deque = deque(maxlen=2400)     # ~4 min at 10 Hz
        self._stop = threading.Event()
        self._thread = None
        self.error: str | None = None
        self._log_path: Path | None = None
        self._log_file = None

        if self.mode == "gpio":
            self._start_gpio()

    def _start_gpio(self) -> None:
        try:
            from gpiozero import DistanceSensor
        except Exception as exc:                     # noqa: BLE001
            self.error = f"gpiozero unavailable ({exc}); no ultrasonic data"
            log.warning(self.error)
            return
        self._sensors = [
            DistanceSensor(echo=s["echo"], trigger=s["trig"],
                           max_distance=self.settings.get("max_distance_m", 4.0),
                           queue_len=3)
            for s in self.settings.get("sensors", [])
        ]
        self._thread = threading.Thread(
            target=self._gpio_loop, name="ultra", daemon=True)
        self._thread.start()

    def _gpio_loop(self) -> None:
        max_d = self.settings.get("max_distance_m", 4.0)
        while not self._stop.is_set():
            t = time.time()
            readings = []
            for s in self._sensors:
                try:
                    d = float(s.distance)
                    if d is None or d >= max_d * 0.98:
                        d = max_d                    # "no echo"
                except Exception as exc:             # noqa: BLE001
                    log.warning("ultrasonic read failed: %s", exc)
                    d = max_d
                readings.append(d)
            self.record(t, readings)
            self._log_row(t, readings)
            self._stop.wait(0.1)

    def _log_row(self, t: float, readings: list) -> None:
        """Append to the continuous ultrasonic archive (one CSV per day).

        The UI owns the GPIO pins, so it is the only process that can log
        them while it runs. Off with `ultra_log_dir: ""` in settings.
        """
        log_dir = self.settings.get("ultra_log_dir", CONTINUOUS_DIR)
        if not log_dir:
            return
        try:
            path = Path(log_dir).expanduser() / time.strftime("us_%Y%m%d.csv", time.localtime(t))
            if path != self._log_path:
                if self._log_file:
                    self._log_file.close()
                path.parent.mkdir(parents=True, exist_ok=True)
                new = not path.exists()
                self._log_file = open(path, "a", encoding="utf-8")
                self._log_path = path
                if new:
                    self._log_file.write("epoch_s," + ",".join(
                        f"d{i + 1}_m" for i in range(len(readings))) + "\n")
            self._log_file.write(f"{t:.3f}," + ",".join(f"{d:.3f}" for d in readings) + "\n")
            self._log_file.flush()
        except OSError as exc:
            log.warning("ultrasonic archive write failed: %s", exc)
            self.settings["ultra_log_dir"] = ""     # stop retrying every 100 ms

    def record(self, t: float, readings: list) -> None:
        """Called from the sim (by the App tick) or the gpio thread."""
        self.latest = (t, readings)
        self.samples.append((t, list(readings)))

    def poll_sim(self) -> None:
        """Sample the simulated room once (App tick, sim mode)."""
        if self.sim is not None and self.mode == "sim":
            self.record(time.time(), self.sim.distances())

    def recent(self, seconds: float = 120.0):
        cutoff = time.time() - seconds
        return [(t, r) for t, r in self.samples if t >= cutoff]

    def close(self) -> None:
        self._stop.set()
        if self._log_file:
            self._log_file.close()


class AudioLevelFeed:
    """Noise level (dBFS) from the tail of the rolling WAV, plus freshness.

    Reads only the last ~0.5 s of the file the recorder keeps replacing, so
    it costs almost nothing. A stale WAV means the mic path is down.
    """

    def __init__(self, settings: dict, interval=0.25):
        self.settings = settings
        self.interval = interval
        self.dbfs: float | None = None
        self.mtime: float | None = None
        self.path: Path | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="audio-level", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.dbfs, self.mtime, self.path = self._read_level()
            self._stop.wait(self.interval)

    def _read_level(self):
        path = _first_existing(
            WAV_CANDIDATES, self.settings.get("wav_path"))
        if path is None:
            return None, None, None
        try:
            import os
            mtime = os.stat(path).st_mtime
            with wave.open(str(path), "rb") as wav:
                sr = wav.getframerate()
                nframes = wav.getnframes()
                keep = min(nframes, int(sr * 0.5))
                wav.setpos(nframes - keep)
                raw = wav.readframes(keep)
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if samples.size == 0:
                return None, mtime, path
            rms = float(np.sqrt(np.mean(samples ** 2)))
            dbfs = 20 * np.log10(rms + 1e-9)
            return float(np.clip(dbfs, -60.0, 0.0)), mtime, path
        except (OSError, EOFError, wave.Error, ValueError) as exc:
            log.debug("audio level read failed: %s", exc)
            return None, None, path

    def fresh(self, max_age=6.0) -> bool:
        return (self.mtime is not None
                and time.time() - self.mtime <= max_age)

    def close(self) -> None:
        self._stop.set()


class SoundEventFeed:
    """New rows appended to fall_events.csv by sound/fall_detector.py.

    Polls the file with a byte offset so only new, complete rows are parsed;
    handles the file being truncated on restart.
    """

    FIELDS = ["unix_time_approx", "rise_db", "crest_factor",
              "peak_amplitude", "top_classes", "status"]

    def __init__(self, settings: dict, interval=1.0, keep=8):
        self.settings = settings
        self.interval = interval
        self.recent_events: deque = deque(maxlen=keep)
        self.pending: deque = deque()          # drained by the App
        self.replies: deque = deque()          # spoken check-in answers
        self.speech_alive_at = 0.0             # last worker heartbeat (local clock)
        self.path: Path | None = None
        self._offset = 0
        self._new_file = True
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="sound-events", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:             # noqa: BLE001
                log.warning("sound event poll failed: %s", exc)
            self._stop.wait(self.interval)

    def _poll_once(self) -> None:
        path = _first_existing(
            CSV_CANDIDATES, self.settings.get("events_path"))
        if path is None:
            return
        import os
        if path != self.path:
            # first sight of this file: show its recent rows, but only rows
            # appended from now on count as new events (a detector that has
            # been running for hours should not replay its whole log)
            self.path = path
            self._offset = 0
            self._new_file = True
        size = os.stat(path).st_size
        if size < self._offset:
            self._offset = 0            # truncated / restarted detector
            self._new_file = True
        if size == self._offset:
            return
        with open(path, encoding="utf-8") as f:
            f.seek(self._offset)
            chunk = f.read()
        self._offset = size
        lines = chunk.splitlines()
        if not chunk.endswith("\n"):
            self._offset -= len(lines[-1]) + 1
            lines = lines[:-1]
        first_sight = self._new_file
        self._new_file = False
        if not lines:
            return
        # the first line may be a header
        if lines[0].startswith("unix_time"):
            lines = lines[1:]
        for line in lines:
            row = self._parse(line)
            if row and row["status"] == "SPEECH_ALIVE":
                self.speech_alive_at = row["read_at"]
            elif row and row["status"] == "SPEECH_REPLY":
                if not first_sight:
                    self.replies.append(row)
            elif row:
                self.recent_events.append(row)
                if (row["status"] in ("REVIEW_CANDIDATE", "DISTRESS_SOUND")
                        and not first_sight):
                    self.pending.append(row)

    def _parse(self, line: str) -> dict | None:
        try:
            values = next(csv.reader([line]))
            if len(values) != len(self.FIELDS):
                return None
            row = dict(zip(self.FIELDS, values))
            row["unix_time_approx"] = float(row["unix_time_approx"])
            row["read_at"] = time.time()
            return row
        except (StopIteration, ValueError):
            return None

    def drain_replies(self):
        out = []
        while self.replies:
            out.append(self.replies.popleft())
        return out

    def speech_server_alive(self, max_age=25.0) -> bool:
        return time.time() - self.speech_alive_at <= max_age

    def drain(self):
        out = []
        while self.pending:
            out.append(self.pending.popleft())
        return out

    def close(self) -> None:
        self._stop.set()
