#!/usr/bin/env bash
# Continuous background capture for training data. Safe to run next to the UI.
# usage: scripts/pi/collect.sh start|stop|status
#
# Writes to ~/captures/continuous/ (Pi clock everywhere, so streams line up):
#   csi_<time>.pcap     5-minute CSI pcap files (tcpdump on wlan0; needs scripts/pi/csi_on.sh first)
#   audio_<epoch>.wav   ~60 s gapless slices of the rolling mic WAV (audio_archive.py)
#   us_<date>.csv       ultrasonic distances, written by the UI itself (it owns the GPIO pins)
#   labels.jsonl        your key presses (scripts/pi/label.py)
# Roughly 0.7 GB per hour. Fetch with scripts/pull_data.sh on the Mac.
set -uo pipefail
DIR=~/captures/continuous; RUN="$DIR/.run"
HERE="$(cd "$(dirname "$0")" && pwd)"
/usr/sbin/getcap /usr/bin/tcpdump 2>/dev/null | grep -q cap_net_raw && SUDO="" || SUDO=sudo

alive() { [ -f "$RUN/$1.pid" ] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }

case "${1:-status}" in
start)
    mkdir -p "$RUN"
    if alive tcpdump; then echo "tcpdump already running"; else
        $SUDO tcpdump -i wlan0 -G 300 -w "$DIR/csi_%Y%m%d_%H%M%S.pcap" >"$RUN/tcpdump.log" 2>&1 &
        echo $! >"$RUN/tcpdump.pid"; echo "CSI ring started"
    fi
    if alive audio; then echo "audio archive already running"; else
        python3 "$HERE/audio_archive.py" "$DIR" >"$RUN/audio.log" 2>&1 &
        echo $! >"$RUN/audio.pid"; echo "audio archive started"
    fi
    echo "ultrasonic: logged by the UI (restart the UI once if it was started before this feature)" ;;
stop)
    for n in tcpdump audio; do
        alive $n && kill "$(cat "$RUN/$n.pid")" && echo "stopped $n"
        rm -f "$RUN/$n.pid"
    done ;;
status)
    for n in tcpdump audio; do alive $n && echo "$n: running" || echo "$n: stopped"; done
    du -sh "$DIR" 2>/dev/null; df -h / | tail -1
    ls -t "$DIR" 2>/dev/null | head -4 ;;
*) echo "usage: $0 start|stop|status"; exit 1 ;;
esac
