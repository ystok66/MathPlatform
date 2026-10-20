from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import choice, token_urlsafe

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.orm import Session

from ..audit import log_audit_event, sanitize_payload
from ..config import get_settings, project_root
from ..db import get_db
from ..import_preview import acquire_import_guard, preview_import
from ..learner_profiles import resolve_learner_id
from ..release_info import release_info
from ..secrets import has_secret
from ..statuses import (
    FAILED_GENERATION_STATUSES,
    QUESTION_PUBLISH_STATUSES,
    PUBLISH_STATUS_RETIRED_BY_ADMIN,
    PUBLISH_STATUS_RETIRED_QUESTION,
    STATUS_DOMAIN_QUESTION_PUBLISH_STATUS,
    STATUS_DOMAIN_QUESTION_STATUS,
    STATUS_DOMAIN_REVIEW_QUEUE,
    STATUS_REJECTED,
    checked_status_update_values,
)
from ..structured_logging import recent_request_logs, request_log_path
from ..models import (
    AiRunLog,
    AnswerEvent,
    AnswerSpec,
    AssistantConversation,
    AssistantMessage,
    AssistantToolRun,
    AuditLog,
    DEFAULT_LEARNER_ID,
    GeneratedCandidate,
    GeneratedCandidateReview,
    GenerationBatch,
    GenerationBatchItem,
    GenerationDurationStat,
    GenerationEvent,
    GenerationJob,
    InterventionRecord,
    LearnerProfile,
    PracticeSession,
    Question,
    QuestionDuplicateAlias,
    QuestionRepairRecord,
    QuestionTag,
    QuestionVariant,
    QuestionVersion,
    ReportExport,
    ReportFilterView,
    RecoveryAgentRun,
    ReviewQueueItem,
    ReviewRecord,
    SourceFile,
    Submission,
    SystemTask,
    TaxonomyAlias,
    TaxonomyGovernanceRecord,
    TaxonomyMigration,
    TaxonomyTag,
    TestBlueprint,
    TestRun,
    WrongQuestionRecord,
)
from ..schemas import (
    ClearHistoryRequest,
    ClearHistoryResponse,
    DestructiveChallengeRequest,
    DestructiveChallengeResponse,
    HistoryImportResponse,
    HistoryTransfer,
    LearnerDeleteRequest,
    LearnerDeleteResponse,
    QuestionBankImportResponse,
    QuestionBankTransfer,
    QuestionDeleteRequest,
    QuestionDeleteResponse,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

CHALLENGE_TTL_SECONDS = 600
CHALLENGE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DESTRUCTIVE_CHALLENGES: dict[str, dict[str, str]] = {}

HISTORY_TABLES = {
    "source_files": SourceFile,
    "learner_profiles": LearnerProfile,
    "taxonomy_tags": TaxonomyTag,
    "taxonomy_aliases": TaxonomyAlias,
    "taxonomy_migrations": TaxonomyMigration,
    "taxonomy_governance_records": TaxonomyGovernanceRecord,
    "questions": Question,
    "question_versions": QuestionVersion,
    "question_repair_records": QuestionRepairRecord,
    "question_duplicate_aliases": QuestionDuplicateAlias,
    "answer_specs": AnswerSpec,
    "question_tags": QuestionTag,
    "question_variants": QuestionVariant,
    "generation_jobs": GenerationJob,
    "generated_candidates": GeneratedCandidate,
    "generation_batches": GenerationBatch,
    "generation_batch_items": GenerationBatchItem,
    "generation_events": GenerationEvent,
    "generated_candidate_reviews": GeneratedCandidateReview,
    "generation_duration_stats": GenerationDurationStat,
    "assistant_conversations": AssistantConversation,
    "assistant_messages": AssistantMessage,
    "assistant_tool_runs": AssistantToolRun,
    "practice_sessions": PracticeSession,
    "test_blueprints": TestBlueprint,
    "test_runs": TestRun,
    "submissions": Submission,
    "intervention_records": InterventionRecord,
    "wrong_question_records": WrongQuestionRecord,
    "review_records": ReviewRecord,
    "answer_events": AnswerEvent,
    "report_filter_views": ReportFilterView,
    "report_exports": ReportExport,
    "ai_run_logs": AiRunLog,
    "system_tasks": SystemTask,
    "recovery_agent_runs": RecoveryAgentRun,
    "audit_logs": AuditLog,
}

QUESTION_BANK_TABLES = {
    "source_files": SourceFile,
    "taxonomy_tags": TaxonomyTag,
    "taxonomy_aliases": TaxonomyAlias,
    "taxonomy_migrations": TaxonomyMigration,
    "taxonomy_governance_records": TaxonomyGovernanceRecord,
    "questions": Question,
    "question_versions": QuestionVersion,
    "question_repair_records": QuestionRepairRecord,
    "question_duplicate_aliases": QuestionDuplicateAlias,
    "answer_specs": AnswerSpec,
    "question_tags": QuestionTag,
    "question_variants": QuestionVariant,
    "review_queue_items": ReviewQueueItem,
}

QUESTION_BANK_IMPORT_ORDER = (
    "source_files",
    "taxonomy_tags",
    "taxonomy_aliases",
    "taxonomy_migrations",
    "taxonomy_governance_records",
    "questions",
    "question_versions",
    "question_repair_records",
    "question_duplicate_aliases",
    "answer_specs",
    "question_tags",
    "question_variants",
    "review_queue_items",
)


def generated_question_ids(db: Session) -> list[str]:
    linked_ids = set(
        db.scalars(
            select(GeneratedCandidate.published_question_id).where(GeneratedCandidate.published_question_id.is_not(None))
        )
    )
    linked_ids.update(
        db.scalars(
            select(GenerationJob.accepted_question_id).where(GenerationJob.accepted_question_id.is_not(None))
        )
    )
    if not linked_ids:
        return []
    return list(db.scalars(select(Question.id).where(Question.id.in_(linked_ids))))


def id_set(values) -> set[str]:
    return {str(value) for value in values if value}


def json_matches_key(value: object, key: str, expected: str) -> bool:
    if isinstance(value, dict):
        if str(value.get(key) or "") == expected:
            return True
        return any(json_matches_key(item, key, expected) for item in value.values())
    if isinstance(value, list):
        return any(json_matches_key(item, key, expected) for item in value)
    return False


def json_contains_any(value: object, candidates: set[str]) -> bool:
    if not candidates:
        return False
    if isinstance(value, dict):
        return any(json_contains_any(item, candidates) for item in value.values())
    if isinstance(value, list):
        return any(json_contains_any(item, candidates) for item in value)
    return isinstance(value, str) and value in candidates


def table_ids_from_rows(rows, attr: str = "id") -> set[str]:
    return {str(getattr(row, attr)) for row in rows if getattr(row, attr, None)}


def build_history_export_scope(db: Session, learner_id: str) -> dict[str, set[str] | str]:
    sessions = list(db.scalars(select(PracticeSession).where(PracticeSession.learner_id == learner_id)))
    session_ids = table_ids_from_rows(sessions)
    question_ids: set[str] = set()
    for session in sessions:
        question_ids.update(id_set(session.question_ids or []))

    submission_ids: set[str] = set()
    answer_event_ids: set[str] = set()
    blueprint_ids: set[str] = set()
    if session_ids:
        submissions = list(db.scalars(select(Submission).where(Submission.session_id.in_(session_ids))))
        submission_ids = table_ids_from_rows(submissions)
        question_ids.update(id_set(row.question_id for row in submissions))

        answer_events = list(db.scalars(select(AnswerEvent).where(AnswerEvent.session_id.in_(session_ids))))
        answer_event_ids = table_ids_from_rows(answer_events)
        question_ids.update(id_set(row.question_id for row in answer_events))

        question_ids.update(id_set(db.scalars(select(WrongQuestionRecord.question_id).where(WrongQuestionRecord.session_id.in_(session_ids)))))
        question_ids.update(id_set(db.scalars(select(ReviewRecord.question_id).where(ReviewRecord.session_id.in_(session_ids)))))
        question_ids.update(id_set(db.scalars(select(InterventionRecord.original_question_id).where(InterventionRecord.session_id.in_(session_ids)))))
        question_ids.update(id_set(db.scalars(select(InterventionRecord.current_question_id).where(InterventionRecord.session_id.in_(session_ids)))))
        blueprint_ids = id_set(db.scalars(select(TestRun.blueprint_id).where(TestRun.session_id.in_(session_ids))))

    all_ai_question_ids = set(generated_question_ids(db))
    ai_question_ids = all_ai_question_ids.intersection(question_ids)

    job_ids: set[str] = set()
    batch_ids: set[str] = set()
    candidate_ids: set[str] = set()
    item_ids: set[str] = set()

    for row in db.scalars(select(GenerationJob)):
        if json_matches_key(row.request or {}, "learner_id", learner_id) or json_matches_key(row.progress or {}, "learner_id", learner_id):
            job_ids.add(row.id)
            if row.accepted_question_id in all_ai_question_ids:
                ai_question_ids.add(str(row.accepted_question_id))
    if ai_question_ids:
        job_ids.update(id_set(db.scalars(select(GenerationJob.id).where(GenerationJob.accepted_question_id.in_(ai_question_ids)))))

    for row in db.scalars(select(GenerationBatch)):
        if json_matches_key(row.request or {}, "learner_id", learner_id) or json_matches_key(row.progress or {}, "learner_id", learner_id):
            batch_ids.add(row.id)

    for row in db.scalars(select(GeneratedCandidate)):
        if row.job_id in job_ids or row.published_question_id in ai_question_ids:
            candidate_ids.add(row.id)
            if row.job_id:
                job_ids.add(row.job_id)
            if row.published_question_id in all_ai_question_ids:
                ai_question_ids.add(str(row.published_question_id))

    for row in db.scalars(select(GenerationBatchItem)):
        if row.batch_id in batch_ids or row.job_id in job_ids or row.candidate_id in candidate_ids or row.question_id in ai_question_ids:
            item_ids.add(row.id)
            if row.batch_id:
                batch_ids.add(row.batch_id)
            if row.job_id:
                job_ids.add(row.job_id)
            if row.candidate_id:
                candidate_ids.add(row.candidate_id)
            if row.question_id in all_ai_question_ids:
                ai_question_ids.add(str(row.question_id))

    for row in db.scalars(select(GeneratedCandidate)):
        if row.job_id in job_ids or row.id in candidate_ids or row.published_question_id in ai_question_ids:
            candidate_ids.add(row.id)
            if row.published_question_id in all_ai_question_ids:
                ai_question_ids.add(str(row.published_question_id))

    source_file_ids: set[str] = set()
    if ai_question_ids:
        source_file_ids = id_set(db.scalars(select(Question.source_file_id).where(Question.id.in_(ai_question_ids))))

    return {
        "learner_id": learner_id,
        "session_ids": session_ids,
        "submission_ids": submission_ids,
        "answer_event_ids": answer_event_ids,
        "question_ids": question_ids,
        "ai_question_ids": ai_question_ids,
        "source_file_ids": source_file_ids,
        "job_ids": job_ids,
        "batch_ids": batch_ids,
        "candidate_ids": candidate_ids,
        "item_ids": item_ids,
        "blueprint_ids": blueprint_ids,
    }


def question_ids_used_by_other_learners(db: Session, learner_id: str) -> set[str]:
    question_ids: set[str] = set()
    sessions = list(db.scalars(select(PracticeSession).where(PracticeSession.learner_id != learner_id)))
    other_session_ids = table_ids_from_rows(sessions)
    for session in sessions:
        question_ids.update(id_set(session.question_ids or []))
    if other_session_ids:
        question_ids.update(id_set(db.scalars(select(Submission.question_id).where(Submission.session_id.in_(other_session_ids)))))
        question_ids.update(id_set(db.scalars(select(AnswerEvent.question_id).where(AnswerEvent.session_id.in_(other_session_ids)))))
        question_ids.update(id_set(db.scalars(select(WrongQuestionRecord.question_id).where(WrongQuestionRecord.session_id.in_(other_session_ids)))))
        question_ids.update(id_set(db.scalars(select(ReviewRecord.question_id).where(ReviewRecord.session_id.in_(other_session_ids)))))
        question_ids.update(id_set(db.scalars(select(InterventionRecord.original_question_id).where(InterventionRecord.session_id.in_(other_session_ids)))))
        question_ids.update(id_set(db.scalars(select(InterventionRecord.current_question_id).where(InterventionRecord.session_id.in_(other_session_ids)))))
    return question_ids


def learner_scoped_row_ids(db: Session, table_name: str, model, scope: dict) -> set[str]:
    rows = history_rows_for_table(db, table_name, model, sorted(scope["ai_question_ids"]), scope)
    return table_ids_from_rows(rows)


def serialize_row(row) -> dict:
    payload = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        payload[column.name] = value.isoformat() if isinstance(value, datetime) else value
    return payload


def column_accepts_datetime(column) -> bool:
    try:
        return column.type.python_type is datetime
    except NotImplementedError:
        return False


def coerce_import_row(model, row: dict, source_file_id_map: dict[str, str]) -> dict:
    columns = {column.name: column for column in model.__table__.columns}
    payload = {}
    for key, value in row.items():
        column = columns.get(key)
        if column is None:
            continue
        if value is not None and column_accepts_datetime(column) and isinstance(value, str):
            value = datetime.fromisoformat(value)
        payload[key] = value
    if model is Question and payload.get("source_file_id") in source_file_id_map:
        payload["source_file_id"] = source_file_id_map[payload["source_file_id"]]
    if model is Question and payload.get("publish_status") not in QUESTION_PUBLISH_STATUSES:
        legacy_publish_status = str(payload.get("publish_status") or "")
        if legacy_publish_status in {"pending_review", "draft", "needs_review"}:
            payload["publish_status"] = "not_published"
        elif legacy_publish_status in {"verified", "published"}:
            payload["publish_status"] = "source_verified"
        else:
            payload["publish_status"] = "not_published"
    return payload


def history_query_for_table(db: Session, table_name: str, model, ai_question_ids: list[str], scope: dict | None = None):
    stmt = select(model)
    if scope is not None:
        learner_id = str(scope["learner_id"])
        session_ids = scope["session_ids"]
        source_file_ids = scope["source_file_ids"]
        job_ids = scope["job_ids"]
        batch_ids = scope["batch_ids"]
        candidate_ids = scope["candidate_ids"]
        item_ids = scope["item_ids"]
        blueprint_ids = scope["blueprint_ids"]

        if table_name == "source_files":
            return stmt.where(SourceFile.id.in_(source_file_ids)) if source_file_ids else stmt.where(False)
        if table_name == "learner_profiles":
            return stmt.where(LearnerProfile.id == learner_id)
        if table_name == "generation_jobs":
            return stmt.where(GenerationJob.id.in_(job_ids)) if job_ids else stmt.where(False)
        if table_name == "generated_candidates":
            return stmt.where(GeneratedCandidate.id.in_(candidate_ids)) if candidate_ids else stmt.where(False)
        if table_name == "generation_batches":
            return stmt.where(GenerationBatch.id.in_(batch_ids)) if batch_ids else stmt.where(False)
        if table_name == "generation_batch_items":
            return stmt.where(GenerationBatchItem.id.in_(item_ids)) if item_ids else stmt.where(False)
        if table_name == "generation_events":
            clauses = []
            if batch_ids:
                clauses.append(and_(GenerationEvent.stream_type == "batch", GenerationEvent.stream_id.in_(batch_ids)))
            if job_ids:
                clauses.append(and_(GenerationEvent.stream_type == "job", GenerationEvent.stream_id.in_(job_ids)))
            return stmt.where(or_(*clauses)) if clauses else stmt.where(False)
        if table_name == "generated_candidate_reviews":
            return stmt.where(or_(GeneratedCandidateReview.job_id.in_(job_ids), GeneratedCandidateReview.candidate_id.in_(candidate_ids))) if job_ids or candidate_ids else stmt.where(False)
        if table_name == "generation_duration_stats":
            return stmt.where(or_(GenerationDurationStat.batch_id.in_(batch_ids), GenerationDurationStat.item_id.in_(item_ids))) if batch_ids or item_ids else stmt.where(False)
        if table_name == "practice_sessions":
            return stmt.where(PracticeSession.id.in_(session_ids)) if session_ids else stmt.where(False)
        if table_name == "test_blueprints":
            return stmt.where(TestBlueprint.id.in_(blueprint_ids)) if blueprint_ids else stmt.where(False)
        if table_name == "test_runs":
            return stmt.where(TestRun.session_id.in_(session_ids)) if session_ids else stmt.where(False)
        if table_name == "submissions":
            return stmt.where(Submission.session_id.in_(session_ids)) if session_ids else stmt.where(False)
        if table_name == "intervention_records":
            return stmt.where(InterventionRecord.session_id.in_(session_ids)) if session_ids else stmt.where(False)
        if table_name == "wrong_question_records":
            return stmt.where(WrongQuestionRecord.session_id.in_(session_ids)) if session_ids else stmt.where(False)
        if table_name == "review_records":
            return stmt.where(ReviewRecord.session_id.in_(session_ids)) if session_ids else stmt.where(False)
        if table_name == "answer_events":
            return stmt.where(AnswerEvent.session_id.in_(session_ids)) if session_ids else stmt.where(False)
        if table_name in {"report_filter_views", "report_exports", "ai_run_logs", "audit_logs"}:
            return stmt

    if table_name == "questions":
        stmt = stmt.where(Question.id.in_(ai_question_ids)) if ai_question_ids else stmt.where(False)
    elif table_name in {"question_versions", "question_repair_records", "answer_specs", "question_tags"}:
        stmt = stmt.where(model.question_id.in_(ai_question_ids)) if ai_question_ids else stmt.where(False)
    elif table_name == "question_duplicate_aliases":
        stmt = stmt.where(or_(QuestionDuplicateAlias.canonical_question_id.in_(ai_question_ids), QuestionDuplicateAlias.alias_question_id.in_(ai_question_ids))) if ai_question_ids else stmt.where(False)
    elif table_name == "question_variants":
        stmt = stmt.where(or_(QuestionVariant.base_question_id.in_(ai_question_ids), QuestionVariant.variant_question_id.in_(ai_question_ids))) if ai_question_ids else stmt.where(False)
    return stmt


def scoped_history_row(row, table_name: str, scope: dict | None) -> bool:
    if scope is None or table_name not in {"report_filter_views", "report_exports", "ai_run_logs", "audit_logs"}:
        return True
    learner_id = str(scope["learner_id"])
    scoped_ids = set()
    for key in ("session_ids", "submission_ids", "answer_event_ids", "question_ids", "ai_question_ids", "job_ids", "batch_ids", "candidate_ids", "item_ids"):
        scoped_ids.update(scope[key])
    if table_name == "ai_run_logs" and getattr(row, "job_id", None) in scope["job_ids"]:
        return True
    if table_name == "audit_logs" and getattr(row, "entity_id", None) in scoped_ids:
        return True
    payloads = []
    for attr in ("filters", "payload", "request_payload", "response_payload", "token_usage"):
        if hasattr(row, attr):
            payloads.append(getattr(row, attr) or {})
    return any(json_matches_key(payload, "learner_id", learner_id) or json_contains_any(payload, scoped_ids) for payload in payloads)


def history_rows_for_table(db: Session, table_name: str, model, ai_question_ids: list[str], scope: dict | None = None):
    rows = db.scalars(history_query_for_table(db, table_name, model, ai_question_ids, scope)).all()
    return [row for row in rows if scoped_history_row(row, table_name, scope)]


def source_conflict_filename(filename: str, sha256: str) -> str:
    digest = (sha256 or "unknown").strip() or "unknown"
    return f"{filename}#sha256-{digest[:12]}"


def source_file_conflict(existing: SourceFile, incoming_sha256: str) -> bool:
    if not incoming_sha256:
        return False
    return bool(existing.sha256 and existing.sha256 != incoming_sha256)


def stable_dict_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def question_row_conflicts(existing: Question, incoming: dict) -> bool:
    for field in ("exact_hash", "normalized_hash"):
        existing_hash = getattr(existing, field, None)
        incoming_hash = incoming.get(field)
        if existing_hash and incoming_hash and existing_hash != incoming_hash:
            return True
    incoming_stem = str(incoming.get("stem_preview") or "").strip()
    existing_stem = str(existing.stem_preview or "").strip()
    if incoming_stem and existing_stem and incoming_stem != existing_stem:
        return True
    return False


def answer_spec_row_conflicts(existing: AnswerSpec, incoming: dict) -> bool:
    if existing.answer_type and incoming.get("answer_type") and existing.answer_type != incoming.get("answer_type"):
        return True
    if existing.canonical_answer and incoming.get("canonical_answer") and existing.canonical_answer != incoming.get("canonical_answer"):
        return True
    return stable_dict_hash(existing.raw_answer_spec or {}) != stable_dict_hash(incoming.get("raw_answer_spec") or {})


def question_version_row_conflicts(db: Session, incoming: dict) -> bool:
    existing_by_id = db.get(QuestionVersion, incoming.get("id"))
    if existing_by_id is not None:
        if existing_by_id.question_id != incoming.get("question_id"):
            return True
        if existing_by_id.content_hash and incoming.get("content_hash") and existing_by_id.content_hash != incoming.get("content_hash"):
            return True
    existing_same_version = db.scalar(
        select(QuestionVersion).where(
            QuestionVersion.question_id == incoming.get("question_id"),
            QuestionVersion.version == incoming.get("version"),
        )
    )
    if existing_same_version is not None and existing_same_version.id != incoming.get("id"):
        if existing_same_version.content_hash and incoming.get("content_hash") and existing_same_version.content_hash != incoming.get("content_hash"):
            return True
    return False


def local_question_version_for_import(db: Session, incoming: dict) -> QuestionVersion | None:
    return db.scalar(
        select(QuestionVersion).where(
            QuestionVersion.question_id == incoming.get("question_id"),
            QuestionVersion.version == incoming.get("version"),
        )
    )


def import_source_files(db: Session, rows: list[dict]) -> tuple[int, dict[str, str], list[dict]]:
    imported = 0
    id_map: dict[str, str] = {}
    conflicts: list[dict] = []
    for row in rows:
        old_id = str(row.get("id") or "")
        filename = str(row.get("filename") or "")
        incoming_sha256 = str(row.get("sha256") or "")
        if not old_id or not filename:
            continue
        existing = db.scalar(select(SourceFile).where(SourceFile.filename == filename))
        if existing:
            target = existing
            conflict_filename = None
            if source_file_conflict(existing, incoming_sha256):
                conflict_filename = source_conflict_filename(filename, incoming_sha256)
                target = db.scalar(select(SourceFile).where(SourceFile.filename == conflict_filename))
                if target is None:
                    target = SourceFile(filename=conflict_filename, sha256=incoming_sha256, question_count=0)
                    db.add(target)
                    db.flush()
                conflicts.append(
                    {
                        "table": "source_files",
                        "kind": "filename_hash_conflict",
                        "filename": filename,
                        "existing_source_file_id": existing.id,
                        "existing_sha256": existing.sha256,
                        "incoming_source_file_id": old_id,
                        "incoming_sha256": incoming_sha256,
                        "mapped_source_file_id": target.id,
                        "mapped_filename": conflict_filename,
                    }
                )
            id_map[old_id] = target.id
            if incoming_sha256 and not source_file_conflict(target, incoming_sha256):
                target.sha256 = incoming_sha256
            if row.get("question_count") is not None:
                target.question_count = int(row["question_count"])
            imported += 1
            continue
        payload = coerce_import_row(SourceFile, row, {})
        db.merge(SourceFile(**payload))
        id_map[old_id] = old_id
        imported += 1
    return imported, id_map, conflicts


def question_bank_rows(db: Session, table_name: str, model):
    return db.scalars(select(model)).all()


def row_references_exist(db: Session, model, row: dict) -> bool:
    if model is Question:
        return db.get(SourceFile, row.get("source_file_id")) is not None
    if model is QuestionRepairRecord:
        if db.get(Question, row.get("question_id")) is None:
            return False
        for key in ("base_version_id", "new_version_id"):
            if row.get(key) and db.get(QuestionVersion, row.get(key)) is None:
                return False
        return True
    if model is QuestionDuplicateAlias:
        return db.get(Question, row.get("canonical_question_id")) is not None and db.get(Question, row.get("alias_question_id")) is not None
    if model in {QuestionVersion, AnswerSpec, ReviewQueueItem}:
        return db.get(Question, row.get("question_id")) is not None
    if model is QuestionTag:
        return db.get(Question, row.get("question_id")) is not None and db.get(TaxonomyTag, row.get("tag_id")) is not None
    if model is TaxonomyAlias:
        return db.get(TaxonomyTag, row.get("canonical_tag_id")) is not None
    if model is TaxonomyMigration:
        return db.get(TaxonomyTag, row.get("from_tag_id")) is not None and db.get(TaxonomyTag, row.get("to_tag_id")) is not None
    if model is TaxonomyGovernanceRecord and row.get("tag_id"):
        return db.get(TaxonomyTag, row.get("tag_id")) is not None
    if model is QuestionVariant:
        return db.get(Question, row.get("base_question_id")) is not None and db.get(Question, row.get("variant_question_id")) is not None
    return True


def row_touches_skipped_question(model, row: dict, skipped_question_ids: set[str]) -> bool:
    if not skipped_question_ids:
        return False
    if model in {QuestionVersion, QuestionRepairRecord, AnswerSpec, QuestionTag, ReviewQueueItem}:
        return str(row.get("question_id") or "") in skipped_question_ids
    if model is QuestionDuplicateAlias:
        return str(row.get("canonical_question_id") or "") in skipped_question_ids or str(row.get("alias_question_id") or "") in skipped_question_ids
    if model is QuestionVariant:
        return str(row.get("base_question_id") or "") in skipped_question_ids or str(row.get("variant_question_id") or "") in skipped_question_ids
    return False


def refresh_source_file_counts(db: Session, source_file_ids: set[str]) -> None:
    for source_file_id in source_file_ids:
        if not source_file_id:
            continue
        source = db.get(SourceFile, source_file_id)
        if source is None:
            continue
        count = db.scalar(select(func.count()).select_from(Question).where(Question.source_file_id == source_file_id))
        source.question_count = int(count)


def execute_delete(db: Session, model, where_clause=None) -> int:
    stmt = delete(model)
    if where_clause is not None:
        stmt = stmt.where(where_clause)
    result = db.execute(stmt)
    return int(result.rowcount or 0)


def count_rows(db: Session, model, where_clause=None) -> int:
    stmt = select(func.count()).select_from(model)
    if where_clause is not None:
        stmt = stmt.where(where_clause)
    return int(db.scalar(stmt) or 0)


def diagnostic_truncate(value, max_chars: int = 800):
    if isinstance(value, dict):
        return {str(key): diagnostic_truncate(item, max_chars) for key, item in value.items()}
    if isinstance(value, list):
        return [diagnostic_truncate(item, max_chars) for item in value[:50]]
    if isinstance(value, str):
        return value if len(value) <= max_chars else f"{value[:max_chars]}...[truncated {len(value) - max_chars} chars]"
    return value


SECRET_VALUE_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b", re.IGNORECASE),
    re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\b(?:xoxb|xoxp|ghp|github_pat)_[A-Za-z0-9_\-]{20,}\b", re.IGNORECASE),
)


def sensitive_value_hits(value, path: str = "$") -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            hits.extend(sensitive_value_hits(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(sensitive_value_hits(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        for pattern in SECRET_VALUE_PATTERNS:
            if pattern.search(value):
                hits.append({"path": path, "pattern": pattern.pattern})
    return hits


def sqlite_integrity_summary(db: Session) -> dict:
    if db.bind is None or db.bind.dialect.name != "sqlite":
        return {"checked": False, "reason": "non_sqlite_database"}
    try:
        integrity = db.execute(text("PRAGMA integrity_check")).scalar()
        journal_mode = db.execute(text("PRAGMA journal_mode")).scalar()
        busy_timeout = db.execute(text("PRAGMA busy_timeout")).scalar()
        foreign_keys = db.execute(text("PRAGMA foreign_keys")).scalar()
        return {
            "checked": True,
            "ok": integrity == "ok",
            "messages": [str(integrity)],
            "journal_mode": str(journal_mode),
            "busy_timeout": int(busy_timeout or 0),
            "foreign_keys": int(foreign_keys or 0),
        }
    except Exception as exc:
        return {"checked": True, "ok": False, "error": str(exc)}


def diagnostic_table_counts(db: Session) -> dict[str, int]:
    return {name: count_rows(db, model) for name, model in HISTORY_TABLES.items()}


def diagnostic_recent_audit_logs(db: Session, limit: int = 50) -> list[dict]:
    rows = list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)))
    return [
        {
            "id": row.id,
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "created_at": row.created_at.isoformat(),
            "payload": diagnostic_truncate(sanitize_payload(row.payload or {}), 500),
        }
        for row in rows
    ]


def diagnostic_recent_ai_logs(db: Session, limit: int = 50) -> list[dict]:
    rows = list(db.scalars(select(AiRunLog).order_by(AiRunLog.created_at.desc(), AiRunLog.id.desc()).limit(limit)))
    summaries = []
    for row in rows:
        trace = ((row.request_payload or {}).get("trace") if isinstance(row.request_payload, dict) else None) or ((row.token_usage or {}).get("trace") if isinstance(row.token_usage, dict) else None) or {}
        summaries.append(
            {
                "id": row.id,
                "job_id": row.job_id,
                "trace_id": trace.get("trace_id"),
                "span_id": trace.get("span_id"),
                "parent_span_id": trace.get("parent_span_id"),
                "operation": trace.get("operation"),
                "role": row.role,
                "provider": row.provider,
                "model": row.model,
                "prompt_version": row.prompt_version,
                "input_hash": row.input_hash,
                "has_error": bool(row.error),
                "error": diagnostic_truncate(row.error, 500),
                "latency_ms": row.latency_ms,
                "token_usage": diagnostic_truncate(sanitize_payload(row.token_usage or {}), 500),
                "created_at": row.created_at.isoformat(),
            }
        )
    return summaries


def diagnostic_recent_request_logs(limit: int = 50) -> list[dict]:
    settings = get_settings()
    return diagnostic_truncate(recent_request_logs(request_log_path(settings.api_request_log_path), limit), 500)


def diagnostic_failed_generation(db: Session, limit: int = 30) -> dict:
    job_statuses = tuple(FAILED_GENERATION_STATUSES)
    batch_statuses = tuple(status for status in FAILED_GENERATION_STATUSES if status != STATUS_REJECTED)
    jobs = list(db.scalars(select(GenerationJob).where(GenerationJob.status.in_(job_statuses)).order_by(GenerationJob.created_at.desc(), GenerationJob.id.desc()).limit(limit)))
    batches = list(db.scalars(select(GenerationBatch).where(GenerationBatch.status.in_(batch_statuses)).order_by(GenerationBatch.created_at.desc(), GenerationBatch.id.desc()).limit(limit)))
    return {
        "jobs": [
            {
                "id": row.id,
                "status": row.status,
                "accepted_question_id": row.accepted_question_id,
                "progress": diagnostic_truncate(sanitize_payload(row.progress or {}), 800),
                "created_at": row.created_at.isoformat(),
            }
            for row in jobs
        ],
        "batches": [
            {
                "id": row.id,
                "status": row.status,
                "target_count": row.target_count,
                "accepted_count": row.accepted_count,
                "progress": diagnostic_truncate(sanitize_payload(row.progress or {}), 800),
                "created_at": row.created_at.isoformat(),
            }
            for row in batches
        ],
    }


def diagnostic_config_summary(db: Session) -> dict:
    settings = get_settings()
    return {
        "app_name": settings.app_name,
        "database_kind": settings.database_url.split(":", 1)[0],
        "redis_configured": bool(settings.redis_url),
        "model_provider": settings.model_provider,
        "base_urls": {
            "liaobots": settings.liaobots_base_url,
            "responses": settings.responses_base_url,
            "anthropic": settings.anthropic_base_url,
            "gemini": settings.gemini_base_url,
        },
        "has_api_key": {
            "liaobots": bool(settings.liaobots_api_key) or has_secret(db, "LIAOBOTS_API_KEY"),
            "responses_env": bool(settings.responses_api_key),
            "anthropic_env": bool(settings.anthropic_api_key),
            "gemini_env": bool(settings.gemini_api_key),
        },
        "models": {
            "default": settings.default_model,
            "planner": settings.planner_model,
            "generator": settings.generator_model,
            "generator_le_3": settings.generator_model_le_3,
            "generator_4": settings.generator_model_4,
            "generator_5": settings.generator_model_5,
            "reviewer": settings.reviewer_model,
            "reporter": settings.reporter_model,
            "answer_judge": settings.answer_judge_model,
            "solution_explainer": settings.solution_explainer_model,
        },
        "timeouts_seconds": {
            "interaction": settings.ai_interaction_timeout_seconds,
            "generator": settings.ai_generator_timeout_seconds,
            "reviewer": settings.ai_reviewer_timeout_seconds,
            "job": settings.ai_job_timeout_seconds,
        },
        "ai_global_max_concurrency": settings.ai_global_max_concurrency,
        "request_logging": {
            "path": settings.api_request_log_path,
            "max_bytes": settings.api_request_log_max_bytes,
            "backup_count": settings.api_request_log_backup_count,
            "retention_days": settings.api_request_log_retention_days,
            "slow_request_ms": settings.api_slow_request_ms,
        },
    }


def diagnostic_disk_summary(root: Path) -> dict:
    usage = shutil.disk_usage(root)
    return {
        "root": str(root),
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
    }


def build_diagnostic_bundle(db: Session) -> dict:
    root = project_root()
    release = release_info(root)
    bundle = {
        "schema_version": "math_platform_api_diagnostic_bundle_v1",
        "generated_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "pid": os.getpid(),
        },
        "paths": {
            "project_root": str(root),
            "cwd": str(Path.cwd()),
            "release_manifest_exists": release["manifest_exists"],
        },
        "release": release,
        "config": diagnostic_config_summary(db),
        "database": {
            "integrity": sqlite_integrity_summary(db),
            "table_counts": diagnostic_table_counts(db),
        },
        "disk": diagnostic_disk_summary(root),
        "generation": diagnostic_failed_generation(db),
        "recent_audit_logs": diagnostic_recent_audit_logs(db),
        "recent_ai_logs": diagnostic_recent_ai_logs(db),
        "recent_request_logs": diagnostic_recent_request_logs(),
        "included": [
            "sanitized_config_summary",
            "sqlite_integrity_summary",
            "table_counts",
            "failed_generation_snapshots",
            "recent_sanitized_audit_logs",
            "recent_ai_run_log_summaries",
            "recent_api_request_log_summaries",
            "disk_summary",
            "release_manifest_summary",
        ],
        "excluded": [
            "api_keys",
            "encrypted_config_ciphertexts",
            "full_ai_request_payloads",
            "full_ai_response_payloads",
            "full_student_free_text",
            "full_question_snapshots",
        ],
    }
    hits = sensitive_value_hits(bundle)
    bundle["redaction_scan"] = {
        "ok": not hits,
        "hit_count": len(hits),
        "hits": hits[:20],
    }
    return bundle


@router.get("/release")
def get_release_info() -> dict:
    return release_info(project_root())


def destructive_target_fingerprint(action: str, details: dict) -> str:
    return stable_dict_hash({"action": action, "details": details})


def clear_history_impact(db: Session) -> dict:
    generated_ids = sorted(generated_question_ids(db))
    counts: dict[str, int] = {
        "review_records": count_rows(db, ReviewRecord),
        "intervention_records": count_rows(db, InterventionRecord),
        "wrong_question_records": count_rows(db, WrongQuestionRecord),
        "answer_events": count_rows(db, AnswerEvent),
        "submissions": count_rows(db, Submission),
        "test_runs": count_rows(db, TestRun),
        "practice_sessions": count_rows(db, PracticeSession),
        "report_exports": count_rows(db, ReportExport),
        "report_filter_views": count_rows(db, ReportFilterView),
        "generated_candidate_reviews": count_rows(db, GeneratedCandidateReview),
        "ai_run_logs": count_rows(db, AiRunLog),
        "assistant_tool_runs": count_rows(db, AssistantToolRun),
        "assistant_messages": count_rows(db, AssistantMessage),
        "assistant_conversations": count_rows(db, AssistantConversation),
        "recovery_agent_runs": count_rows(db, RecoveryAgentRun),
        "system_tasks": count_rows(db, SystemTask),
        "generation_duration_stats": count_rows(db, GenerationDurationStat),
        "generation_events": count_rows(db, GenerationEvent),
        "generation_batch_items": count_rows(db, GenerationBatchItem),
        "generated_candidates": count_rows(db, GeneratedCandidate),
        "generation_jobs": count_rows(db, GenerationJob),
        "generation_batches": count_rows(db, GenerationBatch),
    }
    if generated_ids:
        counts["generated_question_variants"] = count_rows(db, QuestionVariant, or_(QuestionVariant.base_question_id.in_(generated_ids), QuestionVariant.variant_question_id.in_(generated_ids)))
        counts["generated_answer_specs"] = count_rows(db, AnswerSpec, AnswerSpec.question_id.in_(generated_ids))
        counts["generated_question_tags"] = count_rows(db, QuestionTag, QuestionTag.question_id.in_(generated_ids))
        counts["generated_question_versions"] = count_rows(db, QuestionVersion, QuestionVersion.question_id.in_(generated_ids))
        counts["generated_questions"] = count_rows(db, Question, Question.id.in_(generated_ids))
    else:
        counts["generated_question_variants"] = 0
        counts["generated_answer_specs"] = 0
        counts["generated_question_tags"] = 0
        counts["generated_question_versions"] = 0
        counts["generated_questions"] = 0
    details = {
        "counts": counts,
        "generated_question_ids": generated_ids,
    }
    return {
        "target_count": sum(counts.values()),
        "target_fingerprint": destructive_target_fingerprint("clear_history", details),
        "details": details,
    }


def retire_questions_impact(db: Session, question_ids: list[str]) -> dict:
    requested_ids = sorted(set(question_ids))
    questions = list(db.scalars(select(Question).where(Question.id.in_(requested_ids)))) if requested_ids else []
    question_snapshots = sorted(
        (
            {
                "id": question.id,
                "status": question.status,
                "eligible_for_practice": bool(question.eligible_for_practice),
                "eligible_for_test": bool(question.eligible_for_test),
                "publish_status": question.publish_status,
            }
            for question in questions
        ),
        key=lambda item: item["id"],
    )
    details = {
        "requested_question_ids": requested_ids,
        "existing_questions": question_snapshots,
    }
    return {
        "target_count": len(question_snapshots),
        "target_fingerprint": destructive_target_fingerprint("retire_questions", details),
        "details": details,
    }


def delete_learner_impact(db: Session, learner_id: str | None) -> dict:
    requested_learner_id = str(learner_id or "").strip()
    if not requested_learner_id:
        raise HTTPException(status_code=400, detail="learner_id required")
    if requested_learner_id == DEFAULT_LEARNER_ID:
        raise HTTPException(status_code=409, detail="default learner cannot be deleted")
    learner = db.get(LearnerProfile, requested_learner_id)
    if learner is None:
        raise HTTPException(status_code=404, detail="learner profile not found")

    scope = build_history_export_scope(db, requested_learner_id)
    ai_question_ids = set(scope["ai_question_ids"])
    other_used_question_ids = question_ids_used_by_other_learners(db, requested_learner_id)
    exclusive_ai_question_ids = sorted(ai_question_ids - other_used_question_ids)
    shared_ai_question_ids = sorted(ai_question_ids.intersection(other_used_question_ids))

    scoped_row_ids = {
        "report_exports": learner_scoped_row_ids(db, "report_exports", ReportExport, scope),
        "report_filter_views": learner_scoped_row_ids(db, "report_filter_views", ReportFilterView, scope),
        "ai_run_logs": learner_scoped_row_ids(db, "ai_run_logs", AiRunLog, scope),
        "audit_logs": learner_scoped_row_ids(db, "audit_logs", AuditLog, scope),
    }

    counts: dict[str, int] = {
        "learner_profiles": 1,
        "review_records": len(scope["session_ids"]) and count_rows(db, ReviewRecord, ReviewRecord.session_id.in_(scope["session_ids"])) or 0,
        "intervention_records": len(scope["session_ids"]) and count_rows(db, InterventionRecord, InterventionRecord.session_id.in_(scope["session_ids"])) or 0,
        "wrong_question_records": len(scope["session_ids"]) and count_rows(db, WrongQuestionRecord, WrongQuestionRecord.session_id.in_(scope["session_ids"])) or 0,
        "answer_events": len(scope["session_ids"]) and count_rows(db, AnswerEvent, AnswerEvent.session_id.in_(scope["session_ids"])) or 0,
        "submissions": len(scope["session_ids"]) and count_rows(db, Submission, Submission.session_id.in_(scope["session_ids"])) or 0,
        "test_runs": len(scope["session_ids"]) and count_rows(db, TestRun, TestRun.session_id.in_(scope["session_ids"])) or 0,
        "practice_sessions": len(scope["session_ids"]) and count_rows(db, PracticeSession, PracticeSession.id.in_(scope["session_ids"])) or 0,
        "report_exports": len(scoped_row_ids["report_exports"]),
        "report_filter_views": len(scoped_row_ids["report_filter_views"]),
        "generated_candidate_reviews": (len(scope["job_ids"]) or len(scope["candidate_ids"])) and count_rows(db, GeneratedCandidateReview, or_(GeneratedCandidateReview.job_id.in_(scope["job_ids"]), GeneratedCandidateReview.candidate_id.in_(scope["candidate_ids"]))) or 0,
        "ai_run_logs": len(scoped_row_ids["ai_run_logs"]),
        "generation_duration_stats": (len(scope["batch_ids"]) or len(scope["item_ids"])) and count_rows(db, GenerationDurationStat, or_(GenerationDurationStat.batch_id.in_(scope["batch_ids"]), GenerationDurationStat.item_id.in_(scope["item_ids"]))) or 0,
        "generation_events": 0,
        "generation_batch_items": len(scope["item_ids"]),
        "generated_candidates": len(scope["candidate_ids"]),
        "generation_jobs": len(scope["job_ids"]),
        "generation_batches": len(scope["batch_ids"]),
        "audit_logs": len(scoped_row_ids["audit_logs"]),
        "exclusive_generated_question_variants": 0,
        "exclusive_generated_question_repairs": 0,
        "exclusive_generated_question_duplicate_aliases": 0,
        "exclusive_generated_review_queue_items": 0,
        "exclusive_generated_answer_specs": 0,
        "exclusive_generated_question_tags": 0,
        "exclusive_generated_question_versions": 0,
        "exclusive_generated_questions": 0,
    }
    event_clauses = []
    if scope["batch_ids"]:
        event_clauses.append(and_(GenerationEvent.stream_type == "batch", GenerationEvent.stream_id.in_(scope["batch_ids"])))
    if scope["job_ids"]:
        event_clauses.append(and_(GenerationEvent.stream_type == "job", GenerationEvent.stream_id.in_(scope["job_ids"])))
    counts["generation_events"] = count_rows(db, GenerationEvent, or_(*event_clauses)) if event_clauses else 0

    if exclusive_ai_question_ids:
        counts["exclusive_generated_question_variants"] = count_rows(db, QuestionVariant, or_(QuestionVariant.base_question_id.in_(exclusive_ai_question_ids), QuestionVariant.variant_question_id.in_(exclusive_ai_question_ids)))
        counts["exclusive_generated_question_repairs"] = count_rows(db, QuestionRepairRecord, QuestionRepairRecord.question_id.in_(exclusive_ai_question_ids))
        counts["exclusive_generated_question_duplicate_aliases"] = count_rows(db, QuestionDuplicateAlias, or_(QuestionDuplicateAlias.canonical_question_id.in_(exclusive_ai_question_ids), QuestionDuplicateAlias.alias_question_id.in_(exclusive_ai_question_ids)))
        counts["exclusive_generated_review_queue_items"] = count_rows(db, ReviewQueueItem, ReviewQueueItem.question_id.in_(exclusive_ai_question_ids))
        counts["exclusive_generated_answer_specs"] = count_rows(db, AnswerSpec, AnswerSpec.question_id.in_(exclusive_ai_question_ids))
        counts["exclusive_generated_question_tags"] = count_rows(db, QuestionTag, QuestionTag.question_id.in_(exclusive_ai_question_ids))
        counts["exclusive_generated_question_versions"] = count_rows(db, QuestionVersion, QuestionVersion.question_id.in_(exclusive_ai_question_ids))
        counts["exclusive_generated_questions"] = count_rows(db, Question, Question.id.in_(exclusive_ai_question_ids))

    details = {
        "learner": {"id": learner.id, "name": learner.name, "status": learner.status},
        "scope": {key: sorted(value) if isinstance(value, set) else value for key, value in scope.items()},
        "exclusive_ai_question_ids": exclusive_ai_question_ids,
        "shared_ai_question_ids": shared_ai_question_ids,
        "scoped_row_ids": {key: sorted(value) for key, value in scoped_row_ids.items()},
        "counts": counts,
    }
    return {
        "target_count": sum(counts.values()),
        "target_fingerprint": destructive_target_fingerprint("delete_learner", details),
        "details": details,
    }


def challenge_impact_for_request(payload: DestructiveChallengeRequest, db: Session) -> dict:
    if payload.action == "clear_history":
        return clear_history_impact(db)
    if payload.action == "delete_learner":
        return delete_learner_impact(db, payload.learner_id)
    if payload.question_ids:
        return retire_questions_impact(db, payload.question_ids)
    target_count = int(payload.target_count or 0)
    details = {"requested_question_ids": [], "target_count": target_count}
    return {
        "target_count": target_count,
        "target_fingerprint": destructive_target_fingerprint(payload.action, details),
        "details": details,
    }


def finish_import_transaction(db: Session, dry_run: bool) -> str:
    if dry_run:
        db.rollback()
        return "dry_run"
    db.commit()
    return "imported"


def cleanup_expired_challenges() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    expired_ids = [
        challenge_id
        for challenge_id, challenge in DESTRUCTIVE_CHALLENGES.items()
        if datetime.fromisoformat(challenge["expires_at"]) <= now
    ]
    for challenge_id in expired_ids:
        DESTRUCTIVE_CHALLENGES.pop(challenge_id, None)


def generate_challenge_phrase(action: str, target_count: int) -> str:
    random_part = "".join(choice(CHALLENGE_ALPHABET) for _ in range(8))
    return f"{action.replace('_', '-')}-{target_count}-{random_part}"


def validate_destructive_challenge(action: str, challenge_id: str | None, challenge_phrase: str | None, impact: dict) -> None:
    cleanup_expired_challenges()
    if not challenge_id or not challenge_phrase:
        raise HTTPException(status_code=400, detail="destructive challenge required")
    challenge = DESTRUCTIVE_CHALLENGES.pop(challenge_id, None)
    if challenge is None:
        raise HTTPException(status_code=400, detail="destructive challenge expired or already used")
    if challenge.get("action") != action or challenge.get("phrase") != challenge_phrase:
        raise HTTPException(status_code=400, detail="destructive challenge mismatch")
    if int(challenge.get("target_count") or -1) != int(impact["target_count"]) or challenge.get("target_fingerprint") != impact["target_fingerprint"]:
        raise HTTPException(status_code=409, detail="destructive challenge target changed")


@router.post("/destructive-challenges", response_model=DestructiveChallengeResponse)
def create_destructive_challenge(payload: DestructiveChallengeRequest, db: Session = Depends(get_db)) -> DestructiveChallengeResponse:
    cleanup_expired_challenges()
    challenge_id = token_urlsafe(18)
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=CHALLENGE_TTL_SECONDS)
    impact = challenge_impact_for_request(payload, db)
    phrase = generate_challenge_phrase(payload.action, int(impact["target_count"]))
    DESTRUCTIVE_CHALLENGES[challenge_id] = {
        "action": payload.action,
        "phrase": phrase,
        "expires_at": expires_at.isoformat(),
        "target_count": str(impact["target_count"]),
        "target_fingerprint": impact["target_fingerprint"],
    }
    return DestructiveChallengeResponse(
        challenge_id=challenge_id,
        action=payload.action,
        phrase=phrase,
        expires_at=expires_at.isoformat(),
        target_count=int(impact["target_count"]),
        target_fingerprint=impact["target_fingerprint"],
    )


@router.post("/clear-history", response_model=ClearHistoryResponse)
def clear_history(payload: ClearHistoryRequest, db: Session = Depends(get_db)) -> ClearHistoryResponse:
    impact = clear_history_impact(db)
    validate_destructive_challenge("clear_history", payload.challenge_id, payload.challenge_phrase, impact)

    generated_ids = impact["details"]["generated_question_ids"]
    counts: dict[str, int] = {}
    counts["review_records"] = execute_delete(db, ReviewRecord)
    counts["intervention_records"] = execute_delete(db, InterventionRecord)
    counts["wrong_question_records"] = execute_delete(db, WrongQuestionRecord)
    counts["answer_events"] = execute_delete(db, AnswerEvent)
    counts["submissions"] = execute_delete(db, Submission)
    counts["test_runs"] = execute_delete(db, TestRun)
    counts["practice_sessions"] = execute_delete(db, PracticeSession)
    counts["report_exports"] = execute_delete(db, ReportExport)
    counts["report_filter_views"] = execute_delete(db, ReportFilterView)
    counts["generated_candidate_reviews"] = execute_delete(db, GeneratedCandidateReview)
    counts["ai_run_logs"] = execute_delete(db, AiRunLog)
    counts["assistant_tool_runs"] = execute_delete(db, AssistantToolRun)
    counts["assistant_messages"] = execute_delete(db, AssistantMessage)
    counts["assistant_conversations"] = execute_delete(db, AssistantConversation)
    counts["recovery_agent_runs"] = execute_delete(db, RecoveryAgentRun)
    counts["system_tasks"] = execute_delete(db, SystemTask)
    counts["generation_duration_stats"] = execute_delete(db, GenerationDurationStat)
    counts["generation_events"] = execute_delete(db, GenerationEvent)
    counts["generation_batch_items"] = execute_delete(db, GenerationBatchItem)
    counts["generated_candidates"] = execute_delete(db, GeneratedCandidate)
    counts["generation_jobs"] = execute_delete(db, GenerationJob)
    counts["generation_batches"] = execute_delete(db, GenerationBatch)

    if generated_ids:
        counts["generated_question_variants"] = execute_delete(db, QuestionVariant, or_(QuestionVariant.base_question_id.in_(generated_ids), QuestionVariant.variant_question_id.in_(generated_ids)))
        counts["generated_answer_specs"] = execute_delete(db, AnswerSpec, AnswerSpec.question_id.in_(generated_ids))
        counts["generated_question_tags"] = execute_delete(db, QuestionTag, QuestionTag.question_id.in_(generated_ids))
        counts["generated_question_versions"] = execute_delete(db, QuestionVersion, QuestionVersion.question_id.in_(generated_ids))
        counts["generated_questions"] = execute_delete(db, Question, Question.id.in_(generated_ids))
    else:
        counts["generated_question_variants"] = 0
        counts["generated_answer_specs"] = 0
        counts["generated_question_tags"] = 0
        counts["generated_question_versions"] = 0
        counts["generated_questions"] = 0
    ai_source = db.scalar(select(SourceFile).where(SourceFile.filename == "__ai_generated__"))
    if ai_source is not None:
        refresh_source_file_counts(db, {ai_source.id})
    log_audit_event(db, "admin.clear_history", "local_history", None, {"deleted_counts": counts, "preserved": ["source_files", "imported_questions", "question_versions", "answer_specs", "taxonomy_tags"]})
    db.commit()
    return ClearHistoryResponse(
        status="cleared",
        deleted_counts=counts,
        preserved=["source_files", "imported_questions", "imported_question_versions", "imported_answer_specs", "taxonomy_tags"],
    )


@router.post("/learners/{learner_id}/delete", response_model=LearnerDeleteResponse)
def delete_learner_data(learner_id: str, payload: LearnerDeleteRequest, db: Session = Depends(get_db)) -> LearnerDeleteResponse:
    impact = delete_learner_impact(db, learner_id)
    validate_destructive_challenge("delete_learner", payload.challenge_id, payload.challenge_phrase, impact)

    details = impact["details"]
    scope = details["scope"]
    scoped_row_ids = details["scoped_row_ids"]
    session_ids = set(scope["session_ids"])
    job_ids = set(scope["job_ids"])
    batch_ids = set(scope["batch_ids"])
    candidate_ids = set(scope["candidate_ids"])
    item_ids = set(scope["item_ids"])
    exclusive_ai_question_ids = set(details["exclusive_ai_question_ids"])
    shared_ai_question_ids = set(details["shared_ai_question_ids"])

    counts: dict[str, int] = {}
    counts["review_records"] = execute_delete(db, ReviewRecord, ReviewRecord.session_id.in_(session_ids)) if session_ids else 0
    counts["intervention_records"] = execute_delete(db, InterventionRecord, InterventionRecord.session_id.in_(session_ids)) if session_ids else 0
    counts["wrong_question_records"] = execute_delete(db, WrongQuestionRecord, WrongQuestionRecord.session_id.in_(session_ids)) if session_ids else 0
    counts["answer_events"] = execute_delete(db, AnswerEvent, AnswerEvent.session_id.in_(session_ids)) if session_ids else 0
    counts["submissions"] = execute_delete(db, Submission, Submission.session_id.in_(session_ids)) if session_ids else 0
    counts["test_runs"] = execute_delete(db, TestRun, TestRun.session_id.in_(session_ids)) if session_ids else 0
    counts["practice_sessions"] = execute_delete(db, PracticeSession, PracticeSession.id.in_(session_ids)) if session_ids else 0

    report_export_ids = set(scoped_row_ids["report_exports"])
    report_view_ids = set(scoped_row_ids["report_filter_views"])
    counts["report_exports"] = execute_delete(db, ReportExport, ReportExport.id.in_(report_export_ids)) if report_export_ids else 0
    counts["report_filter_views"] = execute_delete(db, ReportFilterView, ReportFilterView.id.in_(report_view_ids)) if report_view_ids else 0

    counts["generated_candidate_reviews"] = execute_delete(db, GeneratedCandidateReview, or_(GeneratedCandidateReview.job_id.in_(job_ids), GeneratedCandidateReview.candidate_id.in_(candidate_ids))) if job_ids or candidate_ids else 0
    ai_run_log_ids = set(scoped_row_ids["ai_run_logs"])
    counts["ai_run_logs"] = execute_delete(db, AiRunLog, AiRunLog.id.in_(ai_run_log_ids)) if ai_run_log_ids else 0
    counts["generation_duration_stats"] = execute_delete(db, GenerationDurationStat, or_(GenerationDurationStat.batch_id.in_(batch_ids), GenerationDurationStat.item_id.in_(item_ids))) if batch_ids or item_ids else 0
    event_clauses = []
    if batch_ids:
        event_clauses.append(and_(GenerationEvent.stream_type == "batch", GenerationEvent.stream_id.in_(batch_ids)))
    if job_ids:
        event_clauses.append(and_(GenerationEvent.stream_type == "job", GenerationEvent.stream_id.in_(job_ids)))
    counts["generation_events"] = execute_delete(db, GenerationEvent, or_(*event_clauses)) if event_clauses else 0
    counts["generation_batch_items"] = execute_delete(db, GenerationBatchItem, GenerationBatchItem.id.in_(item_ids)) if item_ids else 0
    counts["generated_candidates"] = execute_delete(db, GeneratedCandidate, GeneratedCandidate.id.in_(candidate_ids)) if candidate_ids else 0
    counts["generation_jobs"] = execute_delete(db, GenerationJob, GenerationJob.id.in_(job_ids)) if job_ids else 0
    counts["generation_batches"] = execute_delete(db, GenerationBatch, GenerationBatch.id.in_(batch_ids)) if batch_ids else 0

    if exclusive_ai_question_ids:
        counts["exclusive_generated_question_variants"] = execute_delete(db, QuestionVariant, or_(QuestionVariant.base_question_id.in_(exclusive_ai_question_ids), QuestionVariant.variant_question_id.in_(exclusive_ai_question_ids)))
        counts["exclusive_generated_question_repairs"] = execute_delete(db, QuestionRepairRecord, QuestionRepairRecord.question_id.in_(exclusive_ai_question_ids))
        counts["exclusive_generated_question_duplicate_aliases"] = execute_delete(db, QuestionDuplicateAlias, or_(QuestionDuplicateAlias.canonical_question_id.in_(exclusive_ai_question_ids), QuestionDuplicateAlias.alias_question_id.in_(exclusive_ai_question_ids)))
        counts["exclusive_generated_review_queue_items"] = execute_delete(db, ReviewQueueItem, ReviewQueueItem.question_id.in_(exclusive_ai_question_ids))
        counts["exclusive_generated_answer_specs"] = execute_delete(db, AnswerSpec, AnswerSpec.question_id.in_(exclusive_ai_question_ids))
        counts["exclusive_generated_question_tags"] = execute_delete(db, QuestionTag, QuestionTag.question_id.in_(exclusive_ai_question_ids))
        counts["exclusive_generated_question_versions"] = execute_delete(db, QuestionVersion, QuestionVersion.question_id.in_(exclusive_ai_question_ids))
        counts["exclusive_generated_questions"] = execute_delete(db, Question, Question.id.in_(exclusive_ai_question_ids))
    else:
        counts["exclusive_generated_question_variants"] = 0
        counts["exclusive_generated_question_repairs"] = 0
        counts["exclusive_generated_question_duplicate_aliases"] = 0
        counts["exclusive_generated_review_queue_items"] = 0
        counts["exclusive_generated_answer_specs"] = 0
        counts["exclusive_generated_question_tags"] = 0
        counts["exclusive_generated_question_versions"] = 0
        counts["exclusive_generated_questions"] = 0

    audit_log_ids = set(scoped_row_ids["audit_logs"])
    counts["audit_logs"] = execute_delete(db, AuditLog, AuditLog.id.in_(audit_log_ids)) if audit_log_ids else 0
    counts["learner_profiles"] = execute_delete(db, LearnerProfile, LearnerProfile.id == learner_id)

    ai_source = db.scalar(select(SourceFile).where(SourceFile.filename == "__ai_generated__"))
    if ai_source is not None:
        refresh_source_file_counts(db, {ai_source.id})
    skipped = {
        "shared_ai_question_ids": sorted(shared_ai_question_ids),
        "shared_ai_question_count": len(shared_ai_question_ids),
    }
    log_audit_event(
        db,
        "admin.learner_deleted",
        "learner_profile",
        learner_id,
        {
            "deleted_counts": counts,
            "skipped": skipped,
            "preserved": ["source_files", "imported_questions", "taxonomy_tags", "shared_ai_questions"],
        },
    )
    db.commit()
    return LearnerDeleteResponse(
        status="deleted",
        learner_id=learner_id,
        deleted_counts=counts,
        preserved=["source_files", "imported_questions", "taxonomy_tags", "shared_ai_questions"],
        skipped=skipped,
    )


@router.get("/diagnostics/export")
def export_diagnostic_bundle(db: Session = Depends(get_db)) -> dict:
    bundle = build_diagnostic_bundle(db)
    log_audit_event(
        db,
        "admin.diagnostic_bundle_exported",
        "diagnostic_bundle",
        None,
        {
            "schema_version": bundle["schema_version"],
            "redaction_scan_ok": bundle["redaction_scan"]["ok"],
            "table_count_keys": sorted((bundle.get("database") or {}).get("table_counts", {}).keys()),
        },
    )
    db.commit()
    return bundle


@router.get("/history/export", response_model=HistoryTransfer)
def export_history(db: Session = Depends(get_db), learner_id: str | None = None) -> HistoryTransfer:
    scope = None
    filters: dict[str, str] = {}
    if learner_id is not None:
        resolved_learner_id = resolve_learner_id(db, learner_id)
        scope = build_history_export_scope(db, resolved_learner_id)
        ai_question_ids = sorted(scope["ai_question_ids"])
        filters["learner_id"] = resolved_learner_id
    else:
        ai_question_ids = generated_question_ids(db)
    tables: dict[str, list[dict]] = {}
    for table_name, model in HISTORY_TABLES.items():
        rows = history_rows_for_table(db, table_name, model, ai_question_ids, scope)
        tables[table_name] = [serialize_row(row) for row in rows]
    table_counts = {key: len(value) for key, value in tables.items()}
    log_audit_event(db, "admin.history_exported", "local_history", filters.get("learner_id"), {"filters": filters, "table_counts": table_counts})
    db.commit()
    return HistoryTransfer(schema_version="history_transfer_v1", exported_at=datetime.now(UTC).replace(tzinfo=None).isoformat(), filters=filters, table_counts=table_counts, tables=tables)


@router.post("/history/import", response_model=HistoryImportResponse)
def import_history(payload: HistoryTransfer, dry_run: bool = False, db: Session = Depends(get_db)) -> HistoryImportResponse:
    if payload.schema_version != "history_transfer_v1":
        raise HTTPException(status_code=400, detail="unsupported history transfer schema")
    if dry_run:
        return HistoryImportResponse(**preview_import(db, payload, HISTORY_TABLES, question_bank=False))
    import_guard = acquire_import_guard(db)
    imported_counts: dict[str, int] = {}
    skipped_tables: list[str] = []
    skipped_rows: dict[str, int] = {}
    conflict_report: list[dict] = []
    source_file_id_map: dict[str, str] = {}

    try:
        if "source_files" in payload.tables:
            imported_counts["source_files"], source_file_id_map, conflict_report = import_source_files(db, payload.tables.get("source_files") or [])
            db.flush()

        for table_name, model in HISTORY_TABLES.items():
            if table_name == "source_files":
                continue
            rows = payload.tables.get(table_name)
            if rows is None:
                skipped_tables.append(table_name)
                continue
            count = 0
            skipped = 0
            for row in rows:
                if not isinstance(row, dict) or not row.get("id"):
                    skipped += 1
                    continue
                if model is QuestionVariant and (
                    db.get(Question, row.get("base_question_id")) is None
                    or db.get(Question, row.get("variant_question_id")) is None
                ):
                    skipped += 1
                    continue
                data = coerce_import_row(model, row, source_file_id_map)
                db.merge(model(**data))
                count += 1
            imported_counts[table_name] = count
            if skipped:
                skipped_rows[table_name] = skipped
            db.flush()

        log_audit_event(db, "admin.history_imported", "local_history", None, {"imported_counts": imported_counts, "skipped_tables": skipped_tables, "skipped_rows": skipped_rows, "conflict_report": conflict_report, "dry_run": dry_run})
        status = finish_import_transaction(db, dry_run)
        return HistoryImportResponse(status=status, imported_counts=imported_counts, skipped_tables=skipped_tables, skipped_rows=skipped_rows, conflict_report=conflict_report, dry_run=dry_run)
    except Exception:
        db.rollback()
        raise
    finally:
        import_guard.release()


@router.get("/question-bank/export", response_model=QuestionBankTransfer)
def export_question_bank(db: Session = Depends(get_db)) -> QuestionBankTransfer:
    tables: dict[str, list[dict]] = {}
    for table_name, model in QUESTION_BANK_TABLES.items():
        rows = question_bank_rows(db, table_name, model)
        tables[table_name] = [serialize_row(row) for row in rows]
    table_counts = {key: len(value) for key, value in tables.items()}
    log_audit_event(db, "admin.question_bank_exported", "question_bank", None, {"table_counts": table_counts})
    db.commit()
    return QuestionBankTransfer(
        exported_at=datetime.now(UTC).replace(tzinfo=None).isoformat(),
        filters={},
        table_counts=table_counts,
        tables=tables,
    )


@router.post("/question-bank/import", response_model=QuestionBankImportResponse)
def import_question_bank(payload: QuestionBankTransfer, dry_run: bool = False, db: Session = Depends(get_db)) -> QuestionBankImportResponse:
    if payload.schema_version != "question_bank_transfer_v1":
        raise HTTPException(status_code=400, detail="unsupported question bank transfer schema")
    if dry_run:
        return QuestionBankImportResponse(**preview_import(db, payload, QUESTION_BANK_TABLES, question_bank=True))
    import_guard = acquire_import_guard(db)

    imported_counts: dict[str, int] = {}
    skipped_tables: list[str] = []
    skipped_rows: dict[str, int] = {}
    conflict_report: list[dict] = []
    source_file_id_map: dict[str, str] = {}
    question_version_id_map: dict[str, str] = {}
    touched_source_file_ids: set[str] = set()
    skipped_question_ids: set[str] = set()

    try:
        if "source_files" in payload.tables:
            imported_counts["source_files"], source_file_id_map, conflict_report = import_source_files(db, payload.tables.get("source_files") or [])
            touched_source_file_ids.update(source_file_id_map.values())
            db.flush()

        for table_name in QUESTION_BANK_IMPORT_ORDER:
            if table_name == "source_files":
                continue
            model = QUESTION_BANK_TABLES[table_name]
            rows = payload.tables.get(table_name)
            if rows is None:
                skipped_tables.append(table_name)
                continue
            count = 0
            skipped = 0
            for row in rows:
                if not isinstance(row, dict) or not row.get("id"):
                    skipped += 1
                    continue
                data = coerce_import_row(model, row, source_file_id_map)
                if model is QuestionRepairRecord:
                    for version_key in ("base_version_id", "new_version_id"):
                        if data.get(version_key) in question_version_id_map:
                            data[version_key] = question_version_id_map[data[version_key]]
                if row_touches_skipped_question(model, data, skipped_question_ids):
                    skipped += 1
                    continue
                if model is Question and data.get("source_file_id"):
                    existing_question = db.get(Question, data["id"])
                    if existing_question is not None and question_row_conflicts(existing_question, data):
                        skipped_question_ids.add(str(data["id"]))
                        conflict_report.append(
                            {
                                "table": "questions",
                                "kind": "question_id_content_conflict",
                                "question_id": data["id"],
                                "existing_source_name": existing_question.source_name,
                                "incoming_source_name": data.get("source_name"),
                                "existing_exact_hash": existing_question.exact_hash,
                                "incoming_exact_hash": data.get("exact_hash"),
                                "existing_normalized_hash": existing_question.normalized_hash,
                                "incoming_normalized_hash": data.get("normalized_hash"),
                            }
                        )
                        skipped += 1
                        continue
                    touched_source_file_ids.add(str(data["source_file_id"]))
                if model is QuestionVersion and question_version_row_conflicts(db, data):
                    conflict_report.append(
                        {
                            "table": "question_versions",
                            "kind": "question_version_content_conflict",
                            "question_id": data.get("question_id"),
                            "version": data.get("version"),
                            "incoming_version_id": data.get("id"),
                            "incoming_content_hash": data.get("content_hash"),
                        }
                    )
                    skipped += 1
                    continue
                if model is QuestionVersion:
                    incoming_version_id = str(data.get("id") or "")
                    existing_version = local_question_version_for_import(db, data)
                    if existing_version is not None:
                        data["id"] = existing_version.id
                    if incoming_version_id:
                        question_version_id_map[incoming_version_id] = str(data.get("id") or incoming_version_id)
                if model is AnswerSpec:
                    existing_answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == data.get("question_id")))
                    if existing_answer is not None:
                        if answer_spec_row_conflicts(existing_answer, data):
                            conflict_report.append(
                                {
                                    "table": "answer_specs",
                                    "kind": "answer_spec_content_conflict",
                                    "question_id": data.get("question_id"),
                                    "existing_answer_spec_id": existing_answer.id,
                                    "incoming_answer_spec_id": data.get("id"),
                                }
                            )
                            skipped += 1
                            continue
                        data["id"] = existing_answer.id
                if model is QuestionTag:
                    existing_tag = db.scalar(
                        select(QuestionTag).where(
                            QuestionTag.question_id == data.get("question_id"),
                            QuestionTag.tag_id == data.get("tag_id"),
                        )
                    )
                    if existing_tag is not None:
                        data["id"] = existing_tag.id
                if model is QuestionDuplicateAlias:
                    existing_alias = db.scalar(
                        select(QuestionDuplicateAlias).where(
                            QuestionDuplicateAlias.alias_question_id == data.get("alias_question_id"),
                            QuestionDuplicateAlias.status == "active",
                        )
                    )
                    if existing_alias is not None:
                        if existing_alias.canonical_question_id != data.get("canonical_question_id"):
                            conflict_report.append(
                                {
                                    "table": "question_duplicate_aliases",
                                    "kind": "duplicate_alias_canonical_conflict",
                                    "alias_question_id": data.get("alias_question_id"),
                                    "existing_canonical_question_id": existing_alias.canonical_question_id,
                                    "incoming_canonical_question_id": data.get("canonical_question_id"),
                                }
                            )
                            skipped += 1
                            continue
                        data["id"] = existing_alias.id
                if model is ReviewQueueItem:
                    existing_review_item = db.scalar(
                        select(ReviewQueueItem).where(
                            ReviewQueueItem.question_id == data.get("question_id"),
                            ReviewQueueItem.reason_code == data.get("reason_code"),
                        )
                    )
                    if existing_review_item is not None:
                        data["id"] = existing_review_item.id
                if not row_references_exist(db, model, data):
                    skipped += 1
                    continue
                db.merge(model(**data))
                count += 1
            imported_counts[table_name] = count
            if skipped:
                skipped_rows[table_name] = skipped
            db.flush()

        refresh_source_file_counts(db, touched_source_file_ids)
        log_audit_event(
            db,
            "admin.question_bank_imported",
            "question_bank",
            None,
            {"imported_counts": imported_counts, "skipped_tables": skipped_tables, "skipped_rows": skipped_rows, "conflict_report": conflict_report, "dry_run": dry_run},
        )
        status = finish_import_transaction(db, dry_run)
        return QuestionBankImportResponse(status=status, imported_counts=imported_counts, skipped_tables=skipped_tables, skipped_rows=skipped_rows, conflict_report=conflict_report, dry_run=dry_run)
    except Exception:
        db.rollback()
        raise
    finally:
        import_guard.release()


@router.post("/questions/delete", response_model=QuestionDeleteResponse)
def delete_questions(payload: QuestionDeleteRequest, db: Session = Depends(get_db)) -> QuestionDeleteResponse:
    question_ids = sorted(set(payload.question_ids))
    questions = list(db.scalars(select(Question).where(Question.id.in_(question_ids))))
    existing_ids = [question.id for question in questions]
    impact = retire_questions_impact(db, question_ids)
    validate_destructive_challenge("retire_questions", payload.challenge_id, payload.challenge_phrase, impact)
    if not existing_ids:
        raise HTTPException(status_code=404, detail="no matching questions found")

    version_ids = list(db.scalars(select(QuestionVersion.id).where(QuestionVersion.question_id.in_(existing_ids))))
    counts: dict[str, int] = {
        "review_records": 0,
        "wrong_question_records": 0,
        "intervention_records": 0,
        "answer_events": 0,
        "submissions": 0,
        "review_queue_items": 0,
        "question_variants": 0,
        "question_tags": 0,
        "answer_specs": 0,
        "question_versions": 0,
        "questions": 0,
    }
    updated_counts: dict[str, int] = {}
    candidate_question_clause = GeneratedCandidate.published_question_id.in_(existing_ids)
    if version_ids:
        candidate_question_clause = or_(candidate_question_clause, GeneratedCandidate.published_question_version_id.in_(version_ids))

    now = datetime.now(UTC).replace(tzinfo=None)
    updated_counts["questions_retired"] = int(
        db.execute(
            update(Question)
            .where(Question.id.in_(existing_ids))
            .values(
                **checked_status_update_values(
                    {
                        "status": (STATUS_DOMAIN_QUESTION_STATUS, "retired"),
                        "publish_status": (STATUS_DOMAIN_QUESTION_PUBLISH_STATUS, PUBLISH_STATUS_RETIRED_BY_ADMIN),
                    }
                ),
                eligible_for_practice=False,
                eligible_for_test=False,
                question_fault_status=PUBLISH_STATUS_RETIRED_BY_ADMIN,
                question_trust_score=0.0,
            )
        ).rowcount
        or 0
    )
    updated_counts["generated_candidates"] = int(
        db.execute(
            update(GeneratedCandidate)
            .where(candidate_question_clause)
            .values(
                **checked_status_update_values(
                    {"publish_status": (STATUS_DOMAIN_QUESTION_PUBLISH_STATUS, PUBLISH_STATUS_RETIRED_QUESTION)}
                )
            )
        ).rowcount
        or 0
    )
    updated_counts["generation_batch_items"] = int(
        db.execute(
            update(GenerationBatchItem)
            .where(GenerationBatchItem.question_id.in_(existing_ids))
            .values(rejection_reason="published question retired")
        ).rowcount
        or 0
    )
    updated_counts["review_queue_items"] = int(
        db.execute(
            update(ReviewQueueItem)
            .where(ReviewQueueItem.question_id.in_(existing_ids), ReviewQueueItem.status == "open")
            .values(
                **checked_status_update_values({"status": (STATUS_DOMAIN_REVIEW_QUEUE, "closed")}),
                payload={"closed_reason": "question_retired_by_admin", "closed_at": now.isoformat()},
            )
        ).rowcount
        or 0
    )

    log_audit_event(db, "admin.questions_retired", "question_bank", None, {"question_ids": existing_ids, "deleted_counts": counts, "updated_counts": updated_counts})
    db.commit()
    return QuestionDeleteResponse(status="retired", question_ids=existing_ids, deleted_counts=counts, updated_counts=updated_counts)
