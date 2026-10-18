#!/usr/bin/env python3
"""Archive the rolling mic WAV as gapless ~60 s slices, so audio is kept for later training.

usage: audio_archive.py OUTDIR [rolling.wav]

The rolling WAV holds only the newest 300 s and is republished every ~2 s; its mtime is when the
newest sample was published (positions accurate to about +-2 s, same as audio_slice.py). Each slice
starts where the last one ended: audio_<start epoch>.wav.
"""
import os
import sys
import time
import wave

import numpy as np

SLICE_S = 60
LAG_S = 4   # stay behind the newest sample so the slice is fully published


def cut(src: str, t0: float, t1: float):
    """Return (int16 samples, rate) covering epoch [t0, t1]; zeros where the buffer has no audio."""
    with wave.open(src) as w:
        rate, n = w.getframerate(), w.getnframes()
        data = np.frombuffer(w.readframes(n), "<i2")
    end_t = os.path.getmtime(src)
    a, b = int(round(n - (end_t - t0) * rate)), int(round(n - (end_t - t1) * rate))
    seg = np.concatenate([np.zeros(max(0, -a), "<i2"), data[max(a, 0):max(b, 0)]])
    return seg, rate


def main(out_dir: str, src: str = "/dev/shm/rolling_audio.wav") -> None:
    os.makedirs(out_dir, exist_ok=True)
    last_end = time.time() - LAG_S
    while True:
        time.sleep(SLICE_S)
        t1 = time.time() - LAG_S
        try:
            seg, rate = cut(src, last_end, t1)
        except (OSError, EOFError, wave.Error) as exc:
            print(f"{time.strftime('%X')} no audio: {exc}", flush=True)
            last_end = t1
            continue
        with wave.open(os.path.join(out_dir, f"audio_{last_end:.0f}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(seg.tobytes())
        last_end = t1


if __name__ == "__main__":
    main(*sys.argv[1:3])
