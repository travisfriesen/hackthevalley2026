#!/bin/bash
# Restricted entry point for the cloud detector (and any remote reader). Allows exactly two operations:
#   audio_tail <prev_mtime>   prints "<mtime> <nbytes>" then the new raw int16 audio since prev_mtime
#                             (prev 0 = the last 14 s); only the new audio is sent, ~32 KB per second
#   append_event              appends stdin to fall_events.csv, which the UI watches
# Used two ways: as an ssh forced command (command text in $SSH_ORIGINAL_COMMAND), or called directly.
WAV=${KAZE_WAV:-/dev/shm/rolling_audio.wav}
EVENTS=${KAZE_EVENTS:-/dev/shm/fall_events.csv}
SR=16000
MAXB=448000   # 14 s of int16
cmd="${SSH_ORIGINAL_COMMAND:-$*}"
if [[ "$cmd" =~ ^audio_tail\ ([0-9]+(\.[0-9]+)?)$ ]]; then
  prev="${BASH_REMATCH[1]}"
  mtime=$(stat -c %.9Y "$WAV") || exit 1
  bytes=$(awk -v m="$mtime" -v p="$prev" -v sr=$SR -v mx=$MAXB 'BEGIN{ if (p+0==0) b=mx; else { b=int((m-p)*sr+0.5)*2; if (b>mx) b=mx; if (b<0) b=0 }; print b }')
  echo "$mtime $bytes"
  [ "$bytes" -gt 0 ] && tail -c "$bytes" "$WAV"
elif [[ "$cmd" == "append_event" ]]; then
  exec cat >> "$EVENTS"
else
  echo "denied" >&2
  exit 1
fi
