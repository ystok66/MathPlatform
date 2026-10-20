from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.app.ai_gateway import process_generation_job
from apps.api.app.db import Base
from apps.api.app.generation_events import persist_generation_event
from apps.api.app.models import AiRunLog, AnswerSpec, GeneratedCandidate, GenerationBatch, GenerationBatchItem, GenerationEvent, GenerationJob, Question, SourceFile, utc_now
from apps.api.app.routers.generation import batch_sse_events, job_sse_events, sse_format, sse_should_emit


def test_batch_sse_events_replay_after_last_event_id():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    now = utc_now() - timedelta(minutes=1)
    batch = GenerationBatch(id="sse-batch", status="running", request={}, target_count=2, concurrency=1, progress={}, created_at=now, updated_at=now)
    db.add(batch)
    db.flush()
    db.add_all(
        [
            GenerationBatchItem(
                id="item-1",
                batch_id=batch.id,
                item_index=1,
                status="accepted",
                question_id="q1",
                similarity_review={"source_type": "reused_ai"},
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=1),
            ),
            GenerationBatchItem(
                id="item-2",
                batch_id=batch.id,
                item_index=2,
                status="running",
                similarity_review={},
                created_at=now + timedelta(seconds=2),
                updated_at=now + timedelta(seconds=2),
            ),
        ]
    )
    db.commit()

    events = batch_sse_events(db, batch)
    first_event_id = events[0]["id"]
    replayed = batch_sse_events(db, batch, first_event_id)

    assert [event["event"] for event in events].count("batch_item") == 2
    assert events[-1]["event"] == "batch"
    assert all(sse_should_emit(event["id"], first_event_id) for event in replayed)
    assert first_event_id not in {event["id"] for event in replayed}


def test_batch_sse_events_emit_terminal_done_after_snapshot():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    now = utc_now() - timedelta(minutes=1)
    batch = GenerationBatch(id="terminal-batch", status="running", request={}, target_count=1, concurrency=1, progress={}, created_at=now, updated_at=now)
    db.add(batch)
    db.flush()
    db.add(
        GenerationBatchItem(
            id="terminal-item",
            batch_id=batch.id,
            item_index=1,
            status="accepted",
            question_id="q1",
            similarity_review={},
            created_at=now + timedelta(seconds=1),
            updated_at=now + timedelta(seconds=1),
        )
    )
    db.commit()

    events = batch_sse_events(db, batch)
    replayed = batch_sse_events(db, batch, events[-2]["id"])

    assert [event["event"] for event in events][-2:] == ["batch", "done"]
    assert [event["event"] for event in replayed] == ["done"]


def test_job_sse_events_include_candidates_logs_and_replay_filter():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    now = utc_now() - timedelta(minutes=1)
    job = GenerationJob(id="sse-job", status="accepted", request={}, progress={"stage": "done"}, accepted_question_id="q1", created_at=now, updated_at=now + timedelta(seconds=3))
    db.add(job)
    db.add(
        GeneratedCandidate(
            id="cand-1",
            job_id=job.id,
            strategy="structural_elegance",
            status="accepted",
            payload={},
            review={},
            published_at=now + timedelta(seconds=1),
        )
    )
    db.add(
        AiRunLog(
            id="log-1",
            job_id=job.id,
            role="generator",
            provider="mock",
            model="mock",
            prompt_version="v1",
            input_hash="hash",
            request_payload={},
            response_payload={},
            token_usage={},
            created_at=now + timedelta(seconds=2),
        )
    )
    db.commit()

    events = job_sse_events(db, job)
    replayed = job_sse_events(db, job, events[1]["id"])

    assert [event["event"] for event in events] == ["candidate", "ai_log", "job", "done"]
    assert [event["event"] for event in replayed] == ["job", "done"]
    assert "id: " in sse_format("job", events[2]["data"], events[2]["id"])
    assert "event: job" in sse_format("job", events[2]["data"], events[2]["id"])


def test_batch_sse_events_include_persisted_events():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    now = utc_now() - timedelta(minutes=1)
    batch = GenerationBatch(id="stored-batch", status="running", request={}, target_count=1, concurrency=1, progress={}, created_at=now, updated_at=now)
    db.add(batch)
    db.flush()
    persist_generation_event(
        db,
        "batch",
        batch.id,
        "batch_item",
        {"id": "stored-item", "batch_id": batch.id, "status": "running", "marker": "persisted"},
        group=20,
        status="running",
        index=1,
        occurred_at=now + timedelta(seconds=1),
    )
    db.commit()

    events = batch_sse_events(db, batch)

    assert any(event["event"] == "batch_item" and event["data"].get("marker") == "persisted" for event in events)


def test_job_sse_events_include_persisted_events():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    now = utc_now() - timedelta(minutes=1)
    job = GenerationJob(id="stored-job", status="running", request={}, progress={"stage": "generating"}, created_at=now, updated_at=now)
    db.add(job)
    db.flush()
    persist_generation_event(
        db,
        "job",
        job.id,
        "job",
        {"id": job.id, "status": "running", "marker": "persisted"},
        group=40,
        status="running",
        occurred_at=now + timedelta(seconds=1),
    )
    db.commit()

    events = job_sse_events(db, job)

    assert any(event["event"] == "job" and event["data"].get("marker") == "persisted" for event in events)


def test_process_generation_job_persists_sse_event_rows():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=1)
    db.add(source)
    db.add(Question(id="q1", source_file_id=source.id, status="verified", source_name="source.json", stem_preview="两位数问题", difficulty_star=3, premium=False))
    db.add(AnswerSpec(question_id="q1", status="verified", answer_type="integer", canonical_answer="64", normalized_spec={}, raw_answer_spec={}, verification={}))
    job = GenerationJob(
        id="persisted-job",
        status="queued",
        request={"target_tags": ["数论"], "difficulty_star": 3, "candidate_count": 1, "source_example_count": 0, "ai_negative_example_count": 0},
        progress={},
    )
    db.add(job)
    db.commit()

    process_generation_job(db, job.id, provider="mock")

    events = list(db.scalars(select(GenerationEvent).where(GenerationEvent.stream_type == "job", GenerationEvent.stream_id == job.id)))
    event_types = {event.event_type for event in events}
    assert {"job", "candidate", "ai_log", "done"}.issubset(event_types)
