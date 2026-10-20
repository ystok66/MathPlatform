from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_providers import MockProvider, ProviderChain
from .models import AnswerEvent, Question, QuestionTag, ReviewRecord, Submission, TaxonomyTag, WrongQuestionRecord, utc_now
from .prompt_security import guarded_json_message, guarded_system_prompt


ERROR_TYPE_BY_REASON = {
    "numeric_mismatch": "calculation_or_numeric",
    "numeric_with_tolerance": "calculation_or_numeric",
    "not_numeric": "format_or_expression",
    "unit_missing": "format_or_expression",
    "unit_mismatch": "format_or_expression",
    "text_mismatch": "format_or_expression",
    "multi_part": "multi_part_or_order",
    "part_count_mismatch": "multi_part_or_order",
    "part_value_or_order_mismatch": "multi_part_or_order",
    "set_compare": "multi_part_or_order",
    "set_missing": "multi_part_or_order",
    "set_extra": "multi_part_or_order",
    "set_mismatch": "multi_part_or_order",
    "set_duplicate_part": "multi_part_or_order",
    "expression_mismatch": "algebraic_expression",
    "expression_unverified": "review_or_uncertain",
    "answer_spec_invalid": "review_or_uncertain",
    "question_needs_review": "review_or_uncertain",
}

PREREQUISITE_BY_ERROR = {
    "calculation_or_numeric": ["计算准确性", "数量关系复核"],
    "format_or_expression": ["答案表达规范", "题意到答案形式转换"],
    "multi_part_or_order": ["多空顺序意识", "集合与有序结果表达"],
    "algebraic_expression": ["代数表达等价", "式子化简"],
    "review_or_uncertain": ["人工复核"],
    "other_wrong": ["解题策略复盘"],
}

OFF_TASK_MARKERS = ("哈哈", "随便", "乱写", "玩", "不知道", "不会", "懒得", "无语", "abc", "asdf")
UNCLEAR_MARKERS = ("不清楚", "大概", "可能", "猜", "试试", "感觉")


def classify_error(grading_result: dict[str, Any] | None, is_correct: bool | None) -> str:
    if is_correct is True:
        return "correct"
    if is_correct is None:
        return "review_or_uncertain"
    reason = (grading_result or {}).get("reason", "")
    return ERROR_TYPE_BY_REASON.get(reason, "other_wrong")


def classify_thought(thought_text: str | None) -> dict[str, Any]:
    text = (thought_text or "").strip()
    compact = re.sub(r"\s+", "", text.lower())
    if not compact:
        return {
            "thought_quality": "missing_thought",
            "confidence": "low",
            "deterministic_report_allowed": False,
            "reason": "no_thought_text",
        }
    if any(marker in compact for marker in OFF_TASK_MARKERS) or re.fullmatch(r"([a-z0-9])\1{4,}", compact):
        return {
            "thought_quality": "off_task_or_play",
            "confidence": "low",
            "deterministic_report_allowed": False,
            "reason": "off_task_marker",
        }
    if len(compact) < 8 or any(marker in compact for marker in UNCLEAR_MARKERS):
        return {
            "thought_quality": "unclear_expression",
            "confidence": "low",
            "deterministic_report_allowed": False,
            "reason": "unclear_or_too_short",
        }
    return {
        "thought_quality": "reasonable_attempt",
        "confidence": "high" if len(compact) >= 18 else "medium",
        "deterministic_report_allowed": True,
        "reason": "usable_thought_text",
    }


def tag_prerequisites(db: Session | None, question_id: str) -> list[str]:
    if db is None:
        return []
    rows = db.execute(
        select(TaxonomyTag.kind, TaxonomyTag.label)
        .join(QuestionTag, QuestionTag.tag_id == TaxonomyTag.id)
        .where(QuestionTag.question_id == question_id)
        .limit(6)
    ).all()
    return [f"{kind}:{label}" for kind, label in rows]


def analyze_submission(submission: Submission, db: Session | None = None) -> dict[str, Any]:
    error_type = classify_error(submission.grading_result, submission.is_correct)
    thought = classify_thought(submission.thought_text)
    prerequisites = list(PREREQUISITE_BY_ERROR.get(error_type, PREREQUISITE_BY_ERROR["other_wrong"]))
    prerequisites.extend(tag_prerequisites(db, submission.question_id))
    confidence = thought["confidence"]
    deterministic_allowed = bool(thought["deterministic_report_allowed"] and error_type not in {"review_or_uncertain", "correct"})
    return {
        "schema_version": "learning_analysis_v1",
        "submission_id": submission.id,
        "question_id": submission.question_id,
        "is_correct": submission.is_correct,
        "error_type": error_type,
        "thought_quality": thought["thought_quality"],
        "thought_confidence": confidence,
        "thought_reason": thought["reason"],
        "prerequisites": prerequisites,
        "write_to_deterministic_report": deterministic_allowed,
        "deterministic_report_allowed": deterministic_allowed,
    }


def build_deep_diagnosis(submission: Submission, analysis: dict[str, Any], question: Question | None) -> dict[str, Any]:
    misconceptions = []
    if analysis["error_type"] == "calculation_or_numeric":
        misconceptions.append("calculation_check_missing")
    if analysis["thought_quality"] in {"missing_thought", "unclear_expression"}:
        misconceptions.append("reasoning_trace_incomplete")
    if analysis["error_type"] == "format_or_expression":
        misconceptions.append("answer_form_not_aligned")
    if not misconceptions and submission.is_correct is False:
        misconceptions.append("strategy_selection_uncertain")
    next_actions = []
    if "calculation_check_missing" in misconceptions:
        next_actions.append("ask_student_to_recompute_key_quantity_before_final_answer")
    if "reasoning_trace_incomplete" in misconceptions:
        next_actions.append("require_one_sentence_method_before_submitting")
    if "answer_form_not_aligned" in misconceptions:
        next_actions.append("compare_answer_type_with_question_target")
    return {
        "schema_version": "deep_learning_diagnosis_v1",
        "evidence": {
            "submission_id": submission.id,
            "question_id": submission.question_id,
            "question_preview": question.stem_preview if question else "",
            "answer_text": submission.answer_text,
            "is_correct": submission.is_correct,
            "grading_reason": (submission.grading_result or {}).get("reason"),
            "thought_text_present": bool(submission.thought_text),
            "elapsed_ms": submission.elapsed_ms,
            "deterministic_analysis": analysis,
        },
        "likely_misconceptions": misconceptions,
        "prerequisite_focus": analysis["prerequisites"][:8],
        "next_actions": next_actions or ["review_solution_and_retry_a_near_transfer_question"],
        "confidence": "medium" if analysis["deterministic_report_allowed"] else "low",
    }


def deep_diagnose_submission(
    submission: Submission,
    db: Session | None = None,
    chain: ProviderChain | None = None,
    roles: dict[str, str] | None = None,
) -> dict[str, Any]:
    analysis = analyze_submission(submission, db)
    question = db.get(Question, submission.question_id) if db is not None else None
    deterministic = build_deep_diagnosis(submission, analysis, question)
    messages = [
        {
            "role": "system",
            "content": guarded_system_prompt(
                "You are a learning diagnosis reviewer for parents and teachers. First privately compare the grading result, student answer, optional thought text, and deterministic diagnosis; do not reveal hidden chain-of-thought or scratchpad. Return only one JSON object with evidence citations, concise misconceptions, prerequisite focus, next_actions, and confidence. Do not invent facts."
            ),
        },
        {
            "role": "user",
            "content": guarded_json_message(
                "reporter",
                deterministic,
                untrusted_fields=[
                    "payload.evidence",
                    "payload.question",
                    "payload.submission",
                    "payload.thought_text",
                ],
            ),
        },
    ]
    chain = chain or ProviderChain([MockProvider()], max_retries=0, max_concurrency=1)
    roles = roles or {"reporter": "mock", "default": "mock"}
    results = chain.run_json(messages, "reporter", roles, {"mock_factory": lambda: deterministic})
    final = results[-1]
    payload = final.payload or deterministic
    return {
        "analysis": analysis,
        "diagnosis": payload,
        "provider": final.provider,
        "model": final.model,
        "latency_ms": final.latency_ms,
        "error": final.error,
        "token_usage": final.token_usage,
    }


def record_wrong_answer(db: Session, submission: Submission, analysis: dict[str, Any] | None = None) -> WrongQuestionRecord | None:
    if submission.is_correct is not False:
        return None
    existing = db.scalar(select(WrongQuestionRecord).where(WrongQuestionRecord.submission_id == submission.id))
    analysis = analysis or analyze_submission(submission, db)
    if existing:
        existing.error_type = analysis["error_type"]
        existing.analysis = analysis
        existing.updated_at = utc_now()
        return existing
    record = WrongQuestionRecord(
        session_id=submission.session_id,
        question_id=submission.question_id,
        submission_id=submission.id,
        error_type=analysis["error_type"],
        analysis=analysis,
    )
    db.add(record)
    db.flush()
    db.add(
        AnswerEvent(
            session_id=submission.session_id,
            question_id=submission.question_id,
            event_type="wrong_book_recorded",
            payload={"wrong_record_id": record.id, "submission_id": submission.id, "analysis": analysis},
        )
    )
    return record


def create_review_record(db: Session, wrong_record: WrongQuestionRecord, payload: dict[str, Any] | None = None) -> ReviewRecord:
    question = db.get(Question, wrong_record.question_id)
    review = ReviewRecord(
        question_id=wrong_record.question_id,
        session_id=wrong_record.session_id,
        source_record_id=wrong_record.id,
        payload={
            "wrong_record_id": wrong_record.id,
            "stem_preview": question.stem_preview if question else "",
            "analysis": wrong_record.analysis,
            **(payload or {}),
        },
    )
    db.add(review)
    db.flush()
    return review
