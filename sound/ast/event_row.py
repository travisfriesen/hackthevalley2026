"""One row of fall_events.csv in the format ui/feeds.py reads (same columns as sound/fall_detector.py)."""
import csv
import io

FIELDS = ["unix_time_approx", "rise_db", "crest_factor", "peak_amplitude", "top_classes", "status"]


def event_row(t, peak, label, prob, status):
    """rise_db and crest_factor are left blank: they belong to the loud-sound detector."""
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerow(
        [f"{t:.3f}", "", "", f"{peak:.4f}", f"AST {label} {prob:.2f}", status])
    return out.getvalue()
