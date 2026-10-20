from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .ai_providers import ProviderConfig, provider_health_snapshot
from .release_info import release_info
from .secrets import has_secret


HEALTH_SCHEMA_VERSION = "health_detail_v1"
MIN_FREE_DISK_BYTES = 512 * 1024 * 1024


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _database_health(db: Session) -> dict[str, Any]:
    started = perf_counter()
    try:
        db.execute(text("SELECT 1")).scalar()
        return {"status": "ok", "ok": True, "latency_ms": _elapsed_ms(started)}
    except Exception as exc:
        return {
            "status": "error",
            "ok": False,
            "latency_ms": _elapsed_ms(started),
            "error_class": exc.__class__.__name__,
            "error": str(exc)[:300],
        }


def _disk_health(root: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(root)
    except Exception as exc:
        return {"status": "error", "ok": False, "error_class": exc.__class__.__name__, "error": str(exc)[:300]}
    free_bytes = int(usage.free)
    total_bytes = int(usage.total)
    return {
        "status": "ok" if free_bytes >= MIN_FREE_DISK_BYTES else "degraded",
        "ok": free_bytes >= MIN_FREE_DISK_BYTES,
        "free_bytes": free_bytes,
        "total_bytes": total_bytes,
        "free_gb": round(free_bytes / 1024 / 1024 / 1024, 2),
        "min_free_gb": round(MIN_FREE_DISK_BYTES / 1024 / 1024 / 1024, 2),
    }


def _provider_health(db: Session, settings: Any) -> dict[str, Any]:
    provider = str(getattr(settings, "model_provider", "openai_compatible") or "openai_compatible")
    if provider == "responses":
        base_url = getattr(settings, "responses_base_url", "")
        api_key = getattr(settings, "responses_api_key", None)
    elif provider == "anthropic":
        base_url = getattr(settings, "anthropic_base_url", "")
        api_key = getattr(settings, "anthropic_api_key", None)
    elif provider == "gemini":
        base_url = getattr(settings, "gemini_base_url", "")
        api_key = getattr(settings, "gemini_api_key", None)
    else:
        base_url = getattr(settings, "liaobots_base_url", "")
        api_key = has_secret(db, "LIAOBOTS_API_KEY") or getattr(settings, "liaobots_api_key", None)
    snapshot = provider_health_snapshot(
        provider,
        ProviderConfig(
            name=provider,
            base_url=str(base_url or ""),
            api_key=str(api_key or "") or None,
            models={"default": getattr(settings, "default_model", "")},
        ),
    )
    configured = bool(snapshot["ok"])
    return {
        "schema_version": snapshot["schema_version"],
        "status": "ok" if configured else "degraded",
        "ok": configured,
        "provider": provider,
        "default_model": getattr(settings, "default_model", ""),
        "api_key_configured": bool(snapshot["checks"]["api_key_configured"]),
        "base_url_configured": bool(snapshot["checks"]["base_url_configured"]),
        "network_probe": snapshot["checks"]["network_probe"],
        "missing": snapshot.get("missing", []),
        "capabilities": snapshot.get("capabilities", {}),
        "note": "Provider key presence only; this endpoint does not call external models.",
    }


def _release_health(root: Path) -> dict[str, Any]:
    info = release_info(root)
    verification = info.get("verification") if isinstance(info.get("verification"), dict) else {}
    manifest_exists = bool(info.get("manifest_exists"))
    verification_ok = bool(verification.get("ok", True))
    status = "ok" if manifest_exists and verification_ok else "degraded"
    return {
        "status": status,
        "ok": status == "ok",
        "manifest_exists": manifest_exists,
        "app_version": info.get("app_version"),
        "verification": verification,
        "associations": info.get("associations") if isinstance(info.get("associations"), dict) else {},
    }


def health_detail(db: Session, root: Path, settings: Any) -> dict[str, Any]:
    database = _database_health(db)
    disk = _disk_health(root)
    provider = _provider_health(db, settings)
    release = _release_health(root)
    checks = {
        "liveness": {"status": "ok", "ok": True, "app_running": True},
        "readiness": {
            "status": "ok" if database["ok"] else "error",
            "ok": bool(database["ok"]),
            "database": database,
        },
        "degraded": {
            "status": "degraded" if not disk["ok"] or not provider["ok"] or not release["ok"] else "ok",
            "ok": bool(disk["ok"] and provider["ok"] and release["ok"]),
            "disk": disk,
            "provider": provider,
            "release": release,
        },
        "diagnostic": {
            "status": "ok",
            "ok": True,
            "root": str(root),
            "database_url_kind": str(getattr(settings, "database_url", "")).split(":", 1)[0],
            "redis_configured": bool(getattr(settings, "redis_url", "")),
            "cors_origins": str(getattr(settings, "cors_origins", "")),
        },
    }
    if not checks["readiness"]["ok"]:
        overall_status = "error"
    elif not checks["degraded"]["ok"]:
        overall_status = "degraded"
    else:
        overall_status = "ok"
    return {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "overall": {
            "status": overall_status,
            "ok": overall_status != "error",
            "degraded": overall_status == "degraded",
        },
        "checks": checks,
    }
