import json
import shutil
import uuid
from pathlib import Path

from apps.api.app.release_info import release_info
from scripts.pack.release_manifest import build_release_manifest, should_include, verify_release_manifest


TEST_WORK_ROOT = Path(__file__).resolve().parents[2] / ".test-work" / "release-info"


def make_release_root() -> Path:
    root = TEST_WORK_ROOT / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_release_manifest_hashes_existing_pack_files():
    root = Path("scripts/pack")

    manifest = build_release_manifest(root, app_version="test")
    paths = {row["path"] for row in manifest["files"]}

    assert manifest["manifest_version"] == "release_manifest_v1"
    assert "release_manifest.py" in paths
    assert all(row["size_bytes"] > 0 for row in manifest["files"])
    assert all(len(row["sha256"]) == 64 for row in manifest["files"])
    assert "prompt_bundle" in manifest["components"]
    assert "schema_bundle" in manifest["components"]
    assert len(manifest["components"]["prompt_bundle"]["bundle_hash"]) == 64
    assert manifest["dependencies"]["schema_version"] == "release_dependencies_v1"
    assert len(manifest["dependencies"]["summary_hash"]) == 64


def test_release_manifest_excludes_runtime_dirs_and_manifest_file():
    root = Path("release-root")

    assert should_include(root / "api" / "math-api.exe", root) is True
    assert should_include(root / "logs" / "api.out.log", root) is False
    assert should_include(root / "backups" / "old.db", root) is False
    assert should_include(root / "diagnostics" / "diagnostic_bundle.json", root) is False
    assert should_include(root / "release_manifest.json", root) is False


def test_release_manifest_verify_detects_changed_hash_without_writing_files():
    root = Path("scripts/pack")
    manifest = build_release_manifest(root, app_version="test")
    manifest["files"][0]["sha256"] = "0" * 64

    report = verify_release_manifest(root, manifest)

    assert report["ok"] is False
    assert manifest["files"][0]["path"] in report["changed"]


def test_api_release_info_verifies_manifest_roundtrip():
    root = make_release_root()
    try:
        (root / "app.txt").write_text("ok", encoding="utf-8")
        manifest = build_release_manifest(root, app_version="test-version")
        (root / "release_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        info = release_info(root)

        assert info["schema_version"] == "release_info_v1"
        assert info["app_version"] == "test-version"
        assert info["manifest_exists"] is True
        assert info["verification"]["ok"] is True
        assert info["components"]["prompt_bundle"]["exists"] is False
        assert info["dependencies"]["summary_hash"] == info["manifest_dependencies"]["summary_hash"]
        assert info["associations"]["schema_version"] == "release_manifest_associations_v1"
        assert info["associations"]["status"] == "ok"
        assert info["associations"]["total_problem_count"] == 0
        assert any(row["name"] == "/api/admin/release" for row in info["associations"]["surfaces"])
        assert any(row["phase"] == "doctor" for row in info["associations"]["smoke_phases"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_release_manifest_records_api_and_web_dependency_versions():
    root = make_release_root()
    try:
        api_internal = root / "api" / "_internal" / "demo-1.2.3.dist-info"
        api_internal.mkdir(parents=True)
        (root / "api" / "requirements.txt").write_text("fastapi==0.115.6\npsycopg[binary]==3.2.3\n", encoding="utf-8")
        (api_internal / "METADATA").write_text("Name: demo\nVersion: 1.2.3\n", encoding="utf-8")
        web_dir = root / "web"
        web_dir.mkdir()
        (web_dir / "package.json").write_text(
            json.dumps({"dependencies": {"next": "15.1.3", "react": "^19.0.0"}}),
            encoding="utf-8",
        )
        (web_dir / "package-lock.json").write_text(
            json.dumps({"packages": {"node_modules/next": {"version": "15.1.3"}, "node_modules/react": {"version": "19.0.0"}}}),
            encoding="utf-8",
        )

        manifest = build_release_manifest(root, app_version="test-version")
        dependencies = manifest["dependencies"]

        assert dependencies["api"]["requirements_path"] == "api/requirements.txt"
        assert {row["name"]: row["version"] for row in dependencies["api"]["declared"]}["fastapi"] == "0.115.6"
        assert dependencies["api"]["bundled_distributions"][0]["name"] == "demo"
        assert dependencies["web"]["package_json_path"] == "web/package.json"
        assert {row["name"]: row["locked"] for row in dependencies["web"]["locked"]}["react"] == "19.0.0"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_release_info_detects_dependency_mismatch():
    root = make_release_root()
    try:
        web_dir = root / "web"
        web_dir.mkdir(parents=True)
        (web_dir / "package.json").write_text(json.dumps({"dependencies": {"next": "15.1.3"}}), encoding="utf-8")
        lock_path = web_dir / "package-lock.json"
        lock_path.write_text(json.dumps({"packages": {"node_modules/next": {"version": "15.1.3"}}}), encoding="utf-8")
        manifest = build_release_manifest(root, app_version="test-version")
        (root / "release_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        lock_path.write_text(json.dumps({"packages": {"node_modules/next": {"version": "15.2.0"}}}), encoding="utf-8")
        info = release_info(root)

        assert info["verification"]["ok"] is False
        assert info["verification"]["dependency_mismatches"] == ["dependencies"]
        assert info["manifest_dependencies"]["summary_hash"] != info["dependencies"]["summary_hash"]
        assert info["associations"]["status"] == "needs_attention"
        assert info["associations"]["problem_counts"]["dependency_mismatches"] == 1
        assert any("依赖" in action for action in info["associations"]["next_actions"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_api_release_info_detects_changed_file():
    root = make_release_root()
    try:
        target = root / "app.txt"
        target.write_text("before", encoding="utf-8")
        manifest = build_release_manifest(root, app_version="test-version")
        (root / "release_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        target.write_text("after", encoding="utf-8")

        info = release_info(root)

        assert info["verification"]["ok"] is False
        assert info["verification"]["changed"] == ["app.txt"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_release_info_detects_prompt_bundle_mismatch():
    root = make_release_root()
    try:
        prompt_dir = root / "prompts"
        schema_dir = root / "packages" / "schemas"
        prompt_dir.mkdir(parents=True)
        schema_dir.mkdir(parents=True)
        (prompt_dir / "generator_v1.md").write_text("prompt before", encoding="utf-8")
        (schema_dir / "question_v2.schema.json").write_text('{"type":"object"}', encoding="utf-8")
        manifest = build_release_manifest(root, app_version="test-version")
        (root / "release_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        (prompt_dir / "generator_v1.md").write_text("prompt after", encoding="utf-8")
        report = verify_release_manifest(root, manifest)
        info = release_info(root)

        assert report["ok"] is False
        assert "prompt_bundle" in report["component_mismatches"]
        assert "prompt_bundle" in info["verification"]["component_mismatches"]
        assert info["manifest_components"]["prompt_bundle"]["bundle_hash"] != info["components"]["prompt_bundle"]["bundle_hash"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_api_release_info_reports_missing_manifest():
    root = make_release_root()
    try:
        info = release_info(root)

        assert info["manifest_exists"] is False
        assert info["verification"]["ok"] is False
        assert info["verification"]["reason"] == "release_manifest_missing_or_unreadable"
        assert info["associations"]["schema_version"] == "release_manifest_associations_v1"
        assert info["associations"]["status"] == "needs_attention"
        assert info["associations"]["problem_counts"]["manifest_missing"] == 1
        assert any(row["area"] == "release_smoke" for row in info["associations"]["affected_areas"])
    finally:
        shutil.rmtree(root, ignore_errors=True)
