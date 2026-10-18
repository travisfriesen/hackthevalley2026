# DownLink

Fall and distress detection for someone living alone, built for Hack the Valley 2026 (the KAZE challenge). A Raspberry Pi with a 7" touchscreen watches a room with three kinds of sensor, and when it thinks someone may have fallen it asks if they are okay, then escalates to a (simulated) call to a trusted contact.

> **Research prototype, not a validated safety device.** The help call is simulated. Real human falls are untested: the fall classifiers were trained on dummy-fall data and ordinary household sounds.

![DownLink](ui/assets/logo_full.png)

## How it works

| Sensor | What it tells us | Where it runs |
|---|---|---|
| Wi-Fi CSI (Pico 2 W transmits, Pi 4B with nexmon receives) | someone is moving, not whether they are lying still | Pi |
| 2x HC-SR04 ultrasonic | someone entered or left the room, rough depth | Pi (GPIO) |
| Microphone (phone in a browser, or USB) | loud impacts, screams, crying, glass, sirens | audio on the Pi, AST model on a GPU box |

Presence from the ultrasonic sensors gates the stillness check: a person who is in the room and has not moved for a set time gets a check-in. A sound that looks like a fall or distress starts a check-in on its own. The full state machine (Away, Active, Still, CheckIn, Alert, HelpSent) is in [`docs/UI.md`](docs/UI.md).

```
phone / USB mic ─► audio_receiver.py ─► rolling WAV (/dev/shm) ──┐
                                                                  ├─ ssh (restricted gate) ─► remote_detector.py (GPU)
Pi: fall_detector.py (YAMNet, loud-sound heuristic)               │        fine-tuned AST + 3 s fall classifier
                                                                  ▼                       │
                                             fall_events.csv ◄── events ◄────────────────┘
                                                   │
Pico ─ CSI ─► tcpdump ─► csi_stream.py ──► ui/main.py (DownLink screen, escalation, voice)
HC-SR04 ─ GPIO ───────────────────────────────┘
```

The Pi does not run the AST model. `sound/ast/remote_detector.py` reads the last 14 s of audio from the Pi through `scripts/pi/cloud_gate.sh` (an ssh forced command that allows only reading audio and appending events), classifies it on a GPU, and writes events back to `fall_events.csv`, which the UI watches.

## Sound detection

- **Distress** (cry and scream merged), **glass** and **siren**: AST (`MIT/ast-finetuned-audioset`), either a frozen backbone with a logistic head or the fully fine-tuned model (`--model`).
- **Falls**: an impulse detector finds loud spikes, then a small 3 s STFT classifier (`fall3s.py`, trained on the SAFE dummy-fall set) scores each one. A fall alert needs P >= 0.9, and with `--model` it is dropped when the fine-tuned AST calls the window `not_distress` (> 0.5).
- Every alert's audio is saved to `sound/ast/triggers/` so false alarms can be relabelled and trained on.
- Measured on held-out test clips (not real falls): fine-tuned AST macro-F1 0.95, fall F1 0.91. The frozen head scored 0.87 and 0.75. Treat fall alerts as weak until the model is retrained on real recorded falls from your own mic.
- Training scripts: `sound/ast/build_dataset.py`, `package_audio.py`, `finetune.py`, `stress_test.py`. Datasets and models are large and gitignored.

## Running it

**On the Pi (kiosk, real sensors only):**
```
sudo apt install python3-tk espeak-ng libportaudio2
scripts/pi/enable_tcpdump.sh                     # once: tcpdump without sudo
scripts/pi/csi_on.sh                             # CSI mode, channel 11, Pico MAC in the script
scripts/pi/start_audio.sh                        # audio receiver + phone mic bridge (https://<pi>:8443)
scripts/pi/run_ui.sh --csi live --us gpio        # fall_detector.py + the fullscreen UI
```
The Pico follows whatever channel its hotspot uses. If that is not 11, run `install_nexmon_csi.sh activate` and `csi_start.sh <channel>/20 <pico-mac>` instead of `csi_on.sh`. The UI never falls back to simulated data on its own.

**GPU box (AST detector):**
```
cd sound/ast
python remote_detector.py --pi pi --forced --model models/ast_ft_lr1e-5
```

**Development on a laptop, everything simulated:**
```
python3 ui/main.py --csi sim --us sim
```
Simulation and replay (`--csi replay --replay <pcap>`) only happen when you ask for them on the command line.

**Tests:** `python3 -m pytest tests`

## Layout

| Path | Contents |
|---|---|
| `ui/` | touchscreen UI, escalation state machine, voice, shared light theme (`theme.py`), logo in `assets/` |
| `src/` | CSI packet reading and motion scoring |
| `sound/` | audio capture, phone mic bridge, YAMNet loud-sound detector, `ast/` classifiers and training |
| `scripts/pi/` | Pi setup, CSI capture, launchers, the restricted ssh gate |
| `scripts/pico/` | Pico firmware that sends UDP so the Pi can measure CSI |
| `data/` | recorded CSI captures (phase0 empty vs walking, session1 falls) |
| `docs/` | `UI.md` (design), `LOG.md` (hardware notes and results) |

## Known limits

- CSI detects motion well but cannot tell a still person from an empty room, which is why ultrasonic presence gates stillness.
- Fall sound detection has only been checked against dummy falls and test splits. False alarms from speech, doors and dropped objects are being tuned.
- The laptop/phone-to-Pi audio stream is not encrypted.
