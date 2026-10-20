from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
JSON_OUTPUT = ROOT / "docs" / "generated" / "security_threat_model.json"
MD_OUTPUT = ROOT / "docs" / "generated" / "security_threat_model.md"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app.audit import SENSITIVE_KEY_FRAGMENTS, SENSITIVE_KEYS  # noqa: E402
from apps.api.app.config import FEATURE_FLAGS, SECRET_SETTING_NAMES, Settings  # noqa: E402
from apps.api.app.prompt_security import (  # noqa: E402
    DEFAULT_MAX_JSON_CHARS,
    DEFAULT_MAX_TEXT_CHARS,
    PROMPT_INJECTION_GUARD,
    PROMPT_SECURITY_VERSION,
)
from apps.api.app.security import (  # noqa: E402
    CSRF_HEADER_NAME,
    CSRF_HEADER_VALUE,
    LOOPBACK_HOSTS,
    UNSAFE_METHODS,
)
from apps.api.app.structured_logging import REQUEST_LOG_SCHEMA_VERSION  # noqa: E402


SOURCE_FILES = {
    "api_security": ROOT / "apps" / "api" / "app" / "security.py",
    "api_main": ROOT / "apps" / "api" / "app" / "main.py",
    "api_config": ROOT / "apps" / "api" / "app" / "config.py",
    "api_secrets": ROOT / "apps" / "api" / "app" / "secrets.py",
    "api_audit": ROOT / "apps" / "api" / "app" / "audit.py",
    "api_structured_logging": ROOT / "apps" / "api" / "app" / "structured_logging.py",
    "api_admin": ROOT / "apps" / "api" / "app" / "routers" / "admin.py",
    "api_settings": ROOT / "apps" / "api" / "app" / "routers" / "settings.py",
    "api_practice": ROOT / "apps" / "api" / "app" / "routers" / "practice.py",
    "prompt_security": ROOT / "apps" / "api" / "app" / "prompt_security.py",
    "frontend_math_content": ROOT / "apps" / "web" / "app" / "components" / "MathContent.tsx",
    "frontend_page": ROOT / "apps" / "web" / "app" / "page.tsx",
    "frontend_next_config": ROOT / "apps" / "web" / "next.config.js",
    "launcher": ROOT / "tools" / "launcher" / "math_platform_launcher.py",
    "make_release": ROOT / "scripts" / "pack" / "make_release.ps1",
}


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _contains(file_key: str, needle: str) -> bool:
    return needle in _read(SOURCE_FILES[file_key])


def _regex(file_key: str, pattern: str, flags: int = 0) -> re.Match[str] | None:
    return re.search(pattern, _read(SOURCE_FILES[file_key]), flags=flags)


def _line_for(file_key: str, pattern: str) -> int | None:
    text = _read(SOURCE_FILES[file_key])
    for index, line in enumerate(text.splitlines(), start=1):
        if pattern in line:
            return index
    return None


def _source_ref(file_key: str, pattern: str, label: str) -> dict[str, Any]:
    path = SOURCE_FILES[file_key]
    return {
        "label": label,
        "file": _relative(path),
        "line": _line_for(file_key, pattern),
        "pattern": pattern,
        "found": pattern in _read(path),
    }


def _challenge_ttl_seconds() -> int | None:
    match = _regex("api_admin", r"CHALLENGE_TTL_SECONDS\s*=\s*(\d+)")
    return int(match.group(1)) if match else None


def _source_checks() -> dict[str, Any]:
    math_content = _read(SOURCE_FILES["frontend_math_content"])
    next_config = _read(SOURCE_FILES["frontend_next_config"])
    make_release = _read(SOURCE_FILES["make_release"])
    return {
        "browser_write_guard": {
            "path": _relative(SOURCE_FILES["api_security"]),
            "csrf_header_name": CSRF_HEADER_NAME,
            "csrf_header_value": CSRF_HEADER_VALUE,
            "unsafe_methods": sorted(UNSAFE_METHODS),
            "loopback_hosts": sorted(LOOPBACK_HOSTS),
            "default_cors_origins": Settings.model_fields["cors_origins"].default,
            "main_uses_cors_middleware": _contains("api_main", "CORSMiddleware"),
            "main_uses_write_guard": _contains("api_main", "unsafe_browser_request_rejection"),
            "rejects_cross_site_fetch_metadata": _contains("api_security", 'sec_fetch_site == "cross-site"'),
            "requires_platform_header": _contains("api_security", "CSRF_HEADER_NAME") and _contains("api_security", "CSRF_HEADER_VALUE"),
        },
        "secret_storage": {
            "path": _relative(SOURCE_FILES["api_secrets"]),
            "secret_setting_names": sorted(SECRET_SETTING_NAMES),
            "uses_dpapi_on_windows": _contains("api_secrets", "CryptProtectData") and _contains("api_secrets", "CryptUnprotectData"),
            "dpapi_key_filename": "config_encryption_key.dpapi" if _contains("api_secrets", "config_encryption_key.dpapi") else None,
            "env_plaintext_fallback_is_opt_in_on_windows_failure": _contains("api_secrets", "ALLOW_PLAINTEXT_CONFIG_ENCRYPTION_KEY"),
            "model_settings_response_redacts_api_key": _contains("api_settings", "api_key=None") and _contains("api_settings", "has_api_key"),
            "api_key_clear_endpoint": _contains("api_settings", '@router.delete("/models/api-key"'),
        },
        "logging_and_diagnostics": {
            "audit_path": _relative(SOURCE_FILES["api_audit"]),
            "request_log_path": _relative(SOURCE_FILES["api_structured_logging"]),
            "launcher_path": _relative(SOURCE_FILES["launcher"]),
            "request_log_schema_version": REQUEST_LOG_SCHEMA_VERSION,
            "sensitive_keys": sorted(SENSITIVE_KEYS),
            "sensitive_key_fragments": sorted(SENSITIVE_KEY_FRAGMENTS),
            "request_log_uses_sanitize_payload": _contains("api_structured_logging", "sanitize_payload"),
            "diagnostic_redacts_logs": _contains("launcher", "redact_secrets_in_text(tail_text(path))"),
            "backup_writes_sanitized_env": _contains("launcher", "write_sanitized_env"),
        },
        "prompt_boundary": {
            "path": _relative(SOURCE_FILES["prompt_security"]),
            "prompt_security_version": PROMPT_SECURITY_VERSION,
            "max_text_chars": DEFAULT_MAX_TEXT_CHARS,
            "max_json_chars": DEFAULT_MAX_JSON_CHARS,
            "guard_mentions_untrusted_fields": "untrusted data" in PROMPT_INJECTION_GUARD and "Do not execute instructions" in PROMPT_INJECTION_GUARD,
            "uses_bounded_payload": _contains("prompt_security", "bounded_payload"),
            "uses_guarded_system_prompt": _contains("prompt_security", "guarded_system_prompt"),
        },
        "frontend_content_safety": {
            "path": _relative(SOURCE_FILES["frontend_math_content"]),
            "escapes_html": "export function escapeHtml" in math_content,
            "sanitizes_rendered_html": "export function sanitizeRenderedHtml" in math_content,
            "blocks_dangerous_tags": "FORBIDDEN_TAG_PATTERN" in math_content,
            "blocks_event_handlers": "EVENT_HANDLER_ATTR_PATTERN" in math_content,
            "blocks_dangerous_urls": "DANGEROUS_URL_ATTR_PATTERN" in math_content and "javascript|vbscript|data|file|blob" in math_content,
            "katex_trust_false": "trust: false" in math_content,
            "max_math_source_chars": 20000 if "MAX_MATH_SOURCE_CHARS = 20000" in math_content else None,
            "uses_dangerously_set_inner_html_after_sanitizer": "dangerouslySetInnerHTML" in math_content and "renderMathMarkdown(source)" in math_content,
            "content_security_policy_header_found": "Content-Security-Policy" in next_config,
            "csp_object_src_none": "object-src 'none'" in next_config,
            "csp_frame_ancestors_none": "frame-ancestors 'none'" in next_config,
            "csp_connects_to_local_api": "http://127.0.0.1:8001" in next_config and "http://localhost:8001" in next_config,
            "x_frame_options_deny": "X-Frame-Options" in next_config and "DENY" in next_config,
            "referrer_policy_same_origin": "Referrer-Policy" in next_config and "same-origin" in next_config,
        },
        "destructive_actions": {
            "path": _relative(SOURCE_FILES["api_admin"]),
            "challenge_ttl_seconds": _challenge_ttl_seconds(),
            "challenge_phrase_has_random_suffix": _contains("api_admin", "token_urlsafe(18)") and _contains("api_admin", "choice(CHALLENGE_ALPHABET)"),
            "challenge_is_one_time": _contains("api_admin", "DESTRUCTIVE_CHALLENGES.pop(challenge_id, None)"),
            "challenge_binds_target_fingerprint": _contains("api_admin", "target_fingerprint"),
            "clear_history_requires_challenge": _contains("api_admin", 'validate_destructive_challenge("clear_history"'),
        },
        "strict_test_boundary": {
            "path": _relative(SOURCE_FILES["api_practice"]),
            "strict_mode_blocks_generated_help": _contains("api_practice", 'session.mode == "strict_test"') and _contains("api_practice", "strict_no_ai"),
            "reveal_answer_endpoint_exists": _contains("api_practice", 'reveal-answer"'),
            "strict_reveal_answer_guard": _contains("api_practice", "reveal answer is disabled in strict test"),
            "answer_lock_error_code": _contains("api_practice", "answer_locked_after_reveal"),
            "solution_explanation_endpoint_exists": _contains("api_practice", 'solution-explanation"'),
        },
        "release_runtime_boundary": {
            "path": _relative(SOURCE_FILES["make_release"]),
            "api_binds_loopback": "set HOST=127.0.0.1" in make_release,
            "web_binds_loopback": "set HOSTNAME=127.0.0.1" in make_release,
            "web_uses_loopback_api_base": "NEXT_PUBLIC_API_BASE = \"http://127.0.0.1:8001\"" in make_release,
            "env_example_leaves_liaobots_key_empty": "LIAOBOTS_API_KEY=" in make_release,
            "release_readme_mentions_key_empty": "LIAOBOTS_API_KEY empty" in make_release,
        },
    }


def _checks_ok(checks: dict[str, Any]) -> bool:
    required = [
        checks["browser_write_guard"]["main_uses_cors_middleware"],
        checks["browser_write_guard"]["main_uses_write_guard"],
        checks["browser_write_guard"]["rejects_cross_site_fetch_metadata"],
        checks["browser_write_guard"]["requires_platform_header"],
        checks["secret_storage"]["uses_dpapi_on_windows"],
        checks["secret_storage"]["model_settings_response_redacts_api_key"],
        checks["logging_and_diagnostics"]["request_log_uses_sanitize_payload"],
        checks["logging_and_diagnostics"]["diagnostic_redacts_logs"],
        checks["prompt_boundary"]["guard_mentions_untrusted_fields"],
        checks["prompt_boundary"]["uses_guarded_system_prompt"],
        checks["frontend_content_safety"]["escapes_html"],
        checks["frontend_content_safety"]["sanitizes_rendered_html"],
        checks["frontend_content_safety"]["blocks_dangerous_urls"],
        checks["frontend_content_safety"]["katex_trust_false"],
        checks["frontend_content_safety"]["content_security_policy_header_found"],
        checks["frontend_content_safety"]["csp_object_src_none"],
        checks["frontend_content_safety"]["csp_frame_ancestors_none"],
        checks["frontend_content_safety"]["csp_connects_to_local_api"],
        checks["destructive_actions"]["challenge_is_one_time"],
        checks["destructive_actions"]["challenge_binds_target_fingerprint"],
        checks["strict_test_boundary"]["answer_lock_error_code"],
        checks["release_runtime_boundary"]["api_binds_loopback"],
        checks["release_runtime_boundary"]["web_binds_loopback"],
    ]
    return all(bool(item) for item in required)


def _boundaries() -> list[dict[str, Any]]:
    return [
        {
            "id": "B01",
            "name": "本地浏览器到 API 写请求边界",
            "assets": ["做题记录", "题库管理操作", "模型配置", "历史导入导出"],
            "trusted_side": "本机平台前端和无浏览器上下文的本地 CLI/API smoke。",
            "untrusted_side": "带 Origin/Referer/Sec-Fetch-Site 的浏览器请求、其他网页和扩展脚本。",
            "controls": ["CORS 允许来源白名单", "unsafe 写请求 CSRF header", "Sec-Fetch-Site cross-site 拒绝", "X-Trace-Id 记录"],
            "residual_risk": "本地同机进程仍可直接调用 API；当前产品定位是本地单用户，不提供完整登录权限隔离。",
        },
        {
            "id": "B02",
            "name": "API Key 与模型配置边界",
            "assets": ["LiaoBots/OpenAI-compatible API Key", "角色模型配置", "价格表和预算配置"],
            "trusted_side": "后端 settings/secrets 模块和加密配置表。",
            "untrusted_side": "前端展示、诊断包、日志、备份包、题库/历史迁移包。",
            "controls": ["前端响应 api_key=None", "Windows DPAPI 保护本地加密密钥", "audit/request log 脱敏", "backup 写 env.sanitized", "清除 API Key 端点"],
            "residual_risk": "非 Windows 或显式允许 fallback 时可能使用 .env 存储加密 key；本机管理员仍可读取本地数据文件。",
        },
        {
            "id": "B03",
            "name": "题面 Markdown/KaTeX 渲染边界",
            "assets": ["前端页面 DOM", "题面、答案、AI 解释文本"],
            "trusted_side": "MathContent 渲染器输出的清洗后 HTML。",
            "untrusted_side": "导入题面、AI 输出、用户自由文本、自定义标签说明。",
            "controls": ["HTML escape", "危险标签移除", "事件属性移除", "危险 URL 协议移除", "KaTeX trust=false", "输入长度上限", "Next Content-Security-Policy", "X-Frame-Options DENY"],
            "residual_risk": "当前是轻量正则清洗加 CSP 双层保护，不等同完整浏览器级 HTML sanitizer；仍建议后续补 DOMPurify 级回归样例。",
        },
        {
            "id": "B04",
            "name": "Agent/Prompt 输入边界",
            "assets": ["出题发布阈值", "判分规则", "严格测试边界", "Prompt schema"],
            "trusted_side": "系统 prompt、角色契约、平台 policy 字段和 JSON schema。",
            "untrusted_side": "题目、学生输入、示例题、证据、provider 输出、自定义描述和 Markdown。",
            "controls": ["agent_input_envelope_v1", "untrusted_fields 显式标记", "字段级截断", "JSON 总长截断", "Prompt injection guard 自动追加"],
            "residual_risk": "Prompt 约束不能替代代码校验；发布、状态迁移和不可逆操作必须继续由后端代码决定。",
        },
        {
            "id": "B05",
            "name": "严格测试完整性边界",
            "assets": ["严格测试成绩", "无提示答题过程", "测试报告可信度"],
            "trusted_side": "严格测试 session 的服务端状态。",
            "untrusted_side": "学生请求、题目质疑、讲解提问、AI 复核输出。",
            "controls": ["strict_test/strict_no_ai 服务端判断", "禁提示/看答案/讲解", "AI 复核严格模式脱敏", "已看答案后提交 409 锁题"],
            "residual_risk": "严格测试外的复习和普通练习允许帮助；报告必须按 mode/session 过滤解释。",
        },
        {
            "id": "B06",
            "name": "危险管理操作边界",
            "assets": ["历史答题记录", "题库题目", "生成题和审计日志"],
            "trusted_side": "一次性 destructive challenge 和目标 fingerprint 校验。",
            "untrusted_side": "误点、重复点击、旧页面上的 stale 请求。",
            "controls": ["挑战短语", "600 秒 TTL", "一次性 pop", "target_count/target_fingerprint 绑定", "审计日志"],
            "residual_risk": "challenge 不是用户认证；同机页面持有挑战后仍可执行对应操作。",
        },
        {
            "id": "B07",
            "name": "诊断包、日志、备份和迁移包边界",
            "assets": ["本地数据库", "日志", "备份包", "诊断包", "历史/题库迁移包"],
            "trusted_side": "脱敏后的导出内容和 manifest/hash 记录。",
            "untrusted_side": "可被复制到其他电脑或发给他人的文件包。",
            "controls": ["sanitize_payload", "redact_secrets_in_text", "env.sanitized", "release manifest", "dry-run 导入 hash 对比"],
            "residual_risk": "题目、答题内容和 AI 解释本身可能属于隐私；导出前仍需用户判断是否可分享。",
        },
    ]


def _threats() -> list[dict[str, Any]]:
    return [
        {
            "id": "T01",
            "title": "其他网页诱导本地 API 写请求",
            "severity": "high",
            "boundaries": ["B01"],
            "controls": ["csrf_cross_site", "csrf_bad_origin", "csrf_bad_referer", "csrf_header_missing"],
            "verification": ["tests/unit/test_api_security.py"],
        },
        {
            "id": "T02",
            "title": "API Key 在前端、日志、备份或诊断包中泄露",
            "severity": "high",
            "boundaries": ["B02", "B07"],
            "controls": ["api_key=None", "DPAPI/Fernet", "sanitize_payload", "env.sanitized", "redact_secrets_in_text"],
            "verification": ["tests/unit/test_config.py", "tests/unit/test_structured_logging.py", "tests/unit/test_diagnostics.py"],
        },
        {
            "id": "T03",
            "title": "导入题面或 AI 输出触发前端脚本执行",
            "severity": "high",
            "boundaries": ["B03"],
            "controls": ["escapeHtml", "sanitizeRenderedHtml", "KaTeX trust=false", "危险 URL 协议移除", "Content-Security-Policy"],
            "verification": ["tests/unit/test_frontend_content_security.py"],
        },
        {
            "id": "T04",
            "title": "题目/学生输入/示例题中的 Prompt Injection 改写角色边界",
            "severity": "high",
            "boundaries": ["B04"],
            "controls": ["agent_input_envelope_v1", "Prompt injection guard", "字段长度限制"],
            "verification": ["tests/unit/test_prompt_security_envelope.py"],
        },
        {
            "id": "T05",
            "title": "严格测试中通过提示、看答案或 AI 问答泄露答案",
            "severity": "high",
            "boundaries": ["B05"],
            "controls": ["strict_test 服务端判断", "strict_no_ai", "answer_locked_after_reveal"],
            "verification": ["tests/unit/test_strict_ai_gate.py"],
        },
        {
            "id": "T06",
            "title": "误清空历史或误删题目",
            "severity": "medium",
            "boundaries": ["B06"],
            "controls": ["destructive challenge", "target fingerprint", "审计日志"],
            "verification": ["tests/unit/test_admin_destructive_challenge.py if present", "tests/unit/test_question_delete.py if present"],
        },
        {
            "id": "T07",
            "title": "发布包服务暴露到局域网",
            "severity": "medium",
            "boundaries": ["B01", "B07"],
            "controls": ["HOST=127.0.0.1", "HOSTNAME=127.0.0.1", "CORS_ORIGINS loopback"],
            "verification": ["tests/unit/test_release_checklist_export.py", "tests/unit/test_api_security.py"],
        },
    ]


def _known_gaps() -> list[dict[str, str]]:
    return [
        {
            "id": "G01",
            "severity": "accepted_current_scope",
            "description": "本地单用户版本没有登录/多用户权限系统；CSRF/CORS 只保护浏览器来源，不能阻止同机恶意进程直接调用 API。",
        },
        {
            "id": "G02",
            "severity": "follow_up",
            "description": "前端内容安全当前是轻量清洗、KaTeX trust=false 和 CSP header；建议后续增加更完整的 DOM sanitizer 回归样例。",
        },
        {
            "id": "G03",
            "severity": "follow_up",
            "description": "DPAPI 是 Windows 主路径；非 Windows 或显式 fallback 场景需要重新评估密钥静态存储安全。",
        },
        {
            "id": "G04",
            "severity": "follow_up",
            "description": "destructive challenge 是误操作保护，不是认证；如果未来开放局域网或多用户，必须引入独立认证和权限。",
        },
        {
            "id": "G05",
            "severity": "follow_up",
            "description": "本文件是静态事实源，不替代动态安全测试、依赖漏洞扫描或渗透测试。",
        },
    ]


def _source_refs() -> list[dict[str, Any]]:
    return [
        _source_ref("api_security", "CSRF_HEADER_NAME", "CSRF header 常量"),
        _source_ref("api_main", "unsafe_browser_request_rejection", "写请求防护中间件"),
        _source_ref("api_secrets", "CryptProtectData", "Windows DPAPI 加密"),
        _source_ref("api_audit", "SENSITIVE_KEYS", "敏感字段脱敏"),
        _source_ref("api_structured_logging", "REQUEST_LOG_SCHEMA_VERSION", "结构化请求日志"),
        _source_ref("prompt_security", "PROMPT_INJECTION_GUARD", "Prompt injection guard"),
        _source_ref("frontend_math_content", "sanitizeRenderedHtml", "前端内容清洗"),
        _source_ref("frontend_next_config", "Content-Security-Policy", "前端 CSP header"),
        _source_ref("api_admin", "validate_destructive_challenge", "危险操作挑战校验"),
        _source_ref("api_practice", "answer_locked_after_reveal", "看答案锁题错误码"),
        _source_ref("make_release", "set HOST=127.0.0.1", "发布包 API loopback 绑定"),
    ]


def render_security_threat_model() -> dict[str, Any]:
    checks = _source_checks()
    return {
        "schema_version": "security_threat_model_v1",
        "source": "apps/api security/config/secrets/audit/logging/practice/admin + apps/web MathContent + launcher/release scripts",
        "source_checks_ok": _checks_ok(checks),
        "source_checks": checks,
        "feature_flags": {key: value for key, value in sorted(FEATURE_FLAGS.items())},
        "boundaries": _boundaries(),
        "threats": _threats(),
        "known_gaps": _known_gaps(),
        "source_refs": _source_refs(),
        "verification": [
            {"command": r"python -m pytest -q tests\unit\test_api_security.py", "scope": "浏览器写请求 CSRF/CORS/loopback 边界"},
            {"command": r"python -m pytest -q tests\unit\test_frontend_content_security.py", "scope": "前端题面/KaTeX 内容安全"},
            {"command": r"python -m pytest -q tests\unit\test_prompt_security_envelope.py", "scope": "Agent 输入安全信封与 prompt guard"},
            {"command": r"python -m pytest -q tests\unit\test_structured_logging.py tests\unit\test_diagnostics.py", "scope": "日志和诊断包脱敏"},
            {"command": r"python scripts\dev\export_security_threat_model.py --check", "scope": "安全威胁模型生成物防漂移"},
        ],
    }


def _md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text.replace("|", "\\|").replace("\n", " ")


def render_security_threat_model_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 安全边界与威胁模型",
        "",
        "> 自动生成文件。来源：`scripts/dev/export_security_threat_model.py`、后端安全/配置/日志/练习/管理模块、前端 MathContent 和发布脚本。不要手工编辑生成区内容。",
        "",
        "## 摘要",
        "",
        f"- schema_version: `{payload['schema_version']}`",
        f"- source_checks_ok: `{payload['source_checks_ok']}`",
        f"- boundary_count: `{len(payload['boundaries'])}`",
        f"- threat_count: `{len(payload['threats'])}`",
        f"- known_gap_count: `{len(payload['known_gaps'])}`",
        "",
        "## 信任边界",
        "",
        "| ID | 名称 | 资产 | 可信侧 | 不可信侧 | 控制措施 | 剩余风险 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in payload["boundaries"]:
        lines.append(
            "| "
            f"`{_md_escape(row['id'])}` | "
            f"{_md_escape(row['name'])} | "
            f"{_md_escape(', '.join(row['assets']))} | "
            f"{_md_escape(row['trusted_side'])} | "
            f"{_md_escape(row['untrusted_side'])} | "
            f"{_md_escape(', '.join(row['controls']))} | "
            f"{_md_escape(row['residual_risk'])} |"
        )

    lines.extend(["", "## 威胁清单", "", "| ID | 严重度 | 威胁 | 边界 | 控制/错误码 | 验证 |", "|---|---|---|---|---|---|"])
    for row in payload["threats"]:
        lines.append(
            "| "
            f"`{_md_escape(row['id'])}` | "
            f"`{_md_escape(row['severity'])}` | "
            f"{_md_escape(row['title'])} | "
            f"{_md_escape(', '.join(row['boundaries']))} | "
            f"{_md_escape(', '.join(row['controls']))} | "
            f"{_md_escape(', '.join(row['verification']))} |"
        )

    lines.extend(["", "## 源码检查", "", "| 分组 | 检查项 | 结果 |", "|---|---|---|"])
    for group, checks in payload["source_checks"].items():
        for key, value in checks.items():
            lines.append(f"| `{_md_escape(group)}` | `{_md_escape(key)}` | `{_md_escape(value)}` |")

    lines.extend(["", "## 实现来源", "", "| 标签 | 文件 | 行 | 结果 |", "|---|---|---:|---|"])
    for ref in payload["source_refs"]:
        lines.append(f"| {_md_escape(ref['label'])} | `{_md_escape(ref['file'])}` | {ref.get('line') or ''} | `{_md_escape(ref['found'])}` |")

    lines.extend(["", "## 已知缺口", "", "| ID | 级别 | 说明 |", "|---|---|---|"])
    for row in payload["known_gaps"]:
        lines.append(f"| `{_md_escape(row['id'])}` | `{_md_escape(row['severity'])}` | {_md_escape(row['description'])} |")

    lines.extend(["", "## 验证命令", "", "| 命令 | 覆盖范围 |", "|---|---|"])
    for row in payload["verification"]:
        lines.append(f"| `{_md_escape(row['command'])}` | {_md_escape(row['scope'])} |")
    lines.append("")
    return "\n".join(lines)


def export_security_threat_model(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> dict[str, Any]:
    payload = render_security_threat_model()
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    md_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.write_text(render_security_threat_model_markdown(payload), encoding="utf-8", newline="\n")
    return payload


def check_security_threat_model(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> bool:
    payload = render_security_threat_model()
    expected_json = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    expected_md = render_security_threat_model_markdown(payload)
    current_json = json_output.read_text(encoding="utf-8") if json_output.exists() else ""
    current_md = md_output.read_text(encoding="utf-8") if md_output.exists() else ""
    return current_json == expected_json and current_md == expected_md


def main() -> int:
    parser = argparse.ArgumentParser(description="Export local security boundary and threat model facts.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if generated security threat model files are stale.")
    parser.add_argument("--json-output", type=Path, default=JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=MD_OUTPUT)
    args = parser.parse_args()

    if args.check:
        if not check_security_threat_model(args.json_output, args.md_output):
            print(f"stale security threat model: {args.json_output} or {args.md_output}", file=sys.stderr)
            return 1
        return 0
    payload = export_security_threat_model(args.json_output, args.md_output)
    print(f"wrote security_threat_model.json and security_threat_model.md with {len(payload['boundaries'])} boundaries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
