import json
import shutil
import uuid
from pathlib import Path

from scripts.dev.export_data_dictionary import check_data_dictionary, export_data_dictionary, render_data_dictionary, render_data_dictionary_markdown


def _table(dictionary: dict, name: str) -> dict:
    return next(row for row in dictionary["tables"] if row["name"] == name)


def _make_test_root(name: str) -> Path:
    root = Path.cwd() / "tests" / "runtime_tmp" / f"{name}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_render_data_dictionary_contains_core_tables_and_relationships():
    dictionary = render_data_dictionary()
    table_names = {row["name"] for row in dictionary["tables"]}

    assert dictionary["schema_version"] == "data_dictionary_v1"
    assert dictionary["table_count"] >= 35
    assert {"questions", "question_versions", "answer_specs", "submissions", "generation_jobs", "ai_run_logs"}.issubset(table_names)

    questions = _table(dictionary, "questions")
    assert questions["primary_key"] == ["id"]
    assert any(column["name"] == "source_file_id" and column["foreign_keys"][0]["target"] == "source_files.id" for column in questions["columns"])
    assert any(constraint["name"] == "ck_questions_status" for constraint in questions["check_constraints"])

    answer_specs = _table(dictionary, "answer_specs")
    assert any(column["name"] == "question_id" and column["unique"] is True for column in answer_specs["columns"])

    generation_jobs = _table(dictionary, "generation_jobs")
    assert any(constraint["name"] == "ck_generation_jobs_status" and "queued" in constraint["sqltext"] for constraint in generation_jobs["check_constraints"])

    review_queue_items = _table(dictionary, "review_queue_items")
    assert any(constraint["name"] == "ck_review_queue_items_status" and "closed" in constraint["sqltext"] for constraint in review_queue_items["check_constraints"])

    generated_candidates = _table(dictionary, "generated_candidates")
    assert any(constraint["name"] == "ck_generated_candidates_publish_status" and "retired_question" in constraint["sqltext"] for constraint in generated_candidates["check_constraints"])

    practice_sessions = _table(dictionary, "practice_sessions")
    assert any(constraint["name"] == "ck_practice_sessions_mode" and "strict_test" in constraint["sqltext"] for constraint in practice_sessions["check_constraints"])

    relationships = {
        (row["from_table"], row["from_column"], row["to_table"], row["to_column"])
        for row in dictionary["relationships"]
    }
    assert ("question_versions", "question_id", "questions", "id") in relationships
    assert ("submissions", "session_id", "practice_sessions", "id") in relationships
    assert ("ai_run_logs", "job_id", "generation_jobs", "id") in relationships


def test_render_data_dictionary_markdown_contains_mermaid_er():
    markdown = render_data_dictionary_markdown(render_data_dictionary())

    assert "```mermaid" in markdown
    assert "erDiagram" in markdown
    assert "questions ||--o{ answer_specs" in markdown
    assert "| `questions` |" in markdown
    assert "## `submissions`" in markdown
    assert "CHECK 约束" in markdown


def test_export_data_dictionary_and_check():
    root = _make_test_root("data-dictionary")
    json_output = root / "data_dictionary.json"
    md_output = root / "data_dictionary.md"
    try:
        export_data_dictionary(json_output=json_output, md_output=md_output)

        payload = json.loads(json_output.read_text(encoding="utf-8"))
        assert payload["table_count"] == len(payload["tables"])
        assert check_data_dictionary(json_output=json_output, md_output=md_output) is True

        md_output.write_text("stale\n", encoding="utf-8")
        assert check_data_dictionary(json_output=json_output, md_output=md_output) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)
