from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
JSON_OUTPUT = ROOT / "docs" / "generated" / "release_checklist.json"
MD_OUTPUT = ROOT / "docs" / "generated" / "release_checklist.md"
MAKE_RELEASE = ROOT / "scripts" / "pack" / "make_release.ps1"
SMOKE_PHASE = ROOT / "scripts" / "smoke" / "release_smoke_phase.ps1"
BUSINESS_SMOKE = ROOT / "scripts" / "smoke" / "release_business_smoke.ps1"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pack import release_manifest  # noqa: E402


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _extract_validate_set(text: str, variable_name: str) -> list[str]:
    pattern = rf"\[ValidateSet\(([^\)]*)\)\]\s*\[string\]\${re.escape(variable_name)}"
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def _extract_required_files(text: str) -> list[str]:
    match = re.search(r"\$required\s*=\s*@\((.*?)\)", text, flags=re.DOTALL)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def _extract_api_paths(text: str) -> list[str]:
    return sorted(set(re.findall(r'["](/api/[^"\s]+)', text)))


def _source_checks() -> dict[str, Any]:
    make_release = _read(MAKE_RELEASE)
    smoke_phase = _read(SMOKE_PHASE)
    business_smoke = _read(BUSINESS_SMOKE)
    return {
        "make_release": {
            "path": _relative(MAKE_RELEASE),
            "exists": MAKE_RELEASE.exists(),
            "builds_api_exe": "build_api_exe.ps1" in make_release,
            "builds_launcher_exe": "build_launcher_exe.ps1" in make_release,
            "builds_web_release_inline": "npm run build" in make_release and "web\\server.js" in make_release and "Copy-Item" in make_release,
            "writes_release_manifest": "release_manifest.py" in make_release,
            "copies_release_smoke_phase": "release_smoke_phase.ps1" in make_release,
            "copies_release_business_smoke": "release_business_smoke.ps1" in make_release,
            "writes_start_stop_check_backup_restore": all(name in make_release for name in ("start.cmd", "check.cmd", "stop.cmd", "backup.cmd", "restore.cmd", "START_HERE.cmd")),
            "packaged_readme_mentions_start_here": "Double-click START_HERE.cmd." in make_release,
        },
        "release_smoke_phase": {
            "path": _relative(SMOKE_PHASE),
            "exists": SMOKE_PHASE.exists(),
            "schema_version": "release_smoke_phase_v1" if "release_smoke_phase_v1" in smoke_phase else None,
            "phases": _extract_validate_set(smoke_phase, "Phase"),
            "required_files": _extract_required_files(smoke_phase),
            "supports_data_dir": "[string]$DataDir" in smoke_phase and "--data-dir" in smoke_phase,
            "supports_smoke_profile": "[string]$SmokeProfile" in smoke_phase and "--smoke-profile" in smoke_phase,
            "contains_start": '"start"' in smoke_phase,
            "contains_stop": '"stop"' in smoke_phase,
            "contains_pause": bool(re.search(r"(?im)^\s*pause\s*$", smoke_phase)),
        },
        "release_business_smoke": {
            "path": _relative(BUSINESS_SMOKE),
            "exists": BUSINESS_SMOKE.exists(),
            "schema_version": "release_business_smoke_v1" if "release_business_smoke_v1" in business_smoke else None,
            "api_paths": _extract_api_paths(business_smoke),
            "uses_bounded_import": "/api/import/json-files?max_questions=1" in business_smoke,
            "uses_mock_generation": "use_mock = $true" in business_smoke,
            "uses_one_candidate": "candidate_count = 1" in business_smoke,
            "checks_history_transfer_dry_run": "/api/admin/history/import?dry_run=true" in business_smoke and "history_transfer_dry_run_hash_mismatch" in business_smoke,
            "checks_question_bank_transfer_dry_run": "/api/admin/question-bank/import?dry_run=true" in business_smoke and "question_bank_transfer_dry_run_hash_mismatch" in business_smoke,
            "contains_start": '"start"' in business_smoke,
            "contains_stop": '"stop"' in business_smoke,
            "contains_pause": bool(re.search(r"(?im)^\s*pause\s*$", business_smoke)),
        },
    }


def _check_ok(checks: dict[str, Any]) -> bool:
    make_release = checks["make_release"]
    smoke = checks["release_smoke_phase"]
    business = checks["release_business_smoke"]
    return all(
        [
            make_release["exists"],
            make_release["builds_api_exe"],
            make_release["builds_launcher_exe"],
            make_release["builds_web_release_inline"],
            make_release["writes_release_manifest"],
            make_release["copies_release_smoke_phase"],
            make_release["copies_release_business_smoke"],
            make_release["writes_start_stop_check_backup_restore"],
            smoke["exists"],
            smoke["phases"] == ["preflight", "after-start", "doctor", "after-stop"],
            smoke["supports_data_dir"],
            smoke["supports_smoke_profile"],
            not smoke["contains_start"],
            not smoke["contains_stop"],
            not smoke["contains_pause"],
            business["exists"],
            business["uses_bounded_import"],
            business["uses_mock_generation"],
            business["uses_one_candidate"],
            business["checks_history_transfer_dry_run"],
            business["checks_question_bank_transfer_dry_run"],
            not business["contains_start"],
            not business["contains_stop"],
            not business["contains_pause"],
        ]
    )


def render_release_checklist() -> dict[str, Any]:
    checks = _source_checks()
    return {
        "schema_version": "release_checklist_v1",
        "source": "scripts/pack/make_release.ps1 + scripts/smoke/release_smoke_phase.ps1 + scripts/smoke/release_business_smoke.ps1 + scripts/pack/release_manifest.py",
        "source_checks_ok": _check_ok(checks),
        "source_checks": checks,
        "release_manifest": {
            "manifest_version": release_manifest.MANIFEST_VERSION,
            "excluded_dirs": sorted(release_manifest.DEFAULT_EXCLUDED_DIRS),
            "excluded_files": sorted(release_manifest.DEFAULT_EXCLUDED_FILES),
            "component_dirs": {name: path.as_posix() for name, path in release_manifest.COMPONENT_DIRS.items()},
        },
        "checklist": [
            {
                "stage": "source_preflight",
                "purpose": "确认源码生成物、事实源和单元测试未漂移。",
                "commands": [
                    r"python scripts\dev\export_openapi.py --check",
                    r"python scripts\dev\export_data_dictionary.py --check",
                    r"python scripts\dev\export_status_machines.py --check",
                    r"python scripts\dev\export_error_catalog.py --check",
                    r"python scripts\dev\export_prompt_contracts.py --check",
                    r"python scripts\dev\export_backup_restore_runbook.py --check",
                    r"python scripts\dev\export_db_migration_guide.py --check",
                    r"python scripts\dev\export_release_checklist.py --check",
                    r"python scripts\dev\export_security_threat_model.py --check",
                    r"python scripts\dev\export_adr_index.py --check",
                    r"python scripts\dev\export_compatibility_matrix.py --check",
                    r"python -m pytest -q",
                    r"cd apps\web; npm run check",
                ],
                "bounded_timeout_seconds": 180,
                "requires_services": False,
            },
            {
                "stage": "build_release",
                "purpose": "重建 API exe、Web 静态运行目录、launcher 和 release_manifest。",
                "commands": [
                    r"powershell -File scripts\pack\make_release.ps1",
                ],
                "bounded_timeout_seconds": 600,
                "requires_services": False,
            },
            {
                "stage": "release_preflight",
                "purpose": "检查发布包必需文件、manifest 可读、cmd 无 pause。",
                "commands": [
                    r"powershell -File scripts\smoke\release_smoke_phase.ps1 -Phase preflight -ReleaseRoot dist\math-platform",
                ],
                "bounded_timeout_seconds": 180,
                "requires_services": False,
            },
            {
                "stage": "start_release",
                "purpose": "单独启动长驻服务；start 必须快速返回，不等待 readiness。",
                "commands": [
                    r"dist\math-platform\launcher\MathPlatform.exe start --smoke-profile release-smoke --json",
                ],
                "bounded_timeout_seconds": 30,
                "requires_services": False,
            },
            {
                "stage": "after_start",
                "purpose": "服务已启动后检查 status/API/Web health。",
                "commands": [
                    r"powershell -File scripts\smoke\release_smoke_phase.ps1 -Phase after-start -ReleaseRoot dist\math-platform -SmokeProfile release-smoke",
                ],
                "bounded_timeout_seconds": 180,
                "requires_services": True,
            },
            {
                "stage": "business_smoke",
                "purpose": "执行有限业务路径，不全量导入、不调用真实 provider。",
                "commands": [
                    r"powershell -File scripts\smoke\release_business_smoke.ps1 -ReleaseRoot dist\math-platform -SmokeProfile release-smoke",
                ],
                "bounded_timeout_seconds": 180,
                "requires_services": True,
            },
            {
                "stage": "doctor",
                "purpose": "运行 launcher doctor。",
                "commands": [
                    r"powershell -File scripts\smoke\release_smoke_phase.ps1 -Phase doctor -ReleaseRoot dist\math-platform -SmokeProfile release-smoke",
                ],
                "bounded_timeout_seconds": 180,
                "requires_services": True,
            },
            {
                "stage": "stop_release",
                "purpose": "单独停止长驻服务。",
                "commands": [
                    r"dist\math-platform\launcher\MathPlatform.exe stop --smoke-profile release-smoke --json",
                ],
                "bounded_timeout_seconds": 30,
                "requires_services": True,
            },
            {
                "stage": "after_stop",
                "purpose": "确认发布包进程已清理。",
                "commands": [
                    r"powershell -File scripts\smoke\release_smoke_phase.ps1 -Phase after-stop -ReleaseRoot dist\math-platform -SmokeProfile release-smoke",
                ],
                "bounded_timeout_seconds": 180,
                "requires_services": False,
            },
        ],
        "safety_rules": [
            "不要把 start、health、business smoke、stop 合并在一个自动化命令里。",
            "不要前台运行 uvicorn、npm run dev、next start、node server.js 或 math-api.exe。",
            "只停止发布目录内的进程，不要广泛 kill node/cmd/powershell/python/math-api。",
            "发布 smoke 使用 --smoke-profile 或 --data-dir 隔离数据库、pid、日志、备份和诊断包。",
            "真实 AI provider 不参与发布业务 smoke；业务 smoke 使用 mock 出题和 1 题 bounded import。",
            "升级或危险导入前先执行 backup.cmd，并保留 release_manifest.json。",
        ],
    }


def _md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text.replace("|", "\\|").replace("\n", " ")


def render_release_checklist_markdown(payload: dict[str, Any]) -> str:
    manifest = payload["release_manifest"]
    lines = [
        "# 发布 Checklist",
        "",
        "> 自动生成文件。来源：`scripts/dev/export_release_checklist.py`、打包脚本、release manifest 生成器和 smoke 脚本。不要手工编辑生成区内容。",
        "",
        "## 摘要",
        "",
        f"- schema_version: `{payload['schema_version']}`",
        f"- source_checks_ok: `{payload['source_checks_ok']}`",
        f"- manifest_version: `{manifest['manifest_version']}`",
        f"- component_dirs: `{json.dumps(manifest['component_dirs'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## 发布阶段",
        "",
        "| 顺序 | 阶段 | 目的 | 超时秒 | 需要服务运行 | 命令 |",
        "|---:|---|---|---:|---|---|",
    ]
    for index, row in enumerate(payload["checklist"], start=1):
        commands = "<br>".join(f"`{_md_escape(command)}`" for command in row["commands"])
        lines.append(
            "| "
            f"{index} | "
            f"`{_md_escape(row['stage'])}` | "
            f"{_md_escape(row['purpose'])} | "
            f"{row['bounded_timeout_seconds']} | "
            f"{'是' if row['requires_services'] else '否'} | "
            f"{commands} |"
        )

    lines.extend(["", "## 安全规则", ""])
    for item in payload["safety_rules"]:
        lines.append(f"- {_md_escape(item)}")

    checks = payload["source_checks"]
    lines.extend(["", "## 源码检查", "", "### make_release.ps1", "", "| 检查项 | 结果 |", "|---|---|"])
    for key, value in checks["make_release"].items():
        lines.append(f"| `{_md_escape(key)}` | `{_md_escape(value)}` |")
    lines.extend(["", "### release_smoke_phase.ps1", "", "| 检查项 | 结果 |", "|---|---|"])
    for key, value in checks["release_smoke_phase"].items():
        lines.append(f"| `{_md_escape(key)}` | `{_md_escape(value)}` |")
    lines.extend(["", "### release_business_smoke.ps1", "", "| 检查项 | 结果 |", "|---|---|"])
    for key, value in checks["release_business_smoke"].items():
        lines.append(f"| `{_md_escape(key)}` | `{_md_escape(value)}` |")
    lines.append("")
    return "\n".join(lines)


def export_release_checklist(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> dict[str, Any]:
    payload = render_release_checklist()
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    md_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.write_text(render_release_checklist_markdown(payload), encoding="utf-8", newline="\n")
    return payload


def check_release_checklist(json_output: Path = JSON_OUTPUT, md_output: Path = MD_OUTPUT) -> bool:
    payload = render_release_checklist()
    expected_json = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    expected_md = render_release_checklist_markdown(payload)
    current_json = json_output.read_text(encoding="utf-8") if json_output.exists() else ""
    current_md = md_output.read_text(encoding="utf-8") if md_output.exists() else ""
    return current_json == expected_json and current_md == expected_md


def main() -> int:
    parser = argparse.ArgumentParser(description="Export release checklist from packaging and smoke scripts.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if generated release checklist files are stale.")
    parser.add_argument("--json-output", type=Path, default=JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=MD_OUTPUT)
    args = parser.parse_args()

    if args.check:
        if not check_release_checklist(args.json_output, args.md_output):
            print(f"stale release checklist: {args.json_output} or {args.md_output}", file=sys.stderr)
            return 1
        return 0
    payload = export_release_checklist(args.json_output, args.md_output)
    print(f"wrote release_checklist.json and release_checklist.md with {len(payload['checklist'])} stages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
