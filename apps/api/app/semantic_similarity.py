from __future__ import annotations

import hashlib
import re
from itertools import combinations
from typing import Any

from .text_normalization import normalize_question_text


NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?|[零一二三四五六七八九十百千万亿两]+")


def semantic_signature(text: str) -> str:
    normalized = normalize_question_text(text)
    return NUMBER_PATTERN.sub("#", normalized)


def semantic_hash(text: str) -> str:
    return hashlib.sha256(semantic_signature(text).encode("utf-8")).hexdigest()


def _ngrams(text: str, n: int = 3) -> set[str]:
    if not text:
        return set()
    if len(text) <= n:
        return {text}
    return {text[index : index + n] for index in range(len(text) - n + 1)}


def jaccard_similarity(left: str, right: str) -> float:
    left_grams = _ngrams(left)
    right_grams = _ngrams(right)
    if not left_grams and not right_grams:
        return 1.0
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def numeric_tokens(text: str) -> list[str]:
    return NUMBER_PATTERN.findall(str(text))


def similarity_report(source_text: str, candidate_text: str, high_threshold: float = 0.86, medium_threshold: float = 0.72) -> dict[str, Any]:
    source_norm = normalize_question_text(source_text)
    candidate_norm = normalize_question_text(candidate_text)
    source_signature = semantic_signature(source_text)
    candidate_signature = semantic_signature(candidate_text)
    lexical = jaccard_similarity(source_norm, candidate_norm)
    semantic = jaccard_similarity(source_signature, candidate_signature)
    source_numbers = numeric_tokens(source_text)
    candidate_numbers = numeric_tokens(candidate_text)
    number_only_variant = (
        semantic >= high_threshold
        and source_signature == candidate_signature
        and source_numbers != candidate_numbers
        and source_norm != candidate_norm
    )
    if number_only_variant or (lexical >= high_threshold and semantic >= high_threshold):
        risk = "high"
    elif lexical >= medium_threshold or semantic >= medium_threshold:
        risk = "medium"
    else:
        risk = "low"
    return {
        "lexical_similarity": round(lexical, 4),
        "semantic_similarity": round(semantic, 4),
        "source_semantic_hash": semantic_hash(source_text),
        "candidate_semantic_hash": semantic_hash(candidate_text),
        "source_numbers": source_numbers,
        "candidate_numbers": candidate_numbers,
        "number_only_variant": number_only_variant,
        "source_similarity_risk": risk,
        "thresholds": {"medium": medium_threshold, "high": high_threshold},
    }


def near_duplicate_groups(items: list[tuple[str, str]], threshold: float = 0.86, max_pairs: int = 50) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for (left_id, left_text), (right_id, right_text) in combinations(items, 2):
        report = similarity_report(left_text, right_text, high_threshold=threshold)
        if report["semantic_similarity"] >= threshold:
            pairs.append(
                {
                    "question_ids": [left_id, right_id],
                    "semantic_similarity": report["semantic_similarity"],
                    "lexical_similarity": report["lexical_similarity"],
                    "number_only_variant": report["number_only_variant"],
                    "source_similarity_risk": report["source_similarity_risk"],
                }
            )
            if len(pairs) >= max_pairs:
                break
    return pairs
