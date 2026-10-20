from .localization import LanguageRequest
from typing import Any, Literal

from pydantic import BaseModel, Field


class Tolerance(BaseModel):
    type: Literal["exact", "absolute"] = "exact"
    value: float = 0


class UnifiedAnswerSpec(BaseModel):
    schema_version: Literal["answer_spec_v1"] = "answer_spec_v1"
    status: Literal["verified", "needs_review"]
    answer_type: str
    canonical_answer: str
    acceptable_answers: list[str] = Field(default_factory=list)
    unit: str | None = None
    tolerance: Tolerance = Field(default_factory=Tolerance)
    blanks: list[dict[str, Any]] = Field(default_factory=list)
    order_sensitive: bool = True
    grading_method: str = "exact_normalized"
    verification: dict[str, Any] = Field(default_factory=dict)


class QuestionListItem(BaseModel):
    id: str
    source_name: str
    section: str | None
    stem_preview: str
    difficulty_star: int | None
    premium: bool
    status: str
    publish_status: str = "source_verified"
    eligible_for_practice: bool = True
    eligible_for_test: bool = True
    answer_status: str | None = None
    last_answered_at: str | None = None


class QuestionDetail(BaseModel):
    id: str
    source_name: str
    section: str | None
    original_number: str | None
    question_v2: dict[str, Any]
    answer_spec: dict[str, Any] | None
    tags: list[dict[str, str]]


class TaxonomyTagItem(BaseModel):
    id: str
    kind: str
    label: str
    status: str = "active"
    version: str = "taxonomy_v1"
    question_count: int = 0
    description: str | None = None


class ImportResult(BaseModel):
    files: int
    questions: int
    verified: int
    needs_review: int


class CreatePracticeSessionRequest(LanguageRequest):
    learner_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)
    mode: str = "practice"
    count: int = Field(default=10, ge=1, le=100)
    template_name: str | None = None
    source_names: list[str] | None = None
    difficulty_stars: list[int] | None = None
    star_quota: dict[int, int] | None = None
    tag_ids: list[str] | None = None
    verified_only: bool = True
    include_generated: bool = False
    strict_source_mode: Literal["bank_only", "ai_mixed", "ai_only"] = "bank_only"
    fill_missing_with_ai: bool = False
    ai_candidate_count: int = Field(default=3, ge=1, le=5)
    ai_source_example_count: int = Field(default=5, ge=0, le=8)
    ai_negative_example_count: int = Field(default=3, ge=0, le=5)
    allow_reviewer_quality_downgrade: bool = False
    exclude_done: bool | None = None
    exclude_recent_question_ids: list[str] = Field(default_factory=list)
    recent_exposure_count: int = Field(default=0, ge=0, le=500)
    exclude_family_ids: list[str] = Field(default_factory=list)


class CreatePracticeSessionResponse(BaseModel):
    id: str
    learner_id: str = "default-learner"
    mode: str | None = None
    created_at: str | None = None
    display_name: str | None = None
    question_ids: list[str]
    config: dict[str, Any] = Field(default_factory=dict)


class SubmitAnswerRequest(BaseModel):
    question_id: str
    answer_text: str
    thought_text: str | None = None
    elapsed_ms: int = 0
    idempotency_key: str | None = Field(default=None, max_length=128)
    expected_state_version: int | None = Field(default=None, ge=0)


class SubmitAnswerResponse(BaseModel):
    submission_id: str
    is_correct: bool | None
    result: dict[str, Any]
    attempt_index: int = 1
    effective_attempt_index: int = 1
    effective_error_count: int = 0
    duplicate_answer: bool = False
    duplicate_of_submission_id: str | None = None
    is_first_attempt: bool = True
    is_final_attempt: bool = True
    state_version: int | None = None


class AnswerJudgeRequest(LanguageRequest):
    question_id: str
    answer_text: str
    thought_text: str | None = None
    submission_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)
    expected_state_version: int | None = Field(default=None, ge=0)


class AnswerJudgeResponse(BaseModel):
    question_id: str
    deterministic_result: dict[str, Any]
    verdict: Literal["correct", "incorrect", "uncertain"]
    is_correct: bool | None
    confidence: float = 0
    analysis: str = ""
    student_feedback: str = ""
    normalized_student_answer: str | None = None
    missing_parts: list[str] = Field(default_factory=list)
    extra_parts: list[str] = Field(default_factory=list)
    format_issue: str | None = None
    should_override_deterministic: bool = False
    override_reason: str | None = None
    provider: str
    model: str
    latency_ms: int | None = None
    error: str | None = None
    evaluation_snapshot: dict[str, Any] | None = None
    state_version: int | None = None


class PracticeEventRequest(BaseModel):
    question_id: str = ""
    event_type: str
    at_ms: int = Field(default=0, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)
    expected_state_version: int | None = Field(default=None, ge=0)


class PracticeFeedbackRequest(BaseModel):
    question_id: str = ""
    feedback_type: Literal["confusing", "suspected_error", "too_hard", "too_easy", "ui_issue", "other"] = "other"
    message: str = Field(min_length=1, max_length=2000)
    at_ms: int = Field(default=0, ge=0)


class QuestionSupportRequest(LanguageRequest):
    question_id: str
    concern_type: Literal["format_issue", "do_not_understand", "suspected_error", "other"] = "format_issue"
    message: str = Field(min_length=1, max_length=1000)
    at_ms: int = Field(default=0, ge=0)


class QuestionSupportResponse(BaseModel):
    status: Literal["ok", "error"] = "ok"
    question_problem: bool = False
    issue_type: str | None = None
    safe_response: str = ""
    confidence: float = 0
    replacement_question_id: str | None = None
    replaced: bool = False
    provider: str = "mock"
    model: str = "mock"
    latency_ms: int | None = None
    error: str | None = None


class SolutionExplanationRequest(LanguageRequest):
    question_id: str
    message: str = Field(min_length=1, max_length=1200)
    at_ms: int = Field(default=0, ge=0)


class SolutionExplanationResponse(BaseModel):
    status: Literal["ok", "refused", "error"] = "ok"
    explanation: str = ""
    key_points: list[str] = Field(default_factory=list)
    common_misunderstandings: list[str] = Field(default_factory=list)
    next_review_suggestion: str = ""
    refused: bool = False
    refusal_reason: str | None = None
    provider: str = "mock"
    model: str = "mock"
    latency_ms: int | None = None
    error: str | None = None


class PracticeTimingResponse(BaseModel):
    session_id: str
    timing: dict[str, Any]


class InterventionActionRequest(BaseModel):
    action: Literal["bridge", "return_original", "complete"]
    success: bool | None = None
    at_ms: int = Field(default=0, ge=0)


class BreakConfirmRequest(BaseModel):
    checkpoint_index: int = Field(ge=1)
    at_ms: int = Field(default=0, ge=0)


class PracticeSessionStateResponse(BaseModel):
    id: str
    learner_id: str = "default-learner"
    mode: str
    status: str
    created_at: str | None = None
    display_name: str | None = None
    question_ids: list[str]
    answered_count: int
    current_index: int
    next_breakpoint: int | None = None
    breakpoints: list[int] = Field(default_factory=list)
    break_confirmed: list[int] = Field(default_factory=list)
    strict_no_ai: bool = False
    skipped_question_ids: list[str] = Field(default_factory=list)
    bookmarked_question_ids: list[str] = Field(default_factory=list)
    uncertain_question_ids: list[str] = Field(default_factory=list)
    solution_revealed_question_ids: list[str] = Field(default_factory=list)
    effective_error_counts: dict[str, int] = Field(default_factory=dict)
    intervention_due_question_ids: list[str] = Field(default_factory=list)
    active_interventions: list[dict[str, Any]] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class GenerationJobRequest(LanguageRequest):
    target_tags: list[str] = Field(default_factory=list)
    difficulty_star: int | None = Field(default=None, ge=1, le=5)
    count: int = Field(default=1, ge=1, le=20)
    constraints: str | None = None
    source_session_id: str | None = None
    solution_style: Literal["elementary_preferred", "elementary_required", "unrestricted"] = "elementary_preferred"
    use_mock: bool = False
    prefer_reuse_existing_ai: bool = True
    candidate_count: int = Field(default=3, ge=1, le=5)
    source_example_count: int = Field(default=5, ge=0, le=8)
    ai_negative_example_count: int = Field(default=3, ge=0, le=5)
    allow_budget_overrun: bool = False
    allow_reviewer_quality_downgrade: bool = False


class GenerationJobResponse(BaseModel):
    id: str
    status: str
    accepted_question_id: str | None = None
    progress: dict[str, Any] = Field(default_factory=dict)


class GenerationBatchRequest(LanguageRequest):
    target_tags: list[str] = Field(default_factory=list)
    difficulty_star: int | None = Field(default=None, ge=1, le=5)
    count: int = Field(default=5, ge=1, le=50)
    concurrency: int = Field(default=2, ge=1, le=8)
    constraints: str | None = None
    custom_description: str | None = None
    source_session_id: str | None = None
    solution_style: Literal["elementary_preferred", "elementary_required", "unrestricted"] = "elementary_preferred"
    use_mock: bool = False
    prefer_reuse_existing_ai: bool = True
    candidate_count: int = Field(default=3, ge=1, le=5)
    source_example_count: int = Field(default=5, ge=0, le=8)
    ai_negative_example_count: int = Field(default=3, ge=0, le=5)
    allow_budget_overrun: bool = False
    allow_reviewer_quality_downgrade: bool = False
    allow_parallel_batches: bool = False


class GenerationBatchPracticeRequest(LanguageRequest):
    session_id: str | None = None
    learner_id: str | None = None


class GenerationBatchResponse(BaseModel):
    id: str
    display_name: str | None = None
    status: str
    target_count: int
    concurrency: int = 1
    accepted_count: int = 0
    rejected_count: int = 0
    failed_count: int = 0
    eta_seconds: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    progress: dict[str, Any] = Field(default_factory=dict)
    request: dict[str, Any] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(default_factory=list)


class ModelSettings(BaseModel):
    provider: str = "openai_compatible"
    base_url: str
    api_key: str | None = None
    has_api_key: bool = False
    default_model: str
    roles: dict[str, str] = Field(default_factory=dict)
    star_generator_models: dict[str, str] = Field(default_factory=dict)
    model_price_json: str = ""
    strict_reviewer_model_allowlist: str = ""


class ModelDiscoveryResponse(BaseModel):
    models: list[str] = Field(default_factory=list)
    source: str
    capabilities: dict[str, dict[str, Any]] = Field(default_factory=dict)
    error: str | None = None


class ModelTestResponse(BaseModel):
    success: bool
    models_ok: bool = False
    chat_ok: bool = False
    source: str
    model: str
    elapsed_ms: int
    models: list[str] = Field(default_factory=list)
    response_preview: str | None = None
    error: str | None = None


class QualityFixedCaseRunRequest(LanguageRequest):
    use_mock: bool = True
    provider: str | None = None
    suites: list[str] = Field(default_factory=list)
    case_ids: list[str] = Field(default_factory=list)
    max_cases: int = Field(default=50, ge=1, le=200)


class ClearHistoryRequest(BaseModel):
    command: str | None = None
    challenge_id: str | None = None
    challenge_phrase: str | None = None


class ClearHistoryResponse(BaseModel):
    status: str
    deleted_counts: dict[str, int]
    preserved: list[str] = Field(default_factory=list)


class HistoryTransfer(BaseModel):
    schema_version: Literal["history_transfer_v1"] = "history_transfer_v1"
    exported_at: str
    filters: dict[str, Any] = Field(default_factory=dict)
    table_counts: dict[str, int] = Field(default_factory=dict)
    tables: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class HistoryImportResponse(BaseModel):
    status: str
    imported_counts: dict[str, int]
    skipped_tables: list[str] = Field(default_factory=list)
    skipped_rows: dict[str, int] = Field(default_factory=dict)
    conflict_report: list[dict[str, Any]] = Field(default_factory=list)
    dry_run: bool = False


class QuestionBankTransfer(BaseModel):
    schema_version: Literal["question_bank_transfer_v1"] = "question_bank_transfer_v1"
    exported_at: str
    filters: dict[str, Any] = Field(default_factory=dict)
    table_counts: dict[str, int] = Field(default_factory=dict)
    tables: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class QuestionBankImportResponse(BaseModel):
    status: str
    imported_counts: dict[str, int]
    skipped_tables: list[str] = Field(default_factory=list)
    skipped_rows: dict[str, int] = Field(default_factory=dict)
    conflict_report: list[dict[str, Any]] = Field(default_factory=list)
    dry_run: bool = False


class QuestionDeleteRequest(BaseModel):
    command: str | None = None
    challenge_id: str | None = None
    challenge_phrase: str | None = None
    question_ids: list[str] = Field(min_length=1, max_length=500)


class QuestionDeleteResponse(BaseModel):
    status: str
    question_ids: list[str]
    deleted_counts: dict[str, int]
    updated_counts: dict[str, int] = Field(default_factory=dict)


class LearnerDeleteRequest(BaseModel):
    challenge_id: str | None = None
    challenge_phrase: str | None = None


class LearnerDeleteResponse(BaseModel):
    status: str
    learner_id: str
    deleted_counts: dict[str, int]
    preserved: list[str] = Field(default_factory=list)
    skipped: dict[str, Any] = Field(default_factory=dict)


class DestructiveChallengeRequest(BaseModel):
    action: Literal["clear_history", "retire_questions", "delete_learner"]
    learner_id: str | None = None
    question_ids: list[str] = Field(default_factory=list, max_length=500)
    target_count: int | None = Field(default=None, ge=0)


class DestructiveChallengeResponse(BaseModel):
    challenge_id: str
    action: Literal["clear_history", "retire_questions", "delete_learner"]
    phrase: str
    expires_at: str
    target_count: int
    target_fingerprint: str
