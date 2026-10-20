from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import project_root
from .models import PromptTemplate, utc_now

ACTIVE_PROMPT_MAP_SCHEMA_VERSION = "active_prompts_v1"
PROMPT_METADATA_SCHEMA_VERSION = "prompt_metadata_v1"

ROLE_PROMPT_CONTRACTS = {
    "planner": {
        "input_schema_ref": "agent_input_envelope_v1#planning_context",
        "output_schema_ref": "generation_plan_v1",
        "required_output_fields": ["target_tags", "difficulty_star", "constraints"],
        "compatible_models": {
            "required_capabilities": ["json_object"],
            "recommended_reasoning_tier": "standard_or_high",
            "fallback_allowed": True,
        },
        "eval_baseline": {
            "suite": "prompt_regression",
            "minimum_status": "ready",
            "checks": ["required_phrases", "json_contract"],
        },
    },
    "generator": {
        "input_schema_ref": "agent_input_envelope_v1#generation_payload",
        "output_schema_ref": "packages/schemas/ai_candidate.schema.json",
        "required_output_fields": ["candidate_id", "question", "answer_spec", "review_notes"],
        "compatible_models": {
            "required_capabilities": ["json_object"],
            "recommended_reasoning_tier": "high",
            "fallback_allowed": False,
        },
        "eval_baseline": {
            "suite": "ai_quality_suite_v1",
            "minimum_status": "partial",
            "checks": ["math_correctness", "format_quality", "duplicate_attack"],
        },
    },
    "reviewer": {
        "input_schema_ref": "agent_input_envelope_v1#candidate_review_payload",
        "output_schema_ref": "review_decision_v1",
        "required_output_fields": ["status", "decision", "independent_answer", "issues"],
        "compatible_models": {
            "required_capabilities": ["json_object", "independent_reasoning"],
            "recommended_reasoning_tier": "high",
            "fallback_allowed": False,
        },
        "eval_baseline": {
            "suite": "ai_quality_suite_v1",
            "minimum_status": "partial",
            "checks": ["star_calibration", "math_correctness", "elementary_solution_style"],
        },
    },
    "set_reviewer": {
        "input_schema_ref": "agent_input_envelope_v1#set_review_payload",
        "output_schema_ref": "set_review_v1",
        "required_output_fields": ["status", "diversity_score", "duplicate_risks"],
        "compatible_models": {
            "required_capabilities": ["json_object"],
            "recommended_reasoning_tier": "standard_or_high",
            "fallback_allowed": True,
        },
        "eval_baseline": {"suite": "dedupe_threshold_report", "minimum_status": "ready", "checks": ["duplicate_groups"]},
    },
    "answer_judge": {
        "input_schema_ref": "agent_input_envelope_v1#answer_judge_payload",
        "output_schema_ref": "answer_judge_v1",
        "required_output_fields": ["verdict", "is_correct", "confidence", "student_feedback"],
        "compatible_models": {
            "required_capabilities": ["json_object", "safe_student_feedback"],
            "recommended_reasoning_tier": "high",
            "fallback_allowed": False,
        },
        "eval_baseline": {"suite": "ai_quality_suite_v1", "minimum_status": "partial", "checks": ["answer_judge"]},
    },
    "solution_explainer": {
        "input_schema_ref": "agent_input_envelope_v1#solution_explainer_payload",
        "output_schema_ref": "solution_explanation_v1",
        "required_output_fields": ["status", "explanation", "key_points", "next_review_suggestion"],
        "compatible_models": {
            "required_capabilities": ["json_object", "safe_student_feedback"],
            "recommended_reasoning_tier": "standard_or_high",
            "fallback_allowed": True,
        },
        "eval_baseline": {"suite": "prompt_regression", "minimum_status": "ready", "checks": ["leakage_guard"]},
    },
    "hint_generator": {
        "input_schema_ref": "agent_input_envelope_v1#hint_generation_payload",
        "output_schema_ref": "hint_question_v1",
        "required_output_fields": ["status", "hint_question", "hint_question.answer_spec", "hint_question.solution", "private_diagnosis"],
        "compatible_models": {
            "required_capabilities": ["json_object"],
            "recommended_reasoning_tier": "standard_or_high",
            "fallback_allowed": True,
        },
        "eval_baseline": {"suite": "ai_quality_suite_v1", "minimum_status": "partial", "checks": ["format_quality"]},
    },
    "diagnostician": {
        "input_schema_ref": "agent_input_envelope_v1#question_support_payload",
        "output_schema_ref": "question_support_v1",
        "required_output_fields": ["question_problem", "safe_response", "confidence"],
        "compatible_models": {
            "required_capabilities": ["json_object", "prompt_injection_resistance"],
            "recommended_reasoning_tier": "standard_or_high",
            "fallback_allowed": False,
        },
        "eval_baseline": {"suite": "prompt_injection_regression_report", "minimum_status": "ready", "checks": ["answer_leakage_block"]},
    },
    "fault_auditor": {
        "input_schema_ref": "agent_input_envelope_v1#fault_audit_payload",
        "output_schema_ref": "question_fault_audit_v1",
        "required_output_fields": ["status", "fault_type", "confidence"],
        "compatible_models": {
            "required_capabilities": ["json_object"],
            "recommended_reasoning_tier": "standard_or_high",
            "fallback_allowed": True,
        },
        "eval_baseline": {"suite": "ai_quality_suite_v1", "minimum_status": "partial", "checks": ["format_quality"]},
    },
    "reporter": {
        "input_schema_ref": "agent_input_envelope_v1#report_payload",
        "output_schema_ref": "reporter_summary_v1",
        "required_output_fields": ["findings", "evidence_ids", "next_steps"],
        "compatible_models": {
            "required_capabilities": ["json_object", "evidence_grounding"],
            "recommended_reasoning_tier": "standard_or_high",
            "fallback_allowed": True,
        },
        "eval_baseline": {"suite": "ai_quality_suite_v1", "minimum_status": "partial", "checks": ["report_faithfulness"]},
    },
    "misc": {
        "input_schema_ref": "agent_input_envelope_v1#generic_payload",
        "output_schema_ref": "json_object",
        "required_output_fields": [],
        "compatible_models": {
            "required_capabilities": ["json_object"],
            "recommended_reasoning_tier": "standard_or_high",
            "fallback_allowed": True,
        },
        "eval_baseline": {"suite": "prompt_regression", "minimum_status": "partial", "checks": ["required_phrases"]},
    },
}


def prompt_role_from_name(path: Path) -> str:
    name = path.stem.lower()
    role_prefixes = {
        "planner": "planner",
        "diversity_planner": "planner",
        "generator": "generator",
        "hint_question_generator": "hint_generator",
        "reviewer": "reviewer",
        "set_reviewer": "set_reviewer",
        "answer_judge": "answer_judge",
        "solution_explainer": "solution_explainer",
        "reporter": "reporter",
        "question_fault_auditor": "fault_auditor",
        "question_support": "diagnostician",
        "diagnostician": "diagnostician",
    }
    for prefix, role in role_prefixes.items():
        if name.startswith(prefix):
            return role
    return "misc"


def prompt_version_from_name(path: Path) -> str:
    return path.stem


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_release_channel(path: Path) -> str:
    stem = path.stem.lower()
    if "canary" in stem or stem.endswith("_v0"):
        return "canary"
    return "stable"


def prompt_contract_for_role(role: str) -> dict:
    return ROLE_PROMPT_CONTRACTS.get(role) or ROLE_PROMPT_CONTRACTS["misc"]


def prompt_metadata(path: Path, text: str, role: str) -> dict:
    contract = prompt_contract_for_role(role)
    release_channel = prompt_release_channel(path)
    return {
        "schema_version": PROMPT_METADATA_SCHEMA_VERSION,
        "bytes": len(text.encode("utf-8")),
        "line_count": text.count("\n") + 1,
        "input_schema_ref": contract["input_schema_ref"],
        "output_schema_ref": contract["output_schema_ref"],
        "required_output_fields": contract["required_output_fields"],
        "compatible_models": contract["compatible_models"],
        "eval_baseline": contract["eval_baseline"],
        "release": {
            "channel": release_channel,
            "canary_state": "disabled" if release_channel != "canary" else "candidate",
            "canary_percent": 0,
            "rollback_supported": True,
        },
    }


def active_prompt_map_path(root: Path | None = None) -> Path:
    base = root or project_root()
    return base / "prompts" / "active_prompts.json"


def safe_prompt_filename(value: str | None) -> str | None:
    if not value:
        return None
    name = Path(str(value)).name
    if name != value or not name.endswith(".md") or name.startswith("."):
        return None
    return name


def load_active_prompt_map(path: Path | None = None) -> dict:
    target = path or active_prompt_map_path()
    if not target.exists():
        return {"schema_version": ACTIVE_PROMPT_MAP_SCHEMA_VERSION, "prompts": {}}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {"schema_version": ACTIVE_PROMPT_MAP_SCHEMA_VERSION, "prompts": {}, "error": "active_prompt_map_unreadable"}
    prompts = payload.get("prompts") if isinstance(payload, dict) else {}
    safe_prompts = {str(role): filename for role, value in (prompts or {}).items() if (filename := safe_prompt_filename(str(value)))}
    return {"schema_version": ACTIVE_PROMPT_MAP_SCHEMA_VERSION, "prompts": safe_prompts}


def save_active_prompt_map(mapping: dict, path: Path | None = None) -> dict:
    target = path or active_prompt_map_path()
    safe_mapping = {
        str(role): filename
        for role, value in (mapping or {}).items()
        if (filename := safe_prompt_filename(str(value)))
    }
    payload = {"schema_version": ACTIVE_PROMPT_MAP_SCHEMA_VERSION, "prompts": dict(sorted(safe_mapping.items()))}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def active_prompt_filename(default_name: str, active_map: dict | None = None) -> str:
    default_filename = safe_prompt_filename(default_name) or default_name
    mapping = (active_map or load_active_prompt_map()).get("prompts") or {}
    role = prompt_role_from_name(Path(default_filename))
    selected = safe_prompt_filename(mapping.get(role))
    return selected or default_filename


def scan_prompt_files(prompt_dir: Path | None = None) -> list[dict]:
    root = prompt_dir or (project_root() / "prompts")
    if not root.exists():
        return []
    rows = []
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        role = prompt_role_from_name(path)
        try:
            display_path = str(path.relative_to(project_root()))
        except ValueError:
            display_path = str(path)
        rows.append(
            {
                "role": role,
                "version": prompt_version_from_name(path),
                "path": display_path,
                "content_hash": prompt_hash(text),
                "metadata_json": prompt_metadata(path, text, role),
            }
        )
    return rows


def sync_prompt_registry(db: Session, prompt_dir: Path | None = None, active_map_path: Path | None = None) -> list[PromptTemplate]:
    synced: list[PromptTemplate] = []
    active_map = load_active_prompt_map(active_map_path)
    active_by_role = active_map.get("prompts") or {}
    for item in scan_prompt_files(prompt_dir):
        row = db.scalar(select(PromptTemplate).where(PromptTemplate.role == item["role"], PromptTemplate.version == item["version"]))
        selected_filename = safe_prompt_filename(active_by_role.get(item["role"]))
        prompt_filename = Path(item["path"]).name
        status = "active" if selected_filename is None or selected_filename == prompt_filename else "available"
        if row is None:
            row = PromptTemplate(**item, status=status)
            db.add(row)
        else:
            row.path = item["path"]
            row.content_hash = item["content_hash"]
            row.metadata_json = item["metadata_json"]
            row.status = status
            row.updated_at = utc_now()
        synced.append(row)
    db.flush()
    return synced


def activate_prompt_version(db: Session, role: str, version: str, active_map_path: Path | None = None) -> tuple[PromptTemplate, dict]:
    row = db.scalar(select(PromptTemplate).where(PromptTemplate.role == role, PromptTemplate.version == version))
    if row is None:
        raise ValueError("prompt_template_not_found")
    filename = safe_prompt_filename(Path(row.path).name)
    if filename is None:
        raise ValueError("prompt_path_is_not_a_safe_markdown_file")
    rows = list(db.scalars(select(PromptTemplate).where(PromptTemplate.role == role)))
    for candidate in rows:
        candidate.status = "active" if candidate.id == row.id else "available"
        candidate.updated_at = utc_now()
    mapping = load_active_prompt_map(active_map_path).get("prompts") or {}
    mapping[role] = filename
    active_map = save_active_prompt_map(mapping, active_map_path)
    db.flush()
    return row, active_map
