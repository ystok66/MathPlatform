from __future__ import annotations

import argparse
import inspect
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
JSON_OUTPUT = ROOT / "docs" / "generated" / "backup_restore_runbook.json"
MD_OUTPUT = ROOT / "docs" / "generated" / "backup_restore_runbook.md"
RELEASE_SCRIPT = ROOT / "scripts" / "pack" / "make_release.ps1"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.launcher.math_platform_launcher as launcher  # noqa: E402


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _source_ref(obj: Any) -> dict[str, Any]:
    try:
        path = Path(inspect.getsourcefile(obj) or "")
        _, line = inspect.getsourcelines(obj)
        return {"file": _relative(path), "line": line}
    except Exception:
        return {"file": "", "line": None}


def _release_script_checks() -> dict[str, Any]:
    text = RELEASE_SCRIPT.read_text(encoding="utf-8") if RELEASE_SCRIPT.exists() else ""
    checks = {
        "exists": RELEASE_SCRIPT.exists(),
        "path": _relative(RELEASE_SCRIPT),
        "writes_backup_cmd": "backup.cmd" in text and "backup --json" in text,
        "writes_restore_cmd": "restore.cmd" in text and "restore --file" in text and "--dry-run --json" in text,
        "writes_backup_release_ps1": "tools\\backup-release.ps1" in text and "backup --json" in text,
        "packaged_readme_mentions_backup": "Use backup.cmd before upgrades or risky imports." in text,
        "packaged_readme_mentions_restore_dry_run": "restore.cmd first supports dry-run validation" in text,
        "cmd_pause_present": "pause" in text.lower(),
    }
    checks["ok"] = all(
        bool(checks[key])
        for key in (
            "exists",
            "writes_backup_cmd",
            "writes_restore_cmd",
            "writes_backup_release_ps1",
            "packaged_readme_mentions_backup",
            "packaged_readme_mentions_restore_dry_run",
        )
    )
    return checks


def _manifest_schema() -> list[dict[str, Any]]:
    return [
        {"path": "manifest_version", "required": True, "source": "BACKUP_MANIFEST_VERSION", "description": "备份 manifest 版本，当前只接受 math_platform_backup_v1。"},
        {"path": "backup_id", "required": True, "source": "create_backup", "description": "备份目录名，也是恢复确认短语。"},
        {"path": "created_at", "required": True, "source": "create_backup", "description": "备份创建时间。"},
        {"path": "root", "required": True, "source": "create_backup", "description": "备份时的发布包根目录。"},
        {"path": "data_dir", "required": True, "source": "create_backup", "description": "运行数据目录，默认是发布包根目录，也可由 --data-dir/--smoke-profile 指定。"},
        {"path": "reason", "required": True, "source": "create_backup", "description": "备份原因，例如 manual 或 before_restore_from_*。"},
        {"path": "database.source", "required": True, "source": "create_backup", "description": "源 SQLite 数据库路径。"},
        {"path": "database.backup_file", "required": True, "source": "create_backup", "description": "备份数据库文件名，当前是 math_platform_dev.db。"},
        {"path": "database.size_bytes", "required": True, "source": "create_backup", "description": "备份数据库大小。"},
        {"path": "database.sha256", "required": True, "source": "create_backup/validate_backup_manifest", "description": "恢复前后用于校验的数据库 SHA-256。"},
        {"path": "database.integrity_check", "required": True, "source": "sqlite_integrity_check_file", "description": "SQLite PRAGMA integrity_check 结果。"},
        {"path": "database.table_counts", "required": True, "source": "sqlite_table_counts_file", "description": "关键表行数快照。"},
        {"path": "files[]", "required": True, "source": "file_row/write_sanitized_env", "description": "备份目录内文件清单，包含数据库和可选 env.sanitized。"},
        {"path": "release_manifest", "required": False, "source": "load_release_manifest_summary", "description": "发布包 manifest 摘要。"},
        {"path": "notes[]", "required": True, "source": "create_backup", "description": "备份恢复安全说明。"},
    ]


def _error_codes() -> list[dict[str, str]]:
    return [
        {"error_code": "database_missing", "stage": "backup/integrity", "meaning": "目标数据库不存在。"},
        {"error_code": "sqlite_backup_failed", "stage": "backup", "meaning": "SQLite online backup 失败。"},
        {"error_code": "manifest_missing", "stage": "restore validation", "meaning": "找不到 manifest.json。"},
        {"error_code": "manifest_invalid_json", "stage": "restore validation", "meaning": "manifest.json 不是合法 JSON。"},
        {"error_code": "unsupported_manifest_version", "stage": "restore validation", "meaning": "manifest_version 不是当前支持版本。"},
        {"error_code": "database_backup_missing", "stage": "restore validation", "meaning": "manifest 指向的备份数据库不存在。"},
        {"error_code": "database_hash_mismatch", "stage": "restore validation", "meaning": "备份数据库 SHA-256 与 manifest 不一致。"},
        {"error_code": "database_integrity_failed", "stage": "restore validation", "meaning": "备份数据库完整性检查失败。"},
        {"error_code": "services_running", "stage": "restore", "meaning": "恢复前检测到平台服务仍在运行。"},
        {"error_code": "confirmation_required", "stage": "restore", "meaning": "缺少或输错恢复确认短语；必须等于 backup_id。"},
        {"error_code": "restored_database_integrity_failed", "stage": "restore", "meaning": "待替换数据库复制后完整性检查失败，未覆盖当前数据库。"},
        {"error_code": "post_restore_validation_failed", "stage": "restore", "meaning": "数据库替换后 hash 或完整性校验失败，会尝试自动回滚。"},
        {"error_code": "pre_restore_backup_missing", "stage": "rollback", "meaning": "恢复失败后找不到恢复前快照。"},
        {"error_code": "rollback_database_integrity_failed", "stage": "rollback", "meaning": "恢复前快照自身完整性检查失败。"},
        {"error_code": "restore_file_required", "stage": "cli", "meaning": "restore 命令缺少 --file 参数。"},
    ]


def render_backup_restore_runbook() -> dict[str, Any]:
    release_checks = _release_script_checks()
    launcher_refs = {
        name: _source_ref(getattr(launcher, name))
        for name in (
            "create_backup",
            "backup",
            "validate_backup_manifest",
            "restore_backup",
            "restore_confirmation_phrase",
            "restore_confirmation_matches",
            "raw_pre_restore_copy",
            "rollback_restored_database",
            "sqlite_backup_connection",
            "sanitize_env_text",
            "latest_backup_summaries",
        )
    }
    return {
        "schema_version": "backup_restore_runbook_v1",
        "source": "tools.launcher.math_platform_launcher + scripts.pack.make_release.ps1",
        "launcher": {
            "path": "tools/launcher/math_platform_launcher.py",
            "backup_manifest_version": launcher.BACKUP_MANIFEST_VERSION,
            "database_relative_path": launcher.DATABASE_RELATIVE_PATH.as_posix(),
            "critical_tables": list(launcher.CRITICAL_TABLES),
            "sensitive_key_markers": list(launcher.SENSITIVE_KEY_MARKERS),
            "source_refs": launcher_refs,
        },
        "release_script_checks": release_checks,
        "entrypoints": [
            {"name": "release backup shortcut", "command": r"backup.cmd", "notes": "发布包根目录双击/运行，内部执行 launcher\\MathPlatform.exe backup --json。"},
            {"name": "release restore dry-run", "command": r"restore.cmd backups\\backup-YYYYMMDD-HHMMSS", "notes": "首次不带 backup_id，只做校验并输出 required_confirmation。"},
            {"name": "release restore confirmed", "command": r"restore.cmd backups\\backup-YYYYMMDD-HHMMSS backup-YYYYMMDD-HHMMSS", "notes": "第二个参数必须等于 dry-run 输出的 backup_id。"},
            {"name": "direct backup", "command": r"launcher\\MathPlatform.exe backup --json", "notes": "适合维护者在发布包根目录执行。"},
            {"name": "direct restore dry-run", "command": r"launcher\\MathPlatform.exe restore --file backups\\backup-YYYYMMDD-HHMMSS --dry-run --json", "notes": "只验证 manifest、hash、完整性，不改数据库。"},
            {"name": "direct restore confirmed", "command": r"launcher\\MathPlatform.exe restore --file backups\\backup-YYYYMMDD-HHMMSS --confirm backup-YYYYMMDD-HHMMSS --json", "notes": "恢复前必须先 stop，且确认短语必须匹配 backup_id。"},
            {"name": "isolated profile backup", "command": r"launcher\\MathPlatform.exe backup --data-dir .smoke-profiles\\release-smoke --json", "notes": "仅用于隔离 profile，避免污染正式 data 目录。"},
        ],
        "backup_workflow": [
            {"step": 1, "name": "resolve runtime data dir", "implementation": "runtime_data_dir + database_path", "details": "默认数据库是 data/math_platform_dev.db；--data-dir 或 --smoke-profile 会切换到隔离目录。"},
            {"step": 2, "name": "create backup directory", "implementation": "unique_child_dir(backups/<backup_id>)", "details": "backup_id 形如 backup-YYYYMMDD-HHMMSS，冲突时生成唯一目录。"},
            {"step": 3, "name": "copy SQLite database", "implementation": "sqlite3.Connection.backup", "details": "使用 SQLite online backup API，不直接复制正在写入的数据库文件。"},
            {"step": 4, "name": "validate backup", "implementation": "PRAGMA integrity_check + key table counts + sha256", "details": "生成 integrity_check、关键表行数和数据库 SHA-256。"},
            {"step": 5, "name": "write sanitized env", "implementation": "write_sanitized_env", "details": ".env 存在时写 env.sanitized；敏感 key、token、password、authorization、cookie、encryption_key 会脱敏。"},
            {"step": 6, "name": "write manifest", "implementation": "manifest.json", "details": "写入 manifest_version、backup_id、database、files、release_manifest 和 notes。"},
        ],
        "restore_workflow": [
            {"step": 1, "name": "dry-run first", "implementation": "restore --dry-run", "details": "校验 manifest、数据库 hash 和完整性，输出 required_confirmation，不改变当前数据库。"},
            {"step": 2, "name": "stop services", "implementation": "release_processes + port checks", "details": "恢复前检测发布包进程、API 8001 和 Web 3000；仍在运行时返回 services_running。"},
            {"step": 3, "name": "confirm backup id", "implementation": "restore_confirmation_matches", "details": "确认短语必须等于 manifest.backup_id，没有固定全局密码。"},
            {"step": 4, "name": "pre-restore snapshot", "implementation": "create_backup(prefix='pre_restore')", "details": "覆盖前自动保存当前数据库；普通备份失败时退化为 raw_pre_restore_copy。"},
            {"step": 5, "name": "copy to temporary db", "implementation": "shutil.copy2 + integrity_check", "details": "先复制到 .restore-*.tmp 并检查完整性，失败时不覆盖当前数据库。"},
            {"step": 6, "name": "atomic replace", "implementation": "os.replace + sidecar cleanup", "details": "删除 SQLite -wal/-shm sidecar 后替换数据库，再清理替换后的 sidecar。"},
            {"step": 7, "name": "post-restore validation", "implementation": "sha256 + integrity_check", "details": "替换后校验 hash 与完整性，失败时尝试从 pre_restore 自动回滚。"},
        ],
        "manifest_schema": _manifest_schema(),
        "safety_gates": [
            "备份使用 SQLite online backup API，避免直接复制热数据库。",
            "恢复必须先 dry-run，正式恢复必须带 backup_id 确认短语。",
            "恢复前必须 stop；检测到发布包进程或 3000/8001 可达时拒绝恢复。",
            "正式覆盖前自动创建 pre_restore 快照；失败时尝试自动回滚。",
            ".env 只写 env.sanitized，不写 API Key、token、password、authorization、cookie 或 encryption_key 明文。",
            "支持 --data-dir/--smoke-profile，烟测和维护操作可以隔离正式数据目录。",
        ],
        "error_codes": _error_codes(),
        "verification": [
            {"command": r"python -m pytest -q tests\\unit\\test_launcher_backup.py", "scope": "备份、dry-run、确认短语、恢复前快照、恢复失败回滚、脱敏和隔离 data-dir。"},
            {"command": r"python scripts\\dev\\export_backup_restore_runbook.py --check", "scope": "确认 runbook 生成物与 launcher/打包脚本未漂移。"},
        ],
    }


def _md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text.replace("|", "\\|").replace("\n", " ")


def render_backup_restore_runbook_markdown(payload: dict[str, Any]) -> str:
    launcher_info = payload["launcher"]
    lines = [
        "# 备份与恢复 Runbook",
        "",
        "> 自动生成文件。来源：`scripts/dev/export_backup_restore_runbook.py`、`tools/launcher/math_platform_launcher.py` 和 `scripts/pack/make_release.ps1`。不要手工编辑生成区内容。",
        "",
        "## 摘要",
        "",
        f"- schema_version: `{payload['schema_version']}`",
        f"- backup_manifest_version: `{launcher_info['backup_manifest_version']}`",
        f"- database_relative_path: `{launcher_info['database_relative_path']}`",
        f"- critical_table_count: `{len(launcher_info['critical_tables'])}`",
        f"- release_script_ok: `{payload['release_script_checks']['ok']}`",
        "",
        "## 常用入口",
        "",
        "| 场景 | 命令 | 说明 |",
        "|---|---|---|",
    ]
    for row in payload["entrypoints"]:
        lines.append(f"| {_md_escape(row['name'])} | `{_md_escape(row['command'])}` | {_md_escape(row['notes'])} |")

    lines.extend(["", "## 备份流程", "", "| 步骤 | 名称 | 实现 | 说明 |", "|---:|---|---|---|"])
    for row in payload["backup_workflow"]:
        lines.append(f"| {row['step']} | {_md_escape(row['name'])} | `{_md_escape(row['implementation'])}` | {_md_escape(row['details'])} |")

    lines.extend(["", "## 恢复流程", "", "| 步骤 | 名称 | 实现 | 说明 |", "|---:|---|---|---|"])
    for row in payload["restore_workflow"]:
        lines.append(f"| {row['step']} | {_md_escape(row['name'])} | `{_md_escape(row['implementation'])}` | {_md_escape(row['details'])} |")

    lines.extend(
        [
            "",
            "## Manifest Schema",
            "",
            "| 字段 | 必需 | 来源 | 说明 |",
            "|---|---|---|---|",
        ]
    )
    for row in payload["manifest_schema"]:
        lines.append(f"| `{_md_escape(row['path'])}` | {'是' if row['required'] else '否'} | `{_md_escape(row['source'])}` | {_md_escape(row['description'])} |")

    lines.extend(["", "## 安全门禁", ""])
    for item in payload["safety_gates"]:
        lines.append(f"- {_md_escape(item)}")

    lines.extend(
        [
            "",
            "## 错误码",
            "",
            "| 错误码 | 阶段 | 含义 |",
            "|---|---|---|",
        ]
    )
    for row in payload["error_codes"]:
        lines.append(f"| `{_md_escape(row['error_code'])}` | {_md_escape(row['stage'])} | {_md_escape(row['meaning'])} |")

    lines.extend(["", "## 实现来源", "", "| 函数 | 文件 | 行 |", "|---|---|---:|"])
    for name, ref in launcher_info["source_refs"].items():
        lines.append(f"| `{_md_escape(name)}` | `{_md_escape(ref['file'])}` | {ref.get('line') or ''} |")

    checks = payload["release_script_checks"]
    lines.extend(["", "## 打包脚本检查", "", "| 检查项 | 结果 |", "|---|---|"])
    for key, value in checks.items():
        if key in {"path"}:
            continue
        lines.append(f"| `{_md_escape(key)}` | `{_md_escape(value)}` |")

    lines.extend(["", "## 验证命令", "", "| 命令 | 覆盖范围 |", "|---|---|"])
    for row in payload["verification"]:
        lines.append(f"| `{_md_escape(row['command'])}` | {_md_escape(row['scope'])} |")
    lines.append("")
    return "\n".join(lines)


def export_backup_restore_runbook(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> dict[str, Any]:
    payload = render_backup_restore_runbook()
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    md_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.write_text(render_backup_restore_runbook_markdown(payload), encoding="utf-8", newline="\n")
    return payload


def check_backup_restore_runbook(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> bool:
    payload = render_backup_restore_runbook()
    expected_json = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    expected_md = render_backup_restore_runbook_markdown(payload)
    current_json = json_output.read_text(encoding="utf-8") if json_output.exists() else ""
    current_md = md_output.read_text(encoding="utf-8") if md_output.exists() else ""
    return current_json == expected_json and current_md == expected_md


def main() -> int:
    parser = argparse.ArgumentParser(description="Export backup/restore runbook from launcher implementation.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if generated backup/restore runbook files are stale.")
    parser.add_argument("--json-output", type=Path, default=JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=MD_OUTPUT)
    args = parser.parse_args()

    if args.check:
        if not check_backup_restore_runbook(args.json_output, args.md_output):
            print(f"stale backup/restore runbook: {args.json_output} or {args.md_output}", file=sys.stderr)
            return 1
        return 0
    payload = export_backup_restore_runbook(args.json_output, args.md_output)
    print(f"wrote backup_restore_runbook.json and backup_restore_runbook.md with {len(payload['entrypoints'])} entrypoints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
