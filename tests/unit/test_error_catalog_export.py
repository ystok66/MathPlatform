import json
import shutil
import uuid
from pathlib import Path

from scripts.dev.export_error_catalog import (
    check_error_catalog,
    export_error_catalog,
    render_error_catalog_export,
    render_error_catalog_markdown,
)


def _make_test_root(name: str) -> Path:
    root = Path.cwd() / "tests" / "runtime_tmp" / f"{name}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_render_error_catalog_export_contains_registered_and_provider_codes():
    payload = render_error_catalog_export()
    registered_codes = set(payload["registered_error_codes"])
    provider_codes = {row["error_code"] for row in payload["provider_errors"]}

    assert payload["schema_version"] == "error_catalog_export_v1"
    assert "ANSWER_LOCKED_AFTER_REVEAL" in registered_codes
    assert "STATE_VERSION_CONFLICT" in registered_codes
    assert {"provider_timeout", "provider_auth_error", "provider_rate_limited", "provider_bad_json"}.issubset(provider_codes)
    assert payload["source_occurrence_count"] >= 100


def test_render_error_catalog_export_scans_http_exception_sources():
    payload = render_error_catalog_export()
    http_rows = [row for row in payload["source_occurrences"] if row["source_type"] == "http_exception"]

    assert any(row["detail"] == "answer_locked_after_reveal" and row["envelope_error_code"] == "ANSWER_LOCKED_AFTER_REVEAL" for row in http_rows)
    assert any(row["detail"] == "AI answer judge is disabled during strict tests" and row["envelope_error_code"] == "STRICT_TEST_ACTION_DISABLED" for row in http_rows)
    assert any(row["raw_detail_code"] == "generation_call_budget_exceeded" for row in http_rows)


def test_render_error_catalog_markdown_contains_source_tables():
    markdown = render_error_catalog_markdown(render_error_catalog_export())

    assert "# 错误码目录" in markdown
    assert "## Provider 归一化错误" in markdown
    assert "`PROVIDER_TIMEOUT`" in markdown
    assert "## HTTPException 源码出现位置" in markdown
    assert "`apps/api/app/routers/practice.py`" in markdown


def test_export_error_catalog_and_check():
    root = _make_test_root("error-catalog")
    json_output = root / "error_catalog.json"
    md_output = root / "error_catalog.md"
    try:
        export_error_catalog(json_output=json_output, md_output=md_output)

        payload = json.loads(json_output.read_text(encoding="utf-8"))
        assert payload["registered_catalog"]["schema_version"] == "error_catalog_v1"
        assert check_error_catalog(json_output=json_output, md_output=md_output) is True

        json_output.write_text("{}\n", encoding="utf-8")
        assert check_error_catalog(json_output=json_output, md_output=md_output) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)
