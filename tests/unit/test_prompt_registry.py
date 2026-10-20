from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from pathlib import Path
import shutil
import pytest

from apps.api.app.db import Base
from apps.api.app.models import PromptTemplate
from apps.api.app.prompt_loader import prompt_template
from apps.api.app.localization import language_context
from apps.api.app.prompt_registry import activate_prompt_version, active_prompt_filename, load_active_prompt_map, scan_prompt_files, sync_prompt_registry


def test_prompt_registry_scans_hashes_and_upserts():
    prompt_dir = Path(".pytest_tmp_prompt_registry")
    if prompt_dir.exists():
        shutil.rmtree(prompt_dir)
    prompt_dir.mkdir()
    prompt_file = prompt_dir / "reviewer_v2.md"
    prompt_file.write_text("审核\n答案\n", encoding="utf-8")
    answer_judge_file = prompt_dir / "answer_judge_v1.md"
    answer_judge_file.write_text("复核\n答案\n", encoding="utf-8")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)

    scanned = scan_prompt_files(prompt_dir)
    synced = sync_prompt_registry(db, prompt_dir)
    db.commit()
    prompt_file.write_text("审核\n答案\ninvalid\n", encoding="utf-8")
    synced_again = sync_prompt_registry(db, prompt_dir)
    db.commit()
    rows = list(db.scalars(select(PromptTemplate)))

    roles = {row["version"]: row["role"] for row in scanned}
    assert roles["answer_judge_v1"] == "answer_judge"
    assert roles["reviewer_v2"] == "reviewer"
    assert len(synced) == 2
    assert len(synced_again) == 2
    assert len(rows) == 2
    reviewer = next(row for row in rows if row.version == "reviewer_v2")
    generator_contract = next(row for row in scanned if row["version"] == "reviewer_v2")["metadata_json"]
    assert reviewer.metadata_json["line_count"] == 4
    assert reviewer.metadata_json["schema_version"] == "prompt_metadata_v1"
    assert reviewer.metadata_json["input_schema_ref"] == "agent_input_envelope_v1#candidate_review_payload"
    assert reviewer.metadata_json["output_schema_ref"] == "review_decision_v1"
    assert reviewer.metadata_json["compatible_models"]["required_capabilities"] == ["json_object", "independent_reasoning"]
    assert reviewer.metadata_json["eval_baseline"]["suite"] == "ai_quality_suite_v1"
    assert reviewer.metadata_json["release"]["rollback_supported"] is True
    assert generator_contract["required_output_fields"] == ["status", "decision", "independent_answer", "issues"]
    shutil.rmtree(prompt_dir)


def test_prompt_registry_activation_writes_safe_active_map_and_preserves_statuses():
    prompt_dir = Path(".pytest_tmp_prompt_activation")
    if prompt_dir.exists():
        shutil.rmtree(prompt_dir)
    prompt_dir.mkdir()
    active_map_path = prompt_dir / "active_prompts.json"
    (prompt_dir / "generator_v1.md").write_text("old generator", encoding="utf-8")
    (prompt_dir / "generator_v2.md").write_text("new generator", encoding="utf-8")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)

    try:
        sync_prompt_registry(db, prompt_dir, active_map_path=active_map_path)
        db.commit()
        row, active_map = activate_prompt_version(db, "generator", "generator_v1", active_map_path=active_map_path)
        db.commit()
        sync_prompt_registry(db, prompt_dir, active_map_path=active_map_path)
        db.commit()
        rows = {row.version: row for row in db.scalars(select(PromptTemplate))}

        assert row.version == "generator_v1"
        assert active_map["prompts"]["generator"] == "generator_v1.md"
        assert load_active_prompt_map(active_map_path)["prompts"]["generator"] == "generator_v1.md"
        assert active_prompt_filename("generator_v2.md", active_map) == "generator_v1.md"
        assert rows["generator_v1"].status == "active"
        assert rows["generator_v2"].status == "available"
    finally:
        shutil.rmtree(prompt_dir, ignore_errors=True)


def test_prompt_loader_uses_active_prompt_map(monkeypatch):
    root = Path(".pytest_tmp_prompt_loader")
    prompt_dir = root / "prompts"
    if root.exists():
        shutil.rmtree(root)
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "generator_v1.md").write_text("active old", encoding="utf-8")
    (prompt_dir / "generator_v2.md").write_text("default new", encoding="utf-8")
    (prompt_dir / "active_prompts.json").write_text('{"schema_version":"active_prompts_v1","prompts":{"generator":"generator_v1.md"}}', encoding="utf-8")

    try:
        monkeypatch.setattr("apps.api.app.prompt_loader.project_root", lambda: root)
        monkeypatch.setattr("apps.api.app.prompt_registry.project_root", lambda: root)
        prompt_template.cache_clear()

        with language_context("zh-CN"):
            assert prompt_template("generator_v2.md") == "active old"
    finally:
        prompt_template.cache_clear()
        shutil.rmtree(root, ignore_errors=True)


def test_prompt_loader_blocks_when_active_prompt_file_is_missing(monkeypatch):
    root = Path(".pytest_tmp_prompt_loader_missing")
    prompt_dir = root / "prompts"
    if root.exists():
        shutil.rmtree(root)
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "generator_v2.md").write_text("default still works", encoding="utf-8")
    (prompt_dir / "active_prompts.json").write_text('{"schema_version":"active_prompts_v1","prompts":{"generator":"missing.md"}}', encoding="utf-8")

    try:
        monkeypatch.setattr("apps.api.app.prompt_loader.project_root", lambda: root)
        monkeypatch.setattr("apps.api.app.prompt_registry.project_root", lambda: root)
        prompt_template.cache_clear()

        with pytest.raises(ValueError, match="prompt_language_unavailable"):
            prompt_template("generator_v2.md")
    finally:
        prompt_template.cache_clear()
        shutil.rmtree(root, ignore_errors=True)
