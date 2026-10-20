import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from apps.api.app.db import Base
from apps.api.app.models import GenerationJob, QuestionDuplicateAlias, ReviewQueueItem
import apps.api.app.models  # noqa: F401
from apps.api.app.statuses import (
    ACTIVE_TASK_STATUSES,
    CANCEL_REQUESTED_BATCH_STATUSES,
    FAILED_GENERATION_STATUSES,
    GENERATION_BATCH_STATUSES,
    GENERATION_ITEM_STATUSES,
    GENERATION_JOB_STATUSES,
    STATUS_ACCEPTED,
    STATUS_CANCELLED,
    STATUS_CANCELLING,
    STATUS_COMPLETED,
    STATUS_DOMAIN_GENERATION_BATCH,
    STATUS_DOMAIN_GENERATION_ITEM,
    STATUS_DOMAIN_GENERATION_JOB,
    STATUS_DOMAIN_QUESTION_DUPLICATE_ALIAS,
    STATUS_DOMAIN_REVIEW_QUEUE,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_QUEUED,
    STATUS_REJECTED,
    STATUS_RUNNING,
    STATUS_TIMED_OUT,
    STATUS_VALUES_BY_DOMAIN,
    TERMINAL_BATCH_STATUSES,
    TERMINAL_JOB_STATUSES,
    allowed_status_transitions,
    assert_status_transition,
    checked_status_update_values,
    status_catalog,
    status_transition_allowed,
    status_value_allowed,
)


def test_generation_status_sets_do_not_overlap_active_and_terminal():
    assert ACTIVE_TASK_STATUSES == {STATUS_QUEUED, STATUS_RUNNING}
    assert not ACTIVE_TASK_STATUSES & TERMINAL_JOB_STATUSES
    assert not ACTIVE_TASK_STATUSES & TERMINAL_BATCH_STATUSES


def test_batch_and_job_terminal_statuses_are_explicit():
    assert TERMINAL_JOB_STATUSES == {STATUS_ACCEPTED, STATUS_REJECTED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMED_OUT}
    assert TERMINAL_BATCH_STATUSES == {STATUS_COMPLETED, STATUS_PARTIAL, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMED_OUT}
    assert CANCEL_REQUESTED_BATCH_STATUSES == {STATUS_CANCELLED, STATUS_CANCELLING}
    assert FAILED_GENERATION_STATUSES == {STATUS_FAILED, STATUS_TIMED_OUT, STATUS_REJECTED, STATUS_CANCELLED, STATUS_CANCELLING}
    assert GENERATION_JOB_STATUSES == ACTIVE_TASK_STATUSES | TERMINAL_JOB_STATUSES
    assert GENERATION_BATCH_STATUSES == {STATUS_QUEUED, STATUS_RUNNING, STATUS_CANCELLING} | TERMINAL_BATCH_STATUSES
    assert GENERATION_ITEM_STATUSES == {STATUS_QUEUED, STATUS_RUNNING, STATUS_ACCEPTED, STATUS_REJECTED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMED_OUT}


def test_status_catalog_exposes_domains_and_invariants():
    catalog = status_catalog()
    domains = {row["domain"]: row for row in catalog["domains"]}

    assert catalog["schema_version"] == "status_catalog_v1"
    assert catalog["domain_count"] == len(domains)
    assert catalog["status_count"] == sum(len(row["statuses"]) for row in domains.values())
    assert {
        "generation_job",
        "generation_batch",
        "generation_item",
        "question_status",
        "practice_mode",
        "review_queue",
        "question_repair",
        "question_duplicate_alias",
        "intervention_record",
        "intervention_prefetch",
        "wrong_question",
        "review_record",
    } <= set(domains)

    job_statuses = {row["value"]: row for row in domains["generation_job"]["statuses"]}
    assert {STATUS_ACCEPTED, STATUS_REJECTED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMED_OUT} <= {value for value, row in job_statuses.items() if row["terminal"]}
    assert {STATUS_QUEUED, STATUS_RUNNING} == {value for value, row in job_statuses.items() if row["active"]}
    assert STATUS_ACCEPTED in job_statuses[STATUS_RUNNING]["allowed_next"]
    assert job_statuses[STATUS_ACCEPTED]["allowed_next"] == []

    question_statuses = {row["value"]: row for row in domains["question_status"]["statuses"]}
    assert question_statuses["verified"]["blocks_practice"] is False
    assert question_statuses["needs_review"]["blocks_practice"] is True
    assert STATUS_VALUES_BY_DOMAIN["intervention_record"] >= {"active", "prefetched", "cancelled", "stale", "completed", "failed"}
    assert STATUS_VALUES_BY_DOMAIN["intervention_prefetch"] >= {"not_prefetched", "generating", "ready", "shown", "unused_prefetch", "stale", "failed", "completed"}
    assert STATUS_VALUES_BY_DOMAIN["wrong_question"] >= {"active", "reviewing", "resolved", "ignored"}
    assert STATUS_VALUES_BY_DOMAIN["review_record"] >= {"pending", "completed", "resolved"}
    assert STATUS_VALUES_BY_DOMAIN["review_queue"] >= {"open", "resolved", "closed"}
    assert STATUS_VALUES_BY_DOMAIN["question_publish_status"] >= {"retired_by_admin", "retired_question"}
    assert STATUS_VALUES_BY_DOMAIN["question_duplicate_alias"] == {"active", "reverted"}


def test_status_transition_helpers_reject_illegal_terminal_reopen():
    assert status_transition_allowed(STATUS_DOMAIN_GENERATION_JOB, STATUS_QUEUED, STATUS_RUNNING) is True
    assert status_transition_allowed(STATUS_DOMAIN_GENERATION_JOB, STATUS_RUNNING, STATUS_ACCEPTED) is True
    assert status_transition_allowed(STATUS_DOMAIN_GENERATION_JOB, STATUS_ACCEPTED, STATUS_RUNNING) is False
    assert allowed_status_transitions(STATUS_DOMAIN_GENERATION_BATCH, STATUS_CANCELLING) == (STATUS_CANCELLED, STATUS_TIMED_OUT)
    assert STATUS_REJECTED in allowed_status_transitions(STATUS_DOMAIN_GENERATION_ITEM, STATUS_ACCEPTED)

    try:
        assert_status_transition(STATUS_DOMAIN_GENERATION_JOB, STATUS_ACCEPTED, STATUS_RUNNING)
    except ValueError as exc:
        assert str(exc) == "invalid_status_transition:generation_job:accepted->running"
    else:
        raise AssertionError("terminal job status should not reopen")


def test_status_value_validation_rejects_values_outside_catalog():
    assert status_value_allowed(STATUS_DOMAIN_REVIEW_QUEUE, "closed") is True
    assert status_value_allowed(STATUS_DOMAIN_REVIEW_QUEUE, "bogus") is False

    with pytest.raises(ValueError) as exc:
        assert_status_transition(STATUS_DOMAIN_REVIEW_QUEUE, "open", "bogus")
    assert str(exc.value) == "invalid_status_value:review_queue:bogus"

    assert checked_status_update_values({"status": (STATUS_DOMAIN_REVIEW_QUEUE, "closed")}) == {"status": "closed"}
    with pytest.raises(ValueError) as exc:
        checked_status_update_values({"status": (STATUS_DOMAIN_REVIEW_QUEUE, "bogus")})
    assert str(exc.value) == "invalid_status_value:review_queue:bogus"


def test_orm_status_domain_guard_validates_direct_assignment():
    item = ReviewQueueItem(question_id="q1", reason_code="answer_spec_needs_review", severity="high", status="open", payload={})
    item.status = "closed"
    with pytest.raises(ValueError) as exc:
        item.status = "bogus"
    assert "ReviewQueueItem.status:invalid_status_value:review_queue:bogus" in str(exc.value)

    alias = QuestionDuplicateAlias(canonical_question_id="q1", alias_question_id="q2", status="active", reason="same normalized stem", reviewer="test", metadata_json={})
    alias.status = "reverted"
    with pytest.raises(ValueError) as exc:
        alias.status = "bogus"
    assert f"QuestionDuplicateAlias.status:invalid_status_value:{STATUS_DOMAIN_QUESTION_DUPLICATE_ALIAS}:bogus" in str(exc.value)

    job = GenerationJob(status=STATUS_QUEUED, request={}, progress={})
    job.status = STATUS_RUNNING
    job.status = STATUS_ACCEPTED
    with pytest.raises(ValueError) as exc:
        job.status = STATUS_RUNNING
    assert "GenerationJob.status:invalid_status_transition:generation_job:accepted->running" in str(exc.value)


def test_status_check_constraints_reject_invalid_values_in_new_sqlite_schema():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO generation_jobs "
                "(id, status, request, progress, created_at, updated_at) "
                "VALUES ('bad-job', 'bogus', '{}', '{}', '2026-07-23 00:00:00', '2026-07-23 00:00:00')"
            )
        )
