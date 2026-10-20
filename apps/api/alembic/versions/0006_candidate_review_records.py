"""generated candidate manual review records

Revision ID: 0006_candidate_review_records
Revises: 0005_test_and_report_records
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_candidate_review_records"
down_revision = "0005_test_and_report_records"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "generated_candidate_reviews",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_id", sa.String(length=36), sa.ForeignKey("generation_jobs.id"), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), sa.ForeignKey("generated_candidates.id"), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("diff", json_type, nullable=False),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_generated_candidate_reviews_job_id", "generated_candidate_reviews", ["job_id"])
    op.create_index("ix_generated_candidate_reviews_candidate_id", "generated_candidate_reviews", ["candidate_id"])
    op.create_index("ix_generated_candidate_reviews_action", "generated_candidate_reviews", ["action"])
    op.create_index("ix_generated_candidate_reviews_status", "generated_candidate_reviews", ["status"])


def downgrade() -> None:
    op.drop_table("generated_candidate_reviews")
