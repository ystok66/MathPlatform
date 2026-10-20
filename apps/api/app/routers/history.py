from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_audit_event
from ..db import get_db
from ..learner_profiles import resolve_learner_id
from ..models import (
    AnswerEvent,
    GenerationBatch,
    GenerationBatchItem,
    PracticeSession,
    Question,
    QuestionTag,
    QuestionVariant,
    Submission,
    TaxonomyTag,
    TestBlueprint,
    TestRun,
    utc_now,
)
from ..practice_state import normalize_answer_text
from ..routers.reports import classify_error, regrade_meta_from_grading
from ..schemas import CreatePracticeSessionResponse
from ..session_idempotency import find_idempotent_practice_session, practice_session_response

router = APIRouter(prefix="/api/history", tags=["history"])

ReviewScope = Literal["all", "wrong_only", "weak_only", "selected"]

HELP_EVENT_TYPES = {
    "help_revealed",
    "intervention_due",
    "intervention_prefetched",
    "assisted_completion",
    "mastery_check_requested",
    "mastery_check_ready",
    "mastery_check_attempted",
    "mastery_check_completed",
}
REVEAL_EVENT_TYPES = {"solution_revealed", "answer_reveal_debt_created", "answer_locked_by_solution_reveal"}
EXPLANATION_EVENT_TYPES = {"solution_explanation_requested", "solution_explanation_answered"}


class HistoryReviewSessionRequest(BaseModel):
    scope: ReviewScope = "weak_only"
    selected_question_ids: list[str] = Field(default_factory=list)
    learner_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def local_day(value: datetime | None) -> str:
    return value.date().isoformat() if value else date.today().isoformat()


def mode_label(mode: str) -> str:
    return {
        "practice": "普通练习",
        "strict_test": "严格测试",
        "generated_practice": "套题练习",
        "review_practice": "历史重做",
        "diagnostic": "标准诊断",
    }.get(mode, mode)


def group_type_label(group_type: str) -> str:
    return {
        "test_run": "严格测试",
        "generation_batch": "套题出题",
        "mastery_variant_batch": "批量类似题",
        "practice": "普通练习",
        "strict_test": "严格测试",
        "generated_practice": "套题练习",
        "review_practice": "历史重做",
        "diagnostic": "标准诊断",
    }.get(group_type, group_type)


def session_generation_batch_id(session: PracticeSession) -> str | None:
    config = session.config or {}
    selection = config.get("selection") if isinstance(config.get("selection"), dict) else {}
    value = config.get("generation_batch_id") or selection.get("generation_batch_id")
    return str(value) if value else None


def accepted_question_ids_for_batch(db: Session, batch_id: str) -> list[str]:
    rows = db.execute(
        select(GenerationBatchItem.question_id)
        .where(
            GenerationBatchItem.batch_id == batch_id,
            GenerationBatchItem.status == "accepted",
            GenerationBatchItem.question_id.is_not(None),
        )
        .order_by(GenerationBatchItem.item_index, GenerationBatchItem.id)
    ).all()
    return [str(question_id) for (question_id,) in rows if question_id]


def stable_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def session_group_spec(session: PracticeSession) -> dict[str, Any]:
    group_type = session.mode or "practice"
    question_count = len(session.question_ids or [])
    return {
        "id": f"session:{session.id}",
        "group_type": group_type,
        "title": f"{local_day(session.created_at)} {mode_label(group_type)} {question_count}题",
        "mode": session.mode,
        "status": session.status,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "session_id": session.id,
        "session_ids": [session.id],
        "learner_id": session.learner_id,
        "test_run_id": None,
        "generation_batch_id": session_generation_batch_id(session),
        "question_ids": list(session.question_ids or []),
        "report_type": "session",
    }


def test_run_group_spec(db: Session, run: TestRun, learner_id: str | None = None) -> dict[str, Any] | None:
    session = db.get(PracticeSession, run.session_id)
    if session is None:
        return None
    if learner_id and session.learner_id != learner_id:
        return None
    blueprint = db.get(TestBlueprint, run.blueprint_id)
    question_count = len(session.question_ids or [])
    title_name = blueprint.name if blueprint and blueprint.name else "严格测试"
    return {
        "id": f"test_run:{run.id}",
        "group_type": "test_run",
        "title": f"{local_day(run.created_at)} {title_name} {question_count}题",
        "mode": session.mode,
        "status": run.status or session.status,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "session_id": session.id,
        "session_ids": [session.id],
        "learner_id": session.learner_id,
        "test_run_id": run.id,
        "generation_batch_id": None,
        "question_ids": list(session.question_ids or []),
        "report_type": "session",
    }


def batch_group_spec(db: Session, batch: GenerationBatch, sessions: list[PracticeSession]) -> dict[str, Any]:
    request = batch.request if isinstance(batch.request, dict) else {}
    is_mastery_variant_batch = request.get("origin") == "mastery_variant_batch"
    accepted_ids = accepted_question_ids_for_batch(db, batch.id)
    session_question_ids: list[str] = []
    for session in sessions:
        session_question_ids.extend(list(session.question_ids or []))
    question_ids = accepted_ids or stable_unique(session_question_ids)
    shown_count = batch.target_count or len(question_ids)
    learner_ids = sorted({row.learner_id for row in sessions if row.learner_id})
    group_type = "mastery_variant_batch" if is_mastery_variant_batch else "generation_batch"
    title_label = "批量类似题" if is_mastery_variant_batch else "套题出题"
    return {
        "id": f"batch:{batch.id}",
        "group_type": group_type,
        "title": f"{local_day(batch.created_at)} {title_label} {shown_count}题",
        "mode": "generated_practice",
        "status": batch.status,
        "created_at": batch.created_at,
        "updated_at": batch.updated_at,
        "session_id": sessions[0].id if sessions else None,
        "session_ids": [row.id for row in sessions],
        "learner_id": learner_ids[0] if len(learner_ids) == 1 else None,
        "learner_ids": learner_ids,
        "test_run_id": None,
        "generation_batch_id": batch.id,
        "question_ids": question_ids,
        "report_type": "generation_batch",
    }


def history_group_specs(db: Session, limit: int = 200, learner_id: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(500, int(limit or 200)))
    session_stmt = select(PracticeSession).order_by(PracticeSession.created_at.desc(), PracticeSession.id.desc()).limit(limit * 2)
    if learner_id:
        session_stmt = session_stmt.where(PracticeSession.learner_id == learner_id)
    sessions = list(db.scalars(session_stmt))
    runs = list(db.scalars(select(TestRun).order_by(TestRun.created_at.desc(), TestRun.id.desc()).limit(limit)))
    batches = list(db.scalars(select(GenerationBatch).order_by(GenerationBatch.created_at.desc(), GenerationBatch.id.desc()).limit(limit)))

    specs: list[dict[str, Any]] = []
    test_session_ids: set[str] = set()
    for run in runs:
        spec = test_run_group_spec(db, run, learner_id=learner_id)
        if spec is None:
            continue
        test_session_ids.add(str(spec["session_id"]))
        specs.append(spec)

    generated_session_ids: set[str] = set()
    sessions_by_batch: dict[str, list[PracticeSession]] = defaultdict(list)
    for session in sessions:
        batch_id = session_generation_batch_id(session)
        if batch_id:
            sessions_by_batch[batch_id].append(session)
    for batch in batches:
        batch_sessions = sorted(sessions_by_batch.get(batch.id, []), key=lambda row: (row.created_at, row.id))
        if learner_id and not batch_sessions:
            continue
        generated_session_ids.update(row.id for row in batch_sessions)
        specs.append(batch_group_spec(db, batch, batch_sessions))

    for session in sessions:
        if session.id in test_session_ids or session.id in generated_session_ids:
            continue
        specs.append(session_group_spec(session))

    specs.sort(key=lambda row: (row["created_at"] or datetime.min, row["id"]), reverse=True)
    return specs[:limit]


def resolve_group_spec(db: Session, group_id: str, learner_id: str | None = None) -> dict[str, Any]:
    if group_id.startswith("session:"):
        session = db.get(PracticeSession, group_id.removeprefix("session:"))
        if session is None:
            raise HTTPException(status_code=404, detail="history group not found")
        if learner_id and session.learner_id != learner_id:
            raise HTTPException(status_code=404, detail="history group not found")
        return session_group_spec(session)
    if group_id.startswith("test_run:"):
        run = db.get(TestRun, group_id.removeprefix("test_run:"))
        if run is None:
            raise HTTPException(status_code=404, detail="history group not found")
        spec = test_run_group_spec(db, run, learner_id=learner_id)
        if spec is None:
            raise HTTPException(status_code=404, detail="history group session not found")
        return spec
    if group_id.startswith("batch:"):
        batch = db.get(GenerationBatch, group_id.removeprefix("batch:"))
        if batch is None:
            raise HTTPException(status_code=404, detail="history group not found")
        session_stmt = select(PracticeSession).order_by(PracticeSession.created_at, PracticeSession.id)
        if learner_id:
            session_stmt = session_stmt.where(PracticeSession.learner_id == learner_id)
        sessions = list(db.scalars(session_stmt))
        if learner_id and not any(session_generation_batch_id(row) == batch.id for row in sessions):
            raise HTTPException(status_code=404, detail="history group not found")
        return batch_group_spec(db, batch, [row for row in sessions if session_generation_batch_id(row) == batch.id])
    raise HTTPException(status_code=404, detail="history group not found")


def rows_by_question(rows: list[Submission] | list[AnswerEvent]) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        if row.question_id:
            result[row.question_id].append(row)
    return result


def effective_wrong_count(attempts: list[Submission]) -> int:
    seen: set[str] = set()
    count = 0
    for row in attempts:
        key = normalize_answer_text(row.answer_text)
        if key in seen:
            continue
        seen.add(key)
        grading = row.grading_result or {}
        explicitly_effective = grading.get("effective_wrong")
        if row.is_correct is False and not bool(row.excluded_from_mastery) and (bool(row.effective_wrong) or explicitly_effective is not False):
            count += 1
    return count


def item_performance(question: Question | None, attempts: list[Submission], events: list[AnswerEvent]) -> tuple[str, str]:
    skipped = any(row.event_type == "skipped" for row in events)
    revealed = any(row.event_type in REVEAL_EVENT_TYPES for row in events) or any(row.solution_revealed for row in attempts)
    used_hint = any(row.event_type in HELP_EVENT_TYPES for row in events) or any(row.help_level_reached > 0 for row in attempts)
    wrong_count = effective_wrong_count(attempts)
    if question is None:
        return "gray", "原题已删除"
    if question.status != "verified" or not bool(question.eligible_for_practice):
        return "gray", "不可练习"
    if revealed:
        return "red", "看过答案"
    if not attempts:
        if skipped:
            return "yellow", "跳过未答"
        return "gray", "未作答"
    first_correct = attempts[0].is_correct
    final_correct = attempts[-1].is_correct
    if final_correct is False:
        return "red", "最终错误"
    if wrong_count >= 2:
        return "red", "有效错误较多"
    if first_correct is True and final_correct is True and len(attempts) == 1 and not used_hint:
        return "green", "首答正确"
    return "yellow", "最终正确但需复习"


def question_tag_map(db: Session, question_ids: list[str]) -> dict[str, list[dict[str, str]]]:
    if not question_ids:
        return {}
    rows = db.execute(
        select(QuestionTag.question_id, QuestionTag.tag_id, TaxonomyTag.kind, TaxonomyTag.label)
        .join(TaxonomyTag, TaxonomyTag.id == QuestionTag.tag_id)
        .where(QuestionTag.question_id.in_(stable_unique(question_ids)))
        .order_by(QuestionTag.role, TaxonomyTag.kind, TaxonomyTag.label)
    ).all()
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    for question_id, tag_id, kind, label in rows:
        result[str(question_id)].append({"id": str(tag_id), "kind": str(kind), "label": str(label)})
    return result


def mastery_variant_completion_map(db: Session, learner_id: str | None, question_ids: list[str]) -> dict[str, dict[str, Any]]:
    unique_ids = stable_unique(question_ids)
    if not unique_ids:
        return {}
    variant_rows = db.execute(
        select(QuestionVariant.base_question_id, QuestionVariant.variant_question_id).where(
            QuestionVariant.base_question_id.in_(unique_ids),
            QuestionVariant.purpose == "mastery_check",
        )
    ).all()
    variant_ids_by_base: dict[str, list[str]] = defaultdict(list)
    variant_to_base: dict[str, str] = {}
    for base_question_id, variant_question_id in variant_rows:
        base = str(base_question_id)
        variant = str(variant_question_id)
        if variant not in variant_ids_by_base[base]:
            variant_ids_by_base[base].append(variant)
        variant_to_base[variant] = base
    attempted_by_base: dict[str, set[str]] = defaultdict(set)
    if variant_to_base:
        stmt = (
            select(Submission.question_id)
            .join(PracticeSession, PracticeSession.id == Submission.session_id)
            .where(Submission.question_id.in_(list(variant_to_base.keys())))
        )
        if learner_id:
            stmt = stmt.where(PracticeSession.learner_id == learner_id)
        for (variant_question_id,) in db.execute(stmt).all():
            variant = str(variant_question_id)
            base = variant_to_base.get(variant)
            if base:
                attempted_by_base[base].add(variant)
    return {
        base: {
            "generated_count": len(variants),
            "attempted_count": len(attempted_by_base.get(base, set())),
            "question_ids": variants,
        }
        for base, variants in variant_ids_by_base.items()
    }


def group_details(
    db: Session,
    spec: dict[str, Any],
    *,
    source_name: str | None = None,
    difficulty_star: int | None = None,
    tag_id: list[str] | None = None,
    weak_only: bool = False,
) -> dict[str, Any]:
    question_ids = [str(value) for value in spec.get("question_ids", [])]
    unique_ids = stable_unique(question_ids)
    questions = {row.id: row for row in db.scalars(select(Question).where(Question.id.in_(unique_ids))) } if unique_ids else {}
    tag_map = question_tag_map(db, unique_ids)
    session_ids = [str(value) for value in spec.get("session_ids", []) if value]
    marker_learner_id = str(spec.get("learner_id") or "") or None
    if marker_learner_id is None:
        learner_ids = [str(value) for value in spec.get("learner_ids", []) if value]
        if len(learner_ids) == 1:
            marker_learner_id = learner_ids[0]
    mastery_variants_by_question = mastery_variant_completion_map(db, marker_learner_id, unique_ids)

    submissions: list[Submission] = []
    events: list[AnswerEvent] = []
    if session_ids:
        submissions = list(
            db.scalars(
                select(Submission)
                .where(Submission.session_id.in_(session_ids))
                .order_by(Submission.created_at, Submission.id)
            )
        )
        events = list(
            db.scalars(
                select(AnswerEvent)
                .where(
                    AnswerEvent.session_id.in_(session_ids),
                    AnswerEvent.question_id != "",
                    AnswerEvent.event_type.not_like("temporary_hint_%"),
                )
                .order_by(AnswerEvent.created_at, AnswerEvent.id)
            )
        )
    submissions_by_question = rows_by_question(submissions)
    events_by_question = rows_by_question(events)
    duplicate_ids = sorted([question_id for question_id, count in Counter(question_ids).items() if count > 1])

    items: list[dict[str, Any]] = []
    for index, question_id in enumerate(question_ids, start=1):
        question = questions.get(question_id)
        attempts = submissions_by_question.get(question_id, [])
        item_events = events_by_question.get(question_id, [])
        tags = tag_map.get(question_id, [])
        performance, performance_label = item_performance(question, attempts, item_events)
        wrong_count = effective_wrong_count(attempts)
        mastery_variant = mastery_variants_by_question.get(question_id, {"generated_count": 0, "attempted_count": 0, "question_ids": []})
        mastery_variant_attempted_count = int(mastery_variant.get("attempted_count") or 0)
        skipped = any(row.event_type == "skipped" for row in item_events)
        solution_revealed = any(row.event_type in REVEAL_EVENT_TYPES for row in item_events) or any(row.solution_revealed for row in attempts)
        used_hint = any(row.event_type in HELP_EVENT_TYPES for row in item_events) or any(row.help_level_reached > 0 for row in attempts)
        asked_explanation = any(row.event_type in EXPLANATION_EVENT_TYPES for row in item_events)
        question_support_count = sum(1 for row in item_events if row.event_type == "question_support_requested")
        final = attempts[-1] if attempts else None
        first = attempts[0] if attempts else None
        last_event_at = max([row.created_at for row in item_events], default=None)
        regrade_rows = [regrade_meta_from_grading(row.grading_result) for row in attempts]
        regraded_submission_count = sum(1 for row in regrade_rows if row.get("is_regraded"))
        latest_regrade_at = max([str(row.get("applied_at") or "") for row in regrade_rows if row.get("applied_at")] or [""]) or None
        item = {
            "position": index,
            "question_id": question_id,
            "exists": question is not None,
            "available": question is not None and question.status == "verified" and bool(question.eligible_for_practice),
            "stem_preview": question.stem_preview if question else "原题已删除或不可读取",
            "source_name": question.source_name if question else None,
            "difficulty_star": question.difficulty_star if question else None,
            "premium": bool(question.premium) if question else False,
            "tags": tags[:12],
            "attempt_count": len(attempts),
            "effective_wrong_count": wrong_count,
            "first_correct": first.is_correct if first else None,
            "final_correct": final.is_correct if final else None,
            "last_answered_at": iso(final.created_at) if final else None,
            "last_event_at": iso(last_event_at),
            "skipped": skipped,
            "solution_revealed": solution_revealed,
            "used_hint": used_hint,
            "asked_explanation": asked_explanation,
            "question_support_count": question_support_count,
            "error_type": classify_error(final.grading_result, final.is_correct) if final else None,
            "regraded_submission_count": regraded_submission_count,
            "latest_regrade_at": latest_regrade_at,
            "performance": performance,
            "performance_label": performance_label,
            "mastery_variant_done": wrong_count > 0 and mastery_variant_attempted_count > 0,
            "mastery_variant_generated_count": int(mastery_variant.get("generated_count") or 0),
            "mastery_variant_attempted_count": mastery_variant_attempted_count,
            "mastery_variant_question_ids": list(mastery_variant.get("question_ids") or []),
        }
        if source_name and item["source_name"] != source_name:
            continue
        if difficulty_star is not None and item["difficulty_star"] != difficulty_star:
            continue
        if tag_id and not any(tag["id"] in tag_id for tag in tags):
            continue
        if weak_only and performance not in {"red", "yellow"}:
            continue
        items.append(item)

    answered_items = [row for row in items if row["attempt_count"] > 0]
    first_correct = sum(1 for row in answered_items if row["first_correct"] is True)
    final_correct = sum(1 for row in answered_items if row["final_correct"] is True)
    attempts_total = sum(int(row["attempt_count"]) for row in items)
    performance_counts = Counter(row["performance"] for row in items)
    summary = {
        "id": spec["id"],
        "group_type": spec["group_type"],
        "title": spec["title"],
        "mode": spec.get("mode"),
        "status": spec.get("status"),
        "created_at": iso(spec.get("created_at")),
        "updated_at": iso(spec.get("updated_at")),
        "session_id": spec.get("session_id"),
        "learner_id": spec.get("learner_id"),
        "learner_ids": spec.get("learner_ids", []),
        "test_run_id": spec.get("test_run_id"),
        "generation_batch_id": spec.get("generation_batch_id"),
        "question_count": len(items),
        "answered_count": len(answered_items),
        "first_accuracy": first_correct / len(answered_items) if answered_items else None,
        "final_accuracy": final_correct / len(answered_items) if answered_items else None,
        "avg_attempts": attempts_total / len(answered_items) if answered_items else None,
        "reveal_count": sum(1 for row in items if row["solution_revealed"]),
        "hint_count": sum(1 for row in items if row["used_hint"]),
        "explanation_count": sum(1 for row in items if row["asked_explanation"]),
        "performance_counts": {key: int(performance_counts.get(key, 0)) for key in ["green", "yellow", "red", "gray"]},
        "weak_count": int(performance_counts.get("red", 0) + performance_counts.get("yellow", 0)),
        "mastery_variant_done_count": sum(1 for row in items if row.get("mastery_variant_done")),
        "report_type": spec.get("report_type", "session"),
        "duplicate_question_ids": duplicate_ids,
    }
    return {**summary, "items": items}


def within_date_range(spec: dict[str, Any], date_from: date | None, date_to: date | None) -> bool:
    created = spec.get("created_at")
    if not isinstance(created, datetime):
        return True
    if date_from and created < datetime.combine(date_from, time.min):
        return False
    if date_to and created > datetime.combine(date_to, time.max):
        return False
    return True


@router.get("/groups")
def list_history_groups(
    limit: Annotated[int, Query(ge=1, le=500)] = 80,
    learner_id: str | None = None,
    group_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    source_name: str | None = None,
    difficulty_star: Annotated[int | None, Query(ge=1, le=5)] = None,
    tag_id: Annotated[list[str] | None, Query()] = None,
    weak_only: bool = False,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    resolved_learner_id = resolve_learner_id(db, learner_id) if learner_id else None
    rows: list[dict[str, Any]] = []
    for spec in history_group_specs(db, limit=limit, learner_id=resolved_learner_id):
        if group_type and group_type not in {spec["group_type"], spec.get("mode")}:
            continue
        if not within_date_range(spec, date_from, date_to):
            continue
        detail = group_details(
            db,
            spec,
            source_name=source_name,
            difficulty_star=difficulty_star,
            tag_id=tag_id or [],
            weak_only=weak_only,
        )
        if source_name or difficulty_star is not None or tag_id or weak_only:
            if detail["question_count"] == 0:
                continue
        detail.pop("items", None)
        rows.append(detail)
    return rows[:limit]


@router.get("/groups/{group_id:path}")
def get_history_group(
    group_id: str,
    learner_id: str | None = None,
    source_name: str | None = None,
    difficulty_star: Annotated[int | None, Query(ge=1, le=5)] = None,
    tag_id: Annotated[list[str] | None, Query()] = None,
    weak_only: bool = False,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    resolved_learner_id = resolve_learner_id(db, learner_id) if learner_id else None
    spec = resolve_group_spec(db, group_id, learner_id=resolved_learner_id)
    return group_details(
        db,
        spec,
        source_name=source_name,
        difficulty_star=difficulty_star,
        tag_id=tag_id or [],
        weak_only=weak_only,
    )


@router.post("/groups/{group_id:path}/review-session", response_model=CreatePracticeSessionResponse)
def create_review_session_from_history(
    group_id: str,
    payload: HistoryReviewSessionRequest,
    db: Session = Depends(get_db),
) -> CreatePracticeSessionResponse:
    resolved_learner_id = resolve_learner_id(db, payload.learner_id) if payload.learner_id else None
    spec = resolve_group_spec(db, group_id, learner_id=resolved_learner_id)
    session_learner_id = resolve_learner_id(db, resolved_learner_id or spec.get("learner_id"))
    existing = find_idempotent_practice_session(db, learner_id=session_learner_id, idempotency_key=payload.idempotency_key, mode="review_practice")
    if existing is not None:
        return practice_session_response(existing)
    detail = group_details(db, spec)
    selected_set = set(payload.selected_question_ids)
    if payload.scope == "selected" and not selected_set:
        raise HTTPException(status_code=400, detail="selected_question_ids is required")

    selected_items: list[dict[str, Any]] = []
    for item in detail["items"]:
        if not item["available"]:
            continue
        if payload.scope == "wrong_only" and item["performance"] != "red":
            continue
        if payload.scope == "weak_only" and item["performance"] not in {"red", "yellow"}:
            continue
        if payload.scope == "selected" and item["question_id"] not in selected_set:
            continue
        selected_items.append(item)

    question_ids = stable_unique([str(item["question_id"]) for item in selected_items])
    if not question_ids:
        raise HTTPException(status_code=409, detail="no available questions for review session")
    skipped_unavailable_count = sum(1 for item in detail["items"] if not item["available"])
    source = {
        "group_id": group_id,
        "group_title": detail["title"],
        "group_type": detail["group_type"],
        "learner_id": session_learner_id,
        "scope": payload.scope,
        "idempotency_key": payload.idempotency_key,
        "source_question_count": len(detail["items"]),
        "selected_position_count": len(selected_items),
        "deduped_count": len(question_ids),
        "skipped_unavailable_count": skipped_unavailable_count,
        "duplicate_question_ids": detail.get("duplicate_question_ids", []),
        "created_at": utc_now().isoformat(timespec="seconds") + "Z",
    }
    session = PracticeSession(
        mode="review_practice",
        learner_id=session_learner_id,
        status="active",
        config={
            "learner_id": session_learner_id,
            "review_source": source,
            "selection": {
                "review_group_id": group_id,
                "review_scope": payload.scope,
                "include_generated": True,
                "strict_no_ai": False,
            },
            "break_confirmed": [],
        },
        question_ids=question_ids,
    )
    db.add(session)
    db.flush()
    db.add(
        AnswerEvent(
            session_id=session.id,
            question_id="",
            event_type="review_session_created_from_history",
            payload={"review_source": source, "question_ids": question_ids, "at_ms": 0},
        )
    )
    log_audit_event(db, "practice.review_session_created_from_history", "practice_session", session.id, source)
    db.commit()
    return practice_session_response(session)
