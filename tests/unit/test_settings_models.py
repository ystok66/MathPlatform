from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app.config import get_settings
from apps.api.app.db import Base
from apps.api.app.ai_gateway import model_roles
from apps.api.app.models import AiRunLog
from apps.api.app.routers.settings import ai_usage, clear_model_api_key, discover_models, get_error_codes, get_quality_suite, get_runtime_config, get_status_catalog, run_model_settings_test, run_quality_fixed_cases, update_model_settings
from apps.api.app.secrets import has_secret, set_secret
from apps.api.app.schemas import ModelSettings, QualityFixedCaseRunRequest


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"data": [{"id": "gpt-4o-mini"}, {"id": "o3-mini"}, {"id": "gpt-4o-mini"}]}


class FakeChatResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "OK"}}], "usage": {"prompt_tokens": 2, "completion_tokens": 1}}


def test_discover_models_returns_capability_profiles(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    calls = []

    def fake_get(url, headers, timeout, trust_env):
        calls.append({"url": url, "headers": headers, "timeout": timeout, "trust_env": trust_env})
        return FakeResponse()

    monkeypatch.setattr("apps.api.app.routers.settings.httpx.get", fake_get)

    result = discover_models(
        ModelSettings(
            provider="openai_compatible",
            base_url="https://provider.example/v1",
            api_key="secret",
            default_model="gpt-4o-mini",
        ),
        db,
    )

    assert result.models == ["gpt-4o-mini", "o3-mini"]
    assert result.capabilities["gpt-4o-mini"]["supports_json"] is True
    assert result.capabilities["o3-mini"]["reasoning_tier"] == "high"
    assert calls[0]["headers"]["Authorization"] == "Bearer secret"
    assert calls[0]["trust_env"] is False


def test_runtime_config_endpoint_returns_redacted_snapshot():
    result = get_runtime_config()
    assert result["schema_version"] == "runtime_config_v1"
    assert "settings" in result
    assert "feature_flags" in result
    assert any(item["name"] == "liaobots_api_key" and item["redacted"] is True and item["value"] is None for item in result["settings"])


def test_model_test_uses_models_and_light_chat(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    calls = []

    def fake_get(url, headers, timeout, trust_env):
        calls.append(("get", url, headers, timeout, trust_env))
        return FakeResponse()

    def fake_post(url, headers, json, timeout, trust_env):
        calls.append(("post", url, headers, json, timeout, trust_env))
        return FakeChatResponse()

    monkeypatch.setattr("apps.api.app.routers.settings.httpx.get", fake_get)
    monkeypatch.setattr("apps.api.app.routers.settings.httpx.post", fake_post)

    result = run_model_settings_test(
        ModelSettings(
            provider="openai_compatible",
            base_url="https://provider.example/v1",
            api_key="secret",
            default_model="o3-mini",
        ),
        db,
    )

    assert result.success is True
    assert result.models_ok is True
    assert result.chat_ok is True
    assert result.model == "o3-mini"
    assert result.response_preview == "OK"
    assert calls[1][3]["max_tokens"] == 3
    assert calls[0][4] is False
    assert calls[1][5] is False


def test_clear_model_api_key_hides_and_deletes_saved_secret(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    set_secret(db, "LIAOBOTS_API_KEY", "real-secret", {"provider": "openai_compatible"}, Fernet.generate_key().decode("ascii"))
    db.commit()
    monkeypatch.setattr("apps.api.app.routers.settings.delete_env_keys", lambda keys: None)

    result = clear_model_api_key(db)

    assert result.api_key is None
    assert result.has_api_key is False
    assert has_secret(db, "LIAOBOTS_API_KEY") is False


def test_model_settings_save_preserves_price_table_json(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setattr("apps.api.app.routers.settings.env_file_path", lambda: Path("__missing_test_env__"), raising=False)
    captured: dict[str, str] = {}

    def fake_write_env_updates(updates):
        captured.update(updates)
        for key, value in updates.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        return Path("__missing_test_env__")

    monkeypatch.setattr("apps.api.app.routers.settings.write_env_updates", fake_write_env_updates)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    price_json = '{"default":{"input":0.01,"output":0.02},"openai_compatible:m":{"input":0.03,"output":0.04}}'

    result = update_model_settings(
        ModelSettings(
            provider="openai_compatible",
            base_url="https://provider.example/v1",
            default_model="m",
            roles={},
            star_generator_models={},
            model_price_json=price_json,
            strict_reviewer_model_allowlist="openai_compatible:approved-reviewer",
        ),
        db,
    )

    assert captured["AI_MODEL_PRICE_JSON"] == price_json
    assert captured["SECONDARY_REVIEWER_MODEL"] == ""
    assert captured["AI_STRICT_REVIEWER_MODEL_ALLOWLIST"] == "openai_compatible:approved-reviewer"
    assert result.model_price_json == price_json
    assert result.strict_reviewer_model_allowlist == "openai_compatible:approved-reviewer"
    get_settings.cache_clear()


def test_model_settings_rejects_invalid_price_json(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)

    try:
        update_model_settings(
            ModelSettings(
                provider="openai_compatible",
                base_url="https://provider.example/v1",
                default_model="m",
                roles={},
                star_generator_models={},
                model_price_json="{bad json",
            ),
            db,
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
        assert exc.detail["error_code"] == "invalid_model_price_json"
    else:
        raise AssertionError("invalid price JSON should be rejected")
    get_settings.cache_clear()


def test_generation_roles_use_role_specific_settings():
    settings = SimpleNamespace(
        planner_model="planner-a",
        generator_model="generator-a",
        reviewer_model="reviewer-a",
        secondary_reviewer_model=None,
        reporter_model=None,
        diagnostician_model="diagnostician-a",
        hint_generator_model="hint-a",
        fault_auditor_model="fault-a",
        set_reviewer_model="set-reviewer-a",
        answer_judge_model="answer-judge-a",
        solution_explainer_model="explainer-a",
    )

    roles = model_roles("default-a", settings)

    assert roles == {
        "default": "default-a",
        "planner": "planner-a",
        "generator": "generator-a",
        "reviewer": "reviewer-a",
        "secondary_reviewer": "set-reviewer-a",
        "reporter": "default-a",
        "diagnostician": "diagnostician-a",
        "hint_generator": "hint-a",
        "fault_auditor": "fault-a",
        "set_reviewer": "set-reviewer-a",
        "answer_judge": "answer-judge-a",
        "solution_explainer": "explainer-a",
        "question_repair_reviewer": "reviewer-a",
    }


def test_ai_usage_summarizes_tokens_cost_and_remaining_budget(monkeypatch):
    monkeypatch.setenv("AI_MONTHLY_BUDGET_USD", "1.0")
    monkeypatch.setenv("AI_DAILY_BUDGET_USD", "0.3")
    monkeypatch.setenv("AI_MODEL_PRICE_JSON", '{"openai_compatible:m1":{"input":0.01,"output":0.02}}')
    get_settings.cache_clear()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    yesterday = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    db.add_all(
        [
            AiRunLog(
                role="generator",
                provider="mock",
                model="m1",
                prompt_version="v1",
                input_hash="a",
                request_payload={},
                response_payload={},
                token_usage={"prompt_tokens": 10, "completion_tokens": 20, "estimated_cost_usd": 0.25},
            ),
            AiRunLog(
                role="reviewer",
                provider="mock",
                model="m1",
                prompt_version="v1",
                input_hash="b",
                request_payload={},
                response_payload={},
                token_usage={"input_tokens": 5, "output_tokens": 7, "estimated_cost_usd": 0.1},
            ),
            AiRunLog(
                role="generator",
                provider="mock",
                model="m2",
                prompt_version="v1",
                input_hash="c",
                request_payload={},
                response_payload={},
                token_usage={"prompt_tokens": 100, "completion_tokens": 200, "estimated_cost_usd": 0.5},
                created_at=yesterday,
            ),
        ]
    )
    db.commit()

    usage = ai_usage(db)

    assert usage["calls"] == 3
    assert usage["prompt_tokens"] == 115
    assert usage["completion_tokens"] == 227
    assert usage["estimated_cost_usd"] == 0.85
    assert usage["remaining_budget_usd"] == 0.15
    assert usage["today"]["calls"] == 2
    assert usage["today"]["estimated_cost_usd"] == 0.35
    assert usage["today"]["remaining_budget_usd"] == 0.0
    assert usage["today"]["budget_exceeded"] is True
    assert usage["price_table"]["entries"]["openai_compatible:m1"] == {"input": 0.01, "output": 0.02}
    assert usage["estimated_tokens_per_call"]["generator"]["prompt_tokens"] == 2500
    assert usage["by_role"]["generator"]["calls"] == 2
    assert usage["by_model"]["m1"]["calls"] == 2


def test_quality_suite_settings_endpoint_returns_schema(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    monkeypatch.setattr("apps.api.app.routers.settings.project_root", lambda: Path("."))

    report = get_quality_suite(db)

    assert report["schema_version"] == "ai_quality_suite_v1"
    assert "overall" in report
    assert {row["name"] for row in report["suites"]} == set(report["suite_names"])


def test_quality_fixed_case_run_settings_endpoint_refreshes_report(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    monkeypatch.setattr("apps.api.app.routers.settings.project_root", lambda: Path("."))

    result = run_quality_fixed_cases(QualityFixedCaseRunRequest(use_mock=True, suites=["answer_judge"], max_cases=1), db)

    assert result["schema_version"] == "fixed_quality_case_run_v1"
    assert result["evaluated_count"] == 1
    assert result["failed"] == 0
    assert result["quality_suite"]["raw_reports"]["fixed_quality_case_evaluations"]["evaluated_case_count"] == 1


def test_error_codes_settings_endpoint_returns_catalog():
    catalog = get_error_codes()
    codes = {row["error_code"] for row in catalog["items"]}

    assert catalog["schema_version"] == "error_catalog_v1"
    assert "STATE_VERSION_CONFLICT" in codes
    assert "CSRF_CROSS_SITE" in codes


def test_status_catalog_settings_endpoint_returns_catalog():
    catalog = get_status_catalog()
    domains = {row["domain"] for row in catalog["domains"]}

    assert catalog["schema_version"] == "status_catalog_v1"
    assert "generation_job" in domains
    assert "question_publish_status" in domains
    assert catalog["status_count"] >= 30
