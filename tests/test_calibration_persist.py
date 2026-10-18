"""Calibration survives a UI restart."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT / "ui")):
    if p not in sys.path:
        sys.path.insert(0, p)

import settings_store  # noqa: E402
from csi_stream import CsiProcessor  # noqa: E402
from room_math import PresenceEstimator  # noqa: E402


def test_motion_baseline_roundtrip_recomputes_threshold():
    a = CsiProcessor(window_s=2.0, factor=1.5)
    a.baseline = 0.04
    saved = json.loads(json.dumps(a.export_calibration()))
    b = CsiProcessor(window_s=2.0, factor=2.0)             # factor changed meanwhile
    b.import_calibration(saved)
    assert b.baseline == 0.04 and b.threshold == pytest.approx(0.08)


def test_nothing_to_export_before_calibration():
    assert CsiProcessor(window_s=2.0, factor=1.5).export_calibration() is None


def test_ultrasonic_freeze_roundtrip():
    a = PresenceEstimator()
    a._frozen = [2.1, None]
    b = PresenceEstimator()
    b.import_frozen(json.loads(json.dumps(a.export_frozen())))
    assert b.baseline(0) == 2.1
    b.unfreeze()
    assert b.export_frozen() is None


def test_store_roundtrip_and_bad_files(tmp_path):
    path = tmp_path / "calibration.json"
    assert settings_store.load_calibration(path) == {}      # missing
    settings_store.save_calibration({"motion": {"baseline": 0.1}}, path)
    assert settings_store.load_calibration(path)["motion"]["baseline"] == 0.1
    path.write_text("{not json")
    assert settings_store.load_calibration(path) == {}      # corrupt -> start clean
