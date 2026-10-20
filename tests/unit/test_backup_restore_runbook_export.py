import json
import shutil
import uuid
from pathlib import Path

from scripts.dev.export_backup_restore_runbook import (
    check_backup_restore_runbook,
    export_backup_restore_runbook,
    render_backup_restore_runbook,
    render_backup_restore_runbook_markdown,
)


def _make_test_root(name: str) -> Path:
    root = Path.cwd() / "tests" / "runtime_tmp" / f"{name}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_render_backup_restore_runbook_tracks_launcher_contract():
    payload = render_backup_restore_runbook()
    launcher = payload["launcher"]
    entrypoint_commands = {row["command"] for row in payload["entrypoints"]}

    assert payload["schema_version"] == "backup_restore_runbook_v1"
    assert launcher["backup_manifest_version"] == "math_platform_backup_v1"
    assert launcher["database_relative_path"] == "data/math_platform_dev.db"
    assert "practice_sessions" in launcher["critical_tables"]
    assert "api_key" in launcher["sensitive_key_markers"]
    assert r"launcher\\MathPlatform.exe backup --json" in entrypoint_commands


def test_render_backup_restore_runbook_documents_safety_and_release_scripts():
    payload = render_backup_restore_runbook()
    safety = "\n".join(payload["safety_gates"])
    restore_steps = {row["name"]: row for row in payload["restore_workflow"]}
    errors = {row["error_code"] for row in payload["error_codes"]}

    assert payload["release_script_checks"]["ok"] is True
    assert payload["release_script_checks"]["writes_backup_cmd"] is True
    assert payload["release_script_checks"]["writes_restore_cmd"] is True
    assert "dry-run" in safety
    assert "backup_id" in restore_steps["confirm backup id"]["details"]
    assert {"services_running", "confirmation_required", "post_restore_validation_failed"}.issubset(errors)


def test_render_backup_restore_runbook_markdown_contains_operational_steps():
    markdown = render_backup_restore_runbook_markdown(render_backup_restore_runbook())

    assert "# 备份与恢复 Runbook" in markdown
    assert "`backup.cmd`" in markdown
    assert "`restore.cmd backups\\\\backup-YYYYMMDD-HHMMSS`" in markdown
    assert "SQLite online backup API" in markdown
    assert "`confirmation_required`" in markdown
    assert "## Manifest Schema" in markdown


def test_export_backup_restore_runbook_and_check():
    root = _make_test_root("backup-restore-runbook")
    json_output = root / "backup_restore_runbook.json"
    md_output = root / "backup_restore_runbook.md"
    try:
        export_backup_restore_runbook(json_output=json_output, md_output=md_output)

        payload = json.loads(json_output.read_text(encoding="utf-8"))
        assert payload["release_script_checks"]["ok"] is True
        assert check_backup_restore_runbook(json_output=json_output, md_output=md_output) is True

        md_output.write_text("stale\n", encoding="utf-8")
        assert check_backup_restore_runbook(json_output=json_output, md_output=md_output) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)
