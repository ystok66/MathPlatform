from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import AnswerEvent, AuditLog, PracticeSession, Submission
from apps.api.app.practice_state import session_interaction_state, submission_attempt_stats
from apps.api.app.routers.practice import submit_feedback
from apps.api.app.schemas import PracticeFeedbackRequest


def seeded_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_duplicate_answer_does_not_increment_effective_attempt_or_error_count():
    db = seeded_db()
    first = Submission(
        session_id="s1",
        question_id="q1",
        answer_text="  42 ",
        is_correct=False,
        grading_result={"is_correct": False},
    )
    db.add(first)
    db.commit()

    duplicate = submission_attempt_stats(db, "s1", "q1", "42")
    changed = submission_attempt_stats(db, "s1", "q1", "43")

    assert duplicate["raw_attempt_index"] == 2
    assert duplicate["effective_attempt_index"] == 1
    assert duplicate["effective_error_count_before"] == 1
    assert duplicate["duplicate_of_submission_id"] == first.id
    assert changed["effective_attempt_index"] == 2
    assert changed["duplicate_of_submission_id"] is None


def test_session_interaction_state_tracks_skip_bookmark_and_uncertain_flags():
    db = seeded_db()
    db.add_all(
        [
            AnswerEvent(session_id="s1", question_id="q1", event_type="skipped", payload={"at_ms": 1}),
            AnswerEvent(session_id="s1", question_id="q2", event_type="bookmarked", payload={"at_ms": 2}),
            AnswerEvent(session_id="s1", question_id="q3", event_type="marked_uncertain", payload={"at_ms": 3}),
            AnswerEvent(session_id="s1", question_id="q3", event_type="uncertain_removed", payload={"at_ms": 4}),
        ]
    )
    db.commit()

    state = session_interaction_state(db, "s1")

    assert state["skipped_question_ids"] == ["q1"]
    assert state["bookmarked_question_ids"] == ["q2"]
    assert state["uncertain_question_ids"] == []


def test_submit_feedback_records_event_and_audit_log():
    db = seeded_db()
    db.add(PracticeSession(id="s1", mode="practice", config={}, question_ids=["q1"]))
    db.commit()

    submit_feedback("s1", PracticeFeedbackRequest(question_id="q1", feedback_type="confusing", message="题意不清楚", at_ms=123), db)
    event = db.query(AnswerEvent).filter_by(event_type="feedback_submitted").one()
    audit = db.query(AuditLog).filter_by(action="practice.feedback_submitted").one()

    assert event.payload["feedback_type"] == "confusing"
    assert event.payload["message"] == "题意不清楚"
    assert audit.entity_id == "s1"
