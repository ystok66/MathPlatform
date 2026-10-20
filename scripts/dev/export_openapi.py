from __future__ import annotations

import argparse
import importlib
import os
import sys
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs" / "generated" / "openapi.json"
ISOLATED_ENV_KEYS = ("MATH_PLATFORM_ROOT", "DATABASE_URL", "MATH_PLATFORM_PRESERVE_PROCESS_ENV")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _clear_app_caches() -> None:
    from apps.api.app.config import get_settings
    from apps.api.app.db import get_engine, get_sessionmaker

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def _prepare_isolated_app_env(root: Path) -> dict[str, str | None]:
    previous = {key: os.environ.get(key) for key in ISOLATED_ENV_KEYS}
    os.environ["MATH_PLATFORM_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["MATH_PLATFORM_PRESERVE_PROCESS_ENV"] = "1"
    _clear_app_caches()
    return previous


def _restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    _clear_app_caches()


def render_openapi_schema(root: Path = ROOT) -> dict[str, Any]:
    previous_env = _prepare_isolated_app_env(root)
    try:
        module_name = "apps.api.app.main"
        if module_name in sys.modules:
            module = importlib.reload(sys.modules[module_name])
        else:
            module = importlib.import_module(module_name)
        schema = module.app.openapi()
        schema["x-math-platform-contract"] = {
            "schema_version": "openapi_export_v1",
            "source": "scripts/dev/export_openapi.py",
            "isolated_database": os.environ.get("DATABASE_URL") == "sqlite:///:memory:",
            "route_count": len(schema.get("paths", {})),
        }
        return schema
    finally:
        _restore_env(previous_env)


def export_openapi_schema(output: Path = OUTPUT, root: Path = ROOT) -> dict[str, Any]:
    schema = render_openapi_schema(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return schema


def check_openapi_schema(output: Path = OUTPUT, root: Path = ROOT) -> bool:
    current = output.read_text(encoding="utf-8") if output.exists() else ""
    schema = render_openapi_schema(root)
    rendered = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return current == rendered


def main() -> int:
    parser = argparse.ArgumentParser(description="Export FastAPI OpenAPI contract without starting long-lived services.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if the generated OpenAPI file is stale.")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    if args.check:
        if not check_openapi_schema(args.output):
            print(f"stale OpenAPI contract: {args.output}", file=sys.stderr)
            return 1
        return 0
    schema = export_openapi_schema(args.output)
    try:
        display_path = args.output.resolve().relative_to(ROOT)
    except ValueError:
        display_path = args.output
    print(f"wrote {display_path.as_posix()} with {len(schema.get('paths', {}))} paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
