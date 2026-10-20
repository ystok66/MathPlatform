from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.health import health_detail


def make_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_health_detail_reports_degraded_optional_provider_without_secret():
    db = make_db()
    settings = SimpleNamespace(
        model_provider="openai_compatible",
        default_model="m",
        liaobots_base_url="https://ai.example/v1",
        liaobots_api_key=None,
        responses_api_key=None,
        responses_base_url="",
        anthropic_api_key=None,
        anthropic_base_url="",
        gemini_api_key=None,
        gemini_base_url="",
        database_url="sqlite:///:memory:",
        redis_url="redis://localhost:6379/0",
        cors_origins="http://127.0.0.1:3000",
    )

    report = health_detail(db, Path("."), settings)

    assert report["schema_version"] == "health_detail_v1"
    assert report["checks"]["liveness"]["ok"] is True
    assert report["checks"]["readiness"]["database"]["ok"] is True
    assert report["checks"]["degraded"]["provider"]["status"] == "degraded"
    assert report["checks"]["degraded"]["provider"]["api_key_configured"] is False
    assert report["checks"]["degraded"]["provider"]["base_url_configured"] is True
    assert report["checks"]["degraded"]["release"]["associations"]["schema_version"] == "release_manifest_associations_v1"
    assert "problem_counts" in report["checks"]["degraded"]["release"]["associations"]
    assert "liaobots_api_key" not in str(report)


def test_health_detail_reports_provider_ready_without_network_probe():
    db = make_db()
    settings = SimpleNamespace(
        model_provider="openai_compatible",
        default_model="m",
        liaobots_base_url="https://ai.example/v1",
        liaobots_api_key="secret-value",
        responses_api_key=None,
        responses_base_url="",
        anthropic_api_key=None,
        anthropic_base_url="",
        gemini_api_key=None,
        gemini_base_url="",
        database_url="sqlite:///:memory:",
        redis_url="redis://localhost:6379/0",
        cors_origins="http://127.0.0.1:3000",
    )

    report = health_detail(db, Path("."), settings)

    assert report["checks"]["degraded"]["provider"]["status"] == "ok"
    assert report["checks"]["degraded"]["provider"]["network_probe"] == "not_run"
    assert report["checks"]["degraded"]["provider"]["capabilities"]["supports_health_check"] is True
    assert report["checks"]["degraded"]["release"]["associations"]["schema_version"] == "release_manifest_associations_v1"
    assert "secret-value" not in str(report)
