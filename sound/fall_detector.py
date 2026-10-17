"""Experimental acoustic anomaly / possible-fall detector.

Reads rolling_audio.wav from rolling_audio.py.
This is a research prototype, not a validated safety device.
"""

from __future__ import annotations

import csv
import logging
import math
import os
import time
import wave
from pathlib import Path

import numpy as np

AUDIO_PATH = Path("rolling_audio.wav")
EVENT_LOG = Path("fall_events.csv")

SAMPLE_RATE = 16_000          # rate YAMNet expects
POLL_SECONDS = 1.0
TAIL_SECONDS = 6.0
MIN_AUDIO_SECONDS = 1.0

FRAME_MS = 100
BACKGROUND_SECONDS = 2.0
MIN_RISE_DB = 15.0
MIN_CREST_FACTOR = 4.0
MIN_PEAK = 0.02
# Background RMS is never taken as lower than this (about -66 dBFS). It stops
# digital silence (e.g. the recorder's mic-outage filler) from producing a
# huge "rise" for any sound that follows.
BACKGROUND_FLOOR_RMS = 5e-4

# An event is only analysed once this much audio exists after it, so YAMNet
# sees the whole sound. Younger events are picked up on a later poll.
MIN_EVENT_AGE = 1.0
DEDUP_SECONDS = 1.0

# Warn if the recorder has not updated the WAV for this long.
STALE_SECONDS = 10.0

# YAMNet: 0.96 s windows, 0.48 s hop.
YAMNET_WINDOW = 0.96
YAMNET_HOP = 0.48

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("fall_detector")


def read_tail(path: Path, tail_seconds: float = TAIL_SECONDS):
    """Read only the newest `tail_seconds` of the WAV.

    Returns (audio float32 in [-1, 1], sample_rate, total_duration_seconds,
    published_at). The mtime comes from the same open handle as the audio,
    so the two can't belong to different published files.
    """
    with open(path, "rb") as f:
        published_at = os.fstat(f.fileno()).st_mtime

        with wave.open(f, "rb") as wav:
            if wav.getnchannels() != 1:
                raise ValueError("Expected mono WAV")
            if wav.getsampwidth() != 2:
                raise ValueError("Expected 16-bit PCM WAV")

            sr = wav.getframerate()
            nframes = wav.getnframes()
            keep = min(nframes, int(tail_seconds * sr))
            wav.setpos(nframes - keep)
            raw = wav.readframes(keep)

    # The recorder publishes little-endian int16 PCM.
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return audio, sr, nframes / sr, published_at


def to_model_rate(audio: np.ndarray, sr: int) -> np.ndarray:
    """Resample to 16 kHz (needed when the recorder used --exclusive)."""
    if sr == SAMPLE_RATE:
        return audio

    try:
        from scipy.signal import resample_poly
    except ImportError:
        raise SystemExit(
            f"WAV is {sr} Hz but YAMNet needs {SAMPLE_RATE} Hz. "
            "Install scipy (pip install scipy) so it can be resampled, or "
            "run the recorder without --exclusive."
        )

    g = math.gcd(SAMPLE_RATE, sr)
    out = resample_poly(audio, SAMPLE_RATE // g, sr // g)
    return np.clip(out, -1.0, 1.0).astype(np.float32)


def load_model():
    import tensorflow_hub as hub

    # Keep the downloaded model across reboots (the default is the temp dir).
    os.environ.setdefault(
        "TFHUB_CACHE_DIR", str(Path.home() / ".cache" / "tfhub")
    )

    log.info("Loading YAMNet...")
    model = hub.load("https://tfhub.dev/google/yamnet/1")
    class_map = model.class_map_path().numpy().decode("utf-8")

    with open(class_map, newline="", encoding="utf-8") as f:
        labels = [
            row["display_name"]
            for row in csv.DictReader(f)
        ]

    return model, labels


def run_yamnet(model, waveform: np.ndarray) -> np.ndarray:
    import tensorflow as tf

    # YAMNet needs float32 mono audio in [-1, 1].
    scores, _embeddings, _spectrogram = model(
        tf.convert_to_tensor(waveform, dtype=tf.float32)
    )
    return scores.numpy()


def find_transients(audio: np.ndarray) -> list[dict]:
    """Find sharp, loud changes relative to local RMS background."""
    frame_size = SAMPLE_RATE * FRAME_MS // 1000
    count = len(audio) // frame_size
    if count < 3:
        return []

    frames = audio[:count * frame_size].reshape(-1, frame_size)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    peak = np.max(np.abs(frames), axis=1)
    crest = peak / (rms + 1e-9)

    radius = max(1, round(BACKGROUND_SECONDS * 1000 / FRAME_MS))
    events = []

    for i in range(count):
        left = max(0, i - radius)
        right = min(count, i + radius + 1)

        neighbors = np.concatenate((rms[left:i], rms[i + 1:right]))
        if len(neighbors) == 0:
            continue

        background = max(float(np.median(neighbors)), BACKGROUND_FLOOR_RMS)
        rise_db = 20 * np.log10((rms[i] + 1e-8) / background)

        if (
            rise_db >= MIN_RISE_DB
            and crest[i] >= MIN_CREST_FACTOR
            and peak[i] >= MIN_PEAK
        ):
            events.append({
                "offset": i * FRAME_MS / 1000.0,
                "rise_db": float(rise_db),
                "crest": float(crest[i]),
                "peak": float(peak[i]),
            })

    # Merge adjacent transient frames into one event.
    merged = []
    for event in events:
        if (
            merged
            and event["offset"] - merged[-1]["offset"] < 0.8
        ):
            if event["peak"] > merged[-1]["peak"]:
                merged[-1] = event
        else:
            merged.append(event)

    return merged


def top_classes(scores: np.ndarray, labels: list[str], center: float) -> str:
    """Top-5 classes over every YAMNet window that contains time `center`."""
    last = len(scores) - 1
    hi = min(last, int(center // YAMNET_HOP))
    lo = max(0, math.ceil((center - YAMNET_WINDOW) / YAMNET_HOP))
    lo = min(lo, hi)

    frame_scores = scores[lo:hi + 1].max(axis=0)
    top_idx = np.argsort(frame_scores)[::-1][:5]
    return "; ".join(
        f"{labels[i]}:{float(frame_scores[i]):.3f}" for i in top_idx
    )


def append_event(row: dict) -> None:
    exists = EVENT_LOG.exists()
    fields = [
        "unix_time_approx",
        "rise_db",
        "crest_factor",
        "peak_amplitude",
        "top_classes",
        "status",
    ]

    with EVENT_LOG.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def poll_once(classify, labels, last_reported: float, state: dict) -> float:
    """Analyse the newest audio once. Returns the updated last_reported."""
    if not AUDIO_PATH.exists():
        return last_reported

    raw, sr, duration, published_at = read_tail(AUDIO_PATH)
    if duration < MIN_AUDIO_SECONDS:
        return last_reported

    # Detect a dead recorder: the file stops being updated.
    since_update = time.time() - published_at
    if since_update > STALE_SECONDS:
        if not state.get("stale"):
            log.warning(
                "%s has not been updated for %.0f s - is the recorder "
                "running?", AUDIO_PATH, since_update,
            )
            state["stale"] = True
    elif state.get("stale"):
        log.info("Recorder is updating %s again", AUDIO_PATH)
        state["stale"] = False

    tail = to_model_rate(raw, sr)
    tail_duration = len(tail) / SAMPLE_RATE

    scores = None  # YAMNet only runs once there is a new event to label

    for event in find_transients(tail):
        # Time of the middle of the transient frame, within the tail.
        center = event["offset"] + FRAME_MS / 2000.0
        age = tail_duration - center

        # Too recent: wait until YAMNet can see the whole sound.
        if age < MIN_EVENT_AGE:
            continue

        # Approximate wall-clock time, anchored on the file's mtime.
        event_time = published_at - age

        # Already reported on an earlier poll.
        if event_time <= last_reported + DEDUP_SECONDS:
            continue

        if scores is None:
            scores = classify(tail)
        summary = top_classes(scores, labels, center)

        # Write the CSV first: if it fails (e.g. open in Excel) we leave
        # last_reported alone, so the event is retried on the next poll.
        try:
            append_event({
                "unix_time_approx": round(event_time, 3),
                "rise_db": round(event["rise_db"], 2),
                "crest_factor": round(event["crest"], 2),
                "peak_amplitude": round(event["peak"], 4),
                "top_classes": summary,
                "status": "REVIEW_CANDIDATE",
            })
        except OSError as exc:
            log.warning(
                "Could not write %s (%s); will retry next poll",
                EVENT_LOG, exc,
            )
            break

        log.warning(
            "ACOUSTIC CANDIDATE at %.2f | "
            "rise=%.1fdB crest=%.1f peak=%.3f | %s",
            event_time,
            event["rise_db"],
            event["crest"],
            event["peak"],
            summary,
        )
        last_reported = event_time

    return last_reported


def main():
    model, labels = load_model()

    def classify(waveform: np.ndarray) -> np.ndarray:
        return run_yamnet(model, waveform)

    last_reported = float("-inf")
    state: dict = {}

    log.info("Watching %s", AUDIO_PATH.resolve())

    while True:
        try:
            last_reported = poll_once(classify, labels, last_reported, state)
        except (OSError, EOFError, ValueError, wave.Error) as exc:
            log.warning("Snapshot not ready or invalid: %s", exc)
        except Exception:
            log.exception("Analysis iteration failed")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()