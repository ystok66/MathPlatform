from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import AiRunLog, AnswerEvent, AnswerSpec, PracticeSession, Question, QuestionTag, QuestionVersion, SourceFile, TaxonomyTag
from apps.api.app.routers.practice import request_question_support
from apps.api.app.schemas import QuestionSupportRequest


def mock_settings():
    return SimpleNamespace(
        default_model="mock-model",
        model_provider="mock",
        liaobots_api_key=None,
        responses_api_key=None,
        anthropic_api_key=None,
        gemini_api_key=None,
        liaobots_base_url="",
        responses_base_url="",
        anthropic_base_url="",
        gemini_base_url="",
        planner_model=None,
        generator_model=None,
        reviewer_model=None,
        reporter_model=None,
        diagnostician_model=None,
        hint_generator_model=None,
        fault_auditor_model=None,
        set_reviewer_model=None,
        answer_judge_model=None,
    )


def seeded_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf", filename="source.json", sha256="0" * 64, question_count=2)
    db.add(source)
    db.add_all(
        [
            Question(id="q1", source_file_id=source.id, status="verified", source_name="source.json", stem_preview="竖式题", difficulty_star=3, premium=False),
            Question(id="q2", source_file_id=source.id, status="verified", source_name="source.json", stem_preview="同星级替换题", difficulty_star=3, premium=False),
            QuestionVersion(question_id="q1", version=1, raw_json={}, question_v2={"content": {"stem_md": "竖式题"}}, content_hash="h1"),
            QuestionVersion(question_id="q2", version=1, raw_json={}, question_v2={"content": {"stem_md": "同星级替换题"}}, content_hash="h2"),
            AnswerSpec(question_id="q1", status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={"status": "verified", "answer_type": "integer", "canonical_answer": "1"}, verification={}),
            AnswerSpec(question_id="q2", status="verified", answer_type="integer", canonical_answer="2", normalized_spec={}, raw_answer_spec={"status": "verified", "answer_type": "integer", "canonical_answer": "2"}, verification={}),
            TaxonomyTag(id="primary:number", kind="primary", label="数论", aliases=[]),
            QuestionTag(question_id="q1", tag_id="primary:number", role="primary", raw_label="数论"),
            QuestionTag(question_id="q2", tag_id="primary:number", role="primary", raw_label="数论"),
            PracticeSession(id="s1", mode="practice", config={}, question_ids=["q1"]),
        ]
    )
    db.commit()
    return db


def test_question_support_replaces_broken_question_with_same_star(monkeypatch):
    db = seeded_db()
    monkeypatch.setattr("apps.api.app.question_support.get_settings", mock_settings)

    result = request_question_support(
        "s1",
        QuestionSupportRequest(question_id="q1", concern_type="format_issue", message="竖式显示有问题，像是缺图", at_ms=12),
        db,
    )

    session = db.get(PracticeSession, "s1")
    assert result.question_problem is True
    assert result.replaced is True
    assert result.replacement_question_id == "q2"
    assert session.question_ids == ["q2"]
    assert db.scalar(select(AnswerEvent).where(AnswerEvent.event_type == "question_support_requested")) is not None
    assert db.scalar(select(AnswerEvent).where(AnswerEvent.event_type == "question_replaced")) is not None
    assert db.scalar(select(AiRunLog).where(AiRunLog.role == "question_support")) is not None


def test_question_support_refuses_hint_request(monkeypatch):
    db = seeded_db()
    monkeypatch.setattr("apps.api.app.question_support.get_settings", mock_settings)

    result = request_question_support(
        "s1",
        QuestionSupportRequest(question_id="q1", concern_type="do_not_understand", message="我不会，给我一点思路", at_ms=0),
        db,
    )

    assert result.question_problem is False
    assert result.replaced is False
    assert db.get(PracticeSession, "s1").question_ids == ["q1"]
    assert "不能提供解题提示" in result.safe_response
