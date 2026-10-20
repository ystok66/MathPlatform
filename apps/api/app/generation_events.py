from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import GenerationEvent, utc_now
from .statuses import (
    STATUS_ACCEPTED,
    STATUS_CANCELLED,
    STATUS_CANCELLING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_QUEUED,
    STATUS_REJECTED,
    STATUS_RUNNING,
    STATUS_TIMED_OUT,
)


SSE_STATUS_RANK = {
    STATUS_QUEUED: 10,
    STATUS_RUNNING: 20,
    STATUS_CANCELLING: 30,
    STATUS_ACCEPTED: 40,
    STATUS_REJECTED: 40,
    STATUS_FAILED: 40,
    STATUS_CANCELLED: 40,
    STATUS_TIMED_OUT: 40,
    STATUS_PARTIAL: 40,
    STATUS_COMPLETED: 50,
}


def sse_timestamp_us(value: datetime | None) -> int:
    current = value or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return int(current.timestamp() * 1_000_000)


def sse_status_rank(status: str | None) -> int:
    return SSE_STATUS_RANK.get(str(status or ""), 20)


def sse_event_id(timestamp: datetime | None, group: int, status: str | None = None, index: int = 0) -> str:
    return f"{sse_timestamp_us(timestamp):016d}-{group:02d}-{sse_status_rank(status):02d}-{index:04d}"


def sse_latest_time(*values: datetime | None) -> datetime | None:
    timestamps = [value for value in values if value is not None]
    return max(timestamps) if timestamps else None


def sse_event_key(event_id: str | None) -> tuple[int, int, int, int]:
    if not event_id:
        return (-1, -1, -1, -1)
    try:
        ts, group, rank, index = str(event_id).split("-", 3)
        return (int(ts), int(group), int(rank), int(index))
    except (TypeError, ValueError):
        return (-1, -1, -1, -1)


def sse_should_emit(event_id: str, last_event_id: str | None) -> bool:
    return sse_event_key(event_id) > sse_event_key(last_event_id)


def persist_generation_event(
    db: Session,
    stream_type: str,
    stream_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    group: int,
    status: str | None = None,
    index: int = 0,
    occurred_at: datetime | None = None,
    source: str = "persistent",
) -> GenerationEvent:
    timestamp = occurred_at or utc_now()
    next_index = max(0, int(index or 0))
    event_id = sse_event_id(timestamp, group, status, next_index)
    while db.scalar(
        select(GenerationEvent.id).where(
            GenerationEvent.stream_type == stream_type,
            GenerationEvent.stream_id == stream_id,
            GenerationEvent.event_id == event_id,
        )
    ):
        next_index += 1
        event_id = sse_event_id(timestamp, group, status, next_index)
    row = GenerationEvent(
        stream_type=stream_type,
        stream_id=stream_id,
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        source=source,
        created_at=timestamp,
    )
    db.add(row)
    db.flush()
    return row


def stored_generation_events(db: Session, stream_type: str, stream_id: str, last_event_id: str | None = None) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(GenerationEvent)
            .where(GenerationEvent.stream_type == stream_type, GenerationEvent.stream_id == stream_id)
            .order_by(GenerationEvent.event_id, GenerationEvent.created_at, GenerationEvent.id)
        )
    )
    return [
        {"id": row.event_id, "event": row.event_type, "data": row.payload, "source": row.source}
        for row in rows
        if sse_should_emit(row.event_id, last_event_id)
    ]


def merge_sse_events(primary: list[dict[str, Any]], fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    merged: list[dict[str, Any]] = []
    for row in [*primary, *fallback]:
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        key = (str(row.get("event")), json.dumps(data, ensure_ascii=False, sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return sorted(merged, key=lambda row: sse_event_key(row.get("id")))
