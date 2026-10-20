"""taxonomy governance records

Revision ID: 0011_taxonomy_governance
Revises: 0010_encrypted_configs
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0011_taxonomy_governance"
down_revision = "0010_encrypted_configs"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "taxonomy_governance_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("tag_id", sa.String(length=160), sa.ForeignKey("taxonomy_tags.id"), nullable=True),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("reviewer", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_taxonomy_governance_records_action", "taxonomy_governance_records", ["action"])
    op.create_index("ix_taxonomy_governance_records_tag_id", "taxonomy_governance_records", ["tag_id"])
    op.create_index("ix_taxonomy_governance_records_reviewer", "taxonomy_governance_records", ["reviewer"])
    op.create_index("ix_taxonomy_governance_records_status", "taxonomy_governance_records", ["status"])


def downgrade() -> None:
    op.drop_table("taxonomy_governance_records")
