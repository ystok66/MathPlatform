from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_audit_event
from ..db import get_db
from ..learner_profiles import resolve_learner_id
from ..learning_analysis import analyze_submission, create_review_record, deep_diagnose_submission, record_wrong_answer
from ..mastery import build_mastery_payload, build_today_tasks
from ..models import AnswerEvent, PracticeSession, Question, QuestionTag, ReviewRecord, Submission, WrongQuestionRecord, utc_now
from ..schemas import CreatePracticeSessionResponse
from ..session_idempotency import find_idempotent_practice_session, ordered_unique_question_ids, practice_session_response

router = APIRouter(prefix="/api/learning", tags=["learning"])


class WrongRecordUpdate(BaseModel):
    status: Literal["active", "reviewing", "resolved", "ignored"]


class ReviewRecordRequest(BaseModel):
    status: Literal["pending", "completed"] = "pending"
    payload: dict[str, Any] = Field(default_factory=dict)


class LearningReviewSessionRequest(BaseModel):
    learner_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)
    question_ids: list[str] = Field(default_factory=list)
    target_tags: list[str] = Field(default_factory=list)
    difficulty_star: int | None = Field(default=None, ge=1, le=5)
    question_count: int = Field(default=5, ge=1, le=50)
    exclude_answered: bool = True
    reason: str = Field(default="learning_review", max_length=128)


@router.post("/analyze-submission/{submission_id}")
def analyze_submission_endpoint(submission_id: str, db: Session = Depends(get_db)) -> dict:
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="submission not found")
    analysis = analyze_submission(submission, db)
    record = record_wrong_answer(db, submission, analysis)
    db.commit()
    return {"analysis": analysis, "wrong_record_id": record.id if record else None}


@router.post("/deep-diagnose/{submission_id}")
def deep_diagnose_endpoint(submission_id: str, db: Session = Depends(get_db)) -> dict:
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="submission not found")
    result = deep_diagnose_submission(submission, db)
    log_audit_event(
        db,
        "learning.deep_diagnosis_created",
        "submission",
        submission_id,
        {"provider": result["provider"], "model": result["model"], "error": result["error"]},
    )
    db.commit()
    return result


@router.get("/wrong-book")
def wrong_book(status: str | None = "active", learner_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    stmt = select(WrongQuestionRecord, Question).join(Question, Question.id == WrongQuestionRecord.question_id)
    if status:
        stmt = stmt.where(WrongQuestionRecord.status == status)
    if learner_id:
        resolved_learner_id = resolve_learner_id(db, learner_id)
        stmt = stmt.join(PracticeSession, PracticeSession.id == WrongQuestionRecord.session_id).where(PracticeSession.learner_id == resolved_learner_id)
    rows = list(db.execute(stmt.order_by(WrongQuestionRecord.created_at.desc(), WrongQuestionRecord.id)).all())
    return {
        "items": [
            {
                "id": record.id,
                "session_id": record.session_id,
                "question_id": record.question_id,
                "submission_id": record.submission_id,
                "status": record.status,
                "error_type": record.error_type,
                "analysis": record.analysis,
                "question": {
                    "source_name": question.source_name,
                    "difficulty_star": question.difficulty_star,
                    "stem_preview": question.stem_preview,
                },
                "created_at": record.created_at.isoformat(),
                "updated_at": record.updated_at.isoformat(),
            }
            for record, question in rows
        ]
    }


@router.put("/wrong-book/{record_id}")
def update_wrong_record(record_id: str, payload: WrongRecordUpdate, db: Session = Depends(get_db)) -> dict:
    record = db.get(WrongQuestionRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="wrong record not found")
    record.status = payload.status
    record.updated_at = utc_now()
    db.commit()
    return {"id": record.id, "status": record.status}


@router.post("/wrong-book/{record_id}/review")
def create_review(record_id: str, payload: ReviewRecordRequest, db: Session = Depends(get_db)) -> dict:
    record = db.get(WrongQuestionRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="wrong record not found")
    review = create_review_record(db, record, payload.payload)
    review.status = payload.status
    record.status = "reviewing" if payload.status == "pending" else "resolved"
    record.updated_at = utc_now()
    db.commit()
    return {"id": review.id, "status": review.status, "source_record_id": review.source_record_id}


@router.get("/reviews")
def list_reviews(status: str | None = None, learner_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    stmt = select(ReviewRecord)
    if status:
        stmt = stmt.where(ReviewRecord.status == status)
    if learner_id:
        resolved_learner_id = resolve_learner_id(db, learner_id)
        stmt = stmt.join(PracticeSession, PracticeSession.id == ReviewRecord.session_id).where(PracticeSession.learner_id == resolved_learner_id)
    reviews = list(db.scalars(stmt.order_by(ReviewRecord.created_at.desc(), ReviewRecord.id)))
    return {
        "items": [
            {
                "id": row.id,
                "question_id": row.question_id,
                "session_id": row.session_id,
                "source_record_id": row.source_record_id,
                "status": row.status,
                "payload": row.payload,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
            for row in reviews
        ]
    }


@router.get("/mastery")
def mastery_profile(learner_id: str | None = None, limit: int = 80, db: Session = Depends(get_db)) -> dict:
    resolved_learner_id = resolve_learner_id(db, learner_id)
    return build_mastery_payload(db, resolved_learner_id, limit=limit)


@router.get("/today-tasks")
def today_tasks(learner_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    resolved_learner_id = resolve_learner_id(db, learner_id)
    return build_today_tasks(db, resolved_learner_id)


@router.post("/review-session", response_model=CreatePracticeSessionResponse)
def create_learning_review_session(payload: LearningReviewSessionRequest, db: Session = Depends(get_db)) -> CreatePracticeSessionResponse:
    learner_id = resolve_learner_id(db, payload.learner_id)
    existing = find_idempotent_practice_session(db, learner_id=learner_id, idempotency_key=payload.idempotency_key, mode="review_practice")
    if existing is not None:
        return practice_session_response(existing)
    seen: set[str] = set()
    requested_ids = []
    for question_id in payload.question_ids:
        if question_id and question_id not in seen:
            requested_ids.append(question_id)
            seen.add(question_id)
    target_tags = []
    for tag_id in payload.target_tags:
        if tag_id and tag_id not in target_tags:
            target_tags.append(tag_id)
    selection_mode = "explicit_questions" if requested_ids else "tag_star_report_task"
    fallback_used: str | None = None
    if requested_ids:
        rows = list(
            db.scalars(
                select(Question).where(
                    Question.id.in_(requested_ids),
                    Question.status == "verified",
                    Question.eligible_for_practice.is_(True),
                    Question.question_fault_status == "none",
                )
            )
        )
        available_ids = [question_id for question_id in requested_ids if any(row.id == question_id for row in rows)]
    else:
        available_ids = _select_review_questions(
            db=db,
            learner_id=learner_id,
            target_tags=target_tags,
            difficulty_star=payload.difficulty_star,
            question_count=payload.question_count,
            exclude_answered=payload.exclude_answered,
        )
        if not available_ids and payload.exclude_answered:
            available_ids = _select_review_questions(
                db=db,
                learner_id=learner_id,
                target_tags=target_tags,
                difficulty_star=payload.difficulty_star,
                question_count=payload.question_count,
                exclude_answered=False,
            )
            if available_ids:
                fallback_used = "answered_pool"
    if not available_ids:
        raise HTTPException(status_code=409, detail="no available questions for review session")
    config = {
        "learner_id": learner_id,
        "review_source": {
            "source": "learning_review_session",
            "reason": payload.reason,
            "idempotency_key": payload.idempotency_key,
            "selection_mode": selection_mode,
            "target_tags": target_tags,
            "difficulty_star": payload.difficulty_star,
            "exclude_answered": payload.exclude_answered,
            "fallback_used": fallback_used,
            "requested_count": len(requested_ids) if requested_ids else payload.question_count,
            "available_count": len(available_ids),
            "skipped_count": (len(requested_ids) - len(available_ids)) if requested_ids else max(0, payload.question_count - len(available_ids)),
            "created_at": utc_now().isoformat(),
        },
        "selection": {"include_generated": True, "strict_no_ai": False, "learning_review_reason": payload.reason},
        "break_confirmed": [],
    }
    available_ids = ordered_unique_question_ids(available_ids)
    session = PracticeSession(mode="review_practice", learner_id=learner_id, status="active", config=config, question_ids=available_ids)
    db.add(session)
    db.flush()
    db.add(AnswerEvent(session_id=session.id, question_id="", event_type="learning_review_session_created", payload={"question_ids": available_ids, "reason": payload.reason, "idempotency_key": payload.idempotency_key, "at_ms": 0}))
    log_audit_event(db, "learning.review_session_created", "practice_session", session.id, {"learner_id": learner_id, "question_count": len(available_ids), "reason": payload.reason})
    db.commit()
    return practice_session_response(session)


def _select_review_questions(
    *,
    db: Session,
    learner_id: str,
    target_tags: list[str],
    difficulty_star: int | None,
    question_count: int,
    exclude_answered: bool,
) -> list[str]:
    stmt = select(Question).where(
        Question.status == "verified",
        Question.eligible_for_practice.is_(True),
        Question.question_fault_status == "none",
    )
    if difficulty_star is not None:
        stmt = stmt.where(Question.difficulty_star == difficulty_star)
    if target_tags:
        tagged_questions = select(QuestionTag.question_id).where(QuestionTag.tag_id.in_(target_tags))
        stmt = stmt.where(Question.id.in_(tagged_questions))
    if exclude_answered:
        answered_questions = (
            select(Submission.question_id)
            .join(PracticeSession, PracticeSession.id == Submission.session_id)
            .where(PracticeSession.learner_id == learner_id)
        )
        stmt = stmt.where(Question.id.notin_(answered_questions))
    rows = list(db.scalars(stmt.limit(max(question_count * 8, 80))).all())
    if not rows:
        return []
    overlap: dict[str, int] = {row.id: 0 for row in rows}
    if target_tags:
        candidate_ids = list(overlap)
        tag_rows = db.execute(
            select(QuestionTag.question_id, QuestionTag.tag_id).where(
                QuestionTag.question_id.in_(candidate_ids),
                QuestionTag.tag_id.in_(target_tags),
            )
        ).all()
        for question_id, tag_id in tag_rows:
            if tag_id in target_tags:
                overlap[question_id] = overlap.get(question_id, 0) + 1
    rows.sort(key=lambda row: (-overlap.get(row.id, 0), not row.premium, row.created_at.isoformat() if row.created_at else "", row.id))
    return [row.id for row in rows[:question_count]]
