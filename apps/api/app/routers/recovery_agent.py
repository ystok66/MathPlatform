from __future__ import annotations
from ..localization import LanguageRequest

import hashlib
import json
from datetime import timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_gateway import model_roles, provider_chain
from ..ai_transactions import run_model_outside_transaction
from ..config import get_settings, project_root
from ..db import get_db, get_engine
from ..health import health_detail
from ..models import AiRunLog, RecoveryAgentRun, utc_now
from ..prompt_security import guarded_json_message, guarded_system_prompt
from ..prompt_loader import prompt_template
from ..localization import current_language, language_context, translate_message
from ..statuses import STATUS_CANCELLED, STATUS_FAILED, STATUS_RUNNING, STATUS_TIMED_OUT
from ..task_center import cancel_task_by_id, retry_task_by_id, schedule_generation_batch_queue, task_center_snapshot

router = APIRouter(prefix="/api/recovery/agent-runs", tags=["maintenance"])

PROMPT_VERSION = "emergency_recovery_agent_v1"
ALLOWED_ACTIONS = {
    "observe_only",
    "recover_stale_state",
    "dispatch_queue",
    "cancel_task",
    "retry_task",
}
TERMINAL_AGENT_STATUSES = {"completed", STATUS_CANCELLED, STATUS_FAILED, STATUS_TIMED_OUT}


class RecoveryAgentRunRequest(LanguageRequest):
    max_loops: int = Field(default=10, ge=1, le=10)
    restart_existing: bool = False
    note: str | None = Field(default=None, max_length=1000)


def _json_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "[truncated]"
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lower = str(key).lower()
            if any(marker in lower for marker in ("api_key", "apikey", "secret", "token", "authorization", "password")):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = _redact(item, depth + 1)
        return redacted
    if isinstance(value, list):
        return [_redact(item, depth + 1) for item in value[:80]]
    if isinstance(value, str) and len(value) > 1200:
        return value[:1200] + "...[truncated]"
    return value


def _snapshot(db: Session, note: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    health = health_detail(db, project_root(), settings)
    tasks = task_center_snapshot(db, limit=60)
    return _redact(
        {
            "note": note,
            "health": health,
            "task_center": tasks,
            "allowed_actions": [
                {
                    "action": "recover_stale_state",
                    "input": {"stale_after_seconds": "integer 60..3600", "force_active_items": "boolean"},
                    "effect": "deterministically marks stale running/cancelling jobs, batches, and interventions as terminal or recoverable",
                },
                {
                    "action": "dispatch_queue",
                    "input": {},
                    "effect": "starts the queued generation-batch dispatcher if it is not already running",
                },
                {
                    "action": "cancel_task",
                    "input": {"task_id": "task center id such as batch:<id>"},
                    "effect": "requests safe cancellation for queued/running tasks",
                },
                {
                    "action": "retry_task",
                    "input": {"task_id": "retryable task center id such as batch:<id>"},
                    "effect": "requeues retryable failed/partial/timed-out batch tasks",
                },
                {
                    "action": "observe_only",
                    "input": {"reason": "string"},
                    "effect": "do nothing except report diagnosis",
                },
            ],
            "hard_safety_rules": [
                "Do not request SQL, file writes, deletion, key edits, or unsafe data mutation.",
                "Do not generate or publish questions; use only the whitelisted recovery actions.",
                "Do not reveal hidden chain-of-thought; summarize diagnosis and evidence briefly.",
            ],
        }
    )


def _normal_decision(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "diagnosis": translate_message("diagnostics.invalid_payload", language=current_language()),
            "actions": [{"action": "observe_only", "reason": "invalid_model_payload"}],
            "stop": True,
            "user_message": translate_message("diagnostics.no_structured_actions", language=current_language()),
        }
    raw_actions = payload.get("actions")
    if raw_actions is None and payload.get("action"):
        raw_actions = [payload]
    if not isinstance(raw_actions, list):
        raw_actions = []
    actions = []
    for raw in raw_actions[:5]:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action") or "").strip()
        if action not in ALLOWED_ACTIONS:
            actions.append({"action": "observe_only", "reason": f"blocked_action:{action or 'empty'}"})
            continue
        actions.append(raw)
    if not actions:
        actions = [{"action": "observe_only", "reason": "no_safe_action"}]
    return {
        "diagnosis": str(payload.get("diagnosis") or payload.get("summary") or translate_message("diagnostics.no_diagnosis", language=current_language()))[:2000],
        "actions": actions,
        "stop": bool(payload.get("stop", True)),
        "user_message": str(payload.get("user_message") or payload.get("next_step") or "")[:2000],
        "unresolved_risks": payload.get("unresolved_risks") if isinstance(payload.get("unresolved_risks"), list) else [],
    }


def _deterministic_decision(snapshot: dict[str, Any]) -> dict[str, Any]:
    tasks = (snapshot.get("task_center") or {}).get("tasks") or []
    queued = [task for task in tasks if task.get("status") == "queued"]
    stale = [
        task
        for task in tasks
        if task.get("status") in {"running", "cancelling", "generating"}
        and int(((task.get("progress") or {}).get("stale_seconds") or 0)) >= 300
    ]
    if stale:
        return {
            "diagnosis": translate_message("diagnostics.stale_found", language=current_language()),
            "actions": [{"action": "recover_stale_state", "stale_after_seconds": 300, "force_active_items": False}],
            "stop": False,
            "user_message": translate_message("diagnostics.recovery_attempted", language=current_language()),
            "unresolved_risks": [],
        }
    if queued and not any(task.get("status") in {"running", "cancelling"} for task in tasks):
        return {
            "diagnosis": translate_message("diagnostics.queue_idle", language=current_language()),
            "actions": [{"action": "dispatch_queue"}],
            "stop": False,
            "user_message": translate_message("diagnostics.dispatch_attempted", language=current_language()),
            "unresolved_risks": [],
        }
    return {
        "diagnosis": translate_message("diagnostics.no_safe_action", language=current_language()),
        "actions": [{"action": "observe_only", "reason": "no_safe_deterministic_action"}],
        "stop": True,
        "user_message": translate_message("diagnostics.check_tasks_restart", language=current_language()),
        "unresolved_risks": [],
    }


def _call_agent(db: Session, snapshot: dict[str, Any], run: RecoveryAgentRun) -> dict[str, Any]:
    with language_context((run.input_snapshot or {}).get("output_language") or (run.input_snapshot or {}).get("language") or current_language()):
        return _call_agent_localized(db, snapshot, run)


def _call_agent_localized(db: Session, snapshot: dict[str, Any], run: RecoveryAgentRun) -> dict[str, Any]:
    settings = get_settings()
    if settings.model_provider == "mock":
        return _deterministic_decision(snapshot)
    model = run.model or getattr(settings, "emergency_recovery_agent_model", None) or "gpt-5.6-sol"
    roles = {**model_roles(settings.default_model, settings), "emergency_recovery_agent": model}
    messages = [
        {
            "role": "system",
            "content": guarded_system_prompt(
                "你是本地小学数学平台的极端恢复 Agent。你只能诊断和选择白名单安全动作，不能写 SQL、不能删数据、不能修改密钥、不能绕过审核发布题目。"
                "每轮先根据任务中心和健康状态判断是否存在可确定修复的问题，再输出一个 JSON 对象。"
                "输出格式：{\"diagnosis\":\"...\",\"actions\":[{\"action\":\"recover_stale_state|dispatch_queue|cancel_task|retry_task|observe_only\"}],\"stop\":true,\"user_message\":\"...\",\"unresolved_risks\":[]}。"
                "不要输出隐藏推理过程，只给简洁诊断、证据和动作。"
            ),
        },
        {
            "role": "user",
            "content": guarded_json_message(
                "emergency_recovery_agent",
                {"snapshot": snapshot, "expected_output": "single JSON object; no markdown"},
                untrusted_fields=["payload.snapshot.task_center.tasks.*.error", "payload.snapshot.note"],
            ),
        },
    ]
    chain = provider_chain(settings.model_provider, settings, db)
    messages[0]["content"] = guarded_system_prompt(prompt_template("emergency_recovery_agent_v1.md"))
    results = run_model_outside_transaction(db, chain, messages, "emergency_recovery_agent", roles, {"snapshot": snapshot})
    last = results[-1] if results else None
    payload = last.payload if last else None
    log = AiRunLog(
        job_id=None,
        role="emergency_recovery_agent",
        provider=last.provider if last else settings.model_provider,
        model=last.model if last else model,
        prompt_version=PROMPT_VERSION,
        input_hash=_json_hash(snapshot),
        request_payload={"snapshot": snapshot},
        response_payload=_redact(payload),
        error=last.error if last else "no_provider_result",
        latency_ms=last.latency_ms if last else None,
        token_usage=last.token_usage if last else {},
    )
    db.add(log)
    db.commit()
    return _normal_decision(payload)


def _execute_action(db: Session, action: dict[str, Any]) -> dict[str, Any]:
    name = str(action.get("action") or "observe_only")
    if name == "observe_only":
        return {"action": name, "ok": True, "message": str(action.get("reason") or "observe_only")}
    if name == "dispatch_queue":
        return {"action": name, "ok": True, "result": schedule_generation_batch_queue(None)}
    if name == "cancel_task":
        task_id = str(action.get("task_id") or "").strip()
        if not task_id:
            return {"action": name, "ok": False, "error": "missing_task_id"}
        return {"action": name, "ok": True, "result": cancel_task_by_id(db, task_id)}
    if name == "retry_task":
        task_id = str(action.get("task_id") or "").strip()
        if not task_id:
            return {"action": name, "ok": False, "error": "missing_task_id"}
        return {"action": name, "ok": True, "result": retry_task_by_id(db, task_id)}
    if name == "recover_stale_state":
        from .recovery import StaleStateRecoveryRequest, recover_stale_state

        request = StaleStateRecoveryRequest(
            stale_after_seconds=max(60, min(int(action.get("stale_after_seconds") or 300), 3600)),
            force_active_items=bool(action.get("force_active_items") or False),
        )
        return {"action": name, "ok": True, "result": recover_stale_state(request, None, db)}
    return {"action": name, "ok": False, "error": "action_not_allowed"}


def _utc_iso(value):
    if value is None:
        return None
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).isoformat()


def _serialize_run(run: RecoveryAgentRun) -> dict[str, Any]:
    snapshot = run.input_snapshot or {}
    steps = run.steps or []
    results = [item for step in steps for item in (step.get("results") or []) if isinstance(item, dict)]
    if not results:
        results = (run.report or {}).get("actions_taken") or []
    action_count = sum(1 for item in results if isinstance(item, dict) and item.get("ok") is True and item.get("action") in ALLOWED_ACTIONS - {"observe_only"})
    output_language = snapshot.get("output_language") or snapshot.get("language")
    diagnosis_status = run.status
    if run.status == "completed":
        diagnosis_status = "completed_actions_unverified" if action_count else "completed_no_repairs"
    return {
        "id": run.id,
        "status": run.status,
        "model": run.model,
        "max_loops": run.max_loops,
        "loop_count": run.loop_count,
        "input_snapshot": run.input_snapshot,
        "steps": run.steps or [],
        "report": run.report,
        "output_language": output_language,
        "diagnosis_status": diagnosis_status,
        "repair_action_count": action_count,
        "snapshot_at": (steps[-1].get("snapshot_at") if steps else None) or (snapshot.get("health") or {}).get("generated_at") or _utc_iso(run.started_at or run.created_at),
        "error": run.error,
        "cancel_requested": run.cancel_requested,
        "created_at": _utc_iso(run.created_at),
        "started_at": _utc_iso(run.started_at),
        "updated_at": _utc_iso(run.updated_at),
        "completed_at": _utc_iso(run.completed_at),
    }


def process_recovery_agent_run_background(run_id: str) -> None:
    with Session(get_engine()) as db:
        run = db.get(RecoveryAgentRun, run_id)
        if not run:
            return
        snapshot = run.input_snapshot or {}
        output_language = snapshot.get("output_language") or snapshot.get("language") or "en"
    with language_context(output_language):
        _process_recovery_agent_run_background(run_id)


def _process_recovery_agent_run_background(run_id: str) -> None:
    with Session(get_engine()) as db:
        run = db.get(RecoveryAgentRun, run_id)
        if not run or run.status in TERMINAL_AGENT_STATUSES:
            return
        run.status = STATUS_RUNNING
        run.started_at = run.started_at or utc_now()
        run.updated_at = utc_now()
        db.add(run)
        db.commit()
    final_report: dict[str, Any] | None = None
    try:
        for loop_index in range(1, 11):
            with Session(get_engine()) as db:
                run = db.get(RecoveryAgentRun, run_id)
                if not run:
                    return
                if run.cancel_requested:
                    run.status = STATUS_CANCELLED
                    run.completed_at = utc_now()
                    run.updated_at = utc_now()
                    db.add(run)
                    db.commit()
                    return
                if loop_index > run.max_loops:
                    break
                snapshot = _snapshot(db, None)
                decision = _call_agent(db, snapshot, run)
                action_results = []
                for action in decision.get("actions") or []:
                    current = db.get(RecoveryAgentRun, run_id)
                    if current and current.cancel_requested:
                        break
                    action_results.append(_execute_action(db, action))
                steps = list(run.steps or [])
                steps.append(
                    {
                        "loop": loop_index,
                        "diagnosis": decision.get("diagnosis"),
                        "actions": decision.get("actions"),
                        "results": action_results,
                        "user_message": decision.get("user_message"),
                        "created_at": utc_now().isoformat(),
                        "snapshot_at": (snapshot.get("health") or {}).get("generated_at"),
                    }
                )
                run.loop_count = loop_index
                run.steps = _redact(steps)
                run.updated_at = utc_now()
                db.add(run)
                db.commit()
                final_report = {
                    "diagnosis": decision.get("diagnosis"),
                    "actions_taken": action_results,
                    "unresolved_risks": decision.get("unresolved_risks") or [],
                    "next_steps": decision.get("user_message") or translate_message("diagnostics.check_tasks", language=current_language()),
                }
                if decision.get("stop", True):
                    break
        with Session(get_engine()) as db:
            run = db.get(RecoveryAgentRun, run_id)
            if not run:
                return
            if run.cancel_requested:
                run.status = STATUS_CANCELLED
            else:
                run.status = "completed"
            run.report = _redact(final_report or {"diagnosis": translate_message("diagnostics.no_actions", language=current_language()), "actions_taken": [], "unresolved_risks": [], "next_steps": translate_message("diagnostics.check_tasks", language=current_language())})
            run.completed_at = utc_now()
            run.updated_at = utc_now()
            db.add(run)
            db.commit()
    except Exception as exc:  # pragma: no cover - background protection
        with Session(get_engine()) as db:
            run = db.get(RecoveryAgentRun, run_id)
            if not run:
                return
            run.status = STATUS_FAILED
            run.error = str(exc)[:2000]
            run.completed_at = utc_now()
            run.updated_at = utc_now()
            db.add(run)
            db.commit()


@router.post("")
def start_recovery_agent_run(
    payload: RecoveryAgentRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    active_limit = max(1, int(getattr(settings, "ai_emergency_agent_max_concurrency", 1) or 1))
    active_runs = db.scalars(
        select(RecoveryAgentRun).where(RecoveryAgentRun.status.in_(["queued", STATUS_RUNNING])).order_by(RecoveryAgentRun.created_at.desc())
    ).all()
    if active_runs and not payload.restart_existing:
        return {"ok": True, "already_running": True, "run": _serialize_run(active_runs[0])}
    if len(active_runs) >= active_limit and payload.restart_existing:
        for run in active_runs:
            run.cancel_requested = True
            run.updated_at = utc_now()
            db.add(run)
    snapshot = _snapshot(db, payload.note)
    snapshot["language"] = payload.output_language or current_language()
    snapshot["output_language"] = snapshot["language"]
    run = RecoveryAgentRun(
        status="queued",
        model=getattr(settings, "emergency_recovery_agent_model", None) or "gpt-5.6-sol",
        max_loops=payload.max_loops,
        input_snapshot=snapshot,
        steps=[],
        report=None,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(process_recovery_agent_run_background, run.id)
    return {"ok": True, "already_running": False, "run": _serialize_run(run)}


@router.get("")
def list_recovery_agent_runs(preferred_language: str | None = None, limit: int = Query(default=50, ge=1, le=100), db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(RecoveryAgentRun).order_by(RecoveryAgentRun.created_at.desc(), RecoveryAgentRun.id.desc()).limit(limit)))
    summaries = []
    for row in rows:
        item = _serialize_run(row)
        item.pop("input_snapshot", None)
        item.pop("steps", None)
        summaries.append(item)
    if preferred_language:
        summaries.sort(key=lambda item: item.get("output_language") != preferred_language)
    return {"ok": True, "runs": summaries}


@router.get("/{run_id}")
def get_recovery_agent_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.get(RecoveryAgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    return {"ok": True, "run": _serialize_run(run)}


@router.post("/{run_id}/cancel")
def cancel_recovery_agent_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.get(RecoveryAgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    if run.status in TERMINAL_AGENT_STATUSES:
        return {"ok": True, "changed": False, "run": _serialize_run(run)}
    run.cancel_requested = True
    if run.status == "queued":
        run.status = STATUS_CANCELLED
        run.completed_at = utc_now()
    run.updated_at = utc_now()
    db.add(run)
    db.commit()
    db.refresh(run)
    return {"ok": True, "changed": True, "run": _serialize_run(run)}
