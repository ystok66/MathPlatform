from __future__ import annotations

import hashlib
import json
import re
import runpy
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
GPT_ROOT_PREFIX = "GPT"
OUTPUT_FILENAME = "ai_generated_gpt_6packs_20260721_import.json"
SUMMARY_FILENAME = "gpt6_import_summary_20260721.json"
AI_SOURCE_DISPLAY = "AI\u751f\u6210\u9898"
LEVEL1_BY_CODE = {
    "CALC": "\u8ba1\u7b97\u4e0e\u8fd0\u7b97",
    "NT": "\u6570\u8bba",
    "ALG": "\u4ee3\u6570\u4e0e\u89c4\u5f8b",
    "APP": "\u5e94\u7528\u9898",
    "GEO": "\u51e0\u4f55",
    "COMB": "\u8ba1\u6570\u3001\u7ec4\u5408\u4e0e\u6982\u7387",
    "LOGIC": "\u903b\u8f91\u4e0e\u7b56\u7565",
    "INNOV": "\u7efc\u5408\u4e0e\u521b\u65b0",
}


def slugify(value: str) -> str:
    value = value.replace("\\", "/").lower()
    value = re.sub(r"[^0-9a-zA-Z]+", "-", value).strip("-")
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    base = value[:48].strip("-") or "source"
    return f"{base}-{digest}"


def old_answer_type(answer_type: str | None) -> str:
    mapping = {
        "integer": "integer",
        "decimal": "decimal",
        "rational": "fraction",
        "fraction": "fraction",
        "choice": "choice",
        "set": "set",
        "multi_blank": "multi_blank",
        "ordered_tuple": "ordered_tuple",
        "expression": "expression",
        "open_response": "open_response",
        "proof_rubric": "open_response",
    }
    return mapping.get(str(answer_type or ""), "open_response")


def grading_method(answer_type: str, order_sensitive: bool = True) -> str:
    if answer_type in {"integer", "decimal", "fraction", "number"}:
        return "numeric_exact"
    if answer_type == "set":
        return "set_exact"
    if answer_type in {"multi_blank", "ordered_tuple"}:
        return "ordered_tuple_exact" if order_sensitive else "set_exact"
    if answer_type == "expression":
        return "expression_equiv"
    return "exact_normalized"


def as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def tag_label(tag_id: Any) -> str:
    value = str(tag_id or "").strip()
    if not value:
        return ""
    for prefix in ("METHOD-", "TASK-", "STRUCT-", "CONSTRAINT-"):
        if value.startswith(prefix):
            return value[len(prefix) :]
    match = re.match(r"^KN-([A-Z]+)-(\d+)-(.+)$", value)
    if match:
        return match.group(3)
    return value


def fallback_primary_path(tag_id: Any) -> dict[str, str]:
    value = str(tag_id or "").strip()
    match = re.match(r"^KN-([A-Z]+)-(\d+)-(.+)$", value)
    if match:
        code, _, label = match.groups()
        return {
            "level_1_code": code,
            "level_1": LEVEL1_BY_CODE.get(code, "\u672a\u5206\u7c7b"),
            "level_2": label,
            "level_3": "",
        }
    label = tag_label(value) or "\u672a\u5206\u7c7b"
    return {"level_1": label, "level_2": "", "level_3": ""}


def collect_tag_manifest_records(value: Any, out: dict[str, dict[str, Any]]) -> None:
    if isinstance(value, dict):
        tag_id = value.get("tag_id")
        if tag_id and value.get("level_1"):
            out[str(tag_id)] = value
        for item in value.values():
            collect_tag_manifest_records(item, out)
    elif isinstance(value, list):
        for item in value:
            collect_tag_manifest_records(item, out)


def load_tag_lookup(gpt_root: Path) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for path in gpt_root.rglob("tag_manifest.json"):
        try:
            collect_tag_manifest_records(json.loads(path.read_text(encoding="utf-8")), lookup)
        except Exception:
            continue
    return lookup


def path_from_tag_id(tag_id: Any, tag_lookup: dict[str, dict[str, Any]]) -> dict[str, str]:
    record = tag_lookup.get(str(tag_id or ""))
    if record:
        return {
            "level_1_code": str(record.get("level_1_code") or ""),
            "level_1": str(record.get("level_1") or ""),
            "level_2": str(record.get("level_2") or ""),
            "level_3": str(record.get("level_3") or ""),
        }
    return fallback_primary_path(tag_id)


def canonical_answer(canonical: list[str], answer_type: str) -> str:
    if not canonical:
        return ""
    if answer_type in {"set", "multi_blank", "ordered_tuple"}:
        return ", ".join(canonical)
    if answer_type == "open_response" and len(canonical) > 1:
        return "\uff1b".join(canonical)
    return canonical[0]


def solution_steps(solution: dict[str, Any], verification: dict[str, Any]) -> list[str]:
    student = solution.get("student_solution") or {}
    steps = as_str_list(student.get("steps"))
    if steps:
        return steps
    if isinstance(solution.get("steps"), list):
        return as_str_list(solution.get("steps"))
    verification_solution = solution.get("verification_solution") or {}
    steps = as_str_list(verification_solution.get("steps"))
    if steps:
        return steps
    return as_str_list(verification.get("steps"))


def compact_original(raw: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(raw, ensure_ascii=False))


def qv2_to_legacy(q: dict[str, Any], source_rel: str, source_index: int, tag_lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    content = q.get("content") or {}
    taxonomy = q.get("taxonomy") or {}
    solution = q.get("solution") or {}
    evaluation = q.get("evaluation") or {}
    difficulty = evaluation.get("difficulty") or {}
    premium = evaluation.get("premium") or {}
    verification = q.get("verification") or {}
    answer = q.get("answer_spec") or {}

    answer_type = old_answer_type(content.get("answer_type"))
    canonical = as_str_list(answer.get("canonical"))
    accepted = as_str_list(answer.get("accepted_forms"))
    final = (
        ((solution.get("student_solution") or {}).get("final_answer"))
        or solution.get("final_answer")
        or canonical_answer(canonical, answer_type)
    )
    primary_path = taxonomy.get("primary_path") or path_from_tag_id(taxonomy.get("primary_tag_id"), tag_lookup)
    secondary_paths = taxonomy.get("secondary_paths")
    if not isinstance(secondary_paths, list):
        secondary_paths = [path_from_tag_id(tag_id, tag_lookup) for tag_id in as_str_list(taxonomy.get("secondary_tag_ids"))]
    steps = solution_steps(solution, verification)
    source_slug = slugify(source_rel)
    original_id = str(q.get("id") or f"{source_index:03d}")

    return {
        "id": f"gpt6-{source_slug}-{slugify(original_id)}",
        "section": "GPT\u51fa\u98986\u4efd",
        "original_number": source_index,
        "question_text": str(content.get("stem_md") or ""),
        "primary_path": primary_path,
        "secondary_paths": secondary_paths,
        "method_tags": as_str_list(taxonomy.get("method_tags")) or [tag_label(tag_id) for tag_id in as_str_list(taxonomy.get("method_tag_ids")) if tag_label(tag_id)],
        "task_tags": as_str_list(taxonomy.get("task_tags")) or [tag_label(tag_id) for tag_id in as_str_list(taxonomy.get("task_tag_ids")) if tag_label(tag_id)],
        "structure_tags": as_str_list(taxonomy.get("structure_tags")) or [tag_label(tag_id) for tag_id in as_str_list(taxonomy.get("structure_tag_ids")) if tag_label(tag_id)],
        "constraint_tags": as_str_list(taxonomy.get("constraint_tags")) or [tag_label(tag_id) for tag_id in as_str_list(taxonomy.get("constraint_tag_ids")) if tag_label(tag_id)],
        "classification_basis": str(taxonomy.get("classification_basis") or ""),
        "answer_spec": {
            "status": "verified",
            "answer_type": answer_type,
            "canonical_answer": canonical_answer(canonical, answer_type),
            "acceptable_answers": list(dict.fromkeys([*canonical, *accepted])),
            "tolerance": answer.get("tolerance"),
            "unit": answer.get("unit"),
            "order_sensitive": bool(answer.get("order_sensitive", True)),
            "grading_method": grading_method(answer_type, bool(answer.get("order_sensitive", True))),
            "verification": {
                "engine": verification.get("engine") or ((solution.get("verification_solution") or {}).get("method")),
                "steps": steps,
                "constraints": verification.get("constraints") or [],
                "expected": verification.get("expected") or final,
                "review_notes": "Imported from GPT-generated QuestionV2 package; answer spec is structured and practice-enabled, but source is marked as AI-generated.",
            },
        },
        "difficulty_quality_evaluation": {
            "dimension_scores": {
                key: value
                for key, value in difficulty.items()
                if key not in {"total_score", "star", "reason"}
            },
            "total_score": difficulty.get("total_score") or 0,
            "difficulty_star": difficulty.get("star") or 5,
            "difficulty_reason": difficulty.get("reason") or "",
            "premium_score": premium.get("total_score") or 0,
            "premium": bool(premium.get("is_premium")),
            "premium_reasons": premium.get("reasons") or [],
            "solution_hook": solution.get("solution_hook") or "",
            "main_pitfall": (solution.get("pitfalls") or [""])[0] if isinstance(solution.get("pitfalls"), list) else "",
            "quality_flags": ["ai_generated_import"],
        },
        "source_metadata": {
            "source_kind": "ai_generated_import",
            "source_display_name": AI_SOURCE_DISPLAY,
            "source_file": source_rel,
            "source_question_id": original_id,
            "source_status": q.get("status"),
            "source": q.get("source") or {},
            "provenance": q.get("provenance") or {},
            "original_question_v2": compact_original(q),
        },
    }


def py_item_to_legacy(item: dict[str, Any], source_rel: str, source_index: int) -> dict[str, Any]:
    answer = item.get("answer_spec") or {}
    answer_type = old_answer_type(item.get("answer_type"))
    canonical = as_str_list(answer.get("canonical"))
    accepted = as_str_list(answer.get("accepted_forms"))
    difficulty = item.get("difficulty") or {}
    premium = item.get("premium") or {}
    verification = item.get("verification") or {}
    original_id = str(item.get("id") or f"{source_index:03d}")
    source_slug = slugify(source_rel)

    return {
        "id": f"gpt6-{source_slug}-{slugify(original_id)}",
        "section": "GPT\u51fa\u98986\u4efd",
        "original_number": source_index,
        "question_text": str(item.get("stem_md") or ""),
        "primary_path": item.get("primary_path") or {},
        "secondary_paths": item.get("secondary_paths") or [],
        "method_tags": as_str_list(item.get("method_tags")),
        "task_tags": as_str_list(item.get("task_tags")),
        "structure_tags": as_str_list(item.get("structure_tags")),
        "constraint_tags": as_str_list(item.get("constraint_tags")),
        "classification_basis": str(item.get("classification_basis") or ""),
        "answer_spec": {
            "status": "verified",
            "answer_type": answer_type,
            "canonical_answer": canonical_answer(canonical, answer_type),
            "acceptable_answers": list(dict.fromkeys([*canonical, *accepted])),
            "tolerance": answer.get("tolerance"),
            "unit": answer.get("unit"),
            "order_sensitive": bool(answer.get("order_sensitive", True)),
            "grading_method": grading_method(answer_type, bool(answer.get("order_sensitive", True))),
            "verification": {
                "engine": verification.get("engine"),
                "steps": as_str_list(item.get("steps")),
                "constraints": verification.get("constraints") or [],
                "expected": verification.get("expected") or item.get("final_answer"),
                "review_notes": "Imported from GPT-generated Python source; answer spec is structured and practice-enabled, but source is marked as AI-generated.",
            },
        },
        "difficulty_quality_evaluation": {
            "dimension_scores": {
                key: value
                for key, value in difficulty.items()
                if key not in {"total_score", "star", "reason"}
            },
            "total_score": difficulty.get("total_score") or 0,
            "difficulty_star": difficulty.get("star") or 5,
            "difficulty_reason": difficulty.get("reason") or "",
            "premium_score": premium.get("total_score") or 0,
            "premium": bool(premium.get("is_premium")),
            "premium_reasons": premium.get("reasons") or [],
            "solution_hook": item.get("solution_hook") or "",
            "main_pitfall": (item.get("pitfalls") or [""])[0],
            "quality_flags": ["ai_generated_import"],
        },
        "source_metadata": {
            "source_kind": "ai_generated_import",
            "source_display_name": AI_SOURCE_DISPLAY,
            "source_file": source_rel,
            "source_question_id": original_id,
            "candidate_id": item.get("candidate_id"),
            "model": "GPT-5.6 Pro",
            "batch_id": "20260721-five-star-100-v1",
            "original_item": compact_original(item),
        },
    }


def find_gpt_root() -> Path:
    candidates = [p for p in ROOT.iterdir() if p.is_dir() and p.name.startswith(GPT_ROOT_PREFIX)]
    if not candidates:
        raise FileNotFoundError("No GPT generated question root found")
    return sorted(candidates, key=lambda p: p.name)[0]


def formal_question_v2_files(gpt_root: Path) -> list[Path]:
    files = []
    for path in gpt_root.rglob("*.json"):
        name = path.name.lower()
        rel = str(path.relative_to(gpt_root)).replace("\\", "/")
        if "question_v2" not in name:
            continue
        if "schema" in name or "expanded" in name or "/individual/" in rel:
            continue
        files.append(path)
    return sorted(files, key=lambda p: str(p.relative_to(gpt_root)))


def load_question_v2(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("questions", "items"):
            if isinstance(payload.get(key), list):
                return payload[key]
    raise ValueError(f"Unsupported question_v2 payload: {path}")


def build_payload() -> tuple[dict[str, Any], dict[str, Any]]:
    gpt_root = find_gpt_root()
    tag_lookup = load_tag_lookup(gpt_root)
    questions: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    star_counts: dict[str, int] = {}

    for path in formal_question_v2_files(gpt_root):
        source_rel = str(path.relative_to(gpt_root)).replace("\\", "/")
        qv2_items = load_question_v2(path)
        source_counts[source_rel] = len(qv2_items)
        for index, q in enumerate(qv2_items, start=1):
            converted = qv2_to_legacy(q, source_rel, index, tag_lookup)
            questions.append(converted)
            star = str((converted.get("difficulty_quality_evaluation") or {}).get("difficulty_star"))
            star_counts[star] = star_counts.get(star, 0) + 1

    py_path = gpt_root / "build_100_questions.py"
    namespace = runpy.run_path(str(py_path))
    py_items = namespace.get("items") or []
    source_counts[py_path.name] = len(py_items)
    for index, item in enumerate(py_items, start=1):
        converted = py_item_to_legacy(item, py_path.name, index)
        questions.append(converted)
        star = str((converted.get("difficulty_quality_evaluation") or {}).get("difficulty_star"))
        star_counts[star] = star_counts.get(star, 0) + 1

    seen_ids: set[str] = set()
    seen_stems: set[str] = set()
    duplicates: list[dict[str, str]] = []
    clean_questions: list[dict[str, Any]] = []
    for question in questions:
        qid = str(question.get("id"))
        stem_key = re.sub(r"\s+", "", str(question.get("question_text") or "")).lower()
        if qid in seen_ids or stem_key in seen_stems:
            duplicates.append({"id": qid, "stem_preview": str(question.get("question_text") or "")[:120]})
            continue
        seen_ids.add(qid)
        seen_stems.add(stem_key)
        clean_questions.append(question)

    payload = {
        "source": "GPT\u51fa\u98986\u4efd",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "questions": clean_questions,
    }
    summary = {
        "output_file": OUTPUT_FILENAME,
        "source_root": str(gpt_root),
        "source_counts": source_counts,
        "total_before_dedupe": len(questions),
        "total_after_dedupe": len(clean_questions),
        "removed_duplicates": duplicates,
        "star_counts": star_counts,
        "verified": sum(1 for q in clean_questions if (q.get("answer_spec") or {}).get("status") == "verified"),
        "needs_review": sum(1 for q in clean_questions if (q.get("answer_spec") or {}).get("status") != "verified"),
    }
    return payload, summary


def main() -> None:
    payload, summary = build_payload()
    output_dir = ROOT / "\u505a\u9898\u9879\u76ee"
    output_dir.mkdir(exist_ok=True)
    (output_dir / OUTPUT_FILENAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_dir = ROOT / "docs" / "generated"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / SUMMARY_FILENAME).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    main()
