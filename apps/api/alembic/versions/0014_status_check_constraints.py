"""status check constraints

Revision ID: 0014_status_check_constraints
Revises: 0013_generation_batches
Create Date: 2026-07-23
"""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0014_status_check_constraints"
down_revision = "0013_generation_batches"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
DEFAULT_LEARNER_ID = "default-learner"


STATUS_CHECKS = (
    ("encrypted_configs", "status", ("active", "deleted"), "ck_encrypted_configs_status"),
    ("learner_profiles", "status", ("active", "archived"), "ck_learner_profiles_status"),
    ("questions", "status", ("needs_review", "quarantined", "retired", "verified"), "ck_questions_status"),
    (
        "questions",
        "publish_status",
        (
            "active_practice",
            "ai_generated",
            "batch_rejected_cancelled",
            "batch_rejected_similar",
            "duplicate_alias",
            "intervention_only",
            "not_published",
            "quarantined",
            "retired",
            "retired_by_admin",
            "retired_question",
            "source_verified",
        ),
        "ck_questions_publish_status",
    ),
    ("answer_specs", "status", ("needs_review", "verified"), "ck_answer_specs_status"),
    ("review_queue_items", "status", ("closed", "open", "resolved"), "ck_review_queue_items_status"),
    ("question_repair_records", "status", ("draft", "published", "rejected", "reverted"), "ck_question_repair_records_status"),
    ("question_duplicate_aliases", "status", ("active", "reverted"), "ck_question_duplicate_aliases_status"),
    ("practice_sessions", "status", ("active", "completed", "paused"), "ck_practice_sessions_status"),
    ("practice_sessions", "mode", ("diagnostic", "generated_practice", "practice", "review_practice", "strict_test"), "ck_practice_sessions_mode"),
    ("test_blueprints", "mode", ("diagnostic", "generated_practice", "practice", "review_practice", "strict_test"), "ck_test_blueprints_mode"),
    ("test_runs", "status", ("active", "completed", "paused"), "ck_test_runs_status"),
    ("intervention_records", "status", ("active", "cancelled", "completed", "failed", "prefetched", "stale"), "ck_intervention_records_status"),
    (
        "intervention_records",
        "prefetch_status",
        ("completed", "failed", "generating", "not_prefetched", "ready", "shown", "stale", "unused_prefetch"),
        "ck_intervention_records_prefetch_status",
    ),
    ("wrong_question_records", "status", ("active", "ignored", "resolved", "reviewing"), "ck_wrong_question_records_status"),
    ("review_records", "status", ("completed", "pending", "resolved"), "ck_review_records_status"),
    ("prompt_templates", "status", ("active", "available"), "ck_prompt_templates_status"),
    ("generation_jobs", "status", ("accepted", "cancelled", "failed", "queued", "rejected", "running", "timed_out"), "ck_generation_jobs_status"),
    ("generation_batches", "status", ("cancelled", "cancelling", "completed", "failed", "partial", "queued", "running", "timed_out"), "ck_generation_batches_status"),
    ("generation_batch_items", "status", ("accepted", "cancelled", "failed", "queued", "rejected", "running", "timed_out"), "ck_generation_batch_items_status"),
    ("generated_candidates", "status", ("accepted", "approved", "edited", "failed", "manual_rejected", "rejected"), "ck_generated_candidates_status"),
    ("generated_candidate_reviews", "status", ("accepted", "approved", "edited", "failed", "manual_rejected", "rejected"), "ck_generated_candidate_reviews_status"),
    (
        "generated_candidates",
        "publish_status",
        (
            "active_practice",
            "ai_generated",
            "batch_rejected_cancelled",
            "batch_rejected_similar",
            "duplicate_alias",
            "intervention_only",
            "not_published",
            "quarantined",
            "retired",
            "retired_by_admin",
            "retired_question",
            "source_verified",
        ),
        "ck_generated_candidates_publish_status",
    ),
)


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    quoted_values = []
    for value in values:
        escaped = value.replace("'", "''")
        quoted_values.append(f"'{escaped}'")
    quoted = ", ".join(quoted_values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return

    op.create_table(
        "learner_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("settings", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_learner_profiles_status", "learner_profiles", ["status"])
    op.create_index("ix_learner_profiles_display_order", "learner_profiles", ["display_order"])
    now = datetime.now(UTC).replace(tzinfo=None)
    learner_profiles = sa.table(
        "learner_profiles",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("status", sa.String),
        sa.column("display_order", sa.Integer),
        sa.column("settings", json_type),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    op.bulk_insert(
        learner_profiles,
        [
            {
                "id": DEFAULT_LEARNER_ID,
                "name": "默认学习者",
                "status": "active",
                "display_order": 0,
                "settings": {},
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    op.add_column(
        "practice_sessions",
        sa.Column("learner_id", sa.String(length=36), nullable=False, server_default=DEFAULT_LEARNER_ID),
    )
    op.create_foreign_key("fk_practice_sessions_learner_id", "practice_sessions", "learner_profiles", ["learner_id"], ["id"])
    op.create_index("ix_practice_sessions_learner_id", "practice_sessions", ["learner_id"])

    op.create_table(
        "question_repair_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("base_version_id", sa.String(length=36), sa.ForeignKey("question_versions.id"), nullable=True),
        sa.Column("new_version_id", sa.String(length=36), sa.ForeignKey("question_versions.id"), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("draft_question_v2", json_type, nullable=False),
        sa.Column("draft_answer_spec", json_type, nullable=True),
        sa.Column("base_question_v2", json_type, nullable=False),
        sa.Column("base_answer_spec", json_type, nullable=True),
        sa.Column("diff", json_type, nullable=False),
        sa.Column("reviewer", sa.String(length=64), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_question_repair_records_question_id", "question_repair_records", ["question_id"])
    op.create_index("ix_question_repair_records_base_version_id", "question_repair_records", ["base_version_id"])
    op.create_index("ix_question_repair_records_new_version_id", "question_repair_records", ["new_version_id"])
    op.create_index("ix_question_repair_records_status", "question_repair_records", ["status"])
    op.create_index("ix_question_repair_records_reason", "question_repair_records", ["reason"])
    op.create_index("ix_question_repair_records_severity", "question_repair_records", ["severity"])
    op.create_index("ix_question_repair_records_reviewer", "question_repair_records", ["reviewer"])

    op.create_table(
        "question_duplicate_aliases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("canonical_question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("alias_question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("alias_question_id", name="uq_question_duplicate_alias"),
        sa.UniqueConstraint("canonical_question_id", "alias_question_id", name="uq_question_duplicate_canonical_alias"),
    )
    op.create_index("ix_question_duplicate_aliases_canonical_question_id", "question_duplicate_aliases", ["canonical_question_id"])
    op.create_index("ix_question_duplicate_aliases_alias_question_id", "question_duplicate_aliases", ["alias_question_id"])
    op.create_index("ix_question_duplicate_aliases_reviewer", "question_duplicate_aliases", ["reviewer"])
    op.create_index("ix_question_duplicate_aliases_status", "question_duplicate_aliases", ["status"])

    op.create_table(
        "generation_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("stream_type", sa.String(length=16), nullable=False),
        sa.Column("stream_id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("stream_type", "stream_id", "event_id", name="uq_generation_event_stream_event"),
    )
    op.create_index("ix_generation_events_stream_type", "generation_events", ["stream_type"])
    op.create_index("ix_generation_events_stream_id", "generation_events", ["stream_id"])
    op.create_index("ix_generation_events_event_id", "generation_events", ["event_id"])
    op.create_index("ix_generation_events_event_type", "generation_events", ["event_type"])
    op.create_index("ix_generation_events_created_at", "generation_events", ["created_at"])

    for table_name, column_name, values, constraint_name in STATUS_CHECKS:
        op.create_check_constraint(constraint_name, table_name, _in_clause(column_name, values))


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    for table_name, _column_name, _values, constraint_name in reversed(STATUS_CHECKS):
        op.drop_constraint(constraint_name, table_name, type_="check")

    op.drop_table("generation_events")
    op.drop_table("question_duplicate_aliases")
    op.drop_table("question_repair_records")
    op.drop_index("ix_practice_sessions_learner_id", table_name="practice_sessions")
    op.drop_constraint("fk_practice_sessions_learner_id", "practice_sessions", type_="foreignkey")
    op.drop_column("practice_sessions", "learner_id")
    op.drop_table("learner_profiles")
