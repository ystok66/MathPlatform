from pathlib import Path


def test_math_content_renderer_keeps_dangerous_html_behind_sanitizer():
    source = Path("apps/web/app/components/MathContent.tsx").read_text(encoding="utf-8")

    assert "export function escapeHtml" in source
    assert "export function sanitizeRenderedHtml" in source
    assert "FORBIDDEN_TAG_PATTERN" in source
    assert "EVENT_HANDLER_ATTR_PATTERN" in source
    assert "DANGEROUS_URL_ATTR_PATTERN" in source
    assert "MAX_MATH_SOURCE_CHARS = 20000" in source
    assert "javascript|vbscript|data|file|blob" in source
    assert "\\u25A1\\u25A2_" in source
    assert "\\u00D7xX\\u00F7" in source
    assert "trust: false" in source
    assert "return sanitizeRenderedHtml(html);" in source
    assert "dangerouslySetInnerHTML={{ __html: renderMathMarkdown(source) }}" in source
