#!/usr/bin/env bash
# Record CSI to a pcap for a fixed time. Run AFTER csi_start.sh's setup (params + monitor mode).
# usage: csi_record.sh <label> [seconds=30]     e.g. csi_record.sh empty 30
# Output: ~/captures/<label>_<timestamp>.pcap  (copy to the Mac after restore-wifi)
set -euo pipefail
LABEL="${1:?label, e.g. empty|walking}"; SECS="${2:-30}"
/usr/sbin/getcap /usr/bin/tcpdump 2>/dev/null | grep -q cap_net_raw && SUDO="" || SUDO=sudo  # no sudo once enable_tcpdump.sh was run
mkdir -p ~/captures
OUT=~/captures/${LABEL}_$(date +%Y%m%d_%H%M%S).pcap
echo "recording $SECS s -> $OUT (get in position now)"
# no live BPF filter: it recorded 0 frames in the first run while an unfiltered capture worked (diag.sh)
# ultrasonic log in parallel (same Pi clock), skipped if gpiozero/sensors are unavailable
python3 "$(dirname "$0")/us_log.py" "${OUT%.pcap}.us.csv" "$SECS" >/dev/null 2>&1 &
$SUDO timeout "$SECS" tcpdump -i wlan0 -w "$OUT" || true
wait
echo "CSI frames (udp 5500): $(tcpdump -nr "$OUT" dst port 5500 2>/dev/null | wc -l)  (expect ~ 90 x $SECS)"
