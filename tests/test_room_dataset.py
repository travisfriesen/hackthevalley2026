"""Building fine-tuning clips from in-room recordings."""
import csv
import sys
from pathlib import Path

import numpy as np
import pytest

sf = pytest.importorskip("soundfile")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sound" / "ast"))

import build_room_dataset as brd  # noqa: E402

SR = brd.SR


def room(seconds, rng, thumps=()):
    """Quiet room noise with loud thumps (decaying bursts) at the given seconds."""
    x = (0.003 * rng.standard_normal(int(seconds * SR))).astype(np.float32)
    for t in thumps:
        i = int(t * SR)
        n = np.arange(int(0.2 * SR))
        x[i:i + len(n)] += (0.8 * np.exp(-n / 800) * rng.standard_normal(len(n))).astype(np.float32)
    return np.clip(x, -1, 1)


@pytest.fixture
def rng():
    return np.random.default_rng(0)


def test_a_fall_take_gives_a_fall_window_with_the_thump_inside(rng):
    x = room(30, rng, thumps=[15.0])
    clips = brd.clips_from_take(x, "fall", rng)
    assert [label for label, _ in clips] == ["fall"]
    window = clips[0][1]
    assert len(window) == brd.LEN and np.abs(window).max() > 0.3


def test_a_walking_take_is_negative_and_harmless_thumps_become_hard_negatives(rng):
    x = room(30, rng, thumps=[15.0])
    clips = brd.clips_from_take(x, "walking", rng)
    assert {label for label, _ in clips} == {"not_distress"}
    loud = [w for _, w in clips if np.abs(w).max() > 0.3]
    assert len(loud) >= brd.HARD_COPIES               # the thump, repeated at shifted positions


def test_triggers_follow_the_verdict(rng):
    x = room(14, rng, thumps=[7.0])
    assert [l for l, _ in brd.clips_from_trigger(x, "real", rng)] == ["fall"]
    false = brd.clips_from_trigger(x, "false", rng)
    assert {l for l, _ in false} == {"not_distress"} and len(false) == brd.HARD_COPIES


def test_archive_windows_near_a_staged_fall_are_left_out(rng):
    x = room(60, rng)
    kept_all = brd.clips_from_archive(x, 1000.0, [])
    kept = brd.clips_from_archive(x, 1000.0, [1030.0])
    assert len(kept) < len(kept_all) and all(l == "not_distress" for l, _ in kept)


def test_build_writes_manifest_keeps_existing_rows_and_splits_by_file(tmp_path, rng):
    takes = tmp_path / "takes"
    takes.mkdir()
    for name, thump in [("fall_1", 15.0), ("empty_2", None), ("walking_3", None), ("sitting_4", None)]:
        sf.write(takes / f"{name}.wav", room(30, rng, [thump] if thump else []), SR, subtype="PCM_16")
    out = tmp_path / "packed"
    out.mkdir()
    (out / "manifest.csv").write_text("path,label,source,split\nkaggle_0.wav,not_distress,kaggle,train\n")

    class A:
        pass
    a = A()
    a.takes, a.triggers, a.reviewed, a.archive, a.falls = str(takes), None, None, None, None
    found = brd.collect(a, rng)
    brd.write(out, found)
    brd.write(out, found)                              # rerun is idempotent

    rows = list(csv.DictReader(open(out / "manifest.csv")))
    assert sum(r["source"] == "kaggle" for r in rows) == 1                  # existing rows kept
    room_rows = [r for r in rows if r["source"] == "room"]
    assert len(room_rows) == len(found) and (out / room_rows[0]["path"]).exists()
    assert {r["label"] for r in room_rows} == {"fall", "not_distress"}
    # a source file lives in one split only
    by_source = {}
    for (src, _, _), r in zip(found, [r for r in rows if r["source"] == "room"]):
        by_source.setdefault(src, set()).add(r["split"])
    assert all(len(s) == 1 for s in by_source.values())
    assert {"train", "room_val"} <= {r["split"] for r in room_rows}
