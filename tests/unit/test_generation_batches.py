from datetime import timedelta
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.config import get_settings
from apps.api.app.db import Base
from apps.api.app.ai_gateway import job_terminal_reason, process_generation_job
from apps.api.app.models import AiRunLog, AnswerEvent, AnswerSpec, GeneratedCandidate, GenerationBatch, GenerationBatchItem, GenerationDurationStat, GenerationJob, PracticeSession, Question, QuestionTag, SourceFile, Submission, TaxonomyTag, utc_now
from apps.api.app.routers.generation import batch_payload, cancel_batch, create_batch, create_job, create_or_update_batch_practice, enforce_final_batch_diversity, generation_call_budget_snapshot, list_batches, mark_stale_job_if_needed, process_batch_item, process_generation_batch, refresh_batch_counts, top_up_batch
from apps.api.app.schemas import GenerationBatchPracticeRequest, GenerationBatchRequest, GenerationJobRequest
from apps.api.app.routers.generation import GenerationBatchTopUpRequest


def seed_reusable_ai_pool(db: Session, count: int = 3, done_ids: set[str] | None = None) -> None:
    done_ids = done_ids or set()
    source = SourceFile(id="sf-ai", filename="__ai_generated__", sha256="generated", question_count=count)
    db.add(source)
    db.add(TaxonomyTag(id="method:target", kind="method", label="目标", aliases=[]))
    stems = [
        "把1到9填入九宫格，每行三个数之和相等，中心数是多少？",
        "一条路分三段修完，第一段比第二段多12米，第三段是第二段的2倍，求第二段长度。",
        "若三个连续奇数的和比中间数的4倍少9，求最大的奇数。",
        "五张卡片写着不同数字，任取两张的和共有6种结果，判断缺少的数字。",
    ]
    for index in range(count):
        question_id = f"ai-reuse-{index}"
        db.add(
            Question(
                id=question_id,
                source_file_id=source.id,
                status="verified",
                source_name="AI生成题",
                publish_status="active_practice",
                eligible_for_practice=True,
                eligible_for_test=True,
                question_fault_status="none",
                stem_preview=stems[index % len(stems)],
                difficulty_star=3,
                premium=False,
            )
        )
        db.add(AnswerSpec(question_id=question_id, status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}))
        db.add(QuestionTag(question_id=question_id, tag_id="method:target", role="method", raw_label="目标"))
    if done_ids:
        db.add(PracticeSession(id="done-session", mode="practice", config={}, question_ids=list(done_ids)))
        for question_id in done_ids:
            db.add(Submission(session_id="done-session", question_id=question_id, answer_text="1", is_correct=True, grading_result={}))
    db.commit()


def test_generation_batch_processes_mock_items_and_creates_live_practice():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    batch = GenerationBatch(
        status="queued",
        request={"target_tags": ["数论"], "difficulty_star": 3, "count": 1, "solution_style": "elementary_preferred", "batch_provider": "mock"},
        target_count=3,
        concurrency=2,
        progress={},
    )
    db.add(batch)
    db.commit()

    result = process_generation_batch(db, batch.id, provider="mock", threaded=False)

    assert result["status"] == "completed"
    assert result["accepted_count"] == 3
    assert result["eta_seconds"] == 0
    assert len([row for row in result["items"] if row["question_id"]]) == 3
    assert db.scalar(select(Question).where(Question.source_name == "AI生成题")) is not None

    practice = create_or_update_batch_practice(batch.id, GenerationBatchPracticeRequest(), db)
    session = db.get(PracticeSession, practice.id)

    assert len(practice.question_ids) == 3
    assert session.mode == "generated_practice"
    assert session.config["generation_batch_id"] == batch.id
    assert db.scalar(select(AnswerEvent).where(AnswerEvent.event_type == "generation_batch_queue_updated")) is not None


def test_generation_batch_eta_progress_includes_confidence_and_range():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    for index in range(12):
        db.add(GenerationDurationStat(provider="mock", model="m", status="accepted", duration_ms=20_000 + index))
    batch = GenerationBatch(
        status="queued",
        request={"target_tags": ["数论"], "difficulty_star": 3, "count": 4, "solution_style": "elementary_preferred", "batch_provider": "mock"},
        target_count=4,
        concurrency=2,
        progress={},
    )
    db.add(batch)
    db.commit()

    refresh_batch_counts(db, batch)
    db.commit()

    assert batch.eta_seconds == 40
    assert batch.progress["eta_seconds"] == 40
    assert batch.progress["eta_source"] == "history"
    assert batch.progress["eta_sample_count"] == 12
    assert batch.progress["eta_confidence"] == "medium"
    assert batch.progress["eta_range_seconds"][0] < 40 < batch.progress["eta_range_seconds"][1]


def test_generation_batch_progress_aggregates_failure_reasons():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    batch = GenerationBatch(
        status="running",
        request={},
        target_count=4,
        concurrency=2,
        progress={},
    )
    db.add(batch)
    db.flush()
    db.add_all(
        [
            GenerationBatchItem(batch_id=batch.id, item_index=1, status="accepted", question_id=None, similarity_review={}),
            GenerationBatchItem(batch_id=batch.id, item_index=2, status="rejected", rejection_reason="near_duplicate_in_batch", similarity_review={}),
            GenerationBatchItem(batch_id=batch.id, item_index=3, status="failed", rejection_reason="provider_failed", similarity_review={}),
            GenerationBatchItem(batch_id=batch.id, item_index=4, status="failed", rejection_reason="provider_failed", similarity_review={}),
            GenerationBatchItem(batch_id=batch.id, item_index=5, status="cancelled", rejection_reason=None, similarity_review={"terminal_reason": "batch_cancelled"}),
        ]
    )
    db.commit()

    refresh_batch_counts(db, batch)

    assert batch.progress["accepted_count"] == 1
    assert batch.progress["remaining_target_count"] == 3
    assert batch.progress["failure_reason_counts"] == {
        "provider_failed": 2,
        "batch_cancelled": 1,
        "near_duplicate_in_batch": 1,
    }


def test_generation_batch_reuses_unanswered_ai_questions_before_provider_jobs():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    seed_reusable_ai_pool(db, count=3, done_ids={"ai-reuse-0"})
    batch = GenerationBatch(
        status="queued",
        request={"target_tags": ["method:target"], "difficulty_star": 3, "count": 1, "solution_style": "elementary_preferred", "prefer_reuse_existing_ai": True, "batch_provider": "mock"},
        target_count=2,
        concurrency=1,
        progress={},
    )
    db.add(batch)
    db.commit()

    result = process_generation_batch(db, batch.id, provider="mock", threaded=False)

    assert result["status"] == "completed"
    assert result["accepted_count"] == 2
    assert result["progress"]["reused_count"] == 2
    assert result["progress"]["generated_count"] == 0
    assert {row["question_id"] for row in result["items"]} == {"ai-reuse-1", "ai-reuse-2"}
    assert {row["source_type"] for row in result["items"]} == {"reused_ai"}
    assert db.scalar(select(GenerationJob)) is None


def test_generation_batch_respects_custom_candidate_count_when_generating():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    batch = GenerationBatch(
        status="queued",
        request={
            "target_tags": ["数论"],
            "difficulty_star": 3,
            "count": 1,
            "solution_style": "elementary_preferred",
            "prefer_reuse_existing_ai": False,
            "candidate_count": 1,
            "source_example_count": 0,
            "ai_negative_example_count": 0,
            "batch_provider": "mock",
        },
        target_count=1,
        concurrency=1,
        progress={},
    )
    db.add(batch)
    db.commit()

    result = process_generation_batch(db, batch.id, provider="mock", threaded=False)
    job = db.scalar(select(GenerationJob))
    candidate_rows = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job.id)))

    assert result["status"] == "completed"
    assert job.progress["model_selection"]["candidate_count"] == 1
    assert job.progress["model_selection"]["candidate_strategies"] == ["structural_elegance"]
    assert job.progress["source_example_count"] == 0
    assert job.progress["ai_negative_example_count"] == 0
    assert len(candidate_rows) == 1


def test_create_generation_job_reuses_existing_ai_question_without_candidate_generation():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    seed_reusable_ai_pool(db, count=1)

    response = create_job(GenerationJobRequest(target_tags=["method:target"], difficulty_star=3, count=1), run_now=False, idempotency_key=None, db=db)
    job = db.scalar(select(GenerationJob))

    assert response.status == "accepted"
    assert job.accepted_question_id == "ai-reuse-0"
    assert job.progress["source_type"] == "reused_ai"
    assert job.progress["call_budget"]["actual_provider_calls_planned"] == 0


def test_strict_generation_call_budget_counts_secondary_reviewer_calls():
    snapshot = generation_call_budget_snapshot(
        {"candidate_count": 5, "source_example_count": 8, "ai_negative_example_count": 5},
        target_count=2,
        scope="job",
        settings=get_settings(),
    )

    assert snapshot["dual_reviewer_required"] is True
    assert snapshot["planned_generation_calls"] == 10
    assert snapshot["planned_primary_reviewer_calls"] == 10
    assert snapshot["planned_secondary_reviewer_calls"] == 10
    assert snapshot["planned_reviewer_calls"] == 20
    assert snapshot["planned_total_calls"] == 30


def test_create_generation_job_rejects_call_budget_exceeded(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("AI_MAX_GENERATION_CALLS_PER_JOB", "4")
    monkeypatch.setenv("AI_MAX_REVIEWER_CALLS_PER_JOB", "5")
    get_settings.cache_clear()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)

    with pytest.raises(HTTPException) as exc:
        create_job(GenerationJobRequest(target_tags=["数论"], difficulty_star=3, candidate_count=5), run_now=False, idempotency_key=None, db=db)

    assert exc.value.status_code == 429
    assert exc.value.detail["error_code"] == "generation_call_budget_exceeded"
    assert exc.value.detail["details_safe"]["planned_generation_calls"] == 5
    get_settings.cache_clear()


def test_create_generation_batch_rejects_call_budget_exceeded(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("AI_MAX_GENERATION_CALLS_PER_BATCH", "10")
    monkeypatch.setenv("AI_MAX_REVIEWER_CALLS_PER_BATCH", "25")
    get_settings.cache_clear()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)

    with pytest.raises(HTTPException) as exc:
        create_batch(
            GenerationBatchRequest(target_tags=["数论"], count=2, concurrency=1, candidate_count=5),
            BackgroundTasks(),
            run_now=False,
            db=db,
        )

    assert exc.value.status_code == 429
    assert exc.value.detail["error_code"] == "generation_call_budget_exceeded"
    assert exc.value.detail["details_safe"]["planned_generation_calls"] == 20
    get_settings.cache_clear()


def test_create_generation_batch_stores_set_blueprint_snapshot():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)

    response = create_batch(
        GenerationBatchRequest(
            target_tags=["method:counting", "task:proof"],
            difficulty_star=3,
            count=4,
            concurrency=1,
            candidate_count=1,
            use_mock=True,
            allow_reviewer_quality_downgrade=True,
        ),
        BackgroundTasks(),
        run_now=False,
        db=db,
    )
    batch = db.get(GenerationBatch, response["id"])
    blueprint = response["progress"]["batch_blueprint"]

    assert blueprint["schema_version"] == "generation_batch_blueprint_v1"
    assert blueprint["target_count"] == 4
    assert blueprint["star_distribution"] == {"3": 4}
    assert blueprint["method_diversity_min"] == 4
    assert blueprint["answer_type_limits"]["numeric_max"] == 3
    assert [slot["slot_id"] for slot in blueprint["slots"]] == ["slot-01", "slot-02", "slot-03", "slot-04"]
    assert batch.request["batch_blueprint"]["difficulty_curve"] == "same_star_warmup_to_challenge"
    assert batch.request["allow_reviewer_quality_downgrade"] is True


def test_generation_batch_assigns_blueprint_slots_to_items_and_jobs():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    response = create_batch(
        GenerationBatchRequest(
            target_tags=["method:counting", "task:proof"],
            difficulty_star=3,
            count=2,
            concurrency=1,
            candidate_count=1,
            prefer_reuse_existing_ai=False,
            use_mock=True,
        ),
        BackgroundTasks(),
        run_now=False,
        db=db,
    )

    result = process_generation_batch(db, response["id"], provider="mock", threaded=False)
    items = list(db.scalars(select(GenerationBatchItem).where(GenerationBatchItem.batch_id == response["id"]).order_by(GenerationBatchItem.item_index)))
    jobs = list(db.scalars(select(GenerationJob).order_by(GenerationJob.created_at, GenerationJob.id)))

    assert result["progress"]["blueprint_review"]["planned_slot_count"] == 2
    assert result["progress"]["blueprint_review"]["accepted_slot_count"] == 2
    assert result["progress"]["blueprint_review"]["missing_slot_ids"] == []
    assert [item.similarity_review["blueprint_slot_id"] for item in items] == ["slot-01", "slot-02"]
    assert [job.request["batch_blueprint_slot_id"] for job in jobs] == ["slot-01", "slot-02"]
    assert jobs[0].request["batch_blueprint_slot"]["slot_contract"]


def test_top_up_partial_batch_only_adds_missing_slot_and_preserves_accepted_item():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    response = create_batch(
        GenerationBatchRequest(
            target_tags=["method:counting", "task:proof"],
            difficulty_star=3,
            count=2,
            concurrency=1,
            candidate_count=1,
            prefer_reuse_existing_ai=False,
            use_mock=True,
        ),
        BackgroundTasks(),
        run_now=False,
        db=db,
    )
    batch = db.get(GenerationBatch, response["id"])
    slot1, slot2 = batch.request["batch_blueprint"]["slots"][:2]
    db.add(SourceFile(id="sf-existing", filename="existing.json", sha256="e" * 64, question_count=1))
    db.add(
        Question(
            id="existing-accepted",
            source_file_id="sf-existing",
            status="verified",
            source_name="AI生成题",
            publish_status="active_practice",
            eligible_for_practice=True,
            eligible_for_test=True,
            question_fault_status="none",
            stem_preview="一个正方形被分成四个小正方形，求阴影部分面积。",
            difficulty_star=3,
            premium=False,
        )
    )
    db.add(AnswerSpec(question_id="existing-accepted", status="verified", answer_type="integer", canonical_answer="4", normalized_spec={}, raw_answer_spec={}, verification={}))
    db.add(
        GenerationBatchItem(
            batch_id=batch.id,
            item_index=1,
            status="accepted",
            question_id="existing-accepted",
            similarity_review={"blueprint_slot_id": slot1["slot_id"], "blueprint_slot": {"slot_index": 1}},
        )
    )
    db.add(
        GenerationBatchItem(
            batch_id=batch.id,
            item_index=2,
            status="rejected",
            rejection_reason="no_candidate_accepted",
            similarity_review={"blueprint_slot_id": slot2["slot_id"], "blueprint_slot": {"slot_index": 2}},
        )
    )
    batch.status = "partial"
    batch.progress = {**(batch.progress or {}), "stage": "partial"}
    db.commit()

    queued = top_up_batch(batch.id, GenerationBatchTopUpRequest(run_now=False), BackgroundTasks(), db)
    top_up_plan = queued["progress"]["top_up"]["plan"]

    assert queued["status"] == "queued"
    assert top_up_plan["needed_accept_count"] == 1
    assert top_up_plan["planned_new_items"] == 2
    assert top_up_plan["missing_slot_ids"] == ["slot-02"]
    assert queued["progress"]["top_up"]["call_budget"]["planned_generation_calls"] == 2

    completed = process_generation_batch(db, batch.id, provider="mock", threaded=False)
    rows = list(db.scalars(select(GenerationBatchItem).where(GenerationBatchItem.batch_id == batch.id).order_by(GenerationBatchItem.item_index)))
    jobs = list(db.scalars(select(GenerationJob).order_by(GenerationJob.created_at, GenerationJob.id)))

    assert completed["status"] == "completed"
    assert completed["accepted_count"] == 2
    assert rows[0].question_id == "existing-accepted"
    accepted_top_up = [row for row in rows if row.item_index > 2 and row.status == "accepted"]
    assert len(accepted_top_up) == 1
    assert accepted_top_up[0].similarity_review["blueprint_slot_id"] == "slot-02"
    assert jobs[-1].request["batch_blueprint_slot_id"] == "slot-02"


def test_top_up_batch_rejects_when_slot_retry_attempt_limit_is_exhausted():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    response = create_batch(
        GenerationBatchRequest(target_tags=["method:counting"], difficulty_star=3, count=1, concurrency=1, candidate_count=1, use_mock=True),
        BackgroundTasks(),
        run_now=False,
        db=db,
    )
    batch = db.get(GenerationBatch, response["id"])
    db.add_all(
        [
            GenerationBatchItem(batch_id=batch.id, item_index=1, status="rejected", rejection_reason="no_candidate_accepted", similarity_review={"blueprint_slot_id": "slot-01"}),
            GenerationBatchItem(batch_id=batch.id, item_index=2, status="failed", rejection_reason="provider_failed", similarity_review={"blueprint_slot_id": "slot-01"}),
        ]
    )
    batch.status = "failed"
    batch.progress = {**(batch.progress or {}), "stage": "failed"}
    db.commit()

    with pytest.raises(HTTPException) as exc:
        top_up_batch(batch.id, GenerationBatchTopUpRequest(run_now=False), BackgroundTasks(), db)

    assert exc.value.status_code == 409
    assert exc.value.detail["error_code"] == "generation_batch_top_up_unavailable"
    assert exc.value.detail["details_safe"]["reason"] == "slot_retry_attempt_limit_exhausted"


def seed_ai_cost_log(db: Session, cost: float = 0.1) -> None:
    db.add(
        AiRunLog(
            role="generator",
            provider="openai_compatible",
            model="m",
            prompt_version="v1",
            input_hash="h",
            request_payload={},
            response_payload={},
            token_usage={"estimated_cost_usd": cost},
        )
    )
    db.commit()


def test_create_generation_job_rejects_real_provider_when_cost_budget_exceeded(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("AI_DAILY_BUDGET_USD", "0.1")
    monkeypatch.setenv("AI_MONTHLY_BUDGET_USD", "1.0")
    get_settings.cache_clear()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    seed_ai_cost_log(db, 0.1)

    with pytest.raises(HTTPException) as exc:
        create_job(GenerationJobRequest(target_tags=["数论"], difficulty_star=3, prefer_reuse_existing_ai=False), run_now=False, idempotency_key=None, db=db)

    assert exc.value.status_code == 429
    assert exc.value.detail["error_code"] == "ai_cost_budget_exceeded"
    assert "daily_budget_usd" in exc.value.detail["details_safe"]["exceeded"]
    assert db.scalar(select(GenerationJob)) is None
    get_settings.cache_clear()


def test_create_generation_job_rejects_when_planned_cost_would_exceed_budget(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("AI_DAILY_BUDGET_USD", "0.001")
    monkeypatch.setenv("AI_MONTHLY_BUDGET_USD", "1.0")
    monkeypatch.setenv("AI_MODEL_PRICE_JSON", '{"default":{"input":0.001,"output":0.002}}')
    get_settings.cache_clear()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)

    with pytest.raises(HTTPException) as exc:
        create_job(GenerationJobRequest(target_tags=["æ•°è®º"], difficulty_star=3, prefer_reuse_existing_ai=False), run_now=False, idempotency_key=None, db=db)

    details = exc.value.detail["details_safe"]
    assert exc.value.status_code == 429
    assert exc.value.detail["error_code"] == "ai_cost_budget_exceeded"
    assert "daily_budget_usd_planned" in details["exceeded"]
    assert details["planned_estimated_cost_usd"] > 0
    assert details["planned_cost"]["pricing_sources"][0]["source"] == "default"
    assert db.scalar(select(GenerationJob)) is None
    get_settings.cache_clear()


def test_create_generation_job_allows_cost_budget_overrun_with_explicit_confirmation(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("AI_DAILY_BUDGET_USD", "0.1")
    monkeypatch.setenv("AI_MONTHLY_BUDGET_USD", "1.0")
    get_settings.cache_clear()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    seed_ai_cost_log(db, 0.1)

    response = create_job(
        GenerationJobRequest(
            target_tags=["数论"],
            difficulty_star=3,
            prefer_reuse_existing_ai=False,
            allow_budget_overrun=True,
        ),
        run_now=False,
        idempotency_key=None,
        db=db,
    )
    job = db.scalar(select(GenerationJob))

    assert response.status == "queued"
    assert job is not None
    assert job.progress["cost_budget"]["ok"] is False
    assert job.progress["cost_budget"]["overrun_confirmed"] is True
    assert job.progress["cost_budget"]["blocking"] is False
    assert "daily_budget_usd" in job.progress["cost_budget"]["exceeded"]
    get_settings.cache_clear()


def test_runtime_cost_budget_marks_job_failed_before_publish(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("AI_DAILY_BUDGET_USD", "0.1")
    get_settings.cache_clear()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    future = (utc_now() + timedelta(seconds=60)).isoformat(timespec="seconds") + "Z"
    job = GenerationJob(
        id="runtime-budget-job",
        status="running",
        request={"job_provider": "openai_compatible"},
        progress={"stage": "reviewing_candidates", "deadline_at": future},
    )
    db.add(job)
    db.commit()
    seed_ai_cost_log(db, 0.1)

    reason = job_terminal_reason(db, job, get_settings(), job.request)
    saved = db.get(GenerationJob, job.id)

    assert reason == "ai_cost_budget_exceeded_runtime"
    assert saved.status == "failed"
    assert saved.progress["terminal_reason"] == "ai_cost_budget_exceeded_runtime"
    assert "daily_budget_usd_runtime" in saved.progress["runtime_cost_budget"]["exceeded"]
    get_settings.cache_clear()


def test_cost_budget_does_not_block_mock_generation(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("AI_DAILY_BUDGET_USD", "0.1")
    get_settings.cache_clear()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    seed_ai_cost_log(db, 0.1)

    response = create_job(GenerationJobRequest(target_tags=["数论"], difficulty_star=3, use_mock=True, prefer_reuse_existing_ai=False), run_now=False, idempotency_key=None, db=db)
    job = db.scalar(select(GenerationJob))

    assert response.status == "queued"
    assert job.progress["cost_budget"]["enforced"] is False
    assert job.progress["cost_budget"]["ok"] is True
    get_settings.cache_clear()


def test_cost_budget_does_not_block_fully_reused_ai_batch(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("AI_DAILY_BUDGET_USD", "0.1")
    get_settings.cache_clear()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    seed_ai_cost_log(db, 0.1)
    seed_reusable_ai_pool(db, count=2)

    response = create_batch(
        GenerationBatchRequest(target_tags=["method:target"], difficulty_star=3, count=2, concurrency=1, prefer_reuse_existing_ai=True),
        BackgroundTasks(),
        run_now=False,
        db=db,
    )

    assert response["progress"]["reusable_ai_prefetch_count"] == 2
    assert response["progress"]["cost_budget"]["enforced"] is False
    assert response["progress"]["cost_budget"]["ok"] is True
    get_settings.cache_clear()


def test_list_batches_returns_recent_batch_payloads():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    now = utc_now()
    first = GenerationBatch(id="batch-old", status="completed", request={}, target_count=1, concurrency=1, progress={}, accepted_count=1, created_at=now - timedelta(seconds=1))
    second = GenerationBatch(id="batch-new", status="running", request={}, target_count=2, concurrency=1, progress={}, accepted_count=0, created_at=now)
    db.add_all([first, second])
    db.commit()

    rows = list_batches(limit=10, db=db)

    assert [row["id"] for row in rows] == ["batch-new", "batch-old"]
    assert rows[0]["target_count"] == 2


def test_generation_batch_similarity_review_rejects_duplicate_item():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    batch = GenerationBatch(
        status="queued",
        request={"target_tags": ["数论"], "difficulty_star": 3, "count": 1, "solution_style": "elementary_preferred", "batch_provider": "mock"},
        target_count=1,
        concurrency=1,
        progress={},
    )
    db.add(batch)
    db.commit()

    process_generation_batch(db, batch.id, provider="mock", threaded=False)
    accepted = db.scalar(select(GenerationBatchItem).where(GenerationBatchItem.batch_id == batch.id, GenerationBatchItem.status == "accepted"))
    duplicate = GenerationBatchItem(batch_id=batch.id, item_index=99, status="accepted", question_id=accepted.question_id, similarity_review={})
    db.add(duplicate)
    db.commit()

    enforce_final_batch_diversity(db, batch)
    refresh_batch_counts(db, batch)
    db.commit()
    payload = batch_payload(db, batch)
    duplicate_row = db.get(GenerationBatchItem, duplicate.id)
    assert duplicate_row.status == "rejected"
    assert duplicate_row.rejection_reason == "near_duplicate_in_final_batch_review"
    assert payload["accepted_count"] == 1


def test_batch_practice_filters_out_needs_review_accepted_items():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf-review", filename="review.json", sha256="1" * 64, question_count=1)
    db.add(source)
    db.add(
        Question(
            id="review-q1",
            source_file_id=source.id,
            status="needs_review",
            source_name="review.json",
            stem_preview="needs review",
            difficulty_star=3,
            premium=False,
        )
    )
    db.add(AnswerSpec(question_id="review-q1", status="needs_review", answer_type="integer", canonical_answer="", normalized_spec={}, raw_answer_spec={}, verification={}))
    batch = GenerationBatch(status="completed", request={}, target_count=1, concurrency=1, accepted_count=1, progress={})
    db.add(batch)
    db.flush()
    db.add(GenerationBatchItem(batch_id=batch.id, item_index=1, status="accepted", question_id="review-q1", similarity_review={}))
    db.commit()

    with pytest.raises(Exception) as exc_info:
        create_or_update_batch_practice(batch.id, GenerationBatchPracticeRequest(), db)

    assert getattr(exc_info.value, "status_code", None) == 409


def test_cancel_batch_does_not_override_completed_target():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    batch = GenerationBatch(status="running", request={}, target_count=1, concurrency=1, progress={})
    db.add(batch)
    db.flush()
    db.add(GenerationBatchItem(batch_id=batch.id, item_index=1, status="accepted", question_id="q1", similarity_review={}))
    db.commit()

    payload = cancel_batch(batch.id, db)

    assert payload["status"] == "completed"
    assert payload["accepted_count"] == 1
    assert payload["progress"]["cancel_ignored"] == "target_already_reached"


def test_batch_payload_marks_running_batch_completed_when_target_reached():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    batch = GenerationBatch(status="running", request={}, target_count=1, concurrency=1, progress={"stage": "running"})
    db.add(batch)
    db.flush()
    db.add(GenerationBatchItem(batch_id=batch.id, item_index=1, status="accepted", question_id="q1", similarity_review={}))
    db.commit()

    payload = batch_payload(db, batch)

    assert payload["status"] == "completed"
    assert payload["accepted_count"] == 1
    assert payload["progress"]["stage"] == "completed"


def test_stale_running_generation_job_is_marked_timed_out():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    job = GenerationJob(status="running", request={}, progress={"deadline_at": "2000-01-01T00:00:00Z", "stage": "running"})
    db.add(job)
    db.commit()

    changed = mark_stale_job_if_needed(db, job)
    db.commit()
    db.refresh(job)

    assert changed is True
    assert job.status == "timed_out"
    assert job.progress["terminal_reason"] == "job_deadline_exceeded"


def test_active_generation_job_lease_blocks_second_worker():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    lease_expires = (utc_now() + timedelta(minutes=10)).isoformat(timespec="seconds") + "Z"
    job = GenerationJob(
        status="running",
        request={"target_tags": [], "difficulty_star": 3},
        progress={"stage": "generating_candidates", "lease_owner": "worker-a", "lease_expires_at": lease_expires, "state_version": 3},
    )
    db.add(job)
    db.commit()

    progress = process_generation_job(db, job.id, provider="mock")
    db.refresh(job)

    assert progress["terminal_reason"] == "lease_conflict_or_terminal"
    assert job.status == "running"
    assert job.progress["lease_owner"] == "worker-a"
    assert job.progress["lease_conflict_owner"].startswith(f"job-{job.id}-")
    assert db.scalar(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job.id)) is None


def test_cancel_batch_enters_cancelling_when_item_is_running():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    batch = GenerationBatch(status="running", request={}, target_count=1, concurrency=1, progress={})
    db.add(batch)
    db.flush()
    db.add(GenerationBatchItem(batch_id=batch.id, item_index=1, status="running", similarity_review={"lease_owner": "worker-a"}))
    db.commit()

    payload = cancel_batch(batch.id, db)
    db.refresh(batch)

    assert payload["status"] == "cancelling"
    assert batch.status == "cancelling"
    assert batch.progress["cancelling"] is True
    assert batch.progress["cancel_requested_at"]


def test_cancelled_batch_blocks_late_item_publish(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    batch = GenerationBatch(status="running", request={"target_tags": [], "difficulty_star": 3}, target_count=1, concurrency=1, progress={})
    db.add(batch)
    db.flush()
    item = GenerationBatchItem(batch_id=batch.id, item_index=1, status="queued", similarity_review={})
    db.add(item)
    db.commit()

    def fake_process_generation_job(db, job_id, provider="mock"):
        job = db.get(GenerationJob, job_id)
        running_batch = db.get(GenerationBatch, job.request["batch_id"])
        running_batch.status = "cancelled"
        job.status = "running"
        job.status = "accepted"
        job.accepted_question_id = "late-question"
        job.progress = {"stage": "done", "accepted": True, "question_id": "late-question"}
        db.commit()
        return job.progress

    monkeypatch.setattr("apps.api.app.routers.generation.process_generation_job", fake_process_generation_job)

    process_batch_item(db, item.id, provider="mock")
    db.refresh(item)
    db.refresh(batch)

    assert batch.status == "cancelled"
    assert item.status == "cancelled"
    assert item.question_id is None
    assert item.rejection_reason == "batch_cancelled"
