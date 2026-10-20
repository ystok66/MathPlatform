from pathlib import Path

from scripts.dev.generate_status_types import render_status_catalog_types


def test_generated_status_catalog_types_are_current():
    path = Path("packages/schemas/status_catalog.ts")
    rendered = render_status_catalog_types()

    assert path.exists()
    assert path.read_text(encoding="utf-8") == rendered
    assert "export type StatusCatalog" in rendered
    assert "export type StatusDomain" in rendered
    assert "export type StatusValue" in rendered
