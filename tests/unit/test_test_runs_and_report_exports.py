from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import AnswerSpec, PracticeSession, Question, ReportExport, ReportFilterView, SourceFile, TestBlueprint as BlueprintModel, TestRun as RunModel
from apps.api.app.routers.practice import TestBlueprintRequest as BlueprintRequest, resume_test_run, save_test_blueprint, start_test_run, submit_answer
from apps.api.app.routers.reports import ReportExportRequest, ReportViewRequest, export_report, save_report_view
from apps.api.app.schemas import SubmitAnswerRequest


def seeded_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=3)
    db.add(source)
    for index in range(3):
        question_id = f"q{index + 1}"
        db.add(
            Question(
                id=question_id,
                source_file_id=source.id,
                status="verified",
                source_name="source.json",
                stem_preview=f"题目 {index + 1}",
                difficulty_star=3,
                premium=index == 0,
            )
        )
        db.add(
            AnswerSpec(
                question_id=question_id,
                status="verified",
                answer_type="integer",
                canonical_answer=str(index + 1),
                normalized_spec={},
                raw_answer_spec={
                    "status": "verified",
                    "answer_type": "integer",
                    "canonical_answer": str(index + 1),
                    "acceptable_answers": [],
                    "tolerance": {"type": "exact", "value": 0},
                    "unit": None,
                    "order_sensitive": True,
                    "grading_method": "numeric_exact",
                },
                verification={},
            )
        )
    db.commit()
    return db


def test_blueprint_run_and_resume_tracks_breakpoint_progress():
    db = seeded_db()

    blueprint = save_test_blueprint(BlueprintRequest(name="两题烟测", mode="strict_test", template_name=None, count=2), db)
    run = start_test_run(blueprint["id"], db)
    submit_answer(
        run["session_id"],
        SubmitAnswerRequest(question_id=run["question_ids"][0], answer_text="1", thought_text="按题目直接计算。", elapsed_ms=1000),
        db,
    )
    resumed = resume_test_run(run["id"], db)

    saved_blueprint = db.get(BlueprintModel, blueprint["id"])
    saved_run = db.get(RunModel, run["id"])
    session = db.get(PracticeSession, run["session_id"])

    assert saved_blueprint is not None
    assert saved_run is not None
    assert session is not None
    assert resumed["progress"]["answered_count"] == 1
    assert resumed["progress"]["total"] == 2
    assert resumed["session"]["strict_no_ai"] is True
    assert len(run["question_ids"]) == 2


def test_report_view_save_and_export_snapshot():
    db = seeded_db()
    session = PracticeSession(id="s1", mode="practice", config={}, question_ids=["q1"])
    db.add(session)
    db.commit()
    submit_answer(
        "s1",
        SubmitAnswerRequest(question_id="q1", answer_text="1", thought_text="按题目直接计算。", elapsed_ms=1000),
        db,
    )

    view = save_report_view(ReportViewRequest(name="三星练习", filters={"source_name": "source.json", "difficulty_star": 3, "mode": "practice"}), db)
    exported = export_report(ReportExportRequest(view_id=view["id"]), db)

    saved_view = db.scalar(select(ReportFilterView).where(ReportFilterView.name == "三星练习"))
    saved_export = db.get(ReportExport, exported["id"])

    assert saved_view is not None
    assert saved_export is not None
    assert exported["payload"]["submission_summary"]["answered_questions"] == 1
    assert exported["filters"]["difficulty_star"] == 3
