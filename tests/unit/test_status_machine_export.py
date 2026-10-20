import json
import shutil
import uuid
from pathlib import Path

from scripts.dev.export_status_machines import (
    check_status_machines,
    export_status_machines,
    render_status_machines,
    render_status_machines_markdown,
)


def _domain(machine: dict, name: str) -> dict:
    return next(row for row in machine["domains"] if row["domain"] == name)


def _make_test_root(name: str) -> Path:
    root = Path.cwd() / "tests" / "runtime_tmp" / f"{name}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_render_status_machines_contains_explicit_generation_edges():
    machine = render_status_machines()
    generation_job = _domain(machine, "generation_job")
    edges = {(row["from"], row["to"]) for row in generation_job["edges"]}

    assert machine["schema_version"] == "status_machines_v1"
    assert machine["domain_count"] >= 12
    assert machine["transition_domain_count"] >= 3
    assert generation_job["has_explicit_transitions"] is True
    assert ("queued", "running") in edges
    assert ("running", "accepted") in edges
    assert {"accepted", "rejected", "failed", "cancelled", "timed_out"}.issubset(set(generation_job["terminal_statuses"]))


def test_render_status_machines_keeps_inventory_domains_non_transitioning():
    machine = render_status_machines()
    question_status = _domain(machine, "question_status")
    values = {status["value"] for status in question_status["statuses"]}

    assert question_status["has_explicit_transitions"] is False
    assert question_status["edge_count"] == 0
    assert {"verified", "needs_review", "quarantined", "retired"}.issubset(values)
    assert "needs_review" in question_status["blocking_statuses"]


def test_render_status_machines_markdown_contains_mermaid_and_inventory_notice():
    markdown = render_status_machines_markdown(render_status_machines())

    assert "```mermaid" in markdown
    assert "stateDiagram-v2" in markdown
    assert "## 单题 AI 出题任务 (`generation_job`)" in markdown
    assert "queued --> running" in markdown
    assert "无显式转换表" in markdown
    assert "| `question_status` |" in markdown


def test_export_status_machines_and_check():
    root = _make_test_root("status-machines")
    json_output = root / "status_machines.json"
    md_output = root / "status_machines.md"
    try:
        export_status_machines(json_output=json_output, md_output=md_output)

        payload = json.loads(json_output.read_text(encoding="utf-8"))
        assert payload["edge_count"] == sum(domain["edge_count"] for domain in payload["domains"])
        assert check_status_machines(json_output=json_output, md_output=md_output) is True

        md_output.write_text("stale\n", encoding="utf-8")
        assert check_status_machines(json_output=json_output, md_output=md_output) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)
