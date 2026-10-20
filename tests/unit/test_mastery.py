from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.mastery import build_mastery_payload, build_today_tasks
from apps.api.app.models import AnswerEvent, AnswerSpec, LearnerProfile, PracticeSession, Question, QuestionTag, SourceFile, Submission, TaxonomyTag
from apps.api.app.routers.learning import LearningReviewSessionRequest, create_learning_review_session, mastery_profile, today_tasks
from apps.api.app.routers.practice import create_session
from apps.api.app.schemas import CreatePracticeSessionRequest


def seeded_mastery_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all(
        [
            LearnerProfile(id="learner-a", name="A", status="active", display_order=1, settings={}),
            LearnerProfile(id="learner-b", name="B", status="active", display_order=2, settings={}),
            SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=3),
            TaxonomyTag(id="method:equation", kind="method", label="方程", aliases=[]),
            TaxonomyTag(id="task:geometry", kind="task", label="几何", aliases=[]),
            Question(id="q1", source_file_id="sf1", status="verified", question_fault_status="none", source_name="source.json", stem_preview="q1", difficulty_star=3, premium=False),
            Question(id="q2", source_file_id="sf1", status="verified", question_fault_status="none", source_name="source.json", stem_preview="q2", difficulty_star=4, premium=False),
            Question(id="q3", source_file_id="sf1", status="verified", question_fault_status="none", source_name="source.json", stem_preview="q3", difficulty_star=3, premium=True),
            AnswerSpec(question_id="q1", status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}),
            AnswerSpec(question_id="q2", status="verified", answer_type="integer", canonical_answer="2", normalized_spec={}, raw_answer_spec={}, verification={}),
            AnswerSpec(question_id="q3", status="verified", answer_type="integer", canonical_answer="3", normalized_spec={}, raw_answer_spec={}, verification={}),
            QuestionTag(question_id="q1", tag_id="method:equation", role="method", raw_label="方程"),
            QuestionTag(question_id="q2", tag_id="task:geometry", role="task", raw_label="几何"),
            QuestionTag(question_id="q3", tag_id="method:equation", role="method", raw_label="equation"),
            PracticeSession(id="s-a", learner_id="learner-a", mode="practice", status="active", config={}, question_ids=["q1", "q2"], updated_at=datetime(2026, 7, 20, 9, 0, 0)),
            PracticeSession(id="s-b", learner_id="learner-b", mode="practice", status="active", config={}, question_ids=["q1"], updated_at=datetime(2026, 7, 22, 9, 0, 0)),
            Submission(id="a-q1-wrong", session_id="s-a", question_id="q1", answer_text="0", is_correct=False, effective_wrong=True, grading_result={"is_correct": False, "reason": "numeric_mismatch"}, elapsed_ms=1000, created_at=datetime(2026, 7, 20, 9, 1, 0)),
            Submission(id="a-q1-correct", session_id="s-a", question_id="q1", answer_text="1", is_correct=True, help_level_reached=3, grading_result={"is_correct": True}, elapsed_ms=2000, created_at=datetime(2026, 7, 20, 9, 3, 0)),
            Submission(id="a-q2-correct", session_id="s-a", question_id="q2", answer_text="2", is_correct=True, grading_result={"is_correct": True}, elapsed_ms=2000, created_at=datetime(2026, 7, 21, 9, 3, 0)),
            Submission(id="b-q1-correct", session_id="s-b", question_id="q1", answer_text="1", is_correct=True, grading_result={"is_correct": True}, elapsed_ms=1500, created_at=datetime(2026, 7, 22, 9, 1, 0)),
            AnswerEvent(id="a-q1-help", session_id="s-a", question_id="q1", event_type="help_revealed", payload={"level": 3}, created_at=datetime(2026, 7, 20, 9, 2, 0)),
            AnswerEvent(id="a-q1-reveal", session_id="s-a", question_id="q1", event_type="solution_revealed", payload={"level": 5}, created_at=datetime(2026, 7, 20, 9, 4, 0)),
        ]
    )
    db.commit()
    return db


def test_mastery_profile_scores_tag_star_and_due_questions_by_learner():
    db = seeded_mastery_db()
    now = datetime(2026, 7, 22, 9, 0, 0)

    learner_a = build_mastery_payload(db, "learner-a", now=now)
    learner_b = build_mastery_payload(db, "learner-b", now=now)

    equation_a = next(row for row in learner_a["mastery"] if row["tag_id"] == "method:equation")
    equation_b = next(row for row in learner_b["mastery"] if row["tag_id"] == "method:equation")
    assert equation_a["mastery_level"] == "unstable"
    assert "answer_revealed" in equation_a["reason_codes"]
    assert learner_a["summary"]["due_question_count"] == 1
    assert learner_a["due_questions"][0]["question_id"] == "q1"
    assert equation_b["score"] > equation_a["score"]
    assert learner_b["summary"]["due_question_count"] == 0


def test_today_tasks_prioritizes_due_review_and_weak_tag_check():
    db = seeded_mastery_db()
    payload = build_today_tasks(db, "learner-a", now=datetime(2026, 7, 22, 9, 0, 0))

    assert payload["tasks"][0]["task_type"] == "review_due_questions"
    assert payload["tasks"][0]["question_ids"] == ["q1"]
    assert any(task["task_type"] == "tag_star_mastery_check" for task in payload["tasks"])


def test_mastery_and_today_routes_resolve_learner_id():
    db = seeded_mastery_db()

    profile = mastery_profile(learner_id="learner-a", db=db)
    tasks = today_tasks(learner_id="learner-a", db=db)

    assert profile["learner_id"] == "learner-a"
    assert tasks["learner_id"] == "learner-a"


def test_learning_review_session_uses_available_questions_and_learner():
    db = seeded_mastery_db()

    response = create_learning_review_session(
        LearningReviewSessionRequest(learner_id="learner-a", question_ids=["q1", "q1", "missing"], reason="due_review"),
        db=db,
    )

    session = db.get(PracticeSession, response.id)
    assert response.learner_id == "learner-a"
    assert response.question_ids == ["q1"]
    assert session is not None
    assert session.mode == "review_practice"
    assert session.learner_id == "learner-a"
    assert session.config["review_source"]["skipped_count"] == 1


def test_learning_review_session_can_select_report_task_by_tag_and_star():
    db = seeded_mastery_db()

    response = create_learning_review_session(
        LearningReviewSessionRequest(
            learner_id="learner-a",
            target_tags=["method:equation"],
            difficulty_star=3,
            question_count=1,
            reason="review-task-1",
        ),
        db=db,
    )

    session = db.get(PracticeSession, response.id)
    assert response.question_ids == ["q3"]
    assert session is not None
    assert session.mode == "review_practice"
    assert session.config["review_source"]["selection_mode"] == "tag_star_report_task"
    assert session.config["review_source"]["target_tags"] == ["method:equation"]
    assert session.config["review_source"]["fallback_used"] is None


def test_learning_review_session_falls_back_to_answered_pool_when_unseen_pool_is_empty():
    db = seeded_mastery_db()

    response = create_learning_review_session(
        LearningReviewSessionRequest(
            learner_id="learner-a",
            target_tags=["task:geometry"],
            difficulty_star=4,
            question_count=1,
            reason="review-task-geometry",
        ),
        db=db,
    )

    session = db.get(PracticeSession, response.id)
    assert response.question_ids == ["q2"]
    assert session is not None
    assert session.config["review_source"]["fallback_used"] == "answered_pool"


def test_create_practice_session_reuses_existing_session_for_idempotency_key():
    db = seeded_mastery_db()

    first = create_session(
        CreatePracticeSessionRequest(learner_id="learner-a", idempotency_key="practice-key-1", count=1, tag_ids=["method:equation"]),
        db=db,
    )
    second = create_session(
        CreatePracticeSessionRequest(learner_id="learner-a", idempotency_key="practice-key-1", count=1, tag_ids=["task:geometry"]),
        db=db,
    )

    assert second.id == first.id
    assert second.question_ids == first.question_ids


def test_learning_review_session_reuses_existing_session_for_idempotency_key():
    db = seeded_mastery_db()

    first = create_learning_review_session(
        LearningReviewSessionRequest(
            learner_id="learner-a",
            idempotency_key="review-key-1",
            target_tags=["method:equation"],
            difficulty_star=3,
            question_count=1,
        ),
        db=db,
    )
    second = create_learning_review_session(
        LearningReviewSessionRequest(
            learner_id="learner-a",
            idempotency_key="review-key-1",
            target_tags=["task:geometry"],
            difficulty_star=4,
            question_count=1,
        ),
        db=db,
    )

    assert second.id == first.id
    assert second.question_ids == first.question_ids
