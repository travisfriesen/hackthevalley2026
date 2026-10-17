#!/usr/bin/env python3
"""Log both HC-SR04 sensors to CSV at ~10 Hz: epoch_s,d1_cm,d2_cm (400 = no echo). Same Pi clock as the tcpdump pcap times.
usage: us_log.py out.csv seconds     Pins (BCM): sensor1 echo17/trig4, sensor2 echo27/trig22."""
import sys, time, warnings
warnings.filterwarnings("ignore")
from gpiozero import DistanceSensor

out, secs = sys.argv[1], float(sys.argv[2])
s1 = DistanceSensor(echo=17, trigger=4, max_distance=4, queue_len=3)
s2 = DistanceSensor(echo=27, trigger=22, max_distance=4, queue_len=3)
end = time.time() + secs
with open(out, "w") as f:
    f.write("epoch_s,d1_cm,d2_cm\n")
    while time.time() < end:
        t = time.time()
        f.write(f"{t:.3f},{s1.distance * 100:.1f},{s2.distance * 100:.1f}\n"); f.flush()
        time.sleep(max(0, 0.1 - (time.time() - t)))
