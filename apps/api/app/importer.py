from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .calibration import calibrate_evaluation
from .models import AnswerSpec, Question, QuestionTag, QuestionVersion, ReviewQueueItem, SourceFile, TaxonomyAlias, TaxonomyTag
from .semantic_similarity import semantic_hash
from .text_normalization import family_id_from_text, normalize_label, sha256_normalized

QUESTION_FILES = [
    "丘班真题_纯文字题目整理(1)_手工标签分类.json",
    "华罗庚杯1986-2020_纯文字题目整理_手工标签分类.json",
    "希望杯五年级_文字题整理_2010-2017_手工标签分类.json",
    "希望杯六年级_2010-2017_纯文字题整理_手工标签分类.json",
    "数学文字题整理_华附小联盟大联盟_2006-2014_手工标签分类.json",
    "ai_generated_gpt_6packs_20260721_import.json",
]
AI_GENERATED_SOURCE_NAME = "AI生成题"
RUSSIAN_ROOT_NAME = "俄罗斯题目"
RUSSIAN_SOURCE_NAMES = {
    "春季阿基米德竞赛.json": "俄罗斯·春季阿基米德竞赛",
    "斯皮瓦克.json": "俄罗斯·斯皮瓦克",
    "vmo_grade5_6_text_only_question_v2.json": "俄罗斯·俄罗斯全国奥赛学校阶段",
    "matprazdnik_question_v2_1990_2026.json": "俄罗斯·莫斯科数学节六年级",
}
SKIP_IMPORT_NAME_PARTS = ("manifest", "validation", "excluded", "taxonomy_overrides")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_tag_id(kind: str, label: str) -> str:
    canonical = normalize_label(label)
    slug = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", canonical).strip("_")
    digest = hashlib.sha1(f"{kind}:{canonical}".encode("utf-8")).hexdigest()[:8]
    return f"{kind}:{slug}:{digest}"


def _first_list_value(value: Any, default: str = "") -> str:
    if isinstance(value, list):
        return str(value[0]) if value else default
    if value is None:
        return default
    return str(value)


def _string_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def map_answer_spec(raw: dict[str, Any]) -> dict[str, Any]:
    canonical = _string_values(raw.get("canonical") or raw.get("canonical_answer"))
    accepted = _string_values(raw.get("accepted_forms") or raw.get("acceptable_answers"))
    grading_method = raw.get("grading_method")
    if grading_method is None and isinstance(raw.get("normalization"), list):
        grading_method = "exact_normalized"
    return {
        "canonical": canonical or [""],
        "accepted_forms": accepted,
        "tolerance": raw.get("tolerance"),
        "unit": raw.get("unit"),
        "order_sensitive": bool(raw.get("order_sensitive", True)),
        "normalization": [grading_method or "exact_normalized"],
        "raw": raw,
    }


def _step_texts_from_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        if isinstance(value.get("steps"), list):
            return _step_texts_from_value(value.get("steps"))
        text = value.get("explanation") or value.get("text") or value.get("content") or value.get("step")
        return [str(text).strip()] if text and str(text).strip() else []
    if isinstance(value, list):
        texts: list[str] = []
        for item in value:
            texts.extend(_step_texts_from_value(item))
        return texts
    return []


def map_solution_steps(q: dict[str, Any], raw_answer: dict[str, Any]) -> list[dict[str, str]]:
    solution = q.get("solution") or {}
    sources = [
        solution.get("steps"),
        solution.get("student_steps"),
        q.get("student_solution"),
        q.get("student_steps"),
        solution.get("verification_solution"),
        q.get("verification_solution"),
        (raw_answer.get("verification") or {}).get("steps"),
    ]
    for source in sources:
        texts = _step_texts_from_value(source)
        if texts:
            return [{"explanation": text} for text in texts]
    return []


def map_question_v2(q: dict[str, Any], source_name: str) -> dict[str, Any]:
    original_qv2 = q.get("_original_question_v2")
    if isinstance(original_qv2, dict):
        source = dict(original_qv2.get("source") or {})
        source["source_name"] = source_name
        source["batch_id"] = source_name
        return {
            **original_qv2,
            "source": source,
            "answer_spec": map_answer_spec(q.get("answer_spec") or {}),
            "provenance": {
                **(original_qv2.get("provenance") or {}),
                "source_metadata": q.get("source_metadata") or {},
                "importer_version": "importer_v2",
            },
        }
    raw_answer = q.get("answer_spec") or {}
    eval_block = q.get("difficulty_quality_evaluation") or {}
    solution = q.get("solution") or {}
    calibration = calibrate_evaluation(eval_block)
    primary = q.get("primary_path") or {}
    return {
        "schema_version": "question_v2",
        "id": q.get("id"),
        "version": 1,
        "status": "verified" if raw_answer.get("status") == "verified" else "needs_review",
        "source": {
            "kind": "imported",
            "source_name": source_name,
            "source_question_id": q.get("id"),
            "parent_question_id": None,
            "family_id": None,
            "generation_job_id": None,
        },
        "content": {
            "stem_md": q.get("question_text", ""),
            "answer_type": map_content_answer_type(raw_answer.get("answer_type")),
            "language": "zh-CN",
            "assets": [],
        },
        "answer_spec": map_answer_spec(raw_answer),
        "solution": {
            "final_answer": str(raw_answer.get("canonical_answer", "")),
            "solution_hook": solution.get("solution_hook") or eval_block.get("solution_hook") or q.get("classification_basis") or "",
            "primary_method": solution.get("primary_method"),
            "solution_style_used": solution.get("solution_style_used") or q.get("solution_style_used"),
            "natural_solution_available": solution.get("natural_solution_available") if "natural_solution_available" in solution else q.get("natural_solution_available"),
            "equation_necessity": solution.get("equation_necessity") or q.get("equation_necessity"),
            "equation_reason": solution.get("equation_reason") or q.get("equation_reason"),
            "steps": map_solution_steps(q, raw_answer),
            "student_steps": _step_texts_from_value(solution.get("student_steps") or q.get("student_solution") or q.get("student_steps")),
            "alternative_methods": solution.get("alternative_methods") or [],
            "alternative_solution": solution.get("alternative_solution") or q.get("alternative_solution"),
            "verification_solution": solution.get("verification_solution") or q.get("verification_solution"),
            "pitfalls": solution.get("pitfalls") or ([eval_block.get("main_pitfall")] if eval_block.get("main_pitfall") else []),
        },
        "taxonomy": {
            "primary_tag_id": stable_tag_id("primary", primary.get("level_3") or primary.get("level_2") or primary.get("level_1") or "未分类"),
            "secondary_tag_ids": [],
            "method_tag_ids": [stable_tag_id("method", x) for x in q.get("method_tags") or []],
            "task_tag_ids": [stable_tag_id("task", x) for x in q.get("task_tags") or []],
            "structure_tag_ids": [stable_tag_id("structure", x) for x in q.get("structure_tags") or []],
            "constraint_tag_ids": [stable_tag_id("constraint", x) for x in q.get("constraint_tags") or []],
            "constraint_notes": q.get("constraint_tags") or [],
        },
        "evaluation": {
            "validity": "normal" if raw_answer.get("status") == "verified" else "needs_review",
            "quality_flags": eval_block.get("quality_flags") or [],
            "difficulty": {
                **(eval_block.get("dimension_scores") or {}),
                "total_score": eval_block.get("total_score") or 0,
                "star": eval_block.get("difficulty_star") or 1,
                "reason": eval_block.get("difficulty_reason") or "",
                "calibration_version": "source_original_v1",
            },
            "premium": {
                "innovation": 0,
                "elegance": 0,
                "integration": 0,
                "transfer_value": 0,
                "total_score": eval_block.get("premium_score") or 0,
                "is_premium": bool(eval_block.get("premium")),
                "reasons": eval_block.get("premium_reasons") or [],
            },
        },
        "verification": {
            "level": 2 if raw_answer.get("status") == "verified" else 0,
            "independent_answers": [],
            "deterministic_engine": "human",
            "spec": raw_answer.get("verification") or {},
            "verified_at": None,
            "reviewer_model": None,
            "human_approved_by": None,
        },
        "dedupe": {
            "exact_hash": sha256_text(q.get("question_text", "")),
            "normalized_hash": sha256_normalized(q.get("question_text", "")),
            "semantic_hash": semantic_hash(q.get("question_text", "")),
            "family_id": family_id_from_text(q.get("question_text", "")),
        },
        "provenance": {"source_metadata": q.get("source_metadata") or {}, "importer_version": "importer_v2"},
        "created_at": None,
    }


def map_content_answer_type(answer_type: str | None) -> str:
    mapping = {
        "integer": "integer",
        "number": "decimal",
        "fraction": "rational",
        "rational": "rational",
        "decimal": "decimal",
        "choice": "choice",
        "set": "set",
        "ordered_tuple": "multi_blank",
        "multi_blank": "multi_blank",
        "expression": "expression",
        "proof_rubric": "open_response",
    }
    return mapping.get(answer_type or "", "open_response")


def collect_tags(q: dict[str, Any]) -> list[tuple[str, str, str]]:
    tags: list[tuple[str, str, str]] = []
    primary = q.get("primary_path") or {}
    for key in ("level_1", "level_2", "level_3"):
        if primary.get(key):
                tags.append(("primary", normalize_label(primary[key]), key))
    for sp in q.get("secondary_paths") or []:
        for key in ("level_1", "level_2", "level_3"):
            if sp.get(key):
                tags.append(("secondary", normalize_label(sp[key]), key))
    for kind, field in (
        ("method", "method_tags"),
        ("task", "task_tags"),
        ("structure", "structure_tags"),
        ("constraint", "constraint_tags"),
    ):
        for label in q.get(field) or []:
            tags.append((kind, normalize_label(str(label)), field))
    deduped: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind, label, role in tags:
        key = (kind, label)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((kind, label, role))
    return deduped


def display_source_name(source_name: str, q: dict[str, Any]) -> str:
    meta = q.get("source_metadata") or {}
    display = str(meta.get("source_display_name") or "").strip()
    return display or source_name


def is_ai_generated_import(q: dict[str, Any], source_name: str) -> bool:
    meta = q.get("source_metadata") or {}
    return meta.get("source_kind") == "ai_generated_import" or source_name == AI_GENERATED_SOURCE_NAME


def _is_russian_path(path: Path) -> bool:
    return RUSSIAN_ROOT_NAME in path.parts


def _russian_source_name(path: Path) -> str | None:
    if not _is_russian_path(path):
        return None
    if path.name in RUSSIAN_SOURCE_NAMES:
        return RUSSIAN_SOURCE_NAMES[path.name]
    parts = list(path.parts)
    root_index = parts.index(RUSSIAN_ROOT_NAME)
    if len(parts) > root_index + 1:
        return f"俄罗斯·{parts[root_index + 1]}"
    return "俄罗斯·俄罗斯题目"


def _candidate_question_paths(data_dir: Path) -> list[Path]:
    fixed = [data_dir / filename for filename in QUESTION_FILES if (data_dir / filename).exists()]
    if fixed and not _is_russian_path(data_dir):
        return fixed
    json_stems = {path.with_suffix("") for path in data_dir.rglob("*.json")}
    paths: list[Path] = []
    for path in sorted([*data_dir.rglob("*.json"), *data_dir.rglob("*.jsonl")]):
        lowered = path.name.lower()
        if any(part in lowered for part in SKIP_IMPORT_NAME_PARTS):
            continue
        if path.parent.name == "questions":
            continue
        if path.suffix.lower() == ".jsonl" and path.with_suffix("") in json_stems:
            continue
        paths.append(path)
    return paths


def _load_json_or_jsonl(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def _grading_method_for(answer_type: str, raw: dict[str, Any]) -> str:
    if raw.get("grading_method"):
        return str(raw["grading_method"])
    if answer_type in {"integer", "number", "fraction", "decimal"}:
        return "numeric_exact"
    if answer_type == "set":
        return "set_exact"
    if answer_type in {"multi_blank", "ordered_tuple"}:
        return "ordered_tuple_exact"
    if answer_type == "expression":
        return "expression_equiv"
    return "exact_normalized"


def _status_for_import(raw_status: Any, canonical: list[str], force_needs_review: bool = False) -> str:
    if force_needs_review:
        return "needs_review"
    if raw_status in {"needs_review", "invalid", "removed", "quarantined"}:
        return "needs_review"
    return "verified" if canonical else "needs_review"


def normalize_import_question(q: dict[str, Any], path: Path, source_name: str) -> dict[str, Any] | None:
    if not isinstance(q, dict):
        return None
    force_needs_review = q.get("_import_from_removed_questions") is True
    if q.get("schema_version") == "question_v2" and isinstance(q.get("content"), dict):
        content = q.get("content") or {}
        source = q.get("source") or {}
        raw_answer = q.get("answer_spec") or {}
        canonical = _string_values(raw_answer.get("canonical") or raw_answer.get("canonical_answer"))
        answer_type = str(content.get("answer_type") or raw_answer.get("answer_type") or "text")
        answer_spec = {
            "schema_version": "answer_spec_v1",
            "status": _status_for_import(raw_answer.get("status") or q.get("status"), canonical, force_needs_review),
            "answer_type": answer_type,
            "canonical_answer": _first_list_value(canonical),
            "acceptable_answers": _string_values(raw_answer.get("accepted_forms") or raw_answer.get("acceptable_answers")),
            "unit": raw_answer.get("unit"),
            "tolerance": raw_answer.get("tolerance") or {"type": "exact", "value": 0},
            "blanks": raw_answer.get("blanks") or [],
            "order_sensitive": bool(raw_answer.get("order_sensitive", True)),
            "grading_method": _grading_method_for(answer_type, raw_answer),
            "verification": raw_answer.get("verification") or q.get("verification") or {},
            "raw": raw_answer.get("raw") or {},
        }
        taxonomy = q.get("taxonomy") or {}
        evaluation = q.get("evaluation") or {}
        difficulty = evaluation.get("difficulty") or {}
        premium = evaluation.get("premium") or {}
        solution = q.get("solution") or {}
        raw_meta = raw_answer.get("raw") or {}
        source_metadata = {
            **(q.get("source_metadata") or {}),
            "source_display_name": source_name,
            "source_country": "俄罗斯" if _is_russian_path(path) else None,
            "batch_id": source_name if _is_russian_path(path) else source.get("batch_id"),
            "original_batch_id": source.get("batch_id"),
            "source_file": path.name,
            "source_path": str(path),
        }
        original = {
            **q,
            "status": answer_spec["status"],
            "source": {**source, "source_name": source_name, "batch_id": source_metadata["batch_id"]},
        }
        return {
            "id": q.get("id"),
            "question_text": str(content.get("stem_md") or ""),
            "answer_spec": answer_spec,
            "primary_path": taxonomy.get("primary_path") or {},
            "secondary_paths": taxonomy.get("secondary_paths") or [],
            "method_tags": taxonomy.get("method_tags") or [],
            "task_tags": taxonomy.get("task_tags") or [],
            "structure_tags": taxonomy.get("structure_tags") or [],
            "constraint_tags": taxonomy.get("constraint_tags") or [],
            "classification_basis": taxonomy.get("classification_basis") or "",
            "difficulty_quality_evaluation": {
                "dimension_scores": {key: value for key, value in difficulty.items() if key not in {"total_score", "star", "reason", "calibration_version"}},
                "total_score": difficulty.get("total_score") or 0,
                "difficulty_star": difficulty.get("star") or 1,
                "difficulty_reason": difficulty.get("reason") or "",
                "premium_score": premium.get("total_score") or 0,
                "premium": bool(premium.get("is_premium")),
                "premium_reasons": premium.get("reasons") or [],
                "main_pitfall": (solution.get("pitfalls") or [None])[0],
                "solution_hook": solution.get("solution_hook") or "",
            },
            "source_metadata": source_metadata,
            "section": source.get("batch_id") or source_name,
            "original_number": raw_meta.get("source_problem_number") or raw_meta.get("problem_no"),
            "_original_question_v2": original,
        }
    if "id" not in q and "stem_md" not in q:
        return None
    raw_answer = q.get("answer_spec") or {}
    canonical = _string_values(raw_answer.get("canonical_answer") or raw_answer.get("canonical") or q.get("canonical"))
    answer_type = str(raw_answer.get("answer_type") or q.get("answer_type") or "open_response")
    return {
        **q,
        "question_text": str(q.get("question_text") or q.get("stem_md") or ""),
        "answer_spec": {
            **raw_answer,
            "schema_version": "answer_spec_v1",
            "status": _status_for_import(raw_answer.get("status") or q.get("status"), canonical, force_needs_review),
            "answer_type": answer_type,
            "canonical_answer": _first_list_value(raw_answer.get("canonical_answer") or raw_answer.get("canonical") or q.get("canonical")),
            "acceptable_answers": _string_values(raw_answer.get("acceptable_answers") or raw_answer.get("accepted_forms") or q.get("canonical")),
            "tolerance": raw_answer.get("tolerance") or {"type": "exact", "value": 0},
            "order_sensitive": bool(raw_answer.get("order_sensitive", True)),
            "grading_method": _grading_method_for(answer_type, raw_answer),
        },
        "source_metadata": {
            **(q.get("source_metadata") or {}),
            "source_display_name": source_name,
            "source_country": "俄罗斯" if _is_russian_path(path) else None,
            "batch_id": source_name if _is_russian_path(path) else None,
            "source_file": path.name,
            "source_path": str(path),
            "imported_from_removed_questions": force_needs_review,
        },
    }


def extract_questions(data: Any, path: Path, source_name: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        raw_items = [item for item in data if isinstance(item, dict)]
    elif isinstance(data, dict) and isinstance(data.get("questions"), list):
        raw_items = [item for item in data["questions"] if isinstance(item, dict)]
    elif isinstance(data, dict) and isinstance(data.get("removed_questions"), list):
        raw_items = [{**item, "_import_from_removed_questions": True} for item in data["removed_questions"] if isinstance(item, dict)]
    elif isinstance(data, dict) and data.get("schema_version") == "question_v2":
        raw_items = [data]
    else:
        raw_items = []
    questions = []
    for item in raw_items:
        normalized = normalize_import_question(item, path, source_name)
        if normalized and normalized.get("id") and normalized.get("question_text"):
            questions.append(normalized)
    return questions


def parse_question_files(data_dir: Path) -> list[tuple[Path, Any]]:
    parsed = []
    for path in _candidate_question_paths(data_dir):
        parsed.append((path, _load_json_or_jsonl(path)))
    return parsed


def import_question_files(db: Session, data_dir: Path, max_questions: int | None = None) -> dict[str, int]:
    files = parse_question_files(data_dir)
    stats = {"files": 0, "questions": 0, "verified": 0, "needs_review": 0}
    remaining = max_questions
    for path, data in files:
        if remaining is not None and remaining <= 0:
            break
        text = path.read_text(encoding="utf-8")
        digest = sha256_text(text)
        source_name = _russian_source_name(path) or path.name
        questions = extract_questions(data, path, source_name)
        if remaining is not None:
            questions = questions[:remaining]
        source = db.scalar(select(SourceFile).where(SourceFile.filename == path.name))
        if source is None:
            source = SourceFile(filename=path.name, sha256=digest, question_count=len(questions))
            db.add(source)
            db.flush()
        else:
            source.sha256 = digest
            source.question_count = len(questions)

        for q in questions:
            upsert_question(db, source, source_name, q)
            status = (q.get("answer_spec") or {}).get("status")
            if status == "verified":
                stats["verified"] += 1
            elif status == "needs_review":
                stats["needs_review"] += 1
            stats["questions"] += 1
            if remaining is not None:
                remaining -= 1
        stats["files"] += 1
    db.commit()
    return stats


def upsert_question(db: Session, source: SourceFile, source_name: str, q: dict[str, Any]) -> None:
    qid = q["id"]
    raw_answer = q.get("answer_spec") or {}
    eval_block = q.get("difficulty_quality_evaluation") or {}
    stem = q.get("question_text") or ""
    exact_hash = sha256_text(stem)
    normalized_hash = sha256_normalized(stem)
    semantic_text_hash = semantic_hash(stem)
    family_id = family_id_from_text(stem)
    question = db.get(Question, qid)
    status = "verified" if raw_answer.get("status") == "verified" else "needs_review"
    calibration = calibrate_evaluation(eval_block)
    question_source_name = display_source_name(source_name, q)
    ai_generated_import = is_ai_generated_import(q, question_source_name)
    publish_status = "active_practice" if status == "verified" and ai_generated_import else "source_verified" if status == "verified" else "not_published"
    eligible_for_practice = status == "verified"
    eligible_for_test = status == "verified"
    question_trust_score = 0.8 if status == "verified" and ai_generated_import else 1.0 if status == "verified" else 0.5
    if question is None:
        question = Question(
            id=qid,
            source_file_id=source.id,
            status=status,
            publish_status=publish_status,
            eligible_for_practice=eligible_for_practice,
            eligible_for_test=eligible_for_test,
            question_fault_status="none",
            question_trust_score=question_trust_score,
            solution_style="elementary_preferred",
            source_name=question_source_name,
            section=q.get("section"),
            original_number=str(q.get("original_number")) if q.get("original_number") is not None else None,
            stem_preview=(q.get("question_text") or "")[:500],
            difficulty_star=eval_block.get("difficulty_star"),
            premium=bool(eval_block.get("premium")),
            source_difficulty_star=calibration["source_difficulty_star"],
            calibrated_difficulty_star=calibration["calibrated_difficulty_star"],
            source_premium=calibration["source_premium"],
            calibrated_premium=calibration["calibrated_premium"],
            calibration_version=calibration["calibration_version"],
            exact_hash=exact_hash,
            normalized_hash=normalized_hash,
            semantic_hash=semantic_text_hash,
            family_id=family_id,
        )
        db.add(question)
        db.flush()
    else:
        question.status = status
        question.publish_status = publish_status
        question.eligible_for_practice = eligible_for_practice
        question.eligible_for_test = eligible_for_test
        question.question_fault_status = "none"
        question.question_trust_score = question_trust_score
        question.solution_style = "elementary_preferred"
        question.source_file_id = source.id
        question.source_name = question_source_name
        question.section = q.get("section")
        question.stem_preview = (q.get("question_text") or "")[:500]
        question.difficulty_star = eval_block.get("difficulty_star")
        question.premium = bool(eval_block.get("premium"))
        question.source_difficulty_star = calibration["source_difficulty_star"]
        question.calibrated_difficulty_star = calibration["calibrated_difficulty_star"]
        question.source_premium = calibration["source_premium"]
        question.calibrated_premium = calibration["calibrated_premium"]
        question.calibration_version = calibration["calibration_version"]
        question.exact_hash = exact_hash
        question.normalized_hash = normalized_hash
        question.semantic_hash = semantic_text_hash
        question.family_id = family_id

    raw_hash = sha256_text(json.dumps(q, ensure_ascii=False, sort_keys=True))
    version = db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == qid, QuestionVersion.version == 1))
    qv2 = map_question_v2(q, question_source_name)
    if version is None:
        version = QuestionVersion(question_id=qid, version=1, raw_json=q, question_v2=qv2, content_hash=raw_hash)
        db.add(version)
        db.flush()
    else:
        version.raw_json = q
        version.question_v2 = qv2
        version.content_hash = raw_hash
    question.current_version_id = version.id

    answer = db.scalar(select(AnswerSpec).where(AnswerSpec.question_id == qid))
    normalized = map_answer_spec(raw_answer)
    if answer is None:
        answer = AnswerSpec(
            question_id=qid,
            status=raw_answer.get("status", "needs_review"),
            answer_type=raw_answer.get("answer_type", "text"),
            canonical_answer=str(raw_answer.get("canonical_answer", "")),
            normalized_spec=normalized,
            raw_answer_spec=raw_answer,
            verification=raw_answer.get("verification") or {},
        )
        db.add(answer)
    else:
        answer.status = raw_answer.get("status", "needs_review")
        answer.answer_type = raw_answer.get("answer_type", "text")
        answer.canonical_answer = str(raw_answer.get("canonical_answer", ""))
        answer.normalized_spec = normalized
        answer.raw_answer_spec = raw_answer
        answer.verification = raw_answer.get("verification") or {}

    db.execute(delete(QuestionTag).where(QuestionTag.question_id == qid))
    question_tags: list[tuple[str, str, str, str]] = []
    aliases_to_add: list[tuple[str, str, str]] = []
    for kind, label, role in collect_tags(q):
        tag_id = stable_tag_id(kind, label)
        tag = db.get(TaxonomyTag, tag_id)
        if tag is None:
            tag = TaxonomyTag(id=tag_id, kind=kind, label=label, aliases=[], version="taxonomy_v1", status="active", description=None)
            db.add(tag)
            aliases_to_add.append((kind, label, tag_id))
        question_tags.append((tag_id, kind, label, role))
    db.flush()
    for kind, label, tag_id in aliases_to_add:
        db.add(TaxonomyAlias(kind=kind, alias=label, canonical_tag_id=tag_id, reason="source_label"))
    db.flush()
    for tag_id, _kind, label, role in question_tags:
        db.add(QuestionTag(question_id=qid, tag_id=tag_id, role=role, raw_label=label))

    if status == "needs_review":
        item = db.scalar(select(ReviewQueueItem).where(ReviewQueueItem.question_id == qid, ReviewQueueItem.reason_code == "answer_spec_needs_review"))
        payload = {
            "source_name": source_name,
            "answer_status": raw_answer.get("status"),
            "review_notes": raw_answer.get("review_notes") or raw_answer.get("verification", {}).get("review_notes"),
        }
        if item is None:
            db.add(ReviewQueueItem(question_id=qid, reason_code="answer_spec_needs_review", severity="high", status="open", payload=payload))
        else:
            item.payload = payload
