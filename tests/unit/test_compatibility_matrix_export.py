import json
import shutil
import uuid
from pathlib import Path

from scripts.dev.export_compatibility_matrix import (
    check_compatibility_matrix,
    export_compatibility_matrix,
    render_compatibility_matrix,
    render_compatibility_matrix_markdown,
)


def _make_test_root(name: str) -> Path:
    root = Path.cwd() / "tests" / "runtime_tmp" / f"{name}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_render_compatibility_matrix_tracks_versions_and_transfer_schemas():
    payload = render_compatibility_matrix()
    matrix_ids = {row["id"] for row in payload["matrix"]}

    assert payload["schema_version"] == "compatibility_matrix_v1"
    assert payload["source_checks_ok"] is True
    assert {"C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08", "C09"}.issubset(matrix_ids)
    assert payload["api_runtime"]["fastapi"] == "0.115.6"
    assert payload["web_runtime"]["next_version"] == "15.1.3"
    assert "18.18.0" in payload["web_runtime"]["node_engine_from_next"]
    assert payload["transfer_schemas"]["history_transfer"] == "history_transfer_v1"
    assert payload["transfer_schemas"]["question_bank_transfer"] == "question_bank_transfer_v1"


def test_render_compatibility_matrix_tracks_migration_and_release_contracts():
    payload = render_compatibility_matrix()

    assert payload["database_migrations"]["head_revision"] == "0014_status_check_constraints"
    assert payload["database_migrations"]["single_head"] is True
    assert payload["database_migrations"]["linear_chain"] is True
    assert payload["launcher_versions"]["backup_manifest"] == "math_platform_backup_v1"
    assert payload["launcher_versions"]["diagnostic_bundle"] == "math_platform_diagnostic_bundle_v1"
    assert payload["release_runtime"]["api_binds_loopback"] is True
    assert payload["release_runtime"]["web_binds_loopback"] is True


def test_render_compatibility_matrix_markdown_contains_rows_and_commands():
    markdown = render_compatibility_matrix_markdown(render_compatibility_matrix())

    assert "# CHANGELOG 与兼容性矩阵" in markdown
    assert "`C01`" in markdown
    assert "question_bank_transfer_v1" in markdown
    assert "export_compatibility_matrix.py --check" in markdown


def test_export_compatibility_matrix_and_check():
    root = _make_test_root("compatibility-matrix")
    json_output = root / "compatibility_matrix.json"
    md_output = root / "compatibility_matrix.md"
    try:
        export_compatibility_matrix(json_output=json_output, md_output=md_output)

        payload = json.loads(json_output.read_text(encoding="utf-8"))
        assert payload["source_checks_ok"] is True
        assert check_compatibility_matrix(json_output=json_output, md_output=md_output) is True

        md_output.write_text("stale\n", encoding="utf-8")
        assert check_compatibility_matrix(json_output=json_output, md_output=md_output) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)
