from apps.api.app.timing import reduce_timing_events
import random


def event(event_type: str, at_ms: int):
    return {"event_type": event_type, "at_ms": at_ms}


def test_thought_time_is_excluded_when_answer_not_changed_afterward():
    state = reduce_timing_events(
        [
            event("session_created", 0),
            event("answer_changed", 1000),
            event("thought_focus", 3000),
            event("thought_changed", 8000),
            event("submitted", 12000),
        ]
    )
    assert state["answer_elapsed_ms"] == 3000
    assert state["thought_elapsed_ms"] == 9000
    assert state["effective_answer_elapsed_ms"] == 3000


def test_thought_time_counts_if_answer_changes_after_thought():
    state = reduce_timing_events(
        [
            event("session_created", 0),
            event("answer_changed", 1000),
            event("thought_focus", 3000),
            event("thought_changed", 8000),
            event("answer_changed", 12000),
            event("submitted", 15000),
        ]
    )
    assert state["thought_elapsed_ms"] == 9000
    assert state["answer_elapsed_ms"] == 15000
    assert state["pending_thought_ms"] == 0
    assert state["answer_revision_count"] == 2


def test_pause_excludes_elapsed_time_until_resume():
    state = reduce_timing_events(
        [
            event("session_created", 0),
            event("answer_changed", 1000),
            event("paused", 5000),
            event("resumed_answer", 15000),
            event("submitted", 18000),
        ]
    )
    assert state["answer_elapsed_ms"] == 8000
    assert state["status"] == "submitted"


def test_timing_state_randomized_invariants():
    rng = random.Random(20260719)
    event_types = ["answer_focus", "thought_focus", "answer_changed", "thought_changed", "paused", "resumed_answer", "resumed_thought"]
    for _ in range(50):
        at_ms = 0
        events = [event("session_created", at_ms)]
        for _step in range(20):
            at_ms += rng.randint(0, 3000)
            events.append(event(rng.choice(event_types), at_ms))
        at_ms += rng.randint(0, 3000)
        events.append(event(rng.choice(["submitted", "skipped"]), at_ms))

        state = reduce_timing_events(events)

        assert state["answer_elapsed_ms"] >= 0
        assert state["thought_elapsed_ms"] >= 0
        assert state["pending_thought_ms"] >= 0
        assert state["answer_elapsed_ms"] <= at_ms
        assert state["thought_elapsed_ms"] <= at_ms
        assert state["last_event_ms"] == at_ms
        assert state["status"] in {"submitted", "skipped"}
