from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from .localization import current_language, normalize_language

from .models import PracticeSession, Question, QuestionTag, Submission, TaxonomyTag, WrongQuestionRecord


def _accuracy(correct: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(correct / total, 4)


def build_learner_profile(db: Session, session_id: str) -> dict[str, Any]:
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise ValueError(f"source session not found: {session_id}")
    submissions = list(db.scalars(select(Submission).where(Submission.session_id == session_id).order_by(Submission.created_at, Submission.id)))
    latest_by_question: dict[str, Submission] = {}
    for submission in submissions:
        latest_by_question[submission.question_id] = submission
    finals = list(latest_by_question.values())
    correct_count = sum(1 for row in finals if row.is_correct is True)
    wrong_ids = [row.question_id for row in finals if row.is_correct is False]
    wrong_records = list(db.scalars(select(WrongQuestionRecord).where(WrongQuestionRecord.session_id == session_id)))
    error_types = Counter(row.error_type or "unknown" for row in wrong_records)

    weak_tags: Counter[str] = Counter()
    tag_meta: dict[str, dict[str, str]] = {}
    if wrong_ids:
        tag_rows = db.execute(
            select(TaxonomyTag.id, TaxonomyTag.kind, TaxonomyTag.label)
            .join(QuestionTag, QuestionTag.tag_id == TaxonomyTag.id)
            .where(QuestionTag.question_id.in_(wrong_ids))
        ).all()
        for tag_id, kind, label in tag_rows:
            weak_tags[tag_id] += 1
            tag_meta[tag_id] = {"tag_id": tag_id, "kind": kind, "label": label}

    stars = [int(question.difficulty_star or 0) for question in db.scalars(select(Question).where(Question.id.in_(wrong_ids))).all() if question.difficulty_star]
    avg_wrong_star = round(sum(stars) / len(stars), 2) if stars else None
    recommended_star = None
    session_accuracy = _accuracy(correct_count, len(finals))
    if avg_wrong_star is not None:
        recommended_star = max(1, min(5, int(round(avg_wrong_star))))
        if session_accuracy is not None and session_accuracy < 0.45:
            recommended_star = max(1, recommended_star - 1)

    elapsed_values = [row.elapsed_ms for row in finals if row.elapsed_ms]
    return {
        "schema_version": "learner_profile_v1",
        "source_session_id": session_id,
        "mode": session.mode,
        "answered_count": len(finals),
        "correct_count": correct_count,
        "accuracy": session_accuracy,
        "avg_elapsed_ms": round(sum(elapsed_values) / len(elapsed_values), 2) if elapsed_values else None,
        "weak_error_types": [{"error_type": key, "count": count} for key, count in error_types.most_common(5)],
        "weak_tags": [
            {**tag_meta.get(key, {"tag_id": key, "kind": "unknown", "label": key}), "count": count}
            for key, count in weak_tags.most_common(8)
        ],
        "avg_wrong_star": avg_wrong_star,
        "recommended_difficulty_star": recommended_star,
    }


def plan_generation_request(db: Session, request: dict[str, Any]) -> dict[str, Any]:
    planned = dict(request)
    planned["language"] = normalize_language(planned.get("language") or current_language())
    session_id = planned.get("source_session_id")
    if not session_id:
        return planned
    profile = build_learner_profile(db, session_id)
    derived_tags = [row["tag_id"] for row in profile["weak_tags"]]
    explicit_tags = list(planned.get("target_tags") or [])
    explicit_star = planned.get("difficulty_star")
    if not explicit_tags and derived_tags:
        planned["target_tags"] = derived_tags[:5]
    if explicit_star is None and profile["recommended_difficulty_star"] is not None:
        planned["difficulty_star"] = profile["recommended_difficulty_star"]
    planned["learner_profile"] = profile
    planned["planning"] = {
        "schema_version": "generation_planning_v1",
        "source": "learner_profile" if profile["answered_count"] else "explicit_request",
        "used_profile": True,
        "target_tags_source": "explicit" if explicit_tags else ("weak_tags" if derived_tags else "empty"),
        "difficulty_source": "explicit" if explicit_star is not None else ("profile" if profile["recommended_difficulty_star"] is not None else "empty"),
        "constraints": planned.get("constraints"),
    }
    return planned
