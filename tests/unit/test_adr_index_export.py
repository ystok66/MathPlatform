import json
import shutil
import uuid
from pathlib import Path

from scripts.dev.export_adr_index import (
    check_adr_index,
    export_adr_index,
    render_adr_index,
    render_adr_index_markdown,
)


def _make_test_root(name: str) -> Path:
    root = Path.cwd() / "tests" / "runtime_tmp" / f"{name}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_render_adr_index_tracks_existing_records_and_template():
    payload = render_adr_index()
    checks = payload["source_checks"]
    ids = {item["id"] for item in payload["items"]}

    assert payload["schema_version"] == "adr_index_v1"
    assert payload["source_checks_ok"] is True
    assert checks["template_exists"] is True
    assert checks["required_sections_ok"] is True
    assert checks["duplicate_ids"] == []
    assert {"0001", "0002"}.issubset(ids)
    assert payload["template"]["next_id_hint"] == "0003"


def test_render_adr_index_summaries_include_decision_content():
    payload = render_adr_index()
    by_id = {item["id"]: item for item in payload["items"]}

    assert "PostgreSQL" in by_id["0001"]["decision_summary"]
    assert "docs/generated" in by_id["0002"]["decision_summary"]
    assert by_id["0002"]["status"] == "Accepted"


def test_render_adr_index_markdown_contains_records_and_commands():
    markdown = render_adr_index_markdown(render_adr_index())

    assert "# ADR 索引" in markdown
    assert "`0001`" in markdown
    assert "`0002`" in markdown
    assert "export_adr_index.py --check" in markdown


def test_export_adr_index_and_check():
    root = _make_test_root("adr-index")
    json_output = root / "adr_index.json"
    md_output = root / "adr_index.md"
    try:
        export_adr_index(json_output=json_output, md_output=md_output)

        payload = json.loads(json_output.read_text(encoding="utf-8"))
        assert payload["source_checks_ok"] is True
        assert check_adr_index(json_output=json_output, md_output=md_output) is True

        md_output.write_text("stale\n", encoding="utf-8")
        assert check_adr_index(json_output=json_output, md_output=md_output) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)
