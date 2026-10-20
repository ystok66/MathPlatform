from __future__ import annotations

from typing import Any

from .grading import grade_answer


DIFFICULTY_DIMENSIONS = ("knowledge_span", "modeling", "insight", "reasoning_chain", "case_analysis", "calculation_load")
PREMIUM_DIMENSIONS = ("innovation", "elegance", "integration", "transfer_value")
EQUATION_MARKERS = ("设x", "设 x", "设未知数", "列方程", "解方程", "解得x", "解得 x", "移项", "x=", "x =")


def difficulty_star_from_score(score: int) -> int:
    if score <= 2:
        return 1
    if score <= 5:
        return 2
    if score <= 8:
        return 3
    if score <= 11:
        return 4
    return 5


def answer_spec_for_grading(question: dict[str, Any]) -> dict[str, Any]:
    answer_spec = question.get("answer_spec") or {}
    answer_type = question.get("answer_type", "text")
    canonical = [str(item) for item in (answer_spec.get("canonical") or [])]
    grading_method = "exact_normalized"
    if answer_type in {"integer", "rational", "decimal"}:
        grading_method = "numeric_exact"
    elif answer_type == "expression":
        grading_method = "expression"
    elif answer_type == "multi_blank":
        grading_method = "ordered_tuple_exact"
    elif answer_type == "set":
        grading_method = "set_exact"
    return {
        "status": "verified",
        "answer_type": "fraction" if answer_type == "rational" else answer_type,
        "canonical_answer": canonical[0] if canonical else "",
        "acceptable_answers": [str(item) for item in answer_spec.get("accepted_forms", [])],
        "tolerance": {"type": "absolute", "value": answer_spec.get("tolerance")} if answer_spec.get("tolerance") is not None else {"type": "exact", "value": 0},
        "unit": answer_spec.get("unit"),
        "order_sensitive": answer_spec.get("order_sensitive", True),
        "grading_method": grading_method,
    }


def independent_difficulty(question: dict[str, Any]) -> dict[str, Any]:
    source = question.get("difficulty") or {}
    dimension_scores = {key: int(source.get(key) or 0) for key in DIFFICULTY_DIMENSIONS}
    total = sum(dimension_scores.values())
    return {
        "dimension_scores": dimension_scores,
        "total_score": total,
        "star": difficulty_star_from_score(total),
        "source_star": source.get("star"),
        "star_matches_source": source.get("star") in {None, difficulty_star_from_score(total)},
    }


def independent_premium(question: dict[str, Any]) -> dict[str, Any]:
    source = question.get("premium") or {}
    dimension_scores = {key: int(source.get(key) or 0) for key in PREMIUM_DIMENSIONS}
    total = sum(dimension_scores.values())
    is_premium = total >= 6
    return {
        "dimension_scores": dimension_scores,
        "total_score": total,
        "is_premium": is_premium,
        "source_is_premium": source.get("is_premium"),
        "premium_matches_source": source.get("is_premium") in {None, is_premium},
    }


def _dimension_values(block: dict[str, Any], dimensions: tuple[str, ...]) -> dict[str, int]:
    values: dict[str, int] = {}
    for key in dimensions:
        value = block.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        values[key] = int(value)
    return values


def difficulty_self_consistency(question: dict[str, Any]) -> dict[str, Any]:
    source = question.get("difficulty") or {}
    values = _dimension_values(source, DIFFICULTY_DIMENSIONS)
    total = sum(values.values())
    source_total = source.get("total_score")
    source_star = source.get("star")
    return {
        "has_all_dimensions": set(values) == set(DIFFICULTY_DIMENSIONS),
        "dimensions_in_range": all(0 <= value <= 3 for value in values.values()),
        "total_matches_dimensions": isinstance(source_total, (int, float)) and int(source_total) == total,
        "star_matches_total": isinstance(source_star, (int, float)) and int(source_star) == difficulty_star_from_score(total),
        "computed_total": total,
        "computed_star": difficulty_star_from_score(total),
        "source_total": source_total,
        "source_star": source_star,
    }


def premium_self_consistency(question: dict[str, Any]) -> dict[str, Any]:
    source = question.get("premium") or {}
    values = _dimension_values(source, PREMIUM_DIMENSIONS)
    total = sum(values.values())
    source_total = source.get("total_score")
    source_premium = source.get("is_premium")
    return {
        "has_all_dimensions": set(values) == set(PREMIUM_DIMENSIONS),
        "dimensions_in_range": all(0 <= value <= 3 for key, value in values.items() if key in {"innovation", "elegance"}) and all(0 <= value <= 2 for key, value in values.items() if key in {"integration", "transfer_value"}),
        "total_matches_dimensions": isinstance(source_total, (int, float)) and int(source_total) == total,
        "premium_matches_total": isinstance(source_premium, bool) and source_premium == (total >= 6),
        "computed_total": total,
        "computed_is_premium": total >= 6,
        "source_total": source_total,
        "source_is_premium": source_premium,
    }


def _solution_text(solution: dict[str, Any]) -> str:
    fragments: list[str] = []
    for key in ("solution_hook", "primary_method", "solution_style_used", "equation_necessity", "equation_reason", "key_insight", "final_answer"):
        value = solution.get(key)
        if value is not None:
            fragments.append(str(value))
    for key in ("steps", "student_steps"):
        value = solution.get(key) or []
        if isinstance(value, list):
            fragments.extend(str(item) for item in value)
    return " ".join(fragments).replace(" ", "").lower()


def equation_usage_detector(question: dict[str, Any]) -> dict[str, Any]:
    solution = question.get("solution") or {}
    taxonomy = question.get("taxonomy") or {}
    requested_style = solution.get("requested_solution_style") or question.get("solution_style") or "elementary_preferred"
    text = _solution_text(solution)
    markers = [marker for marker in EQUATION_MARKERS if marker.replace(" ", "").lower() in text]
    tag_text = " ".join(str(value) for value in taxonomy.values()).lower()
    equation_topic = any(marker in tag_text for marker in ("equation", "algebra", "方程", "代数"))
    necessity = str(solution.get("equation_necessity") or "").lower()
    justified = requested_style == "unrestricted" or equation_topic or necessity in {"needed", "required", "justified", "necessary"}
    natural_available = solution.get("natural_solution_available")
    style_valid = not (requested_style == "elementary_required" and markers and not justified)
    preferred_warning = requested_style == "elementary_preferred" and markers and natural_available is not False and not justified
    return {
        "requested_solution_style": requested_style,
        "equation_markers": markers,
        "equation_detected": bool(markers),
        "equation_topic": equation_topic,
        "equation_use_justified": bool(justified),
        "natural_solution_available": natural_available,
        "student_solution_style_valid": style_valid,
        "warning": "equation_usage_style_warning" if preferred_warning else None,
    }


def verify_candidate_rules(candidate: dict[str, Any]) -> dict[str, Any]:
    question = candidate.get("question") or {}
    solution = question.get("solution") or {}
    answer_spec = question.get("answer_spec") or {}
    canonical = [str(item) for item in (answer_spec.get("canonical") or [])]
    final_answer = str(solution.get("final_answer", ""))
    grading_spec = answer_spec_for_grading(question)
    grading_result = grade_answer(grading_spec, final_answer) if final_answer else {"is_correct": False, "reason": "missing_final_answer"}
    verification_spec = candidate.get("verification_spec") or {}
    expected = verification_spec.get("expected")
    equation_usage = equation_usage_detector(question)
    difficulty_consistency = difficulty_self_consistency(question)
    premium_consistency = premium_self_consistency(question)
    checks = {
        "has_stem": bool(str(question.get("stem_md", "")).strip()),
        "has_canonical_answer": bool(canonical),
        "final_answer_grades_correct": grading_result.get("is_correct") is True,
        "verification_expected_matches": expected in {None, "", final_answer, *(canonical or [])},
        "answer_recomputed_self_check": bool((candidate.get("self_checks") or {}).get("answer_recomputed", True)),
        "unique_answer_self_check": bool((candidate.get("self_checks") or {}).get("unique_answer", True)),
        "within_grade_self_check": bool((candidate.get("self_checks") or {}).get("within_grade", True)),
        "student_solution_style_valid": equation_usage["student_solution_style_valid"],
        "difficulty_has_all_dimensions": difficulty_consistency["has_all_dimensions"],
        "difficulty_dimensions_in_range": difficulty_consistency["dimensions_in_range"],
        "difficulty_total_matches_dimensions": difficulty_consistency["total_matches_dimensions"],
        "difficulty_star_matches_total": difficulty_consistency["star_matches_total"],
        "premium_has_all_dimensions": premium_consistency["has_all_dimensions"],
        "premium_dimensions_in_range": premium_consistency["dimensions_in_range"],
        "premium_total_matches_dimensions": premium_consistency["total_matches_dimensions"],
        "premium_flag_matches_total": premium_consistency["premium_matches_total"],
    }
    difficulty = independent_difficulty(question)
    premium = independent_premium(question)
    return {
        "checks": checks,
        "valid": all(checks.values()),
        "grading_result": grading_result,
        "difficulty": difficulty,
        "premium": premium,
        "difficulty_self_consistency": difficulty_consistency,
        "premium_self_consistency": premium_consistency,
        "equation_usage_detector": equation_usage,
        "issues": [key for key, ok in checks.items() if not ok] + ([equation_usage["warning"]] if equation_usage["warning"] else []),
    }
