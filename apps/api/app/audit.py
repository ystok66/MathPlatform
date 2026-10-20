from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog


SENSITIVE_KEYS = {"api_key", "authorization", "password", "secret", "token", "x-api-key", "cookie", "set-cookie"}
SENSITIVE_KEY_FRAGMENTS = ("api_key", "authorization", "password", "secret", "token", "x-api-key", "cookie")


def is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return lower in SENSITIVE_KEYS or any(fragment in lower for fragment in SENSITIVE_KEY_FRAGMENTS)


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if is_sensitive_key(str(key)) else sanitize_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str) and value.lower().startswith("bearer "):
        return "Bearer ***"
    return value


def log_audit_event(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
    actor: str = "local_user",
) -> AuditLog:
    row = AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=sanitize_payload(payload or {}),
    )
    db.add(row)
    return row
