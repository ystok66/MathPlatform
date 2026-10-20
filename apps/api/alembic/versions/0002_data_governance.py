"""data governance tables and immutable submission versions

Revision ID: 0002_data_governance
Revises: 0001_initial
Create Date: 2026-07-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_data_governance"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

json_type = sa.JSON()


def upgrade() -> None:
    with op.batch_alter_table("questions") as batch:
        batch.add_column(sa.Column("source_difficulty_star", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("calibrated_difficulty_star", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_premium", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("calibrated_premium", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("calibration_version", sa.String(length=64), nullable=False, server_default="source_original_v1"))
        batch.add_column(sa.Column("exact_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("normalized_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("semantic_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("family_id", sa.String(length=128), nullable=True))
    op.create_index("ix_questions_calibrated_difficulty_star", "questions", ["calibrated_difficulty_star"])
    op.create_index("ix_questions_exact_hash", "questions", ["exact_hash"])
    op.create_index("ix_questions_normalized_hash", "questions", ["normalized_hash"])
    op.create_index("ix_questions_semantic_hash", "questions", ["semantic_hash"])
    op.create_index("ix_questions_family_id", "questions", ["family_id"])

    with op.batch_alter_table("taxonomy_tags") as batch:
        batch.add_column(sa.Column("version", sa.String(length=32), nullable=False, server_default="taxonomy_v1"))
        batch.add_column(sa.Column("status", sa.String(length=32), nullable=False, server_default="active"))

    op.create_table(
        "taxonomy_aliases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("alias", sa.String(length=256), nullable=False),
        sa.Column("canonical_tag_id", sa.String(length=160), sa.ForeignKey("taxonomy_tags.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("kind", "alias", name="uq_taxonomy_alias_kind_alias"),
    )
    op.create_index("ix_taxonomy_aliases_kind", "taxonomy_aliases", ["kind"])
    op.create_index("ix_taxonomy_aliases_alias", "taxonomy_aliases", ["alias"])
    op.create_table(
        "taxonomy_migrations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("from_tag_id", sa.String(length=160), sa.ForeignKey("taxonomy_tags.id"), nullable=False),
        sa.Column("to_tag_id", sa.String(length=160), sa.ForeignKey("taxonomy_tags.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "review_queue_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("question_id", "reason_code", name="uq_review_queue_question_reason"),
    )
    op.create_index("ix_review_queue_items_question_id", "review_queue_items", ["question_id"])
    op.create_index("ix_review_queue_items_reason_code", "review_queue_items", ["reason_code"])
    op.create_index("ix_review_queue_items_status", "review_queue_items", ["status"])
    with op.batch_alter_table("submissions") as batch:
        batch.add_column(sa.Column("question_version_id", sa.String(length=36), sa.ForeignKey("question_versions.id"), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("submissions") as batch:
        batch.drop_column("question_version_id")
    op.drop_table("review_queue_items")
    op.drop_table("taxonomy_migrations")
    op.drop_table("taxonomy_aliases")
    with op.batch_alter_table("taxonomy_tags") as batch:
        batch.drop_column("status")
        batch.drop_column("version")
    for index in (
        "ix_questions_family_id",
        "ix_questions_semantic_hash",
        "ix_questions_normalized_hash",
        "ix_questions_exact_hash",
        "ix_questions_calibrated_difficulty_star",
    ):
        op.drop_index(index, table_name="questions")
    with op.batch_alter_table("questions") as batch:
        batch.drop_column("family_id")
        batch.drop_column("semantic_hash")
        batch.drop_column("normalized_hash")
        batch.drop_column("exact_hash")
        batch.drop_column("calibration_version")
        batch.drop_column("calibrated_premium")
        batch.drop_column("source_premium")
        batch.drop_column("calibrated_difficulty_star")
        batch.drop_column("source_difficulty_star")
