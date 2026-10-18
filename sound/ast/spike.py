"""Impulse/spike detector: finds candidate fall thumps so a classifier can look at a 3 s clip.

Follows the SAFE paper (sec 3.2): 50-sample running mean -> abs -> find spikes -> 1.5 s either side.
Instead of an HMM, a spike is a 10 ms frame whose envelope exceeds RATIO x a slowly adapting background.

Usage: python spike.py            (self-test / report on the local datasets)
In code: trig = SpikeTrigger(); hit = trig.update(chunk)  ->  None | (event_time_s, clip_48000)
"""
import sys
from pathlib import Path

import numpy as np

SR = 16000
CLIP = 3 * SR
SMOOTH = 50              # running-mean length (samples), as in the paper
FRAME = SR // 100        # 10 ms envelope frames
RATIO = 8.0              # spike = envelope > RATIO x background (~18 dB; fall_detector uses 15 dB on RMS)
FLOOR = 0.03             # ...and above this absolute smoothed amplitude (rejects room-noise bumps)
TAU = 10.0               # background time constant, seconds
PEAK_WINDOW = 0.3        # after the trigger, look this long for the true peak (the spike centre)
REFRACTORY = 2.0         # seconds after a trigger during which nothing else fires
KEEP = 8 * SR            # rolling buffer length


def clip_around(audio, centre_idx):
    """Exactly 48000 samples centred on centre_idx (zero-padded at the edges)."""
    start = int(centre_idx) - CLIP // 2
    out = np.zeros(CLIP, dtype=np.float32)
    lo, hi = max(start, 0), min(start + CLIP, len(audio))
    if hi > lo:
        out[lo - start:hi - start] = audio[lo:hi]
    return out


class SpikeTrigger:
    """Streaming spike detector. Feed consecutive chunks to update()."""

    def __init__(self, ratio=RATIO, floor=FLOOR, tau=TAU, refractory=REFRACTORY):
        self.ratio, self.floor, self.refractory = ratio, floor, refractory
        self.alpha = FRAME / SR / tau
        self.buf = np.zeros(0, dtype=np.float32)   # rolling audio
        self.buf_start = 0                          # absolute sample index of buf[0]
        self.n = 0                                  # total samples seen
        self.frames = 0                             # frames processed
        self.bg = None                              # background envelope level
        self.until = 0                              # no trigger before this frame (refractory)
        self.pending = None                         # [trigger_frame, best_env, best_frame]
        self.centres = []                           # every finalised spike centre (sample index)
        self.queue = []                             # centres waiting for 1.5 s of trailing audio

    def _envelope(self, chunk):
        """abs(50-sample running mean) -> max per 10 ms frame, for the newly complete frames."""
        a = self.frames * FRAME
        b = (self.n // FRAME) * FRAME
        s = a - SMOOTH + 1
        seg = self.buf[max(s, 0) - self.buf_start:b - self.buf_start]
        pad = max(-s, 0)   # zeros before sample 0
        c = np.cumsum(np.concatenate((np.zeros(1 + pad), seg)), dtype=np.float64)
        smooth = np.abs(c[SMOOTH:] - c[:-SMOOTH]) / SMOOTH
        return smooth[:b - a].reshape(-1, FRAME).max(axis=1)

    def _finalise(self):
        _, _, f = self.pending
        centre = f * FRAME + FRAME // 2
        self.centres.append(centre)
        self.queue.append(centre)
        self.pending = None

    def update(self, chunk):
        """Add a mic chunk. Returns (event_time_s, clip) once 1.5 s after a spike has arrived."""
        chunk = np.asarray(chunk, dtype=np.float32).ravel()
        self.buf = np.concatenate((self.buf, chunk))
        self.n += len(chunk)
        if self.n // FRAME > self.frames:
            for e in self._envelope(chunk):
                self._step(e)
                self.frames += 1
        if len(self.buf) > KEEP:
            drop = len(self.buf) - KEEP
            self.buf, self.buf_start = self.buf[drop:], self.buf_start + drop
        if self.queue and self.n >= self.queue[0] + CLIP // 2:
            centre = self.queue.pop(0)
            rel = centre - self.buf_start
            return centre / SR, clip_around(self.buf, rel)
        return None

    def _step(self, e):
        f = self.frames
        if self.bg is None:
            self.bg = max(e, 1e-4)
        if self.pending:
            if e > self.pending[1]:
                self.pending[1:] = [e, f]
            if f - self.pending[0] >= PEAK_WINDOW * SR / FRAME:
                self._finalise()
            return
        if f >= self.until and e > max(self.ratio * self.bg, self.floor):
            self.pending = [f, e, f]
            self.until = f + int(self.refractory * SR / FRAME)
        elif f >= self.until:   # only learn the background from quiet, non-event frames
            # fast at start-up, then slow; clipped so loud sounds can't drag it up quickly
            a = max(self.alpha, 1 / (f + 1)) if f < 100 else self.alpha
            self.bg += a * (min(e, 2 * self.bg) - self.bg)

    def flush(self):
        """End of a finite file: finalise a spike whose peak window is still open."""
        if self.pending:
            self._finalise()


def find_spikes(audio16k, sr=SR, **kw):
    """Sample indices of spike centres in a whole array (same detector as the streaming one)."""
    assert sr == SR, "resample to 16 kHz first"
    trig = SpikeTrigger(**kw)
    for i in range(0, len(audio16k), SR):
        trig.update(audio16k[i:i + SR])
    trig.flush()
    return trig.centres


# ---------------------------------------------------------------- self-test
def _load16k(path):
    import soundfile as sf
    from scipy.signal import resample_poly
    x, sr = sf.read(path, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != SR:
        from math import gcd
        g = gcd(SR, sr)
        x = resample_poly(x, SR // g, sr // g).astype(np.float32)
    return x


def _report():
    here = Path(__file__).resolve().parent.parent
    rng = np.random.default_rng(0)
    files = sorted((here / "data" / "kaggle_fall").glob("*.wav"))
    falls = [_load16k(f) for f in files if f.stem.endswith("-01")]
    nonf = [_load16k(f) for f in files if f.stem.endswith("-02")]
    print(f"SAFE clips: {len(falls)} falls, {len(nonf)} non-falls")

    off, hit, nspk = [], 0, 0
    for x in falls:
        c = find_spikes(x)
        nspk += len(c)
        if c:
            hit += 1
            off.append(min((abs(i - CLIP // 2) for i in c)) / SR)
    off = np.array(off)
    print(f"falls detected: {hit}/{len(falls)} = {hit/len(falls):.1%}; spikes/clip {nspk/len(falls):.2f}")
    print(f"  offset from 1.5 s (closest spike): median {np.median(off)*1000:.0f} ms, "
          f"p90 {np.percentile(off, 90)*1000:.0f} ms, within 0.25 s {np.mean(off < .25):.1%}, "
          f"within 0.5 s {np.mean(off < .5):.1%}")
    print(f"non-fall clips that trigger: {np.mean([len(find_spikes(x)) > 0 for x in nonf]):.1%}")

    # falls embedded at random positions in 10 s of non-fall noise
    ok = near = n = 0
    for x in falls:
        bg = np.concatenate([nonf[i] for i in rng.integers(len(nonf), size=4)])[:10 * SR].copy()
        pos = int(rng.integers(0, 10 * SR - len(x)))
        bg[pos:pos + len(x)] = bg[pos:pos + len(x)] * 0.5 + x   # mix in, keep background audible
        centre = pos + len(x) // 2
        c = find_spikes(bg)
        n += 1
        ok += bool(c)
        near += any(abs(i - centre) < SR // 2 for i in c)
    print(f"embedded in 10 s of noise: any spike {ok/n:.1%}, spike within 0.5 s of true centre {near/n:.1%}")

    room = _load16k(here / "rolling_audio.wav")
    c = find_spikes(room)
    print(f"rolling_audio.wav: {len(room)/SR:.0f} s, {len(c)} triggers = {len(c)/(len(room)/SR/60):.2f}/min at "
          f"{[round(i/SR, 1) for i in c][:20]}")

    spliced = Path(sys.argv[1] if len(sys.argv) > 1 else "")
    if spliced.is_file():
        s = _load16k(spliced)
        print(f"{spliced.name}: {[round(i/SR, 1) for i in find_spikes(s)]}")

    # streaming vs offline
    for name, x in (("rolling", room), ("falls[0]", falls[0])):
        trig, got = SpikeTrigger(), []
        for i in range(0, len(x), 1024):
            r = trig.update(x[i:i + 1024])
            if r:
                assert len(r[1]) == CLIP
                got.append(round(r[0] * SR))
        trig.flush()
        print(f"stream(1024) {name}: {len(got)} emitted, centres match offline: "
              f"{trig.centres == find_spikes(x)} (emitted subset ok: {set(got) <= set(trig.centres)})")


if __name__ == "__main__":
    _report()
