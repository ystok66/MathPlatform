from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from .db import Base
from .models import DEFAULT_LEARNER_ID, utc_now


QUESTION_COLUMNS = {
    "publish_status": "VARCHAR(32) NOT NULL DEFAULT 'source_verified'",
    "eligible_for_practice": "BOOLEAN NOT NULL DEFAULT 1",
    "eligible_for_test": "BOOLEAN NOT NULL DEFAULT 1",
    "question_fault_status": "VARCHAR(32) NOT NULL DEFAULT 'none'",
    "question_fault_confidence": "FLOAT",
    "question_trust_score": "FLOAT NOT NULL DEFAULT 1.0",
    "solution_style": "VARCHAR(32) NOT NULL DEFAULT 'elementary_preferred'",
    "source_difficulty_star": "INTEGER",
    "calibrated_difficulty_star": "INTEGER",
    "source_premium": "BOOLEAN NOT NULL DEFAULT 0",
    "calibrated_premium": "BOOLEAN NOT NULL DEFAULT 0",
    "calibration_version": "VARCHAR(64) NOT NULL DEFAULT 'source_original_v1'",
    "exact_hash": "VARCHAR(64)",
    "normalized_hash": "VARCHAR(64)",
    "semantic_hash": "VARCHAR(64)",
    "family_id": "VARCHAR(128)",
}

TAXONOMY_COLUMNS = {
    "version": "VARCHAR(32) NOT NULL DEFAULT 'taxonomy_v1'",
    "status": "VARCHAR(32) NOT NULL DEFAULT 'active'",
    "description": "TEXT",
}

SUBMISSION_COLUMNS = {
    "question_version_id": "VARCHAR(36)",
    "effective_wrong": "BOOLEAN NOT NULL DEFAULT 0",
    "excluded_from_mastery": "BOOLEAN NOT NULL DEFAULT 0",
    "solution_revealed": "BOOLEAN NOT NULL DEFAULT 0",
    "help_level_reached": "INTEGER NOT NULL DEFAULT 0",
}

PRACTICE_SESSION_COLUMNS = {
    "learner_id": f"VARCHAR(36) NOT NULL DEFAULT '{DEFAULT_LEARNER_ID}'",
}

INTERVENTION_COLUMNS = {
    "intervention_cycle": "INTEGER NOT NULL DEFAULT 1",
    "prefetch_status": "VARCHAR(32) NOT NULL DEFAULT 'not_prefetched'",
    "prefetch_trigger_submission_id": "VARCHAR(36)",
    "evidence_hash": "VARCHAR(64)",
    "available_after_wrong_count": "INTEGER NOT NULL DEFAULT 3",
}

GENERATED_CANDIDATE_COLUMNS = {
    "published_question_id": "VARCHAR(128)",
    "published_question_version_id": "VARCHAR(36)",
    "published_at": "DATETIME",
    "publish_status": "VARCHAR(32) NOT NULL DEFAULT 'not_published'",
}

GENERATION_JOB_COLUMNS = {
    "idempotency_key": "VARCHAR(128)",
}

GENERATION_BATCH_COLUMNS = {
    "queue_claim_token": "VARCHAR(36)",
    "queue_claimed_at": "DATETIME",
}


def add_missing_columns(engine: Engine, table_name: str, columns: dict[str, str]) -> None:
    inspector = inspect(engine)
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    with engine.begin() as conn:
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}"))


def ensure_sqlite_dev_schema(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    Base.metadata.create_all(engine)
    add_missing_columns(engine, "questions", QUESTION_COLUMNS)
    add_missing_columns(engine, "taxonomy_tags", TAXONOMY_COLUMNS)
    add_missing_columns(engine, "practice_sessions", PRACTICE_SESSION_COLUMNS)
    add_missing_columns(engine, "submissions", SUBMISSION_COLUMNS)
    add_missing_columns(engine, "intervention_records", INTERVENTION_COLUMNS)
    add_missing_columns(engine, "generated_candidates", GENERATED_CANDIDATE_COLUMNS)
    add_missing_columns(engine, "generation_jobs", GENERATION_JOB_COLUMNS)
    add_missing_columns(engine, "generation_batches", GENERATION_BATCH_COLUMNS)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE intervention_records "
                "SET status = 'cancelled', prefetch_status = 'unused_prefetch' "
                "WHERE id IN ("
                "  SELECT id FROM ("
                "    SELECT id, ROW_NUMBER() OVER ("
                "      PARTITION BY session_id, original_question_id, intervention_type, intervention_cycle "
                "      ORDER BY created_at, id"
                "    ) AS rn "
                "    FROM intervention_records "
                "    WHERE intervention_type = 'mastery_check' AND status = 'active'"
                "  ) WHERE rn > 1"
                ")"
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_mastery_intervention_cycle "
                "ON intervention_records(session_id, original_question_id, intervention_type, intervention_cycle) "
                "WHERE intervention_type = 'mastery_check' AND status = 'active'"
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_mastery_intervention_evidence_hash "
                "ON intervention_records(evidence_hash) "
                "WHERE intervention_type = 'mastery_check' AND status = 'active' AND evidence_hash IS NOT NULL"
            )
        )
        conn.execute(text("UPDATE questions SET publish_status = COALESCE(publish_status, 'source_verified')"))
        conn.execute(text("UPDATE questions SET eligible_for_practice = COALESCE(eligible_for_practice, 1)"))
        conn.execute(text("UPDATE questions SET eligible_for_test = COALESCE(eligible_for_test, 1)"))
        conn.execute(text("UPDATE questions SET question_fault_status = COALESCE(question_fault_status, 'none')"))
        conn.execute(text("UPDATE questions SET question_trust_score = COALESCE(question_trust_score, 1.0)"))
        conn.execute(text("UPDATE questions SET solution_style = COALESCE(solution_style, 'elementary_preferred')"))
        conn.execute(text("UPDATE questions SET source_difficulty_star = COALESCE(source_difficulty_star, difficulty_star)"))
        conn.execute(text("UPDATE questions SET calibrated_difficulty_star = COALESCE(calibrated_difficulty_star, difficulty_star)"))
        conn.execute(text("UPDATE questions SET source_premium = COALESCE(source_premium, premium, 0)"))
        conn.execute(text("UPDATE questions SET calibrated_premium = COALESCE(calibrated_premium, premium, 0)"))
        conn.execute(text("UPDATE questions SET calibration_version = COALESCE(calibration_version, 'source_original_v1')"))
        conn.execute(text("UPDATE taxonomy_tags SET version = COALESCE(version, 'taxonomy_v1')"))
        conn.execute(text("UPDATE taxonomy_tags SET status = COALESCE(status, 'active')"))
        conn.execute(
            text(
                "INSERT OR IGNORE INTO learner_profiles "
                "(id, name, status, display_order, settings, created_at, updated_at) "
                "VALUES (:id, :name, 'active', 0, '{}', :created_at, :updated_at)"
            ),
            {
                "id": DEFAULT_LEARNER_ID,
                "name": "默认学习者",
                "created_at": utc_now(),
                "updated_at": utc_now(),
            },
        )
        conn.execute(text("UPDATE practice_sessions SET learner_id = COALESCE(learner_id, :learner_id)"), {"learner_id": DEFAULT_LEARNER_ID})
        conn.execute(text("UPDATE submissions SET effective_wrong = COALESCE(effective_wrong, 0)"))
        conn.execute(text("UPDATE submissions SET excluded_from_mastery = COALESCE(excluded_from_mastery, 0)"))
        conn.execute(text("UPDATE submissions SET solution_revealed = COALESCE(solution_revealed, 0)"))
        conn.execute(text("UPDATE submissions SET help_level_reached = COALESCE(help_level_reached, 0)"))
        conn.execute(text("UPDATE intervention_records SET intervention_cycle = COALESCE(intervention_cycle, 1)"))
        conn.execute(text("UPDATE intervention_records SET prefetch_status = COALESCE(prefetch_status, 'not_prefetched')"))
        conn.execute(text("UPDATE intervention_records SET available_after_wrong_count = COALESCE(available_after_wrong_count, 3)"))
        conn.execute(text("UPDATE generated_candidates SET publish_status = COALESCE(publish_status, 'not_published')"))
