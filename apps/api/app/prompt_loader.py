from __future__ import annotations
from .localization import translate_message, current_language

from functools import lru_cache
from pathlib import Path

from .config import project_root
from .localization import current_language, normalize_language, translate
from .prompt_registry import active_prompt_filename


GUIDE_FILENAME = "题目难度与精品评级指南_GPT版.md"
CORE_GUIDE_SECTION_KEYWORDS = (
    "评级基线",
    "六维评分",
    "1—5 星标准",
    "精品",
    "GPT 分类流程",
)
REVIEWER_GUIDE_SECTION_KEYWORDS = (
    *CORE_GUIDE_SECTION_KEYWORDS,
    "推荐 JSON 格式",
    "可直接复制给 GPT",
)

CANDIDATE_OUTPUT_SCHEMA = """
## 平台候选题输出 schema（必须优先于评级指南中的示例 JSON）

只允许输出以下顶层结构之一。

成功时：
{
  "status": "ok",
  "candidate_id": "稳定唯一字符串",
  "reject_reason": null,
  "design_summary": {
    "generation_goal": "...",
    "tested_points": ["..."],
    "chosen_strategy": "...",
    "difference_axes_from_examples": ["task_form", "representation", "secondary_method"],
    "numeric_design": "...",
    "fairness_notes": "...",
    "difference_from_examples": "..."
  },
  "question": {
    "stem_md": "完整题干 Markdown",
    "answer_type": "integer | rational | decimal | expression | multi_blank | choice | set | open_response",
      "answer_spec": {
      "canonical": ["唯一标准答案字符串；即使答案是集合或多空，也必须把完整答案写成一个字符串"],
      "accepted_forms": ["可接受等价答案字符串"],
      "tolerance": null,
      "unit": null,
      "order_sensitive": true
    },
    "solution": {
      "primary_method": "...",
      "solution_style_used": "elementary_non_equation | equation | mixed",
      "natural_solution_available": true,
      "equation_necessity": "not_needed | needed | justified",
      "equation_reason": null,
      "key_insight": "...",
      "solution_hook": "...",
      "steps": [{"explanation": "..."}],
      "student_steps": ["..."],
      "final_answer": "必须与 canonical[0] 完全相同",
      "alternative_methods": [],
      "alternative_solution": null,
      "verification_solution": {"method": "...", "steps": ["..."]},
      "requested_solution_style": "elementary_preferred",
      "pitfalls": ["..."]
    },
    "taxonomy": {
      "primary_tag_id": "...",
      "secondary_tag_ids": [],
      "method_tag_ids": [],
      "task_tag_ids": [],
      "structure_tag_ids": [],
      "constraint_tag_ids": [],
      "constraint_notes": []
    },
    "difficulty": {
      "knowledge_span": 0,
      "modeling": 0,
      "insight": 0,
      "reasoning_chain": 0,
      "case_analysis": 0,
      "calculation_load": 0,
      "total_score": 0,
      "star": 1,
      "reason": "说明为什么符合该星级"
    },
    "premium": {
      "innovation": 0,
      "elegance": 0,
      "integration": 0,
      "transfer_value": 0,
      "total_score": 0,
      "is_premium": false,
      "reasons": []
    }
  },
  "verification_spec": {
    "engine": "arithmetic | symbolic | enumeration",
    "constraints": ["..."],
    "target": "final_answer",
    "expected": "必须等于 canonical[0]"
  },
  "self_checks": {
    "complete": true,
    "solvable": true,
    "unique_answer": true,
    "answer_recomputed": true,
    "within_grade": true,
    "not_number_only_variant": true,
    "source_similarity_risk": "low"
  }
}

失败时：
{"status":"reject","candidate_id":"稳定唯一字符串","reject_reason":"...","question":null}

硬性格式要求：
- 不得输出评级指南里的 `validity/quality_flags/primary_domain` 根结构。
- `question.difficulty` 必须包含六个维度、total_score、star、reason，且 star 必须按 0—2=1、3—5=2、6—8=3、9—11=4、12—18=5 映射。
- `question.premium` 必须包含四个维度、total_score、is_premium、reasons，且 is_premium 必须与 total_score>=6 一致。
- `solution.final_answer` 必须与 `question.answer_spec.canonical[0]` 完全一致。
- 如果 `answer_type="set"`，`canonical` 也只能有一个完整集合字符串，例如 `["{1,3,5}"]`，不能写成 `["1","3","5"]`。
"""


CANDIDATE_OUTPUT_SCHEMA_EN = """
## Platform candidate output schema (takes precedence over example JSON in the rubric)

Return only one of the following top-level structures.

On success:
{
  "status": "ok",
  "candidate_id": "stable unique string",
  "reject_reason": null,
  "design_summary": {
    "generation_goal": "...",
    "tested_points": ["..."],
    "chosen_strategy": "...",
    "difference_axes_from_examples": ["task_form", "representation", "secondary_method"],
    "numeric_design": "...",
    "fairness_notes": "...",
    "difference_from_examples": "..."
  },
  "question": {
    "stem_md": "complete question in Markdown",
    "answer_type": "integer | rational | decimal | expression | multi_blank | choice | set | open_response",
    "answer_spec": {
      "canonical": ["one complete canonical answer string, including sets or multiple blanks"],
      "accepted_forms": ["equivalent acceptable answer string"],
      "tolerance": null,
      "unit": null,
      "order_sensitive": true
    },
    "solution": {
      "primary_method": "...",
      "solution_style_used": "elementary_non_equation | equation | mixed",
      "natural_solution_available": true,
      "equation_necessity": "not_needed | needed | justified",
      "equation_reason": null,
      "key_insight": "...",
      "solution_hook": "...",
      "steps": [{"explanation": "..."}],
      "student_steps": ["..."],
      "final_answer": "must equal canonical[0] exactly",
      "alternative_methods": [],
      "alternative_solution": null,
      "verification_solution": {"method": "...", "steps": ["..."]},
      "requested_solution_style": "elementary_preferred",
      "pitfalls": ["..."]
    },
    "taxonomy": {
      "primary_tag_id": "...",
      "secondary_tag_ids": [],
      "method_tag_ids": [],
      "task_tag_ids": [],
      "structure_tag_ids": [],
      "constraint_tag_ids": [],
      "constraint_notes": []
    },
    "difficulty": {
      "knowledge_span": 0,
      "modeling": 0,
      "insight": 0,
      "reasoning_chain": 0,
      "case_analysis": 0,
      "calculation_load": 0,
      "total_score": 0,
      "star": 1,
      "reason": "explain why this star rating applies"
    },
    "premium": {
      "innovation": 0,
      "elegance": 0,
      "integration": 0,
      "transfer_value": 0,
      "total_score": 0,
      "is_premium": false,
      "reasons": []
    }
  },
  "verification_spec": {
    "engine": "arithmetic | symbolic | enumeration",
    "constraints": ["..."],
    "target": "final_answer",
    "expected": "must equal canonical[0] exactly"
  },
  "self_checks": {
    "complete": true,
    "solvable": true,
    "unique_answer": true,
    "answer_recomputed": true,
    "within_grade": true,
    "not_number_only_variant": true,
    "source_similarity_risk": "low"
  }
}

On rejection:
{"status":"reject","candidate_id":"stable unique string","reject_reason":"...","question":null}

Mandatory format rules:
- Do not return the rubric's validity/quality_flags/primary_domain root structure.
- question.difficulty must include all six dimensions, total_score, star, and reason.
  Map total_score to star exactly: 0-2=1, 3-5=2, 6-8=3, 9-11=4, 12-18=5.
- question.premium must include all four dimensions, total_score, is_premium, and reasons.
  is_premium must agree with total_score>=6.
- solution.final_answer must equal question.answer_spec.canonical[0] exactly.
- For answer_type="set", canonical must still contain just one complete set string,
  such as ["{1,3,5}"], not ["1","3","5"].
"""


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=128)
def _cached_prompt_template(root: Path, name: str, selected_name: str, locale: str) -> str:
    if Path(selected_name).name != selected_name or not selected_name.endswith(".md"):
        raise ValueError("unsafe_prompt_filename")
    path = root / locale / selected_name
    text = _read_text(path)
    # Root-level files are the historical Chinese assets, never an English fallback.
    if not text and locale == "zh-CN":
        text = _read_text(root / selected_name)
    if not text:
        raise ValueError(f"prompt_language_unavailable:{locale}:{selected_name}")
    return text


def prompt_template(name: str, locale: str | None = None) -> str:
    language = current_language() if locale is None else normalize_language(locale)
    return _cached_prompt_template(
        project_root() / "prompts", name, active_prompt_filename(name), language,
    )


# The settings activation endpoint and existing callers use these cache hooks.
prompt_template.cache_clear = _cached_prompt_template.cache_clear
prompt_template.cache_info = _cached_prompt_template.cache_info
prompt_template.cache_parameters = _cached_prompt_template.cache_parameters


def _guide_path(root: Path, locale: str) -> Path | None:
    localized_path = root / "prompts" / locale / "difficulty_guide.md"
    if localized_path.exists():
        return localized_path
    if locale == "en":
        path = root / "prompts" / "en" / "difficulty_guide.md"
        return path if path.exists() else None
    for candidate in (root / "做题项目" / GUIDE_FILENAME, root / GUIDE_FILENAME):
        if candidate.exists():
            return candidate
    return None


def _selected_sections(text: str, keywords: tuple[str, ...]) -> list[str]:
    sections: list[str] = []
    current_header = ""
    current_lines: list[str] = []

    def flush() -> None:
        if current_lines and any(keyword in current_header for keyword in keywords):
            sections.append("\n".join(current_lines).strip())

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            current_header = line
            current_lines = [line]
        elif current_lines:
            current_lines.append(line)
    flush()
    return sections


@lru_cache(maxsize=16)
def _cached_difficulty_guide_excerpt(root: Path, locale: str, include_output_format: bool) -> str:
    path = _guide_path(root, locale)
    if path is None:
        if locale == "en":
            return (
                "Difficulty and premium guide not found. Use the default difficulty scale: "
                "0-2=1 star, 3-5=2 stars, 6-8=3 stars, 9-11=4 stars, 12-18=5 stars."
            )
        return (
            "未找到题目难度与精品评级指南文件。按默认尺度：0-2=1星，3-5=2星，"
            "6-8=3星，9-11=4星，12-18=5星。"
        )
    text = _read_text(path)
    if locale == "en":
        # English guide headings are owned by the translated prompt asset, not the
        # Chinese section-name filter. Retain the full bounded English rubric.
        return f"Source file: {path.name}\n\n{text[:9000]}"
    keywords = REVIEWER_GUIDE_SECTION_KEYWORDS if include_output_format else CORE_GUIDE_SECTION_KEYWORDS
    sections = _selected_sections(text, keywords)
    excerpt = "\n\n".join(sections) if sections else text
    return f"来源文件：{path.name}\n\n{excerpt[:9000]}"


def difficulty_guide_excerpt(include_output_format: bool = False, locale: str | None = None) -> str:
    language = current_language() if locale is None else normalize_language(locale)
    return _cached_difficulty_guide_excerpt(project_root(), language, include_output_format)


difficulty_guide_excerpt.cache_clear = _cached_difficulty_guide_excerpt.cache_clear
difficulty_guide_excerpt.cache_info = _cached_difficulty_guide_excerpt.cache_info


@lru_cache(maxsize=8)
def _cached_candidate_output_schema(root: Path, locale: str) -> str:
    text = _read_text(root / "prompts" / locale / "candidate_output_schema.md")
    if not text:
        raise ValueError(f"prompt_language_unavailable:{locale}:candidate_output_schema.md")
    return text


def candidate_output_schema(locale: str | None = None) -> str:
    language = current_language() if locale is None else normalize_language(locale)
    return _cached_candidate_output_schema(project_root(), language)


candidate_output_schema.cache_clear = _cached_candidate_output_schema.cache_clear


def generator_system_prompt() -> str:
    template = prompt_template("generator_v2.md") or translate_message('backend.you_are_a_mathematics_competition_content_expert_for_upper_ad58aef3', language=current_language())
    if current_language() == "en":
        return (
            f"{template}\n\n"
            "## Difficulty and premium rubric (mandatory)\n"
            f"{difficulty_guide_excerpt()}\n\n"
            "First score all six difficulty dimensions using the rubric, then map the total to stars: "
            "0-2=1, 3-5=2, 6-8=3, 9-11=4, 12-18=5. Do not inflate scores to match the target.\n\n"
            "The rubric governs difficulty/premium fields only, not the task's output JSON structure.\n"
            f"{candidate_output_schema()}"
        )
    return (
        f"{template}\n\n"
        "## 难度与精品评级指南（必须执行）\n"
        f"{difficulty_guide_excerpt()}\n\n"
        "生成时必须先按指南自评六维分数，再映射星级：0—2=1星，3—5=2星，6—8=3星，9—11=4星，12—18=5星。"
        "不得为了匹配目标星级而虚高。\n\n"
        "注意：评级指南只用于 difficulty/premium 字段，不是本任务的输出 JSON 结构。\n"
        f"{candidate_output_schema()}"
    )


def reviewer_system_prompt() -> str:
    template = prompt_template("reviewer_v2.md") or translate_message('backend.you_are_an_independent_mathematics_competition_question_reviewer_privately_682936d0', language=current_language())
    if current_language() == "en":
        return (
            f"{template}\n\n"
            "## Difficulty and premium rubric (apply independently)\n"
            f"{difficulty_guide_excerpt(include_output_format=True)}\n\n"
            "Independently provide dimension_scores, total_score, difficulty_star, difficulty_reason, "
            "premium_score, premium, and premium_reasons. Do not trust the candidate's self-reported rating."
        )
    return (
        f"{template}\n\n"
        "## 难度与精品评级指南（必须独立执行）\n"
        f"{difficulty_guide_excerpt(include_output_format=True)}\n\n"
        "审核时必须独立给出 dimension_scores、total_score、difficulty_star、difficulty_reason、"
        "premium_score、premium、premium_reasons。不得相信候选自报星级。"
    )


def mastery_variant_generator_system_prompt() -> str:
    template = prompt_template("mastery_variant_generator_2step_v1.md") or (
        "You are a primary-school math mastery-variant generator. Internally diagnose only the necessary "
        "student evidence, design one or more independent similar questions, solve and self-check them. "
        "Return only valid JSON matching mastery_variant_generator_output_v2. Do not reveal chain-of-thought."
    )
    return (
        f"{template}\n\n"
        "## Platform output adapter requirements\n"
        "Each ok candidate must include a question_v2-like object with content.stem_md, answer_spec, "
        "solution, taxonomy, evaluation, and dedupe where possible. The platform will normalize this into "
        "its internal QuestionV2 schema before publishing.\n\n"
        "## Difficulty rubric compact\n"
        f"{difficulty_guide_excerpt()[:6000]}"
    )


def mastery_variant_batch_reviewer_system_prompt() -> str:
    template = prompt_template("mastery_variant_batch_reviewer_2step_v1.md") or (
        "You are an independent batch reviewer for primary-school math mastery variants. Independently solve "
        "each candidate, check answer_spec, difficulty, distance from root, diagnostic alignment, and batch "
        "diversity. Return only valid JSON matching mastery_variant_batch_review_output_v2."
    )
    return (
        f"{template}\n\n"
        "## Platform review adapter requirements\n"
        "Only select candidates that are mathematically valid, auto-gradable, not too near, not too far, and "
        "not duplicate within the batch. If none satisfy the contract, return all_rejected with an empty "
        "selected_candidate_ids list.\n\n"
        "## Difficulty rubric compact\n"
        f"{difficulty_guide_excerpt(include_output_format=True)[:6000]}"
    )


def answer_judge_system_prompt() -> str:
    return prompt_template("answer_judge_v1.md") or translate_message('backend.you_review_primary_school_mathematics_answers_privately_normalize_the_b3446d32', language=current_language())


def question_support_system_prompt() -> str:
    return prompt_template("question_support_v1.md") or translate_message('backend.you_clarify_wording_and_formatting_of_primary_school_mathematics_b8e0d99e', language=current_language())


def solution_explainer_system_prompt() -> str:
    return prompt_template("solution_explainer_v1.md") or translate_message('backend.you_explain_primary_school_mathematics_solutions_privately_compare_the_d5f5c93e', language=current_language())


def question_repair_reviewer_system_prompt() -> str:
    return prompt_template("question_repair_reviewer_v1.md") or translate_message('backend.you_review_repairs_to_primary_school_mathematics_questions_privately_df1b5a47', language=current_language())
