#!/usr/bin/env bash
# Guided multi-sensor training session. Run on the Mac; records on the Pi over SSH. You read the prompts to the person performing.
# usage: scripts/session.sh [name=session1] [seconds=30]
#   -> data/<name>/<label>_<time>.pcap (CSI), .us.csv (ultrasonic: d1 = top sensor, d2 = bottom), .wav (audio)
# Needs the phone mic (sound/phone_mic.py) or audio_sender.py streaming into audio_receiver.py on the Pi.
# Pi must already be in CSI mode (csi_on.sh) with the Pico ~3 m away, powered separately.
set -uo pipefail
NAME="${1:-session1}"; SECS="${2:-30}"; PI=travis@100.127.214.108
OUT="$(cd "$(dirname "$0")/.." && pwd)/data/$NAME"; mkdir -p "$OUT"
rec() { ssh -o BatchMode=yes "$PI" "~/hackthevalley2026/scripts/pi/csi_record.sh $1 $2"; }

echo "== stopping the UI (it holds the ultrasonic pins); audio receiver stays up =="
ssh -o BatchMode=yes "$PI" '~/hackthevalley2026/scripts/pi/stop_ui.sh'
echo "== pre-flight: ultrasonic (3 s) =="
ssh -o BatchMode=yes "$PI" 'timeout 15 python3 ~/hackthevalley2026/scripts/pi/us_log.py /tmp/us_pre.csv 3 >/dev/null 2>&1; awk -F, "NR>1{n++; if(\$2<399)a++; if(\$3<399)b++} END{printf \"ultrasonic reads: %d | d1 (top) echo %d | d2 (bottom) echo %d  (no echo is normal if it faces open space)\n\", n, a, b}" /tmp/us_pre.csv'
echo "== pre-flight: audio (last 5 s) =="
ssh -o BatchMode=yes "$PI" 'python3 -c "
import wave, numpy as np
w = wave.open(\"/dev/shm/rolling_audio.wav\"); n = w.getnframes(); r = w.getframerate()
d = np.frombuffer(w.readframes(n), \"<i2\")[-5 * r:]
print(\"audio peak (last 5 s):\", int(np.abs(d).max()), \"of 32767 - clap or speak now; a flat ~0-30 means the phone mic is not streaming\")" 2>&1 | tail -1'
echo "== pre-flight: 6 s CSI check =="
n=$(ssh -o BatchMode=yes "$PI" 'timeout 6 tcpdump -i wlan0 -w /tmp/pre.pcap >/dev/null 2>&1; tcpdump -nr /tmp/pre.pcap dst port 5500 2>/dev/null | wc -l')
echo "CSI frames in 6 s: $n (expect ~1200 at 200 Hz)"
[ "${n:-0}" -ge 300 ] || { echo "Pico not heard. Check it is powered/blinking slowly, hotspot on, Pi in CSI mode. Aborting."; exit 1; }
ssh -o BatchMode=yes "$PI" 'touch ~/captures/.start_'"$NAME"

# label | seconds | what to say to the person
TAKES=(
"empty|$SECS|LEAVE the room (out of sight of the Pico-Pi line) and stay out. Nobody in the room."
"walking|$SECS|WALK back and forth across the line between the Pico and the Pi, steady pace, the whole time."
"standing|$SECS|STAND still in the middle of the line (between Pico and Pi), arms at your sides. Breathe normally."
"sitting|$SECS|SIT on a chair in the middle of the line, stay still. Breathe normally."
"lying|$SECS|LIE on the floor/mat across the middle of the line, stay still, as if you cannot get up."
"fall|$SECS|Start 3 m away. WALK to the middle, FALL onto the mat (controlled!), then LIE completely still until time is up."
"empty|$SECS|LEAVE the room again. Nobody in the room."
"lying|$SECS|LIE on the floor/mat across the middle of the line, stay still."
"sitting|$SECS|SIT on the chair in the middle of the line, stay still."
"standing|$SECS|STAND still in the middle of the line."
"walking|$SECS|WALK back and forth across the line, steady pace."
"fall|$SECS|Start 3 m away. WALK to the middle, FALL onto the mat, then LIE completely still."
"fall|$SECS|Again: WALK to the middle, FALL onto the mat, then LIE completely still."
)
i=0; total=${#TAKES[@]}
for t in "${TAKES[@]}"; do
  i=$((i+1)); IFS='|' read -r label secs say <<<"$t"
  printf '\n==== take %d/%d: %s (%ss) ====\nTell them: %s\n' "$i" "$total" "$label" "$secs" "$say"
  read -rp "Enter when they are in position (s = skip, q = quit)... " a
  [ "$a" = q ] && break; [ "$a" = s ] && continue
  for c in 5 4 3 2 1; do printf '\rstarting in %d ' "$c"; sleep 1; done; printf '\rRECORDING %ss -- GO          \n' "$secs"
  rec "$label" "$secs"
done

echo; echo "== copying captures to $OUT =="
ssh -o BatchMode=yes "$PI" 'cd ~/captures && find . \( -name "*.pcap" -o -name "*.csv" -o -name "*.wav" \) -newer .start_'"$NAME"' -print0 | tar --null -T - -cf -' | tar -xf - -C "$OUT"
ls -l "$OUT"
echo "Restart the UI on the Pi when done:  ssh $PI 'bash /tmp/restart_ui.sh'  (or scripts/pi/run_ui.sh --csi live --us gpio)"
