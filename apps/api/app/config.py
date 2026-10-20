import os
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def project_root() -> Path:
    explicit_root = os.getenv("MATH_PLATFORM_ROOT")
    if explicit_root:
        return Path(explicit_root).resolve()
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / "做题项目").exists() and (candidate / "apps").exists():
            return candidate
    return here


def env_file_path() -> Path:
    root = project_root()
    if os.getenv("MATH_PLATFORM_ROOT"):
        return root / ".env"
    for candidate in (root / ".env", Path.cwd() / ".env"):
        if candidate.exists():
            return candidate
    return root / ".env"


def resolve_project_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return project_root() / path


def _int_env(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _truthy_env(key: str) -> bool:
    return os.getenv(key, "").strip().lower() in {"1", "true", "yes", "on"}


PRESERVED_PROCESS_ENV_KEYS = {
    "DATABASE_URL",
    "API_REQUEST_LOG_PATH",
}


class Settings(BaseModel):
    app_name: str = "数学出题与学情诊断平台"
    database_url: str = "postgresql+psycopg://math:math@localhost:5432/math_platform"
    redis_url: str = "redis://localhost:6379/0"
    question_data_dir: Path = Path("做题项目")
    liaobots_base_url: str = "https://api.liaobots.work/v1"
    liaobots_api_key: str | None = None
    responses_base_url: str = "https://api.openai.com/v1"
    responses_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    anthropic_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_api_key: str | None = None
    default_model: str = "gpt-5.6-terra"
    model_provider: str = "openai_compatible"
    planner_model: str | None = None
    generator_model: str | None = None
    generator_model_le_3: str | None = "gpt-5.6-terra"
    generator_model_4: str | None = "gpt-5.6-sol"
    generator_model_5: str | None = "gpt-5.6-sol"
    reviewer_model: str | None = None
    secondary_reviewer_model: str | None = None
    reporter_model: str | None = None
    diagnostician_model: str | None = None
    hint_generator_model: str | None = None
    hint_reviewer_model: str | None = None
    fault_auditor_model: str | None = None
    set_reviewer_model: str | None = None
    answer_judge_model: str | None = None
    solution_explainer_model: str | None = None
    question_repair_reviewer_model: str | None = None
    emergency_recovery_agent_model: str | None = "gpt-5.6-sol"
    project_assistant_model: str | None = "gpt-5.6-sol"
    ai_monthly_budget_usd: float = 0.0
    ai_daily_budget_usd: float = 0.0
    ai_model_price_json: str = ""
    ai_strict_reviewer_model_allowlist: str = ""
    ai_estimated_generator_input_tokens_per_call: int = 2500
    ai_estimated_generator_output_tokens_per_call: int = 1200
    ai_estimated_reviewer_input_tokens_per_call: int = 2000
    ai_estimated_reviewer_output_tokens_per_call: int = 800
    ai_interaction_timeout_seconds: int = 60
    ai_generator_timeout_seconds: int = 180
    ai_reviewer_timeout_seconds: int = 120
    ai_job_timeout_seconds: int = 420
    ai_global_max_concurrency: int = 6
    ai_emergency_agent_max_concurrency: int = 1
    ai_max_generation_calls_per_job: int = 5
    ai_max_reviewer_calls_per_job: int = 5
    ai_max_generation_calls_per_batch: int = 500
    ai_max_reviewer_calls_per_batch: int = 500
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    api_request_log_path: str = "logs/api.jsonl"
    api_request_log_max_bytes: int = 1048576
    api_request_log_backup_count: int = 5
    api_request_log_retention_days: int = 14
    api_slow_request_ms: int = 2000


@lru_cache
def get_settings() -> Settings:
    env_file = env_file_path()
    preserve_process_env = _truthy_env("MATH_PLATFORM_PRESERVE_PROCESS_ENV")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            env_key = key.strip().lstrip("\ufeff")
            if preserve_process_env and env_key in PRESERVED_PROCESS_ENV_KEYS and env_key in os.environ:
                continue
            os.environ[env_key] = value.strip().strip('"')
    return Settings(
        app_name=os.getenv("APP_NAME", Settings.model_fields["app_name"].default),
        database_url=os.getenv("DATABASE_URL", Settings.model_fields["database_url"].default),
        redis_url=os.getenv("REDIS_URL", Settings.model_fields["redis_url"].default),
        question_data_dir=resolve_project_path(os.getenv("QUESTION_DATA_DIR", str(Settings.model_fields["question_data_dir"].default))),
        liaobots_base_url=os.getenv("LIAOBOTS_BASE_URL", Settings.model_fields["liaobots_base_url"].default),
        liaobots_api_key=os.getenv("LIAOBOTS_API_KEY"),
        responses_base_url=os.getenv("RESPONSES_BASE_URL", Settings.model_fields["responses_base_url"].default),
        responses_api_key=os.getenv("RESPONSES_API_KEY"),
        anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL", Settings.model_fields["anthropic_base_url"].default),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        gemini_base_url=os.getenv("GEMINI_BASE_URL", Settings.model_fields["gemini_base_url"].default),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        default_model=os.getenv("DEFAULT_MODEL", Settings.model_fields["default_model"].default),
        model_provider=os.getenv("MODEL_PROVIDER", Settings.model_fields["model_provider"].default),
        planner_model=os.getenv("PLANNER_MODEL"),
        generator_model=os.getenv("GENERATOR_MODEL"),
        generator_model_le_3=os.getenv("GENERATOR_MODEL_LE_3", Settings.model_fields["generator_model_le_3"].default),
        generator_model_4=os.getenv("GENERATOR_MODEL_4", Settings.model_fields["generator_model_4"].default),
        generator_model_5=os.getenv("GENERATOR_MODEL_5", Settings.model_fields["generator_model_5"].default),
        reviewer_model=os.getenv("REVIEWER_MODEL"),
        secondary_reviewer_model=os.getenv("SECONDARY_REVIEWER_MODEL"),
        reporter_model=os.getenv("REPORTER_MODEL"),
        diagnostician_model=os.getenv("DIAGNOSTICIAN_MODEL"),
        hint_generator_model=os.getenv("HINT_GENERATOR_MODEL"),
        hint_reviewer_model=os.getenv("HINT_REVIEWER_MODEL"),
        fault_auditor_model=os.getenv("FAULT_AUDITOR_MODEL"),
        set_reviewer_model=os.getenv("SET_REVIEWER_MODEL"),
        answer_judge_model=os.getenv("ANSWER_JUDGE_MODEL"),
        solution_explainer_model=os.getenv("SOLUTION_EXPLAINER_MODEL"),
        question_repair_reviewer_model=os.getenv("QUESTION_REPAIR_REVIEWER_MODEL"),
        emergency_recovery_agent_model=os.getenv("EMERGENCY_RECOVERY_AGENT_MODEL", Settings.model_fields["emergency_recovery_agent_model"].default),
        project_assistant_model=os.getenv("PROJECT_ASSISTANT_MODEL", Settings.model_fields["project_assistant_model"].default),
        ai_monthly_budget_usd=float(os.getenv("AI_MONTHLY_BUDGET_USD", Settings.model_fields["ai_monthly_budget_usd"].default)),
        ai_daily_budget_usd=float(os.getenv("AI_DAILY_BUDGET_USD", Settings.model_fields["ai_daily_budget_usd"].default)),
        ai_model_price_json=os.getenv("AI_MODEL_PRICE_JSON", Settings.model_fields["ai_model_price_json"].default),
        ai_strict_reviewer_model_allowlist=os.getenv("AI_STRICT_REVIEWER_MODEL_ALLOWLIST", Settings.model_fields["ai_strict_reviewer_model_allowlist"].default),
        ai_estimated_generator_input_tokens_per_call=_int_env("AI_ESTIMATED_GENERATOR_INPUT_TOKENS_PER_CALL", Settings.model_fields["ai_estimated_generator_input_tokens_per_call"].default),
        ai_estimated_generator_output_tokens_per_call=_int_env("AI_ESTIMATED_GENERATOR_OUTPUT_TOKENS_PER_CALL", Settings.model_fields["ai_estimated_generator_output_tokens_per_call"].default),
        ai_estimated_reviewer_input_tokens_per_call=_int_env("AI_ESTIMATED_REVIEWER_INPUT_TOKENS_PER_CALL", Settings.model_fields["ai_estimated_reviewer_input_tokens_per_call"].default),
        ai_estimated_reviewer_output_tokens_per_call=_int_env("AI_ESTIMATED_REVIEWER_OUTPUT_TOKENS_PER_CALL", Settings.model_fields["ai_estimated_reviewer_output_tokens_per_call"].default),
        ai_interaction_timeout_seconds=_int_env("AI_INTERACTION_TIMEOUT_SECONDS", Settings.model_fields["ai_interaction_timeout_seconds"].default),
        ai_generator_timeout_seconds=_int_env("AI_GENERATOR_TIMEOUT_SECONDS", Settings.model_fields["ai_generator_timeout_seconds"].default),
        ai_reviewer_timeout_seconds=_int_env("AI_REVIEWER_TIMEOUT_SECONDS", Settings.model_fields["ai_reviewer_timeout_seconds"].default),
        ai_job_timeout_seconds=_int_env("AI_JOB_TIMEOUT_SECONDS", Settings.model_fields["ai_job_timeout_seconds"].default),
        ai_global_max_concurrency=_int_env("AI_GLOBAL_MAX_CONCURRENCY", Settings.model_fields["ai_global_max_concurrency"].default),
        ai_emergency_agent_max_concurrency=_int_env("AI_EMERGENCY_AGENT_MAX_CONCURRENCY", Settings.model_fields["ai_emergency_agent_max_concurrency"].default),
        ai_max_generation_calls_per_job=_int_env("AI_MAX_GENERATION_CALLS_PER_JOB", Settings.model_fields["ai_max_generation_calls_per_job"].default),
        ai_max_reviewer_calls_per_job=_int_env("AI_MAX_REVIEWER_CALLS_PER_JOB", Settings.model_fields["ai_max_reviewer_calls_per_job"].default),
        ai_max_generation_calls_per_batch=_int_env("AI_MAX_GENERATION_CALLS_PER_BATCH", Settings.model_fields["ai_max_generation_calls_per_batch"].default),
        ai_max_reviewer_calls_per_batch=_int_env("AI_MAX_REVIEWER_CALLS_PER_BATCH", Settings.model_fields["ai_max_reviewer_calls_per_batch"].default),
        cors_origins=os.getenv("CORS_ORIGINS", Settings.model_fields["cors_origins"].default),
        api_request_log_path=os.getenv("API_REQUEST_LOG_PATH", Settings.model_fields["api_request_log_path"].default),
        api_request_log_max_bytes=_int_env("API_REQUEST_LOG_MAX_BYTES", Settings.model_fields["api_request_log_max_bytes"].default),
        api_request_log_backup_count=_int_env("API_REQUEST_LOG_BACKUP_COUNT", Settings.model_fields["api_request_log_backup_count"].default),
        api_request_log_retention_days=_int_env("API_REQUEST_LOG_RETENTION_DAYS", Settings.model_fields["api_request_log_retention_days"].default),
        api_slow_request_ms=_int_env("API_SLOW_REQUEST_MS", Settings.model_fields["api_slow_request_ms"].default),
    )


ENV_KEY_ORDER = [
    "APP_NAME",
    "DATABASE_URL",
    "REDIS_URL",
    "QUESTION_DATA_DIR",
    "CORS_ORIGINS",
    "LIAOBOTS_BASE_URL",
    "LIAOBOTS_API_KEY",
    "RESPONSES_BASE_URL",
    "RESPONSES_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY",
    "GEMINI_BASE_URL",
    "GEMINI_API_KEY",
    "MODEL_PROVIDER",
    "DEFAULT_MODEL",
    "PLANNER_MODEL",
    "GENERATOR_MODEL",
    "GENERATOR_MODEL_LE_3",
    "GENERATOR_MODEL_4",
    "GENERATOR_MODEL_5",
    "REVIEWER_MODEL",
    "SECONDARY_REVIEWER_MODEL",
    "REPORTER_MODEL",
    "DIAGNOSTICIAN_MODEL",
    "HINT_GENERATOR_MODEL",
    "HINT_REVIEWER_MODEL",
    "FAULT_AUDITOR_MODEL",
    "SET_REVIEWER_MODEL",
    "ANSWER_JUDGE_MODEL",
    "SOLUTION_EXPLAINER_MODEL",
    "QUESTION_REPAIR_REVIEWER_MODEL",
    "EMERGENCY_RECOVERY_AGENT_MODEL",
    "PROJECT_ASSISTANT_MODEL",
    "AI_STRICT_REVIEWER_MODEL_ALLOWLIST",
    "AI_MONTHLY_BUDGET_USD",
    "AI_DAILY_BUDGET_USD",
    "AI_MODEL_PRICE_JSON",
    "AI_ESTIMATED_GENERATOR_INPUT_TOKENS_PER_CALL",
    "AI_ESTIMATED_GENERATOR_OUTPUT_TOKENS_PER_CALL",
    "AI_ESTIMATED_REVIEWER_INPUT_TOKENS_PER_CALL",
    "AI_ESTIMATED_REVIEWER_OUTPUT_TOKENS_PER_CALL",
    "AI_INTERACTION_TIMEOUT_SECONDS",
    "AI_GENERATOR_TIMEOUT_SECONDS",
    "AI_REVIEWER_TIMEOUT_SECONDS",
    "AI_JOB_TIMEOUT_SECONDS",
    "AI_GLOBAL_MAX_CONCURRENCY",
    "AI_EMERGENCY_AGENT_MAX_CONCURRENCY",
    "AI_MAX_GENERATION_CALLS_PER_JOB",
    "AI_MAX_REVIEWER_CALLS_PER_JOB",
    "AI_MAX_GENERATION_CALLS_PER_BATCH",
    "AI_MAX_REVIEWER_CALLS_PER_BATCH",
    "API_REQUEST_LOG_PATH",
    "API_REQUEST_LOG_MAX_BYTES",
    "API_REQUEST_LOG_BACKUP_COUNT",
    "API_REQUEST_LOG_RETENTION_DAYS",
    "API_SLOW_REQUEST_MS",
]

CONFIG_SCHEMA_VERSION = "runtime_config_v1"

SETTING_ENV_KEYS = {
    "app_name": "APP_NAME",
    "database_url": "DATABASE_URL",
    "redis_url": "REDIS_URL",
    "question_data_dir": "QUESTION_DATA_DIR",
    "liaobots_base_url": "LIAOBOTS_BASE_URL",
    "liaobots_api_key": "LIAOBOTS_API_KEY",
    "responses_base_url": "RESPONSES_BASE_URL",
    "responses_api_key": "RESPONSES_API_KEY",
    "anthropic_base_url": "ANTHROPIC_BASE_URL",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "gemini_base_url": "GEMINI_BASE_URL",
    "gemini_api_key": "GEMINI_API_KEY",
    "default_model": "DEFAULT_MODEL",
    "model_provider": "MODEL_PROVIDER",
    "planner_model": "PLANNER_MODEL",
    "generator_model": "GENERATOR_MODEL",
    "generator_model_le_3": "GENERATOR_MODEL_LE_3",
    "generator_model_4": "GENERATOR_MODEL_4",
    "generator_model_5": "GENERATOR_MODEL_5",
    "reviewer_model": "REVIEWER_MODEL",
    "secondary_reviewer_model": "SECONDARY_REVIEWER_MODEL",
    "reporter_model": "REPORTER_MODEL",
    "diagnostician_model": "DIAGNOSTICIAN_MODEL",
    "hint_generator_model": "HINT_GENERATOR_MODEL",
    "hint_reviewer_model": "HINT_REVIEWER_MODEL",
    "fault_auditor_model": "FAULT_AUDITOR_MODEL",
    "set_reviewer_model": "SET_REVIEWER_MODEL",
    "answer_judge_model": "ANSWER_JUDGE_MODEL",
    "solution_explainer_model": "SOLUTION_EXPLAINER_MODEL",
    "question_repair_reviewer_model": "QUESTION_REPAIR_REVIEWER_MODEL",
    "emergency_recovery_agent_model": "EMERGENCY_RECOVERY_AGENT_MODEL",
    "project_assistant_model": "PROJECT_ASSISTANT_MODEL",
    "ai_monthly_budget_usd": "AI_MONTHLY_BUDGET_USD",
    "ai_daily_budget_usd": "AI_DAILY_BUDGET_USD",
    "ai_model_price_json": "AI_MODEL_PRICE_JSON",
    "ai_strict_reviewer_model_allowlist": "AI_STRICT_REVIEWER_MODEL_ALLOWLIST",
    "ai_estimated_generator_input_tokens_per_call": "AI_ESTIMATED_GENERATOR_INPUT_TOKENS_PER_CALL",
    "ai_estimated_generator_output_tokens_per_call": "AI_ESTIMATED_GENERATOR_OUTPUT_TOKENS_PER_CALL",
    "ai_estimated_reviewer_input_tokens_per_call": "AI_ESTIMATED_REVIEWER_INPUT_TOKENS_PER_CALL",
    "ai_estimated_reviewer_output_tokens_per_call": "AI_ESTIMATED_REVIEWER_OUTPUT_TOKENS_PER_CALL",
    "ai_interaction_timeout_seconds": "AI_INTERACTION_TIMEOUT_SECONDS",
    "ai_generator_timeout_seconds": "AI_GENERATOR_TIMEOUT_SECONDS",
    "ai_reviewer_timeout_seconds": "AI_REVIEWER_TIMEOUT_SECONDS",
    "ai_job_timeout_seconds": "AI_JOB_TIMEOUT_SECONDS",
    "ai_global_max_concurrency": "AI_GLOBAL_MAX_CONCURRENCY",
    "ai_emergency_agent_max_concurrency": "AI_EMERGENCY_AGENT_MAX_CONCURRENCY",
    "ai_max_generation_calls_per_job": "AI_MAX_GENERATION_CALLS_PER_JOB",
    "ai_max_reviewer_calls_per_job": "AI_MAX_REVIEWER_CALLS_PER_JOB",
    "ai_max_generation_calls_per_batch": "AI_MAX_GENERATION_CALLS_PER_BATCH",
    "ai_max_reviewer_calls_per_batch": "AI_MAX_REVIEWER_CALLS_PER_BATCH",
    "cors_origins": "CORS_ORIGINS",
    "api_request_log_path": "API_REQUEST_LOG_PATH",
    "api_request_log_max_bytes": "API_REQUEST_LOG_MAX_BYTES",
    "api_request_log_backup_count": "API_REQUEST_LOG_BACKUP_COUNT",
    "api_request_log_retention_days": "API_REQUEST_LOG_RETENTION_DAYS",
    "api_slow_request_ms": "API_SLOW_REQUEST_MS",
}

SECRET_SETTING_NAMES = {"liaobots_api_key", "responses_api_key", "anthropic_api_key", "gemini_api_key"}

SETTING_CATEGORIES = {
    "installation": {"app_name", "database_url", "redis_url", "question_data_dir", "cors_origins"},
    "provider_secret": SECRET_SETTING_NAMES,
    "provider_model": {
        "liaobots_base_url",
        "responses_base_url",
        "anthropic_base_url",
        "gemini_base_url",
        "default_model",
        "model_provider",
        "planner_model",
        "generator_model",
        "generator_model_le_3",
        "generator_model_4",
        "generator_model_5",
        "reviewer_model",
        "secondary_reviewer_model",
        "reporter_model",
        "diagnostician_model",
        "hint_generator_model",
        "hint_reviewer_model",
        "fault_auditor_model",
        "set_reviewer_model",
        "answer_judge_model",
        "solution_explainer_model",
        "question_repair_reviewer_model",
        "emergency_recovery_agent_model",
        "project_assistant_model",
        "ai_strict_reviewer_model_allowlist",
    },
    "ai_budget": {
        "ai_monthly_budget_usd",
        "ai_daily_budget_usd",
        "ai_model_price_json",
        "ai_estimated_generator_input_tokens_per_call",
        "ai_estimated_generator_output_tokens_per_call",
        "ai_estimated_reviewer_input_tokens_per_call",
        "ai_estimated_reviewer_output_tokens_per_call",
        "ai_global_max_concurrency",
        "ai_emergency_agent_max_concurrency",
        "ai_max_generation_calls_per_job",
        "ai_max_reviewer_calls_per_job",
        "ai_max_generation_calls_per_batch",
        "ai_max_reviewer_calls_per_batch",
    },
    "timeouts": {"ai_interaction_timeout_seconds", "ai_generator_timeout_seconds", "ai_reviewer_timeout_seconds", "ai_job_timeout_seconds"},
    "logging": {"api_request_log_path", "api_request_log_max_bytes", "api_request_log_backup_count", "api_request_log_retention_days", "api_slow_request_ms"},
}

FEATURE_FLAGS = {
    "ai_generation": {"enabled": True, "description": "AI single-question and batch generation endpoints are enabled."},
    "ai_reuse_existing_questions": {"enabled": True, "description": "AI generation can reuse unused same-star AI questions before calling a provider."},
    "strict_test": {"enabled": True, "description": "Strict test sessions are enabled and hide help/reveal actions."},
    "history_review": {"enabled": True, "description": "Historical groups can create independent review_practice sessions."},
    "mastery_today_tasks": {"enabled": True, "description": "Rule-based mastery profile and today task cards are enabled."},
    "question_repair_workflow": {"enabled": True, "description": "Question repair, duplicate alias and regrade-preview workflows are enabled."},
    "provider_contract_v1": {"enabled": True, "description": "Provider capability, health and normalized error contracts are enabled."},
    "health_detail_v1": {"enabled": True, "description": "Layered detailed health endpoint is enabled."},
    "release_manifest_verification": {"enabled": True, "description": "Release manifest verification and diagnostic export summary are enabled."},
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return value


def _setting_category(name: str) -> str:
    for category, names in SETTING_CATEGORIES.items():
        if name in names:
            return category
    return "other"


def runtime_config_snapshot(settings: Settings | None = None) -> dict[str, Any]:
    current_settings = settings or get_settings()
    items = []
    for name, field_info in Settings.model_fields.items():
        env_key = SETTING_ENV_KEYS.get(name)
        current_value = getattr(current_settings, name)
        default_value = field_info.default
        sensitive = name in SECRET_SETTING_NAMES
        configured = bool(current_value) if sensitive else bool(env_key and env_key in os.environ)
        source = "configured" if configured else "default"
        item = {
            "name": name,
            "env_key": env_key,
            "category": _setting_category(name),
            "source": source,
            "redacted": sensitive,
            "configured": bool(current_value) if sensitive else configured,
            "default": None if sensitive else _jsonable(default_value),
            "value": None if sensitive else _jsonable(current_value),
        }
        items.append(item)
    category_counts: dict[str, int] = {}
    configured_count = 0
    secret_configured_count = 0
    for item in items:
        category_counts[item["category"]] = category_counts.get(item["category"], 0) + 1
        if item["source"] == "configured":
            configured_count += 1
        if item["redacted"] and item["configured"]:
            secret_configured_count += 1
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "setting_count": len(items),
            "configured_count": configured_count,
            "secret_configured_count": secret_configured_count,
            "category_counts": category_counts,
        },
        "settings": items,
        "feature_flags": FEATURE_FLAGS,
    }


def _read_env_map(path: Path) -> dict[str, str]:
    current: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, value = stripped.split("=", 1)
                current[key.strip().lstrip("\ufeff")] = value.strip().strip('"')
    return current


def _write_env_map(path: Path, current: dict[str, str]) -> Path:
    lines = [f"{key}={current[key]}" for key in ENV_KEY_ORDER if key in current]
    lines.extend(f"{key}={value}" for key, value in sorted(current.items()) if key not in ENV_KEY_ORDER)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for key, value in current.items():
        os.environ[key] = value
    get_settings.cache_clear()
    return path


def write_env_updates(updates: dict[str, str]) -> Path:
    path = env_file_path()
    current = _read_env_map(path)
    current.update({key: value for key, value in updates.items() if value is not None})
    return _write_env_map(path, current)


def delete_env_keys(keys: set[str]) -> Path:
    path = env_file_path()
    current = _read_env_map(path)
    for key in keys:
        current.pop(key, None)
        os.environ.pop(key, None)
    return _write_env_map(path, current)
