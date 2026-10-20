from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import CheckConstraint, UniqueConstraint


ROOT = Path(__file__).resolve().parents[2]
JSON_OUTPUT = ROOT / "docs" / "generated" / "data_dictionary.json"
MD_OUTPUT = ROOT / "docs" / "generated" / "data_dictionary.md"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app.db import Base  # noqa: E402
import apps.api.app.models  # noqa: F401,E402


def _column_type(column: Any) -> str:
    return str(column.type).replace("\n", " ")


def _default_text(column: Any) -> str | None:
    default = column.default
    server_default = column.server_default
    if default is not None:
        if getattr(default, "is_callable", False):
            return getattr(getattr(default, "arg", None), "__name__", "callable")
        return str(getattr(default, "arg", default))
    if server_default is not None:
        return str(getattr(server_default, "arg", server_default))
    return None


def _foreign_keys(column: Any) -> list[dict[str, str]]:
    refs = []
    for fk in sorted(column.foreign_keys, key=lambda row: str(row.target_fullname)):
        refs.append(
            {
                "target_table": fk.column.table.name,
                "target_column": fk.column.name,
                "target": fk.target_fullname,
            }
        )
    return refs


def _unique_constraints(table: Any) -> list[dict[str, Any]]:
    constraints = []
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            constraints.append(
                {
                    "name": constraint.name,
                    "columns": [column.name for column in constraint.columns],
                }
            )
    return sorted(constraints, key=lambda row: row["name"] or ",".join(row["columns"]))


def _check_constraints(table: Any) -> list[dict[str, str | None]]:
    constraints = []
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint):
            constraints.append(
                {
                    "name": constraint.name,
                    "sqltext": str(constraint.sqltext),
                }
            )
    return sorted(constraints, key=lambda row: row["name"] or row["sqltext"] or "")


def _indexes(table: Any) -> list[dict[str, Any]]:
    indexes = []
    for index in table.indexes:
        indexes.append(
            {
                "name": index.name,
                "columns": [column.name for column in index.columns],
                "unique": bool(index.unique),
            }
        )
    return sorted(indexes, key=lambda row: row["name"] or ",".join(row["columns"]))


def render_data_dictionary() -> dict[str, Any]:
    tables = []
    relationships = []
    for table in sorted(Base.metadata.sorted_tables, key=lambda row: row.name):
        columns = []
        for column in table.columns:
            refs = _foreign_keys(column)
            columns.append(
                {
                    "name": column.name,
                    "type": _column_type(column),
                    "nullable": bool(column.nullable),
                    "primary_key": bool(column.primary_key),
                    "unique": bool(column.unique),
                    "index": bool(column.index),
                    "default": _default_text(column),
                    "foreign_keys": refs,
                }
            )
            for ref in refs:
                relationships.append(
                    {
                        "from_table": table.name,
                        "from_column": column.name,
                        "to_table": ref["target_table"],
                        "to_column": ref["target_column"],
                    }
                )
        tables.append(
            {
                "name": table.name,
                "column_count": len(columns),
                "columns": columns,
                "primary_key": [column.name for column in table.primary_key.columns],
                "unique_constraints": _unique_constraints(table),
                "check_constraints": _check_constraints(table),
                "indexes": _indexes(table),
            }
        )
    return {
        "schema_version": "data_dictionary_v1",
        "source": "SQLAlchemy Base.metadata",
        "table_count": len(tables),
        "relationship_count": len(relationships),
        "tables": tables,
        "relationships": sorted(relationships, key=lambda row: (row["from_table"], row["from_column"], row["to_table"], row["to_column"])),
    }


def _md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_mermaid_er(dictionary: dict[str, Any]) -> str:
    lines = ["```mermaid", "erDiagram"]
    for table in dictionary["tables"]:
        lines.append(f"  {table['name']} {{")
        for column in table["columns"]:
            markers = []
            if column["primary_key"]:
                markers.append("PK")
            if column["foreign_keys"]:
                markers.append("FK")
            marker = f" \"{','.join(markers)}\"" if markers else ""
            lines.append(f"    {column['type'].split('(')[0].replace(' ', '_')} {column['name']}{marker}")
        lines.append("  }")
    seen = set()
    for row in dictionary["relationships"]:
        edge = (row["to_table"], row["from_table"])
        if edge in seen:
            continue
        seen.add(edge)
        lines.append(f"  {row['to_table']} ||--o{{ {row['from_table']} : \"{row['to_column']} to {row['from_column']}\"")
    lines.append("```")
    return "\n".join(lines)


def render_data_dictionary_markdown(dictionary: dict[str, Any]) -> str:
    lines = [
        "# 数据字典与 ER 图",
        "",
        "> 自动生成文件。来源：`scripts/dev/export_data_dictionary.py` 和 SQLAlchemy `Base.metadata`。不要手工编辑生成区内容。",
        "",
        "## 摘要",
        "",
        f"- schema_version: `{dictionary['schema_version']}`",
        f"- table_count: `{dictionary['table_count']}`",
        f"- relationship_count: `{dictionary['relationship_count']}`",
        "",
        "## ER 图",
        "",
        render_mermaid_er(dictionary),
        "",
        "## 表清单",
        "",
        "| 表 | 字段数 | 主键 | 唯一约束数 | CHECK 约束数 | 索引数 |",
        "|---|---:|---|---:|---:|---:|",
    ]
    for table in dictionary["tables"]:
        lines.append(
            f"| `{table['name']}` | {table['column_count']} | `{', '.join(table['primary_key'])}` | {len(table['unique_constraints'])} | {len(table['check_constraints'])} | {len(table['indexes'])} |"
        )
    for table in dictionary["tables"]:
        lines.extend(
            [
                "",
                f"## `{table['name']}`",
                "",
                "| 字段 | 类型 | 空值 | 主键 | 唯一 | 索引 | 默认值 | 外键 |",
                "|---|---|---|---|---|---|---|---|",
            ]
        )
        for column in table["columns"]:
            fk_text = ", ".join(f"`{ref['target']}`" for ref in column["foreign_keys"])
            lines.append(
                "| "
                f"`{_md_escape(column['name'])}` | "
                f"`{_md_escape(column['type'])}` | "
                f"{'是' if column['nullable'] else '否'} | "
                f"{'是' if column['primary_key'] else '否'} | "
                f"{'是' if column['unique'] else '否'} | "
                f"{'是' if column['index'] else '否'} | "
                f"`{_md_escape(column['default'])}` | "
                f"{fk_text} |"
            )
        if table["unique_constraints"]:
            lines.extend(["", "唯一约束："])
            for constraint in table["unique_constraints"]:
                lines.append(f"- `{constraint['name'] or '(unnamed)'}`: `{', '.join(constraint['columns'])}`")
        if table["check_constraints"]:
            lines.extend(["", "CHECK 约束："])
            for constraint in table["check_constraints"]:
                lines.append(f"- `{constraint['name'] or '(unnamed)'}`: `{_md_escape(constraint['sqltext'])}`")
    lines.append("")
    return "\n".join(lines)


def export_data_dictionary(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> dict[str, Any]:
    dictionary = render_data_dictionary()
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(dictionary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    md_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.write_text(render_data_dictionary_markdown(dictionary), encoding="utf-8", newline="\n")
    return dictionary


def check_data_dictionary(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> bool:
    dictionary = render_data_dictionary()
    expected_json = json.dumps(dictionary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    expected_md = render_data_dictionary_markdown(dictionary)
    current_json = json_output.read_text(encoding="utf-8") if json_output.exists() else ""
    current_md = md_output.read_text(encoding="utf-8") if md_output.exists() else ""
    return current_json == expected_json and current_md == expected_md


def main() -> int:
    parser = argparse.ArgumentParser(description="Export SQLAlchemy data dictionary and Mermaid ER diagram.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if generated data dictionary files are stale.")
    parser.add_argument("--json-output", type=Path, default=JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=MD_OUTPUT)
    args = parser.parse_args()

    if args.check:
        if not check_data_dictionary(args.json_output, args.md_output):
            print(f"stale data dictionary: {args.json_output} or {args.md_output}", file=sys.stderr)
            return 1
        return 0
    dictionary = export_data_dictionary(args.json_output, args.md_output)
    print(f"wrote data_dictionary.json and data_dictionary.md with {dictionary['table_count']} tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
