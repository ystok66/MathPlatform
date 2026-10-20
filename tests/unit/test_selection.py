from collections import Counter
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import AnswerSpec, PracticeSession, Question, QuestionTag, SourceFile, Submission, TaxonomyTag
from apps.api.app.selection import SelectionPolicy, select_questions_for_session


STRICT_QUOTA = {1: 10, 2: 20, 3: 40, 4: 20, 5: 10}
DIAGNOSTIC_QUOTA = {1: 10, 2: 25, 3: 40, 4: 20, 5: 5}


def seeded_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="synthetic.json", sha256="0" * 64, question_count=120)
    db.add(source)
    for star, count in {1: 12, 2: 30, 3: 45, 4: 25, 5: 12}.items():
        for index in range(count):
            question_id = f"q{star}-{index:03d}"
            db.add(
                Question(
                    id=question_id,
                    source_file_id=source.id,
                    status="verified",
                    source_name=f"source-{index % 3}.json",
                    stem_preview=f"{star} star {index}",
                    difficulty_star=star,
                    premium=False,
                    family_id=f"family-{star}-{index}",
                )
            )
            db.add(
                AnswerSpec(
                    question_id=question_id,
                    status="verified",
                    answer_type="integer",
                    canonical_answer="1",
                    normalized_spec={},
                    raw_answer_spec={},
                    verification={},
                )
            )
    db.add(
        Question(
            id="ai-1",
            source_file_id=source.id,
            status="verified",
            source_name="AI生成题",
            publish_status="active_practice",
            eligible_for_practice=True,
            eligible_for_test=False,
            stem_preview="generated",
            difficulty_star=3,
            premium=False,
        )
    )
    db.add(
        AnswerSpec(
            question_id="ai-1",
            status="verified",
            answer_type="integer",
            canonical_answer="1",
            normalized_spec={},
            raw_answer_spec={},
            verification={},
        )
    )
    db.commit()
    return db


def test_strict_100_high_intensity_quota_uses_verified_imported_questions():
    db = seeded_db()
    ids, metadata = select_questions_for_session(
        db,
        SelectionPolicy(mode="strict_test", count=100, template_name="strict_100_high_intensity", verified_only=True),
    )
    rows = list(db.scalars(select(Question).where(Question.id.in_(ids))))
    quota = Counter(int(row.difficulty_star or 0) for row in rows)
    assert len(ids) == 100
    assert quota == STRICT_QUOTA
    assert all(row.status == "verified" for row in rows)
    assert all(row.source_name != "AI生成题" for row in rows)
    assert metadata["breakpoints"] == [40, 80]
    assert metadata["strict_no_ai"] is True


def test_strict_ai_only_can_select_verified_generated_questions():
    db = seeded_db()
    ids, metadata = select_questions_for_session(
        db,
        SelectionPolicy(mode="strict_test", count=1, strict_source_mode="ai_only", include_generated=True),
    )

    assert ids == ["ai-1"]
    assert metadata["policy"]["strict_source_mode"] == "ai_only"
    assert metadata["source_counts"] == {"AI生成题": 1}


def test_strict_selection_excludes_done_questions_and_reports_all_done_pool():
    db = seeded_db()
    db.add(PracticeSession(id="s-done", mode="strict_test", config={}, question_ids=[]))
    db.add(
        Submission(
            id="sub-ai",
            session_id="s-done",
            question_id="ai-1",
            answer_text="1",
            is_correct=True,
            grading_result={"is_correct": True},
            created_at=datetime(2026, 7, 20, 8, 0, 0),
        )
    )
    db.commit()

    ids, metadata = select_questions_for_session(
        db,
        SelectionPolicy(mode="strict_test", count=1, strict_source_mode="ai_only", include_generated=True, exclude_done=True),
    )

    assert ids == []
    assert metadata["excluded_done_question_count"] == 1
    assert "all_ai_only_questions_done" in metadata["selection_warnings"]


def test_standard_diagnostic_quota():
    db = seeded_db()
    ids, metadata = select_questions_for_session(db, SelectionPolicy(mode="diagnostic", count=100, template_name="standard_diagnostic_100"))
    rows = list(db.scalars(select(Question).where(Question.id.in_(ids))))
    quota = Counter(int(row.difficulty_star or 0) for row in rows)
    assert quota == DIAGNOSTIC_QUOTA
    assert metadata["target_quota"] == DIAGNOSTIC_QUOTA


def small_tagged_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf-tags", filename="tags.json", sha256="1" * 64, question_count=4)
    db.add(source)
    for tag_id, label in [("method:a", "A"), ("method:b", "B"), ("method:c", "C")]:
        db.add(TaxonomyTag(id=tag_id, kind="method", label=label, aliases=[]))
    for index in range(4):
        question_id = f"t{index}"
        db.add(
            Question(
                id=question_id,
                source_file_id=source.id,
                status="verified",
                source_name="tags.json",
                stem_preview=f"tagged {index}",
                difficulty_star=3,
                premium=False,
                family_id=f"family-t{index}",
            )
        )
        db.add(AnswerSpec(question_id=question_id, status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}))
    db.add_all(
        [
            QuestionTag(question_id="t0", tag_id="method:a", role="method", raw_label="A"),
            QuestionTag(question_id="t1", tag_id="method:a", role="method", raw_label="A"),
            QuestionTag(question_id="t2", tag_id="method:b", role="method", raw_label="B"),
            QuestionTag(question_id="t3", tag_id="method:c", role="method", raw_label="C"),
        ]
    )
    db.commit()
    return db


def test_selection_prefers_unseen_tags_for_coverage():
    db = small_tagged_db()

    ids, metadata = select_questions_for_session(db, SelectionPolicy(mode="practice", count=3))

    assert ids == ["t0", "t2", "t3"]
    assert metadata["tag_unique_count"] == 3


def test_selection_auto_excludes_recently_submitted_questions():
    db = small_tagged_db()
    db.add(PracticeSession(id="s1", mode="practice", learner_id="default-learner", config={}, question_ids=["t0"]))
    db.add(
        Submission(
            id="sub-recent",
            session_id="s1",
            question_id="t0",
            answer_text="1",
            is_correct=True,
            grading_result={"is_correct": True},
            created_at=datetime(2026, 7, 19, 9, 0, 0),
        )
    )
    db.commit()

    ids, metadata = select_questions_for_session(db, SelectionPolicy(mode="practice", count=3, recent_exposure_count=1))

    assert "t0" not in ids
    assert metadata["auto_excluded_recent_question_ids"] == ["t0"]


def test_selection_done_and_recent_are_scoped_to_learner():
    db = small_tagged_db()
    db.add(PracticeSession(id="s-learner-a", mode="practice", learner_id="learner-a", config={}, question_ids=["t0"]))
    db.add(
        Submission(
            id="sub-learner-a",
            session_id="s-learner-a",
            question_id="t0",
            answer_text="1",
            is_correct=True,
            grading_result={"is_correct": True},
            created_at=datetime(2026, 7, 19, 9, 0, 0),
        )
    )
    db.commit()

    learner_a_done_ids, learner_a_done_meta = select_questions_for_session(
        db,
        SelectionPolicy(mode="practice", learner_id="learner-a", count=4, exclude_done=True),
    )
    learner_a_recent_ids, learner_a_recent_meta = select_questions_for_session(
        db,
        SelectionPolicy(mode="practice", learner_id="learner-a", count=4, recent_exposure_count=1),
    )
    learner_b_ids, learner_b_meta = select_questions_for_session(
        db,
        SelectionPolicy(mode="practice", learner_id="learner-b", count=4, exclude_done=True, recent_exposure_count=1),
    )

    assert "t0" not in learner_a_done_ids
    assert learner_a_done_meta["excluded_done_question_count"] == 1
    assert "t0" not in learner_a_recent_ids
    assert learner_a_recent_meta["auto_excluded_recent_question_ids"] == ["t0"]
    assert "t0" in learner_b_ids
    assert learner_b_meta["excluded_done_question_count"] == 0
    assert learner_b_meta["auto_excluded_recent_question_ids"] == []


def test_selection_never_serves_needs_review_even_when_verified_only_false():
    db = small_tagged_db()
    db.add(
        Question(
            id="review-1",
            source_file_id="sf-tags",
            status="needs_review",
            source_name="tags.json",
            stem_preview="needs review",
            difficulty_star=3,
            premium=False,
            family_id="family-review",
        )
    )
    db.add(
        AnswerSpec(
            question_id="review-1",
            status="needs_review",
            answer_type="integer",
            canonical_answer="",
            normalized_spec={},
            raw_answer_spec={},
            verification={},
        )
    )
    db.commit()

    ids, metadata = select_questions_for_session(db, SelectionPolicy(mode="practice", count=5, verified_only=False))

    assert "review-1" not in ids
    assert set(ids) == {"t0", "t1", "t2", "t3"}
    assert metadata["policy"]["verified_only"] is True
