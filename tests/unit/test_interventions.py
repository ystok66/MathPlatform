from types import SimpleNamespace

from sqlalchemy import create_engine, select
from fastapi import HTTPException
from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.interventions import VARIANT_LEVELS, advance_intervention, create_variant_question, prefetch_intervention, record_variant_manual_review, review_variant_quality, trigger_intervention
from apps.api.app.models import AiRunLog, AnswerEvent, AnswerSpec, GenerationJob, InterventionRecord, PracticeSession, Question, QuestionVariant, SourceFile
from apps.api.app.routers.practice import MasteryCheckRequest, give_up_question, get_help_state, get_session_state, record_event, request_mastery_check, request_solution_explanation, reveal_next_help, submit_answer
from apps.api.app.schemas import PracticeEventRequest, SolutionExplanationRequest, SubmitAnswerRequest


def mock_ai_settings():
    return SimpleNamespace(
        default_model="mock-model",
        model_provider="mock",
        liaobots_api_key=None,
        responses_api_key=None,
        anthropic_api_key=None,
        gemini_api_key=None,
        liaobots_base_url="",
        responses_base_url="",
        anthropic_base_url="",
        gemini_base_url="",
        planner_model=None,
        generator_model=None,
        reviewer_model=None,
        reporter_model=None,
        diagnostician_model=None,
        hint_generator_model=None,
        fault_auditor_model=None,
        set_reviewer_model=None,
        answer_judge_model=None,
        solution_explainer_model=None,
    )


def seeded_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=1)
    db.add(source)
    db.add(
        Question(
            id="q1",
            source_file_id=source.id,
            status="verified",
            source_name="source.json",
            stem_preview="一个两位数的十位数字比个位数字大2，两个数字之和是10。这个两位数是多少？",
            difficulty_star=3,
            premium=False,
            family_id="family-q1",
        )
    )
    db.add(
        AnswerSpec(
            question_id="q1",
            status="verified",
            answer_type="integer",
            canonical_answer="64",
            normalized_spec={},
            raw_answer_spec={
                "status": "verified",
                "answer_type": "integer",
                "canonical_answer": "64",
                "acceptable_answers": [],
                "tolerance": {"type": "exact", "value": 0},
                "unit": None,
                "order_sensitive": True,
                "grading_method": "numeric_exact",
            },
            verification={},
        )
    )
    db.add(PracticeSession(id="s1", mode="practice", config={}, question_ids=["q1"]))
    db.commit()
    return db


def test_variant_definitions_cover_l1_l2_l3_and_create_family_link():
    db = seeded_db()
    question, link = create_variant_question(db, "q1", level="L2", purpose="hint")

    assert set(VARIANT_LEVELS) == {"L1", "L2", "L3"}
    assert question.source_name == "启发题"
    assert question.family_id == "family-q1"
    assert link.base_question_id == "q1"
    assert link.variant_question_id == question.id
    assert link.variant_level == "L2"
    assert link.transformation["not_number_only_variant"] is True
    assert "semantic_similarity" in link.transformation["similarity"]
    assert link.transformation["requires_manual_review"] is False


def test_variant_quality_and_manual_review_are_recorded_on_link():
    db = seeded_db()
    _, link = create_variant_question(db, "q1", level="L1", purpose="hint")

    quality = review_variant_quality(db, link.id)
    manual = record_variant_manual_review(db, link.id, approved=True, reviewer_notes="surface changed and load is lower")

    db.refresh(link)
    assert quality["schema_version"] == "variant_quality_review_v1"
    assert quality["provider"] == "mock"
    assert link.transformation["quality_review"]["variant_question_id"] == link.variant_question_id
    assert manual["status"] == "approved"
    assert link.transformation["manual_review"]["reviewer_notes"] == "surface changed and load is lower"
    assert link.transformation["requires_manual_review"] is False


def test_trigger_bridge_return_and_complete_intervention_flow():
    db = seeded_db()

    record = trigger_intervention(db, "s1", "q1", effective_error_count=3)
    db.commit()
    hint_link = db.scalar(select(QuestionVariant).where(QuestionVariant.variant_question_id == record.current_question_id))
    assert record.intervention_type == "hint"
    assert record.payload["state"] == "hint_assigned"
    assert hint_link.variant_level == "L2"

    bridge = advance_intervention(db, record.id, "bridge")
    assert bridge.intervention_type == "bridge"
    assert bridge.current_question_id != "q1"
    bridge_link = db.scalar(select(QuestionVariant).where(QuestionVariant.variant_question_id == bridge.current_question_id))
    assert bridge_link.variant_level == "L3"

    returned = advance_intervention(db, record.id, "return_original")
    assert returned.current_question_id == "q1"
    assert returned.payload["state"] == "returned_original"

    completed = advance_intervention(db, record.id, "complete", success=True)
    assert completed.status == "completed"
    assert completed.payload["transfer_success"] is True
    assert db.scalar(select(InterventionRecord).where(InterventionRecord.id == record.id)).status == "completed"


def test_submit_third_distinct_wrong_answer_auto_assigns_hint_question():
    db = seeded_db()

    for answer_text in ["1", "2", "3"]:
        submit_answer("s1", SubmitAnswerRequest(question_id="q1", answer_text=answer_text), db)

    state = get_session_state("s1", db)
    records = list(db.scalars(select(InterventionRecord)))
    due_events = list(db.scalars(select(AnswerEvent).where(AnswerEvent.event_type == "intervention_due")))
    prefetch_events = list(db.scalars(select(AnswerEvent).where(AnswerEvent.event_type == "intervention_prefetched")))

    assert state.intervention_due_question_ids == ["q1"]
    assert len(records) == 1
    assert records[0].intervention_cycle == 1
    assert records[0].prefetch_status == "shown"
    assert records[0].current_question_id != "q1"
    assert state.active_interventions[0]["current_question_id"] == records[0].current_question_id
    assert prefetch_events[0].payload["available_after_wrong_count"] == 3
    assert due_events[-1].payload["hint_question_id"] == records[0].current_question_id


def test_prefetch_is_cancelled_when_student_answers_correctly_before_hint_is_shown():
    db = seeded_db()

    submit_answer("s1", SubmitAnswerRequest(question_id="q1", answer_text="1"), db)
    submit_answer("s1", SubmitAnswerRequest(question_id="q1", answer_text="2"), db)
    submit_answer("s1", SubmitAnswerRequest(question_id="q1", answer_text="64"), db)

    records = list(db.scalars(select(InterventionRecord)))
    cancelled = list(db.scalars(select(AnswerEvent).where(AnswerEvent.event_type == "intervention_prefetch_cancelled")))

    assert len(records) == 1
    assert records[0].status == "cancelled"
    assert records[0].prefetch_status == "unused_prefetch"
    assert cancelled[0].payload["reason"] == "answered_correctly_before_hint"


def test_duplicate_wrong_answer_does_not_advance_intervention_cycle():
    db = seeded_db()

    submit_answer("s1", SubmitAnswerRequest(question_id="q1", answer_text="1"), db)
    duplicate = submit_answer("s1", SubmitAnswerRequest(question_id="q1", answer_text="1"), db)

    assert duplicate.duplicate_answer is True
    assert duplicate.effective_error_count == 1
    assert list(db.scalars(select(InterventionRecord))) == []


def test_prefetch_can_be_marked_stale_when_question_context_changes():
    db = seeded_db()

    submit_answer("s1", SubmitAnswerRequest(question_id="q1", answer_text="1"), db)
    submit_answer("s1", SubmitAnswerRequest(question_id="q1", answer_text="2"), db)
    record_event("s1", PracticeEventRequest(question_id="q1", event_type="context_changed", at_ms=100), db)

    record = db.scalar(select(InterventionRecord))
    event = db.scalar(select(AnswerEvent).where(AnswerEvent.event_type == "intervention_prefetch_stale"))

    assert record.status == "stale"
    assert record.prefetch_status == "stale"
    assert event.payload["reason"] == "context_changed"


def test_second_cycle_prefetches_at_fourth_effective_wrong_and_shows_at_fifth():
    db = seeded_db()

    first = trigger_intervention(db, "s1", "q1", effective_error_count=3)
    advance_intervention(db, first.id, "complete", success=True)
    second_prefetch = prefetch_intervention(db, "s1", "q1", effective_error_count=4, trigger_submission_id="sub4")
    second_show = trigger_intervention(db, "s1", "q1", effective_error_count=5)

    assert second_prefetch is not None
    assert second_prefetch.intervention_cycle == 2
    assert second_prefetch.prefetch_status == "shown"
    assert second_prefetch.id == second_show.id
    link = db.scalar(select(QuestionVariant).where(QuestionVariant.variant_question_id == second_show.current_question_id))
    assert link.variant_level == "L1"


def test_layered_help_unlocks_by_effective_wrong_count_and_records_answer_debt():
    db = seeded_db()

    state0 = get_help_state("s1", "q1", db)
    assert state0["allowed_level"] == 0
    assert state0["current_help"]["level"] == 0
    try:
        reveal_next_help("s1", "q1", db=db)
        assert False, "help should not unlock before an effective wrong answer"
    except HTTPException as exc:
        assert exc.status_code == 403

    submit_answer("s1", SubmitAnswerRequest(question_id="q1", answer_text="1"), db)
    state1 = get_help_state("s1", "q1", db)
    revealed = reveal_next_help("s1", "q1", db=db)
    give_up = give_up_question("s1", "q1", db=db)
    debt_events = list(db.scalars(select(AnswerEvent).where(AnswerEvent.event_type == "answer_reveal_debt_created")))

    assert state1["allowed_level"] == 1
    assert revealed["reached_level"] == 1
    assert give_up["independent_mastery_credit"] is False
    assert give_up["answer_locked"] is True
    try:
        submit_answer("s1", SubmitAnswerRequest(question_id="q1", answer_text="64"), db)
        assert False, "submitting after reveal must be blocked"
    except HTTPException as exc:
        assert exc.status_code == 409
    assert debt_events[0].payload["status"] == "open"


def test_solution_explanation_requires_reveal_and_records_history(monkeypatch):
    db = seeded_db()
    monkeypatch.setattr("apps.api.app.solution_explainer.get_settings", mock_ai_settings)

    try:
        request_solution_explanation("s1", "q1", SolutionExplanationRequest(question_id="q1", message="为什么答案唯一？"), db)
        assert False, "solution explanation must require answer reveal first"
    except HTTPException as exc:
        assert exc.status_code == 409

    give_up_question("s1", "q1", db=db)
    result = request_solution_explanation(
        "s1",
        "q1",
        SolutionExplanationRequest(question_id="q1", message="为什么答案唯一？", at_ms=20),
        db,
    )

    assert result.status == "ok"
    assert "为什么答案唯一" in result.explanation
    assert db.scalar(select(AnswerEvent).where(AnswerEvent.event_type == "solution_explanation_requested")) is not None
    assert db.scalar(select(AnswerEvent).where(AnswerEvent.event_type == "solution_explanation_answered")) is not None
    assert db.scalar(select(AiRunLog).where(AiRunLog.role == "solution_explainer")) is not None


def test_mastery_check_generates_same_star_question_and_appends_to_session():
    db = seeded_db()

    submit_answer("s1", SubmitAnswerRequest(question_id="q1", answer_text="1"), db)
    state = request_mastery_check("s1", "q1", BackgroundTasks(), MasteryCheckRequest(run_now=True, use_mock=True), db)
    record = db.scalar(select(InterventionRecord).where(InterventionRecord.intervention_type == "mastery_check"))
    job = db.scalar(select(GenerationJob).where(GenerationJob.id == record.payload["generation_job_id"]))
    generated = db.get(Question, record.current_question_id)
    link = db.scalar(
        select(QuestionVariant).where(
            QuestionVariant.base_question_id == "q1",
            QuestionVariant.variant_question_id == record.current_question_id,
            QuestionVariant.variant_level == "mastery_check",
        )
    )
    ready_event = db.scalar(select(AnswerEvent).where(AnswerEvent.event_type == "mastery_check_ready"))

    assert record.prefetch_status == "ready"
    assert record.payload["state"] == "ready_to_start"
    assert record.current_question_id != "q1"
    assert state.question_ids == ["q1", record.current_question_id]
    assert job.status == "accepted"
    assert generated.difficulty_star == 3
    assert generated.family_id == "family-q1"
    assert link.purpose == "mastery_check"
    assert ready_event.payload["check_question_id"] == record.current_question_id


def test_answering_mastery_check_marks_record_completed():
    db = seeded_db()

    state = request_mastery_check("s1", "q1", BackgroundTasks(), MasteryCheckRequest(run_now=True, use_mock=True), db)
    check_question_id = state.question_ids[-1]
    check_answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == check_question_id))
    submitted = submit_answer("s1", SubmitAnswerRequest(question_id=check_question_id, answer_text=check_answer.canonical_answer), db)
    record = db.scalar(select(InterventionRecord).where(InterventionRecord.intervention_type == "mastery_check"))
    completed_event = db.scalar(select(AnswerEvent).where(AnswerEvent.event_type == "mastery_check_completed"))

    assert submitted.is_correct is True
    assert submitted.result["mastery_check"]["state"] == "completed"
    assert record.status == "completed"
    assert record.prefetch_status == "completed"
    assert completed_event.payload["check_question_id"] == check_question_id


def test_strict_test_disables_solution_reveal():
    db = seeded_db()
    db.add(PracticeSession(id="strict-s1", mode="strict_test", config={"selection": {"strict_no_ai": True}}, question_ids=["q1"]))
    db.commit()

    state = get_help_state("strict-s1", "q1", db)

    assert state["strict_no_ai"] is True
    assert state["allowed_level"] == 0
    try:
        give_up_question("strict-s1", "q1", db=db)
        assert False, "strict mode must not reveal solutions"
    except HTTPException as exc:
        assert exc.status_code == 403


def test_strict_test_disables_mastery_check_generation():
    db = seeded_db()
    db.add(PracticeSession(id="strict-s2", mode="strict_test", config={"selection": {"strict_no_ai": True}}, question_ids=["q1"]))
    db.commit()

    try:
        request_mastery_check("strict-s2", "q1", BackgroundTasks(), MasteryCheckRequest(), db)
        assert False, "strict mode must not start mastery check generation"
    except HTTPException as exc:
        assert exc.status_code == 403
