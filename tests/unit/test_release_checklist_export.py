import json
import shutil
import uuid
from pathlib import Path

from scripts.dev.export_release_checklist import (
    check_release_checklist,
    export_release_checklist,
    render_release_checklist,
    render_release_checklist_markdown,
)


def _make_test_root(name: str) -> Path:
    root = Path.cwd() / "tests" / "runtime_tmp" / f"{name}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_render_release_checklist_tracks_split_phase_smoke_contract():
    payload = render_release_checklist()
    smoke = payload["source_checks"]["release_smoke_phase"]
    business = payload["source_checks"]["release_business_smoke"]

    assert payload["schema_version"] == "release_checklist_v1"
    assert payload["source_checks_ok"] is True
    assert smoke["phases"] == ["preflight", "after-start", "doctor", "after-stop"]
    assert smoke["contains_start"] is False
    assert smoke["contains_stop"] is False
    assert business["uses_bounded_import"] is True
    assert business["uses_mock_generation"] is True
    assert business["uses_one_candidate"] is True


def test_render_release_checklist_stage_order_and_manifest_rules():
    payload = render_release_checklist()
    stages = [row["stage"] for row in payload["checklist"]]
    manifest = payload["release_manifest"]

    assert stages == [
        "source_preflight",
        "build_release",
        "release_preflight",
        "start_release",
        "after_start",
        "business_smoke",
        "doctor",
        "stop_release",
        "after_stop",
    ]
    assert manifest["manifest_version"] == "release_manifest_v1"
    assert "logs" in manifest["excluded_dirs"]
    assert manifest["component_dirs"]["prompt_bundle"] == "prompts"
    assert any("不要把 start、health" in item for item in payload["safety_rules"])


def test_render_release_checklist_markdown_contains_commands():
    markdown = render_release_checklist_markdown(render_release_checklist())

    assert "# 发布 Checklist" in markdown
    assert "`source_preflight`" in markdown
    assert "release_smoke_phase.ps1 -Phase preflight" in markdown
    assert "release_business_smoke.ps1" in markdown
    assert "MathPlatform.exe stop" in markdown


def test_export_release_checklist_and_check():
    root = _make_test_root("release-checklist")
    json_output = root / "release_checklist.json"
    md_output = root / "release_checklist.md"
    try:
        export_release_checklist(json_output=json_output, md_output=md_output)

        payload = json.loads(json_output.read_text(encoding="utf-8"))
        assert payload["source_checks_ok"] is True
        assert check_release_checklist(json_output=json_output, md_output=md_output) is True

        md_output.write_text("stale\n", encoding="utf-8")
        assert check_release_checklist(json_output=json_output, md_output=md_output) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)
