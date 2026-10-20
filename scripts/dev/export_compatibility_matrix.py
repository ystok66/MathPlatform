from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
JSON_OUTPUT = ROOT / "docs" / "generated" / "compatibility_matrix.json"
MD_OUTPUT = ROOT / "docs" / "generated" / "compatibility_matrix.md"
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"
REQUIREMENTS = ROOT / "apps" / "api" / "requirements.txt"
WEB_PACKAGE = ROOT / "apps" / "web" / "package.json"
WEB_LOCK = ROOT / "apps" / "web" / "package-lock.json"
SCHEMAS = ROOT / "apps" / "api" / "app" / "schemas.py"
LAUNCHER = ROOT / "tools" / "launcher" / "math_platform_launcher.py"
MAKE_RELEASE = ROOT / "scripts" / "pack" / "make_release.ps1"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app.config import Settings  # noqa: E402
from scripts.dev.export_db_migration_guide import render_db_migration_guide  # noqa: E402
from scripts.pack import release_manifest  # noqa: E402


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _regex_text(text: str, pattern: str, default: str = "") -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else default


def _literal_schema_versions() -> dict[str, str]:
    text = _read(SCHEMAS)
    return {
        "history_transfer": "history_transfer_v1" if "Literal[\"history_transfer_v1\"]" in text else "",
        "question_bank_transfer": "question_bank_transfer_v1" if "Literal[\"question_bank_transfer_v1\"]" in text else "",
    }


def _launcher_versions() -> dict[str, str]:
    text = _read(LAUNCHER)
    return {
        "backup_manifest": _regex_text(text, r'BACKUP_MANIFEST_VERSION\s*=\s*"([^"]+)"'),
        "diagnostic_bundle": _regex_text(text, r'DIAGNOSTIC_BUNDLE_VERSION\s*=\s*"([^"]+)"'),
    }


def _web_runtime_summary() -> dict[str, Any]:
    package = _read_json(WEB_PACKAGE)
    lock = _read_json(WEB_LOCK)
    packages = lock.get("packages") if isinstance(lock.get("packages"), dict) else {}
    next_entry = packages.get("node_modules/next") if isinstance(packages.get("node_modules/next"), dict) else {}
    dependencies = package.get("dependencies") if isinstance(package.get("dependencies"), dict) else {}
    return {
        "package_json_path": _relative(WEB_PACKAGE),
        "package_lock_path": _relative(WEB_LOCK),
        "next_version": str(dependencies.get("next") or ""),
        "react_version": str(dependencies.get("react") or ""),
        "typescript_version": str(dependencies.get("typescript") or ""),
        "node_engine_from_next": str((next_entry.get("engines") or {}).get("node") or ""),
    }


def _api_runtime_summary(dependencies: dict[str, Any]) -> dict[str, Any]:
    declared = dependencies["api"].get("declared", [])
    by_name = {str(row["name"]).lower(): row for row in declared if isinstance(row, dict) and row.get("name")}
    return {
        "requirements_path": dependencies["api"].get("requirements_path"),
        "requirements_hash": dependencies["api"].get("requirements_hash"),
        "declared_count": dependencies["api"].get("declared_count"),
        "fastapi": by_name.get("fastapi", {}).get("version", ""),
        "sqlalchemy": by_name.get("sqlalchemy", {}).get("version", ""),
        "alembic": by_name.get("alembic", {}).get("version", ""),
        "httpx": by_name.get("httpx", {}).get("version", ""),
        "cryptography": by_name.get("cryptography", {}).get("version", ""),
    }


def _changelog_summary() -> dict[str, Any]:
    text = _read(CHANGELOG)
    headings = re.findall(r"(?m)^##\s+\[(.+?)\]\s+-\s+(.+?)\s*$", text)
    return {
        "path": _relative(CHANGELOG),
        "exists": CHANGELOG.exists(),
        "has_unreleased": any(name == "Unreleased" for name, _date in headings),
        "entries": [{"name": name, "date": date} for name, date in headings],
    }


def _compatibility_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    api = payload["api_runtime"]
    web = payload["web_runtime"]
    migrations = payload["database_migrations"]
    transfer = payload["transfer_schemas"]
    launcher = payload["launcher_versions"]
    release = payload["release_runtime"]
    return [
        {
            "id": "C01",
            "surface": "Windows 发布包",
            "current_contract": "复制 dist\\math-platform 后通过 MathPlatform.exe/start.cmd 启动；API/Web 绑定 127.0.0.1。",
            "compatibility": "目标是无 Python 环境 Windows 电脑；发布包内包含 API exe、launcher、Web 运行目录、node.exe 和 SQLite 数据库。",
            "source": release["make_release_path"],
            "migration_notes": "换电脑后 API Key 需要在模型配置中重新填写；历史和新题保存到发布包 data 目录。",
        },
        {
            "id": "C02",
            "surface": "后端源码依赖",
            "current_contract": f"requirements 声明 {api['declared_count']} 个依赖；FastAPI {api['fastapi']}，SQLAlchemy {api['sqlalchemy']}，Alembic {api['alembic']}。",
            "compatibility": "源码运行按 apps/api/requirements.txt 和本机 Python 环境安装；发布包以 PyInstaller exe 为运行面。",
            "source": str(api["requirements_path"]),
            "migration_notes": "更新依赖后重新生成 release_manifest 和 compatibility_matrix，并跑后端测试。",
        },
        {
            "id": "C03",
            "surface": "前端源码依赖",
            "current_contract": f"Next {web['next_version']}，React {web['react_version']}，TypeScript {web['typescript_version']}；Next 锁定 Node engine: {web['node_engine_from_next']}。",
            "compatibility": "源码构建按 package-lock 锁定依赖；发布包使用随包复制的 node.exe 和 web/server.js。",
            "source": web["package_json_path"],
            "migration_notes": "升级 Next/React/TypeScript 后运行 npm run check/build，并更新发布包。",
        },
        {
            "id": "C04",
            "surface": "数据库 schema",
            "current_contract": f"Alembic head {migrations['head_revision']}；模型表 {migrations['model_table_count']}；SQLite 启动兼容列 {migrations['sqlite_compat_column_count']}。",
            "compatibility": "非 SQLite 数据库由 Alembic 管理；发布包 SQLite 启动时由 ensure_sqlite_dev_schema 补齐兼容列。",
            "source": "apps/api/alembic + apps/api/app/sqlite_dev_schema.py",
            "migration_notes": "新增字段先写迁移和 SQLite 兼容，再刷新 db_migration_guide。",
        },
        {
            "id": "C05",
            "surface": "历史迁移包",
            "current_contract": transfer["history_transfer"],
            "compatibility": "只接受平台导出的 history_transfer_v1；支持 dry_run。",
            "source": _relative(SCHEMAS),
            "migration_notes": "历史导入不包含 API Key；跨电脑后需重新配置模型密钥。",
        },
        {
            "id": "C06",
            "surface": "题库迁移包",
            "current_contract": transfer["question_bank_transfer"],
            "compatibility": "只接受平台导出的 question_bank_transfer_v1；支持 dry_run 和冲突报告。",
            "source": _relative(SCHEMAS),
            "migration_notes": "同名不同 hash、版本 hash 冲突和答案规范冲突应跳过并报告。",
        },
        {
            "id": "C07",
            "surface": "备份与诊断包",
            "current_contract": f"backup={launcher['backup_manifest']}；diagnostic={launcher['diagnostic_bundle']}。",
            "compatibility": "backup manifest 版本不匹配会拒绝恢复；诊断包脱敏后用于排查。",
            "source": _relative(LAUNCHER),
            "migration_notes": "恢复前先停止服务并自动创建 pre_restore 快照。",
        },
        {
            "id": "C08",
            "surface": "发布 manifest 与依赖摘要",
            "current_contract": f"{release_manifest.MANIFEST_VERSION} + release_dependencies_v1。",
            "compatibility": "manifest 记录文件 hash、prompt/schema bundle hash 和依赖 summary_hash。",
            "source": "scripts/pack/release_manifest.py",
            "migration_notes": "发布包被修改、依赖锁变化或 prompt/schema bundle 变化会在 release 检查中体现。",
        },
        {
            "id": "C09",
            "surface": "AI provider 配置",
            "current_contract": f"default provider={Settings.model_fields['model_provider'].default}；default model={Settings.model_fields['default_model'].default}。",
            "compatibility": "OpenAI-compatible/LiaoBots、Responses、Anthropic、Gemini 通过 provider adapter 合约接入。",
            "source": "apps/api/app/config.py + apps/api/app/ai_providers.py",
            "migration_notes": "API Key 不进入题库包/历史包；真实 provider 失败按 fail closed，不自动发布 mock 题。",
        },
    ]


def render_compatibility_matrix() -> dict[str, Any]:
    dependencies = release_manifest.dependency_summary(ROOT)
    db_guide = render_db_migration_guide()
    changelog = _changelog_summary()
    transfer = _literal_schema_versions()
    launcher_versions = _launcher_versions()
    web_runtime = _web_runtime_summary()
    api_runtime = _api_runtime_summary(dependencies)
    release_runtime = {
        "make_release_path": _relative(MAKE_RELEASE),
        "release_manifest_version": release_manifest.MANIFEST_VERSION,
        "api_binds_loopback": "set HOST=127.0.0.1" in _read(MAKE_RELEASE),
        "web_binds_loopback": "set HOSTNAME=127.0.0.1" in _read(MAKE_RELEASE),
        "copies_node_exe": "Copy-Item -LiteralPath $NodeExe" in _read(MAKE_RELEASE),
        "sqlite_database_url": "sqlite:///./data/math_platform_dev.db" if "sqlite:///./data/math_platform_dev.db" in _read(MAKE_RELEASE) else "",
    }
    payload: dict[str, Any] = {
        "schema_version": "compatibility_matrix_v1",
        "source": "docs/CHANGELOG.md + requirements/package-lock + Alembic/sqlite guide + transfer schemas + launcher/release manifest",
        "changelog": changelog,
        "api_runtime": api_runtime,
        "web_runtime": web_runtime,
        "database_migrations": {
            "head_revision": (db_guide["revision_graph"]["head_revisions"] or [""])[0],
            "base_revision": (db_guide["revision_graph"]["base_revisions"] or [""])[0],
            "revision_count": db_guide["revision_graph"]["revision_count"],
            "single_head": db_guide["revision_graph"]["is_single_head"],
            "linear_chain": db_guide["revision_graph"]["is_linear"],
            "model_table_count": db_guide["model_metadata"]["table_count"],
            "sqlite_compat_column_count": db_guide["sqlite_dev_schema"]["column_count"],
        },
        "transfer_schemas": transfer,
        "launcher_versions": launcher_versions,
        "release_runtime": release_runtime,
        "dependency_summary_hash": dependencies["summary_hash"],
        "dependency_summary": dependencies,
    }
    payload["matrix"] = _compatibility_rows(payload)
    payload["source_checks_ok"] = all(
        [
            changelog["exists"],
            changelog["has_unreleased"],
            REQUIREMENTS.exists(),
            WEB_PACKAGE.exists(),
            WEB_LOCK.exists(),
            bool(api_runtime["fastapi"]),
            bool(web_runtime["next_version"]),
            bool(web_runtime["node_engine_from_next"]),
            bool(payload["database_migrations"]["head_revision"]),
            transfer["history_transfer"] == "history_transfer_v1",
            transfer["question_bank_transfer"] == "question_bank_transfer_v1",
            launcher_versions["backup_manifest"] == "math_platform_backup_v1",
            launcher_versions["diagnostic_bundle"] == "math_platform_diagnostic_bundle_v1",
            release_runtime["api_binds_loopback"],
            release_runtime["web_binds_loopback"],
            release_runtime["copies_node_exe"],
        ]
    )
    payload["verification"] = [
        {"command": r"python scripts\dev\export_compatibility_matrix.py --check", "scope": "兼容性矩阵生成物防漂移"},
        {"command": r"python -m pytest -q tests\unit\test_compatibility_matrix_export.py", "scope": "版本、依赖、迁移、transfer schema 和 changelog 检查"},
    ]
    return payload


def _md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text.replace("|", "\\|").replace("\n", " ")


def render_compatibility_matrix_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# CHANGELOG 与兼容性矩阵",
        "",
        "> 自动生成文件。来源：`scripts/dev/export_compatibility_matrix.py`、`docs/CHANGELOG.md`、依赖锁、迁移、transfer schema、launcher 和 release manifest。不要手工编辑生成区内容。",
        "",
        "## 摘要",
        "",
        f"- schema_version: `{payload['schema_version']}`",
        f"- source_checks_ok: `{payload['source_checks_ok']}`",
        f"- changelog: `{payload['changelog']['path']}`",
        f"- latest_changelog_entry: `{payload['changelog']['entries'][0] if payload['changelog']['entries'] else ''}`",
        f"- dependency_summary_hash: `{payload['dependency_summary_hash']}`",
        "",
        "## 兼容性矩阵",
        "",
        "| ID | 面向对象 | 当前契约 | 兼容性说明 | 迁移/升级备注 | 来源 |",
        "|---|---|---|---|---|---|",
    ]
    for row in payload["matrix"]:
        lines.append(
            "| "
            f"`{_md_escape(row['id'])}` | "
            f"{_md_escape(row['surface'])} | "
            f"{_md_escape(row['current_contract'])} | "
            f"{_md_escape(row['compatibility'])} | "
            f"{_md_escape(row['migration_notes'])} | "
            f"`{_md_escape(row['source'])}` |"
        )
    lines.extend(["", "## 源码检查", "", "| 分组 | 检查项 | 结果 |", "|---|---|---|"])
    for group in ("changelog", "api_runtime", "web_runtime", "database_migrations", "transfer_schemas", "launcher_versions", "release_runtime"):
        for key, value in payload[group].items():
            lines.append(f"| `{_md_escape(group)}` | `{_md_escape(key)}` | `{_md_escape(value)}` |")
    lines.extend(["", "## 验证命令", "", "| 命令 | 覆盖范围 |", "|---|---|"])
    for row in payload["verification"]:
        lines.append(f"| `{_md_escape(row['command'])}` | {_md_escape(row['scope'])} |")
    lines.append("")
    return "\n".join(lines)


def export_compatibility_matrix(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> dict[str, Any]:
    payload = render_compatibility_matrix()
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    md_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.write_text(render_compatibility_matrix_markdown(payload), encoding="utf-8", newline="\n")
    return payload


def check_compatibility_matrix(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> bool:
    payload = render_compatibility_matrix()
    expected_json = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    expected_md = render_compatibility_matrix_markdown(payload)
    current_json = json_output.read_text(encoding="utf-8") if json_output.exists() else ""
    current_md = md_output.read_text(encoding="utf-8") if md_output.exists() else ""
    return current_json == expected_json and current_md == expected_md


def main() -> int:
    parser = argparse.ArgumentParser(description="Export changelog and compatibility matrix facts.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if generated compatibility matrix files are stale.")
    parser.add_argument("--json-output", type=Path, default=JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=MD_OUTPUT)
    args = parser.parse_args()

    if args.check:
        if not check_compatibility_matrix(args.json_output, args.md_output):
            print(f"stale compatibility matrix: {args.json_output} or {args.md_output}", file=sys.stderr)
            return 1
        return 0
    payload = export_compatibility_matrix(args.json_output, args.md_output)
    print(f"wrote compatibility_matrix.json and compatibility_matrix.md with {len(payload['matrix'])} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
