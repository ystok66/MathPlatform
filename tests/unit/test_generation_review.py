from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import GeneratedCandidate, GeneratedCandidateReview, GenerationJob
from apps.api.app.routers.generation import CandidateReviewActionRequest, coverage_review, review_candidate_action


def seeded_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    job = GenerationJob(id="job1", status="running", request={"count": 3}, progress={})
    db.add(job)
    for index, strategy in enumerate(["structural_elegance", "constraint_shift", "inverse_task"], start=1):
        db.add(
            GeneratedCandidate(
                id=f"cand{index}",
                job_id="job1",
                strategy=strategy,
                status="rejected",
                payload={
                    "status": "ok",
                    "question": {
                        "stem_md": "同一道题" if index < 3 else "另一道题",
                        "answer_type": "integer",
                    },
                    "self_checks": {"source_similarity_risk": "high" if index == 2 else "low"},
                },
                review={},
                score=80,
            )
        )
    db.commit()
    return db


def test_generation_coverage_review_flags_duplicates_and_similarity_risk():
    db = seeded_db()

    report = coverage_review("job1", db)

    assert report["candidate_count"] == 3
    assert report["strategy_counts"] == {"constraint_shift": 1, "inverse_task": 1, "structural_elegance": 1}
    assert report["duplicate_groups"][0]["candidate_ids"] == ["cand1", "cand2"]
    assert report["pairwise_similarity_matrix"]
    assert report["diversity_score"] < 100
    assert report["regeneration_slots"][0]["reason"] == "pairwise_similarity_high"
    assert "duplicate_candidate_stems" in report["warnings"]
    assert "high_source_similarity_risk" in report["warnings"]
    assert "high_pairwise_candidate_similarity" in report["warnings"]


def test_manual_candidate_edit_and_approval_records_diff():
    db = seeded_db()
    edited = {
        "status": "ok",
        "question": {"stem_md": "改后题面", "answer_type": "integer"},
        "self_checks": {"source_similarity_risk": "low"},
    }

    edit = review_candidate_action(
        "job1",
        "cand1",
        CandidateReviewActionRequest(action="edit", edited_payload=edited, reviewer_notes="改题面"),
        db,
    )
    approve = review_candidate_action("job1", "cand1", CandidateReviewActionRequest(action="approve", reviewer_notes="通过"), db)

    candidate = db.get(GeneratedCandidate, "cand1")
    reviews = list(db.scalars(select(GeneratedCandidateReview).where(GeneratedCandidateReview.candidate_id == "cand1")))

    assert candidate is not None
    assert candidate.status == "approved"
    assert candidate.payload["question"]["stem_md"] == "改后题面"
    assert edit["diff"]["change_count"] >= 1
    assert approve["status"] == "approved"
    assert [row.action for row in reviews] == ["edit", "approve"]
