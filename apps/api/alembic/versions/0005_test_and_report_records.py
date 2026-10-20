"""test blueprints and report export records

Revision ID: 0005_test_and_report_records
Revises: 0004_learning_records
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_test_and_report_records"
down_revision = "0004_learning_records"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "test_blueprints",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("template_name", sa.String(length=128), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("quota", json_type, nullable=False),
        sa.Column("config", json_type, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name", name="uq_test_blueprint_name"),
    )
    op.create_index("ix_test_blueprints_mode", "test_blueprints", ["mode"])
    op.create_index("ix_test_blueprints_status", "test_blueprints", ["status"])

    op.create_table(
        "test_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("blueprint_id", sa.String(length=36), sa.ForeignKey("test_blueprints.id"), nullable=False),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("practice_sessions.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_test_runs_blueprint_id", "test_runs", ["blueprint_id"])
    op.create_index("ix_test_runs_session_id", "test_runs", ["session_id"])
    op.create_index("ix_test_runs_status", "test_runs", ["status"])

    op.create_table(
        "report_filter_views",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("filters", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name", name="uq_report_filter_view_name"),
    )

    op.create_table(
        "report_exports",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("view_id", sa.String(length=36), sa.ForeignKey("report_filter_views.id"), nullable=True),
        sa.Column("export_type", sa.String(length=32), nullable=False),
        sa.Column("filters", json_type, nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_report_exports_view_id", "report_exports", ["view_id"])
    op.create_index("ix_report_exports_export_type", "report_exports", ["export_type"])


def downgrade() -> None:
    op.drop_table("report_exports")
    op.drop_table("report_filter_views")
    op.drop_table("test_runs")
    op.drop_table("test_blueprints")
