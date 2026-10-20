from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.importer import import_question_files, map_question_v2, parse_question_files
from apps.api.app.models import AnswerSpec, AuditLog, Question, ReviewQueueItem, TaxonomyAlias, TaxonomyTag
from apps.api.app.routers.import_routes import import_json_files


DATA_DIR = Path("做题项目")


def test_parse_question_files_fast_sample_counts():
    parsed = parse_question_files(DATA_DIR)
    sample = []
    for _, data in parsed:
        sample.extend(data["questions"])
        if len(sample) >= 10:
            break
    assert len(parsed) == 6
    assert len(sample[:10]) == 10
    assert all("answer_spec" in question for question in sample[:10])


@pytest.mark.slow
def test_parse_question_files_full_counts():
    parsed = parse_question_files(DATA_DIR)
    statuses = {"verified": 0, "needs_review": 0}
    total = 0
    for _, data in parsed:
        questions = data["questions"]
        total += len(questions)
        for q in questions:
            statuses[q["answer_spec"]["status"]] += 1
    assert total == 3041
    assert statuses == {"verified": 2983, "needs_review": 58}


def test_question_v2_mapping_preserves_answer_and_star():
    parsed = parse_question_files(DATA_DIR)
    source_path, data = parsed[0]
    question = data["questions"][0]
    mapped = map_question_v2(question, source_path.name)
    assert mapped["schema_version"] == "question_v2"
    assert mapped["content"]["stem_md"] == question["question_text"]
    assert mapped["answer_spec"]["raw"] == question["answer_spec"]
    assert mapped["evaluation"]["difficulty"]["star"] == question["difficulty_quality_evaluation"]["difficulty_star"]


def test_import_is_idempotent_and_keeps_needs_review_out_of_verified_pool():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        first = import_question_files(db, DATA_DIR, max_questions=10)
        second = import_question_files(db, DATA_DIR, max_questions=10)
        assert first["questions"] == 10
        assert first["verified"] + first["needs_review"] == 10
        assert second == first
        question_count = db.scalar(select(func.count()).select_from(Question))
        answer_count = db.scalar(select(func.count()).select_from(AnswerSpec))
        review_count = db.scalar(select(func.count()).select_from(ReviewQueueItem))
        alias_count = db.scalar(select(func.count()).select_from(TaxonomyAlias))
        verified_pool = list(db.scalars(select(Question.id).join(AnswerSpec, AnswerSpec.question_id == Question.id).where(Question.status == "verified", AnswerSpec.status == "verified")))
        sample = db.scalar(select(Question).where(Question.id == verified_pool[0]))
        tag = db.scalar(select(TaxonomyTag).limit(1))
    assert question_count == 10
    assert answer_count == 10
    assert review_count == first["needs_review"]
    assert alias_count and alias_count > 0
    assert len(verified_pool) == first["verified"]
    assert sample.current_version_id is not None
    assert sample.source_difficulty_star == sample.calibrated_difficulty_star
    assert sample.exact_hash and sample.normalized_hash and sample.family_id
    assert tag.version == "taxonomy_v1"


def test_import_json_files_route_supports_bounded_smoke_import():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        result = import_json_files(data_dir=str(DATA_DIR), max_questions=1, db=db)
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "import.json_files"))

        assert result["questions"] == 1
        assert db.scalar(select(func.count()).select_from(Question)) == 1
        assert audit is not None
        assert audit.payload["max_questions"] == 1
