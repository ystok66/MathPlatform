from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from apps.api.app.ai_gateway import STRATEGY_CONTRACTS, build_messages, retrieve_examples, review_candidate
from apps.api.app.ai_providers import build_reviewer_messages, estimate_cost
from apps.api.app.ai_validation import extract_json_payload, validate_candidate_payload
from apps.api.app.config import get_settings
from apps.api.app.db import get_engine
from apps.api.app.prompt_loader import (
    CANDIDATE_OUTPUT_SCHEMA,
    answer_judge_system_prompt,
    difficulty_guide_excerpt,
    generator_system_prompt,
    prompt_template,
    question_support_system_prompt,
    reviewer_system_prompt,
)
from apps.api.app.secrets import get_secret


DEFAULT_MODELS = [
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.4-mini",
    "grok-4-5",
    "deepseek-v3.2",
]
STAR_STRATEGY = {
    3: "structural_elegance",
    4: "constraint_shift",
    5: "inverse_task",
}
ROLE_ORDER = [
    "planner",
    "generator",
    "reviewer",
    "set_reviewer",
    "answer_judge",
    "hint_generator",
    "fault_auditor",
    "diagnostician",
    "reporter",
]


def utc_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def visible_char_count(value: str) -> int:
    return len(compact_text(value))


def cjk_char_count(value: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", value or ""))


def ascii_word_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z0-9_]+", value or ""))


def approx_tokens(value: str) -> int:
    text = value or ""
    # Conservative mixed Chinese/English approximation used only when the
    # provider does not return token_usage.
    return max(1, round(cjk_char_count(text) / 1.6 + ascii_word_count(text) * 1.3))


def extract_content(raw: dict[str, Any]) -> str:
    try:
        return str(((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    except Exception:
        return ""


def parse_json_any(content: str) -> tuple[Any, str | None]:
    parsed, error = extract_json_payload(content)
    if parsed is not None or error is None:
        return parsed, error
    object_texts = extract_json_object_texts(content)
    for object_text in reversed(object_texts):
        parsed, object_error = extract_json_payload(object_text)
        if parsed is not None:
            return parsed, None
        error = object_error or error
    stripped = content.strip()
    stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
    stripped = re.sub(r"```$", "", stripped).strip()
    if "[" in stripped and "]" in stripped:
        candidate = stripped[stripped.find("[") : stripped.rfind("]") + 1]
        try:
            return json.loads(candidate), None
        except json.JSONDecodeError:
            return None, "json_array_parse_failed"
    return None, error


def extract_json_object_texts(content: str) -> list[str]:
    texts: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(content or ""):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                texts.append(content[start : index + 1])
                start = None
    return texts


def validate_candidate_from_content(content: str) -> tuple[dict[str, Any] | None, list[str]]:
    candidate, errors = validate_candidate_payload(content)
    if candidate is not None:
        return candidate, errors
    collected_errors = list(errors)
    for object_text in reversed(extract_json_object_texts(content)):
        candidate, object_errors = validate_candidate_payload(object_text)
        if candidate is not None:
            return candidate, object_errors
        collected_errors.extend(object_errors)
    return None, collected_errors


def call_model(
    *,
    client: httpx.Client,
    base_url: str,
    api_key: str,
    model: str,
    role: str,
    messages: list[dict[str, str]],
    timeout_seconds: int,
    json_mode: bool = True,
    temperature: float | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    request_payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.7 if temperature is None and role == "generator" else 0.1 if temperature is None else temperature,
    }
    if json_mode:
        request_payload["response_format"] = {"type": "json_object"}

    raw: dict[str, Any] = {}
    content = ""
    error: str | None = None
    used_json_mode = json_mode
    try:
        response = client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=request_payload,
            timeout=timeout_seconds,
        )
        if response.status_code >= 400 and json_mode:
            # Some OpenAI-compatible routes reject response_format. Retry once
            # without JSON mode, then record whichever outcome is final.
            used_json_mode = False
            request_payload.pop("response_format", None)
            response = client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=request_payload,
                timeout=timeout_seconds,
            )
        response.raise_for_status()
        raw = response.json()
        content = extract_content(raw)
    except Exception as exc:
        error = str(exc)
        raw = {"request_payload": {k: v for k, v in request_payload.items() if k != "messages"}}

    latency_ms = int((time.perf_counter() - started) * 1000)
    usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
    usage = {**usage}
    if "estimated_cost_usd" not in usage:
        usage["estimated_cost_usd"] = estimate_cost(model, usage)
    reported_completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    reported_prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    reported_total_tokens = int(usage.get("total_tokens") or (reported_prompt_tokens + reported_completion_tokens))
    parsed, parse_error = parse_json_any(content) if content else (None, "empty_response")
    return {
        "model": model,
        "role": role,
        "latency_ms": latency_ms,
        "error": error or parse_error,
        "http_error": error,
        "parse_error": parse_error,
        "json_mode": used_json_mode,
        "content": content,
        "content_preview": content[:1400],
        "parsed": parsed,
        "usage": usage,
        "reported_prompt_tokens": reported_prompt_tokens,
        "reported_completion_tokens": reported_completion_tokens,
        "reported_total_tokens": reported_total_tokens,
        "approx_completion_tokens": reported_completion_tokens or approx_tokens(content),
        "output_visible_chars": visible_char_count(content),
        "output_cjk_chars": cjk_char_count(content),
        "output_ascii_words": ascii_word_count(content),
    }


def role_messages(role: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    if role == "planner":
        system = (
            (prompt_template("diversity_planner_v1.md") or "")
            + "\n\n只输出 JSON 对象，格式为 {\"plans\": [...], \"anti_duplicate_notes\": [...]}。"
        )
    elif role == "set_reviewer":
        system = (
            (prompt_template("set_reviewer_v1.md") or "")
            + "\n\n只输出 JSON 对象，不要输出 Markdown。"
        )
    elif role == "answer_judge":
        system = answer_judge_system_prompt()
    elif role == "hint_generator":
        system = (
            (prompt_template("hint_question_generator_v1.md") or "")
            + "\n\n只输出 JSON 对象，不要输出 Markdown。"
        )
    elif role == "fault_auditor":
        system = (
            (prompt_template("question_fault_auditor_v1.md") or "")
            + "\n\n只输出 JSON 对象，不要输出 Markdown。"
        )
    elif role == "diagnostician":
        system = question_support_system_prompt()
    elif role == "reporter":
        system = (
            (prompt_template("reporter_v1.md") or "")
            + "\n\n只输出 JSON 对象，格式为 {\"summary_md\":\"...\", \"findings\":[], \"risks\":[], \"recommendations\":[]}。"
        )
    else:
        system = reviewer_system_prompt()
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def question_brief(candidate: dict[str, Any] | None) -> dict[str, Any]:
    question = ((candidate or {}).get("question") or {}) if isinstance(candidate, dict) else {}
    solution = question.get("solution") or {}
    return {
        "stem_md": question.get("stem_md"),
        "answer_type": question.get("answer_type"),
        "answer_spec": question.get("answer_spec"),
        "final_answer": solution.get("final_answer"),
        "solution_hook": solution.get("solution_hook"),
        "student_steps": solution.get("student_steps") or [step.get("explanation") for step in solution.get("steps", []) if isinstance(step, dict)],
        "difficulty": question.get("difficulty"),
        "premium": question.get("premium"),
        "design_summary": (candidate or {}).get("design_summary") if isinstance(candidate, dict) else {},
    }


def build_generation_request(star: int, target_tags: list[str], constraints: str) -> dict[str, Any]:
    return {
        "target_tags": target_tags,
        "difficulty_star": star,
        "count": 1,
        "constraints": f"本次调用只生成 1 道 {star} 星题；3/4/5 星会由评测脚本分别调用。{constraints}",
        "solution_style": "elementary_preferred",
    }


def prepare_generator_messages(strategy: str, request: dict[str, Any], examples: list[Any]) -> list[dict[str, str]]:
    messages = build_messages(strategy, request, examples)
    try:
        payload = json.loads(messages[1]["content"])
    except Exception:
        return messages
    payload["output_clarification"] = {
        "single_question_only": True,
        "do_not_return_working_status": True,
        "difference_axes_location": "把与范本的至少 3 个差异轴写入 design_summary.difference_axes_from_examples；不要输出隐藏思考过程。",
        "reject_policy": "只有在题目不可解、答案不唯一、无法自动判分或确实同构时才 reject；不要因为差异轴需要内部检查而 reject。",
    }
    duplicate_policy = payload.setdefault("duplicate_policy", {})
    duplicate_policy["generation_time_requirement"] = (
        "内部完成范本差异检查，并把结果写入 design_summary.difference_axes_from_examples。"
        "不要输出 status='working'，也不要连续输出多个 JSON。"
    )
    payload["request"]["count"] = 1
    messages[1]["content"] = json.dumps(payload, ensure_ascii=False)
    return messages


def compact_generator_system_prompt() -> str:
    return (
        "你是小学高年级与小升初数学竞赛出题专家。必须生成题干完整、答案唯一或明确列举、可自动判分、"
        "适合小学高年级理解的题目。只输出一个合法 JSON 对象，不要输出 Markdown，不要输出 status='working'。\n\n"
        "星级标尺：3星=至少一个关键转化/隐含中间量/倒推/分类/整体观察；"
        "4星=两种以上思想组合、较强结构观察或多步分类；"
        "5星=不变量、极端、构造、递推、整体代换等高阶洞察。不得虚高标星。\n\n"
        "反重复要求：不得只改数字、人物、单位或故事背景；必须与范本至少在任务形式、信息呈现、"
        "关键方法、条件组织、故事领域、答案类型中三项不同，并写入 design_summary.difference_axes_from_examples。\n\n"
        "学生解答要求：优先非方程方法，步骤必须解释为什么这样做，不得只给算式。\n\n"
        f"{CANDIDATE_OUTPUT_SCHEMA}"
    )


def compact_generator_messages(strategy: str, request: dict[str, Any], examples: list[Any]) -> list[dict[str, str]]:
    example_cards = [
        {
            "id": q.id,
            "source": q.source_name,
            "difficulty_star": q.difficulty_star,
            "stem_preview": q.stem_preview[:160],
        }
        for q in examples[:3]
    ]
    payload = {
        "request": request,
        "strategy": strategy,
        "private_design_contract": STRATEGY_CONTRACTS.get(strategy, {}),
        "example_cards": example_cards,
        "output_clarification": {
            "single_question_only": True,
            "do_not_return_working_status": True,
            "difference_axes_location": "design_summary.difference_axes_from_examples",
            "reject_policy": "只有在题目不可解、答案不唯一、无法自动判分或确实同构时才 reject。",
        },
    }
    return [
        {"role": "system", "content": compact_generator_system_prompt()},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def fallback_generator_messages(
    *,
    star: int,
    target_tags: list[str],
    constraints: str,
    failed_output: str,
) -> list[dict[str, str]]:
    payload = {
        "task": "为模型横向评测生成 1 道可自动判分的小学高年级/小升初竞赛题。",
        "target_tags": target_tags,
        "difficulty_star": star,
        "hard_requirements": [
            "只输出一个 JSON 对象，不允许连续输出多个 JSON。",
            "不要输出 status='working'。",
            "除非题目确实无法保证唯一答案或自动判分，否则必须输出 status='ok'。",
            "answer_spec.canonical 必须只有一个完整标准答案字符串。",
            "solution.final_answer 必须与 canonical[0] 完全一致。",
            "difficulty.total_score 必须等于六维分数之和，star 必须按指南映射。",
        ],
        "anti_duplicate_constraints": constraints,
        "previous_failed_output_summary": failed_output[:1800],
        "output_note": "使用系统提示中的平台候选题 schema。差异轴写在 design_summary.difference_axes_from_examples。",
    }
    return [
        {"role": "system", "content": compact_generator_system_prompt()},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def generate_star(
    *,
    db: Session,
    client: httpx.Client,
    base_url: str,
    api_key: str,
    model: str,
    star: int,
    target_tags: list[str],
    constraints: str,
    timeout_seconds: int,
    generator_prompt: str,
    generator_json_mode: bool,
    use_fallback: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    request = build_generation_request(star, target_tags, constraints)
    examples = retrieve_examples(db, request)
    strategy = STAR_STRATEGY.get(star, "structural_elegance")
    messages = compact_generator_messages(strategy, request, examples) if generator_prompt == "compact" else prepare_generator_messages(strategy, request, examples)
    calls: list[dict[str, Any]] = []
    generator_call = call_model(
        client=client,
        base_url=base_url,
        api_key=api_key,
        model=model,
        role="generator",
        messages=messages,
        timeout_seconds=timeout_seconds,
        json_mode=generator_json_mode,
    )
    generator_call["star"] = star
    generator_call["strategy"] = strategy
    calls.append(generator_call)
    candidate, validation_errors = validate_candidate_from_content(generator_call["content"])

    if candidate is None and generator_call["content"]:
        repair_payload = {
            "task": "修复上一条候选题输出为平台合法 JSON；不得改题意、不得补造不存在的内容。若无法修复，返回 status=reject。",
            "validation_errors": validation_errors,
            "previous_output": generator_call["content"][:10000],
        }
        repair_messages = [
            {"role": "system", "content": messages[0]["content"]},
            {"role": "user", "content": json.dumps(repair_payload, ensure_ascii=False)},
        ]
        repair_call = call_model(
            client=client,
            base_url=base_url,
            api_key=api_key,
            model=model,
            role="generator",
            messages=repair_messages,
            timeout_seconds=timeout_seconds,
            json_mode=generator_json_mode,
        )
        repair_call["star"] = star
        repair_call["strategy"] = f"{strategy}:repair"
        calls.append(repair_call)
        candidate, validation_errors = validate_candidate_from_content(repair_call["content"])

    if use_fallback and not (isinstance(candidate, dict) and candidate.get("status") == "ok" and candidate.get("question")):
        failed_fragments = "\n\n".join(call.get("content_preview", "") for call in calls if call.get("role") == "generator")
        fallback_call = call_model(
            client=client,
            base_url=base_url,
            api_key=api_key,
            model=model,
            role="generator",
            messages=fallback_generator_messages(star=star, target_tags=target_tags, constraints=constraints, failed_output=failed_fragments),
            timeout_seconds=timeout_seconds,
            json_mode=generator_json_mode,
        )
        fallback_call["star"] = star
        fallback_call["strategy"] = f"{strategy}:fallback_compact"
        calls.append(fallback_call)
        fallback_candidate, fallback_errors = validate_candidate_from_content(fallback_call["content"])
        if isinstance(fallback_candidate, dict):
            candidate, validation_errors = fallback_candidate, fallback_errors

    local_review = review_candidate(candidate) if isinstance(candidate, dict) and candidate.get("status") == "ok" else {
        "decision": "reject",
        "valid": False,
        "issues": validation_errors or ["generator_failed"],
        "scores": {},
        "total_score": 0,
    }
    reviewer_call: dict[str, Any] | None = None
    model_review_payload: Any = None
    if isinstance(candidate, dict):
        reviewer_call = call_model(
            client=client,
            base_url=base_url,
            api_key=api_key,
            model=model,
            role="reviewer",
            messages=build_reviewer_messages(candidate),
            timeout_seconds=timeout_seconds,
        )
        reviewer_call["star"] = star
        reviewer_call["strategy"] = strategy
        calls.append(reviewer_call)
        model_review_payload = reviewer_call.get("parsed")

    result = {
        "requested_star": star,
        "strategy": strategy,
        "retrieved_example_ids": [example.id for example in examples],
        "candidate": candidate,
        "candidate_validation_errors": validation_errors,
        "question": question_brief(candidate),
        "local_review": local_review,
        "model_review": model_review_payload,
    }
    return result, calls


def run_supplemental_roles(
    *,
    client: httpx.Client,
    base_url: str,
    api_key: str,
    model: str,
    model_result: dict[str, Any],
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    questions = [item for item in model_result["stars"] if item.get("candidate")]
    candidates = [item.get("candidate") for item in questions if isinstance(item.get("candidate"), dict)]
    first = candidates[0] if candidates else None
    first_question = question_brief(first)
    first_answer = (((first_question.get("answer_spec") or {}).get("canonical") or [first_question.get("final_answer") or ""])[0])

    planner_payload = {
        "target_tags": model_result["target_tags"],
        "stars": [3, 4, 5],
        "constraints": model_result["constraints"],
        "difficulty_guide_excerpt": difficulty_guide_excerpt()[:3000],
        "same_model_for_all_roles": model,
        "output_goal": "为 3/4/5 星各一题安排互斥设计合同，避免同构题和只改数字。",
    }
    calls.append(call_model(client=client, base_url=base_url, api_key=api_key, model=model, role="planner", messages=role_messages("planner", planner_payload), timeout_seconds=timeout_seconds))

    set_payload = {
        "target_tags": model_result["target_tags"],
        "same_model_for_all_roles": model,
        "candidates": [
            {
                "requested_star": item["requested_star"],
                "question": item["question"],
                "local_review": item["local_review"],
                "model_review": item["model_review"],
            }
            for item in model_result["stars"]
        ],
    }
    calls.append(call_model(client=client, base_url=base_url, api_key=api_key, model=model, role="set_reviewer", messages=role_messages("set_reviewer", set_payload), timeout_seconds=timeout_seconds))

    if first:
        answer_payload = {
            "question": first_question,
            "answer_spec": {
                "status": "verified",
                "answer_type": first_question.get("answer_type"),
                "canonical_answer": first_answer,
                "acceptable_answers": (first_question.get("answer_spec") or {}).get("accepted_forms") or [],
                "order_sensitive": (first_question.get("answer_spec") or {}).get("order_sensitive", True),
            },
            "deterministic_result": {"is_correct": True, "reason": "synthetic_canonical_answer"},
            "student_answer": first_answer,
            "student_thought": "按标准答案提交，用于角色输出统计。",
            "strict_mode": False,
        }
        calls.append(call_model(client=client, base_url=base_url, api_key=api_key, model=model, role="answer_judge", messages=role_messages("answer_judge", answer_payload), timeout_seconds=timeout_seconds))

        hint_payload = {
            "root_question": first_question,
            "student_answer": "我不会",
            "student_thought": "没有找到关键转化。",
            "error_diagnosis": "strategy_selection_uncertain",
            "requirement": "生成一道启发题，不能泄露原题答案或关键步骤。",
        }
        calls.append(call_model(client=client, base_url=base_url, api_key=api_key, model=model, role="hint_generator", messages=role_messages("hint_generator", hint_payload), timeout_seconds=timeout_seconds))

        fault_payload = {
            "question_version": first_question,
            "answer_spec": first_question.get("answer_spec"),
            "generator_candidate": first,
            "reviewer_result": questions[0].get("model_review") if questions else None,
            "deterministic_verification": questions[0].get("local_review") if questions else None,
            "student_evidence": [{"answer": "我不会", "grading": "incorrect", "thought": "题意不确定"}],
        }
        calls.append(call_model(client=client, base_url=base_url, api_key=api_key, model=model, role="fault_auditor", messages=role_messages("fault_auditor", fault_payload), timeout_seconds=timeout_seconds))

        diagnostic_payload = {
            "question": first_question,
            "answer_spec_metadata": {"answer_type": first_question.get("answer_type"), "status": "verified"},
            "concern_type": "format_issue",
            "student_message": "请只判断题面显示、符号或条件是否有问题，不要给提示。",
            "strict_mode": False,
            "policy": "只能澄清题面格式和题意表达；禁止给答案、步骤或提示。",
        }
        calls.append(call_model(client=client, base_url=base_url, api_key=api_key, model=model, role="diagnostician", messages=role_messages("diagnostician", diagnostic_payload), timeout_seconds=timeout_seconds))

    reporter_payload = {
        "data_scope": f"模型 {model}，相同标签 {model_result['target_tags']}，3/4/5 星各一题。",
        "evidence": [
            {
                "evidence_id": f"{model}-{item['requested_star']}",
                "requested_star": item["requested_star"],
                "stem_preview": str((item.get("question") or {}).get("stem_md") or "")[:260],
                "answer_type": (item.get("question") or {}).get("answer_type"),
                "generated_star": ((item.get("question") or {}).get("difficulty") or {}).get("star"),
                "local_review_decision": (item.get("local_review") or {}).get("decision"),
                "model_review_decision": (item.get("model_review") or {}).get("decision") if isinstance(item.get("model_review"), dict) else None,
            }
            for item in model_result["stars"]
        ],
        "telemetry_note": "这是模型对本批生成结果的结构化报告，不是学生画像。",
    }
    calls.append(call_model(client=client, base_url=base_url, api_key=api_key, model=model, role="reporter", messages=role_messages("reporter", reporter_payload), timeout_seconds=timeout_seconds))
    return calls


def aggregate_calls(calls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_role: dict[str, dict[str, Any]] = {}
    for call in calls:
        role = call["role"]
        item = by_role.setdefault(
            role,
            {
                "calls": 0,
                "errors": 0,
                "latency_ms": 0,
                "output_visible_chars": 0,
                "output_cjk_chars": 0,
                "output_ascii_words": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "approx_completion_tokens": 0,
                "estimated_cost_usd": 0.0,
            },
        )
        item["calls"] += 1
        item["errors"] += 1 if call.get("error") else 0
        item["latency_ms"] += int(call.get("latency_ms") or 0)
        item["output_visible_chars"] += int(call.get("output_visible_chars") or 0)
        item["output_cjk_chars"] += int(call.get("output_cjk_chars") or 0)
        item["output_ascii_words"] += int(call.get("output_ascii_words") or 0)
        item["prompt_tokens"] += int(call.get("reported_prompt_tokens") or 0)
        item["completion_tokens"] += int(call.get("reported_completion_tokens") or 0)
        item["total_tokens"] += int(call.get("reported_total_tokens") or 0)
        item["approx_completion_tokens"] += int(call.get("approx_completion_tokens") or 0)
        item["estimated_cost_usd"] = round(item["estimated_cost_usd"] + float((call.get("usage") or {}).get("estimated_cost_usd") or 0), 6)
    return by_role


def render_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(cell).replace("\n", "<br>") for cell in row) + " |")
    return "\n".join(out)


def render_report(results: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# AI 模型出题与角色输出统计报告")
    lines.append("")
    lines.append(f"- 生成时间：{results['created_at']}")
    lines.append(f"- 固定标签：{', '.join(results['target_tags'])}")
    lines.append("- 星级任务：每个模型分别生成 3 星、4 星、5 星各 1 题")
    lines.append(f"- 生成提示模式：`{results.get('generator_prompt', 'compact')}`")
    lines.append(f"- 生成 JSON mode：`{results.get('generator_json_mode', 'on')}`；fallback：`{results.get('use_fallback', True)}`")
    lines.append("- 数据写入：本报告为离线评测，不发布进题库，不改变练习抽题池")
    lines.append("- token 说明：优先使用接口返回的 `usage`；如果某次调用未返回 completion token，则报告中保留近似 token 字段。")
    lines.append("")
    lines.append("## 模型总览")
    overview_rows = []
    for model, item in results["models"].items():
        role_stats = item["role_stats"]
        total_calls = sum(stat["calls"] for stat in role_stats.values())
        errors = sum(stat["errors"] for stat in role_stats.values())
        tokens = sum(stat["total_tokens"] for stat in role_stats.values())
        chars = sum(stat["output_visible_chars"] for stat in role_stats.values())
        accepted = sum(1 for star_item in item["stars"] if (star_item.get("candidate") or {}).get("status") == "ok")
        overview_rows.append([model, accepted, total_calls, errors, tokens, chars, round(sum(stat["latency_ms"] for stat in role_stats.values()) / 1000, 1)])
    lines.append(render_table(["模型", "有效候选题数", "角色调用数", "错误/解析失败", "接口 total_tokens", "输出可见字数", "总耗时秒"], overview_rows))
    lines.append("")

    lines.append("## 按模型题目")
    for model, item in results["models"].items():
        lines.append(f"### {model}")
        for star_item in item["stars"]:
            q = star_item.get("question") or {}
            difficulty = q.get("difficulty") or {}
            premium = q.get("premium") or {}
            lines.append(f"#### {star_item['requested_star']} 星")
            if q.get("stem_md"):
                lines.append("")
                lines.append(q["stem_md"])
                lines.append("")
                lines.append(f"- 标准答案：`{q.get('final_answer') or ((q.get('answer_spec') or {}).get('canonical') or [''])[0]}`")
                lines.append(f"- 答案类型：`{q.get('answer_type')}`")
                lines.append(f"- 模型自评星级：`{difficulty.get('star')}`，总分：`{difficulty.get('total_score')}`")
                lines.append(f"- 精品自评：`{premium.get('is_premium')}`，精品分：`{premium.get('total_score')}`")
                lines.append(f"- 本地审核：`{(star_item.get('local_review') or {}).get('decision')}`")
                model_review = star_item.get("model_review")
                if isinstance(model_review, dict):
                    lines.append(f"- 模型审核：`{model_review.get('decision')}`，独立答案：`{model_review.get('independent_answer')}`")
                if q.get("solution_hook"):
                    lines.append(f"- 解题入口：{q.get('solution_hook')}")
            else:
                lines.append("")
                lines.append("- 未获得合法候选题。")
                lines.append(f"- 校验错误：`{star_item.get('candidate_validation_errors')}`")
            lines.append("")

        lines.append("#### 角色输出统计")
        role_rows = []
        for role in ROLE_ORDER:
            stat = item["role_stats"].get(role, {})
            role_rows.append(
                [
                    role,
                    stat.get("calls", 0),
                    stat.get("errors", 0),
                    stat.get("output_visible_chars", 0),
                    stat.get("output_cjk_chars", 0),
                    stat.get("completion_tokens", 0),
                    stat.get("prompt_tokens", 0),
                    stat.get("total_tokens", 0),
                    stat.get("approx_completion_tokens", 0),
                    stat.get("estimated_cost_usd", 0.0),
                    round((stat.get("latency_ms", 0) or 0) / 1000, 1),
                ]
            )
        lines.append(render_table(["角色", "调用", "错误", "输出可见字数", "中文字符", "completion_tokens", "prompt_tokens", "total_tokens", "近似输出token", "估算USD", "耗时秒"], role_rows))
        lines.append("")

    lines.append("## 全模型角色汇总")
    total_by_role: dict[str, dict[str, Any]] = defaultdict(lambda: defaultdict(int))
    for item in results["models"].values():
        for role, stat in item["role_stats"].items():
            for key, value in stat.items():
                total_by_role[role][key] += value
    total_rows = []
    for role in ROLE_ORDER:
        stat = total_by_role.get(role, {})
        total_rows.append(
            [
                role,
                stat.get("calls", 0),
                stat.get("errors", 0),
                stat.get("output_visible_chars", 0),
                stat.get("completion_tokens", 0),
                stat.get("prompt_tokens", 0),
                stat.get("total_tokens", 0),
                stat.get("approx_completion_tokens", 0),
                round(float(stat.get("estimated_cost_usd", 0.0)), 6),
                round((stat.get("latency_ms", 0) or 0) / 1000, 1),
            ]
        )
    lines.append(render_table(["角色", "调用", "错误", "输出可见字数", "completion_tokens", "prompt_tokens", "total_tokens", "近似输出token", "估算USD", "耗时秒"], total_rows))
    lines.append("")

    errors = []
    for model, item in results["models"].items():
        for call in item["calls"]:
            if call.get("error"):
                errors.append([model, call.get("role"), call.get("star", ""), call.get("strategy", ""), str(call.get("error"))[:220]])
    lines.append("## 错误与解析问题")
    if errors:
        lines.append(render_table(["模型", "角色", "星级", "策略", "问题"], errors))
    else:
        lines.append("本轮未记录 HTTP 错误或 JSON 解析错误。")
    lines.append("")
    lines.append("## 原始结果")
    lines.append(f"- JSON：`{results['json_path']}`")
    return "\n".join(lines) + "\n"


def run_model(
    model: str,
    *,
    base_url: str,
    api_key: str,
    target_tags: list[str],
    constraints: str,
    timeout_seconds: int,
    generator_prompt: str,
    generator_json_mode: bool,
    use_fallback: bool,
) -> dict[str, Any]:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] start model={model}", flush=True)
    calls: list[dict[str, Any]] = []
    stars: list[dict[str, Any]] = []
    with Session(get_engine()) as db, httpx.Client(trust_env=False) as client:
        for star in [3, 4, 5]:
            print(f"[{datetime.now().isoformat(timespec='seconds')}] model={model} star={star} generator/reviewer", flush=True)
            star_result, star_calls = generate_star(
                db=db,
                client=client,
                base_url=base_url,
                api_key=api_key,
                model=model,
                star=star,
                target_tags=target_tags,
                constraints=constraints,
                timeout_seconds=timeout_seconds,
                generator_prompt=generator_prompt,
                generator_json_mode=generator_json_mode,
                use_fallback=use_fallback,
            )
            stars.append(star_result)
            calls.extend(star_calls)
        model_result = {
            "model": model,
            "target_tags": target_tags,
            "constraints": constraints,
            "stars": stars,
        }
        print(f"[{datetime.now().isoformat(timespec='seconds')}] model={model} supplemental roles", flush=True)
        calls.extend(
            run_supplemental_roles(
                client=client,
                base_url=base_url,
                api_key=api_key,
                model=model,
                model_result=model_result,
                timeout_seconds=timeout_seconds,
            )
        )
    role_stats = aggregate_calls(calls)
    print(f"[{datetime.now().isoformat(timespec='seconds')}] done model={model}", flush=True)
    return {
        **model_result,
        "calls": calls,
        "role_stats": role_stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--target-tags", default="数论,组合,不变量")
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--generator-prompt", choices=["compact", "platform"], default="compact")
    parser.add_argument("--generator-json-mode", choices=["on", "off"], default="on")
    parser.add_argument("--no-fallback", action="store_true")
    parser.add_argument("--out-dir", default="docs/generated")
    parser.add_argument(
        "--constraints",
        default=(
            "生成可自动判分的小学高年级/小升初竞赛题。所有模型和星级使用同一组标签基准，但不得同构，"
            "不得只改数字、人物、单位或故事背景。3星必须有关键转化；4星必须组合两种以上思想；"
            "5星必须体现不变量、极端、构造、递推或整体代换等高阶洞察。"
        ),
    )
    args = parser.parse_args()
    target_tags = [item.strip() for item in args.target_tags.split(",") if item.strip()]
    settings = get_settings()
    with Session(get_engine()) as db:
        api_key = get_secret(db, "LIAOBOTS_API_KEY") or settings.liaobots_api_key
    if not api_key:
        print("missing LIAOBOTS_API_KEY", file=sys.stderr)
        return 2

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_timestamp()
    json_path = out_dir / f"ai_model_role_generation_{stamp}.json"
    md_path = out_dir / f"ai_model_role_generation_{stamp}.md"
    results: dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": settings.liaobots_base_url,
        "target_tags": target_tags,
        "constraints": args.constraints,
        "generator_prompt": args.generator_prompt,
        "generator_json_mode": args.generator_json_mode,
        "use_fallback": not args.no_fallback,
        "json_path": str(json_path),
        "md_path": str(md_path),
        "models": {},
    }

    workers = max(1, min(args.workers, len(args.models)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_by_model = {
            executor.submit(
                run_model,
                model,
                base_url=settings.liaobots_base_url,
                api_key=api_key,
                target_tags=target_tags,
                constraints=args.constraints,
                timeout_seconds=args.timeout_seconds,
                generator_prompt=args.generator_prompt,
                generator_json_mode=args.generator_json_mode == "on",
                use_fallback=not args.no_fallback,
            ): model
            for model in args.models
        }
        for future in as_completed(future_by_model):
            model = future_by_model[future]
            try:
                results["models"][model] = future.result()
            except Exception as exc:
                results["models"][model] = {
                    "model": model,
                    "target_tags": target_tags,
                    "constraints": args.constraints,
                    "stars": [],
                    "calls": [],
                    "role_stats": {},
                    "fatal_error": str(exc),
                }
            json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            md_path.write_text(render_report(results), encoding="utf-8")
            print(f"[{datetime.now().isoformat(timespec='seconds')}] wrote partial report {md_path}", flush=True)

    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_report(results), encoding="utf-8")
    print(json.dumps({"json_path": str(json_path), "md_path": str(md_path), "models": list(results["models"].keys())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
