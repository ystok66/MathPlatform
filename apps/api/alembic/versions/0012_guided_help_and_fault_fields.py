"""guided help and fault fields

Revision ID: 0012_guided_help_and_fault_fields
Revises: 0011_taxonomy_governance
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_guided_help_and_fault_fields"
down_revision = "0011_taxonomy_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("questions", sa.Column("publish_status", sa.String(length=32), nullable=False, server_default="source_verified"))
    op.add_column("questions", sa.Column("eligible_for_practice", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("questions", sa.Column("eligible_for_test", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("questions", sa.Column("question_fault_status", sa.String(length=32), nullable=False, server_default="none"))
    op.add_column("questions", sa.Column("question_fault_confidence", sa.Float(), nullable=True))
    op.add_column("questions", sa.Column("question_trust_score", sa.Float(), nullable=False, server_default="1.0"))
    op.add_column("questions", sa.Column("solution_style", sa.String(length=32), nullable=False, server_default="elementary_preferred"))
    op.create_index("ix_questions_publish_status", "questions", ["publish_status"])
    op.create_index("ix_questions_eligible_for_practice", "questions", ["eligible_for_practice"])
    op.create_index("ix_questions_eligible_for_test", "questions", ["eligible_for_test"])
    op.create_index("ix_questions_question_fault_status", "questions", ["question_fault_status"])
    op.create_index("ix_questions_solution_style", "questions", ["solution_style"])

    op.add_column("submissions", sa.Column("effective_wrong", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("submissions", sa.Column("excluded_from_mastery", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("submissions", sa.Column("solution_revealed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("submissions", sa.Column("help_level_reached", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_submissions_effective_wrong", "submissions", ["effective_wrong"])
    op.create_index("ix_submissions_excluded_from_mastery", "submissions", ["excluded_from_mastery"])
    op.create_index("ix_submissions_solution_revealed", "submissions", ["solution_revealed"])

    op.add_column("intervention_records", sa.Column("intervention_cycle", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("intervention_records", sa.Column("prefetch_status", sa.String(length=32), nullable=False, server_default="not_prefetched"))
    op.add_column("intervention_records", sa.Column("prefetch_trigger_submission_id", sa.String(length=36), sa.ForeignKey("submissions.id"), nullable=True))
    op.add_column("intervention_records", sa.Column("evidence_hash", sa.String(length=64), nullable=True))
    op.add_column("intervention_records", sa.Column("available_after_wrong_count", sa.Integer(), nullable=False, server_default="3"))
    op.create_index("ix_intervention_records_intervention_cycle", "intervention_records", ["intervention_cycle"])
    op.create_index("ix_intervention_records_prefetch_status", "intervention_records", ["prefetch_status"])
    op.create_index("ix_intervention_records_prefetch_trigger_submission_id", "intervention_records", ["prefetch_trigger_submission_id"])
    op.create_index("ix_intervention_records_evidence_hash", "intervention_records", ["evidence_hash"])

    op.add_column("generated_candidates", sa.Column("published_question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=True))
    op.add_column("generated_candidates", sa.Column("published_question_version_id", sa.String(length=36), sa.ForeignKey("question_versions.id"), nullable=True))
    op.add_column("generated_candidates", sa.Column("published_at", sa.DateTime(), nullable=True))
    op.add_column("generated_candidates", sa.Column("publish_status", sa.String(length=32), nullable=False, server_default="not_published"))
    op.create_index("ix_generated_candidates_published_question_id", "generated_candidates", ["published_question_id"])
    op.create_index("ix_generated_candidates_published_question_version_id", "generated_candidates", ["published_question_version_id"])
    op.create_index("ix_generated_candidates_publish_status", "generated_candidates", ["publish_status"])


def downgrade() -> None:
    op.drop_index("ix_generated_candidates_publish_status", table_name="generated_candidates")
    op.drop_index("ix_generated_candidates_published_question_version_id", table_name="generated_candidates")
    op.drop_index("ix_generated_candidates_published_question_id", table_name="generated_candidates")
    op.drop_column("generated_candidates", "publish_status")
    op.drop_column("generated_candidates", "published_at")
    op.drop_column("generated_candidates", "published_question_version_id")
    op.drop_column("generated_candidates", "published_question_id")

    op.drop_index("ix_intervention_records_evidence_hash", table_name="intervention_records")
    op.drop_index("ix_intervention_records_prefetch_trigger_submission_id", table_name="intervention_records")
    op.drop_index("ix_intervention_records_prefetch_status", table_name="intervention_records")
    op.drop_index("ix_intervention_records_intervention_cycle", table_name="intervention_records")
    op.drop_column("intervention_records", "available_after_wrong_count")
    op.drop_column("intervention_records", "evidence_hash")
    op.drop_column("intervention_records", "prefetch_trigger_submission_id")
    op.drop_column("intervention_records", "prefetch_status")
    op.drop_column("intervention_records", "intervention_cycle")

    op.drop_index("ix_submissions_solution_revealed", table_name="submissions")
    op.drop_index("ix_submissions_excluded_from_mastery", table_name="submissions")
    op.drop_index("ix_submissions_effective_wrong", table_name="submissions")
    op.drop_column("submissions", "help_level_reached")
    op.drop_column("submissions", "solution_revealed")
    op.drop_column("submissions", "excluded_from_mastery")
    op.drop_column("submissions", "effective_wrong")

    op.drop_index("ix_questions_solution_style", table_name="questions")
    op.drop_index("ix_questions_question_fault_status", table_name="questions")
    op.drop_index("ix_questions_eligible_for_test", table_name="questions")
    op.drop_index("ix_questions_eligible_for_practice", table_name="questions")
    op.drop_index("ix_questions_publish_status", table_name="questions")
    op.drop_column("questions", "solution_style")
    op.drop_column("questions", "question_trust_score")
    op.drop_column("questions", "question_fault_confidence")
    op.drop_column("questions", "question_fault_status")
    op.drop_column("questions", "eligible_for_test")
    op.drop_column("questions", "eligible_for_practice")
    op.drop_column("questions", "publish_status")
