from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
JSON_OUTPUT = ROOT / "docs" / "generated" / "status_machines.json"
MD_OUTPUT = ROOT / "docs" / "generated" / "status_machines.md"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app.statuses import STATUS_TRANSITIONS, status_catalog  # noqa: E402


_MERMAID_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _status_order(statuses: list[dict[str, Any]]) -> dict[str, int]:
    return {str(status["value"]): index for index, status in enumerate(statuses)}


def _sorted_values(values: list[str] | tuple[str, ...] | set[str], order: dict[str, int]) -> list[str]:
    return sorted((str(value) for value in values), key=lambda value: (order.get(value, 10_000), value))


def _domain_edges(domain_name: str, statuses: list[dict[str, Any]]) -> list[dict[str, str]]:
    order = _status_order(statuses)
    transitions = STATUS_TRANSITIONS.get(domain_name, {})
    edges = []
    for source in _sorted_values(list(transitions.keys()), order):
        for target in _sorted_values(list(transitions.get(source, ())), order):
            edges.append({"from": source, "to": target})
    return edges


def render_status_machines() -> dict[str, Any]:
    catalog = status_catalog()
    domains = []
    for domain in catalog.get("domains", []):
        domain_name = str(domain["domain"])
        statuses = []
        order = _status_order(domain.get("statuses", []))
        for status in domain.get("statuses", []):
            row = dict(status)
            row["allowed_next"] = _sorted_values(row.get("allowed_next", []), order)
            statuses.append(row)
        edges = _domain_edges(domain_name, statuses)
        domains.append(
            {
                "domain": domain_name,
                "label": domain["label"],
                "description": domain["description"],
                "model_fields": list(domain.get("model_fields", [])),
                "has_explicit_transitions": domain_name in STATUS_TRANSITIONS,
                "status_count": len(statuses),
                "edge_count": len(edges),
                "active_statuses": [str(status["value"]) for status in statuses if status.get("active")],
                "terminal_statuses": [str(status["value"]) for status in statuses if status.get("terminal")],
                "blocking_statuses": [str(status["value"]) for status in statuses if status.get("blocks_practice")],
                "statuses": statuses,
                "edges": edges,
            }
        )
    return {
        "schema_version": "status_machines_v1",
        "source": "apps.api.app.statuses.status_catalog + STATUS_TRANSITIONS",
        "domain_count": len(domains),
        "transition_domain_count": sum(1 for domain in domains if domain["has_explicit_transitions"]),
        "status_count": sum(domain["status_count"] for domain in domains),
        "edge_count": sum(domain["edge_count"] for domain in domains),
        "domains": domains,
    }


def _md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _mermaid_escape(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _state_id(value: str, fallback_index: int) -> str:
    return value if _MERMAID_ID.match(value) else f"S{fallback_index}"


def render_mermaid_state_diagram(domain: dict[str, Any]) -> str:
    statuses = domain["statuses"]
    id_by_value = {str(status["value"]): _state_id(str(status["value"]), index) for index, status in enumerate(statuses)}
    lines = ["```mermaid", "stateDiagram-v2"]
    initial = "queued" if "queued" in id_by_value else None
    if initial is None:
        active = [str(status["value"]) for status in statuses if status.get("active")]
        initial = active[0] if active else (str(statuses[0]["value"]) if statuses else None)
    if initial is not None and domain["has_explicit_transitions"]:
        lines.append(f"  [*] --> {id_by_value[initial]}")
    for status in statuses:
        value = str(status["value"])
        label = f"{status.get('label', value)} ({value})"
        lines.append(f'  state "{_mermaid_escape(label)}" as {id_by_value[value]}')
    if domain["has_explicit_transitions"]:
        for edge in domain["edges"]:
            source = id_by_value.get(edge["from"], _state_id(edge["from"], len(id_by_value)))
            target = id_by_value.get(edge["to"], _state_id(edge["to"], len(id_by_value) + 1))
            lines.append(f"  {source} --> {target}")
        for value in domain["terminal_statuses"]:
            if value in id_by_value:
                lines.append(f"  {id_by_value[value]} --> [*]")
    lines.append("```")
    return "\n".join(lines)


def render_status_machines_markdown(machine: dict[str, Any]) -> str:
    lines = [
        "# 状态机图",
        "",
        "> 自动生成文件。来源：`scripts/dev/export_status_machines.py`、`apps/api/app/statuses.py` 的 `status_catalog()` 与 `STATUS_TRANSITIONS`。不要手工编辑生成区内容。",
        "",
        "## 摘要",
        "",
        f"- schema_version: `{machine['schema_version']}`",
        f"- domain_count: `{machine['domain_count']}`",
        f"- transition_domain_count: `{machine['transition_domain_count']}`",
        f"- status_count: `{machine['status_count']}`",
        f"- edge_count: `{machine['edge_count']}`",
        "",
        "## 域清单",
        "",
        "| 域 | 中文名 | 状态数 | 转换边数 | 显式转换表 | 模型字段 |",
        "|---|---|---:|---:|---|---|",
    ]
    for domain in machine["domains"]:
        lines.append(
            "| "
            f"`{_md_escape(domain['domain'])}` | "
            f"{_md_escape(domain['label'])} | "
            f"{domain['status_count']} | "
            f"{domain['edge_count']} | "
            f"{'是' if domain['has_explicit_transitions'] else '否'} | "
            f"{', '.join(f'`{_md_escape(field)}`' for field in domain['model_fields'])} |"
        )

    for domain in machine["domains"]:
        lines.extend(
            [
                "",
                f"## {domain['label']} (`{domain['domain']}`)",
                "",
                domain["description"],
                "",
                f"- 模型字段：{', '.join(f'`{_md_escape(field)}`' for field in domain['model_fields'])}",
                f"- 显式转换表：{'是' if domain['has_explicit_transitions'] else '否'}",
                f"- 活跃态：{', '.join(f'`{value}`' for value in domain['active_statuses']) or '无'}",
                f"- 终态：{', '.join(f'`{value}`' for value in domain['terminal_statuses']) or '无'}",
                f"- 阻止练习状态：{', '.join(f'`{value}`' for value in domain['blocking_statuses']) or '无'}",
                "",
            ]
        )
        if not domain["has_explicit_transitions"]:
            lines.extend(
                [
                    "> 该域目前无显式转换表；平台只把它作为状态目录和 UI/治理语义使用。代码层面对这些状态默认不限制任意转换。",
                    "",
                ]
            )
        lines.extend(
            [
                render_mermaid_state_diagram(domain),
                "",
                "| 状态值 | 中文名 | 严重度 | 活跃态 | 终态 | 阻止练习 | 允许后继 | 说明 |",
                "|---|---|---|---|---|---|---|---|",
            ]
        )
        for status in domain["statuses"]:
            allowed_next = ", ".join(f"`{value}`" for value in status.get("allowed_next", [])) or "无"
            lines.append(
                "| "
                f"`{_md_escape(status['value'])}` | "
                f"{_md_escape(status.get('label', ''))} | "
                f"`{_md_escape(status.get('severity', 'neutral'))}` | "
                f"{'是' if status.get('active') else '否'} | "
                f"{'是' if status.get('terminal') else '否'} | "
                f"{'是' if status.get('blocks_practice') else '否'} | "
                f"{allowed_next} | "
                f"{_md_escape(status.get('description', ''))} |"
            )
    lines.append("")
    return "\n".join(lines)


def export_status_machines(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> dict[str, Any]:
    machine = render_status_machines()
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(machine, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    md_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.write_text(render_status_machines_markdown(machine), encoding="utf-8", newline="\n")
    return machine


def check_status_machines(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> bool:
    machine = render_status_machines()
    expected_json = json.dumps(machine, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    expected_md = render_status_machines_markdown(machine)
    current_json = json_output.read_text(encoding="utf-8") if json_output.exists() else ""
    current_md = md_output.read_text(encoding="utf-8") if md_output.exists() else ""
    return current_json == expected_json and current_md == expected_md


def main() -> int:
    parser = argparse.ArgumentParser(description="Export status catalog and explicit transition tables as status machine docs.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if generated status machine files are stale.")
    parser.add_argument("--json-output", type=Path, default=JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=MD_OUTPUT)
    args = parser.parse_args()

    if args.check:
        if not check_status_machines(args.json_output, args.md_output):
            print(f"stale status machines: {args.json_output} or {args.md_output}", file=sys.stderr)
            return 1
        return 0
    machine = export_status_machines(args.json_output, args.md_output)
    print(f"wrote status_machines.json and status_machines.md with {machine['domain_count']} domains")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
