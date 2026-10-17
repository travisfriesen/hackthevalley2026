#!/usr/bin/env bash
# KAZE touchscreen UI - full launcher for the Raspberry Pi 4B.
#
# usage: scripts/pi/run_ui.sh [ui/main.py args]
#        scripts/pi/run_ui.sh --csi live            # nexmon CSI must be on
#        scripts/pi/run_ui.sh --csi replay          # recorded takes, no radio
#
# Starts, in order:
#   1. sound/audio_receiver.py   (laptop streams the mic in; writes the
#      rolling WAV to /dev/shm on Linux to protect the SD card)
#   2. sound/fall_detector.py    (run from /dev/shm so its default
#      "rolling_audio.wav" path resolves; events go to /dev/shm/fall_events.csv)
#   3. ui/main.py --fullscreen   (the touchscreen UI; see docs/UI.md)
#
# Prereqs on the Pi (once):
#   sudo apt install python3-tk espeak-ng libportaudio2
#   pip install numpy
#   scripts/pi/enable_tcpdump.sh         # tcpdump without sudo (live CSI)
#   live CSI: scripts/pi/csi_on.sh <chan/bw> <pico-mac> must have been run
#             first (it takes wlan0 down; keep the Pico powered separately).
#   HC-SR04 wiring as in scripts/pi/us_log.py: us1 echo BCM17 / trig BCM4,
#             us2 echo BCM27 / trig BCM22 (echo through the divider).
set -uo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

PIDS=()
cleanup() {
    for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done
}
trap cleanup EXIT INT TERM

# The receiver is started on its own by scripts/pi/start_audio.sh so it survives UI restarts and
# training sessions; only start one here if nothing is listening on its port yet.
if ss -ltn | grep -q ':5005 '; then
    echo "== audio receiver already running =="
else
    echo "== starting audio receiver (WAV in /dev/shm) =="
    python3 sound/audio_receiver.py &
    PIDS+=($!)
fi

echo "== starting fall detector (cwd /dev/shm) =="
SOUND_PY="${SOUND_PY:-$HOME/venv-sound/bin/python}"   # tensorflow_hub lives in this venv, not system python
( cd /dev/shm && exec "$SOUND_PY" "$REPO/sound/fall_detector.py" ) &
PIDS+=($!)

echo "== starting UI (kiosk) =="
python3 ui/main.py --fullscreen "$@" &
PIDS+=($!)

wait -n
cleanup
