from __future__ import annotations

import re
from collections import Counter
from fractions import Fraction
from typing import Any

try:
    import sympy as sp
except Exception:  # pragma: no cover - optional in production images
    sp = None


FULLWIDTH_MAP = str.maketrans(
    "，；：（）【】　０１２３４５６７８９＋－．／％",
    ",;:()[] 0123456789+-./%",
)


def normalize_text(value: str) -> str:
    value = value.strip().translate(FULLWIDTH_MAP)
    value = re.sub(r"\s+", "", value)
    return value.lower()


def strip_unit(value: str, unit: str | None) -> str:
    if not unit:
        return value
    stripped, _ = strip_unit_with_status(value, unit, {"unit_required": False})
    return stripped


def unit_aliases(spec: dict[str, Any], unit: str | None) -> list[str]:
    aliases = [str(unit)] if unit else []
    for key in ("unit_aliases", "acceptable_units"):
        aliases.extend(str(value) for value in (spec.get(key) or []) if value is not None)
    normalized = []
    seen = set()
    for alias in aliases:
        candidate = normalize_text(alias)
        if candidate and candidate not in seen:
            normalized.append(candidate)
            seen.add(candidate)
    return sorted(normalized, key=len, reverse=True)


def strip_trailing_unit_like(value: str) -> str:
    return re.sub(r"[\u4e00-\u9fffA-Za-z²³㎡㎝]+$", "", value)


def strip_unit_with_status(value: str, unit: str | None, spec: dict[str, Any]) -> tuple[str, str | None]:
    if not unit:
        return value, None
    normalized = normalize_text(value)
    for alias in unit_aliases(spec, unit):
        if normalized.endswith(alias):
            prefix = normalized[: -len(alias)]
            if prefix and re.search(r"[\u4e00-\u9fffA-Za-z²³㎡㎝]$", prefix):
                continue
            return prefix, None
    if not bool(spec.get("unit_required", False)):
        return strip_trailing_unit_like(normalized), None
    unitless = strip_trailing_unit_like(normalized)
    if unitless != normalized and parse_number(unitless) is not None:
        return unitless, "unit_mismatch"
    if parse_number(normalized) is not None:
        return normalized, "unit_missing"
    return normalized, None


def parse_number(value: str) -> Fraction | None:
    value = normalize_text(value)
    value = value.replace("约", "")
    value = re.sub(r"(个|条|米|厘米|平方厘米|立方厘米|分钟|小时|元|岁|人|本|颗|枚|种|组|平方步)$", "", value)
    if not value:
        return None
    try:
        if value.endswith("%"):
            return Fraction(value[:-1]) / 100
        mixed = re.fullmatch(r"([+-]?\d+)\u53c8(\d+/\d+)", value)
        if mixed:
            whole = Fraction(mixed.group(1))
            part = Fraction(mixed.group(2))
            return whole + part if whole >= 0 else whole - part
        if "/" in value:
            return Fraction(value)
        return Fraction(value)
    except Exception:
        return None


def split_multi(value: str) -> list[str]:
    return [part for part in re.split(r"[,;，；、\s]+", value.strip()) if part]


def normalize_part(value: str) -> str:
    return normalize_text(value).strip("{}[]().。．")


def choice_token(value: str) -> str:
    normalized = normalize_text(value)
    normalized = re.sub(r"^(答案是|答案为|答案|选择|选项|选)", "", normalized)
    match = re.match(r"^([a-z]|\d+)", normalized)
    return match.group(1) if match else normalized


def choice_matches(answer: str, candidate: str) -> bool:
    normalized_answer = normalize_text(answer)
    normalized_candidate = normalize_text(candidate)
    if normalized_answer == normalized_candidate:
        return True
    candidate_token = choice_token(candidate)
    if not candidate_token:
        return False
    return choice_token(answer) == candidate_token


def compare_expression(a: str, b: str) -> bool | None:
    if normalize_text(a) == normalize_text(b):
        return True
    if sp is None:
        return None
    try:
        return bool(sp.simplify(sp.sympify(a) - sp.sympify(b)) == 0)
    except Exception:
        return None


def equivalent_expression(a: str, b: str) -> bool:
    return compare_expression(a, b) is True


def grade_answer(spec: dict[str, Any], answer_text: str) -> dict[str, Any]:
    status = spec.get("status")
    if status != "verified":
        return {
            "is_correct": None,
            "reason": "question_needs_review",
            "message": "该题答案规范尚需人工复核，不能自动判分。",
        }

    unit = spec.get("unit")
    canonical = str(spec.get("canonical_answer", ""))
    accepted = [str(x) for x in spec.get("acceptable_answers") or []]
    candidates = [canonical, *accepted]
    grading_method = spec.get("grading_method", "exact_normalized")
    answer_type = spec.get("answer_type", "text")
    tolerance = spec.get("tolerance") or {"type": "exact", "value": 0}
    answer_wo_unit, unit_error = strip_unit_with_status(answer_text, unit, spec)

    if grading_method in {"numeric_exact", "numeric_with_tolerance"} or answer_type in {"integer", "number", "fraction", "decimal"}:
        if unit_error:
            return {"is_correct": False, "reason": unit_error, "required_unit": unit}
        user_num = parse_number(answer_wo_unit)
        if user_num is None:
            return {"is_correct": False, "reason": "not_numeric"}
        target_found = False
        for candidate in candidates:
            target = parse_number(strip_unit(candidate, unit))
            if target is None:
                continue
            target_found = True
            if tolerance.get("type") == "absolute":
                if abs(float(user_num - target)) <= float(tolerance.get("value", 0)):
                    return {"is_correct": True, "reason": "numeric_with_tolerance", "matched": candidate}
            elif user_num == target:
                return {"is_correct": True, "reason": "numeric_exact", "matched": candidate}
        if not target_found:
            return {"is_correct": None, "reason": "answer_spec_invalid", "canonical": canonical}
        return {"is_correct": False, "reason": "numeric_mismatch", "canonical": canonical}

    if answer_type == "expression":
        saw_uncertain = False
        for candidate in candidates:
            comparison = compare_expression(answer_text, candidate)
            if comparison is True:
                return {"is_correct": True, "reason": "expression_equivalent", "matched": candidate}
            if comparison is None:
                saw_uncertain = True
        if saw_uncertain:
            return {"is_correct": None, "reason": "expression_unverified", "canonical": canonical}
        return {"is_correct": False, "reason": "expression_mismatch", "canonical": canonical}

    if answer_type in {"multi_blank", "ordered_tuple"} or grading_method == "ordered_tuple_exact":
        user_parts = split_multi(answer_text)
        canonical_parts = split_multi(canonical)
        if not user_parts:
            return {"is_correct": False, "reason": "empty_answer", "canonical": canonical}
        if len(user_parts) != len(canonical_parts):
            return {
                "is_correct": False,
                "reason": "part_count_mismatch",
                "expected_count": len(canonical_parts),
                "actual_count": len(user_parts),
                "canonical": canonical,
            }
        if spec.get("order_sensitive", True):
            ok = [normalize_text(x) for x in user_parts] == [normalize_text(x) for x in canonical_parts]
        else:
            ok = sorted(normalize_text(x) for x in user_parts) == sorted(normalize_text(x) for x in canonical_parts)
        return {"is_correct": ok, "reason": "multi_part" if ok else "part_value_or_order_mismatch", "canonical": canonical}

    if answer_type == "set" or grading_method == "set_exact":
        user_parts = [normalize_part(x) for x in split_multi(answer_text)]
        user_parts = [part for part in user_parts if part]
        canonical_parts = [normalize_part(x) for x in split_multi(canonical)]
        user_set = set(user_parts)
        canonical_set = set(canonical_parts)
        if not user_parts:
            return {"is_correct": False, "reason": "empty_answer", "canonical": canonical}
        duplicates = sorted(part for part, count in Counter(user_parts).items() if count > 1)
        if duplicates:
            return {"is_correct": False, "reason": "set_duplicate_part", "duplicate_parts": duplicates, "canonical": canonical}
        missing = sorted(canonical_set - user_set)
        extra = sorted(user_set - canonical_set)
        if missing or extra:
            reason = "set_missing" if missing and not extra else "set_extra" if extra and not missing else "set_mismatch"
            return {"is_correct": False, "reason": reason, "missing_parts": missing, "extra_parts": extra, "canonical": canonical}
        return {"is_correct": True, "reason": "set_compare", "canonical": canonical}

    if answer_type == "choice" or grading_method == "choice_exact":
        normalized_answer = normalize_text(strip_unit(answer_text, unit))
        for candidate in candidates:
            normalized_candidate = normalize_text(strip_unit(candidate, unit))
            if choice_matches(normalized_answer, normalized_candidate):
                return {"is_correct": True, "reason": "choice_match", "matched": candidate}
        return {"is_correct": False, "reason": "choice_mismatch", "canonical": canonical}

    normalized_answer = normalize_text(strip_unit(answer_text, unit))
    for candidate in candidates:
        if normalized_answer == normalize_text(strip_unit(candidate, unit)):
            return {"is_correct": True, "reason": "exact_normalized", "matched": candidate}
    return {"is_correct": False, "reason": "text_mismatch", "canonical": canonical}
