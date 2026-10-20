import json
import shutil
import uuid
from pathlib import Path

from scripts.dev.export_openapi import check_openapi_schema, export_openapi_schema, render_openapi_schema


def _make_test_root(name: str) -> Path:
    root = Path.cwd() / "tests" / "runtime_tmp" / f"{name}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_render_openapi_schema_uses_isolated_database(monkeypatch):
    root = _make_test_root("openapi-render")
    monkeypatch.setenv("MATH_PLATFORM_ROOT", str(root))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    try:
        schema = render_openapi_schema(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    assert schema["openapi"].startswith("3.")
    assert schema["x-math-platform-contract"]["schema_version"] == "openapi_export_v1"
    assert schema["x-math-platform-contract"]["isolated_database"] is True
    assert "/api/health/details" in schema["paths"]
    assert "/api/settings/runtime-config" in schema["paths"]
    assert "/api/generation/jobs" in schema["paths"]
    assert "sk-" not in json.dumps(schema, ensure_ascii=False)


def test_export_openapi_schema_and_check(monkeypatch):
    root = _make_test_root("openapi-export")
    monkeypatch.setenv("MATH_PLATFORM_ROOT", str(root))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    output = root / "openapi.json"
    try:
        export_openapi_schema(output=output, root=root)

        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["x-math-platform-contract"]["route_count"] == len(payload["paths"])
        assert check_openapi_schema(output=output, root=root) is True

        output.write_text('{"stale":true}\n', encoding="utf-8")
        assert check_openapi_schema(output=output, root=root) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)
