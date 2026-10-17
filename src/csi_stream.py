"""Streaming Nexmon CSI reader + motion scoring for the UI.

Same wire format as src/csi.py: a pcap stream (little-endian microseconds by
default, but all four pcap magics are accepted) whose UDP:5500 payloads are
an 18-byte CSI header followed by nfft x (int16 re, int16 im).

Three sources feed the identical CsiProcessor:
- LiveSource   tcpdump -i wlan0 -w - -U subprocess (unfiltered: a live BPF
                filter recorded 0 frames once; we filter in code like csi.py)
- ReplaySource a session pcap paced by its own timestamps, optionally looping
- SimSource    synthetic subcarrier series for demos without radio setup

The motion score is the validated phase-0 feature (docs/LOG.md): per-packet
gain normalisation, glitch packets dropped, usable-subcarrier mask, then the
std over a rolling window. Baselines drift per session, so the UI calibrates
against an empty room and sets threshold = baseline * factor.
"""

from __future__ import annotations

import math
import struct
import subprocess
import threading
import time
from collections import deque

import numpy as np

try:
    from csi import HDR          # 18-byte CSI header size (src/csi.py)
except ImportError:               # direct execution / tests
    HDR = 18

CSI_MAGIC = 0x1111
CSI_PORT = 5500
USABLE_UPDATE_EVERY = 50          # packets between usable-mask refreshes
GLITCH_MEDIAN_PACKETS = 400
MIN_PACKETS_FOR_SCORE = 8

# pcap magic -> (endian char, seconds-per-fraction)
PCAP_MAGICS = {
    0xA1B2C3D4: ("<", 1e-6),
    0xD4C3B2A1: (">", 1e-6),
    0xA1B23C4D: ("<", 1e-9),
    0x4D3CB2A1: (">", 1e-9),
}


def _parse_csi_payload(u: bytes, want_mac: bytes | None):
    """UDP payload -> (rssi, seq, complex subcarriers) or None."""
    if len(u) < HDR + 4 or struct.unpack("<H", u[:2])[0] != CSI_MAGIC:
        return None
    if want_mac is not None and u[4:10] != want_mac:
        return None
    iq = np.frombuffer(u[HDR:], dtype="<i2").astype(np.float32)
    if iq.size < 4 or iq.size % 2:
        return None
    rssi = struct.unpack("b", u[2:3])[0]
    seq = struct.unpack("<H", u[10:12])[0]
    return rssi, seq, iq[0::2] + 1j * iq[1::2]


def _iter_pcap_records(f, chunk=65536):
    """Yield (timestamp_s, payload) from a pcap on a file-like object."""
    header = b""
    while len(header) < 24:
        piece = f.read(24 - len(header))
        if not piece:
            return
        header += piece
    magic = struct.unpack("<I", header[:4])[0]
    if magic not in PCAP_MAGICS:
        raise ValueError(f"not a pcap file (magic {magic:#x})")
    endian, frac = PCAP_MAGICS[magic]

    buf = b""
    while True:
        if len(buf) < 16:
            piece = f.read(chunk)
            if not piece:
                return
            buf += piece
            continue
        ts_s, ts_f, caplen, _wire = struct.unpack(endian + "IIII", buf[:16])
        buf = buf[16:]
        while len(buf) < caplen:
            piece = f.read(max(chunk, caplen - len(buf)))
            if not piece:
                return
            buf += piece
        yield ts_s + ts_f * frac, buf[:caplen]
        buf = buf[caplen:]


def iter_csi(f, mac: str | None = None):
    """Yield (t, rssi, seq, complex subcarriers) for CSI packets on stream f."""
    want = bytes.fromhex(mac.replace(":", "")) if mac else None
    for ts, payload in _iter_pcap_records(f):
        if len(payload) < 42 or payload[12:14] != b"\x08\x00":
            continue                       # not IPv4
        ihl = (payload[14] & 15) * 4
        if len(payload) < ihl + 12:
            continue
        if payload[23] != 17:              # not UDP
            continue
        # UDP dst port lives right after the 14 B Ethernet + IP headers
        if struct.unpack(">H", payload[14 + ihl + 2:14 + ihl + 4])[0] != CSI_PORT:
            continue
        parsed = _parse_csi_payload(payload[14 + ihl + 8:], want)
        if parsed is None:
            continue
        rssi, seq, csi = parsed
        yield ts, rssi, seq, csi


# -- sources -----------------------------------------------------------------

class ReplaySource:
    """Replay a recorded pcap at (a multiple of) its original pace."""

    def __init__(self, path, speed=1.0, loop=False):
        self.path = path
        self.speed = max(0.1, float(speed))
        self.loop = loop

    def run(self, push, stop: threading.Event):
        while not stop.is_set():
            with open(self.path, "rb") as f:
                first = None
                wall0 = time.monotonic()
                for ts, rssi, seq, csi in iter_csi(f):
                    if stop.is_set():
                        return
                    if first is None:
                        first = ts
                    wait = (ts - first) / self.speed - (
                        time.monotonic() - wall0
                    )
                    while wait > 0.02 and not stop.is_set():
                        stop.wait(min(0.1, wait))
                        wait = (ts - first) / self.speed - (
                            time.monotonic() - wall0
                        )
                    push(time.monotonic(), csi)
            if not self.loop:
                return


class LiveSource:
    """Live CSI: parse the tcpdump pipe with the shared iter_csi logic.
    Requires the Pi to be in CSI mode first (scripts/pi/csi_on.sh)."""

    def __init__(self, cmd, mac=None, restart_delay=2.0):
        self.cmd = list(cmd)
        self.mac = mac
        self.restart_delay = restart_delay

    def run(self, push, stop: threading.Event):
        while not stop.is_set():
            try:
                proc = subprocess.Popen(
                    self.cmd, stdout=subprocess.PIPE, bufsize=65536,
                )
            except OSError:
                stop.wait(self.restart_delay)
                continue
            try:
                for _ts, _rssi, _seq, csi in iter_csi(proc.stdout, self.mac):
                    if stop.is_set():
                        break
                    push(time.monotonic(), csi)
            except (OSError, ValueError):
                pass
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if stop.is_set():
                return
            stop.wait(self.restart_delay)


class SimSource:
    """Synthetic CSI. `activity(now) -> 'empty' | 'walking' | 'lying' | 'fall'
    comes from the simulated room (or anything else) at call time."""

    def __init__(self, activity, rate_hz=100.0, nfft=64, seed=7):
        self.activity = activity
        self.rate_hz = rate_hz
        rng = np.random.default_rng(seed)
        k = np.arange(nfft)
        # two static multipath fingerprints: "empty" and "person present"
        self.shape_empty = 0.5 + 0.35 * np.abs(np.sin(k * 0.72 + 0.3))
        self.shape_person = 0.5 + 0.35 * np.abs(np.sin(k * 0.51 + 1.7))
        self.phase = rng.uniform(0, 2 * np.pi, nfft).astype(np.float32)

    def run(self, push, stop: threading.Event):
        period = 1.0 / self.rate_hz
        nxt = time.monotonic()
        t_sim = 0.0
        while not stop.is_set():
            mode = self.activity()
            amp = self._packet(t_sim, mode)
            if amp is not None:
                push(time.monotonic(), amp)
            t_sim += period
            nxt += period
            wait = nxt - time.monotonic()
            if wait > 0:
                stop.wait(min(0.05, wait))
            else:
                nxt = time.monotonic()

    def _packet(self, t, mode) -> np.ndarray | None:
        noise = np.random.default_rng(int(t * 1000) & 0xFFFF)
        base = np.where(
            mode in ("lying", "fall"), self.shape_person, self.shape_empty
        )
        amp = base * (1.0 + 0.03 * noise.standard_normal(base.size) * 0.5)
        if mode in ("walking", "fall"):
            depth = 0.16 if mode == "walking" else 0.24
            speed = 2 * math.pi * (0.9 if mode == "walking" else 1.4)
            amp = amp * (1.0 + depth * np.sin(speed * t + self.phase))
        # gain drift, like a real AGC
        amp *= 1.0 + 0.05 * math.sin(0.23 * t)
        return amp.astype(np.complex64) * (1 + 0.02j)  # tiny imaginary part


# -- scoring ------------------------------------------------------------------

class CsiProcessor:
    """Thread-safe motion scorer + waterfall columns.

    push() runs on the feed thread; snapshot()/calibrate_*() on the UI side.
    """

    def __init__(self, window_s=2.0, factor=1.5, history_s=20.0):
        self.window_s = window_s
        self.factor = factor
        self.lock = threading.Lock()
        self.norm = deque()          # (t, np normalized amp)
        self.times = deque()          # every packet time (for Hz)
        self.means = deque(maxlen=GLITCH_MEDIAN_PACKETS)
        self.usable = None            # bool mask, learned from data
        self._raw_recent = deque(maxlen=300)
        self._since_mask_update = 0
        self.last_packet_t = None
        self.baseline = None          # calibrated empty-room motion score
        self.threshold = None
        self.shape_base = None        # calibrated empty-room shape
        self.shape_scatter = None
        self.cal_until = None
        self.cal_scores = []
        self.cal_shapes = []
        self._last_score_t = 0.0

    # -- feed thread -------------------------------------------------------

    def push(self, wall_t: float, csi: np.ndarray) -> None:
        amp = np.abs(np.asarray(csi))
        with self.lock:
            self.times.append(wall_t)
            self.last_packet_t = wall_t
            if self.usable is None:
                if len(self._raw_recent) < 64:
                    self._raw_recent.append(amp)
                    return
                self._refresh_mask()
            self._since_mask_update += 1
            self._raw_recent.append(amp)
            if self._since_mask_update >= USABLE_UPDATE_EVERY:
                self._refresh_mask()
                self._since_mask_update = 0

            mean = float(np.mean(amp[self.usable])) if self.usable.any() else 0.0
            if mean <= 1e-6:
                return
            # glitch packets (AGC spikes) are excluded from scoring
            if len(self.means) >= 20:
                med = float(np.median(self.means))
                if abs(mean - med) > 0.25 * med:
                    self.means.append(mean)
                    return
            self.means.append(mean)

            vec = amp / mean
            self.norm.append((wall_t, vec))
            cutoff = wall_t - 20.0
            while self.norm and self.norm[0][0] < cutoff:
                self.norm.popleft()
            while self.times and self.times[0] < wall_t - 10.0:
                self.times.popleft()

            # during calibration: collect score + shape samples
            if self.cal_until is not None and wall_t < self.cal_until:
                if wall_t - self._last_score_t >= 0.5:
                    self._last_score_t = wall_t
                    score = self._motion_score_locked(wall_t)
                    if score is not None:
                        self.cal_scores.append(score)
                    shape = self._shape_locked(wall_t, 5.0)
                    if shape is not None:
                        self.cal_shapes.append(shape)
                if wall_t >= self.cal_until - 0.01 and self.cal_scores:
                    self._finish_calibration_locked()

    def _refresh_mask(self) -> None:
        """Usable subcarriers: median amplitude within [0.4, 2] of the overall
        median; index 0 (DC) always dropped (same rule as phase 0)."""
        if not self._raw_recent:
            return
        med = np.median(
            np.concatenate([a[None, :] for a in self._raw_recent]), axis=0
        )
        overall = float(np.median(med))
        if overall <= 0:
            return
        mask = (med > 0.4 * overall) & (med < 2.0 * overall)
        mask[0] = False
        if not mask.any():
            mask[:] = True
            mask[0] = False
        self.usable = mask

    # -- scoring internals (call with the lock held) -----------------------

    def _window_vecs(self, now):
        out = [v for t, v in self.norm if now - self.window_s <= t <= now]
        return out

    def _motion_score_locked(self, now) -> float | None:
        vecs = self._window_vecs(now)
        if len(vecs) < MIN_PACKETS_FOR_SCORE or self.usable is None:
            return None
        mat = np.stack(vecs)[:, self.usable]
        return float(np.mean(mat.std(axis=0)))

    def _shape_locked(self, now, span=5.0) -> np.ndarray | None:
        rows = [v for t, v in self.norm if now - span <= t <= now]
        if len(rows) < 10 or self.usable is None:
            return None
        mat = np.stack(rows)[:, self.usable]
        return mat.mean(axis=0)

    def _finish_calibration_locked(self) -> None:
        if not self.cal_scores:
            self.cal_until = None
            return
        self.baseline = float(np.median(self.cal_scores))
        self.threshold = self.baseline * self.factor
        if self.cal_shapes:
            shapes = np.stack(self.cal_shapes)
            self.shape_base = shapes.mean(axis=0)
            self.shape_scatter = float(np.median(
                np.linalg.norm(shapes - self.shape_base, axis=1)
            ))
        self.cal_until = None

    # -- UI side ------------------------------------------------------------

    def calibrate(self, now: float, seconds: float = 10.0) -> None:
        with self.lock:
            self.cal_until = now + seconds
            self.cal_scores = []
            self.cal_shapes = []

    def finish_calibration(self) -> None:
        """Compute baseline/threshold from whatever was collected. The App
        calls this once the calibration window has elapsed."""
        with self.lock:
            self._finish_calibration_locked()

    def calibrating(self, now: float) -> bool:
        return self.cal_until is not None and now < self.cal_until

    def motion_score(self, now: float) -> float | None:
        with self.lock:
            return self._motion_score_locked(now)

    def shape_distance(self, now: float) -> float | None:
        with self.lock:
            shape = self._shape_locked(now)
            if shape is None or self.shape_base is None:
                return None
            return float(np.linalg.norm(shape - self.shape_base))

    def hz(self, now: float) -> float:
        with self.lock:
            count = sum(1 for t in self.times if t > now - 5.0)
            return count / 5.0

    def alive(self, now: float) -> bool:
        with self.lock:
            return (self.last_packet_t is not None
                    and now - self.last_packet_t < 5.0)

    def snapshot(self, now: float) -> dict:
        """Everything the UI draws in one call, so the lock is taken once."""
        with self.lock:
            score = self._motion_score_locked(now)
            column = None
            if self.usable is not None and self.norm:
                column = np.clip(self.norm[-1][1][self.usable], 0.0, 2.0)
            shape_dist = None
            shape_changed = False
            if self.shape_base is not None:
                shape = self._shape_locked(now)
                if shape is not None:
                    shape_dist = float(np.linalg.norm(shape - self.shape_base))
                    limit = 3.0 * (self.shape_scatter or 0.02) + 0.05
                    shape_changed = shape_dist > limit
            count = sum(1 for t in self.times if t > now - 5.0)
            return {
                "column": column,
                "motion_score": score,
                "baseline": self.baseline,
                "threshold": self.threshold,
                "moving": (score is not None and self.threshold is not None
                           and score > self.threshold),
                "shape_distance": shape_dist,
                "shape_changed": shape_changed,
                "hz": count / 5.0,
                "alive": (self.last_packet_t is not None
                          and now - self.last_packet_t < 5.0),
                "calibrating": self.calibrating(now),
            }
