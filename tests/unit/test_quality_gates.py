from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app.ai_gateway import mock_candidate
from apps.api.app.ai_providers import MockProvider, ProviderChain, ProviderResult
from apps.api.app.db import Base
from apps.api.app.models import AiRunLog, AnswerEvent, AnswerSpec, PracticeSession, Question, SourceFile, Submission
from apps.api.app.quality_gates import (
    QUALITY_SUITE_NAMES,
    ai_quality_suite_report,
    cost_guard,
    dedupe_threshold_report,
    dual_reviewer_consistency,
    fixed_quality_case_evaluation_report,
    fixed_quality_case_report,
    golden_sample,
    prompt_injection_regression_report,
    prompt_regression_report,
    run_fixed_quality_cases,
)
from apps.api.app.routers.practice import get_session_state
from apps.api.app.semantic_similarity import semantic_hash


class NetworkDownProvider:
    name = "network_down"

    def run_json(self, messages, role, model, context):
        return ProviderResult(provider=self.name, model=model, role=role, payload=None, raw_response={}, latency_ms=10, error="network down")


def seeded_db(question_count: int = 220) -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=question_count)
    db.add(source)
    for index in range(question_count):
        qid = f"q{index:03d}"
        db.add(
            Question(
                id=qid,
                source_file_id=source.id,
                status="verified",
                source_name=f"source-{index % 4}.json",
                stem_preview=f"题目 {index}：求稳定答案。",
                difficulty_star=index % 5 + 1,
                premium=False,
                normalized_hash=f"h-{index // 2}" if index < 4 else f"h-{index}",
                semantic_hash=semantic_hash(f"题目 {index // 2 if index < 4 else index}：求稳定答案。"),
                family_id=f"family-{index // 3}",
            )
        )
        db.add(
            AnswerSpec(
                question_id=qid,
                status="verified",
                answer_type="integer",
                canonical_answer="1",
                normalized_spec={},
                raw_answer_spec={"status": "verified", "answer_type": "integer", "canonical_answer": "1"},
                verification={},
            )
        )
    db.commit()
    return db


def test_golden_sample_collects_200_stable_question_handles():
    db = seeded_db()
    sample = golden_sample(db, size=200)

    assert len(sample) == 200
    assert sample[0]["id"] == "q000"
    assert all(row["stem_hash"] for row in sample)
    assert all(row["canonical_answer"] == "1" for row in sample)
    assert all(row["manual_review_status"] == "source_answer_spec_verified" for row in sample)


def test_prompt_regression_required_phrases_and_hashes():
    report = prompt_regression_report(Path("prompts"))

    assert report["ok"] is True
    assert set(report["prompts"]) == {
        "generator_v1.md",
        "reviewer_v1.md",
        "reporter_v1.md",
        "generator_v2.md",
        "reviewer_v2.md",
        "hint_question_generator_v1.md",
        "question_fault_auditor_v1.md",
        "diversity_planner_v1.md",
        "set_reviewer_v1.md",
        "answer_judge_v1.md",
        "question_support_v1.md",
        "solution_explainer_v1.md",
    }
    assert all(row["sha256"] for row in report["prompts"].values())


def test_prompt_injection_regression_required_boundary_phrases():
    report = prompt_injection_regression_report(Path("prompts"))

    assert report["ok"] is True
    assert set(report["prompts"]) == {
        "generator_v2.md",
        "reviewer_v2.md",
        "answer_judge_v1.md",
        "question_support_v1.md",
        "solution_explainer_v1.md",
    }
    assert all(not row["missing_required_phrases"] for row in report["prompts"].values())


def test_fixed_quality_case_report_loads_suite_inventory():
    report = fixed_quality_case_report(Path("."))

    assert report["schema_version"] == "ai_quality_fixed_cases_v1"
    assert report["status"] == "ready"
    assert report["case_count"] >= 27
    assert report["by_suite"]["star_calibration"]["case_count"] >= 15
    assert report["by_suite"]["star_calibration"]["polarity_counts"]["good"] >= 15
    assert report["by_suite"]["format_quality"]["case_count"] >= 4
    assert report["by_suite"]["answer_judge"]["polarity_counts"]["bad"] >= 1
    assert report["by_suite"]["report_faithfulness"]["polarity_counts"]["good"] >= 1


def test_fixed_quality_case_evaluation_report_summarizes_latest_logs():
    db = seeded_db()
    fixed_cases = fixed_quality_case_report(Path("."))
    db.add_all(
        [
            AiRunLog(
                role="format_quality",
                provider="mock",
                model="mock",
                prompt_version="fixed_eval_v1",
                input_hash="case-1",
                request_payload={"quality_case_id": "format-vertical-arithmetic-codeblock"},
                response_payload={"passed": True},
                token_usage={},
            ),
            AiRunLog(
                role="answer_judge",
                provider="mock",
                model="mock",
                prompt_version="fixed_eval_v1",
                input_hash="case-2",
                request_payload={"quality_case_id": "answer-judge-unordered-prime-set-missing"},
                response_payload={"evaluation": {"matches_expected": False}},
                token_usage={},
            ),
            AiRunLog(
                role="reporter",
                provider="mock",
                model="mock",
                prompt_version="fixed_eval_v1",
                input_hash="case-3",
                request_payload={"fixed_quality_case": {"id": "report-faithfulness-evidence-cited"}},
                response_payload={"result": {"ok": True}},
                token_usage={},
            ),
        ]
    )
    db.commit()

    report = fixed_quality_case_evaluation_report(db, fixed_cases)

    assert report["evaluated_case_count"] == 3
    assert report["passed"] == 2
    assert report["failed"] == 1
    assert report["pass_rate"] == round(2 / 3, 4)
    assert report["by_suite"]["answer_judge"]["failed_case_ids"] == ["answer-judge-unordered-prime-set-missing"]
    assert "fixed_quality_case_evaluation_failures" in report["gaps"]


def test_run_fixed_quality_cases_mock_writes_evaluation_logs():
    db = seeded_db()

    result = run_fixed_quality_cases(
        db,
        Path("."),
        settings=SimpleNamespace(default_model="mock"),
        use_mock=True,
        suites=["answer_judge"],
        max_cases=2,
    )

    logs = [row for row in db.query(AiRunLog).all() if row.prompt_version == "fixed_quality_case_eval_v1"]
    report = fixed_quality_case_evaluation_report(db, fixed_quality_case_report(Path(".")))

    assert result["schema_version"] == "fixed_quality_case_run_v1"
    assert result["evaluated_count"] == 2
    assert result["failed"] == 0
    assert len(logs) == 2
    assert all(log.request_payload["fixed_quality_case"]["suite"] == "answer_judge" for log in logs)
    assert report["evaluated_case_count"] >= 2
    assert report["passed"] >= 2


def test_run_fixed_star_calibration_cases_mock_covers_all_target_stars():
    db = seeded_db()

    result = run_fixed_quality_cases(
        db,
        Path("."),
        settings=SimpleNamespace(default_model="mock"),
        use_mock=True,
        suites=["star_calibration"],
        max_cases=20,
    )
    report = ai_quality_suite_report(db, root=Path("."))
    star_suite = {row["name"]: row for row in report["suites"]}["star_calibration"]

    assert result["evaluated_count"] >= 15
    assert result["failed"] == 0
    assert result["unknown"] == 0
    assert star_suite["status"] == "ready"
    assert star_suite["metrics"]["fixed_case_target_star_distribution"] == {"1": 3, "2": 3, "3": 3, "4": 3, "5": 3}
    assert star_suite["metrics"]["fixed_case_pass_rate"] == 1
    assert star_suite["gaps"] == []


def test_dual_reviewer_consistency_and_cost_guard():
    candidate = mock_candidate("structural_elegance", {"difficulty_star": 3}, [])
    consistency = dual_reviewer_consistency(candidate)
    chain_consistency = dual_reviewer_consistency(
        candidate,
        ProviderChain([MockProvider()], max_retries=0, max_concurrency=1),
        ProviderChain([MockProvider()], max_retries=0, max_concurrency=1),
    )

    assert consistency["consistent"] is True
    assert chain_consistency["consistent"] is True
    assert chain_consistency["review_a"]["provider"] == "mock"
    assert chain_consistency["review_b"]["provider"] == "mock"
    assert cost_guard(0.012, 0.02)["ok"] is True
    assert cost_guard(0.03, 0.02)["ok"] is False


def test_dedupe_threshold_report_surfaces_duplicate_groups():
    db = seeded_db()
    report = dedupe_threshold_report(db)

    assert report["question_count"] == 220
    assert report["normalized_duplicate_groups"] >= 2
    assert report["semantic_hash_duplicate_groups"] >= 1
    assert report["semantic_near_duplicate_pairs"] >= 1
    assert report["family_cluster_groups"] > 0


def test_ai_quality_suite_report_unifies_fixed_suites_and_runtime_usage():
    db = seeded_db()
    db.add(
        AiRunLog(
            role="generator",
            provider="mock",
            model="mock",
            prompt_version="v1",
            input_hash="h",
            request_payload={},
            response_payload={},
            latency_ms=120,
            token_usage={"prompt_tokens": 100, "completion_tokens": 30, "estimated_cost_usd": 0.02},
        )
    )
    db.commit()

    report = ai_quality_suite_report(db, root=Path("."), max_cost_usd=1.0)
    suites = {row["name"]: row for row in report["suites"]}

    assert report["schema_version"] == "ai_quality_suite_v1"
    assert report["suite_names"] == list(QUALITY_SUITE_NAMES)
    assert set(suites) == set(QUALITY_SUITE_NAMES)
    assert suites["math_correctness"]["status"] == "ready"
    assert suites["math_correctness"]["metrics"]["verified_answer_specs"] == 200
    assert suites["prompt_injection"]["ok"] is True
    assert suites["duplicate_attack"]["status"] == "ready"
    assert suites["duplicate_attack"]["ok"] is False
    assert suites["star_calibration"]["metrics"]["fixed_case_count"] >= 15
    assert suites["star_calibration"]["metrics"]["fixed_case_target_star_distribution"] == {"1": 3, "2": 3, "3": 3, "4": 3, "5": 3}
    assert suites["star_calibration"]["gaps"] == ["needs_model_evaluation_on_fixed_star_calibration_cases"]
    assert suites["format_quality"]["metrics"]["fixed_case_count"] >= 4
    assert suites["format_quality"]["gaps"] == ["needs_model_evaluation_on_fixed_visual_format_cases"]
    assert suites["answer_judge"]["metrics"]["fixed_case_count"] >= 4
    assert suites["report_faithfulness"]["metrics"]["fixed_case_count"] >= 4
    assert report["raw_reports"]["fixed_quality_cases"]["status"] == "ready"
    assert report["raw_reports"]["fixed_quality_case_evaluations"]["status"] == "not_run"
    assert suites["cost_performance"]["metrics"]["calls"] == 1
    assert suites["cost_performance"]["metrics"]["cost_guard"]["ok"] is True
    assert report["raw_reports"]["prompt_regression"]["ok"] is True


def test_ai_quality_suite_marks_fixed_case_failures_as_failed_suite():
    db = seeded_db()
    db.add(
        AiRunLog(
            role="format_quality",
            provider="mock",
            model="mock",
            prompt_version="fixed_eval_v1",
            input_hash="case-fail",
            request_payload={"quality_case_id": "format-missing-diagram-reference"},
            response_payload={"passed": False},
            token_usage={},
        )
    )
    db.commit()

    report = ai_quality_suite_report(db, root=Path("."))
    suites = {row["name"]: row for row in report["suites"]}

    assert suites["format_quality"]["status"] == "failed"
    assert suites["format_quality"]["ok"] is False
    assert suites["format_quality"]["metrics"]["fixed_case_evaluated_count"] == 1
    assert suites["format_quality"]["metrics"]["fixed_case_pass_rate"] == 0
    assert "format_quality_fixed_case_evaluation_failures" in suites["format_quality"]["gaps"]


def test_ai_quality_suite_report_without_database_marks_data_suites_not_run():
    report = ai_quality_suite_report(None, root=Path("."))
    suites = {row["name"]: row for row in report["suites"]}

    assert report["schema_version"] == "ai_quality_suite_v1"
    assert suites["duplicate_attack"]["status"] == "not_run"
    assert suites["duplicate_attack"]["ok"] is True
    assert suites["cost_performance"]["status"] == "not_run"
    assert suites["prompt_injection"]["status"] == "ready"


def test_network_timeout_fallback_to_mock_provider():
    chain = ProviderChain([NetworkDownProvider(), MockProvider()], max_retries=0, max_concurrency=1)
    results = chain.run_json(
        [{"role": "user", "content": "{}"}],
        "generator",
        {"default": "mock"},
        {"mock_factory": lambda: {"status": "reject", "candidate_id": "fallback", "reject_reason": "network"}},
    )

    assert [row.provider for row in results] == ["network_down", "mock"]
    assert results[-1].payload["candidate_id"] == "fallback"


def test_strict_100_interruption_restore_uses_session_state_and_events():
    db = seeded_db()
    question_ids = [f"q{index:03d}" for index in range(100)]
    session = PracticeSession(
        id="strict-1",
        mode="strict_test",
        status="paused",
        question_ids=question_ids,
        config={"selection": {"breakpoints": [40, 80], "strict_no_ai": True}, "break_confirmed": [40]},
    )
    db.add(session)
    for index, qid in enumerate(question_ids[:42]):
        db.add(
            Submission(
                session_id=session.id,
                question_id=qid,
                answer_text="1",
                is_correct=True,
                grading_result={"is_correct": True},
                elapsed_ms=1000 + index,
            )
        )
    db.add(AnswerEvent(session_id=session.id, question_id="", event_type="paused", payload={"at_ms": 42000}))
    db.commit()

    state = get_session_state("strict-1", db)

    assert state.mode == "strict_test"
    assert state.status == "paused"
    assert state.answered_count == 42
    assert state.next_breakpoint is None
    assert state.breakpoints == [40, 80]
    assert state.strict_no_ai is True
