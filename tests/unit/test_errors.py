from apps.api.app.errors import error_catalog, error_envelope


def test_error_envelope_maps_common_string_errors():
    payload = error_envelope(status_code=409, detail="answer_locked_after_reveal", trace_id="trace-1")

    assert payload["detail"] == "answer_locked_after_reveal"
    assert payload["error_code"] == "ANSWER_LOCKED_AFTER_REVEAL"
    assert payload["user_message"] == "本题已经看过答案，回答已锁定，不能再提交或修改。"
    assert payload["retryable"] is False
    assert payload["trace_id"] == "trace-1"


def test_error_envelope_preserves_structured_detail_and_sanitizes_safe_details():
    payload = error_envelope(
        status_code=409,
        detail={
            "error_code": "state_version_conflict",
            "expected": 1,
            "current": 2,
            "api_key": "sk-secret",
        },
        trace_id="trace-2",
    )

    assert payload["error_code"] == "STATE_VERSION_CONFLICT"
    assert payload["retryable"] is True
    assert payload["details_safe"]["api_key"] == "***"
    assert payload["details_safe"]["current"] == 2


def test_error_envelope_defaults_to_actionable_not_found():
    payload = error_envelope(status_code=404, detail="question not found", trace_id="trace-3")

    assert payload["error_code"] == "RESOURCE_NOT_FOUND"
    assert payload["user_message"] == "请求的数据不存在或已经不可用。"
    assert payload["action"] == "刷新页面或返回上一层重新选择。"


def test_error_catalog_exposes_common_and_default_errors():
    catalog = error_catalog()
    by_code = {row["error_code"]: row for row in catalog["items"]}

    assert catalog["schema_version"] == "error_catalog_v1"
    assert catalog["common_count"] >= 1
    assert by_code["ANSWER_LOCKED_AFTER_REVEAL"]["retryable"] is False
    assert by_code["AI_COST_BUDGET_EXCEEDED"]["source"] == "common_error_map"
    assert by_code["CSRF_HEADER_MISSING"]["action"]
    assert by_code["RESOURCE_NOT_FOUND"]["http_status"] == 404
