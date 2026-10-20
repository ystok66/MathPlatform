from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .ai_gateway import hash_payload, model_roles, provider_chain
from .models import AiRunLog, AnswerSpec, Question, QuestionVersion
from .prompt_security import guarded_json_message, guarded_system_prompt
from .prompt_loader import prompt_template
from .localization import current_language


HINT_GENERATOR_PROMPT_VERSION = "hint_generator_v1"
HINT_REVIEWER_PROMPT_VERSION = "hint_reviewer_v1"


def hint_generator_system_prompt() -> str:
    return prompt_template('hint_generator_runtime_v1.md')


def hint_reviewer_system_prompt() -> str:
    return prompt_template('hint_reviewer_v1.md')


def stem_md_from_version(question: Question, version: QuestionVersion | None) -> str:
    qv2 = version.question_v2 if version else {}
    return str(((qv2.get("content") or {}).get("stem_md") or question.stem_preview or "")).strip()


def solution_from_version(version: QuestionVersion | None) -> dict[str, Any]:
    qv2 = version.question_v2 if version else {}
    solution = qv2.get("solution") if isinstance(qv2, dict) else {}
    return solution if isinstance(solution, dict) else {}


def build_hint_context(
    question: Question,
    version: QuestionVersion | None,
    answer: AnswerSpec | None,
    evidence: dict[str, Any],
    *,
    cycle: int,
    level: str,
) -> dict[str, Any]:
    return {
        "schema_version": "temporary_hint_generation_context_v1",
        "purpose": "temporary_hint_question",
        "cycle": cycle,
        "level": level,
        "base_question": {
            "id": question.id,
            "source_name": question.source_name,
            "difficulty_star": question.difficulty_star,
            "stem_md": stem_md_from_version(question, version)[:1800],
        },
        "base_answer_spec": answer.raw_answer_spec if answer else {},
        "base_solution": solution_from_version(version),
        "student_evidence": evidence,
        "output_contract": {
            "storage": "temporary_intervention_payload_only",
            "must_be_question_not_explanation": True,
            "must_not_reveal_original_answer": True,
            "must_not_name_student_weakness": True,
            "unique_answer_required": True,
            "star_not_required": True,
            "answer_spec_must_grade_automatically": True,
            "required_top_level_fields": ["status", "student_visible_intro", "hint_question", "private_diagnosis", "review_checks"],
            "required_hint_question_fields": ["stem_md", "answer_spec", "solution"],
        },
        "reasoning_contract": {
            "private_work_required": True,
            "use_student_evidence": ["answer_text", "thought_text", "effective_wrong_count", "help_and_solution_events"],
            "infer_likely_gap_privately": True,
            "do_not_show_diagnosis_to_student": True,
            "final_output_only": "Return only one JSON object containing a real temporary hint question, answer_spec, solution, and a concise private_diagnosis label/summary. Do not output hidden chain-of-thought or scratchpad.",
            "parser_behavior": "The platform stores only the temporary hint payload and deterministic-grades hint attempts; it does not add this question to the bank or reports.",
        },
    }


def build_generator_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": guarded_system_prompt(hint_generator_system_prompt())},
        {
            "role": "user",
            "content": guarded_json_message(
                "hint_generator",
                context,
                untrusted_fields=[
                    "payload.base_question.stem_md",
                    "payload.base_solution",
                    "payload.student_evidence",
                    "payload.student_evidence.submissions",
                    "payload.student_evidence.events",
                ],
            ),
        },
    ]


def build_reviewer_messages(context: dict[str, Any], generated_payload: dict[str, Any]) -> list[dict[str, str]]:
    review_context = {
        "schema_version": "temporary_hint_review_context_v1",
        "base_question": context.get("base_question"),
        "base_answer_spec": context.get("base_answer_spec"),
        "base_solution": context.get("base_solution"),
        "student_evidence_summary": {
            "attempt_count": (context.get("student_evidence") or {}).get("attempt_count"),
            "effective_wrong_count": (context.get("student_evidence") or {}).get("effective_wrong_count"),
        },
        "generated_payload": generated_payload,
        "required_review_output": {
            "decision": "accept|reject",
            "independent_answer": "string",
            "matches_answer_spec": "boolean",
            "is_real_question": "boolean",
            "leaks_original_answer": "boolean",
            "leaks_student_weakness": "boolean",
            "solution_correct": "boolean",
            "issues": "string[]",
            "reviewer_notes": "string",
        },
    }
    return [
        {"role": "system", "content": guarded_system_prompt(hint_reviewer_system_prompt())},
        {
            "role": "user",
            "content": guarded_json_message(
                "hint_reviewer",
                review_context,
                untrusted_fields=[
                    "payload.base_question.stem_md",
                    "payload.base_solution",
                    "payload.generated_payload",
                ],
            ),
        },
    ]


def normalize_answer_spec(raw: dict[str, Any]) -> dict[str, Any]:
    spec = dict(raw or {})
    canonical = spec.get("canonical_answer")
    if canonical is None:
        canonical_values = spec.get("canonical")
        if isinstance(canonical_values, list) and canonical_values:
            canonical = canonical_values[0]
    spec["canonical_answer"] = str(canonical or "").strip()
    spec["answer_type"] = str(spec.get("answer_type") or spec.get("type") or "text").strip() or "text"
    spec.setdefault("acceptable_answers", [])
    spec.setdefault("tolerance", {"type": "exact", "value": 0})
    spec.setdefault("order_sensitive", True)
    spec.setdefault("grading_method", "exact_normalized")
    spec.setdefault("status", "verified")
    return spec


def normalize_hint_payload(payload: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(payload, dict):
        return None, ["missing_payload"]
    if str(payload.get("status") or "accept").lower() == "reject":
        return None, ["generator_rejected"]
    hint = payload.get("hint_question")
    if isinstance(hint, str):
        hint = {"stem_md": hint}
    if not isinstance(hint, dict):
        hint = payload.get("question") if isinstance(payload.get("question"), dict) else None
    if not isinstance(hint, dict):
        return None, ["missing_hint_question"]
    stem_md = str(hint.get("stem_md") or hint.get("question_text") or "").strip()
    raw_answer_spec = hint.get("answer_spec") if isinstance(hint.get("answer_spec"), dict) else payload.get("answer_spec")
    answer_spec = normalize_answer_spec(raw_answer_spec if isinstance(raw_answer_spec, dict) else {})
    raw_solution = hint.get("solution") if isinstance(hint.get("solution"), dict) else payload.get("solution")
    if not isinstance(raw_solution, dict):
        raw_solution = payload.get("student_solution")
    solution = raw_solution if isinstance(raw_solution, dict) else {}
    final_answer = str((solution or {}).get("final_answer") or answer_spec.get("canonical_answer") or "").strip()
    issues: list[str] = []
    if not stem_md:
        issues.append("missing_stem_md")
    if not answer_spec.get("canonical_answer"):
        issues.append("missing_canonical_answer")
    if not final_answer:
        issues.append("missing_solution_final_answer")
    normalized = {
        "schema_version": "temporary_hint_question_v1",
        "id": str(hint.get("id") or ""),
        "stem_md": stem_md,
        "answer_spec": answer_spec,
        "solution": {**solution, "final_answer": final_answer},
        "student_visible_intro": str(payload.get("student_visible_intro") or "先做一个小问题。").strip(),
    }
    return (normalized if not issues else None), issues


def review_accepts(payload: dict[str, Any] | None) -> tuple[bool, list[str]]:
    if not isinstance(payload, dict):
        return False, ["missing_review_payload"]
    decision = str(payload.get("decision") or "").strip().lower()
    issues = [str(item) for item in payload.get("issues", []) if item]
    checks = {
        "matches_answer_spec": coerce_review_bool(payload.get("matches_answer_spec")) is True,
        "is_real_question": coerce_review_bool(payload.get("is_real_question")) is True,
        "leaks_original_answer": coerce_review_bool(payload.get("leaks_original_answer")) is False,
        "leaks_student_weakness": coerce_review_bool(payload.get("leaks_student_weakness")) is False,
        "solution_correct": coerce_review_bool(payload.get("solution_correct")) is True,
    }
    for key, ok in checks.items():
        if not ok:
            issues.append(key)
    return decision in {"accept", "approve", "approved", "ok"} and not issues, sorted(set(issues))


def coerce_review_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    lowered = str(value).strip().lower()
    if lowered in {"true", "yes", "1", "accept", "accepted", "pass", "通过", "是"}:
        return True
    if lowered in {"false", "no", "0", "reject", "rejected", "fail", "不通过", "否"}:
        return False
    return None


def mock_hint_payload(context: dict[str, Any]) -> dict[str, Any]:
    wrong_count = (context.get("student_evidence") or {}).get("effective_wrong_count") or 0
    return {
        "status": "accept",
        "student_visible_intro": "先做一个更小的问题。",
        "hint_question": {
            "stem_md": f"有 {int(wrong_count) + 2} 盒彩笔，每盒 3 支。一共有多少支彩笔？",
            "answer_spec": {
                "status": "verified",
                "answer_type": "integer",
                "canonical_answer": str((int(wrong_count) + 2) * 3),
                "acceptable_answers": [],
                "tolerance": {"type": "exact", "value": 0},
                "grading_method": "exact_normalized",
            },
            "solution": {
                "steps": ["把每盒支数相加，等价于盒数乘每盒支数。"],
                "final_answer": str((int(wrong_count) + 2) * 3),
            },
        },
        "private_diagnosis": {"likely_gap": "mock"},
    }


def mock_review_payload(generated_payload: dict[str, Any]) -> dict[str, Any]:
    hint, issues = normalize_hint_payload(generated_payload)
    return {
        "decision": "accept" if hint and not issues else "reject",
        "independent_answer": ((hint or {}).get("answer_spec") or {}).get("canonical_answer", ""),
        "matches_answer_spec": bool(hint and not issues),
        "is_real_question": bool(hint),
        "leaks_original_answer": False,
        "leaks_student_weakness": False,
        "solution_correct": bool(hint and not issues),
        "issues": issues,
        "reviewer_notes": "mock review",
    }


def log_ai_results(
    db: Session,
    *,
    role: str,
    prompt_version: str,
    input_hash: str,
    request_payload: dict[str, Any],
    results: list[Any],
) -> None:
    for result in results:
        db.add(
            AiRunLog(
                job_id=None,
                role=role,
                provider=result.provider,
                model=result.model,
                prompt_version=prompt_version,
                input_hash=input_hash,
                request_payload=request_payload,
                response_payload=result.payload,
                error=result.error,
                latency_ms=result.latency_ms,
                token_usage=result.token_usage or {},
            )
        )


def generate_reviewed_temporary_hint_question(
    db: Session,
    question: Question,
    version: QuestionVersion | None,
    answer: AnswerSpec | None,
    evidence: dict[str, Any],
    *,
    cycle: int,
    level: str,
    provider: str,
    settings: Any,
) -> dict[str, Any]:
    roles = model_roles(settings.default_model, settings)
    if "hint_reviewer" not in roles:
        roles = {**roles, "hint_reviewer": roles.get("reviewer") or roles.get("default") or settings.default_model}
    chain = provider_chain(provider, settings, db)
    context = build_hint_context(question, version, answer, evidence, cycle=cycle, level=level)
    generator_messages = build_generator_messages(context)
    generator_request = {"messages": generator_messages, "context": context, "role": "hint_generator"}
    generator_hash = hash_payload(generator_request)
    generator_results = chain.run_json(generator_messages, "hint_generator", roles, {"mock_factory": lambda: mock_hint_payload(context)})
    log_ai_results(
        db,
        role="hint_generator",
        prompt_version=HINT_GENERATOR_PROMPT_VERSION,
        input_hash=generator_hash,
        request_payload=generator_request,
        results=generator_results,
    )
    generated_payload = generator_results[-1].payload if generator_results else None
    hint, generation_issues = normalize_hint_payload(generated_payload)
    if not hint:
        return {
            "status": "failed",
            "stage": "generator",
            "issues": generation_issues,
            "provider": generator_results[-1].provider if generator_results else provider,
            "model": generator_results[-1].model if generator_results else roles.get("hint_generator"),
            "error": generator_results[-1].error if generator_results else "generator_failed",
        }

    reviewer_messages = build_reviewer_messages(context, generated_payload or {})
    reviewer_request = {"messages": reviewer_messages, "candidate": generated_payload, "role": "hint_reviewer"}
    reviewer_hash = hash_payload(reviewer_request)
    reviewer_results = chain.run_json(reviewer_messages, "hint_reviewer", roles, {"mock_factory": lambda: mock_review_payload(generated_payload or {})})
    log_ai_results(
        db,
        role="hint_reviewer",
        prompt_version=HINT_REVIEWER_PROMPT_VERSION,
        input_hash=reviewer_hash,
        request_payload=reviewer_request,
        results=reviewer_results,
    )
    review_payload = reviewer_results[-1].payload if reviewer_results else None
    accepted, review_issues = review_accepts(review_payload)
    if not accepted:
        return {
            "status": "failed",
            "stage": "reviewer",
            "hint_question": hint,
            "issues": review_issues,
            "review": review_payload,
            "provider": reviewer_results[-1].provider if reviewer_results else provider,
            "model": reviewer_results[-1].model if reviewer_results else roles.get("hint_reviewer"),
            "error": reviewer_results[-1].error if reviewer_results else "reviewer_rejected",
        }
    hint["id"] = hint["id"] or f"tmp-hint-{question.id}-{cycle}"
    return {
        "status": "accepted",
        "hint_question": hint,
        "private_diagnosis": (generated_payload or {}).get("private_diagnosis") if isinstance(generated_payload, dict) else None,
        "review": review_payload,
        "generator": {
            "provider": generator_results[-1].provider,
            "model": generator_results[-1].model,
            "latency_ms": generator_results[-1].latency_ms,
        },
        "reviewer": {
            "provider": reviewer_results[-1].provider,
            "model": reviewer_results[-1].model,
            "latency_ms": reviewer_results[-1].latency_ms,
        },
    }
