"""UI smoke test: build the real app, run ticks, drive the full help flow.

Skips when no display is available (headless CI / SSH).
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src"), str(ROOT / "ui")):
    if p not in sys.path:
        sys.path.insert(0, p)

tk = pytest.importorskip("tkinter")
try:
    root = tk.Tk()
    root.withdraw()
    root.destroy()
except tk.TclError as exc:
    pytest.skip(f"no display: {exc}", allow_module_level=True)


def _settings():
    import settings_store
    settings = settings_store.load()      # defaults; no live file needed
    settings["csi_mode"] = "sim"
    settings["ultra_mode"] = "sim"
    settings["voice_muted"] = True
    # hermetic: never pick up the repo's stale rolling WAV / events CSV
    tmp = Path(tempfile.mkdtemp(prefix="kaze-smoke-"))
    settings["wav_path"] = str(tmp / "rolling_audio.wav")
    settings["events_path"] = str(tmp / "fall_events.csv")
    return settings


def test_full_help_flow():
    from app import App
    from escalation import State

    root = tk.Tk()
    root.geometry("800x480")
    app = App(root, _settings(), fullscreen=False)
    try:
        # warm-up: let the natural 10 Hz loop run ~3 s so the empty room's
        # ultrasonic baseline is learned (what "Calibrate empty room" does
        # on the real kit)
        for _ in range(35):
            root.update()
            time.sleep(0.1)
        assert app.escalation.machine.state is State.AWAY

        # scripted incident: person walks in and collapses mid-room,
        # right across the sensor beams -> presence latches
        app.demo_fall()
        for _ in range(130):
            root.update()
            time.sleep(0.03)
        assert app.d["presence"] is True
        snap = app.processor.snapshot(app.now)
        if snap["alive"]:
            assert snap["motion_score"] is not None

        # manual help: dashboard -> ALERT -> cancel -> back to present
        app.manual_help()
        assert app.escalation.machine.state is State.ALERT
        app.alert_cancel()
        assert app.escalation.machine.state in (State.ACTIVE, State.STILL)

        # sound candidate -> CHECK_IN -> I'M OK
        app.escalation.sound_candidate(app.now, "test bang")
        assert app.escalation.machine.state is State.CHECK_IN
        app.checkin_ok()
        assert app.escalation.machine.state in (State.ACTIVE, State.STILL)

        # full escalation: check-in countdown -> alert grace -> help sent
        app.escalation.sound_candidate(app.now, "test thud")
        assert app.escalation.machine.state is State.CHECK_IN
        app.escalation.tick(app.now + 45)          # countdown expires
        assert app.escalation.machine.state is State.ALERT
        app.escalation.tick(app.now + 45 + 11)     # grace expires
        assert app.escalation.machine.state is State.HELP_SENT
        app.helpsent_clear()
        assert app.escalation.machine.state in (
            State.ACTIVE, State.STILL, State.AWAY)

        # settings screen round-trip
        app.show_settings()
        root.update()
    finally:
        app.close()
        root.destroy()


def test_speaker_fallback_picks_a_command():
    from voice import Speaker
    cache = ROOT / "ui" / "voice_cache_test"
    cache.mkdir(exist_ok=True)
    s = Speaker(cache, muted=False)
    cmd = s._fallback_cmd("hello")
    if shutil.which("espeak-ng") or sys.platform == "darwin":
        assert cmd is not None
    s.close()
