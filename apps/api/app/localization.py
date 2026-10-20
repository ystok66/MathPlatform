from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, field_validator

from .config import project_root

LANGUAGE_HEADER = "X-Math-Platform-Language"
OUTPUT_LANGUAGE_HEADER = "X-Math-Platform-Output-Language"
_language: ContextVar[str] = ContextVar("math_platform_output_language", default="en")
_ui_language: ContextVar[str] = ContextVar("math_platform_ui_language", default="en")
_missing_keys: set[tuple[str, str]] = set()
_logger = logging.getLogger(__name__)


def _resource_root() -> Path:
    root = project_root() / "packages" / "i18n"
    if not (root / "languages.json").is_file() and not getattr(sys, "frozen", False):
        source = Path(__file__).resolve().parents[3] / "packages" / "i18n"
        if (source / "languages.json").is_file():
            return source
    return root


@lru_cache(maxsize=16)
def _load_registry(root: Path) -> dict:
    data = json.loads((root / "languages.json").read_text(encoding="utf-8"))
    codes = [item["code"] for item in data["languages"] if item.get("enabled", True)]
    if not codes or len(codes) != len(set(codes)) or data["default_language"] not in codes:
        raise ValueError("invalid_language_registry")
    return data


def language_registry() -> dict:
    return _load_registry(_resource_root())


def supported_languages() -> tuple[str, ...]:
    return tuple(item["code"] for item in language_registry()["languages"] if item.get("enabled", True))


def normalize_language(value: object = None) -> str:
    if isinstance(value, str):
        candidate = value.strip().casefold()
        for code in supported_languages():
            if candidate == code.casefold():
                return code
    return language_registry()["default_language"]


def validate_output_language(value: object = None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and any(value.strip().casefold() == code.casefold() for code in supported_languages()):
        return normalize_language(value)
    raise ValueError("unsupported_output_language")


def resolve_output_language(explicit: object = None, header: object = None, ui_language: object = None) -> str:
    if explicit is not None:
        return validate_output_language(explicit)  # type: ignore[return-value]
    if header is not None:
        return validate_output_language(header)  # type: ignore[return-value]
    return normalize_language(ui_language)


class LanguageRequest(BaseModel):
    output_language: str | None = None

    def model_dump(self, *args, **kwargs):
        # Freeze both languages before a request enters a persisted queue.
        return language_snapshot(super().model_dump(*args, **kwargs))

    @field_validator("output_language", mode="before")
    @classmethod
    def validate_language(cls, value):
        return validate_output_language(value)


def current_language() -> str:
    """Compatibility name: the frozen AI output language, not the UI language."""
    return _language.get()


def current_ui_language() -> str:
    return _ui_language.get()


def language_name(language: str) -> str:
    normalized = normalize_language(language)
    return next(item["name"] for item in language_registry()["languages"] if item["code"] == normalized)


@contextmanager
def language_context(value: object, ui_language: object = None) -> Iterator[str]:
    language = normalize_language(value)
    token = _language.set(language)
    ui_token = _ui_language.set(normalize_language(ui_language)) if ui_language is not None else None
    try:
        yield language
    finally:
        if ui_token is not None:
            _ui_language.reset(ui_token)
        _language.reset(token)


def language_snapshot(payload: Mapping | None = None) -> dict:
    result = dict(payload or {})
    explicit = result.get("output_language")
    output = validate_output_language(explicit) if explicit is not None else current_language()
    result.update(ui_language=current_ui_language(), output_language=output, language=output)
    return result


def stored_output_language(payload: Mapping | None) -> str:
    data = payload or {}
    return normalize_language(data.get("output_language") or data.get("language"))


@lru_cache(maxsize=32)
def _load_messages(root: Path, language: str) -> dict[str, str]:
    messages: dict[str, str] = {}
    for path in sorted((root / "locales" / language).glob("*.json")):
        values = json.loads(path.read_text(encoding="utf-8"))
        if messages.keys() & values.keys():
            raise ValueError("duplicate_message_keys")
        messages.update(values)
    return messages


@lru_cache(maxsize=16)
def _load_aliases(root: Path) -> dict[str, str]:
    path = root / "legacy-keys.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def message_key(value: str) -> str | None:
    root = _resource_root()
    if value in _load_messages(root, language_registry()["default_language"]):
        return value
    return _load_aliases(root).get(value)


def translate_message(key: str, params: Mapping | None = None, *, language: str | None = None, default: str | None = None) -> str:
    locale = normalize_language(language if language is not None else current_ui_language())
    root = _resource_root()
    canonical = message_key(key) or key
    value = _load_messages(root, locale).get(canonical)
    if value is None:
        marker = (locale, canonical)
        if marker not in _missing_keys:
            _missing_keys.add(marker)
            _logger.warning("missing_translation locale=%s key=%s", locale, canonical if len(canonical) < 120 else "unmapped_text")
        value = _load_messages(root, language_registry()["default_language"]).get(canonical, default if default is not None else key)
    for name, replacement in (params or {}).items():
        value = value.replace("{" + str(name) + "}", str(replacement))
    return value


def translate(zh: str, en: str) -> str:
    """Compatibility adapter for old static pairs; never traverse business data."""
    key = message_key(zh)
    if key:
        return translate_message(key, language=current_language(), default=en)
    return zh if current_language() == "zh-CN" else en


def languages_snapshot() -> dict:
    root = project_root() / "prompts"
    expected = {path.name for path in (root / "en").glob("*.md")}
    items = []
    for item in language_registry()["languages"]:
        missing = sorted(name for name in expected if not (root / item["code"] / name).is_file())
        items.append({**item, "resources_available": bool(_load_messages(_resource_root(), item["code"])) and not missing, "missing_prompts": missing})
    return {"schema_version": "language_settings_v1", "default_language": language_registry()["default_language"], "output_default": "follow-ui", "languages": items}


def clear_language_caches() -> None:
    _load_registry.cache_clear()
    _load_messages.cache_clear()
    _load_aliases.cache_clear()
    _missing_keys.clear()
