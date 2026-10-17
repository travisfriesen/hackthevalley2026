"""Escalation state machine: the transitions the UI depends on."""
import pytest
from escalation import Config, Escalation, State


@pytest.fixture
def esc():
    e = Escalation(Config(still_seconds=45, checkin_seconds=30,
                          alert_grace_seconds=10, rearm_seconds=120),
                   on_transition=None)
    e.transitions = []
    e.on_transition = lambda now, frm, to, reason: e.transitions.append(
        (to.value, reason))
    return e


def test_enter_and_leave(esc):
    esc.set_presence(0.0, True)
    assert esc.machine.state is State.STILL          # entered, no motion yet
    esc.set_motion(1.0, True)
    assert esc.machine.state is State.ACTIVE
    esc.set_motion(2.0, False)
    assert esc.machine.state is State.STILL
    esc.set_presence(3.0, False)
    assert esc.machine.state is State.AWAY


def test_stillness_escalates_to_check_in(esc):
    esc.set_presence(0.0, True)
    esc.set_motion(0.5, True)      # walk in, then stop
    esc.set_motion(1.0, False)
    esc.tick(1.0 + 10)            # well within the window
    assert esc.machine.state is State.STILL
    esc.tick(1.0 + 45.1)
    assert esc.machine.state is State.CHECK_IN
    assert "no movement" in esc.machine.reason


def test_im_ok_rearms(esc):
    esc.set_presence(0.0, True)
    esc.set_motion(0.5, True)
    esc.set_motion(1.0, False)
    esc.tick(46.2)                              # check-in
    esc.im_ok(47.0)
    assert esc.machine.state is State.STILL     # present, not moving
    # re-arm grace holds the stillness trigger off
    esc.tick(47.0 + 119)
    assert esc.machine.state is State.STILL
    esc.tick(47.0 + 121)
    assert esc.machine.state is State.CHECK_IN


def test_check_in_timeout_then_all_clear(esc):
    esc.set_presence(0.0, True)
    esc.sound_candidate(5.0, "bang")
    assert esc.machine.state is State.CHECK_IN
    assert "sound" in esc.machine.reason
    esc.tick(35.2)                              # past the 30 s countdown
    assert esc.machine.state is State.ALERT
    esc.tick(45.2)                               # past the 10 s grace
    assert esc.machine.state is State.HELP_SENT
    esc.all_clear(46.0)
    assert esc.machine.state in (State.ACTIVE, State.STILL, State.AWAY)


def test_manual_help_jumps_to_alert(esc):
    esc.request_help(0.0)
    assert esc.machine.state is State.ALERT
    esc.im_ok(1.0)
    assert esc.machine.state is State.AWAY      # nobody present; stay calm


def test_sound_candidate_triggers_check_in_even_away(esc):
    esc.sound_candidate(0.0, "crash")
    assert esc.machine.state is State.CHECK_IN
    assert esc.machine.presence is False         # nobody confirmed present


def test_motion_resets_the_clock(esc):
    esc.set_presence(0.0, True)
    esc.set_motion(0.5, True)
    esc.set_motion(1.0, False)
    esc.tick(20)
    esc.set_motion(21, True)                    # they move again
    assert esc.machine.state is State.ACTIVE
    esc.set_motion(22, False)
    esc.tick(22 + 45.1)
    assert esc.machine.state is State.CHECK_IN


def test_presence_exit_leaves_monitor_states_only(esc):
    esc.set_presence(0.0, True)
    esc.sound_candidate(1.0, "thud")
    esc.set_presence(2.0, False)
    assert esc.machine.state is State.CHECK_IN   # keep checking; timeout decides


def test_snapshot_shape(esc):
    esc.set_presence(0.0, True)
    esc.sound_candidate(1.0, "yell")
    snap = esc.snapshot(5.0)
    assert snap["state"] == "check_in"
    assert snap["remaining"] == pytest.approx(26.0)
    assert snap["presence"] is True
