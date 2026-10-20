from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.generation_planning import plan_generation_request
from apps.api.app.models import GenerationJob, PracticeSession, Question, QuestionTag, SourceFile, Submission, TaxonomyTag, WrongQuestionRecord
from apps.api.app.routers.generation import create_job
from apps.api.app.schemas import GenerationJobRequest


def seeded_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=2)
    db.add(source)
    db.add_all(
        [
            Question(id="q1", source_file_id="sf1", status="verified", source_name="source.json", stem_preview="q1", difficulty_star=4, premium=False),
            Question(id="q2", source_file_id="sf1", status="verified", source_name="source.json", stem_preview="q2", difficulty_star=3, premium=False),
        ]
    )
    db.add(TaxonomyTag(id="method:equation", kind="method", label="equation"))
    db.add(QuestionTag(question_id="q1", tag_id="method:equation", role="method", raw_label="equation"))
    db.add(PracticeSession(id="s1", mode="practice", config={}, question_ids=["q1", "q2"]))
    db.add_all(
        [
            Submission(session_id="s1", question_id="q1", answer_text="0", is_correct=False, grading_result={"reason": "numeric_mismatch"}, elapsed_ms=30000),
            Submission(session_id="s1", question_id="q2", answer_text="1", is_correct=True, grading_result={"reason": "exact"}, elapsed_ms=12000),
        ]
    )
    db.add(WrongQuestionRecord(session_id="s1", question_id="q1", submission_id=None, error_type="calculation_or_numeric", analysis={}))
    db.commit()
    return db


def test_generation_request_planning_uses_learner_profile_when_source_session_is_given():
    db = seeded_db()

    planned = plan_generation_request(db, {"target_tags": [], "difficulty_star": None, "count": 1, "constraints": None, "source_session_id": "s1"})

    assert planned["target_tags"] == ["method:equation"]
    assert planned["difficulty_star"] == 4
    assert planned["learner_profile"]["accuracy"] == 0.5
    assert planned["planning"]["target_tags_source"] == "weak_tags"


def test_create_job_persists_planned_request():
    db = seeded_db()

    response = create_job(GenerationJobRequest(source_session_id="s1", count=1), run_now=False, idempotency_key=None, db=db)
    job = db.scalar(select(GenerationJob))

    assert response.status == "queued"
    assert job.request["learner_profile"]["source_session_id"] == "s1"
    assert job.request["planning"]["used_profile"] is True
