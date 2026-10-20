import pytest

from apps.api.app.localization import language_context
from apps.api.app.routers import assistant


@pytest.mark.parametrize("request_text,extra", [
    ("Please create new questions", {"prefer_reuse_existing_ai": False}),
    ("prefer_reuse_existing_ai=false", {}),
    ("Do not reuse existing AI questions", {}),
    ("不要复用AI题", {}),
])
def test_no_reuse_survives_assistant_draft(monkeypatch, request_text, extra):
    monkeypatch.setattr(assistant, "_parse_tags", lambda *args: [])
    monkeypatch.setattr(assistant, "_parse_sources", lambda *args: [])
    with language_context("en", ui_language="zh-CN"):
        result = assistant._draft_generation_batch(None, {"request": request_text, "count": 3, "difficulty_star": 3, **extra}, {})
    draft = result["ui_draft"]
    assert draft["payload"]["prefer_reuse_existing_ai"] is False
    assert draft["output_language"] == "en"
    assert draft["title"].startswith("Question-set draft")
    assert draft["payload"]["constraints"].startswith("Generate automatically gradable")
