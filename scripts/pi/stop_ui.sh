#!/usr/bin/env bash
# Stop the UI and the YAMNet fall detector (frees the ultrasonic GPIO pins for recording).
# The audio receiver and phone_mic.py keep running so audio can still be recorded.
pkill -f "[u]i/main.py"; pkill -f "[f]all_detector.py"; pkill -f "[r]un_ui.sh"
sleep 1
echo "UI stopped"
