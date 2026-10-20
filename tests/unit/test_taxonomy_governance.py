from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import QuestionVersion, TaxonomyTag
from apps.api.app.taxonomy_governance import governance_scan, standardize_constraint_note


def test_standardize_constraint_note_classifies_common_free_text():
    equation = standardize_constraint_note("875=125×7")
    long_note = standardize_constraint_note("两个乘积项含125倍数并且需要合并")

    assert "equation_constraint" in equation["standard_tags"]
    assert equation["needs_manual_review"] is False
    assert long_note["needs_manual_review"] is True


def test_governance_scan_finds_suspicious_tags_and_alias_drift():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all(
        [
            TaxonomyTag(id="constraint:eq1", kind="constraint", label="875=125×7", aliases=[]),
            TaxonomyTag(id="method:a", kind="method", label="方程", aliases=[]),
            TaxonomyTag(id="method:b", kind="method", label=" 方程 ", aliases=[]),
            QuestionVersion(
                question_id="q1",
                version=1,
                raw_json={},
                question_v2={"taxonomy": {"constraint_notes": ["至少2种不同方法", "很长的自由文本条件需要人工处理"]}},
                content_hash="hash",
            ),
        ]
    )
    db.commit()

    report = governance_scan(db)

    assert report["sampled_question_versions"] == 1
    assert report["suspicious_tags"][0]["tag_id"] == "constraint:eq1"
    assert report["alias_drift_groups"][0]["tag_ids"] == ["method:a", "method:b"]
    assert len(report["constraint_notes_needing_review"]) == 1
