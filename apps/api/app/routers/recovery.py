from __future__ import annotations
from ..localization import LanguageRequest

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_gateway import progress_timestamp, set_generation_job_status
from ..audit import log_audit_event
from ..db import get_db
from ..models import GenerationBatch, GenerationBatchItem, GenerationJob, InterventionRecord, utc_now
from ..task_center import schedule_generation_batch_queue
from ..statuses import (
    ACTIVE_TASK_STATUSES,
    STATUS_CANCELLED,
    STATUS_CANCELLING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_TIMED_OUT,
    TERMINAL_BATCH_STATUSES,
    TERMINAL_JOB_STATUSES,
)
from .generation import (
    batch_items,
    mark_stale_batch_items,
    persist_batch_item_sse_event,
    persist_batch_sse_event,
    refresh_batch_counts,
    release_batch_item_lease,
    set_generation_batch_status,
    set_generation_item_status,
)

router = APIRouter(prefix="/api/recovery", tags=["maintenance"])


class StaleStateRecoveryRequest(LanguageRequest):
    stale_after_seconds: int = Field(default=300, ge=30, le=86_400)
    force_active_items: bool = False


def _older_than(value, cutoff) -> bool:
    return value is not None and value < cutoff


def _terminalize_batch_if_idle(db: Session, batch: GenerationBatch, *, reason: str) -> bool:
    if batch.status in TERMINAL_BATCH_STATUSES:
        return False
    rows = batch_items(db, batch.id)
    active = [row for row in rows if row.status in ACTIVE_TASK_STATUSES]
    if active:
        return False
    refresh_batch_counts(db, batch)
    if batch.status in TERMINAL_BATCH_STATUSES:
        return True
    if batch.status == STATUS_CANCELLING:
        set_generation_batch_status(batch, STATUS_CANCELLED)
    elif batch.accepted_count >= batch.target_count:
        set_generation_batch_status(batch, STATUS_COMPLETED)
    elif batch.accepted_count > 0:
        set_generation_batch_status(batch, STATUS_PARTIAL)
    else:
        set_generation_batch_status(batch, STATUS_FAILED)
    batch.completed_at = batch.completed_at or utc_now()
    batch.progress = {
        **(batch.progress or {}),
        "stage": batch.status,
        "recovered_at": progress_timestamp(),
        "recovery_reason": reason,
    }
    batch.updated_at = utc_now()
    persist_batch_sse_event(db, batch)
    return True


@router.post("/stale-state")
def recover_stale_state(
    payload: StaleStateRecoveryRequest | None = None,
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payload = payload or StaleStateRecoveryRequest()
    cutoff = utc_now() - timedelta(seconds=payload.stale_after_seconds)
    counts = {
        "batches_seen": 0,
        "batches_terminalized": 0,
        "items_timed_out": 0,
        "jobs_timed_out": 0,
        "interventions_failed": 0,
    }

    active_batches = list(
        db.scalars(
            select(GenerationBatch)
            .where(GenerationBatch.status.in_(["queued", "running", STATUS_CANCELLING]))
            .order_by(GenerationBatch.updated_at, GenerationBatch.id)
        )
    )
    for batch in active_batches:
        counts["batches_seen"] += 1
        mark_stale_batch_items(db, batch)
        rows = batch_items(db, batch.id)
        for item in rows:
            if item.status not in ACTIVE_TASK_STATUSES:
                continue
            if not payload.force_active_items and not _older_than(item.updated_at or item.created_at, cutoff):
                continue
            if item.job_id:
                job = db.get(GenerationJob, item.job_id)
                if job is not None and job.status not in TERMINAL_JOB_STATUSES:
                    set_generation_job_status(job, STATUS_TIMED_OUT)
                    job.progress = {
                        **(job.progress or {}),
                        "stage": STATUS_TIMED_OUT,
                        "terminal_reason": "manual_recovery_stale_job",
                        "recovered_at": progress_timestamp(),
                    }
                    job.updated_at = utc_now()
                    counts["jobs_timed_out"] += 1
            set_generation_item_status(item, STATUS_TIMED_OUT)
            item.rejection_reason = "manual_recovery_stale_item"
            item.updated_at = utc_now()
            release_batch_item_lease(item, source_type=(item.similarity_review or {}).get("source_type") or "pending", terminal_reason="manual_recovery_stale_item")
            persist_batch_item_sse_event(db, item)
            counts["items_timed_out"] += 1
        if _terminalize_batch_if_idle(db, batch, reason="manual_recovery_idle_batch"):
            counts["batches_terminalized"] += 1
        refresh_batch_counts(db, batch)

    orphan_jobs = list(
        db.scalars(
            select(GenerationJob)
            .where(GenerationJob.status.in_(["queued", "running"]))
            .order_by(GenerationJob.updated_at, GenerationJob.id)
        )
    )
    for job in orphan_jobs:
        if not payload.force_active_items and not _older_than(job.updated_at or job.created_at, cutoff):
            continue
        set_generation_job_status(job, STATUS_TIMED_OUT)
        job.progress = {
            **(job.progress or {}),
            "stage": STATUS_TIMED_OUT,
            "terminal_reason": "manual_recovery_orphan_stale_job",
            "recovered_at": progress_timestamp(),
        }
        job.updated_at = utc_now()
        counts["jobs_timed_out"] += 1

    interventions = list(
        db.scalars(
            select(InterventionRecord)
            .where(
                InterventionRecord.status.in_(["active", "prefetched"]),
                InterventionRecord.prefetch_status == "generating",
            )
            .order_by(InterventionRecord.updated_at, InterventionRecord.id)
        )
    )
    for record in interventions:
        if not payload.force_active_items and not _older_than(record.updated_at or record.created_at, cutoff):
            continue
        record.status = "failed"
        record.prefetch_status = "failed"
        record.payload = {
            **(record.payload or {}),
            "state": "failed",
            "terminal_reason": "manual_recovery_stale_intervention",
            "recovered_at": progress_timestamp(),
        }
        record.updated_at = utc_now()
        counts["interventions_failed"] += 1

    log_audit_event(db, "maintenance.stale_state_recovered", "maintenance", None, {"request": payload.model_dump(), "counts": counts})
    db.commit()
    dispatch = schedule_generation_batch_queue(background_tasks)
    return {
        "schema_version": "stale_state_recovery_v1",
        "status": "ok",
        "cutoff": progress_timestamp(cutoff),
        "counts": counts,
        "queue_dispatch": dispatch,
        "preserved": ["question_bank", "practice_history", "reports", "model_settings", "api_keys"],
        "note": "This only repairs stale queued/running states; it does not delete history, reports, question bank, or API settings.",
    }
