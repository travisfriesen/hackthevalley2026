"""The AST detector's rows must be accepted by the UI's sound-event reader as new events."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sound", "ast"))

from event_row import FIELDS, event_row  # noqa: E402
from feeds import SoundEventFeed  # noqa: E402


def make_feed(path):
    return SoundEventFeed({"events_path": str(path)}, interval=3600)


def test_row_matches_ui_columns():
    assert FIELDS == SoundEventFeed.FIELDS
    row = make_feed("/nonexistent")._parse(event_row(1800000000.5, 0.42, "distress", 0.93, "DISTRESS_SOUND"))
    assert row["status"] == "DISTRESS_SOUND"
    assert row["top_classes"] == "AST distress 0.93"
    assert row["unix_time_approx"] == 1800000000.5


def test_ui_treats_appended_ast_rows_as_new_events(tmp_path):
    csv_path = tmp_path / "fall_events.csv"
    csv_path.write_text(",".join(FIELDS) + "\n")
    feed = make_feed(csv_path)
    feed._poll_once()                       # first sight of the file: nothing counts as new
    with open(csv_path, "a") as f:
        f.write(event_row(1800000001.0, 0.5, "glass", 0.91, "DISTRESS_SOUND"))
        f.write(event_row(1800000002.0, 0.9, "fall", 0.97, "REVIEW_CANDIDATE"))
    feed._poll_once()
    labels = [e["top_classes"] for e in feed.drain()]
    assert labels == ["AST glass 0.91", "AST fall 0.97"]
