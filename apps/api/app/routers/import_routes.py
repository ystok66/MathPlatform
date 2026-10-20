from pathlib import Path

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..config import get_settings
from ..audit import log_audit_event
from ..db import get_db
from ..importer import import_question_files
from ..schemas import ImportResult

router = APIRouter(prefix="/api/import", tags=["import"])


@router.post("/json-files", response_model=ImportResult)
def import_json_files(
    data_dir: str | None = None,
    max_questions: int | None = Query(default=None, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    root = Path(data_dir) if data_dir else get_settings().question_data_dir
    result = import_question_files(db, root, max_questions=max_questions)
    log_audit_event(db, "import.json_files", "source_files", None, {"data_dir": str(root), "max_questions": max_questions, "result": result})
    db.commit()
    return result
