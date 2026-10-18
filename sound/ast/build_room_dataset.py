"""Turn in-room recordings into 10 s training clips for finetune.py.

    python build_room_dataset.py --out /root/data/packed \\
        --takes ~/captures                     # <label>_<stamp>.wav from guided training / csi_record.sh
        --triggers triggers --reviewed reviewed.csv   # saved alert clips + review_triggers.py verdicts
        --archive archive --falls falls.jsonl         # audio_archive.py slices; every time NOT in falls.jsonl is normal

Labels written (the trainer's classes): "fall" for the 10 s around a real fall thump,
"not_distress" for everything else, with extra copies of the loud-but-harmless thumps
(the false alarms) as hard negatives. Rows are added to OUT/manifest.csv with
source "room"; re-running replaces them. About a quarter of the SOURCE FILES go to the
"room_val" split (never a window of a training file), so val numbers are honest.

Sources
  takes     labels: fall -> windows around each thump; empty/walking/standing/sitting/lying
            -> not_distress (the thumps in them are hard negatives).
  triggers  clips the detector saved when it raised an alert; reviewed.csv (file,verdict)
            says real|false. Unreviewed clips are ignored.
  archive   continuous audio; falls.jsonl lists staged falls ({"t": epoch}); a window within
            5 s of one is skipped, everything else is a normal-life negative.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np
import soundfile as sf

from spike import find_spikes

SR = 16000
LEN = 10 * SR
NEGATIVE_LABELS = {"empty", "walking", "standing", "sitting", "lying", "negative"}
HARD_COPIES = 3          # extra shifted copies of each harmless thump


def read16k(path) -> np.ndarray:
    x, sr = sf.read(path, dtype="float32")
    x = x if x.ndim == 1 else x.mean(1)
    if sr != SR:
        import torch
        import torchaudio.functional as AF
        x = AF.resample(torch.from_numpy(x), sr, SR).numpy()
    return x


def window_at(x: np.ndarray, centre: int, offset_s: float) -> np.ndarray:
    """10 s window with `centre` placed offset_s from its start (zero padded)."""
    start = int(centre - offset_s * SR)
    out = np.zeros(LEN, np.float32)
    lo, hi = max(start, 0), min(start + LEN, len(x))
    if hi > lo:
        out[lo - start:hi - start] = x[lo:hi]
    return out


def sliding_windows(x: np.ndarray, hop_s: float = 5.0):
    """(start_sample, window) over a recording; a short tail is zero padded."""
    hop = int(hop_s * SR)
    for start in range(0, max(1, len(x) - LEN // 2), hop):
        w = np.zeros(LEN, np.float32)
        piece = x[start:start + LEN]
        w[:len(piece)] = piece
        yield start, w


def thumps(x: np.ndarray) -> list:
    """Sample positions of loud impulses, the same finder the live detector uses."""
    return [int(c) for c in find_spikes(x)]


def clips_from_take(x: np.ndarray, label: str, rng) -> list:
    """[(label, window)] from one labelled take."""
    out = []
    if label == "fall":
        for c in thumps(x):
            out.append(("fall", window_at(x, c, rng.uniform(3, 7))))
        return out
    for _, w in sliding_windows(x):
        out.append(("not_distress", w))
    for c in thumps(x):                                  # harmless thumps: hard negatives
        for _ in range(HARD_COPIES):
            out.append(("not_distress", window_at(x, c, rng.uniform(2, 8))))
    return out


def clips_from_trigger(x: np.ndarray, verdict: str, rng) -> list:
    """The detector's own alert: 'real' -> fall, 'false' -> a hard negative."""
    peaks = thumps(x) or [int(np.argmax(np.abs(x)))]
    label = "fall" if verdict == "real" else "not_distress"
    copies = 1 if label == "fall" else HARD_COPIES
    return [(label, window_at(x, p, rng.uniform(3, 7))) for p in peaks for _ in range(copies)]


def clips_from_archive(x: np.ndarray, start_epoch: float, falls: list) -> list:
    """Normal life: every window not near a staged fall is a negative."""
    out = []
    for start, w in sliding_windows(x, hop_s=10.0):
        t0, t1 = start_epoch + start / SR, start_epoch + (start + LEN) / SR
        if any(t0 - 5 <= f <= t1 + 5 for f in falls):
            continue
        out.append(("not_distress", w))
    return out


def label_from_name(path: Path) -> str | None:
    m = re.match(r"([a-z]+)_", path.name)
    return m.group(1) if m else None


def split_of(index: int) -> str:
    return "room_val" if index % 4 == 3 else "train"


def collect(args, rng) -> list:
    """[(source_file, label, window)] from every input."""
    found = []

    def add(src, clips):
        found.extend((src, label, w) for label, w in clips)

    for p in sorted(Path(args.takes).glob("*.wav")) if args.takes else []:
        label = label_from_name(p)
        if label not in NEGATIVE_LABELS | {"fall"}:
            continue
        x = read16k(p)
        if len(x) < 5 * SR:
            continue
        clips = clips_from_take(x, label, rng)
        if label == "fall" and not clips:
            print(f"  {p.name}: no thump found, skipped")
        add(p.name, clips)

    if args.triggers and args.reviewed:
        with open(args.reviewed, newline="") as f:
            verdicts = {r["file"]: r["verdict"] for r in csv.DictReader(f)}
        for p in sorted(Path(args.triggers).glob("*.wav")):
            if verdicts.get(p.name) in ("real", "false"):
                add(p.name, clips_from_trigger(read16k(p), verdicts[p.name], rng))

    if args.archive:
        falls = []
        if args.falls and Path(args.falls).exists():
            falls = [json.loads(line)["t"] for line in open(args.falls) if line.strip()]
        for p in sorted(Path(args.archive).glob("audio_*.wav")):
            epoch = float(p.stem.split("_")[1])
            add(p.name, clips_from_archive(read16k(p), epoch, falls))
    return found


def write(out: Path, found: list) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / "manifest.csv"
    rows = []
    if manifest.exists():
        with open(manifest, newline="") as f:
            rows = [r for r in csv.DictReader(f) if r["source"] != "room"]
    for old in out.glob("room_*.wav"):
        old.unlink()
    sources = sorted({s for s, _, _ in found})
    split_for = {s: split_of(i) for i, s in enumerate(sources)}
    counts = {}
    for n, (src, label, w) in enumerate(found):
        name = f"room_{n:05d}.wav"
        sf.write(out / name, w, SR, subtype="PCM_16")
        split = split_for[src]
        rows.append({"path": name, "label": label, "source": "room", "split": split})
        counts[(split, label)] = counts.get((split, label), 0) + 1
    with open(manifest, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["path", "label", "source", "split"])
        w.writeheader()
        w.writerows(rows)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="packed dataset folder (has manifest.csv from package_audio.py)")
    ap.add_argument("--takes")
    ap.add_argument("--triggers")
    ap.add_argument("--reviewed")
    ap.add_argument("--archive")
    ap.add_argument("--falls")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    found = collect(args, np.random.default_rng(args.seed))
    if not found:
        raise SystemExit("no usable in-room audio found")
    for (split, label), n in sorted(write(Path(args.out), found).items()):
        print(f"{split:9} {label:13} {n}")


if __name__ == "__main__":
    main()
