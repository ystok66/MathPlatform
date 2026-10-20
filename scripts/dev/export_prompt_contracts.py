from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = ROOT / "prompts"
SCHEMA_DIR = ROOT / "packages" / "schemas"
JSON_OUTPUT = ROOT / "docs" / "generated" / "prompt_contracts.json"
MD_OUTPUT = ROOT / "docs" / "generated" / "prompt_contracts.md"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app.prompt_registry import (  # noqa: E402
    ACTIVE_PROMPT_MAP_SCHEMA_VERSION,
    PROMPT_METADATA_SCHEMA_VERSION,
    ROLE_PROMPT_CONTRACTS,
    load_active_prompt_map,
    prompt_hash,
    prompt_metadata,
    prompt_release_channel,
    prompt_role_from_name,
    prompt_version_from_name,
    safe_prompt_filename,
)


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _file_sha256(path: Path) -> str:
    return prompt_hash(path.read_text(encoding="utf-8"))


def _schema_summary(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "path": _relative(path),
            "exists": True,
            "valid_json": False,
            "error": str(exc),
        }
    properties = payload.get("properties") if isinstance(payload, dict) else {}
    return {
        "path": _relative(path),
        "exists": True,
        "valid_json": True,
        "schema": payload.get("$schema"),
        "title": payload.get("title"),
        "type": payload.get("type"),
        "required": payload.get("required") if isinstance(payload.get("required"), list) else [],
        "property_count": len(properties) if isinstance(properties, dict) else 0,
        "properties": sorted(properties.keys()) if isinstance(properties, dict) else [],
        "sha256": _file_sha256(path),
    }


def _resolve_schema_ref(ref: str) -> dict[str, Any]:
    if ref == "json_object" or ref.endswith("_v1") or "#" in ref:
        return {
            "ref": ref,
            "kind": "logical_contract",
            "exists": False,
            "note": "运行时逻辑契约；当前没有独立 JSON Schema 文件。",
        }
    candidate = ROOT / ref.replace("/", "\\")
    if candidate.exists() and candidate.is_file():
        return {
            "ref": ref,
            "kind": "json_schema_file",
            **_schema_summary(candidate),
        }
    return {
        "ref": ref,
        "kind": "missing_file",
        "exists": False,
        "note": "引用看起来像文件路径，但当前仓库没有找到对应文件。",
    }


def _prompt_rows(prompt_dir: Path = PROMPT_DIR) -> list[dict[str, Any]]:
    active_map = load_active_prompt_map(prompt_dir.parent / "prompts" / "active_prompts.json")
    active_by_role = active_map.get("prompts") or {}
    active_map_present = (prompt_dir / "active_prompts.json").exists()
    rows = []
    for path in sorted(prompt_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        role = prompt_role_from_name(path)
        metadata = prompt_metadata(path, text, role)
        selected_filename = safe_prompt_filename(active_by_role.get(role))
        filename = path.name
        effective_status = "active" if selected_filename is None or selected_filename == filename else "available"
        rows.append(
            {
                "filename": filename,
                "path": _relative(path),
                "role": role,
                "version": prompt_version_from_name(path),
                "effective_status": effective_status,
                "active_map_present": active_map_present,
                "release_channel": prompt_release_channel(path),
                "sha256": prompt_hash(text),
                "bytes": len(text.encode("utf-8")),
                "line_count": text.count("\n") + 1,
                "metadata": metadata,
                "content_md": text,
            }
        )
    return rows


def render_prompt_contracts() -> dict[str, Any]:
    prompt_rows = _prompt_rows()
    schema_files = [_schema_summary(path) for path in sorted(SCHEMA_DIR.glob("*.schema.json"))]
    refs = sorted(
        {
            str(contract["input_schema_ref"])
            for contract in ROLE_PROMPT_CONTRACTS.values()
        }
        | {
            str(contract["output_schema_ref"])
            for contract in ROLE_PROMPT_CONTRACTS.values()
        }
    )
    schema_refs = [_resolve_schema_ref(ref) for ref in refs]
    roles_with_prompts = sorted({row["role"] for row in prompt_rows})
    roles_without_prompt = sorted(set(ROLE_PROMPT_CONTRACTS) - set(roles_with_prompts) - {"misc"})
    return {
        "schema_version": "prompt_contracts_v1",
        "source": "apps.api.app.prompt_registry.ROLE_PROMPT_CONTRACTS + prompts/*.md + packages/schemas/*.schema.json",
        "prompt_metadata_schema_version": PROMPT_METADATA_SCHEMA_VERSION,
        "active_prompt_map_schema_version": ACTIVE_PROMPT_MAP_SCHEMA_VERSION,
        "role_count": len(ROLE_PROMPT_CONTRACTS),
        "prompt_count": len(prompt_rows),
        "schema_file_count": len(schema_files),
        "schema_ref_count": len(schema_refs),
        "roles_without_prompt": roles_without_prompt,
        "role_contracts": dict(sorted(ROLE_PROMPT_CONTRACTS.items())),
        "schema_files": schema_files,
        "schema_refs": schema_refs,
        "prompts": prompt_rows,
    }


def _md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text.replace("|", "\\|").replace("\n", " ")


def _fence(text: str) -> str:
    if "```" not in text:
        return f"```markdown\n{text.rstrip()}\n```"
    return f"````markdown\n{text.rstrip()}\n````"


def render_prompt_contracts_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Prompt 输入输出契约",
        "",
        "> 自动生成文件。来源：`scripts/dev/export_prompt_contracts.py`、`apps/api/app/prompt_registry.py`、`prompts/*.md` 和 `packages/schemas/*.schema.json`。不要手工编辑生成区内容。",
        "",
        "## 摘要",
        "",
        f"- schema_version: `{payload['schema_version']}`",
        f"- role_count: `{payload['role_count']}`",
        f"- prompt_count: `{payload['prompt_count']}`",
        f"- schema_file_count: `{payload['schema_file_count']}`",
        f"- schema_ref_count: `{payload['schema_ref_count']}`",
        f"- roles_without_prompt: `{', '.join(payload['roles_without_prompt']) or '无'}`",
        "",
        "## 角色契约",
        "",
        "| 角色 | 输入 schema | 输出 schema | 必需输出字段 | 模型能力 | 评测基线 |",
        "|---|---|---|---|---|---|",
    ]
    for role, contract in payload["role_contracts"].items():
        lines.append(
            "| "
            f"`{_md_escape(role)}` | "
            f"`{_md_escape(contract['input_schema_ref'])}` | "
            f"`{_md_escape(contract['output_schema_ref'])}` | "
            f"{', '.join(f'`{_md_escape(field)}`' for field in contract.get('required_output_fields', [])) or '无'} | "
            f"`{_md_escape(contract.get('compatible_models'))}` | "
            f"`{_md_escape(contract.get('eval_baseline'))}` |"
        )

    lines.extend(
        [
            "",
            "## Schema 引用解析",
            "",
            "| 引用 | 类型 | 是否存在文件 | 摘要 |",
            "|---|---|---|---|",
        ]
    )
    for row in payload["schema_refs"]:
        summary = row.get("title") or row.get("note") or f"{len(row.get('properties') or [])} properties"
        lines.append(
            "| "
            f"`{_md_escape(row['ref'])}` | "
            f"`{_md_escape(row.get('kind'))}` | "
            f"{'是' if row.get('exists') else '否'} | "
            f"{_md_escape(summary)} |"
        )

    lines.extend(
        [
            "",
            "## JSON Schema 文件",
            "",
            "| 文件 | title | 类型 | required | properties | SHA-256 |",
            "|---|---|---|---|---:|---|",
        ]
    )
    for row in payload["schema_files"]:
        lines.append(
            "| "
            f"`{_md_escape(row['path'])}` | "
            f"{_md_escape(row.get('title'))} | "
            f"`{_md_escape(row.get('type'))}` | "
            f"{', '.join(f'`{_md_escape(field)}`' for field in row.get('required', [])) or '无'} | "
            f"{row.get('property_count', 0)} | "
            f"`{_md_escape(row.get('sha256'))}` |"
        )

    lines.extend(
        [
            "",
            "## Prompt 文件清单",
            "",
            "| 文件 | 角色 | 版本 | 状态 | 发布通道 | 行数 | 字节 | SHA-256 | 输入 schema | 输出 schema |",
            "|---|---|---|---|---|---:|---:|---|---|---|",
        ]
    )
    for row in payload["prompts"]:
        metadata = row["metadata"]
        lines.append(
            "| "
            f"`{_md_escape(row['filename'])}` | "
            f"`{_md_escape(row['role'])}` | "
            f"`{_md_escape(row['version'])}` | "
            f"`{_md_escape(row['effective_status'])}` | "
            f"`{_md_escape(row['release_channel'])}` | "
            f"{row['line_count']} | "
            f"{row['bytes']} | "
            f"`{_md_escape(row['sha256'])}` | "
            f"`{_md_escape(metadata.get('input_schema_ref'))}` | "
            f"`{_md_escape(metadata.get('output_schema_ref'))}` |"
        )

    lines.extend(["", "## Prompt 全文", ""])
    for row in payload["prompts"]:
        lines.extend(
            [
                f"### `{row['filename']}`",
                "",
                f"- 角色：`{row['role']}`",
                f"- 输出 schema：`{row['metadata'].get('output_schema_ref')}`",
                "",
                _fence(row["content_md"]),
                "",
            ]
        )
    return "\n".join(lines)


def export_prompt_contracts(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> dict[str, Any]:
    payload = render_prompt_contracts()
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    md_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.write_text(render_prompt_contracts_markdown(payload), encoding="utf-8", newline="\n")
    return payload


def check_prompt_contracts(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> bool:
    payload = render_prompt_contracts()
    expected_json = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    expected_md = render_prompt_contracts_markdown(payload)
    current_json = json_output.read_text(encoding="utf-8") if json_output.exists() else ""
    current_md = md_output.read_text(encoding="utf-8") if md_output.exists() else ""
    return current_json == expected_json and current_md == expected_md


def main() -> int:
    parser = argparse.ArgumentParser(description="Export prompt role contracts, schema refs, and prompt file metadata.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if generated prompt contract files are stale.")
    parser.add_argument("--json-output", type=Path, default=JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=MD_OUTPUT)
    args = parser.parse_args()

    if args.check:
        if not check_prompt_contracts(args.json_output, args.md_output):
            print(f"stale prompt contracts: {args.json_output} or {args.md_output}", file=sys.stderr)
            return 1
        return 0
    payload = export_prompt_contracts(args.json_output, args.md_output)
    print(f"wrote prompt_contracts.json and prompt_contracts.md with {payload['prompt_count']} prompts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
