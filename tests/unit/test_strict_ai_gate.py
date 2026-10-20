import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import PracticeSession
from apps.api.app.routers.generation import create_job
from apps.api.app.schemas import GenerationJobRequest


def test_generation_is_blocked_for_strict_test_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add(PracticeSession(id="strict1", mode="strict_test", config={"selection": {"strict_no_ai": True}}, question_ids=[]))
    db.commit()

    with pytest.raises(HTTPException) as exc:
        create_job(GenerationJobRequest(source_session_id="strict1", count=1), run_now=False, idempotency_key=None, db=db)

    assert exc.value.status_code == 403
    assert "disabled" in exc.value.detail
