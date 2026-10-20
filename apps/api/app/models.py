from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, attributes as orm_attributes, mapped_column, relationship

from .db import Base
from .statuses import (
    ANSWER_SPEC_STATUSES,
    ENCRYPTED_CONFIG_STATUSES,
    GENERATED_CANDIDATE_STATUSES,
    GENERATION_BATCH_STATUSES,
    GENERATION_ITEM_STATUSES,
    GENERATION_JOB_STATUSES,
    INTERVENTION_PREFETCH_STATUSES,
    INTERVENTION_RECORD_STATUSES,
    LEARNER_PROFILE_STATUSES,
    PRACTICE_MODES,
    PRACTICE_SESSION_STATUSES,
    PROMPT_TEMPLATE_STATUSES,
    QUESTION_DUPLICATE_ALIAS_STATUSES,
    QUESTION_PUBLISH_STATUSES,
    QUESTION_REPAIR_STATUSES,
    QUESTION_STATUSES,
    REVIEW_QUEUE_STATUSES,
    REVIEW_RECORD_STATUSES,
    STATUS_DOMAIN_ANSWER_SPEC_STATUS,
    STATUS_DOMAIN_ENCRYPTED_CONFIG,
    STATUS_DOMAIN_GENERATED_CANDIDATE,
    STATUS_DOMAIN_GENERATION_BATCH,
    STATUS_DOMAIN_GENERATION_ITEM,
    STATUS_DOMAIN_GENERATION_JOB,
    STATUS_DOMAIN_INTERVENTION_PREFETCH,
    STATUS_DOMAIN_INTERVENTION_RECORD,
    STATUS_DOMAIN_LEARNER_PROFILE,
    STATUS_DOMAIN_PRACTICE_MODE,
    STATUS_DOMAIN_PRACTICE_SESSION,
    STATUS_DOMAIN_PROMPT_TEMPLATE,
    STATUS_DOMAIN_QUESTION_DUPLICATE_ALIAS,
    STATUS_DOMAIN_QUESTION_PUBLISH_STATUS,
    STATUS_DOMAIN_QUESTION_REPAIR,
    STATUS_DOMAIN_QUESTION_STATUS,
    STATUS_DOMAIN_REVIEW_QUEUE,
    STATUS_DOMAIN_REVIEW_RECORD,
    STATUS_DOMAIN_WRONG_QUESTION,
    WRONG_QUESTION_STATUSES,
    assert_status_transition,
)


def uuid_str() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


JsonType = JSON().with_variant(JSONB, "postgresql")
DEFAULT_LEARNER_ID = "default-learner"


def status_check(column_name: str, values: frozenset[str], name: str) -> CheckConstraint:
    quoted = []
    for value in sorted(values):
        escaped = value.replace("'", "''")
        quoted.append(f"'{escaped}'")
    quoted_values = ", ".join(quoted)
    return CheckConstraint(f"{column_name} IN ({quoted_values})", name=name)


class SourceFile(Base):
    __tablename__ = "source_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    filename: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    actor: Mapped[str] = mapped_column(String(64), default="local_user", nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class EncryptedConfig(Base):
    __tablename__ = "encrypted_configs"
    __table_args__ = (
        UniqueConstraint("key", name="uq_encrypted_config_key"),
        status_check("status", ENCRYPTED_CONFIG_STATUSES, "ck_encrypted_configs_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    value_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class LearnerProfile(Base):
    __tablename__ = "learner_profiles"
    __table_args__ = (status_check("status", LEARNER_PROFILE_STATUSES, "ck_learner_profiles_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        status_check("status", QUESTION_STATUSES, "ck_questions_status"),
        status_check("publish_status", QUESTION_PUBLISH_STATUSES, "ck_questions_publish_status"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_file_id: Mapped[str] = mapped_column(String(36), ForeignKey("source_files.id"), nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    publish_status: Mapped[str] = mapped_column(String(32), default="source_verified", nullable=False, index=True)
    eligible_for_practice: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    eligible_for_test: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    question_fault_status: Mapped[str] = mapped_column(String(32), default="none", nullable=False, index=True)
    question_fault_confidence: Mapped[float | None] = mapped_column(Float)
    question_trust_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    solution_style: Mapped[str] = mapped_column(String(32), default="elementary_preferred", nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    section: Mapped[str | None] = mapped_column(Text)
    original_number: Mapped[str | None] = mapped_column(String(64))
    stem_preview: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty_star: Mapped[int | None] = mapped_column(Integer, index=True)
    premium: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    source_difficulty_star: Mapped[int | None] = mapped_column(Integer)
    calibrated_difficulty_star: Mapped[int | None] = mapped_column(Integer, index=True)
    source_premium: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    calibrated_premium: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    calibration_version: Mapped[str] = mapped_column(String(64), default="source_original_v1", nullable=False)
    exact_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    normalized_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    semantic_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    family_id: Mapped[str | None] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    source_file: Mapped[SourceFile] = relationship()
    versions: Mapped[list["QuestionVersion"]] = relationship(back_populates="question")
    answer_spec: Mapped["AnswerSpec"] = relationship(back_populates="question", uselist=False)


class QuestionVersion(Base):
    __tablename__ = "question_versions"
    __table_args__ = (UniqueConstraint("question_id", "version", name="uq_question_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    question_id: Mapped[str] = mapped_column(String(128), ForeignKey("questions.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    question_v2: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    question: Mapped[Question] = relationship(back_populates="versions")


class AnswerSpec(Base):
    __tablename__ = "answer_specs"
    __table_args__ = (status_check("status", ANSWER_SPEC_STATUSES, "ck_answer_specs_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    question_id: Mapped[str] = mapped_column(String(128), ForeignKey("questions.id"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    answer_type: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_answer: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_spec: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    raw_answer_spec: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    verification: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    question: Mapped[Question] = relationship(back_populates="answer_spec")


class TaxonomyTag(Base):
    __tablename__ = "taxonomy_tags"
    __table_args__ = (UniqueConstraint("kind", "label", name="uq_taxonomy_kind_label"),)

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(String(160), ForeignKey("taxonomy_tags.id"))
    aliases: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="taxonomy_v1", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class TaxonomyAlias(Base):
    __tablename__ = "taxonomy_aliases"
    __table_args__ = (UniqueConstraint("kind", "alias", name="uq_taxonomy_alias_kind_alias"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    canonical_tag_id: Mapped[str] = mapped_column(String(160), ForeignKey("taxonomy_tags.id"), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class TaxonomyMigration(Base):
    __tablename__ = "taxonomy_migrations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    from_tag_id: Mapped[str] = mapped_column(String(160), ForeignKey("taxonomy_tags.id"), nullable=False)
    to_tag_id: Mapped[str] = mapped_column(String(160), ForeignKey("taxonomy_tags.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="proposed", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class TaxonomyGovernanceRecord(Base):
    __tablename__ = "taxonomy_governance_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tag_id: Mapped[str | None] = mapped_column(String(160), ForeignKey("taxonomy_tags.id"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    reviewer: Mapped[str] = mapped_column(String(64), default="local_user", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="recorded", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ReviewQueueItem(Base):
    __tablename__ = "review_queue_items"
    __table_args__ = (
        UniqueConstraint("question_id", "reason_code", name="uq_review_queue_question_reason"),
        status_check("status", REVIEW_QUEUE_STATUSES, "ck_review_queue_items_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    question_id: Mapped[str] = mapped_column(String(128), ForeignKey("questions.id"), nullable=False, index=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(32), default="medium", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class QuestionRepairRecord(Base):
    __tablename__ = "question_repair_records"
    __table_args__ = (status_check("status", QUESTION_REPAIR_STATUSES, "ck_question_repair_records_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    question_id: Mapped[str] = mapped_column(String(128), ForeignKey("questions.id"), nullable=False, index=True)
    base_version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("question_versions.id"), nullable=True, index=True)
    new_version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("question_versions.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(32), default="medium", nullable=False, index=True)
    draft_question_v2: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    draft_answer_spec: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    base_question_v2: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    base_answer_spec: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    diff: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list, nullable=False)
    reviewer: Mapped[str] = mapped_column(String(64), default="local_user", nullable=False, index=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class QuestionDuplicateAlias(Base):
    __tablename__ = "question_duplicate_aliases"
    __table_args__ = (
        UniqueConstraint("alias_question_id", name="uq_question_duplicate_alias"),
        UniqueConstraint("canonical_question_id", "alias_question_id", name="uq_question_duplicate_canonical_alias"),
        status_check("status", QUESTION_DUPLICATE_ALIAS_STATUSES, "ck_question_duplicate_aliases_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    canonical_question_id: Mapped[str] = mapped_column(String(128), ForeignKey("questions.id"), nullable=False, index=True)
    alias_question_id: Mapped[str] = mapped_column(String(128), ForeignKey("questions.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str] = mapped_column(String(64), default="local_user", nullable=False, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class QuestionTag(Base):
    __tablename__ = "question_tags"
    __table_args__ = (UniqueConstraint("question_id", "tag_id", name="uq_question_tag"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    question_id: Mapped[str] = mapped_column(String(128), ForeignKey("questions.id"), nullable=False, index=True)
    tag_id: Mapped[str] = mapped_column(String(160), ForeignKey("taxonomy_tags.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_label: Mapped[str] = mapped_column(String(256), nullable=False)


class PracticeSession(Base):
    __tablename__ = "practice_sessions"
    __table_args__ = (
        status_check("status", PRACTICE_SESSION_STATUSES, "ck_practice_sessions_status"),
        status_check("mode", PRACTICE_MODES, "ck_practice_sessions_mode"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    learner_id: Mapped[str] = mapped_column(String(36), ForeignKey("learner_profiles.id"), default=DEFAULT_LEARNER_ID, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(32), default="practice", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    question_ids: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class TestBlueprint(Base):
    __tablename__ = "test_blueprints"
    __table_args__ = (
        UniqueConstraint("name", name="uq_test_blueprint_name"),
        status_check("mode", PRACTICE_MODES, "ck_test_blueprints_mode"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), default="strict_test", nullable=False, index=True)
    template_name: Mapped[str | None] = mapped_column(String(128))
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    quota: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class TestRun(Base):
    __tablename__ = "test_runs"
    __table_args__ = (status_check("status", PRACTICE_SESSION_STATUSES, "ck_test_runs_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    blueprint_id: Mapped[str] = mapped_column(String(36), ForeignKey("test_blueprints.id"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("practice_sessions.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    progress: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("practice_sessions.id"), nullable=False)
    question_id: Mapped[str] = mapped_column(String(128), ForeignKey("questions.id"), nullable=False)
    question_version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("question_versions.id"))
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    thought_text: Mapped[str | None] = mapped_column(Text)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    effective_wrong: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    excluded_from_mastery: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    solution_revealed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    help_level_reached: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    grading_result: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class AnswerEvent(Base):
    __tablename__ = "answer_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("practice_sessions.id"), nullable=False)
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class QuestionVariant(Base):
    __tablename__ = "question_variants"
    __table_args__ = (UniqueConstraint("base_question_id", "variant_question_id", "variant_level", name="uq_question_variant_link"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    base_question_id: Mapped[str] = mapped_column(String(128), ForeignKey("questions.id"), nullable=False, index=True)
    variant_question_id: Mapped[str] = mapped_column(String(128), ForeignKey("questions.id"), nullable=False, index=True)
    variant_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    family_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    transformation: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class InterventionRecord(Base):
    __tablename__ = "intervention_records"
    __table_args__ = (
        status_check("status", INTERVENTION_RECORD_STATUSES, "ck_intervention_records_status"),
        status_check("prefetch_status", INTERVENTION_PREFETCH_STATUSES, "ck_intervention_records_prefetch_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("practice_sessions.id"), nullable=False, index=True)
    original_question_id: Mapped[str] = mapped_column(String(128), ForeignKey("questions.id"), nullable=False, index=True)
    current_question_id: Mapped[str] = mapped_column(String(128), ForeignKey("questions.id"), nullable=False, index=True)
    intervention_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    intervention_cycle: Mapped[int] = mapped_column(Integer, default=1, nullable=False, index=True)
    prefetch_status: Mapped[str] = mapped_column(String(32), default="not_prefetched", nullable=False, index=True)
    prefetch_trigger_submission_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("submissions.id"), index=True)
    evidence_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    available_after_wrong_count: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class WrongQuestionRecord(Base):
    __tablename__ = "wrong_question_records"
    __table_args__ = (
        UniqueConstraint("submission_id", name="uq_wrong_question_submission"),
        status_check("status", WRONG_QUESTION_STATUSES, "ck_wrong_question_records_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("practice_sessions.id"), nullable=False, index=True)
    question_id: Mapped[str] = mapped_column(String(128), ForeignKey("questions.id"), nullable=False, index=True)
    submission_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("submissions.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    error_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    analysis: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ReviewRecord(Base):
    __tablename__ = "review_records"
    __table_args__ = (status_check("status", REVIEW_RECORD_STATUSES, "ck_review_records_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    question_id: Mapped[str] = mapped_column(String(128), ForeignKey("questions.id"), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("practice_sessions.id"), index=True)
    source_record_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("wrong_question_records.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ReportFilterView(Base):
    __tablename__ = "report_filter_views"
    __table_args__ = (UniqueConstraint("name", name="uq_report_filter_view_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ReportExport(Base):
    __tablename__ = "report_exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    view_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("report_filter_views.id"), index=True)
    export_type: Mapped[str] = mapped_column(String(32), default="summary_json", nullable=False, index=True)
    filters: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class SystemTask(Base):
    __tablename__ = "system_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(128), default="system", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(String(64), index=True)
    target_id: Mapped[str | None] = mapped_column(String(64), index=True)
    progress: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    error: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class RecoveryAgentRun(Base):
    __tablename__ = "recovery_agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), default="gpt-5.6-sol", nullable=False)
    max_loops: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    loop_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    steps: Mapped[list[dict[str, Any]] | None] = mapped_column(JsonType)
    report: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    error: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    learner_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("learner_profiles.id"), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(32), default="learner", nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    page_context: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False, index=True)


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("assistant_conversations.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)


class AssistantToolRun(Base):
    __tablename__ = "assistant_tool_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("assistant_conversations.id"), nullable=False, index=True)
    message_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("assistant_messages.id"), nullable=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    permission_level: Mapped[str] = mapped_column(String(32), default="read", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    confirmation_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint("role", "version", name="uq_prompt_template_role_version"),
        status_check("status", PROMPT_TEMPLATE_STATUSES, "ck_prompt_templates_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    role: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (status_check("status", GENERATION_JOB_STATUSES, "ck_generation_jobs_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    request: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    progress: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    accepted_question_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class GenerationBatch(Base):
    __tablename__ = "generation_batches"
    __table_args__ = (status_check("status", GENERATION_BATCH_STATUSES, "ck_generation_batches_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    request: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    concurrency: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    progress: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    eta_seconds: Mapped[int | None] = mapped_column(Integer)
    queue_claim_token: Mapped[str | None] = mapped_column(String(36), index=True)
    queue_claimed_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class GenerationBatchItem(Base):
    __tablename__ = "generation_batch_items"
    __table_args__ = (status_check("status", GENERATION_ITEM_STATUSES, "ck_generation_batch_items_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    batch_id: Mapped[str] = mapped_column(String(36), ForeignKey("generation_batches.id"), nullable=False, index=True)
    item_index: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("generation_jobs.id"), index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("generated_candidates.id"), index=True)
    question_id: Mapped[str | None] = mapped_column(String(128), ForeignKey("questions.id"), index=True)
    similarity_review: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class GenerationDurationStat(Base):
    __tablename__ = "generation_duration_stats"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    batch_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("generation_batches.id"), index=True)
    item_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("generation_batch_items.id"), index=True)
    provider: Mapped[str] = mapped_column(String(64), default="mock", nullable=False, index=True)
    model: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_ms: Mapped[int | None] = mapped_column(Integer)
    review_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class GenerationEvent(Base):
    __tablename__ = "generation_events"
    __table_args__ = (UniqueConstraint("stream_type", "stream_id", "event_id", name="uq_generation_event_stream_event"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    stream_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    stream_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="persistent", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)


class GeneratedCandidate(Base):
    __tablename__ = "generated_candidates"
    __table_args__ = (
        status_check("status", GENERATED_CANDIDATE_STATUSES, "ck_generated_candidates_status"),
        status_check("publish_status", QUESTION_PUBLISH_STATUSES, "ck_generated_candidates_publish_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("generation_jobs.id"), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    review: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    published_question_id: Mapped[str | None] = mapped_column(String(128), ForeignKey("questions.id"), index=True)
    published_question_version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("question_versions.id"), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    publish_status: Mapped[str] = mapped_column(String(32), default="not_published", nullable=False, index=True)


class GeneratedCandidateReview(Base):
    __tablename__ = "generated_candidate_reviews"
    __table_args__ = (status_check("status", GENERATED_CANDIDATE_STATUSES, "ck_generated_candidate_reviews_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("generation_jobs.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(36), ForeignKey("generated_candidates.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    diff: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    reviewer_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class AiRunLog(Base):
    __tablename__ = "ai_run_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    job_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("generation_jobs.id"))
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    token_usage: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


def _old_status_value(value: Any) -> str | None:
    for sentinel_name in ("NO_VALUE", "NEVER_SET", "PASSIVE_NO_RESULT"):
        if value is getattr(orm_attributes, sentinel_name, None):
            return None
    if value is None:
        return None
    return str(value)


def _status_guard(domain: str, model_name: str, field_name: str):
    def guard(_target: Any, value: Any, oldvalue: Any, _initiator: Any) -> Any:
        if value is None:
            return value
        try:
            normalized_next = str(value)
            normalized_old = _old_status_value(oldvalue)
            assert_status_transition(domain, normalized_old, normalized_next)
        except ValueError as exc:
            raise ValueError(f"{model_name}.{field_name}:{exc}") from exc
        return value

    return guard


def register_status_domain_guards() -> None:
    bindings = (
        (EncryptedConfig, "status", STATUS_DOMAIN_ENCRYPTED_CONFIG),
        (LearnerProfile, "status", STATUS_DOMAIN_LEARNER_PROFILE),
        (Question, "status", STATUS_DOMAIN_QUESTION_STATUS),
        (Question, "publish_status", STATUS_DOMAIN_QUESTION_PUBLISH_STATUS),
        (AnswerSpec, "status", STATUS_DOMAIN_ANSWER_SPEC_STATUS),
        (ReviewQueueItem, "status", STATUS_DOMAIN_REVIEW_QUEUE),
        (QuestionRepairRecord, "status", STATUS_DOMAIN_QUESTION_REPAIR),
        (QuestionDuplicateAlias, "status", STATUS_DOMAIN_QUESTION_DUPLICATE_ALIAS),
        (PracticeSession, "status", STATUS_DOMAIN_PRACTICE_SESSION),
        (PracticeSession, "mode", STATUS_DOMAIN_PRACTICE_MODE),
        (TestBlueprint, "mode", STATUS_DOMAIN_PRACTICE_MODE),
        (TestRun, "status", STATUS_DOMAIN_PRACTICE_SESSION),
        (InterventionRecord, "status", STATUS_DOMAIN_INTERVENTION_RECORD),
        (InterventionRecord, "prefetch_status", STATUS_DOMAIN_INTERVENTION_PREFETCH),
        (WrongQuestionRecord, "status", STATUS_DOMAIN_WRONG_QUESTION),
        (ReviewRecord, "status", STATUS_DOMAIN_REVIEW_RECORD),
        (PromptTemplate, "status", STATUS_DOMAIN_PROMPT_TEMPLATE),
        (GenerationJob, "status", STATUS_DOMAIN_GENERATION_JOB),
        (GenerationBatch, "status", STATUS_DOMAIN_GENERATION_BATCH),
        (GenerationBatchItem, "status", STATUS_DOMAIN_GENERATION_ITEM),
        (GeneratedCandidate, "status", STATUS_DOMAIN_GENERATED_CANDIDATE),
        (GeneratedCandidate, "publish_status", STATUS_DOMAIN_QUESTION_PUBLISH_STATUS),
        (GeneratedCandidateReview, "status", STATUS_DOMAIN_GENERATED_CANDIDATE),
    )
    for model, field_name, domain in bindings:
        event.listen(getattr(model, field_name), "set", _status_guard(domain, model.__name__, field_name), retval=True, active_history=True)


register_status_domain_guards()
