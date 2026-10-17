#!/usr/bin/env python3
"""Cut [t0, t1] (epoch seconds) out of the rolling WAV into a wav file, for labelled takes.

usage: audio_slice.py out.wav t0 t1 [rolling.wav]

The rolling WAV holds the newest 300 s and is republished every ~2 s; its mtime is when the
newest sample was published, so sample positions are accurate to about +-2 s.
"""
import os
import sys
import wave

import numpy as np

out, t0, t1 = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
src = sys.argv[4] if len(sys.argv) > 4 else "/dev/shm/rolling_audio.wav"
with wave.open(src) as w:
    rate, n = w.getframerate(), w.getnframes()
    data = np.frombuffer(w.readframes(n), "<i2")
end_t = os.path.getmtime(src)
a, b = int(n - (end_t - t0) * rate), int(n - (end_t - t1) * rate)
seg = np.concatenate([np.zeros(max(0, -a), "<i2"), data[max(a, 0):max(b, 0)]])
with wave.open(out, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(seg.tobytes())
print(f"audio {len(seg) / rate:.1f} s, peak {int(np.abs(seg).max()) if len(seg) else 0}")
