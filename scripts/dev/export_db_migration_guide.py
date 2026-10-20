from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_DIR = ROOT / "apps" / "api" / "alembic"
VERSIONS_DIR = ALEMBIC_DIR / "versions"
JSON_OUTPUT = ROOT / "docs" / "generated" / "db_migration_guide.json"
MD_OUTPUT = ROOT / "docs" / "generated" / "db_migration_guide.md"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app.db import Base  # noqa: E402
import apps.api.app.models  # noqa: F401,E402
from apps.api.app import sqlite_dev_schema  # noqa: E402


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _literal(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        try:
            return ast.unparse(node)
        except Exception:
            return None


def _assignment_value(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return _literal(node.value)
    return None


def _first_line_with_prefix(doc: str, prefix: str) -> str | None:
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped.removeprefix(prefix).strip()
    return None


def _function_node(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    return next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name), None)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _op_calls(fn: ast.FunctionDef | None) -> list[dict[str, Any]]:
    if fn is None:
        return []
    calls: list[dict[str, Any]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        if not name.startswith("op."):
            continue
        operation = name.split(".", 1)[1]
        first_arg = _literal(node.args[0]) if node.args else None
        calls.append(
            {
                "operation": operation,
                "line": int(getattr(node, "lineno", 0) or 0),
                "target": first_arg if isinstance(first_arg, str) else None,
            }
        )
    return sorted(calls, key=lambda row: row["line"])


def _parse_revision(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    doc = ast.get_docstring(tree) or ""
    revision = _assignment_value(tree, "revision") or _first_line_with_prefix(doc, "Revision ID:")
    down_revision = _assignment_value(tree, "down_revision")
    if down_revision is None:
        down_revision = _first_line_with_prefix(doc, "Revises:") or None
        if down_revision == "":
            down_revision = None
    branch_labels = _assignment_value(tree, "branch_labels")
    depends_on = _assignment_value(tree, "depends_on")
    upgrade_calls = _op_calls(_function_node(tree, "upgrade"))
    downgrade_calls = _op_calls(_function_node(tree, "downgrade"))
    affected_tables = sorted(
        {
            str(row["target"])
            for row in upgrade_calls + downgrade_calls
            if row.get("target")
        }
    )
    return {
        "revision": str(revision),
        "down_revision": down_revision,
        "branch_labels": branch_labels,
        "depends_on": depends_on,
        "path": _relative(path),
        "filename": path.name,
        "summary": doc.splitlines()[0].strip() if doc.splitlines() else path.stem,
        "create_date": _first_line_with_prefix(doc, "Create Date:"),
        "has_upgrade": _function_node(tree, "upgrade") is not None,
        "has_downgrade": _function_node(tree, "downgrade") is not None,
        "upgrade_operation_count": len(upgrade_calls),
        "downgrade_operation_count": len(downgrade_calls),
        "upgrade_operations": upgrade_calls,
        "downgrade_operations": downgrade_calls,
        "affected_tables": affected_tables,
    }


def _revision_graph(revisions: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {str(row["revision"]): row for row in revisions}
    referenced: set[str] = set()
    edges: list[dict[str, str]] = []
    broken: list[dict[str, str]] = []
    for row in revisions:
        down = row.get("down_revision")
        parents: list[str]
        if down is None:
            parents = []
        elif isinstance(down, (list, tuple)):
            parents = [str(value) for value in down]
        else:
            parents = [str(down)]
        for parent in parents:
            referenced.add(parent)
            edges.append({"from": parent, "to": str(row["revision"])})
            if parent not in by_id:
                broken.append({"revision": str(row["revision"]), "missing_down_revision": parent})
    bases = sorted(row["revision"] for row in revisions if row.get("down_revision") is None)
    heads = sorted(set(by_id) - referenced)
    ordered: list[str] = []
    current = bases[0] if len(bases) == 1 else None
    seen: set[str] = set()
    while current and current not in seen:
        ordered.append(current)
        seen.add(current)
        children = sorted(edge["to"] for edge in edges if edge["from"] == current)
        current = children[0] if len(children) == 1 else None
    return {
        "revision_count": len(revisions),
        "base_revisions": bases,
        "head_revisions": heads,
        "edge_count": len(edges),
        "edges": sorted(edges, key=lambda row: (row["from"], row["to"])),
        "broken_down_revisions": sorted(broken, key=lambda row: (row["revision"], row["missing_down_revision"])),
        "linear_order": ordered,
        "is_single_head": len(heads) == 1,
        "is_single_base": len(bases) == 1,
        "is_linear": len(ordered) == len(revisions) and not broken and len(heads) == 1 and len(bases) == 1,
    }


def _sqlite_dev_columns() -> dict[str, Any]:
    tables = {
        "questions": sqlite_dev_schema.QUESTION_COLUMNS,
        "taxonomy_tags": sqlite_dev_schema.TAXONOMY_COLUMNS,
        "practice_sessions": sqlite_dev_schema.PRACTICE_SESSION_COLUMNS,
        "submissions": sqlite_dev_schema.SUBMISSION_COLUMNS,
        "intervention_records": sqlite_dev_schema.INTERVENTION_COLUMNS,
        "generated_candidates": sqlite_dev_schema.GENERATED_CANDIDATE_COLUMNS,
        "generation_jobs": sqlite_dev_schema.GENERATION_JOB_COLUMNS,
    }
    return {
        "table_count": len(tables),
        "column_count": sum(len(columns) for columns in tables.values()),
        "tables": {
            table: [{"column": column, "ddl": ddl} for column, ddl in columns.items()]
            for table, columns in tables.items()
        },
    }


def render_db_migration_guide() -> dict[str, Any]:
    revisions = [_parse_revision(path) for path in sorted(VERSIONS_DIR.glob("*.py"))]
    graph = _revision_graph(revisions)
    op_counts = Counter(
        call["operation"]
        for revision in revisions
        for call in revision["upgrade_operations"]
    )
    table_names = sorted(table.name for table in Base.metadata.sorted_tables)
    return {
        "schema_version": "db_migration_guide_v1",
        "source": "apps/api/alembic + apps.api.app.db.Base.metadata + apps.api.app.sqlite_dev_schema",
        "alembic": {
            "ini_path": "apps/api/alembic.ini",
            "script_location": "apps/api/alembic",
            "versions_path": "apps/api/alembic/versions",
            "env_path": "apps/api/alembic/env.py",
            "uses_settings_database_url": True,
            "target_metadata": "apps.api.app.db.Base.metadata",
        },
        "runtime_schema_management": {
            "startup_sqlite_shim": "apps.api.app.sqlite_dev_schema.ensure_sqlite_dev_schema",
            "startup_caller": "apps.api.app.main.create_app",
            "sqlite_behavior": "SQLite 启动时执行 Base.metadata.create_all，并补齐一组历史兼容列；不会执行 Alembic revision 链。",
            "non_sqlite_behavior": "非 SQLite 数据库不会执行 ensure_sqlite_dev_schema；应通过 Alembic 管理 schema。",
        },
        "model_metadata": {
            "table_count": len(table_names),
            "tables": table_names,
        },
        "revision_graph": graph,
        "operation_summary": dict(sorted(op_counts.items())),
        "sqlite_dev_schema": _sqlite_dev_columns(),
        "revisions": sorted(revisions, key=lambda row: row["filename"]),
        "runbook": {
            "preflight": [
                "运行备份：launcher\\MathPlatform.exe backup --json 或发布包 backup.cmd。",
                "确认 docs/generated/data_dictionary.json 与 Base.metadata 当前输出一致。",
                "确认 docs/generated/db_migration_guide.json 中 revision_graph.is_linear=true 且 is_single_head=true。",
                "在测试库或 --smoke-profile / --data-dir 隔离库上演练迁移和回滚。",
            ],
            "dev_postgres_commands": [
                r"cd apps\api",
                r"alembic current",
                r"alembic upgrade head",
                r"alembic downgrade -1",
                r"alembic upgrade head --sql",
            ],
            "sqlite_release_commands": [
                r"backup.cmd",
                r"stop.cmd",
                r"START_HERE.cmd",
                r"check.cmd",
                r"launcher\MathPlatform.exe doctor --json",
            ],
            "new_revision_steps": [
                "修改 SQLAlchemy model。",
                "新增 Alembic revision，命名使用连续编号和语义短名。",
                "upgrade/downgrade 都必须实现；不可只写 pass。",
                "如果需要兼容已发布 SQLite 数据库，同步更新 sqlite_dev_schema.ensure_sqlite_dev_schema。",
                "重新生成 data_dictionary、db_migration_guide，并运行 --check。",
                "在空库和含旧数据的库上分别验证 upgrade；在可回滚场景验证 downgrade 或记录不可逆原因。",
            ],
            "rollback_policy": [
                "业务数据迁移前必须先备份。",
                "PostgreSQL 使用 Alembic downgrade 或从备份恢复，取决于 migration 是否可逆。",
                "发布包 SQLite 首选从 backup/restore 恢复；不要手动复制热数据库。",
                "恢复失败时保留 pre_restore 或 raw_pre_restore 目录用于诊断。",
            ],
        },
    }


def _md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text.replace("|", "\\|").replace("\n", " ")


def render_db_migration_guide_markdown(payload: dict[str, Any]) -> str:
    graph = payload["revision_graph"]
    lines = [
        "# 数据库迁移指南",
        "",
        "> 自动生成文件。来源：`scripts/dev/export_db_migration_guide.py`、`apps/api/alembic`、SQLAlchemy `Base.metadata` 和 `sqlite_dev_schema.py`。不要手工编辑生成区内容。",
        "",
        "## 摘要",
        "",
        f"- schema_version: `{payload['schema_version']}`",
        f"- revision_count: `{graph['revision_count']}`",
        f"- base_revisions: `{', '.join(graph['base_revisions'])}`",
        f"- head_revisions: `{', '.join(graph['head_revisions'])}`",
        f"- is_linear: `{graph['is_linear']}`",
        f"- model_table_count: `{payload['model_metadata']['table_count']}`",
        f"- sqlite_dev_schema_column_count: `{payload['sqlite_dev_schema']['column_count']}`",
        "",
        "## 当前运行时差异",
        "",
        f"- SQLite：{payload['runtime_schema_management']['sqlite_behavior']}",
        f"- 非 SQLite：{payload['runtime_schema_management']['non_sqlite_behavior']}",
        "",
        "## Revision 链",
        "",
        "| 文件 | revision | down_revision | create_date | upgrade ops | downgrade ops | 影响表 |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for row in payload["revisions"]:
        down = row.get("down_revision")
        down_text = ", ".join(down) if isinstance(down, list) else ("" if down is None else str(down))
        lines.append(
            "| "
            f"`{_md_escape(row['filename'])}` | "
            f"`{_md_escape(row['revision'])}` | "
            f"`{_md_escape(down_text)}` | "
            f"{_md_escape(row.get('create_date'))} | "
            f"{row['upgrade_operation_count']} | "
            f"{row['downgrade_operation_count']} | "
            f"{', '.join(f'`{_md_escape(table)}`' for table in row['affected_tables']) or '无'} |"
        )

    lines.extend(
        [
            "",
            "## Revision 图",
            "",
            "```mermaid",
            "flowchart LR",
        ]
    )
    for edge in graph["edges"]:
        lines.append(f"  {edge['from']} --> {edge['to']}")
    lines.append("```")

    lines.extend(["", "## Upgrade 操作统计", "", "| 操作 | 次数 |", "|---|---:|"])
    for operation, count in payload["operation_summary"].items():
        lines.append(f"| `{_md_escape(operation)}` | {count} |")

    lines.extend(["", "## SQLite 启动兼容列", "", "| 表 | 列 | DDL |", "|---|---|---|"])
    for table, columns in payload["sqlite_dev_schema"]["tables"].items():
        for row in columns:
            lines.append(f"| `{_md_escape(table)}` | `{_md_escape(row['column'])}` | `{_md_escape(row['ddl'])}` |")

    lines.extend(["", "## 迁移 Runbook", "", "### 预检", ""])
    for item in payload["runbook"]["preflight"]:
        lines.append(f"- {_md_escape(item)}")
    lines.extend(["", "### PostgreSQL / 开发库命令", ""])
    for command in payload["runbook"]["dev_postgres_commands"]:
        lines.append(f"- `{_md_escape(command)}`")
    lines.extend(["", "### SQLite 发布包命令", ""])
    for command in payload["runbook"]["sqlite_release_commands"]:
        lines.append(f"- `{_md_escape(command)}`")
    lines.extend(["", "### 新增 Revision 步骤", ""])
    for item in payload["runbook"]["new_revision_steps"]:
        lines.append(f"- {_md_escape(item)}")
    lines.extend(["", "### 回滚策略", ""])
    for item in payload["runbook"]["rollback_policy"]:
        lines.append(f"- {_md_escape(item)}")
    lines.append("")
    return "\n".join(lines)


def export_db_migration_guide(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> dict[str, Any]:
    payload = render_db_migration_guide()
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    md_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.write_text(render_db_migration_guide_markdown(payload), encoding="utf-8", newline="\n")
    return payload


def check_db_migration_guide(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> bool:
    payload = render_db_migration_guide()
    expected_json = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    expected_md = render_db_migration_guide_markdown(payload)
    current_json = json_output.read_text(encoding="utf-8") if json_output.exists() else ""
    current_md = md_output.read_text(encoding="utf-8") if md_output.exists() else ""
    return current_json == expected_json and current_md == expected_md


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Alembic and SQLite migration guide from current source.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if generated DB migration guide files are stale.")
    parser.add_argument("--json-output", type=Path, default=JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=MD_OUTPUT)
    args = parser.parse_args()

    if args.check:
        if not check_db_migration_guide(args.json_output, args.md_output):
            print(f"stale DB migration guide: {args.json_output} or {args.md_output}", file=sys.stderr)
            return 1
        return 0
    payload = export_db_migration_guide(args.json_output, args.md_output)
    print(f"wrote db_migration_guide.json and db_migration_guide.md with {payload['revision_graph']['revision_count']} revisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
