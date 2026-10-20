import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import AnswerSpec, AuditLog, GeneratedCandidate, PracticeSession, Question, QuestionDuplicateAlias, QuestionRepairRecord, QuestionTag, QuestionVersion, ReviewQueueItem, SourceFile, Submission, TaxonomyTag
from apps.api.app.routers.admin import create_destructive_challenge, delete_questions, export_question_bank, import_question_bank
from apps.api.app.routers.questions import list_questions
from apps.api.app.schemas import DestructiveChallengeRequest, QuestionDeleteRequest


def seeded_bank_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add(SourceFile(id="sf-source", filename="source.json", sha256="0" * 64, question_count=1))
    db.add(TaxonomyTag(id="primary:number", kind="primary", label="数论", aliases=[], description="整数性质"))
    db.add(
        Question(
            id="q-source",
            source_file_id="sf-source",
            status="verified",
            source_name="source.json",
            stem_preview="题干",
            difficulty_star=3,
            premium=False,
        )
    )
    db.add(QuestionVersion(id="v-source", question_id="q-source", version=1, raw_json={"id": "q-source"}, question_v2={"content": {"stem_md": "题干"}}, content_hash="a"))
    db.add(
        QuestionRepairRecord(
            id="repair-source",
            question_id="q-source",
            base_version_id="v-source",
            new_version_id=None,
            status="draft",
            reason="manual_fix",
            severity="medium",
            draft_question_v2={"content": {"stem_md": "题干修复草稿"}},
            draft_answer_spec=None,
            base_question_v2={"content": {"stem_md": "题干"}},
            base_answer_spec=None,
            diff=[{"path": "question_v2.content.stem_md", "change": "changed", "before": "题干", "after": "题干修复草稿"}],
        )
    )
    db.add(AnswerSpec(question_id="q-source", status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}))
    db.add(QuestionTag(question_id="q-source", tag_id="primary:number", role="level_1", raw_label="数论"))
    db.add(PracticeSession(id="s1", mode="practice", config={}, question_ids=["q-source"]))
    db.add(Submission(id="sub1", session_id="s1", question_id="q-source", answer_text="1", is_correct=True, grading_result={"is_correct": True}))
    db.commit()
    return db


def test_question_bank_export_includes_imported_questions_and_answers():
    db = seeded_bank_db()

    exported = export_question_bank(db)

    assert exported.schema_version == "question_bank_transfer_v1"
    assert exported.table_counts["questions"] == 1
    assert exported.tables["questions"][0]["id"] == "q-source"
    assert exported.tables["answer_specs"][0]["canonical_answer"] == "1"
    assert exported.tables["question_versions"][0]["question_v2"]["content"]["stem_md"] == "题干"
    assert exported.tables["question_repair_records"][0]["base_version_id"] == "v-source"
    assert "submissions" not in exported.tables


def test_question_bank_import_rebuilds_bank_and_maps_source_files():
    db = seeded_bank_db()
    exported = export_question_bank(db)

    engine2 = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine2)
    db2 = Session(engine2)
    db2.add(SourceFile(id="sf-existing", filename="source.json", sha256="0" * 64, question_count=0))
    db2.commit()

    result = import_question_bank(exported, db=db2)
    imported_question = db2.get(Question, "q-source")

    assert result.status == "imported"
    assert result.imported_counts["questions"] == 1
    assert imported_question is not None
    assert imported_question.source_file_id == "sf-existing"
    assert db2.scalar(select(AnswerSpec).where(AnswerSpec.question_id == "q-source")).canonical_answer == "1"
    assert db2.get(SourceFile, "sf-existing").question_count == 1


def test_question_bank_import_keeps_same_filename_different_hash_as_distinct_source():
    db = seeded_bank_db()
    exported = export_question_bank(db)

    engine2 = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine2)
    db2 = Session(engine2)
    db2.add(SourceFile(id="sf-existing", filename="source.json", sha256="f" * 64, question_count=0))
    db2.commit()

    result = import_question_bank(exported, db=db2)
    imported_question = db2.get(Question, "q-source")

    assert result.status == "imported"
    assert result.conflict_report[0]["kind"] == "filename_hash_conflict"
    assert result.conflict_report[0]["filename"] == "source.json"
    assert db2.get(SourceFile, "sf-existing").sha256 == "f" * 64
    assert imported_question.source_file_id != "sf-existing"
    assert db2.get(SourceFile, imported_question.source_file_id).filename == "source.json#sha256-000000000000"


def test_question_bank_import_dry_run_rolls_back_writes_but_returns_counts():
    db = seeded_bank_db()
    exported = export_question_bank(db)

    engine2 = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine2)
    db2 = Session(engine2)

    result = import_question_bank(exported, dry_run=True, db=db2)

    assert result.status == "dry_run"
    assert result.dry_run is True
    assert result.imported_counts["questions"] == 1
    assert db2.get(Question, "q-source") is None
    assert db2.scalar(select(SourceFile)) is None


def test_question_bank_import_skips_same_question_id_with_different_content():
    db = seeded_bank_db()
    exported = export_question_bank(db)

    db2 = seeded_bank_db()
    existing_question = db2.get(Question, "q-source")
    existing_question.stem_preview = "另一道题"
    existing_question.exact_hash = "existing-exact"
    existing_answer = db2.scalar(select(AnswerSpec).where(AnswerSpec.question_id == "q-source"))
    existing_answer.canonical_answer = "9"
    existing_version = db2.get(QuestionVersion, "v-source")
    existing_version.content_hash = "different-content"
    db2.commit()

    result = import_question_bank(exported, db=db2)

    assert result.status == "imported"
    assert result.imported_counts["questions"] == 0
    assert result.skipped_rows["questions"] == 1
    assert result.skipped_rows["question_versions"] == 1
    assert result.skipped_rows["answer_specs"] == 1
    assert result.conflict_report[0]["kind"] == "question_id_content_conflict"
    assert db2.get(Question, "q-source").stem_preview == "另一道题"
    assert db2.scalar(select(AnswerSpec).where(AnswerSpec.question_id == "q-source")).canonical_answer == "9"
    assert db2.get(QuestionVersion, "v-source").content_hash == "different-content"


def test_question_bank_import_existing_same_question_is_idempotent():
    db = seeded_bank_db()
    exported = export_question_bank(db)
    db2 = seeded_bank_db()

    result = import_question_bank(exported, db=db2)

    assert result.status == "imported"
    assert result.imported_counts["questions"] == 1
    assert result.imported_counts["answer_specs"] == 1
    assert result.conflict_report == []
    assert db2.scalar(select(AnswerSpec).where(AnswerSpec.question_id == "q-source")).canonical_answer == "1"


def test_question_bank_import_maps_question_repair_version_references():
    db = seeded_bank_db()
    exported = export_question_bank(db)

    engine2 = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine2)
    db2 = Session(engine2)
    db2.add(SourceFile(id="sf-existing", filename="source.json", sha256="0" * 64, question_count=1))
    db2.add(Question(id="q-source", source_file_id="sf-existing", status="verified", source_name="source.json", stem_preview="题干", difficulty_star=3, premium=False))
    db2.add(QuestionVersion(id="local-v1", question_id="q-source", version=1, raw_json={"id": "q-source"}, question_v2={"content": {"stem_md": "题干"}}, content_hash="a"))
    db2.add(AnswerSpec(question_id="q-source", status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}))
    db2.commit()

    result = import_question_bank(exported, db=db2)
    repair = db2.get(QuestionRepairRecord, "repair-source")

    assert result.status == "imported"
    assert repair is not None
    assert repair.base_version_id == "local-v1"


def test_question_bank_export_import_preserves_duplicate_aliases():
    db = seeded_bank_db()
    db.add(Question(id="q-alias", source_file_id="sf-source", status="retired", publish_status="duplicate_alias", eligible_for_practice=False, eligible_for_test=False, source_name="source.json", stem_preview="重复题", difficulty_star=3, premium=False))
    db.add(QuestionDuplicateAlias(id="alias-1", canonical_question_id="q-source", alias_question_id="q-alias", status="active", reason="same problem", metadata_json={"note": "manual merge"}))
    db.commit()

    exported = export_question_bank(db)
    assert exported.table_counts["question_duplicate_aliases"] == 1

    engine2 = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine2)
    db2 = Session(engine2)

    result = import_question_bank(exported, db=db2)
    relation = db2.get(QuestionDuplicateAlias, "alias-1")
    alias_question = db2.get(Question, "q-alias")

    assert result.status == "imported"
    assert result.imported_counts["question_duplicate_aliases"] == 1
    assert relation is not None
    assert relation.canonical_question_id == "q-source"
    assert relation.alias_question_id == "q-alias"
    assert alias_question.publish_status == "duplicate_alias"


def retire_questions_challenge_payload(db: Session, question_ids: list[str]) -> dict[str, str]:
    challenge = create_destructive_challenge(DestructiveChallengeRequest(action="retire_questions", question_ids=question_ids), db)
    return {"challenge_id": challenge.challenge_id, "challenge_phrase": challenge.phrase}


def test_delete_questions_retires_question_and_preserves_history_snapshots():
    db = seeded_bank_db()
    db.add(GeneratedCandidate(job_id="job-source", strategy="test", status="accepted", payload={}, published_question_id="q-source"))
    db.add(ReviewQueueItem(id="rq-source", question_id="q-source", reason_code="manual_review", severity="medium", status="open", payload={}))
    db.commit()

    with pytest.raises(Exception):
        delete_questions(QuestionDeleteRequest(question_ids=["q-source"]), db)

    result = delete_questions(QuestionDeleteRequest(**retire_questions_challenge_payload(db, ["q-source"]), question_ids=["q-source"]), db)
    retired = db.get(Question, "q-source")

    assert result.status == "retired"
    assert result.deleted_counts["questions"] == 0
    assert result.updated_counts["questions_retired"] == 1
    assert retired is not None
    assert retired.status == "retired"
    assert retired.eligible_for_practice is False
    assert retired.eligible_for_test is False
    assert db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == "q-source")) is not None
    assert db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == "q-source")) is not None
    assert db.scalar(select(QuestionTag).where(QuestionTag.question_id == "q-source")) is not None
    assert db.scalar(select(Submission).where(Submission.question_id == "q-source")) is not None
    assert db.get(PracticeSession, "s1").question_ids == ["q-source"]
    assert db.get(SourceFile, "sf-source").question_count == 1
    assert db.scalar(select(GeneratedCandidate).where(GeneratedCandidate.published_question_id == "q-source")).publish_status == "retired_question"
    assert db.get(ReviewQueueItem, "rq-source").status == "closed"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "admin.questions_retired")) is not None
    assert list_questions(limit=50, offset=0, db=db) == []
    assert [question.id for question in list_questions(status="retired", limit=50, offset=0, db=db)] == ["q-source"]


def test_delete_questions_challenge_rejects_different_target():
    db = seeded_bank_db()
    db.add(
        Question(
            id="q-other",
            source_file_id="sf-source",
            status="verified",
            source_name="source.json",
            stem_preview="other",
            difficulty_star=3,
            premium=False,
        )
    )
    db.commit()
    payload = retire_questions_challenge_payload(db, ["q-source"])

    with pytest.raises(Exception) as exc_info:
        delete_questions(QuestionDeleteRequest(**payload, question_ids=["q-other"]), db)

    assert getattr(exc_info.value, "status_code", None) == 409
    assert db.get(Question, "q-source").status == "verified"
    assert db.get(Question, "q-other").status == "verified"
