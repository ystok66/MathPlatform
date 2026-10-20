from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def sqlite_connect_args(database_url: str) -> dict:
    if not database_url.startswith("sqlite"):
        return {}
    return {"timeout": 30, "check_same_thread": False}


def configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def attach_sqlite_pragmas(engine: Engine) -> None:
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", configure_sqlite_connection)


@lru_cache
def get_engine(database_url: str | None = None):
    resolved_url = database_url or get_settings().database_url
    engine = create_engine(resolved_url, pool_pre_ping=True, connect_args=sqlite_connect_args(resolved_url))
    attach_sqlite_pragmas(engine)
    return engine


@lru_cache
def get_sessionmaker(database_url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(database_url), autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


@event.listens_for(Session, "before_flush")
def persist_new_task_languages(session, _flush_context, _instances):
    from .localization import language_snapshot
    fields = {
        "generation_jobs": "request", "generation_batches": "request",
        "intervention_records": "payload", "recovery_agent_runs": "input_snapshot",
        "assistant_tool_runs": "request_payload", "system_tasks": "progress",
    }
    for row in session.new:
        field = fields.get(getattr(row, "__tablename__", ""))
        if not field:
            continue
        payload = dict(getattr(row, field, None) or {})
        if not payload.get("output_language") and payload.get("language"):
            payload["output_language"] = payload["language"]
        setattr(row, field, language_snapshot(payload))
