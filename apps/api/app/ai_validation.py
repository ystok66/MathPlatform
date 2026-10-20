from __future__ import annotations

import json
import re
import ast
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


class CandidateAnswerSpec(BaseModel):
    canonical: list[str] = Field(min_length=1)
    accepted_forms: list[str] = Field(default_factory=list)
    tolerance: float | int | None = None
    unit: str | None = None
    order_sensitive: bool = True


class CandidateSolution(BaseModel):
    solution_hook: str = ""
    steps: list[dict[str, Any]] = Field(min_length=1)
    final_answer: str
    alternative_methods: list[Any] = Field(default_factory=list)
    pitfalls: list[str] = Field(default_factory=list)

    @field_validator("steps")
    @classmethod
    def require_visible_student_steps(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not value:
            raise ValueError("solution.steps must contain visible student-facing steps")
        for index, step in enumerate(value, start=1):
            explanation = str(step.get("explanation") or step.get("text") or step.get("content") or "").strip()
            if len(explanation) < 4:
                raise ValueError(f"solution.steps[{index}] is missing a clear explanation")
        return value


class CandidateQuestion(BaseModel):
    stem_md: str = Field(min_length=1)
    answer_type: Literal["integer", "rational", "decimal", "expression", "multi_blank", "choice", "set", "open_response"]
    answer_spec: CandidateAnswerSpec
    solution: CandidateSolution
    taxonomy: dict[str, Any] = Field(default_factory=dict)
    difficulty: dict[str, Any] = Field(default_factory=dict)
    premium: dict[str, Any] = Field(default_factory=dict)

    @field_validator("stem_md")
    @classmethod
    def reject_blank_stem(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("stem_md is blank")
        return value


class AICandidatePayload(BaseModel):
    status: Literal["ok", "reject"]
    candidate_id: str = Field(min_length=1)
    reject_reason: str | None = None
    design_summary: dict[str, Any] = Field(default_factory=dict)
    question: CandidateQuestion | None = None
    verification_spec: dict[str, Any] = Field(default_factory=dict)
    self_checks: dict[str, Any] = Field(default_factory=dict)

    @field_validator("question")
    @classmethod
    def ok_requires_question(cls, value: CandidateQuestion | None, info):
        if info.data.get("status") == "ok" and value is None:
            raise ValueError("status=ok requires question")
        return value


def extract_json_payload(value: str | dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
    if value is None:
        return None, "empty_response"
    if isinstance(value, dict):
        return value, None
    text = value.strip()
    if not text:
        return None, "empty_response"
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    attempts = [text]
    if "{" in text and "}" in text:
        attempts.append(text[text.find("{") : text.rfind("}") + 1])
    for candidate in attempts:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed, None
            return None, "json_root_not_object"
        except json.JSONDecodeError:
            continue
    return None, "json_parse_failed"


def repair_json_text(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    if "{" in text and "}" in text:
        text = text[text.find("{") : text.rfind("}") + 1]
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def repair_json_payload(value: str | dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(value, str):
        return None, "repair_not_applicable"
    repaired = repair_json_text(value)
    try:
        parsed = json.loads(repaired)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(repaired)
        except Exception:
            return None, "json_repair_failed"
    if isinstance(parsed, dict):
        return parsed, None
    return None, "json_root_not_object"


def validate_candidate_payload(value: str | dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
    parsed, error = extract_json_payload(value)
    if error:
        parsed, repair_error = repair_json_payload(value)
        if repair_error:
            return None, [error, repair_error]
    try:
        candidate = AICandidatePayload.model_validate(parsed)
        if candidate.status == "reject":
            return candidate.model_dump(), []
        if candidate.question and candidate.question.solution.final_answer not in candidate.question.answer_spec.canonical:
            return None, ["final_answer_not_in_canonical"]
        return candidate.model_dump(), []
    except ValidationError as exc:
        return None, [f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()]
