from ..localization import LanguageRequest
from collections import Counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import log_audit_event
from ..ai_gateway import hash_payload
from ..db import get_db
from ..generation_review import diff_payload
from ..grading import grade_answer
from ..importer import map_answer_spec
from ..learner_profiles import resolve_learner_id
from ..models import AnswerEvent, AnswerSpec, InterventionRecord, LearnerProfile, PracticeSession, Question, QuestionDuplicateAlias, QuestionRepairRecord, QuestionTag, QuestionVersion, ReviewQueueItem, ReviewRecord, Submission, TaxonomyGovernanceRecord, TaxonomyTag, WrongQuestionRecord, utc_now, uuid_str
from ..practice_state import normalize_answer_text
from ..question_repair_reviewer import suggest_question_repair
from ..schemas import QuestionDetail, QuestionListItem, TaxonomyTagItem
from ..statuses import PUBLISH_STATUS_DUPLICATE_ALIAS
from ..taxonomy_export import export_taxonomy
from ..taxonomy_governance import export_governance_scan

router = APIRouter(prefix="/api/questions", tags=["questions"])


class TaxonomyGovernanceRequest(BaseModel):
    action: str = Field(min_length=1, max_length=64)
    tag_id: str | None = None
    payload: dict = Field(default_factory=dict)
    reviewer: str = "local_user"


class TaxonomyTagUpsertRequest(BaseModel):
    kind: str = Field(default="custom", min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)


class ReviewQueueUpdateRequest(BaseModel):
    status: str = Field(min_length=1, max_length=32)
    note: str | None = None


class QuestionRepairDraftRequest(LanguageRequest):
    reason: str = Field(default="manual_fix", min_length=1, max_length=64)
    severity: str = Field(default="medium", min_length=1, max_length=32)
    question_v2: dict[str, Any] = Field(default_factory=dict)
    answer_spec: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=2000)
    reviewer: str = Field(default="local_user", min_length=1, max_length=64)
    quarantine: bool = False


class QuestionRepairActionRequest(LanguageRequest):
    note: str | None = Field(default=None, max_length=2000)
    reviewer: str = Field(default="local_user", min_length=1, max_length=64)


class QuestionRepairSuggestionRequest(LanguageRequest):
    issue_type: str = Field(default="other", min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=2000)
    use_mock: bool = False


class QuestionDuplicateMergeRequest(BaseModel):
    canonical_question_id: str = Field(min_length=1, max_length=128)
    alias_question_ids: list[str] = Field(min_length=1, max_length=100)
    reason: str = Field(default="duplicate_question", min_length=1, max_length=2000)
    reviewer: str = Field(default="local_user", min_length=1, max_length=64)
    retire_aliases: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class QuestionRegradeRequest(BaseModel):
    confirm: bool = False
    reason: str = Field(default="question_repair_regrade", min_length=1, max_length=2000)
    reviewer: str = Field(default="local_user", min_length=1, max_length=64)
    max_submissions: int = Field(default=500, ge=1, le=5000)
    changed_only: bool = True
    allow_unverified_answer_spec: bool = False


@router.get("", response_model=list[QuestionListItem])
def list_questions(
    source_name: str | None = None,
    status: str | None = None,
    premium: bool | None = None,
    q: str | None = None,
    difficulty_star: Annotated[list[int] | None, Query()] = None,
    tag_id: Annotated[list[str] | None, Query()] = None,
    learner_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[QuestionListItem]:
    resolved_learner_id = resolve_learner_id(db, learner_id) if learner_id else None
    last_answered_stmt = select(
        Submission.question_id.label("question_id"),
        func.max(Submission.created_at).label("last_answered_at"),
    )
    if resolved_learner_id:
        last_answered_stmt = last_answered_stmt.join(PracticeSession, PracticeSession.id == Submission.session_id).where(PracticeSession.learner_id == resolved_learner_id)
    last_answered = last_answered_stmt.group_by(Submission.question_id).subquery()
    stmt = (
        select(Question, AnswerSpec.status, last_answered.c.last_answered_at)
        .join(AnswerSpec, AnswerSpec.question_id == Question.id, isouter=True)
        .join(last_answered, last_answered.c.question_id == Question.id, isouter=True)
    )
    if source_name:
        stmt = stmt.where(Question.source_name == source_name)
    if status:
        stmt = stmt.where(Question.status == status)
    else:
        stmt = stmt.where(Question.status != "retired")
    if premium is not None:
        stmt = stmt.where(Question.premium == premium)
    if q:
        needle = q.strip()
        if needle:
            stmt = stmt.where(
                or_(
                    Question.id.contains(needle),
                    Question.source_name.contains(needle),
                    Question.section.contains(needle),
                    Question.original_number.contains(needle),
                    Question.stem_preview.contains(needle),
                    AnswerSpec.answer_type.contains(needle),
                )
            )
    if difficulty_star:
        stmt = stmt.where(Question.difficulty_star.in_(difficulty_star))
    if tag_id:
        stmt = stmt.join(QuestionTag, QuestionTag.question_id == Question.id).where(QuestionTag.tag_id.in_(tag_id))
    stmt = stmt.order_by(Question.id).offset(offset).limit(limit)
    return [
        QuestionListItem(
            id=q.id,
            source_name=q.source_name,
            section=q.section,
            stem_preview=q.stem_preview,
            difficulty_star=q.difficulty_star,
            premium=q.premium,
            status=q.status,
            publish_status=q.publish_status,
            eligible_for_practice=q.eligible_for_practice,
            eligible_for_test=q.eligible_for_test,
            answer_status=answer_status,
            last_answered_at=last_answered_at.isoformat() if last_answered_at else None,
        )
        for q, answer_status, last_answered_at in db.execute(stmt).all()
    ]


@router.get("/tags", response_model=list[TaxonomyTagItem])
def list_tags(
    kind: str | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    db: Session = Depends(get_db),
) -> list[TaxonomyTagItem]:
    stmt = (
        select(TaxonomyTag, func.count(QuestionTag.question_id))
        .join(QuestionTag, QuestionTag.tag_id == TaxonomyTag.id, isouter=True)
        .group_by(TaxonomyTag.id)
    )
    if kind:
        stmt = stmt.where(TaxonomyTag.kind == kind)
    if q:
        stmt = stmt.where(or_(TaxonomyTag.label.contains(q), TaxonomyTag.description.contains(q)))
    stmt = stmt.order_by(func.count(QuestionTag.question_id).desc(), TaxonomyTag.kind, TaxonomyTag.label).limit(limit)
    return [
        TaxonomyTagItem(id=tag.id, kind=tag.kind, label=tag.label, status=tag.status, version=tag.version, question_count=count, description=tag.description)
        for tag, count in db.execute(stmt).all()
    ]


@router.post("/tags", response_model=TaxonomyTagItem)
def upsert_custom_tag(payload: TaxonomyTagUpsertRequest, db: Session = Depends(get_db)) -> TaxonomyTagItem:
    kind = payload.kind.strip() or "custom"
    label = payload.label.strip()
    description = (payload.description or "").strip() or None
    row = db.scalar(select(TaxonomyTag).where(TaxonomyTag.kind == kind, TaxonomyTag.label == label))
    if row is None:
        row = TaxonomyTag(
            id=f"custom:{uuid_str()}",
            kind=kind,
            label=label,
            aliases=[],
            version="custom_v1",
            status="active",
            description=description,
        )
        db.add(row)
        action = "taxonomy.tag_created"
    else:
        row.description = description
        row.status = "active"
        action = "taxonomy.tag_updated"
    db.flush()
    db.add(TaxonomyGovernanceRecord(action=action, tag_id=row.id, payload={"kind": kind, "label": label, "description": description}, reviewer="local_user"))
    log_audit_event(db, action, "taxonomy_tag", row.id, {"kind": kind, "label": label})
    db.commit()
    return TaxonomyTagItem(id=row.id, kind=row.kind, label=row.label, status=row.status, version=row.version, question_count=0, description=row.description)


@router.get("/review-queue")
def list_review_queue(
    status: str | None = "open",
    reason_code: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(ReviewQueueItem, Question).join(Question, Question.id == ReviewQueueItem.question_id)
    if status:
        stmt = stmt.where(ReviewQueueItem.status == status)
    if reason_code:
        stmt = stmt.where(ReviewQueueItem.reason_code == reason_code)
    rows = list(db.execute(stmt.order_by(ReviewQueueItem.severity.desc(), ReviewQueueItem.created_at, ReviewQueueItem.id).limit(limit)).all())
    return {
        "items": [
            {
                "id": item.id,
                "question_id": item.question_id,
                "reason_code": item.reason_code,
                "severity": item.severity,
                "status": item.status,
                "payload": item.payload,
                "question": {
                    "source_name": question.source_name,
                    "difficulty_star": question.difficulty_star,
                    "stem_preview": question.stem_preview,
                },
                "created_at": item.created_at.isoformat(),
            }
            for item, question in rows
        ]
    }


@router.put("/review-queue/{item_id}")
def update_review_queue_item(item_id: str, payload: ReviewQueueUpdateRequest, db: Session = Depends(get_db)) -> dict:
    item = db.get(ReviewQueueItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="review queue item not found")
    item.status = payload.status
    item.payload = {**(item.payload or {}), "last_note": payload.note, "updated_at": utc_now().isoformat()}
    log_audit_event(db, "review_queue.updated", "review_queue_item", item.id, {"question_id": item.question_id, "status": item.status, "reason_code": item.reason_code})
    db.commit()
    return {"id": item.id, "question_id": item.question_id, "status": item.status, "payload": item.payload}


def _repair_response(row: QuestionRepairRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "question_id": row.question_id,
        "base_version_id": row.base_version_id,
        "new_version_id": row.new_version_id,
        "status": row.status,
        "reason": row.reason,
        "severity": row.severity,
        "diff": row.diff,
        "reviewer": row.reviewer,
        "note": row.note,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _current_question_version(db: Session, question: Question) -> QuestionVersion | None:
    if question.current_version_id:
        version = db.get(QuestionVersion, question.current_version_id)
        if version:
            return version
    return db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == question.id).order_by(QuestionVersion.version.desc()))


def _answer_snapshot(answer: AnswerSpec | None) -> dict[str, Any] | None:
    if answer is None:
        return None
    return {
        "status": answer.status,
        "answer_type": answer.answer_type,
        "canonical_answer": answer.canonical_answer,
        "normalized_spec": answer.normalized_spec,
        "raw_answer_spec": answer.raw_answer_spec,
        "verification": answer.verification,
    }


def _restore_answer_snapshot(answer: AnswerSpec, snapshot: dict[str, Any]) -> None:
    answer.status = str(snapshot.get("status") or "needs_review")
    answer.answer_type = str(snapshot.get("answer_type") or "text")
    answer.canonical_answer = str(snapshot.get("canonical_answer") or "")
    answer.normalized_spec = dict(snapshot.get("normalized_spec") or {})
    answer.raw_answer_spec = dict(snapshot.get("raw_answer_spec") or {})
    answer.verification = dict(snapshot.get("verification") or {})


def _raw_answer_spec_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "canonical_answer" in payload or "acceptable_answers" in payload:
        return dict(payload)
    canonical_values = payload.get("canonical") or payload.get("accepted_forms") or [""]
    if not isinstance(canonical_values, list):
        canonical_values = [canonical_values]
    accepted_forms = payload.get("accepted_forms") or []
    if not isinstance(accepted_forms, list):
        accepted_forms = [accepted_forms]
    return {
        "schema_version": "answer_spec_v1",
        "status": payload.get("status") or "verified",
        "answer_type": payload.get("answer_type") or "text",
        "canonical_answer": str(canonical_values[0] if canonical_values else ""),
        "acceptable_answers": [str(value) for value in accepted_forms],
        "unit": payload.get("unit"),
        "tolerance": payload.get("tolerance") or {"type": "exact", "value": 0},
        "blanks": payload.get("blanks") or [],
        "order_sensitive": bool(payload.get("order_sensitive", True)),
        "grading_method": payload.get("grading_method") or "exact_normalized",
        "verification": payload.get("verification") or {},
    }


def _prefixed_changes(prefix: str, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for change in changes:
        path = change.get("path")
        rows.append({**change, "path": f"{prefix}.{path}" if path else prefix})
    return rows


def _stem_from_question_v2(question_v2: dict[str, Any], fallback: str = "") -> str:
    content = question_v2.get("content") if isinstance(question_v2, dict) else {}
    if isinstance(content, dict):
        stem = content.get("stem_md")
        if stem:
            return str(stem)
    return fallback


def _difficulty_from_question_v2(question_v2: dict[str, Any], fallback: int | None) -> int | None:
    try:
        value = ((question_v2.get("evaluation") or {}).get("difficulty") or {}).get("star")
        return int(value) if value is not None else fallback
    except (TypeError, ValueError, AttributeError):
        return fallback


def _premium_from_question_v2(question_v2: dict[str, Any], fallback: bool) -> bool:
    try:
        premium = ((question_v2.get("evaluation") or {}).get("premium") or {}).get("is_premium")
        return bool(premium) if premium is not None else fallback
    except AttributeError:
        return fallback


@router.get("/{question_id}/repairs")
def list_question_repairs(question_id: str, db: Session = Depends(get_db)) -> dict:
    if db.get(Question, question_id) is None:
        raise HTTPException(status_code=404, detail="question not found")
    rows = list(db.scalars(select(QuestionRepairRecord).where(QuestionRepairRecord.question_id == question_id).order_by(QuestionRepairRecord.created_at.desc(), QuestionRepairRecord.id)))
    return {"items": [_repair_response(row) for row in rows]}


@router.post("/{question_id}/repairs")
def create_question_repair(question_id: str, payload: QuestionRepairDraftRequest, db: Session = Depends(get_db)) -> dict:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    version = _current_question_version(db, question)
    if version is None:
        raise HTTPException(status_code=409, detail="question has no version to repair")
    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == question_id))
    base_question_v2 = dict(version.question_v2 or {})
    draft_question_v2 = dict(payload.question_v2 or base_question_v2)
    base_answer = _answer_snapshot(answer)
    draft_answer = _raw_answer_spec_from_payload(payload.answer_spec) if payload.answer_spec is not None else None
    changes = _prefixed_changes("question_v2", diff_payload(base_question_v2, draft_question_v2))
    if draft_answer is not None:
        changes.extend(_prefixed_changes("answer_spec", diff_payload((base_answer or {}).get("raw_answer_spec") or {}, draft_answer)))
    if not changes and not payload.quarantine:
        raise HTTPException(status_code=400, detail="repair draft contains no changes")
    row = QuestionRepairRecord(
        question_id=question.id,
        base_version_id=version.id,
        status="draft",
        reason=payload.reason,
        severity=payload.severity,
        draft_question_v2=draft_question_v2,
        draft_answer_spec=draft_answer,
        base_question_v2=base_question_v2,
        base_answer_spec=base_answer,
        diff=changes,
        reviewer=payload.reviewer,
        note=payload.note,
    )
    db.add(row)
    if payload.quarantine:
        question.status = "quarantined"
        question.publish_status = "quarantined"
        question.eligible_for_practice = False
        question.eligible_for_test = False
        question.question_fault_status = "suspected"
        question.question_fault_confidence = max(float(question.question_fault_confidence or 0), 0.9)
        item = db.scalar(select(ReviewQueueItem).where(ReviewQueueItem.question_id == question.id, ReviewQueueItem.reason_code == "manual_repair_quarantine"))
        if item is None:
            db.add(ReviewQueueItem(question_id=question.id, reason_code="manual_repair_quarantine", severity=payload.severity, status="open", payload={"repair_id": row.id, "reason": payload.reason}))
    db.flush()
    log_audit_event(db, "question_repair.created", "question", question.id, {"repair_id": row.id, "reason": row.reason, "severity": row.severity, "diff_count": len(changes), "quarantine": payload.quarantine})
    db.commit()
    return _repair_response(row)


@router.post("/{question_id}/repairs/ai-suggestion")
def create_question_repair_ai_suggestion(question_id: str, payload: QuestionRepairSuggestionRequest, db: Session = Depends(get_db)) -> dict:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    impact = get_question_impact(question_id, db)
    suggestion = suggest_question_repair(db, question, payload.issue_type, payload.note, impact=impact, use_mock=payload.use_mock)
    suggested_draft = {
        "reason": suggestion.get("issue_type") or payload.issue_type,
        "severity": suggestion.get("severity") or "medium",
        "note": "\n".join(
            item
            for item in [
                str(suggestion.get("summary") or "").strip(),
                str(suggestion.get("reviewer_notes") or "").strip(),
            ]
            if item
        )[:2000],
        "quarantine": bool(suggestion.get("should_quarantine")),
        "draft_question_v2_patch": suggestion.get("draft_question_v2_patch") or {},
        "draft_answer_spec_patch": suggestion.get("draft_answer_spec_patch") or {},
        "should_regrade_after_publish": bool(suggestion.get("should_regrade_after_publish")),
    }
    log_audit_event(
        db,
        "question_repair.ai_suggestion_created",
        "question",
        question_id,
        {
            "issue_type": payload.issue_type,
            "suggestion_status": suggestion.get("status"),
            "suggestion_issue_type": suggestion.get("issue_type"),
            "severity": suggestion.get("severity"),
            "confidence": suggestion.get("confidence"),
            "use_mock": payload.use_mock,
        },
    )
    db.commit()
    return {
        "question_id": question_id,
        "impact_summary": impact.get("totals", {}),
        "suggestion": suggestion,
        "suggested_draft": suggested_draft,
    }


@router.post("/{question_id}/repairs/{repair_id}/publish")
def publish_question_repair(question_id: str, repair_id: str, payload: QuestionRepairActionRequest, db: Session = Depends(get_db)) -> dict:
    question = db.get(Question, question_id)
    row = db.get(QuestionRepairRecord, repair_id)
    if question is None or row is None or row.question_id != question_id:
        raise HTTPException(status_code=404, detail="repair draft not found")
    if row.status != "draft":
        raise HTTPException(status_code=409, detail=f"repair is {row.status}, not draft")
    base_version = db.get(QuestionVersion, row.base_version_id) if row.base_version_id else _current_question_version(db, question)
    if base_version is None:
        raise HTTPException(status_code=409, detail="base version no longer exists")
    next_version = int(db.scalar(select(func.max(QuestionVersion.version)).where(QuestionVersion.question_id == question.id)) or 0) + 1
    stem = _stem_from_question_v2(row.draft_question_v2, question.stem_preview)
    raw_json = dict(base_version.raw_json or {})
    raw_json["question_text"] = stem
    if row.draft_answer_spec is not None:
        raw_json["answer_spec"] = row.draft_answer_spec
    raw_json["_platform_repair"] = {
        "repair_id": row.id,
        "base_version_id": base_version.id,
        "published_at": utc_now().isoformat(),
        "reviewer": payload.reviewer,
        "reason": row.reason,
    }
    new_version = QuestionVersion(
        question_id=question.id,
        version=next_version,
        raw_json=raw_json,
        question_v2={**row.draft_question_v2, "version": next_version},
        content_hash=hash_payload(row.draft_question_v2),
    )
    db.add(new_version)
    db.flush()
    question.current_version_id = new_version.id
    question.stem_preview = stem[:500]
    question.difficulty_star = _difficulty_from_question_v2(row.draft_question_v2, question.difficulty_star)
    question.premium = _premium_from_question_v2(row.draft_question_v2, question.premium)
    if row.draft_answer_spec is not None:
        raw_answer = _raw_answer_spec_from_payload(row.draft_answer_spec)
        answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == question.id))
        if answer is None:
            answer = AnswerSpec(question_id=question.id, status="needs_review", answer_type="text", canonical_answer="", normalized_spec={}, raw_answer_spec={}, verification={})
            db.add(answer)
        answer.status = str(raw_answer.get("status") or "needs_review")
        answer.answer_type = str(raw_answer.get("answer_type") or "text")
        answer.canonical_answer = str(raw_answer.get("canonical_answer") or "")
        answer.normalized_spec = map_answer_spec(raw_answer)
        answer.raw_answer_spec = raw_answer
        answer.verification = dict(raw_answer.get("verification") or {})
    answer_status = row.draft_answer_spec.get("status") if isinstance(row.draft_answer_spec, dict) else None
    if answer_status is None and row.base_answer_spec:
        answer_status = row.base_answer_spec.get("status")
    if answer_status == "verified":
        question.status = "verified"
        question.publish_status = "source_verified" if question.publish_status not in {"active_practice", "ai_generated"} else question.publish_status
        question.eligible_for_practice = True
        question.eligible_for_test = True
        question.question_fault_status = "none"
        question.question_fault_confidence = None
    row.status = "published"
    row.new_version_id = new_version.id
    row.reviewer = payload.reviewer
    row.note = payload.note or row.note
    row.updated_at = utc_now()
    open_items = list(db.scalars(select(ReviewQueueItem).where(ReviewQueueItem.question_id == question.id, ReviewQueueItem.status == "open")))
    for item in open_items:
        item.status = "resolved"
        item.payload = {**(item.payload or {}), "resolved_by_repair_id": row.id, "resolved_at": utc_now().isoformat(), "note": payload.note}
    log_audit_event(db, "question_repair.published", "question", question.id, {"repair_id": row.id, "new_version_id": new_version.id, "version": next_version, "closed_review_items": len(open_items)})
    db.commit()
    return _repair_response(row)


@router.post("/{question_id}/repairs/{repair_id}/revert")
def revert_question_repair(question_id: str, repair_id: str, payload: QuestionRepairActionRequest, db: Session = Depends(get_db)) -> dict:
    question = db.get(Question, question_id)
    row = db.get(QuestionRepairRecord, repair_id)
    if question is None or row is None or row.question_id != question_id:
        raise HTTPException(status_code=404, detail="repair record not found")
    if row.status != "published":
        raise HTTPException(status_code=409, detail=f"repair is {row.status}, not published")
    base_version = db.get(QuestionVersion, row.base_version_id) if row.base_version_id else None
    if base_version is None:
        raise HTTPException(status_code=409, detail="base version no longer exists")
    question.current_version_id = base_version.id
    question.stem_preview = _stem_from_question_v2(base_version.question_v2, question.stem_preview)[:500]
    question.difficulty_star = _difficulty_from_question_v2(base_version.question_v2, question.difficulty_star)
    question.premium = _premium_from_question_v2(base_version.question_v2, question.premium)
    if row.base_answer_spec is not None:
        answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == question.id))
        if answer is None:
            answer = AnswerSpec(question_id=question.id, status="needs_review", answer_type="text", canonical_answer="", normalized_spec={}, raw_answer_spec={}, verification={})
            db.add(answer)
        _restore_answer_snapshot(answer, row.base_answer_spec)
        question.status = "verified" if answer.status == "verified" else "needs_review"
        question.eligible_for_practice = answer.status == "verified"
        question.eligible_for_test = answer.status == "verified"
    row.status = "reverted"
    row.reviewer = payload.reviewer
    row.note = payload.note or row.note
    row.updated_at = utc_now()
    log_audit_event(db, "question_repair.reverted", "question", question.id, {"repair_id": row.id, "base_version_id": base_version.id, "new_version_id": row.new_version_id})
    db.commit()
    return _repair_response(row)


@router.post("/taxonomy/export")
def export_taxonomy_endpoint(db: Session = Depends(get_db)) -> dict:
    payload = export_taxonomy(db)
    log_audit_event(db, "taxonomy.exported", "taxonomy", payload["taxonomy_version"], {"content_hash": payload["content_hash"], "output_path": payload["output_path"]})
    db.commit()
    return payload


@router.post("/taxonomy/governance-scan")
def export_taxonomy_governance_scan_endpoint(db: Session = Depends(get_db)) -> dict:
    payload = export_governance_scan(db)
    log_audit_event(db, "taxonomy.governance_scan_exported", "taxonomy", "taxonomy_v1", {"output_path": payload["output_path"]})
    db.commit()
    return payload


@router.post("/taxonomy/governance-records")
def create_taxonomy_governance_record(payload: TaxonomyGovernanceRequest, db: Session = Depends(get_db)) -> dict:
    if payload.tag_id and db.get(TaxonomyTag, payload.tag_id) is None:
        raise HTTPException(status_code=404, detail="tag not found")
    row = TaxonomyGovernanceRecord(action=payload.action, tag_id=payload.tag_id, payload=payload.payload, reviewer=payload.reviewer)
    db.add(row)
    db.flush()
    log_audit_event(db, "taxonomy.governance_recorded", "taxonomy_governance_record", row.id, {"action": row.action, "tag_id": row.tag_id})
    db.commit()
    return {
        "id": row.id,
        "action": row.action,
        "tag_id": row.tag_id,
        "payload": row.payload,
        "reviewer": row.reviewer,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
    }


def _question_summary(question: Question | None) -> dict[str, Any] | None:
    if question is None:
        return None
    return {
        "id": question.id,
        "source_name": question.source_name,
        "status": question.status,
        "publish_status": question.publish_status,
        "eligible_for_practice": question.eligible_for_practice,
        "eligible_for_test": question.eligible_for_test,
        "difficulty_star": question.difficulty_star,
        "stem_preview": question.stem_preview,
    }


def _duplicate_alias_response(row: QuestionDuplicateAlias, db: Session) -> dict[str, Any]:
    return {
        "id": row.id,
        "canonical_question_id": row.canonical_question_id,
        "alias_question_id": row.alias_question_id,
        "status": row.status,
        "reason": row.reason,
        "reviewer": row.reviewer,
        "metadata": row.metadata_json,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "canonical_question": _question_summary(db.get(Question, row.canonical_question_id)),
        "alias_question": _question_summary(db.get(Question, row.alias_question_id)),
    }


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _question_index(session: PracticeSession, question_id: str) -> int | None:
    try:
        return list(session.question_ids or []).index(question_id) + 1
    except ValueError:
        return None


def _ensure_impact_session(sessions: dict[str, dict[str, Any]], session: PracticeSession, question_id: str) -> dict[str, Any]:
    row = sessions.get(session.id)
    if row is None:
        row = {
            "session_id": session.id,
            "learner_id": session.learner_id,
            "mode": session.mode,
            "status": session.status,
            "question_index": _question_index(session, question_id),
            "created_at": _iso(session.created_at),
            "updated_at": _iso(session.updated_at),
            "attempt_count": 0,
            "first_correct": None,
            "final_correct": None,
            "first_submission_at": None,
            "last_submission_at": None,
            "event_count": 0,
            "event_types": {},
            "solution_revealed": False,
            "max_help_level": 0,
        }
        sessions[session.id] = row
    return row


def _help_level_from_payload(payload: dict[str, Any]) -> int:
    value = payload.get("level") if isinstance(payload, dict) else None
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _current_answer_and_version(db: Session, question: Question) -> tuple[AnswerSpec, QuestionVersion | None]:
    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == question.id))
    if answer is None:
        raise HTTPException(status_code=404, detail="answer spec not found")
    version = db.get(QuestionVersion, question.current_version_id) if question.current_version_id else None
    if version is None:
        version = db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == question.id).order_by(QuestionVersion.version.desc()))
    return answer, version


def _history_recalculation_scopes(*, include_mastery: bool = True) -> list[dict[str, Any]]:
    scopes = [
        {
            "scope": "reports.summary",
            "status": "online_recomputed_next_read",
            "description": "报告 summary 每次从 submissions 和 answer_events 在线计算；执行 regrade 后刷新报告即可看到新口径。",
        },
        {
            "scope": "history.groups",
            "status": "online_recomputed_next_read",
            "description": "历史重做分组和题目红黄绿表现从 submissions、answer_events 在线汇总；执行 regrade 后刷新历史页即可看到新口径。",
        },
        {
            "scope": "reports.exported_snapshots",
            "status": "may_be_stale",
            "description": "已经导出的报告是历史快照，不会自动改写；如果本题影响了报告结论，应重新导出。",
        },
    ]
    if include_mastery:
        scopes.append(
            {
                "scope": "learning.mastery",
                "status": "online_recomputed_next_read",
                "description": "掌握画像按最新 submissions 重新计算；is_correct 或 effective_wrong 变化会影响薄弱点和今日任务。",
            }
        )
    return scopes


def _history_recalculation_from_impact(question: Question, affected_sessions: list[dict[str, Any]], submission_count: int, recommended: bool) -> dict[str, Any]:
    affected_learner_ids = sorted({str(row.get("learner_id")) for row in affected_sessions if row.get("learner_id")})
    return {
        "schema_version": "history_recalculation_strategy_v1",
        "status": "recommended" if recommended else "not_needed",
        "recommended": recommended,
        "strategy": "preview_then_explicit_regrade_then_online_recompute",
        "reason": "题目已影响历史作答，且当前处于隔离、停用、重复别名或质量事故状态；应先预览 regrade，再显式执行。" if recommended else "当前没有需要自动重算的治理信号。",
        "requires_explicit_regrade": recommended,
        "affected_counts": {
            "submission_count": submission_count,
            "session_count": len(affected_sessions),
            "learner_count": len(affected_learner_ids),
        },
        "affected_session_ids_preview": [str(row["session_id"]) for row in affected_sessions[:20]],
        "affected_learner_ids": affected_learner_ids,
        "affected_question": {
            "question_id": question.id,
            "source_name": question.source_name,
            "difficulty_star": question.difficulty_star,
            "publish_status": question.publish_status,
            "question_fault_status": question.question_fault_status,
        },
        "affected_scopes": _history_recalculation_scopes(),
        "safe_defaults": [
            "预览接口不写库。",
            "执行接口必须 confirm=true。",
            "执行后只更新 submissions 的判定快照和写入 submission_regraded 事件，不改原 session/test_run/generation_batch 的题目序列。",
            "已导出的报告快照不会被静默改写，需要重新导出。",
        ],
    }


def _regrade_snapshot(question: Question, version: QuestionVersion | None, answer: AnswerSpec) -> dict[str, Any]:
    raw_answer_spec = answer.raw_answer_spec or {}
    normalized_spec = answer.normalized_spec or {}
    verification = answer.verification or {}
    return {
        "schema_version": "regrade_snapshot_v1",
        "question_id": question.id,
        "question_status": question.status,
        "question_publish_status": question.publish_status,
        "question_version_id": version.id if version else None,
        "question_version": version.version if version else None,
        "question_content_hash": version.content_hash if version else None,
        "answer_spec_id": answer.id,
        "answer_spec_status": answer.status,
        "answer_type": answer.answer_type,
        "answer_spec_hash": hash_payload(raw_answer_spec),
        "normalized_spec_hash": hash_payload(normalized_spec),
        "verification_hash": hash_payload(verification),
        "grading_snapshot": {
            "answer_type": answer.answer_type,
            "grading_method": str(raw_answer_spec.get("grading_method") or ""),
            "tolerance": raw_answer_spec.get("tolerance"),
            "unit": raw_answer_spec.get("unit"),
            "order_sensitive": bool(raw_answer_spec.get("order_sensitive", True)),
            "canonical_answer_hash": hash_payload(str(raw_answer_spec.get("canonical_answer") or "")),
            "acceptable_answer_count": len(raw_answer_spec.get("acceptable_answers") or []),
            "verification_level": verification.get("level"),
        },
    }


def _submission_regrade_delta(submission: Submission, question: Question, version: QuestionVersion | None, answer: AnswerSpec) -> dict[str, Any]:
    previous_result = dict(submission.grading_result or {})
    new_result = grade_answer(answer.raw_answer_spec or {}, submission.answer_text)
    snapshot = _regrade_snapshot(question, version, answer)
    duplicate_answer = bool(previous_result.get("duplicate_answer"))
    excluded_from_mastery = bool(submission.excluded_from_mastery)
    normalized_answer = normalize_answer_text(submission.answer_text)
    new_effective_wrong = bool(new_result.get("is_correct") is False and normalized_answer and not duplicate_answer and not excluded_from_mastery)
    merged_result = {
        **new_result,
        "evaluation_snapshot": snapshot,
        "regrade": {
            "schema_version": "submission_regrade_v1",
            "reason": "question_repair_regrade",
            "previous_is_correct": submission.is_correct,
            "previous_reason": previous_result.get("reason"),
            "previous_effective_wrong": submission.effective_wrong,
            "previous_question_version_id": submission.question_version_id,
        },
        "duplicate_answer": duplicate_answer,
        "effective_wrong": new_effective_wrong,
        "excluded_from_mastery": excluded_from_mastery,
        "solution_revealed": bool(submission.solution_revealed),
        "help_level_reached": int(submission.help_level_reached or 0),
    }
    changed_fields: list[str] = []
    if submission.is_correct != new_result.get("is_correct"):
        changed_fields.append("is_correct")
    if previous_result.get("reason") != new_result.get("reason"):
        changed_fields.append("reason")
    if bool(submission.effective_wrong) != new_effective_wrong:
        changed_fields.append("effective_wrong")
    if submission.question_version_id != (version.id if version else None):
        changed_fields.append("question_version_id")
    return {
        "submission_id": submission.id,
        "session_id": submission.session_id,
        "question_id": submission.question_id,
        "answer_text_preview": submission.answer_text[:120],
        "previous": {
            "is_correct": submission.is_correct,
            "reason": previous_result.get("reason"),
            "effective_wrong": submission.effective_wrong,
            "excluded_from_mastery": submission.excluded_from_mastery,
            "question_version_id": submission.question_version_id,
        },
        "new": {
            "is_correct": new_result.get("is_correct"),
            "reason": new_result.get("reason"),
            "effective_wrong": new_effective_wrong,
            "excluded_from_mastery": excluded_from_mastery,
            "question_version_id": version.id if version else None,
        },
        "changed": bool(changed_fields),
        "changed_fields": changed_fields,
        "new_grading_result": merged_result,
    }


def _public_regrade_delta(delta: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in delta.items() if key != "new_grading_result"}


def _history_recalculation_from_regrade(db: Session, question: Question, rows: list[Submission], deltas: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    session_ids = sorted({row.session_id for row in rows})
    sessions = {
        row.id: row
        for row in db.scalars(select(PracticeSession).where(PracticeSession.id.in_(session_ids))).all()
    } if session_ids else {}
    learner_ids = sorted({str(row.learner_id) for row in sessions.values() if row.learner_id})
    changed = [row for row in deltas if row.get("changed")]
    return {
        "schema_version": "history_recalculation_strategy_v1",
        "status": "applied" if mode == "applied" and changed else ("ready_to_apply" if changed else "not_needed"),
        "recommended": bool(changed),
        "strategy": "explicit_regrade_updates_submissions_then_online_recompute",
        "mode": mode,
        "reason": "这些历史提交会因当前答案规范变化而改变统计口径。" if changed else "当前重判结果未发现需要改写的提交。",
        "affected_counts": {
            "checked_submission_count": len(rows),
            "changed_submission_count": len(changed),
            "affected_session_count": len({row["session_id"] for row in changed}),
            "affected_learner_count": len(learner_ids),
            "changed_correct_to_wrong": sum(1 for row in changed if row["previous"]["is_correct"] is True and row["new"]["is_correct"] is False),
            "changed_wrong_to_correct": sum(1 for row in changed if row["previous"]["is_correct"] is False and row["new"]["is_correct"] is True),
            "effective_wrong_changed": sum(1 for row in changed if row["previous"]["effective_wrong"] != row["new"]["effective_wrong"]),
            "question_version_changed": sum(1 for row in changed if row["previous"]["question_version_id"] != row["new"]["question_version_id"]),
        },
        "affected_session_ids": sorted({row["session_id"] for row in changed})[:50],
        "affected_learner_ids": learner_ids,
        "affected_question": {
            "question_id": question.id,
            "source_name": question.source_name,
            "difficulty_star": question.difficulty_star,
            "publish_status": question.publish_status,
            "question_fault_status": question.question_fault_status,
        },
        "affected_scopes": _history_recalculation_scopes(),
        "safe_defaults": [
            "预览模式不写库，只返回差异和影响计划。",
            "执行模式只处理本次请求范围内的 submissions。",
            "原严格测试、套题批次、历史组题目序列保持不变。",
            "报告、历史重做和掌握画像刷新后按最新 submissions 在线重算。",
            "历史导出的报告快照不自动改写，需要重新导出。",
        ],
    }


def _question_regrade_deltas(question_id: str, max_submissions: int, db: Session) -> tuple[Question, AnswerSpec, QuestionVersion | None, list[Submission], list[dict[str, Any]]]:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    answer, version = _current_answer_and_version(db, question)
    rows = list(
        db.scalars(
            select(Submission)
            .where(Submission.question_id == question_id)
            .order_by(Submission.created_at, Submission.id)
            .limit(max_submissions)
        )
    )
    deltas = [_submission_regrade_delta(row, question, version, answer) for row in rows]
    return question, answer, version, rows, deltas


def _question_regrade_payload(question_id: str, max_submissions: int, changed_only: bool, db: Session) -> dict[str, Any]:
    question, answer, version, rows, all_deltas = _question_regrade_deltas(question_id, max_submissions, db)
    deltas = all_deltas
    if changed_only:
        deltas = [row for row in all_deltas if row["changed"]]
    all_changed_count = sum(1 for row in all_deltas if row["changed"])
    changed_count = sum(1 for row in deltas if row["changed"])
    return {
        "question": _question_summary(question),
        "answer_spec": {
            "id": answer.id,
            "status": answer.status,
            "answer_type": answer.answer_type,
            "answer_spec_hash": hash_payload(answer.raw_answer_spec or {}),
        },
        "current_version_id": version.id if version else None,
        "checked_count": len(rows),
        "returned_count": len(deltas),
        "changed_count": changed_count,
        "total_changed_count": all_changed_count,
        "unchanged_count": len(rows) - all_changed_count,
        "changed_correct_to_wrong": sum(1 for row in deltas if row["previous"]["is_correct"] is True and row["new"]["is_correct"] is False),
        "changed_wrong_to_correct": sum(1 for row in deltas if row["previous"]["is_correct"] is False and row["new"]["is_correct"] is True),
        "items": [_public_regrade_delta(row) for row in deltas[:200]],
        "truncated": len(deltas) > 200,
        "mode": "preview",
        "history_recalculation": _history_recalculation_from_regrade(db, question, rows, all_deltas, "preview"),
    }


@router.get("/{question_id}/regrade-preview")
def preview_question_regrade(
    question_id: str,
    max_submissions: Annotated[int, Query(ge=1, le=5000)] = 500,
    changed_only: bool = True,
    db: Session = Depends(get_db),
) -> dict:
    return _question_regrade_payload(question_id, max_submissions, changed_only, db)


@router.post("/{question_id}/regrade")
def apply_question_regrade(question_id: str, payload: QuestionRegradeRequest, db: Session = Depends(get_db)) -> dict:
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="confirm=true is required to apply regrade")
    question, answer, version, submissions, all_deltas = _question_regrade_deltas(question_id, payload.max_submissions, db)
    if answer.status != "verified" and not payload.allow_unverified_answer_spec:
        raise HTTPException(status_code=409, detail={"code": "answer_spec_not_verified", "answer_spec_status": answer.status})
    deltas = [row for row in all_deltas if row["changed"]] if payload.changed_only else all_deltas
    submissions_by_id = {row.id: row for row in submissions}
    now = utc_now()
    applied: list[dict[str, Any]] = []
    for delta in deltas:
        submission = submissions_by_id.get(delta["submission_id"])
        if submission is None:
            continue
        new_result = dict(delta["new_grading_result"])
        new_result["regrade"] = {
            **dict(new_result.get("regrade") or {}),
            "reason": payload.reason,
            "reviewer": payload.reviewer,
            "applied_at": now.isoformat(),
        }
        submission.is_correct = delta["new"]["is_correct"]
        submission.effective_wrong = bool(delta["new"]["effective_wrong"])
        submission.question_version_id = delta["new"]["question_version_id"]
        submission.grading_result = new_result
        db.add(
            AnswerEvent(
                session_id=submission.session_id,
                question_id=question_id,
                event_type="submission_regraded",
                payload={
                    "submission_id": submission.id,
                    "reason": payload.reason,
                    "reviewer": payload.reviewer,
                    "previous": delta["previous"],
                    "new": delta["new"],
                    "changed": delta["changed"],
                    "changed_fields": delta["changed_fields"],
                    "answer_spec_hash": hash_payload(answer.raw_answer_spec or {}),
                    "question_version_id": version.id if version else None,
                    "at_ms": 0,
                },
            )
        )
        applied.append(_public_regrade_delta(delta))
    log_audit_event(
        db,
        "question.submissions_regraded",
        "question",
        question_id,
        {
            "question_id": question_id,
            "reviewer": payload.reviewer,
            "reason": payload.reason,
            "checked_count": len(submissions),
            "applied_count": len(applied),
            "changed_only": payload.changed_only,
            "answer_spec_id": answer.id,
            "answer_spec_hash": hash_payload(answer.raw_answer_spec or {}),
            "question_version_id": version.id if version else None,
        },
    )
    db.commit()
    return {
        "status": "applied",
        "question": _question_summary(question),
        "checked_count": len(submissions),
        "applied_count": len(applied),
        "changed_correct_to_wrong": sum(1 for row in applied if row["previous"]["is_correct"] is True and row["new"]["is_correct"] is False),
        "changed_wrong_to_correct": sum(1 for row in applied if row["previous"]["is_correct"] is False and row["new"]["is_correct"] is True),
        "items": applied[:200],
        "truncated": len(applied) > 200,
        "history_recalculation": _history_recalculation_from_regrade(db, question, submissions, deltas, "applied"),
    }


@router.get("/{question_id}/impact")
def get_question_impact(question_id: str, db: Session = Depends(get_db)) -> dict:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")

    session_impacts: dict[str, dict[str, Any]] = {}
    submission_rows = list(
        db.execute(
            select(Submission, PracticeSession)
            .join(PracticeSession, PracticeSession.id == Submission.session_id)
            .where(Submission.question_id == question_id)
            .order_by(Submission.created_at, Submission.id)
        ).all()
    )
    for submission, session in submission_rows:
        impact = _ensure_impact_session(session_impacts, session, question_id)
        impact["attempt_count"] += 1
        if impact["first_correct"] is None:
            impact["first_correct"] = submission.is_correct
            impact["first_submission_at"] = _iso(submission.created_at)
        impact["final_correct"] = submission.is_correct
        impact["last_submission_at"] = _iso(submission.created_at)
        impact["solution_revealed"] = bool(impact["solution_revealed"] or submission.solution_revealed)
        impact["max_help_level"] = max(int(impact["max_help_level"] or 0), int(submission.help_level_reached or 0))

    event_type_counts: Counter[str] = Counter()
    event_rows = list(
        db.execute(
            select(AnswerEvent, PracticeSession)
            .join(PracticeSession, PracticeSession.id == AnswerEvent.session_id)
            .where(AnswerEvent.question_id == question_id)
            .order_by(AnswerEvent.created_at, AnswerEvent.id)
        ).all()
    )
    for event, session in event_rows:
        impact = _ensure_impact_session(session_impacts, session, question_id)
        event_type_counts[event.event_type] += 1
        impact["event_count"] += 1
        impact["event_types"] = {**impact["event_types"], event.event_type: int(impact["event_types"].get(event.event_type, 0)) + 1}
        if event.event_type in {"solution_revealed", "answer_locked_by_solution_reveal"}:
            impact["solution_revealed"] = True
        if event.event_type == "help_revealed":
            impact["max_help_level"] = max(int(impact["max_help_level"] or 0), _help_level_from_payload(event.payload or {}))

    for session in db.scalars(select(PracticeSession).order_by(PracticeSession.created_at, PracticeSession.id)):
        if question_id in list(session.question_ids or []):
            _ensure_impact_session(session_impacts, session, question_id)

    learner_ids = sorted({str(row["learner_id"]) for row in session_impacts.values() if row.get("learner_id")})
    learner_names = {
        learner.id: learner.name
        for learner in db.scalars(select(LearnerProfile).where(LearnerProfile.id.in_(learner_ids))).all()
    } if learner_ids else {}
    for row in session_impacts.values():
        row["learner_name"] = learner_names.get(row["learner_id"], row["learner_id"])

    affected_sessions = sorted(
        session_impacts.values(),
        key=lambda row: (row.get("last_submission_at") or row.get("updated_at") or row.get("created_at") or "", row["session_id"]),
        reverse=True,
    )
    affected_learners: list[dict[str, Any]] = []
    for learner_id in learner_ids:
        rows = [row for row in affected_sessions if row.get("learner_id") == learner_id]
        final_correct = sum(1 for row in rows if row.get("final_correct") is True)
        final_wrong = sum(1 for row in rows if row.get("final_correct") is False)
        affected_learners.append(
            {
                "learner_id": learner_id,
                "learner_name": learner_names.get(learner_id, learner_id),
                "session_count": len(rows),
                "submission_count": sum(int(row.get("attempt_count") or 0) for row in rows),
                "final_correct_count": final_correct,
                "final_wrong_count": final_wrong,
                "solution_revealed_count": sum(1 for row in rows if row.get("solution_revealed")),
                "max_help_level": max([int(row.get("max_help_level") or 0) for row in rows] or [0]),
                "last_seen_at": max([row.get("last_submission_at") or row.get("updated_at") or row.get("created_at") or "" for row in rows] or [""]) or None,
            }
        )

    wrong_count = int(db.scalar(select(func.count()).select_from(WrongQuestionRecord).where(WrongQuestionRecord.question_id == question_id)) or 0)
    review_record_count = int(db.scalar(select(func.count()).select_from(ReviewRecord).where(ReviewRecord.question_id == question_id)) or 0)
    intervention_count = int(
        db.scalar(
            select(func.count()).select_from(InterventionRecord).where(or_(InterventionRecord.original_question_id == question_id, InterventionRecord.current_question_id == question_id))
        )
        or 0
    )
    open_review_items = list(db.scalars(select(ReviewQueueItem).where(ReviewQueueItem.question_id == question_id, ReviewQueueItem.status == "open")))
    version_count = int(db.scalar(select(func.count()).select_from(QuestionVersion).where(QuestionVersion.question_id == question_id)) or 0)
    alias_info = db.scalar(select(QuestionDuplicateAlias).where(QuestionDuplicateAlias.alias_question_id == question_id, QuestionDuplicateAlias.status == "active"))
    alias_count = int(db.scalar(select(func.count()).select_from(QuestionDuplicateAlias).where(QuestionDuplicateAlias.canonical_question_id == question_id, QuestionDuplicateAlias.status == "active")) or 0)

    submission_count = len(submission_rows)
    first_submission_at = min([row["first_submission_at"] for row in affected_sessions if row.get("first_submission_at")] or [None])
    last_submission_at = max([row["last_submission_at"] for row in affected_sessions if row.get("last_submission_at")] or [None])
    recalculation_recommended = bool(submission_count and (question.publish_status in {"quarantined", "retired", PUBLISH_STATUS_DUPLICATE_ALIAS} or question.question_fault_status not in {"", "none"}))

    return {
        "question": _question_summary(question),
        "totals": {
            "session_count": len(affected_sessions),
            "learner_count": len(learner_ids),
            "submission_count": submission_count,
            "event_count": len(event_rows),
            "correct_submission_count": sum(1 for submission, _ in submission_rows if submission.is_correct is True),
            "wrong_submission_count": sum(1 for submission, _ in submission_rows if submission.is_correct is False),
            "uncertain_submission_count": sum(1 for submission, _ in submission_rows if submission.is_correct is None),
            "solution_revealed_count": sum(1 for row in affected_sessions if row.get("solution_revealed")),
            "wrong_record_count": wrong_count,
            "review_record_count": review_record_count,
            "intervention_count": intervention_count,
            "open_review_queue_count": len(open_review_items),
            "version_count": version_count,
            "alias_count": alias_count,
            "is_duplicate_alias": alias_info is not None,
            "first_submission_at": first_submission_at,
            "last_submission_at": last_submission_at,
        },
        "event_counts_by_type": dict(sorted(event_type_counts.items())),
        "affected_learners": affected_learners,
        "affected_sessions": affected_sessions[:50],
        "governance": {
            "open_review_items": [
                {"id": item.id, "reason_code": item.reason_code, "severity": item.severity, "payload": item.payload, "created_at": _iso(item.created_at)}
                for item in open_review_items
            ],
            "canonical_alias": _duplicate_alias_response(alias_info, db) if alias_info else None,
            "alias_count": alias_count,
        },
        "history_recalculation": _history_recalculation_from_impact(question, affected_sessions, submission_count, recalculation_recommended),
    }


@router.get("/{question_id}/aliases")
def list_question_duplicate_aliases(question_id: str, db: Session = Depends(get_db)) -> dict:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    canonical_alias = db.scalar(
        select(QuestionDuplicateAlias).where(
            QuestionDuplicateAlias.alias_question_id == question_id,
            QuestionDuplicateAlias.status == "active",
        )
    )
    alias_rows = list(
        db.scalars(
            select(QuestionDuplicateAlias)
            .where(QuestionDuplicateAlias.canonical_question_id == question_id, QuestionDuplicateAlias.status == "active")
            .order_by(QuestionDuplicateAlias.created_at.desc(), QuestionDuplicateAlias.id)
        )
    )
    return {
        "question": _question_summary(question),
        "canonical_alias": _duplicate_alias_response(canonical_alias, db) if canonical_alias else None,
        "aliases": [_duplicate_alias_response(row, db) for row in alias_rows],
    }


@router.post("/duplicates/merge")
def merge_question_duplicates(payload: QuestionDuplicateMergeRequest, db: Session = Depends(get_db)) -> dict:
    canonical_id = payload.canonical_question_id.strip()
    alias_ids = [question_id.strip() for question_id in payload.alias_question_ids if question_id.strip()]
    alias_ids = list(dict.fromkeys(alias_ids))
    if not alias_ids:
        raise HTTPException(status_code=400, detail="alias_question_ids is empty")
    if canonical_id in alias_ids:
        raise HTTPException(status_code=400, detail="canonical question cannot be its own alias")

    canonical = db.get(Question, canonical_id)
    if canonical is None:
        raise HTTPException(status_code=404, detail=f"canonical question not found: {canonical_id}")
    canonical_parent = db.scalar(
        select(QuestionDuplicateAlias).where(
            QuestionDuplicateAlias.alias_question_id == canonical_id,
            QuestionDuplicateAlias.status == "active",
        )
    )
    if canonical_parent is not None:
        raise HTTPException(status_code=409, detail={"code": "canonical_is_alias", "canonical_question_id": canonical_id, "parent_canonical_question_id": canonical_parent.canonical_question_id})

    missing_alias_ids = [question_id for question_id in alias_ids if db.get(Question, question_id) is None]
    if missing_alias_ids:
        raise HTTPException(status_code=404, detail={"code": "alias_question_not_found", "question_ids": missing_alias_ids})

    alias_as_canonical = list(
        db.scalars(
            select(QuestionDuplicateAlias).where(
                QuestionDuplicateAlias.canonical_question_id.in_(alias_ids),
                QuestionDuplicateAlias.status == "active",
            )
        )
    )
    if alias_as_canonical:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "alias_has_own_aliases",
                "question_ids": sorted({row.canonical_question_id for row in alias_as_canonical}),
            },
        )

    rows: list[QuestionDuplicateAlias] = []
    created_count = 0
    touched_alias_ids: list[str] = []
    now = utc_now()
    for alias_id in alias_ids:
        existing = db.scalar(
            select(QuestionDuplicateAlias).where(
                QuestionDuplicateAlias.alias_question_id == alias_id,
                QuestionDuplicateAlias.status == "active",
            )
        )
        if existing is not None:
            if existing.canonical_question_id != canonical_id:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "alias_already_merged_elsewhere",
                        "alias_question_id": alias_id,
                        "existing_canonical_question_id": existing.canonical_question_id,
                    },
                )
            row = existing
        else:
            alias_question = db.get(Question, alias_id)
            previous = {
                "status": alias_question.status,
                "publish_status": alias_question.publish_status,
                "eligible_for_practice": alias_question.eligible_for_practice,
                "eligible_for_test": alias_question.eligible_for_test,
                "question_fault_status": alias_question.question_fault_status,
            }
            row = QuestionDuplicateAlias(
                canonical_question_id=canonical_id,
                alias_question_id=alias_id,
                status="active",
                reason=payload.reason,
                reviewer=payload.reviewer,
                metadata_json={
                    **(payload.metadata or {}),
                    "previous_alias_question_state": previous,
                    "merge_policy": "retire_alias_keep_history",
                },
            )
            db.add(row)
            created_count += 1
        if payload.retire_aliases:
            alias_question = db.get(Question, alias_id)
            alias_question.status = "retired"
            alias_question.publish_status = PUBLISH_STATUS_DUPLICATE_ALIAS
            alias_question.eligible_for_practice = False
            alias_question.eligible_for_test = False
            alias_question.question_fault_status = "duplicate"
            alias_question.question_fault_confidence = max(float(alias_question.question_fault_confidence or 0), 0.95)
            alias_question.question_trust_score = min(float(alias_question.question_trust_score or 1.0), 0.2)
            touched_alias_ids.append(alias_id)
        row.updated_at = now
        rows.append(row)

    review_items = list(
        db.scalars(
            select(ReviewQueueItem).where(
                ReviewQueueItem.question_id.in_(alias_ids),
                ReviewQueueItem.status == "open",
                ReviewQueueItem.reason_code.in_(("duplicate_question", "question_duplicate", "near_duplicate")),
            )
        )
    )
    for item in review_items:
        item.status = "resolved"
        item.payload = {
            **(item.payload or {}),
            "resolved_by": "duplicate_alias_merge",
            "canonical_question_id": canonical_id,
            "resolved_at": now.isoformat(),
        }

    db.flush()
    log_audit_event(
        db,
        "question_duplicate_aliases.merged",
        "question",
        canonical_id,
        {
            "canonical_question_id": canonical_id,
            "alias_question_ids": alias_ids,
            "created_count": created_count,
            "retired_alias_ids": touched_alias_ids,
            "closed_review_items": len(review_items),
            "reviewer": payload.reviewer,
        },
    )
    db.commit()
    return {
        "status": "merged",
        "canonical_question": _question_summary(canonical),
        "aliases": [_duplicate_alias_response(row, db) for row in rows],
        "created_count": created_count,
        "retired_alias_ids": touched_alias_ids,
        "closed_review_items": len(review_items),
    }


def redact_question_v2(question_v2: dict, include_answer: bool = False) -> dict:
    if include_answer:
        return question_v2
    redacted = dict(question_v2 or {})
    redacted.pop("answer_spec", None)
    redacted.pop("verification", None)
    solution = dict(redacted.get("solution") or {})
    for key in ("final_answer", "student_steps", "alternative_methods", "alternative_solution", "verification_solution"):
        solution.pop(key, None)
    if solution:
        redacted["solution"] = solution
    return redacted


@router.get("/{question_id}", response_model=QuestionDetail)
def get_question(question_id: str, include_answer: bool = False, db: Session = Depends(get_db)) -> QuestionDetail:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    version = db.get(QuestionVersion, question.current_version_id) if question.current_version_id else None
    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == question_id))
    tag_rows = db.execute(
        select(TaxonomyTag, QuestionTag.role).join(QuestionTag, QuestionTag.tag_id == TaxonomyTag.id).where(QuestionTag.question_id == question_id)
    ).all()
    return QuestionDetail(
        id=question.id,
        source_name=question.source_name,
        section=question.section,
        original_number=question.original_number,
        question_v2=redact_question_v2(version.question_v2 if version else {}, include_answer=include_answer),
        answer_spec=answer.raw_answer_spec if (answer and include_answer) else None,
        tags=[{"id": tag.id, "kind": tag.kind, "label": tag.label, "role": role} for tag, role in tag_rows],
    )
