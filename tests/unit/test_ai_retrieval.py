import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app.ai_gateway import build_messages, existing_generation_duplicate, retrieve_ai_negative_examples, retrieve_examples
from apps.api.app.db import Base
from apps.api.app.models import AnswerSpec, Question, QuestionTag, SourceFile, TaxonomyTag
from apps.api.app.routers.generation import DuplicateCheckRequest, duplicate_check


def test_retrieve_examples_prefers_target_tags_and_limits_to_five_diverse_examples():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=6)
    db.add(source)
    db.add(TaxonomyTag(id="method:equation", kind="method", label="方程", aliases=[]))
    db.add(TaxonomyTag(id="method:counting", kind="method", label="计数", aliases=[]))
    for index in range(6):
        question_id = f"q{index}"
        db.add(
            Question(
                id=question_id,
                source_file_id="sf1",
                status="verified",
                source_name=f"source-{index % 3}.json",
                stem_preview=f"题目 {index}",
                difficulty_star=3,
                premium=False,
                family_id=f"family-{index // 2}",
            )
        )
        db.add(AnswerSpec(question_id=question_id, status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}))
        db.add(QuestionTag(question_id=question_id, tag_id="method:equation" if index in {4, 5} else "method:counting", role="method", raw_label="x"))
    db.add(
        Question(
            id="ai-close",
            source_file_id="sf1",
            status="verified",
            source_name="AI生成题",
            stem_preview="AI 题目",
            difficulty_star=3,
            premium=False,
        )
    )
    db.add(AnswerSpec(question_id="ai-close", status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}))
    db.add(QuestionTag(question_id="ai-close", tag_id="method:equation", role="method", raw_label="x"))
    db.commit()

    examples = retrieve_examples(db, {"difficulty_star": 3, "target_tags": ["method:equation"]})

    assert len(examples) == 5
    assert all(example.source_name != "AI生成题" for example in examples)
    assert [example.id for example in examples[:2]] == ["q4", "q5"]
    assert len({example.source_name for example in examples}) >= 3


def test_retrieve_ai_negative_examples_uses_same_star_and_closest_tags():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="__ai_generated__", sha256="generated", question_count=5)
    db.add(source)
    db.add(TaxonomyTag(id="method:target", kind="method", label="目标", aliases=[]))
    db.add(TaxonomyTag(id="method:other", kind="method", label="其他", aliases=[]))
    for index in range(5):
        question_id = f"ai-{index}"
        db.add(
            Question(
                id=question_id,
                source_file_id="sf1",
                status="verified",
                source_name="AI生成题",
                stem_preview=f"AI 题 {index}",
                difficulty_star=3 if index != 4 else 4,
                premium=False,
                publish_status="active_practice",
                eligible_for_practice=True,
                question_fault_status="none",
            )
        )
        db.add(AnswerSpec(question_id=question_id, status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}))
        db.add(QuestionTag(question_id=question_id, tag_id="method:target" if index in {2, 3} else "method:other", role="method", raw_label="x"))
    db.commit()

    examples = retrieve_ai_negative_examples(db, {"difficulty_star": 3, "target_tags": ["method:target"]})
    messages = build_messages("structural_elegance", {"difficulty_star": 3, "target_tags": ["method:target"]}, [], examples)
    payload = messages[1]["content"]

    assert len(examples) == 3
    assert [example.id for example in examples[:2]] == ["ai-2", "ai-3"]
    assert all(example.difficulty_star == 3 for example in examples)
    assert "agent_input_envelope_v1" in payload
    assert "security_boundary" in payload
    assert "ai_negative_example_cards" in payload
    assert "source_examples_exclude_ai" in payload


def test_generation_example_counts_are_request_configurable():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=12)
    db.add(source)
    db.add(TaxonomyTag(id="method:target", kind="method", label="目标", aliases=[]))
    for index in range(6):
        db.add(
            Question(
                id=f"q{index}",
                source_file_id="sf1",
                status="verified",
                source_name="source.json",
                stem_preview=f"非 AI 题 {index}",
                difficulty_star=3,
                premium=False,
            )
        )
        db.add(AnswerSpec(question_id=f"q{index}", status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}))
        db.add(QuestionTag(question_id=f"q{index}", tag_id="method:target", role="method", raw_label="目标"))
    for index in range(4):
        db.add(
            Question(
                id=f"ai{index}",
                source_file_id="sf1",
                status="verified",
                source_name="AI生成题",
                stem_preview=f"AI 题 {index}",
                difficulty_star=3,
                premium=False,
                publish_status="active_practice",
                eligible_for_practice=True,
                question_fault_status="none",
            )
        )
        db.add(AnswerSpec(question_id=f"ai{index}", status="verified", answer_type="integer", canonical_answer="1", normalized_spec={}, raw_answer_spec={}, verification={}))
        db.add(QuestionTag(question_id=f"ai{index}", tag_id="method:target", role="method", raw_label="目标"))
    db.commit()

    request = {
        "difficulty_star": 3,
        "target_tags": ["method:target"],
        "source_example_count": 2,
        "ai_negative_example_count": 1,
        "candidate_count": 4,
    }
    examples = retrieve_examples(db, request)
    ai_examples = retrieve_ai_negative_examples(db, request)
    envelope = json.loads(build_messages("representation_shift", request, examples, ai_examples)[1]["content"])
    payload = envelope["payload"]

    assert len(examples) == 2
    assert envelope["schema_version"] == "agent_input_envelope_v1"
    assert "payload.request.custom_description" in envelope["security_boundary"]["untrusted_fields"]
    assert all(example.source_name != "AI生成题" for example in examples)
    assert len(ai_examples) == 1
    assert ai_examples[0].source_name == "AI生成题"
    assert payload["candidate_strategy_count"] == 4
    assert payload["candidate_strategies"] == ["structural_elegance", "constraint_shift", "inverse_task", "representation_shift"]
    assert payload["example_selection_policy"]["max_source_examples"] == 2
    assert payload["example_selection_policy"]["actual_source_examples"] == 2
    assert payload["example_selection_policy"]["max_ai_negative_examples"] == 1
    assert payload["example_selection_policy"]["actual_ai_negative_examples"] == 1


def test_generation_example_counts_can_be_zero():
    request = {"source_example_count": 0, "ai_negative_example_count": 0, "candidate_count": 1}
    payload = json.loads(build_messages("structural_elegance", request, [], [])[1]["content"])["payload"]

    assert payload["source_example_cards"] == []
    assert payload["ai_negative_example_cards"] == []
    assert payload["candidate_strategy_count"] == 1


def test_retrieve_examples_allows_at_most_one_needs_review_sample():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="source.json", sha256="0" * 64, question_count=7)
    db.add(source)
    db.add(TaxonomyTag(id="method:target", kind="method", label="ç›®æ ‡", aliases=[]))
    db.add(TaxonomyTag(id="method:other", kind="method", label="å…¶ä»–", aliases=[]))
    for index in range(7):
        question_id = f"q{index}"
        needs_review = index in {0, 1}
        db.add(
            Question(
                id=question_id,
                source_file_id="sf1",
                status="needs_review" if needs_review else "verified",
                source_name=f"source-{index % 3}.json",
                stem_preview=f"é¢˜ç›® {index}",
                difficulty_star=3,
                premium=False,
                family_id=f"family-{index}",
            )
        )
        db.add(
            AnswerSpec(
                question_id=question_id,
                status="needs_review" if needs_review else "verified",
                answer_type="integer",
                canonical_answer="1",
                normalized_spec={},
                raw_answer_spec={},
                verification={},
            )
        )
        db.add(QuestionTag(question_id=question_id, tag_id="method:target" if needs_review else "method:other", role="method", raw_label="x"))
    db.commit()

    examples = retrieve_examples(db, {"difficulty_star": 3, "target_tags": ["method:target"]})

    assert len(examples) == 5
    assert sum(1 for example in examples if example.status == "needs_review") == 1
    assert examples[0].status == "needs_review"


def test_existing_generation_duplicate_rejects_number_only_variant_against_active_questions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="__ai_generated__", sha256="generated", question_count=1)
    db.add(source)
    db.add(
        Question(
            id="ai-existing",
            source_file_id="sf1",
            status="verified",
            source_name="AI生成题",
            publish_status="active_practice",
            eligible_for_practice=True,
            eligible_for_test=False,
            stem_preview="三盒彩笔共有72支，第一盒比第二盒多6支，第三盒比第二盒少6支。第二盒有多少支？",
            difficulty_star=2,
            premium=False,
            semantic_hash="old",
        )
    )
    db.commit()

    duplicate = existing_generation_duplicate(
        db,
        "ai-new",
        "三盒彩笔共有90支，第一盒比第二盒多8支，第三盒比第二盒少8支。第二盒有多少支？",
        {"dedupe": {"semantic_hash": "new"}},
    )

    assert duplicate is not None
    assert duplicate["question_id"] == "ai-existing"
    assert duplicate["reason"] == "near_duplicate_existing_question"


def test_duplicate_check_api_reports_high_risk_variant():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = SourceFile(id="sf1", filename="__ai_generated__", sha256="generated", question_count=1)
    db.add(source)
    db.add(
        Question(
            id="ai-existing",
            source_file_id="sf1",
            status="verified",
            source_name="AI生成题",
            publish_status="active_practice",
            eligible_for_practice=True,
            stem_preview="一个长方形的周长是54厘米，长比宽多7厘米。这个长方形的宽是多少厘米？",
            difficulty_star=2,
            premium=False,
        )
    )
    db.commit()

    result = duplicate_check(
        DuplicateCheckRequest(stem_md="一个长方形的周长是70厘米，长比宽多9厘米。这个长方形的宽是多少厘米？"),
        db,
    )

    assert result["risk"] == "high"
    assert result["duplicate"]["question_id"] == "ai-existing"
