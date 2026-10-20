from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


MANIFEST_VERSION = "release_manifest_v1"
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


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def should_include(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if path.name in DEFAULT_EXCLUDED_FILES:
        return False
    return not any(part in DEFAULT_EXCLUDED_DIRS for part in relative.parts)


def component_summary(root: Path, relative_dir: Path) -> dict[str, Any]:
    target = root / relative_dir
    files = []
    total_bytes = 0
    if target.exists():
        for path in sorted(target.rglob("*")):
            if not path.is_file() or not should_include(path, root):
                continue
            size = path.stat().st_size
            total_bytes += size
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size_bytes": size,
                    "sha256": sha256_file(path),
                }
            )
    digest = hashlib.sha256(json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "path": relative_dir.as_posix(),
        "exists": target.exists(),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "bundle_hash": digest,
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


def build_release_manifest(root: Path, app_version: str = "local") -> dict[str, Any]:
    root = root.resolve()
    files = []
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not should_include(path, root):
            continue
        size = path.stat().st_size
        total_bytes += size
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "manifest_version": MANIFEST_VERSION,
        "app_version": app_version,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "excluded_dirs": sorted(DEFAULT_EXCLUDED_DIRS),
        "excluded_files": sorted(DEFAULT_EXCLUDED_FILES),
        "components": release_components(root),
        "dependencies": dependency_summary(root),
        "files": files,
    }


def write_release_manifest(root: Path, output: Path | None = None, app_version: str = "local") -> Path:
    root = root.resolve()
    output = output or (root / "release_manifest.json")
    manifest = build_release_manifest(root, app_version=app_version)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def verify_release_manifest(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    root = root.resolve()
    expected = {row["path"]: row for row in manifest.get("files", []) if isinstance(row, dict) and row.get("path")}
    actual_manifest = build_release_manifest(root, app_version=str(manifest.get("app_version") or "local"))
    actual = {row["path"]: row for row in actual_manifest["files"]}
    missing = sorted(path for path in expected if path not in actual)
    changed = sorted(
        path
        for path, row in expected.items()
        if path in actual and (row.get("sha256") != actual[path]["sha256"] or int(row.get("size_bytes") or -1) != actual[path]["size_bytes"])
    )
    extra = sorted(path for path in actual if path not in expected)
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
        "ok": not missing and not changed and not extra and not component_mismatches and not dependency_mismatches,
        "missing": missing,
        "changed": changed,
        "extra": extra,
        "component_mismatches": component_mismatches,
        "dependency_mismatches": dependency_mismatches,
        "expected_count": len(expected),
        "actual_count": len(actual),
        "component_mismatch_count": len(component_mismatches),
        "dependency_mismatch_count": len(dependency_mismatches),
    }


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("release_root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--app-version", default="local")
    args = parser.parse_args()
    output = write_release_manifest(args.release_root, args.output, args.app_version)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
