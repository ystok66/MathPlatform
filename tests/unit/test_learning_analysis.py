from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.learning_analysis import analyze_submission, deep_diagnose_submission
from apps.api.app.models import AnswerSpec, AuditLog, PracticeSession, Question, ReviewRecord, SourceFile, Submission, TaxonomyTag, QuestionTag, WrongQuestionRecord
from apps.api.app.routers.learning import ReviewRecordRequest, create_review, deep_diagnose_endpoint, wrong_book
from apps.api.app.routers.practice import submit_answer
from apps.api.app.schemas import SubmitAnswerRequest


def seeded_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=1)
    db.add(source)
    db.add(
        Question(
            id="q1",
            source_file_id=source.id,
            status="verified",
            source_name="source.json",
            stem_preview="一个两位数的十位数字比个位数字大2，两个数字之和是10。这个两位数是多少？",
            difficulty_star=3,
            premium=False,
        )
    )
    db.add(
        AnswerSpec(
            question_id="q1",
            status="verified",
            answer_type="integer",
            canonical_answer="64",
            normalized_spec={},
            raw_answer_spec={
                "status": "verified",
                "answer_type": "integer",
                "canonical_answer": "64",
                "acceptable_answers": [],
                "tolerance": {"type": "exact", "value": 0},
                "unit": None,
                "order_sensitive": True,
                "grading_method": "numeric_exact",
            },
            verification={},
        )
    )
    db.add(TaxonomyTag(id="method:equation", kind="method", label="方程", aliases=[]))
    db.add(QuestionTag(question_id="q1", tag_id="method:equation", role="method", raw_label="方程"))
    db.add(PracticeSession(id="s1", mode="practice", config={}, question_ids=["q1"]))
    db.commit()
    return db


def test_clear_wrong_attempt_gets_error_type_prerequisites_and_report_gate():
    db = seeded_db()
    submission = Submission(
        id="sub1",
        session_id="s1",
        question_id="q1",
        answer_text="46",
        thought_text="先设十位为x，再按数字和检查，最后代回计算。",
        is_correct=False,
        grading_result={"is_correct": False, "reason": "numeric_mismatch"},
        elapsed_ms=12000,
    )
    db.add(submission)
    db.commit()

    analysis = analyze_submission(submission, db)

    assert analysis["error_type"] == "calculation_or_numeric"
    assert analysis["thought_quality"] == "reasonable_attempt"
    assert analysis["write_to_deterministic_report"] is True
    assert "计算准确性" in analysis["prerequisites"]
    assert "method:方程" in analysis["prerequisites"]


def test_off_task_and_unclear_thought_are_not_deterministic_findings():
    db = seeded_db()
    off_task = Submission(
        id="sub2",
        session_id="s1",
        question_id="q1",
        answer_text="1",
        thought_text="哈哈随便乱写",
        is_correct=False,
        grading_result={"is_correct": False, "reason": "numeric_mismatch"},
        elapsed_ms=1000,
    )
    unclear = Submission(
        id="sub3",
        session_id="s1",
        question_id="q1",
        answer_text="2",
        thought_text="猜的",
        is_correct=False,
        grading_result={"is_correct": False, "reason": "numeric_mismatch"},
        elapsed_ms=1000,
    )

    off_task_analysis = analyze_submission(off_task, db)
    unclear_analysis = analyze_submission(unclear, db)

    assert off_task_analysis["thought_quality"] == "off_task_or_play"
    assert unclear_analysis["thought_quality"] == "unclear_expression"
    assert off_task_analysis["write_to_deterministic_report"] is False
    assert unclear_analysis["write_to_deterministic_report"] is False


def test_submit_wrong_answer_creates_wrong_book_record_and_review():
    db = seeded_db()

    response = submit_answer(
        "s1",
        SubmitAnswerRequest(
            question_id="q1",
            answer_text="46",
            thought_text="先设十位为x，再按数字和检查，最后代回计算。",
            elapsed_ms=1000,
        ),
        db,
    )

    record = db.scalar(select(WrongQuestionRecord).where(WrongQuestionRecord.submission_id == response.submission_id))
    assert record is not None
    assert response.result["wrong_record_id"] == record.id
    assert response.result["learning_analysis"]["error_type"] == "calculation_or_numeric"
    assert wrong_book(db=db)["items"][0]["id"] == record.id

    review = create_review(record.id, ReviewRecordRequest(payload={"note": "复盘方程设元"}), db)
    saved_review = db.get(ReviewRecord, review["id"])

    assert review["source_record_id"] == record.id
    assert saved_review is not None
    assert saved_review.payload["note"] == "复盘方程设元"


def test_deep_diagnosis_returns_evidence_and_audit_log():
    db = seeded_db()
    submission = Submission(
        id="sub-deep",
        session_id="s1",
        question_id="q1",
        answer_text="46",
        thought_text="先设十位为x，但最后没有复核个位。",
        is_correct=False,
        grading_result={"is_correct": False, "reason": "numeric_mismatch"},
        elapsed_ms=9000,
    )
    db.add(submission)
    db.commit()

    direct = deep_diagnose_submission(submission, db)
    routed = deep_diagnose_endpoint("sub-deep", db)
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "learning.deep_diagnosis_created"))

    assert direct["provider"] == "mock"
    assert direct["diagnosis"]["evidence"]["submission_id"] == "sub-deep"
    assert "calculation_check_missing" in direct["diagnosis"]["likely_misconceptions"]
    assert direct["diagnosis"]["prerequisite_focus"]
    assert routed["diagnosis"]["next_actions"]
    assert audit is not None
