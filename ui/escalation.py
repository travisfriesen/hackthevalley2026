"""Escalation state machine: the "help" core of the DownLink UI.

Pure Python (no Tk), with time injected by the caller, so the transitions can
be unit-tested. The App feeds it events (presence, motion, sound candidates,
button presses) and calls tick() from its UI loop; the machine decides when to
escalate and reports transitions through a callback.

Design (from docs/LOG.md): CSI detects motion reliably but cannot tell a
still person from an empty room, so ultrasonic presence gates the stillness
escalation. Sound candidates escalate on their own. Every escalation has a
dignity-preserving cancel, and a re-arm grace stops repeated false triggers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    AWAY = "away"          # nobody in the room
    ACTIVE = "active"     # present, and CSI sees motion
    STILL = "still"       # present, CSI quiet, stillness clock running
    CHECK_IN = "check_in" # "Are you OK?" countdown
    ALERT = "alert"       # red alarm, last-chance cancel window
    HELP_SENT = "help_sent"  # simulated call made


@dataclass
class Config:
    still_seconds: float = 45.0
    checkin_seconds: float = 30.0
    alert_grace_seconds: float = 10.0
    rearm_seconds: float = 120.0


@dataclass
class Transition:
    t: float
    frm: str
    to: str
    reason: str


@dataclass
class Machine:
    config: Config = field(default_factory=Config)
    state: State = State.AWAY
    deadline: float | None = None        # countdown target for the current state
    still_since: float | None = None      # when CSI motion last fell quiet
    rearm_until: float = 0.0             # stillness escalation held off until
    reason: str = ""
    presence: bool = False
    moving: bool = False
    history: list = field(default_factory=list)

    # -- internal ---------------------------------------------------------

    def _go(self, now: float, to: State, reason: str, on_transition=None) -> None:
        frm = self.state
        self.state = to
        self.reason = reason
        self.history.append(Transition(now, frm.value, to.value, reason))
        if on_transition is not None:
            on_transition(now, frm, to, reason)


def _enter_monitor(machine: Machine, now: float) -> State:
    """Pick ACTIVE or STILL based on the latest presence/motion inputs."""
    if not machine.presence:
        return State.AWAY
    if machine.moving:
        return State.ACTIVE
    machine.still_since = now
    machine.deadline = max(
        now + machine.config.still_seconds, machine.rearm_until
    )
    return State.STILL


class Escalation:
    """Event-driven wrapper around the Machine dataclass."""

    def __init__(self, config: Config | None = None,
                 on_transition=None, log=None):
        self.machine = Machine(config=config or Config())
        self.on_transition = on_transition
        self.log = log

    # -- events -----------------------------------------------------------

    def set_presence(self, now: float, present: bool) -> None:
        m = self.machine
        m.presence = present
        if m.state in (State.ACTIVE, State.STILL) and not present:
            m._go(now, State.AWAY, "ultrasonic: person left", self._emit())
        elif m.state is State.AWAY and present:
            m._go(now, _enter_monitor(m, now), "ultrasonic: person entered",
                  self._emit())

    def set_motion(self, now: float, moving: bool) -> None:
        m = self.machine
        m.moving = moving
        if m.state is State.ACTIVE and not moving:
            m.still_since = now
            m.deadline = max(
                now + m.config.still_seconds, m.rearm_until
            )
            m._go(now, State.STILL, "CSI motion stopped", self._emit())
        elif m.state is State.STILL and moving:
            m._go(now, State.ACTIVE, "CSI motion resumed", self._emit())

    def sound_candidate(self, now: float, label: str = "loud sound") -> None:
        m = self.machine
        if m.state in (State.AWAY, State.ACTIVE, State.STILL):
            self._start_check_in(now, f"sound: {label}")

    def request_help(self, now: float) -> None:
        """Big red button, on the dashboard or the check-in screen."""
        m = self.machine
        if m.state in (State.AWAY, State.ACTIVE, State.STILL, State.CHECK_IN):
            m._go(now, State.ALERT, "manual: I NEED HELP pressed", self._emit())
            m.deadline = now + m.config.alert_grace_seconds

    def im_ok(self, now: float) -> None:
        """Green button on check-in, cancel on the alert screen."""
        m = self.machine
        if m.state in (State.CHECK_IN, State.ALERT):
            m.rearm_until = now + m.config.rearm_seconds
            to = _enter_monitor(m, now)
            m._go(now, to, "person confirmed OK", self._emit())

    def all_clear(self, now: float) -> None:
        m = self.machine
        if m.state is State.HELP_SENT:
            to = _enter_monitor(m, now)
            m._go(now, to, "help arrived / all clear", self._emit())

    def tick(self, now: float) -> None:
        """Advance timers. Call regularly (~10 Hz) with a steady clock."""
        m = self.machine
        if m.state is State.STILL and m.deadline is not None and now >= m.deadline:
            held = " (re-arm grace)" if m.deadline > (
                (m.still_since or now) + m.config.still_seconds) else ""
            self._start_check_in(
                now,
                f"no movement for {m.config.still_seconds:.0f} s "
                f"while someone is in the room{held}",
            )
        elif m.state is State.CHECK_IN and now >= (m.deadline or now):
            m._go(now, State.ALERT, "check-in countdown expired", self._emit())
            m.deadline = now + m.config.alert_grace_seconds
        elif m.state is State.ALERT and now >= (m.deadline or now):
            m._go(now, State.HELP_SENT, "alert grace expired", self._emit())
            m.deadline = None

    # -- introspection ------------------------------------------------------

    def snapshot(self, now: float) -> dict:
        m = self.machine
        remaining = None
        if m.deadline is not None and m.state in (
            State.CHECK_IN, State.ALERT, State.STILL
        ):
            remaining = max(0.0, m.deadline - now)
        return {
            "state": m.state.value,
            "reason": m.reason,
            "remaining": remaining,
            "still_since": m.still_since,
            "presence": m.presence,
            "moving": m.moving,
        }

    # -- internal -----------------------------------------------------------

    def _start_check_in(self, now: float, reason: str) -> None:
        m = self.machine
        m._go(now, State.CHECK_IN, reason, self._emit())
        m.deadline = now + m.config.checkin_seconds

    def _emit(self):
        return self._on_transition

    def _on_transition(self, now, frm, to, reason):
        if self.log is not None:
            self.log(now, frm, to, reason)
        if self.on_transition is not None:
            self.on_transition(now, frm, to, reason)
