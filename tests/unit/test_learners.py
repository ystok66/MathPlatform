import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import DEFAULT_LEARNER_ID
from apps.api.app.routers.learners import LearnerProfileRequest, LearnerProfileUpdateRequest, create_learner, list_learners, update_learner


def learner_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_learner_profiles_create_default_and_active_rows():
    db = learner_db()

    initial = list_learners(db=db)
    assert initial["default_learner_id"] == DEFAULT_LEARNER_ID
    assert [row["id"] for row in initial["items"]] == [DEFAULT_LEARNER_ID]

    created = create_learner(LearnerProfileRequest(name="学生A"), db=db)
    assert created["name"] == "学生A"
    assert created["status"] == "active"

    rows = list_learners(db=db)["items"]
    assert {row["id"] for row in rows} == {DEFAULT_LEARNER_ID, created["id"]}


def test_learner_profiles_can_archive_non_default_only():
    db = learner_db()
    created = create_learner(LearnerProfileRequest(name="学生A"), db=db)

    archived = update_learner(created["id"], LearnerProfileUpdateRequest(status="archived"), db=db)
    assert archived["status"] == "archived"
    assert [row["id"] for row in list_learners(db=db)["items"]] == [DEFAULT_LEARNER_ID]

    with pytest.raises(HTTPException) as exc_info:
        update_learner(DEFAULT_LEARNER_ID, LearnerProfileUpdateRequest(status="archived"), db=db)
    assert exc_info.value.status_code == 409
