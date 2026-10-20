"""Opt-in acceptance-run budget at the external HTTP inference boundary.

The production default is disabled. Only the isolated acceptance launcher sets
MATH_ACCEPTANCE_BUDGET_FILE. No request bodies, credentials, or URLs are stored.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

_role: ContextVar[str] = ContextVar("acceptance_provider_role", default="connection_test")


class AcceptanceBudgetExceeded(RuntimeError):
    pass


@contextmanager
def provider_role(role: str):
    token = _role.set(role)
    try:
        yield
    finally:
        _role.reset(token)


def _database() -> sqlite3.Connection | None:
    configured = os.getenv("MATH_ACCEPTANCE_BUDGET_FILE", "").strip()
    if not configured:
        return None
    path = Path(configured).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY CHECK(id=1), started REAL NOT NULL, deadline REAL NOT NULL, call_limit INTEGER NOT NULL, stopped INTEGER NOT NULL DEFAULT 0)")
    db.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, created REAL NOT NULL, role TEXT NOT NULL, model TEXT NOT NULL, language TEXT NOT NULL, status TEXT NOT NULL, elapsed_ms INTEGER, usage_json TEXT, error_code TEXT)")
    return db


def reserve(model: str, language: str) -> tuple[str, float] | None:
    db = _database()
    if db is None:
        return None
    try:
        db.execute("BEGIN IMMEDIATE")
        now = time.time()
        limit = min(160, max(1, int(os.getenv("MATH_ACCEPTANCE_CALL_LIMIT", "160"))))
        duration = min(5400, max(1, int(os.getenv("MATH_ACCEPTANCE_SECONDS", "5400"))))
        db.execute("INSERT OR IGNORE INTO budget(id,started,deadline,call_limit) VALUES(1,?,?,?)", (now, now + duration, limit))
        started, deadline, stored_limit, stopped = db.execute("SELECT started,deadline,call_limit,stopped FROM budget WHERE id=1").fetchone()
        count = db.execute("SELECT count(*) FROM attempts").fetchone()[0]
        if stopped or now < started - 1 or now >= deadline or count >= min(limit, stored_limit):
            db.execute("UPDATE budget SET stopped=1 WHERE id=1")
            db.commit()
            raise AcceptanceBudgetExceeded("acceptance_budget_exhausted: no further provider requests are permitted")
        attempt_id = uuid4().hex
        db.execute("INSERT INTO attempts(id,created,role,model,language,status) VALUES(?,?,?,?,?,?)", (attempt_id, now, _role.get(), model[:160], language, "reserved"))
        db.commit()
        return attempt_id, deadline
    finally:
        db.close()


def _finish(ticket: tuple[str, float] | None, status: str, elapsed: float, usage: Any = None, error_code: str | None = None) -> None:
    if ticket is None:
        return
    safe_usage = None
    if isinstance(usage, dict):
        allowed = ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens", "promptTokenCount", "candidatesTokenCount")
        safe_usage = {key: value for key, value in usage.items() if key in allowed and isinstance(value, (int, float))}
    db = _database()
    if db is None:
        raise AcceptanceBudgetExceeded("acceptance_budget_configuration_changed")
    try:
        db.execute("UPDATE attempts SET status=?,elapsed_ms=?,usage_json=?,error_code=? WHERE id=?", (status, int(elapsed * 1000), json.dumps(safe_usage) if safe_usage is not None else None, error_code, ticket[0]))
    finally:
        db.close()


def budgeted_post(url: str, **kwargs: Any) -> httpx.Response:
    from .localization import current_language

    payload = kwargs.get("json") or {}
    model = str(payload.get("model") or "provider_selected") if isinstance(payload, dict) else "unknown"
    ticket = reserve(model, current_language())
    started = time.perf_counter()
    if ticket is not None:
        remaining = ticket[1] - time.time()
        if remaining <= 0:
            _finish(ticket, "not_sent", 0, error_code="deadline")
            raise AcceptanceBudgetExceeded("acceptance_deadline_reached")
        configured_timeout = kwargs.get("timeout", 180)
        if isinstance(configured_timeout, (int, float)):
            kwargs["timeout"] = max(0.001, min(float(configured_timeout), 180, remaining))
        else:
            kwargs["timeout"] = max(0.001, min(180, remaining))
    try:
        response = httpx.post(url, **kwargs)
    except Exception as exc:
        _finish(ticket, "transport_error", time.perf_counter() - started, error_code=type(exc).__name__)
        raise
    usage = None
    try:
        raw = response.json()
        if isinstance(raw, dict):
            usage = raw.get("usage") or raw.get("usageMetadata")
    except (ValueError, TypeError):
        pass
    elapsed = time.perf_counter() - started
    _finish(ticket, "response_received" if response.is_success else "http_error", elapsed, usage, None if response.is_success else str(response.status_code))
    if ticket is not None:
        db = _database()
        try:
            stopped = db.execute("SELECT stopped FROM budget WHERE id=1").fetchone()[0] if db is not None else 1
        finally:
            if db is not None:
                db.close()
        if stopped or time.time() >= ticket[1]:
            raise AcceptanceBudgetExceeded("acceptance_late_response_quarantined")
    return response
