"""generation batches

Revision ID: 0013_generation_batches
Revises: 0012_guided_help_and_fault_fields
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0013_generation_batches"
down_revision = "0012_guided_help_and_fault_fields"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "generation_batches",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request", json_type, nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("concurrency", sa.Integer(), nullable=False),
        sa.Column("progress", json_type, nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("eta_seconds", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_generation_batches_status", "generation_batches", ["status"])

    op.create_table(
        "generation_batch_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("batch_id", sa.String(length=36), sa.ForeignKey("generation_batches.id"), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=36), sa.ForeignKey("generation_jobs.id"), nullable=True),
        sa.Column("candidate_id", sa.String(length=36), sa.ForeignKey("generated_candidates.id"), nullable=True),
        sa.Column("question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=True),
        sa.Column("similarity_review", json_type, nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_generation_batch_items_batch_id", "generation_batch_items", ["batch_id"])
    op.create_index("ix_generation_batch_items_item_index", "generation_batch_items", ["item_index"])
    op.create_index("ix_generation_batch_items_status", "generation_batch_items", ["status"])
    op.create_index("ix_generation_batch_items_job_id", "generation_batch_items", ["job_id"])
    op.create_index("ix_generation_batch_items_candidate_id", "generation_batch_items", ["candidate_id"])
    op.create_index("ix_generation_batch_items_question_id", "generation_batch_items", ["question_id"])

    op.create_table(
        "generation_duration_stats",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("batch_id", sa.String(length=36), sa.ForeignKey("generation_batches.id"), nullable=True),
        sa.Column("item_id", sa.String(length=36), sa.ForeignKey("generation_batch_items.id"), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("generation_ms", sa.Integer(), nullable=True),
        sa.Column("review_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_generation_duration_stats_batch_id", "generation_duration_stats", ["batch_id"])
    op.create_index("ix_generation_duration_stats_item_id", "generation_duration_stats", ["item_id"])
    op.create_index("ix_generation_duration_stats_provider", "generation_duration_stats", ["provider"])
    op.create_index("ix_generation_duration_stats_model", "generation_duration_stats", ["model"])
    op.create_index("ix_generation_duration_stats_status", "generation_duration_stats", ["status"])


def downgrade() -> None:
    op.drop_table("generation_duration_stats")
    op.drop_table("generation_batch_items")
    op.drop_table("generation_batches")
