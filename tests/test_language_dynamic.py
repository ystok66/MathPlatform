from datetime import datetime
from types import SimpleNamespace

import pytest

from apps.api.app.display_names import batch_display_name, session_display_name
from apps.api.app.localization import language_context, translate_message


@pytest.mark.parametrize("locale,prefix", [("en", "AI generation"), ("zh-CN", "AI出题")])
def test_batch_title_uses_ui_language(locale, prefix):
    batch = SimpleNamespace(id="batch123456", request={"difficulty_star": 3}, accepted_count=1, target_count=2, created_at=datetime(2026, 10, 17))
    with language_context("en", ui_language=locale):
        assert batch_display_name(batch).startswith(prefix)


def test_system_title_does_not_translate_source_names():
    session = SimpleNamespace(id="session123", config={"source_names": ["原始题源名称"]}, mode="practice", question_ids=["q1"], created_at=None)
    with language_context("zh-CN", ui_language="en"):
        title = session_display_name(session)
        assert title.startswith("Regular practice")
        assert "原始题源名称" in title
        assert "1 questions" in title


def test_help_is_ui_language_not_ai_output_language():
    with language_context("zh-CN", ui_language="en"):
        assert translate_message("help.check_units") == "Are the units consistent?"
    with language_context("en", ui_language="zh-CN"):
        assert translate_message("help.check_units") == "单位是否统一？"
