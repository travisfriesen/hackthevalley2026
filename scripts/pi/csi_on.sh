#!/usr/bin/env bash
# One command: switch the Pi's Wi-Fi into CSI mode for the Pico (ch 11, MAC below). Keeps the USB tether (usb0) up.
# usage: ./scripts/pi/csi_on.sh        undo: ./scripts/pi/install_nexmon_csi.sh restore
set -euo pipefail
cd "$(dirname "$0")"
YES=1 ./install_nexmon_csi.sh activate
NOSNIFF=1 ./csi_start.sh 11/20 2c:cf:67:c5:c4:14
