"""Record one labelled training take from inside the running UI.

The UI already owns the ultrasonic pins (scripts/pi/csi_record.sh needs it
stopped), so this takes the same three files from the live feeds instead:

    <dir>/<label>_<stamp>.pcap      CSI, from a second tcpdump (live CSI only)
    <dir>/<label>_<stamp>.us.csv    epoch_s,d1_cm,d2_cm (400 = no echo), as us_log.py
    <dir>/<label>_<stamp>.wav       audio slice of the rolling mic WAV (scripts/pi/audio_slice.py)

plus one line per take in <dir>/labels.jsonl. Names and columns match
csi_record.sh, so scripts/analyze_session.py reads them unchanged.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("recorder")

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIO_SLICE = REPO_ROOT / "scripts" / "pi" / "audio_slice.py"
ROLLING_WAV = "/dev/shm/rolling_audio.wav"


class TakeRecorder:
    def __init__(self, out_dir: Path, tcpdump_cmd: list | None, max_distance_m=4.0):
        """tcpdump_cmd: the live CSI capture command (None = no CSI, e.g. sim)."""
        self.out_dir = Path(out_dir)
        self.tcpdump_cmd = tcpdump_cmd
        self.max_cm = max_distance_m * 100.0
        self._proc: subprocess.Popen | None = None
        self._csv = None
        self.label = None
        self.base: Path | None = None
        self.t0 = 0.0

    @property
    def recording(self) -> bool:
        return self.label is not None

    def start(self, label: str, now_wall: float | None = None) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.t0 = now_wall if now_wall is not None else time.time()
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(self.t0))
        self.label = label
        self.base = self.out_dir / f"{label}_{stamp}"
        self._csv = open(self.base.with_suffix(".us.csv"), "w", encoding="utf-8")
        self._csv.write("epoch_s,d1_cm,d2_cm\n")
        if self.tcpdump_cmd:
            cmd = list(self.tcpdump_cmd)
            if "-w" in cmd:                      # the live source writes to stdout
                i = cmd.index("-w")
                cmd[i + 1:i + 2] = [str(self.base.with_suffix(".pcap"))]
                cmd = [c for c in cmd if c != "-U"]
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                log.warning("could not start CSI capture: %s", exc)
        return self.base

    def sample(self, wall_t: float, readings_m: list) -> None:
        """One ultrasonic reading per UI tick (meters; None/over range = no echo)."""
        if self._csv is None or not readings_m:
            return
        cm = [self.max_cm if (d is None or d * 100.0 >= self.max_cm) else d * 100.0
              for d in readings_m[:2]]
        cm += [self.max_cm] * (2 - len(cm))
        self._csv.write(f"{wall_t:.3f},{cm[0]:.1f},{cm[1]:.1f}\n")

    def stop(self, now_wall: float | None = None) -> None:
        """Finish the take; the audio slice is cut a few seconds later, once
        the rolling WAV has been republished to include the end of the take."""
        if self.label is None:
            return
        t1 = now_wall if now_wall is not None else time.time()
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self._csv is not None:
            self._csv.close()
            self._csv = None
        label, base, t0 = self.label, self.base, self.t0
        self.label = None
        self._append_label(label, base, t0, t1)
        threading.Thread(target=self._slice_audio, args=(base, t0, t1),
                         name="audio-slice", daemon=True).start()

    def _append_label(self, label, base, t0, t1) -> None:
        row = {"label": label, "file": base.name, "t0": round(t0, 3),
               "t1": round(t1, 3), "seconds": round(t1 - t0, 1)}
        try:
            with open(self.out_dir / "labels.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except OSError as exc:
            log.warning("could not write labels: %s", exc)

    @staticmethod
    def _slice_audio(base: Path, t0: float, t1: float) -> None:
        if not Path(ROLLING_WAV).exists() or not AUDIO_SLICE.exists():
            return
        time.sleep(3)                  # the rolling WAV is republished every ~2 s
        try:
            subprocess.run([sys.executable, str(AUDIO_SLICE),
                            str(base.with_suffix(".wav")), str(t0), str(t1)],
                           capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("audio slice failed: %s", exc)
