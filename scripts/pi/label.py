#!/usr/bin/env python3
"""Press-a-key labelling for training data. Run while the system is live (over SSH or on the Pi).

usage: label.py [labels.jsonl]        (default ~/captures/continuous/labels.jsonl)

STATES persist until you press another one (what the person in the room is doing now):
  e empty room   w walking   t standing   s sitting   l lying still
EVENTS mark a single moment (press right after it happens):
  f fall happened     n near-fall / stumble     x last system alert was a FALSE alarm
  m system MISSED something just now (note what in the prompt that follows)
  u undo last label   q quit

Every press is one JSON line with the Pi clock time, matching the pcap, ultrasonic and audio files.
For falls, press within a couple of seconds: training windows are cut from just before the press.
"""
import json
import os
import select
import sys
import termios
import time
import tty

STATES = {"e": "empty", "w": "walking", "t": "standing", "s": "sitting", "l": "lying"}
EVENTS = {"f": "fall", "n": "near_fall", "x": "false_alarm", "m": "missed"}
DEFAULT = os.path.expanduser("~/captures/continuous/labels.jsonl")


def append_label(path: str, kind: str, label: str, note: str = "", t: float | None = None) -> dict:
    row = {"t": round(t if t is not None else time.time(), 3), "kind": kind, "label": label}
    if note:
        row["note"] = note
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def undo_last(path: str) -> dict | None:
    """Drop the last line of the file; return it."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    if not lines:
        return None
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(line + "\n" for line in lines[:-1]))
    return json.loads(lines[-1])


def read_note() -> str:
    """Line input while the terminal is in cbreak mode."""
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    try:
        return input("  note> ").strip()
    finally:
        tty.setcbreak(fd)


def main(path: str) -> None:
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    state, recent = "(none)", []
    print(__doc__)
    try:
        tty.setcbreak(fd)
        while True:
            print(f"\r\033[Kstate: {state:9s} last: {', '.join(recent[-4:]) or '-'}   ", end="", flush=True)
            if not select.select([sys.stdin], [], [], 1.0)[0]:
                continue
            ch = sys.stdin.read(1).lower()
            if ch == "q":
                break
            if ch == "u":
                gone = undo_last(path)
                if gone:
                    recent.append(f"undid {gone['label']}")
                    state = next((r["label"] for r in reversed(_load(path)) if r["kind"] == "state"), "(none)")
                continue
            if ch in STATES:
                state = STATES[ch]
                append_label(path, "state", state)
                recent.append(state)
            elif ch in EVENTS:
                note = ""
                if ch == "m":
                    print()
                    note = read_note()
                append_label(path, "event", EVENTS[ch], note)
                recent.append(EVENTS[ch].upper())
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        print()


def _load(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT)
