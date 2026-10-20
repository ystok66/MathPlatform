from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from .models import GeneratedCandidate
from .semantic_similarity import similarity_report


def normalize_stem(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip().lower())


def candidate_question(candidate: GeneratedCandidate) -> dict[str, Any]:
    return (candidate.payload or {}).get("question") or {}


def design_fingerprint(candidate: GeneratedCandidate) -> dict[str, str]:
    payload = candidate.payload or {}
    question = candidate_question(candidate)
    design = payload.get("design_summary") or {}
    solution = question.get("solution") or {}
    taxonomy = question.get("taxonomy") or {}
    return {
        "strategy": str(candidate.strategy or ""),
        "answer_type": str(question.get("answer_type") or ""),
        "primary_method": str(solution.get("primary_method") or solution.get("solution_hook") or ""),
        "task_form": str(design.get("task_form") or design.get("chosen_strategy") or ""),
        "representation": str(design.get("representation") or ""),
        "constraint_pattern": str(design.get("constraint_change") or design.get("numeric_design") or ""),
        "story_domain": str(design.get("story_domain") or ""),
        "primary_tag": str(taxonomy.get("primary_tag_id") or ""),
    }


def pairwise_candidate_similarity(candidates: list[GeneratedCandidate]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, left in enumerate(candidates):
        for right in candidates[index + 1 :]:
            left_question = candidate_question(left)
            right_question = candidate_question(right)
            text_report = similarity_report(left_question.get("stem_md", ""), right_question.get("stem_md", ""))
            left_fp = design_fingerprint(left)
            right_fp = design_fingerprint(right)
            matching_axes = [key for key in left_fp if left_fp[key] and left_fp[key] == right_fp[key]]
            rows.append(
                {
                    "candidate_ids": [left.id, right.id],
                    "lexical_similarity": text_report["lexical_similarity"],
                    "semantic_similarity": text_report["semantic_similarity"],
                    "number_only_variant": text_report["number_only_variant"],
                    "matching_fingerprint_axes": matching_axes,
                    "risk": "high" if text_report["semantic_similarity"] > 0.86 or len(matching_axes) >= 6 else ("medium" if text_report["semantic_similarity"] > 0.72 or len(matching_axes) >= 4 else "low"),
                }
            )
    return rows


def candidate_set_review(candidates: list[GeneratedCandidate]) -> dict[str, Any]:
    strategy_counts = Counter(candidate.strategy for candidate in candidates)
    answer_type_counts = Counter((candidate_question(candidate).get("answer_type") or "unknown") for candidate in candidates)
    similarity_risk_counts = Counter(((candidate.payload or {}).get("self_checks") or {}).get("source_similarity_risk", "unknown") for candidate in candidates)
    stems: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        stem = normalize_stem(candidate_question(candidate).get("stem_md", ""))
        if stem:
            stems[stem].append(candidate.id)
    duplicate_groups = [{"stem_hash": stem, "candidate_ids": sorted(ids), "count": len(ids)} for stem, ids in stems.items() if len(ids) > 1]
    pairwise = pairwise_candidate_similarity(candidates)
    high_pairs = [row for row in pairwise if row["risk"] == "high"]
    regeneration_slots = []
    for row in high_pairs:
        left_id, right_id = row["candidate_ids"]
        left = next(candidate for candidate in candidates if candidate.id == left_id)
        right = next(candidate for candidate in candidates if candidate.id == right_id)
        weaker = left if (left.score or 0) <= (right.score or 0) else right
        regeneration_slots.append({"candidate_id": weaker.id, "reason": "pairwise_similarity_high", "conflict": row})
    warnings = []
    if duplicate_groups:
        warnings.append("duplicate_candidate_stems")
    if similarity_risk_counts.get("high", 0):
        warnings.append("high_source_similarity_risk")
    if len(strategy_counts) < 3 and candidates:
        warnings.append("strategy_coverage_below_three")
    if high_pairs:
        warnings.append("high_pairwise_candidate_similarity")
    diversity_score = max(0, 100 - len(high_pairs) * 35 - len(duplicate_groups) * 25 - max(0, 3 - len(strategy_counts)) * 15)
    coverage_score = min(100, len(strategy_counts) * 25 + len(answer_type_counts) * 15 + max(0, 40 - len(warnings) * 10))
    return {
        "candidate_count": len(candidates),
        "accepted_count": sum(1 for candidate in candidates if candidate.status in {"accepted", "approved"}),
        "strategy_counts": dict(strategy_counts),
        "answer_type_counts": dict(answer_type_counts),
        "source_similarity_risk_counts": dict(similarity_risk_counts),
        "duplicate_groups": duplicate_groups,
        "pairwise_similarity_matrix": pairwise,
        "diversity_score": diversity_score,
        "coverage_score": coverage_score,
        "regeneration_slots": regeneration_slots,
        "warnings": warnings,
    }


def diff_payload(original: Any, edited: Any, path: str = "") -> list[dict[str, Any]]:
    if isinstance(original, dict) and isinstance(edited, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(original) | set(edited)):
            child_path = f"{path}.{key}" if path else str(key)
            if key not in original:
                changes.append({"path": child_path, "change": "added", "before": None, "after": edited[key]})
            elif key not in edited:
                changes.append({"path": child_path, "change": "removed", "before": original[key], "after": None})
            else:
                changes.extend(diff_payload(original[key], edited[key], child_path))
        return changes
    if isinstance(original, list) and isinstance(edited, list):
        if original == edited:
            return []
        return [{"path": path, "change": "changed", "before": original, "after": edited}]
    if original != edited:
        return [{"path": path, "change": "changed", "before": original, "after": edited}]
    return []
