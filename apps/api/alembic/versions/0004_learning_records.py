"""learning analysis and review records

Revision ID: 0004_learning_records
Revises: 0003_variants_interventions
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004_learning_records"
down_revision = "0003_variants_interventions"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "wrong_question_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("practice_sessions.id"), nullable=False),
        sa.Column("question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("submission_id", sa.String(length=36), sa.ForeignKey("submissions.id"), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_type", sa.String(length=64), nullable=False),
        sa.Column("analysis", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("submission_id", name="uq_wrong_question_submission"),
    )
    op.create_index("ix_wrong_question_records_session_id", "wrong_question_records", ["session_id"])
    op.create_index("ix_wrong_question_records_question_id", "wrong_question_records", ["question_id"])
    op.create_index("ix_wrong_question_records_submission_id", "wrong_question_records", ["submission_id"])
    op.create_index("ix_wrong_question_records_status", "wrong_question_records", ["status"])
    op.create_index("ix_wrong_question_records_error_type", "wrong_question_records", ["error_type"])

    op.create_table(
        "review_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("practice_sessions.id"), nullable=True),
        sa.Column("source_record_id", sa.String(length=36), sa.ForeignKey("wrong_question_records.id"), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_review_records_question_id", "review_records", ["question_id"])
    op.create_index("ix_review_records_session_id", "review_records", ["session_id"])
    op.create_index("ix_review_records_source_record_id", "review_records", ["source_record_id"])
    op.create_index("ix_review_records_status", "review_records", ["status"])


def downgrade() -> None:
    op.drop_table("review_records")
    op.drop_table("wrong_question_records")
