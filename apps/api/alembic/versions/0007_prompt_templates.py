"""prompt template registry

Revision ID: 0007_prompt_templates
Revises: 0006_candidate_review_records
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0007_prompt_templates"
down_revision = "0006_candidate_review_records"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "prompt_templates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("role", "version", name="uq_prompt_template_role_version"),
    )
    op.create_index("ix_prompt_templates_role", "prompt_templates", ["role"])
    op.create_index("ix_prompt_templates_version", "prompt_templates", ["version"])
    op.create_index("ix_prompt_templates_content_hash", "prompt_templates", ["content_hash"])
    op.create_index("ix_prompt_templates_status", "prompt_templates", ["status"])


def downgrade() -> None:
    op.drop_table("prompt_templates")
