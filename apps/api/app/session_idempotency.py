from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PracticeSession
from .schemas import CreatePracticeSessionResponse
from .display_names import session_display_name


def ordered_unique_question_ids(question_ids) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in question_ids or []:
        question_id = str(value).strip()
        if not question_id or question_id in seen:
            continue
        seen.add(question_id)
        result.append(question_id)
    return result


def find_idempotent_practice_session(
    db: Session,
    *,
    learner_id: str,
    idempotency_key: str | None,
    mode: str | None = None,
    scan_limit: int = 100,
) -> PracticeSession | None:
    if not idempotency_key:
        return None
    stmt = select(PracticeSession).where(PracticeSession.learner_id == learner_id)
    if mode:
        stmt = stmt.where(PracticeSession.mode == mode)
    rows = list(db.scalars(stmt.order_by(PracticeSession.created_at.desc(), PracticeSession.id.desc()).limit(scan_limit)))
    for session in rows:
        config = session.config or {}
        review_source = config.get("review_source") if isinstance(config.get("review_source"), dict) else {}
        if config.get("idempotency_key") == idempotency_key or review_source.get("idempotency_key") == idempotency_key:
            return session
    return None


def practice_session_response(session: PracticeSession) -> CreatePracticeSessionResponse:
    return CreatePracticeSessionResponse(
        id=session.id,
        learner_id=session.learner_id,
        mode=session.mode,
        created_at=session.created_at.isoformat() if session.created_at else None,
        display_name=session_display_name(session),
        question_ids=ordered_unique_question_ids(session.question_ids or []),
        config=dict(session.config or {}),
    )
