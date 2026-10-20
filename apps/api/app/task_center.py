from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_gateway import progress_timestamp, set_generation_job_status
from .db import get_engine
from .models import (
    GenerationBatch,
    GenerationBatchItem,
    GenerationJob,
    InterventionRecord,
    RecoveryAgentRun,
    SystemTask,
    utc_now,
)
from .statuses import (
    ACTIVE_TASK_STATUSES,
    STATUS_CANCELLED,
    STATUS_CANCELLING,
    STATUS_ACCEPTED,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_TIMED_OUT,
    TERMINAL_BATCH_STATUSES,
    TERMINAL_JOB_STATUSES,
)


ACTIVE_CENTER_STATUSES = {STATUS_QUEUED, STATUS_RUNNING, STATUS_CANCELLING}
RETRYABLE_BATCH_STATUSES = {STATUS_FAILED, STATUS_PARTIAL, STATUS_TIMED_OUT}
AGENT_TERMINAL_STATUSES = {"completed", STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMED_OUT}
_DISPATCH_LOCK = threading.Lock()


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else None


def _batch_origin(batch: GenerationBatch) -> str:
    request = batch.request or {}
    progress = batch.progress or {}
    return str(request.get("origin") or progress.get("origin") or "generation_batch")


def _batch_title(batch: GenerationBatch) -> str:
    request = batch.request or {}
    display_name = request.get("display_name") or request.get("title")
    if display_name:
        return str(display_name)
    if _batch_origin(batch) == "mastery_variant_batch":
        return "批量做类似题/检测"
    return "批量出题"


def _status_can_cancel(status: str) -> bool:
    return status in ACTIVE_CENTER_STATUSES


def _batch_summary(batch: GenerationBatch) -> dict[str, Any]:
    progress = batch.progress or {}
    task_type = "mastery_variant_batch" if _batch_origin(batch) == "mastery_variant_batch" else "generation_batch"
    updated_at = batch.updated_at or batch.created_at
    stale_seconds = None
    if batch.status in ACTIVE_CENTER_STATUSES and updated_at:
        stale_seconds = max(0, int((utc_now() - updated_at).total_seconds()))
    return {
        "task_id": f"batch:{batch.id}",
        "task_type": task_type,
        "source": str(progress.get("source") or progress.get("page") or "generation"),
        "status": batch.status,
        "title": _batch_title(batch),
        "description": f"{batch.accepted_count}/{batch.target_count} 可练，失败/拒绝 {batch.rejected_count + batch.failed_count}",
        "target_type": "generation_batch",
        "target_id": batch.id,
        "progress": {
            **progress,
            "accepted_count": batch.accepted_count,
            "rejected_count": batch.rejected_count,
            "failed_count": batch.failed_count,
            "target_count": batch.target_count,
            "concurrency": batch.concurrency,
            "eta_seconds": batch.eta_seconds,
            "stale_seconds": stale_seconds,
        },
        "result": None,
        "error": str(progress.get("error") or progress.get("failure_reason") or "") or None,
        "can_cancel": _status_can_cancel(batch.status),
        "can_retry": batch.status in RETRYABLE_BATCH_STATUSES,
        "created_at": _iso(batch.created_at),
        "updated_at": _iso(batch.updated_at),
        "completed_at": _iso(batch.completed_at),
    }


def _job_title(job: GenerationJob) -> str:
    request = job.request or {}
    origin = request.get("origin")
    if origin == "strict_test_ai_fill":
        return "严格测试 AI 补题"
    if origin == "mastery_check":
        return "做类似题/检测"
    return "单题 AI 出题"


def _job_summary(job: GenerationJob) -> dict[str, Any]:
    progress = job.progress or {}
    request = job.request or {}
    return {
        "task_id": f"job:{job.id}",
        "task_type": str(request.get("origin") or "generation_job"),
        "source": str(request.get("source") or "practice"),
        "status": job.status,
        "title": _job_title(job),
        "description": str(progress.get("stage") or progress.get("reason") or request.get("topic") or ""),
        "target_type": "generation_job",
        "target_id": job.id,
        "progress": progress,
        "result": {"accepted_question_id": job.accepted_question_id} if job.accepted_question_id else None,
        "error": str(progress.get("error") or progress.get("failure_reason") or "") or None,
        "can_cancel": job.status in ACTIVE_TASK_STATUSES,
        "can_retry": job.status in {STATUS_FAILED, STATUS_TIMED_OUT},
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
        "completed_at": None,
    }


def _intervention_summary(record: InterventionRecord) -> dict[str, Any]:
    payload = record.payload or {}
    return {
        "task_id": f"intervention:{record.id}",
        "task_type": str(record.intervention_type or "intervention"),
        "source": "practice",
        "status": str(record.prefetch_status or record.status),
        "title": "提示题生成" if record.intervention_type == "hint" else "做类似题/检测",
        "description": f"session {record.session_id} · 原题 {record.original_question_id}",
        "target_type": "intervention_record",
        "target_id": record.id,
        "progress": {
            "session_id": record.session_id,
            "original_question_id": record.original_question_id,
            "current_question_id": record.current_question_id,
            "prefetch_status": record.prefetch_status,
            "status": record.status,
            **payload,
        },
        "result": None,
        "error": str(payload.get("error") or payload.get("failure_reason") or "") or None,
        "can_cancel": record.prefetch_status == "generating",
        "can_retry": record.prefetch_status == "failed",
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
        "completed_at": None,
    }


def _system_task_summary(task: SystemTask) -> dict[str, Any]:
    return {
        "task_id": f"system:{task.id}",
        "task_type": task.task_type,
        "source": task.source,
        "status": task.status,
        "title": task.title,
        "description": task.description,
        "target_type": task.target_type,
        "target_id": task.target_id,
        "progress": task.progress or {},
        "result": task.result,
        "error": task.error,
        "can_cancel": task.status in ACTIVE_CENTER_STATUSES,
        "can_retry": task.status in {STATUS_FAILED, STATUS_TIMED_OUT},
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
        "completed_at": _iso(task.completed_at),
    }


def _agent_run_summary(run: RecoveryAgentRun) -> dict[str, Any]:
    return {
        "task_id": f"agent:{run.id}",
        "task_type": "emergency_recovery_agent",
        "source": "recovery",
        "status": run.status,
        "title": "高级 AI 诊断修复",
        "description": f"{run.loop_count}/{run.max_loops} 轮 · {run.model}",
        "target_type": "recovery_agent_run",
        "target_id": run.id,
        "progress": {"loop_count": run.loop_count, "max_loops": run.max_loops, "cancel_requested": run.cancel_requested},
        "result": run.report,
        "error": run.error,
        "can_cancel": run.status in {"queued", STATUS_RUNNING},
        "can_retry": run.status in {STATUS_FAILED, STATUS_TIMED_OUT},
        "created_at": _iso(run.created_at),
        "updated_at": _iso(run.updated_at),
        "completed_at": _iso(run.completed_at),
    }


def _sort_key(item: dict[str, Any]) -> tuple[int, str]:
    active_rank = 0 if item["status"] in ACTIVE_CENTER_STATUSES or item["status"] == "generating" else 1
    timestamp = item.get("updated_at") or item.get("created_at") or ""
    return (active_rank, timestamp)


def task_center_snapshot(db: Session, limit: int = 80) -> dict[str, Any]:
    batches = db.scalars(select(GenerationBatch).order_by(GenerationBatch.updated_at.desc()).limit(limit)).all()
    jobs = db.scalars(select(GenerationJob).order_by(GenerationJob.updated_at.desc()).limit(limit)).all()
    interventions = db.scalars(
        select(InterventionRecord)
        .where(InterventionRecord.prefetch_status.in_(["generating", "failed", "ready"]))
        .order_by(InterventionRecord.updated_at.desc())
        .limit(limit)
    ).all()
    system_tasks = db.scalars(select(SystemTask).order_by(SystemTask.updated_at.desc()).limit(limit)).all()
    agent_runs = db.scalars(select(RecoveryAgentRun).order_by(RecoveryAgentRun.updated_at.desc()).limit(10)).all()
    tasks = (
        [_batch_summary(batch) for batch in batches]
        + [_job_summary(job) for job in jobs]
        + [_intervention_summary(record) for record in interventions]
        + [_system_task_summary(task) for task in system_tasks]
        + [_agent_run_summary(run) for run in agent_runs]
    )
    tasks.sort(key=_sort_key)
    active_tasks = [item for item in tasks if item["status"] in ACTIVE_CENTER_STATUSES or item["status"] == "generating"]
    inactive_tasks = [item for item in tasks if item not in active_tasks]
    active_tasks.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    inactive_tasks.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    tasks = (active_tasks + inactive_tasks)[:limit]
    running_count = sum(1 for item in tasks if item["status"] == STATUS_RUNNING or item["status"] == "generating")
    queued_count = sum(1 for item in tasks if item["status"] == STATUS_QUEUED)
    failed_count = sum(1 for item in tasks if item["status"] in {STATUS_FAILED, STATUS_TIMED_OUT})
    active_count = sum(1 for item in tasks if item["status"] in ACTIVE_CENTER_STATUSES or item["status"] == "generating")
    stale_count = sum(
        1
        for item in tasks
        if item["status"] in {STATUS_RUNNING, STATUS_CANCELLING, "generating"}
        and int(((item.get("progress") or {}).get("stale_seconds") or 0)) >= 300
    )
    return {
        "schema_version": "task_center_v1",
        "generated_at": _iso(utc_now()),
        "active_count": active_count,
        "running_count": running_count,
        "queued_count": queued_count,
        "failed_count": failed_count,
        "stale_count": stale_count,
        "tasks": tasks,
        "guidance": [
            "重型 AI 操作默认排队，避免多个批次同时压爆外部 API。",
            "运行中或等待中的任务可以取消；取消后不会阻塞下一次生成。",
            "长时间无更新时先点“修复异常状态”，常规修复失败再用“高级 AI 诊断修复”。",
        ],
    }


def _active_batch_exists(db: Session) -> bool:
    active = db.scalars(
        select(GenerationBatch.id)
        .where(GenerationBatch.status == STATUS_RUNNING)
        .limit(1)
    ).first()
    return active is not None


def _claim_next_queued_batch(db: Session) -> tuple[str, str, str] | None:
    with _DISPATCH_LOCK:
        claim_token = str(uuid4())
        try:
            if db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if _active_batch_exists(db):
                db.rollback()
                return None
            batch = db.scalars(
                select(GenerationBatch)
                .where(GenerationBatch.status == STATUS_QUEUED)
                .order_by(GenerationBatch.created_at.asc())
                .limit(1)
            ).first()
            if not batch:
                db.rollback()
                return None
            now = utc_now()
            batch.status = STATUS_RUNNING
            batch.queue_claim_token = claim_token
            batch.queue_claimed_at = now
            batch.started_at = batch.started_at or now
            progress = batch.progress or {}
            progress.update(
                {
                    "stage": "running",
                    "queue_policy": "one_active_generation_batch",
                    "queue_claim_token": claim_token,
                    "queue_claimed_at": progress_timestamp(now),
                }
            )
            batch.progress = progress
            batch.updated_at = now
            db.add(batch)
            db.commit()
            request = batch.request or {}
            return batch.id, str(request.get("batch_provider") or request.get("provider") or "mock"), _batch_origin(batch)
        except Exception:
            db.rollback()
            raise


def _mark_batch_background_error(batch_id: str, message: str) -> None:
    with Session(get_engine()) as db:
        batch = db.get(GenerationBatch, batch_id)
        if not batch:
            return
        if batch.status not in TERMINAL_BATCH_STATUSES:
            batch.status = STATUS_FAILED
        progress = batch.progress or {}
        progress.update({"stage": "failed", "error": message[:1000], "failed_at": progress_timestamp()})
        batch.progress = progress
        batch.completed_at = batch.completed_at or utc_now()
        batch.updated_at = utc_now()
        db.add(batch)
        db.commit()


def dispatch_generation_batch_queue(max_batches: int = 20) -> dict[str, Any]:
    started: list[dict[str, str]] = []
    for _ in range(max(1, max_batches)):
        with Session(get_engine()) as db:
            claimed = _claim_next_queued_batch(db)
        if not claimed:
            break
        batch_id, provider, origin = claimed
        started.append({"batch_id": batch_id, "origin": origin})
        try:
            if origin == "mastery_variant_batch":
                from .routers.mastery_variants import process_mastery_variant_batch

                with Session(get_engine()) as run_db:
                    process_mastery_variant_batch(run_db, batch_id, provider)
            else:
                from .routers.generation import process_generation_batch

                with Session(get_engine()) as run_db:
                    process_generation_batch(run_db, batch_id, provider=provider, threaded=True)
        except Exception as exc:  # pragma: no cover - background protection
            _mark_batch_background_error(batch_id, str(exc))
    return {"started_count": len(started), "started": started}


def _dispatch_generation_batch_queue_guarded(max_batches: int = 20) -> dict[str, Any]:
    result = dispatch_generation_batch_queue(max_batches=max_batches)
    return {"started": True, **result}


def schedule_generation_batch_queue(background_tasks: BackgroundTasks | None = None) -> dict[str, Any]:
    if background_tasks is not None:
        background_tasks.add_task(_dispatch_generation_batch_queue_guarded)
        return {"scheduled": True, "mode": "fastapi_background"}
    thread = threading.Thread(target=_dispatch_generation_batch_queue_guarded, name="generation-batch-dispatcher", daemon=True)
    thread.start()
    return {"scheduled": True, "mode": "thread"}


def cancel_task_by_id(db: Session, task_id: str) -> dict[str, Any]:
    kind, _, raw_id = task_id.partition(":")
    target_id = raw_id or task_id
    if kind in {"batch", ""}:
        batch = db.get(GenerationBatch, target_id)
        if batch:
            return _cancel_batch(db, batch)
    if kind == "job" or not raw_id:
        job = db.get(GenerationJob, target_id)
        if job:
            if job.status in TERMINAL_JOB_STATUSES:
                return {"changed": False, "message": "任务已经结束。"}
            set_generation_job_status(job, STATUS_CANCELLED)
            progress = job.progress or {}
            progress.update({"stage": "cancelled", "cancelled_at": progress_timestamp(), "cancelled_by": "user"})
            job.progress = progress
            job.updated_at = utc_now()
            db.add(job)
            db.commit()
            return {"changed": True, "message": "单题任务已取消。"}
    if kind == "intervention" or not raw_id:
        record = db.get(InterventionRecord, target_id)
        if record:
            payload = record.payload or {}
            payload.update({"state": "cancelled", "cancelled_at": progress_timestamp(), "cancelled_by": "user"})
            record.payload = payload
            record.prefetch_status = "unused_prefetch" if record.prefetch_status == "generating" else record.prefetch_status
            record.status = "cancelled"
            record.updated_at = utc_now()
            db.add(record)
            db.commit()
            return {"changed": True, "message": "提示/类似题任务已取消。"}
    if kind == "system" or not raw_id:
        task = db.get(SystemTask, target_id)
        if task:
            if task.status in AGENT_TERMINAL_STATUSES:
                return {"changed": False, "message": "任务已经结束。"}
            task.cancel_requested = True
            task.status = STATUS_CANCELLED if task.status == STATUS_QUEUED else STATUS_CANCELLING
            task.updated_at = utc_now()
            if task.status == STATUS_CANCELLED:
                task.completed_at = utc_now()
            db.add(task)
            db.commit()
            return {"changed": True, "message": "系统任务已收到取消请求。"}
    if kind == "agent" or not raw_id:
        run = db.get(RecoveryAgentRun, target_id)
        if run:
            if run.status in AGENT_TERMINAL_STATUSES:
                return {"changed": False, "message": "Agent 已经结束。"}
            run.cancel_requested = True
            if run.status == STATUS_QUEUED:
                run.status = STATUS_CANCELLED
                run.completed_at = utc_now()
            run.updated_at = utc_now()
            db.add(run)
            db.commit()
            return {"changed": True, "message": "高级 AI 诊断修复已收到中断请求。"}
    raise HTTPException(status_code=404, detail="task_not_found")


def _cancel_batch(db: Session, batch: GenerationBatch) -> dict[str, Any]:
    if batch.status in TERMINAL_BATCH_STATUSES:
        return {"changed": False, "message": "批次已经结束。", "batch_id": batch.id, "status": batch.status}
    now = utc_now()
    rows = db.scalars(select(GenerationBatchItem).where(GenerationBatchItem.batch_id == batch.id)).all()
    active_running = False
    for row in rows:
        if row.status == STATUS_QUEUED:
            row.status = STATUS_CANCELLED
            row.completed_at = now
            row.updated_at = now
            if row.job_id:
                job = db.get(GenerationJob, row.job_id)
                if job and job.status in ACTIVE_TASK_STATUSES:
                    set_generation_job_status(job, STATUS_CANCELLED)
                    job.progress = {**(job.progress or {}), "stage": "cancelled", "cancelled_at": progress_timestamp()}
                    job.updated_at = now
                    db.add(job)
            db.add(row)
        elif row.status == STATUS_RUNNING:
            active_running = True
    batch.status = STATUS_CANCELLING if active_running else STATUS_CANCELLED
    if not active_running:
        batch.completed_at = now
    progress = batch.progress or {}
    progress.update({"stage": "cancelling" if active_running else "cancelled", "cancel_requested_at": progress_timestamp()})
    batch.progress = progress
    batch.updated_at = now
    db.add(batch)
    db.commit()
    return {"changed": True, "message": "批次已取消；运行中的外部请求晚返回也不会进入可练题池。", "batch_id": batch.id, "status": batch.status}


def retry_task_by_id(db: Session, task_id: str) -> dict[str, Any]:
    kind, _, raw_id = task_id.partition(":")
    target_id = raw_id or task_id
    if kind in {"batch", ""}:
        batch = db.get(GenerationBatch, target_id)
        if batch:
            if batch.status not in RETRYABLE_BATCH_STATUSES:
                return {"changed": False, "message": "只有失败、部分完成或超时批次支持一键重试。"}
            if _batch_origin(batch) == "mastery_variant_batch":
                rows = db.scalars(select(GenerationBatchItem).where(GenerationBatchItem.batch_id == batch.id)).all()
                reset_count = 0
                for row in rows:
                    if row.status == STATUS_ACCEPTED:
                        continue
                    review = dict(row.similarity_review or {})
                    for key in ("lease_owner", "lease_acquired_at", "lease_expires_at", "terminal_reason"):
                        review.pop(key, None)
                    review.update({"retry_requested_at": progress_timestamp(), "retry_source_status": row.status})
                    row.status = STATUS_QUEUED
                    row.job_id = None
                    row.candidate_id = None
                    row.question_id = None
                    row.rejection_reason = None
                    row.duration_ms = None
                    row.similarity_review = review
                    row.updated_at = utc_now()
                    db.add(row)
                    reset_count += 1
                batch.progress = {**(batch.progress or {}), "mastery_variant_retry_reset_count": reset_count}
            batch.status = STATUS_QUEUED
            batch.completed_at = None
            batch.queue_claim_token = None
            batch.queue_claimed_at = None
            progress = batch.progress or {}
            progress.update({"stage": "queued", "retry_requested_at": progress_timestamp()})
            batch.progress = progress
            batch.updated_at = utc_now()
            db.add(batch)
            db.commit()
            return {"changed": True, "message": "批次已重新加入队列。", "batch_id": batch.id}
    raise HTTPException(status_code=404, detail="task_not_retryable")
