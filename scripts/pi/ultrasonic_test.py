#!/usr/bin/env python3
"""Print HC-SR04 distance (cm) twice a second. Wiring: TRIG=GPIO23 (pin 16), ECHO=GPIO24 (pin 18, via 1k/2k divider).
usage: ultrasonic_test.py [seconds=20]   Wave a hand at 10-50 cm, then aim at a wall: readings should follow."""
import sys, time, statistics
from gpiozero import DistanceSensor

s = DistanceSensor(echo=24, trigger=23, max_distance=3.0, queue_len=1)
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 20
t0 = time.time()
while time.time() - t0 < secs:
    r = []
    for _ in range(5):
        r.append(s.distance * 100); time.sleep(0.06)
    m = statistics.median(r)
    note = "  (max range / no echo?)" if m >= 299 else ""
    print(f"{m:6.1f} cm   spread {max(r) - min(r):4.1f}{note}", flush=True)
