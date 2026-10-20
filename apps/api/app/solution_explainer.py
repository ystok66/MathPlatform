from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .ai_gateway import hash_payload, model_roles, provider_chain
from .ai_trace import request_payload_with_trace, root_ai_trace, token_usage_with_trace
from .audit import sanitize_payload
from .config import get_settings
from .models import AiRunLog, AnswerSpec, Question, QuestionVersion
from .prompt_loader import solution_explainer_system_prompt
from .prompt_security import guarded_json_message, guarded_system_prompt


SOLUTION_EXPLAINER_PROMPT_VERSION = "solution_explainer_v1"


def _string_list(value: Any, limit: int = 4) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


def build_solution_explanation_messages(
    question: Question,
    version: QuestionVersion | None,
    answer: AnswerSpec | None,
    student_question: str,
    practice_evidence: dict[str, Any],
) -> list[dict[str, str]]:
    qv2 = version.question_v2 if version else {}
    solution = qv2.get("solution") or {}
    payload = {
        "answer_already_revealed": True,
        "question": {
            "id": question.id,
            "source_name": question.source_name,
            "difficulty_star": question.difficulty_star,
            "stem_md": ((qv2.get("content") or {}).get("stem_md") or question.stem_preview),
        },
        "answer_spec": answer.raw_answer_spec if answer else None,
        "solution": solution,
        "practice_evidence": practice_evidence,
        "student_question": student_question,
        "reasoning_contract": {
            "private_work_required": True,
            "use_student_evidence": ["answer_text", "thought_text", "help_level", "solution_revealed", "mistake_reason"],
            "audience": "elementary_school_student",
            "final_output_only": "Return only one JSON object. Do not expose hidden chain-of-thought; write a concise child-friendly explanation, key_points, common_misunderstandings, and next_review_suggestion.",
            "teaching_policy": "Explain only after answer was revealed. It is okay to explain the shown answer, but do not invent facts outside the question and recorded attempts.",
        },
    }
    return [
        {"role": "system", "content": guarded_system_prompt(solution_explainer_system_prompt())},
        {
            "role": "user",
            "content": guarded_json_message(
                "solution_explainer",
                payload,
                untrusted_fields=[
                    "payload.question.stem_md",
                    "payload.student_question",
                    "payload.practice_evidence",
                ],
            ),
        },
    ]


def mock_solution_explanation(question: Question, version: QuestionVersion | None, answer: AnswerSpec | None, student_question: str) -> dict[str, Any]:
    qv2 = version.question_v2 if version else {}
    solution = qv2.get("solution") or {}
    steps = solution.get("student_steps") or [row.get("explanation", "") for row in solution.get("steps", []) if isinstance(row, dict)]
    final_answer = solution.get("final_answer") or (answer.canonical_answer if answer else "")
    key_insight = solution.get("key_insight") or solution.get("solution_hook") or "先把题目条件整理成可检查的数量关系。"
    first_step = steps[0] if steps else "先找出题目中不变或必须同时满足的条件。"
    return {
        "status": "ok",
        "explanation": f"你问的是：{student_question.strip()}。这题已经看过答案，可以围绕关键想法理解：{key_insight} 第一步通常是：{first_step} 最终答案是 {final_answer}。",
        "key_points": [key_insight, first_step],
        "common_misunderstandings": ["只盯着最后答案，忽略题目条件之间的对应关系。"],
        "next_review_suggestion": "先复看第一步为什么成立，再做一题同星级掌握检测题。",
        "refused": False,
        "refusal_reason": None,
    }


def normalize_solution_explanation_payload(payload: dict[str, Any] | None, provider: str, model: str, latency_ms: int | None, error: str | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "status": "error",
            "explanation": "",
            "key_points": [],
            "common_misunderstandings": [],
            "next_review_suggestion": "",
            "refused": False,
            "refusal_reason": None,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "error": error or "invalid_json",
        }
    refused = bool(payload.get("refused") or str(payload.get("status") or "").lower() == "refused")
    return {
        "status": "refused" if refused else str(payload.get("status") or "ok"),
        "explanation": str(payload.get("explanation") or "")[:3000],
        "key_points": _string_list(payload.get("key_points")),
        "common_misunderstandings": _string_list(payload.get("common_misunderstandings")),
        "next_review_suggestion": str(payload.get("next_review_suggestion") or "")[:800],
        "refused": refused,
        "refusal_reason": str(payload.get("refusal_reason")) if payload.get("refusal_reason") is not None else None,
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "error": error,
    }


def explain_solution_with_ai(
    db: Session,
    question: Question,
    version: QuestionVersion | None,
    answer: AnswerSpec | None,
    student_question: str,
    practice_evidence: dict[str, Any],
) -> dict[str, Any]:
    settings = get_settings()
    roles = model_roles(settings.default_model, settings)
    chain = provider_chain(settings.model_provider, settings, db)
    messages = build_solution_explanation_messages(question, version, answer, student_question, practice_evidence)
    request_payload = {
        "messages": messages,
        "role": "solution_explainer",
        "question_id": question.id,
        "student_question": student_question,
    }
    input_hash = hash_payload(request_payload)
    trace = root_ai_trace("solution_explainer", question_id=question.id, role="solution_explainer")
    results = chain.run_json(
        messages,
        "solution_explainer",
        roles,
        {
            "question": question,
            "answer_spec": answer.raw_answer_spec if answer else None,
            "student_question": student_question,
            "practice_evidence": practice_evidence,
            "mock_factory": lambda: mock_solution_explanation(question, version, answer, student_question),
        },
    )
    for result in results:
        traced_request, run_trace = request_payload_with_trace(request_payload, result, trace)
        db.add(
            AiRunLog(
                job_id=None,
                role="solution_explainer",
                provider=result.provider,
                model=result.model,
                prompt_version=SOLUTION_EXPLAINER_PROMPT_VERSION,
                input_hash=input_hash,
                request_payload=sanitize_payload(traced_request),
                response_payload=sanitize_payload(result.raw_response),
                error=result.error,
                latency_ms=result.latency_ms,
                token_usage=token_usage_with_trace(result.token_usage, result, run_trace),
            )
        )
    final = results[-1]
    return normalize_solution_explanation_payload(final.payload, final.provider, final.model, final.latency_ms, final.error)
