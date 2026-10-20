import logging
import os
import shutil
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.app.config import get_settings
from apps.api.app.structured_logging import configure_request_logger, log_request_entry, path_operation_context, recent_request_logs


TEST_WORK_ROOT = Path(__file__).resolve().parents[2] / ".test-work" / "structured-logging"


def make_work_root() -> Path:
    root = TEST_WORK_ROOT / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


def flush_logger(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        handler.flush()


def close_log_handlers_for(path: Path) -> None:
    logger = logging.getLogger("math_platform.api.requests")
    target = str(path.resolve())
    for handler in list(logger.handlers):
        if getattr(handler, "math_platform_log_path", None) == target:
            logger.removeHandler(handler)
            handler.close()


def test_structured_request_log_writes_jsonl_and_extracts_context():
    root = make_work_root()
    try:
        log_path = root / "api.jsonl"
        logger = configure_request_logger(path=log_path, max_bytes=4096, backup_count=1)

        log_request_entry(
            logger,
            {
                "event": "api_request",
                "trace_id": "trace-1",
                "method": "POST",
                "path": "/api/generation/batches/batch-1/cancel",
                "status_code": 409,
                "duration_ms": 12,
                "authorization": "Bearer sk-secret-value-1234567890",
                **path_operation_context("/api/generation/batches/batch-1/cancel"),
            },
        )
        flush_logger(logger)
        rows = recent_request_logs(log_path, limit=5)

        assert rows[0]["schema_version"] == "api_request_log_v1"
        assert rows[0]["level"] == "warning"
        assert rows[0]["trace_id"] == "trace-1"
        assert rows[0]["batch_id"] == "batch-1"
        assert "sk-secret" not in log_path.read_text(encoding="utf-8")
    finally:
        close_log_handlers_for(log_path)
        shutil.rmtree(root, ignore_errors=True)


def test_slow_request_logs_as_warning_and_retention_removes_old_rotated_logs():
    root = make_work_root()
    try:
        log_path = root / "api.jsonl"
        old_rotated = root / "api.jsonl.1"
        old_rotated.write_text("old\n", encoding="utf-8")
        old_time = time.time() - 3 * 86400
        os.utime(old_rotated, (old_time, old_time))

        logger = configure_request_logger(path=log_path, max_bytes=4096, backup_count=1, retention_days=1)
        log_request_entry(
            logger,
            {
                "event": "api_request",
                "trace_id": "trace-slow",
                "method": "GET",
                "path": "/api/reports/summary",
                "status_code": 200,
                "duration_ms": 2500,
                "duration_class": "slow",
                "slow_request": True,
                "slow_request_ms": 2000,
            },
        )
        flush_logger(logger)
        rows = recent_request_logs(log_path, limit=5)

        assert not old_rotated.exists()
        assert rows[0]["level"] == "warning"
        assert rows[0]["slow_request"] is True
        assert rows[0]["duration_class"] == "slow"
    finally:
        close_log_handlers_for(log_path)
        shutil.rmtree(root, ignore_errors=True)


def test_app_health_adds_trace_header_and_request_log(monkeypatch):
    root = make_work_root()
    try:
        db_path = root / "app.db"
        log_path = root / "logs" / "api.jsonl"
        monkeypatch.setenv("MATH_PLATFORM_ROOT", str(root))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("API_REQUEST_LOG_PATH", str(log_path))
        get_settings.cache_clear()

        from apps.api.app.main import create_app

        client = TestClient(create_app())
        response = client.get("/api/health", headers={"X-Request-Id": "trace-health"})
        missing = client.get("/api/missing", headers={"X-Request-Id": "trace-missing"})
        rows = recent_request_logs(log_path, limit=10)

        assert response.status_code == 200
        assert response.headers["X-Trace-Id"] == "trace-health"
        assert missing.status_code == 404
        assert missing.headers["X-Trace-Id"] == "trace-missing"
        assert missing.headers["X-Error-Code"] == "RESOURCE_NOT_FOUND"
        health_row = next(row for row in rows if row["trace_id"] == "trace-health" and row["path"] == "/api/health" and row["status_code"] == 200)
        assert health_row["app_version"] == "dev"
        assert health_row["duration_class"] in {"normal", "slow"}
        assert health_row["slow_request_ms"] == 2000
        assert any(row["trace_id"] == "trace-missing" and row["error_code"] == "RESOURCE_NOT_FOUND" and row["status_code"] == 404 for row in rows)
    finally:
        close_log_handlers_for(log_path)
        get_settings.cache_clear()
        shutil.rmtree(root, ignore_errors=True)
