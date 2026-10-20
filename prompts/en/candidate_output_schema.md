## Platform candidate-question output schema

This schema takes precedence over the rating guide's example JSON. Only one of the following top-level structures is permitted. Pipe-separated strings describe allowed enum alternatives; return one permitted value, not the entire list. Descriptive strings are placeholders, not literal answers.

Write new problem statements, formal solutions, and human-readable summaries in natural English. Use upper-primary language for student-facing prose while retaining the requested mathematical star level. Preserve keys, enum values, supplied IDs, taxonomy codes, and canonical mathematical representations. Do not translate a supplied canonical answer or change its numerical value or notation merely for localization.

### Success

```json
{
  "status": "ok",
  "candidate_id": "A stable unique string",
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
    "stem_md": "The complete problem statement in Markdown",
    "answer_type": "integer | rational | decimal | expression | multi_blank | choice | set | open_response",
    "answer_spec": {
      "canonical": ["One complete reference-answer string, including the entire set or all blanks when applicable"],
      "accepted_forms": ["Acceptable equivalent answer strings"],
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
      "final_answer": "Must exactly equal canonical[0]",
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
      "reason": "Explain why this star level fits"
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
    "expected": "Must equal canonical[0]"
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
```

### Failure

```json
{"status":"reject","candidate_id":"A stable unique string","reject_reason":"...","question":null}
```

### Mandatory format rules

- Do not output the rating guide's root structure based on `validity/quality_flags/primary_domain`.
- `question.difficulty` must include all six dimensions, `total_score`, `star`, and `reason`. Map stars exactly as 0-2=1, 3-5=2, 6-8=3, 9-11=4, and 12-18=5. Apply the guide's dimension definitions and hard gates honestly; do not inflate scores to hit the requested star.
- `question.premium` must include all four dimensions, `total_score`, `is_premium`, and `reasons`. `is_premium` must agree with `total_score>=6`.
- `solution.final_answer` must exactly equal `question.answer_spec.canonical[0]`.
- `verification_spec.expected` must equal `question.answer_spec.canonical[0]`.
- When `answer_type="set"`, `canonical` still contains one complete set string, for example `["{1,3,5}"]`, not `["1","3","5"]`. The same single-complete-string rule applies to multiple blanks.
- `question.solution.steps` contains the formal, student-readable solution, with an `explanation` in each step. The one-step skeleton above illustrates shape, not sufficient solution length; follow the generator's 3-to-8-step requirement. `student_steps`, a hook, a final answer, or private verification alone cannot substitute for it.
- Keep key insights, solution hooks, and private verification out of `question.stem_md`. Clear rules and object relationships are allowed; solving hints are not.
- The final model response must be exactly one valid JSON object, without Markdown fences or text outside it.
