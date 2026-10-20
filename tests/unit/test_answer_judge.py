from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app.answer_judge import answer_requires_ai_judge, judge_answer_with_ai
from apps.api.app.db import Base
from apps.api.app.models import AiRunLog, AnswerEvent, AnswerSpec, PracticeSession, Question, QuestionVersion, SourceFile
from apps.api.app.routers.practice import submit_answer
from apps.api.app.schemas import SubmitAnswerRequest


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


def test_answer_judge_mock_uses_deterministic_result(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(filename="sample.json", sha256="x", question_count=1)
    db.add(source)
    db.flush()
    question = Question(
        id="q-set",
        source_file_id=source.id,
        status="verified",
        source_name="sample",
        stem_preview="List all primes from 1, 2, 3.",
        difficulty_star=2,
        premium=False,
    )
    db.add(question)
    db.flush()
    version = QuestionVersion(
        question_id=question.id,
        version=1,
        raw_json={"question_text": question.stem_preview},
        question_v2={"content": {"stem_md": question.stem_preview}},
        content_hash="hash",
    )
    db.add(version)
    answer = AnswerSpec(
        question_id=question.id,
        status="verified",
        answer_type="set",
        canonical_answer="2,3,13,23,31",
        normalized_spec={},
        raw_answer_spec={
            "status": "verified",
            "answer_type": "set",
            "canonical_answer": "2,3,13,23,31",
            "acceptable_answers": [],
            "unit": None,
            "order_sensitive": False,
            "grading_method": "set_exact",
        },
        verification={},
    )
    db.add(answer)
    db.commit()
    monkeypatch.setattr("apps.api.app.answer_judge.get_settings", mock_settings)

    result = judge_answer_with_ai(db, question, version, answer, "31 23 13 3 2")
    db.commit()

    assert result["verdict"] == "correct"
    assert result["is_correct"] is True
    assert result["provider"] == "mock"
    assert db.query(AiRunLog).filter(AiRunLog.role == "answer_judge").count() == 1
    log = db.query(AiRunLog).filter(AiRunLog.role == "answer_judge").one()
    assert log.request_payload["trace"]["schema_version"] == "ai_trace_v1"
    assert log.request_payload["trace"]["operation"].startswith("answer_judge.")
    assert log.request_payload["trace"]["span_id"] == log.token_usage["trace"]["span_id"]


def test_answer_requires_ai_judge_for_multi_or_non_unique_specs():
    assert answer_requires_ai_judge({"status": "verified", "answer_type": "integer", "canonical_answer": "64", "acceptable_answers": ["64"], "grading_method": "numeric_exact"}) is False
    assert answer_requires_ai_judge({"status": "verified", "answer_type": "set", "canonical_answer": "2,3,13,23,31", "grading_method": "set_exact"}) is True
    assert answer_requires_ai_judge({"status": "verified", "answer_type": "integer", "canonical_answer": "2", "acceptable_answers": ["3"], "grading_method": "numeric_exact"}) is True
    assert answer_requires_ai_judge({"status": "verified", "answer_type": "expression", "canonical_answer": "x+1", "grading_method": "expression_equiv"}) is True


def test_submit_auto_triggers_ai_judge_for_set_answers(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(filename="sample.json", sha256="x", question_count=1)
    db.add(source)
    db.flush()
    question = Question(
        id="q-auto-set",
        source_file_id=source.id,
        status="verified",
        source_name="sample",
        stem_preview="List all primes from 1, 2, 3.",
        difficulty_star=2,
        premium=False,
    )
    db.add(question)
    db.flush()
    version = QuestionVersion(
        question_id=question.id,
        version=1,
        raw_json={"question_text": question.stem_preview},
        question_v2={"content": {"stem_md": question.stem_preview}},
        content_hash="hash",
    )
    db.add(version)
    db.add(
        AnswerSpec(
            question_id=question.id,
            status="verified",
            answer_type="set",
            canonical_answer="2,3,13,23,31",
            normalized_spec={},
            raw_answer_spec={
                "status": "verified",
                "answer_type": "set",
                "canonical_answer": "2,3,13,23,31",
                "acceptable_answers": [],
                "unit": None,
                "order_sensitive": False,
                "grading_method": "set_exact",
            },
            verification={},
        )
    )
    db.add(PracticeSession(id="s-auto", mode="strict_test", config={"selection": {"strict_no_ai": True}}, question_ids=[question.id]))
    db.commit()
    monkeypatch.setattr("apps.api.app.answer_judge.get_settings", mock_settings)

    response = submit_answer("s-auto", SubmitAnswerRequest(question_id=question.id, answer_text="31 23 13 3 2"), db)

    assert response.is_correct is True
    assert response.result["auto_answer_judge_triggered"] is True
    assert response.result["evaluation_snapshot"]["question_version_id"] == version.id
    assert response.result["evaluation_snapshot"]["answer_spec_status"] == "verified"
    assert response.result["auto_answer_judge"]["student_feedback"] == "严格测试中不显示复核解释。"
    assert db.query(AnswerEvent).filter(AnswerEvent.event_type == "answer_judged").count() == 1
