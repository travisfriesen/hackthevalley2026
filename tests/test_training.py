"""Guided training sequencing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ui"))

from training import PHRASES, TAKES, Training  # noqa: E402


def run(t, seconds, step=0.25, start=0.0):
    """Tick for `seconds`; returns [(time, action)]."""
    out, now = [], start
    while now <= start + seconds:
        out += [(round(now, 2), a) for a in t.tick(now)]
        now += step
    return out


def small(**kw):
    return Training(takes=TAKES[:2], position_s=6, record_s=10, rest_s=2, **kw)


def test_one_take_speaks_counts_records_and_says_done():
    t = small()
    t.start(0.0)
    acts = run(t, 25)
    kinds = [a for _, a in acts]
    # instruction, 5..1, recording, record_start, record_stop, done
    assert kinds[0] == ("speak", "train_empty")
    assert [a[1] for a in kinds[1:6]] == ["count_5", "count_4", "count_3", "count_2", "count_1"]
    assert kinds[6] == ("speak", "train_recording")
    assert kinds[7] == ("record_start", "empty", 10)
    assert kinds[8] == ("record_stop", "empty")
    assert kinds[9] == ("speak", "train_done")
    assert kinds[10] == ("speak", "train_walking")        # next take begins


def test_timing_of_the_first_take():
    t = small()
    t.start(0.0)
    times = {a: ts for ts, a in run(t, 25)}
    assert times[("speak", "train_empty")] == 0.0
    assert times[("speak", "count_5")] == 6.0              # after position_s
    assert times[("speak", "train_recording")] == 11.0     # after the 5 s count
    assert times[("record_stop", "empty")] == 21.0         # after record_s
    assert 23.0 <= times[("speak", "train_walking")] <= 23.25    # after rest_s (+ one tick)


def test_each_number_is_spoken_once_even_with_fast_ticks():
    t = small()
    t.start(0.0)
    counts = [a for _, a in run(t, 25, step=0.05) if a[0] == "speak" and a[1].startswith("count_")]
    assert [c[1] for c in counts[:5]] == ["count_5", "count_4", "count_3", "count_2", "count_1"]


def test_finishes_after_the_last_take():
    t = small()
    t.start(0.0)
    acts = [a for _, a in run(t, 80)]
    assert ("speak", "train_complete") in acts and ("finished",) in acts
    assert not t.active
    assert acts.count(("record_start", "walking", 10)) == 1


def test_skip_moves_on_and_stops_a_running_recording():
    t = small()
    t.start(0.0)
    run(t, 12)                                  # now recording "empty"
    assert t.snapshot(12.0)["phase"] == "record"
    assert t.skip(12.0) == [("record_stop", "empty")]
    assert t.snapshot(12.0)["label"] == "walking"
    assert t.snapshot(12.0)["phase"] == "instruct"


def test_stop_mid_recording_asks_to_stop_it():
    t = small()
    t.start(0.0)
    run(t, 12)
    assert t.stop(12.0) == [("record_stop", "empty")]
    assert not t.active and t.tick(13.0) == []


def test_every_spoken_key_has_text():
    t = Training()
    t.start(0.0)
    keys = {a[1] for _, a in run(t, 600) if a[0] == "speak"}
    assert keys - set(PHRASES) == {f"count_{n}" for n in range(1, 6)}   # counts live in voice.py
