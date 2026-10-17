#!/usr/bin/env bash
# Record CSI to a pcap for a fixed time. Run AFTER csi_start.sh's setup (params + monitor mode).
# usage: csi_record.sh <label> [seconds=30]     e.g. csi_record.sh empty 30
# Output: ~/captures/<label>_<timestamp>.pcap  (+ .us.csv ultrasonic, + .wav audio slice if available)
# Stop the UI first (scripts/pi/stop_ui.sh): it holds the ultrasonic GPIO pins. Audio comes from the rolling
# WAV, so audio_receiver.py (phone_mic.py / audio_sender.py) must be running.
set -euo pipefail
LABEL="${1:?label, e.g. empty|walking}"; SECS="${2:-30}"
/usr/sbin/getcap /usr/bin/tcpdump 2>/dev/null | grep -q cap_net_raw && SUDO="" || SUDO=sudo  # no sudo once enable_tcpdump.sh was run
mkdir -p ~/captures
OUT=~/captures/${LABEL}_$(date +%Y%m%d_%H%M%S).pcap
echo "recording $SECS s -> $OUT (get in position now)"
# no live BPF filter: it recorded 0 frames in the first run while an unfiltered capture worked (diag.sh)
# ultrasonic log in parallel (same Pi clock), skipped if gpiozero/sensors are unavailable
T0=$(date +%s.%N)
timeout $((SECS + 10)) python3 "$(dirname "$0")/us_log.py" "${OUT%.pcap}.us.csv" "$SECS" >/dev/null 2>&1 &
$SUDO timeout "$SECS" tcpdump -i wlan0 -w "$OUT" || true
T1=$(date +%s.%N)
wait || true
echo "CSI frames (udp 5500): $(tcpdump -nr "$OUT" dst port 5500 2>/dev/null | wc -l)  (expect ~ 90 x $SECS)"
us=$(( $(wc -l < "${OUT%.pcap}.us.csv" 2>/dev/null || echo 0) - 1 ))
[ "$us" -ge 5 ] && echo "ultrasonic rows: $us (d1 = top sensor, d2 = bottom)" || echo "WARNING: ultrasonic log empty/short - is the UI still holding the GPIO pins?"
if [ -f /dev/shm/rolling_audio.wav ]; then
  sleep 3   # the rolling WAV is republished every ~2 s
  python3 "$(dirname "$0")/audio_slice.py" "${OUT%.pcap}.wav" "$T0" "$T1" || echo "WARNING: audio slice failed"
else
  echo "WARNING: no rolling WAV - audio_receiver.py not running, no audio for this take"
fi
