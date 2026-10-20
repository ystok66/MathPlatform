from pathlib import Path

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.ai_gateway import build_messages, generator_model_for_request, model_roles, process_generation_job, provider_chain
from apps.api.app.ai_providers import AnthropicProvider, GeminiProvider, MockProvider, OpenAICompatibleProvider, ProviderChain, ProviderConfig, ProviderResult, ResponsesProvider, build_reviewer_messages, normalize_provider_error, provider_capabilities, provider_health_snapshot
from apps.api.app.config import get_settings
from apps.api.app.db import Base
from apps.api.app.models import AiRunLog, AnswerSpec, GeneratedCandidate, GenerationJob, Question, SourceFile
from apps.api.app.localization import language_context


class FailingProvider:
    name = "failing"

    def run_json(self, messages, role, model, context):
        return ProviderResult(provider=self.name, model=model, role=role, payload=None, raw_response={"error": "boom"}, latency_ms=1, error="boom")


class RealLikeMockProvider(MockProvider):
    name = "openai_compatible"


class ConflictingReviewerProvider(MockProvider):
    name = "openai_compatible"

    def run_json(self, messages, role, model, context):
        result = super().run_json(messages, role, model, context)
        if role == "reviewer":
            result.payload = {
                "status": "ok",
                "decision": "accept",
                "independent_answer": "999",
                "solution_summary": "conflicting reviewer answer",
                "issues": [],
            }
            result.raw_response = {"provider": self.name, "payload": result.payload}
        return result


class ModelAwareReviewerProvider(MockProvider):
    name = "openai_compatible"

    def __init__(self, secondary_answer: str | None = None):
        self.secondary_answer = secondary_answer

    def run_json(self, messages, role, model, context):
        result = super().run_json(messages, role, model, context)
        if role == "reviewer" and model == "secondary-reviewer" and self.secondary_answer is not None:
            result.payload = {
                **(result.payload or {}),
                "status": "ok",
                "decision": "accept",
                "independent_answer": self.secondary_answer,
                "solution_summary": "secondary reviewer override",
                "issues": [],
            }
            result.raw_response = {"provider": self.name, "payload": result.payload}
        return result


def seeded_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=1)
    db.add(source)
    db.add(
        Question(
            id="q1",
            source_file_id=source.id,
            status="verified",
            source_name="source.json",
            stem_preview="一个两位数问题",
            difficulty_star=3,
            premium=False,
        )
    )
    db.add(
        AnswerSpec(
            question_id="q1",
            status="verified",
            answer_type="integer",
            canonical_answer="64",
            normalized_spec={},
            raw_answer_spec={},
            verification={},
        )
    )
    db.commit()
    return db


def test_provider_chain_retries_and_falls_back_to_mock():
    chain = ProviderChain([FailingProvider(), MockProvider()], max_retries=1, max_concurrency=1)
    results = chain.run_json(
        [{"role": "user", "content": "{}"}],
        "generator",
        {"default": "mock"},
        {"mock_factory": lambda: {"status": "reject", "candidate_id": "mock", "reject_reason": "sample"}},
    )

    assert [result.provider for result in results] == ["failing", "failing", "mock"]
    assert results[-1].payload["candidate_id"] == "mock"
    assert results[-1].token_usage["estimated_cost_usd"] == 0.0


def test_provider_contract_capabilities_health_and_error_normalization():
    capabilities = provider_capabilities("openai_compatible", "gpt-4o")
    assert capabilities["schema_version"] == "provider_capabilities_v1"
    assert capabilities["supports_list_models"] is True
    assert capabilities["supports_json_mode"] is True
    assert capabilities["max_context_tokens_estimate"] >= 128000

    missing = provider_health_snapshot("openai_compatible", ProviderConfig(name="openai_compatible", base_url="https://ai.example/v1", api_key=None))
    assert missing["schema_version"] == "provider_health_v1"
    assert missing["status"] == "not_configured"
    assert missing["checks"]["network_probe"] == "not_run"
    assert "api_key" in missing["missing"]

    ready = OpenAICompatibleProvider(ProviderConfig(name="openai_compatible", base_url="https://ai.example/v1", api_key="secret")).health_check()
    assert ready["ok"] is True
    assert "secret" not in str(ready)

    timeout = normalize_provider_error(httpx.TimeoutException("request timed out"), "openai_compatible")
    assert timeout["error_code"] == "provider_timeout"
    assert timeout["retryable"] is True


def test_provider_result_records_normalized_error(monkeypatch):
    def fake_post(url, headers, json, timeout, trust_env):
        raise httpx.TimeoutException("request timed out")

    monkeypatch.setattr("apps.api.app.ai_providers.httpx.post", fake_post)
    provider = OpenAICompatibleProvider(ProviderConfig(name="openai_compatible", base_url="https://ai.example/v1", api_key="secret"))

    result = provider.run_json([{"role": "user", "content": "{}"}], "reviewer", "gpt-4o-mini", {})

    assert result.payload is None
    assert result.error_info["error_code"] == "provider_timeout"
    assert result.error_info["provider"] == "openai_compatible"


class FakeResponse:
    # The HTTP-boundary acceptance ledger observes status without reading secrets.
    status_code = 200
    is_success = True

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_optional_provider_adapters_parse_reviewer_json(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout, trust_env):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout, "trust_env": trust_env})
        if url.endswith("/responses"):
            return FakeResponse({"output_text": '{"status":"ok","decision":"accept"}', "usage": {"input_tokens": 2, "output_tokens": 3}})
        if url.endswith("/messages"):
            return FakeResponse({"content": [{"type": "text", "text": '{"status":"ok","decision":"accept"}'}], "usage": {"input_tokens": 2, "output_tokens": 3}})
        return FakeResponse(
            {
                "candidates": [{"content": {"parts": [{"text": '{"status":"ok","decision":"accept"}'}]}}],
                "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3},
            }
        )

    monkeypatch.setattr("apps.api.app.ai_providers.httpx.post", fake_post)
    messages = [{"role": "user", "content": "{}"}]

    providers = [
        ResponsesProvider(ProviderConfig(name="responses", base_url="https://api.example/v1", api_key="k")),
        AnthropicProvider(ProviderConfig(name="anthropic", base_url="https://anthropic.example/v1", api_key="k")),
        GeminiProvider(ProviderConfig(name="gemini", base_url="https://gemini.example/v1beta", api_key="k")),
    ]
    results = [provider.run_json(messages, "reviewer", "model-a", {}) for provider in providers]

    assert [result.provider for result in results] == ["responses", "anthropic", "gemini"]
    assert all(result.payload["decision"] == "accept" for result in results)
    assert all(result.error is None for result in results)
    assert calls[0]["url"].endswith("/responses")
    assert calls[1]["url"].endswith("/messages")
    assert calls[2]["url"].endswith("/models/model-a:generateContent")
    assert all(call["trust_env"] is False for call in calls)


def test_openai_compatible_provider_uses_chat_completions_json_mode(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout, trust_env):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout, "trust_env": trust_env})
        return FakeResponse(
            {
                "choices": [{"message": {"content": '{"status":"ok","decision":"accept"}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7},
            }
        )

    monkeypatch.setattr("apps.api.app.ai_providers.httpx.post", fake_post)
    provider = OpenAICompatibleProvider(ProviderConfig(name="openai_compatible", base_url="https://ai.liaobots2.work/v1", api_key="secret"))

    result = provider.run_json([{"role": "user", "content": "{}"}], "reviewer", "gpt-4o-mini", {})

    assert result.provider == "openai_compatible"
    assert result.payload["decision"] == "accept"
    assert calls[0]["url"] == "https://ai.liaobots2.work/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer secret"
    assert calls[0]["json"]["response_format"] == {"type": "json_object"}
    assert calls[0]["trust_env"] is False


def test_provider_cost_uses_configured_provider_model_price(monkeypatch):
    def fake_post(url, headers, json, timeout, trust_env):
        return FakeResponse(
            {
                "choices": [{"message": {"content": '{"status":"ok","decision":"accept"}'}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
            }
        )

    monkeypatch.setattr("apps.api.app.ai_providers.httpx.post", fake_post)
    provider = OpenAICompatibleProvider(
        ProviderConfig(
            name="openai_compatible",
            base_url="https://ai.liaobots2.work/v1",
            api_key="secret",
            price_table={"openai_compatible:priced-model": {"input": 0.25, "output": 0.5}, "default": {"input": 9.0, "output": 9.0}},
        )
    )

    result = provider.run_json([{"role": "user", "content": "{}"}], "reviewer", "priced-model", {})

    assert result.token_usage["estimated_cost_usd"] == 0.5
    assert result.token_usage["pricing_source"] == "openai_compatible:priced-model"
    assert result.token_usage["pricing"]["input_per_1k"] == 0.25


def test_openai_compatible_provider_uses_role_specific_timeout(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout, trust_env):
        calls.append({"timeout": timeout})
        return FakeResponse(
            {
                "choices": [{"message": {"content": '{"status":"ok","decision":"accept"}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )

    monkeypatch.setattr("apps.api.app.ai_providers.httpx.post", fake_post)
    provider = OpenAICompatibleProvider(
        ProviderConfig(
            name="openai_compatible",
            base_url="https://ai.liaobots2.work/v1",
            api_key="secret",
            timeout_seconds=9,
            role_timeouts={"reviewer": 12},
        )
    )

    provider.run_json([{"role": "user", "content": "{}"}], "reviewer", "gpt-4o-mini", {})

    assert calls[0]["timeout"] == 12


def test_real_provider_chain_does_not_append_mock_when_key_exists(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("LIAOBOTS_API_KEY", "secret")
    get_settings.cache_clear()

    chain = provider_chain("openai_compatible", get_settings())

    assert [provider.name for provider in chain.providers] == ["openai_compatible"]
    get_settings.cache_clear()


def test_provider_chain_uses_mock_only_when_no_real_key(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.delenv("LIAOBOTS_API_KEY", raising=False)
    get_settings.cache_clear()

    chain = provider_chain("openai_compatible", get_settings())

    assert [provider.name for provider in chain.providers] == ["mock"]
    get_settings.cache_clear()


def test_generation_pipeline_logs_generator_and_independent_reviewer():
    db = seeded_db()
    job = GenerationJob(status="queued", request={"target_tags": ["数论"], "difficulty_star": 3, "count": 1}, progress={})
    db.add(job)
    db.commit()

    progress = process_generation_job(db, job.id, provider="mock")
    logs = list(db.scalars(select(AiRunLog).where(AiRunLog.job_id == job.id)))
    candidates = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job.id)))

    assert progress["accepted"] is True
    assert {log.role for log in logs} == {"generator", "reviewer"}
    assert len([log for log in logs if log.role == "generator"]) == 3
    assert len([log for log in logs if log.role == "reviewer"]) == 3
    assert all("estimated_cost_usd" in log.token_usage for log in logs)
    assert progress["ai_trace"]["schema_version"] == "ai_trace_v1"
    assert {log.request_payload["trace"]["trace_id"] for log in logs} == {progress["ai_trace"]["trace_id"]}
    assert all(log.request_payload["trace"]["span_id"] == log.token_usage["trace"]["span_id"] for log in logs)
    assert all(log.request_payload["trace"]["parent_span_id"] for log in logs)
    assert len(candidates) == 3
    assert len([candidate for candidate in candidates if candidate.status == "accepted"]) == 1
    assert len([candidate for candidate in candidates if candidate.review.get("not_selected_reason")]) == 2
    assert candidates[0].review["model_review"]["provider"] == "mock"


def test_generation_job_unhandled_exception_marks_failed(monkeypatch):
    db = seeded_db()
    job = GenerationJob(status="queued", request={"target_tags": ["æ•°è®º"], "difficulty_star": 3, "count": 1}, progress={})
    db.add(job)
    db.commit()

    def fail_retrieve(_db, _request):
        raise RuntimeError("retrieve exploded")

    monkeypatch.setattr("apps.api.app.ai_gateway.retrieve_examples", fail_retrieve)

    progress = process_generation_job(db, job.id, provider="mock")
    db.refresh(job)

    assert job.status == "failed"
    assert progress["stage"] == "failed"
    assert "retrieve exploded" in progress["terminal_reason"]


def test_generator_model_resolves_by_difficulty_star(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("DEFAULT_MODEL", "default-model")
    monkeypatch.setenv("GENERATOR_MODEL", "base-generator")
    monkeypatch.setenv("GENERATOR_MODEL_LE_3", "low-star-generator")
    monkeypatch.setenv("GENERATOR_MODEL_4", "four-star-generator")
    monkeypatch.setenv("GENERATOR_MODEL_5", "five-star-generator")
    get_settings.cache_clear()

    settings = get_settings()
    roles = model_roles(settings.default_model, settings)

    assert generator_model_for_request(settings, {"difficulty_star": 2}, roles) == ("low-star-generator", "le_3")
    assert generator_model_for_request(settings, {"difficulty_star": 4}, roles) == ("four-star-generator", "star_4")
    assert generator_model_for_request(settings, {"difficulty_star": 5}, roles) == ("five-star-generator", "star_5")
    assert generator_model_for_request(settings, {"difficulty_star": None}, roles) == ("base-generator", "fallback")

    get_settings.cache_clear()


def test_generation_pipeline_logs_star_specific_generator_model(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("DEFAULT_MODEL", "default-model")
    monkeypatch.setenv("GENERATOR_MODEL", "base-generator")
    monkeypatch.setenv("GENERATOR_MODEL_5", "five-star-generator")
    monkeypatch.setenv("REVIEWER_MODEL", "review-model")
    get_settings.cache_clear()
    db = seeded_db()
    job = GenerationJob(status="queued", request={"target_tags": ["数论"], "difficulty_star": 5, "count": 1}, progress={})
    db.add(job)
    db.commit()

    progress = process_generation_job(db, job.id, provider="mock")
    logs = list(db.scalars(select(AiRunLog).where(AiRunLog.job_id == job.id)))
    generator_logs = [log for log in logs if log.role == "generator"]
    reviewer_logs = [log for log in logs if log.role == "reviewer"]

    assert progress["model_selection"]["generator_model"] == "five-star-generator"
    assert progress["model_selection"]["candidate_count"] == 3
    policy = progress["model_selection"]["reviewer_independence_policy"]
    assert policy["quality_tier"] == "balanced"
    assert policy["generator_model"] == "five-star-generator"
    assert policy["reviewer_model"] == "review-model"
    assert policy["quality_degraded"] is False
    assert {log.model for log in generator_logs} == {"five-star-generator"}
    assert {log.model for log in reviewer_logs} == {"review-model"}

    get_settings.cache_clear()


def test_strict_real_generation_blocks_same_reviewer_model(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("DEFAULT_MODEL", "shared-model")
    monkeypatch.setenv("GENERATOR_MODEL", "shared-model")
    monkeypatch.setenv("GENERATOR_MODEL_5", "shared-model")
    monkeypatch.setenv("REVIEWER_MODEL", "shared-model")
    get_settings.cache_clear()

    monkeypatch.setattr(
        "apps.api.app.ai_gateway.provider_chain",
        lambda provider, settings, db=None: ProviderChain([RealLikeMockProvider()], max_retries=0, max_concurrency=1),
    )
    db = seeded_db()
    job = GenerationJob(
        status="queued",
        request={
            "target_tags": ["æ•°è®º"],
            "difficulty_star": 5,
            "count": 1,
            "candidate_count": 5,
            "source_example_count": 8,
            "ai_negative_example_count": 5,
            "prefer_reuse_existing_ai": False,
        },
        progress={},
    )
    db.add(job)
    db.commit()

    progress = process_generation_job(db, job.id, provider="openai_compatible")
    db.refresh(job)
    candidates = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job.id)))

    policy = progress["model_selection"]["reviewer_independence_policy"]
    assert job.status == "rejected"
    assert progress["accepted"] is False
    assert policy["quality_tier"] == "strict"
    assert policy["same_generator_reviewer_model"] is True
    assert policy["hard_block_publish"] is True
    assert len(candidates) == 5
    assert all(candidate.status == "rejected" for candidate in candidates)
    assert all("reviewer_independence_required" in candidate.review.get("issues", []) for candidate in candidates)

    get_settings.cache_clear()


def test_strict_real_generation_allows_same_reviewer_model_with_explicit_downgrade_confirmation(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("DEFAULT_MODEL", "shared-model")
    monkeypatch.setenv("GENERATOR_MODEL", "shared-model")
    monkeypatch.setenv("GENERATOR_MODEL_5", "shared-model")
    monkeypatch.setenv("REVIEWER_MODEL", "shared-model")
    get_settings.cache_clear()

    monkeypatch.setattr(
        "apps.api.app.ai_gateway.provider_chain",
        lambda provider, settings, db=None: ProviderChain([RealLikeMockProvider()], max_retries=0, max_concurrency=1),
    )
    db = seeded_db()
    job = GenerationJob(
        status="queued",
        request={
            "target_tags": ["æ•°è®º"],
            "difficulty_star": 5,
            "count": 1,
            "candidate_count": 5,
            "source_example_count": 8,
            "ai_negative_example_count": 5,
            "prefer_reuse_existing_ai": False,
            "allow_reviewer_quality_downgrade": True,
        },
        progress={},
    )
    db.add(job)
    db.commit()

    progress = process_generation_job(db, job.id, provider="openai_compatible")
    db.refresh(job)
    candidates = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job.id)))

    policy = progress["model_selection"]["reviewer_independence_policy"]
    assert job.status == "accepted"
    assert progress["accepted"] is True
    assert policy["quality_tier"] == "strict"
    assert policy["same_generator_reviewer_model"] is True
    assert policy["quality_degraded"] is True
    assert policy["downgrade_allowed_by_user"] is True
    assert policy["downgrade_confirmation_required"] is False
    assert policy["hard_block_publish"] is False
    assert any(candidate.status == "accepted" for candidate in candidates)
    assert all("reviewer_quality_degraded" in candidate.review.get("issues", []) for candidate in candidates)
    assert all("reviewer_independence_required" not in candidate.review.get("issues", []) for candidate in candidates)

    get_settings.cache_clear()


def test_strict_real_generation_allows_reviewer_model_in_allowlist(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("DEFAULT_MODEL", "default-model")
    monkeypatch.setenv("GENERATOR_MODEL", "base-generator")
    monkeypatch.setenv("GENERATOR_MODEL_5", "five-star-generator")
    monkeypatch.setenv("REVIEWER_MODEL", "approved-reviewer")
    monkeypatch.setenv("SECONDARY_REVIEWER_MODEL", "secondary-reviewer")
    monkeypatch.setenv("AI_STRICT_REVIEWER_MODEL_ALLOWLIST", "openai_compatible:approved-reviewer")
    get_settings.cache_clear()

    monkeypatch.setattr(
        "apps.api.app.ai_gateway.provider_chain",
        lambda provider, settings, db=None: ProviderChain([RealLikeMockProvider()], max_retries=0, max_concurrency=1),
    )
    db = seeded_db()
    job = GenerationJob(
        status="queued",
        request={
            "target_tags": ["Ã¦â€¢Â°Ã¨Â®Âº"],
            "difficulty_star": 5,
            "count": 1,
            "candidate_count": 5,
            "source_example_count": 8,
            "ai_negative_example_count": 5,
            "prefer_reuse_existing_ai": False,
        },
        progress={},
    )
    db.add(job)
    db.commit()

    progress = process_generation_job(db, job.id, provider="openai_compatible")
    db.refresh(job)

    policy = progress["model_selection"]["reviewer_independence_policy"]
    assert job.status == "accepted"
    assert policy["quality_tier"] == "strict"
    assert policy["same_generator_reviewer_model"] is False
    assert policy["strict_reviewer_allowlist_configured"] is True
    assert policy["strict_reviewer_allowlist_allowed"] is True
    assert policy["hard_block_publish"] is False
    assert "reviewer_model_not_in_strict_allowlist" not in policy["warnings"]

    get_settings.cache_clear()


def test_strict_real_generation_blocks_reviewer_model_not_in_allowlist(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("DEFAULT_MODEL", "default-model")
    monkeypatch.setenv("GENERATOR_MODEL", "base-generator")
    monkeypatch.setenv("GENERATOR_MODEL_5", "five-star-generator")
    monkeypatch.setenv("REVIEWER_MODEL", "weak-reviewer")
    monkeypatch.setenv("AI_STRICT_REVIEWER_MODEL_ALLOWLIST", '["openai_compatible:approved-reviewer"]')
    get_settings.cache_clear()

    monkeypatch.setattr(
        "apps.api.app.ai_gateway.provider_chain",
        lambda provider, settings, db=None: ProviderChain([RealLikeMockProvider()], max_retries=0, max_concurrency=1),
    )
    db = seeded_db()
    job = GenerationJob(
        status="queued",
        request={
            "target_tags": ["Ã¦â€¢Â°Ã¨Â®Âº"],
            "difficulty_star": 5,
            "count": 1,
            "candidate_count": 5,
            "source_example_count": 8,
            "ai_negative_example_count": 5,
            "prefer_reuse_existing_ai": False,
        },
        progress={},
    )
    db.add(job)
    db.commit()

    progress = process_generation_job(db, job.id, provider="openai_compatible")
    db.refresh(job)
    candidates = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job.id)))

    policy = progress["model_selection"]["reviewer_independence_policy"]
    assert job.status == "rejected"
    assert progress["accepted"] is False
    assert policy["quality_tier"] == "strict"
    assert policy["same_generator_reviewer_model"] is False
    assert policy["strict_reviewer_allowlist_configured"] is True
    assert policy["strict_reviewer_allowlist_allowed"] is False
    assert policy["quality_degraded"] is True
    assert policy["hard_block_publish"] is True
    assert "strict_tier_requires_allowlisted_reviewer_model" in policy["warnings"]
    assert len(candidates) == 5
    assert all(candidate.status == "rejected" for candidate in candidates)
    assert all("reviewer_model_not_in_strict_allowlist" in candidate.review.get("issues", []) for candidate in candidates)
    assert all("reviewer_independence_required" in candidate.review.get("issues", []) for candidate in candidates)

    get_settings.cache_clear()


def test_strict_real_generation_blocks_missing_distinct_secondary_reviewer(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("DEFAULT_MODEL", "default-model")
    monkeypatch.setenv("GENERATOR_MODEL", "base-generator")
    monkeypatch.setenv("GENERATOR_MODEL_5", "five-star-generator")
    monkeypatch.setenv("REVIEWER_MODEL", "reviewer-model")
    monkeypatch.setenv("SECONDARY_REVIEWER_MODEL", "reviewer-model")
    get_settings.cache_clear()

    monkeypatch.setattr(
        "apps.api.app.ai_gateway.provider_chain",
        lambda provider, settings, db=None: ProviderChain([RealLikeMockProvider()], max_retries=0, max_concurrency=1),
    )
    db = seeded_db()
    job = GenerationJob(
        status="queued",
        request={
            "target_tags": ["Ã¦â€¢Â°Ã¨Â®Âº"],
            "difficulty_star": 5,
            "count": 1,
            "candidate_count": 5,
            "source_example_count": 8,
            "ai_negative_example_count": 5,
            "prefer_reuse_existing_ai": False,
        },
        progress={},
    )
    db.add(job)
    db.commit()

    progress = process_generation_job(db, job.id, provider="openai_compatible")
    db.refresh(job)
    candidates = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job.id)))

    policy = progress["model_selection"]["reviewer_independence_policy"]
    assert job.status == "rejected"
    assert policy["dual_reviewer_required"] is True
    assert policy["dual_reviewer_distinct_model"] is False
    assert policy["hard_block_publish"] is True
    assert all(candidate.review["dual_reviewer_review"]["performed"] is False for candidate in candidates)
    assert all("secondary_reviewer_not_distinct" in candidate.review.get("issues", []) for candidate in candidates)
    assert all("reviewer_independence_required" in candidate.review.get("issues", []) for candidate in candidates)

    get_settings.cache_clear()


def test_strict_reviewer_allowlist_downgrade_confirmation_allows_publish(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("DEFAULT_MODEL", "default-model")
    monkeypatch.setenv("GENERATOR_MODEL", "base-generator")
    monkeypatch.setenv("GENERATOR_MODEL_5", "five-star-generator")
    monkeypatch.setenv("REVIEWER_MODEL", "weak-reviewer")
    monkeypatch.setenv("AI_STRICT_REVIEWER_MODEL_ALLOWLIST", "approved-reviewer")
    get_settings.cache_clear()

    monkeypatch.setattr(
        "apps.api.app.ai_gateway.provider_chain",
        lambda provider, settings, db=None: ProviderChain([RealLikeMockProvider()], max_retries=0, max_concurrency=1),
    )
    db = seeded_db()
    job = GenerationJob(
        status="queued",
        request={
            "target_tags": ["Ã¦â€¢Â°Ã¨Â®Âº"],
            "difficulty_star": 5,
            "count": 1,
            "candidate_count": 5,
            "source_example_count": 8,
            "ai_negative_example_count": 5,
            "prefer_reuse_existing_ai": False,
            "allow_reviewer_quality_downgrade": True,
        },
        progress={},
    )
    db.add(job)
    db.commit()

    progress = process_generation_job(db, job.id, provider="openai_compatible")
    db.refresh(job)
    candidates = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job.id)))

    policy = progress["model_selection"]["reviewer_independence_policy"]
    assert job.status == "accepted"
    assert progress["accepted"] is True
    assert policy["quality_tier"] == "strict"
    assert policy["strict_reviewer_allowlist_allowed"] is False
    assert policy["downgrade_allowed_by_user"] is True
    assert policy["downgrade_confirmation_required"] is False
    assert policy["hard_block_publish"] is False
    assert any(candidate.status == "accepted" for candidate in candidates)
    assert all("reviewer_model_not_in_strict_allowlist" in candidate.review.get("issues", []) for candidate in candidates)
    assert all("reviewer_quality_degraded" in candidate.review.get("issues", []) for candidate in candidates)
    assert all("reviewer_independence_required" not in candidate.review.get("issues", []) for candidate in candidates)

    get_settings.cache_clear()


def test_strict_real_generation_runs_dual_reviewer_with_distinct_secondary_model(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("DEFAULT_MODEL", "default-model")
    monkeypatch.setenv("GENERATOR_MODEL", "base-generator")
    monkeypatch.setenv("GENERATOR_MODEL_5", "five-star-generator")
    monkeypatch.setenv("REVIEWER_MODEL", "primary-reviewer")
    monkeypatch.setenv("SECONDARY_REVIEWER_MODEL", "secondary-reviewer")
    get_settings.cache_clear()

    monkeypatch.setattr(
        "apps.api.app.ai_gateway.provider_chain",
        lambda provider, settings, db=None: ProviderChain([ModelAwareReviewerProvider()], max_retries=0, max_concurrency=1),
    )
    db = seeded_db()
    job = GenerationJob(
        status="queued",
        request={
            "target_tags": ["Ã¦â€¢Â°Ã¨Â®Âº"],
            "difficulty_star": 5,
            "count": 1,
            "candidate_count": 5,
            "source_example_count": 8,
            "ai_negative_example_count": 5,
            "prefer_reuse_existing_ai": False,
        },
        progress={},
    )
    db.add(job)
    db.commit()

    progress = process_generation_job(db, job.id, provider="openai_compatible")
    db.refresh(job)
    candidates = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job.id)))
    logs = list(db.scalars(select(AiRunLog).where(AiRunLog.job_id == job.id)))

    policy = progress["model_selection"]["reviewer_independence_policy"]
    assert job.status == "accepted"
    assert policy["dual_reviewer_required"] is True
    assert policy["dual_reviewer_distinct_model"] is True
    assert any(candidate.status == "accepted" for candidate in candidates)
    assert all(candidate.review["dual_reviewer_review"]["performed"] is True for candidate in candidates)
    assert all(candidate.review["dual_reviewer_review"]["consistent"] is True for candidate in candidates)
    assert {log.role for log in logs} >= {"reviewer", "secondary_reviewer"}
    assert {log.model for log in logs if log.role == "secondary_reviewer"} == {"secondary-reviewer"}

    get_settings.cache_clear()


def test_strict_real_generation_rejects_dual_reviewer_answer_conflict(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("DEFAULT_MODEL", "default-model")
    monkeypatch.setenv("GENERATOR_MODEL", "base-generator")
    monkeypatch.setenv("GENERATOR_MODEL_5", "five-star-generator")
    monkeypatch.setenv("REVIEWER_MODEL", "primary-reviewer")
    monkeypatch.setenv("SECONDARY_REVIEWER_MODEL", "secondary-reviewer")
    get_settings.cache_clear()

    monkeypatch.setattr(
        "apps.api.app.ai_gateway.provider_chain",
        lambda provider, settings, db=None: ProviderChain([ModelAwareReviewerProvider(secondary_answer="999")], max_retries=0, max_concurrency=1),
    )
    db = seeded_db()
    job = GenerationJob(
        status="queued",
        request={
            "target_tags": ["Ã¦â€¢Â°Ã¨Â®Âº"],
            "difficulty_star": 5,
            "count": 1,
            "candidate_count": 5,
            "source_example_count": 8,
            "ai_negative_example_count": 5,
            "prefer_reuse_existing_ai": False,
        },
        progress={},
    )
    db.add(job)
    db.commit()

    progress = process_generation_job(db, job.id, provider="openai_compatible")
    db.refresh(job)
    candidates = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job.id)))

    assert job.status == "rejected"
    assert progress["accepted"] is False
    assert len(candidates) == 5
    assert all(candidate.status == "rejected" for candidate in candidates)
    assert all("dual_reviewer_answer_conflict" in candidate.review.get("issues", []) for candidate in candidates)
    assert all("secondary_reviewer_answer_spec_conflict" in candidate.review.get("issues", []) for candidate in candidates)
    assert all(candidate.review["dual_reviewer_review"]["consistent"] is False for candidate in candidates)

    get_settings.cache_clear()


def test_reviewer_independent_answer_conflict_blocks_publish(monkeypatch):
    monkeypatch.setattr("apps.api.app.config.env_file_path", lambda: Path("__missing_test_env__"))
    monkeypatch.setenv("DEFAULT_MODEL", "default-model")
    monkeypatch.setenv("GENERATOR_MODEL", "base-generator")
    monkeypatch.setenv("REVIEWER_MODEL", "reviewer-model")
    get_settings.cache_clear()

    monkeypatch.setattr(
        "apps.api.app.ai_gateway.provider_chain",
        lambda provider, settings, db=None: ProviderChain([ConflictingReviewerProvider()], max_retries=0, max_concurrency=1),
    )
    db = seeded_db()
    job = GenerationJob(
        status="queued",
        request={
            "target_tags": ["Ã¦â€¢Â°Ã¨Â®Âº"],
            "difficulty_star": 3,
            "count": 1,
            "candidate_count": 1,
            "source_example_count": 0,
            "ai_negative_example_count": 0,
            "prefer_reuse_existing_ai": False,
        },
        progress={},
    )
    db.add(job)
    db.commit()

    progress = process_generation_job(db, job.id, provider="openai_compatible")
    db.refresh(job)
    candidates = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job.id)))

    assert job.status == "rejected"
    assert progress["accepted"] is False
    assert len(candidates) == 1
    review = candidates[0].review
    assert candidates[0].status == "rejected"
    assert "reviewer_answer_spec_conflict" in review.get("issues", [])
    assert review["reviewer_answer_spec_alignment"]["checked"] is True
    assert review["reviewer_answer_spec_alignment"]["matches_answer_spec"] is False

    get_settings.cache_clear()


def test_generation_and_reviewer_prompts_include_rating_guide():
    with language_context("zh-CN"):
        generator_messages = build_messages("structural_elegance", {"difficulty_star": 4, "target_tags": ["数论"]}, [])
        reviewer_messages = build_reviewer_messages({"status": "ok", "candidate_id": "c1"})
    payload = generator_messages[1]["content"]

    assert "六维" in generator_messages[0]["content"]
    assert "0—2=1" in generator_messages[0]["content"]
    assert "private_design_contract" in payload
    assert "required_difference_axes" in payload
    assert "do_not_clone_or_number-shift" in payload
    assert "difficulty_star" in reviewer_messages[0]["content"]
    assert "不得相信候选自报星级" in reviewer_messages[0]["content"]


def test_generator_prompt_includes_targeted_retry_constraints():
    messages = build_messages(
        "structural_elegance",
        {
            "difficulty_star": 4,
            "target_tags": ["数论"],
            "regeneration_mode": "targeted_retry",
            "retry_attempt": 2,
            "max_retry_attempts": 3,
            "regeneration_constraints": [{"code": "near_duplicate", "instruction": "change axes"}],
        },
        [],
    )
    payload = messages[1]["content"]

    assert "regeneration_retry" in payload
    assert "targeted_retry" in payload
    assert "must_change_design_axes" in payload
