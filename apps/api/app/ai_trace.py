from __future__ import annotations

from typing import Any
from uuid import uuid4


def new_trace_id(prefix: str = "ai") -> str:
    return f"{prefix}-{uuid4().hex}"


def new_span_id(prefix: str) -> str:
    safe_prefix = "".join(ch if ch.isalnum() else "_" for ch in prefix.lower()).strip("_") or "span"
    return f"{safe_prefix}-{uuid4().hex[:16]}"


def root_ai_trace(operation: str, *, trace_id: str | None = None, **context: Any) -> dict[str, Any]:
    return _compact_trace(
        {
            "schema_version": "ai_trace_v1",
            "trace_id": trace_id or new_trace_id(),
            "span_id": new_span_id(operation),
            "parent_span_id": None,
            "operation": operation,
            **context,
        }
    )


def child_ai_trace(parent: dict[str, Any], operation: str, **context: Any) -> dict[str, Any]:
    return _compact_trace(
        {
            "schema_version": "ai_trace_v1",
            "trace_id": parent.get("trace_id") or new_trace_id(),
            "span_id": new_span_id(operation),
            "parent_span_id": parent.get("span_id"),
            "operation": operation,
            **context,
        }
    )


def provider_attempt_trace(parent: dict[str, Any], result: Any) -> dict[str, Any]:
    role = getattr(result, "role", None)
    provider = getattr(result, "provider", None)
    operation = ".".join(part for part in (parent.get("operation"), provider, f"attempt_{getattr(result, 'attempt', 1)}") if part)
    return child_ai_trace(
        parent,
        operation,
        role=role,
        provider=provider,
        model=getattr(result, "model", None),
        attempt=getattr(result, "attempt", None),
        latency_ms=getattr(result, "latency_ms", None),
        error=bool(getattr(result, "error", None)),
    )


def request_payload_with_trace(request_payload: dict[str, Any], result: Any, parent_trace: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    run_trace = provider_attempt_trace(parent_trace, result)
    payload = {**request_payload, "attempt": getattr(result, "attempt", 1), "trace": run_trace}
    return payload, run_trace


def token_usage_with_trace(token_usage: dict[str, Any] | None, result: Any, run_trace: dict[str, Any]) -> dict[str, Any]:
    return {
        **(token_usage or {}),
        "attempt": getattr(result, "attempt", 1),
        "fallback_provider": getattr(result, "provider", None),
        "trace": run_trace,
    }


def ensure_job_root_trace(progress: dict[str, Any] | None, job_id: str) -> dict[str, Any]:
    existing = (progress or {}).get("ai_trace")
    if isinstance(existing, dict) and existing.get("trace_id") and existing.get("span_id"):
        return existing
    return root_ai_trace("generation_job", job_id=job_id)


def _compact_trace(trace: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in trace.items() if value is not None}
