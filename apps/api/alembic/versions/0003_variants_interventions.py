"""variants and intervention records

Revision ID: 0003_variants_interventions
Revises: 0002_data_governance
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_variants_interventions"
down_revision = "0002_data_governance"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "question_variants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("base_question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("variant_question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("variant_level", sa.String(length=16), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("family_id", sa.String(length=128), nullable=False),
        sa.Column("transformation", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("base_question_id", "variant_question_id", "variant_level", name="uq_question_variant_link"),
    )
    op.create_index("ix_question_variants_base_question_id", "question_variants", ["base_question_id"])
    op.create_index("ix_question_variants_variant_question_id", "question_variants", ["variant_question_id"])
    op.create_index("ix_question_variants_variant_level", "question_variants", ["variant_level"])
    op.create_index("ix_question_variants_purpose", "question_variants", ["purpose"])
    op.create_index("ix_question_variants_family_id", "question_variants", ["family_id"])

    op.create_table(
        "intervention_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("practice_sessions.id"), nullable=False),
        sa.Column("original_question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("current_question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("intervention_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_intervention_records_session_id", "intervention_records", ["session_id"])
    op.create_index("ix_intervention_records_original_question_id", "intervention_records", ["original_question_id"])
    op.create_index("ix_intervention_records_current_question_id", "intervention_records", ["current_question_id"])
    op.create_index("ix_intervention_records_intervention_type", "intervention_records", ["intervention_type"])
    op.create_index("ix_intervention_records_status", "intervention_records", ["status"])


def downgrade() -> None:
    op.drop_table("intervention_records")
    op.drop_table("question_variants")
