from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import GeneratedCandidate, GenerationJob
from apps.api.app.routers.generation import RetryGenerationJobRequest, cancel_job, retry_job, retry_job_preview


def seeded_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all(
        [
            GenerationJob(id="queued-job", status="queued", request={"count": 1}, progress={"stage": "queued"}),
            GenerationJob(id="rejected-job", status="rejected", request={"count": 1}, progress={"stage": "done", "accepted": False}),
        ]
    )
    db.commit()
    return db


def test_generation_job_can_be_cancelled():
    db = seeded_db()

    result = cancel_job("queued-job", db)

    saved = db.get(GenerationJob, "queued-job")
    assert saved is not None
    assert result["status"] == "cancelled"
    assert saved.progress["cancelled"] is True


def test_retry_rejected_job_creates_child_and_reuses_idempotency_key():
    db = seeded_db()

    first = retry_job("rejected-job", RetryGenerationJobRequest(run_now=False, idempotency_key="retry-key"), db)
    second = retry_job("rejected-job", RetryGenerationJobRequest(run_now=False, idempotency_key="retry-key"), db)
    jobs = list(db.scalars(select(GenerationJob).where(GenerationJob.idempotency_key == "retry-key")))

    assert first.id == second.id
    assert len(jobs) == 1
    assert jobs[0].progress["retry_of"] == "rejected-job"
    assert jobs[0].request["retry_attempt"] == 1
    assert jobs[0].request["regeneration_mode"] == "targeted_retry"


def test_retry_rejected_job_adds_targeted_regeneration_constraints():
    db = seeded_db()
    db.add(
        GeneratedCandidate(
            job_id="rejected-job",
            strategy="structural_elegance",
            status="rejected",
            payload={"errors": ["schema_validation_failed"], "reject_reason": "bad_json"},
            review={
                "issues": ["final_answer_grades_correct", "student_solution_style_valid"],
                "not_selected_reason": "pairwise_similarity_high",
            },
            score=0,
        )
    )
    db.commit()

    response = retry_job("rejected-job", RetryGenerationJobRequest(run_now=False), db)
    child = db.get(GenerationJob, response.id)

    assert child is not None
    codes = {row["code"] for row in child.request["regeneration_constraints"]}
    assert {"near_duplicate", "answer_spec_inconsistent", "solution_style_invalid", "general_rework"}.issubset(codes)
    assert child.progress["regeneration_attempt"] == 1
    assert child.request["prefer_reuse_existing_ai"] is False


def test_retry_preview_shows_constraints_without_creating_child():
    db = seeded_db()
    db.add(
        GeneratedCandidate(
            job_id="rejected-job",
            strategy="constraint_shift",
            status="rejected",
            payload={"reject_reason": "bad_json"},
            review={"issues": ["final_answer_grades_correct"], "not_selected_reason": "pairwise_similarity_high"},
            score=0,
        )
    )
    db.commit()

    preview = retry_job_preview("rejected-job", db)
    jobs = list(db.scalars(select(GenerationJob)))

    assert preview["retryable"] is True
    assert preview["regeneration_attempt"] == 1
    assert preview["prefer_reuse_existing_ai"] is False
    assert {row["code"] for row in preview["regeneration_constraints"]} >= {"near_duplicate", "answer_spec_inconsistent", "general_rework"}
    assert len(jobs) == 2


def test_retry_rejected_job_stops_after_attempt_limit():
    db = seeded_db()

    first = retry_job("rejected-job", RetryGenerationJobRequest(run_now=False), db)
    second = retry_job("rejected-job", RetryGenerationJobRequest(run_now=False), db)
    third = retry_job("rejected-job", RetryGenerationJobRequest(run_now=False), db)
    fourth = retry_job("rejected-job", RetryGenerationJobRequest(run_now=False), db)
    fourth_job = db.get(GenerationJob, fourth.id)

    assert [db.get(GenerationJob, row.id).request["retry_attempt"] for row in (first, second, third, fourth)] == [1, 2, 3, 4]
    assert fourth.status == "rejected"
    assert fourth_job is not None
    assert fourth_job.progress["retry_budget_exhausted"] is True
    assert fourth_job.progress["terminal_reason"] == "regeneration_attempt_limit_reached"


def test_retry_preview_reports_attempt_limit_exhausted():
    db = seeded_db()
    retry_job("rejected-job", RetryGenerationJobRequest(run_now=False), db)
    retry_job("rejected-job", RetryGenerationJobRequest(run_now=False), db)
    retry_job("rejected-job", RetryGenerationJobRequest(run_now=False), db)

    preview = retry_job_preview("rejected-job", db)

    assert preview["retryable"] is False
    assert preview["retry_exhausted"] is True
    assert preview["regeneration_attempt"] == 4
    assert preview["reason"] == "regeneration_attempt_limit_reached"
