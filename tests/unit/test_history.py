from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import (
    AnswerEvent,
    AnswerSpec,
    GenerationBatch,
    GenerationBatchItem,
    LearnerProfile,
    PracticeSession,
    Question,
    QuestionTag,
    SourceFile,
    Submission,
    TaxonomyTag,
    TestBlueprint,
    TestRun,
)
from apps.api.app.routers.history import HistoryReviewSessionRequest, create_review_session_from_history, get_history_group, list_history_groups


def seeded_history_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=5)
    db.add(source)
    db.add_all(
        [
            LearnerProfile(id="learner-a", name="A", status="active", display_order=1, settings={}),
            LearnerProfile(id="learner-b", name="B", status="active", display_order=2, settings={}),
            TaxonomyTag(id="method:counting", kind="method", label="counting", aliases=[]),
            TaxonomyTag(id="primary:number", kind="primary", label="number", aliases=[]),
            Question(id="q1", source_file_id="sf1", status="verified", eligible_for_practice=True, source_name="bank-a.json", stem_preview="q1", difficulty_star=3, premium=False),
            Question(id="q2", source_file_id="sf1", status="verified", eligible_for_practice=True, source_name="bank-a.json", stem_preview="q2", difficulty_star=4, premium=False),
            Question(id="q3", source_file_id="sf1", status="verified", eligible_for_practice=True, source_name="bank-b.json", stem_preview="q3", difficulty_star=5, premium=True),
            Question(id="q4", source_file_id="sf1", status="verified", eligible_for_practice=True, source_name="AI生成题", stem_preview="q4", difficulty_star=3, premium=False),
            Question(id="q5", source_file_id="sf1", status="needs_review", eligible_for_practice=False, source_name="bank-a.json", stem_preview="q5", difficulty_star=3, premium=False),
            AnswerSpec(question_id="q1", status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}),
            AnswerSpec(question_id="q2", status="verified", answer_type="integer", canonical_answer="2", normalized_spec={}, raw_answer_spec={}, verification={}),
            AnswerSpec(question_id="q3", status="verified", answer_type="integer", canonical_answer="3", normalized_spec={}, raw_answer_spec={}, verification={}),
            AnswerSpec(question_id="q4", status="verified", answer_type="integer", canonical_answer="4", normalized_spec={}, raw_answer_spec={}, verification={}),
            AnswerSpec(question_id="q5", status="needs_review", answer_type="integer", canonical_answer="5", normalized_spec={}, raw_answer_spec={}, verification={}),
            QuestionTag(question_id="q1", tag_id="method:counting", role="method", raw_label="counting"),
            QuestionTag(question_id="q2", tag_id="primary:number", role="primary", raw_label="number"),
            QuestionTag(question_id="q3", tag_id="method:counting", role="method", raw_label="counting"),
            QuestionTag(question_id="q4", tag_id="method:counting", role="method", raw_label="counting"),
            PracticeSession(
                id="practice1",
                mode="practice",
                learner_id="learner-a",
                status="active",
                config={},
                question_ids=["q1", "q2", "q5", "missing-q"],
                created_at=datetime(2026, 7, 21, 9, 0, 0),
                updated_at=datetime(2026, 7, 21, 9, 30, 0),
            ),
            PracticeSession(
                id="strict1",
                mode="strict_test",
                learner_id="learner-b",
                status="active",
                config={},
                question_ids=["q3"],
                created_at=datetime(2026, 7, 20, 8, 0, 0),
                updated_at=datetime(2026, 7, 20, 8, 20, 0),
            ),
            PracticeSession(
                id="generated1",
                mode="generated_practice",
                learner_id="learner-a",
                status="active",
                config={"generation_batch_id": "batch1", "selection": {"generation_batch_id": "batch1"}},
                question_ids=["q4", "q1"],
                created_at=datetime(2026, 7, 19, 8, 0, 0),
                updated_at=datetime(2026, 7, 19, 8, 20, 0),
            ),
            PracticeSession(
                id="review0",
                mode="review_practice",
                learner_id="learner-a",
                status="active",
                config={"review_source": {"group_id": "session:practice1"}},
                question_ids=["q2"],
                created_at=datetime(2026, 7, 18, 8, 0, 0),
                updated_at=datetime(2026, 7, 18, 8, 10, 0),
            ),
            TestBlueprint(id="bp1", name="Strict 100", mode="strict_test", count=100, quota={}, config={}),
            TestRun(
                id="run1",
                blueprint_id="bp1",
                session_id="strict1",
                status="active",
                progress={},
                created_at=datetime(2026, 7, 20, 8, 0, 0),
                updated_at=datetime(2026, 7, 20, 8, 20, 0),
            ),
            GenerationBatch(
                id="batch1",
                status="partial",
                request={"target_tags": ["counting"]},
                target_count=3,
                concurrency=2,
                progress={},
                accepted_count=2,
                created_at=datetime(2026, 7, 19, 8, 0, 0),
                updated_at=datetime(2026, 7, 19, 8, 20, 0),
            ),
            GenerationBatchItem(id="bi1", batch_id="batch1", item_index=1, status="accepted", question_id="q4", similarity_review={}),
            GenerationBatchItem(id="bi2", batch_id="batch1", item_index=2, status="accepted", question_id="q1", similarity_review={}),
            GenerationBatchItem(id="bi3", batch_id="batch1", item_index=3, status="failed", rejection_reason="timeout", similarity_review={}),
            Submission(
                id="sub-q1-wrong",
                session_id="practice1",
                question_id="q1",
                answer_text="0",
                is_correct=False,
                effective_wrong=True,
                grading_result={"is_correct": False, "reason": "numeric_mismatch", "effective_wrong": True},
                elapsed_ms=10000,
                created_at=datetime(2026, 7, 21, 9, 5, 0),
            ),
            Submission(
                id="sub-q1-correct",
                session_id="practice1",
                question_id="q1",
                answer_text="1",
                is_correct=True,
                grading_result={"is_correct": True},
                elapsed_ms=15000,
                created_at=datetime(2026, 7, 21, 9, 7, 0),
            ),
            Submission(
                id="sub-q3-correct",
                session_id="strict1",
                question_id="q3",
                answer_text="3",
                is_correct=True,
                grading_result={"is_correct": True},
                elapsed_ms=12000,
                created_at=datetime(2026, 7, 20, 8, 5, 0),
            ),
            Submission(
                id="sub-q4-correct",
                session_id="generated1",
                question_id="q4",
                answer_text="4",
                is_correct=True,
                grading_result={"is_correct": True},
                elapsed_ms=11000,
                created_at=datetime(2026, 7, 19, 8, 5, 0),
            ),
            Submission(
                id="sub-q2-review",
                session_id="review0",
                question_id="q2",
                answer_text="2",
                is_correct=True,
                grading_result={"is_correct": True},
                elapsed_ms=9000,
                created_at=datetime(2026, 7, 18, 8, 5, 0),
            ),
            AnswerEvent(id="evt-q1-help", session_id="practice1", question_id="q1", event_type="help_revealed", payload={"level": 1}),
            AnswerEvent(id="evt-q2-skip", session_id="practice1", question_id="q2", event_type="skipped", payload={}),
            AnswerEvent(id="evt-q2-reveal", session_id="practice1", question_id="q2", event_type="solution_revealed", payload={"level": 5}),
            AnswerEvent(id="evt-q2-explain", session_id="practice1", question_id="q2", event_type="solution_explanation_answered", payload={"status": "ok"}),
        ]
    )
    db.commit()
    return db


def group_by_id(groups: list[dict], suffix: str) -> dict:
    return next(row for row in groups if row["id"].endswith(suffix))


def test_history_groups_cover_sessions_test_runs_batches_and_reviews():
    db = seeded_history_db()
    groups = list_history_groups(db=db)
    ids = {row["id"] for row in groups}

    assert "session:practice1" in ids
    assert "test_run:run1" in ids
    assert "batch:batch1" in ids
    assert "session:review0" in ids
    assert "session:strict1" not in ids
    assert "session:generated1" not in ids
    assert group_by_id(groups, "practice1")["weak_count"] == 2
    assert group_by_id(groups, "batch1")["report_type"] == "generation_batch"


def test_history_groups_are_scoped_to_learner():
    db = seeded_history_db()
    learner_a_groups = list_history_groups(learner_id="learner-a", db=db)
    learner_b_groups = list_history_groups(learner_id="learner-b", db=db)

    learner_a_ids = {row["id"] for row in learner_a_groups}
    learner_b_ids = {row["id"] for row in learner_b_groups}
    assert "session:practice1" in learner_a_ids
    assert "batch:batch1" in learner_a_ids
    assert "session:review0" in learner_a_ids
    assert "test_run:run1" not in learner_a_ids
    assert learner_a_ids.isdisjoint(learner_b_ids)
    assert learner_b_ids == {"test_run:run1"}


def test_history_group_detail_reports_per_question_state_and_unavailable_items():
    db = seeded_history_db()
    submission = db.get(Submission, "sub-q1-correct")
    submission.grading_result = {
        "is_correct": True,
        "regrade": {
            "reason": "fixed answer",
            "reviewer": "tester",
            "applied_at": "2026-07-22T08:00:00",
        },
    }
    db.commit()

    detail = get_history_group("session:practice1", db=db)
    by_question = {row["question_id"]: row for row in detail["items"]}

    assert detail["question_count"] == 4
    assert detail["answered_count"] == 1
    assert by_question["q1"]["performance"] == "yellow"
    assert by_question["q1"]["attempt_count"] == 2
    assert by_question["q1"]["effective_wrong_count"] == 1
    assert by_question["q1"]["used_hint"] is True
    assert by_question["q1"]["regraded_submission_count"] == 1
    assert by_question["q1"]["latest_regrade_at"] == "2026-07-22T08:00:00"
    assert by_question["q2"]["performance"] == "red"
    assert by_question["q2"]["solution_revealed"] is True
    assert by_question["q2"]["asked_explanation"] is True
    assert by_question["q5"]["performance"] == "gray"
    assert by_question["q5"]["available"] is False
    assert by_question["missing-q"]["performance"] == "gray"
    assert by_question["missing-q"]["exists"] is False


def test_history_filters_and_weak_only_are_applied_to_groups_and_details():
    db = seeded_history_db()
    weak_groups = list_history_groups(source_name="bank-a.json", difficulty_star=3, tag_id=["method:counting"], weak_only=True, db=db)
    assert [row["id"] for row in weak_groups] == ["session:practice1"]

    detail = get_history_group("session:practice1", weak_only=True, db=db)
    assert [row["question_id"] for row in detail["items"]] == ["q1", "q2"]


def test_create_review_session_from_history_dedupes_and_keeps_original_session_unchanged():
    db = seeded_history_db()
    response = create_review_session_from_history("session:practice1", HistoryReviewSessionRequest(scope="weak_only"), db=db)

    created = db.get(PracticeSession, response.id)
    original = db.get(PracticeSession, "practice1")
    assert created is not None
    assert created.mode == "review_practice"
    assert created.learner_id == "learner-a"
    assert response.learner_id == "learner-a"
    assert response.question_ids == ["q1", "q2"]
    assert created.config["review_source"]["group_id"] == "session:practice1"
    assert created.config["learner_id"] == "learner-a"
    assert created.config["selection"]["strict_no_ai"] is False
    assert original.question_ids == ["q1", "q2", "q5", "missing-q"]


def test_create_review_session_from_history_reuses_existing_session_for_idempotency_key():
    db = seeded_history_db()

    first = create_review_session_from_history(
        "session:practice1",
        HistoryReviewSessionRequest(scope="weak_only", idempotency_key="history-review-key-1"),
        db=db,
    )
    second = create_review_session_from_history(
        "session:practice1",
        HistoryReviewSessionRequest(scope="all", idempotency_key="history-review-key-1"),
        db=db,
    )

    assert second.id == first.id
    assert second.question_ids == first.question_ids


def test_create_review_session_selected_scope_and_empty_scope_errors():
    db = seeded_history_db()
    response = create_review_session_from_history(
        "batch:batch1",
        HistoryReviewSessionRequest(scope="selected", selected_question_ids=["q1", "q4", "q1"]),
        db=db,
    )
    assert response.question_ids == ["q4", "q1"]

    learner_response = create_review_session_from_history(
        "session:practice1",
        HistoryReviewSessionRequest(scope="selected", selected_question_ids=["q1"], learner_id="learner-a"),
        db=db,
    )
    assert learner_response.learner_id == "learner-a"

    with pytest.raises(HTTPException) as wrong_learner_info:
        create_review_session_from_history(
            "session:practice1",
            HistoryReviewSessionRequest(scope="selected", selected_question_ids=["q1"], learner_id="learner-b"),
            db=db,
        )
    assert wrong_learner_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        create_review_session_from_history("session:practice1", HistoryReviewSessionRequest(scope="selected"), db=db)
    assert exc_info.value.status_code == 400

    with pytest.raises(HTTPException) as empty_info:
        create_review_session_from_history("session:practice1", HistoryReviewSessionRequest(scope="selected", selected_question_ids=["q5"]), db=db)
    assert empty_info.value.status_code == 409
