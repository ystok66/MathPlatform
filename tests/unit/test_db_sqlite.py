import shutil
import uuid
from pathlib import Path

from sqlalchemy import text

from apps.api.app.db import get_engine


def test_sqlite_engine_enables_wal_busy_timeout_and_foreign_keys():
    root = Path(__file__).resolve().parents[2] / "tests" / "runtime_tmp" / f"sqlite-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "math-platform-test.db"
    engine = get_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            busy_timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
            foreign_keys = conn.execute(text("PRAGMA foreign_keys")).scalar()
            synchronous = conn.execute(text("PRAGMA synchronous")).scalar()

        assert str(journal_mode).lower() == "wal"
        assert int(busy_timeout) >= 5000
        assert int(foreign_keys) == 1
        assert int(synchronous) == 1
    finally:
        engine.dispose()
        shutil.rmtree(root, ignore_errors=True)
