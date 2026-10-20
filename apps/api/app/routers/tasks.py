from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..task_center import (
    cancel_task_by_id,
    retry_task_by_id,
    schedule_generation_batch_queue,
    task_center_snapshot,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
def list_system_tasks(
    limit: int = Query(default=80, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    return task_center_snapshot(db, limit=limit)


@router.post("/{task_id}/cancel")
def cancel_system_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    result = cancel_task_by_id(db, task_id)
    schedule_generation_batch_queue(background_tasks)
    return {"ok": True, **result, "task_center": task_center_snapshot(db, limit=80)}


@router.post("/{task_id}/retry")
def retry_system_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    result = retry_task_by_id(db, task_id)
    schedule_generation_batch_queue(background_tasks)
    return {"ok": True, **result, "task_center": task_center_snapshot(db, limit=80)}


@router.post("/dispatch")
def dispatch_system_tasks(background_tasks: BackgroundTasks, db: Session = Depends(get_db)) -> dict:
    scheduled = schedule_generation_batch_queue(background_tasks)
    return {"ok": True, **scheduled, "task_center": task_center_snapshot(db, limit=80)}
