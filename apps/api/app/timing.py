from __future__ import annotations

from dataclasses import dataclass
from typing import Any


COUNTED_EVENTS = {
    "session_created",
    "answer_focus",
    "thought_focus",
    "answer_changed",
    "thought_changed",
    "paused",
    "resumed_answer",
    "resumed_thought",
    "submitted",
    "skipped",
}


@dataclass
class TimingState:
    active_phase: str | None = None
    active_since_ms: int | None = None
    answer_elapsed_ms: int = 0
    thought_elapsed_ms: int = 0
    pending_thought_ms: int = 0
    answer_revision_count: int = 0
    effective_answer_elapsed_ms: int = 0
    status: str = "active"
    last_event_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "active_phase": self.active_phase,
            "active_since_ms": self.active_since_ms,
            "answer_elapsed_ms": self.answer_elapsed_ms,
            "thought_elapsed_ms": self.thought_elapsed_ms,
            "pending_thought_ms": self.pending_thought_ms,
            "answer_revision_count": self.answer_revision_count,
            "effective_answer_elapsed_ms": self.effective_answer_elapsed_ms,
            "status": self.status,
            "last_event_ms": self.last_event_ms,
        }


def close_phase(state: TimingState, at_ms: int) -> None:
    if state.active_phase is None or state.active_since_ms is None:
        state.active_since_ms = at_ms
        return
    delta = max(0, at_ms - state.active_since_ms)
    if state.active_phase == "answer":
        state.answer_elapsed_ms += delta
    elif state.active_phase == "thought":
        state.thought_elapsed_ms += delta
        state.pending_thought_ms += delta
    state.active_since_ms = at_ms


def reduce_timing_events(events: list[dict[str, Any]], now_ms: int | None = None) -> dict[str, Any]:
    state = TimingState()
    for event in sorted(events, key=lambda x: int(x.get("at_ms", 0))):
        event_type = event.get("event_type")
        if event_type not in COUNTED_EVENTS:
            continue
        at_ms = int(event.get("at_ms", 0))
        close_phase(state, at_ms)
        state.last_event_ms = at_ms

        if event_type in {"session_created", "answer_focus", "resumed_answer"}:
            state.active_phase = "answer"
            state.active_since_ms = at_ms
            state.status = "active"
        elif event_type in {"thought_focus", "resumed_thought"}:
            state.active_phase = "thought"
            state.active_since_ms = at_ms
            state.status = "active"
        elif event_type == "answer_changed":
            state.answer_revision_count += 1
            if state.pending_thought_ms > 0:
                state.answer_elapsed_ms += state.pending_thought_ms
                state.pending_thought_ms = 0
            state.active_phase = "answer"
            state.active_since_ms = at_ms
        elif event_type == "thought_changed":
            state.active_phase = "thought"
            state.active_since_ms = at_ms
        elif event_type == "paused":
            state.active_phase = None
            state.active_since_ms = at_ms
            state.status = "paused"
        elif event_type in {"submitted", "skipped"}:
            state.active_phase = None
            state.active_since_ms = at_ms
            state.status = "submitted" if event_type == "submitted" else "skipped"

    if now_ms is not None and state.active_phase is not None:
        close_phase(state, now_ms)

    state.effective_answer_elapsed_ms = state.answer_elapsed_ms
    return state.as_dict()
