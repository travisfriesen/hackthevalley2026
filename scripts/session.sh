#!/usr/bin/env bash
# Guided CSI training session. Run on the Mac; records on the Pi over SSH. You read the prompts to the person performing.
# usage: scripts/session.sh [name=session1] [seconds=30]    -> data/<name>/<label>_<time>.pcap
# Pi must already be in CSI mode (csi_on.sh) with the Pico ~3 m away, powered separately.
set -uo pipefail
NAME="${1:-session1}"; SECS="${2:-30}"; PI=travis@100.127.214.108
OUT="$(cd "$(dirname "$0")/.." && pwd)/data/$NAME"; mkdir -p "$OUT"
rec() { ssh -o BatchMode=yes "$PI" "~/hackthevalley2026/scripts/pi/csi_record.sh $1 $2"; }

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
ssh -o BatchMode=yes "$PI" 'cd ~/captures && find . \( -name "*.pcap" -o -name "*.csv" \) -newer .start_'"$NAME"' -print0 | tar --null -T - -cf -' | tar -xf - -C "$OUT"
ls -l "$OUT"
