"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

json_type = sa.JSON()


def upgrade() -> None:
    op.create_table(
        "source_files",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("filename", sa.String(length=512), nullable=False, unique=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "questions",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("source_file_id", sa.String(length=36), sa.ForeignKey("source_files.id"), nullable=False),
        sa.Column("current_version_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_name", sa.String(length=512), nullable=False),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("original_number", sa.String(length=64), nullable=True),
        sa.Column("stem_preview", sa.Text(), nullable=False),
        sa.Column("difficulty_star", sa.Integer(), nullable=True),
        sa.Column("premium", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_questions_status", "questions", ["status"])
    op.create_index("ix_questions_source_name", "questions", ["source_name"])
    op.create_index("ix_questions_difficulty_star", "questions", ["difficulty_star"])
    op.create_index("ix_questions_premium", "questions", ["premium"])
    op.create_table(
        "question_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("raw_json", json_type, nullable=False),
        sa.Column("question_v2", json_type, nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("question_id", "version", name="uq_question_version"),
    )
    op.create_index("ix_question_versions_question_id", "question_versions", ["question_id"])
    op.create_table(
        "answer_specs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("answer_type", sa.String(length=64), nullable=False),
        sa.Column("canonical_answer", sa.Text(), nullable=False),
        sa.Column("normalized_spec", json_type, nullable=False),
        sa.Column("raw_answer_spec", json_type, nullable=False),
        sa.Column("verification", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_answer_specs_status", "answer_specs", ["status"])
    op.create_table(
        "taxonomy_tags",
        sa.Column("id", sa.String(length=160), primary_key=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=False),
        sa.Column("parent_id", sa.String(length=160), sa.ForeignKey("taxonomy_tags.id"), nullable=True),
        sa.Column("aliases", json_type, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.UniqueConstraint("kind", "label", name="uq_taxonomy_kind_label"),
    )
    op.create_index("ix_taxonomy_tags_kind", "taxonomy_tags", ["kind"])
    op.create_index("ix_taxonomy_tags_label", "taxonomy_tags", ["label"])
    op.create_table(
        "question_tags",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("tag_id", sa.String(length=160), sa.ForeignKey("taxonomy_tags.id"), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("raw_label", sa.String(length=256), nullable=False),
        sa.UniqueConstraint("question_id", "tag_id", name="uq_question_tag"),
    )
    op.create_index("ix_question_tags_question_id", "question_tags", ["question_id"])
    op.create_index("ix_question_tags_tag_id", "question_tags", ["tag_id"])
    op.create_table(
        "practice_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("config", json_type, nullable=False),
        sa.Column("question_ids", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "submissions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("practice_sessions.id"), nullable=False),
        sa.Column("question_id", sa.String(length=128), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("thought_text", sa.Text(), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("grading_result", json_type, nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "answer_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("practice_sessions.id"), nullable=False),
        sa.Column("question_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request", json_type, nullable=False),
        sa.Column("progress", json_type, nullable=False),
        sa.Column("accepted_question_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_generation_jobs_status", "generation_jobs", ["status"])
    op.create_table(
        "generated_candidates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_id", sa.String(length=36), sa.ForeignKey("generation_jobs.id"), nullable=False),
        sa.Column("strategy", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("review", json_type, nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
    )
    op.create_table(
        "ai_run_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_id", sa.String(length=36), sa.ForeignKey("generation_jobs.id"), nullable=True),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("request_payload", json_type, nullable=False),
        sa.Column("response_payload", json_type, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("token_usage", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "ai_run_logs",
        "generated_candidates",
        "generation_jobs",
        "answer_events",
        "submissions",
        "practice_sessions",
        "question_tags",
        "taxonomy_tags",
        "answer_specs",
        "question_versions",
        "questions",
        "source_files",
    ):
        op.drop_table(table)
