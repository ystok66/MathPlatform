from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.audit import sanitize_payload
from apps.api.app.ai_providers import ProviderResult
from apps.api.app.ai_gateway import add_ai_logs
from apps.api.app.db import Base
from apps.api.app.models import AiRunLog, AnswerSpec, AuditLog, PracticeSession, Question, SourceFile
from apps.api.app.routers.generation import cancel_job, create_job
from apps.api.app.routers.reports import ReportExportRequest, export_report
from apps.api.app.schemas import GenerationJobRequest


def seeded_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=1)
    db.add(source)
    db.add(
        Question(
            id="q1",
            source_file_id="sf1",
            status="verified",
            source_name="source.json",
            stem_preview="题目",
            difficulty_star=3,
            premium=False,
        )
    )
    db.add(
        AnswerSpec(
            question_id="q1",
            status="verified",
            answer_type="integer",
            canonical_answer="1",
            normalized_spec={},
            raw_answer_spec={},
            verification={},
        )
    )
    db.add(PracticeSession(id="s1", mode="practice", config={}, question_ids=["q1"]))
    db.commit()
    return db


def test_audit_payload_sanitizes_secret_values():
    payload = sanitize_payload({"api_key": "secret", "Authorization": "Bearer abc", "headers": {"x-api-key": "k", "safe": 1}, "nested": {"token": "abc", "safe": 1}})

    assert payload == {"api_key": "***", "Authorization": "***", "headers": {"x-api-key": "***", "safe": 1}, "nested": {"token": "***", "safe": 1}}


def test_ai_run_logs_sanitize_request_and_response_payloads():
    db = seeded_db()

    add_ai_logs(
        db,
        "job1",
        "generator",
        "hash",
        {"messages": [], "headers": {"Authorization": "Bearer secret"}, "api_key": "secret"},
        [ProviderResult(provider="mock", model="mock", role="generator", payload={}, raw_response={"x-api-key": "secret", "ok": True}, latency_ms=1, error=None)],
    )
    db.commit()
    row = db.query(AiRunLog).filter(AiRunLog.role == "generator").one()

    assert row.request_payload["headers"]["Authorization"] == "***"
    assert row.request_payload["api_key"] == "***"
    assert row.response_payload["x-api-key"] == "***"
    assert row.response_payload["ok"] is True


def test_generation_and_report_routes_write_audit_logs():
    db = seeded_db()

    job = create_job(GenerationJobRequest(target_tags=["数论"], difficulty_star=3, count=1), run_now=False, idempotency_key=None, db=db)
    cancel_job(job.id, db)
    export_report(ReportExportRequest(filters={"source_name": "source.json"}), db)
    logs = list(db.scalars(select(AuditLog).order_by(AuditLog.created_at, AuditLog.action)))

    assert {row.action for row in logs} == {"generation.job_created", "generation.job_cancelled", "reports.export_created"}
    assert all(row.actor == "local_user" for row in logs)
    assert logs[0].payload
