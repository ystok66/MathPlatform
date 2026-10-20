from __future__ import annotations

import json
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from starlette.requests import Request

from .audit import sanitize_payload
from .config import project_root


REQUEST_LOGGER_NAME = "math_platform.api.requests"
REQUEST_LOG_SCHEMA_VERSION = "api_request_log_v1"
DEFAULT_REQUEST_LOG_RELATIVE_PATH = Path("logs") / "api.jsonl"


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = record.msg if isinstance(record.msg, dict) else {"message": record.getMessage()}
        row = {
            "schema_version": REQUEST_LOG_SCHEMA_VERSION,
            "level": record.levelname.lower(),
            **payload,
        }
        return json.dumps(sanitize_payload(row), ensure_ascii=False, sort_keys=True, default=str)


def request_log_path(configured_path: str | None = None) -> Path:
    if configured_path:
        path = Path(configured_path)
        return path if path.is_absolute() else project_root() / path
    return project_root() / DEFAULT_REQUEST_LOG_RELATIVE_PATH


def request_log_files_for_retention(target: Path) -> list[Path]:
    parent = target.parent
    if not parent.exists():
        return []
    escaped = target.name.replace("[", "[[]")
    return sorted(parent.glob(f"{escaped}*"))


def cleanup_old_request_logs(target: Path, retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    cutoff = time.time() - (retention_days * 86400)
    removed = 0
    for path in request_log_files_for_retention(target):
        if path == target or not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def configure_request_logger(
    *,
    path: Path | None = None,
    max_bytes: int = 1_048_576,
    backup_count: int = 5,
    retention_days: int = 14,
) -> logging.Logger:
    target = path or request_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    cleanup_old_request_logs(target, retention_days)
    logger = logging.getLogger(REQUEST_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    target_key = str(target.resolve())
    for handler in logger.handlers:
        if getattr(handler, "math_platform_log_path", None) == target_key:
            return logger
    handler = RotatingFileHandler(target, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    handler.setFormatter(JsonLineFormatter())
    handler.math_platform_log_path = target_key  # type: ignore[attr-defined]
    logger.addHandler(handler)
    return logger


def trace_id_for_request(request: Request) -> str:
    supplied = request.headers.get("x-request-id") or request.headers.get("x-trace-id")
    return supplied.strip()[:128] if supplied and supplied.strip() else uuid4().hex


def path_operation_context(path: str) -> dict[str, str]:
    parts = [part for part in path.split("/") if part]
    context: dict[str, str] = {}
    for index, part in enumerate(parts):
        next_part = parts[index + 1] if index + 1 < len(parts) else ""
        if part == "batches" and next_part:
            context["batch_id"] = next_part
        elif part == "jobs" and next_part:
            context["job_id"] = next_part
        elif part == "sessions" and next_part:
            context["session_id"] = next_part
        elif part == "questions" and next_part:
            context["question_id"] = next_part
    return context


def request_log_entry(
    *,
    request: Request,
    trace_id: str,
    status_code: int,
    duration_ms: int,
    slow_request_ms: int = 2000,
    app_version: str | None = None,
    error_code: str | None = None,
    error_class: str | None = None,
) -> dict[str, Any]:
    slow_threshold = max(1, int(slow_request_ms or 1))
    is_slow = duration_ms >= slow_threshold
    return {
        "event": "api_request",
        "trace_id": trace_id,
        "app_version": app_version,
        "method": request.method,
        "path": request.url.path,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "duration_class": "slow" if is_slow else "normal",
        "slow_request": is_slow,
        "slow_request_ms": slow_threshold,
        "client_host": request.client.host if request.client else None,
        "user_agent_family": (request.headers.get("user-agent") or "").split("/", 1)[0][:80] or None,
        "error_code": error_code,
        "error_class": error_class,
        **path_operation_context(request.url.path),
    }


def log_request_entry(logger: logging.Logger, entry: dict[str, Any]) -> None:
    level = (
        logging.ERROR
        if int(entry.get("status_code") or 0) >= 500
        else logging.WARNING
        if int(entry.get("status_code") or 0) >= 400 or entry.get("slow_request")
        else logging.INFO
    )
    logger.log(level, entry)


def elapsed_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))


def recent_request_logs(path: Path | None = None, limit: int = 50) -> list[dict[str, Any]]:
    target = path or request_log_path()
    if not target.exists():
        return []
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    rows: list[dict[str, Any]] = []
    for line in reversed(lines[-max(limit * 3, limit) :]):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(sanitize_payload(payload))
        if len(rows) >= limit:
            break
    return rows
