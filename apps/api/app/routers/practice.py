from ..localization import LanguageRequest
from dataclasses import replace

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import Any

from ..db import get_db, get_engine
from ..localization import current_language, translate_message
from ..answer_judge import should_run_ai_answer_judge, judge_answer_with_ai
from ..ai_gateway import hash_payload, process_generation_job
from ..audit import log_audit_event
from ..config import get_settings
from ..display_names import session_display_name
from ..grading import grade_answer
from ..interventions import advance_intervention, cancel_prefetched_interventions, intervention_cycle_for_error_count, intervention_evidence_hash, intervention_is_temporary_hint, mark_prefetched_interventions_stale, prefetch_intervention, trigger_intervention
from ..learning_analysis import analyze_submission, record_wrong_answer
from ..learner_profiles import resolve_learner_id
from ..models import AnswerEvent, AnswerSpec, GenerationJob, InterventionRecord, PracticeSession, Question, QuestionTag, QuestionVariant, QuestionVersion, Submission, TaxonomyTag, TestBlueprint, TestRun, utc_now
from ..practice_state import normalize_answer_text, session_interaction_state, submission_attempt_stats
from ..question_support import find_replacement_question_id, run_question_support
from ..question_faults import audit_question_fault
from ..schemas import (
    CreatePracticeSessionRequest,
    CreatePracticeSessionResponse,
    AnswerJudgeRequest,
    AnswerJudgeResponse,
    BreakConfirmRequest,
    PracticeEventRequest,
    PracticeFeedbackRequest,
    QuestionSupportRequest,
    QuestionSupportResponse,
    SolutionExplanationRequest,
    SolutionExplanationResponse,
    InterventionActionRequest,
    PracticeSessionStateResponse,
    PracticeTimingResponse,
    SubmitAnswerRequest,
    SubmitAnswerResponse,
)
from ..selection import SelectionPolicy, quota_for_policy, select_questions_for_session
from ..session_idempotency import find_idempotent_practice_session, ordered_unique_question_ids, practice_session_response
from ..solution_explainer import explain_solution_with_ai
from ..timing import reduce_timing_events

router = APIRouter(prefix="/api/practice/sessions", tags=["practice"])

QUESTION_STATE_CONFIG_KEY = "question_states"
MAX_IDEMPOTENCY_RECORDS_PER_QUESTION = 20
EVALUATION_SNAPSHOT_SCHEMA_VERSION = "evaluation_snapshot_v2"
GRADER_VERSION = "deterministic_grader_v1"
NORMALIZER_VERSION = "answer_normalizer_v1"


def normalize_star_quota(value: Any) -> dict[int, int]:
    if not isinstance(value, dict):
        return {}
    quota: dict[int, int] = {}
    for raw_star, raw_count in value.items():
        try:
            star = int(raw_star)
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        if 1 <= star <= 5 and count > 0:
            quota[star] = count
    return quota


def validate_star_quota_for_count(quota: dict[int, int], count: int) -> None:
    if quota and sum(quota.values()) != count:
        raise HTTPException(status_code=422, detail=f"star_quota total must equal count: {sum(quota.values())} != {count}")


class TestBlueprintRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    mode: str = "strict_test"
    template_name: str | None = "strict_100_high_intensity"
    count: int = Field(default=100, ge=1, le=100)
    source_names: list[str] = Field(default_factory=list)
    difficulty_stars: list[int] = Field(default_factory=list)
    star_quota: dict[int, int] | None = None
    tag_ids: list[str] = Field(default_factory=list)
    verified_only: bool = True
    include_generated: bool = False
    strict_source_mode: str = "bank_only"
    fill_missing_with_ai: bool = False
    learner_id: str | None = None
    exclude_done: bool | None = None
    exclude_recent_question_ids: list[str] = Field(default_factory=list)
    recent_exposure_count: int = Field(default=0, ge=0, le=500)
    exclude_family_ids: list[str] = Field(default_factory=list)


class HelpRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=200)
    at_ms: int = Field(default=0, ge=0)
    idempotency_key: str | None = Field(default=None, max_length=128)
    expected_state_version: int | None = Field(default=None, ge=0)


class TemporaryHintAnswerRequest(BaseModel):
    answer_text: str = Field(min_length=1, max_length=4000)
    thought_text: str | None = Field(default=None, max_length=4000)
    at_ms: int = Field(default=0, ge=0)
    idempotency_key: str | None = Field(default=None, max_length=128)


class MasteryCheckRequest(LanguageRequest):
    at_ms: int = Field(default=0, ge=0)
    extra_tag_ids: list[str] = Field(default_factory=list)
    count: int = Field(default=1, ge=1, le=5)
    candidate_count: int = Field(default=3, ge=1, le=5)
    run_now: bool = False
    use_mock: bool = False
    idempotency_key: str | None = Field(default=None, max_length=128)


def evaluation_snapshot(question: Question | None, version: QuestionVersion | None, answer: AnswerSpec) -> dict:
    qv2 = version.question_v2 if version else {}
    content = qv2.get("content") or {}
    raw_answer_spec = answer.raw_answer_spec or {}
    normalized_spec = answer.normalized_spec or {}
    verification = answer.verification or {}
    return {
        "schema_version": EVALUATION_SNAPSHOT_SCHEMA_VERSION,
        "grader_version": GRADER_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "question_id": question.id if question else answer.question_id,
        "question_status": question.status if question else None,
        "question_publish_status": question.publish_status if question else None,
        "question_version_id": version.id if version else None,
        "question_version": version.version if version else None,
        "question_content_hash": version.content_hash if version else None,
        "question_v2_hash": hash_payload(qv2),
        "raw_question_hash": hash_payload(version.raw_json if version else {}),
        "question_snapshot": {
            "source_name": question.source_name if question else None,
            "difficulty_star": question.difficulty_star if question else None,
            "family_id": question.family_id if question else None,
            "stem_hash": hash_payload(content.get("stem_md") or (question.stem_preview if question else "")),
            "stem_preview": question.stem_preview if question else None,
        },
        "answer_spec_id": answer.id,
        "answer_spec_status": answer.status,
        "answer_type": answer.answer_type,
        "answer_spec_hash": hash_payload(raw_answer_spec),
        "normalized_spec_hash": hash_payload(normalized_spec),
        "verification_hash": hash_payload(verification),
        "grading_snapshot": {
            "answer_type": answer.answer_type,
            "grading_method": str(raw_answer_spec.get("grading_method") or ""),
            "tolerance": raw_answer_spec.get("tolerance"),
            "unit": raw_answer_spec.get("unit"),
            "order_sensitive": bool(raw_answer_spec.get("order_sensitive", True)),
            "canonical_answer_hash": hash_payload(str(raw_answer_spec.get("canonical_answer") or "")),
            "acceptable_answer_count": len(raw_answer_spec.get("acceptable_answers") or []),
            "verification_level": verification.get("level"),
        },
    }


def blueprint_to_policy(blueprint: TestBlueprint) -> SelectionPolicy:
    config = blueprint.config or {}
    return SelectionPolicy(
        mode=blueprint.mode,
        count=blueprint.count,
        template_name=blueprint.template_name,
        source_names=tuple(config.get("source_names") or ()),
        difficulty_stars=tuple(config.get("difficulty_stars") or ()),
        star_quota=normalize_star_quota(config.get("star_quota")),
        tag_ids=tuple(config.get("tag_ids") or ()),
        verified_only=bool(config.get("verified_only", True)),
        include_generated=bool(config.get("include_generated", False)),
        strict_source_mode=str(config.get("strict_source_mode") or "bank_only"),
        learner_id=str(config.get("learner_id") or "default-learner"),
        exclude_done=bool(config.get("exclude_done", blueprint.mode == "strict_test")),
        exclude_recent_question_ids=tuple(config.get("exclude_recent_question_ids") or ()),
        recent_exposure_count=int(config.get("recent_exposure_count") or 0),
        exclude_family_ids=tuple(config.get("exclude_family_ids") or ()),
    )


def blueprint_payload(row: TestBlueprint) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "mode": row.mode,
        "template_name": row.template_name,
        "count": row.count,
        "quota": row.quota,
        "config": row.config,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def help_events(db: Session, session_id: str, question_id: str) -> list[AnswerEvent]:
    return list(
        db.scalars(
            select(AnswerEvent)
            .where(
                AnswerEvent.session_id == session_id,
                AnswerEvent.question_id == question_id,
                AnswerEvent.event_type.in_(("help_revealed", "solution_revealed", "assisted_completion")),
            )
            .order_by(AnswerEvent.created_at, AnswerEvent.id)
        )
    )


def effective_wrong_count_for_question(db: Session, session_id: str, question_id: str) -> int:
    stats = submission_attempt_stats(db, session_id, question_id, "__new_unique_probe__")
    return int(stats["effective_error_count_before"])


def allowed_help_level(db: Session, session: PracticeSession, question_id: str) -> int:
    if session.mode == "strict_test" or bool((session.config or {}).get("selection", {}).get("strict_no_ai", False)):
        return 0
    wrong_count = effective_wrong_count_for_question(db, session.id, question_id)
    if any((event.payload or {}).get("level") == 5 for event in help_events(db, session.id, question_id)):
        return 5
    if wrong_count <= 0:
        return 0
    if wrong_count == 1:
        return 1
    if wrong_count == 2:
        return 2
    return 3


def reached_help_level(db: Session, session_id: str, question_id: str) -> int:
    levels = [int((event.payload or {}).get("level") or 0) for event in help_events(db, session_id, question_id)]
    return max(levels, default=0)


def solution_revealed_for_question(db: Session, session_id: str, question_id: str) -> bool:
    return (
        db.scalar(
            select(AnswerEvent.id)
            .where(
                AnswerEvent.session_id == session_id,
                AnswerEvent.question_id == question_id,
                AnswerEvent.event_type == "solution_revealed",
            )
            .limit(1)
        )
        is not None
    )


def help_payload_for_level(db: Session, question_id: str, level: int) -> dict:
    question = db.get(Question, question_id)
    version = db.get(QuestionVersion, question.current_version_id) if question and question.current_version_id else None
    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == question_id))
    qv2 = version.question_v2 if version else {}
    solution = qv2.get("solution") or {}
    student_steps = solution.get("student_steps") or [row.get("explanation", "") for row in solution.get("steps", [])]
    final_answer = solution.get("final_answer") or (answer.canonical_answer if answer else None)
    levels = {
        0: {"title": translate_message("help.level.self_check"), "content": [translate_message(key) for key in ("help.check_all_conditions", "help.check_units", "help.check_whole_or_part")]},
        1: {"title": translate_message("help.level.micro_hint"), "content": [solution.get("key_insight") or translate_message("help.fallback.invariant")]},
        2: {"title": translate_message("help.level.direction"), "content": [solution.get("primary_method") or solution.get("solution_hook") or translate_message("help.fallback.arithmetic")]},
        3: {"title": translate_message("help.level.intermediate"), "content": student_steps[:1] or [translate_message("help.fallback.intermediate")]},
        4: {"title": translate_message("help.level.steps"), "content": student_steps or [translate_message("help.fallback.no_steps")]},
        5: {
            "title": translate_message("help.level.full"),
            "content": student_steps or [translate_message("help.fallback.no_steps")],
            "final_answer": final_answer,
            "pitfalls": solution.get("pitfalls") or [],
            "alternative_solution": solution.get("alternative_solution"),
        },
    }
    payload = levels[max(0, min(5, level))]
    return {"level": level, "question_id": question_id, **payload}


def strict_ai_disabled(session: PracticeSession) -> bool:
    return session.mode == "strict_test" or bool((session.config or {}).get("selection", {}).get("strict_no_ai", False))


def session_config_copy(session: PracticeSession) -> dict[str, Any]:
    return dict(session.config or {})


def question_state(session: PracticeSession, question_id: str) -> dict[str, Any]:
    config = session.config or {}
    states = config.get(QUESTION_STATE_CONFIG_KEY) or {}
    raw = states.get(question_id) or {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "state_version": int(raw.get("state_version") or 0),
        "answer_locked_at": raw.get("answer_locked_at"),
        "answer_locked_reason": raw.get("answer_locked_reason"),
        "operations": raw.get("operations") if isinstance(raw.get("operations"), list) else [],
    }


def set_question_state(session: PracticeSession, question_id: str, state: dict[str, Any]) -> None:
    config = session_config_copy(session)
    states = dict(config.get(QUESTION_STATE_CONFIG_KEY) or {})
    states[question_id] = state
    config[QUESTION_STATE_CONFIG_KEY] = states
    session.config = config
    session.updated_at = utc_now()


def find_idempotent_response(session: PracticeSession, question_id: str, operation: str, idempotency_key: str | None) -> dict[str, Any] | None:
    if not idempotency_key:
        return None
    for row in question_state(session, question_id).get("operations", []):
        if row.get("operation") == operation and row.get("idempotency_key") == idempotency_key:
            return row.get("response")
    return None


def current_state_version(session: PracticeSession, question_id: str) -> int:
    return int(question_state(session, question_id).get("state_version") or 0)


def ensure_expected_state_version(session: PracticeSession, question_id: str, expected_state_version: int | None) -> None:
    if expected_state_version is None:
        return
    actual = current_state_version(session, question_id)
    if expected_state_version != actual:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "state_version_conflict",
                "question_id": question_id,
                "expected_state_version": expected_state_version,
                "actual_state_version": actual,
            },
        )


def record_question_operation(
    session: PracticeSession,
    question_id: str,
    *,
    operation: str,
    idempotency_key: str | None,
    response: dict[str, Any],
    lock_answer: bool = False,
    lock_reason: str | None = None,
) -> int:
    state = question_state(session, question_id)
    state["state_version"] = int(state.get("state_version") or 0) + 1
    if lock_answer:
        state["answer_locked_at"] = utc_now().isoformat()
        state["answer_locked_reason"] = lock_reason or operation
    if idempotency_key:
        operations = [
            row
            for row in state.get("operations", [])
            if not (row.get("operation") == operation and row.get("idempotency_key") == idempotency_key)
        ]
        operations.append(
            {
                "operation": operation,
                "idempotency_key": idempotency_key,
                "state_version": state["state_version"],
                "response": response,
                "created_at": utc_now().isoformat(),
            }
        )
        state["operations"] = operations[-MAX_IDEMPOTENCY_RECORDS_PER_QUESTION:]
    set_question_state(session, question_id, state)
    return int(state["state_version"])


def question_answer_locked(session: PracticeSession, question_id: str) -> bool:
    return bool(question_state(session, question_id).get("answer_locked_at"))


def question_tag_rows(db: Session, question_id: str) -> list[dict]:
    rows = db.execute(
        select(QuestionTag.tag_id, QuestionTag.role, TaxonomyTag.kind, TaxonomyTag.label)
        .join(TaxonomyTag, TaxonomyTag.id == QuestionTag.tag_id)
        .where(QuestionTag.question_id == question_id)
        .order_by(QuestionTag.role, TaxonomyTag.kind, TaxonomyTag.label)
    ).all()
    return [
        {"tag_id": tag_id, "role": role, "kind": kind, "label": label}
        for tag_id, role, kind, label in rows
    ]


def mastery_submission_evidence(db: Session, session_id: str, question_id: str) -> dict:
    submissions = list(
        db.scalars(
            select(Submission)
            .where(Submission.session_id == session_id, Submission.question_id == question_id)
            .order_by(Submission.created_at, Submission.id)
        )
    )
    events = list(
        db.scalars(
            select(AnswerEvent)
            .where(
                AnswerEvent.session_id == session_id,
                AnswerEvent.question_id == question_id,
                AnswerEvent.event_type.in_(
                    (
                        "submitted",
                        "help_revealed",
                        "solution_revealed",
                        "assisted_completion",
                        "answer_reveal_debt_created",
                        "intervention_prefetched",
                        "intervention_due",
                    )
                ),
            )
            .order_by(AnswerEvent.created_at, AnswerEvent.id)
        )
    )
    effective_wrong_count = 0
    for submission in submissions:
        grading = submission.grading_result or {}
        if submission.is_correct is False and not bool(submission.excluded_from_mastery) and grading.get("duplicate_answer") is not True:
            effective_wrong_count += 1
    return {
        "schema_version": "mastery_check_evidence_v1",
        "session_id": session_id,
        "question_id": question_id,
        "attempt_count": len(submissions),
        "effective_wrong_count": effective_wrong_count,
        "solution_revealed": any(row.event_type == "solution_revealed" for row in events),
        "submissions": [
            {
                "submission_id": row.id,
                "answer_text": row.answer_text,
                "thought_text": row.thought_text,
                "thought_text_present": bool(row.thought_text),
                "is_correct": row.is_correct,
                "effective_wrong": row.effective_wrong,
                "excluded_from_mastery": row.excluded_from_mastery,
                "help_level_reached": row.help_level_reached,
                "elapsed_ms": row.elapsed_ms,
                "created_at": row.created_at.isoformat(),
                "reason": (row.grading_result or {}).get("reason"),
                "duplicate_answer": (row.grading_result or {}).get("duplicate_answer"),
            }
            for row in submissions
        ],
        "events": [
            {
                "event_type": row.event_type,
                "created_at": row.created_at.isoformat(),
                "payload": row.payload,
            }
            for row in events
        ],
    }


def build_mastery_generation_request(
    db: Session,
    session: PracticeSession,
    question: Question,
    version: QuestionVersion | None,
    payload: MasteryCheckRequest,
    *,
    sibling_index: int = 0,
    sibling_count: int = 1,
) -> dict:
    qv2 = version.question_v2 if version else {}
    stem_md = ((qv2.get("content") or {}).get("stem_md") or question.stem_preview or "").strip()
    base_tags = question_tag_rows(db, question.id)
    target_tags: list[str] = []
    for tag in base_tags:
        tag_id = str(tag["tag_id"])
        if tag_id not in target_tags:
            target_tags.append(tag_id)
    for tag_id in payload.extra_tag_ids:
        if tag_id not in target_tags:
            target_tags.append(tag_id)
    evidence = mastery_submission_evidence(db, session.id, question.id)
    star = int(question.difficulty_star or 3)
    variation_axes = [
        "change_story_domain",
        "change_representation",
        "change_question_form",
        "change_secondary_constraint",
        "change_intermediate_structure",
    ]
    required_axis = variation_axes[sibling_index % len(variation_axes)]
    constraints = "\n".join(
        [
            "生成一道普通练习掌握检测题，用来判断学生在多次尝试、看提示或完成启发题后是否真正理解。",
            f"必须以原题 {star} 星作为难度下限；可以同星级或更难，但不得低于原题星级。",
            "必须保持同一核心数学关系、核心方法或核心能力点，但不要只改数字、只替换人名、只改变单位。",
            "表面情境、问法、表示方式或附加标签可以变化，鼓励结合已选标签形成迁移检测。",
            "题目必须适合小学高年级，答案必须唯一且能自动判分，并给出可验证 answer_spec。",
            "不要泄露原题答案，也不要把学生已经写过的错误答案改造成标准答案。",
            "如果无法做出足够不同但核心相同的题，请返回 reject。",
        ]
    )
    constraints = "\n".join(
        [
            constraints,
            f"Mastery-check hard contract: final published difficulty_star must be >= base star {star}. Do not create an easier check question.",
            f"This is sibling {sibling_index + 1}/{sibling_count}; prioritize difference axis `{required_axis}`.",
            "If multiple mastery checks are requested, each accepted question must differ on at least two non-numeric design axes from the base question and from sibling requests.",
            "Use the student's answer_text and thought_text only as evidence of what to test; do not reveal or quote private diagnosis to the learner.",
        ]
    )
    return {
        "target_tags": target_tags[:8],
        "language": current_language(),
        "difficulty_star": star,
        "count": 1,
        "constraints": constraints,
        "source_session_id": session.id,
        "solution_style": "elementary_preferred",
        "origin": "mastery_check",
        "candidate_count": payload.candidate_count,
        "batch_item_index": sibling_index,
        "mastery_check_context": {
            "base_question_id": question.id,
            "base_question_stem_md": stem_md[:1200],
            "base_question_source": question.source_name,
            "base_difficulty_star": star,
            "minimum_difficulty_star": star,
            "base_tags": base_tags,
            "extra_tag_ids": payload.extra_tag_ids,
            "sibling_index": sibling_index,
            "sibling_count": sibling_count,
            "required_difference_axis": required_axis,
            "answer_history": evidence,
            "required_output_behavior": {
                "same_star": False,
                "minimum_same_star": True,
                "allow_harder_than_base": True,
                "same_core_content": True,
                "surface_changed": True,
                "not_number_only_variant": True,
                "defer_start_until_user_clicks": True,
            },
        },
    }


def active_mastery_check_record(db: Session, session_id: str, question_id: str) -> InterventionRecord | None:
    return db.scalar(
        select(InterventionRecord).where(
            InterventionRecord.session_id == session_id,
            InterventionRecord.original_question_id == question_id,
            InterventionRecord.intervention_type == "mastery_check",
            InterventionRecord.status == "active",
        )
    )


def mastery_check_evidence_key(session_id: str, question_id: str, idempotency_key: str, sibling_index: int, sibling_count: int) -> str:
    return hash_payload(
        {
            "purpose": "mastery_check_request",
            "session_id": session_id,
            "question_id": question_id,
            "idempotency_key": idempotency_key,
            "sibling_index": sibling_index,
            "sibling_count": sibling_count,
        }
    )


def lock_mastery_question_to_base_star(db: Session, base_question: Question, check_question: Question) -> None:
    base_star = int(base_question.difficulty_star or 3)
    generated_star = int(check_question.difficulty_star or base_star)
    star = max(base_star, generated_star)
    family_id = base_question.family_id or f"family:{base_question.id}"
    check_question.difficulty_star = star
    check_question.source_difficulty_star = star
    check_question.calibrated_difficulty_star = star
    check_question.family_id = family_id
    check_question.eligible_for_test = False
    check_question.publish_status = "active_practice"
    version = db.get(QuestionVersion, check_question.current_version_id) if check_question.current_version_id else None
    if version is not None:
        raw_json = dict(version.raw_json or {})
        raw_eval = dict(raw_json.get("difficulty_quality_evaluation") or {})
        raw_eval["difficulty_star"] = star
        raw_eval["mastery_check_minimum_star_locked"] = base_star
        raw_json["difficulty_quality_evaluation"] = raw_eval
        qv2 = dict(version.question_v2 or {})
        evaluation = dict(qv2.get("evaluation") or {})
        difficulty = dict(evaluation.get("difficulty") or {})
        difficulty["star"] = star
        difficulty["mastery_check_minimum_star_locked"] = base_star
        evaluation["difficulty"] = difficulty
        qv2["evaluation"] = evaluation
        provenance = dict(qv2.get("provenance") or {})
        provenance["mastery_check_base_question_id"] = base_question.id
        provenance["mastery_check_minimum_star_locked"] = base_star
        qv2["provenance"] = provenance
        version.raw_json = raw_json
        version.question_v2 = qv2
    existing_link = db.scalar(
        select(QuestionVariant).where(
            QuestionVariant.base_question_id == base_question.id,
            QuestionVariant.variant_question_id == check_question.id,
            QuestionVariant.variant_level == "mastery_check",
        )
    )
    if existing_link is None:
        db.add(
            QuestionVariant(
                base_question_id=base_question.id,
                variant_question_id=check_question.id,
                variant_level="mastery_check",
                purpose="mastery_check",
                family_id=family_id,
                transformation={
                    "schema_version": "mastery_check_variant_v1",
                    "minimum_star_locked": base_star,
                    "published_star": star,
                    "same_core_content_required": True,
                    "not_number_only_variant_required": True,
                },
            )
        )


def finish_mastery_check_record(db: Session, record_id: str, provider: str = "openai_compatible") -> InterventionRecord:
    record = db.get(InterventionRecord, record_id)
    if record is None:
        raise ValueError(f"mastery check record not found: {record_id}")
    payload = dict(record.payload or {})
    job_id = payload.get("generation_job_id")
    if not job_id:
        record.status = "failed"
        record.prefetch_status = "failed"
        record.payload = {**payload, "state": "failed", "terminal_reason": "missing_generation_job_id"}
        record.updated_at = utc_now()
        db.commit()
        return record
    job = db.get(GenerationJob, job_id)
    if job is None:
        record.status = "failed"
        record.prefetch_status = "failed"
        record.payload = {**payload, "state": "failed", "terminal_reason": "generation_job_not_found"}
        record.updated_at = utc_now()
        db.commit()
        return record
    if job.status in {"queued", "running"}:
        process_generation_job(db, job.id, provider=provider)
        record = db.get(InterventionRecord, record_id)
        job = db.get(GenerationJob, job_id)
        payload = dict(record.payload or {}) if record is not None else payload
    if record is None or job is None:
        raise ValueError(f"mastery check record disappeared: {record_id}")
    session = db.get(PracticeSession, record.session_id)
    base_question = db.get(Question, record.original_question_id)
    accepted_question = db.get(Question, job.accepted_question_id) if job.accepted_question_id else None
    if job.status == "accepted" and accepted_question is not None and session is not None and base_question is not None:
        lock_mastery_question_to_base_star(db, base_question, accepted_question)
        question_ids = list(session.question_ids or [])
        if accepted_question.id not in question_ids:
            question_ids.append(accepted_question.id)
            session.question_ids = question_ids
            session.updated_at = utc_now()
        record.current_question_id = accepted_question.id
        record.prefetch_status = "ready"
        record.payload = {
            **payload,
            "state": "ready_to_start",
            "check_question_id": accepted_question.id,
            "generation_job_status": job.status,
            "ready_at": utc_now().isoformat(),
        }
        record.updated_at = utc_now()
        db.add(
            AnswerEvent(
                session_id=record.session_id,
                question_id=record.original_question_id,
                event_type="mastery_check_ready",
                payload={"record_id": record.id, "generation_job_id": job.id, "check_question_id": accepted_question.id},
            )
        )
    else:
        terminal_reason = (job.progress or {}).get("terminal_reason") or job.status
        record.status = "failed"
        record.prefetch_status = "failed"
        record.payload = {
            **payload,
            "state": "failed",
            "generation_job_status": job.status,
            "terminal_reason": terminal_reason,
        }
        record.updated_at = utc_now()
        db.add(
            AnswerEvent(
                session_id=record.session_id,
                question_id=record.original_question_id,
                event_type="mastery_check_failed",
                payload={"record_id": record.id, "generation_job_id": job.id, "terminal_reason": terminal_reason},
            )
        )
    db.commit()
    return record


def process_mastery_check_background(record_id: str, provider: str = "openai_compatible") -> None:
    with Session(get_engine()) as db:
        try:
            finish_mastery_check_record(db, record_id, provider=provider)
        except Exception as exc:
            db.rollback()
            mark_mastery_check_record_failed(db, record_id, f"provider_or_generation_exception: {exc}", provider=provider)


def mark_mastery_check_record_failed(db: Session, record_id: str, reason: str, provider: str = "openai_compatible") -> None:
    record = db.get(InterventionRecord, record_id)
    if record is None:
        return
    payload = dict(record.payload or {})
    record.status = "failed"
    record.prefetch_status = "failed"
    record.payload = {
        **payload,
        "state": "failed",
        "terminal_reason": reason[:500],
        "provider": provider,
        "failed_at": utc_now().isoformat(),
    }
    record.updated_at = utc_now()
    db.add(
        AnswerEvent(
            session_id=record.session_id,
            question_id=record.original_question_id,
            event_type="mastery_check_failed",
            payload={
                "record_id": record.id,
                "generation_job_id": payload.get("generation_job_id"),
                "terminal_reason": reason[:500],
                "provider": provider,
            },
        )
    )
    db.commit()


def question_star_counts(db: Session, question_ids: list[str]) -> dict[int, int]:
    if not question_ids:
        return {}
    counts: dict[int, int] = {}
    rows = db.execute(select(Question.id, Question.difficulty_star).where(Question.id.in_(question_ids))).all()
    by_id = {question_id: star for question_id, star in rows}
    for question_id in question_ids:
        try:
            star = int(by_id.get(question_id) or 0)
        except (TypeError, ValueError):
            star = 0
        counts[star] = counts.get(star, 0) + 1
    return counts


def strict_ai_fill_missing_questions(
    db: Session,
    payload: CreatePracticeSessionRequest,
    learner_id: str,
    question_ids: list[str],
    selection_meta: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    if payload.mode != "strict_test" or not payload.fill_missing_with_ai or payload.strict_source_mode == "bank_only":
        return question_ids, {"enabled": False, "added_question_ids": [], "errors": []}
    target_quota = normalize_star_quota(selection_meta.get("target_quota"))
    actual_quota = question_star_counts(db, question_ids)
    missing: dict[int, int] = {}
    if target_quota:
        for star, needed in target_quota.items():
            missing[star] = max(0, needed - actual_quota.get(star, 0))
    elif len(question_ids) < payload.count:
        fallback_star = int((payload.difficulty_stars or [3])[0] or 3)
        missing[fallback_star] = payload.count - len(question_ids)
    missing = {star: count for star, count in missing.items() if count > 0}
    if not missing:
        return question_ids, {"enabled": True, "added_question_ids": [], "errors": [], "missing_before": {}}

    settings = get_settings()
    provider = "mock" if str(getattr(settings, "model_provider", "") or "").strip().lower() == "mock" else "openai_compatible"
    added: list[str] = []
    errors: list[dict[str, Any]] = []
    base_key = payload.idempotency_key or hash_payload({"learner_id": learner_id, "mode": payload.mode, "count": payload.count, "star_quota": target_quota})
    target_tags = list(payload.tag_ids or [])
    for star, needed in sorted(missing.items()):
        for index in range(needed):
            if len(question_ids) >= payload.count:
                break
            job_key = f"strict-fill:{base_key}:{star}:{index}"
            job = db.scalar(select(GenerationJob).where(GenerationJob.idempotency_key == job_key))
            if job is None:
                request = {
                    "target_tags": target_tags,
                    "language": current_language(),
                    "difficulty_star": star,
                    "count": 1,
                    "constraints": (
                        "严格测试补题。必须生成适合小学高年级、可自动判分、有完整 answer_spec 和完整解答的题。"
                        "不得生成开放讨论题；不得泄露为补题或测试内部流程。"
                    ),
                    "source_session_id": None,
                    "solution_style": "elementary_required",
                    "origin": "strict_test_ai_fill",
                    "strict_test_context": {
                        "learner_id": learner_id,
                        "strict_source_mode": payload.strict_source_mode,
                        "target_star": star,
                        "target_quota": target_quota,
                        "already_selected_count": len(question_ids),
                    },
                    "prefer_reuse_existing_ai": False,
                    "candidate_count": payload.ai_candidate_count,
                    "source_example_count": payload.ai_source_example_count,
                    "ai_negative_example_count": payload.ai_negative_example_count,
                    "allow_reviewer_quality_downgrade": payload.allow_reviewer_quality_downgrade,
                }
                job = GenerationJob(idempotency_key=job_key, status="queued", request=request, progress={"origin": "strict_test_ai_fill", "target_star": star})
                db.add(job)
                db.flush()
            try:
                if job.status not in {"accepted", "rejected", "failed", "cancelled", "timed_out"}:
                    process_generation_job(db, job.id, provider=provider)
                db.refresh(job)
                if job.accepted_question_id and job.accepted_question_id not in question_ids:
                    accepted_question = db.get(Question, job.accepted_question_id)
                    if accepted_question is not None:
                        accepted_question.difficulty_star = star
                        accepted_question.source_difficulty_star = star
                        accepted_question.calibrated_difficulty_star = star
                        accepted_question.eligible_for_test = True
                        accepted_question.publish_status = "active_test"
                    question_ids.append(job.accepted_question_id)
                    added.append(job.accepted_question_id)
                else:
                    errors.append({"star": star, "job_id": job.id, "status": job.status, "reason": (job.progress or {}).get("terminal_reason")})
            except Exception as exc:
                db.rollback()
                errors.append({"star": star, "job_id": job.id if job else None, "error": str(exc)})
    return question_ids, {
        "enabled": True,
        "provider": provider,
        "missing_before": missing,
        "added_question_ids": added,
        "errors": errors,
    }


@router.post("", response_model=CreatePracticeSessionResponse)
def create_session(payload: CreatePracticeSessionRequest, db: Session = Depends(get_db)) -> CreatePracticeSessionResponse:
    learner_id = resolve_learner_id(db, payload.learner_id)
    existing = find_idempotent_practice_session(db, learner_id=learner_id, idempotency_key=payload.idempotency_key, mode=payload.mode)
    if existing is not None:
        return practice_session_response(existing)
    star_quota = normalize_star_quota(payload.star_quota)
    validate_star_quota_for_count(star_quota, payload.count)
    policy = SelectionPolicy(
        mode=payload.mode,
        count=payload.count,
        template_name=payload.template_name,
        source_names=tuple(payload.source_names or ()),
        difficulty_stars=tuple(payload.difficulty_stars or ()),
        star_quota=star_quota or None,
        tag_ids=tuple(payload.tag_ids or ()),
        verified_only=payload.verified_only,
        include_generated=payload.include_generated,
        strict_source_mode=payload.strict_source_mode,
        learner_id=learner_id,
        exclude_done=bool(payload.exclude_done if payload.exclude_done is not None else payload.mode == "strict_test"),
        exclude_recent_question_ids=tuple(payload.exclude_recent_question_ids),
        recent_exposure_count=payload.recent_exposure_count,
        exclude_family_ids=tuple(payload.exclude_family_ids),
    )
    question_ids, selection_meta = select_questions_for_session(db, policy)
    question_ids, ai_fill = strict_ai_fill_missing_questions(db, payload, learner_id, question_ids, selection_meta)
    question_ids = ordered_unique_question_ids(question_ids)
    if ai_fill.get("enabled"):
        selection_meta = {
            **selection_meta,
            "ai_fill": ai_fill,
            "actual_quota": question_star_counts(db, question_ids),
            "selection_warnings": [
                *(selection_meta.get("selection_warnings") or []),
                *(["ai_fill_incomplete"] if ai_fill.get("errors") and len(question_ids) < payload.count else []),
            ],
        }
    config = {**payload.model_dump(), "learner_id": learner_id, "selection": selection_meta, "break_confirmed": []}
    session = PracticeSession(mode=payload.mode, learner_id=learner_id, config=config, question_ids=question_ids)
    db.add(session)
    db.flush()
    db.add(AnswerEvent(session_id=session.id, question_id="", event_type="session_created", payload={"question_ids": question_ids, "idempotency_key": payload.idempotency_key, "at_ms": 0}))
    log_audit_event(db, "practice.session_created", "practice_session", session.id, {"mode": session.mode, "learner_id": learner_id, "question_count": len(question_ids)})
    db.commit()
    return practice_session_response(session)


@router.post("/test-blueprints")
def save_test_blueprint(payload: TestBlueprintRequest, db: Session = Depends(get_db)) -> dict:
    learner_id = resolve_learner_id(db, payload.learner_id)
    validate_star_quota_for_count(normalize_star_quota(payload.star_quota), payload.count)
    config = {**payload.model_dump(), "learner_id": learner_id}
    row = db.scalar(select(TestBlueprint).where(TestBlueprint.name == payload.name))
    if row is None:
        row = TestBlueprint(
            name=payload.name,
            mode=payload.mode,
            template_name=payload.template_name,
            count=payload.count,
            quota={},
            config=config,
        )
        db.add(row)
    else:
        row.mode = payload.mode
        row.template_name = payload.template_name
        row.count = payload.count
        row.config = config
        row.updated_at = utc_now()
    row.quota = (quota_for_policy(blueprint_to_policy(row)) or {})
    db.commit()
    return blueprint_payload(row)


@router.get("/test-blueprints")
def list_test_blueprints(db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(TestBlueprint).order_by(TestBlueprint.created_at.desc(), TestBlueprint.id)))
    return {"items": [blueprint_payload(row) for row in rows]}


@router.post("/test-blueprints/{blueprint_id}/runs")
def start_test_run(blueprint_id: str, db: Session = Depends(get_db)) -> dict:
    blueprint = db.get(TestBlueprint, blueprint_id)
    if blueprint is None:
        raise HTTPException(status_code=404, detail="test blueprint not found")
    learner_id = resolve_learner_id(db, (blueprint.config or {}).get("learner_id"))
    policy = replace(blueprint_to_policy(blueprint), learner_id=learner_id)
    question_ids, selection_meta = select_questions_for_session(db, policy)
    question_ids = ordered_unique_question_ids(question_ids)
    config = {**(blueprint.config or {}), "learner_id": learner_id, "selection": selection_meta, "break_confirmed": [], "blueprint_id": blueprint.id}
    session = PracticeSession(mode=blueprint.mode, learner_id=learner_id, config=config, question_ids=question_ids)
    db.add(session)
    db.flush()
    run = TestRun(
        blueprint_id=blueprint.id,
        session_id=session.id,
        progress={"answered_count": 0, "total": len(question_ids), "selection": selection_meta, "learner_id": learner_id},
    )
    db.add(run)
    db.flush()
    db.add(AnswerEvent(session_id=session.id, question_id="", event_type="test_run_started", payload={"test_run_id": run.id, "question_ids": question_ids, "at_ms": 0}))
    db.commit()
    return {"id": run.id, "blueprint_id": blueprint.id, "session_id": session.id, "learner_id": learner_id, "status": run.status, "question_ids": question_ids, "progress": run.progress}


@router.get("/test-runs/{run_id}/resume")
def resume_test_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.get(TestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="test run not found")
    state = get_session_state(run.session_id, db)
    run.progress = {
        **(run.progress or {}),
        "answered_count": state.answered_count,
        "total": len(state.question_ids),
        "current_index": state.current_index,
        "next_breakpoint": state.next_breakpoint,
        "break_confirmed": state.break_confirmed,
    }
    run.status = state.status
    run.updated_at = utc_now()
    db.commit()
    return {"id": run.id, "blueprint_id": run.blueprint_id, "session_id": run.session_id, "status": run.status, "progress": run.progress, "session": state.model_dump()}


@router.post("/{session_id}/submit", response_model=SubmitAnswerResponse)
def submit_answer(session_id: str, payload: SubmitAnswerRequest, db: Session = Depends(get_db)) -> SubmitAnswerResponse:
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if payload.question_id not in session.question_ids:
        raise HTTPException(status_code=400, detail="question does not belong to session")
    existing_response = find_idempotent_response(session, payload.question_id, "submit", payload.idempotency_key)
    if existing_response:
        return SubmitAnswerResponse(**existing_response)
    ensure_expected_state_version(session, payload.question_id, payload.expected_state_version)
    if question_answer_locked(session, payload.question_id) or solution_revealed_for_question(db, session_id, payload.question_id):
        db.add(
            AnswerEvent(
                session_id=session_id,
                question_id=payload.question_id,
                event_type="answer_locked_submit_blocked",
                payload={"reason": "solution_revealed", "at_ms": payload.elapsed_ms},
            )
        )
        db.commit()
        raise HTTPException(status_code=409, detail="answer_locked_after_reveal")
    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == payload.question_id))
    if answer is None:
        raise HTTPException(status_code=404, detail="answer spec not found")
    version = db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == payload.question_id).order_by(QuestionVersion.version.desc()))
    question = db.get(Question, payload.question_id)
    snapshot = evaluation_snapshot(question, version, answer)
    result = grade_answer(answer.raw_answer_spec, payload.answer_text)
    strict_judge_mode = session.mode == "strict_test" or bool((session.config or {}).get("selection", {}).get("strict_no_ai", False))
    auto_answer_judge = None
    auto_answer_judge_triggered = should_run_ai_answer_judge(answer.raw_answer_spec, result)
    if auto_answer_judge_triggered and question is not None:
        auto_answer_judge = judge_answer_with_ai(db, question, version, answer, payload.answer_text, payload.thought_text, strict_mode=strict_judge_mode, deterministic_result=result)
        confidence = float(auto_answer_judge.get("confidence") or 0)
        judge_is_correct = auto_answer_judge.get("is_correct")
        should_override = bool(auto_answer_judge.get("should_override_deterministic")) or (result.get("is_correct") is not True and judge_is_correct is True)
        if confidence >= 0.75 and should_override:
            result = {
                **result,
                "is_correct": judge_is_correct,
                "reason": "ai_answer_judge_override",
                "deterministic_result_before_ai_judge": auto_answer_judge.get("deterministic_result"),
            }
        result = {
            **result,
            "auto_answer_judge_triggered": True,
            "auto_answer_judge": auto_answer_judge,
        }
    else:
        result = {
            **result,
            "auto_answer_judge_triggered": False,
            "auto_answer_judge_skipped_reason": "deterministic_correct" if result.get("is_correct") is True else "single_numeric_or_not_required",
        }
    result = {**result, "evaluation_snapshot": snapshot}
    attempt_stats = submission_attempt_stats(db, session_id, payload.question_id, payload.answer_text)
    attempt_index = attempt_stats["raw_attempt_index"]
    duplicate_answer = attempt_stats["duplicate_of_submission_id"] is not None
    effective_error_count = attempt_stats["effective_error_count_before"]
    normalized_submitted_answer = normalize_answer_text(payload.answer_text)
    is_effective_wrong = result.get("is_correct") is False and not duplicate_answer and bool(normalized_submitted_answer)
    current_help_level = reached_help_level(db, session_id, payload.question_id)
    solution_already_revealed = current_help_level >= 5
    if is_effective_wrong:
        effective_error_count += 1
    _, intervention_action = intervention_cycle_for_error_count(effective_error_count)
    result = {
        **result,
        "duplicate_answer": duplicate_answer,
        "effective_attempt_index": attempt_stats["effective_attempt_index"],
        "effective_error_count": effective_error_count,
        "intervention_due": intervention_action in {"show", "guided_solution"},
        "intervention_prefetch_due": intervention_action == "prefetch",
        "effective_wrong": is_effective_wrong,
        "excluded_from_mastery": solution_already_revealed,
        "solution_revealed": solution_already_revealed,
        "help_level_reached": current_help_level,
    }
    submission = Submission(
        session_id=session_id,
        question_id=payload.question_id,
        question_version_id=version.id if version else None,
        answer_text=payload.answer_text,
        thought_text=payload.thought_text,
        is_correct=result.get("is_correct"),
        effective_wrong=is_effective_wrong,
        excluded_from_mastery=solution_already_revealed,
        solution_revealed=solution_already_revealed,
        help_level_reached=current_help_level,
        grading_result=result,
        elapsed_ms=payload.elapsed_ms,
    )
    db.add(submission)
    db.flush()
    fault_audit = None
    if is_effective_wrong and effective_error_count >= 2 and question and question.source_name == "AI生成题":
        fault_audit = audit_question_fault(db, payload.question_id, session_id)
        if not fault_audit["should_count_as_wrong"]:
            submission.effective_wrong = False
            submission.excluded_from_mastery = True
            effective_error_count = max(0, effective_error_count - 1)
            cancel_prefetched_interventions(db, session_id, payload.question_id, "question_fault_audit")
        _, intervention_action = intervention_cycle_for_error_count(effective_error_count)
        result = {
            **result,
            "question_fault_audit": fault_audit,
            "effective_error_count": effective_error_count,
            "intervention_due": intervention_action in {"show", "guided_solution"},
            "intervention_prefetch_due": intervention_action == "prefetch",
            "effective_wrong": submission.effective_wrong,
            "excluded_from_mastery": submission.excluded_from_mastery,
        }
        submission.grading_result = result
    if result.get("is_correct") is True:
        cancelled = cancel_prefetched_interventions(db, session_id, payload.question_id, "answered_correctly_before_hint")
        for row in cancelled:
            db.add(
                AnswerEvent(
                    session_id=session_id,
                    question_id=payload.question_id,
                    event_type="intervention_prefetch_cancelled",
                    payload={"record_id": row.id, "reason": "answered_correctly_before_hint", "at_ms": payload.elapsed_ms},
                )
            )
    learning_analysis = None
    wrong_record = None
    if result.get("is_correct") is False and not submission.excluded_from_mastery:
        learning_analysis = analyze_submission(submission, db)
        wrong_record = record_wrong_answer(db, submission, learning_analysis)
        result = {
            **result,
            "learning_analysis": learning_analysis,
            "wrong_record_id": wrong_record.id if wrong_record else None,
        }
        submission.grading_result = result
    db.add(
        AnswerEvent(
            session_id=session_id,
            question_id=payload.question_id,
            event_type="submitted",
            payload={
                "submission_id": submission.id,
                "question_version_id": submission.question_version_id,
                "evaluation_snapshot": snapshot,
                "result": result,
                "at_ms": payload.elapsed_ms,
                "attempt_index": attempt_index,
                "effective_attempt_index": attempt_stats["effective_attempt_index"],
                "duplicate_answer": duplicate_answer,
                "effective_error_count": effective_error_count,
                "effective_wrong": submission.effective_wrong,
                "excluded_from_mastery": submission.excluded_from_mastery,
                "solution_revealed": submission.solution_revealed,
                "help_level_reached": submission.help_level_reached,
                "question_fault_audit": fault_audit,
                "learning_analysis": learning_analysis,
                "wrong_record_id": wrong_record.id if wrong_record else None,
            },
        )
    )
    if auto_answer_judge:
        db.add(
            AnswerEvent(
                session_id=session_id,
                question_id=payload.question_id,
                event_type="answer_judged",
                payload={
                    "submission_id": submission.id,
                    "auto": True,
                    "strict_mode": strict_judge_mode,
                    "verdict": auto_answer_judge.get("verdict"),
                    "is_correct": auto_answer_judge.get("is_correct"),
                    "confidence": auto_answer_judge.get("confidence"),
                    "provider": auto_answer_judge.get("provider"),
                    "model": auto_answer_judge.get("model"),
                    "should_override_deterministic": auto_answer_judge.get("should_override_deterministic"),
                    "reason": result.get("reason"),
                    "evaluation_snapshot": snapshot,
                },
            )
        )
    log_audit_event(
        db,
        "practice.answer_submitted",
        "submission",
        submission.id,
        {
            "session_id": session_id,
            "question_id": payload.question_id,
            "is_correct": result.get("is_correct"),
            "effective_error_count": effective_error_count,
            "effective_wrong": submission.effective_wrong,
            "excluded_from_mastery": submission.excluded_from_mastery,
            "solution_revealed": submission.solution_revealed,
            "help_level_reached": submission.help_level_reached,
            "wrong_record_id": wrong_record.id if wrong_record else None,
        },
    )
    _, intervention_action = intervention_cycle_for_error_count(effective_error_count)
    if submission.effective_wrong and intervention_action == "prefetch":
        intervention = prefetch_intervention(db, session_id, payload.question_id, effective_error_count, submission.id)
        if intervention:
            temporary_hint = intervention_is_temporary_hint(intervention)
            db.add(
                AnswerEvent(
                    session_id=session_id,
                    question_id=payload.question_id,
                    event_type="temporary_hint_prefetched" if temporary_hint else "intervention_prefetched",
                    payload={
                        "at_ms": payload.elapsed_ms,
                        "effective_error_count": effective_error_count,
                        "intervention_record_id": intervention.id,
                        "hint_question_id": ((intervention.payload or {}).get("hint_question") or {}).get("id") if temporary_hint else intervention.current_question_id,
                        "cycle": intervention.intervention_cycle,
                        "available_after_wrong_count": intervention.available_after_wrong_count,
                        "temporary_hint": temporary_hint,
                    },
                )
            )
    if submission.effective_wrong and intervention_action in {"show", "guided_solution"}:
        intervention = trigger_intervention(db, session_id, payload.question_id, effective_error_count)
        temporary_hint = intervention_is_temporary_hint(intervention)
        db.add(
            AnswerEvent(
                session_id=session_id,
                question_id=payload.question_id,
                event_type="temporary_hint_due" if temporary_hint else "intervention_due",
                payload={
                    "at_ms": payload.elapsed_ms,
                    "effective_error_count": effective_error_count,
                    "intervention_record_id": intervention.id,
                    "hint_question_id": ((intervention.payload or {}).get("hint_question") or {}).get("id") if temporary_hint else intervention.current_question_id,
                    "cycle": intervention.intervention_cycle,
                    "temporary_hint": temporary_hint,
                },
            )
        )
    mastery_record = db.scalar(
        select(InterventionRecord).where(
            InterventionRecord.session_id == session_id,
            InterventionRecord.current_question_id == payload.question_id,
            InterventionRecord.intervention_type == "mastery_check",
            InterventionRecord.status == "active",
        )
    )
    if mastery_record:
        mastery_payload = dict(mastery_record.payload or {})
        mastery_payload.update(
            {
                "state": "completed" if result.get("is_correct") is True else "attempted",
                "last_submission_id": submission.id,
                "last_result": {
                    "is_correct": result.get("is_correct"),
                    "reason": result.get("reason"),
                    "effective_error_count": effective_error_count,
                },
            }
        )
        if result.get("is_correct") is True:
            mastery_record.status = "completed"
            mastery_record.prefetch_status = "completed"
            mastery_payload["completed_at"] = utc_now().isoformat()
        mastery_record.payload = mastery_payload
        mastery_record.updated_at = utc_now()
        result = {
            **result,
            "mastery_check": {
                "record_id": mastery_record.id,
                "original_question_id": mastery_record.original_question_id,
                "state": mastery_payload["state"],
            },
        }
        submission.grading_result = result
        db.add(
            AnswerEvent(
                session_id=session_id,
                question_id=mastery_record.original_question_id,
                event_type="mastery_check_completed" if result.get("is_correct") is True else "mastery_check_attempted",
                payload={
                    "record_id": mastery_record.id,
                    "check_question_id": payload.question_id,
                    "submission_id": submission.id,
                    "success": result.get("is_correct"),
                    "at_ms": payload.elapsed_ms,
                },
            )
        )
    state_version = current_state_version(session, payload.question_id) + 1
    result = {**result, "state_version": state_version}
    submission.grading_result = result
    response_data = {
        "submission_id": submission.id,
        "is_correct": result.get("is_correct"),
        "result": result,
        "attempt_index": attempt_index,
        "effective_attempt_index": attempt_stats["effective_attempt_index"],
        "effective_error_count": effective_error_count,
        "duplicate_answer": duplicate_answer,
        "duplicate_of_submission_id": attempt_stats["duplicate_of_submission_id"],
        "is_first_attempt": attempt_index == 1,
        "is_final_attempt": True,
        "state_version": state_version,
    }
    record_question_operation(
        session,
        payload.question_id,
        operation="submit",
        idempotency_key=payload.idempotency_key,
        response=response_data,
    )
    db.commit()
    return SubmitAnswerResponse(**response_data)


@router.post("/{session_id}/judge-answer", response_model=AnswerJudgeResponse)
def judge_answer(session_id: str, payload: AnswerJudgeRequest, db: Session = Depends(get_db)) -> AnswerJudgeResponse:
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if payload.question_id not in session.question_ids:
        raise HTTPException(status_code=400, detail="question does not belong to session")
    if session.mode == "strict_test" or bool((session.config or {}).get("selection", {}).get("strict_no_ai", False)):
        raise HTTPException(status_code=403, detail="AI answer judge is disabled during strict tests")
    existing_response = find_idempotent_response(session, payload.question_id, "judge_answer", payload.idempotency_key)
    if existing_response:
        return AnswerJudgeResponse(**existing_response)
    ensure_expected_state_version(session, payload.question_id, payload.expected_state_version)

    question = db.get(Question, payload.question_id)
    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == payload.question_id))
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    if answer is None:
        raise HTTPException(status_code=404, detail="answer spec not found")
    version = db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == payload.question_id).order_by(QuestionVersion.version.desc()))
    snapshot = evaluation_snapshot(question, version, answer)
    result = judge_answer_with_ai(db, question, version, answer, payload.answer_text, payload.thought_text)
    state_version = current_state_version(session, payload.question_id) + 1
    result = {**result, "evaluation_snapshot": snapshot, "state_version": state_version}
    db.add(
        AnswerEvent(
            session_id=session_id,
            question_id=payload.question_id,
            event_type="answer_judged",
            payload={
                "submission_id": payload.submission_id,
                "verdict": result["verdict"],
                "is_correct": result["is_correct"],
                "confidence": result["confidence"],
                "provider": result["provider"],
                "model": result["model"],
                "should_override_deterministic": result["should_override_deterministic"],
                "state_version": state_version,
                "evaluation_snapshot": snapshot,
            },
        )
    )
    log_audit_event(
        db,
        "practice.answer_judged",
        "practice_session",
        session_id,
        {
            "question_id": payload.question_id,
            "verdict": result["verdict"],
            "provider": result["provider"],
            "model": result["model"],
        },
    )
    record_question_operation(
        session,
        payload.question_id,
        operation="judge_answer",
        idempotency_key=payload.idempotency_key,
        response=result,
    )
    db.commit()
    return AnswerJudgeResponse(**result)


@router.post("/{session_id}/questions/{question_id}/mastery-check", response_model=PracticeSessionStateResponse)
def request_mastery_check(
    session_id: str,
    question_id: str,
    background_tasks: BackgroundTasks,
    payload: MasteryCheckRequest | None = None,
    db: Session = Depends(get_db),
) -> PracticeSessionStateResponse:
    payload = payload or MasteryCheckRequest()
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    session_question_ids = ordered_unique_question_ids(session.question_ids or [])
    if question_id not in session_question_ids:
        raise HTTPException(status_code=400, detail="question does not belong to session")
    if strict_ai_disabled(session):
        raise HTTPException(status_code=403, detail="mastery check generation is disabled during strict tests")
    existing = active_mastery_check_record(db, session_id, question_id)
    if existing is not None:
        return get_session_state(session_id, db)
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    version = db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == question_id).order_by(QuestionVersion.version.desc()))
    records: list[InterventionRecord] = []
    job_ids: list[str] = []
    requested_count = int(payload.count or 1)
    idempotency_key = (payload.idempotency_key or "").strip()
    if idempotency_key:
        evidence_keys = [mastery_check_evidence_key(session_id, question_id, idempotency_key, index, requested_count) for index in range(requested_count)]
        existing_by_key = db.scalar(
            select(InterventionRecord).where(
                InterventionRecord.session_id == session_id,
                InterventionRecord.original_question_id == question_id,
                InterventionRecord.intervention_type == "mastery_check",
                InterventionRecord.evidence_hash.in_(evidence_keys),
            )
        )
        if existing_by_key is not None:
            return get_session_state(session_id, db)
    try:
        for sibling_index in range(requested_count):
            request_payload = build_mastery_generation_request(db, session, question, version, payload, sibling_index=sibling_index, sibling_count=requested_count)
            if idempotency_key:
                request_payload["idempotency_key"] = idempotency_key
            job = GenerationJob(
                status="queued",
                request=request_payload,
                progress={"stage": "queued", "origin": "mastery_check", "base_question_id": question_id, "sibling_index": sibling_index, "sibling_count": requested_count, "idempotency_key": idempotency_key or None},
            )
            db.add(job)
            db.flush()
            job_ids.append(job.id)
            evidence_hash = mastery_check_evidence_key(session_id, question_id, idempotency_key, sibling_index, requested_count) if idempotency_key else intervention_evidence_hash(session_id, question_id, sibling_index, job.id)
            record = InterventionRecord(
                session_id=session_id,
                original_question_id=question_id,
                current_question_id=question_id,
                intervention_type="mastery_check",
                status="active",
                intervention_cycle=sibling_index,
                prefetch_status="generating",
                evidence_hash=evidence_hash,
                available_after_wrong_count=0,
                payload={
                    "state": "generating",
                    "generation_job_id": job.id,
                    "base_question_id": question_id,
                    "source_session_id": session_id,
                    "requested_at": utc_now().isoformat(),
                    "minimum_difficulty_star": question.difficulty_star,
                    "sibling_index": sibling_index,
                    "sibling_count": requested_count,
                    "candidate_count": payload.candidate_count,
                    "idempotency_key": idempotency_key or None,
                    "target_tags": request_payload.get("target_tags", []),
                    "answer_history": (request_payload.get("mastery_check_context") or {}).get("answer_history"),
                },
            )
            db.add(record)
            db.flush()
            records.append(record)
        db.add(
            AnswerEvent(
                session_id=session_id,
                question_id=question_id,
                event_type="mastery_check_requested",
                payload={
                    "record_id": records[0].id if records else None,
                    "record_ids": [row.id for row in records],
                    "generation_job_id": job_ids[0] if job_ids else None,
                    "generation_job_ids": job_ids,
                    "at_ms": payload.at_ms,
                    "state": "generating",
                    "requested_count": requested_count,
                    "candidate_count": payload.candidate_count,
                    "idempotency_key": idempotency_key or None,
                },
            )
        )
        log_audit_event(
            db,
            "practice.mastery_check_requested",
            "practice_session",
            session_id,
            {"question_id": question_id, "generation_job_ids": job_ids, "record_ids": [row.id for row in records], "idempotency_key": idempotency_key or None},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return get_session_state(session_id, db)
    provider = "mock" if payload.use_mock else "openai_compatible"
    for record in records:
        if payload.run_now:
            try:
                finish_mastery_check_record(db, record.id, provider=provider)
            except Exception as exc:
                db.rollback()
                mark_mastery_check_record_failed(db, record.id, f"provider_or_generation_exception: {exc}", provider=provider)
        else:
            background_tasks.add_task(process_mastery_check_background, record.id, provider)
    return get_session_state(session_id, db)


@router.post("/{session_id}/events", response_model=PracticeTimingResponse)
def record_event(session_id: str, payload: PracticeEventRequest, db: Session = Depends(get_db)) -> PracticeTimingResponse:
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if payload.question_id and payload.question_id not in session.question_ids:
        raise HTTPException(status_code=400, detail="question does not belong to session")
    db.add(
        AnswerEvent(
            session_id=session_id,
            question_id=payload.question_id,
            event_type=payload.event_type,
            payload={**payload.payload, "at_ms": payload.at_ms},
        )
    )
    if payload.question_id and payload.event_type in {"skipped", "context_changed"}:
        stale_rows = mark_prefetched_interventions_stale(db, session_id, payload.question_id, payload.event_type)
        for row in stale_rows:
            db.add(
                AnswerEvent(
                    session_id=session_id,
                    question_id=payload.question_id,
                    event_type="intervention_prefetch_stale",
                    payload={"record_id": row.id, "reason": payload.event_type, "at_ms": payload.at_ms},
                )
            )
    if payload.event_type == "paused":
        session.status = "paused"
    elif payload.event_type in {"resumed_answer", "resumed_thought", "answer_focus", "thought_focus"}:
        session.status = "active"
    elif payload.event_type == "skipped":
        session.status = "active"
    db.commit()
    return PracticeTimingResponse(session_id=session_id, timing=get_session_timing_state(session_id, db).timing)


@router.post("/{session_id}/feedback", response_model=PracticeTimingResponse)
def submit_feedback(session_id: str, payload: PracticeFeedbackRequest, db: Session = Depends(get_db)) -> PracticeTimingResponse:
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if payload.question_id and payload.question_id not in session.question_ids:
        raise HTTPException(status_code=400, detail="question does not belong to session")
    db.add(
        AnswerEvent(
            session_id=session_id,
            question_id=payload.question_id,
            event_type="feedback_submitted",
            payload={
                "feedback_type": payload.feedback_type,
                "message": payload.message,
                "at_ms": payload.at_ms,
            },
        )
    )
    log_audit_event(
        db,
        "practice.feedback_submitted",
        "practice_session",
        session_id,
        {"question_id": payload.question_id, "feedback_type": payload.feedback_type},
    )
    db.commit()
    return PracticeTimingResponse(session_id=session_id, timing=get_session_timing_state(session_id, db).timing)


@router.post("/{session_id}/question-support", response_model=QuestionSupportResponse)
def request_question_support(session_id: str, payload: QuestionSupportRequest, db: Session = Depends(get_db)) -> QuestionSupportResponse:
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if payload.question_id not in session.question_ids:
        raise HTTPException(status_code=400, detail="question does not belong to session")

    question = db.get(Question, payload.question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    version = db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == payload.question_id).order_by(QuestionVersion.version.desc()))
    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == payload.question_id))
    strict_mode = session.mode == "strict_test" or bool((session.config or {}).get("selection", {}).get("strict_no_ai", False))
    result = run_question_support(db, question, version, answer, payload.concern_type, payload.message, strict_mode=strict_mode)

    replacement_question_id = None
    replaced = False
    if result.get("question_problem"):
        replacement_question_id = find_replacement_question_id(db, session, question)
        if replacement_question_id:
            question_ids = list(session.question_ids or [])
            question_ids[question_ids.index(payload.question_id)] = replacement_question_id
            session.question_ids = question_ids
            session.updated_at = utc_now()
            replaced = True
            result["safe_response"] = f'{result.get("safe_response") or "已记录题目问题。"} 已更换为同星级题；这是替换原题位，总题数不变，可继续作答。'
        else:
            result["safe_response"] = f'{result.get("safe_response") or "已记录题目问题。"} 暂时没有可替换的同星级题，原题仍保留；可以跳过或暂停并人工处理。'

    db.add(
        AnswerEvent(
            session_id=session_id,
            question_id=payload.question_id,
            event_type="question_support_requested",
            payload={
                "concern_type": payload.concern_type,
                "message": payload.message,
                "at_ms": payload.at_ms,
                "question_problem": result.get("question_problem"),
                "issue_type": result.get("issue_type"),
                "confidence": result.get("confidence"),
                "replacement_question_id": replacement_question_id,
                "replaced": replaced,
            },
        )
    )
    if replaced and replacement_question_id:
        db.add(
            AnswerEvent(
                session_id=session_id,
                question_id=replacement_question_id,
                event_type="question_replaced",
                payload={
                    "old_question_id": payload.question_id,
                    "reason": result.get("issue_type"),
                    "at_ms": payload.at_ms,
                },
            )
        )
    log_audit_event(
        db,
        "practice.question_support_requested",
        "practice_session",
        session_id,
        {
            "question_id": payload.question_id,
            "question_problem": result.get("question_problem"),
            "replacement_question_id": replacement_question_id,
            "replaced": replaced,
            "provider": result.get("provider"),
            "model": result.get("model"),
        },
    )
    db.commit()
    return QuestionSupportResponse(**result, replacement_question_id=replacement_question_id, replaced=replaced)


@router.post("/{session_id}/pause", response_model=PracticeTimingResponse)
def pause_session(session_id: str, payload: PracticeEventRequest, db: Session = Depends(get_db)) -> PracticeTimingResponse:
    payload.event_type = "paused"
    return record_event(session_id, payload, db)


@router.post("/{session_id}/resume", response_model=PracticeTimingResponse)
def resume_session(session_id: str, payload: PracticeEventRequest, db: Session = Depends(get_db)) -> PracticeTimingResponse:
    if payload.event_type not in {"resumed_answer", "resumed_thought"}:
        payload.event_type = "resumed_answer"
    return record_event(session_id, payload, db)


@router.get("/{session_id}/timing", response_model=PracticeTimingResponse)
def get_session_timing_state(session_id: str, db: Session = Depends(get_db)) -> PracticeTimingResponse:
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    rows = db.scalars(select(AnswerEvent).where(AnswerEvent.session_id == session_id).order_by(AnswerEvent.created_at)).all()
    events = [{"event_type": row.event_type, "at_ms": (row.payload or {}).get("at_ms", 0)} for row in rows]
    return PracticeTimingResponse(session_id=session_id, timing=reduce_timing_events(events))


@router.get("/{session_id}/questions/{question_id}/help-state")
def get_help_state(session_id: str, question_id: str, db: Session = Depends(get_db)) -> dict:
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if question_id not in session.question_ids:
        raise HTTPException(status_code=400, detail="question does not belong to session")
    allowed = allowed_help_level(db, session, question_id)
    reached = reached_help_level(db, session_id, question_id)
    return {
        "session_id": session_id,
        "question_id": question_id,
        "strict_no_ai": session.mode == "strict_test" or bool((session.config or {}).get("selection", {}).get("strict_no_ai", False)),
        "allowed_level": allowed,
        "reached_level": reached,
        "can_reveal_next": reached < allowed,
        "solution_revealed": reached >= 5,
        "current_help": help_payload_for_level(db, question_id, reached),
    }


@router.post("/{session_id}/questions/{question_id}/help/next")
def reveal_next_help(session_id: str, question_id: str, payload: HelpRequest | None = None, db: Session = Depends(get_db)) -> dict:
    payload = payload or HelpRequest()
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if question_id not in session.question_ids:
        raise HTTPException(status_code=400, detail="question does not belong to session")
    allowed = allowed_help_level(db, session, question_id)
    reached = reached_help_level(db, session_id, question_id)
    if reached >= allowed:
        raise HTTPException(status_code=403, detail="next help level is not available yet")
    next_level = min(allowed, reached + 1)
    content = help_payload_for_level(db, question_id, next_level)
    db.add(
        AnswerEvent(
            session_id=session_id,
            question_id=question_id,
            event_type="help_revealed",
            payload={"level": next_level, "reason": payload.reason, "at_ms": payload.at_ms},
        )
    )
    db.commit()
    return {"allowed_level": allowed, "reached_level": next_level, "help": content}


@router.post("/{session_id}/questions/{question_id}/give-up")
def give_up_question(session_id: str, question_id: str, payload: HelpRequest | None = None, db: Session = Depends(get_db)) -> dict:
    payload = payload or HelpRequest()
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if question_id not in session.question_ids:
        raise HTTPException(status_code=400, detail="question does not belong to session")
    existing_response = find_idempotent_response(session, question_id, "reveal_answer", payload.idempotency_key)
    if existing_response:
        return existing_response
    ensure_expected_state_version(session, question_id, payload.expected_state_version)
    if session.mode == "strict_test" or bool((session.config or {}).get("selection", {}).get("strict_no_ai", False)):
        raise HTTPException(status_code=403, detail="solution reveal is disabled during strict tests")
    content = help_payload_for_level(db, question_id, 5)
    already_revealed = solution_revealed_for_question(db, session_id, question_id)
    state_version = current_state_version(session, question_id)
    if not already_revealed:
        db.add_all(
            [
                AnswerEvent(session_id=session_id, question_id=question_id, event_type="solution_revealed", payload={"level": 5, "reason": payload.reason, "at_ms": payload.at_ms}),
                AnswerEvent(session_id=session_id, question_id=question_id, event_type="assisted_completion", payload={"independent_mastery_credit": False, "at_ms": payload.at_ms}),
                AnswerEvent(session_id=session_id, question_id=question_id, event_type="answer_reveal_debt_created", payload={"status": "open", "reason": payload.reason, "at_ms": payload.at_ms}),
                AnswerEvent(session_id=session_id, question_id=question_id, event_type="answer_locked_by_solution_reveal", payload={"reason": payload.reason, "at_ms": payload.at_ms}),
            ]
        )
        response = {
            "allowed_level": 5,
            "reached_level": 5,
            "independent_mastery_credit": False,
            "answer_locked": True,
            "help": content,
            "state_version": state_version + 1,
        }
        state_version = record_question_operation(
            session,
            question_id,
            operation="reveal_answer",
            idempotency_key=payload.idempotency_key,
            response=response,
            lock_answer=True,
            lock_reason="solution_revealed",
        )
        response["state_version"] = state_version
        db.commit()
        return response
    return {
        "allowed_level": 5,
        "reached_level": 5,
        "independent_mastery_credit": False,
        "answer_locked": True,
        "help": content,
        "state_version": state_version,
    }


@router.post("/{session_id}/questions/{question_id}/reveal-answer")
def reveal_answer(session_id: str, question_id: str, payload: HelpRequest | None = None, db: Session = Depends(get_db)) -> dict:
    return give_up_question(session_id, question_id, payload, db)


@router.post("/{session_id}/questions/{question_id}/solution-explanation", response_model=SolutionExplanationResponse)
def request_solution_explanation(session_id: str, question_id: str, payload: SolutionExplanationRequest, db: Session = Depends(get_db)) -> SolutionExplanationResponse:
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if question_id not in session.question_ids or payload.question_id != question_id:
        raise HTTPException(status_code=400, detail="question does not belong to session")
    if strict_ai_disabled(session):
        raise HTTPException(status_code=403, detail="solution explanation is disabled during strict tests")
    if not solution_revealed_for_question(db, session_id, question_id):
        raise HTTPException(status_code=409, detail="solution must be revealed before explanation")
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    version = db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == question_id).order_by(QuestionVersion.version.desc()))
    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == question_id))
    evidence = mastery_submission_evidence(db, session_id, question_id)
    db.add(
        AnswerEvent(
            session_id=session_id,
            question_id=question_id,
            event_type="solution_explanation_requested",
            payload={"message": payload.message, "at_ms": payload.at_ms},
        )
    )
    result = explain_solution_with_ai(db, question, version, answer, payload.message, evidence)
    db.add(
        AnswerEvent(
            session_id=session_id,
            question_id=question_id,
            event_type="solution_explanation_answered",
            payload={
                "message": payload.message,
                "status": result.get("status"),
                "provider": result.get("provider"),
                "model": result.get("model"),
                "latency_ms": result.get("latency_ms"),
                "error": result.get("error"),
                "at_ms": payload.at_ms,
            },
        )
    )
    log_audit_event(
        db,
        "practice.solution_explanation_requested",
        "practice_session",
        session_id,
        {"question_id": question_id, "provider": result.get("provider"), "model": result.get("model"), "status": result.get("status")},
    )
    db.commit()
    return SolutionExplanationResponse(**result)


@router.get("/{session_id}", response_model=PracticeSessionStateResponse)
def get_session_state(session_id: str, db: Session = Depends(get_db)) -> PracticeSessionStateResponse:
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    session_question_ids = ordered_unique_question_ids(session.question_ids or [])
    answered = set(db.scalars(select(Submission.question_id).where(Submission.session_id == session_id)))
    interaction = session_interaction_state(db, session_id)
    answered_or_skipped = answered | set(interaction["skipped_question_ids"])
    breakpoints = list((session.config or {}).get("selection", {}).get("breakpoints", []))
    confirmed = list((session.config or {}).get("break_confirmed", []))
    next_breakpoint = next((point for point in breakpoints if len(answered_or_skipped) >= point and point not in confirmed), None)
    current_index = min(len(answered_or_skipped), max(0, len(session_question_ids) - 1))
    submitted_rows = list(db.scalars(select(Submission).where(Submission.session_id == session_id).order_by(Submission.created_at, Submission.id)))
    solution_revealed_question_ids = sorted(
        set(
            db.scalars(
                select(AnswerEvent.question_id)
                .where(
                    AnswerEvent.session_id == session_id,
                    AnswerEvent.event_type == "solution_revealed",
                    AnswerEvent.question_id != "",
                )
            )
        )
    )
    seen_by_question: dict[str, set[str]] = {}
    effective_error_counts: dict[str, int] = {}
    for row in submitted_rows:
        seen = seen_by_question.setdefault(row.question_id, set())
        normalized_answer = normalize_answer_text(row.answer_text)
        if normalized_answer in seen:
            continue
        seen.add(normalized_answer)
        grading = row.grading_result or {}
        explicitly_effective = grading.get("effective_wrong")
        should_count_wrong = (
            row.is_correct is False
            and not bool(row.excluded_from_mastery)
            and (bool(row.effective_wrong) or explicitly_effective is not False)
        )
        if should_count_wrong:
            effective_error_counts[row.question_id] = effective_error_counts.get(row.question_id, 0) + 1
    interventions = list(
        db.scalars(
            select(InterventionRecord)
            .where(
                InterventionRecord.session_id == session_id,
                or_(
                    InterventionRecord.status == "active",
                    and_(
                        InterventionRecord.intervention_type == "mastery_check",
                        InterventionRecord.status.in_(("failed", "completed")),
                    ),
                ),
            )
            .order_by(InterventionRecord.created_at, InterventionRecord.id)
        )
    )
    return PracticeSessionStateResponse(
        id=session.id,
        learner_id=session.learner_id,
        mode=session.mode,
        status=session.status,
        created_at=session.created_at.isoformat() if session.created_at else None,
        display_name=session_display_name(session),
        question_ids=session_question_ids,
        answered_count=len(answered_or_skipped),
        current_index=current_index,
        next_breakpoint=next_breakpoint,
        breakpoints=breakpoints,
        break_confirmed=confirmed,
        strict_no_ai=bool((session.config or {}).get("selection", {}).get("strict_no_ai", False)),
        skipped_question_ids=interaction["skipped_question_ids"],
        bookmarked_question_ids=interaction["bookmarked_question_ids"],
        uncertain_question_ids=interaction["uncertain_question_ids"],
        solution_revealed_question_ids=solution_revealed_question_ids,
        effective_error_counts=effective_error_counts,
        intervention_due_question_ids=sorted(
            [
                row.original_question_id
                for row in interventions
                if row.status == "active" and row.intervention_type != "mastery_check"
            ]
        ),
        active_interventions=[
            {
                "id": row.id,
                "original_question_id": row.original_question_id,
                "current_question_id": row.current_question_id,
                "intervention_type": row.intervention_type,
                "status": row.status,
                "prefetch_status": row.prefetch_status,
                "available_after_wrong_count": row.available_after_wrong_count,
                "payload": row.payload,
            }
            for row in interventions
        ],
        config=session.config,
    )


@router.post("/{session_id}/interventions/{record_id}/hint-answer")
def submit_temporary_hint_answer(session_id: str, record_id: str, payload: TemporaryHintAnswerRequest, db: Session = Depends(get_db)) -> dict:
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    record = db.get(InterventionRecord, record_id)
    if record is None or record.session_id != session_id:
        raise HTTPException(status_code=404, detail="intervention not found")
    if not intervention_is_temporary_hint(record):
        raise HTTPException(status_code=400, detail="intervention is not a temporary hint question")
    if record.status != "active":
        raise HTTPException(status_code=409, detail="temporary hint question is not active")
    record_payload = dict(record.payload or {})
    hint_question = record_payload.get("hint_question") if isinstance(record_payload.get("hint_question"), dict) else {}
    answer_spec = hint_question.get("answer_spec") if isinstance(hint_question, dict) and isinstance(hint_question.get("answer_spec"), dict) else None
    if not answer_spec:
        raise HTTPException(status_code=409, detail="temporary hint question has no answer spec")
    result = grade_answer(answer_spec, payload.answer_text)
    attempts = list(record_payload.get("student_attempts") or [])
    attempt = {
        "answer_text": payload.answer_text,
        "thought_text": payload.thought_text,
        "is_correct": result.get("is_correct"),
        "result": result,
        "at_ms": payload.at_ms,
        "created_at": utc_now().isoformat(),
        "idempotency_key": payload.idempotency_key,
    }
    attempts.append(attempt)
    record_payload["student_attempts"] = attempts[-12:]
    record_payload["last_student_attempt"] = attempt
    record_payload["state"] = "hint_answered_correct" if result.get("is_correct") is True else "hint_answered_wrong"
    record.payload = record_payload
    record.updated_at = utc_now()
    db.commit()
    return {
        "record_id": record.id,
        "is_correct": result.get("is_correct"),
        "result": result,
        "state": record_payload["state"],
        "attempt_count": len(attempts),
        "intervention": {
            "id": record.id,
            "original_question_id": record.original_question_id,
            "current_question_id": record.current_question_id,
            "intervention_type": record.intervention_type,
            "status": record.status,
            "prefetch_status": record.prefetch_status,
            "available_after_wrong_count": record.available_after_wrong_count,
            "payload": record.payload,
        },
    }


@router.post("/{session_id}/interventions/{record_id}", response_model=PracticeSessionStateResponse)
def update_intervention(session_id: str, record_id: str, payload: InterventionActionRequest, db: Session = Depends(get_db)) -> PracticeSessionStateResponse:
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        record = advance_intervention(db, record_id, payload.action, payload.success)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.add(
        AnswerEvent(
            session_id=session_id,
            question_id=record.original_question_id,
            event_type=f"temporary_hint_{payload.action}" if intervention_is_temporary_hint(record) else f"intervention_{payload.action}",
            payload={
                "at_ms": payload.at_ms,
                "record_id": record.id,
                "current_question_id": record.current_question_id,
                "success": payload.success,
                "temporary_hint": intervention_is_temporary_hint(record),
            },
        )
    )
    db.commit()
    return get_session_state(session_id, db)


@router.post("/{session_id}/breaks/confirm", response_model=PracticeSessionStateResponse)
def confirm_break(session_id: str, payload: BreakConfirmRequest, db: Session = Depends(get_db)) -> PracticeSessionStateResponse:
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    config = dict(session.config or {})
    confirmed = set(config.get("break_confirmed", []))
    confirmed.add(payload.checkpoint_index)
    config["break_confirmed"] = sorted(confirmed)
    session.config = config
    session.status = "active"
    db.add(AnswerEvent(session_id=session_id, question_id="", event_type="break_confirmed", payload={"at_ms": payload.at_ms, "checkpoint_index": payload.checkpoint_index}))
    db.commit()
    return get_session_state(session_id, db)
