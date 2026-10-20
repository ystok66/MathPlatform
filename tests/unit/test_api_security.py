from apps.api.app.security import (
    CSRF_HEADER_NAME,
    CSRF_HEADER_VALUE,
    is_loopback_host,
    parse_allowed_origins,
    unsafe_browser_request_rejection,
)


def test_browser_write_request_requires_allowed_origin_and_platform_header():
    allowed = parse_allowed_origins("http://127.0.0.1:3000,http://localhost:3000")

    assert (
        unsafe_browser_request_rejection(
            method="POST",
            path="/api/practice/sessions",
            headers={
                "Origin": "http://127.0.0.1:3000",
                CSRF_HEADER_NAME: CSRF_HEADER_VALUE,
            },
            allowed_origins=allowed,
        )
        is None
    )

    missing_header = unsafe_browser_request_rejection(
        method="POST",
        path="/api/practice/sessions",
        headers={"Origin": "http://127.0.0.1:3000"},
        allowed_origins=allowed,
    )
    assert missing_header is not None
    assert missing_header.error_code == "csrf_header_missing"

    bad_origin = unsafe_browser_request_rejection(
        method="POST",
        path="/api/admin/clear-history",
        headers={
            "Origin": "https://example.com",
            CSRF_HEADER_NAME: CSRF_HEADER_VALUE,
        },
        allowed_origins=allowed,
    )
    assert bad_origin is not None
    assert bad_origin.error_code == "csrf_bad_origin"


def test_cross_site_fetch_metadata_is_rejected_even_with_header():
    rejection = unsafe_browser_request_rejection(
        method="DELETE",
        path="/api/settings/models/api-key",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Sec-Fetch-Site": "cross-site",
            CSRF_HEADER_NAME: CSRF_HEADER_VALUE,
        },
        allowed_origins=parse_allowed_origins("http://127.0.0.1:3000"),
    )

    assert rejection is not None
    assert rejection.error_code == "csrf_cross_site"


def test_non_browser_cli_requests_and_safe_methods_are_not_csrf_blocked():
    allowed = parse_allowed_origins("http://127.0.0.1:3000")

    assert unsafe_browser_request_rejection(method="POST", path="/api/import/json-files", headers={}, allowed_origins=allowed) is None
    assert unsafe_browser_request_rejection(method="GET", path="/api/questions", headers={"Origin": "https://example.com"}, allowed_origins=allowed) is None


def test_loopback_host_detection_for_packaged_api_binding():
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("[::1]") is True
    assert is_loopback_host("0.0.0.0") is False
    assert is_loopback_host("192.168.1.20") is False
