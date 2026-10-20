from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import AnswerEvent, AnswerSpec, PracticeSession, Question, QuestionVersion, SourceFile, Submission
from apps.api.app.routers.practice import HelpRequest, give_up_question, judge_answer, submit_answer
from apps.api.app.schemas import AnswerJudgeRequest, SubmitAnswerRequest


def seeded_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf-state", filename="source.json", sha256="1" * 64, question_count=1)
    db.add(source)
    db.add(
        Question(
            id="q-state",
            source_file_id=source.id,
            status="verified",
            source_name="source.json",
            stem_preview="1+1=?",
            difficulty_star=1,
            premium=False,
            family_id="family-state",
        )
    )
    db.add(
        QuestionVersion(
            id="v-state",
            question_id="q-state",
            version=1,
            raw_json={"id": "q-state", "question_text": "1+1=?"},
            question_v2={"content": {"stem_md": "1+1=?"}},
            content_hash="content-hash-state",
        )
    )
    db.add(
        AnswerSpec(
            question_id="q-state",
            status="verified",
            answer_type="integer",
            canonical_answer="2",
            normalized_spec={},
            raw_answer_spec={
                "status": "verified",
                "answer_type": "integer",
                "canonical_answer": "2",
                "acceptable_answers": [],
                "tolerance": {"type": "exact", "value": 0},
                "unit": None,
                "order_sensitive": True,
                "grading_method": "numeric_exact",
            },
            verification={},
        )
    )
    db.add(PracticeSession(id="s-state", mode="practice", config={}, question_ids=["q-state"]))
    db.commit()
    return db


def count_rows(db: Session, model) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def test_submit_idempotency_returns_same_submission_without_duplicate_write():
    db = seeded_db()
    payload = SubmitAnswerRequest(
        question_id="q-state",
        answer_text="2",
        idempotency_key="submit-same",
        expected_state_version=0,
    )

    first = submit_answer("s-state", payload, db)
    second = submit_answer("s-state", payload, db)

    assert first.submission_id == second.submission_id
    assert first.state_version == 1
    assert second.state_version == 1
    assert count_rows(db, Submission) == 1
    snapshot = first.result["evaluation_snapshot"]
    assert snapshot["schema_version"] == "evaluation_snapshot_v2"
    assert snapshot["question_version_id"] == "v-state"
    assert snapshot["question_snapshot"]["stem_preview"] == "1+1=?"
    assert snapshot["grading_snapshot"]["answer_type"] == "integer"
    assert "canonical_answer" not in snapshot["grading_snapshot"]
    assert snapshot["grading_snapshot"]["canonical_answer_hash"]


def test_submit_rejects_stale_expected_state_version_before_writing():
    db = seeded_db()
    submit_answer(
        "s-state",
        SubmitAnswerRequest(question_id="q-state", answer_text="1", idempotency_key="submit-first", expected_state_version=0),
        db,
    )

    try:
        submit_answer(
            "s-state",
            SubmitAnswerRequest(question_id="q-state", answer_text="2", idempotency_key="submit-stale", expected_state_version=0),
            db,
        )
        assert False, "stale state_version should be rejected"
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail["error_code"] == "state_version_conflict"
        assert exc.detail["actual_state_version"] == 1

    assert count_rows(db, Submission) == 1


def test_reveal_answer_idempotency_locks_once():
    db = seeded_db()
    payload = HelpRequest(idempotency_key="reveal-same", expected_state_version=0)

    first = give_up_question("s-state", "q-state", payload, db)
    second = give_up_question("s-state", "q-state", payload, db)

    assert first["answer_locked"] is True
    assert second["state_version"] == first["state_version"] == 1
    events = list(db.scalars(select(AnswerEvent).where(AnswerEvent.event_type == "solution_revealed")))
    assert len(events) == 1


def test_answer_judge_idempotency_reuses_first_ai_result(monkeypatch):
    db = seeded_db()
    calls = {"count": 0}

    def fake_judge(*args, **kwargs):
        calls["count"] += 1
        return {
            "question_id": "q-state",
            "deterministic_result": {"is_correct": True},
            "verdict": "correct",
            "is_correct": True,
            "confidence": 0.95,
            "analysis": "可接受",
            "student_feedback": "答案正确。",
            "normalized_student_answer": "2",
            "missing_parts": [],
            "extra_parts": [],
            "format_issue": None,
            "should_override_deterministic": False,
            "override_reason": None,
            "provider": "mock",
            "model": "mock",
            "latency_ms": 1,
            "error": None,
        }

    monkeypatch.setattr("apps.api.app.routers.practice.judge_answer_with_ai", fake_judge)
    payload = AnswerJudgeRequest(
        question_id="q-state",
        answer_text="2",
        idempotency_key="judge-same",
        expected_state_version=0,
    )

    first = judge_answer("s-state", payload, db)
    second = judge_answer("s-state", payload, db)

    assert first.state_version == second.state_version == 1
    assert first.evaluation_snapshot["schema_version"] == "evaluation_snapshot_v2"
    assert first.evaluation_snapshot["question_version_id"] == "v-state"
    assert "canonical_answer" not in first.evaluation_snapshot["grading_snapshot"]
    assert calls["count"] == 1
    events = list(db.scalars(select(AnswerEvent).where(AnswerEvent.event_type == "answer_judged")))
    assert len(events) == 1
    assert events[0].payload["evaluation_snapshot"]["question_version_id"] == "v-state"
