from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import project_root
from .models import QuestionTag, TaxonomyAlias, TaxonomyGovernanceRecord, TaxonomyMigration, TaxonomyTag


def taxonomy_payload(db: Session) -> dict:
    counts = dict(db.execute(select(QuestionTag.tag_id, func.count(QuestionTag.question_id)).group_by(QuestionTag.tag_id)).all())
    tags = list(db.scalars(select(TaxonomyTag).order_by(TaxonomyTag.kind, TaxonomyTag.label, TaxonomyTag.id)))
    aliases = list(db.scalars(select(TaxonomyAlias).order_by(TaxonomyAlias.kind, TaxonomyAlias.alias)))
    migrations = list(db.scalars(select(TaxonomyMigration).order_by(TaxonomyMigration.created_at, TaxonomyMigration.id)))
    governance = list(db.scalars(select(TaxonomyGovernanceRecord).order_by(TaxonomyGovernanceRecord.created_at, TaxonomyGovernanceRecord.id)))
    payload = {
        "schema_version": "taxonomy_export_v1",
        "taxonomy_version": "taxonomy_v1",
        "tags": [
            {
                "id": tag.id,
                "kind": tag.kind,
                "label": tag.label,
                "parent_id": tag.parent_id,
                "aliases": tag.aliases,
                "status": tag.status,
                "description": tag.description,
                "question_count": int(counts.get(tag.id, 0)),
            }
            for tag in tags
        ],
        "aliases": [
            {"id": row.id, "kind": row.kind, "alias": row.alias, "canonical_tag_id": row.canonical_tag_id, "reason": row.reason}
            for row in aliases
        ],
        "migrations": [
            {"id": row.id, "from_tag_id": row.from_tag_id, "to_tag_id": row.to_tag_id, "reason": row.reason, "status": row.status}
            for row in migrations
        ],
        "governance_records": [
            {"id": row.id, "action": row.action, "tag_id": row.tag_id, "payload": row.payload, "reviewer": row.reviewer, "status": row.status}
            for row in governance
        ],
    }
    payload["content_hash"] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return payload


def export_taxonomy(db: Session, output_path: Path | None = None) -> dict:
    payload = taxonomy_payload(db)
    target = output_path or (project_root() / "docs" / "generated" / "taxonomy_v1.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "output_path": str(target)}
