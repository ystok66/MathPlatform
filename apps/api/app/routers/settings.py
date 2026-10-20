import json
import time
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_providers import model_capabilities, price_table_summary
from ..audit import log_audit_event
from ..config import delete_env_keys, get_settings, project_root, runtime_config_snapshot, write_env_updates
from ..db import get_db
from ..errors import error_catalog
from ..models import AiRunLog, PromptTemplate
from ..prompt_loader import prompt_template
from ..prompt_registry import activate_prompt_version, load_active_prompt_map, sync_prompt_registry
from ..quality_gates import ai_quality_suite_report, run_fixed_quality_cases
from ..secrets import delete_secret, get_secret, has_secret, set_secret
from ..schemas import ModelDiscoveryResponse, ModelSettings, ModelTestResponse, QualityFixedCaseRunRequest
from ..statuses import status_catalog
from ..localization import languages_snapshot
from ..provider_budget import budgeted_post

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/languages")
def get_languages() -> dict:
    return languages_snapshot()


class PromptActivationRequest(BaseModel):
    role: str
    version: str


def normalize_model_price_json(raw: str | None) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_model_price_json",
                "user_message": "价格表 JSON 格式不正确，未保存模型配置。",
                "action": "请填写单个 JSON 对象，例如 {\"default\":{\"input\":0.001,\"output\":0.002}}。",
                "retryable": True,
            },
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_model_price_json",
                "user_message": "价格表必须是 JSON 对象。",
                "action": "请使用 default、model 或 provider:model 作为键，每个值包含 input/output。",
                "retryable": True,
            },
        )
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def model_roles_from_settings(settings) -> dict[str, str]:
    return {
        "planner": settings.planner_model or settings.default_model,
        "generator": settings.generator_model or settings.default_model,
        "reviewer": settings.reviewer_model or settings.default_model,
        "secondary_reviewer": getattr(settings, "secondary_reviewer_model", None) or getattr(settings, "set_reviewer_model", None) or getattr(settings, "reviewer_model", None) or settings.default_model,
        "reporter": settings.reporter_model or settings.default_model,
        "diagnostician": getattr(settings, "diagnostician_model", None) or settings.default_model,
        "hint_generator": getattr(settings, "hint_generator_model", None) or settings.default_model,
        "hint_reviewer": getattr(settings, "hint_reviewer_model", None) or getattr(settings, "reviewer_model", None) or settings.default_model,
        "fault_auditor": getattr(settings, "fault_auditor_model", None) or settings.default_model,
        "set_reviewer": getattr(settings, "set_reviewer_model", None) or settings.default_model,
        "answer_judge": getattr(settings, "answer_judge_model", None) or settings.default_model,
        "solution_explainer": getattr(settings, "solution_explainer_model", None) or settings.default_model,
        "question_repair_reviewer": getattr(settings, "question_repair_reviewer_model", None) or getattr(settings, "reviewer_model", None) or settings.default_model,
        "emergency_recovery_agent": getattr(settings, "emergency_recovery_agent_model", None) or "gpt-5.6-sol",
        "project_assistant": getattr(settings, "project_assistant_model", None) or "gpt-5.6-sol",
    }


def star_generator_models_from_settings(settings) -> dict[str, str]:
    return {
        "le_3": getattr(settings, "generator_model_le_3", None) or "",
        "star_4": getattr(settings, "generator_model_4", None) or "",
        "star_5": getattr(settings, "generator_model_5", None) or "",
    }


def resolve_api_key(payload: ModelSettings, db: Session) -> str | None:
    return payload.api_key or get_secret(db, "LIAOBOTS_API_KEY") or get_settings().liaobots_api_key


def authorization_headers(api_key: str) -> dict[str, str]:
    return {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}


def parse_model_ids(data) -> list[str]:
    raw_models = data.get("data") if isinstance(data, dict) else data
    models: list[str] = []
    if isinstance(raw_models, list):
        for item in raw_models:
            if isinstance(item, dict) and item.get("id"):
                models.append(str(item["id"]))
            elif isinstance(item, str):
                models.append(item)
    return sorted(set(models))


@router.get("/models", response_model=ModelSettings)
def get_model_settings(db: Session = Depends(get_db)) -> ModelSettings:
    settings = get_settings()
    return ModelSettings(
        provider=settings.model_provider,
        base_url=settings.liaobots_base_url,
        api_key=None,
        has_api_key=bool(settings.liaobots_api_key) or has_secret(db, "LIAOBOTS_API_KEY"),
        default_model=settings.default_model,
        roles=model_roles_from_settings(settings),
        star_generator_models=star_generator_models_from_settings(settings),
        model_price_json=settings.ai_model_price_json,
        strict_reviewer_model_allowlist=settings.ai_strict_reviewer_model_allowlist,
    )


@router.get("/runtime-config")
def get_runtime_config() -> dict:
    return runtime_config_snapshot(get_settings())


@router.put("/models", response_model=ModelSettings)
def update_model_settings(payload: ModelSettings, db: Session = Depends(get_db)) -> ModelSettings:
    price_json = normalize_model_price_json(payload.model_price_json)
    updates = {
        "MODEL_PROVIDER": payload.provider,
        "LIAOBOTS_BASE_URL": payload.base_url,
        "DEFAULT_MODEL": payload.default_model,
        "PLANNER_MODEL": payload.roles.get("planner") or payload.default_model,
        "GENERATOR_MODEL": payload.roles.get("generator") or payload.default_model,
        "GENERATOR_MODEL_LE_3": payload.star_generator_models.get("le_3") or "",
        "GENERATOR_MODEL_4": payload.star_generator_models.get("star_4") or "",
        "GENERATOR_MODEL_5": payload.star_generator_models.get("star_5") or "",
        "REVIEWER_MODEL": payload.roles.get("reviewer") or payload.default_model,
        "SECONDARY_REVIEWER_MODEL": payload.roles.get("secondary_reviewer") or "",
        "REPORTER_MODEL": payload.roles.get("reporter") or payload.default_model,
        "DIAGNOSTICIAN_MODEL": payload.roles.get("diagnostician") or payload.default_model,
        "HINT_GENERATOR_MODEL": payload.roles.get("hint_generator") or payload.default_model,
        "HINT_REVIEWER_MODEL": payload.roles.get("hint_reviewer") or payload.roles.get("reviewer") or payload.default_model,
        "FAULT_AUDITOR_MODEL": payload.roles.get("fault_auditor") or payload.default_model,
        "SET_REVIEWER_MODEL": payload.roles.get("set_reviewer") or payload.default_model,
        "ANSWER_JUDGE_MODEL": payload.roles.get("answer_judge") or payload.default_model,
        "SOLUTION_EXPLAINER_MODEL": payload.roles.get("solution_explainer") or payload.default_model,
        "QUESTION_REPAIR_REVIEWER_MODEL": payload.roles.get("question_repair_reviewer") or payload.roles.get("reviewer") or payload.default_model,
        "EMERGENCY_RECOVERY_AGENT_MODEL": payload.roles.get("emergency_recovery_agent") or "gpt-5.6-sol",
        "PROJECT_ASSISTANT_MODEL": payload.roles.get("project_assistant") or "gpt-5.6-sol",
        "AI_MODEL_PRICE_JSON": price_json,
        "AI_STRICT_REVIEWER_MODEL_ALLOWLIST": payload.strict_reviewer_model_allowlist.strip(),
    }
    if payload.api_key:
        set_secret(db, "LIAOBOTS_API_KEY", payload.api_key, {"provider": payload.provider, "base_url": payload.base_url})
        updates["LIAOBOTS_API_KEY"] = payload.api_key
    write_env_updates(updates)
    db.commit()
    settings = get_settings()
    return ModelSettings(
        provider=settings.model_provider,
        base_url=settings.liaobots_base_url,
        api_key=None,
        has_api_key=bool(settings.liaobots_api_key) or has_secret(db, "LIAOBOTS_API_KEY"),
        default_model=settings.default_model,
        roles=model_roles_from_settings(settings),
        star_generator_models=star_generator_models_from_settings(settings),
        model_price_json=settings.ai_model_price_json,
        strict_reviewer_model_allowlist=settings.ai_strict_reviewer_model_allowlist,
    )


@router.post("/models/discover", response_model=ModelDiscoveryResponse)
def discover_models(payload: ModelSettings, db: Session = Depends(get_db)) -> ModelDiscoveryResponse:
    base_url = (payload.base_url or get_settings().liaobots_base_url).rstrip("/")
    api_key = resolve_api_key(payload, db)
    default_model = payload.default_model or get_settings().default_model
    if not api_key:
        return ModelDiscoveryResponse(
            models=[default_model],
            source=f"{base_url}/models",
            capabilities={default_model: model_capabilities(default_model)},
            error="未配置 API Key，已保留默认模型；填写 API Key 后可拉取模型列表。",
        )
    try:
        response = httpx.get(f"{base_url}/models", headers=authorization_headers(api_key), timeout=8, trust_env=False)
        response.raise_for_status()
        unique_models = parse_model_ids(response.json())
        return ModelDiscoveryResponse(models=unique_models, source=f"{base_url}/models", capabilities={model: model_capabilities(model) for model in unique_models})
    except Exception as exc:
        return ModelDiscoveryResponse(models=[], source=f"{base_url}/models", error=str(exc))


@router.post("/models/test", response_model=ModelTestResponse)
def run_model_settings_test(payload: ModelSettings, db: Session = Depends(get_db)) -> ModelTestResponse:
    started = time.perf_counter()
    settings = get_settings()
    base_url = (payload.base_url or settings.liaobots_base_url).rstrip("/")
    default_model = payload.default_model or settings.default_model
    api_key = resolve_api_key(payload, db)
    source = f"{base_url}/models + {base_url}/chat/completions"
    if not api_key:
        return ModelTestResponse(success=False, source=source, model=default_model, elapsed_ms=0, error="API Key is not configured.")
    models: list[str] = []
    try:
        # Keep the setup probe bounded independently of generation timeouts.
        request_timeout = min(45, max(10, int(getattr(settings, "ai_interaction_timeout_seconds", 30) or 30)))
        model_response = httpx.get(f"{base_url}/models", headers=authorization_headers(api_key), timeout=min(15, request_timeout), trust_env=False)
        model_response.raise_for_status()
        models = parse_model_ids(model_response.json())
        if models and default_model not in models:
            return ModelTestResponse(
                success=False,
                models_ok=True,
                chat_ok=False,
                source=source,
                model=default_model,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                models=models,
                error=f"Selected model is not in provider /models list: {default_model}",
            )
        selected_model = default_model
        chat_response = budgeted_post(
            f"{base_url}/chat/completions",
            headers={**authorization_headers(api_key), "Content-Type": "application/json"},
            json={
                "model": selected_model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "temperature": 0,
                "max_tokens": 64,
            },
            timeout=request_timeout,
            trust_env=False,
        )
        chat_response.raise_for_status()
        raw = chat_response.json()
        content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not content:
            return ModelTestResponse(
                success=False, models_ok=bool(models), chat_ok=False,
                source=source, model=selected_model,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                models=models,
                error="The provider returned no assistant text. Select a chat-capable model or check its output-token requirements.",
            )
        return ModelTestResponse(
            success=True,
            models_ok=True,
            chat_ok=True,
            source=source,
            model=selected_model,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            models=models,
            response_preview=content[:80] or "ok",
        )
    except Exception as exc:
        return ModelTestResponse(
            success=False,
            models_ok=bool(models),
            chat_ok=False,
            source=source,
            model=default_model,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            models=models,
            error=str(exc),
        )


@router.delete("/models/api-key", response_model=ModelSettings)
def clear_model_api_key(db: Session = Depends(get_db)) -> ModelSettings:
    delete_secret(db, "LIAOBOTS_API_KEY")
    delete_env_keys({"LIAOBOTS_API_KEY"})
    db.commit()
    settings = get_settings()
    return ModelSettings(
        provider=settings.model_provider,
        base_url=settings.liaobots_base_url,
        api_key=None,
        has_api_key=False,
        default_model=settings.default_model,
        roles=model_roles_from_settings(settings),
        star_generator_models=star_generator_models_from_settings(settings),
        model_price_json=settings.ai_model_price_json,
        strict_reviewer_model_allowlist=settings.ai_strict_reviewer_model_allowlist,
    )


@router.post("/prompts/sync")
def sync_prompts(db: Session = Depends(get_db)) -> dict:
    rows = sync_prompt_registry(db)
    db.commit()
    return {"count": len(rows), "active_prompts": load_active_prompt_map(), "items": [prompt_payload(row) for row in rows]}


@router.get("/prompts")
def list_prompts(db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(PromptTemplate).order_by(PromptTemplate.role, PromptTemplate.version)))
    return {"active_prompts": load_active_prompt_map(), "items": [prompt_payload(row) for row in rows]}


@router.put("/prompts/active")
def activate_prompt(payload: PromptActivationRequest, db: Session = Depends(get_db)) -> dict:
    try:
        row, active_map = activate_prompt_version(db, payload.role, payload.version)
    except ValueError as exc:
        reason = str(exc)
        status_code = 404 if reason == "prompt_template_not_found" else 400
        raise HTTPException(status_code=status_code, detail=reason) from exc
    prompt_template.cache_clear()
    log_audit_event(db, "settings.prompt_activated", "prompt_template", row.id, {"role": row.role, "version": row.version, "path": row.path})
    db.commit()
    return {"active_prompts": active_map, "active": prompt_payload(row)}


@router.get("/quality-suite")
def get_quality_suite(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    budget = float(settings.ai_monthly_budget_usd or 0) or None
    return ai_quality_suite_report(db, root=project_root(), max_cost_usd=budget)


@router.post("/quality-suite/fixed-cases/run")
def run_quality_fixed_cases(payload: QualityFixedCaseRunRequest, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    budget = float(settings.ai_monthly_budget_usd or 0) or None
    provider = "mock" if payload.use_mock else (payload.provider or settings.model_provider)
    try:
        result = run_fixed_quality_cases(
            db,
            project_root(),
            settings=settings,
            provider=provider,
            use_mock=payload.use_mock,
            suites=payload.suites,
            case_ids=payload.case_ids,
            max_cases=payload.max_cases,
        )
    except ValueError as exc:
        if str(exc) == "real_provider_unavailable_for_quality_suite":
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "REAL_PROVIDER_UNAVAILABLE_FOR_QUALITY_SUITE",
                    "user_message": "真实 provider 不可用，固定质量样例没有执行。",
                    "action": "请先保存 API Key 并用“测试 API”确认可用，或改用 mock 固定样例评测。",
                    "retryable": True,
                },
            ) from exc
        raise
    result["quality_suite"] = ai_quality_suite_report(db, root=project_root(), max_cost_usd=budget)
    log_audit_event(
        db,
        "settings.quality_fixed_cases_run",
        "ai_quality_suite",
        None,
        {
            "use_mock": payload.use_mock,
            "provider": provider,
            "evaluated_count": result.get("evaluated_count"),
            "passed": result.get("passed"),
            "failed": result.get("failed"),
            "unknown": result.get("unknown"),
        },
    )
    db.commit()
    return result


@router.get("/error-codes")
def get_error_codes() -> dict:
    return error_catalog()


@router.get("/status-catalog")
def get_status_catalog() -> dict:
    return status_catalog()


def prompt_payload(row: PromptTemplate) -> dict:
    return {
        "id": row.id,
        "role": row.role,
        "version": row.version,
        "path": row.path,
        "content_hash": row.content_hash,
        "status": row.status,
        "metadata": row.metadata_json,
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("/ai-usage")
def ai_usage(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    logs = list(db.scalars(select(AiRunLog).order_by(AiRunLog.created_at, AiRunLog.id)))
    total_prompt = 0
    total_completion = 0
    total_cost = 0.0
    today_start = datetime.now(UTC).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    today_prompt = 0
    today_completion = 0
    today_cost = 0.0
    today_calls = 0
    by_role: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    for row in logs:
        usage = row.token_usage or {}
        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        cost = float(usage.get("estimated_cost_usd") or 0)
        total_prompt += prompt_tokens
        total_completion += completion_tokens
        total_cost += cost
        if row.created_at >= today_start:
            today_prompt += prompt_tokens
            today_completion += completion_tokens
            today_cost += cost
            today_calls += 1
        for bucket, key in ((by_role, row.role), (by_model, row.model)):
            item = bucket.setdefault(key, {"prompt_tokens": 0, "completion_tokens": 0, "estimated_cost_usd": 0.0, "calls": 0})
            item["prompt_tokens"] += prompt_tokens
            item["completion_tokens"] += completion_tokens
            item["estimated_cost_usd"] = round(item["estimated_cost_usd"] + cost, 6)
            item["calls"] += 1
    budget = float(settings.ai_monthly_budget_usd or 0)
    daily_budget = float(settings.ai_daily_budget_usd or 0)
    return {
        "calls": len(logs),
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "estimated_cost_usd": round(total_cost, 6),
        "budget_usd": budget,
        "remaining_budget_usd": round(max(0.0, budget - total_cost), 6) if budget else None,
        "budget_exceeded": bool(budget and total_cost > budget),
        "daily_budget_usd": daily_budget,
        "price_table": price_table_summary(settings.ai_model_price_json),
        "estimated_tokens_per_call": {
            "generator": {
                "prompt_tokens": int(settings.ai_estimated_generator_input_tokens_per_call or 0),
                "completion_tokens": int(settings.ai_estimated_generator_output_tokens_per_call or 0),
            },
            "reviewer": {
                "prompt_tokens": int(settings.ai_estimated_reviewer_input_tokens_per_call or 0),
                "completion_tokens": int(settings.ai_estimated_reviewer_output_tokens_per_call or 0),
            },
        },
        "today": {
            "window_start_utc": today_start.isoformat(),
            "calls": today_calls,
            "prompt_tokens": today_prompt,
            "completion_tokens": today_completion,
            "estimated_cost_usd": round(today_cost, 6),
            "budget_usd": daily_budget,
            "remaining_budget_usd": round(max(0.0, daily_budget - today_cost), 6) if daily_budget else None,
            "budget_exceeded": bool(daily_budget and today_cost > daily_budget),
        },
        "by_role": by_role,
        "by_model": by_model,
    }
