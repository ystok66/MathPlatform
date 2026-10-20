import json
import shutil
import uuid
from pathlib import Path

from scripts.dev.export_db_migration_guide import (
    check_db_migration_guide,
    export_db_migration_guide,
    render_db_migration_guide,
    render_db_migration_guide_markdown,
)


def _make_test_root(name: str) -> Path:
    root = Path.cwd() / "tests" / "runtime_tmp" / f"{name}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_render_db_migration_guide_tracks_alembic_revision_chain():
    payload = render_db_migration_guide()
    graph = payload["revision_graph"]

    assert payload["schema_version"] == "db_migration_guide_v1"
    assert graph["revision_count"] >= 13
    assert graph["base_revisions"] == ["0001_initial"]
    assert graph["head_revisions"] == ["0014_status_check_constraints"]
    assert graph["is_single_head"] is True
    assert graph["is_linear"] is True
    assert graph["broken_down_revisions"] == []
    created_tables = {
        op["target"]
        for revision in payload["revisions"]
        for op in revision["upgrade_operations"]
        if op["operation"] == "create_table"
    }
    assert {"learner_profiles", "question_repair_records", "question_duplicate_aliases", "generation_events"} <= created_tables


def test_render_db_migration_guide_documents_sqlite_runtime_shim():
    payload = render_db_migration_guide()
    sqlite_schema = payload["sqlite_dev_schema"]
    question_columns = {row["column"] for row in sqlite_schema["tables"]["questions"]}

    assert payload["runtime_schema_management"]["startup_sqlite_shim"] == "apps.api.app.sqlite_dev_schema.ensure_sqlite_dev_schema"
    assert "不会执行 Alembic revision 链" in payload["runtime_schema_management"]["sqlite_behavior"]
    assert "publish_status" in question_columns
    assert "idempotency_key" in {row["column"] for row in sqlite_schema["tables"]["generation_jobs"]}
    assert payload["model_metadata"]["table_count"] >= 35


def test_render_db_migration_guide_markdown_contains_runbook_and_mermaid():
    markdown = render_db_migration_guide_markdown(render_db_migration_guide())

    assert "# 数据库迁移指南" in markdown
    assert "flowchart LR" in markdown
    assert "0001_initial --> 0002_data_governance" in markdown
    assert "`alembic upgrade head`" in markdown
    assert "`backup.cmd`" in markdown
    assert "ensure_sqlite_dev_schema" in markdown


def test_export_db_migration_guide_and_check():
    root = _make_test_root("db-migration-guide")
    json_output = root / "db_migration_guide.json"
    md_output = root / "db_migration_guide.md"
    try:
        export_db_migration_guide(json_output=json_output, md_output=md_output)

        payload = json.loads(json_output.read_text(encoding="utf-8"))
        assert payload["revision_graph"]["is_linear"] is True
        assert check_db_migration_guide(json_output=json_output, md_output=md_output) is True

        json_output.write_text("{}\n", encoding="utf-8")
        assert check_db_migration_guide(json_output=json_output, md_output=md_output) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)
