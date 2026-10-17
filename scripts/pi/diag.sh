#!/usr/bin/env bash
# Diagnose "0 CSI frames". Run at the Pi's keyboard; Wi-Fi drops then returns. Output: ~/captures/diag.txt
# Separates: (A) Pico frames not seen at all vs (B) seen but CSI extraction not producing UDP.
set -uo pipefail
cd "$(dirname "$0")"
CHAN="11/20"; MAC="2c:cf:67:c5:c4:14"
mkdir -p ~/captures; OUT=~/captures/diag.txt; : > "$OUT"
log() { echo "$@" | tee -a "$OUT"; }
read -rp "Drops Wi-Fi until done (~1 min). Enter to start... " _
sudo -v
trap 'log "== restoring Wi-Fi =="; YES=1 ./install_nexmon_csi.sh restore >>"$OUT" 2>&1' EXIT

YES=1 ./install_nexmon_csi.sh activate >>"$OUT" 2>&1; log "activate rc=$?"
log "firmware in use: $(update-alternatives --query cyfmac43455-sdio.bin | grep Value)"
log "dmesg brcmfmac:"; sudo dmesg | grep -i brcm | tail -8 | tee -a "$OUT"
MCP="${WORK:-$HOME/nexmon-work}/nexmon/patches/bcm43455c0/7_45_189/nexmon_csi/utils/makecsiparams/makecsiparams"
P="$("$MCP" -c "$CHAN" -C 1 -N 1 -m "$MAC")"; log "params: $P"
nexutil -s500 -b -l34 -v"$P" 2>&1 | tee -a "$OUT"; log "nexutil config rc=${PIPESTATUS[0]}"
nexutil -m1 2>&1 | tee -a "$OUT"; log "monitor state (nexutil -m): $(nexutil -m 2>&1)"
log "iw dev:"; iw dev 2>&1 | tee -a "$OUT"

log "== 15 s unfiltered capture on wlan0 (any frame) =="
sudo timeout 15 tcpdump -i wlan0 -w ~/captures/diag_all.pcap >/dev/null 2>&1
log "all frames: $(tcpdump -nr ~/captures/diag_all.pcap 2>/dev/null | wc -l)"
log "frames from Pico MAC (raw 802.11): $(tcpdump -nre ~/captures/diag_all.pcap 2>/dev/null | grep -ci "$MAC")"
log "UDP port 5500 (CSI): $(tcpdump -nr ~/captures/diag_all.pcap dst port 5500 2>/dev/null | wc -l)"
log "first lines:"; tcpdump -nre ~/captures/diag_all.pcap 2>/dev/null | head -5 | tee -a "$OUT"
log "== done; results in $OUT =="
