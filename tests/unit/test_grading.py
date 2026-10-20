import random

from apps.api.app.grading import grade_answer, normalize_text


def base_spec(**overrides):
    spec = {
        "status": "verified",
        "answer_type": "integer",
        "canonical_answer": "64",
        "acceptable_answers": [],
        "tolerance": {"type": "exact", "value": 0},
        "unit": None,
        "order_sensitive": True,
        "grading_method": "numeric_exact",
    }
    spec.update(overrides)
    return spec


def test_integer_exact():
    assert grade_answer(base_spec(), "64")["is_correct"] is True
    assert grade_answer(base_spec(), "46")["is_correct"] is False


def test_fraction_accepts_decimal_equivalent():
    spec = base_spec(answer_type="fraction", canonical_answer="331/50")
    assert grade_answer(spec, "6.62")["is_correct"] is True


def test_absolute_tolerance_and_unit():
    spec = base_spec(
        answer_type="number",
        canonical_answer="3960",
        unit="平方步",
        tolerance={"type": "absolute", "value": 5},
        grading_method="numeric_with_tolerance",
    )
    assert grade_answer(spec, "3959平方步")["is_correct"] is True
    assert grade_answer(spec, "3940平方步")["is_correct"] is False


def test_required_unit_missing_mismatch_and_alias():
    spec = base_spec(
        answer_type="number",
        canonical_answer="12",
        unit="米",
        unit_aliases=["m"],
        grading_method="numeric_exact",
    )
    assert grade_answer(spec, "12m")["is_correct"] is True
    assert grade_answer(spec, "12")["reason"] == "unit_missing"
    assert grade_answer(spec, "12厘米")["reason"] == "unit_mismatch"


def test_percent_and_mixed_fraction_numeric_parsing():
    percent = base_spec(answer_type="fraction", canonical_answer="1/2")
    mixed = base_spec(answer_type="fraction", canonical_answer="13/2")
    assert grade_answer(percent, "50%")["is_correct"] is True
    assert grade_answer(mixed, "6又1/2")["is_correct"] is True


def test_multi_blank_and_set():
    multi = base_spec(answer_type="multi_blank", canonical_answer="3,5", grading_method="ordered_tuple_exact")
    unordered = base_spec(answer_type="set", canonical_answer="甲,乙", grading_method="set_exact")
    assert grade_answer(multi, "3 5")["is_correct"] is True
    assert grade_answer(multi, "5 3")["is_correct"] is False
    assert grade_answer(unordered, "乙、甲")["is_correct"] is True
    prime_set = base_spec(answer_type="set", canonical_answer="2,3,13,23,31", grading_method="set_exact")
    assert grade_answer(prime_set, "{31、23、13、3、2}。")["is_correct"] is True


def test_multi_part_and_set_explain_boundary_errors():
    multi = base_spec(answer_type="multi_blank", canonical_answer="3,5", grading_method="ordered_tuple_exact")
    set_spec = base_spec(answer_type="set", canonical_answer="2,3,13", grading_method="set_exact")
    assert grade_answer(multi, "3")["reason"] == "part_count_mismatch"
    assert grade_answer(multi, "5 3")["reason"] == "part_value_or_order_mismatch"
    assert grade_answer(set_spec, "2,3")["reason"] == "set_missing"
    assert grade_answer(set_spec, "2,3,13,17")["reason"] == "set_extra"
    assert grade_answer(set_spec, "2,3,3,13")["reason"] == "set_duplicate_part"


def test_expression_equivalence_and_needs_review():
    expr = base_spec(answer_type="expression", canonical_answer="2*x+2", grading_method="expression_equiv")
    review = base_spec(status="needs_review")
    assert grade_answer(expr, "2*(x+1)")["is_correct"] is True
    assert grade_answer(review, "64")["is_correct"] is None


def test_expression_parse_failure_is_uncertain_not_wrong():
    expr = base_spec(answer_type="expression", canonical_answer="2*x+2", grading_method="expression_equiv")
    result = grade_answer(expr, "2*(")
    assert result["is_correct"] is None
    assert result["reason"] == "expression_unverified"


def test_unparseable_numeric_answer_spec_is_uncertain():
    spec = base_spec(answer_type="integer", canonical_answer="not-a-number")
    result = grade_answer(spec, "64")
    assert result["is_correct"] is None
    assert result["reason"] == "answer_spec_invalid"


def test_normalize_text_accepts_fullwidth_digits_and_math_symbols():
    assert normalize_text(" １２３．５／２ ") == "123.5/2"


def test_numeric_normalizer_randomized_format_variants():
    spec = base_spec(answer_type="fraction", canonical_answer="331/50")
    rng = random.Random(20260722)
    variants = ["331/50", "６．６２", " 6.62 ", "３３１／５０", "约6.62"]
    separators = ["", " ", "　"]

    for _ in range(80):
        value = rng.choice(variants)
        left = rng.choice(separators)
        right = rng.choice(separators)
        assert grade_answer(spec, f"{left}{value}{right}")["is_correct"] is True


def test_set_normalizer_randomized_order_and_separators():
    spec = base_spec(answer_type="set", canonical_answer="2,3,13,23,31", grading_method="set_exact")
    rng = random.Random(20260722)
    parts = ["２", "3", "１３", "23", "31"]
    separators = [",", "，", "、", ";", "；", " "]

    for _ in range(80):
        shuffled = parts[:]
        rng.shuffle(shuffled)
        answer = ""
        for index, part in enumerate(shuffled):
            answer += part
            if index < len(shuffled) - 1:
                answer += rng.choice(separators)
        assert grade_answer(spec, f"{{{answer}}}")["is_correct"] is True
