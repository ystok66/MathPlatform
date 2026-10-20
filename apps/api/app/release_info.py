from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


MANIFEST_VERSION = "release_manifest_v1"
RELEASE_INFO_SCHEMA_VERSION = "release_info_v1"
RELEASE_ASSOCIATIONS_SCHEMA_VERSION = "release_manifest_associations_v1"
DEFAULT_EXCLUDED_DIRS = {"backups", "diagnostics", "logs", "__pycache__"}
DEFAULT_EXCLUDED_FILES = {"release_manifest.json"}
COMPONENT_DIRS = {
    "prompt_bundle": Path("prompts"),
    "schema_bundle": Path("packages") / "schemas",
}
API_REQUIREMENT_CANDIDATES = (
    Path("api") / "requirements.txt",
    Path("apps") / "api" / "requirements.txt",
    Path("requirements.txt"),
)
API_DIST_INFO_CANDIDATES = (
    Path("api") / "_internal",
    Path("apps") / "api" / "_internal",
)
WEB_PACKAGE_CANDIDATES = (
    Path("web"),
    Path("apps") / "web",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def should_include_release_file(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if path.name in DEFAULT_EXCLUDED_FILES:
        return False
    return not any(part in DEFAULT_EXCLUDED_DIRS for part in relative.parts)


def release_file_entry(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def included_release_files(root: Path) -> list[dict[str, Any]]:
    root = root.resolve()
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and should_include_release_file(path, root):
            files.append(release_file_entry(path, root))
    return files


def component_summary(root: Path, relative_dir: Path) -> dict[str, Any]:
    target = root / relative_dir
    files = []
    total_bytes = 0
    if target.exists():
        for path in sorted(target.rglob("*")):
            if path.is_file() and should_include_release_file(path, root):
                size = path.stat().st_size
                total_bytes += size
                files.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "size_bytes": size,
                        "sha256": sha256_file(path),
                    }
                )
    bundle_hash = hashlib.sha256(json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "path": relative_dir.as_posix(),
        "exists": target.exists(),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "bundle_hash": bundle_hash,
        "files": files,
    }


def release_components(root: Path) -> dict[str, Any]:
    return {name: component_summary(root, relative_dir) for name, relative_dir in COMPONENT_DIRS.items()}


def component_file_count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def parse_requirement_line(line: str) -> dict[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("-"):
        return None
    name = stripped
    version = ""
    operator = ""
    for marker in ("==", ">=", "<=", "~=", "!=", ">", "<"):
        if marker in stripped:
            left, right = stripped.split(marker, 1)
            name = left.split("[", 1)[0].strip()
            version = right.split(";", 1)[0].strip()
            operator = marker
            break
    return {"name": name, "specifier": stripped, "operator": operator, "version": version}


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def api_dependency_summary(root: Path) -> dict[str, Any]:
    requirements_path = next((root / relative for relative in API_REQUIREMENT_CANDIDATES if (root / relative).exists()), None)
    declared = []
    if requirements_path is not None:
        for line in requirements_path.read_text(encoding="utf-8").splitlines():
            parsed = parse_requirement_line(line)
            if parsed:
                declared.append(parsed)
    distributions = []
    for relative in API_DIST_INFO_CANDIDATES:
        base = root / relative
        if not base.exists():
            continue
        for metadata_path in sorted(base.glob("*.dist-info/METADATA")):
            name = ""
            version = ""
            for line in metadata_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("Name: "):
                    name = line.removeprefix("Name: ").strip()
                elif line.startswith("Version: "):
                    version = line.removeprefix("Version: ").strip()
                if name and version:
                    break
            if name:
                distributions.append({"name": name, "version": version, "metadata_path": metadata_path.relative_to(root).as_posix()})
    return {
        "requirements_path": requirements_path.relative_to(root).as_posix() if requirements_path else None,
        "requirements_hash": sha256_file(requirements_path) if requirements_path else None,
        "declared_count": len(declared),
        "declared": sorted(declared, key=lambda row: row["name"].lower()),
        "bundled_distribution_count": len(distributions),
        "bundled_distributions": sorted(distributions, key=lambda row: row["name"].lower()),
    }


def web_dependency_summary(root: Path) -> dict[str, Any]:
    web_dir = next((root / relative for relative in WEB_PACKAGE_CANDIDATES if (root / relative / "package.json").exists()), None)
    if web_dir is None:
        return {
            "package_json_path": None,
            "package_lock_path": None,
            "declared_count": 0,
            "declared": [],
            "locked_count": 0,
            "locked": [],
        }
    package_path = web_dir / "package.json"
    lock_path = web_dir / "package-lock.json"
    package = read_json_file(package_path)
    dependencies = {}
    for field in ("dependencies", "devDependencies"):
        values = package.get(field)
        if isinstance(values, dict):
            dependencies.update({str(key): str(value) for key, value in values.items()})
    lock = read_json_file(lock_path) if lock_path.exists() else {}
    packages = lock.get("packages") if isinstance(lock.get("packages"), dict) else {}
    locked = []
    for name in sorted(dependencies):
        lock_entry = packages.get(f"node_modules/{name}")
        locked.append(
            {
                "name": name,
                "declared": dependencies[name],
                "locked": str(lock_entry.get("version")) if isinstance(lock_entry, dict) and lock_entry.get("version") else "",
            }
        )
    return {
        "package_json_path": package_path.relative_to(root).as_posix(),
        "package_json_hash": sha256_file(package_path),
        "package_lock_path": lock_path.relative_to(root).as_posix() if lock_path.exists() else None,
        "package_lock_hash": sha256_file(lock_path) if lock_path.exists() else None,
        "declared_count": len(dependencies),
        "declared": sorted(({"name": name, "specifier": specifier} for name, specifier in dependencies.items()), key=lambda row: row["name"].lower()),
        "locked_count": len([row for row in locked if row["locked"]]),
        "locked": locked,
    }


def dependency_summary(root: Path) -> dict[str, Any]:
    summary = {
        "schema_version": "release_dependencies_v1",
        "api": api_dependency_summary(root),
        "web": web_dependency_summary(root),
    }
    summary["summary_hash"] = hashlib.sha256(json.dumps(summary, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return summary


def load_release_manifest(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    manifest_path = root / "release_manifest.json"
    if not manifest_path.exists():
        return None, None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"manifest_parse_error: {exc}"
    if not isinstance(payload, dict):
        return None, "manifest_parse_error: root is not an object"
    return payload, None


def verify_release_manifest(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    root = root.resolve()
    expected = {row["path"]: row for row in manifest.get("files", []) if isinstance(row, dict) and row.get("path")}
    actual_rows = included_release_files(root)
    actual = {row["path"]: row for row in actual_rows}
    missing = sorted(path for path in expected if path not in actual)
    changed = sorted(
        path
        for path, row in expected.items()
        if path in actual and (row.get("sha256") != actual[path]["sha256"] or int(row.get("size_bytes") or -1) != actual[path]["size_bytes"])
    )
    extra = sorted(path for path in actual if path not in expected)
    version_ok = manifest.get("manifest_version") == MANIFEST_VERSION
    expected_components = manifest.get("components") if isinstance(manifest.get("components"), dict) else {}
    actual_components = release_components(root)
    component_mismatches = sorted(
        name
        for name, actual_summary in actual_components.items()
        if not isinstance(expected_components.get(name), dict)
        or expected_components[name].get("bundle_hash") != actual_summary.get("bundle_hash")
        or component_file_count(expected_components[name].get("file_count")) != actual_summary.get("file_count")
    )
    expected_dependencies = manifest.get("dependencies") if isinstance(manifest.get("dependencies"), dict) else {}
    actual_dependencies = dependency_summary(root)
    dependency_mismatches = [] if expected_dependencies.get("summary_hash") == actual_dependencies.get("summary_hash") else ["dependencies"]
    return {
        "ok": version_ok and not missing and not changed and not extra and not component_mismatches and not dependency_mismatches,
        "manifest_version_ok": version_ok,
        "missing": missing[:100],
        "changed": changed[:100],
        "extra": extra[:100],
        "component_mismatches": component_mismatches,
        "dependency_mismatches": dependency_mismatches,
        "missing_count": len(missing),
        "changed_count": len(changed),
        "extra_count": len(extra),
        "component_mismatch_count": len(component_mismatches),
        "dependency_mismatch_count": len(dependency_mismatches),
        "expected_count": len(expected),
        "actual_count": len(actual),
    }


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def release_manifest_associations(*, manifest_exists: bool, manifest_error: str | None, verification: dict[str, Any]) -> dict[str, Any]:
    """Explain where release manifest verification is consumed and how to react."""

    problem_counts = {
        "manifest_missing": 0 if manifest_exists else 1,
        "manifest_unreadable": 1 if manifest_error else 0,
        "manifest_version_mismatch": 1 if manifest_exists and not manifest_error and not bool(verification.get("manifest_version_ok", False)) else 0,
        "missing_files": _count(verification.get("missing_count")),
        "changed_files": _count(verification.get("changed_count")),
        "extra_files": _count(verification.get("extra_count")),
        "component_mismatches": _count(verification.get("component_mismatch_count")),
        "dependency_mismatches": _count(verification.get("dependency_mismatch_count")),
    }
    total_problem_count = sum(problem_counts.values())
    surfaces = [
        {
            "name": "/api/admin/release",
            "kind": "api",
            "field": "response",
            "purpose": "完整发布清单、组件、依赖、校验结果和本关联说明。",
        },
        {
            "name": "/api/health/details",
            "kind": "api",
            "field": "checks.degraded.release",
            "purpose": "健康检查面板和 doctor 的发布清单降级信号。",
        },
        {
            "name": "/api/admin/diagnostics/export",
            "kind": "diagnostic_bundle",
            "field": "release",
            "purpose": "导出给排障或 GPT 审查的离线诊断证据。",
        },
        {
            "name": "MathPlatform.exe doctor --json",
            "kind": "launcher",
            "field": "release_manifest",
            "purpose": "发布包本地自检时确认清单文件存在并可读取。",
        },
        {
            "name": "scripts/smoke/release_smoke_phase.ps1",
            "kind": "release_smoke",
            "field": "preflight/doctor phases",
            "purpose": "发布 smoke 的 preflight、after-start、doctor、after-stop 分阶段证据。",
        },
    ]
    smoke_phases = [
        {"phase": "preflight", "checks": ["release_manifest.json 存在", "manifest_version 可读取"]},
        {"phase": "after-start", "checks": ["服务启动后 API 可访问", "健康检查可返回发布清单状态"]},
        {"phase": "doctor", "checks": ["MathPlatform.exe doctor --json 返回 ok/degraded 证据"]},
        {"phase": "after-stop", "checks": ["服务停止后无目标发布目录残留进程"]},
        {"phase": "business", "checks": ["题库/练习/API 关键路径 smoke 不受发布包漂移影响"]},
    ]
    next_actions: list[str] = []
    if not manifest_exists:
        next_actions.append("重新构建发布包或重新生成 release_manifest.json，然后先跑 preflight smoke。")
    elif manifest_error:
        next_actions.append("检查 release_manifest.json 是否为合法 UTF-8 JSON，修复后重新运行发布清单校验。")
    if problem_counts["manifest_version_mismatch"]:
        next_actions.append("manifest_version 不匹配，使用当前 scripts/pack/release_manifest.py 重新生成清单。")
    if problem_counts["missing_files"]:
        next_actions.append("发布包缺文件，重新同步 dist\\math-platform 资源或重新打包受影响组件。")
    if problem_counts["changed_files"] or problem_counts["extra_files"]:
        next_actions.append("发布包文件与清单不一致，确认是否为手工修改；若不是预期修改，应重新构建并刷新清单。")
    if problem_counts["component_mismatches"]:
        next_actions.append("prompt/schema 组件 hash 不一致，重新同步 prompts 与 packages\\schemas 并重新生成清单。")
    if problem_counts["dependency_mismatches"]:
        next_actions.append("API/Web 依赖摘要不一致，重新构建依赖产物并重新生成清单。")
    if not next_actions:
        next_actions.append("发布清单校验通过；升级或迁移前仍建议先执行 backup。")

    affected_areas: list[dict[str, str]] = []
    if problem_counts["manifest_missing"] or problem_counts["manifest_unreadable"] or problem_counts["manifest_version_mismatch"]:
        affected_areas.append({"area": "release_smoke", "reason": "preflight 无法确认当前包身份。"})
        affected_areas.append({"area": "diagnostics", "reason": "诊断包无法提供可信 manifest 证据。"})
    if problem_counts["missing_files"] or problem_counts["changed_files"] or problem_counts["extra_files"]:
        affected_areas.append({"area": "runtime_files", "reason": "发布包文件集合与清单漂移。"})
    if problem_counts["component_mismatches"]:
        affected_areas.append({"area": "prompt_schema_bundle", "reason": "Prompt 或 schema 资源与清单记录不一致。"})
    if problem_counts["dependency_mismatches"]:
        affected_areas.append({"area": "dependency_bundle", "reason": "API 或 Web 依赖摘要与清单记录不一致。"})
    if not affected_areas:
        affected_areas.append({"area": "release_package", "reason": "未发现发布清单关联问题。"})

    return {
        "schema_version": RELEASE_ASSOCIATIONS_SCHEMA_VERSION,
        "status": "ok" if total_problem_count == 0 and bool(verification.get("ok")) else "needs_attention",
        "problem_counts": problem_counts,
        "total_problem_count": total_problem_count,
        "surfaces": surfaces,
        "smoke_phases": smoke_phases,
        "affected_areas": affected_areas,
        "next_actions": next_actions,
    }


def release_info(root: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest, error = load_release_manifest(root)
    manifest_path = root / "release_manifest.json"
    if manifest is None:
        components = release_components(root)
        dependencies = dependency_summary(root)
        verification = {
            "ok": False,
            "manifest_version_ok": False,
            "missing": [],
            "changed": [],
            "extra": [],
            "component_mismatches": sorted(components),
            "dependency_mismatches": ["dependencies"],
            "missing_count": 0,
            "changed_count": 0,
            "extra_count": 0,
            "component_mismatch_count": len(components),
            "dependency_mismatch_count": 1,
            "expected_count": 0,
            "actual_count": 0,
            "reason": "release_manifest_missing_or_unreadable",
        }
        return {
            "schema_version": RELEASE_INFO_SCHEMA_VERSION,
            "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "release_root": str(root),
            "manifest_path": str(manifest_path),
            "manifest_exists": manifest_path.exists(),
            "manifest_error": error,
            "app_version": "dev",
            "components": components,
            "manifest_components": {},
            "dependencies": dependencies,
            "manifest_dependencies": {},
            "verification": verification,
            "associations": release_manifest_associations(
                manifest_exists=manifest_path.exists(),
                manifest_error=error,
                verification=verification,
            ),
        }
    verification = verify_release_manifest(root, manifest)
    components = release_components(root)
    dependencies = dependency_summary(root)
    return {
        "schema_version": RELEASE_INFO_SCHEMA_VERSION,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "release_root": str(root),
        "manifest_path": str(manifest_path),
        "manifest_exists": True,
        "manifest_error": None,
        "manifest_version": manifest.get("manifest_version"),
        "app_version": manifest.get("app_version") or "unknown",
        "generated_at": manifest.get("generated_at"),
        "file_count": manifest.get("file_count"),
        "total_bytes": manifest.get("total_bytes"),
        "excluded_dirs": manifest.get("excluded_dirs") or sorted(DEFAULT_EXCLUDED_DIRS),
        "excluded_files": manifest.get("excluded_files") or sorted(DEFAULT_EXCLUDED_FILES),
        "components": components,
        "manifest_components": manifest.get("components") if isinstance(manifest.get("components"), dict) else {},
        "dependencies": dependencies,
        "manifest_dependencies": manifest.get("dependencies") if isinstance(manifest.get("dependencies"), dict) else {},
        "verification": verification,
        "associations": release_manifest_associations(
            manifest_exists=True,
            manifest_error=None,
            verification=verification,
        ),
    }
