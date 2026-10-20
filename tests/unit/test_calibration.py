from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app.calibration import apply_calibration, calibrate_evaluation
from apps.api.app.db import Base
from apps.api.app.models import Question, QuestionVersion, SourceFile


def test_calibrate_evaluation_uses_scores_without_overwriting_source_fields():
    calibration = calibrate_evaluation(
        {
            "difficulty_star": 1,
            "total_score": 12,
            "premium": False,
            "premium_score": 6,
        }
    )

    assert calibration["source_difficulty_star"] == 1
    assert calibration["calibrated_difficulty_star"] == 5
    assert calibration["source_premium"] is False
    assert calibration["calibrated_premium"] is True
    assert calibration["calibration_version"] == "deterministic_score_threshold_v1"


def test_apply_calibration_updates_current_question_version():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=1)
    question = Question(
        id="q1",
        source_file_id="sf1",
        status="verified",
        source_name="source.json",
        stem_preview="题",
        difficulty_star=1,
        premium=False,
        calibrated_difficulty_star=1,
        calibrated_premium=False,
    )
    db.add(source)
    db.add(question)
    db.flush()
    version = QuestionVersion(
        question_id="q1",
        version=1,
        raw_json={"difficulty_quality_evaluation": {"difficulty_star": 1, "total_score": 12, "premium": False, "premium_score": 6}},
        question_v2={},
        content_hash="hash",
    )
    db.add(version)
    db.flush()
    question.current_version_id = version.id
    db.commit()

    stats = apply_calibration(db)
    saved = db.get(Question, "q1")

    assert stats == {"seen": 1, "updated": 1, "changed_star": 1, "changed_premium": 1}
    assert saved.calibrated_difficulty_star == 5
    assert saved.calibrated_premium is True
    assert saved.calibration_version == "deterministic_score_threshold_v1"
