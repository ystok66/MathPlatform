from __future__ import annotations

import json
import hashlib
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_providers import MockProvider, ProviderChain
from .config import get_settings
from .hint_generator import generate_reviewed_temporary_hint_question
from .importer import map_question_v2, sha256_text
from .models import AnswerEvent, AnswerSpec, InterventionRecord, PracticeSession, Question, QuestionVariant, QuestionVersion, SourceFile, Submission, utc_now
from .prompt_security import guarded_json_message, guarded_system_prompt
from .semantic_similarity import similarity_report


VARIANT_LEVELS = {
    "L1": {
        "name": "同结构低负担",
        "description": "保持核心方法，换一个更轻的表面情境，降低计算和阅读负担。",
    },
    "L2": {
        "name": "同核心换表面",
        "description": "核心关系相同，但题面对象和问法不同，避免只换数字。",
    },
    "L3": {
        "name": "迁移桥接",
        "description": "从启发题回到原题前的桥接问题，保留关键洞察并增加一步迁移。",
    },
}

INTERVENTION_CYCLE_PLAN = {
    1: {"prefetch_at": 2, "show_at": 3, "level": "L2", "available_after_wrong_count": 3},
    2: {"prefetch_at": 4, "show_at": 5, "level": "L1", "available_after_wrong_count": 5},
    3: {"prefetch_at": 6, "show_at": 7, "level": "L3", "available_after_wrong_count": 7},
}


def intervention_cycle_for_error_count(effective_error_count: int) -> tuple[int | None, str]:
    for cycle, plan in INTERVENTION_CYCLE_PLAN.items():
        if effective_error_count == plan["prefetch_at"]:
            return cycle, "prefetch"
        if effective_error_count >= plan["show_at"] and effective_error_count < plan["show_at"] + 1:
            return cycle, "show"
    if effective_error_count > INTERVENTION_CYCLE_PLAN[3]["show_at"]:
        return 3, "guided_solution"
    return None, "none"


def intervention_evidence_hash(session_id: str, question_id: str, effective_error_count: int, trigger_submission_id: str | None = None) -> str:
    payload = {
        "session_id": session_id,
        "question_id": question_id,
        "effective_error_count": effective_error_count,
        "trigger_submission_id": trigger_submission_id,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def ensure_intervention_source(db: Session) -> SourceFile:
    source = db.scalar(select(SourceFile).where(SourceFile.filename == "__intervention_generated__"))
    if source is None:
        source = SourceFile(filename="__intervention_generated__", sha256="intervention", question_count=0)
        db.add(source)
        db.flush()
    return source


def base_answer(db: Session, question_id: str) -> AnswerSpec:
    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == question_id))
    if answer is None:
        raise ValueError(f"answer spec not found: {question_id}")
    return answer


def intervention_is_temporary_hint(record: InterventionRecord | None) -> bool:
    return bool(record and (record.payload or {}).get("storage") == "temporary_hint_ai")


def hint_submission_evidence(db: Session, session_id: str, question_id: str) -> dict[str, Any]:
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
            .where(AnswerEvent.session_id == session_id, AnswerEvent.question_id == question_id)
            .order_by(AnswerEvent.created_at, AnswerEvent.id)
        )
    )
    effective_wrong_count = 0
    for row in submissions:
        grading = row.grading_result or {}
        if row.is_correct is False and not row.excluded_from_mastery and grading.get("duplicate_answer") is not True:
            effective_wrong_count += 1
    return {
        "schema_version": "temporary_hint_student_evidence_v1",
        "session_id": session_id,
        "question_id": question_id,
        "attempt_count": len(submissions),
        "effective_wrong_count": effective_wrong_count,
        "submissions": [
            {
                "submission_id": row.id,
                "answer_text": row.answer_text,
                "thought_text": row.thought_text,
                "is_correct": row.is_correct,
                "effective_wrong": row.effective_wrong,
                "excluded_from_mastery": row.excluded_from_mastery,
                "help_level_reached": row.help_level_reached,
                "reason": (row.grading_result or {}).get("reason"),
                "created_at": row.created_at.isoformat(),
            }
            for row in submissions[-8:]
        ],
        "events": [
            {
                "event_type": row.event_type,
                "payload": row.payload,
                "created_at": row.created_at.isoformat(),
            }
            for row in events[-12:]
            if row.event_type in {"help_revealed", "solution_revealed", "submitted"}
        ],
    }


def create_temporary_hint_payload(db: Session, session_id: str, base_question_id: str, cycle: int, level: str) -> dict[str, Any]:
    base = db.get(Question, base_question_id)
    if base is None:
        raise ValueError(f"base question not found: {base_question_id}")
    version = db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == base_question_id).order_by(QuestionVersion.version.desc()))
    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == base_question_id))
    settings = get_settings()
    provider = "mock" if str(getattr(settings, "model_provider", "") or "").strip().lower() == "mock" else "openai_compatible"
    generated = generate_reviewed_temporary_hint_question(
        db,
        base,
        version,
        answer,
        hint_submission_evidence(db, session_id, base_question_id),
        cycle=cycle,
        level=level,
        provider=provider,
        settings=settings,
    )
    return {
        "storage": "temporary_hint_ai",
        "schema_version": "temporary_hint_intervention_payload_v1",
        "state": "hint_assigned" if generated.get("status") == "accepted" else "failed",
        "cycle": cycle,
        "level": level,
        "base_question_id": base_question_id,
        "generation_status": generated.get("status"),
        "hint_question": generated.get("hint_question"),
        "private_diagnosis": generated.get("private_diagnosis"),
        "review": generated.get("review"),
        "generator": generated.get("generator"),
        "reviewer": generated.get("reviewer"),
        "issues": generated.get("issues") or [],
        "error": generated.get("error"),
        "created_at": utc_now().isoformat(),
        "student_attempts": [],
    }


def variant_raw_question(base: Question, answer: AnswerSpec, level: str, purpose: str) -> dict[str, Any]:
    definition = VARIANT_LEVELS[level]
    answer_value = answer.canonical_answer
    if purpose == "bridge":
        stem = (
            "桥接题：请重新阅读原题摘要，找出原题最终要求填写的目标量，并提交这个目标量的最终数值。"
        )
    elif level == "L1":
        stem = "启发题：先做一个低负担回看。请根据原题摘要，重新找出题目最终要求的数值。"
    elif level == "L2":
        stem = "启发题：请换一种读题顺序回看原题摘要，先确定条件关系，再提交原题要求的最终数值。"
    else:
        stem = "迁移题：把刚才启发题的整理方法迁移回原题，根据摘要提交原题要求的最终数值。"
    stem = f"{stem}\n\n原题摘要：{base.stem_preview[:180]}"
    return {
        "id": f"iv-{uuid4()}",
        "question_text": stem,
        "answer_spec": {
            "status": "verified",
            "answer_type": answer.answer_type,
            "canonical_answer": answer_value,
            "acceptable_answers": (answer.raw_answer_spec or {}).get("acceptable_answers", []),
            "tolerance": (answer.raw_answer_spec or {}).get("tolerance") or {"type": "exact", "value": 0},
            "unit": (answer.raw_answer_spec or {}).get("unit"),
            "order_sensitive": (answer.raw_answer_spec or {}).get("order_sensitive", True),
            "grading_method": (answer.raw_answer_spec or {}).get("grading_method", "exact_normalized"),
            "verification": {
                "level": level,
                "purpose": purpose,
                "base_question_id": base.id,
                "definition": definition,
                "note": "deterministic intervention scaffold; content is intentionally lower-load.",
            },
        },
        "difficulty_quality_evaluation": {
            "difficulty_star": max(1, int(base.difficulty_star or 3) - (1 if level in {"L1", "L2"} else 0)),
            "premium": False,
            "dimension_scores": {},
            "solution_hook": definition["description"],
        },
        "primary_path": {},
        "method_tags": [],
        "task_tags": ["启发题" if purpose == "hint" else "迁移桥接"],
        "structure_tags": ["变体题"],
        "constraint_tags": [definition["name"]],
        "source_metadata": {"base_question_id": base.id, "variant_level": level, "purpose": purpose},
    }


def create_variant_question(db: Session, base_question_id: str, level: str = "L1", purpose: str = "hint") -> tuple[Question, QuestionVariant]:
    if level not in VARIANT_LEVELS:
        raise ValueError(f"unsupported variant level: {level}")
    base = db.get(Question, base_question_id)
    if base is None:
        raise ValueError(f"base question not found: {base_question_id}")
    answer = base_answer(db, base_question_id)
    source = ensure_intervention_source(db)
    raw = variant_raw_question(base, answer, level, purpose)
    qv2 = map_question_v2(raw, "启发题")
    similarity = similarity_report(base.stem_preview, raw["question_text"])
    family_id = base.family_id or f"family:{base.id}"
    qv2["source"]["kind"] = "intervention"
    qv2["source"]["parent_question_id"] = base.id
    qv2["source"]["family_id"] = family_id
    qv2["provenance"] = {
        "base_question_id": base.id,
        "variant_level": level,
        "purpose": purpose,
        "variant_definition": VARIANT_LEVELS[level],
    }
    question = Question(
        id=raw["id"],
        source_file_id=source.id,
        status="verified",
        publish_status="intervention_only",
        eligible_for_practice=False,
        eligible_for_test=False,
        question_fault_status="none",
        question_trust_score=0.75,
        solution_style="elementary_preferred",
        source_name="启发题",
        stem_preview=raw["question_text"][:500],
        difficulty_star=qv2["evaluation"]["difficulty"]["star"],
        premium=False,
        source_difficulty_star=qv2["evaluation"]["difficulty"]["star"],
        calibrated_difficulty_star=qv2["evaluation"]["difficulty"]["star"],
        source_premium=False,
        calibrated_premium=False,
        calibration_version="intervention_v1",
        exact_hash=sha256_text(raw["question_text"]),
        normalized_hash=qv2["dedupe"]["normalized_hash"],
        semantic_hash=qv2["dedupe"]["semantic_hash"],
        family_id=family_id,
    )
    db.add(question)
    db.flush()
    version = QuestionVersion(
        question_id=question.id,
        version=1,
        raw_json=raw,
        question_v2=qv2,
        content_hash=sha256_text(json.dumps(raw, ensure_ascii=False, sort_keys=True)),
    )
    db.add(version)
    db.flush()
    question.current_version_id = version.id
    db.add(
        AnswerSpec(
            question_id=question.id,
            status="verified",
            answer_type=raw["answer_spec"]["answer_type"],
            canonical_answer=raw["answer_spec"]["canonical_answer"],
            normalized_spec=qv2["answer_spec"],
            raw_answer_spec=raw["answer_spec"],
            verification=raw["answer_spec"]["verification"],
        )
    )
    source.question_count += 1
    link = QuestionVariant(
        base_question_id=base.id,
        variant_question_id=question.id,
        variant_level=level,
        purpose=purpose,
        family_id=family_id,
        transformation={
            "definition": VARIANT_LEVELS[level],
            "not_number_only_variant": not similarity["number_only_variant"],
            "similarity": similarity,
            "requires_manual_review": similarity["number_only_variant"] or similarity["source_similarity_risk"] == "high",
        },
    )
    db.add(link)
    db.flush()
    return question, link


def review_variant_quality(
    db: Session,
    link_id: str,
    chain: ProviderChain | None = None,
    roles: dict[str, str] | None = None,
) -> dict[str, Any]:
    link = db.get(QuestionVariant, link_id)
    if link is None:
        raise ValueError(f"variant link not found: {link_id}")
    base = db.get(Question, link.base_question_id)
    variant = db.get(Question, link.variant_question_id)
    if base is None or variant is None:
        raise ValueError(f"variant question pair not found: {link_id}")
    similarity = similarity_report(base.stem_preview, variant.stem_preview)
    deterministic = {
        "schema_version": "variant_quality_review_v1",
        "decision": "approve" if not similarity["number_only_variant"] and similarity["source_similarity_risk"] != "high" else "needs_manual_review",
        "base_question_id": base.id,
        "variant_question_id": variant.id,
        "variant_level": link.variant_level,
        "purpose": link.purpose,
        "similarity": similarity,
        "surface_changed": similarity["lexical_similarity"] < 0.86,
        "lower_load": (variant.difficulty_star or 0) <= (base.difficulty_star or 0) if link.variant_level in {"L1", "L2"} else True,
    }
    messages = [
        {
            "role": "system",
            "content": guarded_system_prompt(
                "You are a variant quality reviewer. First privately compare base and variant structure, answer leakage, similarity, and whether this is only a number change. Do not reveal hidden chain-of-thought or scratchpad. Return only one JSON object. Approve only if it is not a number-only variant."
            ),
        },
        {
            "role": "user",
            "content": guarded_json_message(
                "reviewer",
                deterministic,
                untrusted_fields=[
                    "payload.base_question_id",
                    "payload.variant_question_id",
                    "payload.similarity",
                ],
            ),
        },
    ]
    chain = chain or ProviderChain([MockProvider()], max_retries=0, max_concurrency=1)
    roles = roles or {"default": "mock", "reviewer": "mock"}
    result = chain.run_json(messages, "reviewer", roles, {"mock_factory": lambda: deterministic, "candidate": {"question": {"answer_spec": {"canonical": ["ok"]}}}})[-1]
    model_payload = result.payload or deterministic
    transformation = dict(link.transformation or {})
    transformation["quality_review"] = {
        **deterministic,
        "model_decision": model_payload.get("decision"),
        "provider": result.provider,
        "model": result.model,
        "error": result.error,
    }
    transformation["requires_manual_review"] = deterministic["decision"] != "approve" or model_payload.get("decision") == "reject"
    link.transformation = transformation
    db.add(link)
    db.flush()
    return transformation["quality_review"]


def record_variant_manual_review(db: Session, link_id: str, approved: bool, reviewer_notes: str | None = None) -> dict[str, Any]:
    link = db.get(QuestionVariant, link_id)
    if link is None:
        raise ValueError(f"variant link not found: {link_id}")
    transformation = dict(link.transformation or {})
    transformation["manual_review"] = {
        "status": "approved" if approved else "rejected",
        "reviewer_notes": reviewer_notes,
        "reviewed_at": utc_now().isoformat(),
    }
    transformation["requires_manual_review"] = not approved
    link.transformation = transformation
    db.add(link)
    db.flush()
    return transformation["manual_review"]


def trigger_intervention(db: Session, session_id: str, question_id: str, effective_error_count: int) -> InterventionRecord:
    session = db.get(PracticeSession, session_id)
    if session is None:
        raise ValueError(f"session not found: {session_id}")
    cycle, action = intervention_cycle_for_error_count(effective_error_count)
    if cycle is None:
        raise ValueError(f"no intervention due for error count: {effective_error_count}")
    plan = INTERVENTION_CYCLE_PLAN[cycle]
    existing = db.scalar(
        select(InterventionRecord).where(
            InterventionRecord.session_id == session_id,
            InterventionRecord.original_question_id == question_id,
            InterventionRecord.intervention_cycle == cycle,
        )
    )
    if existing:
        if existing.status == "failed" and action in {"show", "guided_solution"} and intervention_is_temporary_hint(existing):
            temp_payload = create_temporary_hint_payload(db, session_id, question_id, cycle, plan["level"])
            accepted = temp_payload.get("generation_status") == "accepted" and bool(temp_payload.get("hint_question"))
            existing.current_question_id = question_id
            existing.status = "active" if accepted else "failed"
            existing.prefetch_status = "shown" if accepted else "failed"
            existing.payload = {
                **temp_payload,
                "effective_error_count": effective_error_count,
                "state": "hint_assigned" if accepted else "failed",
                "retry_after_failed_prefetch": True,
            }
            existing.updated_at = utc_now()
            db.flush()
            return existing
        if existing.status == "prefetched" and action in {"show", "guided_solution"}:
            existing.status = "active"
            existing.prefetch_status = "shown"
            payload = dict(existing.payload or {})
            payload.update({"state": "hint_assigned", "shown_at_error_count": effective_error_count})
            existing.payload = payload
            existing.updated_at = utc_now()
            if not intervention_is_temporary_hint(existing):
                question_ids = list(session.question_ids or [])
                if existing.current_question_id not in question_ids:
                    question_ids.append(existing.current_question_id)
                    session.question_ids = question_ids
                    session.updated_at = utc_now()
        return existing
    temp_payload = create_temporary_hint_payload(db, session_id, question_id, cycle, plan["level"])
    accepted = temp_payload.get("generation_status") == "accepted" and bool(temp_payload.get("hint_question"))
    record = InterventionRecord(
        session_id=session_id,
        original_question_id=question_id,
        current_question_id=question_id,
        intervention_type="hint",
        status="active" if accepted and action in {"show", "guided_solution"} else "prefetched" if accepted else "failed",
        intervention_cycle=cycle,
        prefetch_status="shown" if accepted and action in {"show", "guided_solution"} else "ready" if accepted else "failed",
        evidence_hash=intervention_evidence_hash(session_id, question_id, effective_error_count),
        available_after_wrong_count=plan["available_after_wrong_count"],
        payload={
            **temp_payload,
            "effective_error_count": effective_error_count,
            "state": "hint_assigned" if accepted and action in {"show", "guided_solution"} else "prefetched" if accepted else "failed",
        },
    )
    db.add(record)
    if record.status == "active" and not intervention_is_temporary_hint(record):
        question_ids = list(session.question_ids or [])
        if record.current_question_id not in question_ids:
            question_ids.append(record.current_question_id)
            session.question_ids = question_ids
            session.updated_at = utc_now()
    db.flush()
    return record


def prefetch_intervention(db: Session, session_id: str, question_id: str, effective_error_count: int, trigger_submission_id: str | None = None) -> InterventionRecord | None:
    cycle, action = intervention_cycle_for_error_count(effective_error_count)
    if action != "prefetch" or cycle is None:
        return None
    existing = db.scalar(
        select(InterventionRecord).where(
            InterventionRecord.session_id == session_id,
            InterventionRecord.original_question_id == question_id,
            InterventionRecord.intervention_cycle == cycle,
        )
    )
    if existing:
        return existing
    record = trigger_intervention(db, session_id, question_id, effective_error_count)
    record.prefetch_trigger_submission_id = trigger_submission_id
    record.evidence_hash = intervention_evidence_hash(session_id, question_id, effective_error_count, trigger_submission_id)
    return record


def cancel_prefetched_interventions(db: Session, session_id: str, question_id: str, reason: str) -> list[InterventionRecord]:
    rows = list(
        db.scalars(
            select(InterventionRecord).where(
                InterventionRecord.session_id == session_id,
                InterventionRecord.original_question_id == question_id,
                InterventionRecord.status == "prefetched",
            )
        )
    )
    for row in rows:
        payload = dict(row.payload or {})
        payload.update({"state": "unused_prefetch", "cancel_reason": reason})
        row.status = "cancelled"
        row.prefetch_status = "unused_prefetch"
        row.payload = payload
        row.updated_at = utc_now()
    return rows


def mark_prefetched_interventions_stale(db: Session, session_id: str, question_id: str, reason: str) -> list[InterventionRecord]:
    rows = list(
        db.scalars(
            select(InterventionRecord).where(
                InterventionRecord.session_id == session_id,
                InterventionRecord.original_question_id == question_id,
                InterventionRecord.status == "prefetched",
            )
        )
    )
    for row in rows:
        payload = dict(row.payload or {})
        payload.update({"state": "stale_prefetch", "stale_reason": reason})
        row.status = "stale"
        row.prefetch_status = "stale"
        row.payload = payload
        row.updated_at = utc_now()
    return rows


def advance_intervention(db: Session, record_id: str, action: str, success: bool | None = None) -> InterventionRecord:
    record = db.get(InterventionRecord, record_id)
    if record is None:
        raise ValueError(f"intervention record not found: {record_id}")
    payload = dict(record.payload or {})
    if action == "bridge":
        if intervention_is_temporary_hint(record):
            payload.update({"state": "bridge_skipped_for_temporary_hint"})
            record.current_question_id = record.original_question_id
            record.intervention_type = "return_original"
            record.payload = payload
            record.updated_at = utc_now()
            return record
        bridge_question, link = create_variant_question(db, record.original_question_id, level="L3", purpose="bridge")
        record.current_question_id = bridge_question.id
        record.intervention_type = "bridge"
        payload.update({"bridge_question_id": bridge_question.id, "bridge_link_id": link.id, "state": "bridge_assigned"})
    elif action == "return_original":
        record.current_question_id = record.original_question_id
        record.intervention_type = "return_original"
        payload.update({"state": "returned_original"})
    elif action == "complete":
        record.status = "completed"
        payload.update({"state": "completed", "transfer_success": bool(success)})
    else:
        raise ValueError(f"unsupported intervention action: {action}")
    record.payload = payload
    record.updated_at = utc_now()
    return record
