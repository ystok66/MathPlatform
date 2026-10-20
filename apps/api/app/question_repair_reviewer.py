from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_gateway import hash_payload, model_roles, provider_chain
from .ai_trace import request_payload_with_trace, root_ai_trace, token_usage_with_trace
from .audit import sanitize_payload
from .config import get_settings
from .models import AiRunLog, AnswerEvent, AnswerSpec, PracticeSession, Question, QuestionVersion, ReviewQueueItem, Submission
from .prompt_loader import question_repair_reviewer_system_prompt
from .prompt_security import guarded_json_message, guarded_system_prompt


QUESTION_REPAIR_REVIEWER_PROMPT_VERSION = "question_repair_reviewer_v1"


def _current_version(db: Session, question: Question) -> QuestionVersion | None:
    if question.current_version_id:
        row = db.get(QuestionVersion, question.current_version_id)
        if row:
            return row
    return db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == question.id).order_by(QuestionVersion.version.desc()))


def _recent_submissions(db: Session, question_id: str, limit: int = 8) -> list[dict[str, Any]]:
    rows = list(
        db.execute(
            select(Submission, PracticeSession)
            .join(PracticeSession, PracticeSession.id == Submission.session_id)
            .where(Submission.question_id == question_id)
            .order_by(Submission.created_at.desc(), Submission.id.desc())
            .limit(limit)
        ).all()
    )
    return [
        {
            "submission_id": row.id,
            "session_id": row.session_id,
            "learner_id": session.learner_id,
            "mode": session.mode,
            "answer_text": row.answer_text[:200],
            "thought_text_present": bool(row.thought_text),
            "is_correct": row.is_correct,
            "effective_wrong": row.effective_wrong,
            "excluded_from_mastery": row.excluded_from_mastery,
            "grading_reason": (row.grading_result or {}).get("reason"),
            "created_at": row.created_at.isoformat(),
        }
        for row, session in rows
    ]


def _recent_events(db: Session, question_id: str, limit: int = 12) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(AnswerEvent)
            .where(AnswerEvent.question_id == question_id)
            .order_by(AnswerEvent.created_at.desc(), AnswerEvent.id.desc())
            .limit(limit)
        )
    )
    return [
        {
            "event_id": row.id,
            "session_id": row.session_id,
            "event_type": row.event_type,
            "payload": row.payload,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def build_question_repair_review_packet(db: Session, question: Question, issue_type: str, note: str | None, impact: dict[str, Any] | None = None) -> dict[str, Any]:
    version = _current_version(db, question)
    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == question.id))
    review_items = list(
        db.scalars(
            select(ReviewQueueItem)
            .where(ReviewQueueItem.question_id == question.id, ReviewQueueItem.status == "open")
            .order_by(ReviewQueueItem.created_at.desc(), ReviewQueueItem.id.desc())
            .limit(10)
        )
    )
    qv2 = version.question_v2 if version else {}
    return {
        "schema_version": "question_repair_review_packet_v1",
        "requested_issue_type": issue_type,
        "admin_note": note or "",
        "question": {
            "id": question.id,
            "source_name": question.source_name,
            "status": question.status,
            "publish_status": question.publish_status,
            "difficulty_star": question.difficulty_star,
            "premium": question.premium,
            "question_fault_status": question.question_fault_status,
            "stem_preview": question.stem_preview,
        },
        "question_v2": qv2,
        "answer_spec": answer.raw_answer_spec if answer else None,
        "impact": impact or {},
        "open_review_items": [
            {
                "id": item.id,
                "reason_code": item.reason_code,
                "severity": item.severity,
                "payload": item.payload,
                "created_at": item.created_at.isoformat(),
            }
            for item in review_items
        ],
        "recent_submissions": _recent_submissions(db, question.id),
        "recent_events": _recent_events(db, question.id),
    }


def mock_question_repair_suggestion(packet: dict[str, Any]) -> dict[str, Any]:
    issue_type = str(packet.get("requested_issue_type") or "other")
    impact = packet.get("impact") if isinstance(packet.get("impact"), dict) else {}
    totals = impact.get("totals") if isinstance(impact.get("totals"), dict) else {}
    submission_count = int(totals.get("submission_count") or 0)
    open_review_count = int(totals.get("open_review_queue_count") or len(packet.get("open_review_items") or []))
    severity = "high" if issue_type in {"canonical_answer_error", "answer_spec_error", "ambiguous_question", "missing_image"} else "medium"
    should_regrade = submission_count > 0 and issue_type in {"canonical_answer_error", "answer_spec_error", "grading_normalization_error"}
    return {
        "status": "ok",
        "issue_type": issue_type,
        "severity": severity,
        "confidence": 0.72 if open_review_count or submission_count else 0.55,
        "summary": "已生成本地 mock 修复建议；请人工核对题面、答案规范和影响统计后再创建修复草稿。",
        "evidence": [
            f"历史提交 {submission_count} 条",
            f"open 审核项 {open_review_count} 个",
            f"当前题目状态 {((packet.get('question') or {}).get('status'))}",
        ],
        "recommended_actions": [
            "先核对题面是否完整、答案是否唯一。",
            "若答案规范错误，创建修复草稿并发布新版本。",
            "发布后先预览再判分，不要直接改写全部历史。",
        ],
        "should_quarantine": issue_type in {"ambiguous_question", "missing_image", "canonical_answer_error", "answer_spec_error"},
        "should_regrade_after_publish": should_regrade,
        "draft_question_v2_patch": {},
        "draft_answer_spec_patch": {},
        "reviewer_notes": "mock 建议只用于流程验证；真实 provider 可给出更具体的局部补丁。",
        "blocked_reason": None,
    }


def normalize_question_repair_suggestion(payload: dict[str, Any] | None, provider: str, model: str, latency_ms: int | None, error: str | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "status": "error",
            "issue_type": "other",
            "severity": "medium",
            "confidence": 0,
            "summary": "",
            "evidence": [],
            "recommended_actions": [],
            "should_quarantine": False,
            "should_regrade_after_publish": False,
            "draft_question_v2_patch": {},
            "draft_answer_spec_patch": {},
            "reviewer_notes": "",
            "blocked_reason": error or "invalid_json",
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "error": error,
        }
    status = str(payload.get("status") or "ok")
    issue_type = str(payload.get("issue_type") or "other")
    severity = str(payload.get("severity") or "medium")
    try:
        confidence = max(0.0, min(1.0, float(payload.get("confidence") or 0)))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "status": status,
        "issue_type": issue_type,
        "severity": severity,
        "confidence": confidence,
        "summary": str(payload.get("summary") or "")[:1200],
        "evidence": [str(item)[:500] for item in (payload.get("evidence") if isinstance(payload.get("evidence"), list) else [])[:6]],
        "recommended_actions": [str(item)[:500] for item in (payload.get("recommended_actions") if isinstance(payload.get("recommended_actions"), list) else [])[:6]],
        "should_quarantine": bool(payload.get("should_quarantine")),
        "should_regrade_after_publish": bool(payload.get("should_regrade_after_publish")),
        "draft_question_v2_patch": payload.get("draft_question_v2_patch") if isinstance(payload.get("draft_question_v2_patch"), dict) else {},
        "draft_answer_spec_patch": payload.get("draft_answer_spec_patch") if isinstance(payload.get("draft_answer_spec_patch"), dict) else {},
        "reviewer_notes": str(payload.get("reviewer_notes") or "")[:2000],
        "blocked_reason": str(payload.get("blocked_reason")) if payload.get("blocked_reason") is not None else None,
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "error": error,
    }


def suggest_question_repair(
    db: Session,
    question: Question,
    issue_type: str,
    note: str | None,
    impact: dict[str, Any] | None = None,
    use_mock: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    roles = model_roles(settings.default_model, settings)
    chain = provider_chain("mock" if use_mock else settings.model_provider, settings, db)
    packet = build_question_repair_review_packet(db, question, issue_type, note, impact)
    packet["reasoning_contract"] = {
        "private_work_required": True,
        "steps_before_final_json": [
            "inspect the current question and answer spec",
            "compare the reported issue with recent submissions/events",
            "draft the smallest safe repair patch",
            "decide whether quarantine or regrade is required",
        ],
        "final_output_only": "Return only one JSON object with concise reviewer_notes and patch fields. Do not reveal hidden chain-of-thought.",
    }
    messages = [
        {"role": "system", "content": guarded_system_prompt(question_repair_reviewer_system_prompt())},
        {
            "role": "user",
            "content": guarded_json_message(
                "question_repair_reviewer",
                packet,
                untrusted_fields=[
                    "payload.admin_note",
                    "payload.question.stem_preview",
                    "payload.question_v2",
                    "payload.answer_spec",
                    "payload.open_review_items",
                    "payload.recent_submissions",
                    "payload.recent_events",
                ],
            ),
        },
    ]
    request_payload = {
        "messages": messages,
        "role": "question_repair_reviewer",
        "question_id": question.id,
        "issue_type": issue_type,
        "use_mock": use_mock,
    }
    input_hash = hash_payload(request_payload)
    trace = root_ai_trace("question_repair_reviewer", question_id=question.id, role="question_repair_reviewer")
    results = chain.run_json(
        messages,
        "question_repair_reviewer",
        roles,
        {
            "question": question,
            "repair_packet": packet,
            "mock_factory": lambda: mock_question_repair_suggestion(packet),
        },
    )
    for result in results:
        traced_request, run_trace = request_payload_with_trace(request_payload, result, trace)
        db.add(
            AiRunLog(
                job_id=None,
                role="question_repair_reviewer",
                provider=result.provider,
                model=result.model,
                prompt_version=QUESTION_REPAIR_REVIEWER_PROMPT_VERSION,
                input_hash=input_hash,
                request_payload=sanitize_payload(traced_request),
                response_payload=sanitize_payload(result.raw_response),
                error=result.error,
                latency_ms=result.latency_ms,
                token_usage=token_usage_with_trace(result.token_usage, result, run_trace),
            )
        )
    final = results[-1]
    return normalize_question_repair_suggestion(final.payload, final.provider, final.model, final.latency_ms, final.error)
