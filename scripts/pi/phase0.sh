#!/usr/bin/env bash
# Phase 0 go/no-go, one command, run at the Pi's keyboard (Wi-Fi goes down, comes back at the end).
# usage: [ROUNDS=2] ./scripts/pi/phase0.sh [seconds=30]   (rounds alternate empty,walking,empty,walking...)
# Records "empty" then "walking" ROUNDS times CSI from the Pico (MAC/channel below), then restores Wi-Fi.
set -uo pipefail
cd "$(dirname "$0")"
SECS="${1:-30}"; CHAN="11/20"; MAC="2c:cf:67:c5:c4:14"

echo "This drops Wi-Fi/SSH until the end. Pico must be powered and sending (hotspot on, 2.4 GHz)."
read -rp "Press Enter to start (Ctrl-C to abort)... " _
sudo -v   # cache sudo password once up front

trap 'echo; echo "== restoring Wi-Fi =="; YES=1 ./install_nexmon_csi.sh restore' EXIT

YES=1 ./install_nexmon_csi.sh activate || exit 1
NOSNIFF=1 ./csi_start.sh "$CHAN" "$MAC" || exit 1

# pre-flight: refuse to record if the Pico isn't being heard (it needs ~15 s after power-up to join the hotspot)
preflight() {
  sudo timeout 6 tcpdump -i wlan0 -w /tmp/pre.pcap >/dev/null 2>&1
  n=$(tcpdump -nr /tmp/pre.pcap dst port 5500 2>/dev/null | wc -l)
  echo "pre-flight: $n CSI frames in 6 s (expect ~540)"
  [ "$n" -ge 100 ]
}
until preflight; do
  read -rp "Pico not heard. Check it is powered, LED blinking, hotspot on 2.4 GHz with screen awake. Enter to retry, Ctrl-C to quit... " _
done

countdown() { for i in $(seq "$1" -1 1); do printf '\r%s in %2ds ' "$2" "$i"; sleep 1; done; echo; }

for r in $(seq 1 "${ROUNDS:-2}"); do
  echo "== round $r: EMPTY. Leave the room / stay clear of the Pico-Pi line, nobody moving. =="
  countdown 15 "recording EMPTY"
  ./csi_record.sh empty "$SECS"
  echo "== round $r: WALKING. Walk back and forth across the Pico-Pi line the whole time. =="
  countdown 10 "recording WALKING"
  ./csi_record.sh walking "$SECS"
done

echo "== done. Captures: =="; ls -l ~/captures
