from ..localization import LanguageRequest
import json
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..ai_gateway import GENERATED_SOURCE_NAME, INACTIVE_PUBLISH_STATUSES, existing_generation_duplicate, generator_model_for_request, model_roles, process_generation_job
from ..ai_providers import estimated_cost_usage, parse_model_price_table
from ..audit import log_audit_event
from ..config import get_settings
from ..db import get_db, get_engine
from ..display_names import batch_display_name
from ..generation_events import merge_sse_events, persist_generation_event, sse_event_id, sse_event_key, sse_latest_time, sse_should_emit, stored_generation_events
from ..generation_planning import plan_generation_request
from ..generation_review import candidate_set_review, diff_payload
from ..importer import map_question_v2
from ..learner_profiles import resolve_learner_id
from ..localization import current_language, normalize_language, translate_message
from ..models import QuestionVersion
from ..models import AiRunLog, AnswerEvent, AnswerSpec, GeneratedCandidate, GeneratedCandidateReview, GenerationBatch, GenerationBatchItem, GenerationDurationStat, GenerationJob, PracticeSession, Question, QuestionTag, Submission, TaxonomyTag, utc_now
from ..schemas import CreatePracticeSessionResponse, GenerationBatchPracticeRequest, GenerationBatchRequest, GenerationBatchResponse, GenerationJobRequest, GenerationJobResponse
from ..session_idempotency import ordered_unique_question_ids, practice_session_response
from ..semantic_similarity import similarity_report
from ..statuses import (
    ACTIVE_TASK_STATUSES,
    CANCEL_REQUESTED_BATCH_STATUSES,
    FAILED_GENERATION_STATUSES,
    STATUS_ACCEPTED,
    STATUS_CANCELLED,
    STATUS_CANCELLING,
    STATUS_COMPLETED,
    STATUS_DOMAIN_GENERATION_BATCH,
    STATUS_DOMAIN_GENERATION_ITEM,
    STATUS_DOMAIN_GENERATION_JOB,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_QUEUED,
    STATUS_REJECTED,
    STATUS_RUNNING,
    STATUS_TIMED_OUT,
    TERMINAL_BATCH_STATUSES,
    TERMINAL_JOB_STATUSES,
    assert_status_transition,
)
from ..task_center import schedule_generation_batch_queue

router = APIRouter(prefix="/api/generation/jobs", tags=["generation"])
batch_router = APIRouter(prefix="/api/generation/batches", tags=["generation"])
REUSE_AI_SELECTION_LOCK = Lock()

TERMINAL_ITEM_STATUSES = {STATUS_ACCEPTED, STATUS_REJECTED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMED_OUT}
ACTIVE_BATCH_START_BLOCKING_STATUSES = {STATUS_QUEUED, STATUS_RUNNING, STATUS_CANCELLING}


def generation_job_response(job: GenerationJob) -> GenerationJobResponse:
    return GenerationJobResponse(
        id=job.id,
        status=job.status,
        accepted_question_id=job.accepted_question_id,
        progress=job.progress or {},
    )


def active_generation_batch_summaries(db: Session, *, exclude_batch_id: str | None = None, limit: int = 5) -> list[dict]:
    query = (
        select(GenerationBatch)
        .where(GenerationBatch.status.in_(ACTIVE_BATCH_START_BLOCKING_STATUSES))
        .order_by(GenerationBatch.updated_at.desc(), GenerationBatch.created_at.desc())
        .limit(max(1, int(limit or 5)))
    )
    rows: list[dict] = []
    for row in db.scalars(query):
        if exclude_batch_id and row.id == exclude_batch_id:
            continue
        request = row.request if isinstance(row.request, dict) else {}
        progress = row.progress if isinstance(row.progress, dict) else {}
        rows.append(
            {
                "id": row.id,
                "status": row.status,
                "origin": request.get("origin") or progress.get("origin") or "generation_batch",
                "accepted_count": row.accepted_count,
                "target_count": row.target_count,
                "concurrency": row.concurrency,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    return rows


def enforce_parallel_batch_policy(db: Session, *, allow_parallel_batches: bool, exclude_batch_id: str | None = None) -> None:
    return


class CandidateReviewActionRequest(BaseModel):
    action: Literal["edit", "approve", "reject"]
    edited_payload: dict | None = None
    reviewer_notes: str | None = None


class RetryGenerationJobRequest(LanguageRequest):
    run_now: bool = True
    idempotency_key: str | None = None
    allow_budget_overrun: bool = False
    allow_reviewer_quality_downgrade: bool = False


class GenerationBatchTopUpRequest(LanguageRequest):
    run_now: bool = True
    allow_budget_overrun: bool = False
    allow_reviewer_quality_downgrade: bool = False
    allow_parallel_batches: bool = False


class DuplicateCheckRequest(BaseModel):
    stem_md: str
    question_id: str | None = None


ITEM_LEASE_SECONDS = 900
MAX_REGENERATION_ATTEMPTS = 3
REGENERATION_CONSTRAINT_MAP = {
    "pairwise_similarity_high": ("near_duplicate", "Change at least two design axes: task form, representation, constraint, story domain, or answer form."),
    "duplicate_against_existing_question": ("near_duplicate", "Do not reuse the same structure as existing bank or AI questions."),
    "source_similarity_risk_high": ("near_duplicate", "Avoid cloned structure, number-only changes, and background-only rewrites."),
    "final_answer_grades_correct": ("answer_spec_inconsistent", "Recompute the final answer and make answer_spec canonical forms grade it as correct."),
    "verification_expected_matches": ("answer_spec_inconsistent", "Align verification expected value, final answer, and canonical answer."),
    "reviewer_answer_spec_conflict": ("answer_spec_inconsistent", "Recompute independently and align the reviewer answer, solution final answer, and answer_spec canonical forms."),
    "has_canonical_answer": ("answer_not_unique", "Provide a unique or explicitly enumerated canonical answer."),
    "unique_answer_self_check": ("answer_not_unique", "Strengthen constraints so the answer is unique or clearly listed."),
    "student_solution_style_valid": ("solution_style_invalid", "Use the requested elementary solution style or justify any equation use."),
    "equation_usage_style_warning": ("solution_style_invalid", "Prefer a natural non-equation solution when requested."),
    "difficulty_star_matches_total": ("difficulty_mismatch", "Re-score difficulty using the rubric and keep the task at the requested star."),
    "difficulty_total_matches_dimensions": ("difficulty_mismatch", "Make dimension scores and total score internally consistent."),
    "reviewer_independence_required": ("reviewer_independence_required", "Use a distinct reviewer model for strict mode before publishing."),
    "secondary_reviewer_not_distinct": ("reviewer_independence_required", "Configure a distinct secondary reviewer model for strict mode before publishing."),
    "dual_reviewer_decision_conflict": ("reviewer_failed", "Retry only after resolving reviewer disagreement; do not publish candidates with conflicting reviewer decisions."),
    "dual_reviewer_answer_conflict": ("answer_spec_inconsistent", "Recompute the answer and ensure both reviewers independently reach the same canonical answer."),
    "secondary_reviewer_answer_spec_conflict": ("answer_spec_inconsistent", "Align the secondary reviewer independent answer with answer_spec canonical forms."),
    "secondary_reviewer_failed": ("reviewer_failed", "Retry only after secondary reviewer provider is available; do not publish partially reviewed strict candidates."),
    "model_reviewer_failed": ("reviewer_failed", "Retry only after reviewer provider is available; do not publish unreviewed candidates."),
}
def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item not in {None, ""}]
    if isinstance(value, tuple):
        return [str(item) for item in value if item not in {None, ""}]
    if value is None or value == "":
        return []
    return [str(value)]


def retry_root_id(parent: GenerationJob) -> str:
    return str((parent.request or {}).get("retry_root_id") or (parent.progress or {}).get("retry_root_id") or parent.id)


def next_regeneration_attempt(db: Session, root_id: str) -> int:
    attempts = [0]
    for row in db.scalars(select(GenerationJob)):
        request = row.request or {}
        progress = row.progress or {}
        if row.id == root_id or request.get("retry_root_id") == root_id or progress.get("retry_root_id") == root_id:
            try:
                attempts.append(int(request.get("retry_attempt") or progress.get("regeneration_attempt") or 0))
            except (TypeError, ValueError):
                attempts.append(0)
    return max(attempts) + 1


def collect_rejection_codes(db: Session, parent: GenerationJob) -> list[str]:
    codes: set[str] = set()
    progress = parent.progress or {}
    codes.update(_as_list(progress.get("terminal_reason")))
    set_review = progress.get("set_review") if isinstance(progress.get("set_review"), dict) else {}
    for slot in (set_review or {}).get("regeneration_slots", []) or []:
        if isinstance(slot, dict):
            codes.update(_as_list(slot.get("reason")))
    rows = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == parent.id)))
    for row in rows:
        review = row.review or {}
        payload = row.payload or {}
        codes.update(_as_list(review.get("issues")))
        codes.update(_as_list(review.get("not_selected_reason")))
        codes.update(_as_list(review.get("reject_reason")))
        if isinstance(payload, dict):
            codes.update(_as_list(payload.get("errors")))
            codes.update(_as_list(payload.get("reject_reason")))
        duplicate = review.get("duplicate")
        if isinstance(duplicate, dict):
            codes.update(_as_list(duplicate.get("reason")))
            codes.update(_as_list(duplicate.get("source_similarity_risk")))
    return sorted(code for code in codes if code)


def regeneration_constraints_for_codes(codes: list[str]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for code in codes:
        mapped_code, instruction = REGENERATION_CONSTRAINT_MAP.get(code, ("general_rework", "Change the design deliberately and explain which axis changed."))
        row = grouped.setdefault(mapped_code, {"code": mapped_code, "source_codes": [], "instruction": instruction})
        row["source_codes"].append(code)
    if not grouped:
        grouped["general_rework"] = {
            "code": "general_rework",
            "source_codes": [],
            "instruction": "Regenerate with a clearly different design and keep schema, answer_spec, and verification consistent.",
        }
    return [{"code": row["code"], "source_codes": sorted(set(row["source_codes"])), "instruction": row["instruction"]} for row in grouped.values()]


def build_regeneration_retry(parent: GenerationJob, db: Session) -> tuple[dict, dict]:
    root_id = retry_root_id(parent)
    attempt = next_regeneration_attempt(db, root_id)
    codes = collect_rejection_codes(db, parent)
    constraints = regeneration_constraints_for_codes(codes)
    request = dict(parent.request or {})
    request.update(
        {
            "retry_root_id": root_id,
            "retry_of": parent.id,
            "retry_attempt": attempt,
            "max_retry_attempts": MAX_REGENERATION_ATTEMPTS,
            "regeneration_mode": "targeted_retry",
            "regeneration_reason_codes": codes,
            "regeneration_constraints": constraints,
            "prefer_reuse_existing_ai": False,
        }
    )
    progress = {
        "stage": STATUS_QUEUED,
        "retry_of": parent.id,
        "retry_root_id": root_id,
        "regeneration_attempt": attempt,
        "max_regeneration_attempts": MAX_REGENERATION_ATTEMPTS,
        "regeneration_reason_codes": codes,
        "regeneration_constraints": constraints,
    }
    return request, progress


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


def _limit_exceeded(value: int, limit: int) -> bool:
    return bool(limit and value > limit)


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def strict_generation_request(request: dict) -> bool:
    return (
        _bounded_int(request.get("candidate_count"), 3, 1, 5) >= 5
        or _bounded_int(request.get("source_example_count"), 5, 0, 8) >= 8
        or _bounded_int(request.get("ai_negative_example_count"), 3, 0, 5) >= 5
    )


def reviewer_call_multiplier(request: dict) -> int:
    return 2 if strict_generation_request(request) else 1


def generation_call_budget_snapshot(request: dict, target_count: int, scope: str, settings) -> dict:
    candidate_total = max(1, min(5, _positive_int(request.get("candidate_count"), 3)))
    reviewer_multiplier = reviewer_call_multiplier(request)
    target_total = _positive_int(target_count, 1)
    max_items = target_total if scope == "job" else max(target_total, target_total * 4)
    generation_calls = max_items * candidate_total
    primary_reviewer_calls = max_items * candidate_total
    secondary_reviewer_calls = primary_reviewer_calls if reviewer_multiplier > 1 else 0
    reviewer_calls = primary_reviewer_calls + secondary_reviewer_calls
    generation_limit = int(
        getattr(settings, "ai_max_generation_calls_per_job" if scope == "job" else "ai_max_generation_calls_per_batch", 0) or 0
    )
    reviewer_limit = int(getattr(settings, "ai_max_reviewer_calls_per_job" if scope == "job" else "ai_max_reviewer_calls_per_batch", 0) or 0)
    exceeded = []
    if _limit_exceeded(generation_calls, generation_limit):
        exceeded.append("generation_calls")
    if _limit_exceeded(reviewer_calls, reviewer_limit):
        exceeded.append("reviewer_calls")
    return {
        "schema_version": "generation_call_budget_v1",
        "scope": scope,
        "target_count": target_total,
        "max_items": max_items,
        "candidate_count": candidate_total,
        "dual_reviewer_required": reviewer_multiplier > 1,
        "reviewer_call_multiplier": reviewer_multiplier,
        "planned_generation_calls": generation_calls,
        "planned_primary_reviewer_calls": primary_reviewer_calls,
        "planned_secondary_reviewer_calls": secondary_reviewer_calls,
        "planned_reviewer_calls": reviewer_calls,
        "planned_total_calls": generation_calls + reviewer_calls,
        "limits": {
            "generation_calls": generation_limit or None,
            "reviewer_calls": reviewer_limit or None,
        },
        "ok": not exceeded,
        "exceeded": exceeded,
        "note": "batch max_items includes up to three replacement attempts per target item" if scope == "batch" else "single job call budget",
    }


def generation_top_up_call_budget_snapshot(request: dict, batch: GenerationBatch, planned_new_items: int, existing_item_count: int, settings) -> dict:
    candidate_total = max(1, min(5, _positive_int(request.get("candidate_count"), 3)))
    reviewer_multiplier = reviewer_call_multiplier(request)
    planned_new_items = max(0, int(planned_new_items or 0))
    existing_item_count = max(0, int(existing_item_count or 0))
    generation_calls = planned_new_items * candidate_total
    primary_reviewer_calls = planned_new_items * candidate_total
    secondary_reviewer_calls = primary_reviewer_calls if reviewer_multiplier > 1 else 0
    reviewer_calls = primary_reviewer_calls + secondary_reviewer_calls
    cumulative_generation_calls = (existing_item_count + planned_new_items) * candidate_total
    cumulative_primary_reviewer_calls = (existing_item_count + planned_new_items) * candidate_total
    cumulative_secondary_reviewer_calls = cumulative_primary_reviewer_calls if reviewer_multiplier > 1 else 0
    cumulative_reviewer_calls = cumulative_primary_reviewer_calls + cumulative_secondary_reviewer_calls
    generation_limit = int(getattr(settings, "ai_max_generation_calls_per_batch", 0) or 0)
    reviewer_limit = int(getattr(settings, "ai_max_reviewer_calls_per_batch", 0) or 0)
    exceeded = []
    if _limit_exceeded(cumulative_generation_calls, generation_limit):
        exceeded.append("generation_calls")
    if _limit_exceeded(cumulative_reviewer_calls, reviewer_limit):
        exceeded.append("reviewer_calls")
    return {
        "schema_version": "generation_call_budget_v1",
        "scope": "batch_top_up",
        "batch_id": batch.id,
        "target_count": batch.target_count,
        "existing_item_count": existing_item_count,
        "planned_new_items": planned_new_items,
        "candidate_count": candidate_total,
        "dual_reviewer_required": reviewer_multiplier > 1,
        "reviewer_call_multiplier": reviewer_multiplier,
        "planned_generation_calls": generation_calls,
        "planned_primary_reviewer_calls": primary_reviewer_calls,
        "planned_secondary_reviewer_calls": secondary_reviewer_calls,
        "planned_reviewer_calls": reviewer_calls,
        "planned_total_calls": generation_calls + reviewer_calls,
        "cumulative_generation_calls": cumulative_generation_calls,
        "cumulative_primary_reviewer_calls": cumulative_primary_reviewer_calls,
        "cumulative_secondary_reviewer_calls": cumulative_secondary_reviewer_calls,
        "cumulative_reviewer_calls": cumulative_reviewer_calls,
        "limits": {
            "generation_calls": generation_limit or None,
            "reviewer_calls": reviewer_limit or None,
        },
        "ok": not exceeded,
        "exceeded": exceeded,
        "note": "top-up only budgets newly needed failed/missing batch slots; cumulative calls stay under batch limits",
    }


def enforce_generation_call_budget(snapshot: dict) -> None:
    if snapshot.get("ok"):
        return
    raise HTTPException(
        status_code=429,
        detail={
            "error_code": "generation_call_budget_exceeded",
            "user_message": "AI 出题调用规模超过当前本地预算上限，请减少题数/候选数，或在环境配置中提高调用上限。",
            "retryable": True,
            "details_safe": snapshot,
        },
    )


def ai_usage_costs_for_budget(db: Session) -> dict:
    logs = list(db.scalars(select(AiRunLog)))
    total_cost = 0.0
    today_cost = 0.0
    total_calls = 0
    today_calls = 0
    today_start = datetime.now(UTC).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    for row in logs:
        usage = row.token_usage or {}
        cost = float(usage.get("estimated_cost_usd") or 0)
        total_cost += cost
        total_calls += 1
        if row.created_at >= today_start:
            today_cost += cost
            today_calls += 1
    return {
        "today_start_utc": today_start.isoformat(),
        "total_calls": total_calls,
        "today_calls": today_calls,
        "total_estimated_cost_usd": round(total_cost, 6),
        "today_estimated_cost_usd": round(today_cost, 6),
    }


def planned_ai_cost_snapshot(settings, provider: str, call_budget: dict | None = None, request: dict | None = None) -> dict:
    if provider == "mock" or (call_budget or {}).get("actual_provider_calls_planned") == 0:
        return {
            "schema_version": "planned_ai_cost_v1",
            "provider": provider,
            "estimated_cost_usd": 0.0,
            "generation_estimated_cost_usd": 0.0,
            "reviewer_estimated_cost_usd": 0.0,
            "planned_generation_calls": 0,
            "planned_reviewer_calls": 0,
            "pricing_sources": [],
            "note": "no provider cost planned",
        }
    roles = model_roles(settings.default_model, settings)
    generator_model, generator_model_source = generator_model_for_request(settings, request or {}, roles)
    reviewer_model = roles.get("reviewer") or roles.get("default") or settings.default_model
    secondary_reviewer_model = roles.get("secondary_reviewer") or roles.get("set_reviewer") or reviewer_model
    price_table = parse_model_price_table(getattr(settings, "ai_model_price_json", ""))
    generation_calls = int((call_budget or {}).get("planned_generation_calls") or 0)
    reviewer_calls = int((call_budget or {}).get("planned_reviewer_calls") or 0)
    primary_reviewer_calls = int((call_budget or {}).get("planned_primary_reviewer_calls") or reviewer_calls)
    secondary_reviewer_calls = int((call_budget or {}).get("planned_secondary_reviewer_calls") or 0)
    if secondary_reviewer_calls > reviewer_calls:
        secondary_reviewer_calls = reviewer_calls
    if primary_reviewer_calls + secondary_reviewer_calls != reviewer_calls:
        primary_reviewer_calls = max(0, reviewer_calls - secondary_reviewer_calls)
    generator_usage = {
        "prompt_tokens": int(getattr(settings, "ai_estimated_generator_input_tokens_per_call", 2500) or 2500),
        "completion_tokens": int(getattr(settings, "ai_estimated_generator_output_tokens_per_call", 1200) or 1200),
    }
    reviewer_usage = {
        "prompt_tokens": int(getattr(settings, "ai_estimated_reviewer_input_tokens_per_call", 2000) or 2000),
        "completion_tokens": int(getattr(settings, "ai_estimated_reviewer_output_tokens_per_call", 800) or 800),
    }
    generator_cost = estimated_cost_usage(generator_model, generator_usage, provider, price_table)
    reviewer_cost = estimated_cost_usage(reviewer_model, reviewer_usage, provider, price_table)
    secondary_reviewer_cost = estimated_cost_usage(secondary_reviewer_model, reviewer_usage, provider, price_table)
    generation_total = round(generation_calls * float(generator_cost["estimated_cost_usd"]), 6)
    reviewer_total = round(primary_reviewer_calls * float(reviewer_cost["estimated_cost_usd"]) + secondary_reviewer_calls * float(secondary_reviewer_cost["estimated_cost_usd"]), 6)
    return {
        "schema_version": "planned_ai_cost_v1",
        "provider": provider,
        "estimated_cost_usd": round(generation_total + reviewer_total, 6),
        "generation_estimated_cost_usd": generation_total,
        "reviewer_estimated_cost_usd": reviewer_total,
        "planned_generation_calls": generation_calls,
        "planned_reviewer_calls": reviewer_calls,
        "planned_primary_reviewer_calls": primary_reviewer_calls,
        "planned_secondary_reviewer_calls": secondary_reviewer_calls,
        "generator_model": generator_model,
        "generator_model_source": generator_model_source,
        "reviewer_model": reviewer_model,
        "secondary_reviewer_model": secondary_reviewer_model,
        "estimated_tokens_per_call": {
            "generator": generator_usage,
            "reviewer": reviewer_usage,
        },
        "pricing_sources": [
            {"role": "generator", "model": generator_model, "source": generator_cost["pricing_source"], "pricing": generator_cost["pricing"]},
            {"role": "reviewer", "model": reviewer_model, "source": reviewer_cost["pricing_source"], "pricing": reviewer_cost["pricing"]},
            {"role": "secondary_reviewer", "model": secondary_reviewer_model, "source": secondary_reviewer_cost["pricing_source"], "pricing": secondary_reviewer_cost["pricing"]},
        ],
        "note": "planned cost is a local estimate based on configured per-1k-token prices and default token budgets",
    }


def ai_cost_budget_snapshot(db: Session, settings, provider: str, call_budget: dict | None = None, request: dict | None = None) -> dict:
    monthly_budget = float(getattr(settings, "ai_monthly_budget_usd", 0.0) or 0.0)
    daily_budget = float(getattr(settings, "ai_daily_budget_usd", 0.0) or 0.0)
    usage = ai_usage_costs_for_budget(db)
    planned = planned_ai_cost_snapshot(settings, provider, call_budget, request)
    planned_cost = float(planned.get("estimated_cost_usd") or 0)
    exceeded = []
    if provider != "mock" and daily_budget and usage["today_estimated_cost_usd"] >= daily_budget:
        exceeded.append("daily_budget_usd")
    if provider != "mock" and monthly_budget and usage["total_estimated_cost_usd"] >= monthly_budget:
        exceeded.append("monthly_budget_usd")
    if provider != "mock" and daily_budget and usage["today_estimated_cost_usd"] + planned_cost > daily_budget:
        exceeded.append("daily_budget_usd_planned")
    if provider != "mock" and monthly_budget and usage["total_estimated_cost_usd"] + planned_cost > monthly_budget:
        exceeded.append("monthly_budget_usd_planned")
    return {
        "schema_version": "ai_cost_budget_v1",
        "provider": provider,
        "enforced": provider != "mock",
        "overrun_confirmed": bool((request or {}).get("allow_budget_overrun")),
        "daily_budget_usd": daily_budget or None,
        "monthly_budget_usd": monthly_budget or None,
        "today_estimated_cost_usd": usage["today_estimated_cost_usd"],
        "total_estimated_cost_usd": usage["total_estimated_cost_usd"],
        "planned_estimated_cost_usd": planned_cost,
        "today_projected_cost_usd": round(usage["today_estimated_cost_usd"] + planned_cost, 6),
        "monthly_projected_cost_usd": round(usage["total_estimated_cost_usd"] + planned_cost, 6),
        "today_remaining_budget_usd": round(max(0.0, daily_budget - usage["today_estimated_cost_usd"]), 6) if daily_budget else None,
        "monthly_remaining_budget_usd": round(max(0.0, monthly_budget - usage["total_estimated_cost_usd"]), 6) if monthly_budget else None,
        "today_calls": usage["today_calls"],
        "total_calls": usage["total_calls"],
        "today_start_utc": usage["today_start_utc"],
        "planned_total_calls": (call_budget or {}).get("planned_total_calls"),
        "planned_cost": planned,
        "ok": not exceeded,
        "exceeded": exceeded,
        "requires_confirmation": bool(exceeded),
        "note": "mock provider is not blocked by cost budget" if provider == "mock" else "cost budget uses logged usage plus planned local cost estimate before the new task starts",
    }


def enforce_ai_cost_budget(snapshot: dict, *, allow_overrun: bool = False) -> None:
    if snapshot.get("ok"):
        return
    if allow_overrun:
        snapshot["overrun_confirmed"] = True
        snapshot["blocking"] = False
        return
    snapshot["blocking"] = True
    raise HTTPException(
        status_code=429,
        detail={
            "error_code": "ai_cost_budget_exceeded",
            "user_message": "AI 费用预算已超出，未创建新的真实 provider 出题任务。请降低候选数/题数、改用 mock、提高预算，或勾选“确认超预算继续”。",
            "action": "进入管理页查看 AI 用量与预算，调整 AI_DAILY_BUDGET_USD / AI_MONTHLY_BUDGET_USD 后重试；如确认继续，本次请求会在审计和预算快照中记录 overrun_confirmed=true。",
            "retryable": True,
            "details_safe": snapshot,
        },
    )


def sse_format(event: str, data: dict, event_id: str | None = None) -> str:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


def progress_time(value: str | None):
    if not value:
        return None
    try:
        return utc_now().fromisoformat(value.rstrip("Z"))
    except ValueError:
        return None


def progress_timestamp(value=None) -> str:
    current = value or utc_now()
    return current.isoformat(timespec="seconds") + "Z"


def json_state_version(payload: dict | None) -> int:
    if not isinstance(payload, dict):
        return 0
    try:
        return int(payload.get("state_version") or 0)
    except (TypeError, ValueError):
        return 0


def lease_active(payload: dict | None, owner: str | None = None) -> bool:
    if not isinstance(payload, dict):
        return False
    lease_owner = payload.get("lease_owner")
    if not lease_owner or (owner and lease_owner == owner):
        return False
    expires_at = progress_time(payload.get("lease_expires_at"))
    return expires_at is not None and utc_now() < expires_at


def set_generation_job_status(job: GenerationJob, status: str) -> None:
    assert_status_transition(STATUS_DOMAIN_GENERATION_JOB, job.status, status)
    job.status = status


def set_generation_batch_status(batch: GenerationBatch, status: str) -> None:
    assert_status_transition(STATUS_DOMAIN_GENERATION_BATCH, batch.status, status)
    batch.status = status


def set_generation_item_status(item: GenerationBatchItem, status: str) -> None:
    assert_status_transition(STATUS_DOMAIN_GENERATION_ITEM, item.status, status)
    item.status = status


def persist_job_sse_event(db: Session, job: GenerationJob) -> None:
    progress = job.progress if isinstance(job.progress, dict) else {}
    state_index = json_state_version(progress)
    persist_generation_event(
        db,
        "job",
        job.id,
        "job",
        {"id": job.id, "status": job.status, "progress": job.progress, "accepted_question_id": job.accepted_question_id},
        group=40,
        status=job.status,
        index=state_index,
        occurred_at=job.updated_at or job.created_at,
    )
    if job.status in TERMINAL_JOB_STATUSES:
        persist_generation_event(
            db,
            "job",
            job.id,
            "done",
            {"job_id": job.id, "status": job.status},
            group=90,
            status=job.status,
            index=state_index,
            occurred_at=job.updated_at or job.created_at,
        )


def claim_batch_item_lease(db: Session, item: GenerationBatchItem, owner: str) -> bool:
    db.refresh(item)
    review = {**(item.similarity_review or {})}
    if item.status not in ACTIVE_TASK_STATUSES:
        return False
    if item.status == STATUS_RUNNING and lease_active(review, owner):
        review["lease_conflict_at"] = progress_timestamp()
        review["lease_conflict_owner"] = owner
        item.similarity_review = review
        db.commit()
        return False
    now = utc_now()
    set_generation_item_status(item, STATUS_RUNNING)
    item.updated_at = now
    item.similarity_review = {
        **review,
        "lease_owner": owner,
        "lease_acquired_at": progress_timestamp(now),
        "lease_expires_at": progress_timestamp(now + timedelta(seconds=ITEM_LEASE_SECONDS)),
        "state_version": json_state_version(review) + 1,
    }
    persist_batch_item_sse_event(db, item)
    db.commit()
    return True


def release_batch_item_lease(item: GenerationBatchItem, **extra: object) -> None:
    review = {**(item.similarity_review or {})}
    review["state_version"] = json_state_version(review) + 1
    review["lease_released_at"] = progress_timestamp()
    review.update(extra)
    item.similarity_review = review


def mark_stale_job_if_needed(db: Session, job: GenerationJob) -> bool:
    if job.status != STATUS_RUNNING:
        return False
    deadline = progress_time((job.progress or {}).get("deadline_at"))
    if deadline is None or utc_now() < deadline:
        return False
    set_generation_job_status(job, STATUS_TIMED_OUT)
    job.progress = {
        **(job.progress or {}),
        "stage": STATUS_TIMED_OUT,
        "terminal_reason": "job_deadline_exceeded",
        "last_heartbeat_at": utc_now().isoformat(timespec="seconds") + "Z",
    }
    job.updated_at = utc_now()
    db.flush()
    persist_job_sse_event(db, job)
    return True


def mark_stale_batch_item_lease_if_needed(db: Session, row: GenerationBatchItem) -> bool:
    if row.status != STATUS_RUNNING:
        return False
    review = row.similarity_review if isinstance(row.similarity_review, dict) else {}
    lease_deadline = progress_time(review.get("lease_expires_at"))
    if lease_deadline is None or utc_now() < lease_deadline:
        return False
    reason = "item_lease_expired"
    job = db.get(GenerationJob, row.job_id) if row.job_id else None
    if job is not None and job.status in ACTIVE_TASK_STATUSES:
        set_generation_job_status(job, STATUS_TIMED_OUT)
        job.progress = {
            **(job.progress or {}),
            "stage": STATUS_TIMED_OUT,
            "terminal_reason": reason,
            "batch_item_id": row.id,
            "lease_expires_at": review.get("lease_expires_at"),
            "last_heartbeat_at": progress_timestamp(),
        }
        job.updated_at = utc_now()
        quarantine_late_published_question(db, job.accepted_question_id, reason)
        persist_job_sse_event(db, job)
    set_generation_item_status(row, STATUS_TIMED_OUT)
    row.rejection_reason = reason
    row.updated_at = utc_now()
    release_batch_item_lease(
        row,
        source_type=batch_item_source_type(row),
        terminal_reason=reason,
        lease_timed_out_at=progress_timestamp(),
    )
    persist_batch_item_sse_event(db, row)
    return True


def quarantine_late_published_question(db: Session, question_id: str | None, reason: str) -> None:
    if not question_id:
        return
    question = db.get(Question, question_id)
    if question is None:
        return
    question.publish_status = "batch_rejected_cancelled" if reason == "batch_cancelled" else "quarantined"
    question.eligible_for_practice = False
    question.eligible_for_test = False


def mark_stale_batch_items(db: Session, batch: GenerationBatch) -> None:
    changed = False
    for row in batch_items(db, batch.id):
        if row.status not in ACTIVE_TASK_STATUSES:
            continue
        if mark_stale_batch_item_lease_if_needed(db, row):
            changed = True
            continue
        if not row.job_id:
            continue
        job = db.get(GenerationJob, row.job_id)
        if job is None:
            continue
        if mark_stale_job_if_needed(db, job):
            set_generation_item_status(row, STATUS_FAILED)
            row.rejection_reason = "job_timed_out"
            row.updated_at = utc_now()
            quarantine_late_published_question(db, job.accepted_question_id, "job_timed_out")
            persist_batch_item_sse_event(db, row)
            changed = True
    if changed:
        refresh_batch_counts(db, batch)
        db.commit()


def batch_items(db: Session, batch_id: str) -> list[GenerationBatchItem]:
    return list(db.scalars(select(GenerationBatchItem).where(GenerationBatchItem.batch_id == batch_id).order_by(GenerationBatchItem.item_index, GenerationBatchItem.id)))


def batch_item_terminal_reason(row: GenerationBatchItem) -> str | None:
    if row.status not in {STATUS_REJECTED, STATUS_FAILED, STATUS_CANCELLED}:
        return None
    review = row.similarity_review if isinstance(row.similarity_review, dict) else {}
    reason = row.rejection_reason or review.get("terminal_reason") or review.get("reuse_status") or row.status
    return str(reason or row.status)


def batch_failure_reason_counts(rows: list[GenerationBatchItem]) -> dict[str, int]:
    counts = Counter(reason for row in rows if (reason := batch_item_terminal_reason(row)))
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def history_average_duration_ms(db: Session) -> int | None:
    stats = history_duration_stats(db)
    return stats["average_ms"]


def history_duration_stats(db: Session) -> dict:
    rows = list(db.scalars(select(GenerationDurationStat.duration_ms).where(GenerationDurationStat.duration_ms > 0).order_by(GenerationDurationStat.created_at.desc()).limit(30)))
    if not rows:
        return {"average_ms": None, "sample_count": 0}
    return {"average_ms": int(sum(rows) / len(rows)), "sample_count": len(rows)}


def eta_confidence_level(source: str, sample_count: int, remaining: int) -> str:
    if remaining <= 0:
        return "high"
    if source == "current_batch":
        if sample_count >= 3:
            return "high"
        if sample_count >= 1:
            return "medium"
    if source == "history":
        if sample_count >= 10:
            return "medium"
        if sample_count >= 1:
            return "low"
    return "low"


def eta_range_seconds(eta_seconds: int, confidence: str) -> list[int]:
    if eta_seconds <= 0:
        return [0, 0]
    if confidence == "high":
        return [max(0, int(eta_seconds * 0.8)), int(eta_seconds * 1.25)]
    if confidence == "medium":
        return [max(0, int(eta_seconds * 0.6)), int(eta_seconds * 1.6)]
    return [max(0, int(eta_seconds * 0.4)), int(eta_seconds * 2.2)]


def batch_eta_snapshot(db: Session, target_count: int, accepted_count: int, concurrency: int, measured_durations_ms: list[int] | None = None) -> dict:
    measured = [value for value in (measured_durations_ms or []) if value > 0]
    history_stats = history_duration_stats(db)
    source = "fallback_default"
    sample_count = 0
    average_ms = 15000
    if history_stats["average_ms"]:
        source = "history"
        sample_count = int(history_stats["sample_count"])
        average_ms = int(history_stats["average_ms"])
    if measured:
        source = "current_batch"
        sample_count = len(measured)
        average_ms = int(sum(measured) / len(measured))
    remaining = max(0, int(target_count or 0) - int(accepted_count or 0))
    eta_seconds = 0 if remaining == 0 else int((remaining * average_ms) / max(1, int(concurrency or 1)) / 1000)
    confidence = eta_confidence_level(source, sample_count, remaining)
    return {
        "eta_seconds": eta_seconds,
        "eta_source": source,
        "eta_sample_count": sample_count,
        "eta_average_ms": average_ms,
        "eta_confidence": confidence,
        "eta_range_seconds": eta_range_seconds(eta_seconds, confidence),
    }


def target_tag_set(request: dict) -> set[str]:
    return {str(tag).lower() for tag in (request.get("target_tags") or [])}


def tags_by_question(db: Session, question_ids: list[str]) -> dict[str, set[str]]:
    if not question_ids:
        return {}
    rows = db.execute(
        select(QuestionTag.question_id, QuestionTag.tag_id, TaxonomyTag.label)
        .join(TaxonomyTag, TaxonomyTag.id == QuestionTag.tag_id)
        .where(QuestionTag.question_id.in_(question_ids))
    ).all()
    grouped: dict[str, set[str]] = {}
    for question_id, tag_id, label in rows:
        grouped.setdefault(question_id, set()).update({str(tag_id).lower(), str(label).lower()})
    return grouped


def done_question_ids(db: Session) -> set[str]:
    return set(db.scalars(select(Submission.question_id).distinct()))


def batch_used_question_ids(db: Session, batch_id: str) -> set[str]:
    return {
        str(question_id)
        for (question_id,) in db.execute(
            select(GenerationBatchItem.question_id).where(
                GenerationBatchItem.batch_id == batch_id,
                GenerationBatchItem.question_id.is_not(None),
            )
        ).all()
        if question_id
    }


def reusable_ai_questions(db: Session, request: dict, exclude_question_ids: set[str] | None = None, limit: int = 20) -> list[Question]:
    exclude_question_ids = set(exclude_question_ids or set()) | done_question_ids(db)
    stmt = (
        select(Question)
        .join(AnswerSpec, AnswerSpec.question_id == Question.id)
        .join(QuestionVersion, QuestionVersion.id == Question.current_version_id)
        .where(
            QuestionVersion.question_v2["content"]["language"].as_string() == normalize_language(request.get("output_language") or request.get("language") or current_language()),
            Question.status == "verified",
            AnswerSpec.status == "verified",
            Question.source_name == GENERATED_SOURCE_NAME,
            Question.eligible_for_practice.is_(True),
            Question.question_fault_status == "none",
            Question.publish_status.not_in(INACTIVE_PUBLISH_STATUSES),
        )
        .order_by(Question.id)
        .limit(500)
    )
    if request.get("difficulty_star"):
        stmt = stmt.where(Question.difficulty_star == request["difficulty_star"])
    if exclude_question_ids:
        stmt = stmt.where(Question.id.not_in(list(exclude_question_ids)))
    pool = list(db.scalars(stmt))
    target_tags = target_tag_set(request)
    grouped_tags = tags_by_question(db, [question.id for question in pool])

    def score(question: Question) -> tuple[int, int, str]:
        tags = grouped_tags.get(question.id, set())
        overlap = len(target_tags & tags)
        star_mismatch = 0 if not request.get("difficulty_star") or question.difficulty_star == request.get("difficulty_star") else 1
        return (-overlap, star_mismatch, question.id)

    pool.sort(key=score)
    return pool[:limit]


BATCH_BLUEPRINT_VERSION = "generation_batch_blueprint_v1"
BATCH_BLUEPRINT_ANSWER_TYPES = ("integer", "decimal", "number")
BATCH_BLUEPRINT_STORY_DOMAINS = ("number_pattern", "geometry", "counting", "operation_process", "logic_constraints", "measurement")
BATCH_BLUEPRINT_METHOD_HINTS = ("structural_observation", "case_analysis", "invariant", "counting", "extreme_reasoning", "reverse_construction", "diagram_reasoning", "modular_reasoning")
BATCH_BLUEPRINT_DIVERSITY_AXES = ("representation", "constraint_pattern", "story_domain", "reasoning_method", "answer_form", "given_unknown_relation")
BATCH_BLUEPRINT_SECONDARY_SKILLS = ("visual_model", "reverse_reasoning", "case_split", "bounds_check", "pattern_generalization", "unit_consistency", "parity_or_divisibility", "combinatorial_listing")


def _unique_nonempty(values: list[object] | tuple[object, ...] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        text = str(value).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _bounded_star(value: object, default: int = 3) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(5, parsed))


def _count_by(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def batch_blueprint_phase(index: int, total: int) -> str:
    if total <= 1:
        return "single"
    warmup_count = max(1, total // 5) if total >= 5 else 1
    challenge_count = max(1, total // 4) if total >= 4 else 1
    if index <= warmup_count:
        return "warmup"
    if index > total - challenge_count:
        return "challenge"
    return "core"


def build_generation_batch_blueprint(db: Session, request: dict, target_count: int) -> dict:
    target_total = _positive_int(target_count, 1)
    target_tags = _unique_nonempty(request.get("target_tags") or [])
    base_star = _bounded_star(request.get("difficulty_star"), 3)
    slots: list[dict] = []
    focus_tag_counts: dict[str, int] = {}
    for index in range(1, target_total + 1):
        focus_tags = [target_tags[(index - 1) % len(target_tags)]] if target_tags else []
        if len(target_tags) > 1 and target_total >= len(target_tags) * 2:
            secondary = target_tags[index % len(target_tags)]
            if secondary not in focus_tags:
                focus_tags.append(secondary)
        answer_type = BATCH_BLUEPRINT_ANSWER_TYPES[(index - 1) % len(BATCH_BLUEPRINT_ANSWER_TYPES)]
        story_domain = BATCH_BLUEPRINT_STORY_DOMAINS[(index - 1) % len(BATCH_BLUEPRINT_STORY_DOMAINS)]
        method_hint = BATCH_BLUEPRINT_METHOD_HINTS[(index - 1) % len(BATCH_BLUEPRINT_METHOD_HINTS)]
        diversity_axis = BATCH_BLUEPRINT_DIVERSITY_AXES[(index - 1) % len(BATCH_BLUEPRINT_DIVERSITY_AXES)]
        secondary_skill_seed = BATCH_BLUEPRINT_SECONDARY_SKILLS[(index - 1) % len(BATCH_BLUEPRINT_SECONDARY_SKILLS)]
        phase = batch_blueprint_phase(index, target_total)
        for tag in focus_tags or ["open"]:
            focus_tag_counts[tag] = focus_tag_counts.get(tag, 0) + 1
        slots.append(
            {
                "slot_id": f"slot-{index:02d}",
                "slot_index": index,
                "target_star": base_star,
                "phase": phase,
                "focus_tags": focus_tags,
                "answer_type_preference": answer_type,
                "story_domain": story_domain,
                "method_hint": method_hint,
                "diversity_axis": diversity_axis,
                "secondary_skill_seed": secondary_skill_seed,
                "repeat_family_guard": "do_not_clone_same_condition_structure",
                "slot_contract": (
                    f"第 {index} 题作为{phase}位：保持 {base_star} 星，优先训练"
                    f"{'、'.join(focus_tags) if focus_tags else '用户描述中的核心能力'}，"
                    f"答案必须是单一数值，优先为 {answer_type}，只允许整数或有限小数；故事域尽量使用 {story_domain}，方法侧重 {method_hint}；"
                    f"本题必须主要改变 {diversity_axis}，并加入二级能力 {secondary_skill_seed}，不能只换数字。"
                ),
            }
        )
    method_diversity_min = min(target_total, max(1, min(len(BATCH_BLUEPRINT_METHOD_HINTS), 4 if target_total >= 4 else target_total)))
    numeric_max = target_total
    return {
        "schema_version": BATCH_BLUEPRINT_VERSION,
        "planning_source": "deterministic_local_service",
        "target_count": target_total,
        "difficulty_star": base_star,
        "star_distribution": {str(base_star): target_total},
        "primary_tag_coverage": focus_tag_counts if target_tags else {},
        "method_diversity_min": method_diversity_min,
        "answer_type_limits": {"numeric_max": numeric_max, "only_single_numeric": True, "allowed_types": list(BATCH_BLUEPRINT_ANSWER_TYPES)},
        "story_domain_repeat_max": 2 if target_total >= 4 else 1,
        "difficulty_curve": "same_star_warmup_to_challenge" if target_total >= 3 else "same_star_compact",
        "forbidden_family_ids": _unique_nonempty(request.get("forbidden_family_ids") or []),
        "slot_retry_policy": {"max_attempts": max(target_total, target_total * 4), "replacement_slot": "first_missing_slot"},
        "slots": slots,
    }


def blueprint_slot_metadata(slot: dict | None) -> dict:
    if not isinstance(slot, dict):
        return {}
    return {
        "blueprint_slot_id": slot.get("slot_id"),
        "blueprint_slot": {
            "slot_index": slot.get("slot_index"),
            "phase": slot.get("phase"),
            "target_star": slot.get("target_star"),
            "focus_tags": slot.get("focus_tags") or [],
            "answer_type_preference": slot.get("answer_type_preference"),
            "story_domain": slot.get("story_domain"),
            "method_hint": slot.get("method_hint"),
        },
    }


def batch_blueprint(batch: GenerationBatch) -> dict:
    request = batch.request if isinstance(batch.request, dict) else {}
    progress = batch.progress if isinstance(batch.progress, dict) else {}
    blueprint = request.get("batch_blueprint") or progress.get("batch_blueprint") or {}
    return blueprint if isinstance(blueprint, dict) else {}


def batch_blueprint_slot_for_item(db: Session, batch: GenerationBatch, item_index: int) -> dict:
    blueprint = batch_blueprint(batch)
    slots = blueprint.get("slots") if isinstance(blueprint.get("slots"), list) else []
    slots = [slot for slot in slots if isinstance(slot, dict)]
    if not slots:
        return {}
    if 1 <= item_index <= len(slots):
        return slots[item_index - 1]
    accepted_slot_ids = {
        (row.similarity_review or {}).get("blueprint_slot_id")
        for row in batch_items(db, batch.id)
        if row.status == STATUS_ACCEPTED and isinstance(row.similarity_review, dict)
    }
    for slot in slots:
        if slot.get("slot_id") not in accepted_slot_ids:
            return slot
    return slots[(item_index - 1) % len(slots)]


def request_with_blueprint_slot(request: dict, slot: dict) -> dict:
    if not slot:
        return request
    focus_tags = _unique_nonempty(slot.get("focus_tags") or [])
    all_tags = _unique_nonempty(focus_tags + list(request.get("target_tags") or []))
    slot_contract = str(slot.get("slot_contract") or "").strip()
    constraints = "\n\n".join(part for part in [request.get("constraints"), f"套题蓝图要求：{slot_contract}" if slot_contract else ""] if part)
    next_request = {
        **request,
        "target_tags": all_tags,
        "difficulty_star": slot.get("target_star") or request.get("difficulty_star"),
        "constraints": constraints or request.get("constraints"),
        "batch_blueprint_slot": slot,
        "batch_blueprint_slot_id": slot.get("slot_id"),
    }
    return next_request


def generation_batch_blueprint_review(db: Session, batch: GenerationBatch) -> dict:
    blueprint = batch_blueprint(batch)
    slots = blueprint.get("slots") if isinstance(blueprint.get("slots"), list) else []
    slots = [slot for slot in slots if isinstance(slot, dict)]
    accepted_rows = [row for row in batch_items(db, batch.id) if row.status == STATUS_ACCEPTED and row.question_id]
    accepted_slot_ids = [str((row.similarity_review or {}).get("blueprint_slot_id") or "") for row in accepted_rows]
    accepted_slot_ids = [slot_id for slot_id in accepted_slot_ids if slot_id]
    planned_slot_ids = [str(slot.get("slot_id")) for slot in slots if slot.get("slot_id")]
    accepted_slot_set = set(accepted_slot_ids)
    missing_slot_ids = [slot_id for slot_id in planned_slot_ids if slot_id not in accepted_slot_set]
    duplicate_slot_ids = sorted(slot_id for slot_id, count in _count_by(accepted_slot_ids).items() if count > 1)
    answer_types: list[str] = []
    story_domains: list[str] = []
    method_hints: list[str] = []
    for row in accepted_rows:
        spec = db.get(AnswerSpec, row.question_id) if row.question_id else None
        if spec and spec.answer_type:
            answer_types.append(str(spec.answer_type))
        slot_meta = ((row.similarity_review or {}).get("blueprint_slot") or {}) if isinstance(row.similarity_review, dict) else {}
        if isinstance(slot_meta, dict):
            if slot_meta.get("story_domain"):
                story_domains.append(str(slot_meta["story_domain"]))
            if slot_meta.get("method_hint"):
                method_hints.append(str(slot_meta["method_hint"]))
    numeric_answer_types = {"integer", "decimal", "fraction", "number", "numeric"}
    numeric_count = sum(1 for value in answer_types if value in numeric_answer_types)
    numeric_limit = int((blueprint.get("answer_type_limits") or {}).get("numeric_max") or max(1, batch.target_count))
    method_min = int(blueprint.get("method_diversity_min") or 1)
    story_repeat_max = int(blueprint.get("story_domain_repeat_max") or max(1, batch.target_count))
    story_counts = _count_by(story_domains)
    story_over_limit = {key: count for key, count in story_counts.items() if count > story_repeat_max}
    issues: list[str] = []
    if missing_slot_ids:
        issues.append("blueprint_slots_missing")
    if duplicate_slot_ids:
        issues.append("blueprint_slots_duplicated")
    if numeric_count > numeric_limit:
        issues.append("numeric_answer_type_limit_exceeded")
    if len(set(method_hints)) < min(method_min, len(accepted_rows)):
        issues.append("method_diversity_below_blueprint_min")
    if story_over_limit:
        issues.append("story_domain_repeat_limit_exceeded")
    coverage_score = max(0, 100 - len(missing_slot_ids) * 12 - len(duplicate_slot_ids) * 8 - len(issues) * 5)
    return {
        "schema_version": "generation_batch_blueprint_review_v1",
        "blueprint_version": blueprint.get("schema_version"),
        "target_count": batch.target_count,
        "planned_slot_count": len(planned_slot_ids),
        "accepted_slot_count": len(accepted_slot_set),
        "missing_slot_ids": missing_slot_ids,
        "duplicate_slot_ids": duplicate_slot_ids,
        "actual_answer_type_counts": _count_by(answer_types),
        "numeric_answer_count": numeric_count,
        "numeric_answer_limit": numeric_limit,
        "actual_story_domain_counts": story_counts,
        "story_domain_repeat_max": story_repeat_max,
        "actual_method_count": len(set(method_hints)),
        "method_diversity_min": method_min,
        "coverage_score": coverage_score,
        "ok": not issues,
        "issues": issues,
    }


def batch_max_attempts(batch: GenerationBatch) -> int:
    try:
        target_count = max(1, int(batch.target_count or 1))
    except (TypeError, ValueError):
        target_count = 1
    try:
        accepted_count = max(0, min(target_count, int(batch.accepted_count or 0)))
    except (TypeError, ValueError):
        accepted_count = 0
    remaining_target = max(0, target_count - accepted_count)
    dynamic_limit = target_count if remaining_target <= 0 else accepted_count + remaining_target * 4
    blueprint = batch_blueprint(batch)
    policy = blueprint.get("slot_retry_policy") if isinstance(blueprint.get("slot_retry_policy"), dict) else {}
    try:
        configured = int(policy.get("max_attempts") or 0)
    except (TypeError, ValueError):
        configured = 0
    configured_limit = configured if configured > 0 else target_count * 4
    return max(target_count, min(configured_limit, dynamic_limit))


def batch_top_up_plan(db: Session, batch: GenerationBatch) -> dict:
    rows = batch_items(db, batch.id)
    review = generation_batch_blueprint_review(db, batch)
    active_count = sum(1 for row in rows if row.status in ACTIVE_TASK_STATUSES)
    failed_item_count = sum(1 for row in rows if row.status in {STATUS_REJECTED, STATUS_FAILED, STATUS_TIMED_OUT, STATUS_CANCELLED})
    remaining_target = max(0, int(batch.target_count or 0) - int(batch.accepted_count or 0))
    max_attempts = batch_max_attempts(batch)
    remaining_attempts = max(0, max_attempts - len(rows))
    planned_new_items = remaining_attempts if remaining_target > 0 else 0
    eligible_statuses = {STATUS_PARTIAL, STATUS_FAILED, STATUS_TIMED_OUT}
    reason = None
    if batch.status == STATUS_COMPLETED or batch.accepted_count >= batch.target_count:
        reason = "target_already_reached"
    elif batch.status in CANCEL_REQUESTED_BATCH_STATUSES:
        reason = "batch_cancelled_or_cancelling"
    elif batch.status in ACTIVE_TASK_STATUSES:
        reason = "batch_already_active"
    elif batch.status not in eligible_statuses:
        reason = f"batch_status_not_top_up_eligible:{batch.status}"
    elif active_count:
        reason = "batch_has_active_items"
    elif remaining_target <= 0:
        reason = "no_missing_target"
    elif remaining_attempts <= 0:
        reason = "slot_retry_attempt_limit_exhausted"
    return {
        "schema_version": "generation_batch_top_up_plan_v1",
        "batch_id": batch.id,
        "eligible": reason is None,
        "reason": reason,
        "status": batch.status,
        "target_count": batch.target_count,
        "accepted_count": batch.accepted_count,
        "remaining_target": remaining_target,
        "needed_accept_count": remaining_target,
        "existing_item_count": len(rows),
        "failed_item_count": failed_item_count,
        "active_item_count": active_count,
        "max_attempts": max_attempts,
        "remaining_attempts": remaining_attempts,
        "planned_new_items": planned_new_items,
        "next_item_index": len(rows) + 1,
        "missing_slot_ids": review.get("missing_slot_ids", []),
        "blueprint_review": review,
        "policy": {
            "only_add_missing_or_failed_slots": True,
            "preserve_accepted_items": True,
            "no_unbounded_retry": True,
        },
    }


def batch_item_source_type(row: GenerationBatchItem) -> str:
    review = row.similarity_review or {}
    if review.get("source_type"):
        return str(review["source_type"])
    if row.status == STATUS_ACCEPTED and row.job_id:
        return "new_ai"
    if row.status == STATUS_ACCEPTED and row.question_id:
        return "reused_ai"
    return "pending"


def refresh_batch_counts(db: Session, batch: GenerationBatch) -> None:
    previous_state = (
        batch.status,
        batch.accepted_count,
        batch.rejected_count,
        batch.failed_count,
        batch.eta_seconds,
        batch.progress or {},
        batch.completed_at,
    )
    rows = batch_items(db, batch.id)
    accepted = sum(1 for row in rows if row.status == STATUS_ACCEPTED)
    rejected = sum(1 for row in rows if row.status == STATUS_REJECTED)
    failed = sum(1 for row in rows if row.status in {STATUS_FAILED, STATUS_TIMED_OUT})
    cancelled = sum(1 for row in rows if row.status == STATUS_CANCELLED)
    running = sum(1 for row in rows if row.status in ACTIVE_TASK_STATUSES)
    reused = sum(1 for row in rows if row.status == STATUS_ACCEPTED and batch_item_source_type(row) == "reused_ai")
    generated = sum(1 for row in rows if row.status == STATUS_ACCEPTED and batch_item_source_type(row) in {"new_ai", "mastery_variant"})
    completed = sum(1 for row in rows if row.status in TERMINAL_ITEM_STATUSES)
    terminal_reason_counts = batch_failure_reason_counts(rows)
    measured = [row.duration_ms for row in rows if row.duration_ms] if completed else []
    eta_snapshot = batch_eta_snapshot(db, batch.target_count, accepted, batch.concurrency, measured)
    eta_seconds = int(eta_snapshot["eta_seconds"])
    max_attempts = batch_max_attempts(batch)
    attempt_limit_exhausted = len(rows) >= max_attempts
    batch.accepted_count = accepted
    batch.rejected_count = rejected
    batch.failed_count = failed
    batch.eta_seconds = eta_seconds
    if batch.status == STATUS_CANCELLING and running == 0:
        set_generation_batch_status(batch, STATUS_CANCELLED)
        batch.completed_at = batch.completed_at or utc_now()
    elif batch.status not in TERMINAL_BATCH_STATUSES and batch.status != STATUS_CANCELLING and accepted >= batch.target_count and running == 0:
        set_generation_batch_status(batch, STATUS_COMPLETED)
        batch.completed_at = batch.completed_at or utc_now()
    elif batch.status not in TERMINAL_BATCH_STATUSES and batch.status != STATUS_CANCELLING and running == 0 and attempt_limit_exhausted and accepted < batch.target_count:
        set_generation_batch_status(batch, STATUS_PARTIAL if accepted > 0 else STATUS_FAILED)
        batch.completed_at = batch.completed_at or utc_now()
    progress_stage = batch.status if batch.status in TERMINAL_BATCH_STATUSES or batch.status == STATUS_CANCELLING else (batch.progress or {}).get("stage", batch.status)
    next_progress = {
        **(batch.progress or {}),
        "stage": progress_stage,
        "target_count": batch.target_count,
        "accepted_count": accepted,
        "reused_count": reused,
        "generated_count": generated,
        "rejected_count": rejected,
        "failed_count": failed,
        "timed_out_count": sum(1 for row in rows if row.status == STATUS_TIMED_OUT),
        "cancelled_item_count": cancelled,
        "running_count": running,
        "created_count": len(rows),
        "completed_item_count": completed,
        "remaining_target_count": max(0, batch.target_count - accepted),
        "max_attempts": max_attempts,
        "attempt_limit_exhausted": attempt_limit_exhausted,
        "failure_reason_counts": terminal_reason_counts,
        "completion_percent": round(min(1.0, accepted / batch.target_count if batch.target_count else 0), 4),
        "eta_seconds": eta_seconds,
        "eta_source": eta_snapshot["eta_source"],
        "eta_sample_count": eta_snapshot["eta_sample_count"],
        "eta_average_ms": eta_snapshot["eta_average_ms"],
        "eta_confidence": eta_snapshot["eta_confidence"],
        "eta_range_seconds": eta_snapshot["eta_range_seconds"],
    }
    next_state = (
        batch.status,
        accepted,
        rejected,
        failed,
        eta_seconds,
        next_progress,
        batch.completed_at,
    )
    if next_state != previous_state:
        batch.updated_at = utc_now()
        batch.progress = next_progress
        persist_batch_sse_event(db, batch)


def current_batch_payload(db: Session, batch: GenerationBatch) -> dict:
    rows = batch_items(db, batch.id)
    return {
        "id": batch.id,
        "display_name": batch_display_name(batch),
        "status": batch.status,
        "target_count": batch.target_count,
        "concurrency": batch.concurrency,
        "accepted_count": batch.accepted_count,
        "rejected_count": batch.rejected_count,
        "failed_count": batch.failed_count,
        "eta_seconds": batch.eta_seconds,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "updated_at": batch.updated_at.isoformat() if batch.updated_at else None,
        "progress": batch.progress,
        "request": batch.request,
        "items": [
            {
                "id": row.id,
                "item_index": row.item_index,
                "status": row.status,
                "job_id": row.job_id,
                "candidate_id": row.candidate_id,
                "question_id": row.question_id,
                "similarity_review": row.similarity_review,
                "source_type": batch_item_source_type(row),
                "reuse_status": (row.similarity_review or {}).get("reuse_status"),
                "rejection_reason": row.rejection_reason,
                "duration_ms": row.duration_ms,
            }
            for row in rows
        ],
    }


def batch_payload(db: Session, batch: GenerationBatch) -> dict:
    mark_stale_batch_items(db, batch)
    refresh_batch_counts(db, batch)
    return current_batch_payload(db, batch)


def batch_item_sse_payload(row: GenerationBatchItem) -> dict:
    return {
        "id": row.id,
        "batch_id": row.batch_id,
        "item_index": row.item_index,
        "status": row.status,
        "job_id": row.job_id,
        "candidate_id": row.candidate_id,
        "question_id": row.question_id,
        "source_type": batch_item_source_type(row),
        "reuse_status": (row.similarity_review or {}).get("reuse_status"),
        "rejection_reason": row.rejection_reason,
        "duration_ms": row.duration_ms,
        "updated_at": progress_timestamp(row.updated_at),
    }


def persist_batch_item_sse_event(db: Session, row: GenerationBatchItem) -> None:
    persist_generation_event(
        db,
        "batch",
        row.batch_id,
        "batch_item",
        batch_item_sse_payload(row),
        group=20,
        status=row.status,
        index=row.item_index,
        occurred_at=row.updated_at or row.created_at,
    )


def persist_batch_sse_event(db: Session, batch: GenerationBatch) -> None:
    payload = current_batch_payload(db, batch)
    persist_generation_event(
        db,
        "batch",
        batch.id,
        "batch",
        payload,
        group=40,
        status=payload["status"],
        occurred_at=batch.updated_at or batch.created_at,
    )
    if payload["status"] in TERMINAL_BATCH_STATUSES:
        persist_generation_event(
            db,
            "batch",
            batch.id,
            "done",
            {"batch_id": batch.id, "status": payload["status"]},
            group=90,
            status=payload["status"],
            occurred_at=sse_latest_time(batch.completed_at, batch.updated_at, batch.created_at),
        )


def snapshot_batch_sse_events(db: Session, batch: GenerationBatch, last_event_id: str | None = None) -> list[dict]:
    payload = batch_payload(db, batch)
    rows = batch_items(db, batch.id)
    events = []

    for row in rows:
        event_id = sse_event_id(row.updated_at or row.created_at, 20, row.status, row.item_index)
        if sse_should_emit(event_id, last_event_id):
            events.append({"id": event_id, "event": "batch_item", "data": batch_item_sse_payload(row)})

    batch_event_id = sse_event_id(batch.updated_at or batch.created_at, 40, payload["status"])
    if sse_should_emit(batch_event_id, last_event_id):
        events.append({"id": batch_event_id, "event": "batch", "data": payload})

    if payload["status"] in TERMINAL_BATCH_STATUSES:
        done_id = sse_event_id(sse_latest_time(batch.completed_at, batch.updated_at, batch.created_at), 90, payload["status"])
        if sse_should_emit(done_id, last_event_id):
            events.append({"id": done_id, "event": "done", "data": {"batch_id": batch.id, "status": payload["status"]}})

    return sorted(events, key=lambda row: sse_event_key(row["id"]))


def batch_sse_events(db: Session, batch: GenerationBatch, last_event_id: str | None = None) -> list[dict]:
    persisted = stored_generation_events(db, "batch", batch.id, last_event_id)
    snapshot = snapshot_batch_sse_events(db, batch, last_event_id)
    return merge_sse_events(persisted, snapshot)


def create_batch_item(db: Session, batch: GenerationBatch, item_index: int) -> GenerationBatchItem:
    item = GenerationBatchItem(batch_id=batch.id, item_index=item_index, status=STATUS_QUEUED, similarity_review={})
    db.add(item)
    db.flush()
    persist_batch_item_sse_event(db, item)
    refresh_batch_counts(db, batch)
    db.commit()
    return item


def accepted_question_ids_for_batch(db: Session, batch_id: str) -> list[str]:
    batch = db.get(GenerationBatch, batch_id)
    if batch is None:
        return []
    rows = db.execute(
        select(GenerationBatchItem.question_id)
        .join(Question, Question.id == GenerationBatchItem.question_id)
        .join(AnswerSpec, AnswerSpec.question_id == Question.id)
        .where(
            GenerationBatchItem.batch_id == batch_id,
            GenerationBatchItem.status == STATUS_ACCEPTED,
            GenerationBatchItem.question_id.is_not(None),
            Question.status == "verified",
            AnswerSpec.status == "verified",
            Question.eligible_for_practice.is_(True),
            Question.question_fault_status == "none",
            Question.publish_status.not_in(("intervention_only", "quarantined", "retired", "duplicate_alias", "batch_rejected_similar")),
        )
        .order_by(GenerationBatchItem.item_index)
    ).all()
    ordered_unique: list[str] = []
    seen: set[str] = set()
    for (question_id,) in rows:
        if not question_id or question_id in seen:
            continue
        seen.add(str(question_id))
        ordered_unique.append(str(question_id))
        if len(ordered_unique) >= batch.target_count:
            break
    return ordered_unique_question_ids(ordered_unique)


def batch_similarity_review(db: Session, batch_id: str, question_id: str, item_id: str) -> dict:
    question = db.get(Question, question_id)
    if question is None:
        return {"risk": "unknown", "pairs": []}
    pairs = []
    for row in batch_items(db, batch_id):
        if row.id == item_id or row.status != STATUS_ACCEPTED or not row.question_id:
            continue
        other = db.get(Question, row.question_id)
        if other is None:
            continue
        report = similarity_report(other.stem_preview, question.stem_preview)
        pairs.append({"question_ids": [other.id, question.id], **report})
    high_pairs = [row for row in pairs if row.get("source_similarity_risk") == "high" or row.get("semantic_similarity", 0) >= 0.86]
    medium_pairs = [row for row in pairs if row.get("source_similarity_risk") == "medium"]
    risk = "high" if high_pairs else ("medium" if medium_pairs else "low")
    return {"risk": risk, "pairs": pairs[:10], "high_pair_count": len(high_pairs), "medium_pair_count": len(medium_pairs)}


def enforce_final_batch_diversity(db: Session, batch: GenerationBatch) -> None:
    accepted_rows = [row for row in batch_items(db, batch.id) if row.status == STATUS_ACCEPTED and row.question_id]
    kept: list[GenerationBatchItem] = []
    kept_question_ids: set[str] = set()
    for row in accepted_rows:
        existing_review = row.similarity_review or {}
        blueprint_fields = {key: existing_review[key] for key in ("blueprint_slot_id", "blueprint_slot") if key in existing_review}
        review = {"risk": "low", "pairs": []}
        question = db.get(Question, row.question_id) if row.question_id else None
        pairs = []
        if question is not None:
            for kept_row in kept:
                other = db.get(Question, kept_row.question_id) if kept_row.question_id else None
                if other is None:
                    continue
                report = similarity_report(other.stem_preview, question.stem_preview)
                pairs.append({"question_ids": [other.id, question.id], **report})
        high_pairs = [item for item in pairs if item.get("source_similarity_risk") == "high" or item.get("semantic_similarity", 0) >= 0.86]
        medium_pairs = [item for item in pairs if item.get("source_similarity_risk") == "medium"]
        duplicate_question_id = bool(row.question_id and row.question_id in kept_question_ids)
        if duplicate_question_id or high_pairs:
            source_type = batch_item_source_type(row)
            set_generation_item_status(row, STATUS_REJECTED)
            row.rejection_reason = "duplicate_question_in_batch" if duplicate_question_id else "near_duplicate_in_final_batch_review"
            row.similarity_review = {**blueprint_fields, "risk": "high", "pairs": pairs[:10], "high_pair_count": len(high_pairs), "medium_pair_count": len(medium_pairs), "source_type": source_type, "duplicate_question_id": duplicate_question_id}
            if question is not None and source_type != "reused_ai":
                question.publish_status = "batch_rejected_similar"
                question.eligible_for_practice = False
                question.eligible_for_test = False
        else:
            row.similarity_review = {**blueprint_fields, **(review if not pairs else {"risk": "medium" if medium_pairs else "low", "pairs": pairs[:10], "high_pair_count": 0, "medium_pair_count": len(medium_pairs)})}
            kept.append(row)
            if row.question_id:
                kept_question_ids.add(row.question_id)
        row.updated_at = utc_now()
        persist_batch_item_sse_event(db, row)


def try_reuse_ai_question_for_item(db: Session, batch: GenerationBatch, item: GenerationBatchItem, request_payload: dict, started: float, provider: str) -> bool:
    if request_payload.get("prefer_reuse_existing_ai") is False:
        return False
    with REUSE_AI_SELECTION_LOCK:
        db.refresh(batch)
        db.refresh(item)
        if batch.status in CANCEL_REQUESTED_BATCH_STATUSES or item.status not in ACTIVE_TASK_STATUSES:
            return False
        exclude_ids = batch_used_question_ids(db, batch.id)
        for question in reusable_ai_questions(db, request_payload, exclude_question_ids=exclude_ids, limit=20):
            review = batch_similarity_review(db, batch.id, question.id, item.id)
            if review["risk"] == "high":
                continue
            duration_ms = int((time.perf_counter() - started) * 1000)
            set_generation_item_status(item, STATUS_ACCEPTED)
            item.question_id = question.id
            item.duration_ms = duration_ms
            item.updated_at = utc_now()
            release_batch_item_lease(
                item,
                **{
                    **review,
                    **blueprint_slot_metadata(request_payload.get("batch_blueprint_slot") if isinstance(request_payload.get("batch_blueprint_slot"), dict) else None),
                    "source_type": "reused_ai",
                    "reuse_status": "reused_existing_ai_question",
                    "reuse_reason": "same_star_tag_close_unused_ai_question",
                },
            )
            db.add(
                GenerationDurationStat(
                    batch_id=batch.id,
                    item_id=item.id,
                    provider="reuse",
                    model=None,
                    status=STATUS_ACCEPTED,
                    duration_ms=duration_ms,
                )
            )
            persist_batch_item_sse_event(db, item)
            refresh_batch_counts(db, batch)
            batch.progress = {
                **(batch.progress or {}),
                "last_reused_question_id": question.id,
                "last_reuse_reason": "same_star_tag_close_unused_ai_question",
            }
            batch.updated_at = utc_now()
            persist_batch_sse_event(db, batch)
            db.commit()
            return True
    return False


def process_batch_item(db: Session, item_id: str, provider: str) -> None:
    item = db.get(GenerationBatchItem, item_id)
    if item is None:
        return
    lease_owner = f"batch-item-{item_id}-{uuid4()}"
    if not claim_batch_item_lease(db, item, lease_owner):
        return
    batch = db.get(GenerationBatch, item.batch_id)
    if batch is None or batch.status in CANCEL_REQUESTED_BATCH_STATUSES:
        if item.status in ACTIVE_TASK_STATUSES:
            set_generation_item_status(item, STATUS_CANCELLED)
            item.rejection_reason = "batch_cancelled" if batch is not None else "batch_missing"
            release_batch_item_lease(item, source_type=batch_item_source_type(item), terminal_reason=item.rejection_reason)
            item.updated_at = utc_now()
            persist_batch_item_sse_event(db, item)
            db.commit()
        return
    started = time.perf_counter()
    slot = batch_blueprint_slot_for_item(db, batch, item.item_index)
    request_payload = request_with_blueprint_slot(
        {**(batch.request or {}), "batch_id": batch.id, "batch_item_id": item.id, "batch_item_index": item.item_index},
        slot,
    )
    if try_reuse_ai_question_for_item(db, batch, item, request_payload, started, provider):
        return
    item.similarity_review = {
        **(item.similarity_review or {}),
        **blueprint_slot_metadata(slot),
        "source_type": "new_ai",
        "reuse_status": "forced_new_generation" if request_payload.get("prefer_reuse_existing_ai") is False else "no_reusable_ai_question_available",
        "reuse_reason": "reuse_disabled_by_request" if request_payload.get("prefer_reuse_existing_ai") is False else "no_same_star_tag_close_unused_ai_question",
    }
    job = GenerationJob(status=STATUS_QUEUED, request=request_payload, progress={"stage": STATUS_QUEUED, "batch_id": batch.id, "batch_item_id": item.id})
    db.add(job)
    db.flush()
    item.job_id = job.id
    item.updated_at = utc_now()
    persist_batch_item_sse_event(db, item)
    db.commit()
    try:
        process_generation_job(db, job.id, provider=provider)
        db.refresh(job)
        db.refresh(item)
        duration_ms = int((time.perf_counter() - started) * 1000)
        model_selection = ((job.progress or {}).get("model_selection") or {}) if isinstance(job.progress, dict) else {}
        resolved_model = model_selection.get("generator_model") or (batch.request or {}).get("default_model")
        if item.status in {STATUS_CANCELLED, STATUS_TIMED_OUT}:
            quarantine_late_published_question(db, job.accepted_question_id, item.status)
            item.duration_ms = item.duration_ms or duration_ms
            item.updated_at = utc_now()
            release_batch_item_lease(item, source_type=batch_item_source_type(item), terminal_reason=item.rejection_reason or item.status)
            db.add(GenerationDurationStat(batch_id=batch.id, item_id=item.id, provider=provider, model=resolved_model, status=item.status, duration_ms=duration_ms))
            persist_batch_item_sse_event(db, item)
            refresh_batch_counts(db, batch)
            db.commit()
            return
        item.duration_ms = duration_ms
        item.updated_at = utc_now()
        candidate = db.scalar(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job.id, GeneratedCandidate.published_question_id == job.accepted_question_id))
        if candidate is not None:
            item.candidate_id = candidate.id
        db.refresh(batch)
        if batch.status in CANCEL_REQUESTED_BATCH_STATUSES or job.status in {STATUS_CANCELLED, STATUS_TIMED_OUT}:
            reason = "batch_cancelled" if batch.status in CANCEL_REQUESTED_BATCH_STATUSES else job.status
            quarantine_late_published_question(db, job.accepted_question_id, reason)
            set_generation_item_status(item, STATUS_CANCELLED if reason in {"batch_cancelled", STATUS_CANCELLED} else STATUS_FAILED)
            item.question_id = None
            item.rejection_reason = reason
            release_batch_item_lease(item, source_type=batch_item_source_type(item), terminal_reason=reason)
            db.add(GenerationDurationStat(batch_id=batch.id, item_id=item.id, provider=provider, model=resolved_model, status=item.status, duration_ms=duration_ms))
            persist_batch_item_sse_event(db, item)
            refresh_batch_counts(db, batch)
            db.commit()
            return
        if job.accepted_question_id:
            review = batch_similarity_review(db, batch.id, job.accepted_question_id, item.id)
            item.similarity_review = {**review, **blueprint_slot_metadata(slot), "source_type": "new_ai", "reuse_status": "generated_new_question"}
            if review["risk"] == "high":
                set_generation_item_status(item, STATUS_REJECTED)
                item.rejection_reason = "near_duplicate_in_batch"
                question = db.get(Question, job.accepted_question_id)
                if question is not None:
                    question.publish_status = "batch_rejected_similar"
                    question.eligible_for_practice = False
                    question.eligible_for_test = False
            else:
                set_generation_item_status(item, STATUS_ACCEPTED)
                item.question_id = job.accepted_question_id
            release_batch_item_lease(item, source_type="new_ai", reuse_status="generated_new_question")
        elif job.status == STATUS_REJECTED:
            set_generation_item_status(item, STATUS_REJECTED)
            item.rejection_reason = "no_candidate_accepted"
            release_batch_item_lease(item, source_type="new_ai", terminal_reason="no_candidate_accepted")
        else:
            set_generation_item_status(item, STATUS_FAILED)
            item.rejection_reason = job.status
            release_batch_item_lease(item, source_type="new_ai", terminal_reason=job.status)
        db.add(GenerationDurationStat(batch_id=batch.id, item_id=item.id, provider=provider, model=resolved_model, status=item.status, duration_ms=duration_ms))
        persist_batch_item_sse_event(db, item)
        refresh_batch_counts(db, batch)
        db.commit()
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        model_selection = ((job.progress or {}).get("model_selection") or {}) if isinstance(job.progress, dict) else {}
        resolved_model = model_selection.get("generator_model") or (batch.request or {}).get("default_model")
        if item.status not in {STATUS_ACCEPTED, STATUS_REJECTED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMED_OUT}:
            set_generation_item_status(item, STATUS_FAILED)
        item.duration_ms = duration_ms
        item.rejection_reason = str(exc)
        release_batch_item_lease(item, source_type=batch_item_source_type(item), terminal_reason=str(exc))
        item.updated_at = utc_now()
        db.add(GenerationDurationStat(batch_id=batch.id, item_id=item.id, provider=provider, model=resolved_model, status=STATUS_FAILED, duration_ms=duration_ms))
        persist_batch_item_sse_event(db, item)
        refresh_batch_counts(db, batch)
        db.commit()


def process_batch_item_with_new_session(item_id: str, provider: str) -> None:
    with Session(get_engine()) as db:
        process_batch_item(db, item_id, provider)


def mark_generation_batch_background_error(db: Session, batch_id: str, exc: Exception) -> None:
    batch = db.get(GenerationBatch, batch_id)
    if batch is None:
        return
    refresh_batch_counts(db, batch)
    if batch.status == STATUS_CANCELLING:
        set_generation_batch_status(batch, STATUS_CANCELLED)
    elif batch.status == STATUS_CANCELLED:
        pass
    elif batch.status not in TERMINAL_BATCH_STATUSES:
        set_generation_batch_status(batch, STATUS_FAILED)
    batch.completed_at = batch.completed_at or utc_now()
    batch.progress = {
        **(batch.progress or {}),
        "stage": batch.status,
        "background_error": str(exc),
        "background_error_at": progress_timestamp(),
    }
    db.commit()


def process_generation_batch(db: Session, batch_id: str, provider: str = "mock", threaded: bool = False) -> dict:
    batch = db.get(GenerationBatch, batch_id)
    if batch is None:
        raise ValueError(f"generation batch not found: {batch_id}")
    if batch.status in TERMINAL_BATCH_STATUSES or batch.status in CANCEL_REQUESTED_BATCH_STATUSES:
        refresh_batch_counts(db, batch)
        db.commit()
        return batch_payload(db, batch)
    set_generation_batch_status(batch, STATUS_RUNNING)
    batch.started_at = batch.started_at or utc_now()
    batch.progress = {**(batch.progress or {}), "stage": STATUS_RUNNING}
    db.commit()
    max_attempts = batch_max_attempts(batch)
    next_index = len(batch_items(db, batch.id)) + 1

    if not threaded:
        while batch.accepted_count < batch.target_count and next_index <= max_attempts and batch.status not in CANCEL_REQUESTED_BATCH_STATUSES:
            item = create_batch_item(db, batch, next_index)
            process_batch_item(db, item.id, provider)
            db.refresh(batch)
            refresh_batch_counts(db, batch)
            next_index += 1
    else:
        futures = {}
        with ThreadPoolExecutor(max_workers=batch.concurrency) as executor:
            while batch.status not in CANCEL_REQUESTED_BATCH_STATUSES and (batch.accepted_count < batch.target_count or futures):
                db.refresh(batch)
                refresh_batch_counts(db, batch)
                while batch.accepted_count + len(futures) < batch.target_count and len(futures) < batch.concurrency and next_index <= max_attempts and batch.status not in CANCEL_REQUESTED_BATCH_STATUSES:
                    item = create_batch_item(db, batch, next_index)
                    futures[executor.submit(process_batch_item_with_new_session, item.id, provider)] = item.id
                    next_index += 1
                if not futures:
                    break
                done, _ = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
                for future in done:
                    item_id = futures.pop(future, None)
                    try:
                        future.result()
                    except Exception as exc:
                        if item_id:
                            failed_item = db.get(GenerationBatchItem, item_id)
                            if failed_item is not None:
                                if failed_item.status not in {STATUS_ACCEPTED, STATUS_REJECTED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMED_OUT}:
                                    set_generation_item_status(failed_item, STATUS_FAILED)
                                failed_item.rejection_reason = str(exc)
                                failed_item.updated_at = utc_now()
                                release_batch_item_lease(failed_item, source_type=batch_item_source_type(failed_item), terminal_reason=str(exc))
                                persist_batch_item_sse_event(db, failed_item)
                        batch.progress = {
                            **(batch.progress or {}),
                            "stage": STATUS_RUNNING,
                            "background_item_error": str(exc),
                            "background_item_error_at": progress_timestamp(),
                        }
                        refresh_batch_counts(db, batch)
                        db.commit()
                db.refresh(batch)
                refresh_batch_counts(db, batch)
                db.commit()

    db.refresh(batch)
    refresh_batch_counts(db, batch)
    enforce_final_batch_diversity(db, batch)
    refresh_batch_counts(db, batch)
    blueprint_review = generation_batch_blueprint_review(db, batch)
    if batch.status in CANCEL_REQUESTED_BATCH_STATUSES:
        set_generation_batch_status(batch, STATUS_CANCELLED)
    else:
        if batch.accepted_count >= batch.target_count:
            set_generation_batch_status(batch, STATUS_COMPLETED)
        elif batch.accepted_count > 0:
            set_generation_batch_status(batch, STATUS_PARTIAL)
        else:
            set_generation_batch_status(batch, STATUS_FAILED)
    batch.completed_at = utc_now()
    batch.progress = {**(batch.progress or {}), "stage": batch.status, "blueprint_review": blueprint_review}
    db.commit()
    return batch_payload(db, batch)


def process_generation_batch_background(batch_id: str, provider: str) -> None:
    with Session(get_engine()) as db:
        try:
            process_generation_batch(db, batch_id, provider=provider, threaded=True)
        except Exception as exc:
            db.rollback()
            mark_generation_batch_background_error(db, batch_id, exc)


def build_batch_request(db: Session, payload: GenerationBatchRequest) -> dict:
    constraints = "\n".join(part for part in [payload.constraints, payload.custom_description] if part)
    request = {
        "target_tags": payload.target_tags,
        "difficulty_star": payload.difficulty_star,
        "count": 1,
        "constraints": constraints or None,
        "custom_description": payload.custom_description,
        "source_session_id": payload.source_session_id,
        "solution_style": payload.solution_style,
        "prefer_reuse_existing_ai": payload.prefer_reuse_existing_ai,
        "candidate_count": payload.candidate_count,
        "source_example_count": payload.source_example_count,
        "ai_negative_example_count": payload.ai_negative_example_count,
        "allow_budget_overrun": payload.allow_budget_overrun,
        "allow_reviewer_quality_downgrade": payload.allow_reviewer_quality_downgrade,
    }
    return plan_generation_request(db, request)


@batch_router.post("", response_model=GenerationBatchResponse)
def create_batch(payload: GenerationBatchRequest, background_tasks: BackgroundTasks, run_now: bool = True, db: Session = Depends(get_db)) -> dict:
    if payload.source_session_id:
        source_session = db.get(PracticeSession, payload.source_session_id)
        if source_session is None:
            raise HTTPException(status_code=404, detail="source session not found")
        strict_no_ai = bool((source_session.config or {}).get("selection", {}).get("strict_no_ai", False))
        if source_session.mode == "strict_test" or strict_no_ai:
            raise HTTPException(status_code=403, detail="AI generation is disabled for strict test sessions")
    provider = "mock" if payload.use_mock else "openai_compatible"
    settings = get_settings()
    request_payload = {**build_batch_request(db, payload), "batch_provider": provider, "batch_concurrency": payload.concurrency}
    batch_blueprint_snapshot = build_generation_batch_blueprint(db, request_payload, payload.count)
    request_payload["batch_blueprint"] = batch_blueprint_snapshot
    call_budget = generation_call_budget_snapshot(request_payload, payload.count, "batch", settings)
    enforce_generation_call_budget(call_budget)
    reusable_count = 0
    if request_payload.get("prefer_reuse_existing_ai") is not False:
        reusable_count = len(reusable_ai_questions(db, request_payload, limit=payload.count))
    needs_provider_generation = provider != "mock" and reusable_count < payload.count
    cost_budget = ai_cost_budget_snapshot(db, settings, provider if needs_provider_generation else "mock", call_budget, request_payload)
    enforce_ai_cost_budget(cost_budget, allow_overrun=payload.allow_budget_overrun)
    request_payload["call_budget"] = call_budget
    request_payload["cost_budget"] = cost_budget
    eta_snapshot = batch_eta_snapshot(db, payload.count, 0, payload.concurrency)
    batch = GenerationBatch(
        status=STATUS_QUEUED,
        request=request_payload,
        target_count=payload.count,
        concurrency=payload.concurrency,
        progress={
            "stage": STATUS_QUEUED,
            "target_count": payload.count,
            "accepted_count": 0,
            "reused_count": 0,
            "generated_count": 0,
            "rejected_count": 0,
            "failed_count": 0,
            "running_count": 0,
            "completed_item_count": 0,
            "remaining_target_count": payload.count,
            "failure_reason_counts": {},
            "candidate_count": payload.candidate_count,
            "source_example_count": payload.source_example_count,
            "ai_negative_example_count": payload.ai_negative_example_count,
            "prefer_reuse_existing_ai": payload.prefer_reuse_existing_ai,
            "eta_seconds": eta_snapshot["eta_seconds"],
            "eta_source": eta_snapshot["eta_source"],
            "eta_sample_count": eta_snapshot["eta_sample_count"],
            "eta_average_ms": eta_snapshot["eta_average_ms"],
            "eta_confidence": eta_snapshot["eta_confidence"],
            "eta_range_seconds": eta_snapshot["eta_range_seconds"],
            "call_budget": call_budget,
            "cost_budget": cost_budget,
            "reusable_ai_prefetch_count": reusable_count,
            "batch_blueprint": batch_blueprint_snapshot,
            "queue_policy": "one_active_generation_batch",
            "user_message": translate_message("generation.queued_notice"),
            "user_message_key": "generation.queued_notice",
            "user_message_params": {},
        },
    )
    db.add(batch)
    db.flush()
    log_audit_event(db, "generation.batch_created", "generation_batch", batch.id, {"run_now": run_now, "request": request_payload})
    db.commit()
    db.refresh(batch)
    if run_now:
        schedule_generation_batch_queue(background_tasks)
    return batch_payload(db, batch)


@batch_router.get("", response_model=list[GenerationBatchResponse])
def list_batches(limit: int = 20, db: Session = Depends(get_db)) -> list[dict]:
    limit = max(1, min(100, int(limit or 20)))
    rows = list(db.scalars(select(GenerationBatch).order_by(GenerationBatch.created_at.desc(), GenerationBatch.id.desc()).limit(limit)))
    return [batch_payload(db, batch) for batch in rows]


@batch_router.get("/{batch_id}", response_model=GenerationBatchResponse)
def get_batch(batch_id: str, db: Session = Depends(get_db)) -> dict:
    batch = db.get(GenerationBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return batch_payload(db, batch)


@batch_router.post("/{batch_id}/cancel")
def cancel_batch(batch_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)) -> dict:
    batch = db.get(GenerationBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    mark_stale_batch_items(db, batch)
    refresh_batch_counts(db, batch)
    if batch.accepted_count >= batch.target_count:
        set_generation_batch_status(batch, STATUS_COMPLETED)
        batch.completed_at = batch.completed_at or utc_now()
        batch.progress = {**(batch.progress or {}), "stage": STATUS_COMPLETED, "cancel_ignored": "target_already_reached"}
        db.commit()
        return batch_payload(db, batch)
    if batch.status in TERMINAL_BATCH_STATUSES:
        return batch_payload(db, batch)
    rows = batch_items(db, batch_id)
    running_rows = [row for row in rows if row.status == STATUS_RUNNING]
    set_generation_batch_status(batch, STATUS_CANCELLING if running_rows else STATUS_CANCELLED)
    batch.progress = {
        **(batch.progress or {}),
        "stage": batch.status,
        "cancelled": batch.status == STATUS_CANCELLED,
        "cancelling": batch.status == STATUS_CANCELLING,
        "cancel_requested_at": progress_timestamp(),
    }
    for row in rows:
        if row.status == STATUS_QUEUED:
            set_generation_item_status(row, STATUS_CANCELLED)
            release_batch_item_lease(row, source_type=batch_item_source_type(row), terminal_reason="batch_cancelled")
            row.updated_at = utc_now()
    refresh_batch_counts(db, batch)
    log_audit_event(db, "generation.batch_cancelled", "generation_batch", batch.id, {"status": batch.status})
    db.commit()
    schedule_generation_batch_queue(background_tasks)
    return batch_payload(db, batch)


@batch_router.post("/{batch_id}/top-up", response_model=GenerationBatchResponse)
def top_up_batch(batch_id: str, payload: GenerationBatchTopUpRequest | None, background_tasks: BackgroundTasks, db: Session = Depends(get_db)) -> dict:
    payload = payload or GenerationBatchTopUpRequest()
    batch = db.get(GenerationBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    mark_stale_batch_items(db, batch)
    refresh_batch_counts(db, batch)
    request_snapshot = batch.request if isinstance(batch.request, dict) else {}
    if request_snapshot.get("origin") == "mastery_variant_batch":
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "mastery_variant_batch_top_up_unavailable",
                "user_message": "批量类似题不走普通套题补齐；请在历史中重新选择题目生成新批次。",
                "retryable": False,
            },
        )
    plan = batch_top_up_plan(db, batch)
    if not plan["eligible"]:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "generation_batch_top_up_unavailable",
                "user_message": "当前套题批次不能补齐剩余题。",
                "retryable": plan.get("reason") in {"batch_already_active", "batch_has_active_items"},
                "details_safe": plan,
            },
        )
    request_payload = {**(batch.request or {})}
    request_payload["allow_budget_overrun"] = bool(payload.allow_budget_overrun or request_payload.get("allow_budget_overrun"))
    request_payload["allow_reviewer_quality_downgrade"] = bool(payload.allow_reviewer_quality_downgrade or request_payload.get("allow_reviewer_quality_downgrade"))
    provider = str(request_payload.get("batch_provider") or "mock")
    settings = get_settings()
    call_budget = generation_top_up_call_budget_snapshot(request_payload, batch, int(plan["planned_new_items"]), int(plan["existing_item_count"]), settings)
    enforce_generation_call_budget(call_budget)
    reusable_count = 0
    if request_payload.get("prefer_reuse_existing_ai") is not False:
        reusable_count = len(reusable_ai_questions(db, request_payload, exclude_question_ids=batch_used_question_ids(db, batch.id), limit=int(plan["planned_new_items"])))
    needs_provider_generation = provider != "mock" and reusable_count < int(plan["planned_new_items"])
    cost_budget = ai_cost_budget_snapshot(db, settings, provider if needs_provider_generation else "mock", call_budget, request_payload)
    enforce_ai_cost_budget(cost_budget, allow_overrun=bool(request_payload.get("allow_budget_overrun")))
    now_text = progress_timestamp()
    request_payload["top_up"] = {
        "requested_at": now_text,
        "run_now": payload.run_now,
        "plan": plan,
        "call_budget": call_budget,
        "cost_budget": cost_budget,
        "reusable_ai_prefetch_count": reusable_count,
    }
    batch.request = request_payload
    set_generation_batch_status(batch, STATUS_QUEUED)
    batch.completed_at = None
    batch.progress = {
        **(batch.progress or {}),
        "stage": STATUS_QUEUED,
        "top_up": request_payload["top_up"],
        "call_budget": call_budget,
        "cost_budget": cost_budget,
        "reusable_ai_prefetch_count": reusable_count,
        "top_up_requested_at": now_text,
        "queue_policy": "one_active_generation_batch",
        "user_message": "补齐任务已加入队列；如果已有批次正在运行，会自动等待。",
    }
    batch.updated_at = utc_now()
    log_audit_event(
        db,
        "generation.batch_top_up_requested",
        "generation_batch",
        batch.id,
        {"run_now": payload.run_now, "plan": plan, "call_budget": call_budget, "cost_budget": cost_budget},
    )
    db.commit()
    db.refresh(batch)
    if payload.run_now:
        schedule_generation_batch_queue(background_tasks)
    return batch_payload(db, batch)


@batch_router.post("/{batch_id}/practice", response_model=CreatePracticeSessionResponse)
def create_or_update_batch_practice(batch_id: str, payload: GenerationBatchPracticeRequest | None = None, db: Session = Depends(get_db)) -> CreatePracticeSessionResponse:
    payload = payload or GenerationBatchPracticeRequest()
    batch = db.get(GenerationBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    question_ids = ordered_unique_question_ids(accepted_question_ids_for_batch(db, batch_id))
    if not question_ids:
        raise HTTPException(status_code=409, detail="no accepted generated questions are available yet")
    if payload.session_id:
        session = db.get(PracticeSession, payload.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        if payload.learner_id:
            requested_learner_id = resolve_learner_id(db, payload.learner_id)
            if session.learner_id != requested_learner_id:
                raise HTTPException(status_code=409, detail="session belongs to another learner")
        merged = ordered_unique_question_ids([*(session.question_ids or []), *question_ids])
        session.question_ids = merged
        session.config = {**(session.config or {}), "learner_id": session.learner_id, "generation_batch_id": batch_id, "selection": {**((session.config or {}).get("selection") or {}), "generation_batch_id": batch_id}}
        session.updated_at = utc_now()
    else:
        learner_id = resolve_learner_id(db, payload.learner_id)
        session = PracticeSession(
            mode="generated_practice",
            learner_id=learner_id,
            config={"learner_id": learner_id, "generation_batch_id": batch_id, "selection": {"generation_batch_id": batch_id, "include_generated": True}},
            question_ids=question_ids,
        )
        db.add(session)
        db.flush()
        db.add(AnswerEvent(session_id=session.id, question_id="", event_type="generation_batch_practice_created", payload={"generation_batch_id": batch_id, "question_ids": question_ids, "at_ms": 0}))
    db.add(AnswerEvent(session_id=session.id, question_id="", event_type="generation_batch_queue_updated", payload={"generation_batch_id": batch_id, "question_ids": question_ids, "at_ms": 0}))
    db.commit()
    return practice_session_response(session)


@batch_router.get("/{batch_id}/events")
def batch_events(batch_id: str, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")) -> StreamingResponse:
    def stream():
        deadline = time.time() + 300
        cursor = last_event_id
        while True:
            batch_status = "unknown"
            with Session(get_engine()) as stream_db:
                batch = stream_db.get(GenerationBatch, batch_id)
                if batch is None:
                    yield sse_format("error", {"detail": "batch not found"})
                    return
                events = batch_sse_events(stream_db, batch, cursor)
                batch_status = batch.status
                if stream_db.dirty:
                    stream_db.commit()
            if events:
                for event in events:
                    yield sse_format(event["event"], event["data"], event["id"])
                    cursor = event["id"]
            elif batch_status not in TERMINAL_BATCH_STATUSES:
                yield sse_format("heartbeat", {"batch_id": batch_id, "ts": time.time()})
            if batch_status in TERMINAL_BATCH_STATUSES:
                return
            if time.time() >= deadline:
                timeout_id = sse_event_id(utc_now(), 95, "timed_out")
                yield sse_format("timeout", {"detail": "stream timeout; reconnect to continue"}, timeout_id)
                return
            time.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("", response_model=GenerationJobResponse)
def create_job(
    payload: GenerationJobRequest,
    run_now: bool = True,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> GenerationJobResponse:
    if payload.source_session_id:
        source_session = db.get(PracticeSession, payload.source_session_id)
        if source_session is None:
            raise HTTPException(status_code=404, detail="source session not found")
        strict_no_ai = bool((source_session.config or {}).get("selection", {}).get("strict_no_ai", False))
        if source_session.mode == "strict_test" or strict_no_ai:
            raise HTTPException(status_code=403, detail="AI generation is disabled for strict test sessions")
    if idempotency_key:
        existing = db.scalar(select(GenerationJob).where(GenerationJob.idempotency_key == idempotency_key))
        if existing is not None:
            expected_language = payload.output_language or current_language()
            stored_language = (existing.request or {}).get("output_language") or (existing.request or {}).get("language")
            if stored_language != expected_language:
                raise HTTPException(status_code=409, detail="idempotency_output_language_conflict")
            return generation_job_response(existing)
    provider = "mock" if payload.use_mock else "openai_compatible"
    settings = get_settings()
    request_payload = {**plan_generation_request(db, payload.model_dump()), "job_provider": provider}
    call_budget = generation_call_budget_snapshot(request_payload, 1, "job", settings)
    enforce_generation_call_budget(call_budget)
    if request_payload.get("prefer_reuse_existing_ai") is not False:
        reusable = reusable_ai_questions(db, request_payload, limit=1)
        if reusable:
            question = reusable[0]
            cost_budget = ai_cost_budget_snapshot(db, settings, "mock", {**call_budget, "actual_provider_calls_planned": 0}, request_payload)
            job = GenerationJob(
                idempotency_key=idempotency_key,
                status=STATUS_ACCEPTED,
                request=request_payload,
                accepted_question_id=question.id,
                progress={
                    "stage": "done",
                    "accepted": True,
                    "question_id": question.id,
                    "source_type": "reused_ai",
                    "reuse_status": "reused_existing_ai_question",
                    "reuse_reason": "same_star_tag_close_unused_ai_question",
                    "provider": "reuse",
                    "candidate_count": request_payload.get("candidate_count", 3),
                    "source_example_count": request_payload.get("source_example_count", 5),
                    "ai_negative_example_count": request_payload.get("ai_negative_example_count", 3),
                    "prefer_reuse_existing_ai": True,
                    "call_budget": {**call_budget, "actual_provider_calls_planned": 0},
                    "cost_budget": cost_budget,
                },
            )
            db.add(job)
            db.flush()
            log_audit_event(db, "generation.job_created", "generation_job", job.id, {"run_now": run_now, "idempotency_key": bool(idempotency_key), "request": request_payload})
            log_audit_event(db, "generation.job_reused_existing_ai", "generation_job", job.id, {"question_id": question.id, "request": request_payload})
            db.commit()
            db.refresh(job)
            return generation_job_response(job)
    cost_budget = ai_cost_budget_snapshot(db, settings, provider, call_budget, request_payload)
    enforce_ai_cost_budget(cost_budget, allow_overrun=payload.allow_budget_overrun)
    request_payload["cost_budget"] = cost_budget
    job = GenerationJob(
        idempotency_key=idempotency_key,
        status=STATUS_QUEUED,
        request=request_payload,
        progress={
            "stage": STATUS_QUEUED,
            "candidate_count": request_payload.get("candidate_count", 3),
            "source_example_count": request_payload.get("source_example_count", 5),
            "ai_negative_example_count": request_payload.get("ai_negative_example_count", 3),
            "prefer_reuse_existing_ai": request_payload.get("prefer_reuse_existing_ai", True),
            "call_budget": call_budget,
            "cost_budget": cost_budget,
        },
    )
    db.add(job)
    db.flush()
    log_audit_event(db, "generation.job_created", "generation_job", job.id, {"run_now": run_now, "idempotency_key": bool(idempotency_key), "request": request_payload})
    db.commit()
    db.refresh(job)
    if run_now:
        process_generation_job(db, job.id, provider=provider)
        db.refresh(job)
    return generation_job_response(job)


@router.post("/duplicate-check")
def duplicate_check(payload: DuplicateCheckRequest, db: Session = Depends(get_db)) -> dict:
    question_id = payload.question_id or "__candidate_duplicate_check__"
    raw_question = {
        "id": question_id,
        "question_text": payload.stem_md,
        "answer_spec": {
            "status": "verified",
            "answer_type": "text",
            "canonical_answer": "__check__",
            "acceptable_answers": ["__check__"],
            "tolerance": {"type": "exact", "value": 0},
            "unit": None,
            "order_sensitive": True,
            "grading_method": "exact_normalized",
            "verification": {},
        },
        "difficulty_quality_evaluation": {"difficulty_star": 3, "premium": False},
        "primary_path": {},
        "method_tags": [],
        "task_tags": [],
        "structure_tags": [],
        "constraint_tags": [],
    }
    qv2 = map_question_v2(raw_question, "AI生成题")
    duplicate = existing_generation_duplicate(db, question_id, payload.stem_md, qv2)
    rows = list(
        db.scalars(
            select(Question)
            .where(
                Question.id != question_id,
                Question.status == "verified",
                Question.eligible_for_practice.is_(True),
                Question.publish_status.not_in(("batch_rejected_similar", "quarantined", "retired", "duplicate_alias", "intervention_only")),
            )
            .order_by(Question.id)
            .limit(5000)
        )
    )
    comparisons = []
    for row in rows:
        report = similarity_report(row.stem_preview, payload.stem_md)
        if report.get("source_similarity_risk") != "low" or report.get("semantic_similarity", 0) >= 0.5:
            comparisons.append({"question_id": row.id, "source_name": row.source_name, "stem_preview": row.stem_preview[:240], **report})
    comparisons.sort(key=lambda item: (item.get("source_similarity_risk") != "high", -float(item.get("semantic_similarity", 0))))
    return {
        "risk": "high" if duplicate else (comparisons[0]["source_similarity_risk"] if comparisons else "low"),
        "duplicate": duplicate,
        "top_matches": comparisons[:10],
        "checked_questions": len(rows),
    }


@router.get("/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if mark_stale_job_if_needed(db, job):
        db.commit()
    candidates = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job_id)))
    return {
        "id": job.id,
        "status": job.status,
        "request": job.request,
        "progress": job.progress,
        "accepted_question_id": job.accepted_question_id,
        "candidates": [{"id": c.id, "strategy": c.strategy, "status": c.status, "score": c.score, "review": c.review} for c in candidates],
    }


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status not in ACTIVE_TASK_STATUSES:
        raise HTTPException(status_code=409, detail=f"cannot cancel job in status {job.status}")
    set_generation_job_status(job, STATUS_CANCELLED)
    job.progress = {**(job.progress or {}), "stage": STATUS_CANCELLED, "cancelled": True}
    log_audit_event(db, "generation.job_cancelled", "generation_job", job.id, {"previous_status": "running_or_queued"})
    db.commit()
    return {"id": job.id, "status": job.status, "progress": job.progress}


@router.get("/{job_id}/retry-preview")
def retry_job_preview(job_id: str, db: Session = Depends(get_db)) -> dict:
    parent = db.get(GenerationJob, job_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="job not found")
    retryable_statuses = {STATUS_FAILED, STATUS_REJECTED, STATUS_CANCELLED, STATUS_TIMED_OUT}
    if parent.status not in retryable_statuses:
        return {
            "job_id": parent.id,
            "status": parent.status,
            "retryable": False,
            "retry_exhausted": False,
            "reason": f"cannot retry job in status {parent.status}",
            "regeneration_attempt": None,
            "max_regeneration_attempts": MAX_REGENERATION_ATTEMPTS,
            "regeneration_reason_codes": [],
            "regeneration_constraints": [],
        }
    retry_request, retry_progress = build_regeneration_retry(parent, db)
    attempt = int(retry_progress["regeneration_attempt"])
    retry_exhausted = attempt > MAX_REGENERATION_ATTEMPTS
    return {
        "job_id": parent.id,
        "status": parent.status,
        "retryable": not retry_exhausted,
        "retry_exhausted": retry_exhausted,
        "reason": "regeneration_attempt_limit_reached" if retry_exhausted else None,
        "regeneration_attempt": attempt,
        "max_regeneration_attempts": MAX_REGENERATION_ATTEMPTS,
        "regeneration_reason_codes": retry_progress["regeneration_reason_codes"],
        "regeneration_constraints": retry_progress["regeneration_constraints"],
        "prefer_reuse_existing_ai": retry_request.get("prefer_reuse_existing_ai"),
        "regeneration_mode": retry_request.get("regeneration_mode"),
    }


@router.post("/{job_id}/retry", response_model=GenerationJobResponse)
def retry_job(job_id: str, payload: RetryGenerationJobRequest | None = None, db: Session = Depends(get_db)) -> GenerationJobResponse:
    payload = payload or RetryGenerationJobRequest()
    parent = db.get(GenerationJob, job_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="job not found")
    if parent.status not in {STATUS_FAILED, STATUS_REJECTED, STATUS_CANCELLED, STATUS_TIMED_OUT}:
        raise HTTPException(status_code=409, detail=f"cannot retry job in status {parent.status}")
    if payload.idempotency_key:
        existing = db.scalar(select(GenerationJob).where(GenerationJob.idempotency_key == payload.idempotency_key))
        if existing is not None:
            expected_language = payload.output_language or current_language()
            stored_language = (existing.request or {}).get("output_language") or (existing.request or {}).get("language")
            if stored_language != expected_language:
                raise HTTPException(status_code=409, detail="idempotency_output_language_conflict")
            return generation_job_response(existing)
    retry_request, retry_progress = build_regeneration_retry(parent, db)
    retry_request["allow_budget_overrun"] = bool(payload.allow_budget_overrun or retry_request.get("allow_budget_overrun"))
    retry_request["allow_reviewer_quality_downgrade"] = bool(payload.allow_reviewer_quality_downgrade or retry_request.get("allow_reviewer_quality_downgrade"))
    retry_progress["allow_budget_overrun"] = retry_request["allow_budget_overrun"]
    retry_progress["allow_reviewer_quality_downgrade"] = retry_request["allow_reviewer_quality_downgrade"]
    settings = get_settings()
    provider = str((parent.request or {}).get("job_provider") or (parent.request or {}).get("batch_provider") or "mock")
    call_budget = generation_call_budget_snapshot(retry_request, 1, "job", settings)
    enforce_generation_call_budget(call_budget)
    retry_exhausted = retry_progress["regeneration_attempt"] > MAX_REGENERATION_ATTEMPTS
    cost_budget = ai_cost_budget_snapshot(db, settings, provider, call_budget, retry_request)
    if not retry_exhausted:
        enforce_ai_cost_budget(cost_budget, allow_overrun=bool(retry_request.get("allow_budget_overrun")))
    retry_request = {**retry_request, "cost_budget": cost_budget}
    retry_progress = {**retry_progress, "call_budget": call_budget, "cost_budget": cost_budget}
    job = GenerationJob(
        idempotency_key=payload.idempotency_key,
        status=STATUS_REJECTED if retry_exhausted else STATUS_QUEUED,
        request=retry_request,
        progress={
            **retry_progress,
            **(
                {
                    "stage": "done",
                    "accepted": False,
                    "terminal_reason": "regeneration_attempt_limit_reached",
                    "retry_budget_exhausted": True,
                }
                if retry_exhausted
                else {}
            ),
        },
    )
    db.add(job)
    db.flush()
    log_audit_event(
        db,
        "generation.job_retried",
        "generation_job",
        job.id,
        {
            "parent_job_id": parent.id,
            "run_now": payload.run_now and not retry_exhausted,
            "idempotency_key": bool(payload.idempotency_key),
            "regeneration_attempt": retry_progress["regeneration_attempt"],
            "retry_budget_exhausted": retry_exhausted,
            "regeneration_reason_codes": retry_progress["regeneration_reason_codes"],
        },
    )
    db.commit()
    db.refresh(job)
    if payload.run_now and not retry_exhausted:
        process_generation_job(db, job.id, provider=provider)
        db.refresh(job)
    return generation_job_response(job)


@router.get("/{job_id}/coverage-review")
def coverage_review(job_id: str, db: Session = Depends(get_db)) -> dict:
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    candidates = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job_id).order_by(GeneratedCandidate.strategy, GeneratedCandidate.id)))
    return {"job_id": job_id, **candidate_set_review(candidates)}


@router.post("/{job_id}/candidates/{candidate_id}/review-actions")
def review_candidate_action(job_id: str, candidate_id: str, payload: CandidateReviewActionRequest, db: Session = Depends(get_db)) -> dict:
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    candidate = db.get(GeneratedCandidate, candidate_id)
    if candidate is None or candidate.job_id != job_id:
        raise HTTPException(status_code=404, detail="candidate not found")
    original_payload = candidate.payload or {}
    edited_payload = payload.edited_payload if payload.edited_payload is not None else original_payload
    changes = diff_payload(original_payload, edited_payload)
    previous_status = candidate.status
    if payload.action == "edit":
        candidate.payload = edited_payload
        candidate.status = "edited"
    elif payload.action == "approve":
        candidate.status = "approved"
        if job.accepted_question_id is None:
            job.progress = {**(job.progress or {}), "manual_approved_candidate_id": candidate.id}
    else:
        candidate.status = "manual_rejected"
    review = GeneratedCandidateReview(
        job_id=job_id,
        candidate_id=candidate_id,
        action=payload.action,
        status=candidate.status,
        payload={"previous_status": previous_status, "edited_payload": edited_payload},
        diff={"changes": changes, "change_count": len(changes)},
        reviewer_notes=payload.reviewer_notes,
    )
    db.add(review)
    db.commit()
    return {
        "id": review.id,
        "candidate_id": candidate.id,
        "action": review.action,
        "status": review.status,
        "diff": review.diff,
        "reviewer_notes": review.reviewer_notes,
    }


def candidate_sse_payload(candidate: GeneratedCandidate) -> dict:
    return {
        "id": candidate.id,
        "job_id": candidate.job_id,
        "strategy": candidate.strategy,
        "status": candidate.status,
        "score": candidate.score,
        "review": candidate.review,
        "published_question_id": candidate.published_question_id,
        "publish_status": candidate.publish_status,
    }


def ai_log_sse_payload(log: AiRunLog) -> dict:
    return {
        "id": log.id,
        "job_id": log.job_id,
        "role": log.role,
        "provider": log.provider,
        "model": log.model,
        "error": log.error,
        "latency_ms": log.latency_ms,
        "token_usage": log.token_usage,
        "created_at": progress_timestamp(log.created_at),
    }


def snapshot_job_sse_events(db: Session, job: GenerationJob, last_event_id: str | None = None) -> list[dict]:
    if mark_stale_job_if_needed(db, job):
        db.flush()
    events = []
    candidates = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job.id).order_by(GeneratedCandidate.strategy, GeneratedCandidate.id)))
    logs = list(db.scalars(select(AiRunLog).where(AiRunLog.job_id == job.id).order_by(AiRunLog.created_at, AiRunLog.id)))

    for index, candidate in enumerate(candidates, start=1):
        event_time = candidate.published_at or job.updated_at or job.created_at
        event_id = sse_event_id(event_time, 20, candidate.status, index)
        if sse_should_emit(event_id, last_event_id):
            events.append({"id": event_id, "event": "candidate", "data": candidate_sse_payload(candidate)})

    for index, log in enumerate(logs, start=1):
        event_id = sse_event_id(log.created_at, 30, "failed" if log.error else "accepted", index)
        if sse_should_emit(event_id, last_event_id):
            events.append({"id": event_id, "event": "ai_log", "data": ai_log_sse_payload(log)})

    job_event_id = sse_event_id(job.updated_at or job.created_at, 40, job.status)
    if sse_should_emit(job_event_id, last_event_id):
        events.append({"id": job_event_id, "event": "job", "data": {"id": job.id, "status": job.status, "progress": job.progress, "accepted_question_id": job.accepted_question_id}})

    if job.status in TERMINAL_JOB_STATUSES:
        done_id = sse_event_id(sse_latest_time(job.updated_at, job.created_at), 90, job.status)
        if sse_should_emit(done_id, last_event_id):
            events.append({"id": done_id, "event": "done", "data": {"job_id": job.id, "status": job.status}})

    return sorted(events, key=lambda row: sse_event_key(row["id"]))


def job_sse_events(db: Session, job: GenerationJob, last_event_id: str | None = None) -> list[dict]:
    persisted = stored_generation_events(db, "job", job.id, last_event_id)
    snapshot = snapshot_job_sse_events(db, job, last_event_id)
    return merge_sse_events(persisted, snapshot)


@router.get("/{job_id}/events")
def job_events(job_id: str, last_event_id: str | None = Header(default=None, alias="Last-Event-ID"), db: Session = Depends(get_db)) -> StreamingResponse:
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    events = job_sse_events(db, job, last_event_id)
    if db.dirty:
        db.commit()

    def stream():
        for event in events:
            yield sse_format(event["event"], event["data"], event["id"])

    return StreamingResponse(stream(), media_type="text/event-stream")
