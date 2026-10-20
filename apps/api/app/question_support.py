from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .ai_gateway import hash_payload, model_roles, provider_chain
from .ai_trace import request_payload_with_trace, root_ai_trace, token_usage_with_trace
from .audit import sanitize_payload
from .config import get_settings
from .models import AiRunLog, AnswerSpec, PracticeSession, Question, QuestionTag, QuestionVersion
from .prompt_loader import question_support_system_prompt
from .prompt_security import guarded_json_message, guarded_system_prompt


QUESTION_SUPPORT_PROMPT_VERSION = "question_support_v1"
QUESTION_SUPPORT_PROBLEM_THRESHOLD = 0.7
AI_SOURCE_NAME = "AI生成题"


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    return lowered in {"true", "yes", "1", "ok", "problem", "有问题", "是"}


def _coerce_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _safe_text(value: Any) -> str:
    text = str(value or "").strip()
    forbidden_markers = ("答案", "解法", "步骤", "提示", "下一步", "先算", "可以先", "等于")
    if any(marker in text for marker in forbidden_markers) and len(text) > 80:
        return "已记录你的问题。这里不能提供解题提示或答案；如题面显示异常，会尝试更换同星级题。"
    return text[:300] or "已记录你的问题。这里只能处理题面格式、符号和题意表达问题，不能提供解题提示。"


def build_question_support_messages(
    question: Question,
    version: QuestionVersion | None,
    answer: AnswerSpec | None,
    concern_type: str,
    message: str,
    strict_mode: bool,
) -> list[dict[str, str]]:
    qv2 = version.question_v2 if version else {}
    payload = {
        "question": {
            "id": question.id,
            "source_name": question.source_name,
            "difficulty_star": question.difficulty_star,
            "stem_md": ((qv2.get("content") or {}).get("stem_md") or question.stem_preview),
        },
        "answer_spec_metadata": {
            "status": answer.status if answer else None,
            "answer_type": answer.answer_type if answer else None,
            "grading_method": (answer.raw_answer_spec or {}).get("grading_method") if answer else None,
        },
        "concern_type": concern_type,
        "student_message": message,
        "strict_mode": strict_mode,
        "reasoning_contract": {
            "private_work_required": True,
            "steps_before_final_json": [
                "classify whether the issue is display/format/ambiguity versus a request for help",
                "check whether the question appears internally inconsistent",
                "decide whether replacement should be recommended without giving hints",
                "in strict mode, avoid all answer, method, and next-step content",
            ],
            "final_output_only": "Return only one JSON object with question_problem, issue_type, safe_response, and confidence. Do not reveal hidden chain-of-thought.",
        },
        "policy": "只能澄清题面显示、符号、题意表达；禁止给答案、步骤、提示、方法或下一步。",
    }
    return [
        {"role": "system", "content": guarded_system_prompt(question_support_system_prompt())},
        {
            "role": "user",
            "content": guarded_json_message(
                "question_support",
                payload,
                untrusted_fields=[
                    "payload.question.stem_md",
                    "payload.student_message",
                    "payload.answer_spec_metadata",
                ],
            ),
        },
    ]


def mock_question_support(concern_type: str, message: str) -> dict[str, Any]:
    text = message.strip()
    problem_markers = ("格式", "显示", "乱码", "重复", "缺图", "缺少图", "看不清", "断行", "循环点", "圆点", "竖式", "表格", "题目错", "有问题")
    help_markers = ("不会", "思路", "怎么做", "提示", "答案", "解法", "下一步")
    if concern_type in {"format_issue", "suspected_error"} and any(marker in text for marker in problem_markers):
        return {
            "status": "ok",
            "question_problem": True,
            "issue_type": "broken_format",
            "safe_response": "已记录题目格式问题，将尝试更换同星级题。",
            "confidence": 0.82,
        }
    if any(marker in text for marker in help_markers):
        return {
            "status": "ok",
            "question_problem": False,
            "issue_type": "off_policy_help_request",
            "safe_response": "这里不能提供解题提示或答案。若你认为题面显示有问题，请具体说明缺图、乱码、断行或符号异常。",
            "confidence": 0.9,
        }
    return {
        "status": "ok",
        "question_problem": False,
        "issue_type": "none",
        "safe_response": "已记录你的问题。当前只能澄清题面格式和符号含义，不提供解题提示。",
        "confidence": 0.6,
    }


def normalize_question_support_payload(payload: dict[str, Any] | None, provider: str, model: str, latency_ms: int | None, error: str | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "status": "error",
            "question_problem": False,
            "issue_type": "provider_error",
            "safe_response": "题目质疑暂时不可用，已记录为待复核。",
            "confidence": 0.0,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "error": error or "invalid_json",
        }
    confidence = _coerce_confidence(payload.get("confidence"))
    question_problem = _coerce_bool(payload.get("question_problem")) and confidence >= QUESTION_SUPPORT_PROBLEM_THRESHOLD
    return {
        "status": str(payload.get("status") or "ok"),
        "question_problem": question_problem,
        "issue_type": str(payload.get("issue_type") or ("unknown" if question_problem else "none")),
        "safe_response": _safe_text(payload.get("safe_response")),
        "confidence": confidence,
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "error": error,
    }


def run_question_support(
    db: Session,
    question: Question,
    version: QuestionVersion | None,
    answer: AnswerSpec | None,
    concern_type: str,
    message: str,
    strict_mode: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    roles = model_roles(settings.default_model, settings)
    chain = provider_chain(settings.model_provider, settings, db)
    messages = build_question_support_messages(question, version, answer, concern_type, message, strict_mode)
    request_payload = {
        "messages": messages,
        "role": "diagnostician",
        "question_id": question.id,
        "concern_type": concern_type,
    }
    input_hash = hash_payload(request_payload)
    trace = root_ai_trace("question_support", question_id=question.id, role="question_support", concern_type=concern_type)
    results = chain.run_json(
        messages,
        "diagnostician",
        roles,
        {
            "question": question,
            "concern_type": concern_type,
            "student_message": message,
            "mock_factory": lambda: mock_question_support(concern_type, message),
        },
    )
    for result in results:
        traced_request, run_trace = request_payload_with_trace(request_payload, result, trace)
        db.add(
            AiRunLog(
                job_id=None,
                role="question_support",
                provider=result.provider,
                model=result.model,
                prompt_version=QUESTION_SUPPORT_PROMPT_VERSION,
                input_hash=input_hash,
                request_payload=sanitize_payload(traced_request),
                response_payload=sanitize_payload(result.raw_response),
                error=result.error,
                latency_ms=result.latency_ms,
                token_usage=token_usage_with_trace(result.token_usage, result, run_trace),
            )
        )
    final = results[-1]
    return normalize_question_support_payload(final.payload, final.provider, final.model, final.latency_ms, final.error)


def _eligible_replacement_stmt(session: PracticeSession, current_question: Question):
    stmt = (
        select(Question)
        .join(AnswerSpec, AnswerSpec.question_id == Question.id)
        .where(
            Question.id != current_question.id,
            Question.status == "verified",
            AnswerSpec.status == "verified",
            Question.question_fault_status == "none",
            Question.difficulty_star == current_question.difficulty_star,
        )
        .limit(200)
    )
    if session.question_ids:
        stmt = stmt.where(Question.id.not_in(session.question_ids))
    if session.mode == "strict_test":
        selection = (session.config or {}).get("selection") or {}
        policy = selection.get("policy") or {}
        source_mode = str(policy.get("strict_source_mode") or "bank_only")
        bank_strict = and_(
            Question.source_name != AI_SOURCE_NAME,
            Question.eligible_for_test.is_(True),
            Question.publish_status.in_(("active_test", "source_verified")),
        )
        ai_verified = and_(
            Question.source_name == AI_SOURCE_NAME,
            Question.eligible_for_practice.is_(True),
            Question.publish_status.in_(("active_practice", "active_test", "source_verified")),
        )
        if source_mode == "ai_only":
            stmt = stmt.where(ai_verified)
        elif source_mode == "ai_mixed":
            stmt = stmt.where(or_(bank_strict, ai_verified))
        else:
            stmt = stmt.where(bank_strict)
    else:
        stmt = stmt.where(Question.eligible_for_practice.is_(True), Question.publish_status.not_in(("intervention_only", "quarantined", "retired", "duplicate_alias")))
    return stmt.order_by(Question.source_name, Question.id)


def find_replacement_question_id(db: Session, session: PracticeSession, current_question: Question) -> str | None:
    candidates = list(db.scalars(_eligible_replacement_stmt(session, current_question)).unique())
    if not candidates:
        return None
    current_tags = set(db.scalars(select(QuestionTag.tag_id).where(QuestionTag.question_id == current_question.id)))
    tag_rows = db.execute(select(QuestionTag.question_id, QuestionTag.tag_id).where(QuestionTag.question_id.in_([row.id for row in candidates]))).all()
    tags_by_question: dict[str, set[str]] = {}
    for question_id, tag_id in tag_rows:
        tags_by_question.setdefault(question_id, set()).add(tag_id)

    def score(question: Question) -> tuple[int, int, str, str]:
        overlap = len(current_tags & tags_by_question.get(question.id, set()))
        same_source_penalty = 0 if question.source_name == current_question.source_name else 1
        return (-overlap, same_source_penalty, question.source_name, question.id)

    candidates.sort(key=score)
    return candidates[0].id

