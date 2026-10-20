from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import project_root
from .models import QuestionVersion, TaxonomyTag
from .text_normalization import normalize_label


STANDARD_CONSTRAINT_PATTERNS = {
    "equation_constraint": re.compile(r"[=＝]"),
    "number_constraint": re.compile(r"\d"),
    "geometry_constraint": re.compile(r"(图|角|边|面积|周长|圆|三角形|正方形|长方形)"),
    "counting_constraint": re.compile(r"(至少|至多|恰好|不同|相同|排列|组合|种)"),
}


def standardize_constraint_note(note: str) -> dict[str, Any]:
    normalized = normalize_label(note)
    matched = [name for name, pattern in STANDARD_CONSTRAINT_PATTERNS.items() if pattern.search(normalized)]
    return {
        "raw": note,
        "normalized": normalized,
        "standard_tags": matched or ["free_text_constraint"],
        "needs_manual_review": not matched or len(normalized) > 12,
    }


def suspicious_tag(tag: TaxonomyTag) -> dict[str, Any] | None:
    normalized = normalize_label(tag.label)
    reasons = []
    if len(normalized) > 24:
        reasons.append("label_too_long")
    if re.search(r"\d|[=＝]|，|,|；|;", normalized):
        reasons.append("looks_like_condition_not_taxonomy")
    if tag.kind in {"primary", "secondary"} and len(normalized) < 2:
        reasons.append("too_short_for_primary_taxonomy")
    if not reasons:
        return None
    return {"tag_id": tag.id, "kind": tag.kind, "label": tag.label, "normalized": normalized, "reasons": reasons}


def alias_drift_groups(tags: list[TaxonomyTag]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[TaxonomyTag]] = {}
    for tag in tags:
        groups.setdefault((tag.kind, normalize_label(tag.label)), []).append(tag)
    return [
        {"kind": kind, "normalized_label": normalized, "tag_ids": sorted(tag.id for tag in rows), "labels": sorted(tag.label for tag in rows)}
        for (kind, normalized), rows in groups.items()
        if len(rows) > 1
    ]


def governance_scan(db: Session) -> dict[str, Any]:
    tags = list(db.scalars(select(TaxonomyTag).order_by(TaxonomyTag.kind, TaxonomyTag.label, TaxonomyTag.id)))
    versions = list(db.scalars(select(QuestionVersion).order_by(QuestionVersion.question_id).limit(500)))
    constraint_notes = []
    for version in versions:
        notes = (((version.question_v2 or {}).get("taxonomy") or {}).get("constraint_notes") or [])
        for note in notes:
            if isinstance(note, str):
                constraint_notes.append({"question_id": version.question_id, **standardize_constraint_note(note)})
    suspicious = [item for tag in tags if (item := suspicious_tag(tag))]
    payload = {
        "schema_version": "taxonomy_governance_scan_v1",
        "sampled_question_versions": len(versions),
        "constraint_note_findings": constraint_notes,
        "constraint_notes_needing_review": [row for row in constraint_notes if row["needs_manual_review"]],
        "suspicious_tags": suspicious,
        "alias_drift_groups": alias_drift_groups(tags),
    }
    return payload


def export_governance_scan(db: Session, output_path: Path | None = None) -> dict[str, Any]:
    payload = governance_scan(db)
    target = output_path or (project_root() / "docs" / "generated" / "taxonomy_governance_scan.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "output_path": str(target)}
