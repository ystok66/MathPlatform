"""encrypted configs

Revision ID: 0010_encrypted_configs
Revises: 0009_audit_logs
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0010_encrypted_configs"
down_revision = "0009_audit_logs"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "encrypted_configs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value_ciphertext", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("key", name="uq_encrypted_config_key"),
    )
    op.create_index("ix_encrypted_configs_key", "encrypted_configs", ["key"])
    op.create_index("ix_encrypted_configs_status", "encrypted_configs", ["status"])


def downgrade() -> None:
    op.drop_table("encrypted_configs")
