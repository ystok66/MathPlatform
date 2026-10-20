from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .candidate_quality import difficulty_star_from_score
from .models import Question, QuestionVersion


def numeric_values(value: Any) -> list[int]:
    if isinstance(value, dict):
        values: list[int] = []
        for item in value.values():
            values.extend(numeric_values(item))
        return values
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [int(value)]
    return []


def difficulty_total_score(eval_block: dict[str, Any]) -> int | None:
    if isinstance(eval_block.get("total_score"), (int, float)):
        return int(eval_block["total_score"])
    values = numeric_values(eval_block.get("dimension_scores") or {})
    if values:
        return sum(values)
    return None


def premium_total_score(eval_block: dict[str, Any]) -> int | None:
    if isinstance(eval_block.get("premium_score"), (int, float)):
        return int(eval_block["premium_score"])
    values = numeric_values(eval_block.get("premium_dimension_scores") or {})
    if values:
        return sum(values)
    return None


def calibrate_evaluation(eval_block: dict[str, Any]) -> dict[str, Any]:
    source_star = eval_block.get("difficulty_star")
    total_score = difficulty_total_score(eval_block)
    calibrated_star = difficulty_star_from_score(total_score) if total_score is not None else source_star
    premium_score = premium_total_score(eval_block)
    source_premium = bool(eval_block.get("premium"))
    calibrated_premium = premium_score >= 6 if premium_score is not None else source_premium
    return {
        "source_difficulty_star": source_star,
        "calibrated_difficulty_star": calibrated_star,
        "difficulty_total_score": total_score,
        "source_premium": source_premium,
        "calibrated_premium": calibrated_premium,
        "premium_total_score": premium_score,
        "calibration_version": "deterministic_score_threshold_v1" if total_score is not None or premium_score is not None else "source_original_v1",
    }


def calibrate_question_from_raw(raw_json: dict[str, Any]) -> dict[str, Any]:
    return calibrate_evaluation(raw_json.get("difficulty_quality_evaluation") or {})


def apply_calibration(db: Session, limit: int | None = None) -> dict[str, int]:
    stmt = select(Question, QuestionVersion).join(QuestionVersion, QuestionVersion.id == Question.current_version_id)
    if limit:
        stmt = stmt.limit(limit)
    stats = {"seen": 0, "updated": 0, "changed_star": 0, "changed_premium": 0}
    for question, version in db.execute(stmt).all():
        stats["seen"] += 1
        calibration = calibrate_question_from_raw(version.raw_json)
        old_star = question.calibrated_difficulty_star
        old_premium = question.calibrated_premium
        question.source_difficulty_star = calibration["source_difficulty_star"]
        question.calibrated_difficulty_star = calibration["calibrated_difficulty_star"]
        question.source_premium = calibration["source_premium"]
        question.calibrated_premium = calibration["calibrated_premium"]
        question.calibration_version = calibration["calibration_version"]
        stats["updated"] += 1
        if old_star != question.calibrated_difficulty_star:
            stats["changed_star"] += 1
        if old_premium != question.calibrated_premium:
            stats["changed_premium"] += 1
    return stats
