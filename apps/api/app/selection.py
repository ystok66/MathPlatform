from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .models import DEFAULT_LEARNER_ID, AnswerSpec, PracticeSession, Question, QuestionTag, Submission


STRICT_HIGH_INTENSITY_QUOTA = {1: 10, 2: 20, 3: 40, 4: 20, 5: 10}
STANDARD_DIAGNOSTIC_QUOTA = {1: 10, 2: 25, 3: 40, 4: 20, 5: 5}
STRICT_TEST_STRUCTURED_ANSWER_TYPES = (
    "integer",
    "number",
    "fraction",
    "rational",
    "decimal",
    "multi_blank",
    "ordered_tuple",
    "set",
    "choice",
)


@dataclass(frozen=True)
class SelectionPolicy:
    learner_id: str = DEFAULT_LEARNER_ID
    mode: str = "practice"
    count: int = 10
    template_name: str | None = None
    source_names: tuple[str, ...] = ()
    difficulty_stars: tuple[int, ...] = ()
    star_quota: dict[int, int] | None = None
    tag_ids: tuple[str, ...] = ()
    verified_only: bool = True
    include_generated: bool = False
    strict_source_mode: str = "bank_only"
    exclude_done: bool = False
    exclude_recent_question_ids: tuple[str, ...] = ()
    recent_exposure_count: int = 0
    exclude_family_ids: tuple[str, ...] = ()


def quota_for_policy(policy: SelectionPolicy) -> dict[int, int] | None:
    if policy.star_quota:
        quota: dict[int, int] = {}
        for raw_star, raw_count in policy.star_quota.items():
            try:
                star = int(raw_star)
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if 1 <= star <= 5 and count > 0:
                quota[star] = count
        if quota:
            return quota
    if policy.template_name == "strict_100_high_intensity" or (policy.mode == "strict_test" and policy.count == 100):
        return STRICT_HIGH_INTENSITY_QUOTA.copy()
    if policy.template_name == "standard_diagnostic_100":
        return STANDARD_DIAGNOSTIC_QUOTA.copy()
    return None


def recent_question_ids(db: Session, count: int, learner_id: str | None = None) -> tuple[str, ...]:
    if count <= 0:
        return ()
    stmt = select(Submission.question_id).order_by(Submission.created_at.desc(), Submission.id.desc()).limit(count)
    if learner_id:
        stmt = stmt.join(PracticeSession, PracticeSession.id == Submission.session_id).where(PracticeSession.learner_id == learner_id)
    rows = db.scalars(stmt).all()
    seen: list[str] = []
    for question_id in rows:
        if question_id not in seen:
            seen.append(question_id)
    return tuple(seen)


def done_question_timestamps(db: Session, learner_id: str | None = None) -> dict[str, str]:
    stmt = select(Submission.question_id, Submission.created_at).order_by(Submission.created_at.desc(), Submission.id.desc())
    if learner_id:
        stmt = stmt.join(PracticeSession, PracticeSession.id == Submission.session_id).where(PracticeSession.learner_id == learner_id)
    rows = db.execute(stmt).all()
    latest: dict[str, str] = {}
    for question_id, created_at in rows:
        if question_id and question_id not in latest:
            latest[question_id] = created_at.isoformat()
    return latest


def build_candidate_query(policy: SelectionPolicy, effective_exclude_recent_question_ids: tuple[str, ...] | None = None):
    stmt = select(Question).join(AnswerSpec, AnswerSpec.question_id == Question.id)
    excluded_recent = effective_exclude_recent_question_ids if effective_exclude_recent_question_ids is not None else policy.exclude_recent_question_ids
    stmt = stmt.where(Question.question_fault_status == "none")
    stmt = stmt.where(Question.status == "verified", AnswerSpec.status == "verified")
    if policy.mode == "strict_test":
        stmt = stmt.where(AnswerSpec.answer_type.in_(STRICT_TEST_STRUCTURED_ANSWER_TYPES))
        source_mode = policy.strict_source_mode if policy.strict_source_mode in {"bank_only", "ai_mixed", "ai_only"} else "bank_only"
        bank_strict = and_(
            Question.source_name != "AI生成题",
            Question.eligible_for_test.is_(True),
            Question.publish_status.in_(("active_test", "source_verified")),
        )
        ai_verified = and_(
            Question.source_name == "AI生成题",
            Question.eligible_for_practice.is_(True),
            Question.publish_status.in_(("active_practice", "active_test", "source_verified")),
        )
        if source_mode == "ai_only":
            stmt = stmt.where(ai_verified)
        elif source_mode == "ai_mixed":
            stmt = stmt.where(or_(bank_strict, ai_verified))
        else:
            stmt = stmt.where(bank_strict)
    else:
        stmt = stmt.where(Question.eligible_for_practice.is_(True), Question.publish_status.not_in(("intervention_only", "quarantined", "retired", "duplicate_alias")))
    if policy.source_names:
        stmt = stmt.where(Question.source_name.in_(policy.source_names))
    if policy.difficulty_stars:
        stmt = stmt.where(Question.difficulty_star.in_(policy.difficulty_stars))
    if policy.tag_ids:
        stmt = stmt.join(QuestionTag, QuestionTag.question_id == Question.id).where(QuestionTag.tag_id.in_(policy.tag_ids))
    if policy.mode != "strict_test" and not policy.include_generated:
        stmt = stmt.where(Question.source_name != "AI生成题")
    if excluded_recent:
        stmt = stmt.where(Question.id.not_in(excluded_recent))
    if policy.exclude_family_ids:
        stmt = stmt.where(Question.family_id.not_in(policy.exclude_family_ids))
    return stmt.order_by(Question.source_name, Question.id)


def question_tags(db: Session, question_ids: list[str]) -> dict[str, set[str]]:
    if not question_ids:
        return {}
    rows = db.execute(select(QuestionTag.question_id, QuestionTag.tag_id).where(QuestionTag.question_id.in_(question_ids))).all()
    tags: dict[str, set[str]] = defaultdict(set)
    for question_id, tag_id in rows:
        tags[question_id].add(tag_id)
    return tags


def diversity_score(
    question: Question,
    selected_sources: dict[str, int],
    seen_families: set[str],
    seen_tags: set[str],
    tags_by_question: dict[str, set[str]],
) -> tuple[int, int, int, str]:
    family_penalty = 1000 if question.family_id and question.family_id in seen_families else 0
    new_tag_bonus = -len(tags_by_question.get(question.id, set()) - seen_tags)
    source_penalty = selected_sources[question.source_name]
    return (family_penalty, new_tag_bonus, source_penalty, question.id)


def pick_from_pool(
    pool: list[Question],
    needed: int,
    selected: list[Question],
    seen_ids: set[str],
    seen_families: set[str],
    seen_tags: set[str],
    tags_by_question: dict[str, set[str]],
) -> None:
    selected_sources: dict[str, int] = defaultdict(int)
    for question in selected:
        selected_sources[question.source_name] += 1
    while len([q for q in selected if q in pool]) < needed and pool:
        candidates = [q for q in pool if q.id not in seen_ids]
        if not candidates:
            break
        candidates.sort(key=lambda q: diversity_score(q, selected_sources, seen_families, seen_tags, tags_by_question))
        choice = candidates[0]
        selected.append(choice)
        seen_ids.add(choice.id)
        if choice.family_id:
            seen_families.add(choice.family_id)
        seen_tags.update(tags_by_question.get(choice.id, set()))
        selected_sources[choice.source_name] += 1


def select_questions_for_session(db: Session, policy: SelectionPolicy) -> tuple[list[str], dict[str, Any]]:
    quota = quota_for_policy(policy)
    auto_recent_ids = recent_question_ids(db, policy.recent_exposure_count, policy.learner_id)
    done_timestamps = done_question_timestamps(db, policy.learner_id) if policy.exclude_done else {}
    pre_done_exclude_ids = tuple(dict.fromkeys((*policy.exclude_recent_question_ids, *auto_recent_ids)))
    effective_exclude_recent_question_ids = tuple(dict.fromkeys((*pre_done_exclude_ids, *done_timestamps.keys())))
    pre_done_pool = list(db.scalars(build_candidate_query(policy, pre_done_exclude_ids)).unique()) if policy.exclude_done else []
    pool = list(db.scalars(build_candidate_query(policy, effective_exclude_recent_question_ids)).unique())
    tags_by_question = question_tags(db, [question.id for question in pool])
    by_star: dict[int, list[Question]] = defaultdict(list)
    for question in pool:
        if question.difficulty_star is not None:
            by_star[int(question.difficulty_star)].append(question)

    selected: list[Question] = []
    seen_ids: set[str] = set()
    seen_families: set[str] = set()
    seen_tags: set[str] = set()
    if quota:
        for star, needed in quota.items():
            star_pool = by_star.get(star, [])
            local_selected_before = len(selected)
            pick_from_pool(star_pool, needed, selected, seen_ids, seen_families, seen_tags, tags_by_question)
            picked = len(selected) - local_selected_before
            if picked < needed:
                fallback = [q for q in pool if q.difficulty_star == star and q.id not in seen_ids]
                for question in fallback[: needed - picked]:
                    selected.append(question)
                    seen_ids.add(question.id)
    else:
        pick_from_pool(pool, policy.count, selected, seen_ids, seen_families, seen_tags, tags_by_question)

    if len(selected) < policy.count:
        for question in pool:
            if question.id not in seen_ids:
                selected.append(question)
                seen_ids.add(question.id)
            if len(selected) >= policy.count:
                break

    selected = selected[: policy.count]
    selected_quota = defaultdict(int)
    selected_sources = defaultdict(int)
    selected_families = set()
    for question in selected:
        selected_quota[int(question.difficulty_star or 0)] += 1
        selected_sources[question.source_name] += 1
        if question.family_id:
            selected_families.add(question.family_id)
    excluded_done_count = len([question for question in pre_done_pool if question.id in done_timestamps])
    warnings: list[str] = []
    if len(selected) != policy.count:
        warnings.append(f"only_selected_{len(selected)}")
    if policy.exclude_done and excluded_done_count:
        warnings.append(f"excluded_done_{excluded_done_count}")
    if policy.exclude_done and pre_done_pool and excluded_done_count == len(pre_done_pool) and not pool:
        warnings.append(f"all_{policy.strict_source_mode}_questions_done")

    metadata = {
        "policy": {
            "mode": policy.mode,
            "learner_id": policy.learner_id,
            "template_name": policy.template_name,
            "count": policy.count,
            "verified_only": True,
            "include_generated": policy.include_generated or policy.strict_source_mode in {"ai_mixed", "ai_only"},
            "strict_source_mode": policy.strict_source_mode,
            "exclude_done": policy.exclude_done,
            "answer_type_policy": "strict_structured_auto_gradable",
            "allowed_answer_types": list(STRICT_TEST_STRUCTURED_ANSWER_TYPES),
        },
        "target_quota": quota or {},
        "actual_quota": dict(selected_quota),
        "missing_quota": {star: max(0, needed - selected_quota.get(star, 0)) for star, needed in (quota or {}).items()},
        "source_counts": dict(selected_sources),
        "family_unique_count": len(selected_families),
        "tag_unique_count": len({tag_id for question in selected for tag_id in tags_by_question.get(question.id, set())}),
        "auto_excluded_recent_question_ids": list(auto_recent_ids),
        "effective_excluded_recent_question_ids": list(effective_exclude_recent_question_ids),
        "excluded_done_question_count": excluded_done_count,
        "excluded_done_question_timestamps": {question_id: done_timestamps[question_id] for question_id in list(done_timestamps)[:100]},
        "breakpoints": [40, 80] if policy.mode == "strict_test" and policy.count >= 100 else [],
        "strict_no_ai": policy.mode == "strict_test",
        "selection_warnings": warnings,
    }
    return [q.id for q in selected], metadata
