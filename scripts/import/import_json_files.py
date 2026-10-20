from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from apps.api.app.config import get_settings
from apps.api.app.db import Base, get_engine
from apps.api.app.importer import import_question_files
from apps.api.app.sqlite_dev_schema import ensure_sqlite_dev_schema


def main() -> None:
    settings = get_settings()
    engine = get_engine()
    Base.metadata.create_all(engine)
    ensure_sqlite_dev_schema(engine)
    with Session(engine) as db:
        stats = import_question_files(db, Path(settings.question_data_dir))
    print(stats)


if __name__ == "__main__":
    main()
