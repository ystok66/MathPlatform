from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AnswerEvent, Submission


ANSWER_SPLIT_RE = re.compile(r"\s+")


def normalize_answer_text(value: str) -> str:
    return ANSWER_SPLIT_RE.sub(" ", value.strip()).casefold()


def submission_attempt_stats(db: Session, session_id: str, question_id: str, answer_text: str) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(Submission)
            .where(Submission.session_id == session_id, Submission.question_id == question_id)
            .order_by(Submission.created_at, Submission.id)
        )
    )
    normalized = normalize_answer_text(answer_text)
    seen_answers: dict[str, Submission] = {}
    effective_error_count = 0
    for row in rows:
        key = normalize_answer_text(row.answer_text)
        if key in seen_answers:
            continue
        seen_answers[key] = row
        grading = row.grading_result or {}
        explicitly_effective = grading.get("effective_wrong")
        should_count_wrong = (
            row.is_correct is False
            and not bool(getattr(row, "excluded_from_mastery", False))
            and (bool(getattr(row, "effective_wrong", False)) or explicitly_effective is not False)
        )
        if should_count_wrong:
            effective_error_count += 1
    duplicate_of = seen_answers.get(normalized)
    return {
        "raw_attempt_index": len(rows) + 1,
        "effective_attempt_index": len(seen_answers) + (0 if duplicate_of else 1),
        "effective_error_count_before": effective_error_count,
        "duplicate_of_submission_id": duplicate_of.id if duplicate_of else None,
    }


def session_interaction_state(db: Session, session_id: str) -> dict[str, Any]:
    events = list(
        db.scalars(
            select(AnswerEvent)
            .where(AnswerEvent.session_id == session_id)
            .order_by(AnswerEvent.created_at, AnswerEvent.id)
        )
    )
    skipped: set[str] = set()
    bookmarked: set[str] = set()
    uncertain: set[str] = set()
    feedback: list[dict[str, Any]] = []
    for event in events:
        if not event.question_id:
            continue
        if event.event_type == "skipped":
            skipped.add(event.question_id)
        elif event.event_type == "unskipped":
            skipped.discard(event.question_id)
        elif event.event_type == "bookmarked":
            bookmarked.add(event.question_id)
        elif event.event_type == "bookmark_removed":
            bookmarked.discard(event.question_id)
        elif event.event_type == "marked_uncertain":
            uncertain.add(event.question_id)
        elif event.event_type == "uncertain_removed":
            uncertain.discard(event.question_id)
        elif event.event_type == "feedback":
            feedback.append({"question_id": event.question_id, "payload": event.payload})
    return {
        "skipped_question_ids": sorted(skipped),
        "bookmarked_question_ids": sorted(bookmarked),
        "uncertain_question_ids": sorted(uncertain),
        "feedback": feedback[-20:],
    }
