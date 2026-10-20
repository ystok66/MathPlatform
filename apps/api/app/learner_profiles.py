from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import DEFAULT_LEARNER_ID, LearnerProfile, utc_now

DEFAULT_LEARNER_NAME = "默认学习者"


def ensure_default_learner(db: Session) -> LearnerProfile:
    row = db.get(LearnerProfile, DEFAULT_LEARNER_ID)
    if row is None:
        row = LearnerProfile(id=DEFAULT_LEARNER_ID, name=DEFAULT_LEARNER_NAME, status="active", display_order=0, settings={})
        db.add(row)
        db.flush()
    return row


def resolve_learner_id(db: Session, learner_id: str | None) -> str:
    requested = (learner_id or "").strip() or DEFAULT_LEARNER_ID
    ensure_default_learner(db)
    row = db.get(LearnerProfile, requested)
    if row is None:
        raise HTTPException(status_code=404, detail="learner profile not found")
    if row.status != "active":
        raise HTTPException(status_code=409, detail="learner profile is not active")
    return row.id


def active_learners(db: Session) -> list[LearnerProfile]:
    ensure_default_learner(db)
    return list(db.scalars(select(LearnerProfile).where(LearnerProfile.status == "active").order_by(LearnerProfile.display_order, LearnerProfile.created_at, LearnerProfile.id)))
