"""Settings for the touchscreen UI: defaults, load and save.

Everything the team may want to tweak at the demo table lives here, so no
code changes are needed to retune timers or thresholds. `ui/settings.json`
is the live file (gitignored); `ui/settings.example.json` shows the keys.
The API key is also read from the GEMINI_API_KEY environment variable.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = UI_DIR / "settings.json"
EXAMPLE_PATH = UI_DIR / "settings.example.json"
ROOM_PATH = UI_DIR / "room.json"
VOICE_CACHE = UI_DIR / "voice_cache"
CALIBRATION_PATH = UI_DIR / "calibration.json"

DEFAULTS: dict = {
    # Escalation timing (seconds)
    "checkin_seconds": 30,        # "Are you OK?" countdown
    "still_seconds": 45,          # present + no CSI motion for this long -> check-in
    "alert_grace_seconds": 10,    # red screen's last-chance cancel window
    "voice_repeat_seconds": 10,   # re-ask during check-in
    "rearm_seconds": 120,         # after I'M OK: stillness timer held off

    # Voice
    "voice_muted": False,
    "gemini_voice": "Kore",
    "gemini_model": "gemini-3.8-flash-lite-tts",
    "gemini_api_key": "",          # or env GEMINI_API_KEY
    "gemini_stagger_s": 15,        # pause between phrases (free-tier rate limits)

    # Spoken replies to the check-in (needs `pip install vosk` and a model)
    "speech_enabled": True,
    "speech_model": "",            # default: ui/speech_model/

    # CSI motion scoring (see docs/LOG.md phase 0)
    "motion_window_s": 2.0,        # window length
    "motion_factor": 1.5,          # threshold = calibrated baseline * factor
    "csi_mac": "2c:cf:67:c5:c4:14",
    "csi_mode": "live",            # live | replay | sim (replay/sim only via --csi)
    "replay_path": "data/session1/walking_20261016_235147.pcap",
    "replay_speed": 1.0,
    "replay_loop": True,
    "tcpdump_cmd": ["tcpdump", "-i", "wlan0", "-w", "-", "-U"],
    "live_dead_s": 5.0,            # live CSI this quiet -> activity note (never sim)

    # Ultrasonic (BCM pins; proven in scripts/pi/us_log.py)
    "ultra_mode": "gpio",          # gpio | sim (sim only via --us)
    "sensors": [
        {"label": "us1", "echo": 17, "trig": 4},
        {"label": "us2", "echo": 27, "trig": 22},
    ],
    "max_distance_m": 4.0,         # HC-SR04 range used as "no echo"
    "presence_dev_cm": 30.0,       # deviation from baseline = something there
    "presence_enter_s": 1.5,
    "presence_exit_s": 5.0,

    # Simulated room / demo
    "sim_room": [4.0, 3.0],        # used when room.json is missing: w x h meters

    # Trusted contact (displayed in the simulated call)
    "contact_name": "Jordan",
    "contact_phone": "+1 555 0100",
}


def load(path: Path = SETTINGS_PATH) -> dict:
    data = copy.deepcopy(DEFAULTS)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                for key, value in stored.items():
                    if key in data:
                        data[key] = value
        except (OSError, json.JSONDecodeError) as exc:
            print(f"settings: could not read {path} ({exc}); using defaults")
    return data


def save(settings: dict, path: Path = SETTINGS_PATH) -> None:
    payload = {k: v for k, v in settings.items() if k in DEFAULTS}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def write_example() -> None:
    """Write ui/settings.example.json (committed) from the defaults."""
    payload = {k: ("" if k == "gemini_api_key" else v)
               for k, v in copy.deepcopy(DEFAULTS).items()}
    with open(EXAMPLE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def gemini_api_key(settings: dict) -> str:
    return settings.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")


# -- room map (traced once on the touchscreen; ui/room.json) ------------------

def default_room(settings: dict) -> dict:
    """Fallback rectangle so the UI is usable before the trace wizard runs.
    Coordinates are meters, origin top-left, y down."""
    w, h = settings.get("sim_room", [4.0, 3.0])
    outline = [[0, 0], [w, 0], [w, h], [0, h]]
    return {
        "outline": outline,
        "door": [w / 2, 0],
        "sensors": [
            {"label": "us1", "x": 0.15, "y": h / 2, "heading_deg": 0},
            {"label": "us2", "x": w - 0.15, "y": h / 2, "heading_deg": 180},
        ],
        "pico": [w - 0.5, 0.15],
        "pi": [0.5, h - 0.15],
    }


def load_room(settings: dict, path: Path = ROOM_PATH) -> dict:
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and len(data.get("outline", [])) >= 3:
                return data
        except (OSError, json.JSONDecodeError) as exc:
            print(f"room: could not read {path} ({exc}); using default")
    return default_room(settings)


def save_room(room: dict, path: Path = ROOM_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(room, f, indent=2)


def load_calibration(path: Path = CALIBRATION_PATH) -> dict:
    """The last empty-room calibration, so a UI restart does not lose it."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_calibration(data: dict, path: Path = CALIBRATION_PATH) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
