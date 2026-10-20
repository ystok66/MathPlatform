from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import AnswerSpec, AuditLog, Question, QuestionVersion, ReviewQueueItem, SourceFile
from apps.api.app.routers.questions import QuestionRepairActionRequest, QuestionRepairDraftRequest, create_question_repair, list_question_repairs, publish_question_repair, revert_question_repair


def repair_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=1)
    question = Question(id="q1", source_file_id="sf1", status="verified", source_name="source.json", stem_preview="old stem", difficulty_star=3, premium=False)
    db.add_all([source, question])
    db.flush()
    version = QuestionVersion(
        id="v1",
        question_id="q1",
        version=1,
        raw_json={"id": "q1", "question_text": "old stem"},
        question_v2={"schema_version": "question_v2", "content": {"stem_md": "old stem"}, "evaluation": {"difficulty": {"star": 3}, "premium": {"is_premium": False}}},
        content_hash="1" * 64,
    )
    db.add(version)
    db.add(
        AnswerSpec(
            question_id="q1",
            status="verified",
            answer_type="integer",
            canonical_answer="1",
            normalized_spec={"canonical": ["1"]},
            raw_answer_spec={"status": "verified", "answer_type": "integer", "canonical_answer": "1", "acceptable_answers": [], "verification": {"steps": ["old"]}},
            verification={"steps": ["old"]},
        )
    )
    db.add(ReviewQueueItem(id="rq1", question_id="q1", reason_code="answer_spec_needs_review", severity="high", status="open", payload={"source": "test"}))
    db.flush()
    question.current_version_id = version.id
    db.commit()
    return db


def test_question_repair_publishes_new_version_and_can_revert():
    db = repair_db()
    draft_qv2 = {
        "schema_version": "question_v2",
        "content": {"stem_md": "new stem"},
        "evaluation": {"difficulty": {"star": 4}, "premium": {"is_premium": True}},
    }
    draft_answer = {
        "status": "verified",
        "answer_type": "integer",
        "canonical_answer": "2",
        "acceptable_answers": ["2"],
        "verification": {"steps": ["new"]},
    }

    created = create_question_repair("q1", QuestionRepairDraftRequest(reason="canonical_answer_error", severity="high", question_v2=draft_qv2, answer_spec=draft_answer, note="fix answer"), db)
    before_publish = db.get(Question, "q1")

    assert created["status"] == "draft"
    assert before_publish.current_version_id == "v1"
    assert any(change["path"].startswith("answer_spec.") for change in created["diff"])
    assert list_question_repairs("q1", db)["items"][0]["id"] == created["id"]

    published = publish_question_repair("q1", created["id"], QuestionRepairActionRequest(note="approved"), db)
    question = db.get(Question, "q1")
    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == "q1"))
    review = db.get(ReviewQueueItem, "rq1")
    published_version = db.get(QuestionVersion, published["new_version_id"])

    assert published["status"] == "published"
    assert question.current_version_id == published["new_version_id"]
    assert question.stem_preview == "new stem"
    assert question.difficulty_star == 4
    assert question.premium is True
    assert answer.canonical_answer == "2"
    assert answer.raw_answer_spec["verification"]["steps"] == ["new"]
    assert review.status == "resolved"
    assert published_version.version == 2

    reverted = revert_question_repair("q1", created["id"], QuestionRepairActionRequest(note="undo"), db)
    question = db.get(Question, "q1")
    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == "q1"))
    audits = list(db.scalars(select(AuditLog).order_by(AuditLog.created_at)))

    assert reverted["status"] == "reverted"
    assert question.current_version_id == "v1"
    assert question.stem_preview == "old stem"
    assert answer.canonical_answer == "1"
    assert [audit.action for audit in audits] == ["question_repair.created", "question_repair.published", "question_repair.reverted"]
