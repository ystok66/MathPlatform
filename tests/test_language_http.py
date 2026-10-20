from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from apps.api.app.config import get_settings
from apps.api.app.localization import LanguageRequest, current_language, current_ui_language


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Importing main creates an app: establish isolation before the import.
    monkeypatch.setenv("MATH_PLATFORM_ROOT", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'http.db').as_posix()}")
    monkeypatch.setenv("API_REQUEST_LOG_PATH", str(tmp_path / "api.jsonl"))
    get_settings.cache_clear()
    main = importlib.import_module("apps.api.app.main")
    app = main.create_app()

    class Probe(LanguageRequest):
        value: int

    @app.post("/__language_probe")
    def probe(payload: Probe):
        return {"ui": current_ui_language(), "output": current_language(), "request": payload.model_dump()}

    # Future annotations on a function-local model need a concrete annotation.
    probe.__annotations__["payload"] = Probe
    # Register the concrete callable, replacing the already-added probe route.
    app.router.routes.pop()
    app.post("/__language_probe")(probe)
    with TestClient(app) as value:
        yield value
    get_settings.cache_clear()


def test_request_body_survives_language_middleware(client):
    response = client.post("/__language_probe", headers={"X-Math-Platform-Client": "math-platform-web", "X-Math-Platform-Language": "zh-CN", "X-Math-Platform-Output-Language": "en"}, json={"value": 42, "output_language": "zh-CN"})
    assert response.status_code == 200, response.text
    assert response.json()["request"]["value"] == 42
    assert response.json()["output"] == "zh-CN"
    assert response.headers["X-Math-Platform-Output-Language"] == "zh-CN"


def test_ui_header_and_output_header_are_independent(client):
    response = client.post("/__language_probe", headers={"X-Math-Platform-Client": "math-platform-web", "X-Math-Platform-Language": "en", "X-Math-Platform-Output-Language": "zh-CN"}, json={"value": 7})
    assert response.status_code == 200, response.text
    assert response.json()["ui"] == "en"
    assert response.json()["output"] == "zh-CN"


def test_unknown_output_rejected_before_route_execution(client):
    response = client.post("/__language_probe", headers={"X-Math-Platform-Client": "math-platform-web"}, json={"value": 7, "output_language": "not-supported"})
    assert response.status_code == 422
    assert "message_key" in response.json()


def test_language_inventory_is_public_and_default_english(client):
    response = client.get("/api/settings/languages")
    assert response.status_code == 200
    assert response.json()["default_language"] == "en"
    assert {item["code"] for item in response.json()["languages"]} == {"en", "zh-CN"}
