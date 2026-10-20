# Mathematics difficulty and premium-quality rating guide

This English edition preserves the source guide's six-dimension rubric, star thresholds, hard gates, premium-quality criteria, and classification process. Historical sample counts below describe the source document's audit, not a new audit or evidence from this translation task.

Use English for human-readable reasons and reports. Preserve supplied IDs, taxonomy codes, schema keys, enum values, and canonical mathematical notation. Readable, age-appropriate English must not lower the requested mathematical difficulty or disclose a solution strategy in a problem statement. A fixed requested star is a design constraint, not permission to inflate a score; redesign or reject a mismatched generated question.

## 1. Source audit and sampling context

The source audit parsed 2,493 independently answerable subquestions from five question banks. It used stratified random sampling by question bank and year/paper, fixed seed `20260717`, and selected 600 questions. Identical normalized texts were not sampled twice. Near-duplicates and parameter variants were retained and labeled: 6 groups containing 12 questions, used to audit repetition.

| Question-bank group | Sample count |
|---|---:|
| Huafu/Alliance | 120 |
| Qiu class | 160 |
| Hua Luogeng Cup | 180 |
| Hope Cup, Grade 5 | 60 |
| Hope Cup, Grade 6 | 80 |

The sample covered 186 paper/year combinations. Four questions required checking. Damaged statements, missing diagrams, and formatting errors are not mathematical difficulty.

## 2. Rating baseline

Default reference learner: a pupil preparing for secondary-school entry or Qiu-class selection, with Grade 6 curricular knowledge and basic mathematics-competition training. Clearly required secondary-school algebra, radicals, floor functions, and similar prerequisites count toward knowledge difficulty.

Rate the overall difficulty of finding and completing a reliable, reasonable natural solution to a complete problem. Lengthy wording, large numbers, pure computation, unfamiliar background, OCR errors, and missing diagrams must not independently raise the star level.

## 3. Six-dimension scoring: 0 to 3 per dimension

| Dimension / field | 0 points | 1 point | 2 points | 3 points |
|---|---|---|---|---|
| Knowledge span / `knowledge_span` | One basic knowledge point. | One standard competition knowledge point. | Two linked modules or an elementary-to-secondary transition. | Advanced prerequisites or deep integration of multiple modules. |
| Modeling and transformation / `modeling` | Direct substitution. | Routine variable assignment or equal-part method. | Stages, substitution, or hidden relationships. | Independently constructing a non-obvious model. |
| Key insight / `insight` | No notable insight barrier. | A standard formula or familiar pattern. | A key step involving a cycle, invariant, extremal idea, or similar insight. | A highly non-obvious central breakthrough. |
| Reasoning chain / `reasoning_chain` | One or two steps. | 2 to 4 stable steps. | Multiple stages or intermediate conclusions. | A sustained proof chain or nested structure. |
| Case analysis and enumeration / `case_analysis` | No case split. | A small number of cases. | Exhaustive cases, inclusion-exclusion, or worst-case reasoning. | Complex construction, game reasoning, or advanced enumeration. |
| Calculation load / `calculation_load` | Very light. | Routine. | Lengthy but manageable. | Long expressions or high risk of calculation errors. |

The six scores sum to 0 through 18. Pure computational load occupies only one dimension. A problem difficult only because of long calculations should normally receive no more than 3 stars. Do not assign artificial insight or modeling points to evade this limit.

## 4. Star thresholds and hard gates

| Stars | Total score | Standard |
|---|---:|---|
| 1 | 0-2 | One knowledge point, a direct entry, and one or two steps; mainly basic understanding and accuracy. |
| 2 | 3-5 | One familiar model, limited transformation, and stable steps. Standard multi-condition word problems are normally at least 2 stars. |
| 3 | 6-8 | Interacting conditions or a structure that is not immediately visible; moderate ability to distinguish levels of understanding. |
| 4 | 9-11 | A non-obvious breakthrough, often with proof, strategy, complete classification, construction, or complex piecewise analysis. |
| 5 | 12-18 | Advanced prerequisites together with sustained insight. Both `insight` and `reasoning_chain` must be at least 2, and at least 3 dimensions must score at least 2. |

Calibration rules:

1. Check completeness first. A defective question is not a difficult question.
2. Rate a reasonable, discoverable natural solution, not the least efficient method.
3. A prestigious source, a long statement, or large numbers do not directly earn stars.
4. If an elegant shortcut exists, assess how discoverable it is. Do not call it easy merely because it seems obvious after seeing the answer.
5. Keep 5-star ratings strictly rare. Obscure knowledge or long calculations alone do not qualify.

Respect both the score mapping and the hard gates. If they conflict, reassess the evidence and dimension scores rather than report an inconsistent star or inflate another dimension. Reject a generated candidate that cannot honestly satisfy the fixed requested level.

Source sample distribution:

| Stars | Questions | Premium questions |
|---|---:|---:|
| 1 | 95 | 0 |
| 2 | 363 | 7 |
| 3 | 128 | 13 |
| 4 | 11 | 2 |
| 5 | 3 | 0 |

These are historical observations, not quotas for future generation or review.

## 5. Star calibration examples

The following are summaries of the source examples, not complete replacement questions. Truncated source excerpts must not be treated as independently solvable problems. Preserve the sample IDs when referring to them.

| Stars | Sample ID | Domain and question summary | Rating rationale |
|---|---|---|---|
| 1 | `S0092` | Calculation: $\frac35-\frac12$. | One knowledge point, a direct route, normally one or two steps. |
| 1 | `S0089` | Word-problem modeling: from two equal-length steel bars, remove $\frac23$ of one and $\frac23$ meter from the other; compare the remainders. | One knowledge point and a direct route; understanding the quantities matters. |
| 2 | `S0220` | Motion: two people cover the same route at steady speeds in 3 and 4 hours; find when one remaining distance is twice the other. | A standard model with limited transformation and a short solution. |
| 2 | `S0004` | A mean of thirteen natural numbers was reported as 11.27 to two decimal places; only the last digit is wrong. | A familiar model with limited transformation; recognizing the key method is the main demand. |
| 3 | `S0018` | Pigeonhole/guarantee: 42 red, 15 yellow, 20 green, 14 white, and 9 black balls; how many draws guarantee 15 of one color? | Interacting constraints and non-immediate structure, especially method recognition and cases. |
| 3 | `S0218` | Counting: choose 6 distinct digits from $0,1,2,\ldots,9$ for boxes in an expression whose result is 91; the source excerpt omits the full layout. | Multiple conditions or hidden structure; sustained reasoning is central. |
| 4 | `S0206` | Logic: nine distinct two-digit hat numbers, each person seeing the other eight, plus a displayed number $A$; the excerpt is incomplete. | A non-obvious breakthrough, multi-stage reasoning, translation of conditions, and possible cases or construction. |
| 4 | `S0548` | New definitions: an $m$ by $n$ grid with numbered rows/columns and positions $(a,b)$; the excerpt is incomplete. | Translating definitions, identifying a key insight, and sustained reasoning. |
| 5 | `S0132` | Algebra: distinct nonzero real numbers $a,b,c,d,x,y,z,w$ constrained by rational expressions; the excerpt is incomplete. | Wide prerequisites plus sustained insight, structural control, and calculation demands. |
| 5 | `S0232` | Motion: two racers begin at 40 and 50 meters per minute and their speeds change over time; the excerpt omits part of the rules. | Modeling, key insight, and case analysis with sustained high-level demands. |

## 6. Premium quality is independent of stars

Premium does not mean very difficult. A 2-star question can be premium; a 5-star question can be merely complicated and not premium.

### Four premium dimensions: total 0 to 10

| Dimension / field | Range | What to assess |
|---|---:|---|
| Innovation / non-routine design / `innovation` | 0-3 | More than changed numbers or scenery; a new rule, perspective, or counterintuitive entry. |
| Structural beauty and elegance / `elegance` | 0-3 | Interlocking conditions; a coherent invariant, cycle, symmetry, construction, or extremal thread. |
| Integration / `integration` | 0-2 | Natural integration of at least two knowledge domains, not artificial stitching. |
| Transfer and discussion value / `transfer_value` | 0-2 | Generalizable methods, comparison of multiple solutions, or worthwhile further exploration. |

Recommended premium gates:

- Total score >= 6.
- `elegance >= 2`.
- `innovation >= 1`.
- A complete, fair statement and a stable answer.
- More than large numbers, long calculations, or a novel story.

A 1-star premium question additionally requires `innovation >= 2` and `elegance = 3`. The source sample did not force any 1-star premium labels.

Premium vetoes:

- Only large numbers, long expressions, or mechanical steps.
- A template with only numbers, characters, or years changed.
- New story packaging around a completely ordinary model.
- Reliance on obscure trivia, ambiguity, missing diagrams, or OCR errors.
- Brute-force enumeration without structural compression.
- Redundant conditions or unstable answers.
- High overlap with an existing bank question, with only parameters changed.

The source labeled 22 questions premium, or 3.7%, maintaining high selectivity. This is not a target percentage.

For platform candidate output, `question.premium.is_premium` must also agree with the mandatory schema rule `total_score>=6`. Do not weaken the rubric's qualitative gates or vetoes to satisfy serialization. Resolve a disagreement through evidence-based rescoring or reject the candidate; never fabricate scores. The candidate schema takes precedence over the guide's illustrative JSON structure, not over mathematical honesty.

## 7. Premium examples across star levels

| Sample ID | Stars | Example and premium rationale |
|---|---:|---|
| `S0004` | 2 | The thirteen-number mean with a wrong final digit: an invariant/conservation insight and a concise counterintuitive route, with discussion and transfer value rather than heavy computation. |
| `S0171` | 3 | Start with the natural numbers 1 through 2022. Repeatedly replace two numbers of the same parity with their mean until one natural number remains; seek its maximum and minimum. Invariants/conservation, optimization, and a concise counterintuitive structure support transfer. |
| `S0206` | 4 | The hat-number logic example: a logical chain, proof or construction, and integrated knowledge support discussion and transfer. |
| `S0548` | 4 | The newly defined grid example: original definitions, proof or construction, and optimization support discussion and transfer. |

## 8. Classification workflow

1. Check completeness: missing diagrams or conditions, displaced formulas, missing questions, and conflicting units.
2. Set the reference school stage.
3. Identify the natural solution's entry point; a full written answer is not needed at this preliminary rating step. This does not waive a reviewer role's independent-solving requirement.
4. Score all six dimensions from 0 to 3 and give evidence.
5. Map the total to stars and enforce the pure-calculation ceiling and 5-star hard gates.
6. Set aside stars and independently assess premium quality.
7. Report tested knowledge, solution hook, main pitfall, quality, and confidence.
8. When text is incomplete, lower confidence and flag it as requiring checking. Do not pretend certainty.

Keep private reasoning private. Provide concise rating evidence, not hidden chain-of-thought. A private or reviewer-facing `solution_hook` must not be inserted into a student problem statement.

## 9. Illustrative classification JSON, not a candidate-output schema

The source guide's legacy `validity` values are `正常` (normal) and `需核对` (requires checking). They are preserved enum values, not English prose to translate. Choose one, not the pipe-separated list. Preserve any supplied canonical domain/tag values as well.

```json
{
  "validity": "正常 | 需核对",
  "quality_flags": [],
  "prerequisite_level": "Grade 6 / secondary-school entry",
  "primary_domain": "Number theory",
  "secondary_domains": ["Counting and combinatorics"],
  "knowledge_points": ["Divisibility and congruence", "Place-value properties"],
  "solution_hook": "One sentence identifying the natural solution's entry point",
  "dimension_scores": {
    "knowledge_span": 2,
    "modeling": 1,
    "insight": 2,
    "reasoning_chain": 2,
    "case_analysis": 1,
    "calculation_load": 1
  },
  "total_score": 9,
  "difficulty_star": 4,
  "difficulty_reason": "Explain why this star level, rather than either neighboring level, fits",
  "premium": true,
  "premium_score": 7,
  "premium_reasons": ["Invariant", "Integrated modeling", "Transferable method"],
  "main_pitfall": "The most likely error",
  "comment": "A concise overall comment of 60 to 120 characters",
  "confidence": 0.90
}
```

This example illustrates classification only. For generation, use the platform candidate schema, not a root object based on `validity`, `quality_flags`, or `primary_domain`. For review, follow the reviewer's required output contract.

## 10. Compact reviewer instruction

Act as a mathematics competition question reviewer. First check completeness. Score knowledge span, modeling, key insight, reasoning chain, case analysis, and calculation load from 0 to 3 each. Map totals as 0-2=1 star, 3-5=2 stars, 6-8=3 stars, 9-11=4 stars, and 12-18=5 stars. Pure computation normally merits at most 3 stars. Five stars require both insight and reasoning chain >= 2 and at least three dimensions >= 2. A damaged statement is not difficulty. Independently score premium quality: innovation 0-3, elegance 0-3, integration 0-2, transfer value 0-2. Require at least 6 total, elegance at least 2, innovation at least 1, and a fair, complete question, with all additional gates and vetoes above. Return the role's required JSON with a solution hook, pitfall, star rationale, premium rationale, and confidence. Never confuse difficulty with premium quality.
