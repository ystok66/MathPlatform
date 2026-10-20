from apps.api.app.ai_gateway import mock_candidate, review_candidate
from apps.api.app.candidate_quality import equation_usage_detector, independent_difficulty, independent_premium, verify_candidate_rules


def test_candidate_rule_verification_and_independent_scores_accept_valid_candidate():
    candidate = mock_candidate("structural_elegance", {"difficulty_star": 2}, [])

    report = verify_candidate_rules(candidate)
    review = review_candidate(candidate)

    assert report["valid"] is True
    assert report["checks"]["final_answer_grades_correct"] is True
    assert report["difficulty"]["total_score"] == 4
    assert report["difficulty"]["star"] == 2
    assert report["premium"]["total_score"] == 2
    assert report["premium"]["is_premium"] is False
    assert review["decision"] == "accept"
    assert review["scores"]["difficulty_independent"]["star"] == 2
    assert review["scores"]["premium_independent"]["is_premium"] is False


def test_candidate_with_wrong_final_answer_is_rejected_before_acceptance():
    candidate = mock_candidate("structural_elegance", {"difficulty_star": 3}, [])
    candidate["question"]["solution"]["final_answer"] = "46"

    report = verify_candidate_rules(candidate)
    review = review_candidate(candidate)

    assert report["valid"] is False
    assert "final_answer_grades_correct" in report["issues"]
    assert review["decision"] == "reject"
    assert review["hard_checks"]["rule_verification"] is False


def test_elementary_required_rejects_unjustified_equation_main_solution():
    candidate = mock_candidate("structural_elegance", {"difficulty_star": 3, "solution_style": "elementary_required"}, [])
    solution = candidate["question"]["solution"]
    solution["requested_solution_style"] = "elementary_required"
    solution["solution_style_used"] = "equation"
    solution["student_steps"] = ["设 x 为个位数，列方程 x + x + 2 = 10。", "解得 x = 4，所以答案是64。"]
    solution["equation_necessity"] = "not_needed"

    report = verify_candidate_rules(candidate)

    assert equation_usage_detector(candidate["question"])["equation_detected"] is True
    assert report["valid"] is False
    assert "student_solution_style_valid" in report["issues"]


def test_equation_topic_is_not_rejected_by_static_style_gate():
    candidate = mock_candidate("structural_elegance", {"difficulty_star": 3, "solution_style": "elementary_required"}, [])
    candidate["question"]["taxonomy"]["method_tag_ids"] = ["方程"]
    solution = candidate["question"]["solution"]
    solution["requested_solution_style"] = "elementary_required"
    solution["solution_style_used"] = "equation"
    solution["student_steps"] = ["设 x 为个位数，列方程 x + x + 2 = 10。", "解得 x = 4，所以答案是64。"]
    solution["equation_necessity"] = "needed"

    report = verify_candidate_rules(candidate)

    assert report["equation_usage_detector"]["equation_use_justified"] is True
    assert report["checks"]["student_solution_style_valid"] is True
    assert report["valid"] is True


def test_independent_difficulty_and_premium_do_not_trust_source_totals():
    question = {
        "difficulty": {"knowledge_span": 2, "modeling": 2, "insight": 2, "reasoning_chain": 2, "case_analysis": 2, "calculation_load": 2, "total_score": 1, "star": 1},
        "premium": {"innovation": 2, "elegance": 2, "integration": 1, "transfer_value": 1, "total_score": 0, "is_premium": False},
    }

    difficulty = independent_difficulty(question)
    premium = independent_premium(question)

    assert difficulty["total_score"] == 12
    assert difficulty["star"] == 5
    assert difficulty["star_matches_source"] is False
    assert premium["total_score"] == 6
    assert premium["is_premium"] is True
    assert premium["premium_matches_source"] is False
