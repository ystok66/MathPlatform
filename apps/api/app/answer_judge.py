from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .ai_gateway import hash_payload, model_roles, provider_chain
from .ai_trace import request_payload_with_trace, root_ai_trace, token_usage_with_trace
from .audit import sanitize_payload
from .config import get_settings
from .grading import grade_answer, parse_number, split_multi
from .models import AiRunLog, AnswerSpec, Question, QuestionVersion
from .prompt_loader import answer_judge_system_prompt
from .prompt_security import guarded_json_message, guarded_system_prompt


ANSWER_JUDGE_PROMPT_VERSION = "answer_judge_v1"
SINGLE_NUMERIC_ANSWER_TYPES = {"integer", "number", "fraction", "decimal"}
MULTI_OR_SEMANTIC_ANSWER_TYPES = {"set", "multi_blank", "ordered_tuple", "choice", "text", "open_response", "expression"}


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    lowered = str(value).strip().lower()
    if lowered in {"true", "yes", "correct", "对", "正确"}:
        return True
    if lowered in {"false", "no", "incorrect", "错", "错误"}:
        return False
    return None


def _coerce_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _same_numeric_value(values: list[str]) -> bool:
    parsed = [parse_number(value) for value in values if str(value).strip()]
    return bool(parsed) and all(value is not None for value in parsed) and len(set(parsed)) == 1


def answer_requires_ai_judge(spec: dict[str, Any]) -> bool:
    if spec.get("status") != "verified":
        return True
    answer_type = str(spec.get("answer_type") or "text")
    grading_method = str(spec.get("grading_method") or "")
    canonical = str(spec.get("canonical_answer") or "")
    accepted = [str(value) for value in (spec.get("acceptable_answers") or [])]

    if answer_type in MULTI_OR_SEMANTIC_ANSWER_TYPES:
        return True
    if grading_method in {"set_exact", "ordered_tuple_exact", "expression_equiv"}:
        return True
    if answer_type in SINGLE_NUMERIC_ANSWER_TYPES:
        if "\\" in canonical or any(marker in canonical for marker in ("循环", "…", "...", "{", "}", ";", "；", "," , "，", "、")):
            return True
        if len(split_multi(canonical)) > 1:
            return True
        if accepted and not _same_numeric_value([canonical, *accepted]):
            return True
        return False
    return True


def should_run_ai_answer_judge(spec: dict[str, Any], deterministic_result: dict[str, Any]) -> bool:
    if deterministic_result.get("is_correct") is True:
        return False
    return answer_requires_ai_judge(spec)


def build_answer_judge_messages(
    question: Question,
    version: QuestionVersion | None,
    answer: AnswerSpec,
    answer_text: str,
    thought_text: str | None,
    deterministic_result: dict[str, Any],
    strict_mode: bool = False,
) -> list[dict[str, str]]:
    qv2 = version.question_v2 if version else {}
    payload = {
        "question": {
            "id": question.id,
            "source_name": question.source_name,
            "difficulty_star": question.difficulty_star,
            "stem_md": ((qv2.get("content") or {}).get("stem_md") or question.stem_preview),
        },
        "answer_spec": answer.raw_answer_spec,
        "deterministic_result": deterministic_result,
        "student_answer": answer_text,
        "student_thought": thought_text,
        "strict_mode": strict_mode,
        "reasoning_contract": {
            "private_work_required": True,
            "steps_before_final_json": [
                "interpret the official answer_spec",
                "normalize the student answer",
                "compare mathematical equivalence",
                "check whether deterministic grading was too strict",
                "in strict mode, hide explanations and answers from the student",
            ],
            "final_output_only": "Return only one JSON object with verdict, is_correct, confidence, concise analysis/student_feedback, and override fields. Do not reveal hidden chain-of-thought.",
        },
        "strict_output_policy": "严格测试中只能返回判定字段，不能解释解法、不能给提示、不能展示标准答案。" if strict_mode else "可给出不泄露标准答案的格式反馈。",
    }
    return [
        {"role": "system", "content": guarded_system_prompt(answer_judge_system_prompt())},
        {
            "role": "user",
            "content": guarded_json_message(
                "answer_judge",
                payload,
                untrusted_fields=[
                    "payload.question.stem_md",
                    "payload.student_answer",
                    "payload.student_thought",
                    "payload.deterministic_result",
                ],
            ),
        },
    ]


def normalize_answer_judge_payload(payload: dict[str, Any] | None, deterministic_result: dict[str, Any], provider: str, model: str, latency_ms: int | None, error: str | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        is_correct = deterministic_result.get("is_correct") if error is None else None
        verdict = "correct" if is_correct is True else "incorrect" if is_correct is False else "uncertain"
        return {
            "verdict": verdict,
            "is_correct": is_correct,
            "confidence": 0.0 if error else 0.4,
            "analysis": "AI 复核没有返回可解析 JSON。",
            "student_feedback": "AI 复核暂不可用，请以规则判分和人工复核为准。",
            "normalized_student_answer": None,
            "missing_parts": [],
            "extra_parts": [],
            "format_issue": None,
            "should_override_deterministic": False,
            "override_reason": None,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "error": error,
        }

    is_correct = _coerce_bool(payload.get("is_correct"))
    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in {"correct", "incorrect", "uncertain"}:
        verdict = "correct" if is_correct is True else "incorrect" if is_correct is False else "uncertain"
    if verdict == "correct":
        is_correct = True
    elif verdict == "incorrect":
        is_correct = False
    else:
        is_correct = None

    return {
        "verdict": verdict,
        "is_correct": is_correct,
        "confidence": _coerce_confidence(payload.get("confidence")),
        "analysis": str(payload.get("analysis") or ""),
        "student_feedback": str(payload.get("student_feedback") or ""),
        "normalized_student_answer": str(payload.get("normalized_student_answer")) if payload.get("normalized_student_answer") is not None else None,
        "missing_parts": _string_list(payload.get("missing_parts")),
        "extra_parts": _string_list(payload.get("extra_parts")),
        "format_issue": str(payload.get("format_issue")) if payload.get("format_issue") is not None else None,
        "should_override_deterministic": bool(payload.get("should_override_deterministic") or False),
        "override_reason": str(payload.get("override_reason")) if payload.get("override_reason") is not None else None,
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "error": error,
    }


def sanitize_strict_answer_judge(result: dict[str, Any]) -> dict[str, Any]:
    return {
        **result,
        "analysis": "严格测试中已完成答案复核，解释内容已隐藏。",
        "student_feedback": "严格测试中不显示复核解释。",
        "normalized_student_answer": None,
        "missing_parts": [],
        "extra_parts": [],
        "format_issue": None,
        "override_reason": "strict_mode_hidden" if result.get("override_reason") else None,
    }


def judge_answer_with_ai(
    db: Session,
    question: Question,
    version: QuestionVersion | None,
    answer: AnswerSpec,
    answer_text: str,
    thought_text: str | None = None,
    strict_mode: bool = False,
    deterministic_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    deterministic_result = deterministic_result or grade_answer(answer.raw_answer_spec, answer_text)
    settings = get_settings()
    roles = model_roles(settings.default_model, settings)
    chain = provider_chain(settings.model_provider, settings, db)
    messages = build_answer_judge_messages(question, version, answer, answer_text, thought_text, deterministic_result, strict_mode)
    request_payload = {
        "messages": messages,
        "role": "answer_judge",
        "question_id": question.id,
        "deterministic_result": deterministic_result,
    }
    input_hash = hash_payload(request_payload)
    trace = root_ai_trace("answer_judge", question_id=question.id, role="answer_judge")
    results = chain.run_json(
        messages,
        "answer_judge",
        roles,
        {
            "question": question,
            "answer_spec": answer.raw_answer_spec,
            "student_answer": answer_text,
            "deterministic_result": deterministic_result,
        },
    )

    for result in results:
        traced_request, run_trace = request_payload_with_trace(request_payload, result, trace)
        db.add(
            AiRunLog(
                job_id=None,
                role="answer_judge",
                provider=result.provider,
                model=result.model,
                prompt_version=ANSWER_JUDGE_PROMPT_VERSION,
                input_hash=input_hash,
                request_payload=sanitize_payload(traced_request),
                response_payload=sanitize_payload(result.raw_response),
                error=result.error,
                latency_ms=result.latency_ms,
                token_usage=token_usage_with_trace(result.token_usage, result, run_trace),
            )
        )

    final = results[-1]
    normalized = normalize_answer_judge_payload(final.payload, deterministic_result, final.provider, final.model, final.latency_ms, final.error)
    result = {
        "question_id": question.id,
        "deterministic_result": deterministic_result,
        **normalized,
    }
    return sanitize_strict_answer_judge(result) if strict_mode else result


