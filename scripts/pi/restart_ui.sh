#!/usr/bin/env bash
# Restart the UI stack fully detached from the caller, so it is safe to run
# over ssh: this returns at once instead of hanging until the UI exits.
#
# usage: scripts/pi/restart_ui.sh [run_ui.sh args]   (default: --csi live --us gpio)
#   ssh pi 'cd ~/hackthevalley2026 && scripts/pi/restart_ui.sh'
cd "$(dirname "$0")/../.."
bash scripts/pi/stop_ui.sh
export DISPLAY="${DISPLAY:-:0}" XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
[ $# -eq 0 ] && set -- --csi live --us gpio
setsid nohup bash scripts/pi/run_ui.sh "$@" > ~/ui_run.log 2>&1 < /dev/null &
echo "UI restarting (log: ~/ui_run.log)"
