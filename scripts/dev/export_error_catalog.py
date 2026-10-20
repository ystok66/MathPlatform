from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "apps" / "api" / "app"
JSON_OUTPUT = ROOT / "docs" / "generated" / "error_catalog.json"
MD_OUTPUT = ROOT / "docs" / "generated" / "error_catalog.md"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app.ai_providers import normalize_provider_error  # noqa: E402
from apps.api.app.errors import error_catalog, error_info_from_detail  # noqa: E402


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)


def _literal_int(node: ast.AST | None) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return int(node.value)
    try:
        value = ast.literal_eval(node) if node is not None else None
    except (ValueError, SyntaxError):
        return None
    return int(value) if isinstance(value, int) else None


def _string_template(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for item in node.values:
            if isinstance(item, ast.Constant):
                parts.append(str(item.value))
            elif isinstance(item, ast.FormattedValue):
                try:
                    parts.append("{" + ast.unparse(item.value) + "}")
                except Exception:
                    parts.append("{}")
        return "".join(parts)
    return None


def _literalish(node: ast.AST) -> Any:
    template = _string_template(node)
    if template is not None:
        return template
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        try:
            return ast.unparse(node)
        except Exception:
            return type(node).__name__


def _partial_dict(node: ast.Dict) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in zip(node.keys, node.values):
        if key_node is None:
            continue
        key = _literalish(key_node)
        if isinstance(key, str):
            result[key] = _literalish(value_node)
    return result


def _literal_detail(node: ast.AST | None) -> tuple[str, Any]:
    if node is None:
        return "missing", None
    template = _string_template(node)
    if template is not None:
        kind = "template" if isinstance(node, ast.JoinedStr) else "string"
        return kind, template
    if isinstance(node, ast.Dict):
        return "dict", _partial_dict(node)
    try:
        value = ast.literal_eval(node)
        if isinstance(value, dict):
            return "dict", value
        return type(value).__name__, value
    except (ValueError, SyntaxError):
        try:
            return "expression", ast.unparse(node)
        except Exception:
            return "expression", type(node).__name__


def _normalized_error(status_code: int | None, detail: Any) -> dict[str, Any]:
    info, details_safe = error_info_from_detail(status_code or 500, detail)
    return {
        "error_code": info.error_code,
        "user_message": info.user_message,
        "action": info.action,
        "retryable": info.retryable,
        "retry_after_seconds": info.retry_after_seconds,
        "details_safe": details_safe,
    }


def _raw_detail_code(detail: Any) -> str | None:
    if not isinstance(detail, dict):
        return None
    raw = detail.get("error_code") or detail.get("code")
    return str(raw) if raw else None


def _scan_source_occurrences(source_root: Path = SOURCE_ROOT) -> list[dict[str, Any]]:
    occurrences: list[dict[str, Any]] = []
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            call = node.exc
            call_name = _call_name(call.func)
            short_name = call_name.rsplit(".", 1)[-1]
            if short_name == "HTTPException":
                status_code = _literal_int(_keyword(call, "status_code") or (call.args[0] if call.args else None))
                detail_kind, detail = _literal_detail(_keyword(call, "detail") or (call.args[1] if len(call.args) > 1 else None))
                normalized = _normalized_error(status_code, detail)
                occurrences.append(
                    {
                        "source_type": "http_exception",
                        "file": _relative(path),
                        "line": int(getattr(node, "lineno", 0) or 0),
                        "status_code": status_code,
                        "detail_kind": detail_kind,
                        "detail": detail,
                        "raw_detail_code": _raw_detail_code(detail),
                        "envelope_error_code": normalized["error_code"],
                        "user_message": normalized["user_message"],
                        "action": normalized["action"],
                        "retryable": normalized["retryable"],
                        "retry_after_seconds": normalized["retry_after_seconds"],
                    }
                )
            elif short_name in {"ValueError", "RuntimeError"}:
                detail_kind, detail = _literal_detail(call.args[0] if call.args else None)
                occurrences.append(
                    {
                        "source_type": short_name,
                        "file": _relative(path),
                        "line": int(getattr(node, "lineno", 0) or 0),
                        "status_code": None,
                        "detail_kind": detail_kind,
                        "detail": detail,
                        "raw_detail_code": None,
                        "envelope_error_code": None,
                        "user_message": None,
                        "action": None,
                        "retryable": None,
                        "retry_after_seconds": None,
                    }
                )
    return sorted(occurrences, key=lambda row: (row["file"], row["line"], row["source_type"]))


def _provider_error_samples() -> list[dict[str, Any]]:
    request = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")
    sample_errors: list[tuple[str, Exception]] = [
        ("timeout_exception", httpx.TimeoutException("request timed out")),
        ("connect_error", httpx.ConnectError("connection failed", request=request)),
        ("bad_json", ValueError("json parse failed")),
        ("unknown_blank", RuntimeError("")),
    ]
    for status_code in (400, 401, 403, 429, 500):
        response = httpx.Response(status_code, request=request, text=f"provider status {status_code}")
        sample_errors.append((f"http_{status_code}", httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)))
    return [
        {
            "sample": sample,
            **normalize_provider_error(error, "sample_provider"),
        }
        for sample, error in sample_errors
    ]


def render_error_catalog_export() -> dict[str, Any]:
    registered = error_catalog()
    occurrences = _scan_source_occurrences()
    provider_errors = _provider_error_samples()
    registered_codes = {str(item["error_code"]) for item in registered["items"]}
    dynamic_codes = {
        str(row["envelope_error_code"])
        for row in occurrences
        if row.get("envelope_error_code") and row.get("source_type") == "http_exception"
    }
    provider_codes = {str(row["error_code"]).upper() for row in provider_errors}
    raw_detail_codes = {str(row["raw_detail_code"]).upper() for row in occurrences if row.get("raw_detail_code")}
    unregistered_dynamic_codes = sorted((dynamic_codes | provider_codes | raw_detail_codes) - registered_codes)
    counts = Counter(str(row["source_type"]) for row in occurrences)
    return {
        "schema_version": "error_catalog_export_v1",
        "source": "apps.api.app.errors.error_catalog + source scan + provider error normalization",
        "registered_catalog": registered,
        "registered_error_codes": sorted(registered_codes),
        "provider_error_count": len(provider_errors),
        "provider_errors": sorted(provider_errors, key=lambda row: (row["error_code"], row["sample"])),
        "source_occurrence_count": len(occurrences),
        "source_counts": dict(sorted(counts.items())),
        "source_occurrences": occurrences,
        "unregistered_dynamic_codes": unregistered_dynamic_codes,
    }


def _md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text.replace("|", "\\|").replace("\n", " ")


def render_error_catalog_markdown(payload: dict[str, Any]) -> str:
    registered = payload["registered_catalog"]
    lines = [
        "# 错误码目录",
        "",
        "> 自动生成文件。来源：`scripts/dev/export_error_catalog.py`、`apps/api/app/errors.py`、`apps/api/app/ai_providers.py` 和 `apps/api/app` 源码静态扫描。不要手工编辑生成区内容。",
        "",
        "## 摘要",
        "",
        f"- schema_version: `{payload['schema_version']}`",
        f"- registered_count: `{len(payload['registered_error_codes'])}`",
        f"- provider_error_count: `{payload['provider_error_count']}`",
        f"- source_occurrence_count: `{payload['source_occurrence_count']}`",
        f"- source_counts: `{json.dumps(payload['source_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- unregistered_dynamic_code_count: `{len(payload['unregistered_dynamic_codes'])}`",
        "",
        "## 已注册稳定错误码",
        "",
        "| 错误码 | 来源 | HTTP | 匹配 key | 可重试 | 建议等待 | 用户提示 | 下一步 |",
        "|---|---|---:|---|---|---:|---|---|",
    ]
    for item in registered["items"]:
        lines.append(
            "| "
            f"`{_md_escape(item['error_code'])}` | "
            f"`{_md_escape(item.get('source'))}` | "
            f"{_md_escape(item.get('http_status'))} | "
            f"`{_md_escape(item.get('match_key'))}` | "
            f"{'是' if item.get('retryable') else '否'} | "
            f"{_md_escape(item.get('retry_after_seconds'))} | "
            f"{_md_escape(item.get('user_message'))} | "
            f"{_md_escape(item.get('action'))} |"
        )

    lines.extend(
        [
            "",
            "## Provider 归一化错误",
            "",
            "| Provider 错误码 | 样例 | HTTP | 可重试 | 说明 |",
            "|---|---|---:|---|---|",
        ]
    )
    for row in payload["provider_errors"]:
        lines.append(
            "| "
            f"`{_md_escape(str(row['error_code']).upper())}` | "
            f"`{_md_escape(row.get('sample'))}` | "
            f"{_md_escape(row.get('http_status'))} | "
            f"{'是' if row.get('retryable') else '否'} | "
            f"{_md_escape(row.get('message'))} |"
        )

    lines.extend(["", "## 未注册动态错误码", ""])
    if payload["unregistered_dynamic_codes"]:
        lines.append("| 错误码 | 说明 |")
        lines.append("|---|---|")
        for code in payload["unregistered_dynamic_codes"]:
            lines.append(f"| `{_md_escape(code)}` | 源码或 provider 归一化中可出现，但未登记在 `COMMON_ERROR_MAP`/默认 HTTP 模板中。 |")
    else:
        lines.append("无。")

    lines.extend(
        [
            "",
            "## HTTPException 源码出现位置",
            "",
            "| 文件 | 行 | HTTP | detail 类型 | detail / error_code | envelope 错误码 | 可重试 |",
            "|---|---:|---:|---|---|---|---|",
        ]
    )
    for row in payload["source_occurrences"]:
        if row["source_type"] != "http_exception":
            continue
        detail_display = row.get("raw_detail_code") or row.get("detail")
        lines.append(
            "| "
            f"`{_md_escape(row['file'])}` | "
            f"{row['line']} | "
            f"{_md_escape(row.get('status_code'))} | "
            f"`{_md_escape(row.get('detail_kind'))}` | "
            f"`{_md_escape(detail_display)}` | "
            f"`{_md_escape(row.get('envelope_error_code'))}` | "
            f"{'是' if row.get('retryable') else '否'} |"
        )

    lines.extend(
        [
            "",
            "## Python 异常源码出现位置",
            "",
            "| 文件 | 行 | 异常 | detail 类型 | detail |",
            "|---|---:|---|---|---|",
        ]
    )
    for row in payload["source_occurrences"]:
        if row["source_type"] == "http_exception":
            continue
        lines.append(
            "| "
            f"`{_md_escape(row['file'])}` | "
            f"{row['line']} | "
            f"`{_md_escape(row['source_type'])}` | "
            f"`{_md_escape(row.get('detail_kind'))}` | "
            f"`{_md_escape(row.get('detail'))}` |"
        )
    lines.append("")
    return "\n".join(lines)


def export_error_catalog(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> dict[str, Any]:
    payload = render_error_catalog_export()
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    md_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.write_text(render_error_catalog_markdown(payload), encoding="utf-8", newline="\n")
    return payload


def check_error_catalog(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> bool:
    payload = render_error_catalog_export()
    expected_json = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    expected_md = render_error_catalog_markdown(payload)
    current_json = json_output.read_text(encoding="utf-8") if json_output.exists() else ""
    current_md = md_output.read_text(encoding="utf-8") if md_output.exists() else ""
    return current_json == expected_json and current_md == expected_md


def main() -> int:
    parser = argparse.ArgumentParser(description="Export stable error catalog and source error occurrences.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if generated error catalog files are stale.")
    parser.add_argument("--json-output", type=Path, default=JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=MD_OUTPUT)
    args = parser.parse_args()

    if args.check:
        if not check_error_catalog(args.json_output, args.md_output):
            print(f"stale error catalog: {args.json_output} or {args.md_output}", file=sys.stderr)
            return 1
        return 0
    payload = export_error_catalog(args.json_output, args.md_output)
    print(f"wrote error_catalog.json and error_catalog.md with {len(payload['registered_error_codes'])} registered codes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
