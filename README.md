# Sound: acoustic fall and distress detection

Listens to a microphone and logs sounds that might mean someone has fallen or needs help. It is one input among several sensors, so it flags possible events and leaves the final decision to whatever combines them.

> **Research prototype, not a validated safety device.**

The thing that combines them is the **touchscreen UI** (`ui/`, see `docs/UI.md`): the 7" Pi display shows the room as the sensors see it - Wi-Fi CSI motion, ultrasonic depth, noise - and runs the escalation flow (still-person check-in -> alert -> simulated help call) that the KAZE challenge asks for. `scripts/pi/run_ui.sh` starts the whole stack on the Pi.

## How it works

```
Mic ──► rolling_audio.py ──► rolling_audio.wav ──► fall_detector.py ──► fall_events.csv

Laptop mic ──► audio_sender.py ══ network ══► audio_receiver.py ──► rolling_audio.wav
```

The recorder keeps the latest 5 minutes of audio in a WAV file. The detector reads it and logs two kinds of events:

- **`REVIEW_CANDIDATE`**: any loud, short sound (thump, bang, clap, slam).
- **`DISTRESS_SOUND`**: sustained screaming, shouting, groaning or crying, found with YAMNet.

## Files

| File | Purpose |
|---|---|
| `rolling_audio.py` | Records the mic into the rolling WAV |
| `fall_detector.py` | Watches the WAV and logs events |
| `audio_sender.py` / `audio_receiver.py` / `audio_link.py` | Stream audio from a laptop to the Pi |
| `requirements.txt` | Python packages |

## Quick start

```
pip install -r requirements.txt
```

**On one machine:**
```
python rolling_audio.py        # terminal 1 (--list-devices to pick a mic)
python fall_detector.py        # terminal 2
```

**Laptop mic, Pi detector:**
```
# Pi
python audio_receiver.py --token secret
python fall_detector.py

# Laptop
python audio_sender.py --host <pi-ip> --token secret
```

**Raspberry Pi 4:** use 64-bit Raspberry Pi OS and a USB mic, then `sudo apt install libportaudio2 python3-venv` and install the requirements in a virtual environment. On Linux the WAV lives in `/dev/shm` (RAM) to protect the SD card. If TensorFlow won't install, run the detector with `--no-yamnet`.

## Output

`fall_events.csv` gets one row per event: approximate time, loudness figures, YAMNet's top labels and the event type. Times come from the system clock, so keep the Pi's clock synced.

## Good to know

- Any loud, short noise counts as a candidate, so other sensors need to confirm.
- Thresholds are untuned starting points. Test with real sounds and adjust the constants at the top of `fall_detector.py`.
- If claps are missing from the audio, Windows noise suppression is the usual cause. Try `rolling_audio.py --meter` and turn off audio enhancements.
- The laptop-to-Pi stream is not encrypted. The token only keeps strangers out.
