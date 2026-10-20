import importlib
from pathlib import Path
from uuid import uuid4

from apps.api.app.config import get_settings


def test_main_app_imports_and_registers_history_router(monkeypatch):
    temp_root = Path.cwd() / ".tmp" / "pytest-main-app"
    temp_root.mkdir(parents=True, exist_ok=True)
    db_path = temp_root / f"{uuid4().hex}.db"
    monkeypatch.setenv("MATH_PLATFORM_ROOT", str(temp_root))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()

    module = importlib.import_module("apps.api.app.main")
    routes = {route.path for route in module.app.routes}

    assert "/api/health" in routes
    assert "/api/health/details" in routes
    assert "/api/settings/runtime-config" in routes
    assert "/api/history/groups" in routes
