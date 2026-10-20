from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AnswerEvent, PracticeSession, Question, QuestionTag, Submission, TaxonomyTag, utc_now
from .practice_state import normalize_answer_text

MASTERY_SCHEMA_VERSION = "mastery_profile_v1"


def sample_confidence(sample_size: int) -> str:
    if sample_size >= 5:
        return "high"
    if sample_size >= 2:
        return "medium"
    if sample_size == 1:
        return "low"
    return "none"


def clamp_score(value: float) -> int:
    return max(0, min(100, int(round(value))))


def effective_wrong_count(attempts: list[Submission]) -> int:
    seen: set[str] = set()
    count = 0
    for row in attempts:
        normalized = normalize_answer_text(row.answer_text)
        if normalized in seen:
            continue
        seen.add(normalized)
        grading = row.grading_result or {}
        explicitly_effective = grading.get("effective_wrong")
        if row.is_correct is False and not bool(row.excluded_from_mastery) and (bool(row.effective_wrong) or explicitly_effective is not False):
            count += 1
    return count


def days_since(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    return max(0, int((now - value).total_seconds() // 86400))


def next_review_for_evidence(evidence: dict[str, Any], now: datetime) -> tuple[datetime | None, list[str]]:
    reasons: list[str] = []
    last_at = evidence.get("last_answered_at")
    last_correct = evidence.get("final_correct") is True
    revealed = bool(evidence.get("revealed"))
    wrong_count = int(evidence.get("effective_wrong_count") or 0)
    max_help = int(evidence.get("max_help_level") or 0)
    sample_size = int(evidence.get("attempt_count") or 0)
    if last_at is None:
        return None, ["unobserved"]
    if revealed:
        reasons.append("answer_revealed_short_interval")
        return last_at + timedelta(days=1), reasons
    if wrong_count > 0 or not last_correct:
        reasons.append("wrong_answer_short_interval")
        return last_at + timedelta(days=1), reasons
    if max_help >= 3:
        reasons.append("heavy_hint_requires_check")
        return last_at + timedelta(days=2), reasons
    if max_help > 0:
        reasons.append("hint_used")
        return last_at + timedelta(days=3), reasons
    if sample_size >= 3 and last_correct:
        reasons.append("independent_correct_long_interval")
        return last_at + timedelta(days=14), reasons
    reasons.append("independent_correct_medium_interval")
    return last_at + timedelta(days=7), reasons


def mastery_level(score: int, sample_size: int, final_accuracy: float | None, revealed_count: int) -> str:
    if sample_size <= 0:
        return "unobserved"
    if score >= 80 and (final_accuracy or 0) >= 0.8 and revealed_count == 0:
        return "stable"
    if score >= 60:
        return "initial"
    return "unstable"


def tag_star_score(row: dict[str, Any], now: datetime) -> tuple[int, list[str], datetime | None]:
    sample_size = int(row["sample_size"])
    if sample_size <= 0:
        return 0, ["unobserved"], None
    first_accuracy = row["first_correct"] / sample_size
    final_accuracy = row["final_correct"] / sample_size
    wrong_rate = row["effective_wrong_total"] / sample_size
    help_penalty = min(20, row["max_help_level"] * 4)
    reveal_penalty = min(35, row["revealed_count"] * 18)
    mastery_bonus = min(15, row["mastery_check_passed"] * 8)
    recency_days = days_since(row["last_answered_at"], now) or 0
    recency_penalty = 15 if recency_days >= 60 else 10 if recency_days >= 30 else 5 if recency_days >= 14 else 0
    score = 45 + first_accuracy * 25 + final_accuracy * 20 - wrong_rate * 25 - help_penalty - reveal_penalty - recency_penalty + mastery_bonus
    reasons = []
    if first_accuracy < 0.6:
        reasons.append("first_accuracy_low")
    if final_accuracy < 0.8:
        reasons.append("final_accuracy_low")
    if row["effective_wrong_total"] > 0:
        reasons.append("effective_wrong_seen")
    if row["max_help_level"] >= 3:
        reasons.append("heavy_hint_used")
    elif row["max_help_level"] > 0:
        reasons.append("hint_used")
    if row["revealed_count"] > 0:
        reasons.append("answer_revealed")
    if recency_penalty:
        reasons.append("stale_practice")
    if row["mastery_check_passed"] > 0:
        reasons.append("mastery_check_passed")
    if not reasons:
        reasons.append("independent_correct")
    next_review_values = [value for value in row["next_review_values"] if value is not None]
    return clamp_score(score), reasons, min(next_review_values) if next_review_values else None


def build_mastery_payload(db: Session, learner_id: str, now: datetime | None = None, limit: int = 80) -> dict[str, Any]:
    now = now or utc_now()
    limit = max(1, min(300, int(limit or 80)))
    submissions = list(
        db.scalars(
            select(Submission)
            .join(PracticeSession, PracticeSession.id == Submission.session_id)
            .join(Question, Question.id == Submission.question_id)
            .where(PracticeSession.learner_id == learner_id, Question.question_fault_status == "none")
            .order_by(Submission.created_at, Submission.id)
        )
    )
    question_ids = sorted({row.question_id for row in submissions})
    tag_rows = []
    if question_ids:
        tag_rows = db.execute(
            select(QuestionTag.question_id, TaxonomyTag.id, TaxonomyTag.kind, TaxonomyTag.label)
            .join(TaxonomyTag, TaxonomyTag.id == QuestionTag.tag_id)
            .where(QuestionTag.question_id.in_(question_ids))
        ).all()
    tags_by_question: dict[str, list[dict[str, str]]] = defaultdict(list)
    for question_id, tag_id, kind, label in tag_rows:
        tags_by_question[str(question_id)].append({"tag_id": str(tag_id), "kind": str(kind), "label": str(label)})

    events = []
    if question_ids:
        events = list(
            db.scalars(
                select(AnswerEvent)
                .join(PracticeSession, PracticeSession.id == AnswerEvent.session_id)
                .where(PracticeSession.learner_id == learner_id, AnswerEvent.question_id.in_(question_ids))
                .order_by(AnswerEvent.created_at, AnswerEvent.id)
            )
        )
    events_by_session_question: dict[tuple[str, str], list[AnswerEvent]] = defaultdict(list)
    for event in events:
        events_by_session_question[(event.session_id, event.question_id)].append(event)

    attempts_by_session_question: dict[tuple[str, str], list[Submission]] = defaultdict(list)
    for submission in submissions:
        attempts_by_session_question[(submission.session_id, submission.question_id)].append(submission)

    question_evidence: list[dict[str, Any]] = []
    by_tag_star: dict[tuple[str, int], dict[str, Any]] = {}
    for (session_id, question_id), attempts in attempts_by_session_question.items():
        question = db.get(Question, question_id)
        if question is None or question.difficulty_star is None:
            continue
        attempts = sorted(attempts, key=lambda row: (row.created_at, row.id))
        first = attempts[0]
        final = attempts[-1]
        item_events = events_by_session_question.get((session_id, question_id), [])
        revealed = any(event.event_type in {"solution_revealed", "answer_reveal_debt_created", "answer_locked_by_solution_reveal"} for event in item_events) or any(row.solution_revealed for row in attempts)
        max_help = max([int((event.payload or {}).get("level") or 0) for event in item_events if event.event_type == "help_revealed"] + [int(row.help_level_reached or 0) for row in attempts], default=0)
        mastery_passed = any(event.event_type == "mastery_check_completed" for event in item_events)
        evidence = {
            "session_id": session_id,
            "question_id": question_id,
            "source_name": question.source_name,
            "difficulty_star": int(question.difficulty_star),
            "stem_preview": question.stem_preview,
            "attempt_count": len(attempts),
            "first_correct": first.is_correct,
            "final_correct": final.is_correct,
            "effective_wrong_count": effective_wrong_count(attempts),
            "max_help_level": max_help,
            "revealed": revealed,
            "mastery_check_passed": mastery_passed,
            "last_answered_at": final.created_at,
            "last_answered_at_iso": final.created_at.isoformat(),
        }
        next_review_at, review_reasons = next_review_for_evidence(evidence, now)
        evidence["last_answered_at"] = final.created_at.isoformat()
        evidence["next_review_at"] = next_review_at.isoformat() if next_review_at else None
        evidence["review_reason_codes"] = review_reasons
        evidence["due"] = bool(next_review_at and next_review_at <= now)
        question_evidence.append(evidence)
        for tag in tags_by_question.get(question_id, []):
            key = (tag["tag_id"], int(question.difficulty_star))
            row = by_tag_star.setdefault(
                key,
                {
                    "tag_id": tag["tag_id"],
                    "kind": tag["kind"],
                    "label": tag["label"],
                    "difficulty_star": int(question.difficulty_star),
                    "sample_size": 0,
                    "first_correct": 0,
                    "final_correct": 0,
                    "effective_wrong_total": 0,
                    "max_help_level": 0,
                    "revealed_count": 0,
                    "mastery_check_passed": 0,
                    "last_answered_at": None,
                    "next_review_values": [],
                    "question_ids": set(),
                },
            )
            row["sample_size"] += 1
            row["first_correct"] += 1 if first.is_correct is True else 0
            row["final_correct"] += 1 if final.is_correct is True else 0
            row["effective_wrong_total"] += int(evidence["effective_wrong_count"])
            row["max_help_level"] = max(int(row["max_help_level"]), max_help)
            row["revealed_count"] += 1 if revealed else 0
            row["mastery_check_passed"] += 1 if mastery_passed else 0
            row["last_answered_at"] = max(row["last_answered_at"], final.created_at) if row["last_answered_at"] else final.created_at
            row["next_review_values"].append(next_review_at)
            row["question_ids"].add(question_id)

    mastery_rows: list[dict[str, Any]] = []
    level_counts: Counter[str] = Counter()
    for row in by_tag_star.values():
        score, reasons, next_review_at = tag_star_score(row, now)
        sample_size = int(row["sample_size"])
        final_accuracy = row["final_correct"] / sample_size if sample_size else None
        level = mastery_level(score, sample_size, final_accuracy, int(row["revealed_count"]))
        level_counts[level] += 1
        mastery_rows.append(
            {
                "tag_id": row["tag_id"],
                "kind": row["kind"],
                "label": row["label"],
                "difficulty_star": row["difficulty_star"],
                "score": score,
                "mastery_level": level,
                "confidence": sample_confidence(sample_size),
                "sample_size": sample_size,
                "first_accuracy": row["first_correct"] / sample_size if sample_size else None,
                "final_accuracy": final_accuracy,
                "effective_wrong_total": int(row["effective_wrong_total"]),
                "max_help_level": int(row["max_help_level"]),
                "revealed_count": int(row["revealed_count"]),
                "mastery_check_passed": int(row["mastery_check_passed"]),
                "last_answered_at": row["last_answered_at"].isoformat() if row["last_answered_at"] else None,
                "next_review_at": next_review_at.isoformat() if next_review_at else None,
                "reason_codes": reasons,
                "question_count": len(row["question_ids"]),
            }
        )
    mastery_rows.sort(key=lambda row: (row["mastery_level"] == "stable", row["score"], row["next_review_at"] or "9999", -row["sample_size"]))
    due_questions = sorted(
        [row for row in question_evidence if row["due"]],
        key=lambda row: (row["next_review_at"] or "", -(row["effective_wrong_count"] or 0), row["question_id"]),
    )
    return {
        "schema_version": MASTERY_SCHEMA_VERSION,
        "learner_id": learner_id,
        "generated_at": now.isoformat(),
        "summary": {
            "observed_tag_star_count": len(mastery_rows),
            "due_question_count": len(due_questions),
            "level_counts": {key: int(level_counts.get(key, 0)) for key in ["unobserved", "unstable", "initial", "stable"]},
        },
        "mastery": mastery_rows[:limit],
        "due_questions": due_questions[:limit],
    }


def build_today_tasks(db: Session, learner_id: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    payload = build_mastery_payload(db, learner_id, now=now, limit=120)
    tasks: list[dict[str, Any]] = []
    due_questions = payload["due_questions"]
    if due_questions:
        tasks.append(
            {
                "task_id": "due_review",
                "task_type": "review_due_questions",
                "title": f"复习 {min(5, len(due_questions))} 道到期错题/答案债务",
                "question_count": min(5, len(due_questions)),
                "reason_codes": ["spaced_review_due"],
                "question_ids": [row["question_id"] for row in due_questions[:5]],
                "priority": "high",
            }
        )
    weak_rows = [row for row in payload["mastery"] if row["mastery_level"] in {"unstable", "initial"} and row["confidence"] != "none"]
    if weak_rows:
        weakest = weak_rows[0]
        tasks.append(
            {
                "task_id": f"tag_star_check:{weakest['tag_id']}:{weakest['difficulty_star']}",
                "task_type": "tag_star_mastery_check",
                "title": f"做 3 道 {weakest['label']} × {weakest['difficulty_star']} 星检测题",
                "question_count": 3,
                "target_tags": [weakest["tag_id"]],
                "difficulty_star": weakest["difficulty_star"],
                "reason_codes": weakest["reason_codes"],
                "priority": "medium",
            }
        )
    unfinished_sessions = list(
        db.scalars(
            select(PracticeSession)
            .where(PracticeSession.learner_id == learner_id, PracticeSession.status == "active")
            .order_by(PracticeSession.updated_at.desc(), PracticeSession.id.desc())
            .limit(20)
        )
    )
    for session in unfinished_sessions:
        answered = {
            row[0]
            for row in db.execute(select(Submission.question_id).where(Submission.session_id == session.id)).all()
        }
        remaining = max(0, len(session.question_ids or []) - len(answered))
        if remaining <= 0:
            continue
        tasks.append(
            {
                "task_id": f"continue_session:{session.id}",
                "task_type": "continue_unfinished_session",
                "title": f"继续未完成的{session.mode}，还剩 {remaining} 题",
                "session_id": session.id,
                "question_count": remaining,
                "reason_codes": ["unfinished_session"],
                "priority": "medium" if len(tasks) < 2 else "low",
            }
        )
        break
    if not tasks:
        tasks.append(
            {
                "task_id": "fresh_balanced_practice",
                "task_type": "fresh_practice",
                "title": "开始 5 道均衡练习",
                "question_count": 5,
                "reason_codes": ["no_due_items"],
                "priority": "low",
            }
        )
    return {
        "schema_version": "today_tasks_v1",
        "learner_id": learner_id,
        "generated_at": now.isoformat(),
        "tasks": tasks[:5],
        "mastery_summary": payload["summary"],
    }
