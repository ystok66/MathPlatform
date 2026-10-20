from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import GenerationJob
from apps.api.app.routers.generation import create_job
from apps.api.app.schemas import GenerationJobRequest


def test_generation_create_job_reuses_idempotency_key():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    payload = GenerationJobRequest(target_tags=["数论"], difficulty_star=3, count=1)

    first = create_job(payload, run_now=False, idempotency_key="same-request", db=db)
    second = create_job(payload, run_now=False, idempotency_key="same-request", db=db)
    jobs = list(db.scalars(select(GenerationJob)))

    assert first.id == second.id
    assert len(jobs) == 1
    assert jobs[0].idempotency_key == "same-request"
