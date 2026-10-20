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
