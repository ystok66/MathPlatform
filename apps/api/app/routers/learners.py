from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..audit import log_audit_event
from ..db import get_db
from ..learner_profiles import active_learners, ensure_default_learner
from ..models import DEFAULT_LEARNER_ID, LearnerProfile, utc_now

router = APIRouter(prefix="/api/learners", tags=["learners"])


class LearnerProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    settings: dict[str, Any] = Field(default_factory=dict)


class LearnerProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    status: str | None = Field(default=None, pattern="^(active|archived)$")
    settings: dict[str, Any] | None = None


def learner_payload(row: LearnerProfile) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "status": row.status,
        "display_order": row.display_order,
        "settings": row.settings or {},
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "is_default": row.id == DEFAULT_LEARNER_ID,
    }


@router.get("")
def list_learners(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = active_learners(db)
    db.commit()
    return {"default_learner_id": DEFAULT_LEARNER_ID, "items": [learner_payload(row) for row in rows]}


@router.post("")
def create_learner(payload: LearnerProfileRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    ensure_default_learner(db)
    row = LearnerProfile(name=payload.name.strip(), status="active", display_order=len(active_learners(db)), settings=payload.settings)
    db.add(row)
    db.flush()
    log_audit_event(db, "learner.created", "learner_profile", row.id, {"name": row.name})
    db.commit()
    return learner_payload(row)


@router.put("/{learner_id}")
def update_learner(learner_id: str, payload: LearnerProfileUpdateRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    ensure_default_learner(db)
    row = db.get(LearnerProfile, learner_id)
    if row is None:
        raise HTTPException(status_code=404, detail="learner profile not found")
    if row.id == DEFAULT_LEARNER_ID and payload.status == "archived":
        raise HTTPException(status_code=409, detail="default learner cannot be archived")
    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.status is not None:
        row.status = payload.status
    if payload.settings is not None:
        row.settings = payload.settings
    row.updated_at = utc_now()
    log_audit_event(db, "learner.updated", "learner_profile", row.id, {"name": row.name, "status": row.status})
    db.commit()
    return learner_payload(row)
