from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_providers import AnthropicProvider, GeminiProvider, MockProvider, OpenAICompatibleProvider, ProviderChain, ProviderConfig, ProviderResult, ResponsesProvider, build_reviewer_messages, parse_model_price_table
from .ai_trace import child_ai_trace, ensure_job_root_trace, request_payload_with_trace, token_usage_with_trace
from .ai_validation import validate_candidate_payload
from .audit import sanitize_payload
from .candidate_quality import answer_spec_for_grading, verify_candidate_rules
from .config import get_settings
from .generation_events import persist_generation_event
from .generation_review import candidate_set_review
from .grading import grade_answer
from .importer import map_question_v2, sha256_text
from .localization import current_language, language_context
from .models import AiRunLog, AnswerSpec, GeneratedCandidate, GenerationBatch, GenerationJob, Question, QuestionTag, QuestionVersion, SourceFile, TaxonomyTag, utc_now
from .prompt_loader import generator_system_prompt
from .prompt_security import guarded_json_message, guarded_system_prompt
from .secrets import get_secret
from .semantic_similarity import similarity_report
from .statuses import (
    CANCEL_REQUESTED_BATCH_STATUSES,
    INACTIVE_PUBLISH_STATUSES,
    STATUS_ACCEPTED,
    STATUS_CANCELLED,
    STATUS_DOMAIN_GENERATION_JOB,
    STATUS_FAILED,
    STATUS_REJECTED,
    STATUS_RUNNING,
    STATUS_TIMED_OUT,
    TERMINAL_JOB_STATUSES,
    assert_status_transition,
)


PROMPT_VERSION = "p1_generation_v1"
GENERATED_SOURCE_NAME = "AI生成题"
JOB_LEASE_SECONDS = 900
STRATEGIES = ("structural_elegance", "constraint_shift", "inverse_task")
STRATEGY_POOL = (
    "structural_elegance",
    "constraint_shift",
    "inverse_task",
    "representation_shift",
    "answer_form_shift",
)
SINGLE_NUMERIC_ANSWER_TYPES = {"integer", "decimal", "number", "numeric"}
PLAIN_NUMERIC_ANSWER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")


def plain_single_numeric_text(value: Any) -> bool:
    text = str(value).strip()
    if not text:
        return False
    if any(token in text.lower() for token in ("/", ",", "，", "、", ";", "；", "e", "∞", "nan", "inf")):
        return False
    return bool(PLAIN_NUMERIC_ANSWER_RE.fullmatch(text))


def single_numeric_answer_policy_report(candidate: dict[str, Any]) -> dict[str, Any]:
    question = candidate.get("question") or {}
    answer_spec = question.get("answer_spec") or {}
    answer_type = str(question.get("answer_type") or answer_spec.get("answer_type") or "").strip().lower()
    canonical = answer_spec.get("canonical") or []
    accepted_forms = answer_spec.get("accepted_forms") or []
    issues: list[str] = []
    if answer_type not in SINGLE_NUMERIC_ANSWER_TYPES:
        issues.append("non_single_numeric_answer_type")
    if not isinstance(canonical, list) or len(canonical) != 1:
        issues.append("canonical_answer_not_single")
    elif not plain_single_numeric_text(canonical[0]):
        issues.append("canonical_answer_not_plain_integer_or_finite_decimal")
    if isinstance(accepted_forms, list):
        bad_forms = [str(value) for value in accepted_forms if not plain_single_numeric_text(value)]
        if bad_forms:
            issues.append("accepted_form_not_plain_integer_or_finite_decimal")
    return {
        "valid": not issues,
        "allowed_answer_types": sorted(SINGLE_NUMERIC_ANSWER_TYPES),
        "answer_type": answer_type,
        "canonical_count": len(canonical) if isinstance(canonical, list) else None,
        "issues": issues,
    }


STRATEGY_CONTRACTS = {
    "structural_elegance": {
        "task_form": "求值或确定唯一对象，但必须依赖结构观察",
        "representation": "文字条件 + 可选表格/列表，不使用普通两量和差模板",
        "secondary_method": "不变量、整体观察、周期或对称",
        "constraint_change": "至少引入一个非显然中间量或守恒量",
        "story_domain": "数表、棋盘、编号、操作过程或几何结构",
        "answer_type": "single_numeric_integer_or_finite_decimal",
        "forbidden_axes": ["两位数字和差", "三份总量差量抵消", "长方形半周长和差", "简单盈亏一行算式"],
    },
    "constraint_shift": {
        "task_form": "反向构造、最大最小、保证至少或条件变更后求结论",
        "representation": "分阶段条件、操作规则或限制条件集合",
        "secondary_method": "分类讨论、极端思想、抽屉、枚举压缩",
        "constraint_change": "通过改变限制条件产生新结构，不得只改数字",
        "story_domain": "涂色、分组、摸球、路线、排列或资源分配",
        "answer_type": "single_numeric_integer_or_finite_decimal",
        "forbidden_axes": ["直接套公式", "只换物品", "只换单位", "同一条件顺序改写"],
    },
    "inverse_task": {
        "task_form": "给结果反求条件、判断是否可能、补全缺失信息",
        "representation": "结果约束 + 局部信息 + 需要倒推或排除",
        "secondary_method": "倒推、同余、奇偶、整体代换或反证",
        "constraint_change": "必须改变问题方向，与范本不同问法",
        "story_domain": "算式谜、数位、日历、余数、图形计数或操作还原",
        "answer_type": "single_numeric_integer_or_finite_decimal",
        "forbidden_axes": ["把原题问法反过来但结构不变", "只替换人物数字", "普通两步应用题"],
    },
    "representation_shift": {
        "task_form": "同一核心知识换成不同信息表达，例如表格、图形关系、数轴、日历或操作记录",
        "representation": "必须显著改变题面呈现方式，不能沿用范本叙事结构",
        "secondary_method": "信息整理、分类压缩、图表转化或对应关系",
        "constraint_change": "至少加入一个需要重新组织信息的条件，而不是只换数字",
        "story_domain": "表格、路线、周期记录、图形分割或编号系统",
        "answer_type": "single_numeric_integer_or_finite_decimal",
        "forbidden_axes": ["文字应用题原样换背景", "只把数据放进表格但解法不变", "同一问法顺序调整"],
    },
    "answer_form_shift": {
        "task_form": "Keep one plain numeric final answer, but change what the number represents: value, count, length, area, minimum, maximum, or missing number.",
        "representation": "The answer must be exactly one integer or terminating decimal string; do not output a set, tuple, option letter, text answer, or slash fraction.",
        "secondary_method": "Use enumeration, elimination, construction, invariant, or constraint crossing, but collapse the final result to one number.",
        "constraint_change": "The visible task form changes, while the final answer remains a single numeric value that is directly gradable.",
        "story_domain": "cards, digit puzzles, arrangements, geometry counts, remainders, operations, or measurement contexts",
        "answer_type": "single_numeric_integer_or_finite_decimal",
        "forbidden_axes": ["仍然只求一个普通数值", "只增加一个无关问句", "答案不可直接判分"],
    },
}


MOCK_TEMPLATES = [
    {
        "stem_md": "一个两位数的十位数字比个位数字大2，两个数字之和是10。这个两位数是多少？",
        "answer": "64",
        "primary_method": "和差分析",
        "key_insight": "两个数字的和与差可以直接确定十位和个位。",
        "solution_hook": "和差问题求两个数字。",
        "student_steps": ["十位和个位数字的和是10，差是2。", "较大的数字是(10+2)÷2=6，较小的数字是(10-2)÷2=4。", "十位是6，个位是4，所以这个两位数是64。"],
        "verification": ["6+4=10", "6-4=2", "10×6+4=64"],
    },
    {
        "stem_md": "三盒彩笔共有72支，第一盒比第二盒多6支，第三盒比第二盒少6支。第二盒有多少支？",
        "answer": "24",
        "primary_method": "基准量还原",
        "key_insight": "把第二盒看作基准量，多出的6支和少的6支正好抵消。",
        "solution_hook": "用基准量把三盒数量统一起来。",
        "student_steps": ["以第二盒为基准，第一盒多6支，第三盒少6支。", "多6支和少6支抵消，所以三盒总数相当于3个第二盒。", "第二盒有72÷3=24支。"],
        "verification": ["24+6=30", "24-6=18", "30+24+18=72"],
    },
    {
        "stem_md": "一个长方形的周长是54厘米，长比宽多7厘米。这个长方形的宽是多少厘米？",
        "answer": "10",
        "primary_method": "半周长和差",
        "key_insight": "先把周长除以2，得到长与宽的和。",
        "solution_hook": "先求长宽和，再用和差关系求宽。",
        "student_steps": ["长方形周长54厘米，所以长与宽的和是54÷2=27厘米。", "长比宽多7厘米。", "宽是(27-7)÷2=10厘米。"],
        "verification": ["10+7=17", "2×(17+10)=54"],
    },
    {
        "stem_md": "一批图书平均分给8个小组后还剩5本；如果平均分给9个小组，正好少3本才能同样多。每个小组原来可分到多少本？",
        "answer": "8",
        "primary_method": "盈亏比较",
        "key_insight": "两种分法相差一个小组的份数，同时余数从多5变成少3。",
        "solution_hook": "用盈亏差求每份数量。",
        "student_steps": ["分给8组还剩5本，分给9组少3本。", "多分1组需要的本数是5+3=8本。", "所以每个小组原来可分到8本。"],
        "verification": ["8×8+5=69", "8×9-3=69"],
    },
    {
        "stem_md": "甲、乙两人一共收集了96枚邮票，甲比乙多18枚。乙收集了多少枚？",
        "answer": "39",
        "primary_method": "和差问题",
        "key_insight": "总数减去差，就得到两个乙的数量。",
        "solution_hook": "把甲多出的18枚先拿走，剩下就能平均分。",
        "student_steps": ["甲比乙多18枚，先从总数96枚中减去18枚。", "96-18=78枚，相当于两个乙的数量。", "乙收集了78÷2=39枚。"],
        "verification": ["39+18=57", "57+39=96"],
    },
    {
        "stem_md": "一个数乘4后再加12，结果是100。这个数是多少？",
        "answer": "22",
        "primary_method": "逆推还原",
        "key_insight": "按题目运算的反方向依次还原。",
        "solution_hook": "先撤去最后加上的12，再撤去乘4。",
        "student_steps": ["结果100是先乘4再加12得到的。", "先减去12：100-12=88。", "再除以4：88÷4=22。"],
        "verification": ["22×4+12=100"],
    },
    {
        "stem_md": "学校买来一些跳绳，平均分给6个班还剩4根，平均分给7个班少2根。每个班原来可分到多少根？",
        "answer": "6",
        "primary_method": "盈亏问题",
        "key_insight": "多分一个班，需要用掉原来的余数并补上不足。",
        "solution_hook": "比较两种分法差出一个班的份量。",
        "student_steps": ["分给6个班还剩4根，分给7个班少2根。", "多分1个班需要4+2=6根。", "所以每个班原来可分到6根。"],
        "verification": ["6×6+4=40", "6×7-2=40"],
    },
    {
        "stem_md": "一个三角形三条边的周长是45厘米，第一条边比第二条边多5厘米，第三条边比第二条边多7厘米。第二条边长多少厘米？",
        "answer": "11",
        "primary_method": "基准量还原",
        "key_insight": "把第二条边设为基准，多出的部分从总周长中扣掉。",
        "solution_hook": "先去掉比基准多出的长度，再平均分成三份。",
        "student_steps": ["第一条比第二条多5厘米，第三条比第二条多7厘米。", "45-5-7=33厘米，相当于3条第二条边。", "第二条边长33÷3=11厘米。"],
        "verification": ["11+5=16", "11+7=18", "16+11+18=45"],
    },
    {
        "stem_md": "一桶水连桶重34千克，倒出一半水后连桶重19千克。空桶重多少千克？",
        "answer": "4",
        "primary_method": "差量对应",
        "key_insight": "两次总重量的差正好是一半水的重量。",
        "solution_hook": "先求半桶水重，再求整桶水重。",
        "student_steps": ["34千克和19千克相差15千克，这是一半水的重量。", "整桶水重15×2=30千克。", "空桶重34-30=4千克。"],
        "verification": ["30÷2=15", "4+15=19", "4+30=34"],
    },
    {
        "stem_md": "小明读一本书，第一天读了全书的一半少6页，第二天又读了30页，正好读完全书。这本书有多少页？",
        "answer": "48",
        "primary_method": "半量还原",
        "key_insight": "第二天读的30页对应全书的一半再多6页。",
        "solution_hook": "把第一天少读的6页补回到第二天部分里。",
        "student_steps": ["第一天读了全书的一半少6页。", "剩下的是全书的一半多6页，也就是30页。", "全书的一半是30-6=24页，所以全书有24×2=48页。"],
        "verification": ["48÷2-6=18", "18+30=48"],
    },
]


def hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _target_tags(request: dict[str, Any]) -> set[str]:
    return {str(tag).lower() for tag in (request.get("target_tags") or [])}


def _bounded_int(request: dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(request.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def source_example_count(request: dict[str, Any]) -> int:
    return _bounded_int(request, "source_example_count", 5, 0, 8)


def ai_negative_example_count(request: dict[str, Any]) -> int:
    return _bounded_int(request, "ai_negative_example_count", 3, 0, 5)


def candidate_count(request: dict[str, Any]) -> int:
    return _bounded_int(request, "candidate_count", 3, 1, 5)


def generation_strategies(request: dict[str, Any]) -> tuple[str, ...]:
    return STRATEGY_POOL[:candidate_count(request)]


def _tags_by_question(db: Session, questions: list[Question]) -> dict[str, set[str]]:
    if not questions:
        return {}
    tag_rows = db.execute(
        select(QuestionTag.question_id, QuestionTag.tag_id, TaxonomyTag.label)
        .join(TaxonomyTag, TaxonomyTag.id == QuestionTag.tag_id)
        .where(QuestionTag.question_id.in_([question.id for question in questions]))
    ).all()
    tags_by_question: dict[str, set[str]] = {}
    for question_id, tag_id, label in tag_rows:
        tags_by_question.setdefault(question_id, set()).update({str(tag_id).lower(), str(label).lower()})
    return tags_by_question


def retrieve_examples(db: Session, request: dict[str, Any]) -> list[Question]:
    limit = source_example_count(request)
    if limit <= 0:
        return []
    target_star = None
    try:
        parsed_star = int(request.get("difficulty_star") or 0)
        if 1 <= parsed_star <= 5:
            target_star = parsed_star
    except (TypeError, ValueError):
        target_star = None
    allowed_stars = {target_star} if target_star else set()
    if target_star:
        if target_star > 1:
            allowed_stars.add(target_star - 1)
        if target_star < 5:
            allowed_stars.add(target_star + 1)
    stmt = (
        select(Question, AnswerSpec.status)
        .join(AnswerSpec, AnswerSpec.question_id == Question.id)
        .where(
            Question.status.in_(("verified", "needs_review")),
            AnswerSpec.status.in_(("verified", "needs_review")),
            Question.source_name != GENERATED_SOURCE_NAME,
            Question.eligible_for_practice.is_(True),
            Question.question_fault_status == "none",
            Question.publish_status.not_in(INACTIVE_PUBLISH_STATUSES),
        )
        .order_by(Question.id)
        .limit(200)
    )
    if allowed_stars:
        stmt = stmt.where(Question.difficulty_star.in_(sorted(allowed_stars)))
    rows = db.execute(stmt).all()
    answer_status_by_question = {question.id: answer_status for question, answer_status in rows}
    pool = [question for question, _answer_status in rows]
    target_tags = _target_tags(request)
    tags_by_question = _tags_by_question(db, pool)

    selected: list[Question] = []
    seen_sources: dict[str, int] = {}
    seen_families: set[str] = set()
    needs_review_selected = 0
    def is_needs_review(question: Question) -> bool:
        return question.status == "needs_review" or answer_status_by_question.get(question.id) == "needs_review"

    def pick_from(candidates: list[Question]) -> Question | None:
        nonlocal needs_review_selected
        candidates = [question for question in candidates if question in pool and not (is_needs_review(question) and needs_review_selected >= 1)]
        if not candidates:
            return None
        candidates.sort(key=score)
        choice = candidates[0]
        pool.remove(choice)
        selected.append(choice)
        if is_needs_review(choice):
            needs_review_selected += 1
        seen_sources[choice.source_name] = seen_sources.get(choice.source_name, 0) + 1
        if choice.family_id:
            seen_families.add(choice.family_id)
        return choice

    def score(question: Question) -> tuple[int, int, int, int, int, str]:
        tags = tags_by_question.get(question.id, set())
        tag_bonus = -len(target_tags & tags)
        review_penalty = 20 if is_needs_review(question) else 0
        family_penalty = 1000 if question.family_id and question.family_id in seen_families else 0
        source_penalty = seen_sources.get(question.source_name, 0)
        star_gap = abs(int(question.difficulty_star or target_star or 3) - int(target_star or question.difficulty_star or 3))
        return (tag_bonus, review_penalty, family_penalty, source_penalty, star_gap, question.id)

    if target_star:
        side_quota = int(round(limit * 0.2)) if limit >= 3 else 0
        low_quota = side_quota if target_star > 1 else 0
        high_quota = side_quota if target_star < 5 else 0
        same_quota = max(0, limit - low_quota - high_quota)
        star_targets = ([target_star - 1] * low_quota) + ([target_star] * same_quota) + ([target_star + 1] * high_quota)
        for star in star_targets:
            if len(selected) >= limit:
                break
            pick_from([question for question in pool if question.difficulty_star == star])
    while pool and len(selected) < limit:
        if pick_from(pool) is None:
            break
    return selected


def retrieve_ai_negative_examples(db: Session, request: dict[str, Any]) -> list[Question]:
    limit = ai_negative_example_count(request)
    if limit <= 0:
        return []
    stmt = (
        select(Question)
        .join(AnswerSpec, AnswerSpec.question_id == Question.id)
        .where(
            Question.status == "verified",
            AnswerSpec.status == "verified",
            Question.source_name == GENERATED_SOURCE_NAME,
            Question.eligible_for_practice.is_(True),
            Question.question_fault_status == "none",
            Question.publish_status.not_in(INACTIVE_PUBLISH_STATUSES),
        )
        .order_by(Question.id)
        .limit(300)
    )
    if request.get("difficulty_star"):
        stmt = stmt.where(Question.difficulty_star == request["difficulty_star"])
    pool = list(db.scalars(stmt))
    target_tags = _target_tags(request)
    tags_by_question = _tags_by_question(db, pool)

    def score(question: Question) -> tuple[int, int, str]:
        tags = tags_by_question.get(question.id, set())
        tag_overlap = len(target_tags & tags)
        source_star_mismatch = 0 if not request.get("difficulty_star") or question.difficulty_star == request.get("difficulty_star") else 1
        return (-tag_overlap, source_star_mismatch, question.id)

    pool.sort(key=score)
    return pool[:limit]


def freeze_examples(examples: list[Question]) -> list[Any]:
    return [
        SimpleNamespace(
            id=question.id,
            source_name=question.source_name,
            difficulty_star=question.difficulty_star,
            premium=question.premium,
            stem_preview=question.stem_preview,
        )
        for question in examples
    ]


def mock_candidate(strategy: str, request: dict[str, Any], examples: list[Question]) -> dict[str, Any]:
    try:
        intrinsic_star = max(1, min(5, int(request.get("difficulty_star") or 3)))
    except (TypeError, ValueError):
        intrinsic_star = 3
    star = intrinsic_star
    tag = (request.get("target_tags") or ["数论"])[0]
    solution_style = request.get("solution_style") or "elementary_preferred"
    template_index = int(request.get("batch_item_index") or 0) + STRATEGY_POOL.index(strategy)
    template = MOCK_TEMPLATES[template_index % len(MOCK_TEMPLATES)]
    difficulty_profiles = {
        1: {"knowledge_span": 1, "modeling": 0, "insight": 0, "reasoning_chain": 1, "case_analysis": 0, "calculation_load": 0},
        2: {"knowledge_span": 1, "modeling": 1, "insight": 1, "reasoning_chain": 1, "case_analysis": 0, "calculation_load": 0},
        3: {"knowledge_span": 1, "modeling": 1, "insight": 1, "reasoning_chain": 2, "case_analysis": 1, "calculation_load": 0},
        4: {"knowledge_span": 2, "modeling": 2, "insight": 2, "reasoning_chain": 2, "case_analysis": 1, "calculation_load": 0},
        5: {"knowledge_span": 2, "modeling": 2, "insight": 2, "reasoning_chain": 3, "case_analysis": 2, "calculation_load": 1},
    }
    difficulty_profile = difficulty_profiles[intrinsic_star]
    difficulty_total = sum(difficulty_profile.values())
    return {
        "status": "ok",
        "candidate_id": str(uuid4()),
        "reject_reason": None,
        "design_summary": {
            "generation_goal": f"生成一道{star}星小升初数学题",
            "tested_points": [tag],
            "chosen_strategy": strategy,
            "numeric_design": "使用小整数保证答案稳定",
            "fairness_notes": "不使用初中代数结论",
            "difference_from_examples": f"参考{len(examples)}个范本，只保留方法方向",
        },
        "question": {
            "stem_md": template["stem_md"],
            "answer_type": "integer",
            "answer_spec": {
                "canonical": [template["answer"]],
                "accepted_forms": [template["answer"]],
                "tolerance": None,
                "unit": None,
                "order_sensitive": True,
            },
            "solution": {
                "primary_method": template["primary_method"],
                "solution_style_used": "elementary_non_equation",
                "natural_solution_available": True,
                "equation_necessity": "not_needed",
                "equation_reason": None,
                "key_insight": template["key_insight"],
                "solution_hook": template["solution_hook"],
                "steps": [{"explanation": step} for step in template["student_steps"]],
                "student_steps": template["student_steps"],
                "final_answer": template["answer"],
                "alternative_methods": [],
                "alternative_solution": None,
                "verification_solution": {"method": "arithmetic_check", "steps": template["verification"]},
                "requested_solution_style": solution_style,
                "pitfalls": ["把十位和个位写反"],
            },
            "taxonomy": {
                "primary_tag_id": "generated:number:mock",
                "secondary_tag_ids": [],
                "method_tag_ids": [],
                "task_tag_ids": [],
                "structure_tag_ids": [],
                "constraint_tag_ids": [],
                "constraint_notes": [],
            },
            "difficulty": {
                **difficulty_profile,
                "total_score": difficulty_total,
                "star": intrinsic_star,
                "reason": "Mock 流水线用于流程验证；难度分与请求星级保持一致，便于端到端测试真实审核流程。",
            },
            "premium": {
                "innovation": 0,
                "elegance": 1,
                "integration": 0,
                "transfer_value": 1,
                "total_score": 2,
                "is_premium": False,
                "reasons": [],
            },
        },
        "verification_spec": {
            "engine": "arithmetic",
            "variables": {},
            "constraints": template["verification"],
            "target": "final_answer",
            "expected": template["answer"],
        },
        "self_checks": {
            "complete": True,
            "solvable": True,
            "unique_answer": True,
            "answer_recomputed": True,
            "within_grade": True,
            "not_number_only_variant": True,
            "source_similarity_risk": "low",
        },
    }


def _question_cards(questions: list[Question], limit: int, stem_chars: int) -> list[dict[str, Any]]:
    return [
        {
            "id": q.id,
            "source": q.source_name,
            "difficulty_star": q.difficulty_star,
            "premium": q.premium,
            "stem_preview": q.stem_preview[:stem_chars],
        }
        for q in questions[:limit]
    ]


def build_messages(strategy: str, request: dict[str, Any], examples: list[Question], ai_negative_examples: list[Question] | None = None) -> list[dict[str, str]]:
    ai_negative_examples = ai_negative_examples or []
    source_limit = source_example_count(request)
    ai_negative_limit = ai_negative_example_count(request)
    selected_strategies = generation_strategies(request)
    source_example_star_distribution: dict[str, int] = {}
    for example in examples:
        key = str(getattr(example, "difficulty_star", None) or "unknown")
        source_example_star_distribution[key] = source_example_star_distribution.get(key, 0) + 1
    source_example_cards = _question_cards(examples, source_limit, 360)
    ai_negative_example_cards = _question_cards(ai_negative_examples, ai_negative_limit, 300)
    payload = {
        "request": request,
        "strategy": strategy,
        "batch_blueprint": request.get("batch_blueprint"),
        "batch_blueprint_slot": request.get("batch_blueprint_slot"),
        "batch_slot_contract": {
            "slot_id": request.get("batch_blueprint_slot_id"),
            "must_follow_slot_contract": bool(request.get("batch_blueprint_slot")),
            "slot_contract": (request.get("batch_blueprint_slot") or {}).get("slot_contract") if isinstance(request.get("batch_blueprint_slot"), dict) else None,
            "set_level_goal": "这是套题的一部分：候选必须服务于 slot 方法覆盖、单一数值答案、故事域变化和同套去重目标。",
        },
        "answer_policy": {
            "required": "single_numeric_final_answer",
            "allowed_answer_types": sorted(SINGLE_NUMERIC_ANSWER_TYPES),
            "canonical_rule": "canonical must contain exactly one plain integer or terminating decimal string.",
            "forbidden_answer_forms": ["set", "tuple", "ordered_tuple", "choice", "text", "slash_fraction", "multiple_canonical_answers", "scientific_notation"],
            "student_input_reason": "The learner should be able to type one final number directly.",
        },
        "reasoning_contract": {
            "private_work_required": True,
            "steps_before_final_json": [
                "design at least two non-numeric difference axes before writing the problem",
                "solve the candidate independently",
                "verify answer_spec canonical[0] matches solution.final_answer",
                "check difficulty score and star mapping",
                "check duplicate and number-only-variant risks",
            ],
            "final_output_only": "Return only one JSON object. Do not include hidden chain-of-thought. Put concise conclusions in design_summary and verification_spec.",
            "parser_behavior": "The platform extracts the JSON object and then runs deterministic validation; malformed or unsafe output is rejected.",
        },
        "candidate_strategy_count": len(selected_strategies),
        "candidate_strategies": list(selected_strategies),
        "private_design_contract": STRATEGY_CONTRACTS.get(strategy, {}),
        "example_selection_policy": {
            "same_star_first": bool(request.get("difficulty_star")),
            "target_tag_match_first": bool(request.get("target_tags")),
            "max_source_examples": source_limit,
            "actual_source_examples": len(source_example_cards),
            "source_examples_exclude_ai": True,
            "source_example_star_distribution": source_example_star_distribution,
            "source_example_star_policy": "when a target star is set, use about 20% one-star-lower, 60% same-star, 20% one-star-higher; edge stars fold missing side into same-star",
            "max_ai_negative_examples": ai_negative_limit,
            "actual_ai_negative_examples": len(ai_negative_example_cards),
            "ai_negative_examples_same_star": bool(request.get("difficulty_star")),
            "examples_are_style_references_not_templates": True,
        },
        "duplicate_policy": {
            "reject_number_only_variant": True,
            "reject_same_condition_structure": True,
            "cross_history_duplicate_guard": "hash + semantic similarity check before publish",
            "negative_example_policy": "do_not_clone_or_number-shift",
            "generation_time_requirement": "生成前必须先写出与每个 source_example_card 和 ai_negative_example_card 至少 3 个差异轴；若做不到，返回 status='reject'",
            "required_difference_axes": ["task_form", "representation", "secondary_method", "constraint_pattern", "story_domain", "numeric_context"],
            "ai_negative_examples_are_existing_ai_questions": True,
            "reject_if_similar_to_ai_negative_examples": True,
        },
        "regeneration_retry": {
            "mode": request.get("regeneration_mode"),
            "retry_of": request.get("retry_of"),
            "attempt": request.get("retry_attempt"),
            "max_attempts": request.get("max_retry_attempts"),
            "reason_codes": request.get("regeneration_reason_codes") or [],
            "constraints": request.get("regeneration_constraints") or [],
            "must_change_design_axes": 2 if request.get("regeneration_mode") == "targeted_retry" else 0,
        },
        "negative_examples": [
            {
                "reason": "do_not_clone_or_number-shift",
                "origin": "source_example",
                "id": q.id,
                "stem_preview": q.stem_preview[:240],
            }
            for q in examples
        ] + [
            {
                "reason": "existing_ai_question_do_not_duplicate",
                "origin": "ai_negative_example",
                "id": q.id,
                "stem_preview": q.stem_preview[:240],
            }
            for q in ai_negative_examples
        ],
        "example_cards": source_example_cards,
        "source_example_cards": source_example_cards,
        "ai_negative_example_cards": ai_negative_example_cards,
        "avoid_similarity_rules": {
            "source_examples": "只学习质量、风格和难度，不复制条件结构。",
            "ai_negative_examples": "这些是同星级相近标签的既有 AI 题，候选不得成为它们的改数、改物品、改问法版本。",
            "targeted_retry": "若 regeneration_retry.mode=targeted_retry，必须逐条满足 constraints，且至少改变 2 个设计轴。",
        },
    }
    return [
        {
            "role": "system",
            "content": guarded_system_prompt(generator_system_prompt()),
        },
        {
            "role": "user",
            "content": guarded_json_message(
                "generator",
                payload,
                untrusted_fields=[
                    "payload.request.custom_description",
                    "payload.request.target_tags",
                    "payload.source_example_cards",
                    "payload.ai_negative_example_cards",
                    "payload.negative_examples",
                ],
            ),
        },
    ]


def reviewer_difficulty_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    blocks = [
        payload.get("difficulty"),
        payload.get("difficulty_independent"),
        payload.get("difficulty_review"),
        payload,
    ]
    for block in blocks:
        if not isinstance(block, dict):
            continue
        raw_star = block.get("difficulty_star", block.get("star"))
        try:
            star = int(raw_star)
        except (TypeError, ValueError):
            continue
        if 1 <= star <= 5:
            return {
                "star": star,
                "dimension_scores": block.get("dimension_scores") or payload.get("dimension_scores") or {},
                "total_score": block.get("total_score", payload.get("total_score")),
                "reason": block.get("difficulty_reason") or block.get("reason") or payload.get("difficulty_reason"),
            }
    return {}


def reviewer_premium_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    blocks = [payload.get("premium_review"), payload.get("premium_independent"), payload.get("premium"), payload]
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if "is_premium" in block or "premium" in block:
            value = block.get("is_premium", block.get("premium"))
            return {
                "is_premium": bool(value),
                "total_score": block.get("total_score", payload.get("premium_score")),
                "reasons": block.get("premium_reasons") or block.get("reasons") or payload.get("premium_reasons") or [],
            }
    if "premium" in payload:
        return {"is_premium": bool(payload.get("premium")), "total_score": payload.get("premium_score"), "reasons": payload.get("premium_reasons") or []}
    return {}


def apply_reviewer_calibration(candidate: dict[str, Any], review: dict[str, Any]) -> None:
    question = candidate.get("question") or {}
    difficulty = question.setdefault("difficulty", {})
    premium = question.setdefault("premium", {})
    model_payload = ((review.get("model_review") or {}).get("payload") or {}) if isinstance(review, dict) else {}

    reviewer_difficulty = reviewer_difficulty_from_payload(model_payload)
    if reviewer_difficulty:
        difficulty["star"] = reviewer_difficulty["star"]
        if reviewer_difficulty.get("dimension_scores"):
            for key, value in reviewer_difficulty["dimension_scores"].items():
                difficulty[key] = value
            difficulty["dimension_scores"] = reviewer_difficulty["dimension_scores"]
        if reviewer_difficulty.get("total_score") is not None:
            difficulty["total_score"] = reviewer_difficulty["total_score"]
        if reviewer_difficulty.get("reason"):
            difficulty["reason"] = reviewer_difficulty["reason"]
        difficulty["reviewer_calibrated"] = True

    reviewer_premium = reviewer_premium_from_payload(model_payload)
    if reviewer_premium:
        premium["is_premium"] = reviewer_premium["is_premium"]
        if reviewer_premium.get("total_score") is not None:
            premium["total_score"] = reviewer_premium["total_score"]
        if reviewer_premium.get("reasons"):
            premium["reasons"] = reviewer_premium["reasons"]
        premium["reviewer_calibrated"] = True


def existing_generation_duplicate(db: Session, question_id: str, stem: str, qv2: dict[str, Any]) -> dict[str, Any] | None:
    dedupe = qv2.get("dedupe") or {}
    hash_values = {dedupe.get("exact_hash"), dedupe.get("normalized_hash"), dedupe.get("semantic_hash")} - {None, ""}
    stmt = select(Question).where(
        Question.id != question_id,
        Question.status == "verified",
        Question.eligible_for_practice.is_(True),
        Question.publish_status.not_in(("batch_rejected_similar", "quarantined", "retired", "duplicate_alias", "intervention_only")),
    )
    existing = list(db.scalars(stmt))
    for row in existing:
        if hash_values & {row.exact_hash, row.normalized_hash, row.semantic_hash}:
            return {"reason": "dedupe_hash_match", "question_id": row.id, "stem_preview": row.stem_preview}
    for row in existing:
        report = similarity_report(row.stem_preview, stem)
        if report.get("number_only_variant") or report.get("source_similarity_risk") == "high" or report.get("semantic_similarity", 0) >= 0.9:
            return {"reason": "near_duplicate_existing_question", "question_id": row.id, "stem_preview": row.stem_preview, "similarity": report}
    return None


def review_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    question = candidate.get("question") or {}
    rule_report = verify_candidate_rules(candidate)
    numeric_policy = single_numeric_answer_policy_report(candidate)
    solution_steps = ((question.get("solution") or {}).get("steps") or []) if isinstance(question.get("solution"), dict) else []
    visible_solution_steps = [
        step
        for step in solution_steps
        if isinstance(step, dict) and str(step.get("explanation") or step.get("text") or step.get("content") or "").strip()
    ]
    checks = {
        "complete": bool(question.get("stem_md")),
        "solvable": bool((question.get("answer_spec") or {}).get("canonical")),
        "solution_steps_present": bool(visible_solution_steps),
        "unique_answer": True,
        "answer_matches": rule_report["checks"]["final_answer_grades_correct"],
        "single_numeric_answer": numeric_policy["valid"],
        "within_grade": True,
        "tag_fit": True,
        "difficulty_fit": True,
        "not_overly_similar": candidate.get("self_checks", {}).get("source_similarity_risk") != "high",
        "rule_verification": rule_report["valid"],
    }
    valid = candidate.get("status") == "ok" and all(checks.values())
    return {
        "valid": valid,
        "hard_checks": checks,
        "rule_verification": rule_report,
        "numeric_answer_policy": numeric_policy,
        "independent_answer": ((question.get("answer_spec") or {}).get("canonical") or [""])[0],
        "solution_summary": (question.get("solution") or {}).get("solution_hook", ""),
        "issues": [] if valid else ["候选题缺少可验证题面、单一数值答案规范或学生版分步解答", *rule_report["issues"], *numeric_policy["issues"], *(["solution_steps_missing_or_too_thin"] if not visible_solution_steps else [])],
        "scores": {
            "correctness": 30 if valid else 0,
            "difficulty_independent": rule_report["difficulty"],
            "premium_independent": rule_report["premium"],
            "overall": 82 if valid else 0,
        },
        "total_score": 82 if valid else 0,
        "decision": "accept" if valid else "reject",
    }


def reviewer_answer_spec_alignment(candidate: dict[str, Any], model_payload: dict[str, Any]) -> dict[str, Any]:
    raw_answer = model_payload.get("independent_answer")
    if isinstance(raw_answer, list):
        answer_text = ",".join(str(item).strip() for item in raw_answer if str(item).strip())
    else:
        answer_text = str(raw_answer or "").strip()
    if not answer_text:
        return {"checked": False, "reason": "missing_independent_answer"}
    question = candidate.get("question") or {}
    if not isinstance(question.get("answer_spec"), dict):
        return {"checked": False, "reason": "missing_candidate_answer_spec"}
    grading_spec = answer_spec_for_grading(question)
    grading_result = grade_answer(grading_spec, answer_text)
    answer_text_for_grading = answer_text
    if grading_result.get("is_correct") is not True and str(grading_result.get("reason") or "") == "not_numeric":
        answer_type = str(grading_spec.get("answer_type") or "")
        if answer_type in {"integer", "decimal", "number", "fraction", "rational"}:
            match = re.match(r"^\s*(?:答案(?:是|为)?[:：]?\s*)?([-+]?(?:\d+(?:\.\d+)?|\d+\s*/\s*\d+))(?=$|[\s,，.。;；:：])", answer_text)
            if match:
                answer_text_for_grading = match.group(1).replace(" ", "")
                grading_result = grade_answer(grading_spec, answer_text_for_grading)
    return {
        "checked": True,
        "independent_answer": answer_text,
        "independent_answer_for_grading": answer_text_for_grading,
        "matches_answer_spec": grading_result.get("is_correct") is True,
        "grading_result": grading_result,
    }


def visible_solution_steps_from_qv2(qv2: dict[str, Any]) -> list[dict[str, Any]]:
    solution = qv2.get("solution") if isinstance(qv2, dict) else {}
    raw_steps = (solution or {}).get("steps") if isinstance(solution, dict) else []
    if not isinstance(raw_steps, list):
        return []
    return [
        step
        for step in raw_steps
        if isinstance(step, dict) and str(step.get("explanation") or step.get("text") or step.get("content") or "").strip()
    ]


def model_roles(default_model: str, settings: Any | None = None) -> dict[str, str]:
    return {
        "default": default_model,
        "planner": getattr(settings, "planner_model", None) or default_model,
        "generator": getattr(settings, "generator_model", None) or default_model,
        "reviewer": getattr(settings, "reviewer_model", None) or default_model,
        "secondary_reviewer": getattr(settings, "secondary_reviewer_model", None) or getattr(settings, "set_reviewer_model", None) or getattr(settings, "reviewer_model", None) or default_model,
        "reporter": getattr(settings, "reporter_model", None) or default_model,
        "diagnostician": getattr(settings, "diagnostician_model", None) or default_model,
        "hint_generator": getattr(settings, "hint_generator_model", None) or default_model,
        "hint_reviewer": getattr(settings, "hint_reviewer_model", None) or getattr(settings, "reviewer_model", None) or default_model,
        "fault_auditor": getattr(settings, "fault_auditor_model", None) or default_model,
        "set_reviewer": getattr(settings, "set_reviewer_model", None) or default_model,
        "answer_judge": getattr(settings, "answer_judge_model", None) or default_model,
        "solution_explainer": getattr(settings, "solution_explainer_model", None) or default_model,
        "question_repair_reviewer": getattr(settings, "question_repair_reviewer_model", None) or getattr(settings, "reviewer_model", None) or default_model,
        "emergency_recovery_agent": getattr(settings, "emergency_recovery_agent_model", None) or "gpt-5.6-sol",
        "project_assistant": getattr(settings, "project_assistant_model", None) or "gpt-5.6-sol",
    }


def generator_model_for_request(settings: Any, request: dict[str, Any], roles: dict[str, str]) -> tuple[str, str]:
    fallback = roles.get("generator") or roles.get("default") or settings.default_model
    try:
        star = int(request.get("difficulty_star") or 0)
    except (TypeError, ValueError):
        star = 0
    if star == 5:
        return (getattr(settings, "generator_model_5", None) or fallback, "star_5")
    if star == 4:
        return (getattr(settings, "generator_model_4", None) or fallback, "star_4")
    if 1 <= star <= 3:
        return (getattr(settings, "generator_model_le_3", None) or fallback, "le_3")
    return fallback, "fallback"


def provider_role_timeouts(settings: Any) -> dict[str, int]:
    interaction_timeout = int(getattr(settings, "ai_interaction_timeout_seconds", 60) or 60)
    return {
        "generator": int(getattr(settings, "ai_generator_timeout_seconds", 180) or 180),
        "reviewer": int(getattr(settings, "ai_reviewer_timeout_seconds", 120) or 120),
        "answer_judge": interaction_timeout,
        "fault_auditor": interaction_timeout,
        "hint_generator": interaction_timeout,
        "hint_reviewer": interaction_timeout,
        "reporter": interaction_timeout,
        "solution_explainer": interaction_timeout,
        "question_repair_reviewer": interaction_timeout,
        "emergency_recovery_agent": interaction_timeout,
        "project_assistant": interaction_timeout,
        "set_reviewer": int(getattr(settings, "ai_reviewer_timeout_seconds", 120) or 120),
    }


def provider_config(settings: Any, name: str, base_url: str, api_key: str | None) -> ProviderConfig:
    interaction_timeout = int(getattr(settings, "ai_interaction_timeout_seconds", 60) or 60)
    return ProviderConfig(
        name=name,
        base_url=base_url,
        api_key=api_key,
        models=model_roles(settings.default_model, settings),
        price_table=parse_model_price_table(getattr(settings, "ai_model_price_json", "")),
        timeout_seconds=interaction_timeout,
        role_timeouts=provider_role_timeouts(settings),
        max_retries=1,
    )


def provider_chain(provider: str, settings: Any, db: Session | None = None) -> ProviderChain:
    providers = []
    liaobots_api_key = (get_secret(db, "LIAOBOTS_API_KEY") if db is not None else None) or settings.liaobots_api_key
    if provider == "mock":
        return ProviderChain([MockProvider()], max_retries=1, max_concurrency=getattr(settings, "ai_global_max_concurrency", 6))
    if provider == "responses" and settings.responses_api_key:
        providers.append(ResponsesProvider(provider_config(settings, "responses", settings.responses_base_url, settings.responses_api_key)))
    elif provider == "anthropic" and settings.anthropic_api_key:
        providers.append(AnthropicProvider(provider_config(settings, "anthropic", settings.anthropic_base_url, settings.anthropic_api_key)))
    elif provider == "gemini" and settings.gemini_api_key:
        providers.append(GeminiProvider(provider_config(settings, "gemini", settings.gemini_base_url, settings.gemini_api_key)))
    elif provider != "mock" and liaobots_api_key:
        providers.append(OpenAICompatibleProvider(provider_config(settings, "openai_compatible", settings.liaobots_base_url, liaobots_api_key)))
    if not providers:
        providers.append(MockProvider())
    return ProviderChain(providers, max_retries=1, max_concurrency=getattr(settings, "ai_global_max_concurrency", 6))


def generation_quality_tier(request: dict[str, Any]) -> str:
    if candidate_count(request) >= 5 or source_example_count(request) >= 8 or ai_negative_example_count(request) >= 5:
        return "strict"
    if candidate_count(request) <= 1 and source_example_count(request) <= 3 and ai_negative_example_count(request) <= 1:
        return "fast"
    return "balanced"


def parse_strict_reviewer_allowlist(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value]
    else:
        text = str(value).strip()
        if not text:
            return set()
        parts: list[str]
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                parts = [str(item) for item in parsed] if isinstance(parsed, list) else [text]
            except json.JSONDecodeError:
                parts = [text]
        else:
            parts = []
            current = ""
            for char in text:
                if char in {",", ";", "\n", "\t", " "}:
                    if current.strip():
                        parts.append(current.strip())
                    current = ""
                else:
                    current += char
            if current.strip():
                parts.append(current.strip())
    return {part.strip().lower() for part in parts if part.strip()}


def strict_reviewer_allowlist_check(settings: Any | None, provider_names: list[str], reviewer_model: str) -> dict[str, Any]:
    entries = parse_strict_reviewer_allowlist(getattr(settings, "ai_strict_reviewer_model_allowlist", ""))
    normalized_model = reviewer_model.strip().lower()
    normalized_providers = [str(name).strip().lower() for name in provider_names if str(name).strip()]
    configured = bool(entries)
    if not configured:
        return {"configured": False, "allowed": True, "entry_count": 0}
    if "*" in entries or normalized_model in entries:
        return {"configured": True, "allowed": True, "entry_count": len(entries)}
    provider_matches = {f"{provider_name}:{normalized_model}" for provider_name in normalized_providers}
    provider_wildcards = {f"{provider_name}:*" for provider_name in normalized_providers}
    allowed = bool(entries.intersection(provider_matches) or entries.intersection(provider_wildcards))
    return {"configured": True, "allowed": allowed, "entry_count": len(entries)}


def reviewer_independence_policy(provider: str, roles: dict[str, str], request: dict[str, Any], chain: ProviderChain, settings: Any | None = None) -> dict[str, Any]:
    provider_names = [getattr(row, "name", row.__class__.__name__) for row in getattr(chain, "providers", [])]
    real_provider_active = any(name != "mock" for name in provider_names)
    generator_model = str(roles.get("generator") or roles.get("default") or "").strip()
    reviewer_model = str(roles.get("reviewer") or roles.get("default") or "").strip()
    secondary_reviewer_model = str(roles.get("secondary_reviewer") or "").strip()
    same_model = bool(generator_model and reviewer_model and generator_model == reviewer_model)
    secondary_same_as_generator = bool(generator_model and secondary_reviewer_model and generator_model == secondary_reviewer_model)
    dual_reviewer_required = False
    dual_reviewer_distinct_model = False
    tier = generation_quality_tier(request)
    downgrade_allowed = bool(request.get("allow_reviewer_quality_downgrade"))
    allowlist = strict_reviewer_allowlist_check(settings, provider_names, reviewer_model)
    allowlist_degraded = bool(real_provider_active and tier == "strict" and allowlist["configured"] and not allowlist["allowed"])
    if real_provider_active and tier == "strict":
        dual_reviewer_required = True
        dual_reviewer_distinct_model = bool(secondary_reviewer_model and reviewer_model and secondary_reviewer_model != reviewer_model and not secondary_same_as_generator)
    dual_reviewer_degraded = bool(dual_reviewer_required and not dual_reviewer_distinct_model)
    quality_degraded = bool(real_provider_active and ((same_model and tier in {"balanced", "strict"}) or allowlist_degraded or dual_reviewer_degraded))
    downgrade_confirmation_required = bool(real_provider_active and tier == "strict" and not downgrade_allowed and (same_model or allowlist_degraded or dual_reviewer_degraded))
    hard_block_publish = downgrade_confirmation_required
    warnings: list[str] = []
    if quality_degraded:
        if same_model and tier in {"balanced", "strict"}:
            warnings.append("same_generator_reviewer_model")
        if allowlist_degraded:
            warnings.append("reviewer_model_not_in_strict_allowlist")
        if dual_reviewer_degraded:
            warnings.append("secondary_reviewer_not_distinct")
            if secondary_same_as_generator:
                warnings.append("secondary_reviewer_matches_generator_model")
        if downgrade_allowed:
            warnings.append("reviewer_quality_downgrade_confirmed")
    if downgrade_confirmation_required:
        if same_model:
            warnings.append("strict_tier_requires_distinct_reviewer_model")
        if allowlist_degraded:
            warnings.append("strict_tier_requires_allowlisted_reviewer_model")
        if dual_reviewer_degraded:
            warnings.append("strict_tier_requires_distinct_secondary_reviewer")
    return {
        "schema_version": "reviewer_independence_policy_v1",
        "quality_tier": tier,
        "requested_provider": provider,
        "provider_chain": provider_names,
        "real_provider_active": real_provider_active,
        "generator_model": generator_model,
        "reviewer_model": reviewer_model,
        "secondary_reviewer_model": secondary_reviewer_model,
        "same_generator_reviewer_model": same_model,
        "secondary_reviewer_matches_generator_model": secondary_same_as_generator,
        "dual_reviewer_required": dual_reviewer_required,
        "dual_reviewer_distinct_model": dual_reviewer_distinct_model,
        "strict_reviewer_allowlist_configured": allowlist["configured"],
        "strict_reviewer_allowlist_allowed": allowlist["allowed"],
        "strict_reviewer_allowlist_entry_count": allowlist["entry_count"],
        "deterministic_gate": True,
        "required_level": "logical_only"
        if tier == "fast"
        else ("distinct_role_recommended" if tier == "balanced" else ("distinct_model_and_allowlisted_required" if allowlist["configured"] else "distinct_model_required")),
        "quality_degraded": quality_degraded,
        "downgrade_allowed_by_user": downgrade_allowed,
        "downgrade_confirmation_required": downgrade_confirmation_required,
        "hard_block_publish": hard_block_publish,
        "warnings": warnings,
    }


def run_generation_strategy(strategy: str, request: dict[str, Any], examples: list[Question], ai_negative_examples: list[Question], chain: ProviderChain, roles: dict[str, str]) -> dict[str, Any]:
    with language_context(request.get("language") or current_language()):
        return _run_generation_strategy_localized(strategy, request, examples, ai_negative_examples, chain, roles)


def _run_generation_strategy_localized(strategy: str, request: dict[str, Any], examples: list[Question], ai_negative_examples: list[Question], chain: ProviderChain, roles: dict[str, str]) -> dict[str, Any]:
    messages = build_messages(strategy, request, examples, ai_negative_examples)
    request_payload = {
        "messages": messages,
        "strategy": strategy,
        "role": "generator",
        "candidate_count": candidate_count(request),
        "source_example_count": source_example_count(request),
        "ai_negative_example_count": ai_negative_example_count(request),
        "source_example_ids": [q.id for q in examples],
        "ai_negative_example_ids": [q.id for q in ai_negative_examples],
    }
    results = chain.run_json(
        messages,
        "generator",
        roles,
        {
            "strategy": strategy,
            "request": request,
            "examples": examples,
            "ai_negative_examples": ai_negative_examples,
            "mock_factory": lambda: mock_candidate(strategy, request, examples),
        },
    )
    final = results[-1]
    return {"strategy": strategy, "request_payload": request_payload, "input_hash": hash_payload(request_payload), "results": results, "candidate": final.payload}


def reviewer_decision(candidate: dict[str, Any], chain: ProviderChain, roles: dict[str, str], reviewer_policy: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[ProviderResult], list[ProviderResult]]:
    messages = build_reviewer_messages(candidate)
    request_payload = {"messages": messages, "role": "reviewer", "candidate_id": candidate.get("candidate_id")}
    results = chain.run_json(messages, "reviewer", roles, {"candidate": candidate})
    secondary_results: list[ProviderResult] = []
    local = review_candidate(candidate)
    model_payload = results[-1].payload or {}
    if results[-1].provider != "mock" and (results[-1].error or results[-1].payload is None):
        local["decision"] = "reject"
        local["valid"] = False
        local["issues"] = list(set(local.get("issues", []) + ["model_reviewer_failed"]))
    if model_payload.get("decision") == "reject":
        local["decision"] = "reject"
        local["valid"] = False
        local["issues"] = list(set(local.get("issues", []) + model_payload.get("issues", ["reviewer_rejected"])))
    reviewer_difficulty = reviewer_difficulty_from_payload(model_payload)
    reviewer_premium = reviewer_premium_from_payload(model_payload)
    if reviewer_difficulty:
        local["scores"]["reviewer_difficulty_independent"] = reviewer_difficulty
    if reviewer_premium:
        local["scores"]["reviewer_premium_independent"] = reviewer_premium
    issues = list(local.get("issues", []))
    answer_alignment = reviewer_answer_spec_alignment(candidate, model_payload)
    local["reviewer_answer_spec_alignment"] = answer_alignment
    if answer_alignment.get("checked"):
        local["scores"]["reviewer_answer_spec_match"] = 1 if answer_alignment.get("matches_answer_spec") else 0
        if not answer_alignment.get("matches_answer_spec"):
            local["decision"] = "reject"
            local["valid"] = False
            issues.append("reviewer_answer_spec_conflict")
    dual_required = bool(reviewer_policy and reviewer_policy.get("dual_reviewer_required"))
    dual_distinct = bool(reviewer_policy and reviewer_policy.get("dual_reviewer_distinct_model"))
    if dual_required and dual_distinct and not (reviewer_policy or {}).get("hard_block_publish"):
        secondary_roles = {**roles, "reviewer": roles.get("secondary_reviewer") or roles.get("reviewer") or roles.get("default") or "mock"}
        secondary_results = chain.run_json(messages, "reviewer", secondary_roles, {"candidate": candidate})
        secondary_final = secondary_results[-1]
        secondary_payload = secondary_final.payload or {}
        secondary_alignment = reviewer_answer_spec_alignment(candidate, secondary_payload)
        primary_decision = str(model_payload.get("decision") or ("reject" if results[-1].error or results[-1].payload is None else "accept"))
        secondary_decision = str(secondary_payload.get("decision") or ("reject" if secondary_final.error or secondary_final.payload is None else "accept"))
        primary_answer = str(model_payload.get("independent_answer") or "").strip()
        secondary_answer = str(secondary_payload.get("independent_answer") or "").strip()
        decision_matches = primary_decision == secondary_decision
        answer_matches = bool(primary_answer and secondary_answer and primary_answer == secondary_answer)
        secondary_issues: list[str] = []
        if secondary_final.provider != "mock" and (secondary_final.error or secondary_final.payload is None):
            secondary_issues.append("secondary_reviewer_failed")
        if secondary_payload.get("decision") == "reject":
            secondary_issues.append("secondary_reviewer_rejected")
            secondary_issues.extend(str(item) for item in secondary_payload.get("issues", []) if item)
        if not decision_matches:
            secondary_issues.append("dual_reviewer_decision_conflict")
        if not answer_matches:
            secondary_issues.append("dual_reviewer_answer_conflict")
        if secondary_alignment.get("checked") and not secondary_alignment.get("matches_answer_spec"):
            secondary_issues.append("secondary_reviewer_answer_spec_conflict")
        if secondary_issues:
            local["decision"] = "reject"
            local["valid"] = False
            issues.extend(secondary_issues)
        local["dual_reviewer_review"] = {
            "required": True,
            "performed": True,
            "consistent": not secondary_issues,
            "primary_decision": primary_decision,
            "secondary_decision": secondary_decision,
            "primary_independent_answer": primary_answer,
            "secondary_independent_answer": secondary_answer,
            "decision_matches": decision_matches,
            "answer_matches": answer_matches,
            "secondary_answer_spec_alignment": secondary_alignment,
            "secondary_model_review": {
                "provider": secondary_final.provider,
                "model": secondary_final.model,
                "payload": secondary_payload,
                "error": secondary_final.error,
                "fallback_used": bool(len(secondary_results) > 1 or (secondary_results and secondary_results[-1].provider != secondary_results[0].provider)),
                "attempt_count": len(secondary_results),
            },
        }
    elif dual_required:
        reason = "secondary_reviewer_not_distinct" if not dual_distinct else "primary_reviewer_policy_hard_block"
        local["dual_reviewer_review"] = {"required": True, "performed": False, "consistent": False, "reason": reason}
        if not dual_distinct and "secondary_reviewer_not_distinct" not in issues:
            issues.append("secondary_reviewer_not_distinct")
    if reviewer_policy:
        if reviewer_policy.get("quality_degraded") and "reviewer_quality_degraded" not in issues:
            issues.append("reviewer_quality_degraded")
        if reviewer_policy.get("strict_reviewer_allowlist_allowed") is False and "reviewer_model_not_in_strict_allowlist" not in issues:
            issues.append("reviewer_model_not_in_strict_allowlist")
        if reviewer_policy.get("hard_block_publish"):
            local["decision"] = "reject"
            local["valid"] = False
            if "reviewer_independence_required" not in issues:
                issues.append("reviewer_independence_required")
    local["issues"] = sorted(set(issues))
    local["model_review"] = {
        "provider": results[-1].provider,
        "model": results[-1].model,
        "input_hash": hash_payload(request_payload),
        "payload": model_payload,
        "error": results[-1].error,
        "fallback_used": bool(len(results) > 1 or (results and results[-1].provider != results[0].provider)),
        "attempt_count": len(results),
    }
    if reviewer_policy:
        local["reviewer_independence_policy"] = reviewer_policy
    return local, results, secondary_results


def job_sse_payload(job: GenerationJob) -> dict[str, Any]:
    return {"id": job.id, "status": job.status, "progress": job.progress, "accepted_question_id": job.accepted_question_id}


def candidate_sse_payload(candidate: GeneratedCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "job_id": candidate.job_id,
        "strategy": candidate.strategy,
        "status": candidate.status,
        "score": candidate.score,
        "payload": candidate.payload,
        "review": candidate.review,
        "published_question_id": candidate.published_question_id,
        "publish_status": candidate.publish_status,
    }


def ai_log_sse_payload(log: AiRunLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "job_id": log.job_id,
        "role": log.role,
        "provider": log.provider,
        "model": log.model,
        "error": log.error,
        "latency_ms": log.latency_ms,
        "created_at": progress_timestamp(log.created_at),
    }


def persist_job_sse_event(db: Session, job: GenerationJob) -> None:
    progress = job.progress if isinstance(job.progress, dict) else {}
    persist_generation_event(
        db,
        "job",
        job.id,
        "job",
        job_sse_payload(job),
        group=40,
        status=job.status,
        index=progress_state_version(progress),
        occurred_at=job.updated_at or job.created_at,
    )
    if job.status in TERMINAL_JOB_STATUSES:
        persist_generation_event(
            db,
            "job",
            job.id,
            "done",
            {"job_id": job.id, "status": job.status},
            group=90,
            status=job.status,
            index=progress_state_version(progress),
            occurred_at=job.updated_at or job.created_at,
        )


def persist_candidate_sse_event(db: Session, candidate: GeneratedCandidate, index: int = 0) -> None:
    event_time = candidate.published_at or utc_now()
    persist_generation_event(
        db,
        "job",
        candidate.job_id,
        "candidate",
        candidate_sse_payload(candidate),
        group=20,
        status=candidate.status,
        index=index,
        occurred_at=event_time,
    )


def persist_ai_log_sse_event(db: Session, log: AiRunLog, index: int = 0) -> None:
    persist_generation_event(
        db,
        "job",
        log.job_id or "",
        "ai_log",
        ai_log_sse_payload(log),
        group=30,
        status="failed" if log.error else "accepted",
        index=index,
        occurred_at=log.created_at,
    )


def add_ai_logs(db: Session, job_id: str, role: str, prompt_hash: str, request_payload: dict[str, Any], results: list[ProviderResult], trace: dict[str, Any] | None = None) -> None:
    for index, result in enumerate(results):
        traced_request, run_trace = request_payload_with_trace(request_payload, result, trace or {})
        response_payload = {"raw_response": result.raw_response, "provider_error": result.error_info} if result.error_info else result.raw_response
        row = AiRunLog(
            job_id=job_id,
            role=role,
            provider=result.provider,
            model=result.model,
            prompt_version=PROMPT_VERSION,
            input_hash=prompt_hash,
            request_payload=sanitize_payload(traced_request),
            response_payload=sanitize_payload(response_payload),
            error=result.error,
            latency_ms=result.latency_ms,
            token_usage=token_usage_with_trace(result.token_usage, result, run_trace),
        )
        db.add(row)
        db.flush()
        persist_ai_log_sse_event(db, row, index)


class GenerationJobAborted(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def progress_time(value: str | None) -> Any | None:
    if not value:
        return None
    try:
        return utc_now().fromisoformat(value.rstrip("Z"))
    except ValueError:
        return None


def progress_timestamp(value: Any | None = None) -> str:
    current = value or utc_now()
    return current.isoformat(timespec="seconds") + "Z"


def progress_state_version(progress: dict[str, Any] | None) -> int:
    if not isinstance(progress, dict):
        return 0
    try:
        return int(progress.get("state_version") or 0)
    except (TypeError, ValueError):
        return 0


def lease_is_active(progress: dict[str, Any] | None, owner: str | None = None) -> bool:
    if not isinstance(progress, dict):
        return False
    lease_owner = progress.get("lease_owner")
    if not lease_owner or (owner and lease_owner == owner):
        return False
    expires_at = progress_time(progress.get("lease_expires_at"))
    return expires_at is not None and utc_now() < expires_at


def set_generation_job_status(job: GenerationJob, status: str) -> None:
    assert_status_transition(STATUS_DOMAIN_GENERATION_JOB, job.status, status)
    job.status = status


def claim_job_lease(db: Session, job: GenerationJob, owner: str, timeout_seconds: int) -> bool:
    db.refresh(job)
    if job.status in TERMINAL_JOB_STATUSES:
        return False
    progress = {**(job.progress or {})}
    if job.status == STATUS_RUNNING and lease_is_active(progress, owner):
        progress["lease_conflict_at"] = progress_timestamp()
        progress["lease_conflict_owner"] = owner
        job.progress = progress
        db.commit()
        return False
    now = utc_now()
    progress.update(
        {
            "lease_owner": owner,
            "lease_acquired_at": progress_timestamp(now),
            "lease_expires_at": progress_timestamp(now + timedelta(seconds=max(30, timeout_seconds))),
            "state_version": progress_state_version(progress) + 1,
        }
    )
    set_generation_job_status(job, STATUS_RUNNING)
    job.progress = progress
    job.updated_at = now
    db.commit()
    return True


def mark_job_progress(db: Session, job: GenerationJob, stage: str | None = None, commit: bool = False, **extra: Any) -> None:
    progress = {**(job.progress or {})}
    if stage is not None:
        progress["stage"] = stage
    progress["last_heartbeat_at"] = progress_timestamp()
    progress["state_version"] = progress_state_version(progress) + 1
    progress.update(extra)
    job.progress = progress
    job.updated_at = utc_now()
    db.flush()
    persist_job_sse_event(db, job)
    if commit:
        db.commit()


def mark_job_terminal(db: Session, job: GenerationJob, status: str, reason: str, **extra: Any) -> None:
    set_generation_job_status(job, status)
    mark_job_progress(db, job, status, commit=False, terminal_reason=reason, lease_released_at=progress_timestamp(), **extra)
    db.commit()


def runtime_ai_cost_budget_snapshot(db: Session, settings: Any, provider: str, request: dict[str, Any]) -> dict[str, Any]:
    monthly_budget = float(getattr(settings, "ai_monthly_budget_usd", 0.0) or 0.0)
    daily_budget = float(getattr(settings, "ai_daily_budget_usd", 0.0) or 0.0)
    allow_overrun = bool(request.get("allow_budget_overrun"))
    today_start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    total_cost = 0.0
    today_cost = 0.0
    total_calls = 0
    today_calls = 0
    for row in db.scalars(select(AiRunLog)):
        usage = row.token_usage or {}
        cost = float(usage.get("estimated_cost_usd") or 0)
        total_cost += cost
        total_calls += 1
        if row.created_at >= today_start:
            today_cost += cost
            today_calls += 1
    exceeded: list[str] = []
    if provider != "mock" and daily_budget and today_cost >= daily_budget:
        exceeded.append("daily_budget_usd_runtime")
    if provider != "mock" and monthly_budget and total_cost >= monthly_budget:
        exceeded.append("monthly_budget_usd_runtime")
    return {
        "schema_version": "ai_runtime_cost_budget_v1",
        "provider": provider,
        "enforced": provider != "mock",
        "overrun_confirmed": allow_overrun,
        "daily_budget_usd": daily_budget or None,
        "monthly_budget_usd": monthly_budget or None,
        "today_estimated_cost_usd": round(today_cost, 6),
        "total_estimated_cost_usd": round(total_cost, 6),
        "today_calls": today_calls,
        "total_calls": total_calls,
        "today_start_utc": today_start.isoformat(),
        "ok": bool(allow_overrun or not exceeded),
        "exceeded": exceeded,
    }


def job_terminal_reason(db: Session, job: GenerationJob, settings: Any, request: dict[str, Any]) -> str | None:
    db.refresh(job)
    if job.status == STATUS_CANCELLED:
        return STATUS_CANCELLED
    batch_id = request.get("batch_id")
    if batch_id:
        batch = db.get(GenerationBatch, str(batch_id))
        if batch is not None and batch.status in CANCEL_REQUESTED_BATCH_STATUSES:
            mark_job_terminal(db, job, STATUS_CANCELLED, "batch_cancelled")
            return "batch_cancelled"
    deadline = progress_time((job.progress or {}).get("deadline_at"))
    if deadline is None:
        started = progress_time((job.progress or {}).get("started_at")) or job.updated_at or utc_now()
        deadline = started + timedelta(seconds=int(getattr(settings, "ai_job_timeout_seconds", 420) or 420))
    if utc_now() >= deadline:
        mark_job_terminal(db, job, STATUS_TIMED_OUT, "job_deadline_exceeded")
        return "job_deadline_exceeded"
    provider = str(request.get("job_provider") or request.get("batch_provider") or "mock")
    runtime_budget = runtime_ai_cost_budget_snapshot(db, settings, provider, request)
    if not runtime_budget["ok"]:
        mark_job_terminal(db, job, STATUS_FAILED, "ai_cost_budget_exceeded_runtime", runtime_cost_budget=runtime_budget)
        return "ai_cost_budget_exceeded_runtime"
    return None


def abort_if_job_terminal(db: Session, job: GenerationJob, settings: Any, request: dict[str, Any]) -> None:
    reason = job_terminal_reason(db, job, settings, request)
    if reason:
        raise GenerationJobAborted(reason)


def process_generation_job(db: Session, job_id: str, provider: str = "mock") -> dict[str, Any]:
    try:
        job = db.get(GenerationJob, job_id)
        with language_context((job.request or {}).get("language") if job else current_language()):
            return _process_generation_job(db, job_id, provider)
    except GenerationJobAborted:
        job = db.get(GenerationJob, job_id)
        return job.progress if job is not None else {"stage": "aborted"}
    except Exception as exc:
        db.rollback()
        job = db.get(GenerationJob, job_id)
        if job is None:
            raise
        if job.status not in TERMINAL_JOB_STATUSES:
            set_generation_job_status(job, STATUS_FAILED)
        job.progress = {
            **(job.progress or {}),
            "stage": STATUS_FAILED,
            "terminal_reason": str(exc),
            "last_heartbeat_at": progress_timestamp(),
        }
        job.updated_at = utc_now()
        persist_job_sse_event(db, job)
        db.commit()
        return job.progress


def _process_generation_job(db: Session, job_id: str, provider: str = "mock") -> dict[str, Any]:
    settings = get_settings()
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise ValueError(f"generation job not found: {job_id}")
    timeout_seconds = int(getattr(settings, "ai_job_timeout_seconds", 420) or 420)
    lease_owner = f"job-{job_id}-{uuid4()}"
    if not claim_job_lease(db, job, lease_owner, max(timeout_seconds, JOB_LEASE_SECONDS)):
        return {
            **(job.progress or {}),
            "stage": (job.progress or {}).get("stage", job.status),
            "terminal_reason": (job.progress or {}).get("terminal_reason") or "lease_conflict_or_terminal",
            "lease_owner": (job.progress or {}).get("lease_owner"),
        }
    started_at = utc_now()
    deadline_at = started_at + timedelta(seconds=timeout_seconds)
    job_request = dict(job.request or {})
    job_request.setdefault("job_provider", provider)
    job.request = job_request
    root_trace = ensure_job_root_trace(job.progress, job.id)
    job.progress = {
        **(job.progress or {}),
        "ai_trace": root_trace,
        "stage": "retrieving_examples",
        "events": (job.progress or {}).get("events", []),
        "started_at": (job.progress or {}).get("started_at") or progress_timestamp(started_at),
        "last_heartbeat_at": progress_timestamp(started_at),
        "deadline_at": progress_timestamp(deadline_at),
        "timeout_seconds": timeout_seconds,
        "provider": provider,
        "terminal_reason": None,
    }
    job.updated_at = utc_now()
    db.commit()

    abort_if_job_terminal(db, job, settings, job_request)
    examples = freeze_examples(retrieve_examples(db, job_request))
    ai_negative_examples = freeze_examples(retrieve_ai_negative_examples(db, job_request))
    selected_strategies = generation_strategies(job_request)
    mark_job_progress(
        db,
        job,
        "examples_retrieved",
        commit=True,
        retrieved_example_ids=[q.id for q in examples],
        source_example_ids=[q.id for q in examples],
        ai_negative_example_ids=[q.id for q in ai_negative_examples],
        source_example_count=len(examples),
        ai_negative_example_count=len(ai_negative_examples),
        source_example_star_distribution={
            str(star): sum(1 for q in examples if q.difficulty_star == star)
            for star in sorted({q.difficulty_star for q in examples if q.difficulty_star is not None})
        },
        candidate_count=len(selected_strategies),
        candidate_strategies=list(selected_strategies),
    )
    abort_if_job_terminal(db, job, settings, job_request)
    base_roles = model_roles(settings.default_model, settings)
    resolved_generator_model, generator_model_bucket = generator_model_for_request(settings, job_request, base_roles)
    roles = {**base_roles, "generator": resolved_generator_model}
    chain = provider_chain(provider, settings, db)
    reviewer_policy = reviewer_independence_policy(provider, roles, job_request, chain, settings)
    accepted_candidates: list[tuple[GeneratedCandidate, dict[str, Any], dict[str, Any]]] = []
    job.progress = {
        **(job.progress or {}),
        "model_selection": {
            "difficulty_star": job_request.get("difficulty_star"),
            "generator_bucket": generator_model_bucket,
            "generator_model": resolved_generator_model,
            "reviewer_model": roles["reviewer"],
            "candidate_count": len(selected_strategies),
            "candidate_strategies": list(selected_strategies),
            "selection_policy": "parallel_strategies_best_reviewer_score",
            "reviewer_independence_policy": reviewer_policy,
        },
    }
    mark_job_progress(db, job, "generating_candidates", commit=True)

    with ThreadPoolExecutor(max_workers=len(selected_strategies)) as executor:
        generation_results = list(
            executor.map(lambda strategy: run_generation_strategy(strategy, job_request, examples, ai_negative_examples, chain, roles), selected_strategies)
        )
    mark_job_progress(db, job, "reviewing_candidates", commit=True)
    abort_if_job_terminal(db, job, settings, job_request)

    provider_failure_count = 0
    for candidate_index, generated in enumerate(generation_results):
        abort_if_job_terminal(db, job, settings, job_request)
        strategy = generated["strategy"]
        final_generation = generated["results"][-1] if generated["results"] else None
        if final_generation is not None and final_generation.provider != "mock" and (final_generation.payload is None or final_generation.error):
            provider_failure_count += 1
        candidate, validation_errors = validate_candidate_payload(generated["candidate"] or {"status": "reject", "candidate_id": strategy, "reject_reason": "provider_failed"})
        if candidate is not None:
            candidate["_reference_context"] = {
                "source_example_ids": [q.id for q in examples],
                "ai_negative_example_ids": [q.id for q in ai_negative_examples],
                "source_examples_exclude_ai": True,
                "ai_negative_examples_same_star": bool(job_request.get("difficulty_star")),
                "candidate_count": len(selected_strategies),
                "candidate_strategies": list(selected_strategies),
            }
        generator_trace = child_ai_trace(root_trace, "generation_strategy", job_id=job.id, role="generator", strategy=strategy)
        add_ai_logs(db, job.id, "generator", generated["input_hash"], generated["request_payload"], generated["results"], trace=generator_trace)
        db.commit()
        db.refresh(job)
        abort_if_job_terminal(db, job, settings, job_request)
        review, reviewer_results, secondary_reviewer_results = reviewer_decision(candidate or {"status": "reject", "candidate_id": strategy, "reject_reason": "; ".join(validation_errors)}, chain, roles, reviewer_policy)
        review_hash = review["model_review"]["input_hash"]
        reviewer_trace = child_ai_trace(generator_trace, "candidate_review", job_id=job.id, role="reviewer", strategy=strategy, candidate_id=(candidate or {}).get("candidate_id", strategy))
        add_ai_logs(db, job.id, "reviewer", review_hash, {"candidate_id": (candidate or {}).get("candidate_id", strategy)}, reviewer_results, trace=reviewer_trace)
        if secondary_reviewer_results:
            secondary_trace = child_ai_trace(generator_trace, "candidate_review_secondary", job_id=job.id, role="secondary_reviewer", strategy=strategy, candidate_id=(candidate or {}).get("candidate_id", strategy))
            add_ai_logs(
                db,
                job.id,
                "secondary_reviewer",
                review_hash,
                {"candidate_id": (candidate or {}).get("candidate_id", strategy), "reviewer_round": "secondary"},
                secondary_reviewer_results,
                trace=secondary_trace,
            )
        db.flush()
        abort_if_job_terminal(db, job, settings, job_request)
        row = GeneratedCandidate(
            job_id=job.id,
            strategy=strategy,
            status=STATUS_ACCEPTED if review["decision"] == "accept" else STATUS_REJECTED,
            payload=candidate or {"status": "reject", "errors": validation_errors},
            review=review,
            score=review["total_score"],
        )
        db.add(row)
        db.flush()
        persist_candidate_sse_event(db, row, candidate_index)
        if review["decision"] == "accept":
            accepted_candidates.append((row, candidate or {}, review))
        mark_job_progress(db, job, "reviewing_candidates", commit=True, reviewed_strategy=strategy)

    abort_if_job_terminal(db, job, settings, job_request)
    all_candidate_rows = list(db.scalars(select(GeneratedCandidate).where(GeneratedCandidate.job_id == job.id).order_by(GeneratedCandidate.strategy, GeneratedCandidate.id)))
    set_review = candidate_set_review(all_candidate_rows)
    regeneration_ids = {slot["candidate_id"] for slot in set_review.get("regeneration_slots", [])}
    if regeneration_ids:
        accepted_candidates = [item for item in accepted_candidates if item[0].id not in regeneration_ids]
        for row in all_candidate_rows:
            if row.id in regeneration_ids:
                row.status = STATUS_REJECTED
                row.review = {**(row.review or {}), "decision": "reject", "set_review_rejected": True, "not_selected_reason": "pairwise_similarity_high"}
                persist_candidate_sse_event(db, row)
    job.progress = {**(job.progress or {}), "set_review": set_review}
    mark_job_progress(db, job, "selecting_candidate", commit=True, set_review=set_review)
    abort_if_job_terminal(db, job, settings, job_request)

    if not accepted_candidates:
        if provider != "mock" and provider_failure_count >= len(generation_results):
            set_generation_job_status(job, STATUS_FAILED)
            terminal_reason = "provider_failed"
        else:
            set_generation_job_status(job, STATUS_REJECTED)
            terminal_reason = "all_candidates_rejected"
        job.progress = {**(job.progress or {}), "stage": "done", "accepted": False, "terminal_reason": terminal_reason}
    else:
        accepted_candidates.sort(key=lambda item: (item[2].get("total_score", 0), item[0].strategy, item[0].id), reverse=True)
        selected_candidate: tuple[GeneratedCandidate, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], str] | None = None
        duplicate_rejections: list[dict[str, Any]] = []
        for candidate_row, candidate, best_review in accepted_candidates:
            abort_if_job_terminal(db, job, settings, job_request)
            apply_reviewer_calibration(candidate, best_review)
            question_payload = candidate["question"]
            raw_question = {
                "id": f"ai-{candidate_row.id}",
                "question_text": question_payload["stem_md"],
                "solution": question_payload.get("solution", {}),
                "answer_spec": {
                    "status": "verified",
                    "answer_type": question_payload.get("answer_type", "integer"),
                    "canonical_answer": question_payload["answer_spec"]["canonical"][0],
                    "acceptable_answers": question_payload["answer_spec"].get("accepted_forms", []),
                    "tolerance": {"type": "exact", "value": 0},
                    "unit": question_payload["answer_spec"].get("unit"),
                    "order_sensitive": question_payload["answer_spec"].get("order_sensitive", True),
                    "grading_method": "numeric_exact",
                    "verification": candidate.get("verification_spec") or {},
                },
                "difficulty_quality_evaluation": {
                    "difficulty_star": question_payload.get("difficulty", {}).get("star", 3),
                    "premium": question_payload.get("premium", {}).get("is_premium", False),
                    "dimension_scores": {},
                    "solution_hook": question_payload.get("solution", {}).get("solution_hook", ""),
                },
                "primary_path": {},
                "method_tags": [],
                "task_tags": [],
                "structure_tags": [],
                "constraint_tags": [],
                "source_metadata": {"generation_job_id": job.id},
            }
            qv2 = map_question_v2(raw_question, "AI生成题")
            qv2["source"]["kind"] = "generated"
            qv2["content"]["language"] = current_language()
            qv2["provenance"] = {
                "prompt_template_id": "generator",
                "prompt_version": PROMPT_VERSION,
                "generator_model": roles["generator"],
                "generator_model_bucket": generator_model_bucket,
                "reviewer_model": roles["reviewer"],
                "candidate_id": candidate_row.id,
                "retrieved_example_ids": [q.id for q in examples],
                "source_example_ids": [q.id for q in examples],
                "ai_negative_example_ids": [q.id for q in ai_negative_examples],
                "candidate_count": len(selected_strategies),
                "candidate_strategies": list(selected_strategies),
                "reviewer_independence_policy": reviewer_policy,
            }
            if not visible_solution_steps_from_qv2(qv2):
                candidate_row.status = STATUS_REJECTED
                candidate_row.publish_status = "not_published"
                candidate_row.review = {
                    **(candidate_row.review or {}),
                    "decision": "reject",
                    "not_selected_reason": "solution_steps_missing_or_too_thin",
                    "issues": sorted(set([*(candidate_row.review or {}).get("issues", []), "solution_steps_missing_or_too_thin"])),
                }
                duplicate_rejections.append({"candidate_id": candidate_row.id, "reason": "solution_steps_missing_or_too_thin"})
                persist_candidate_sse_event(db, candidate_row)
                continue
            question_id = raw_question["id"]
            duplicate = existing_generation_duplicate(db, question_id, raw_question["question_text"], qv2)
            if duplicate:
                candidate_row.status = STATUS_REJECTED
                candidate_row.publish_status = "batch_rejected_similar"
                candidate_row.review = {
                    **(candidate_row.review or {}),
                    "decision": "reject",
                    "not_selected_reason": "duplicate_against_existing_question",
                    "duplicate": duplicate,
                }
                duplicate_rejections.append({"candidate_id": candidate_row.id, "duplicate": duplicate})
                persist_candidate_sse_event(db, candidate_row)
                continue
            selected_candidate = (candidate_row, candidate, best_review, raw_question, qv2, question_id)
            break

        if selected_candidate is None:
            set_generation_job_status(job, STATUS_REJECTED)
            job.progress = {
                **(job.progress or {}),
                "stage": "done",
                "accepted": False,
                "terminal_reason": "all_accepted_candidates_duplicate",
                "duplicate_rejections": duplicate_rejections,
            }
            persist_job_sse_event(db, job)
            db.commit()
            return job.progress

        candidate_row, candidate, best_review, raw_question, qv2, question_id = selected_candidate
        for row, _, review in accepted_candidates:
            if row.id == candidate_row.id:
                continue
            if (row.review or {}).get("not_selected_reason") == "duplicate_against_existing_question":
                continue
            row.status = STATUS_REJECTED
            row.review = {
                **review,
                "decision": "reject",
                "not_selected_reason": "lower_reviewer_score_than_best_candidate",
                "selected_candidate_id": candidate_row.id,
                "selected_total_score": best_review.get("total_score", 0),
            }
            persist_candidate_sse_event(db, row)
        mark_job_progress(db, job, "publish_guard_checked", commit=True, publish_guard_checked_at=progress_timestamp())
        abort_if_job_terminal(db, job, settings, job_request)
        source = db.scalar(select(SourceFile).where(SourceFile.filename == "__ai_generated__"))
        if source is None:
            source = SourceFile(filename="__ai_generated__", sha256="generated", question_count=0)
            db.add(source)
            db.flush()
        source.question_count += 1
        question = Question(
            id=question_id,
            source_file_id=source.id,
            status="verified",
            source_name="AI生成题",
            publish_status="active_practice",
            eligible_for_practice=True,
            eligible_for_test=False,
            question_fault_status="none",
            question_trust_score=0.8,
            solution_style=job_request.get("solution_style", "elementary_preferred"),
            stem_preview=raw_question["question_text"][:500],
            difficulty_star=qv2["evaluation"]["difficulty"]["star"],
            premium=False,
            source_difficulty_star=qv2["evaluation"]["difficulty"]["star"],
            calibrated_difficulty_star=qv2["evaluation"]["difficulty"]["star"],
            source_premium=False,
            calibrated_premium=False,
            calibration_version="ai_generation_v1",
            exact_hash=qv2["dedupe"]["exact_hash"],
            normalized_hash=qv2["dedupe"]["normalized_hash"],
            semantic_hash=qv2["dedupe"]["semantic_hash"],
            family_id=qv2["dedupe"]["family_id"],
        )
        db.add(question)
        db.flush()
        version = QuestionVersion(
            question_id=question_id,
            version=1,
            raw_json=raw_question,
            question_v2=qv2,
            content_hash=sha256_text(json.dumps(raw_question, ensure_ascii=False, sort_keys=True)),
        )
        db.add(version)
        db.flush()
        question.current_version_id = version.id
        candidate_row.published_question_id = question_id
        candidate_row.published_question_version_id = version.id
        candidate_row.published_at = utc_now()
        candidate_row.publish_status = "active_practice"
        persist_candidate_sse_event(db, candidate_row)
        db.add(
            AnswerSpec(
                question_id=question_id,
                status="verified",
                answer_type=raw_question["answer_spec"]["answer_type"],
                canonical_answer=raw_question["answer_spec"]["canonical_answer"],
                normalized_spec=qv2["answer_spec"],
                raw_answer_spec=raw_question["answer_spec"],
                verification=raw_question["answer_spec"]["verification"],
            )
        )
        job.accepted_question_id = question_id
        set_generation_job_status(job, STATUS_ACCEPTED)
        job.progress = {**(job.progress or {}), "stage": "done", "accepted": True, "question_id": question_id, "lease_released_at": progress_timestamp()}
    mark_job_progress(db, job, job.progress.get("stage"), commit=False)
    db.commit()
    return job.progress
