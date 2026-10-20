import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import AuditLog, Question, QuestionDuplicateAlias, ReviewQueueItem, SourceFile
from apps.api.app.routers.questions import QuestionDuplicateMergeRequest, list_question_duplicate_aliases, merge_question_duplicates


def duplicate_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add(SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=3))
    db.add_all(
        [
            Question(id="q-canonical", source_file_id="sf1", status="verified", source_name="source.json", stem_preview="canonical stem", difficulty_star=3, premium=False),
            Question(id="q-alias", source_file_id="sf1", status="verified", source_name="source.json", stem_preview="alias stem", difficulty_star=3, premium=False),
            Question(id="q-other", source_file_id="sf1", status="verified", source_name="source.json", stem_preview="other stem", difficulty_star=3, premium=False),
        ]
    )
    db.add(ReviewQueueItem(id="rq-dup", question_id="q-alias", reason_code="duplicate_question", severity="medium", status="open", payload={"source": "test"}))
    db.commit()
    return db


def test_merge_question_duplicates_retires_alias_and_preserves_relation():
    db = duplicate_db()

    result = merge_question_duplicates(
        QuestionDuplicateMergeRequest(canonical_question_id="q-canonical", alias_question_ids=["q-alias"], reason="same stem"),
        db,
    )
    alias_question = db.get(Question, "q-alias")
    relation = db.scalar(select(QuestionDuplicateAlias).where(QuestionDuplicateAlias.alias_question_id == "q-alias"))
    review = db.get(ReviewQueueItem, "rq-dup")

    assert result["status"] == "merged"
    assert result["created_count"] == 1
    assert result["retired_alias_ids"] == ["q-alias"]
    assert alias_question.status == "retired"
    assert alias_question.publish_status == "duplicate_alias"
    assert alias_question.eligible_for_practice is False
    assert alias_question.eligible_for_test is False
    assert relation.canonical_question_id == "q-canonical"
    assert relation.metadata_json["previous_alias_question_state"]["publish_status"] == "source_verified"
    assert review.status == "resolved"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "question_duplicate_aliases.merged")) is not None

    aliases = list_question_duplicate_aliases("q-canonical", db)
    assert aliases["aliases"][0]["alias_question_id"] == "q-alias"
    assert aliases["canonical_alias"] is None


def test_merge_question_duplicates_is_idempotent_for_same_canonical():
    db = duplicate_db()

    first = merge_question_duplicates(QuestionDuplicateMergeRequest(canonical_question_id="q-canonical", alias_question_ids=["q-alias"]), db)
    second = merge_question_duplicates(QuestionDuplicateMergeRequest(canonical_question_id="q-canonical", alias_question_ids=["q-alias"]), db)

    assert first["created_count"] == 1
    assert second["created_count"] == 0
    assert db.scalar(select(QuestionDuplicateAlias).where(QuestionDuplicateAlias.alias_question_id == "q-alias")).canonical_question_id == "q-canonical"
    assert len(list(db.scalars(select(QuestionDuplicateAlias)))) == 1


def test_merge_question_duplicates_rejects_conflicting_canonical():
    db = duplicate_db()
    merge_question_duplicates(QuestionDuplicateMergeRequest(canonical_question_id="q-canonical", alias_question_ids=["q-alias"]), db)

    with pytest.raises(Exception) as excinfo:
        merge_question_duplicates(QuestionDuplicateMergeRequest(canonical_question_id="q-other", alias_question_ids=["q-alias"]), db)

    assert getattr(excinfo.value, "status_code", None) == 409
    assert excinfo.value.detail["code"] == "alias_already_merged_elsewhere"


def test_merge_question_duplicates_rejects_canonical_that_is_already_alias():
    db = duplicate_db()
    merge_question_duplicates(QuestionDuplicateMergeRequest(canonical_question_id="q-canonical", alias_question_ids=["q-alias"]), db)

    with pytest.raises(Exception) as excinfo:
        merge_question_duplicates(QuestionDuplicateMergeRequest(canonical_question_id="q-alias", alias_question_ids=["q-other"]), db)

    assert getattr(excinfo.value, "status_code", None) == 409
    assert excinfo.value.detail["code"] == "canonical_is_alias"
