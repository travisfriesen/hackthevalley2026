#!/usr/bin/env bash
# Start the audio path on its own, detached, so it survives UI restarts and recording sessions:
#   audio_receiver.py  (writes the rolling WAV in /dev/shm)  +  phone_mic.py (phone browser -> receiver)
# Logs: /tmp/audio_receiver.log, /tmp/phone_mic.log. Safe to run twice.
cd "$(dirname "$0")/../../sound" || exit 1
PY="${PY:-$HOME/venv-sound/bin/python}"
if ss -ltn | grep -q ':5005 '; then echo "audio receiver: already running"
else setsid nohup "$PY" audio_receiver.py > /tmp/audio_receiver.log 2>&1 < /dev/null & echo "audio receiver: started"; fi
if ss -ltn | grep -q ':8443 '; then echo "phone mic bridge: already running"
else
  IPS=$(hostname -I | tr ' ' '\n' | grep -E '^[0-9]+\.' | sed 's/^/--host /' | tr '\n' ' ')
  setsid nohup "$PY" phone_mic.py $IPS > /tmp/phone_mic.log 2>&1 < /dev/null & echo "phone mic bridge: started  (https://$(hostname -I | awk '{print $1}'):8443)"
fi
