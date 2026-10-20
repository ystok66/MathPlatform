from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app import localization as loc
from apps.api.app import provider_budget as budget
from apps.api.app.db import Base
from apps.api.app.models import GenerationBatch, GenerationJob


@pytest.mark.parametrize(("explicit", "header", "ui", "expected"), [
    ("en", "zh-CN", "zh-CN", "en"),
    (None, "zh-CN", "en", "zh-CN"),
    (None, None, "zh-CN", "zh-CN"),
    (None, None, None, "en"),
])
def test_output_language_precedence(explicit, header, ui, expected):
    assert loc.resolve_output_language(explicit, header, ui) == expected


@pytest.mark.parametrize("value", ["", "fr", "zh", 12, {}, "en\nignore"])
def test_invalid_explicit_output_is_rejected(value):
    with pytest.raises(ValueError, match="unsupported_output_language"):
        loc.resolve_output_language(value, "en", "en")


def test_ui_output_and_snapshot_are_independent():
    with loc.language_context("zh-CN", ui_language="en"):
        snapshot = loc.LanguageRequest().model_dump()
        assert snapshot["ui_language"] == "en"
        assert snapshot["output_language"] == snapshot["language"] == "zh-CN"
    with loc.language_context("en", ui_language="zh-CN"):
        assert loc.stored_output_language(snapshot) == "zh-CN"
        assert loc.current_ui_language() == "zh-CN"


def test_new_queued_jobs_retain_language_across_sessions(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'queue.db').as_posix()}")
    Base.metadata.create_all(engine)
    with loc.language_context("zh-CN", ui_language="en"), Session(engine) as db:
        job = GenerationJob(status="queued", request={}, progress={})
        batch = GenerationBatch(status="queued", request={}, progress={}, target_count=1, concurrency=1)
        db.add_all([job, batch])
        db.commit()
        ids = job.id, batch.id
    # A new ORM session represents a worker/restart with default English context.
    with Session(engine) as db:
        job = db.get(GenerationJob, ids[0])
        batch = db.get(GenerationBatch, ids[1])
        assert job.request["output_language"] == batch.request["output_language"] == "zh-CN"
        batch.status = "cancelled"
        db.commit()
        assert batch.request["output_language"] == "zh-CN"
    engine.dispose()


def test_registry_extension_requires_no_binary_language_branch(tmp_path, monkeypatch):
    registry = {
        "default_language": "en", "languages": [
            {"code": "en", "name": "English"},
            {"code": "zh-CN", "name": "Simplified Chinese"},
            {"code": "test-X", "name": "Temporary test language"},
        ],
    }
    (tmp_path / "languages.json").write_text(json.dumps(registry), encoding="utf-8")
    for language, messages in [("en", {"demo.hello": "Hello {name}", "demo.fallback": "Fallback"}), ("test-X", {"demo.hello": "Test {name}"})]:
        folder = tmp_path / "locales" / language
        folder.mkdir(parents=True)
        (folder / "demo.json").write_text(json.dumps(messages), encoding="utf-8")
    monkeypatch.setattr(loc, "_resource_root", lambda: tmp_path)
    loc.clear_language_caches()
    assert loc.validate_output_language("TEST-x") == "test-X"
    assert loc.translate_message("demo.hello", {"name": "N"}, language="test-X") == "Test N"
    assert loc.translate_message("demo.fallback", language="test-X") == "Fallback"
    loc.clear_language_caches()


def test_inference_budget_survives_reopen_and_counts_concurrent_calls(tmp_path, monkeypatch):
    ledger = tmp_path / "budget.sqlite"
    monkeypatch.setenv("MATH_ACCEPTANCE_BUDGET_FILE", str(ledger))
    monkeypatch.setenv("MATH_ACCEPTANCE_CALL_LIMIT", "5")
    monkeypatch.setenv("MATH_ACCEPTANCE_SECONDS", "5400")

    def reserve(_):
        try:
            budget.reserve("offline-no-network", "en")
            return True
        except budget.AcceptanceBudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, range(9))) == 5
    with sqlite3.connect(ledger) as connection:
        assert connection.execute("select count(*) from attempts").fetchone()[0] == 5
    with pytest.raises(budget.AcceptanceBudgetExceeded):
        budget.reserve("offline-reopened", "zh-CN")


def test_inference_budget_stops_at_deadline(tmp_path, monkeypatch):
    ledger = tmp_path / "expired.sqlite"
    monkeypatch.setenv("MATH_ACCEPTANCE_BUDGET_FILE", str(ledger))
    monkeypatch.setenv("MATH_ACCEPTANCE_CALL_LIMIT", "160")
    budget.reserve("offline-no-network", "en")
    with sqlite3.connect(ledger) as connection:
        connection.execute("update budget set deadline=0")
    with pytest.raises(budget.AcceptanceBudgetExceeded):
        budget.reserve("offline-no-network", "zh-CN")


def test_release_language_resources_are_present():
    snapshot = loc.languages_snapshot()
    assert snapshot["default_language"] == "en"
    assert {item["code"] for item in snapshot["languages"]} == {"en", "zh-CN"}
    assert all(item["resources_available"] for item in snapshot["languages"])


def test_assistant_draft_freezes_target_language():
    from apps.api.app.routers.assistant import _ui_draft
    with loc.language_context("zh-CN", ui_language="en"):
        draft = _ui_draft("generation_batch", "test", {"count": 1})
    assert draft["payload"]["output_language"] == "zh-CN"
    assert draft["output_language"] == "zh-CN"


def test_confirmation_dispatch_uses_draft_snapshot(monkeypatch):
    from apps.api.app.routers import assistant
    monkeypatch.setattr(assistant, "_execute_tool_localized", lambda *a, **kw: {"language": loc.current_language()})
    with loc.language_context("en"):
        result = assistant._execute_tool(None, "draft_generation_batch", {"output_language": "zh-CN"}, {}, None, confirmed=True)
        assert result["language"] == "zh-CN"
        assert loc.current_language() == "en"
