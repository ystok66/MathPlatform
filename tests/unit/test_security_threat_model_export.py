import json
import shutil
import uuid
from pathlib import Path

from scripts.dev.export_security_threat_model import (
    check_security_threat_model,
    export_security_threat_model,
    render_security_threat_model,
    render_security_threat_model_markdown,
)


def _make_test_root(name: str) -> Path:
    root = Path.cwd() / "tests" / "runtime_tmp" / f"{name}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_render_security_threat_model_tracks_core_boundaries():
    payload = render_security_threat_model()
    checks = payload["source_checks"]
    boundary_ids = {row["id"] for row in payload["boundaries"]}
    threat_ids = {row["id"] for row in payload["threats"]}

    assert payload["schema_version"] == "security_threat_model_v1"
    assert payload["source_checks_ok"] is True
    assert {"B01", "B02", "B03", "B04", "B05", "B06", "B07"}.issubset(boundary_ids)
    assert {"T01", "T02", "T03", "T04", "T05"}.issubset(threat_ids)
    assert checks["browser_write_guard"]["csrf_header_name"] == "x-math-platform-client"
    assert checks["browser_write_guard"]["rejects_cross_site_fetch_metadata"] is True
    assert checks["secret_storage"]["uses_dpapi_on_windows"] is True
    assert checks["frontend_content_safety"]["katex_trust_false"] is True
    assert checks["frontend_content_safety"]["content_security_policy_header_found"] is True
    assert checks["frontend_content_safety"]["csp_object_src_none"] is True
    assert checks["frontend_content_safety"]["csp_frame_ancestors_none"] is True
    assert checks["prompt_boundary"]["prompt_security_version"] == "agent_input_envelope_v1"


def test_render_security_threat_model_records_known_gaps_without_failing_core_checks():
    payload = render_security_threat_model()
    gaps = {row["id"]: row for row in payload["known_gaps"]}

    assert payload["source_checks_ok"] is True
    assert "G01" in gaps
    assert "本地单用户" in gaps["G01"]["description"]
    assert payload["source_checks"]["frontend_content_safety"]["content_security_policy_header_found"] is True


def test_render_security_threat_model_markdown_contains_evidence_and_commands():
    markdown = render_security_threat_model_markdown(render_security_threat_model())

    assert "# 安全边界与威胁模型" in markdown
    assert "`B01`" in markdown
    assert "csrf_cross_site" in markdown
    assert "tests\\unit\\test_api_security.py" in markdown
    assert "export_security_threat_model.py --check" in markdown


def test_export_security_threat_model_and_check():
    root = _make_test_root("security-threat-model")
    json_output = root / "security_threat_model.json"
    md_output = root / "security_threat_model.md"
    try:
        export_security_threat_model(json_output=json_output, md_output=md_output)

        payload = json.loads(json_output.read_text(encoding="utf-8"))
        assert payload["source_checks_ok"] is True
        assert check_security_threat_model(json_output=json_output, md_output=md_output) is True

        md_output.write_text("stale\n", encoding="utf-8")
        assert check_security_threat_model(json_output=json_output, md_output=md_output) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)
