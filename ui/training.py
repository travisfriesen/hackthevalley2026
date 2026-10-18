"""Guided training: the on-device version of scripts/session.sh.

For each activity the UI speaks what to do, gives time to get into position,
counts down 5..1, says "recording", records, and says "done". The recorded
takes (CSI pcap, ultrasonic csv, audio slice) are the labelled data the
detectors are built from.

Pure Python (no Tk, no I/O, time injected): tick() returns actions for the
App to carry out, so the sequencing is unit-testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# label -> what to say to the person performing it
TAKES = [
    ("empty", "Next: leave the room and stay out of sight. Nobody should be in the room."),
    ("walking", "Next: walk back and forth across the room at a steady pace."),
    ("standing", "Next: stand still in the middle of the room, arms at your sides."),
    ("sitting", "Next: sit on a chair in the middle of the room and stay still."),
    ("lying", "Next: lie on the floor in the middle of the room and stay still, as if you cannot get up."),
    ("fall", "Next: start three meters away, walk to the middle, fall onto the mat, "
             "then lie completely still."),
]

# spoken lines, keyed for the voice cache (merged into voice.PHRASES)
PHRASES = {f"train_{label}": text for label, text in TAKES}
PHRASES.update({
    "train_recording": "Recording.",
    "train_done": "Done.",
    "train_complete": "Training complete. Thank you.",
})

INSTRUCT, COUNT, RECORD, REST = "instruct", "count", "record", "rest"


@dataclass
class Training:
    takes: list = field(default_factory=lambda: list(TAKES))
    position_s: float = 10.0      # time to get into position after the instruction
    count_s: int = 5
    record_s: float = 30.0
    rest_s: float = 3.0           # after "done", before the next instruction

    active: bool = False
    index: int = 0
    phase: str = INSTRUCT
    _phase_end: float = 0.0
    _last_count: int | None = None
    _pending: list = field(default_factory=list)

    # -- control ------------------------------------------------------------

    def start(self, now: float) -> None:
        self.active = True
        self.index = 0
        self._begin_instruct(now)

    def stop(self, now: float) -> list:
        """Abort; returns the actions needed (stop a running recording)."""
        actions = []
        if self.active and self.phase == RECORD:
            actions.append(("record_stop", self.takes[self.index][0]))
        self.active = False
        return actions

    def skip(self, now: float) -> list:
        """Drop the current take and move on."""
        if not self.active:
            return []
        actions = []
        if self.phase == RECORD:
            actions.append(("record_stop", self.takes[self.index][0]))
        self.index += 1
        return actions + self._next_or_finish(now)

    # -- time ---------------------------------------------------------------

    def tick(self, now: float) -> list:
        """Advance; returns [("speak", key) | ("record_start", label, seconds)
        | ("record_stop", label) | ("finished",)]."""
        if not self.active:
            return []
        actions, self._pending = self._pending, []
        if self.phase == INSTRUCT and now >= self._phase_end:
            self.phase, self._phase_end = COUNT, now + self.count_s
            self._last_count = None
        if self.phase == COUNT:
            if now >= self._phase_end:
                label = self.takes[self.index][0]
                self.phase, self._phase_end = RECORD, now + self.record_s
                actions += [("speak", "train_recording"),
                            ("record_start", label, self.record_s)]
            else:
                n = math.ceil(self._phase_end - now)
                if n != self._last_count:
                    self._last_count = n
                    actions.append(("speak", f"count_{n}"))
        elif self.phase == RECORD and now >= self._phase_end:
            label = self.takes[self.index][0]
            self.phase, self._phase_end = REST, now + self.rest_s
            actions += [("record_stop", label), ("speak", "train_done")]
        elif self.phase == REST and now >= self._phase_end:
            self.index += 1
            actions += self._next_or_finish(now)
        return actions

    # -- display ------------------------------------------------------------

    def snapshot(self, now: float) -> dict:
        if not self.active:
            return {"active": False}
        label, text = self.takes[self.index]
        return {"active": True, "index": self.index, "total": len(self.takes),
                "label": label, "text": text, "phase": self.phase,
                "remaining": max(0.0, self._phase_end - now)}

    # -- internal -----------------------------------------------------------

    def _begin_instruct(self, now: float) -> None:
        self.phase = INSTRUCT
        self._phase_end = now + self.position_s
        self._pending.append(("speak", f"train_{self.takes[self.index][0]}"))

    def _next_or_finish(self, now: float) -> list:
        if self.index >= len(self.takes):
            self.active = False
            return [("speak", "train_complete"), ("finished",)]
        self._begin_instruct(now)
        return []
