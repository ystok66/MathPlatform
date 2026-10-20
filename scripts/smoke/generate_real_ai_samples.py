from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from apps.api.app.ai_gateway import build_messages, existing_generation_duplicate, process_generation_job, retrieve_examples, review_candidate
from apps.api.app.ai_providers import OpenAICompatibleProvider, ProviderConfig
from apps.api.app.config import get_settings
from apps.api.app.db import get_engine
from apps.api.app.importer import map_question_v2
from apps.api.app.models import GeneratedCandidate, GenerationJob, Question, QuestionVersion
from apps.api.app.secrets import get_secret


def summarize_candidate(row: GeneratedCandidate) -> dict:
    question = (row.payload or {}).get("question") or {}
    review = row.review or {}
    return {
        "candidate_id": row.id,
        "strategy": row.strategy,
        "status": row.status,
        "score": row.score,
        "stem_md": question.get("stem_md"),
        "difficulty": question.get("difficulty"),
        "review_decision": review.get("decision"),
        "issues": review.get("issues"),
        "not_selected_reason": review.get("not_selected_reason"),
        "duplicate": review.get("duplicate"),
    }


def summarize_question(db: Session, question_id: str | None) -> dict | None:
    if not question_id:
        return None
    question = db.get(Question, question_id)
    if question is None:
        return None
    version = db.get(QuestionVersion, question.current_version_id) if question.current_version_id else None
    qv2 = version.question_v2 if version else {}
    return {
        "id": question.id,
        "difficulty_star": question.difficulty_star,
        "publish_status": question.publish_status,
        "eligible_for_practice": question.eligible_for_practice,
        "stem_md": ((qv2.get("content") or {}).get("stem_md") or question.stem_preview),
        "answer_spec": qv2.get("answer_spec"),
        "solution": qv2.get("solution"),
        "evaluation": qv2.get("evaluation"),
    }


def generate_one(db: Session, star: int, provider: str, target_tags: list[str], constraints: str) -> dict:
    request = {
        "target_tags": target_tags,
        "difficulty_star": star,
        "count": 1,
        "constraints": constraints,
        "solution_style": "elementary_preferred",
    }
    job = GenerationJob(status="queued", request=request, progress={})
    db.add(job)
    db.commit()
    progress = process_generation_job(db, job.id, provider=provider)
    db.refresh(job)
    candidates = db.query(GeneratedCandidate).filter(GeneratedCandidate.job_id == job.id).order_by(GeneratedCandidate.strategy, GeneratedCandidate.id).all()
    return {
        "requested_star": star,
        "job_id": job.id,
        "job_status": job.status,
        "progress": progress,
        "accepted_question": summarize_question(db, job.accepted_question_id),
        "candidates": [summarize_candidate(row) for row in candidates],
    }


def direct_generate_one(db: Session, star: int, target_tags: list[str], constraints: str, timeout_seconds: int, model_override: str | None = None) -> dict:
    settings = get_settings()
    api_key = get_secret(db, "LIAOBOTS_API_KEY") or settings.liaobots_api_key
    model = model_override or settings.generator_model or settings.default_model
    request = {
        "target_tags": target_tags,
        "difficulty_star": star,
        "count": 1,
        "constraints": constraints,
        "solution_style": "elementary_preferred",
    }
    examples = retrieve_examples(db, request)
    provider = OpenAICompatibleProvider(
        ProviderConfig(
            name="openai_compatible",
            base_url=settings.liaobots_base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            max_retries=0,
        )
    )
    messages = build_messages("structural_elegance", request, examples)
    result = provider.run_json(messages, "generator", model, {})
    payload = result.payload or {}
    review = review_candidate(payload) if payload.get("status") == "ok" else None
    duplicate = None
    if payload.get("status") == "ok" and payload.get("question"):
        question_payload = payload["question"]
        raw_question = {
            "id": "__direct_sample__",
            "question_text": question_payload["stem_md"],
            "answer_spec": {
                "status": "verified",
                "answer_type": question_payload.get("answer_type", "integer"),
                "canonical_answer": question_payload["answer_spec"]["canonical"][0],
                "acceptable_answers": question_payload["answer_spec"].get("accepted_forms", []),
                "tolerance": {"type": "exact", "value": 0},
                "unit": question_payload["answer_spec"].get("unit"),
                "order_sensitive": question_payload["answer_spec"].get("order_sensitive", True),
                "grading_method": "numeric_exact",
                "verification": payload.get("verification_spec") or {},
            },
            "difficulty_quality_evaluation": {
                "difficulty_star": question_payload.get("difficulty", {}).get("star", star),
                "premium": question_payload.get("premium", {}).get("is_premium", False),
            },
            "primary_path": {},
            "method_tags": [],
            "task_tags": [],
            "structure_tags": [],
            "constraint_tags": [],
        }
        duplicate = existing_generation_duplicate(db, "__direct_sample__", raw_question["question_text"], map_question_v2(raw_question, "AI生成题"))
    question = payload.get("question") or {}
    response_preview = ""
    try:
        response_preview = (((result.raw_response.get("choices") or [{}])[0].get("message") or {}).get("content") or "")[:1200]
    except Exception:
        response_preview = ""
    return {
        "requested_star": star,
        "provider": result.provider,
        "model": result.model,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "response_preview": response_preview,
        "token_usage": result.token_usage,
        "retrieved_example_ids": [example.id for example in examples],
        "candidate_status": payload.get("status"),
        "reject_reason": payload.get("reject_reason"),
        "stem_md": question.get("stem_md"),
        "answer_type": question.get("answer_type"),
        "answer_spec": question.get("answer_spec"),
        "solution": question.get("solution"),
        "difficulty": question.get("difficulty"),
        "premium": question.get("premium"),
        "local_review": review,
        "duplicate": duplicate,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stars", nargs="+", type=int, default=[3, 4, 5])
    parser.add_argument("--mode", choices=["direct", "pipeline"], default="direct")
    parser.add_argument("--provider", default="openai_compatible")
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--model", default=None)
    parser.add_argument("--target-tags", default="数论,组合,不变量")
    parser.add_argument(
        "--constraints",
        default=(
            "生成可自动判分的小学高年级/小升初竞赛题。必须避免两位数字和差、"
            "三份总量差量抵消、长方形半周长和差、简单盈亏一行算式等模板。"
            "题目必须和范本至少在任务形式、条件组织、关键方法、故事领域、答案类型中三项不同。"
        ),
    )
    args = parser.parse_args()
    settings = get_settings()
    print(json.dumps({"provider": args.provider, "base_url": settings.liaobots_base_url, "default_model": settings.default_model, "has_api_key": bool(settings.liaobots_api_key)}, ensure_ascii=False))
    if args.provider != "mock" and not settings.liaobots_api_key:
        print(json.dumps({"error": "missing LIAOBOTS_API_KEY"}, ensure_ascii=False))
        return 2
    target_tags = [item.strip() for item in args.target_tags.split(",") if item.strip()]
    with Session(get_engine()) as db:
        for star in args.stars:
            if args.mode == "pipeline":
                payload = generate_one(db, star, args.provider, target_tags, args.constraints)
            else:
                payload = direct_generate_one(db, star, target_tags, args.constraints, args.timeout_seconds, args.model)
            print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
