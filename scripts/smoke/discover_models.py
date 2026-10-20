from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from apps.api.app.config import get_settings
from apps.api.app.db import get_engine
from apps.api.app.routers.settings import discover_models, run_model_settings_test
from apps.api.app.schemas import ModelSettings


def main() -> int:
    settings = get_settings()
    payload = ModelSettings(
        provider="openai_compatible",
        base_url=settings.liaobots_base_url,
        default_model=settings.default_model,
        roles={},
    )
    with Session(get_engine()) as db:
        discovery = discover_models(payload, db)
        print(json.dumps({"type": "discover", **discovery.model_dump()}, ensure_ascii=False))
        test = run_model_settings_test(payload, db)
        print(json.dumps({"type": "test", **test.model_dump()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
