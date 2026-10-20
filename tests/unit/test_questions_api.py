from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from pathlib import Path
import json
import pytest

from apps.api.app.db import Base
from apps.api.app.models import AiRunLog, AnswerEvent, AnswerSpec, AuditLog, InterventionRecord, LearnerProfile, PracticeSession, Question, QuestionTag, QuestionVersion, ReviewQueueItem, ReviewRecord, SourceFile, Submission, TaxonomyGovernanceRecord, TaxonomyTag, WrongQuestionRecord
from apps.api.app.routers.questions import QuestionRegradeRequest, QuestionRepairSuggestionRequest, ReviewQueueUpdateRequest, TaxonomyGovernanceRequest, TaxonomyTagUpsertRequest, apply_question_regrade, create_question_repair_ai_suggestion, create_taxonomy_governance_record, get_question, get_question_impact, list_questions, list_review_queue, list_tags, preview_question_regrade, update_review_queue_item, upsert_custom_tag
from apps.api.app.taxonomy_export import export_taxonomy


def seeded_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=2)
    db.add(source)
    db.add_all(
        [
            Question(id="q1", source_file_id="sf1", status="verified", source_name="source.json", stem_preview="题一", difficulty_star=3, premium=False),
            Question(id="q2", source_file_id="sf1", status="verified", source_name="source.json", stem_preview="题二", difficulty_star=3, premium=False),
            AnswerSpec(question_id="q1", status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}),
            AnswerSpec(question_id="q2", status="verified", answer_type="integer", canonical_answer="2", normalized_spec={}, raw_answer_spec={}, verification={}),
            TaxonomyTag(id="primary:number", kind="primary", label="数论", aliases=[], description="整除、余数、质数等整数性质"),
            TaxonomyTag(id="method:equation", kind="method", label="列方程", aliases=[]),
            QuestionTag(question_id="q1", tag_id="primary:number", role="level_1", raw_label="数论"),
            QuestionTag(question_id="q2", tag_id="primary:number", role="level_1", raw_label="数论"),
            QuestionTag(question_id="q2", tag_id="method:equation", role="method_tags", raw_label="列方程"),
        ]
    )
    db.commit()
    return db


def test_list_tags_supports_search_kind_and_question_count():
    db = seeded_db()

    all_tags = list_tags(db=db)
    method_tags = list_tags(kind="method", db=db)
    searched = list_tags(q="数", db=db)

    assert all_tags[0].id == "primary:number"
    assert all_tags[0].question_count == 2
    assert all_tags[0].description == "整除、余数、质数等整数性质"
    assert len(method_tags) == 1
    assert method_tags[0].kind == "method"
    assert [tag.id for tag in searched] == ["primary:number"]


def test_list_questions_supports_text_search():
    db = seeded_db()

    by_id = list_questions(q="q1", limit=50, offset=0, db=db)
    by_stem = list_questions(q="题二", limit=50, offset=0, db=db)
    by_answer_type = list_questions(q="integer", limit=50, offset=0, db=db)

    assert [question.id for question in by_id] == ["q1"]
    assert [question.id for question in by_stem] == ["q2"]
    assert [question.id for question in by_answer_type] == ["q1", "q2"]


def test_custom_tag_can_be_created_with_description_and_searched():
    db = seeded_db()

    created = upsert_custom_tag(
        TaxonomyTagUpsertRequest(kind="custom", label="反例构造", description="要求通过构造反例排除错误结论"),
        db,
    )
    searched = list_tags(q="反例", db=db)
    governance = db.query(TaxonomyGovernanceRecord).filter_by(tag_id=created.id).one()

    assert created.kind == "custom"
    assert created.label == "反例构造"
    assert created.description == "要求通过构造反例排除错误结论"
    assert searched[0].id == created.id
    assert governance.action == "taxonomy.tag_created"


def test_taxonomy_export_and_governance_records_are_versioned():
    db = seeded_db()
    record = create_taxonomy_governance_record(
        TaxonomyGovernanceRequest(action="alias_reviewed", tag_id="method:equation", payload={"alias": "方程法"}),
        db,
    )
    output = Path(".pytest_tmp_taxonomy_v1.json")

    exported = export_taxonomy(db, output)
    parsed = json.loads(output.read_text(encoding="utf-8"))
    saved_record = db.get(TaxonomyGovernanceRecord, record["id"])

    assert saved_record is not None
    assert exported["taxonomy_version"] == "taxonomy_v1"
    assert exported["content_hash"] == parsed["content_hash"]
    assert any(tag["id"] == "method:equation" and tag["question_count"] == 1 for tag in exported["tags"])
    assert exported["governance_records"][0]["action"] == "alias_reviewed"
    output.unlink()


def test_review_queue_can_be_listed_and_updated_with_audit():
    db = seeded_db()
    item = ReviewQueueItem(id="rq1", question_id="q1", reason_code="answer_spec_needs_review", severity="high", status="open", payload={"source": "import"})
    db.add(item)
    db.commit()

    queue = list_review_queue(status="open", db=db)
    updated = update_review_queue_item("rq1", ReviewQueueUpdateRequest(status="resolved", note="已复核"), db)
    audit = db.query(AuditLog).filter_by(action="review_queue.updated").one()

    assert queue["items"][0]["id"] == "rq1"
    assert updated["status"] == "resolved"
    assert updated["payload"]["last_note"] == "已复核"
    assert audit.entity_id == "rq1"


def test_question_detail_redacts_answers_by_default_and_allows_explicit_include():
    db = seeded_db()
    question = db.get(Question, "q1")
    version = QuestionVersion(
        question_id="q1",
        version=1,
        raw_json={"id": "q1"},
        question_v2={
            "content": {"stem_md": "题一"},
            "answer_spec": {"canonical_answer": "1"},
            "solution": {
                "solution_hook": "先看数量关系",
                "student_steps": ["一步"],
                "final_answer": "1",
                "verification_solution": {"method": "check"},
            },
            "verification": {"level": "verified"},
        },
        content_hash="1" * 64,
    )
    db.add(version)
    db.flush()
    question.current_version_id = version.id
    db.commit()

    hidden = get_question("q1", include_answer=False, db=db)
    full = get_question("q1", include_answer=True, db=db)

    assert hidden.answer_spec is None
    assert "answer_spec" not in hidden.question_v2
    assert "final_answer" not in hidden.question_v2["solution"]
    assert "student_steps" not in hidden.question_v2["solution"]
    assert full.answer_spec is not None
    assert full.question_v2["solution"]["final_answer"] == "1"


def test_question_impact_summarizes_sessions_learners_events_and_recalc_signal():
    db = seeded_db()
    question = db.get(Question, "q1")
    question.question_fault_status = "suspected"
    db.add(LearnerProfile(id="learner-a", name="学生A", status="active", display_order=1, settings={}))
    db.add(PracticeSession(id="s-impact", learner_id="learner-a", mode="practice", status="active", config={}, question_ids=["q1", "q2"]))
    db.add_all(
        [
            Submission(id="sub1", session_id="s-impact", question_id="q1", answer_text="0", is_correct=False, effective_wrong=True, grading_result={"reason": "numeric_mismatch"}, elapsed_ms=1000),
            Submission(id="sub2", session_id="s-impact", question_id="q1", answer_text="1", is_correct=True, help_level_reached=2, grading_result={"reason": "exact"}, elapsed_ms=2000),
            AnswerEvent(id="evt-help", session_id="s-impact", question_id="q1", event_type="help_revealed", payload={"level": 2}),
            AnswerEvent(id="evt-reveal", session_id="s-impact", question_id="q1", event_type="solution_revealed", payload={"level": 5}),
            WrongQuestionRecord(id="wrong1", session_id="s-impact", question_id="q1", submission_id="sub1", error_type="calculation_or_numeric", analysis={}),
            ReviewRecord(id="review1", question_id="q1", session_id="s-impact", source_record_id="wrong1", status="pending", payload={}),
            InterventionRecord(id="int1", session_id="s-impact", original_question_id="q1", current_question_id="q2", intervention_type="mastery_check", status="active", payload={}),
            ReviewQueueItem(id="rq-impact", question_id="q1", reason_code="duplicate_question", severity="medium", status="open", payload={}),
        ]
    )
    db.commit()

    impact = get_question_impact("q1", db)

    assert impact["totals"]["learner_count"] == 1
    assert impact["totals"]["session_count"] == 1
    assert impact["totals"]["submission_count"] == 2
    assert impact["totals"]["correct_submission_count"] == 1
    assert impact["totals"]["wrong_submission_count"] == 1
    assert impact["totals"]["solution_revealed_count"] == 1
    assert impact["totals"]["wrong_record_count"] == 1
    assert impact["totals"]["review_record_count"] == 1
    assert impact["totals"]["intervention_count"] == 1
    assert impact["totals"]["open_review_queue_count"] == 1
    assert impact["event_counts_by_type"]["help_revealed"] == 1
    assert impact["affected_learners"][0]["learner_name"] == "学生A"
    assert impact["affected_sessions"][0]["attempt_count"] == 2
    assert impact["affected_sessions"][0]["final_correct"] is True
    assert impact["affected_sessions"][0]["max_help_level"] == 2
    assert impact["history_recalculation"]["recommended"] is True
    assert impact["history_recalculation"]["schema_version"] == "history_recalculation_strategy_v1"
    assert impact["history_recalculation"]["requires_explicit_regrade"] is True
    assert impact["history_recalculation"]["affected_counts"]["submission_count"] == 2
    assert any(row["scope"] == "reports.summary" for row in impact["history_recalculation"]["affected_scopes"])


def test_question_regrade_preview_and_apply_updates_submission_with_audit_event():
    db = seeded_db()
    answer = db.query(AnswerSpec).filter_by(question_id="q1").one()
    answer.raw_answer_spec = {
        "status": "verified",
        "answer_type": "integer",
        "canonical_answer": "2",
        "acceptable_answers": [],
        "tolerance": {"type": "exact", "value": 0},
        "grading_method": "numeric_exact",
    }
    answer.normalized_spec = {"canonical": ["2"]}
    db.add(PracticeSession(id="s-regrade", mode="practice", status="active", config={}, question_ids=["q1"]))
    db.add(Submission(id="sub-regrade", session_id="s-regrade", question_id="q1", answer_text="2", is_correct=False, effective_wrong=True, grading_result={"is_correct": False, "reason": "numeric_mismatch"}))
    db.commit()

    preview = preview_question_regrade("q1", max_submissions=50, changed_only=True, db=db)

    assert preview["checked_count"] == 1
    assert preview["changed_count"] == 1
    assert preview["changed_wrong_to_correct"] == 1
    assert "new_grading_result" not in preview["items"][0]
    assert "canonical" not in preview["items"][0]["new"]
    assert preview["history_recalculation"]["status"] == "ready_to_apply"
    assert preview["history_recalculation"]["affected_counts"]["changed_submission_count"] == 1
    assert "s-regrade" in preview["history_recalculation"]["affected_session_ids"]

    with pytest.raises(Exception) as missing_confirm:
        apply_question_regrade("q1", QuestionRegradeRequest(confirm=False), db)

    assert getattr(missing_confirm.value, "status_code", None) == 400

    applied = apply_question_regrade("q1", QuestionRegradeRequest(confirm=True, reason="fixed answer", reviewer="tester"), db)
    submission = db.get(Submission, "sub-regrade")
    event = db.query(AnswerEvent).filter_by(event_type="submission_regraded").one()
    audit = db.query(AuditLog).filter_by(action="question.submissions_regraded").one()

    assert applied["status"] == "applied"
    assert applied["applied_count"] == 1
    assert applied["history_recalculation"]["status"] == "applied"
    assert applied["history_recalculation"]["affected_counts"]["changed_wrong_to_correct"] == 1
    assert submission.is_correct is True
    assert submission.effective_wrong is False
    assert submission.grading_result["reason"] == "numeric_exact"
    assert submission.grading_result["regrade"]["reviewer"] == "tester"
    assert event.payload["previous"]["is_correct"] is False
    assert event.payload["new"]["is_correct"] is True
    assert audit.entity_id == "q1"


def test_question_repair_ai_suggestion_uses_mock_and_writes_ai_audit_log():
    db = seeded_db()
    db.add(PracticeSession(id="s-suggest", mode="practice", status="active", config={}, question_ids=["q1"]))
    db.add(Submission(id="sub-suggest", session_id="s-suggest", question_id="q1", answer_text="0", is_correct=False, grading_result={"reason": "numeric_mismatch"}))
    db.add(ReviewQueueItem(id="rq-suggest", question_id="q1", reason_code="canonical_answer_error", severity="high", status="open", payload={"source": "test"}))
    db.commit()

    result = create_question_repair_ai_suggestion(
        "q1",
        QuestionRepairSuggestionRequest(issue_type="canonical_answer_error", note="疑似标准答案错误", use_mock=True),
        db,
    )
    ai_log = db.query(AiRunLog).filter_by(role="question_repair_reviewer").one()
    audit = db.query(AuditLog).filter_by(action="question_repair.ai_suggestion_created").one()

    assert result["question_id"] == "q1"
    assert result["suggestion"]["status"] == "ok"
    assert result["suggestion"]["issue_type"] == "canonical_answer_error"
    assert result["suggested_draft"]["quarantine"] is True
    assert result["suggested_draft"]["should_regrade_after_publish"] is True
    assert ai_log.prompt_version == "question_repair_reviewer_v1"
    assert ai_log.error is None
    assert audit.entity_id == "q1"
