import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.config import get_settings
from apps.api.app.models import AiRunLog, AuditLog, GenerationBatch, GenerationJob, Question, SourceFile
from apps.api.app.routers.admin import export_diagnostic_bundle


def test_diagnostic_bundle_exports_sanitized_operational_state(monkeypatch):
    monkeypatch.setenv("LIAOBOTS_API_KEY", "sk-should-not-appear-in-diagnostic")
    get_settings.cache_clear()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        db.add(SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=1))
        db.add(Question(id="q1", source_file_id="sf1", status="verified", source_name="source.json", stem_preview="题干", difficulty_star=3, premium=False))
        db.add(GenerationJob(id="job-failed", status="failed", request={"custom_description": "need help"}, progress={"terminal_reason": "provider_timeout"}))
        db.add(GenerationBatch(id="batch-cancelled", status="cancelled", request={}, target_count=3, concurrency=1, progress={"reason": "user_cancelled"}))
        db.add(
            AiRunLog(
                id="log1",
                job_id="job-failed",
                role="generator",
                provider="openai_compatible",
                model="gpt-test",
                prompt_version="generator_v2",
                input_hash="h",
                request_payload={"headers": {"Authorization": "Bearer sk-should-not-appear-in-diagnostic"}, "trace": {"trace_id": "trace-ai", "span_id": "span-ai", "parent_span_id": "span-root", "operation": "generation_strategy.mock.attempt_1"}},
                response_payload={"text": "not exported"},
                error="read timeout",
                latency_ms=1000,
                token_usage={"input_tokens": 1, "output_tokens": 2},
            )
        )
        db.commit()

        bundle = export_diagnostic_bundle(db)
        serialized = json.dumps(bundle, ensure_ascii=False)

        assert bundle["schema_version"] == "math_platform_api_diagnostic_bundle_v1"
        assert bundle["release"]["schema_version"] == "release_info_v1"
        assert bundle["paths"]["release_manifest_exists"] == bundle["release"]["manifest_exists"]
        assert "ok" in bundle["release"]["verification"]
        assert bundle["release"]["associations"]["schema_version"] == "release_manifest_associations_v1"
        assert "next_actions" in bundle["release"]["associations"]
        assert bundle["database"]["table_counts"]["questions"] == 1
        assert bundle["database"]["integrity"]["checked"] is True
        assert bundle["generation"]["jobs"][0]["id"] == "job-failed"
        assert bundle["generation"]["batches"][0]["id"] == "batch-cancelled"
        assert bundle["recent_ai_logs"][0]["has_error"] is True
        assert bundle["recent_ai_logs"][0]["trace_id"] == "trace-ai"
        assert bundle["recent_ai_logs"][0]["span_id"] == "span-ai"
        assert bundle["recent_request_logs"] == []
        assert bundle["config"]["request_logging"]["path"]
        assert bundle["config"]["request_logging"]["retention_days"] == 14
        assert bundle["config"]["request_logging"]["slow_request_ms"] == 2000
        assert "recent_api_request_log_summaries" in bundle["included"]
        assert "sk-should-not-appear-in-diagnostic" not in serialized
        assert "Bearer sk-" not in serialized
        assert bundle["redaction_scan"]["ok"] is True
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "admin.diagnostic_bundle_exported"))
        assert audit is not None
        assert audit.payload["redaction_scan_ok"] is True
    finally:
        get_settings.cache_clear()
