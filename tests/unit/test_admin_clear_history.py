import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import (
    AiRunLog,
    AnswerEvent,
    AnswerSpec,
    AuditLog,
    GeneratedCandidate,
    GenerationBatch,
    GenerationBatchItem,
    GenerationJob,
    LearnerProfile,
    PracticeSession,
    Question,
    QuestionVersion,
    SourceFile,
    Submission,
)
from apps.api.app.routers.admin import clear_history, create_destructive_challenge
from apps.api.app.routers.admin import delete_learner_data, export_history, import_history
from apps.api.app.schemas import ClearHistoryRequest, DestructiveChallengeRequest, LearnerDeleteRequest


def test_clear_history_requires_destructive_challenge():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)

    with pytest.raises(Exception):
        clear_history(ClearHistoryRequest(), db)


def clear_history_challenge_payload(db: Session) -> dict[str, str]:
    challenge = create_destructive_challenge(DestructiveChallengeRequest(action="clear_history"), db)
    return {"challenge_id": challenge.challenge_id, "challenge_phrase": challenge.phrase}


def test_clear_history_challenge_is_single_use():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    payload = clear_history_challenge_payload(db)

    first = clear_history(ClearHistoryRequest(**payload), db)

    assert first.status == "cleared"
    with pytest.raises(Exception):
        clear_history(ClearHistoryRequest(**payload), db)


def test_clear_history_challenge_rejects_changed_target():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    payload = clear_history_challenge_payload(db)
    db.add(PracticeSession(id="s-late", mode="practice", config={}, question_ids=[]))
    db.commit()

    with pytest.raises(Exception) as exc_info:
        clear_history(ClearHistoryRequest(**payload), db)

    assert getattr(exc_info.value, "status_code", None) == 409


def test_clear_history_removes_test_traces_but_preserves_imported_bank():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    imported_source = SourceFile(id="sf-source", filename="source.json", sha256="0" * 64, question_count=1)
    generated_source = SourceFile(id="sf-ai", filename="__ai_generated__", sha256="generated", question_count=1)
    db.add_all([imported_source, generated_source])
    db.add_all(
        [
            Question(id="q-source", source_file_id=imported_source.id, status="verified", source_name="source.json", stem_preview="source", difficulty_star=3, premium=False),
            Question(id="q-ai", source_file_id=generated_source.id, status="verified", source_name="AI生成题", stem_preview="generated", difficulty_star=3, premium=False),
            QuestionVersion(question_id="q-source", version=1, raw_json={}, question_v2={}, content_hash="a"),
            QuestionVersion(question_id="q-ai", version=1, raw_json={}, question_v2={}, content_hash="b"),
            AnswerSpec(question_id="q-source", status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}),
            AnswerSpec(question_id="q-ai", status="verified", answer_type="integer", canonical_answer="2", normalized_spec={}, raw_answer_spec={}, verification={}),
            PracticeSession(id="s1", mode="practice", config={}, question_ids=["q-source", "q-ai"]),
            Submission(id="sub1", session_id="s1", question_id="q-source", answer_text="1", is_correct=True, grading_result={}),
            GenerationJob(id="job1", status="accepted", request={}, progress={}, accepted_question_id="q-ai"),
            GeneratedCandidate(id="cand1", job_id="job1", strategy="mock", status="accepted", payload={}, review={}, published_question_id="q-ai"),
            GenerationBatch(id="batch1", status="completed", request={}, target_count=1, concurrency=1, progress={}, accepted_count=1),
            GenerationBatchItem(id="item1", batch_id="batch1", item_index=1, status="accepted", job_id="job1", question_id="q-ai"),
            AiRunLog(role="generator", provider="mock", model="mock", prompt_version="v1", input_hash="h", request_payload={}, response_payload={}, token_usage={}),
        ]
    )
    db.commit()

    response = clear_history(ClearHistoryRequest(**clear_history_challenge_payload(db)), db)

    assert response.status == "cleared"
    assert db.get(Question, "q-source") is not None
    assert db.get(AnswerSpec, db.scalar(select(AnswerSpec.id).where(AnswerSpec.question_id == "q-source"))) is not None
    assert db.get(Question, "q-ai") is None
    assert db.scalar(select(PracticeSession)) is None
    assert db.scalar(select(Submission)) is None
    assert db.scalar(select(GenerationJob)) is None
    assert db.scalar(select(GenerationBatch)) is None
    assert db.scalar(select(AuditLog).where(AuditLog.action == "admin.clear_history")) is not None


def test_history_export_import_preserves_history_and_generated_questions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    imported_source = SourceFile(id="sf-source", filename="source.json", sha256="0" * 64, question_count=1)
    generated_source = SourceFile(id="sf-ai", filename="__ai_generated__", sha256="generated", question_count=1)
    db.add_all([imported_source, generated_source])
    db.add_all(
        [
            Question(id="q-source", source_file_id=imported_source.id, status="verified", source_name="source.json", stem_preview="source", difficulty_star=3, premium=False),
            Question(id="q-ai", source_file_id=generated_source.id, status="verified", source_name="AI生成题", stem_preview="generated", difficulty_star=3, premium=False),
            QuestionVersion(question_id="q-source", version=1, raw_json={}, question_v2={}, content_hash="a"),
            QuestionVersion(question_id="q-ai", version=1, raw_json={}, question_v2={}, content_hash="b"),
            AnswerSpec(question_id="q-source", status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}),
            AnswerSpec(question_id="q-ai", status="verified", answer_type="integer", canonical_answer="2", normalized_spec={}, raw_answer_spec={}, verification={}),
            PracticeSession(id="s-export", mode="practice", config={}, question_ids=["q-ai"]),
            Submission(id="sub-export", session_id="s-export", question_id="q-ai", answer_text="2", is_correct=True, grading_result={"is_correct": True}),
            GenerationJob(id="job-export", status="accepted", request={}, progress={}, accepted_question_id="q-ai"),
            GeneratedCandidate(id="cand-export", job_id="job-export", strategy="mock", status="accepted", payload={}, review={}, published_question_id="q-ai"),
        ]
    )
    db.commit()

    exported = export_history(db)

    assert any(row["id"] == "q-ai" for row in exported.tables["questions"])
    assert all(row["id"] != "q-source" for row in exported.tables["questions"])
    assert any(row["id"] == "sub-export" for row in exported.tables["submissions"])

    engine2 = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine2)
    db2 = Session(engine2)
    db2.add(SourceFile(id="sf-ai-new", filename="__ai_generated__", sha256="generated", question_count=0))
    db2.commit()

    result = import_history(exported, db=db2)

    migrated_question = db2.get(Question, "q-ai")
    assert result.status == "imported"
    assert migrated_question is not None
    assert migrated_question.source_file_id == "sf-ai-new"
    assert db2.get(Question, "q-source") is None
    assert db2.get(PracticeSession, "s-export") is not None
    assert db2.get(Submission, "sub-export") is not None


def test_history_export_can_scope_to_single_learner():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    imported_source = SourceFile(id="sf-source", filename="source.json", sha256="0" * 64, question_count=1)
    generated_source = SourceFile(id="sf-ai", filename="__ai_generated__", sha256="generated", question_count=2)
    db.add_all(
        [
            imported_source,
            generated_source,
            LearnerProfile(id="learner-a", name="Learner A", status="active", display_order=1, settings={}),
            LearnerProfile(id="learner-b", name="Learner B", status="active", display_order=2, settings={}),
        ]
    )
    db.add_all(
        [
            Question(id="q-source", source_file_id=imported_source.id, status="verified", source_name="source.json", stem_preview="source", difficulty_star=3, premium=False),
            Question(id="q-ai-a", source_file_id=generated_source.id, status="verified", source_name="AI", stem_preview="generated a", difficulty_star=3, premium=False),
            Question(id="q-ai-b", source_file_id=generated_source.id, status="verified", source_name="AI", stem_preview="generated b", difficulty_star=3, premium=False),
            QuestionVersion(question_id="q-source", version=1, raw_json={}, question_v2={}, content_hash="a"),
            QuestionVersion(question_id="q-ai-a", version=1, raw_json={}, question_v2={}, content_hash="b"),
            QuestionVersion(question_id="q-ai-b", version=1, raw_json={}, question_v2={}, content_hash="c"),
            AnswerSpec(question_id="q-source", status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}),
            AnswerSpec(question_id="q-ai-a", status="verified", answer_type="integer", canonical_answer="2", normalized_spec={}, raw_answer_spec={}, verification={}),
            AnswerSpec(question_id="q-ai-b", status="verified", answer_type="integer", canonical_answer="3", normalized_spec={}, raw_answer_spec={}, verification={}),
            PracticeSession(id="s-a", learner_id="learner-a", mode="practice", config={}, question_ids=["q-source", "q-ai-a"]),
            PracticeSession(id="s-b", learner_id="learner-b", mode="practice", config={}, question_ids=["q-ai-b"]),
            Submission(id="sub-a", session_id="s-a", question_id="q-ai-a", answer_text="2", is_correct=True, grading_result={"is_correct": True}),
            Submission(id="sub-b", session_id="s-b", question_id="q-ai-b", answer_text="3", is_correct=True, grading_result={"is_correct": True}),
            AnswerEvent(id="evt-a", session_id="s-a", question_id="q-ai-a", event_type="submit", payload={"learner_id": "learner-a"}),
            AnswerEvent(id="evt-b", session_id="s-b", question_id="q-ai-b", event_type="submit", payload={"learner_id": "learner-b"}),
            GenerationJob(id="job-a", status="accepted", request={"learner_id": "learner-a"}, progress={}, accepted_question_id="q-ai-a"),
            GenerationJob(id="job-b", status="accepted", request={"learner_id": "learner-b"}, progress={}, accepted_question_id="q-ai-b"),
            GeneratedCandidate(id="cand-a", job_id="job-a", strategy="mock", status="accepted", payload={}, review={}, published_question_id="q-ai-a"),
            GeneratedCandidate(id="cand-b", job_id="job-b", strategy="mock", status="accepted", payload={}, review={}, published_question_id="q-ai-b"),
            GenerationBatch(id="batch-a", status="completed", request={"learner_id": "learner-a"}, target_count=1, concurrency=1, progress={}, accepted_count=1),
            GenerationBatch(id="batch-b", status="completed", request={"learner_id": "learner-b"}, target_count=1, concurrency=1, progress={}, accepted_count=1),
            GenerationBatchItem(id="item-a", batch_id="batch-a", item_index=1, status="accepted", job_id="job-a", candidate_id="cand-a", question_id="q-ai-a"),
            GenerationBatchItem(id="item-b", batch_id="batch-b", item_index=1, status="accepted", job_id="job-b", candidate_id="cand-b", question_id="q-ai-b"),
            AiRunLog(id="log-a", job_id="job-a", role="generator", provider="mock", model="mock", prompt_version="v1", input_hash="h1", request_payload={}, response_payload={}, token_usage={}),
            AiRunLog(id="log-b", job_id="job-b", role="generator", provider="mock", model="mock", prompt_version="v1", input_hash="h2", request_payload={}, response_payload={}, token_usage={}),
            AuditLog(id="audit-a", action="practice.submit", entity_type="practice_session", entity_id="s-a", payload={"learner_id": "learner-a"}),
            AuditLog(id="audit-b", action="practice.submit", entity_type="practice_session", entity_id="s-b", payload={"learner_id": "learner-b"}),
        ]
    )
    db.commit()

    exported = export_history(db, learner_id="learner-a")

    assert exported.filters == {"learner_id": "learner-a"}
    assert exported.table_counts == {key: len(value) for key, value in exported.tables.items()}
    assert [row["id"] for row in exported.tables["learner_profiles"]] == ["learner-a"]
    assert [row["id"] for row in exported.tables["practice_sessions"]] == ["s-a"]
    assert [row["id"] for row in exported.tables["submissions"]] == ["sub-a"]
    assert [row["id"] for row in exported.tables["answer_events"]] == ["evt-a"]
    assert {row["id"] for row in exported.tables["questions"]} == {"q-ai-a"}
    assert {row["id"] for row in exported.tables["generation_jobs"]} == {"job-a"}
    assert {row["id"] for row in exported.tables["generation_batches"]} == {"batch-a"}
    assert {row["id"] for row in exported.tables["generated_candidates"]} == {"cand-a"}
    assert {row["id"] for row in exported.tables["generation_batch_items"]} == {"item-a"}
    assert {row["id"] for row in exported.tables["ai_run_logs"]} == {"log-a"}
    assert {row["id"] for row in exported.tables["audit_logs"]} == {"audit-a"}


def test_delete_learner_rejects_default_learner():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add(LearnerProfile(id="default-learner", name="Default", status="active", display_order=0, settings={}))
    db.commit()

    with pytest.raises(Exception) as exc_info:
        create_destructive_challenge(DestructiveChallengeRequest(action="delete_learner", learner_id="default-learner"), db)

    assert getattr(exc_info.value, "status_code", None) == 409


def test_delete_learner_removes_scoped_history_but_preserves_other_learner_and_shared_ai_question():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    generated_source = SourceFile(id="sf-ai", filename="__ai_generated__", sha256="generated", question_count=3)
    db.add(generated_source)
    db.add_all(
        [
            LearnerProfile(id="learner-a", name="Learner A", status="active", display_order=1, settings={}),
            LearnerProfile(id="learner-b", name="Learner B", status="active", display_order=2, settings={}),
            Question(id="q-ai-a", source_file_id=generated_source.id, status="verified", source_name="AI", stem_preview="generated a", difficulty_star=3, premium=False),
            Question(id="q-ai-shared", source_file_id=generated_source.id, status="verified", source_name="AI", stem_preview="generated shared", difficulty_star=3, premium=False),
            Question(id="q-ai-b", source_file_id=generated_source.id, status="verified", source_name="AI", stem_preview="generated b", difficulty_star=3, premium=False),
            QuestionVersion(question_id="q-ai-a", version=1, raw_json={}, question_v2={}, content_hash="a"),
            QuestionVersion(question_id="q-ai-shared", version=1, raw_json={}, question_v2={}, content_hash="s"),
            QuestionVersion(question_id="q-ai-b", version=1, raw_json={}, question_v2={}, content_hash="b"),
            AnswerSpec(question_id="q-ai-a", status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}),
            AnswerSpec(question_id="q-ai-shared", status="verified", answer_type="integer", canonical_answer="2", normalized_spec={}, raw_answer_spec={}, verification={}),
            AnswerSpec(question_id="q-ai-b", status="verified", answer_type="integer", canonical_answer="3", normalized_spec={}, raw_answer_spec={}, verification={}),
            PracticeSession(id="s-a", learner_id="learner-a", mode="practice", config={}, question_ids=["q-ai-a", "q-ai-shared"]),
            PracticeSession(id="s-b", learner_id="learner-b", mode="practice", config={}, question_ids=["q-ai-shared", "q-ai-b"]),
            Submission(id="sub-a", session_id="s-a", question_id="q-ai-a", answer_text="1", is_correct=True, grading_result={}),
            Submission(id="sub-b", session_id="s-b", question_id="q-ai-b", answer_text="3", is_correct=True, grading_result={}),
            AnswerEvent(id="evt-a", session_id="s-a", question_id="q-ai-shared", event_type="submit", payload={}),
            AnswerEvent(id="evt-b", session_id="s-b", question_id="q-ai-shared", event_type="submit", payload={}),
            GenerationJob(id="job-a", status="accepted", request={"learner_id": "learner-a"}, progress={}, accepted_question_id="q-ai-a"),
            GenerationJob(id="job-shared", status="accepted", request={"learner_id": "learner-a"}, progress={}, accepted_question_id="q-ai-shared"),
            GenerationJob(id="job-b", status="accepted", request={"learner_id": "learner-b"}, progress={}, accepted_question_id="q-ai-b"),
            GeneratedCandidate(id="cand-a", job_id="job-a", strategy="mock", status="accepted", payload={}, review={}, published_question_id="q-ai-a"),
            GeneratedCandidate(id="cand-shared", job_id="job-shared", strategy="mock", status="accepted", payload={}, review={}, published_question_id="q-ai-shared"),
            GeneratedCandidate(id="cand-b", job_id="job-b", strategy="mock", status="accepted", payload={}, review={}, published_question_id="q-ai-b"),
            GenerationBatch(id="batch-a", status="completed", request={"learner_id": "learner-a"}, target_count=2, concurrency=1, progress={}, accepted_count=2),
            GenerationBatch(id="batch-b", status="completed", request={"learner_id": "learner-b"}, target_count=1, concurrency=1, progress={}, accepted_count=1),
            GenerationBatchItem(id="item-a", batch_id="batch-a", item_index=1, status="accepted", job_id="job-a", candidate_id="cand-a", question_id="q-ai-a"),
            GenerationBatchItem(id="item-shared", batch_id="batch-a", item_index=2, status="accepted", job_id="job-shared", candidate_id="cand-shared", question_id="q-ai-shared"),
            GenerationBatchItem(id="item-b", batch_id="batch-b", item_index=1, status="accepted", job_id="job-b", candidate_id="cand-b", question_id="q-ai-b"),
            AiRunLog(id="log-a", job_id="job-a", role="generator", provider="mock", model="mock", prompt_version="v1", input_hash="h1", request_payload={}, response_payload={}, token_usage={}),
            AiRunLog(id="log-shared", job_id="job-shared", role="generator", provider="mock", model="mock", prompt_version="v1", input_hash="h2", request_payload={}, response_payload={}, token_usage={}),
            AiRunLog(id="log-b", job_id="job-b", role="generator", provider="mock", model="mock", prompt_version="v1", input_hash="h3", request_payload={}, response_payload={}, token_usage={}),
            AuditLog(id="audit-a", action="practice.submit", entity_type="practice_session", entity_id="s-a", payload={"learner_id": "learner-a"}),
            AuditLog(id="audit-b", action="practice.submit", entity_type="practice_session", entity_id="s-b", payload={"learner_id": "learner-b"}),
        ]
    )
    db.commit()

    challenge = create_destructive_challenge(DestructiveChallengeRequest(action="delete_learner", learner_id="learner-a"), db)
    response = delete_learner_data("learner-a", LearnerDeleteRequest(challenge_id=challenge.challenge_id, challenge_phrase=challenge.phrase), db)

    assert response.status == "deleted"
    assert response.learner_id == "learner-a"
    assert response.skipped["shared_ai_question_ids"] == ["q-ai-shared"]
    assert db.get(LearnerProfile, "learner-a") is None
    assert db.get(LearnerProfile, "learner-b") is not None
    assert db.get(PracticeSession, "s-a") is None
    assert db.get(Submission, "sub-a") is None
    assert db.get(AnswerEvent, "evt-a") is None
    assert db.get(PracticeSession, "s-b") is not None
    assert db.get(Submission, "sub-b") is not None
    assert db.get(AnswerEvent, "evt-b") is not None
    assert db.get(Question, "q-ai-a") is None
    assert db.get(Question, "q-ai-shared") is not None
    assert db.get(Question, "q-ai-b") is not None
    assert db.get(GenerationJob, "job-a") is None
    assert db.get(GenerationJob, "job-shared") is None
    assert db.get(GenerationJob, "job-b") is not None
    assert db.get(AiRunLog, "log-a") is None
    assert db.get(AiRunLog, "log-shared") is None
    assert db.get(AiRunLog, "log-b") is not None
    assert db.get(AuditLog, "audit-a") is None
    assert db.get(AuditLog, "audit-b") is not None
    assert db.scalar(select(AuditLog).where(AuditLog.action == "admin.learner_deleted")) is not None


def test_history_import_dry_run_rolls_back_writes_but_reports_counts():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    generated_source = SourceFile(id="sf-ai", filename="__ai_generated__", sha256="generated", question_count=1)
    db.add(generated_source)
    db.add_all(
        [
            Question(id="q-ai", source_file_id=generated_source.id, status="verified", source_name="AI生成题", stem_preview="generated", difficulty_star=3, premium=False),
            QuestionVersion(question_id="q-ai", version=1, raw_json={}, question_v2={}, content_hash="b"),
            AnswerSpec(question_id="q-ai", status="verified", answer_type="integer", canonical_answer="2", normalized_spec={}, raw_answer_spec={}, verification={}),
            PracticeSession(id="s-export", mode="practice", config={}, question_ids=["q-ai"]),
            Submission(id="sub-export", session_id="s-export", question_id="q-ai", answer_text="2", is_correct=True, grading_result={"is_correct": True}),
            GenerationJob(id="job-export", status="accepted", request={}, progress={}, accepted_question_id="q-ai"),
            GeneratedCandidate(id="cand-export", job_id="job-export", strategy="mock", status="accepted", payload={}, review={}, published_question_id="q-ai"),
        ]
    )
    db.commit()
    exported = export_history(db)

    engine2 = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine2)
    db2 = Session(engine2)

    result = import_history(exported, dry_run=True, db=db2)

    assert result.status == "dry_run"
    assert result.dry_run is True
    assert result.imported_counts["questions"] == 1
    assert db2.get(Question, "q-ai") is None
    assert db2.get(PracticeSession, "s-export") is None
