from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import AnswerEvent, AnswerSpec, GenerationBatch, GenerationBatchItem, InterventionRecord, PracticeSession, Question, QuestionTag, SourceFile, Submission, TaxonomyTag
from apps.api.app.routers.reports import AiSummaryRequest, ai_summary, ai_summary_from_request, evidence_detail, session_report, summary


def seeded_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=2)
    db.add(source)
    db.add_all(
        [
            Question(
                id="q1",
                source_file_id=source.id,
                status="verified",
                source_name="source-a.json",
                stem_preview="题目一",
                difficulty_star=3,
                premium=True,
            ),
            Question(
                id="q2",
                source_file_id=source.id,
                status="verified",
                question_fault_status="ambiguous_question",
                source_name="source-b.json",
                stem_preview="题目二",
                difficulty_star=4,
                premium=False,
            ),
            AnswerSpec(
                question_id="q1",
                status="verified",
                answer_type="integer",
                canonical_answer="1",
                normalized_spec={},
                raw_answer_spec={},
                verification={},
            ),
            AnswerSpec(
                question_id="q2",
                status="verified",
                answer_type="integer",
                canonical_answer="2",
                normalized_spec={},
                raw_answer_spec={},
                verification={},
            ),
            TaxonomyTag(id="method:counting", kind="method", label="计数", aliases=[]),
            QuestionTag(question_id="q1", tag_id="method:counting", role="method", raw_label="计数"),
            PracticeSession(id="s1", mode="practice", learner_id="learner-a", config={}, question_ids=["q1"]),
            PracticeSession(id="s2", mode="strict_test", learner_id="learner-b", config={}, question_ids=["q2"]),
            Submission(
                id="sub1",
                session_id="s1",
                question_id="q1",
                answer_text="0",
                thought_text="先试一个数",
                is_correct=False,
                grading_result={"is_correct": False, "reason": "numeric_mismatch"},
                elapsed_ms=12000,
                created_at=datetime(2026, 7, 18, 8, 0, 0),
            ),
            Submission(
                id="sub2",
                session_id="s1",
                question_id="q1",
                answer_text="1",
                is_correct=True,
                grading_result={"is_correct": True},
                elapsed_ms=18000,
                created_at=datetime(2026, 7, 18, 8, 5, 0),
            ),
            Submission(
                id="sub3",
                session_id="s2",
                question_id="q2",
                answer_text="2",
                is_correct=True,
                grading_result={"is_correct": True},
                elapsed_ms=20000,
                created_at=datetime(2026, 7, 17, 8, 0, 0),
            ),
            AnswerEvent(id="evt1", session_id="s1", question_id="q1", event_type="submitted", payload={"submission_id": "sub1", "at_ms": 12000}, created_at=datetime(2026, 7, 18, 8, 0, 0)),
            AnswerEvent(id="evt2", session_id="s1", question_id="q1", event_type="submitted", payload={"submission_id": "sub2", "at_ms": 18000}, created_at=datetime(2026, 7, 18, 8, 5, 0)),
            AnswerEvent(id="evt3", session_id="s1", question_id="q1", event_type="intervention_prefetched", payload={"at_ms": 13000}, created_at=datetime(2026, 7, 18, 8, 1, 0)),
            AnswerEvent(id="evt4", session_id="s1", question_id="q1", event_type="intervention_due", payload={"at_ms": 19000}, created_at=datetime(2026, 7, 18, 8, 6, 0)),
            AnswerEvent(id="evt5", session_id="s1", question_id="q1", event_type="answer_reveal_debt_created", payload={"at_ms": 20000}, created_at=datetime(2026, 7, 18, 8, 7, 0)),
            InterventionRecord(
                id="iv1",
                session_id="s1",
                original_question_id="q1",
                current_question_id="q1",
                intervention_type="return_original",
                status="completed",
                payload={"transfer_success": True},
            ),
        ]
    )
    db.commit()
    return db


def test_summary_filters_questions_by_tag_star_source_and_premium():
    db = seeded_db()
    report = summary(source_name="source-a.json", difficulty_star=3, premium=True, tag_id=["method:counting"], db=db)

    assert report["question_total"] == 1
    assert report["answer_status"] == {"verified": 1}
    assert report["by_star"] == [{"difficulty_star": 3, "count": 1}]
    assert report["tag_star_heatmap"] == [
        {"tag_id": "method:counting", "kind": "method", "label": "计数", "stars": {"1": 0, "2": 0, "3": 1, "4": 0, "5": 0}, "total": 1}
    ]
    assert report["submission_summary"]["answered_questions"] == 1
    assert report["submission_summary"]["first_correct"] == 0
    assert report["submission_summary"]["final_correct"] == 1
    assert report["error_types"] == {"correct": 1}
    assert report["trend_by_day"] == [{"date": "2026-07-18", "answered": 1, "correct": 1, "accuracy": 1.0, "sample_size": 1, "confidence": "very_low"}]
    assert report["time_stats_by_star"] == [
        {
            "difficulty_star": 3,
            "count": 1,
            "avg_elapsed_ms": 18000.0,
            "max_elapsed_ms": 18000,
            "sample_size": 1,
            "confidence": "very_low",
            "basis": "按每个 session-question 的最终提交用时统计，暂停/页面离开时间由前端计时事件预处理后进入 elapsed_ms。",
        }
    ]
    assert report["intervention_effectiveness"]["prefetched"] == 1
    assert report["intervention_effectiveness"]["shown"] == 1
    assert report["intervention_effectiveness"]["answer_reveal_debt_created"] == 1
    assert report["summary_meta"]["confidence_rule"] == "none=0, very_low<5, low<20, medium<50, high>=50 samples."
    assert report["metric_definitions"]["first_accuracy"]["numerator"] == 0
    assert report["metric_definitions"]["first_accuracy"]["denominator"] == 1
    assert report["metric_definitions"]["first_accuracy"]["basis"] == "每个 session-question 只取第一次提交；未答题不进入分母。"
    assert report["metric_definitions"]["final_accuracy"]["numerator"] == 1
    assert report["metric_definitions"]["question_quality_incidents"]["denominator"] == 1
    assert report["review_plan"]["sample_size"] == 1
    assert report["review_plan"]["priority_weak_points"][0]["tag_id"] == "method:counting"
    assert report["review_plan"]["priority_weak_points"][0]["avg_attempts"] == 2.0
    assert report["review_plan"]["next_tasks"][0]["target_tags"] == ["method:counting"]
    assert report["review_plan"]["next_tasks"][0]["success_rule"] == "3 道中首答至少 2 道正确，且不看答案。"


def test_summary_filters_submissions_by_mode_and_date():
    db = seeded_db()
    strict_report = summary(mode="strict_test", db=db)
    today_report = summary(date_from=date(2026, 7, 18), date_to=date(2026, 7, 18), db=db)

    assert strict_report["submission_summary"]["submissions"] == 1
    assert strict_report["evidence"][0]["mode"] == "strict_test"
    assert strict_report["question_quality_incidents"]["by_status"] == {"ambiguous_question": 1}
    assert today_report["submission_summary"]["submissions"] == 2
    assert today_report["submission_summary"]["answered_questions"] == 1
    assert strict_report["comparison"]["available"] is False
    assert today_report["comparison"]["available"] is True
    assert today_report["comparison"]["current"]["window_start"] == "2026-07-18"
    assert today_report["comparison"]["previous"]["window_start"] == "2026-07-17"
    assert today_report["comparison"]["previous"]["window_end"] == "2026-07-17"
    assert today_report["comparison"]["delta"]["submissions"] == 1
    assert today_report["comparison"]["delta"]["first_accuracy"] == -1.0
    assert today_report["comparison"]["delta"]["final_accuracy"] == 0.0
    assert today_report["comparison"]["delta"]["help_shown"] == 1


def test_summary_filters_submissions_and_events_by_learner():
    db = seeded_db()
    learner_a_report = summary(learner_id="learner-a", db=db)
    learner_b_report = summary(learner_id="learner-b", db=db)

    assert learner_a_report["submission_summary"]["submissions"] == 2
    assert learner_a_report["submission_summary"]["answered_questions"] == 1
    assert learner_a_report["intervention_effectiveness"]["shown"] == 1
    assert learner_a_report["filters"]["learner_id"] == "learner-a"
    assert learner_b_report["submission_summary"]["submissions"] == 1
    assert learner_b_report["submission_summary"]["answered_questions"] == 1
    assert learner_b_report["intervention_effectiveness"]["shown"] == 0
    assert learner_b_report["evidence"][0]["mode"] == "strict_test"


def test_session_report_returns_submission_and_evidence_summary():
    db = seeded_db()
    report = session_report("s1", db=db)

    assert report["session_id"] == "s1"
    assert report["learner_id"] == "learner-a"
    assert report["question_total"] == 1
    assert report["answered_questions"] == 1
    assert report["first_correct"] == 0
    assert report["final_correct"] == 1
    assert report["final_accuracy"] == 1.0
    assert report["submission_count"] == 2
    assert report["time"]["total_elapsed_ms"] == 18000
    assert report["time"]["total_attempt_elapsed_ms"] == 30000
    assert report["evidence"][0]["evidence_id"] == "sub2"


def test_reports_surface_regrade_history_metadata():
    db = seeded_db()
    submission = db.get(Submission, "sub2")
    submission.grading_result = {
        "is_correct": True,
        "reason": "numeric_exact",
        "regrade": {
            "reason": "fixed answer",
            "reviewer": "tester",
            "applied_at": "2026-07-19T10:00:00",
            "previous_is_correct": False,
            "previous_reason": "numeric_mismatch",
            "previous_effective_wrong": True,
            "previous_question_version_id": "old-version",
        },
    }
    db.commit()

    report = summary(db=db)
    session = session_report("s1", db=db)
    detail = evidence_detail("sub2", db=db)

    assert report["summary_meta"]["history_recalculation"]["regraded_submission_count"] == 1
    assert report["summary_meta"]["history_recalculation"]["exported_snapshots_may_be_stale"] is True
    assert any(row["evidence_id"] == "sub2" and row["regrade"]["is_regraded"] is True for row in report["evidence"])
    assert session["regraded_submission_count"] == 1
    assert session["evidence"][0]["regrade"]["reviewer"] == "tester"
    assert detail["attempts"][-1]["regrade"]["previous_reason"] == "numeric_mismatch"


def test_evidence_detail_returns_attempts_events_and_interventions():
    db = seeded_db()
    detail = evidence_detail("sub2", db=db)

    assert detail["evidence_id"] == "sub2"
    assert detail["session"]["mode"] == "practice"
    assert detail["session"]["learner_id"] == "learner-a"
    assert detail["question"]["id"] == "q1"
    assert [row["submission_id"] for row in detail["attempts"]] == ["sub1", "sub2"]
    assert detail["attempts"][0]["error_type"] == "calculation_or_numeric"
    assert detail["attempts"][0]["thought_text_present"] is True
    assert [row["event_type"] for row in detail["events"]] == ["submitted", "intervention_prefetched", "submitted", "intervention_due", "answer_reveal_debt_created"]
    assert detail["interventions"][0]["payload"]["transfer_success"] is True


def test_ai_summary_cites_evidence_ids():
    db = seeded_db()
    report = ai_summary(db=db)

    assert report["sample_size"] == 2
    assert report["confidence"] == "low"
    assert report["evidence_ids"] == ["sub3", "sub2"]
    assert all(finding["evidence_ids"] for finding in report["findings"])


def test_ai_summary_post_accepts_generation_batch_context():
    db = seeded_db()
    db.add(GenerationBatch(id="batch1", status="completed", request={"target_tags": ["method:counting"]}, target_count=1, concurrency=1, progress={}, accepted_count=1))
    db.add(GenerationBatchItem(id="item1", batch_id="batch1", item_index=1, status="accepted", question_id="q1", similarity_review={"risk": "low"}))
    db.commit()

    report = ai_summary_from_request(AiSummaryRequest(report_type="generation_batch", generation_batch_id="batch1", filters={}), db)

    assert report["report_type"] == "generation_batch"
    assert report["requested_context"]["generation_batch_id"] == "batch1"
    assert report["generation_batch"]["accepted_count"] == 1
    assert report["deterministic_summary"]["filters"]["generation_batch_id"] == "batch1"
