from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = ROOT / "docs" / "adr"
JSON_OUTPUT = ROOT / "docs" / "generated" / "adr_index.json"
MD_OUTPUT = ROOT / "docs" / "generated" / "adr_index.md"
TEMPLATE = ADR_DIR / "0000-template.md"
REQUIRED_SECTIONS = ["Status", "Date", "Decision", "Rationale", "Consequences"]


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _section_map(text: str) -> dict[str, str]:
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1).strip()] = text[start:end].strip()
    return sections


def _first_non_empty_line(value: str) -> str:
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _summary(value: str, max_chars: int = 180) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def _parse_adr(path: Path) -> dict[str, Any]:
    text = _read(path)
    title_match = re.search(r"(?m)^#\s+ADR\s+(\d+):\s+(.+?)\s*$", text)
    sections = _section_map(text)
    missing_sections = [name for name in REQUIRED_SECTIONS if not sections.get(name)]
    return {
        "id": title_match.group(1) if title_match else path.stem.split("-", 1)[0],
        "title": title_match.group(2).strip() if title_match else path.stem,
        "path": _relative(path),
        "status": _first_non_empty_line(sections.get("Status", "")) or "unknown",
        "date": _first_non_empty_line(sections.get("Date", "")) or "unknown",
        "decision_summary": _summary(sections.get("Decision", "")),
        "rationale_summary": _summary(sections.get("Rationale", "")),
        "consequences_summary": _summary(sections.get("Consequences", "")),
        "sections": sorted(sections),
        "missing_required_sections": missing_sections,
        "line_count": len(text.splitlines()),
        "word_count": len(re.findall(r"\S+", text)),
    }


def _adr_files() -> list[Path]:
    if not ADR_DIR.exists():
        return []
    return [
        path
        for path in sorted(ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"))
        if path.name != TEMPLATE.name
    ]


def _duplicate_ids(items: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        adr_id = str(item["id"])
        if adr_id in seen:
            duplicates.add(adr_id)
        seen.add(adr_id)
    return sorted(duplicates)


def _source_checks(items: list[dict[str, Any]]) -> dict[str, Any]:
    duplicate_ids = _duplicate_ids(items)
    missing_by_file = {
        item["path"]: item["missing_required_sections"]
        for item in items
        if item["missing_required_sections"]
    }
    return {
        "adr_dir": _relative(ADR_DIR),
        "adr_dir_exists": ADR_DIR.exists(),
        "template_path": _relative(TEMPLATE),
        "template_exists": TEMPLATE.exists(),
        "required_sections": REQUIRED_SECTIONS,
        "adr_count": len(items),
        "indexed_ids": [item["id"] for item in items],
        "duplicate_ids": duplicate_ids,
        "missing_required_sections_by_file": missing_by_file,
        "required_sections_ok": not missing_by_file,
        "docs_review_mentions_adr": "ADR" in _read(ROOT / "docs" / "数学平台_全面审查与改进建议.md"),
    }


def render_adr_index() -> dict[str, Any]:
    items = [_parse_adr(path) for path in _adr_files()]
    checks = _source_checks(items)
    return {
        "schema_version": "adr_index_v1",
        "source": "docs/adr/*.md",
        "source_checks_ok": bool(checks["adr_dir_exists"] and checks["template_exists"] and checks["adr_count"] >= 1 and not checks["duplicate_ids"] and checks["required_sections_ok"]),
        "source_checks": checks,
        "items": items,
        "template": {
            "path": _relative(TEMPLATE),
            "required_sections": REQUIRED_SECTIONS,
            "next_id_hint": f"{max([int(item['id']) for item in items], default=0) + 1:04d}",
        },
        "verification": [
            {"command": r"python scripts\dev\export_adr_index.py --check", "scope": "ADR 索引生成物防漂移"},
            {"command": r"python -m pytest -q tests\unit\test_adr_index_export.py", "scope": "ADR 解析、模板、必需章节和漂移检测"},
        ],
    }


def _md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text.replace("|", "\\|").replace("\n", " ")


def render_adr_index_markdown(payload: dict[str, Any]) -> str:
    checks = payload["source_checks"]
    lines = [
        "# ADR 索引",
        "",
        "> 自动生成文件。来源：`scripts/dev/export_adr_index.py` 和 `docs/adr/*.md`。不要手工编辑生成区内容。",
        "",
        "## 摘要",
        "",
        f"- schema_version: `{payload['schema_version']}`",
        f"- source_checks_ok: `{payload['source_checks_ok']}`",
        f"- adr_count: `{checks['adr_count']}`",
        f"- next_id_hint: `{payload['template']['next_id_hint']}`",
        f"- template: `{payload['template']['path']}`",
        "",
        "## 决策记录",
        "",
        "| ID | 状态 | 日期 | 标题 | 决策摘要 | 影响摘要 | 文件 |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in payload["items"]:
        lines.append(
            "| "
            f"`{_md_escape(item['id'])}` | "
            f"`{_md_escape(item['status'])}` | "
            f"`{_md_escape(item['date'])}` | "
            f"{_md_escape(item['title'])} | "
            f"{_md_escape(item['decision_summary'])} | "
            f"{_md_escape(item['consequences_summary'])} | "
            f"`{_md_escape(item['path'])}` |"
        )

    lines.extend(["", "## 源码检查", "", "| 检查项 | 结果 |", "|---|---|"])
    for key, value in checks.items():
        lines.append(f"| `{_md_escape(key)}` | `{_md_escape(value)}` |")

    lines.extend(["", "## 验证命令", "", "| 命令 | 覆盖范围 |", "|---|---|"])
    for row in payload["verification"]:
        lines.append(f"| `{_md_escape(row['command'])}` | {_md_escape(row['scope'])} |")
    lines.append("")
    return "\n".join(lines)


def export_adr_index(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> dict[str, Any]:
    payload = render_adr_index()
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    md_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.write_text(render_adr_index_markdown(payload), encoding="utf-8", newline="\n")
    return payload


def check_adr_index(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> bool:
    payload = render_adr_index()
    expected_json = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    expected_md = render_adr_index_markdown(payload)
    current_json = json_output.read_text(encoding="utf-8") if json_output.exists() else ""
    current_md = md_output.read_text(encoding="utf-8") if md_output.exists() else ""
    return current_json == expected_json and current_md == expected_md


def main() -> int:
    parser = argparse.ArgumentParser(description="Export ADR index from docs/adr.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if generated ADR index files are stale.")
    parser.add_argument("--json-output", type=Path, default=JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=MD_OUTPUT)
    args = parser.parse_args()

    if args.check:
        if not check_adr_index(args.json_output, args.md_output):
            print(f"stale ADR index: {args.json_output} or {args.md_output}", file=sys.stderr)
            return 1
        return 0
    payload = export_adr_index(args.json_output, args.md_output)
    print(f"wrote adr_index.json and adr_index.md with {len(payload['items'])} ADRs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
