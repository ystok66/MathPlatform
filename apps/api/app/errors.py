from __future__ import annotations
from .localization import translate_message, current_language

from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from .audit import sanitize_payload
from .localization import current_language


@dataclass(frozen=True)
class ErrorInfo:
    error_code: str
    user_message: str
    action: str
    retryable: bool = False
    retry_after_seconds: int | None = None


ENGLISH_ERRORS = {
    "ANSWER_LOCKED_AFTER_REVEAL": ("The answer was revealed, so this response is locked.", "Continue to the next question or start a new review session from History."),
    "STATE_VERSION_CONFLICT": ("This page is out of date. Your newer answer has been preserved.", "Refresh the session before continuing."),
    "DESTRUCTIVE_CHALLENGE_REQUIRED": ("This action requires a one-time confirmation code.", "Request a code and enter it in the confirmation dialog."),
    "DESTRUCTIVE_CHALLENGE_EXPIRED": ("The confirmation code has expired or was already used.", "Request a new code."),
    "DESTRUCTIVE_CHALLENGE_MISMATCH": ("The confirmation code does not match. Nothing was changed.", "Check the code or request a new one."),
    "DESTRUCTIVE_CHALLENGE_TARGET_CHANGED": ("The affected data changed during confirmation.", "Refresh and confirm the updated scope."),
    "NO_ACCEPTED_GENERATED_QUESTIONS": ("This batch has no approved questions ready for practice yet.", "Refresh its status or wait for generation to finish."),
    "NO_AVAILABLE_REVIEW_QUESTIONS": ("No questions in this selection are available for review.", "Broaden the filters or select the entire group."),
    "QUESTION_NOT_IN_SESSION": ("This question is not part of the current session.", "Refresh the session and select a question again."),
    "INVALID_MODEL_PRICE_JSON": ("The pricing JSON is invalid. Model settings were not saved.", "Enter a single JSON object containing the model prices."),
    "GENERATION_CALL_BUDGET_EXCEEDED": ("This generation request exceeds the local API call limit.", "Reduce the question or candidate count and try again."),
    "AI_COST_BUDGET_EXCEEDED": ("The estimated AI cost exceeds the local budget. The task has not started.", "Reduce the question or candidate count, or adjust the budget."),
    "CSRF_CROSS_SITE": ("A cross-site request was blocked.", "Use the platform's own page and refresh if necessary."),
    "CSRF_BAD_ORIGIN": ("This browser origin is not allowed.", "Open the platform using its local address."),
    "CSRF_BAD_REFERER": ("The referring page is not allowed.", "Open the platform using its local address."),
    "CSRF_HEADER_MISSING": ("The request is missing the platform client identifier.", "Refresh the page. Scripts must include the platform client header."),
    "RESOURCE_NOT_FOUND": ("The requested data no longer exists or is unavailable.", "Refresh or go back and select it again."),
    "STRICT_TEST_ACTION_DISABLED": ("This action is disabled during a strict test.", "Finish or exit the test, then use this feature in ordinary practice."),
    "INVALID_TASK_STATE": ("The task's current status does not allow this action.", "Refresh the task and choose an available action."),
    "UNSUPPORTED_IMPORT_SCHEMA": ("This import format is not supported.", "Use a question-bank or history package exported by this platform."),
    "BAD_REQUEST": ("Some request fields are missing or invalid.", "Check the inputs and try again."),
    "FORBIDDEN": ("This action was blocked by a safety rule.", "Refresh and retry. Export diagnostics if the issue persists."),
    "CONFLICT": ("The data changed. The newer state has been preserved.", "Refresh before trying again."),
    "RATE_LIMITED": ("Too many requests, or the external provider is rate-limiting calls.", "Wait a moment or reduce concurrency."),
    "INTERNAL_ERROR": ("The request could not be completed because of a system error.", "Export diagnostics and note the action you were performing."),
}


def localized_error_info(info: ErrorInfo) -> ErrorInfo:
    return replace(info,
        user_message=translate_message(f"errors.{info.error_code.lower()}.message", default=info.user_message),
        action=translate_message(f"errors.{info.error_code.lower()}.action", default=info.action))


COMMON_ERROR_MAP: dict[str, ErrorInfo] = {
    "answer_locked_after_reveal": ErrorInfo(
        "ANSWER_LOCKED_AFTER_REVEAL",
        "本题已经看过答案，回答已锁定，不能再提交或修改。",
        "继续下一题，或在历史重做中重新创建复习会话。",
        False,
    ),
    "state_version_conflict": ErrorInfo(
        "STATE_VERSION_CONFLICT",
        "页面状态已经过期，系统没有覆盖较新的作答状态。",
        "刷新当前 session 状态后再继续。",
        True,
    ),
    "destructive challenge required": ErrorInfo(
        "DESTRUCTIVE_CHALLENGE_REQUIRED",
        "危险操作需要一次性确认码。",
        "重新点击操作按钮并按弹窗输入确认码。",
        True,
    ),
    "destructive challenge expired or already used": ErrorInfo(
        "DESTRUCTIVE_CHALLENGE_EXPIRED",
        "确认码已过期或已经使用。",
        "重新生成确认码后再执行。",
        True,
    ),
    "destructive challenge mismatch": ErrorInfo(
        "DESTRUCTIVE_CHALLENGE_MISMATCH",
        "确认码不匹配，操作没有执行。",
        "检查输入内容，重新生成确认码后再试。",
        True,
    ),
    "destructive challenge target changed": ErrorInfo(
        "DESTRUCTIVE_CHALLENGE_TARGET_CHANGED",
        "操作目标在确认期间发生变化，系统已拒绝旧确认码。",
        "刷新页面后重新确认操作范围。",
        True,
    ),
    "no accepted generated questions are available yet": ErrorInfo(
        "NO_ACCEPTED_GENERATED_QUESTIONS",
        "当前批次还没有审核通过的可练题。",
        "刷新批次状态，或等待生成完成后再开始做题。",
        True,
    ),
    "no available questions for review session": ErrorInfo(
        "NO_AVAILABLE_REVIEW_QUESTIONS",
        "当前选择范围没有可重做的题。",
        "放宽筛选条件，或选择整组重做。",
        False,
    ),
    "question does not belong to session": ErrorInfo(
        "QUESTION_NOT_IN_SESSION",
        "这道题不属于当前练习 session。",
        "刷新 session 状态后重新选择题目。",
        True,
    ),
    "invalid_model_price_json": ErrorInfo(
        "INVALID_MODEL_PRICE_JSON",
        "价格表 JSON 格式不正确，模型配置没有保存。",
        "请填写单个 JSON 对象，例如 {\"default\":{\"input\":0.001,\"output\":0.002}}。",
        True,
    ),
    "generation_call_budget_exceeded": ErrorInfo(
        "GENERATION_CALL_BUDGET_EXCEEDED",
        "本次出题计划调用次数超过本地上限，任务没有启动。",
        "减少题数、候选数或并发数后重试。",
        True,
    ),
    "ai_cost_budget_exceeded": ErrorInfo(
        "AI_COST_BUDGET_EXCEEDED",
        "本次真实 AI 调用预计会超过本地费用预算，任务没有启动。",
        "降低候选数或题量，或在后端配置中调整预算。",
        True,
    ),
    "csrf_cross_site": ErrorInfo(
        "CSRF_CROSS_SITE",
        "浏览器跨站请求被本地安全策略拒绝。",
        "请从平台页面内操作；如果持续失败，请刷新页面。",
        False,
    ),
    "csrf_bad_origin": ErrorInfo(
        "CSRF_BAD_ORIGIN",
        "浏览器请求来源不在允许列表中。",
        "请使用本机平台地址访问，不要从其他网页调用本地 API。",
        False,
    ),
    "csrf_bad_referer": ErrorInfo(
        "CSRF_BAD_REFERER",
        "浏览器请求来源页不在允许列表中。",
        "请使用本机平台地址访问，不要从其他网页调用本地 API。",
        False,
    ),
    "csrf_header_missing": ErrorInfo(
        "CSRF_HEADER_MISSING",
        "请求缺少平台客户端标识，已被本地安全策略拒绝。",
        "刷新页面后重试；如果使用脚本调用，请带上平台客户端请求头。",
        True,
    ),
}

DEFAULT_ERROR_CATALOG_EXAMPLES: tuple[tuple[int, str, str], ...] = (
    (400, "BAD_REQUEST", "请求参数不完整或格式不正确。"),
    (403, "FORBIDDEN", "当前操作被安全策略拒绝。"),
    (404, "RESOURCE_NOT_FOUND", "请求的数据不存在或已经不可用。"),
    (409, "CONFLICT", "当前数据状态已变化，本次操作没有覆盖新状态。"),
    (429, "RATE_LIMITED", "请求过于频繁或外部服务限流。"),
    (500, "INTERNAL_ERROR", "系统处理请求时出现异常。"),
)


def default_error_info(status_code: int, detail_text: str) -> ErrorInfo:
    lowered = detail_text.lower()
    if "not found" in lowered:
        return ErrorInfo("RESOURCE_NOT_FOUND", "请求的数据不存在或已经不可用。", "刷新页面或返回上一层重新选择。", False)
    if "strict test" in lowered or "strict tests" in lowered:
        return ErrorInfo("STRICT_TEST_ACTION_DISABLED", "严格测试中禁用该操作。", "完成或退出严格测试后，在普通练习中使用该功能。", False)
    if "cannot cancel" in lowered or "cannot retry" in lowered:
        return ErrorInfo("INVALID_TASK_STATE", "当前任务状态不允许这个操作。", "刷新任务状态后再选择可用操作。", True)
    if "unsupported" in lowered and "schema" in lowered:
        return ErrorInfo("UNSUPPORTED_IMPORT_SCHEMA", "文件格式不是平台支持的导入格式。", "请使用平台导出的题库包或历史包。", False)
    if status_code == 400:
        return ErrorInfo("BAD_REQUEST", "请求参数不完整或格式不正确。", "检查输入内容后重试。", False)
    if status_code == 403:
        return ErrorInfo("FORBIDDEN", "当前操作被安全策略拒绝。", "刷新页面后重试；如果持续失败，请导出诊断包。", False)
    if status_code == 404:
        return ErrorInfo("RESOURCE_NOT_FOUND", "请求的数据不存在或已经不可用。", "刷新页面或返回上一层重新选择。", False)
    if status_code == 409:
        return ErrorInfo("CONFLICT", "当前数据状态已变化，本次操作没有覆盖新状态。", "刷新状态后再重试。", True)
    if status_code == 429:
        return ErrorInfo("RATE_LIMITED", "请求过于频繁或外部服务限流。", "稍后重试，或降低并发。", True, 30)
    return ErrorInfo("INTERNAL_ERROR", "系统处理请求时出现异常。", "导出诊断包并保留当前操作步骤。", False)


def error_info_from_detail(status_code: int, detail: Any) -> tuple[ErrorInfo, dict[str, Any]]:
    details_safe: dict[str, Any] = {}
    if isinstance(detail, dict):
        details_safe = sanitize_payload(detail)
        raw_code = str(detail.get("error_code") or "").strip()
        if raw_code:
            mapped = COMMON_ERROR_MAP.get(raw_code) or COMMON_ERROR_MAP.get(raw_code.lower())
            if mapped:
                return mapped, details_safe
            return ErrorInfo(
                raw_code.upper(),
                str(detail.get("user_message") or detail.get("message") or detail.get("detail") or "请求未完成。"),
                str(detail.get("action") or "刷新状态后重试，必要时导出诊断包。"),
                bool(detail.get("retryable", status_code in {409, 429, 503})),
                detail.get("retry_after_seconds"),
            ), details_safe
        detail_text = str(detail.get("detail") or detail)
    else:
        detail_text = str(detail)
        details_safe = {"detail": detail_text}
    return COMMON_ERROR_MAP.get(detail_text, default_error_info(status_code, detail_text)), details_safe


def error_envelope(
    *,
    status_code: int,
    detail: Any,
    trace_id: str | None = None,
) -> dict[str, Any]:
    info, details_safe = error_info_from_detail(status_code, detail)
    info = localized_error_info(info)
    return {
        "detail": detail,
        "error_code": info.error_code,
        "user_message": info.user_message,
        "message_key": f"errors.{info.error_code.lower()}.message",
        "message_params": {},
        "action_key": f"errors.{info.error_code.lower()}.action",
        "action": info.action,
        "retryable": info.retryable,
        "retry_after_seconds": info.retry_after_seconds,
        "trace_id": trace_id or uuid4().hex,
        "details_safe": details_safe,
    }


def _catalog_item(
    *,
    info: ErrorInfo,
    source: str,
    match_key: str | None = None,
    http_status: int | None = None,
) -> dict[str, Any]:
    info = localized_error_info(info)
    return {
        "error_code": info.error_code,
        "user_message": info.user_message,
        "message_key": f"errors.{info.error_code.lower()}.message",
        "message_params": {},
        "action_key": f"errors.{info.error_code.lower()}.action",
        "action": info.action,
        "retryable": info.retryable,
        "retry_after_seconds": info.retry_after_seconds,
        "source": source,
        "match_key": match_key,
        "http_status": http_status,
    }


def error_catalog() -> dict[str, Any]:
    common_items = [
        _catalog_item(info=info, source="common_error_map", match_key=key)
        for key, info in sorted(COMMON_ERROR_MAP.items(), key=lambda row: row[1].error_code)
    ]
    default_items = []
    for status_code, expected_code, detail in DEFAULT_ERROR_CATALOG_EXAMPLES:
        info = default_error_info(status_code, detail)
        if info.error_code == expected_code:
            default_items.append(_catalog_item(info=info, source="default_http_status", http_status=status_code))
    items = common_items + default_items
    return {
        "schema_version": "error_catalog_v1",
        "common_count": len(common_items),
        "default_count": len(default_items),
        "items": items,
    }


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", None) or request.headers.get("x-request-id") or uuid4().hex
    payload = error_envelope(status_code=exc.status_code, detail=exc.detail, trace_id=trace_id)
    headers = dict(exc.headers or {})
    headers["X-Trace-Id"] = trace_id
    headers["X-Error-Code"] = payload["error_code"]
    return JSONResponse(payload, status_code=exc.status_code, headers=headers)
