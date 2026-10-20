from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import GenerationBatch, PracticeSession
from .localization import translate_message as msg


SESSION_MODE_LABELS = {
    "practice": "普通练习",
    "strict_test": "严格测试",
    "generated_practice": "套题练习",
    "review_practice": "历史重做",
    "diagnostic": "标准诊断",
}


def _short(value: Any, length: int = 8) -> str:
    text = str(value or "").strip()
    return text[:length] if text else "-"


def _compact(value: Any, max_len: int = 24) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return text[:max_len] + "..." if len(text) > max_len else text


def _time_label(value: datetime | None) -> str:
    return value.strftime("%Y%m%d-%H%M") if value else "no-time"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def session_display_name(session: PracticeSession) -> str:
    config = _as_dict(session.config)
    selection = _as_dict(config.get("selection"))
    review_source = _as_dict(config.get("review_source"))
    mode_label = msg(f"display.session.{session.mode}") if session.mode in SESSION_MODE_LABELS else session.mode or "session"
    count = len(session.question_ids or [])
    if session.mode == "generated_practice":
        batch_id = config.get("generation_batch_id") or selection.get("generation_batch_id")
        origin_flag = config.get("origin") or selection.get("origin")
        if origin_flag == "mastery_variant_batch" or selection.get("mastery_variant_batch"):
            origin = msg("display.origin.variant_batch", {"id": _short(batch_id)}) if batch_id else msg("display.origin.variants")
        else:
            origin = msg("display.origin.batch", {"id": _short(batch_id)}) if batch_id else msg("display.origin.batch_sync")
    elif session.mode == "review_practice":
        origin = _compact(
            review_source.get("group_title")
            or review_source.get("reason")
            or selection.get("review_group_id")
            or msg("display.origin.history")
        )
    elif session.mode == "strict_test":
        origin = _compact(config.get("template_name") or selection.get("template_name") or msg("display.session.strict_test"))
    else:
        source_names = _as_list(config.get("source_names"))
        difficulty_stars = _as_list(config.get("difficulty_stars"))
        tag_ids = _as_list(config.get("tag_ids"))
        if source_names:
            origin = msg("display.origin.source", {"source": _compact(', '.join(str(row) for row in source_names[:2]))})
        elif difficulty_stars:
            origin = msg("display.origin.stars", {"stars": ','.join(str(row) for row in difficulty_stars)})
        elif tag_ids:
            origin = msg("display.origin.tags", {"count": len(tag_ids)})
        else:
            origin = msg("display.origin.bank")
    return msg("display.session.title", {"mode": mode_label, "origin": origin, "time": _time_label(session.created_at), "count": count, "id": _short(session.id)})


def batch_display_name(batch: GenerationBatch) -> str:
    request = _as_dict(batch.request)
    if request.get("origin") == "mastery_variant_batch":
        source_count = request.get("variant_source_question_count") or len(_as_list(request.get("source_refs")))
        variants = request.get("variants_per_question") or 1
        source_label = msg("display.batch.sources", {"count": source_count, "variants": variants}) if source_count else msg("display.batch.cross_history")
        return msg("display.batch.variants_title", {"source": source_label, "time": _time_label(batch.created_at), "accepted": batch.accepted_count, "total": batch.target_count, "id": _short(batch.id)})
    star = request.get("difficulty_star")
    tags = _as_list(request.get("target_tags"))
    tag_label = msg("display.origin.tags", {"count": len(tags)}) if tags else msg("display.batch.any_tags")
    star_label = msg("display.origin.stars", {"stars": star}) if star else msg("display.batch.any_stars")
    return msg("display.batch.title", {"stars": star_label, "tags": tag_label, "time": _time_label(batch.created_at), "accepted": batch.accepted_count, "total": batch.target_count, "id": _short(batch.id)})
