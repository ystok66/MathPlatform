from __future__ import annotations

from ..ai_transactions import run_model_outside_transaction
from ..localization import LanguageRequest
from ..localization import current_language, language_context

import json
import time
from collections import defaultdict
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_gateway import (
    add_ai_logs,
    existing_generation_duplicate,
    generator_model_for_request,
    hash_payload,
    model_roles,
    progress_timestamp,
    provider_chain,
    set_generation_job_status,
)
from ..audit import log_audit_event
from ..config import get_settings
from ..db import get_db, get_engine
from ..importer import map_question_v2, sha256_text
from ..learner_profiles import resolve_learner_id
from ..models import (
    AnswerEvent,
    AnswerSpec,
    GeneratedCandidate,
    GenerationBatch,
    GenerationBatchItem,
    GenerationDurationStat,
    GenerationJob,
    PracticeSession,
    Question,
    QuestionTag,
    QuestionVariant,
    QuestionVersion,
    SourceFile,
    Submission,
    TaxonomyTag,
    utc_now,
)
from ..prompt_loader import (
    difficulty_guide_excerpt,
    mastery_variant_batch_reviewer_system_prompt,
    mastery_variant_generator_system_prompt,
)
from ..prompt_security import guarded_json_message, guarded_system_prompt
from ..schemas import GenerationBatchResponse
from ..session_idempotency import ordered_unique_question_ids, practice_session_response
from ..task_center import schedule_generation_batch_queue
from ..statuses import (
    ACTIVE_TASK_STATUSES,
    CANCEL_REQUESTED_BATCH_STATUSES,
    STATUS_ACCEPTED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_QUEUED,
    STATUS_REJECTED,
    STATUS_RUNNING,
    STATUS_TIMED_OUT,
    TERMINAL_BATCH_STATUSES,
)
from .generation import (
    accepted_question_ids_for_batch,
    batch_item_source_type,
    batch_items,
    batch_payload,
    persist_batch_item_sse_event,
    persist_batch_sse_event,
    refresh_batch_counts,
    release_batch_item_lease,
    set_generation_batch_status,
    set_generation_item_status,
)
from .practice import mastery_submission_evidence

router = APIRouter(prefix="/api/mastery-variants/batches", tags=["generation"])

MASTERY_SOURCE_NAME = "AI类似题"
MASTERY_SOURCE_FILE = "__ai_mastery_variants__"
VARIATION_AXES = (
    "task_form",
    "representation_form",
    "condition_graph",
    "boundary_feature",
    "secondary_tag",
    "range_shape",
    "answer_type",
    "solution_shape",
)
PROFILE_POOL = (
    "near_transfer",
    "misconception_probe",
    "boundary_targeted",
    "condition_interaction",
    "representation_shift",
    "range_decomposition",
    "cross_tag_light",
)


class MasteryVariantSourceRef(BaseModel):
    question_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = None
    group_id: str | None = None
    group_title: str | None = None
    group_type: str | None = None
    position: int | None = Field(default=None, ge=1)


class MasteryVariantBatchRequest(LanguageRequest):
    learner_id: str | None = None
    source_refs: list[MasteryVariantSourceRef] = Field(min_length=1, max_length=50)
    variants_per_question: int = Field(default=1, ge=1, le=3)
    candidate_count: int = Field(default=2, ge=1, le=4)
    concurrency: int = Field(default=2, ge=1, le=4)
    constraints: str | None = None
    solution_style: str = "elementary_preferred"
    use_mock: bool = False
    allow_budget_overrun: bool = False
    allow_reviewer_quality_downgrade: bool = False
    allow_parallel_batches: bool = False


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value in (None, ""):
        return []
    return [value]


def _string_list(value: Any) -> list[str]:
    return [str(row).strip() for row in _as_list(value) if str(row).strip()]


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value not in (None, "") and not isinstance(value, (dict, list, tuple)):
            return str(value).strip()
    return ""


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _stable_source_refs(refs: list[MasteryVariantSourceRef]) -> list[MasteryVariantSourceRef]:
    seen: set[str] = set()
    result: list[MasteryVariantSourceRef] = []
    for ref in refs:
        key = ref.question_id
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def _latest_question_version(db: Session, question: Question) -> QuestionVersion | None:
    if question.current_version_id:
        version = db.get(QuestionVersion, question.current_version_id)
        if version is not None:
            return version
    return db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == question.id).order_by(QuestionVersion.version.desc()))


def _question_tags(db: Session, question_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        select(QuestionTag, TaxonomyTag)
        .join(TaxonomyTag, TaxonomyTag.id == QuestionTag.tag_id)
        .where(QuestionTag.question_id == question_id)
        .order_by(QuestionTag.role, TaxonomyTag.kind, TaxonomyTag.label)
    ).all()
    return [
        {
            "tag_id": tag.tag_id,
            "role": tag.role,
            "raw_label": tag.raw_label,
            "kind": taxonomy.kind,
            "label": taxonomy.label,
        }
        for tag, taxonomy in rows
    ]


def _root_question_payload(db: Session, question: Question) -> dict[str, Any]:
    version = _latest_question_version(db, question)
    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == question.id))
    qv2 = version.question_v2 if version else {}
    content = _as_dict(qv2.get("content"))
    stem_md = _first_text(content.get("stem_md"), question.stem_preview)
    return {
        "question_id": question.id,
        "source_name": question.source_name,
        "difficulty_star": question.difficulty_star,
        "stem_md": stem_md,
        "question_v2": qv2,
        "answer_spec": answer.raw_answer_spec if answer else None,
        "solution": _as_dict(qv2.get("solution")),
        "tags": _question_tags(db, question.id),
    }


def _fallback_evidence(db: Session, learner_id: str | None, question_id: str) -> dict[str, Any]:
    if learner_id:
        sessions = list(
            db.scalars(
                select(PracticeSession)
                .where(PracticeSession.learner_id == learner_id)
                .order_by(PracticeSession.created_at.desc(), PracticeSession.id.desc())
                .limit(60)
            )
        )
    else:
        sessions = list(db.scalars(select(PracticeSession).order_by(PracticeSession.created_at.desc(), PracticeSession.id.desc()).limit(60)))
    for session in sessions:
        if question_id in (session.question_ids or []):
            return mastery_submission_evidence(db, session.id, question_id)
    submissions = list(
        db.scalars(
            select(Submission)
            .where(Submission.question_id == question_id)
            .order_by(Submission.created_at.desc(), Submission.id.desc())
            .limit(3)
        )
    )
    return {
        "schema_version": "mastery_check_evidence_v1",
        "question_id": question_id,
        "attempt_count": len(submissions),
        "effective_wrong_count": sum(1 for row in submissions if row.is_correct is False),
        "solution_revealed": False,
        "submissions": [
            {
                "submission_id": row.id,
                "answer_text": row.answer_text,
                "thought_text": row.thought_text,
                "thought_text_present": bool(row.thought_text),
                "is_correct": row.is_correct,
                "effective_wrong": row.effective_wrong,
                "elapsed_ms": row.elapsed_ms,
                "created_at": row.created_at.isoformat(),
                "reason": (row.grading_result or {}).get("reason"),
            }
            for row in reversed(submissions)
        ],
        "events": [],
    }


def _learner_evidence(db: Session, learner_id: str | None, ref: MasteryVariantSourceRef) -> dict[str, Any]:
    if ref.session_id:
        session = db.get(PracticeSession, ref.session_id)
        if session is not None and ref.question_id in (session.question_ids or []):
            return mastery_submission_evidence(db, session.id, ref.question_id)
    return _fallback_evidence(db, learner_id, ref.question_id)


def _negative_variant_signatures(db: Session, base_question_id: str) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(QuestionVariant)
            .where(QuestionVariant.base_question_id == base_question_id, QuestionVariant.purpose == "mastery_check")
            .order_by(QuestionVariant.created_at.desc(), QuestionVariant.id.desc())
            .limit(20)
        )
    )
    return [
        {
            "variant_question_id": row.variant_question_id,
            "variant_level": row.variant_level,
            "purpose": row.purpose,
            "signature": _as_dict(row.transformation).get("variant_signature") or _as_dict(row.transformation),
        }
        for row in rows
    ]


def _slot_plan(base_star: int, ref: MasteryVariantSourceRef, variants_per_question: int) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for offset in range(variants_per_question):
        absolute = (int(ref.position or 1) + offset - 1) % len(VARIATION_AXES)
        axis = VARIATION_AXES[absolute]
        profile = PROFILE_POOL[(int(ref.position or 1) + offset - 1) % len(PROFILE_POOL)]
        slot_id = f"{ref.question_id[:8]}-{offset + 1}"
        slots.append(
            {
                "slot_id": slot_id,
                "profile": profile,
                "target_star_range": [max(1, base_star - 1), min(5, base_star + 1)],
                "target_star_delta": 0,
                "reserved_unique_axis": axis,
                "required_change_axes": [axis, VARIATION_AXES[(absolute + 3) % len(VARIATION_AXES)]],
                "required_unique_feature_hint": f"Use {axis} as the main visible transfer axis without exposing diagnosis.",
                "forbidden_primary_features": ["number_only_change", "surface_story_swap", "same_solution_template"],
                "min_meaningful_changed_axes": 2,
                "max_new_major_burdens": 1,
                "source_ref": ref.model_dump(),
            }
        )
    return slots


def _generator_messages(generator_input: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": guarded_system_prompt(mastery_variant_generator_system_prompt())},
        {
            "role": "user",
            "content": guarded_json_message(
                "mastery_variant_generator",
                generator_input,
                untrusted_fields=[
                    "payload.root_question",
                    "payload.root_question.stem_md",
                    "payload.root_question.question_v2",
                    "payload.learner_evidence",
                    "payload.learner_evidence.submissions",
                    "payload.relevant_taxonomy",
                    "payload.negative_variant_signatures",
                ],
            ),
        },
    ]


def _reviewer_messages(review_input: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": guarded_system_prompt(mastery_variant_batch_reviewer_system_prompt())},
        {
            "role": "user",
            "content": guarded_json_message(
                "mastery_variant_batch_reviewer",
                review_input,
                untrusted_fields=[
                    "payload.root_question",
                    "payload.learner_evidence",
                    "payload.candidates",
                    "payload.candidates.question_v2",
                    "payload.candidates.solution",
                ],
            ),
        },
    ]


def _mock_generator_output(batch_id: str, root: dict[str, Any], slots: list[dict[str, Any]]) -> dict[str, Any]:
    base_star = _bounded_int(root.get("difficulty_star"), 3, 1, 5)
    candidates = []
    for slot in slots:
        answer = str(20 + len(candidates) + base_star)
        candidates.append(
            {
                "candidate_id": f"{slot['slot_id']}-mock",
                "slot_id": slot["slot_id"],
                "status": "ok",
                "question_v2": {
                    "content": {
                        "stem_md": f"为了检测同一类思路，甲、乙两种卡片共有 {answer} 张。如果甲比乙多 {base_star + 2} 张，按类似的整体关系整理后，乙有多少张？",
                        "answer_type": "integer",
                    },
                    "answer_spec": {"canonical": [answer], "accepted_forms": [answer], "answer_type": "integer", "unit": None, "order_sensitive": True},
                    "solution": {
                        "primary_method": "整体关系整理",
                        "solution_hook": "先找两部分的和与差，再求较小部分。",
                        "student_steps": ["把总数和差分别整理。", "用总数减差后平均分。"],
                        "final_answer": answer,
                        "verification_solution": {"method": "arithmetic_check", "steps": [f"答案为 {answer}。"], "final_answer": answer},
                    },
                    "taxonomy": {},
                    "evaluation": {"difficulty": {"star": base_star, "total_score": max(1, base_star * 2), "reason": "mock"}, "premium": {"is_premium": False, "total_score": 0}},
                    "dedupe": {},
                },
                "variant_signature": {"problem_skeleton": "two_part_sum_difference", "task_form": "find_part", "reserved_unique_axis": slot["reserved_unique_axis"]},
                "self_checks": {"answer_verified": True, "canonical_matches_solution": True, "not_number_only_variant": True},
            }
        )
    return {"status": "ok", "batch_id": batch_id, "root_analysis": {"root_appears_valid": True}, "candidates": candidates, "batch_self_check": {}}


def _mock_review_output(batch_id: str, candidates: list[dict[str, Any]], desired: int) -> dict[str, Any]:
    selected = [str(row.get("candidate_id")) for row in candidates if row.get("status") == "ok"][:desired]
    return {
        "status": "ok" if selected else "all_rejected",
        "batch_id": batch_id,
        "candidate_reviews": [
            {
                "candidate_id": str(row.get("candidate_id")),
                "slot_id": str(row.get("slot_id") or ""),
                "individual_decision": "accept" if str(row.get("candidate_id")) in selected else "reject",
                "hard_failures": [],
                "distance_review": {"structural_distance": "target_band", "number_only_or_surface_variant": False, "mechanical_substitution_possible": False},
                "mastery_alignment": {"core_method_preserved": True, "core_method_centrality": 0.9, "target_gap_activation": 0.8, "overall_core_method_test_power": 0.9, "method_bypass_risk": "low"},
                "difficulty_review": {"within_policy": True, "independent_star": 3},
                "total_score": 90,
            }
            for row in candidates
        ],
        "set_review": {"selected_candidate_ids": selected, "best_candidate_id": selected[0] if selected else None, "pairwise_conflicts": [], "not_selected": []},
        "publish_decision": {"publishable": bool(selected), "selected_candidate_ids": selected, "all_failed": not bool(selected), "selection_summary": "mock review"},
        "retry_constraints": [],
    }


def _review_map(review_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _as_list(review_payload.get("candidate_reviews")):
        if isinstance(row, dict) and row.get("candidate_id"):
            result[str(row["candidate_id"])] = row
    return result


def _selected_candidate_ids(review_payload: dict[str, Any]) -> list[str]:
    publish = _as_dict(review_payload.get("publish_decision"))
    set_review = _as_dict(review_payload.get("set_review"))
    selected = _string_list(publish.get("selected_candidate_ids") or set_review.get("selected_candidate_ids"))
    if not selected and publish.get("best_candidate_id"):
        selected = [str(publish["best_candidate_id"])]
    if not selected and set_review.get("best_candidate_id"):
        selected = [str(set_review["best_candidate_id"])]
    return selected


def _candidate_question_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    qv2 = _as_dict(candidate.get("question_v2"))
    if qv2:
        content = _as_dict(qv2.get("content"))
        answer_spec = _as_dict(qv2.get("answer_spec"))
        solution = _as_dict(qv2.get("solution"))
        evaluation = _as_dict(qv2.get("evaluation"))
        difficulty = _as_dict(evaluation.get("difficulty") or qv2.get("difficulty"))
        premium = _as_dict(evaluation.get("premium") or qv2.get("premium"))
        return {
            "stem_md": _first_text(content.get("stem_md"), qv2.get("stem_md")),
            "answer_type": _first_text(content.get("answer_type"), answer_spec.get("answer_type"), qv2.get("answer_type"), "integer"),
            "answer_spec": answer_spec,
            "solution": solution,
            "taxonomy": _as_dict(qv2.get("taxonomy")),
            "difficulty": difficulty,
            "premium": premium,
        }
    question = _as_dict(candidate.get("question"))
    return {
        "stem_md": _first_text(question.get("stem_md")),
        "answer_type": _first_text(question.get("answer_type"), _as_dict(question.get("answer_spec")).get("answer_type"), "integer"),
        "answer_spec": _as_dict(question.get("answer_spec")),
        "solution": _as_dict(question.get("solution")),
        "taxonomy": _as_dict(question.get("taxonomy")),
        "difficulty": _as_dict(question.get("difficulty")),
        "premium": _as_dict(question.get("premium")),
    }


def _raw_question_for_candidate(candidate: dict[str, Any], question_id: str, base_question: Question, batch: GenerationBatch, job: GenerationJob, generator_model: str, reviewer_model: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    payload = _candidate_question_payload(candidate)
    answer_spec = _as_dict(payload.get("answer_spec"))
    solution = _as_dict(payload.get("solution"))
    verification_solution = _as_dict(solution.get("verification_solution"))
    student_solution = _as_dict(solution.get("student_solution"))
    canonical = _string_list(answer_spec.get("canonical") or answer_spec.get("canonical_answer"))
    final_answer = _first_text(solution.get("final_answer"), student_solution.get("final_answer"), verification_solution.get("final_answer"))
    if not canonical and final_answer:
        canonical = [final_answer]
    accepted = _string_list(answer_spec.get("accepted_forms") or answer_spec.get("acceptable_answers") or canonical)
    answer_type = _first_text(payload.get("answer_type"), answer_spec.get("answer_type"), "integer")
    difficulty = _as_dict(payload.get("difficulty"))
    premium = _as_dict(payload.get("premium"))
    star = _bounded_int(difficulty.get("star") or base_question.difficulty_star, _bounded_int(base_question.difficulty_star, 3, 1, 5), 1, 5)
    stem = _first_text(payload.get("stem_md"))
    if not stem:
        raise ValueError("candidate_missing_stem")
    if not canonical or not canonical[0]:
        raise ValueError("candidate_missing_canonical_answer")
    raw_question = {
        "id": question_id,
        "question_text": stem,
        "answer_spec": {
            "schema_version": "answer_spec_v1",
            "status": "verified",
            "answer_type": answer_type,
            "canonical_answer": canonical[0],
            "acceptable_answers": accepted,
            "tolerance": answer_spec.get("tolerance") or {"type": "exact", "value": 0},
            "unit": answer_spec.get("unit"),
            "order_sensitive": bool(answer_spec.get("order_sensitive", True)),
            "grading_method": answer_spec.get("grading_method") or "exact_normalized",
            "verification": answer_spec.get("verification") or verification_solution or candidate.get("verification_spec") or {},
        },
        "difficulty_quality_evaluation": {
            "difficulty_star": star,
            "difficulty_reason": _first_text(difficulty.get("reason"), "mastery variant reviewer accepted"),
            "dimension_scores": {key: value for key, value in difficulty.items() if key not in {"star", "total_score", "reason"}},
            "total_score": difficulty.get("total_score") or 0,
            "premium_score": premium.get("total_score") or 0,
            "premium": bool(premium.get("is_premium")),
            "premium_reasons": premium.get("reasons") or [],
            "solution_hook": _first_text(solution.get("solution_hook"), solution.get("primary_method")),
            "main_pitfall": "",
        },
        "primary_path": {},
        "method_tags": [],
        "task_tags": [],
        "structure_tags": [],
        "constraint_tags": [],
        "source_metadata": {
            "source_kind": "mastery_variant_batch",
            "source_display_name": MASTERY_SOURCE_NAME,
            "base_question_id": base_question.id,
            "generation_batch_id": batch.id,
            "generation_job_id": job.id,
        },
    }
    qv2 = map_question_v2(raw_question, MASTERY_SOURCE_NAME)
    qv2["source"] = {
        **_as_dict(qv2.get("source")),
        "kind": "generated",
        "source_name": MASTERY_SOURCE_NAME,
        "parent_question_id": base_question.id,
        "family_id": base_question.family_id or f"family:{base_question.id}",
        "generation_job_id": job.id,
        "batch_id": batch.id,
    }
    qv2["solution"] = solution or qv2.get("solution") or {}
    qv2["provenance"] = {
        **_as_dict(qv2.get("provenance")),
        "prompt_template_id": "mastery_variant_generator_2step_v1",
        "reviewer_prompt_template_id": "mastery_variant_batch_reviewer_2step_v1",
        "generator_model": generator_model,
        "reviewer_model": reviewer_model,
        "candidate_id": candidate.get("candidate_id"),
        "base_question_id": base_question.id,
        "generation_batch_id": batch.id,
        "mastery_variant_batch": True,
    }
    return raw_question, qv2, canonical[0]


def _review_accepts(candidate: dict[str, Any], review: dict[str, Any], selected_ids: list[str], selected_ids_present: bool) -> tuple[bool, str | None]:
    candidate_id = str(candidate.get("candidate_id") or "")
    if candidate.get("status") not in {"ok", "accepted", None}:
        return False, str(candidate.get("reject_reason") or "candidate_rejected_by_generator")
    if selected_ids_present and candidate_id not in selected_ids:
        return False, "not_selected_by_batch_reviewer"
    hard_failures = _string_list(review.get("hard_failures"))
    if hard_failures:
        return False, "review_hard_failure:" + ",".join(hard_failures[:3])
    if str(review.get("individual_decision") or "accept").lower() == "reject":
        return False, "reviewer_rejected"
    distance = _as_dict(review.get("distance_review"))
    if distance.get("structural_distance") in {"too_near", "too_far"}:
        return False, f"distance_{distance.get('structural_distance')}"
    if distance.get("number_only_or_surface_variant") is True or distance.get("mechanical_substitution_possible") is True:
        return False, "surface_or_mechanical_variant"
    difficulty = _as_dict(review.get("difficulty_review"))
    if difficulty.get("within_policy") is False:
        return False, "difficulty_out_of_policy"
    try:
        score = float(review.get("total_score") or 0)
    except (TypeError, ValueError):
        score = 0
    if score and score < 85:
        return False, f"review_score_below_85:{score:g}"
    return True, None


def _source_file(db: Session) -> SourceFile:
    source = db.scalar(select(SourceFile).where(SourceFile.filename == MASTERY_SOURCE_FILE))
    if source is None:
        source = SourceFile(filename=MASTERY_SOURCE_FILE, sha256="generated", question_count=0)
        db.add(source)
        db.flush()
    source.question_count += 1
    return source


def _copy_base_tags(db: Session, base_question_id: str, variant_question_id: str) -> None:
    existing = {
        tag_id
        for (tag_id,) in db.execute(select(QuestionTag.tag_id).where(QuestionTag.question_id == variant_question_id)).all()
    }
    for row in db.scalars(select(QuestionTag).where(QuestionTag.question_id == base_question_id)):
        if row.tag_id in existing:
            continue
        db.add(QuestionTag(question_id=variant_question_id, tag_id=row.tag_id, role=row.role, raw_label=row.raw_label))
        existing.add(row.tag_id)


def _publish_candidate(
    db: Session,
    *,
    batch: GenerationBatch,
    item: GenerationBatchItem,
    job: GenerationJob,
    candidate_row: GeneratedCandidate,
    candidate: dict[str, Any],
    review: dict[str, Any],
    base_question: Question,
    generator_model: str,
    reviewer_model: str,
) -> str:
    question_id = f"ai-mv-{candidate_row.id}"
    raw_question, qv2, canonical_answer = _raw_question_for_candidate(candidate, question_id, base_question, batch, job, generator_model, reviewer_model)
    qv2["content"]["language"] = current_language()
    duplicate = existing_generation_duplicate(db, question_id, raw_question["question_text"], qv2)
    if duplicate:
        raise ValueError("duplicate_against_existing_question")
    source = _source_file(db)
    star = _bounded_int(_as_dict(_as_dict(qv2.get("evaluation")).get("difficulty")).get("star"), _bounded_int(base_question.difficulty_star, 3, 1, 5), 1, 5)
    question = Question(
        id=question_id,
        source_file_id=source.id,
        status="verified",
        source_name=MASTERY_SOURCE_NAME,
        publish_status="active_practice",
        eligible_for_practice=True,
        eligible_for_test=False,
        question_fault_status="none",
        question_trust_score=0.82,
        solution_style="elementary_preferred",
        stem_preview=raw_question["question_text"][:500],
        difficulty_star=star,
        premium=False,
        source_difficulty_star=star,
        calibrated_difficulty_star=star,
        source_premium=False,
        calibrated_premium=False,
        calibration_version="mastery_variant_batch_v1",
        exact_hash=qv2["dedupe"]["exact_hash"],
        normalized_hash=qv2["dedupe"]["normalized_hash"],
        semantic_hash=qv2["dedupe"]["semantic_hash"],
        family_id=base_question.family_id or f"family:{base_question.id}",
    )
    db.add(question)
    db.flush()
    version = QuestionVersion(
        question_id=question_id,
        version=1,
        raw_json=raw_question,
        question_v2=qv2,
        content_hash=sha256_text(json.dumps(raw_question, ensure_ascii=False, sort_keys=True)),
    )
    db.add(version)
    db.flush()
    question.current_version_id = version.id
    db.add(
        AnswerSpec(
            question_id=question_id,
            status="verified",
            answer_type=raw_question["answer_spec"]["answer_type"],
            canonical_answer=canonical_answer,
            normalized_spec=qv2["answer_spec"],
            raw_answer_spec=raw_question["answer_spec"],
            verification=raw_question["answer_spec"].get("verification") or {},
        )
    )
    _copy_base_tags(db, base_question.id, question_id)
    db.add(
        QuestionVariant(
            base_question_id=base_question.id,
            variant_question_id=question_id,
            variant_level="mastery_check",
            purpose="mastery_check",
            family_id=base_question.family_id or f"family:{base_question.id}",
            transformation={
                "schema_version": "mastery_variant_batch_v1",
                "generation_batch_id": batch.id,
                "generation_job_id": job.id,
                "batch_item_id": item.id,
                "candidate_id": candidate_row.id,
                "variant_signature": candidate.get("variant_signature") or {},
                "review": review,
            },
        )
    )
    candidate_row.published_question_id = question_id
    candidate_row.published_question_version_id = version.id
    candidate_row.published_at = utc_now()
    candidate_row.publish_status = "active_practice"
    return question_id


def _ensure_practice_session(db: Session, batch: GenerationBatch) -> PracticeSession | None:
    question_ids = ordered_unique_question_ids(accepted_question_ids_for_batch(db, batch.id))
    if not question_ids:
        return None
    request = _as_dict(batch.request)
    progress = _as_dict(batch.progress)
    session_id = _first_text(progress.get("practice_session_id"))
    session = db.get(PracticeSession, session_id) if session_id else None
    learner_id = resolve_learner_id(db, request.get("learner_id"))
    if session is None:
        session = PracticeSession(
            mode="generated_practice",
            learner_id=learner_id,
            status="active",
            config={
                "learner_id": learner_id,
                "origin": "mastery_variant_batch",
                "generation_batch_id": batch.id,
                "selection": {
                    "generation_batch_id": batch.id,
                    "include_generated": True,
                    "mastery_variant_batch": True,
                    "source_refs": request.get("source_refs") or [],
                },
                "break_confirmed": [],
            },
            question_ids=question_ids,
        )
        db.add(session)
        db.flush()
        db.add(
            AnswerEvent(
                session_id=session.id,
                question_id="",
                event_type="mastery_variant_batch_practice_created",
                payload={"generation_batch_id": batch.id, "question_ids": question_ids, "at_ms": 0},
            )
        )
    else:
        merged = list(dict.fromkeys([*(session.question_ids or []), *question_ids]))
        session.question_ids = merged
        session.updated_at = utc_now()
    batch.progress = {
        **(batch.progress or {}),
        "practice_session_id": session.id,
        "practice_question_ids": list(session.question_ids or []),
    }
    return session


def _mark_items_cancelled(db: Session, items: list[GenerationBatchItem], reason: str) -> None:
    for item in items:
        if item.status in ACTIVE_TASK_STATUSES:
            set_generation_item_status(item, STATUS_CANCELLED)
            item.rejection_reason = reason
            item.updated_at = utc_now()
            release_batch_item_lease(item, source_type=batch_item_source_type(item), terminal_reason=reason)
            persist_batch_item_sse_event(db, item)


def _process_root_items(db: Session, batch_id: str, root_question_id: str, provider: str) -> None:
    batch = db.get(GenerationBatch, batch_id)
    if batch is None:
        return
    items = [
        row for row in batch_items(db, batch.id)
        if row.status == STATUS_QUEUED and _as_dict(row.similarity_review).get("base_question_id") == root_question_id
    ]
    if not items:
        return
    if batch.status in CANCEL_REQUESTED_BATCH_STATUSES:
        _mark_items_cancelled(db, items, "batch_cancelled")
        db.commit()
        return
    started = time.perf_counter()
    base_question = db.get(Question, root_question_id)
    if base_question is None:
        for item in items:
            set_generation_item_status(item, STATUS_REJECTED)
            item.rejection_reason = "base_question_not_found"
            item.updated_at = utc_now()
            release_batch_item_lease(item, source_type="mastery_variant", terminal_reason="base_question_not_found")
            persist_batch_item_sse_event(db, item)
        refresh_batch_counts(db, batch)
        db.commit()
        return
    for item in items:
        set_generation_item_status(item, STATUS_RUNNING)
        item.updated_at = utc_now()
        release_batch_item_lease(item, source_type="mastery_variant", base_question_id=root_question_id, lease_owner=f"mastery-root-{uuid4()}")
        persist_batch_item_sse_event(db, item)
    root_ref_payload = _as_dict(items[0].similarity_review).get("source_ref") or {"question_id": root_question_id}
    ref = MasteryVariantSourceRef(**root_ref_payload)
    learner_id = _first_text(_as_dict(batch.request).get("learner_id"))
    root = _root_question_payload(db, base_question)
    evidence = _learner_evidence(db, learner_id, ref)
    base_star = _bounded_int(base_question.difficulty_star, 3, 1, 5)
    slots = _slot_plan(base_star, ref, len(items))
    settings = get_settings()
    base_roles = model_roles(settings.default_model, settings)
    generator_model, generator_bucket = generator_model_for_request(settings, {"difficulty_star": base_star}, base_roles)
    roles = {
        **base_roles,
        "mastery_variant_generator": generator_model,
        "mastery_variant_batch_reviewer": base_roles.get("set_reviewer") or base_roles.get("reviewer") or base_roles.get("default") or settings.default_model,
    }
    chain = provider_chain(provider, settings, db)
    job = GenerationJob(
        status=STATUS_QUEUED,
        request={
            "origin": "mastery_variant_batch",
            "batch_id": batch.id,
            "base_question_id": root_question_id,
            "desired_publish_count": len(items),
            "candidate_count": _bounded_int(_as_dict(batch.request).get("candidate_count"), 2, 1, 4),
            "source_ref": ref.model_dump(),
        },
        progress={"stage": STATUS_QUEUED, "batch_id": batch.id, "base_question_id": root_question_id},
    )
    db.add(job)
    db.flush()
    for item in items:
        item.job_id = job.id
    set_generation_job_status(job, STATUS_RUNNING)
    job.progress = {
        **(job.progress or {}),
        "stage": "generating_mastery_variants",
        "model_selection": {
            "generator_model": generator_model,
            "generator_bucket": generator_bucket,
            "reviewer_model": roles["mastery_variant_batch_reviewer"],
        },
    }
    db.commit()

    generator_input = {
        "schema_version": "mastery_variant_generator_input_v2",
        "generation_batch": {
            "batch_id": batch.id,
            "mode": "single_call_multi_slot",
            "desired_publish_count": len(items),
            "candidate_count": _bounded_int(_as_dict(batch.request).get("candidate_count"), 2, 1, 4),
        },
        "assigned_slots": slots,
        "sibling_reservations": [{"slot_id": row["slot_id"], "reserved_unique_axis": row["reserved_unique_axis"]} for row in slots],
        "desired_publish_count": len(items),
        "root_question": root,
        "learner_evidence": evidence,
        "generation_policy": {
            "allowed_star_range": [max(1, base_star - 1), min(5, base_star + 1)],
            "solution_style": _as_dict(batch.request).get("solution_style") or "elementary_preferred",
            "equation_policy": "prefer_elementary_non_equation",
            "max_new_major_burdens": 1,
            "grade_band": "primary_grade_5_6",
            "student_visible_no_diagnosis": True,
            "publish_to_question_bank": True,
        },
        "difficulty_rubric_compact": difficulty_guide_excerpt()[:6000],
        "relevant_taxonomy": root.get("tags") or [],
        "negative_variant_signatures": _negative_variant_signatures(db, root_question_id),
        "extra_constraints": _as_dict(batch.request).get("constraints"),
    }
    generator_messages = _generator_messages(generator_input)
    generator_results = run_model_outside_transaction(db, chain,
        generator_messages,
        "mastery_variant_generator",
        roles,
        {"mock_factory": lambda: _mock_generator_output(batch.id, root, slots), "request": generator_input},
    )
    add_ai_logs(db, job.id, "mastery_variant_generator", hash_payload({"messages": generator_messages, "input": generator_input}), {"input": generator_input}, generator_results)
    generator_payload = generator_results[-1].payload or {}
    candidates = [row for row in _as_list(generator_payload.get("candidates")) if isinstance(row, dict)]

    db.refresh(batch)
    if batch.status in CANCEL_REQUESTED_BATCH_STATUSES:
        _mark_items_cancelled(db, items, "batch_cancelled_after_generation")
        set_generation_job_status(job, STATUS_CANCELLED)
        job.progress = {**(job.progress or {}), "stage": STATUS_CANCELLED, "terminal_reason": "batch_cancelled_after_generation"}
        refresh_batch_counts(db, batch)
        db.commit()
        return
    if generator_results[-1].provider != "mock" and (generator_results[-1].error or not candidates):
        for item in items:
            set_generation_item_status(item, STATUS_FAILED)
            item.rejection_reason = "mastery_variant_generator_failed"
            item.duration_ms = int((time.perf_counter() - started) * 1000)
            item.updated_at = utc_now()
            release_batch_item_lease(item, source_type="mastery_variant", terminal_reason=item.rejection_reason)
            persist_batch_item_sse_event(db, item)
            db.add(GenerationDurationStat(batch_id=batch.id, item_id=item.id, provider=provider, model=generator_model, status=item.status, duration_ms=item.duration_ms))
        set_generation_job_status(job, STATUS_FAILED)
        job.progress = {**(job.progress or {}), "stage": STATUS_FAILED, "terminal_reason": "mastery_variant_generator_failed", "generator_error": generator_results[-1].error}
        refresh_batch_counts(db, batch)
        db.commit()
        return

    review_input = {
        "schema_version": "mastery_variant_batch_review_input_v2",
        "review_batch": {"batch_id": batch.id, "desired_publish_count": len(items), "selection_strategy": "best_non_duplicate"},
        "root_question": root,
        "learner_evidence": evidence,
        "generation_policy": generator_input["generation_policy"],
        "difficulty_rubric_compact": difficulty_guide_excerpt(include_output_format=True)[:6000],
        "slot_plan": {"assigned_slots": slots, "sibling_reservations": generator_input["sibling_reservations"]},
        "negative_variant_signatures": generator_input["negative_variant_signatures"],
        "candidates": candidates,
        "generator_output_summary": {
            "status": generator_payload.get("status"),
            "root_analysis": generator_payload.get("root_analysis"),
            "batch_self_check": generator_payload.get("batch_self_check"),
        },
    }
    reviewer_messages = _reviewer_messages(review_input)
    reviewer_results = run_model_outside_transaction(db, chain,
        reviewer_messages,
        "mastery_variant_batch_reviewer",
        roles,
        {"mock_factory": lambda: _mock_review_output(batch.id, candidates, len(items)), "request": review_input},
    )
    add_ai_logs(db, job.id, "mastery_variant_batch_reviewer", hash_payload({"messages": reviewer_messages, "input": review_input}), {"input": review_input}, reviewer_results)
    review_payload = reviewer_results[-1].payload or {}
    review_by_candidate = _review_map(review_payload)
    selected_ids = _selected_candidate_ids(review_payload)
    selected_ids_present = bool(selected_ids)
    db.refresh(batch)
    if batch.status in CANCEL_REQUESTED_BATCH_STATUSES:
        _mark_items_cancelled(db, items, "batch_cancelled_after_review")
        set_generation_job_status(job, STATUS_CANCELLED)
        job.progress = {**(job.progress or {}), "stage": STATUS_CANCELLED, "terminal_reason": "batch_cancelled_after_review"}
        refresh_batch_counts(db, batch)
        db.commit()
        return
    item_cursor = 0
    accepted_question_ids: list[str] = []
    rejected_reasons: list[str] = []

    for candidate in candidates:
        if item_cursor >= len(items):
            break
        db.refresh(batch)
        if batch.status in CANCEL_REQUESTED_BATCH_STATUSES:
            _mark_items_cancelled(db, items[item_cursor:], "batch_cancelled_during_publish")
            set_generation_job_status(job, STATUS_CANCELLED)
            job.progress = {
                **(job.progress or {}),
                "stage": STATUS_CANCELLED,
                "terminal_reason": "batch_cancelled_during_publish",
                "accepted_question_ids_before_cancel": accepted_question_ids,
            }
            refresh_batch_counts(db, batch)
            db.commit()
            return
        candidate_id = _first_text(candidate.get("candidate_id"), f"candidate-{item_cursor + 1}")
        review = review_by_candidate.get(candidate_id, {})
        row = GeneratedCandidate(
            job_id=job.id,
            strategy="mastery_variant_batch",
            status=STATUS_REJECTED,
            payload=candidate,
            review={**review, "batch_review": review_payload},
            score=float(review.get("total_score") or 0) if str(review.get("total_score") or "").replace(".", "", 1).isdigit() else None,
        )
        db.add(row)
        db.flush()
        item = items[item_cursor]
        ok, reason = _review_accepts(candidate, review, selected_ids, selected_ids_present)
        if ok:
            try:
                question_id = _publish_candidate(
                    db,
                    batch=batch,
                    item=item,
                    job=job,
                    candidate_row=row,
                    candidate=candidate,
                    review=review,
                    base_question=base_question,
                    generator_model=generator_model,
                    reviewer_model=roles["mastery_variant_batch_reviewer"],
                )
                row.status = STATUS_ACCEPTED
                set_generation_item_status(item, STATUS_ACCEPTED)
                item.candidate_id = row.id
                item.question_id = question_id
                item.rejection_reason = None
                item.similarity_review = {
                    **(item.similarity_review or {}),
                    "source_type": "mastery_variant",
                    "base_question_id": base_question.id,
                    "candidate_id": candidate_id,
                    "review_score": review.get("total_score"),
                    "risk": "low",
                    "variant_signature": candidate.get("variant_signature") or {},
                    "review_status": "accepted_by_batch_reviewer",
                }
                accepted_question_ids.append(question_id)
            except Exception as exc:
                row.status = STATUS_REJECTED
                row.publish_status = "batch_rejected_similar"
                reason = str(exc)[:240]
                set_generation_item_status(item, STATUS_REJECTED)
                item.rejection_reason = reason
                item.similarity_review = {**(item.similarity_review or {}), "source_type": "mastery_variant", "base_question_id": base_question.id, "terminal_reason": reason}
                rejected_reasons.append(reason)
        else:
            set_generation_item_status(item, STATUS_REJECTED)
            item.rejection_reason = reason or "reviewer_rejected"
            item.similarity_review = {**(item.similarity_review or {}), "source_type": "mastery_variant", "base_question_id": base_question.id, "terminal_reason": item.rejection_reason}
            rejected_reasons.append(item.rejection_reason)
        item.duration_ms = int((time.perf_counter() - started) * 1000)
        item.updated_at = utc_now()
        release_batch_item_lease(item, source_type="mastery_variant", terminal_reason=item.rejection_reason or item.status)
        persist_batch_item_sse_event(db, item)
        db.add(GenerationDurationStat(batch_id=batch.id, item_id=item.id, provider=provider, model=roles["mastery_variant_batch_reviewer"], status=item.status, duration_ms=item.duration_ms))
        item_cursor += 1

    for item in items[item_cursor:]:
        set_generation_item_status(item, STATUS_REJECTED)
        item.rejection_reason = "no_review_selected_candidate"
        item.duration_ms = int((time.perf_counter() - started) * 1000)
        item.updated_at = utc_now()
        release_batch_item_lease(item, source_type="mastery_variant", terminal_reason=item.rejection_reason)
        persist_batch_item_sse_event(db, item)
        rejected_reasons.append(item.rejection_reason)

    set_generation_job_status(job, STATUS_ACCEPTED if accepted_question_ids else STATUS_REJECTED)
    job.accepted_question_id = accepted_question_ids[0] if accepted_question_ids else None
    job.progress = {
        **(job.progress or {}),
        "stage": job.status,
        "accepted": bool(accepted_question_ids),
        "accepted_question_ids": accepted_question_ids,
        "terminal_reason": None if accepted_question_ids else (review_payload.get("status") or "all_candidates_rejected"),
        "rejected_reasons": rejected_reasons[:10],
        "review_payload": review_payload,
    }
    refresh_batch_counts(db, batch)
    db.commit()


def process_mastery_variant_batch(db: Session, batch_id: str, provider: str = "openai_compatible") -> dict[str, Any]:
    batch = db.get(GenerationBatch, batch_id)
    with language_context((batch.request or {}).get("language") if batch else current_language()):
        return _process_mastery_variant_batch_localized(db, batch_id, provider)


def _process_mastery_variant_batch_localized(db: Session, batch_id: str, provider: str = "openai_compatible") -> dict[str, Any]:
    batch = db.get(GenerationBatch, batch_id)
    if batch is None:
        raise ValueError(f"mastery variant batch not found: {batch_id}")
    if batch.status in TERMINAL_BATCH_STATUSES:
        return batch_payload(db, batch)
    set_generation_batch_status(batch, STATUS_RUNNING)
    batch.started_at = batch.started_at or utc_now()
    batch.progress = {**(batch.progress or {}), "stage": STATUS_RUNNING}
    db.commit()
    rows_by_root: dict[str, list[GenerationBatchItem]] = defaultdict(list)
    for item in batch_items(db, batch.id):
        review = _as_dict(item.similarity_review)
        if item.status == STATUS_QUEUED and review.get("base_question_id"):
            rows_by_root[str(review["base_question_id"])].append(item)
    for root_question_id in rows_by_root:
        db.refresh(batch)
        if batch.status in CANCEL_REQUESTED_BATCH_STATUSES:
            break
        _process_root_items(db, batch.id, root_question_id, provider)
        db.refresh(batch)
        refresh_batch_counts(db, batch)
        db.commit()
    db.refresh(batch)
    refresh_batch_counts(db, batch)
    if batch.status in CANCEL_REQUESTED_BATCH_STATUSES:
        for item in batch_items(db, batch.id):
            if item.status == STATUS_QUEUED:
                set_generation_item_status(item, STATUS_CANCELLED)
                item.rejection_reason = "batch_cancelled"
                item.updated_at = utc_now()
                release_batch_item_lease(item, source_type=batch_item_source_type(item), terminal_reason="batch_cancelled")
                persist_batch_item_sse_event(db, item)
        set_generation_batch_status(batch, STATUS_CANCELLED)
    elif batch.accepted_count >= batch.target_count:
        set_generation_batch_status(batch, STATUS_COMPLETED)
    elif batch.accepted_count > 0:
        set_generation_batch_status(batch, STATUS_PARTIAL)
    else:
        set_generation_batch_status(batch, STATUS_FAILED)
    batch.completed_at = utc_now()
    if batch.accepted_count > 0:
        session = _ensure_practice_session(db, batch)
        practice_payload = practice_session_response(session).model_dump() if session is not None else None
    else:
        practice_payload = None
    batch.progress = {**(batch.progress or {}), "stage": batch.status, "practice_session": practice_payload}
    log_audit_event(db, "generation.mastery_variant_batch_completed", "generation_batch", batch.id, {"status": batch.status, "accepted_count": batch.accepted_count})
    db.commit()
    return batch_payload(db, batch)


def process_mastery_variant_batch_background(batch_id: str, provider: str) -> None:
    with Session(get_engine()) as db:
        try:
            process_mastery_variant_batch(db, batch_id, provider=provider)
        except Exception as exc:
            db.rollback()
            batch = db.get(GenerationBatch, batch_id)
            if batch is None:
                return
            refresh_batch_counts(db, batch)
            if batch.status not in TERMINAL_BATCH_STATUSES:
                set_generation_batch_status(batch, STATUS_FAILED)
            batch.completed_at = batch.completed_at or utc_now()
            batch.progress = {**(batch.progress or {}), "stage": STATUS_FAILED, "background_error": str(exc), "background_error_at": progress_timestamp()}
            db.commit()


@router.post("", response_model=GenerationBatchResponse)
def create_mastery_variant_batch(payload: MasteryVariantBatchRequest, background_tasks: BackgroundTasks, run_now: bool = True, db: Session = Depends(get_db)) -> dict[str, Any]:
    refs = _stable_source_refs(payload.source_refs)
    if not refs:
        raise HTTPException(status_code=422, detail="source_refs is empty")
    missing = [ref.question_id for ref in refs if db.get(Question, ref.question_id) is None]
    if missing:
        raise HTTPException(status_code=404, detail={"error_code": "base_question_not_found", "question_ids": missing[:10]})
    learner_id = resolve_learner_id(db, payload.learner_id)
    target_count = len(refs) * payload.variants_per_question
    provider = "mock" if payload.use_mock else "openai_compatible"
    request_payload = {
        **payload.model_dump(),
        "language": current_language(),
        "learner_id": learner_id,
        "source_refs": [ref.model_dump() for ref in refs],
        "origin": "mastery_variant_batch",
        "batch_provider": provider,
        "prompt_versions": ["mastery_variant_generator_2step_v1", "mastery_variant_batch_reviewer_2step_v1"],
    }
    batch = GenerationBatch(
        status=STATUS_QUEUED,
        request=request_payload,
        target_count=target_count,
        concurrency=payload.concurrency,
        progress={
            "stage": STATUS_QUEUED,
            "origin": "mastery_variant_batch",
            "target_count": target_count,
            "accepted_count": 0,
            "reused_count": 0,
            "generated_count": 0,
            "rejected_count": 0,
            "failed_count": 0,
            "running_count": 0,
            "completed_item_count": 0,
            "remaining_target_count": target_count,
            "failure_reason_counts": {},
            "variant_source_question_count": len(refs),
            "variants_per_question": payload.variants_per_question,
            "candidate_count": payload.candidate_count,
            "prompt_versions": request_payload["prompt_versions"],
            "queue_policy": "one_active_generation_batch",
            "user_message": "批量类似题已加入任务队列；如果已有批次正在运行，会自动等待。",
        },
    )
    db.add(batch)
    db.flush()
    item_index = 1
    for ref in refs:
        for variant_index in range(payload.variants_per_question):
            item = GenerationBatchItem(
                batch_id=batch.id,
                item_index=item_index,
                status=STATUS_QUEUED,
                similarity_review={
                    "source_type": "mastery_variant",
                    "base_question_id": ref.question_id,
                    "source_ref": ref.model_dump(),
                    "variant_index": variant_index,
                    "variant_count": payload.variants_per_question,
                },
            )
            db.add(item)
            db.flush()
            persist_batch_item_sse_event(db, item)
            item_index += 1
    refresh_batch_counts(db, batch)
    log_audit_event(db, "generation.mastery_variant_batch_created", "generation_batch", batch.id, {"run_now": run_now, "request": request_payload})
    db.commit()
    db.refresh(batch)
    if run_now:
        schedule_generation_batch_queue(background_tasks)
    return batch_payload(db, batch)
