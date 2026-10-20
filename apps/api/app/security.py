from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse


CSRF_HEADER_NAME = "x-math-platform-client"
CSRF_HEADER_VALUE = "math-platform-web"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class RequestRejection:
    error_code: str
    detail: str


def parse_allowed_origins(cors_origins: str) -> set[str]:
    return {origin.strip().rstrip("/") for origin in cors_origins.split(",") if origin.strip()}


def normalize_origin(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value.strip())
    if not parsed.scheme or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme.lower()}://{host}{port}"


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    value = host.strip().lower()
    if value.startswith("[") and "]" in value:
        normalized = value[1 : value.index("]")]
    elif value.count(":") > 1:
        normalized = value
    else:
        normalized = value.split(":", 1)[0]
    return normalized in LOOPBACK_HOSTS


def is_allowed_browser_origin(origin: str | None, allowed_origins: set[str]) -> bool:
    normalized = normalize_origin(origin)
    return normalized is not None and normalized in allowed_origins


def header_value(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def unsafe_browser_request_rejection(
    *,
    method: str,
    path: str,
    headers: Mapping[str, str],
    allowed_origins: set[str],
) -> RequestRejection | None:
    if method.upper() not in UNSAFE_METHODS:
        return None
    if path.startswith("/api/health"):
        return None

    origin = header_value(headers, "origin")
    referer = header_value(headers, "referer")
    sec_fetch_site = (header_value(headers, "sec-fetch-site") or "").lower()
    has_browser_context = bool(origin or referer or sec_fetch_site)
    if not has_browser_context:
        return None

    if sec_fetch_site == "cross-site":
        return RequestRejection("csrf_cross_site", "Cross-site browser requests are not allowed.")

    if origin and not is_allowed_browser_origin(origin, allowed_origins):
        return RequestRejection("csrf_bad_origin", "Browser request origin is not allowed.")

    if not origin and referer and not is_allowed_browser_origin(referer, allowed_origins):
        return RequestRejection("csrf_bad_referer", "Browser request referer is not allowed.")

    if header_value(headers, CSRF_HEADER_NAME) != CSRF_HEADER_VALUE:
        return RequestRejection("csrf_header_missing", "Missing or invalid math platform client header.")

    return None
