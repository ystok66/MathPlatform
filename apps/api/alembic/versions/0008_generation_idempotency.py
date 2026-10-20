"""generation job idempotency key

Revision ID: 0008_generation_idempotency
Revises: 0007_prompt_templates
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_generation_idempotency"
down_revision = "0007_prompt_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_jobs", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.create_index("ix_generation_jobs_idempotency_key", "generation_jobs", ["idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_column("generation_jobs", "idempotency_key")
