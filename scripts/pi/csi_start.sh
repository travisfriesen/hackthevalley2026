#!/usr/bin/env bash
# Configure CSI extraction + monitor mode, then print CSI UDP packets (port 5500).
# usage: csi_start.sh <channel/bandwidth> <transmitter-MAC>   e.g. csi_start.sh 6/20 aa:bb:cc:dd:ee:ff
# Channel must match the AP the Pico joins. 2.4 GHz/20 MHz is UNVERIFIED for this setup.
set -euo pipefail
CHAN="${1:?channel/bw, e.g. 6/20}"; MAC="${2:?transmitter MAC}"
MCP="${WORK:-$HOME/nexmon-work}/nexmon/patches/bcm43455c0/7_45_189/nexmon_csi/utils/makecsiparams/makecsiparams"
PARAMS="$("$MCP" -c "$CHAN" -C 1 -N 1 -m "$MAC")"
echo "params: $PARAMS"
nexutil -s500 -b -l34 -v"$PARAMS"
nexutil -m1
echo "monitor state: $(nexutil -m)"
[ "${NOSNIFF:-0}" = 1 ] || sudo tcpdump -i wlan0 dst port 5500 "${@:3}"
