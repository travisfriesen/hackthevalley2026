"""KAZE touchscreen UI entry point.

Simulated or replayed data is used only when asked for on the command line.

Development (this Mac, windowed, all sensors simulated):
    python3 ui/main.py --csi sim --us sim

Replay the recorded fall takes through the real pipeline:
    python3 ui/main.py --csi replay --replay data/session1/fall_20261017_000105.pcap

On the Pi, kiosk mode over the real radio and sensors:
    python3 ui/main.py --fullscreen --csi live --us gpio

The working directory is forced to the repo root so the rolling WAV and
fall_events.csv probes (which the sound stack writes cwd-relative) work no
matter where it is launched from.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src"), str(ROOT / "ui")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.chdir(ROOT)

import tkinter as tk

import settings_store
from app import App


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fullscreen", action="store_true",
                        help="kiosk fullscreen (Pi touchscreen)")
    parser.add_argument("--csi", choices=("live", "replay", "sim"),
                        help="CSI source (default: settings)")
    parser.add_argument("--us", choices=("gpio", "sim"),
                        help="ultrasonic source (default: settings)")
    parser.add_argument("--replay", metavar="PCAP",
                        help="replay pcap file or glob")
    parser.add_argument("--speed", type=float,
                        help="replay speed multiplier (default 1.0)")
    parser.add_argument("--no-voice", action="store_true",
                        help="start muted")
    parser.add_argument("--settings", metavar="JSON",
                        help="settings file (default ui/settings.json)")
    parser.add_argument("--geometry", default="800x480",
                        help="window size when not fullscreen")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    settings = settings_store.load(Path(args.settings)
                                   if args.settings else settings_store.SETTINGS_PATH)
    # simulated/replayed data only on request: a settings file cannot turn it on
    if settings.get("csi_mode") != "live":
        settings["csi_mode"] = "live"
    if settings.get("ultra_mode") != "gpio":
        settings["ultra_mode"] = "gpio"
    if args.csi:
        settings["csi_mode"] = args.csi
    if args.us:
        settings["ultra_mode"] = args.us
    if args.replay:
        settings["replay_path"] = args.replay
    if args.speed:
        settings["replay_speed"] = args.speed
    if args.no_voice:
        settings["voice_muted"] = True

    root = tk.Tk()
    root.title("KAZE Sentry")
    if args.fullscreen:
        root.config(cursor="none")
        root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")
        # the window manager (labwc via Xwayland) ignores the hint until the window is mapped
        root.update()
        root.attributes("-fullscreen", True)
        root.after(300, lambda: root.attributes("-fullscreen", True))
    else:
        root.geometry(args.geometry)

    app = App(root, settings, args.fullscreen)

    def quit_(_event=None):
        app.close()
        root.destroy()

    def unfullscreen(_event=None):
        if args.fullscreen:
            root.attributes("-fullscreen", False)
            root.config(cursor="")
        else:
            quit_()

    root.protocol("WM_DELETE_WINDOW", quit_)
    root.bind("<Escape>", unfullscreen)
    root.bind("<Control-q>", quit_)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        quit_()


if __name__ == "__main__":
    main()
