"use client";

import { useI18n } from "./i18n/provider";
import { formatUi, getLanguage, getOutputLanguage, languageFetch, localizeUi, translateUi, type Language } from "./i18n/language";
import { displayAutoTitle } from "./i18n/domain-catalog";
import { DEFAULT_GENERATION_CONSTRAINTS, REVIEW_SCHEDULE_LABELS, SYSTEM_TASK_TITLES } from "./i18n/system-catalog";
import { LanguageEventSource } from "./i18n/events";

import { CSSProperties, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { synchronizeStartupReads } from "./startup-sync";
import * as echarts from "echarts";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  BookOpen,
  Bookmark,
  BrainCircuit,
  CheckCircle2,
  Clipboard,
  Clock3,
  Contrast,
  Database,
  Download,
  Flag,
  HelpCircle,
  KeyRound,
  PauseCircle,
  Play,
  RefreshCcw,
  Search,
  Send,
  Settings,
  ShieldCheck,
  SkipForward,
  Tags,
  Trash2,
  Type,
  UserRound,
  Upload,
  X,
} from "lucide-react";
import type { StatusCatalog } from "../../../packages/schemas/status_catalog";
import { MathContent } from "./components/MathContent";
import { ApiSetupDialog } from "./components/ApiSetupDialog";

type Workspace = "practice" | "generated" | "history" | "bank" | "reports" | "guide" | "admin";
type AppMode = "learner" | "admin";
type OperationState = "idle" | "loading" | "success" | "error";
type StrictSourceMode = "bank_only" | "ai_mixed" | "ai_only";

type OperationStatus = {
  state: OperationState;
  label: string;
  traceId?: string;
  errorCode?: string;
  ui?: { key: string; values: readonly unknown[]; uiSlots: readonly number[] };
};

type ErrorEnvelope = {
  detail?: unknown;
  error_code?: string;
  user_message?: string;
  action?: string;
  retryable?: boolean;
  retry_after_seconds?: number | null;
  trace_id?: string;
  details_safe?: Record<string, unknown>;
};

type ErrorCatalogItem = {
  error_code: string;
  user_message: string;
  action: string;
  retryable: boolean;
  retry_after_seconds: number | null;
  source: string;
  match_key: string | null;
  http_status: number | null;
};

type ErrorCatalog = {
  schema_version: string;
  common_count: number;
  default_count: number;
  items: ErrorCatalogItem[];
};

type HealthDetail = {
  schema_version: string;
  generated_at: string;
  overall: {
    status: string;
    ok: boolean;
    degraded: boolean;
  };
  checks: Record<string, {
    status: string;
    ok: boolean;
    [key: string]: unknown;
  }>;
};

type ApiConnectionState = {
  state: "checking" | "online" | "starting" | "offline";
  label: string;
  detail: string;
  last_checked_at?: string;
  latency_ms?: number | null;
  http_status?: number | null;
  retry_count: number;
};

type RuntimeConfig = {
  schema_version: string;
  generated_at: string;
  summary: {
    setting_count: number;
    configured_count: number;
    secret_configured_count: number;
    category_counts: Record<string, number>;
  };
  settings: {
    name: string;
    env_key: string | null;
    category: string;
    source: string;
    redacted: boolean;
    configured: boolean;
    default: unknown;
    value: unknown;
  }[];
  feature_flags: Record<string, { enabled: boolean; description: string }>;
};

class ApiRequestError extends Error {
  status: number;
  envelope: ErrorEnvelope | null;

  constructor(status: number, message: string, envelope: ErrorEnvelope | null) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.envelope = envelope;
  }
}

type GenerationPreset = "fast" | "balanced" | "strict";

const GENERATION_PRESETS: Record<GenerationPreset, { label: string; candidate_count: string; source_example_count: string; ai_negative_example_count: string; description: string }> = {
  fast: { label: "快速", candidate_count: "1", source_example_count: "3", ai_negative_example_count: "1", description: "少调用，适合试跑流程。" },
  balanced: { label: "均衡", candidate_count: "3", source_example_count: "5", ai_negative_example_count: "3", description: "默认质量与耗时平衡。" },
  strict: { label: "严格", candidate_count: "5", source_example_count: "8", ai_negative_example_count: "5", description: "更多候选和参考，耗时与费用更高。" },
};

type Question = {
  id: string;
  source_name: string;
  section: string | null;
  stem_preview: string;
  difficulty_star: number | null;
  premium: boolean;
  status: string;
  publish_status: string;
  eligible_for_practice: boolean;
  eligible_for_test: boolean;
  answer_status: string | null;
  last_answered_at?: string | null;
};

type QuestionFilterState = {
  q: string;
  source_name: string;
  difficulty_star: string;
  premium: string;
  status: string;
};

type SavedQuestionFilterPreset = {
  schema_version: "question_filter_preset_v1";
  id: string;
  name: string;
  filters: QuestionFilterState;
  tag_ids: string[];
  tag_labels: string[];
  created_at: string;
  updated_at: string;
};

type LearnerProfile = {
  id: string;
  name: string;
  status: string;
  display_order: number;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  is_default: boolean;
};

type MetricDefinition = {
  label: string;
  numerator: number | null;
  denominator: number | null;
  sample_size: number;
  confidence: string;
  basis: string;
  exclusions?: string[];
  updated_at?: string | null;
  filters?: Record<string, unknown>;
};

type HistoryRecalculation = {
  schema_version?: string;
  status: string;
  recommended: boolean;
  reason: string;
  strategy?: string;
  mode?: string;
  requires_explicit_regrade?: boolean;
  affected_counts?: Record<string, number>;
  affected_session_ids?: string[];
  affected_session_ids_preview?: string[];
  affected_learner_ids?: string[];
  affected_scopes?: { scope: string; status: string; description: string }[];
  safe_defaults?: string[];
};

type ReviewPlan = {
  sample_size: number;
  confidence: string;
  strengths: { tag_id: string; label: string; answered: number; first_accuracy: number | null; final_accuracy: number | null; avg_attempts: number | null; representative_star?: number | null }[];
  priority_weak_points: { tag_id: string; label: string; answered: number; first_accuracy: number | null; final_accuracy: number | null; avg_attempts: number | null; representative_star?: number | null; evidence_ids: string[] }[];
  dominant_errors: { error_type: string; count: number }[];
  next_tasks: { task_id: string; title: string; description: string; target_tags: string[]; difficulty_star?: number | null; question_count: number; success_rule: string; evidence_ids: string[] }[];
  schedule: { when: string; focus: string }[];
  watch_items: string[];
};

type ReportComparisonWindow = {
  window_start: string | null;
  window_end: string | null;
  submissions: number;
  answered_questions: number;
  first_correct: number;
  final_correct: number;
  first_accuracy: number | null;
  final_accuracy: number | null;
  help_shown: number;
  answer_reveal_debt_created: number;
  confidence: string;
};

type ReportComparison = {
  schema_version: string;
  comparison_type: string;
  available: boolean;
  reason?: string;
  causal_warning: string;
  window_days?: number;
  current?: ReportComparisonWindow;
  previous?: ReportComparisonWindow;
  delta?: {
    submissions: number | null;
    answered_questions: number | null;
    first_accuracy: number | null;
    final_accuracy: number | null;
    help_shown: number | null;
    answer_reveal_debt_created: number | null;
  };
};

type Summary = {
  question_total: number;
  answer_status: Record<string, number>;
  by_source: { source_name: string; count: number }[];
  by_star: { difficulty_star: number; count: number }[];
  top_tags: { tag_id: string; kind: string; label: string; count: number }[];
  tag_star_heatmap: { tag_id: string; kind: string; label: string; stars: Record<string, number>; total: number }[];
  error_types?: Record<string, number>;
  trend_by_day?: { date: string; answered: number; correct: number; accuracy: number | null; sample_size?: number; confidence?: string }[];
  time_stats_by_star?: { difficulty_star: number; count: number; avg_elapsed_ms: number | null; max_elapsed_ms: number | null; sample_size?: number; confidence?: string; basis?: string }[];
  practice: Record<string, number>;
  intervention_effectiveness?: Record<string, number>;
  question_quality_incidents?: { by_status: Record<string, number>; total: number };
  ai_review_events?: { answer_judged: number; auto: number; manual: number; overrides: number; errors: number };
  question_support_events?: { requested: number; problem_confirmed: number; replaced: number; no_replacement: number; question_replaced_events: number; issue_type_counts: Record<string, number> };
  mastery_check_events?: { requested: number; ready: number; failed: number; attempted: number; completed: number };
  mastery_check_details?: {
    event_type: string;
    session_id: string;
    original_question_id: string;
    record_id?: string | null;
    record_ids?: string[];
    generation_job_id?: string | null;
    generation_job_ids?: string[];
    check_question_id?: string | null;
    terminal_reason?: string | null;
    success?: boolean | null;
    created_at?: string | null;
  }[];
  submission_summary?: {
    answered_questions: number;
    submissions: number;
    first_correct: number;
    final_correct: number;
    first_accuracy: number | null;
    final_accuracy: number | null;
  };
  evidence?: {
    evidence_id: string;
    session_id: string;
    mode: string | null;
    question_id: string;
    question_version_id: string | null;
    attempts: number;
    first_correct: boolean | null;
    final_correct: boolean | null;
    elapsed_ms: number;
    submitted_at: string;
  }[];
  filters?: Record<string, unknown>;
  comparison?: ReportComparison;
  summary_meta?: {
    generated_at: string;
    last_submission_at: string | null;
    mode_scope: string;
    answer_basis: string;
    confidence_rule: string;
    history_recalculation?: {
      schema_version: string;
      basis: string;
      regraded_submission_count: number;
      latest_regrade_at: string | null;
      exported_snapshots_may_be_stale: boolean;
    };
  };
  metric_definitions?: Record<string, MetricDefinition>;
  review_plan?: ReviewPlan;
};

type QuestionDetail = {
  id: string;
  source_name: string;
  section: string | null;
  original_number: string | null;
  question_v2: {
    content?: {
      stem_md?: string;
    };
    [key: string]: unknown;
  };
  answer_spec: Record<string, unknown> | null;
  tags: { id: string; kind: string; label: string; role: string }[];
};

type QuestionRepairRecord = {
  id: string;
  question_id: string;
  base_version_id: string | null;
  new_version_id: string | null;
  status: "draft" | "published" | "reverted" | "rejected" | string;
  reason: string;
  severity: string;
  diff: { path: string; change: string; before?: unknown; after?: unknown }[];
  reviewer: string;
  note: string | null;
  created_at: string;
  updated_at: string;
};

type QuestionDuplicateAliasRecord = {
  id: string;
  canonical_question_id: string;
  alias_question_id: string;
  status: string;
  reason: string;
  reviewer: string;
  created_at: string;
  updated_at: string;
};

type QuestionDuplicateMergeResult = {
  status: string;
  canonical_question: { id: string; stem_preview: string; publish_status: string; status: string } | null;
  aliases: QuestionDuplicateAliasRecord[];
  created_count: number;
  retired_alias_ids: string[];
  closed_review_items: number;
};

type QuestionImpact = {
  question: { id: string; stem_preview: string; publish_status: string; status: string } | null;
  totals: {
    session_count: number;
    learner_count: number;
    submission_count: number;
    event_count: number;
    correct_submission_count: number;
    wrong_submission_count: number;
    uncertain_submission_count: number;
    solution_revealed_count: number;
    wrong_record_count: number;
    review_record_count: number;
    intervention_count: number;
    open_review_queue_count: number;
    version_count: number;
    alias_count: number;
    is_duplicate_alias: boolean;
    first_submission_at: string | null;
    last_submission_at: string | null;
  };
  event_counts_by_type: Record<string, number>;
  affected_learners: {
    learner_id: string;
    learner_name: string;
    session_count: number;
    submission_count: number;
    final_correct_count: number;
    final_wrong_count: number;
    solution_revealed_count: number;
    max_help_level: number;
    last_seen_at: string | null;
  }[];
  affected_sessions: {
    session_id: string;
    learner_id: string;
    learner_name: string;
    mode: string;
    status: string;
    question_index: number | null;
    attempt_count: number;
    first_correct: boolean | null;
    final_correct: boolean | null;
    first_submission_at: string | null;
    last_submission_at: string | null;
    event_count: number;
    event_types: Record<string, number>;
    solution_revealed: boolean;
    max_help_level: number;
  }[];
  history_recalculation: HistoryRecalculation;
};

type QuestionRegradeResult = {
  prompt: string;
  checked_count: number;
  returned_count?: number;
  changed_count?: number;
  total_changed_count?: number;
  unchanged_count?: number;
  applied_count?: number;
  changed_correct_to_wrong: number;
  changed_wrong_to_correct: number;
  truncated?: boolean;
  history_recalculation?: HistoryRecalculation;
  items: {
    submission_id: string;
    session_id: string;
    previous: { is_correct: boolean | null; reason?: string | null; effective_wrong?: boolean; question_version_id?: string | null };
    new: { is_correct: boolean | null; reason?: string | null; effective_wrong?: boolean; question_version_id?: string | null };
    changed: boolean;
    changed_fields: string[];
  }[];
};

type QuestionRepairSuggestionResult = {
  question_id: string;
  impact_summary: Record<string, unknown>;
  suggestion: {
    status: string;
    issue_type: string;
    severity: string;
    confidence: number;
    summary: string;
    evidence: string[];
    recommended_actions: string[];
    should_quarantine: boolean;
    should_regrade_after_publish: boolean;
    draft_question_v2_patch: Record<string, unknown>;
    draft_answer_spec_patch: Record<string, unknown>;
    reviewer_notes: string;
    blocked_reason: string | null;
    provider: string;
    model: string;
    error: string | null;
  };
  suggested_draft: {
    reason: string;
    severity: string;
    note: string;
    quarantine: boolean;
    draft_question_v2_patch: Record<string, unknown>;
    draft_answer_spec_patch: Record<string, unknown>;
    should_regrade_after_publish: boolean;
  };
};

type EvidenceDetail = {
  evidence_id: string;
  session: { id: string; mode: string; status: string } | null;
  question: { id: string; source_name: string; difficulty_star: number | null; premium: boolean; stem_preview: string } | null;
  attempts: { submission_id: string; is_correct: boolean | null; error_type: string; thought_text_present: boolean; elapsed_ms: number; created_at: string; regrade?: { is_regraded: boolean; reason?: string | null; applied_at?: string | null } }[];
  events: { id: string; event_type: string; payload: Record<string, unknown>; created_at: string }[];
  interventions: { id: string; intervention_type: string; status: string; current_question_id: string; payload: Record<string, unknown>; created_at: string }[];
};

type TaxonomyTag = {
  id: string;
  kind: string;
  label: string;
  status: string;
  version: string;
  question_count: number;
  description?: string | null;
};

type ModelSettings = {
  provider: string;
  base_url: string;
  api_key?: string | null;
  has_api_key: boolean;
  default_model: string;
  roles: Record<string, string>;
  star_generator_models: Record<string, string>;
  model_price_json: string;
  strict_reviewer_model_allowlist: string;
};

type ModelDiscovery = {
  models: string[];
  source: string;
  capabilities: Record<string, { recommended_roles?: string[]; reasoning_tier?: string; estimated_context_tokens?: number; supports_json?: boolean }>;
  error?: string | null;
};

type ModelTestResult = {
  success: boolean;
  models_ok: boolean;
  chat_ok: boolean;
  source: string;
  model: string;
  elapsed_ms: number;
  models: string[];
  response_preview?: string | null;
  error?: string | null;
};

type SystemTaskSummary = {
  task_id: string;
  task_type: string;
  source: string;
  status: string;
  title: string;
  description?: string | null;
  target_type?: string | null;
  target_id?: string | null;
  progress?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
  error?: string | null;
  can_cancel: boolean;
  can_retry: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
};

type TaskCenterResponse = {
  schema_version: string;
  generated_at: string;
  active_count: number;
  running_count: number;
  queued_count: number;
  failed_count: number;
  stale_count?: number;
  tasks: SystemTaskSummary[];
  guidance: string[];
};

type RecoveryAgentRun = {
  output_language?: string | null;
  diagnosis_status?: string;
  repair_action_count?: number;
  snapshot_at?: string | null;
  id: string;
  status: string;
  model: string;
  max_loops: number;
  loop_count: number;
  input_snapshot?: Record<string, unknown> | null;
  steps: Record<string, unknown>[];
  report?: Record<string, unknown> | null;
  error?: string | null;
  cancel_requested: boolean;
  created_at?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
};

type AssistantConversation = {
  id: string;
  learner_id?: string | null;
  mode: string;
  title?: string | null;
  status: string;
  page_context?: Record<string, unknown>;
  cancel_requested: boolean;
  created_at?: string | null;
  updated_at?: string | null;
};

type AssistantMessage = {
  id: string;
  conversation_id?: string;
  role: "user" | "assistant" | "tool" | string;
  content: string;
  payload?: Record<string, unknown>;
  created_at?: string | null;
};

type AssistantUiDraft = {
  output_language?: string;
  draft_type: "generation_batch" | "practice_session" | "strict_test" | "report_request" | "question_filter" | string;
  title: string;
  payload: Record<string, unknown>;
  apply_label?: string;
};

type AssistantConfirmation = {
  output_language?: string;
  confirmation_id: string;
  tool_run_id?: string;
  tool_name: string;
  title: string;
  permission_level: string;
  arguments: Record<string, unknown>;
};

type AssistantToolResult = {
  tool_name: string;
  tool_run_id?: string;
  result?: Record<string, unknown>;
  error?: string;
};

type AssistantResponse = {
  conversation?: AssistantConversation;
  message?: AssistantMessage;
  assistant_text: string;
  tool_results: AssistantToolResult[];
  ui_drafts: AssistantUiDraft[];
  confirmations_required: AssistantConfirmation[];
  actions_completed: { tool_name: string; result?: Record<string, unknown> }[];
  warnings: string[];
};

type promptTemplateItem = {
  id: string;
  role: string;
  version: string;
  path: string;
  content_hash: string;
  status: string;
  metadata: Record<string, unknown>;
  updated_at: string;
};

type promptRegistryResponse = {
  active_prompts: { schema_version?: string; prompts?: Record<string, string>; error?: string };
  items: promptTemplateItem[];
};

type QualitySuiteEntry = {
  name: string;
  status: string;
  ok: boolean;
  metrics: Record<string, unknown>;
  evidence: Record<string, unknown>;
  gaps: string[];
};

type QualitySuiteReport = {
  schema_version: string;
  generated_at: string;
  root: string;
  prompt_dir: string;
  suite_names: string[];
  overall: {
    ok: boolean;
    ready_count: number;
    partial_count: number;
    missing_count: number;
    not_run_count: number;
    failed_count: number;
  };
  suites: QualitySuiteEntry[];
};

type QualityFixedCaseRunItem = {
  case_id: string;
  suite: string;
  passed: boolean | null;
  provider: string;
  model: string;
  error?: string | null;
  deterministic_passed?: boolean | null;
};

type QualityFixedCaseRunResult = {
  schema_version: string;
  status: string;
  provider: string;
  provider_chain: string[];
  use_mock: boolean;
  requested_case_count: number;
  evaluated_count: number;
  passed: number;
  failed: number;
  unknown: number;
  ok: boolean;
  items: QualityFixedCaseRunItem[];
  quality_suite?: QualitySuiteReport;
};

type AiUsageBucket = {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost_usd: number;
};

type AiUsage = AiUsageBucket & {
  budget_usd: number;
  remaining_budget_usd: number | null;
  budget_exceeded: boolean;
  daily_budget_usd: number;
  price_table?: {
    schema_version: string;
    currency: string;
    unit: string;
    entry_count: number;
    entries: Record<string, { input: number; output: number }>;
    default_source: string;
  };
  estimated_tokens_per_call?: {
    generator?: { prompt_tokens: number; completion_tokens: number };
    reviewer?: { prompt_tokens: number; completion_tokens: number };
  };
  today: AiUsageBucket & {
    window_start_utc: string;
    budget_usd: number;
    remaining_budget_usd: number | null;
    budget_exceeded: boolean;
  };
  by_role: Record<string, AiUsageBucket>;
  by_model: Record<string, AiUsageBucket>;
};

type AnswerJudgeResult = {
  question_id?: string;
  verdict: "correct" | "incorrect" | "uncertain";
  is_correct: boolean | null;
  confidence: number;
  analysis: string;
  student_feedback: string;
  normalized_student_answer?: string | null;
  missing_parts: string[];
  extra_parts: string[];
  format_issue?: string | null;
  should_override_deterministic: boolean;
  override_reason?: string | null;
  provider: string;
  model: string;
  latency_ms?: number | null;
  error?: string | null;
  state_version?: number | null;
};

type QuestionSupportResult = {
  status: "ok" | "error";
  question_problem: boolean;
  issue_type?: string | null;
  safe_response: string;
  confidence: number;
  replacement_question_id?: string | null;
  replaced: boolean;
  provider: string;
  model: string;
  latency_ms?: number | null;
  error?: string | null;
};

type SolutionExplanationResult = {
  status: "ok" | "refused" | "error";
  explanation: string;
  key_points: string[];
  common_misunderstandings: string[];
  next_review_suggestion: string;
  refused: boolean;
  refusal_reason?: string | null;
  provider: string;
  model: string;
  latency_ms?: number | null;
  error?: string | null;
};

type HistoryImportResult = {
  status: string;
  imported_counts: Record<string, number>;
  skipped_tables: string[];
  skipped_rows?: Record<string, number>;
  conflict_report?: Record<string, unknown>[];
  dry_run?: boolean;
};

type QuestionBankImportResult = {
  status: string;
  imported_counts: Record<string, number>;
  skipped_tables: string[];
  skipped_rows: Record<string, number>;
  conflict_report?: Record<string, unknown>[];
  dry_run?: boolean;
};

type QuestionDeleteResult = {
  status: string;
  question_ids: string[];
  deleted_counts: Record<string, number>;
  updated_counts: Record<string, number>;
};

type LearnerDeleteResult = {
  status: string;
  learner_id: string;
  deleted_counts: Record<string, number>;
  preserved: string[];
  skipped: Record<string, unknown>;
};

type DestructiveAction = "clear_history" | "retire_questions" | "delete_learner";

type DestructiveChallenge = {
  challenge_id: string;
  action: DestructiveAction;
  phrase: string;
  expires_at: string;
  target_count: number;
  target_fingerprint: string;
};

type GenerationBatchItem = {
  id: string;
  item_index: number;
  status: string;
  job_id?: string | null;
  candidate_id?: string | null;
  question_id?: string | null;
  similarity_review?: Record<string, unknown> & { risk?: string; high_pair_count?: number; medium_pair_count?: number; pairs?: unknown[] };
  source_type?: string | null;
  reuse_status?: string | null;
  rejection_reason?: string | null;
  duration_ms?: number | null;
};

type GenerationBatch = {
  id: string;
  display_name?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  status: string;
  target_count: number;
  concurrency: number;
  accepted_count: number;
  rejected_count: number;
  failed_count: number;
  eta_seconds?: number | null;
  progress: Record<string, unknown>;
  request: Record<string, unknown>;
  items: GenerationBatchItem[];
};

type RegenerationConstraint = {
  code: string;
  source_codes?: string[];
  instruction: string;
};

type RetryPreview = {
  job_id: string;
  status: string;
  retryable: boolean;
  retry_exhausted: boolean;
  reason?: string | null;
  regeneration_attempt?: number | null;
  max_regeneration_attempts: number;
  regeneration_reason_codes: string[];
  regeneration_constraints: RegenerationConstraint[];
  prefer_reuse_existing_ai?: boolean | null;
  regeneration_mode?: string | null;
};

type HistoryPerformance = "green" | "yellow" | "red" | "gray";

type HistoryGroup = {
  id: string;
  group_type: string;
  title: string;
  mode: string | null;
  status: string | null;
  created_at: string | null;
  updated_at: string | null;
  session_id?: string | null;
  learner_id?: string | null;
  learner_ids?: string[];
  test_run_id?: string | null;
  generation_batch_id?: string | null;
  question_count: number;
  answered_count: number;
  first_accuracy: number | null;
  final_accuracy: number | null;
  avg_attempts: number | null;
  reveal_count: number;
  hint_count: number;
  explanation_count: number;
  performance_counts: Record<HistoryPerformance, number>;
  weak_count: number;
  report_type: "session" | "generation_batch" | "history";
  duplicate_question_ids?: string[];
  mastery_variant_done_count?: number;
};

type HistoryItem = {
  position: number;
  question_id: string;
  exists: boolean;
  available: boolean;
  stem_preview: string;
  source_name: string | null;
  difficulty_star: number | null;
  premium: boolean;
  tags: { id: string; kind: string; label: string }[];
  attempt_count: number;
  effective_wrong_count: number;
  first_correct: boolean | null;
  final_correct: boolean | null;
  last_answered_at: string | null;
  last_event_at: string | null;
  skipped: boolean;
  solution_revealed: boolean;
  used_hint: boolean;
  asked_explanation: boolean;
  question_support_count: number;
  error_type: string | null;
  performance: HistoryPerformance;
  performance_label: string;
  mastery_variant_done?: boolean;
  mastery_variant_generated_count?: number;
  mastery_variant_attempted_count?: number;
  mastery_variant_question_ids?: string[];
};

type HistoryDetail = HistoryGroup & {
  items: HistoryItem[];
};

type HistoryVariantSelection = {
  key: string;
  group_id: string;
  group_title: string;
  group_type: string;
  question_id: string;
  position: number;
  stem_preview: string;
  performance: HistoryPerformance;
  performance_label: string;
  mastery_variant_done?: boolean;
  session_id?: string | null;
  generation_batch_id?: string | null;
};

type StaleRecoveryResult = {
  schema_version: string;
  status: string;
  cutoff: string;
  counts: Record<string, number>;
  preserved: string[];
  note?: string;
};

type AiReport = {
  report_type: string;
  sample_size: number;
  confidence: string;
  findings: { claim: string; evidence_ids: string[] }[];
  evidence_ids: string[];
  summary_md?: string;
  highlights?: string[];
  risks?: string[];
  next_actions?: string[];
  summary_payload?: Record<string, unknown>;
  deterministic_summary?: Summary;
  requested_context?: Record<string, unknown>;
  generation_batch?: Record<string, unknown>;
  provider?: string;
  model?: string;
  error?: string | null;
};

type ReportBatchPlan = {
  schema_version: string;
  report_type: string;
  recommended_count: number;
  difficulty_star: number;
  target_tags: string[];
  keywords: string[];
  focus_points: string[];
  custom_description: string;
  constraints: string;
  parent_summary: string;
  parent_guidance?: string | null;
  confidence: string;
  source_next_tasks: { title?: string; target_tags?: string[]; difficulty_star?: number | null; question_count?: number; description?: string }[];
};

type TodayTask = {
  task_id: string;
  task_type: "review_due_questions" | "tag_star_mastery_check" | "continue_unfinished_session" | "fresh_practice";
  title: string;
  question_count: number;
  question_ids?: string[];
  target_tags?: string[];
  difficulty_star?: number | null;
  session_id?: string;
  reason_codes: string[];
  priority: "high" | "medium" | "low";
};

type TodayTasks = {
  schema_version: string;
  learner_id: string;
  generated_at: string;
  tasks: TodayTask[];
  mastery_summary: {
    observed_tag_star_count: number;
    due_question_count: number;
    level_counts: Record<string, number>;
  };
};

type ActiveIntervention = {
  id: string;
  original_question_id: string;
  current_question_id: string;
  intervention_type: string;
  status: string;
  prefetch_status: string;
  available_after_wrong_count?: number;
  payload: Record<string, unknown>;
};

type TemporaryHintAnswerResponse = {
  record_id: string;
  is_correct: boolean | null;
  result: Record<string, unknown>;
  state: string;
  attempt_count: number;
  intervention: ActiveIntervention;
};

type SessionState = {
  id: string;
  learner_id: string;
  mode: string;
  status: string;
  created_at?: string | null;
  display_name?: string | null;
  question_ids: string[];
  answered_count: number;
  current_index: number;
  next_breakpoint: number | null;
  breakpoints: number[];
  break_confirmed: number[];
  strict_no_ai: boolean;
  skipped_question_ids: string[];
  bookmarked_question_ids: string[];
  uncertain_question_ids: string[];
  solution_revealed_question_ids: string[];
  effective_error_counts: Record<string, number>;
  intervention_due_question_ids: string[];
  active_interventions: ActiveIntervention[];
  config: Record<string, unknown>;
};

type SessionQuestionState = {
  state_version?: number;
  answer_locked_at?: string | null;
  answer_locked_reason?: string | null;
};

type HelpState = {
  session_id?: string;
  question_id?: string;
  allowed_level: number;
  reached_level: number;
  can_reveal_next: boolean;
  solution_revealed: boolean;
  current_help: null | {
    level: number;
    title: string;
    content: string[];
    final_answer?: string | null;
    pitfalls?: string[];
    alternative_solution?: unknown;
  };
};

type QuestionOutcome = "correct" | "wrong" | "review" | "skipped";
type QueueStatus = "todo" | QuestionOutcome | "hint" | "revealed" | "check";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
const PRACTICE_DRAFT_KEY = "math-platform-practice-draft-v2";
const PRACTICE_QUESTION_DRAFT_PREFIX = "math-platform-question-draft-v1";
const ACTIVE_LEARNER_KEY = "math-platform-active-learner-v1";
const QUESTION_FILTER_PRESETS_KEY = "math-platform-question-filter-presets-v1";
const REQUEST_TIMEOUT_MS = {
  normal: 30_000,
  transfer: 180_000,
  aiInteractive: 60_000,
  aiGeneration: 420_000,
  batchStart: 30_000,
};
const QUESTION_PAGE_SIZE = 60;
const MAX_BANK_SELECTION_COUNT = 500;
const DEFAULT_QUESTION_FILTERS: QuestionFilterState = { q: "", source_name: "", difficulty_star: "", premium: "", status: "verified" };

type ApiRequestInit = RequestInit & {
  timeoutMs?: number;
  timeoutLabel?: string;
};

type PracticeQuestionDraft = {
  schema_version: "practice_question_draft_v1";
  active_learner_id: string;
  session_id: string;
  question_id: string;
  state_version: number;
  answer: string;
  thought: string;
  updated_at: string;
};

function practiceQuestionDraftKey(learnerId: string, sessionId: string, questionId: string, stateVersion: number): string {
  if (!sessionId || !questionId) return "";
  return [
    PRACTICE_QUESTION_DRAFT_PREFIX,
    encodeURIComponent(learnerId || "default"),
    encodeURIComponent(sessionId),
    encodeURIComponent(questionId),
    `v${stateVersion}`,
  ].join(":");
}

function readPracticeQuestionDraft(key: string): PracticeQuestionDraft | null {
  if (!key || typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const draft = JSON.parse(raw) as PracticeQuestionDraft;
    return draft?.schema_version === "practice_question_draft_v1" ? draft : null;
  } catch {
    window.localStorage.removeItem(key);
    return null;
  }
}

function writePracticeQuestionDraft(key: string, draft: PracticeQuestionDraft): void {
  if (!key || typeof window === "undefined") return;
  window.localStorage.setItem(key, JSON.stringify(draft));
}

function removePracticeQuestionDraft(key: string): void {
  if (!key || typeof window === "undefined") return;
  window.localStorage.removeItem(key);
}

const WORKSPACES: { id: Workspace; label: string; learner: boolean }[] = [
  { id: "practice", label: "练习", learner: true },
  { id: "generated", label: "套题出题", learner: true },
  { id: "history", label: "历史重做", learner: true },
  { id: "bank", label: "题库", learner: true },
  { id: "reports", label: "报告", learner: true },
  { id: "guide", label: "功能说明", learner: true },
  { id: "admin", label: "管理", learner: false },
];

const GUIDE_SECTIONS = [
  {
    id: "quick",
    title: "快速开始",
    body: [
      "第一次使用先进入题库页点击导入题库；导入完成后题库、练习和报告都会读取同一份本地数据库。",
      "日常做题从练习页开始：先选择普通练习题量，再点击开始练习；系统会按当前标签筛选抽题。点击严格测试会创建低干扰测试。",
      "需要 AI 出题、AI 复核、题目质疑或 AI 报告时，先到管理页填写 接口地址、API 密钥、默认模型和按星级出题模型，再用测试 API 按钮确认可用。",
      "没有 API 密钥时仍可使用题库、确定性判分、报告统计和模拟出题流程；真实 AI 功能会降级为模拟或规则结果。",
    ],
  },
  {
    id: "workspace",
    title: "工作区导航",
    body: [
      "练习页负责当前做题闭环：题目、答案框、思路框、题号队列、提交结果、分层帮助和题目质疑都在这里完成。",
      "套题出题页负责批量 AI 出题和边生成边做；通过审核的题会先进入可作答列表，后续题继续追加。",
      "题库页负责检索和筛选；报告页负责正确率、错因、趋势、用时和证据下钻；管理页负责模型、导入、历史迁移和清空测试数据。",
      "学习者模式默认隐藏管理入口；管理者模式会显示模型配置、AI 出题、历史迁移和清空测试数据。",
    ],
  },
  {
    id: "practice",
    title: "普通练习",
    body: [
      "普通练习适合日常训练和调试题目。你可以提交多次答案，也可以写思路；思路会进入学习分析和报告证据，但可留空。",
      "同一道题的重复答案不会增加有效错误次数；空答案和重复答案不会制造额外错题负担。",
      "题号队列会显示未答、正确、错误、跳过、启发中和已看解答状态，便于快速回到某一道题。",
      "创建练习、严格测试和复习会话时，按钮会立即进入处理中状态；同一次操作重复点击或网络重试会复用原 session，不会重复抽题。",
      "如果一道题是靠多次尝试、提示或启发题才做对，可以点击做类似题/检测。系统会把原题、作答历史、帮助使用情况和已选标签交给出题角色，生成星级不低于当前题、同核心能力但表面不同的检测题；可一次生成 1 到 5 道，每道从 3 个候选中审核选一题，生成完成后只追加到队列，等你点击开始类似题才会切换。",
      "刷新页面后会恢复当前 session、题号和当前题；答案与思路按学习者、session、题目和 state_version 单独保存，输入框旁会显示保存状态。草稿不计入正式记录，提交、跳过、看答案锁题或换题后会清理对应草稿。",
    ],
  },
  {
    id: "todayTasks",
    title: "今日任务与掌握度",
    body: [
      "练习页顶部的今日任务会按当前学习者档案生成，不会混入其他学习者的作答记录。",
      "掌握度按 learner × 标签 × 星级 计算，输入包括首答/终答是否正确、有效错误次数、最高提示等级、是否看答案、距离上次练习时间、掌握检测是否通过、记录量和题目质量状态。",
      "掌握度输出包括 score、mastery_level、confidence、next_review_at 和 reason_codes。它是规则型可解释模型，不是 AI 自由判断。",
      "到期错题/看过答案需重练卡片会创建独立的 review_practice 复习 session，不改写原严格测试或原套题成绩。",
      "今日任务按钮也使用创建锁和幂等 key；重复点击不会创建多个相同复习或检测 session。",
      "薄弱标签检测卡片会按目标标签和星级创建普通练习，用来验证是否真的掌握该类题。",
      "继续未完成卡片会恢复对应 session；如果没有到期内容，系统会给出 5 道均衡练习作为保底任务。",
    ],
  },
  {
    id: "submit",
    title: "提交与判分",
    body: [
      "提交后先走确定性判分：整数、小数、分数、单位、选择、多空、集合和表达式会按 answer_spec 进行规则检查。",
      "如果是集合、多空、文本、表达式、开放回答或非唯一数值答案，系统会自动启动 AI 答案复核。",
      "普通练习会展示复核摘要、置信度和安全反馈；严格测试只保存复核判定，不展示任何答案、缺失项或解题提示。",
      "AI 复核只有在数据充足度且确认确定性判分受格式限制误判时，才会覆盖提交结果；否则保留规则判分或进入待复核。",
    ],
  },
  {
    id: "help",
    title: "分层帮助",
    body: [
      "普通练习中，系统会根据有效错误次数逐步开放 Level 0 到 Level 5 帮助。",
      "Level 0 是自检提醒；Level 1 到 Level 3 逐步给出方向和关键中间步骤；Level 4 到 Level 5 才会接近完整解答。",
      "多次答错后会出现临时提示题：系统会让提示题出题者根据原题、你的答案和思路生成一小题，再由审核者独立验题；提示题不进入题库，也不进入学习报告统计。",
      "查看完整解答会记录看过答案需重练，本题之后即使答对，也不会计入独立掌握。",
      "点击看答案会立即锁定本题，答案框和提交按钮都会禁用；之后可使用讲解解答输入疑问，AI 只解释已经展示的本题解法，讲解历史会进入证据时间线。",
      "严格测试中分层帮助和完整解答默认关闭，避免测试过程被即时提示干扰。",
    ],
  },
  {
    id: "support",
    title: "题目质疑",
    body: [
      "做题时可以提交题目质疑，用于反馈缺图、乱码、竖式看不清、循环点异常、条件矛盾或题意表达不清。",
      "题目质疑不是提示功能。对应 prompt 明确禁止输出答案、候选答案、解题步骤、关键思路、公式套用或下一步该做什么。",
      "如果 AI 判断题目确有格式或条件问题，系统会在当前 session 中替换一题同星级 verified 题，并写入事件日志。",
      "如果只是问不会做、要思路或要答案，系统只会安全拒绝，不会换题，也不会提供解题帮助。",
    ],
  },
  {
    id: "strict",
    title: "严格测试",
    body: [
      "严格测试默认 100 题，按高强度星级比例抽题，并在 40 题和 80 题设置休息确认点。",
      "也可以自定义题量和每个星级的题数；如果选择 AI 混合或纯 AI，并启用缺口补齐，系统会先用算法抽题，再对缺口调用出题者和审核者，全部通过后才创建测试。",
      "来源有三种：题库题、AI 混合题、纯 AI 出题。默认会排除所有已经作答过的题，并在题池不足时给出提示。",
      "严格测试进入低干扰界面，只保留答题相关内容、进度、剩余题数、休息点和退出确认。",
      "退出专注视图不会删除数据库中的测试记录；需要继续时可通过已有 session 状态恢复。",
    ],
  },
  {
    id: "historyReview",
    title: "历史重做",
    body: [
      "历史重做会把普通练习、严格测试、套题出题练习和复习会话按日期与组别整理，例如某天的一次严格测试或某次套题出题。",
      "左侧是历史组列表，中间是题号矩阵，右侧显示选中题的作答次数、首答/终答、提示、看答案、讲解和题目质疑记录。",
      "题号颜色只用于快速扫描，旁边始终有文字状态：绿色表示首答正确且未用提示，黄色表示最终正确但有尝试或帮助，红色表示错误、看答案或强薄弱，灰色表示未答或不可练。",
      "重做整组、只重做薄弱题、重做选中题都会创建新的 `review_practice` 复习 session；原严格测试成绩、原套题成绩和原历史组不会被改写。",
      "历史重做创建会话时也会防重复点击；同一次请求重放只返回原复习 session。",
      "复习会话使用普通练习规则，可以提交、AI 复核、题目质疑、提示、看答案锁题和讲解解答；原题以前看过答案不会锁住新的复习会话。",
    ],
  },
  {
    id: "bank",
    title: "题库筛选",
    body: [
      "题库支持关键词、题源、星级、精品、题目状态和标签联合筛选。关键词会匹配题号、题干摘要、题源、章节、原编号和答案类型。",
      "标签分为主知识线、辅助知识线、解法方法、任务类型、题型结构、限制条件和自定义标签。",
      "标签可以点击加入筛选篮子，也可以拖拽；已选标签会固定显示，方便确认当前筛选条件。",
      "常用筛选可以保存到本机浏览器：保存内容包括关键词、题源、星级、精品、题目状态和标签篮子。套用保存筛选会立即刷新题目列表；删除保存筛选只删除本机预设，不影响题库数据。",
      "题目列表支持跨页累计勾选，最多一次选择 500 道。选中题可以直接创建复习练习；批量停用会继续使用动态确认码，停用后只退出新练习池，历史证据仍保留。",
      "题目列表会分页显示来源、星级、精品状态、可练状态和上次作答时间；上一页/下一页只刷新列表，不影响当前练习 session。",
      "点选题目后可在右侧预览完整题面；管理者模式下还可以进入题目治理面板创建修复草稿。",
    ],
  },
  {
    id: "questionRepair",
    title: "题目修复治理",
    body: [
      "管理者模式下，题库预览区会显示题目治理面板。点击载入当前题后，系统会读取当前 QuestionV2 和 answer_spec，供人工修复题面、答案规范、解法、星级等结构化字段。",
      "修复不会直接覆盖旧题。第一步是创建修复草稿，后端会保存修复原因、严重度、备注、旧版本快照、答案快照和差异预览。",
      "AI 修复建议会读取当前题、答案规范、影响统计、审核队列和最近作答证据，只输出给管理员确认的建议；默认可用模拟流程跑通，取消模拟才调用真实 AI 接口。",
      "如果题目疑似严重质量事故，可以在创建草稿时勾选先隔离出练习池；题目会退出普通练习和严格测试抽题池，并进入审核队列。",
      "发布草稿会创建新的不可变 question_versions 版本，并更新当前题目指针和 answer_specs。旧提交仍保留当时的 question_version_id，历史证据不会悬空。",
      "发布后如果发现修复不合适，可以回滚最新已发布修复。回滚会把当前题目指回修复前版本，并恢复发布前保存的答案规范快照。",
      "重复题不要直接硬删。可以在重复题合并里输入要保留的 canonical 题 ID，把当前题标记为 duplicate alias；当前题会退出后续练习和严格测试，但旧版本、答案、标签和历史提交继续可追溯。",
      "影响统计会显示当前题涉及的学习者数、session 数、提交次数、错误记录、看答案次数和 open 审核项，并列出最近使用过这道题的 session；同时返回历史口径策略，说明报告、历史重做、掌握画像和旧导出快照会怎样受影响。",
      "修复答案规范后，可以先点预览再判分查看历史提交会如何变化；确认执行后才会更新 submission 的判定、有效错误标记和版本指针，并写入 `submission_regraded` 事件供追溯。报告和历史页刷新后按最新 submissions 在线重算，旧导出报告不会被静默改写，需要时应重新导出。",
      "停用题目适合确认不再使用的坏题或重复题；修复治理适合答案、题面、解法、星级等可以修正后继续使用的题。",
    ],
  },
  {
    id: "bankTransfer",
    title: "题库导入导出",
    body: [
      "题库导出会生成 UTF-8 JSON 文件，根结构固定为 `question_bank_transfer_v1`，包含 `schema_version`、`exported_at`、`filters`、`table_counts` 和 `tables`。",
      "导出包的 `tables` 包含题库核心表：source_files、questions、question_versions、question_repair_records、question_duplicate_aliases、answer_specs、taxonomy_tags、taxonomy_aliases、taxonomy_migrations、taxonomy_governance_records、question_tags、question_variants、review_queue_items。",
      "题库导出不包含普通作答历史、练习 session、报告、AI 调用日志和 API 密钥；升级或换电脑时，题库包和历史包要分别导出。",
      "题库导入只接受平台导出的 `question_bank_transfer_v1` JSON，不接受原始富化题库 JSON。内置源 JSON 包含原始 5 份富化题库和 GPT6 整理 AI 题源，统一使用“导入内置 JSON”。",
      "导入前可以先点“校验题库包”，系统会 dry-run 完整校验和计数但不写数据库；确认无异常后再正式导入。",
      "导入时按主键合并，source_files 会按 filename+hash 安全映射；同 filename 但 hash 不同会作为独立来源保留，不会静默覆盖旧来源。",
      "缺少 id 或引用不存在的行会跳过并在结果里计数；文件应保持完整，不要手工删改核心字段。",
    ],
  },
  {
    id: "questionDelete",
    title: "停用/隔离题目",
    body: [
      "题库页选中题目后可以停用当前题。停用前后端会生成一次性动态确认码，用户必须按弹窗输入，确认码过期或复用都会失败。",
      "停用题目不会删除该题的 answer_spec、question_versions、question_tags、review_queue_items、变体关系、提交记录、错题记录、题目质疑和相关事件。",
      "停用后题目会标记为 retired，并从后续普通练习和严格测试题池排除；历史报告、证据时间线和旧 session 仍能追溯到原题。",
      "停用适合处理重复 AI 生成题、确认错误的坏题或暂时不想再抽到的题；真正物理清理数据库应先导出备份并通过维护脚本单独处理。",
    ],
  },
  {
    id: "degraded",
    title: "诊断包与故障排查",
    body: [
      "管理页可以导出脱敏诊断包，用于排查启动失败、API 无响应、出题任务卡住、数据库异常或发布包文件问题。",
      "诊断包包含配置摘要、SQLite 完整性与 PRAGMA 状态、关键表计数、失败 job/batch 快照、最近审计日志摘要、最近 AI 调用摘要和磁盘空间。",
      "诊断包不包含 API 密钥、加密配置密文、完整 provider 请求响应、完整学生自由文本或完整题面。",
      "如果状态条出现错误码或 degraded，可以直接复制 degraded；点击诊断包按钮会切换到管理页，方便导出诊断包并把 degraded 一起提供给维护者。",
      "管理页的健康检查面板会读取详细健康端点，把 存活检查、就绪检查、降级检查和诊断检查 分成四组展示；它只检查本地 API、数据库、磁盘、发布包和 provider 配置状态，不调用外部模型、不显示 API 密钥。",
      "管理页的运行配置面板会显示后端配置版本、默认值、已配置项、功能开关和敏感配置是否已填写；API 密钥只显示“已配置/未配置”，不会显示明文。",
      "管理页的错误码目录可按错误码、提示或下一步搜索，查看该错误是否可重试、对应 HTTP 状态和推荐处理动作。",
      "管理页的状态目录可按状态值、中文说明或模型字段搜索，查看任务、批次、题目、答案规范、练习会话和修复审核状态是否属于活跃态、终态或会阻止练习。",
      "导出前后端会做二次密钥扫描；如果扫描发现疑似密钥，前端会显示警告，用户应先检查文件再发送给维护者。",
      "诊断包不会调用真实 AI 接口，不会消耗 token，也不会改变练习、报告或题库数据。",
    ],
  },
  {
    id: "tags",
    title: "标签与自定义标签",
    body: [
      "出题和筛题都可以使用多个标签。多标签会共同约束题目方向，适合指定知识点、方法和题型结构。",
      "自定义标签可以填写类型、名称和说明；说明会进入出题请求，帮助 AI 理解这个标签的具体使用边界。",
      "标签说明会尽量用业务含义展示，不直接暴露内部 key；搜索框可按标签名、类型和说明检索。",
      "如果临时只想补充一个条件，也可以在套题出题里填写临时标签，与已选标签同时使用。",
    ],
  },
  {
    id: "generation",
    title: "AI 出题",
    body: [
      "AI 出题支持按标签、自定义描述、星级、数量、并发数、候选数、非 AI 案例数、AI 反重复案例数和额外约束生成题目。",
      "默认会先查找同星级、标签相近、用户未做过的已有 AI 题；命中时直接复用，不调用模型、不产生出题费用，不足数量再生成新题。",
      "真正调用模型时，默认提供最多 5 道非 AI 题作为正向范本，并额外提供最多 3 道同星级、标签最相近的已有 AI 题作为反重复参考；高级参数可调整数量。",
      "默认每出一题会并发生成 3 个候选：结构观察、约束变换、反向构造各 1 个；高级参数可改为 1-5 个候选，最终只发布评分最高且不重复的 1 个。",
      "套题出题会先生成整套蓝图：目标 slot、同星级暖身/核心/挑战位置、标签覆盖、方法多样性、单一数值答案要求和故事域重复上限。每个批次 item 都会绑定一个 slot。",
      "生成流程包含范本检索、多策略并行生成、JSON 校验、规则检查、独立审核、集合去重和择优入库。",
      "星级不是让生成器自说自话；系统会把难度与精品评级指南注入 prompt，并要求 reviewer 独立打六维分和精品分。",
      "如果 reviewer 给出的独立答案不能被候选题的 answer_spec 判为正确，系统会拒绝发布该候选，并把原因归入 answer_spec_inconsistent 供定向重试使用。",
      "严格档真实 AI 接口会启用双审核仲裁：主审和二审必须给出一致结论和独立答案；不一致时不会发布候选题。",
      "所有 prompt、模型、输入哈希、输出、错误、延迟和 ID 用量都会写入 AI 日志，方便后续追溯。",
      "单题出题如果进入 rejected、failed、cancelled 或 timed_out，会显示“定向重试”面板；面板会把失败原因转成约束，用户确认后才继续调用 provider。",
      "管理页会显示今日和累计 AI 调用次数、token 用量与估算费用。超出预算时默认阻断新的真实 AI 出题任务；模拟流程和完全复用已有 AI 题不受阻断。",
    ],
  },
  {
    id: "generationAdvanced",
    title: "AI 出题高级参数",
    body: [
      "高级参数默认折叠，默认档位是均衡：每题 3 个候选、5 个非 AI 正向案例、3 个 AI 反重复案例。",
      "快速档位是 1/3/1，适合测试流程；严格档位是 5/8/5，适合质量优先，但生成、审核、等待时间和费用都会增加。",
      "候选数表示每道最终题先生成几个候选，再由审核和集合去重选出 1 道；候选越多，不代表一定更难，而是筛选余量更大。",
      "非 AI 案例只作为正向范本，帮助模型把握题面质量、星级尺度和标签方向；AI 案例只作为反重复参考，防止继续生成类似 AI 题。",
      "关闭“优先复用未做过 AI 题”就是必须新生成，适合明确要新题；默认开启可减少等待和费用。",
      "成本提示会显示调用次数级别，并结合管理页价格表做本地计划费用估算；价格表留空时使用通用默认估算，不代表 provider 官方报价。",
      "创建单题或套题时，后端会冻结一次调用预算快照；如果候选数和题量导致生成/审核调用超过本地上限，任务会直接拒绝创建，不会开始烧 API。",
      "“确认超出日/月费用预算时仍继续真实调用”默认关闭。开启后只允许越过费用预算提醒，不会绕过生成/审核调用次数上限；预算快照和审计记录会写入 overrun_confirmed。",
      "“确认审核模型降级时仍继续发布候选题”默认关闭。严格档下如果生成和审核实际使用同一模型，默认不发布；手动确认后才允许继续。",
      "管理页“第二审核”角色用于严格档双审核仲裁，建议配置成不同于“审核”和“生成”的模型。",
      "严格档可配置审核模型白名单。白名单为空时不额外限制；填写后，不在白名单的审核模型默认不发布，除非用户显式确认降级。",
      "真实 AI 运行中还会按已记录 token 费用复查预算。如果达到日/月预算且未确认超预算，任务会停止并记录原因。",
    ],
  },
  {
    id: "generatedPractice",
    title: "边生成边做",
    body: [
      "套题出题开始后，不必等所有题都完成。前几道通过审核后可以同步到套题练习页先做。",
      "套题也会先复用未做过的相近 AI 题，并在进度里区分复用题、新生成题、运行中、拒绝和失败。",
      "套题进度会显示蓝图曲线、slot 覆盖、覆盖评分和缺口；每题卡片会显示 slot 编号、暖身/核心/挑战阶段和数值答案要求。",
      "ETA 会显示为剩余时间范围，并标出高/中/低可参考程度和记录来源；记录少或刚开始生成时不要把它当作精确倒计时。",
      "批次摘要条会显示可练题数、未完成数、质量档、剩余最多生成/审核调用和失败原因聚合，例如近重复、provider 失败或用户中断。",
      "配置快照可以回看当时的候选数、案例数、并发、复用策略、provider、星级和标签；中断说明会提示在途 provider 请求可能已产生费用，但晚返回不会发布。",
      "复制配置会把当前批次或历史批次的参数填回套题出题表单，不会直接启动新批次；确认标签、模型和高级参数后再点击开始生成。",
      "如果批次因为 provider 失败、审核拒绝或超时只生成了部分题，可以点击“补齐剩余”。系统会保留已接受题，只为缺失/失败 slot 继续尝试，并按剩余尝试次数重新做预算预检，不会无限补题。",
      "后续题继续生成并自动追加到同一个生成题 session，适合一次出很多题时节省等待时间。",
      "套题批次会保留历史入口，可重新打开、继续同步作答，或直接切到报告页生成这一套题的报告。",
      "批次卡片会显示目标数量、已接受数量、拒绝数量、失败数量、预计剩余时间、ETA 可参考程度和每题状态。",
      "若生成题质量不稳定，可查看拒绝原因、相似度风险和审核记录，再调整标签、星级或约束重新生成。",
    ],
  },
  {
    id: "dedupe",
    title: "相似题控制",
    body: [
      "出题时不会只靠标签判断相似；系统会结合范本检索、设计策略、题面规范化、语义签名、题族和集合审核来降低重复风险。",
      "正向范本必须来自非 AI 题，AI 题只作为反重复参考，防止模型继续沿着已有 AI 题做轻微改写；这和标签相似检索不是同一个概念。",
      "多路生成会拿到不同设计合同，要求在任务形式、信息呈现、辅助方法、条件组织、故事领域、数值结构等轴上拉开差异。",
      "集合审核会输出相似度矩阵、多样性分和需要重生成的位置；高风险候选不会直接进入练习题池。",
      "已经做过的题和近期曝光题可以在严格测试选题时排除，避免测试反复抽到同一类题。",
    ],
  },
  {
    id: "reports",
    title: "报告解读",
    body: [
      "报告分为历史整体、单次测试和出题批次三类，可按日期、模式、题源、星级、精品和标签筛选。",
      "核心指标包括题量、提交量、首答正确率、最终正确率、提示使用次数次数、看过答案需重练和题目异常反馈。",
      "每个摘要指标都可以展开“统计说明”，查看统计口径、筛选范围、排除项、更新时间、分子/分母、记录数和可参考程度。",
      "图表包含错误类型分布、正确率趋势、星级用时和标签星级热力图。热力图可点击下钻到指定标签和星级。",
      "证据时间线会按时间展示提交、帮助、启发题、看解答、复答、题目质疑和换题事件，便于回溯每个判断。",
      "当报告同时填写开始日期和结束日期时，会显示上一等长时间窗对比，例如本周对比上一周；对比只说明题量、正确率、启发题和看过答案需重练的变化，不证明因果。",
      "确定性复习计划里的每个下一步任务都可以直接点击开始练习；系统会按该任务的标签、星级和题量创建独立复习 session，并优先选择当前学习者没做过的题。",
    ],
  },
  {
    id: "aiReport",
    title: "AI 报告",
    body: [
      "AI 报告会收到时间窗口、筛选条件、报告类型和证据 ID，不会凭空编造数据库没有的数据。",
      "单次测试报告适合看本次表现；出题批次报告适合评价一批 AI 生成题质量；历史整体报告适合看长期趋势。",
      "低记录量或证据不足时，报告会标记数据较少度；每条结论都应能通过 evidence_id 回到原始提交和事件。",
      "若清空历史记录，报告也会随之清空；如果只是升级项目，应先使用历史导出。",
    ],
  },
  {
    id: "models",
    title: "模型配置",
    body: [
      "degraded 默认可填 OpenAI-compatible 地址，例如 LiaoBots 的 `https://ai.liaobots2.work/v1`。",
      "API 密钥保存后只保存在后端加密配置中，前端输入框会清空，页面之后不会显示明文。",
      "拉取模型会读取 provider 的模型列表；测试 API 会同时检查模型列表和一次极短 Chat 请求。",
      "角色模型可以分别设置规划、生成、审核、报告、诊断、启发题、故障审核、集合审核和答案复核；不填时使用默认模型。",
      "出题 generator 可以按星级覆盖：3 星及以下、4 星、5 星分别指定模型；留空时回退到生成角色模型。候选数增加时，生成角色调用次数会同步增加。",
    ],
  },
  {
    id: "qualitySuite",
    title: "AI 质量套件",
    body: [
      "管理页的 AI 质量套件用于检查 prompt、模型和 provider 配置变化是否值得继续发布或使用。",
      "套件覆盖星级标定、数学正确性、小学解法风格、重复题攻击、越界注入防护、题面格式、答案复核、报告忠实度和成本性能。",
      "每个套件都有状态：就绪表示有固定或可运行检查；部分覆盖表示已有门禁但还缺固定样例集；缺失或未运行表示需要补数据、日志或基准文件。",
      "质量套件读取本地数据库、提示词文件和运行日志，不会调用真实 AI 接口，也不会消耗 token。",
      "如果失败数大于 0，不建议直接切换 prompt 或模型投入真实出题；应先查看缺口提示，例如补充黄金集、修复 prompt 必需字段或处理重复题。",
      "prompt 版本面板负责切换和回滚；质量套件负责在切换前后给出可追溯的验收证据。",
    ],
  },
  {
    id: "history",
    title: "历史迁移",
    body: [
      "管理页的导出历史会生成一个 JSON 文件，包含答题、报告、AI 出题、生成题、变体血缘、自定义标签和 AI 日志。",
      "导出范围可以选择“全部历史”或“当前学习者”；当前学习者包只带该学习者的作答、报告、复习会话和相关 AI 生成追溯，适合分开迁移或备份。",
      "管理页也可以删除当前学习者数据：执行前会生成一次性动态确认码，默认学习者不能删除；其他学习者仍用过的 AI 题会保留，避免破坏共享题库。",
      "导出包不包含 API 密钥。复制到新电脑后，仍需要在模型配置里重新填写一次密钥。",
      "导入历史前可以先点“校验历史”，系统会 dry-run 完整校验和计数但不写数据库。",
      "导入历史会按源文件名和 hash 映射题库来源，避免不同电脑上的 source_file_id 不一致；同 filename 但 hash 不同会作为独立来源保留。",
      "升级或复制项目之前，建议先导出题库包和历史包；新版本启动后先导入题库包或内置 JSON，再导入历史记录。",
    ],
  },
  {
    id: "clear",
    title: "清空测试数据",
    body: [
      "清空历史用于测试时重置报告数据。执行前后端会生成一次性动态确认码，用户必须按弹窗输入，确认码过期或复用都会失败。",
      "清空会删除练习 session、提交、事件、错题、报告、AI 出题、AI 生成题和 AI 日志。",
      "清空不会删除原始导入题库和基础标签，因此重新创建练习不需要再次富化 JSON。",
      "正式使用前不要随意清空；如果只是担心升级丢数据，应该使用历史导出和导入。",
    ],
  },
  {
    id: "packaging",
    title: "复制与启动",
    body: [
      "发布包目录是 `dist\\math-platform`。复制这个文件夹到另一台 Windows 电脑后，优先点击 `START_HERE.cmd`。",
      "在有 Python 环境的电脑上也可以直接用发布包启动；当前发布包内已经包含 API exe、Web 运行文件、Node、题库背景资料和启动器。",
      "常用入口包括 `start.cmd`、`stop.cmd`、`check.cmd`、`backup.cmd` 和 `launcher\\MathPlatform.exe`。",
      "如果端口被占用，可先运行 `check.cmd` 查看状态，再运行 `stop.cmd` 停止本发布包自己的 API/Web 进程。",
    ],
  },
  {
    id: "safety",
    title: "异常与卡住处理",
    body: [
      "普通接口默认 30 秒超时，导入导出类操作 180 秒超时，AI 答案复核和题目质疑 60 秒超时；超时后按钮会恢复，页面会提示刷新、重试或取消。",
      "看答案是短请求；讲解解答是 AI 交互请求，60 秒内没有响应会失败并允许重试，原提交、锁题状态和已展示答案不会被覆盖。",
      "真实 AI 出题采用失败即停止：如果已经填写 API 密钥但 provider 超时、报错或返回坏 JSON，任务会失败或拒绝，不会静默发布模拟题；增加候选数只会扩大真实调用规模，不会绕过失败保护。",
      "AI 出题被拒绝后的重试是定向重试：系统会把重复、答案不一致、难度不符、解法风格不符等原因转成约束，每个 retry root 最多 3 次，超过后直接失败，不会无限调用 provider。",
      "套题出题可以中断。中断后不再派发新题，已经在外部 provider 中运行的请求即使晚返回，也只记录日志，不会加入可练习题池。",
      "批次页面会把失败/拒绝/取消原因聚合显示，并保留配置快照，排查卡住或失败时可以先看原因摘要、ETA 可参考程度、运行中数量和预算快照，再决定刷新、补齐或中断。",
      "批量进度流断开时不会丢失 batch id；自动重连收到新事件会显示已从断线位置恢复，手动刷新批次状态也会说明从上次事件后恢复。后端会把超过 deadline 的 running job 自动标记为 timed_out，避免永久 running。",
      "严格测试中的 AI 复核只保存判定和脱敏结果，不显示答案、步骤、缺失项或提示；题目质疑失败也不会覆盖确定性判分和原提交记录。",
      "错误提示会包含用户消息、错误码、下一步和 degraded；状态条里的复制 degraded 与诊断包入口用于快速定位卡住的 API 请求或 provider 调用。",
    ],
  },
  {
    id: "faq",
    title: "常见问题",
    body: [
      "页面打不开通常是服务没启动或端口被占用。先运行 `START_HERE.cmd`，再用 `check.cmd` 查看 API 和 Web 是否都 ok。",
      "AI 出题慢通常取决于 provider 响应、并发数、审核次数和是否重试。套题出题会显示 ETA 范围和可参考程度；低可参考程度通常表示记录少或刚开始生成。",
      "如果某次出题全部候选被拒绝，这是正常质量门结果；重试会定向修改失败点，但不会为了凑题数降低审核标准。",
      "题目显示异常时不要把它当作不会做处理，应使用题目质疑入口；如果确认题目有问题，系统会尝试同星级换题。",
      "答案明明等价却判错时，复杂答案会自动复核；普通练习也可以手动点击 AI 复核查看安全反馈。",
    ],
  },
];

const ROLE_LABELS: Record<string, string> = {
  planner: "规划",
  generator: "生成",
  reviewer: "审核",
  secondary_reviewer: "第二审核",
  reporter: "报告",
  diagnostician: "诊断",
  hint_generator: "启发题",
  hint_reviewer: "启发题审核",
  fault_auditor: "故障审核",
  set_reviewer: "集合审核",
  answer_judge: "答案复核",
  solution_explainer: "讲解解答",
  question_repair_reviewer: "修复建议",
  emergency_recovery_agent: "极端恢复 Agent",
  project_assistant: "项目助手",
};

const STAR_GENERATOR_LABELS: Record<string, string> = {
  le_3: "3 星及以下",
  star_4: "4 星",
  star_5: "5 星",
};

const QUALITY_SUITE_LABELS: Record<string, string> = {
  star_calibration: "星级标定",
  math_correctness: "数学正确性",
  elementary_solution_style: "小学解法风格",
  duplicate_attack: "重复题攻击",
  prompt_injection: "越界注入防护",
  format_quality: "题面格式质量",
  answer_judge: "答案复核",
  report_faithfulness: "报告忠实度",
  cost_performance: "成本与性能",
};

const QUALITY_STATUS_LABELS: Record<string, string> = {
  ready: "就绪",
  partial: "部分覆盖",
  missing: "缺失",
  not_run: "未运行",
};

const QUALITY_GAP_LABELS: Record<string, string> = {
  database_not_supplied: "没有数据库记录",
  no_ai_run_logs: "暂无 AI 调用日志",
  needs_fixed_expert_star_labels_and_model_comparison: "缺专家星级标定集和模型对比",
  needs_fixed_expert_star_labels: "缺专家星级标定样例",
  needs_model_evaluation_on_fixed_star_calibration_cases: "已有星级标定样例，待运行模型评测",
  star_calibration_fixed_case_evaluation_incomplete: "星级标定固定样例评测未全覆盖",
  star_calibration_fixed_case_evaluation_ID_results: "星级标定固定样例存在未知结果",
  star_calibration_fixed_case_evaluation_failures: "星级标定固定样例存在未通过项",
  needs_200_question_database_sample_or_generated_golden_file: "缺 200 题数据库记录或黄金集文件",
  needs_fixed_solution_style_case_set: "缺小学解法风格固定样例集",
  needs_fixed_visual_format_case_set: "缺题面显示坏例/好例记录",
  needs_fixed_multi_answer_and_rubric_case_set: "缺多答案与 rubric 复核样例集",
  needs_fixed_evidence_citation_report_cases: "缺报告证据引用固定样例",
  needs_model_evaluation_on_fixed_visual_format_cases: "已有题面样例，待接入模型评测",
  needs_model_evaluation_on_fixed_multi_answer_and_rubric_cases: "已有答案复核样例，待接入模型评测",
  needs_model_evaluation_on_fixed_evidence_citation_report_cases: "已有报告样例，待接入模型评测",
  fixed_quality_case_file_missing: "固定质量样例文件缺失",
  fixed_quality_case_file_invalid_json: "固定质量样例 JSON 无法解析",
  fixed_quality_case_file_missing_cases: "固定质量样例缺少 cases 数组",
  fixed_quality_case_file_has_invalid_cases: "固定质量样例中有无效记录",
  fixed_quality_case_evaluation_incomplete: "固定质量样例评测未全覆盖",
  fixed_quality_case_evaluation_ID_results: "固定质量样例评测存在未知结果",
  fixed_quality_case_evaluation_failures: "固定质量样例评测存在未通过项",
  format_quality_fixed_case_evaluation_incomplete: "题面格式固定样例评测未全覆盖",
  format_quality_fixed_case_evaluation_ID_results: "题面格式固定样例存在未知结果",
  format_quality_fixed_case_evaluation_failures: "题面格式固定样例存在未通过项",
  answer_judge_fixed_case_evaluation_incomplete: "答案复核固定样例评测未全覆盖",
  answer_judge_fixed_case_evaluation_ID_results: "答案复核固定样例存在未知结果",
  answer_judge_fixed_case_evaluation_failures: "答案复核固定样例存在未通过项",
  report_faithfulness_fixed_case_evaluation_incomplete: "报告忠实度固定样例评测未全覆盖",
  report_faithfulness_fixed_case_evaluation_ID_results: "报告忠实度固定样例存在未知结果",
  report_faithfulness_fixed_case_evaluation_failures: "报告忠实度固定样例存在未通过项",
  uses_runtime_logs_not_fixed_cost_benchmark: "当前使用运行日志，不是固定成本基准集",
};

const TAG_KIND_META: Record<string, { label: string; description: string }> = {
  primary: { label: "主知识线", description: "题目最核心的知识领域，用来决定出题主方向。" },
  secondary: { label: "辅助知识线", description: "会一起出现的次要知识点，用来做交叉综合。" },
  method: { label: "解法方法", description: "希望学生使用或训练的方法，例如枚举、分类、转化。" },
  task: { label: "任务类型", description: "题目要求完成的动作，例如求值、证明、计数、比较。" },
  structure: { label: "题型结构", description: "题面组织方式，例如多条件、图形组合、递推结构。" },
  constraint: { label: "限制条件", description: "出题时必须遵守或避免的条件，例如不用方程、必须可判分。" },
  custom: { label: "自定义", description: "本地新增的出题标签，可附带自己的说明。" },
};

const ERROR_LABELS: Record<string, string> = {
  correct: "正确",
  numeric_exact: "数值精确匹配",
  numeric_tolerance: "数值在容差内",
  fraction_equivalent: "分数等价",
  choice_match: "选项匹配",
  text_match: "文本匹配",
  text_mismatch: "文本不匹配",
  not_numeric: "答案不是可识别数值",
  numeric_mismatch: "数值不匹配",
  unit_missing: "缺少必要单位",
  unit_mismatch: "单位不匹配",
  expression_mismatch: "表达式不等价",
  expression_unverified: "表达式待复核",
  answer_spec_invalid: "答案规范待修复",
  part_count_mismatch: "填空数量不对",
  part_value_or_order_mismatch: "多空值或顺序不对",
  set_missing: "集合答案有缺漏",
  set_extra: "集合答案有多余",
  set_mismatch: "集合答案不匹配",
  set_duplicate_part: "集合答案有重复",
  empty_answer: "空答案",
  calculation_or_numeric: "计算/数值错误",
  format_or_expression: "格式/表达式错误",
  multi_part_or_order: "多空/顺序错误",
  algebraic_expression: "表达式错误",
  review_or_uncertain: "待复核",
  other_wrong: "其他错误",
};

const QUEUE_STATUS_LABELS: Record<QueueStatus, string> = {
  todo: "未答",
  correct: "正确",
  wrong: "错误",
  review: "待复核",
  skipped: "跳过",
  hint: "启发中",
  revealed: "已看解答",
  check: "检测题",
};

const EVENT_LABELS: Record<string, string> = {
  submitted: "提交",
  help_revealed: "查看帮助",
  solution_revealed: "查看完整解答",
  assisted_completion: "辅助完成",
  answer_reveal_debt_created: "生成看过答案需重练",
  intervention_prefetched: "预生成启发题",
  intervention_due: "展示启发题",
  intervention_prefetch_cancelled: "取消预生成",
  intervention_prefetch_stale: "预生成过期",
  mastery_check_requested: "已请求做类似题/检测",
  mastery_check_ready: "检测题已就绪",
  mastery_check_failed: "检测题生成失败",
  mastery_check_attempted: "完成检测题尝试",
  mastery_check_completed: "掌握检测完成",
  answer_judged: "AI答案复核",
  question_support_requested: "题目质疑",
  question_replaced: "同星级换题",
  answer_locked_by_solution_reveal: "看答案后锁题",
  answer_locked_submit_blocked: "锁题后提交被阻止",
  solution_explanation_requested: "请求讲解解答",
  solution_explanation_answered: "讲解解答已返回",
  skipped: "跳过",
  bookmarked: "收藏",
  marked_uncertain: "暂不确定",
};

const STRICT_SOURCE_LABELS: Record<StrictSourceMode, string> = {
  bank_only: "题库题",
  ai_mixed: "AI 混合题",
  ai_only: "纯 AI 出题",
};

const HISTORY_GROUP_LABELS: Record<string, string> = {
  test_run: "严格测试",
  generation_batch: "套题出题",
  mastery_variant_batch: "批量类似题",
  practice: "普通练习",
  strict_test: "严格测试",
  generated_practice: "套题练习",
  review_practice: "历史重做",
  diagnostic: "标准诊断",
  degraded: "标准诊断",
};

const HISTORY_PERFORMANCE_LABELS: Record<HistoryPerformance, string> = {
  green: "稳",
  yellow: "需复习",
  red: "薄弱",
  gray: "未答/不可练",
};
const MASTERY_VARIANT_MARKER_LABEL = "错后已完成类似题";

function IDDiagnosticsFromLabel(label: string): Pick<OperationStatus, "traceId" | "errorCode"> {
  const degradedMatch = label.match(/degraded\s+([^ ·]+)/);
  const errorCodeMatch = label.match(/错误码\s+([^ ·]+)/);
  return {
    traceId: degradedMatch?.[1],
    errorCode: errorCodeMatch?.[1],
  };
}

async function api<T>(path: string, init?: ApiRequestInit): Promise<T> {
  const { timeoutMs = REQUEST_TIMEOUT_MS.normal, timeoutLabel = "请求", ...fetchInit } = init || {};
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  const headers = new Headers(fetchInit.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  headers.set("X-Math-Platform-Client", "math-platform-web");
  headers.set("X-Math-Platform-Language", getLanguage());
  try {
    const response = await languageFetch(`${API_BASE}${path}`, {
      ...fetchInit,
      signal: controller.signal,
      headers,
    });
    const text = await response.text();
    if (!response.ok) throw parseApiError(response.status, text, response.statusText);
    return (text ? JSON.parse(text) : undefined) as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(`${timeoutLabel}超时（${Math.round(timeoutMs / 1000)} 秒），可刷新状态、重试或取消当前任务。`);
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

function parseApiError(status: number, text: string, statusText: string): ApiRequestError {
  let parsed: unknown = undefined;
  try {
    parsed = text ? JSON.parse(text) : undefined;
  } catch {
    parsed = undefined;
  }
  const envelope = parsed && typeof parsed === "object" ? (parsed as ErrorEnvelope) : null;
  const parts = [
    envelope?.user_message || (typeof envelope?.detail === "string" ? envelope.detail : text || statusText || "请求失败"),
    envelope?.error_code ? `错误码 ${envelope.error_code}` : "",
    envelope?.action ? `下一步：${envelope.action}` : "",
    envelope?.retryable ? "可重试" : "",
    envelope?.trace_id ? `degraded ${envelope.trace_id}` : "",
  ].filter(Boolean);
  return new ApiRequestError(status, parts.join(" · "), envelope);
}

function percent(value: number | null | undefined): string {
  return value == null ? "-" : `${Math.round(value * 100)}%`;
}

function signedCount(value: number | null | undefined): string {
  if (value == null) return "-";
  if (value > 0) return `+${value}`;
  return String(value);
}

function signedPercentPoints(value: number | null | undefined): string {
  if (value == null) return "-";
  const points = Math.round(value * 100);
  return `${points > 0 ? "+" : ""}${points} 点`;
}

function confidenceText(value: string | undefined): string {
  switch (value) {
    case "high":
      return "数据充足";
    case "medium":
      return "数据一般";
    case "low":
      return "数据较少";
    case "very_low":
      return "记录很少";
    case "none":
      return "无记录";
    default:
      return "置信度未知";
  }
}

function metricScopeLine(metric: MetricDefinition | undefined): string {
  if (!metric) return "暂无口径";
  const denominator = metric.denominator == null ? "-" : String(metric.denominator);
  return `${metric.numerator ?? 0}/${denominator} · 记录 ${metric.sample_size} · ${confidenceText(metric.confidence)}`;
}

function chartDataIndex(params: any): number {
  const item = Array.isArray(params) ? params[0] : params;
  if (item && typeof item === "object" && "dataIndex" in item) {
    const value = (item as { dataIndex?: unknown }).dataIndex;
    return typeof value === "number" ? value : 0;
  }
  return 0;
}

function formatSeconds(value: number | null | undefined): string {
  if (value == null) return "-";
  if (value < 60) return `${Math.max(0, Math.round(value))} 秒`;
  return `${Math.floor(value / 60)} 分 ${Math.round(value % 60)} 秒`;
}

function formatMs(value: number | null | undefined): string {
  if (!value) return "-";
  return formatSeconds(value / 1000);
}

function formatUsd(value: number | null | undefined): string {
  return value == null ? "-" : `$${value.toFixed(4)}`;
}

function stableHash(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

function sessionQuestionState(state: SessionState | null, questionId: string): SessionQuestionState {
  const states = state?.config?.question_states;
  if (!states || typeof states !== "object") return {};
  const row = (states as Record<string, SessionQuestionState>)[questionId];
  return row && typeof row === "object" ? row : {};
}

function questionStateVersion(state: SessionState | null, questionId: string): number {
  const version = sessionQuestionState(state, questionId).state_version;
  return typeof version === "number" && Number.isFinite(version) ? version : 0;
}

function clampedNumber(value: string | number | null | undefined, min: number, max: number, fallback: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, Math.round(parsed)));
}

function asRecord(value: any): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function metadataString(value: unknown, fallback = "-"): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function metadataNumber(value: any): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

const ETA_CONFIDENCE_LABELS: Record<string, string> = {
  high: "高可参考程度",
  medium: "中可参考程度",
  low: "低可参考程度",
};

const ETA_SOURCE_LABELS: Record<string, string> = {
  current_batch: "当前批次记录",
  history: "历史耗时记录",
  fallback_default: "默认估算",
};

function etaSummary(progress: Record<string, unknown>, fallbackEtaSeconds?: number | null): string {
  const eta = metadataNumber(progress.eta_seconds) ?? fallbackEtaSeconds ?? null;
  if (eta == null) return "预计剩余 -";
  const range = Array.isArray(progress.eta_range_seconds) ? progress.eta_range_seconds.map(metadataNumber).filter((value): value is number => value != null) : [];
  if (eta <= 0) return "预计剩余 0 秒";
  if (range.length >= 2 && range[0] !== range[1]) {
    return `预计剩余 ${formatSeconds(range[0])}-${formatSeconds(range[1])}`;
  }
  return `预计剩余 ${formatSeconds(eta)}`;
}

function etaDetail(progress: Record<string, unknown>, language: Language = getLanguage()): string {
  const confidence = metadataString(progress.eta_confidence, "low");
  const source = metadataString(progress.eta_source, "fallback_default");
  const sampleCount = metadataNumber(progress.eta_sample_count) ?? 0;
  const confidenceLabel = ETA_CONFIDENCE_LABELS[confidence] || "估算不稳定";
  const sourceLabel = ETA_SOURCE_LABELS[source] || "未知记录";
  return formatUi("common.eta_value_value_value_records", [translateUi(confidenceLabel, language), translateUi(sourceLabel, language), sampleCount], language);
}

function metadataBooleanText(value: any): string {
  if (value === true) return "通过";
  if (value === false) return "未通过";
  return "-";
}

function distributionText(value: unknown, suffix = "", language: Language = getLanguage()): string {
  const rows = Object.entries(asRecord(value));
  if (!rows.length) return "暂无";
  return rows.map(([key, count]) => {
    const label = suffix === "星" ? formatUi("common.value_stars", [key], language) : `${key}${translateUi(suffix, language)}`;
    return `${label} ${metadataString(count)}`;
  }).join(" / ");
}

const RELEASE_PROBLEM_LABELS: Record<string, string> = {
  manifest_missing: "清单缺失",
  manifest_unreadable: "清单不可读",
  manifest_version_mismatch: "版本不符",
  missing_files: "缺文件",
  changed_files: "文件变化",
  extra_files: "额外文件",
  component_mismatches: "组件漂移",
  dependency_mismatches: "依赖漂移",
};

function releaseProblemCountSummary(value: any): string {
  const entries = Object.entries(asRecord(value))
    .map(([key, count]) => [key, Number(count)] as const)
    .filter(([, count]) => Number.isFinite(count) && count > 0);
  if (!entries.length) return "未发现问题";
  return entries.map(([key, count]) => `${RELEASE_PROBLEM_LABELS[key] || key} ${count}`).join(" / ");
}

function metadataStringArray(value: any): string[] {
  return Array.isArray(value) ? value.map((item) => metadataString(item)).filter((item) => item !== "-") : [];
}

function filterString(value: unknown, fallback = ""): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function sanitizeQuestionFilters(value: any): QuestionFilterState {
  const row = asRecord(value);
  return {
    q: filterString(row.q),
    source_name: filterString(row.source_name),
    difficulty_star: filterString(row.difficulty_star),
    premium: filterString(row.premium),
    status: filterString(row.status, DEFAULT_QUESTION_FILTERS.status),
  };
}

function normalizeSavedQuestionFilterPreset(value: any): SavedQuestionFilterPreset | null {
  const row = asRecord(value);
  const id = metadataString(row.id, "");
  const name = metadataString(row.name, "");
  if (!id || !name) return null;
  return {
    schema_version: "question_filter_preset_v1",
    id,
    name,
    filters: sanitizeQuestionFilters(row.filters),
    tag_ids: metadataStringArray(row.tag_ids),
    tag_labels: metadataStringArray(row.tag_labels),
    created_at: metadataString(row.created_at, ""),
    updated_at: metadataString(row.updated_at, ""),
  };
}

function loadSavedQuestionFilterPresets(): SavedQuestionFilterPreset[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(QUESTION_FILTER_PRESETS_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.map(normalizeSavedQuestionFilterPreset).filter((row): row is SavedQuestionFilterPreset => Boolean(row)).slice(0, 24) : [];
  } catch {
    return [];
  }
}

function qualitySuiteLabel(name: string): string {
  return QUALITY_SUITE_LABELS[name] || name;
}

function qualityStatusLabel(status: string): string {
  return QUALITY_STATUS_LABELS[status] || status;
}

function qualityGapLabel(gap: string): string {
  return QUALITY_GAP_LABELS[gap] || gap;
}

function qualityStatusClass(row: QualitySuiteEntry): string {
  if (!row.ok || row.status === "missing") return "error";
  if (row.status === "ready") return "success";
  if (row.status === "not_run") return "muted";
  return "warning";
}

function statusSeverityClass(severity: string): string {
  if (severity === "success" || severity === "warning" || severity === "error" || severity === "muted") return severity;
  return "neutral";
}

function qualityMetricHighlights(row: QualitySuiteEntry): string[] {
  const metrics = row.metrics || {};
  switch (row.name) {
    case "star_calibration":
      return [
        `数据库记录 ${metadataString(metrics.database_sample_count)}`,
        `星级分布 ${distributionText(metrics.difficulty_distribution, "星")}`,
        `标定样例 ${metadataString(metrics.fixed_case_count, "0")}`,
        `目标星级 ${distributionText(metrics.fixed_case_target_star_distribution, "星")}`,
        `已评测 ${metadataString(metrics.fixed_case_evaluated_count, "0")}`,
        `通过率 ${metadataString(metrics.fixed_case_pass_rate == null ? null : percent(Number(metrics.fixed_case_pass_rate)))}`,
      ];
    case "math_correctness":
      return [
        `记录 ${metadataString(metrics.sample_count)}`,
        `已验证答案 ${metadataString(metrics.verified_answer_specs)}`,
        `验证等级 ${distributionText(metrics.verification_levels)}`,
      ];
    case "elementary_solution_style":
      return [
        `生成 prompt ${metadataBooleanText(metrics.generator_v2_prompt_ok)}`,
        `审核 prompt ${metadataBooleanText(metrics.reviewer_v2_prompt_ok)}`,
      ];
    case "duplicate_attack":
      return [
        `题量 ${metadataString(metrics.question_count)}`,
        `规范重复组 ${metadataString(metrics.normalized_duplicate_groups)}`,
        `语义签名重复组 ${metadataString(metrics.semantic_hash_duplicate_groups)}`,
        `近似重复对 ${metadataString(metrics.semantic_near_duplicate_pairs)}`,
      ];
    case "prompt_injection":
    case "format_quality":
      return [
        `prompt ${metadataString(metrics.prompt_count)}`,
        `失败 ${metadataString(metrics.failure_count)}`,
        `固定样例 ${metadataString(metrics.fixed_case_count, "0")}`,
        `已评测 ${metadataString(metrics.fixed_case_evaluated_count, "0")}`,
        `通过率 ${metadataString(metrics.fixed_case_pass_rate == null ? null : percent(Number(metrics.fixed_case_pass_rate)))}`,
      ];
    case "answer_judge":
      return [
        `复核 prompt ${metadataBooleanText(metrics.answer_judge_prompt_ok)}`,
        `注入防护 ${metadataBooleanText(metrics.answer_judge_injection_guard_ok)}`,
        `固定样例 ${metadataString(metrics.fixed_case_count, "0")}`,
        `已评测 ${metadataString(metrics.fixed_case_evaluated_count, "0")}`,
        `通过率 ${metadataString(metrics.fixed_case_pass_rate == null ? null : percent(Number(metrics.fixed_case_pass_rate)))}`,
      ];
    case "report_faithfulness":
      return [
        `报告 prompt ${metadataBooleanText(metrics.reporter_prompt_ok)}`,
        `固定样例 ${metadataString(metrics.fixed_case_count, "0")}`,
        `已评测 ${metadataString(metrics.fixed_case_evaluated_count, "0")}`,
        `通过率 ${metadataString(metrics.fixed_case_pass_rate == null ? null : percent(Number(metrics.fixed_case_pass_rate)))}`,
      ];
    case "cost_performance": {
      const costGuard = asRecord(metrics.cost_guard);
      const averageLatency = metadataNumber(metrics.average_latency_ms);
      return [
        `调用 ${metadataString(metrics.calls, "0")}`,
        `错误 ${metadataString(metrics.errors, "0")}`,
        `估算 ${formatUsd(metadataNumber(metrics.estimated_cost_usd))}`,
        `平均 ${averageLatency == null ? "-" : formatMs(averageLatency)}`,
        costGuard.ok === undefined ? "" : `预算 ${metadataBooleanText(costGuard.ok)}`,
      ].filter(Boolean);
    }
    default:
      return Object.entries(metrics).slice(0, 4).map(([key, value]) => `${key} ${metadataString(value)}`);
  }
}

function batchItemSourceLabel(value: string | null | undefined): string {
  if (value === "reused_ai") return "复用AI题";
  if (value === "new_ai") return "新生成AI题";
  if (value === "bank") return "题库题";
  if (value === "mastery_variant") return "批量类似题";
  return "等待来源";
}

function blueprintCurveLabel(value: any): string {
  switch (String(value || "")) {
    case "same_star_warmup_to_challenge":
      return "同星级暖身到挑战";
    case "same_star_compact":
      return "同星级紧凑套题";
    default:
      return metadataString(value);
  }
}

function blueprintPhaseLabel(value: any): string {
  switch (String(value || "")) {
    case "warmup":
      return "暖身";
    case "core":
      return "核心";
    case "challenge":
      return "挑战";
    case "single":
      return "单题";
    default:
      return metadataString(value);
  }
}

function batchItemReuseReason(value: string | null | undefined): string {
  if (value === "reused_existing_ai_question") return "命中未做过相近 AI 题";
  if (value === "generated_new_question") return "模型新生成并通过审核";
  if (value === "forced_new_generation") return "已关闭复用，强制新生成";
  if (value === "no_reusable_ai_question_available") return "复用池不足，转为新生成";
  return "";
}

const BATCH_FAILURE_REASON_LABELS: Record<string, string> = {
  near_duplicate_in_final_batch_review: "集合近重复",
  near_duplicate_in_batch: "批次内近重复",
  no_candidate_accepted: "候选均未通过",
  provider_failed: "provider 失败",
  job_timed_out: "任务超时",
  batch_cancelled: "用户中断",
  cancelled: "已取消",
  failed: "失败",
  rejected: "拒绝",
  timed_out: "超时",
};

function batchFailureReasonLabel(value: string): string {
  if (BATCH_FAILURE_REASON_LABELS[value]) return BATCH_FAILURE_REASON_LABELS[value];
  if (value.startsWith("batch_ID_not_top_up_eligible")) return "当前批次状态不可补题";
  return value;
}

function batchFailureReasonSummary(value: any, language: Language = getLanguage()): string {
  const rows = Object.entries(asRecord(value))
    .map(([reason, count]) => [reason, Number(count)] as const)
    .filter(([, count]) => Number.isFinite(count) && count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (!rows.length) return "暂无失败或拒绝原因";
  return rows.slice(0, 4).map(([reason, count]) => `${translateUi(batchFailureReasonLabel(reason), language)} ${count}`).join(" / ");
}

function batchQualityTierText(request: Record<string, unknown>, progress: Record<string, unknown>): string {
  const candidateCount = metadataNumber(progress.candidate_count) ?? metadataNumber(request.candidate_count) ?? 3;
  const sourceExampleCount = metadataNumber(progress.source_example_count) ?? metadataNumber(request.source_example_count) ?? 5;
  const aiNegativeExampleCount = metadataNumber(progress.ai_negative_example_count) ?? metadataNumber(request.ai_negative_example_count) ?? 3;
  if (candidateCount >= 5 || sourceExampleCount >= 8 || aiNegativeExampleCount >= 5) return "严格";
  if (candidateCount <= 1 && sourceExampleCount <= 3 && aiNegativeExampleCount <= 1) return "快速";
  return "均衡";
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

const METRIC_FILTER_LABELS: Record<string, string> = {
  source_name: "题源",
  difficulty_star: "星级",
  premium: "精品",
  tag_id: "标签",
  mode: "练习模式",
  date_from: "开始日期",
  date_to: "结束日期",
  practice_session_id: "Session",
  generation_batch_id: "Batch",
  learner_id: "学习者",
};

function metricFilterSummary(metric: MetricDefinition | undefined, language: Language): string {
  const filters = asRecord(metric?.filters);
  const rows = Object.entries(filters)
    .filter(([, value]) => {
      if (value == null || value === "") return false;
      if (Array.isArray(value)) return value.length > 0;
      return true;
    })
    .map(([key, value]) => {
      const label = translateUi(METRIC_FILTER_LABELS[key] || key, language);
      const displayValue = (item: unknown) => {
        const raw = metadataString(item);
        return /(?:^|_)ids?$/.test(key) ? raw : translateUi(raw, language);
      };
      const rendered = Array.isArray(value) ? value.map(displayValue).join(translateUi("common.separator.list", language)) : displayValue(value);
      return `${label}=${rendered}`;
    });
  return rows.length ? rows.join(translateUi("common.separator.sections", language)) : translateUi("common.all_current_data", language);
}

function MetricDetails({ metric }: { metric: MetricDefinition | undefined }) {
  const { language, outputLanguage, t, tf } = useI18n();
  if (!metric) return null;
  const updatedAt = metric.updated_at ? formatDateTime(metric.updated_at) : "暂无提交数据";
  const exclusions = metric.exclusions?.length ? metric.exclusions.join("、") : "无额外排除项";
  return (
    <details className="metricDetails">
              <summary>{t("reports.metric_details")}</summary>
      <dl>
        <dt>{t("common.definition")}</dt>
        <dd>{t(metric.basis)}</dd>
        <dt>{t("common.filter_scope")}</dt>
        <dd>{metricFilterSummary(metric, language)}</dd>
        <dt>{t("common.exclusions")}</dt>
        <dd>{t(exclusions)}</dd>
        <dt>{t("common.updated")}</dt>
        <dd>{t(updatedAt)}</dd>
      </dl>
    </details>
  );
}

function compactSource(value: string): string {
  return value.replace("_手工标签分类.json", "").replace(".json", "");
}

function historyGroupLabel(value: string | null | undefined): string {
  return value ? (HISTORY_GROUP_LABELS[value] || value) : "-";
}

function historyPerformanceLabel(value: HistoryPerformance): string {
  return HISTORY_PERFORMANCE_LABELS[value] || value;
}

function historyAidText(item: HistoryItem): string {
  const parts = [
    item.used_hint ? "用过提示" : "",
    item.solution_revealed ? "看过答案" : "",
    item.asked_explanation ? "问过讲解" : "",
  ].filter(Boolean);
  return parts.length ? parts.join("、") : "-";
}

function historyPerformanceDistributionText(counts: Partial<Record<HistoryPerformance, number>> | undefined): string {
  return (["green", "yellow", "red", "gray"] as HistoryPerformance[])
    .map((ID) => `${historyPerformanceLabel(ID)} ${counts?.[ID] || 0} 题`)
    .join("，");
}

function historyItemAriaLabel(item: HistoryItem, selected: boolean, language: Language): string {
  const ui = (value: string) => translateUi(value, language);
  const first = ui(item.first_correct == null ? "首答未判定" : item.first_correct ? "首答正确" : "首答错误");
  const final = ui(item.final_correct == null ? "终答未判定" : item.final_correct ? "终答正确" : "终答错误");
  const last = formatDateTime(item.last_answered_at) || ui("没有最后作答时间");
  const source = item.source_name ? compactSource(item.source_name) : ui("未知题源");
  const star = item.difficulty_star ? formatUi("common.value_stars", [item.difficulty_star], language) : ui("未标星级");
  const selectedText = ui(selected ? "已选中" : "未选中");
  return formatUi("practice.question_value_value_value_value_value_value_value_value", [item.position, selectedText, ui(item.performance_label), source, star, first, final, item.attempt_count, item.effective_wrong_count, ui(historyAidText(item)), last], language);
}

function heatmapCellAriaLabel(label: string, star: number, count: number, total: number, language: Language): string {
  const ratio = total ? percent(count / total) : "0%";
  return formatUi("reports.value_value_stars_value_questions_value_of_this_tag", [label, star, count, ratio], language);
}

function tagKindLabel(kind: string): string {
  return TAG_KIND_META[kind]?.label || kind;
}

function tagKindDescription(kind: string): string {
  return TAG_KIND_META[kind]?.description || "自定义或导入数据中的标签类型。";
}

function displayTagLabel(tag: Pick<TaxonomyTag, "kind" | "label">, language: Language): string {
  return tag.kind === "custom" ? tag.label : translateUi(tag.label, language);
}

function tagExplanation(tag: TaxonomyTag, language: Language = getLanguage()): string {
  if (tag.kind === "custom" && tag.description) return tag.description;
  return translateUi(tag.description || tagKindDescription(tag.kind), language);
}

function mergeTagRows(current: TaxonomyTag[], nextRows: TaxonomyTag[]): TaxonomyTag[] {
  const byId = new Map<string, TaxonomyTag>();
  [...current, ...nextRows].forEach((tag) => byId.set(tag.id, tag));
  return Array.from(byId.values());
}

function displayErrorType(value: string): string {
  return ERROR_LABELS[value] || value;
}

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiRequestError) {
    return error.message || fallback;
  }
  return error instanceof Error ? error.message : fallback;
}

function answerFormatGuide(spec: Record<string, unknown> | null | undefined): { title: string; lines: string[]; examples: string[] } | null {
  if (!spec) return null;
  const answerType = String(spec.answer_type || "text");
  const gradingMethod = String(spec.grading_method || "exact_normalized");
  const unit = String(spec.unit || "").trim();
  const orderSensitive = spec.order_sensitive !== false;
  const base = [
    `题型：${answerType}`,
    `判分：先规则判分；规则未通过或无法确定时，非单一数字题会自动交给答案复核 agent。`,
    unit ? `单位：可以写“${unit}”，系统判分时会尽量忽略单位差异。` : "单位：按题目要求填写；通常不用额外写单位。",
  ];
  if (["integer", "number"].includes(answerType)) return { title: "填写一个数字", lines: [...base, "只填最终数值即可；如果题目有多个问题，请按题目顺序写清楚。"], examples: ["<整数>", "<负整数>"] };
  if (answerType === "fraction") return { title: "填写分数", lines: [...base, "建议写成 分子/分母；带分数可写成“整数又分子/分母”。"], examples: ["<分子>/<分母>", "<整数>又<分子>/<分母>"] };
  if (answerType === "decimal") return { title: "填写小数", lines: [...base, "小数或整数均可；按题目允许误差判分。"], examples: ["<有限小数>", "<整数>"] };
  if (answerType === "multi_blank" || answerType === "ordered_tuple") return { title: orderSensitive ? "按顺序填写多个答案" : "填写多个答案，顺序不限", lines: [...base, "用逗号、分号、顿号、空格或括号分隔；每一项对应题目中的一问或一空。"], examples: ["<答案1>, <答案2>", "(<答案1>, <答案2>)"] };
  if (answerType === "set") return { title: "填写一组答案", lines: [...base, "答案顺序通常不影响判分；用逗号、顿号或大括号分隔。"], examples: ["{<答案1>, <答案2>, ...}", "<答案1>, <答案2>, ..."] };
  if (answerType === "choice") return { title: "填写选项", lines: [...base, "写选项字母即可，也可以写“选+字母”。"], examples: ["<选项字母>", "选<选项字母>"] };
  if (answerType === "expression") return { title: "填写表达式", lines: [...base, `写等价表达式，尽量用常见符号；当前规则：${gradingMethod}。`], examples: ["<表达式>", "<通项公式>"] };
  return { title: "填写文字结论", lines: [...base, "用一句完整答案说明结论；多个结论按题目顺序列出。"], examples: ["<按题目顺序写出各问结论>", "<用分号分隔多个结论>"] };
}
function selectionWarningText(value: string): string {
  if (value.startsWith("only_selected_")) return `当前题池只抽到 ${value.replace("only_selected_", "")} 题`;
  if (value.startsWith("excluded_done_")) return `已排除做过的 ${value.replace("excluded_done_", "")} 题`;
  if (value === "all_bank_only_questions_done") return "题库严格题池已全部做过";
  if (value === "all_ai_mixed_questions_done") return "AI 混合严格题池已全部做过";
  if (value === "all_ai_only_questions_done") return "AI 生成题池已全部做过";
  if (value === "ai_fill_incomplete") return "AI 补题未完全补齐";
  return value;
}

function interventionState(row: ActiveIntervention | null | undefined): string {
  const value = row?.payload?.state;
  return typeof value === "string" ? value : row?.prefetch_status || "";
}

function interventionPayloadString(row: ActiveIntervention | null | undefined, key: string): string {
  const value = row?.payload?.[key];
  return typeof value === "string" ? value : "";
}

function interventionPayloadStringArray(row: ActiveIntervention | null | undefined, key: string): string[] {
  const value = row?.payload?.[key];
  if (!Array.isArray(value)) return [];
  return value.map(String).filter(Boolean);
}

function masteryCheckStatusText(row: ActiveIntervention | null | undefined): string {
  const state = interventionState(row);
  if (state === "ready_to_start" || row?.prefetch_status === "ready") return "检测题已就绪";
  if (state === "attempted") return "检测题已尝试，可继续作答";
  if (state === "completed" || row?.prefetch_status === "completed") return "掌握检测已完成";
  if (state === "failed" || row?.prefetch_status === "failed") return "检测题生成失败";
  return "检测题生成中";
}

export default function Home() {
  const { language, outputLanguage, t, tf } = useI18n();
  const [activeWorkspace, setActiveWorkspace] = useState<Workspace>("practice");
  const [appMode, setAppMode] = useState<AppMode>("learner");
  const [operationStatus, setOperationStatus] = useState<OperationStatus>({ state: "idle", label: "等待连接后端" });
  const [apiConnection, setApiConnection] = useState<ApiConnectionState>({
    state: "checking",
    label: "正在检查本地 API",
    detail: "网页已打开，正在确认后台 API 服务是否就绪。",
    latency_ms: null,
    http_status: null,
    retry_count: 0,
  });
  const [taskCenter, setTaskCenter] = useState<TaskCenterResponse | null>(null);
  const [recoveryAgentRun, setRecoveryAgentRun] = useState<RecoveryAgentRun | null>(null);
  const [recoveryAgentHistory, setRecoveryAgentHistory] = useState<RecoveryAgentRun[]>([]);
  useEffect(() => {
    let disposed = false;
    api<{ ok: boolean; runs: RecoveryAgentRun[] }>(`/api/recovery/agent-runs?preferred_language=${encodeURIComponent(language)}`)
      .then((response) => {
        if (disposed) return;
        setRecoveryAgentHistory(response.runs);
        setRecoveryAgentRun((current) => {
          if (current && ["queued", "running"].includes(current.status)) return current;
          return response.runs.find((run) => run.report && run.output_language === language)
            || response.runs.find((run) => run.report) || current;
        });
      }).catch(() => undefined);
    return () => { disposed = true; };
  }, [language]);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantConversation, setAssistantConversation] = useState<AssistantConversation | null>(null);
  const [assistantMessages, setAssistantMessages] = useState<AssistantMessage[]>([]);
  const [assistantInput, setAssistantInput] = useState("");
  const [assistantBusy, setAssistantBusy] = useState(false);
  const [assistantLastResponse, setAssistantLastResponse] = useState<AssistantResponse | null>(null);
  const [assistantPendingConfirmations, setAssistantPendingConfirmations] = useState<AssistantConfirmation[]>([]);
  const [assistantConfirmationNotes, setAssistantConfirmationNotes] = useState<Record<string, string>>({});
  const [learners, setLearners] = useState<LearnerProfile[]>([]);
  const [activeLearnerId, setActiveLearnerId] = useState(() => (typeof window === "undefined" ? "" : window.localStorage.getItem(ACTIVE_LEARNER_KEY) || ""));
  const [newLearnerName, setNewLearnerName] = useState("");
  const [historyExportScope, setHistoryExportScope] = useState<"all" | "current_learner">("all");
  const [questions, setQuestions] = useState<Question[]>([]);
  const [tags, setTags] = useState<TaxonomyTag[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [evidenceDetail, setEvidenceDetail] = useState<EvidenceDetail | null>(null);
  const [questionDetails, setQuestionDetails] = useState<Record<string, QuestionDetail>>({});
  const [sessionId, setSessionId] = useState("");
  const [sessionQuestions, setSessionQuestions] = useState<string[]>([]);
  const [sessionState, setSessionState] = useState<SessionState | null>(null);
  const [questionOutcomes, setQuestionOutcomes] = useState<Record<string, QuestionOutcome>>({});
  const [helpState, setHelpState] = useState<HelpState | null>(null);
  const [answer, setAnswer] = useState("");
  const [thought, setThought] = useState("");
  const [hintAnswer, setHintAnswer] = useState("");
  const [hintThought, setHintThought] = useState("");
  const [hintAnswerResult, setHintAnswerResult] = useState<Record<string, unknown> | null>(null);
  const [practiceCount, setPracticeCount] = useState("5");
  const [practiceSourceName, setPracticeSourceName] = useState("");
  const [practiceDifficultyStar, setPracticeDifficultyStar] = useState("");
  const [practiceExcludeDone, setPracticeExcludeDone] = useState(true);
  const [masteryCheckCount, setMasteryCheckCount] = useState("1");
  const [questionDraftStatus, setQuestionDraftStatus] = useState("");
  const [questionDraftSavedAt, setQuestionDraftSavedAt] = useState("");
  const [selectedQuestion, setSelectedQuestion] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [lastSubmissionId, setLastSubmissionId] = useState("");
  const [answerJudgeResult, setAnswerJudgeResult] = useState<AnswerJudgeResult | null>(null);
  const [supportConcernType, setSupportConcernType] = useState<"format_issue" | "do_not_understand" | "suspected_error" | "other">("format_issue");
  const [supportMessage, setSupportMessage] = useState("");
  const [questionSupportResult, setQuestionSupportResult] = useState<QuestionSupportResult | null>(null);
  const [solutionExplanationMessage, setSolutionExplanationMessage] = useState("");
  const [solutionExplanationResult, setSolutionExplanationResult] = useState<SolutionExplanationResult | null>(null);
  const [job, setJob] = useState<Record<string, unknown> | null>(null);
  const [jobRetryPreview, setJobRetryPreview] = useState<RetryPreview | null>(null);
  const [batch, setBatch] = useState<GenerationBatch | null>(null);
  const [batchHistory, setBatchHistory] = useState<GenerationBatch[]>([]);
  const [batchPracticeSessionId, setBatchPracticeSessionId] = useState("");
  const [historyGroups, setHistoryGroups] = useState<HistoryGroup[]>([]);
  const [historyDetail, setHistoryDetail] = useState<HistoryDetail | null>(null);
  const [selectedHistoryGroupId, setSelectedHistoryGroupId] = useState("");
  const [selectedHistoryQuestionIds, setSelectedHistoryQuestionIds] = useState<string[]>([]);
  const [historyVariantSelection, setHistoryVariantSelection] = useState<Record<string, HistoryVariantSelection>>({});
  const [historyVariantCountPerQuestion, setHistoryVariantCountPerQuestion] = useState("1");
  const [historyFilters, setHistoryFilters] = useState({
    group_type: "",
    date_from: "",
    date_to: "",
    source_name: "",
    difficulty_star: "",
    tag_id: "",
    weak_only: false,
  });
  const [generationConstraintsEdited, setGenerationConstraintsEdited] = useState(false);
  const [batchRequest, setBatchRequest] = useState({
    count: "10",
    concurrency: "3",
    difficulty_star: "3",
    target_tags: "",
    custom_description: "",
    constraints: DEFAULT_GENERATION_CONSTRAINTS,
    prefer_reuse_existing_ai: true,
    candidate_count: "3",
    source_example_count: "5",
    ai_negative_example_count: "3",
    allow_budget_overrun: false,
    allow_reviewer_quality_downgrade: false,
  });
  // Only the untouched built-in default follows the UI language. Imported/user text is opaque.
  const generationConstraintsText = generationConstraintsEdited ? batchRequest.constraints : translateUi(DEFAULT_GENERATION_CONSTRAINTS, outputLanguage);
  const [modelSettings, setModelSettings] = useState<ModelSettings>({
    provider: "openai_compatible",
    base_url: "https://ai.liaobots2.work/v1",
    api_key: "",
    has_api_key: false,
  default_model: "gpt-5.6-terra",
  roles: {},
    star_generator_models: { le_3: "gpt-5.6-terra", star_4: "gpt-5.6-sol", star_5: "gpt-5.6-sol" },
    model_price_json: "",
    strict_reviewer_model_allowlist: "",
  });
  const [apiKeyDraft, setApiKeyDraft] = useState("");
  const [apiSetupRequest, setApiSetupRequest] = useState(0);
  const [modelSettingsState, setModelSettingsState] = useState<"loading" | "ready" | "error">("loading");
  const [startupSyncRevision, setStartupSyncRevision] = useState(0);
  const modelSettingsRequestRef = useRef(0);
  const reportRequestRef = useRef(0);
  const hideApiKeyAfterSave = true;
  const [apiTestResult, setApiTestResult] = useState<ModelTestResult | null>(null);
  const [aiUsage, setAiUsage] = useState<AiUsage | null>(null);
  const [qualitySuite, setQualitySuite] = useState<QualitySuiteReport | null>(null);
  const [qualityRunResult, setQualityRunResult] = useState<QualityFixedCaseRunResult | null>(null);
  const [errorCatalog, setErrorCatalog] = useState<ErrorCatalog | null>(null);
  const [errorCatalogSearch, setErrorCatalogSearch] = useState("");
  const [healthDetail, setHealthDetail] = useState<HealthDetail | null>(null);
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig | null>(null);
  const [statusCatalog, setStatusCatalog] = useState<StatusCatalog | null>(null);
  const [statusCatalogSearch, setStatusCatalogSearch] = useState("");
  const [promptRegistry, setpromptRegistry] = useState<promptRegistryResponse | null>(null);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [modelCapabilities, setModelCapabilities] = useState<ModelDiscovery["capabilities"]>({});
  const [modelError, setModelError] = useState("");
  const [clearHistoryResult, setClearHistoryResult] = useState<Record<string, number> | null>(null);
  const [learnerDeleteResult, setLearnerDeleteResult] = useState<LearnerDeleteResult | null>(null);
  const [historyImportResult, setHistoryImportResult] = useState<HistoryImportResult | null>(null);
  const [recoveryResult, setRecoveryResult] = useState<StaleRecoveryResult | null>(null);
  const [questionBankImportResult, setQuestionBankImportResult] = useState<QuestionBankImportResult | null>(null);
  const [questionDeleteResult, setQuestionDeleteResult] = useState<QuestionDeleteResult | null>(null);
  const [questionRepairs, setQuestionRepairs] = useState<Record<string, QuestionRepairRecord[]>>({});
  const [questionImpacts, setQuestionImpacts] = useState<Record<string, QuestionImpact>>({});
  const [questionRegradeResult, setQuestionRegradeResult] = useState<QuestionRegradeResult | null>(null);
  const [repairSuggestionResult, setRepairSuggestionResult] = useState<QuestionRepairSuggestionResult | null>(null);
  const [repairSuggestionUseMock, setRepairSuggestionUseMock] = useState(true);
  const [repairForm, setRepairForm] = useState({
    reason: "manual_fix",
    severity: "medium",
    question_v2: "",
    answer_spec: "",
    note: "",
    quarantine: false,
  });
  const [repairResult, setRepairResult] = useState<QuestionRepairRecord | null>(null);
  const [duplicateMergeForm, setDuplicateMergeForm] = useState({
    canonical_question_id: "",
    reason: "duplicate_question",
    retire_aliases: true,
  });
  const [duplicateMergeResult, setDuplicateMergeResult] = useState<QuestionDuplicateMergeResult | null>(null);
  const [highContrast, setHighContrast] = useState(false);
  const [fontScale, setFontScale] = useState(1);
  const [tagSearch, setTagSearch] = useState("");
  const [generationTagSearch, setGenerationTagSearch] = useState("");
  const [customTagForm, setCustomTagForm] = useState({ kind: "custom", label: "", description: "" });
  const [selectedTagIds, setSelectedTagIds] = useState<string[]>([]);
  const [questionFilters, setQuestionFilters] = useState<QuestionFilterState>(DEFAULT_QUESTION_FILTERS);
  const [savedQuestionFilters, setSavedQuestionFilters] = useState<SavedQuestionFilterPreset[]>(loadSavedQuestionFilterPresets);
  const [savedQuestionFilterName, setSavedQuestionFilterName] = useState("");
  const [selectedSavedQuestionFilterId, setSelectedSavedQuestionFilterId] = useState("");
  const [selectedBankQuestionIds, setSelectedBankQuestionIds] = useState<string[]>([]);
  const [questionOffset, setQuestionOffset] = useState(0);
  const [strictSourceMode, setStrictSourceMode] = useState<StrictSourceMode>("bank_only");
  const [strictTestRequest, setStrictTestRequest] = useState({
    count: "100",
    use_custom_quota: false,
    star_1: "10",
    star_2: "20",
    star_3: "40",
    star_4: "20",
    star_5: "10",
    fill_missing_with_ai: false,
  });
  const [reportFilters, setReportFilters] = useState({
    source_name: "",
    difficulty_star: "",
    premium: "",
    tag_id: "",
    mode: "",
    date_from: "",
    date_to: "",
  });
  const [reportType, setReportType] = useState<"history" | "session" | "generation_batch">("history");
  const [reportSessionId, setReportSessionId] = useState("");
  const [reportBatchId, setReportBatchId] = useState("");
  const [reportTargetSearch, setReportTargetSearch] = useState("");
  const [aiReport, setAiReport] = useState<AiReport | null>(null);
  const [reportBatchPlan, setReportBatchPlan] = useState<ReportBatchPlan | null>(null);
  const [reportBatchCount, setReportBatchCount] = useState("10");
  const [reportBatchGuidance, setReportBatchGuidance] = useState("");
  const [todayTasks, setTodayTasks] = useState<TodayTasks | null>(null);
  const [guideSection, setGuideSection] = useState(GUIDE_SECTIONS[0].id);
  const errorChartRef = useRef<HTMLDivElement | null>(null);
  const trendChartRef = useRef<HTMLDivElement | null>(null);
  const timeChartRef = useRef<HTMLDivElement | null>(null);
  const historyImportInputRef = useRef<HTMLInputElement | null>(null);
  const questionBankImportInputRef = useRef<HTMLInputElement | null>(null);
  const historyImportDryRunRef = useRef(false);
  const questionBankImportDryRunRef = useRef(false);
  const batchEventsRef = useRef<LanguageEventSource | null>(null);
  const batchPollAbortRef = useRef<{ batchId: string; stopped: boolean } | null>(null);
  const batchPracticeSessionRef = useRef("");
  const lastSyncedBatchAcceptedRef = useRef(0);
  const watchedBatchIdRef = useRef("");
  const lastBatchEventIdRef = useRef("");
  const batchStreamInterruptedRef = useRef(false);
  const lastBatchRecoveryNoticeEventIdRef = useRef("");
  const sessionCreateLocksRef = useRef<Record<string, boolean>>({});
  const questionDraftKeyRef = useRef("");
  const skipQuestionDraftSaveRef = useRef(false);

  const currentQuestion = useMemo(() => questions.find((q) => q.id === selectedQuestion), [questions, selectedQuestion]);
  const currentQuestionDetail = selectedQuestion ? questionDetails[selectedQuestion] : undefined;
  const currentQuestionRepairs = selectedQuestion ? questionRepairs[selectedQuestion] || [] : [];
  const currentQuestionImpact = selectedQuestion ? questionImpacts[selectedQuestion] : undefined;
  const selectedBankQuestionIdSet = useMemo(() => new Set(selectedBankQuestionIds), [selectedBankQuestionIds]);
  const visibleSelectedQuestionCount = questions.filter((question) => selectedBankQuestionIdSet.has(question.id)).length;
  const allVisibleQuestionsSelected = questions.length > 0 && visibleSelectedQuestionCount === questions.length;
  const latestDraftRepair = currentQuestionRepairs.find((row) => row.status === "draft") || null;
  const latestPublishedRepair = currentQuestionRepairs.find((row) => row.status === "published") || null;
  const currentStem = currentQuestionDetail?.question_v2?.content?.stem_md || currentQuestion?.stem_preview || t("practice.import_the_bank_then_select_a_question");
  const currentAnswerGuide = useMemo(() => answerFormatGuide(currentQuestionDetail?.answer_spec), [currentQuestionDetail]);
  const modelChoices = useMemo(() => Array.from(new Set([modelSettings.default_model, ...availableModels].filter(Boolean))).sort(), [availableModels, modelSettings.default_model]);
  function renderModelSelect(value: string, onChange: (model: string) => void, fallbackLabel = "使用默认模型") {
    const choices = Array.from(new Set([value, ...modelChoices].filter(Boolean))).sort();
    return (
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">{t(fallbackLabel)}</option>
        {choices.map((model) => <option key={model} value={model}>{model}</option>)}
      </select>
    );
  }
  const currentHelpState = helpState?.question_id === selectedQuestion ? helpState : null;
  const strictFocus = sessionState?.mode === "strict_test";
  const answerLocked = Boolean(!strictFocus && (currentHelpState?.solution_revealed || sessionState?.solution_revealed_question_ids?.includes(selectedQuestion)));
  const currentQuestionStateVersion = selectedQuestion ? questionStateVersion(sessionState, selectedQuestion) : 0;
  const currentQuestionDraftKey = practiceQuestionDraftKey(activeLearnerId, sessionId, selectedQuestion, currentQuestionStateVersion);
  const questionDraftStatusText = sessionId && selectedQuestion
    ? `${questionDraftStatus || "草稿未保存"}${questionDraftSavedAt ? ` · ${formatDateTime(questionDraftSavedAt)}` : ""}${strictFocus ? " · 草稿不计分" : ""}`
    : "";
  const breakDue = Boolean(sessionState?.next_breakpoint);
  const practiceProgress = sessionState?.question_ids.length ? sessionState.answered_count / sessionState.question_ids.length : 0;
  const activeInterventions = sessionState?.active_interventions || [];
  const activeInterventionQuestionIds = activeInterventions
    .map((row) => row.current_question_id)
    .filter(Boolean);
  const activeSessionBaseQueue = sessionQuestions.length ? sessionQuestions : (sessionState?.question_ids || []);
  const activeSessionQueue = [...activeSessionBaseQueue, ...activeInterventionQuestionIds.filter((id) => !activeSessionBaseQueue.includes(id))];
  const activeSessionQuestionIndex = selectedQuestion ? activeSessionQueue.indexOf(selectedQuestion) : -1;
  const selectedTags = selectedTagIds.map((id) => tags.find((tag) => tag.id === id)).filter((tag): tag is TaxonomyTag => Boolean(tag));
  const sourceOptions = useMemo(() => {
    const names = new Set<string>();
    questions.forEach((question) => names.add(question.source_name));
    summary?.by_source.forEach((row) => names.add(row.source_name));
    return Array.from(names).sort();
  }, [questions, summary]);
  const reportTargetOptions = useMemo(() => {
    const seen = new Set<string>();
    const rows: { key: string; label: string; displayLabel: string; report_type: "history" | "session" | "generation_batch"; practice_session_id?: string | null; generation_batch_id?: string | null }[] = [];
    const add = (row: Omit<(typeof rows)[number], "displayLabel">, displayLabel: string) => {
      if (seen.has(row.key)) return;
      seen.add(row.key);
      rows.push({ ...row, displayLabel });
    };
    const short = (value: string | null | undefined) => value ? value.slice(0, 8) : "-";
    const modeLabel = (value: string | null | undefined) => HISTORY_GROUP_LABELS[value || ""] || value || "练习";
    const progressLabel = (answered: number | null | undefined, total: number | null | undefined, localized = false) => localized ? tf("common.value_value_questions_dcd205b9", [answered ?? 0, total ?? 0]) : `${answered ?? 0}/${total ?? 0} 题`;
    const sessionLabel = (state: SessionState | null, fallbackId: string, fallbackMode = "practice", localized = false) => {
      if (state?.display_name) return localized ? displayAutoTitle(state.display_name, language) : state.display_name;
      const total = state?.question_ids.length ?? sessionQuestions.length;
      const mode = modeLabel(state?.mode || fallbackMode);
      return `${localized ? t(mode) : mode} · ${progressLabel(state?.answered_count, total, localized)} · #${short(fallbackId)}`;
    };
    const batchKind = (row: GenerationBatch) => asRecord(row.request).origin === "mastery_variant_batch" ? "类似题批次" : "出题批次";
    const batchLabel = (row: GenerationBatch, localized = false) => row.display_name
      ? (localized ? displayAutoTitle(row.display_name, language) : row.display_name)
      : `${localized ? t(batchKind(row)) : batchKind(row)} · ${row.status} · ${progressLabel(row.accepted_count, row.target_count, localized)} · #${short(row.id)}`;
    if (sessionId && sessionState?.id === sessionId) {
      add({ key: `session:${sessionId}`, label: `当前练习 · ${sessionLabel(sessionState, sessionId)} · Session ${short(sessionId)}`, report_type: "session", practice_session_id: sessionId }, tf("practice.current_practice_value_session_value", [sessionLabel(sessionState, sessionId, "practice", true), short(sessionId)]));
    }
    if (batchPracticeSessionId && sessionState?.id === batchPracticeSessionId) add({ key: `session:${batchPracticeSessionId}`, label: `当前套题练习 · ${sessionLabel(sessionState, batchPracticeSessionId, "generated_practice")} · Session ${short(batchPracticeSessionId)}`, report_type: "session", practice_session_id: batchPracticeSessionId }, tf("practice.current_set_practice_value_session_value", [sessionLabel(sessionState, batchPracticeSessionId, "generated_practice", true), short(batchPracticeSessionId)]));
    if (batch?.id) add({ key: `batch:${batch.id}`, label: `当前${batchKind(batch)} · ${batchLabel(batch)} · Batch ${short(batch.id)}`, report_type: "generation_batch", generation_batch_id: batch.id }, tf("generation.current_value_value_batch_value", [t(batchKind(batch)), batchLabel(batch, true), short(batch.id)]));
    batchHistory.forEach((row) => add({ key: `batch:${row.id}`, label: `批次历史 · ${batchKind(row)} · ${batchLabel(row)} · Batch ${short(row.id)}`, report_type: "generation_batch", generation_batch_id: row.id }, `${t("generation.batch_history")} · ${t(batchKind(row))} · ${batchLabel(row, true)} · Batch ${short(row.id)}`));
    historyGroups.forEach((group) => {
      const time = group.created_at ? formatDateTime(group.created_at) : "";
      const prefix = [group.title, modeLabel(group.group_type), group.status || "", time].filter(Boolean).join(" · ");
      const displayPrefix = [displayAutoTitle(group.title, language), t(modeLabel(group.group_type)), group.status || "", time].filter(Boolean).join(" · ");
      if (group.report_type === "generation_batch" && group.generation_batch_id) add({ key: `batch:${group.generation_batch_id}`, label: `${prefix} · Batch ${short(group.generation_batch_id)}`, report_type: "generation_batch", generation_batch_id: group.generation_batch_id }, `${displayPrefix} · Batch ${short(group.generation_batch_id)}`);
      else if (group.session_id) add({ key: `session:${group.session_id}`, label: `${prefix} · Session ${short(group.session_id)}`, report_type: "session", practice_session_id: group.session_id }, `${displayPrefix} · Session ${short(group.session_id)}`);
    });
    const needle = reportTargetSearch.trim().toLowerCase();
    if (!needle) return rows.slice(0, 30);
    return rows.filter((row) => `${row.label} ${row.displayLabel} ${row.practice_session_id || ""} ${row.generation_batch_id || ""}`.toLowerCase().includes(needle)).slice(0, 30);
  }, [batch, batchHistory, batchPracticeSessionId, historyGroups, reportTargetSearch, sessionId, sessionQuestions.length, sessionState, language, t, tf]);
  const visibleTags = useMemo(() => {
    const needle = tagSearch.trim().toLowerCase();
    const rows = needle ? tags.filter((tag) => `${tag.kind} ${tag.label} ${t(tagKindLabel(tag.kind))} ${displayTagLabel(tag, language)} ${tagExplanation(tag, language)}`.toLowerCase().includes(needle)) : tags;
    return rows.slice(0, 120);
  }, [tags, tagSearch, language, t]);
  const groupedTags = useMemo(() => {
    const groups: Record<string, TaxonomyTag[]> = {};
    visibleTags.forEach((tag) => {
      groups[tag.kind] = [...(groups[tag.kind] || []), tag];
    });
    return groups;
  }, [visibleTags]);
  const visibleGenerationTags = useMemo(() => {
    const needle = generationTagSearch.trim().toLowerCase();
    const rows = needle ? tags.filter((tag) => `${tag.kind} ${tag.label} ${tag.description || ""} ${t(tagKindLabel(tag.kind))} ${displayTagLabel(tag, language)} ${tagExplanation(tag, language)}`.toLowerCase().includes(needle)) : tags;
    return rows.slice(0, 180);
  }, [tags, generationTagSearch, language, t]);
  const groupedGenerationTags = useMemo(() => {
    const groups: Record<string, TaxonomyTag[]> = {};
    visibleGenerationTags.forEach((tag) => {
      groups[tag.kind] = [...(groups[tag.kind] || []), tag];
    });
    return groups;
  }, [visibleGenerationTags]);
  const promptGroups = useMemo(() => {
    const groups: Record<string, promptTemplateItem[]> = {};
    (promptRegistry?.items || []).forEach((item) => {
      groups[item.role] = [...(groups[item.role] || []), item];
    });
    for (const role of Object.keys(groups)) {
      groups[role].sort((a, b) => a.version.localeCompare(b.version));
    }
    return groups;
  }, [promptRegistry]);
  const orderedQualitySuites = useMemo(() => {
    const byName = new Map((qualitySuite?.suites || []).map((row) => [row.name, row]));
    const ordered = (qualitySuite?.suite_names || []).map((name) => byName.get(name)).filter((row): row is QualitySuiteEntry => Boolean(row));
    const extras = (qualitySuite?.suites || []).filter((row) => !byName.has(row.name) || !(qualitySuite?.suite_names || []).includes(row.name));
    return [...ordered, ...extras];
  }, [qualitySuite]);
  const visibleErrorCatalogItems = useMemo(() => {
    const rows = errorCatalog?.items || [];
    const needle = errorCatalogSearch.trim().toLowerCase();
    if (!needle) return rows.slice(0, 80);
    return rows.filter((row) => (
      row.error_code.toLowerCase().includes(needle)
      || row.user_message.toLowerCase().includes(needle)
      || row.action.toLowerCase().includes(needle)
      || (row.match_key || "").toLowerCase().includes(needle)
    )).slice(0, 80);
  }, [errorCatalog, errorCatalogSearch]);
  const runtimeConfigSecrets = useMemo(() => (runtimeConfig?.settings || []).filter((item) => item.redacted), [runtimeConfig]);
  const runtimeConfigHighlights = useMemo(() => {
    const highlightNames = new Set(["model_provider", "default_model", "database_url", "question_data_dir", "ai_global_max_concurrency", "ai_job_timeout_seconds", "api_request_log_path"]);
    return (runtimeConfig?.settings || []).filter((item) => highlightNames.has(item.name)).slice(0, 10);
  }, [runtimeConfig]);
  const runtimeFeatureFlags = useMemo(() => Object.entries(runtimeConfig?.feature_flags || {}).slice(0, 12), [runtimeConfig]);
  const visibleStatusCatalogDomains = useMemo(() => {
    const rows = statusCatalog?.domains || [];
    const needle = statusCatalogSearch.trim().toLowerCase();
    if (!needle) return rows;
    return rows
      .map((domain) => {
        const domainMatches = (
          domain.domain.toLowerCase().includes(needle)
          || domain.label.toLowerCase().includes(needle)
          || domain.description.toLowerCase().includes(needle)
          || domain.model_fields.some((field) => field.toLowerCase().includes(needle))
        );
        return {
          ...domain,
          statuses: domainMatches ? domain.statuses : domain.statuses.filter((row) => (
            row.value.toLowerCase().includes(needle)
            || row.label.toLowerCase().includes(needle)
            || row.description.toLowerCase().includes(needle)
          )),
        };
      })
      .filter((domain) => (
        domain.statuses.length > 0
      ));
  }, [statusCatalog, statusCatalogSearch]);
  const reportHasPracticeData = Boolean(summary?.submission_summary?.submissions);
  const sessionRemaining = sessionState ? Math.max(0, sessionState.question_ids.length - sessionState.answered_count) : 0;
  const sessionSelection = (sessionState?.config?.selection || {}) as Record<string, unknown>;
  const sessionSelectionPolicy = (sessionSelection.policy || {}) as Record<string, unknown>;
  const sessionStrictSourceMode = (sessionSelectionPolicy.strict_source_mode as StrictSourceMode) || "bank_only";
  const sessionSelectionWarnings = Array.isArray(sessionSelection.selection_warnings) ? sessionSelection.selection_warnings.map(String) : [];
  const requestedBatchCount = clampedNumber(batchRequest.count, 1, 50, 10);
  const requestedBatchConcurrency = clampedNumber(batchRequest.concurrency, 1, 8, 3);
  const requestedCandidateCount = clampedNumber(batchRequest.candidate_count, 1, 5, 3);
  const requestedSourceExampleCount = clampedNumber(batchRequest.source_example_count, 0, 8, 5);
  const requestedAiNegativeExampleCount = clampedNumber(batchRequest.ai_negative_example_count, 0, 5, 3);
  const batchProgress = batch ? Math.round(((batch.accepted_count || 0) / Math.max(1, batch.target_count)) * 100) : 0;
  const batchReusedCount = batch ? Number(batch.progress?.reused_count || 0) : 0;
  const batchGeneratedCount = batch ? Number(batch.progress?.generated_count || 0) : 0;
  const batchRunningCount = batch ? Number(batch.progress?.running_count || 0) : 0;
  const batchCallBudget = asRecord(batch?.progress?.call_budget);
  const batchCallBudgetLimits = asRecord(batchCallBudget.limits);
  const batchRequestSnapshot = asRecord(batch?.request);
  const batchProgressSnapshot = asRecord(batch?.progress);
  const activeBatchOrigin = metadataString(batchRequestSnapshot.origin, "");
  const activeBatchIsMasteryVariant = activeBatchOrigin === "mastery_variant_batch";
  const activeBatchLabel = activeBatchIsMasteryVariant ? "批量类似题" : "套题出题";
  const batchRemainingTargetCount = batch ? Math.max(0, metadataNumber(batchProgressSnapshot.remaining_target_count) ?? (batch.target_count - batch.accepted_count)) : 0;
  const batchCandidateCount = metadataNumber(batchProgressSnapshot.candidate_count) ?? metadataNumber(batchRequestSnapshot.candidate_count) ?? 3;
  const batchReviewerMultiplier = metadataNumber(batchCallBudget.reviewer_call_multiplier) ?? 1;
  const batchRemainingGenerationCalls = batchRemainingTargetCount * batchCandidateCount;
  const batchRemainingReviewerCalls = batchRemainingGenerationCalls * batchReviewerMultiplier;
  const batchQualityTier = batch ? batchQualityTierText(batchRequestSnapshot, batchProgressSnapshot) : "-";
  const batchFailureSummary = batch ? batchFailureReasonSummary(batchProgressSnapshot.failure_reason_counts, language) : "暂无失败或拒绝原因";
  const batchTopUp = asRecord(batch?.progress?.top_up);
  const batchTopUpPlan = asRecord(batchTopUp.plan);
  const batchTopUpCallBudget = asRecord(batchTopUp.call_budget);
  const batchBlueprint = asRecord(batch?.progress?.batch_blueprint || batch?.request?.batch_blueprint);
  const batchBlueprintReview = asRecord(batch?.progress?.blueprint_review);
  const batchBlueprintSlots = Array.isArray(batchBlueprint.slots) ? batchBlueprint.slots.map(asRecord) : [];
  const batchMissingBlueprintSlots = Array.isArray(batchBlueprintReview.missing_slot_ids) ? batchBlueprintReview.missing_slot_ids.map(String) : [];
  const batchTopUpEligibleStatus = batch ? !activeBatchIsMasteryVariant && ["partial", "failed", "timed_out"].includes(batch.status) : false;
  const batchNeedsTopUp = batch ? batch.accepted_count < batch.target_count : false;
  const batchCreatedCount = batch ? (metadataNumber(batchProgressSnapshot.created_count) ?? batch.items.length) : 0;
  const batchMaxAttempts = batch ? (activeBatchIsMasteryVariant ? batch.target_count : (metadataNumber(batchProgressSnapshot.max_attempts) ?? metadataNumber(batchCallBudget.max_items) ?? batch.target_count * 4)) : 0;
  const batchAttemptLimitExhausted = Boolean(batchProgressSnapshot.attempt_limit_exhausted);
  const batchConcurrencyWasClamped = batchRequest.concurrency.trim() !== "" && Number(batchRequest.concurrency) !== requestedBatchConcurrency;
  const batchEtaSummary = batch ? etaSummary(batch.progress, batch.eta_seconds) : "预计剩余 -";
  const batchEtaDetail = batch ? etaDetail(batch.progress, language) : "";
  const batchProgressStatusText = batch
    ? tf("common.value_value_status_value_progress_value_value_attempts_value", [t(activeBatchLabel), batch.id.slice(0, 8), batch.status, batch.accepted_count, batch.target_count, batchCreatedCount, batchMaxAttempts, t(batchEtaSummary), batchEtaDetail, t(batchQualityTier), batchReusedCount, batchGeneratedCount, batchRunningCount, batch.rejected_count, batch.failed_count, t(batchFailureSummary)])
    : "";
  const jobProgress = asRecord(job?.progress);
  const currentJobId = typeof job?.id === "string" ? job.id : "";
  const currentJobStatus = typeof job?.status === "string" ? job.status : "";
  const jobRetryableStatus = ["failed", "rejected", "cancelled", "timed_out"].includes(currentJobStatus);
  const activeRetryPreview = jobRetryPreview?.job_id === currentJobId ? jobRetryPreview : null;
  const jobRetryConstraints = activeRetryPreview?.regeneration_constraints || [];
  const jobRetryReasonCodes = activeRetryPreview?.regeneration_reason_codes || [];
  const jobCallBudget = asRecord(jobProgress.call_budget);
  const jobModelSelection = asRecord(jobProgress.model_selection);
  const jobReviewerPolicy = asRecord(jobModelSelection.reviewer_independence_policy);
  const jobQualityTier = String(jobReviewerPolicy.quality_tier || "-");
  const jobSameReviewerModel = Boolean(jobReviewerPolicy.same_generator_reviewer_model);
  const jobQualityDegraded = Boolean(jobReviewerPolicy.quality_degraded);
  const requestedStrictCount = clampedNumber(strictTestRequest.count, 1, 100, 100);
  const requestedStrictStarQuota = {
    1: clampedNumber(strictTestRequest.star_1, 0, 100, 0),
    2: clampedNumber(strictTestRequest.star_2, 0, 100, 0),
    3: clampedNumber(strictTestRequest.star_3, 0, 100, 0),
    4: clampedNumber(strictTestRequest.star_4, 0, 100, 0),
    5: clampedNumber(strictTestRequest.star_5, 0, 100, 0),
  };
  const requestedStrictStarQuotaTotal = Object.values(requestedStrictStarQuota).reduce((sum, value) => sum + value, 0);
  const requestedStrictGeneration = requestedCandidateCount >= 5 || requestedSourceExampleCount >= 8 || requestedAiNegativeExampleCount >= 5;
  const requestedReviewerMultiplier = requestedStrictGeneration ? 2 : 1;
  const peakGeneratorCalls = requestedBatchConcurrency * requestedCandidateCount;
  const peakReviewerCalls = requestedBatchConcurrency * requestedCandidateCount * requestedReviewerMultiplier;
  const providerGlobalConcurrencyLimit = metadataNumber((runtimeConfig?.settings || []).find((item) => item.name === "ai_global_max_concurrency")?.value);
  const effectiveProviderPeak = providerGlobalConcurrencyLimit ? Math.min(providerGlobalConcurrencyLimit, Math.max(peakGeneratorCalls, peakReviewerCalls)) : null;
  const totalWorstCaseCandidateCalls = requestedBatchCount * requestedCandidateCount;
  const totalWorstCaseReviewerCalls = totalWorstCaseCandidateCalls * requestedReviewerMultiplier;
  const batchAcceptedItems = (batch?.items || []).filter((item) => item.status === "accepted" && item.question_id);
  const historyFocusedItem = historyDetail?.items.find((item) => selectedHistoryQuestionIds.includes(item.question_id)) || historyDetail?.items[0] || null;
  const historySelectedCount = selectedHistoryQuestionIds.length;
  const historyVariantSelectedRows = Object.values(historyVariantSelection);
  const historyVariantSelectedCount = historyVariantSelectedRows.length;
  const requestedHistoryVariantCount = clampedNumber(historyVariantCountPerQuestion, 1, 3, 1);
  const historyVariantTargetCount = historyVariantSelectedCount * requestedHistoryVariantCount;
  const workspaceItems = WORKSPACES.filter((item) => appMode === "admin" || item.learner);
  const hintForCurrentOriginal = activeInterventions.find(
    (row) => row.intervention_type !== "mastery_check" && row.original_question_id === selectedQuestion,
  ) || null;
  const hintForSelectedQuestion = activeInterventions.find(
    (row) => row.intervention_type !== "mastery_check" && row.current_question_id === selectedQuestion && row.current_question_id !== row.original_question_id,
  ) || null;
  const currentHintIntervention = hintForSelectedQuestion || hintForCurrentOriginal;
  const currentHintQuestionId = currentHintIntervention && currentHintIntervention.current_question_id !== currentHintIntervention.original_question_id
    ? currentHintIntervention.current_question_id
    : "";
  const currentQuestionIsHint = Boolean(hintForSelectedQuestion);
  const currentTemporaryHintPayload = asRecord(currentHintIntervention?.payload);
  const currentTemporaryHintQuestion = asRecord(currentTemporaryHintPayload.hint_question);
  const currentTemporaryHintAnswerSpec = asRecord(currentTemporaryHintQuestion.answer_spec);
  const currentTemporaryHintStem = typeof currentTemporaryHintQuestion.stem_md === "string" ? currentTemporaryHintQuestion.stem_md : "";
  const currentTemporaryHintIntro = typeof currentTemporaryHintQuestion.student_visible_intro === "string"
    ? currentTemporaryHintQuestion.student_visible_intro
    : typeof currentTemporaryHintPayload.hint_question === "object" && typeof currentTemporaryHintPayload.student_visible_intro === "string"
      ? currentTemporaryHintPayload.student_visible_intro
      : t("practice.start_with_a_small_question");
  const currentTemporaryHintReady = Boolean(currentHintIntervention && currentTemporaryHintPayload.storage === "temporary_hint_ai" && currentTemporaryHintStem);
  const currentTemporaryHintLastAttempt = asRecord(currentTemporaryHintPayload.last_student_attempt);
  const masteryChecksForCurrentOriginal = activeInterventions.filter(
    (row) => row.intervention_type === "mastery_check" && row.original_question_id === selectedQuestion,
  );
  const masteryCheckForCurrentOriginal = masteryChecksForCurrentOriginal.find((row) => ["ready_to_start", "attempted"].includes(interventionState(row)) || row.prefetch_status === "ready")
    || masteryChecksForCurrentOriginal.find((row) => row.status === "active")
    || masteryChecksForCurrentOriginal.find((row) => row.prefetch_status === "failed" || interventionState(row) === "failed")
    || masteryChecksForCurrentOriginal[0]
    || null;
  const masteryCheckForSelectedQuestion = activeInterventions.find(
    (row) => row.intervention_type === "mastery_check" && row.current_question_id === selectedQuestion && row.current_question_id !== row.original_question_id,
  ) || null;
  const currentMasteryCheck = masteryCheckForSelectedQuestion || masteryCheckForCurrentOriginal;
  const currentMasteryState = interventionState(currentMasteryCheck);
  const currentMasteryCheckQuestionIds = [
    ...interventionPayloadStringArray(currentMasteryCheck, "check_question_ids"),
    interventionPayloadString(currentMasteryCheck, "check_question_id"),
    currentMasteryCheck && currentMasteryCheck.current_question_id !== currentMasteryCheck.original_question_id ? currentMasteryCheck.current_question_id : "",
  ].filter(Boolean);
  const currentMasteryCheckQuestionId = currentMasteryCheckQuestionIds[0]
    || (currentMasteryCheck && currentMasteryCheck.current_question_id !== currentMasteryCheck.original_question_id ? currentMasteryCheck.current_question_id : "");
  const currentMasteryReady = Boolean(
    currentMasteryCheckQuestionId
    && (currentMasteryState === "ready_to_start" || currentMasteryCheck?.prefetch_status === "ready"),
  );
  const currentMasteryFailed = Boolean(currentMasteryCheck && (currentMasteryState === "failed" || currentMasteryCheck.prefetch_status === "failed"));
  const currentMasteryCompleted = Boolean(currentMasteryCheck && (currentMasteryState === "completed" || currentMasteryCheck.prefetch_status === "completed" || currentMasteryCheck.status === "completed"));
  const currentQuestionIsMasteryCheck = Boolean(masteryCheckForSelectedQuestion);
  const currentMasteryReadyCount = masteryChecksForCurrentOriginal.filter((row) => interventionState(row) === "ready_to_start" || row.prefetch_status === "ready").length + (masteryCheckForSelectedQuestion && currentQuestionIsMasteryCheck ? 1 : 0);
  const currentMasteryRequestedCount = metadataNumber(currentMasteryCheck?.payload?.sibling_count) || masteryChecksForCurrentOriginal.length || 1;
  const currentMasteryFailedReason = metadataString(currentMasteryCheck?.payload?.terminal_reason, "");

  function setStatus(state: OperationState, label: string, ui?: OperationStatus["ui"]) {
    setOperationStatus({ state, label, ui, ...IDDiagnosticsFromLabel(label) });
  }

  function todayTaskTitle(title: string) {
    // Decode only legacy system-generated titles, never question or user text.
    const resume = /^继续未完成的([^，]+)，还剩 (\d+) 题$/.exec(title);
    if (resume) return tf("learning.resume_task", [t(HISTORY_GROUP_LABELS[resume[1]] || resume[1]), resume[2]]);
    const check = /^做 (\d+) 道 (.+) × (\d+) 星检测题$/.exec(title);
    if (check) return tf("learning.check_task", [check[1], check[2], check[3]]);
    return t(displayAutoTitle(title, language));
  }

  async function checkApiConnection(showStatus = false): Promise<ApiConnectionState["state"]> {
    const started = performance.now();
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 3000);
    try {
      const response = await languageFetch(`${API_BASE}/api/health`, {
        cache: "no-store",
        signal: controller.signal,
        headers: { "X-Math-Platform-Client": "math-platform-web-readiness" },
      });
      const text = await response.text();
      const latency = Math.round(performance.now() - started);
      if (!response.ok) {
        const proxyRefused = /ECONNREFUSED|api proxy failed|connect/i.test(text);
        const next: ApiConnectionState = {
          state: proxyRefused ? "starting" : "offline",
          label: proxyRefused ? "本地 API 还在启动或被系统拦截" : `本地 API 返回 ${response.status}`,
          detail: proxyRefused
            ? "首次启动时请允许 Windows 安全/防火墙弹窗；通常等待 30-120 秒后会自动恢复。也可以点“重新检查”。"
            : (text || response.statusText || "API 返回异常状态").slice(0, 220),
          last_checked_at: new Date().toISOString(),
          latency_ms: latency,
          http_status: response.status,
          retry_count: apiConnection.retry_count + 1,
        };
        setApiConnection(next);
        if (showStatus) setStatus("error", `${next.label} · ${next.detail}`);
        return next.state;
      }
      const next: ApiConnectionState = {
        state: "online",
        label: "本地 API 已连接",
        detail: "后台服务已就绪；API Key 状态会在状态条实时显示。",
        last_checked_at: new Date().toISOString(),
        latency_ms: latency,
        http_status: response.status,
        retry_count: 0,
      };
      setApiConnection(next);
      if (showStatus) {
        setStartupSyncRevision(value => value + 1);
        setStatus("loading", "正在同步配置和报告数据");
      }
      return next.state;
    } catch (error) {
      const timeout = error instanceof DOMException && error.name === "AbortError";
      const next: ApiConnectionState = {
        state: timeout ? "starting" : "offline",
        label: timeout ? "本地 API 检查超时" : "本地 API 暂不可用",
        detail: timeout
          ? "后台服务可能仍在启动。首次启动或刚允许系统弹窗后，请等待片刻或点“重新检查”。"
          : "网页已打开，但后台 API 暂时连不上；如果持续失败，请运行 START_HERE，或用 launcher 的 stop/start 重启。",
        last_checked_at: new Date().toISOString(),
        latency_ms: null,
        http_status: null,
        retry_count: apiConnection.retry_count + 1,
      };
      setApiConnection(next);
      if (showStatus) setStatus("error", `${next.label} · ${next.detail}`);
      return next.state;
    } finally {
      window.clearTimeout(timer);
    }
  }

  function makeIdempotencyKey(scope: string) {
    const randomValue = typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return `${scope}:${randomValue}`;
  }

  async function copyStatusTraceId() {
    if (!operationStatus.traceId) return;
    try {
      await navigator.clipboard.writeText(operationStatus.traceId);
      setOperationStatus((current) => ({
        ...current,
        label: current.label.includes("degraded 已复制") ? current.label : `${current.label} · degraded 已复制`,
      }));
    } catch {
      setOperationStatus((current) => ({
        ...current,
        label: current.label.includes("复制失败") ? current.label : `${current.label} · 复制失败，请手动复制 degraded`,
      }));
    }
  }

  function openDiagnosticsFromStatus() {
    setAppMode("admin");
    setActiveWorkspace("admin");
    setStatus("success", "已切换到管理页，可导出诊断包；提交问题时请附带刚才的 degraded");
  }

  function clearCurrentQuestionDraft(label = "草稿已清理") {
    if (currentQuestionDraftKey) {
      removePracticeQuestionDraft(currentQuestionDraftKey);
    }
    skipQuestionDraftSaveRef.current = true;
    setQuestionDraftStatus(label);
    setQuestionDraftSavedAt("");
  }

  function clearCurrentSessionView() {
    clearCurrentQuestionDraft();
    setSessionId("");
    setSessionQuestions([]);
    setSessionState(null);
    setQuestionOutcomes({});
    setHelpState(null);
    setAnswer("");
    setThought("");
    setResult(null);
    setLastSubmissionId("");
    setAnswerJudgeResult(null);
    setQuestionSupportResult(null);
    setSolutionExplanationMessage("");
    setSolutionExplanationResult(null);
    setBatchPracticeSessionId("");
    batchPracticeSessionRef.current = "";
  }

  function applyModelSettings(settings: ModelSettings) {
    modelSettingsRequestRef.current += 1;
    setModelSettings({ ...settings, api_key: "", roles: settings.roles || {}, star_generator_models: settings.star_generator_models || {}, model_price_json: settings.model_price_json || "", strict_reviewer_model_allowlist: settings.strict_reviewer_model_allowlist || "" });
    setModelSettingsState("ready");
  }

  async function loadModelSettings(isCurrent: () => boolean = () => true) {
    const requestId = ++modelSettingsRequestRef.current;
    setModelSettingsState("loading");
    try {
      const settings = await api<ModelSettings>("/api/settings/models", { cache: "no-store", timeoutMs: 12000 });
      if (isCurrent() && requestId === modelSettingsRequestRef.current) applyModelSettings(settings);
    } catch (error) {
      if (isCurrent() && requestId === modelSettingsRequestRef.current) setModelSettingsState("error");
      throw error;
    }
  }

  function useMockModel() {
    if (apiConnection.state !== "online" || modelSettingsState !== "ready") {
      throw new Error(localizeUi("generation.model_settings_are_not_synchronized_yet_wait_or_use"));
    }
    return !modelSettings.has_api_key;
  }

  async function loadReportSummary(filters = reportFilters, learnerId = activeLearnerId, isCurrent: () => boolean = () => true) {
    const requestId = ++reportRequestRef.current;
    const report = await api<Summary>(reportQuery(filters, learnerId), { cache: "no-store" });
    if (isCurrent() && requestId === reportRequestRef.current) setSummary(report);
  }

  async function loadLearners(isCurrent: () => boolean = () => true) {
    const response = await api<{ default_learner_id: string; items: LearnerProfile[] }>("/api/learners");
    if (!isCurrent()) return "";
    setLearners(response.items);
    const saved = window.localStorage.getItem(ACTIVE_LEARNER_KEY) || "";
    const currentIsValid = activeLearnerId && response.items.some((row) => row.id === activeLearnerId);
    const savedIsValid = saved && response.items.some((row) => row.id === saved);
    const nextLearnerId = currentIsValid ? activeLearnerId : savedIsValid ? saved : response.default_learner_id || response.items[0]?.id || "";
    if (nextLearnerId) {
      setActiveLearnerId(nextLearnerId);
      window.localStorage.setItem(ACTIVE_LEARNER_KEY, nextLearnerId);
    }
    return nextLearnerId;
  }

  async function switchLearner(learnerId: string) {
    if (!learnerId || learnerId === activeLearnerId) return;
    setActiveLearnerId(learnerId);
    window.localStorage.setItem(ACTIVE_LEARNER_KEY, learnerId);
    clearCurrentSessionView();
    setSelectedHistoryGroupId("");
    setSelectedHistoryQuestionIds([]);
    setHistoryDetail(null);
    setReportType("history");
    setReportSessionId("");
    setReportBatchId("");
    await refresh(learnerId);
    if (activeWorkspace === "history") {
      await loadHistoryGroups(learnerId);
    }
    setStatus("success", "已切换学习者，当前任务台已清空");
  }

  async function createLearner() {
    const name = newLearnerName.trim();
    if (!name) {
      setStatus("error", "请输入学习者名称");
      return;
    }
    setStatus("loading", "正在创建学习者档案");
    try {
      const row = await api<LearnerProfile>("/api/learners", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      setNewLearnerName("");
      setLearners((current) => [...current.filter((item) => item.id !== row.id), row]);
      await switchLearner(row.id);
    } catch (error) {
      setStatus("error", errorMessage(error, "创建学习者失败"));
    }
  }

  function applyGenerationPreset(preset: GenerationPreset) {
    const values = GENERATION_PRESETS[preset];
    setBatchRequest((current) => ({
      ...current,
      candidate_count: values.candidate_count,
      source_example_count: values.source_example_count,
      ai_negative_example_count: values.ai_negative_example_count,
    }));
  }

  function reportQuery(filters = reportFilters, learnerId = activeLearnerId) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    if (learnerId) params.set("learner_id", learnerId);
    if (reportType === "session" && reportSessionId) params.set("practice_session_id", reportSessionId);
    if (reportType === "generation_batch" && reportBatchId) params.set("generation_batch_id", reportBatchId);
    const query = params.toString();
    return query ? `/api/reports/summary?${query}` : "/api/reports/summary";
  }

  function questionQuery(learnerId = activeLearnerId, offset = questionOffset, filters: QuestionFilterState = questionFilters, tagIds = selectedTagIds) {
    const params = new URLSearchParams();
    params.set("limit", String(QUESTION_PAGE_SIZE));
    params.set("offset", String(offset));
    if (learnerId) params.set("learner_id", learnerId);
    if (filters.q.trim()) params.set("q", filters.q.trim());
    if (filters.status) params.set("status", filters.status);
    if (filters.source_name) params.set("source_name", filters.source_name);
    if (filters.difficulty_star) params.append("difficulty_star", filters.difficulty_star);
    if (filters.premium) params.set("premium", filters.premium);
    tagIds.forEach((id) => params.append("tag_id", id));
    return `/api/questions?${params.toString()}`;
  }

  async function refresh(learnerId = activeLearnerId, nextQuestionOffset = questionOffset, filters: QuestionFilterState = questionFilters, tagIds = selectedTagIds, isCurrent: () => boolean = () => true) {
    setStatus("loading", "正在刷新数据");
    const tagQuery = tagSearch ? `/api/questions/tags?q=${encodeURIComponent(tagSearch)}` : "/api/questions/tags?limit=200";
    const results = await Promise.allSettled([
      api<Question[]>(questionQuery(learnerId, nextQuestionOffset, filters, tagIds)),
      loadReportSummary(reportFilters, learnerId, isCurrent),
      api<TodayTasks>(`/api/learning/today-tasks${learnerId ? `?learner_id=${encodeURIComponent(learnerId)}` : ""}`),
      api<TaskCenterResponse>("/api/tasks"),
    ]);
    if (!isCurrent()) return;
    api<TaxonomyTag[]>(tagQuery).then((rows) => { if (isCurrent()) setTags((current) => mergeTagRows(current, rows)); }).catch(() => undefined);
    const [questionRows, , tasks, systemTasks] = results;
    if (questionRows.status === "fulfilled") {
      setQuestions(questionRows.value);
      // Bank pagination must never replace the question in an active practice.
      setSelectedQuestion((current) => current || questionRows.value[0]?.id || "");
    }
    if (tasks.status === "fulfilled") setTodayTasks(tasks.value);
    if (systemTasks.status === "fulfilled") setTaskCenter(systemTasks.value);
    const failure = results.find(row => row.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
    setStatus("success", "已刷新");
  }

  useEffect(() => {
    const rawDraft = window.localStorage.getItem(PRACTICE_DRAFT_KEY);
    if (rawDraft) {
      try {
        const draft = JSON.parse(rawDraft) as {
          sessionId?: string;
          sessionQuestions?: string[];
          selectedQuestion?: string;
          answer?: string;
          thought?: string;
          activeWorkspace?: Workspace;
          appMode?: AppMode;
          activeLearnerId?: string;
          questionOutcomes?: Record<string, QuestionOutcome>;
          batchId?: string;
          batchPracticeSessionId?: string;
          reportType?: "history" | "session" | "generation_batch";
          reportBatchId?: string;
          reportSessionId?: string;
        };
        if (draft.activeLearnerId) setActiveLearnerId(draft.activeLearnerId);
        setSessionId(draft.sessionId || "");
        setSessionQuestions(Array.isArray(draft.sessionQuestions) ? draft.sessionQuestions : []);
        setSelectedQuestion(draft.selectedQuestion || "");
        setAnswer(draft.answer || "");
        setThought(draft.thought || "");
        setQuestionOutcomes(draft.questionOutcomes || {});
        if (draft.activeWorkspace) setActiveWorkspace(draft.activeWorkspace);
        if (draft.appMode) setAppMode(draft.appMode);
        if (draft.batchPracticeSessionId) {
          setBatchPracticeSessionId(draft.batchPracticeSessionId);
          batchPracticeSessionRef.current = draft.batchPracticeSessionId;
        }
        if (draft.reportType) setReportType(draft.reportType);
        if (draft.reportBatchId) setReportBatchId(draft.reportBatchId);
        if (draft.reportSessionId) setReportSessionId(draft.reportSessionId);
        if (draft.sessionId) {
          const expectedLearnerId = draft.activeLearnerId || window.localStorage.getItem(ACTIVE_LEARNER_KEY) || "";
          api<SessionState>(`/api/practice/sessions/${draft.sessionId}`)
            .then((state) => {
              if (expectedLearnerId && state.learner_id !== expectedLearnerId) {
                window.localStorage.removeItem(PRACTICE_DRAFT_KEY);
                clearCurrentSessionView();
                return;
              }
              setSessionState(state);
            })
            .catch((error) => {
              if (error instanceof ApiRequestError && error.status === 404) {
                window.localStorage.removeItem(PRACTICE_DRAFT_KEY);
                clearCurrentSessionView();
              }
            });
        }
        if (draft.batchId) {
          api<GenerationBatch>(`/api/generation/batches/${draft.batchId}`)
            .then((restoredBatch) => {
              updateBatchState(restoredBatch);
              if (!isTerminalGenerationBatchStatus(restoredBatch.status)) {
                watchBatch(restoredBatch.id);
                pollBatch(restoredBatch.id);
              }
            })
            .catch(() => undefined);
        }
      } catch {
        window.localStorage.removeItem(PRACTICE_DRAFT_KEY);
      }
    }
  }, []);

  useEffect(() => {
    if (apiConnection.state !== "online") {
      modelSettingsRequestRef.current += 1;
      setModelSettingsState("loading");
      return;
    }
    let cancelled = false;
    const isCurrent = () => !cancelled;
    setStatus("loading", "正在同步配置和报告数据");
    void synchronizeStartupReads([
      () => loadModelSettings(isCurrent),
      async () => {
        const learnerId = await loadLearners(isCurrent);
        if (isCurrent()) await refresh(learnerId, questionOffset, questionFilters, selectedTagIds, isCurrent);
      },
    ], isCurrent).then(failed => {
      if (isCurrent()) setStatus(failed ? "error" : "success", failed
        ? "部分数据同步失败，请点击重新检查或刷新全部；已有数据和密钥未被清除。"
        : "配置和报告数据已同步");
    });
    return () => { cancelled = true; };
  }, [apiConnection.state, startupSyncRevision]);

  useEffect(() => {
    const handle = window.setInterval(() => {
      void loadTaskCenter(false).catch(() => undefined);
      if (recoveryAgentRun?.id && ["queued", "running"].includes(recoveryAgentRun.status)) {
        void loadRecoveryAgentRun(recoveryAgentRun.id).catch(() => undefined);
      }
    }, 5000);
    return () => window.clearInterval(handle);
  }, [recoveryAgentRun?.id, recoveryAgentRun?.status]);

  useEffect(() => {
    if (!currentQuestionDraftKey) {
      questionDraftKeyRef.current = "";
      setQuestionDraftStatus("");
      setQuestionDraftSavedAt("");
      return;
    }
    if (answerLocked) {
      removePracticeQuestionDraft(currentQuestionDraftKey);
      questionDraftKeyRef.current = currentQuestionDraftKey;
      skipQuestionDraftSaveRef.current = true;
      setQuestionDraftStatus("本题已锁定，草稿已清理");
      setQuestionDraftSavedAt("");
      return;
    }
    if (questionDraftKeyRef.current === currentQuestionDraftKey) return;
    questionDraftKeyRef.current = currentQuestionDraftKey;
    skipQuestionDraftSaveRef.current = true;
    const draft = readPracticeQuestionDraft(currentQuestionDraftKey);
    if (draft) {
      setAnswer(draft.answer || "");
      setThought(draft.thought || "");
      setQuestionDraftStatus("已恢复草稿");
      setQuestionDraftSavedAt(draft.updated_at);
    } else {
      setAnswer("");
      setThought("");
      setQuestionDraftStatus("草稿未保存");
      setQuestionDraftSavedAt("");
    }
  }, [currentQuestionDraftKey, answerLocked]);

  useEffect(() => {
    if (!currentQuestionDraftKey || answerLocked) return;
    if (skipQuestionDraftSaveRef.current) {
      skipQuestionDraftSaveRef.current = false;
      return;
    }
    setQuestionDraftStatus("保存中");
    const handle = window.setTimeout(() => {
      try {
        if (!answer && !thought) {
          removePracticeQuestionDraft(currentQuestionDraftKey);
          setQuestionDraftStatus("草稿未保存");
          setQuestionDraftSavedAt("");
          return;
        }
        const updatedAt = new Date().toISOString();
        writePracticeQuestionDraft(currentQuestionDraftKey, {
          schema_version: "practice_question_draft_v1",
          active_learner_id: activeLearnerId || "",
          session_id: sessionId,
          question_id: selectedQuestion,
          state_version: currentQuestionStateVersion,
          answer,
          thought,
          updated_at: updatedAt,
        });
        setQuestionDraftStatus("草稿已保存");
        setQuestionDraftSavedAt(updatedAt);
      } catch {
        setQuestionDraftStatus("草稿保存失败");
      }
    }, 350);
    return () => window.clearTimeout(handle);
  }, [currentQuestionDraftKey, answer, thought, activeLearnerId, sessionId, selectedQuestion, currentQuestionStateVersion, answerLocked]);

  useEffect(() => {
    window.localStorage.setItem(PRACTICE_DRAFT_KEY, JSON.stringify({
      sessionId,
      sessionQuestions,
      selectedQuestion,
      activeWorkspace,
      appMode,
      activeLearnerId,
      questionOutcomes,
      batchId: batch?.id || "",
      batchPracticeSessionId,
      reportType,
      reportBatchId,
      reportSessionId,
    }));
  }, [sessionId, sessionQuestions, selectedQuestion, activeWorkspace, appMode, activeLearnerId, questionOutcomes, batch?.id, batchPracticeSessionId, reportType, reportBatchId, reportSessionId]);

  useEffect(() => {
    batchPracticeSessionRef.current = batchPracticeSessionId;
  }, [batchPracticeSessionId]);

  useEffect(() => {
    if (activeLearnerId) window.localStorage.setItem(ACTIVE_LEARNER_KEY, activeLearnerId);
  }, [activeLearnerId]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      const state = await checkApiConnection(false);
      if (cancelled) return;
      timer = window.setTimeout(poll, state === "online" ? 15000 : 3000);
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    return () => {
      batchEventsRef.current?.close();
    };
  }, []);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "auto" });
    if (apiConnection.state !== "online") return;
    if (activeWorkspace === "generated") {
      loadBatchHistory().catch(() => undefined);
    }
    if (activeWorkspace === "history") {
      loadHistoryGroups().catch((error) => setStatus("error", errorMessage(error, "历史组读取失败")));
    }
    if (activeWorkspace === "reports") {
      loadBatchHistory().catch(() => undefined);
      loadHistoryGroups().catch(() => undefined);
    }
    if (activeWorkspace === "admin" && appMode === "admin") {
      loadAiUsage(false).catch(() => undefined);
      loadQualitySuite(false).catch(() => undefined);
      loadErrorCatalog(false).catch(() => undefined);
      loadHealthDetails(false).catch(() => undefined);
      loadRuntimeConfig(false).catch(() => undefined);
      loadStatusCatalog(false).catch(() => undefined);
      loadpromptRegistry(false).catch(() => undefined);
    }
  }, [activeWorkspace, appMode, apiConnection.state, activeLearnerId]);

  useEffect(() => {
    if (activeWorkspace !== "reports" || apiConnection.state !== "online") return;
    let cancelled = false;
    void synchronizeStartupReads([
      () => loadReportSummary(reportFilters, activeLearnerId, () => !cancelled),
    ], () => !cancelled).then(failed => {
      if (!cancelled && failed) setStatus("error", "报告数据读取失败，请点击重新检查或刷新全部。");
    });
    return () => { cancelled = true; };
  }, [activeWorkspace, apiConnection.state, activeLearnerId, language]);

  useEffect(() => {
    if (!currentJobId || !jobRetryableStatus) {
      setJobRetryPreview(null);
      return;
    }
    let cancelled = false;
    api<RetryPreview>(`/api/generation/jobs/${currentJobId}/retry-preview`, {
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "retry preview",
    })
      .then((preview) => {
        if (!cancelled) setJobRetryPreview(preview);
      })
      .catch(() => {
        if (!cancelled) setJobRetryPreview(null);
      });
    return () => {
      cancelled = true;
    };
  }, [currentJobId, jobRetryableStatus]);

  useEffect(() => {
    api<TaxonomyTag[]>("/api/questions/tags?limit=200").then(setTags).catch(() => undefined);
  }, []);

  useEffect(() => {
    const query = generationTagSearch.trim();
    const timer = window.setTimeout(() => {
      const path = query ? `/api/questions/tags?q=${encodeURIComponent(query)}&limit=300` : "/api/questions/tags?limit=300";
      api<TaxonomyTag[]>(path)
        .then((rows) => setTags((current) => mergeTagRows(current, rows)))
        .catch(() => undefined);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [generationTagSearch]);

  useEffect(() => {
    const query = tagSearch.trim();
    const timer = window.setTimeout(() => {
      const path = query ? `/api/questions/tags?q=${encodeURIComponent(query)}&limit=300` : "/api/questions/tags?limit=300";
      api<TaxonomyTag[]>(path)
        .then((rows) => setTags((current) => mergeTagRows(current, rows)))
        .catch(() => undefined);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [tagSearch]);

  useEffect(() => {
    if (!summary || !errorChartRef.current || !trendChartRef.current || !timeChartRef.current) return;
    const errorChart = echarts.init(errorChartRef.current);
    const trendChart = echarts.init(trendChartRef.current);
    const timeChart = echarts.init(timeChartRef.current);
    const errorEntries = Object.entries(summary.error_types || {}).map(([key, value]) => [t(displayErrorType(key)), value] as const);
    const trendRows = summary.trend_by_day || [];
    const timeRows = summary.time_stats_by_star || [];
    errorChart.setOption({
      tooltip: { trigger: "axis" },
      grid: { left: 44, right: 12, top: 18, bottom: 42 },
      xAxis: { type: "category", data: errorEntries.map(([key]) => key), axisLabel: { rotate: 20, interval: 0 } },
      yAxis: { type: "value", minInterval: 1 },
      series: [{ type: "bar", data: errorEntries.map(([, value]) => value), itemStyle: { color: "#0f7b6c" } }],
    });
    trendChart.setOption({
      tooltip: {
        trigger: "axis",
        formatter: (params: any) => {
          const row = trendRows[chartDataIndex(params)];
          if (!row) return "";
          return t(`${row.date}<br />正确率 ${percent(row.accuracy)}<br />${row.correct}/${row.answered} · ${confidenceText(row.confidence)}`);
        },
      },
      grid: { left: 38, right: 12, top: 18, bottom: 30 },
      xAxis: { type: "category", data: trendRows.map((row) => row.date) },
      yAxis: { type: "value", min: 0, max: 1, axisLabel: { formatter: (value: number) => `${Math.round(value * 100)}%` } },
      series: [{ type: "line", smooth: true, data: trendRows.map((row) => row.accuracy ?? 0), itemStyle: { color: "#2563eb" } }],
    });
    timeChart.setOption({
      tooltip: {
        trigger: "axis",
        formatter: (params: any) => {
          const row = timeRows[chartDataIndex(params)];
          if (!row) return "";
          return t(`${row.difficulty_star}星<br />平均 ${formatMs(row.avg_elapsed_ms)}<br />记录 ${row.sample_size ?? row.count} · ${confidenceText(row.confidence)}<br />${row.basis || "按最终提交用时统计"}`);
        },
      },
      grid: { left: 50, right: 12, top: 18, bottom: 30 },
      xAxis: { type: "category", data: timeRows.map((row) => t(`${row.difficulty_star}星`)) },
      yAxis: { type: "value", axisLabel: { formatter: (value: number) => `${Math.round(value / 1000)}s` } },
      series: [{ type: "bar", data: timeRows.map((row) => row.avg_elapsed_ms ?? 0), itemStyle: { color: "#b84c1d" } }],
    });
    const resize = () => {
      errorChart.resize();
      trendChart.resize();
      timeChart.resize();
    };
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      errorChart.dispose();
      trendChart.dispose();
      timeChart.dispose();
    };
  }, [summary, language]);

  useEffect(() => {
    if (!selectedQuestion || questionDetails[selectedQuestion]) return;
    api<QuestionDetail>(`/api/questions/${selectedQuestion}?include_answer=true`)
      .then((detail) => setQuestionDetails((current) => ({ ...current, [selectedQuestion]: detail })))
      .catch((error) => setStatus("error", `题目详情读取失败：${error.message}`));
  }, [selectedQuestion, questionDetails]);

  useEffect(() => {
    if (activeWorkspace !== "bank" || !selectedQuestion) return;
    loadQuestionRepairs(selectedQuestion).catch(() => undefined);
    if (appMode === "admin") loadQuestionImpact(selectedQuestion, false).catch(() => undefined);
  }, [activeWorkspace, appMode, selectedQuestion]);

  useEffect(() => {
    setQuestionRegradeResult(null);
    setRepairSuggestionResult(null);
  }, [selectedQuestion]);

  useEffect(() => {
    if (!sessionId || !selectedQuestion) {
      setHelpState(null);
      return;
    }
    loadHelpState().catch(() => setHelpState(null));
  }, [sessionId, selectedQuestion, sessionState?.answered_count]);

  useEffect(() => {
    setAnswerJudgeResult(null);
    setSolutionExplanationResult(null);
    setSolutionExplanationMessage("");
    setLastSubmissionId("");
  }, [selectedQuestion]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        submitCurrentAnswer().catch((error) => setStatus("error", error.message));
      } else if (event.altKey && event.key.toLowerCase() === "s") {
        event.preventDefault();
        recordPracticeEvent("skipped").catch((error) => setStatus("error", error.message));
      } else if (event.altKey && event.key.toLowerCase() === "b") {
        event.preventDefault();
        recordPracticeEvent("bookmarked").catch((error) => setStatus("error", error.message));
      } else if (event.altKey && event.key.toLowerCase() === "u") {
        event.preventDefault();
        recordPracticeEvent("marked_uncertain").catch((error) => setStatus("error", error.message));
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [sessionId, selectedQuestion, answer, thought, breakDue]);

  function questionStatus(id: string): QueueStatus {
    if (sessionState?.solution_revealed_question_ids?.includes(id) || (currentHelpState?.solution_revealed && id === selectedQuestion)) return "revealed";
    if (id === selectedQuestion && (currentHelpState?.reached_level ?? 0) > 0 && questionOutcomes[id] !== "correct") return "hint";
    if (sessionState?.intervention_due_question_ids.includes(id)) return "hint";
    if (activeInterventions.some((row) => row.intervention_type !== "mastery_check" && (row.original_question_id === id || row.current_question_id === id))) return "hint";
    if (sessionState?.skipped_question_ids.includes(id)) return "skipped";
    if (questionOutcomes[id]) return questionOutcomes[id];
    if (activeInterventions.some((row) => row.intervention_type === "mastery_check" && (row.original_question_id === id || row.current_question_id === id))) return "check";
    return "todo";
  }

  function heatmapTone(count: number, total: number) {
    if (!count || !total) return 0;
    return Math.max(0.16, Math.min(1, count / total));
  }

  function toggleTag(tagId: string) {
    setQuestionOffset(0);
    setSelectedTagIds((current) => current.includes(tagId) ? current.filter((id) => id !== tagId) : [...current, tagId]);
  }

  function removeTag(tagId: string) {
    setQuestionOffset(0);
    setSelectedTagIds((current) => current.filter((id) => id !== tagId));
  }

  function clearSelectedTags() {
    setQuestionOffset(0);
    setSelectedTagIds([]);
  }

  async function createCustomTag() {
    const label = customTagForm.label.trim();
    if (!label) {
      setStatus("error", "请先填写自定义标签名称");
      return;
    }
    try {
      const created = await api<TaxonomyTag>("/api/questions/tags", {
        method: "POST",
        body: JSON.stringify({
          kind: customTagForm.kind || "custom",
          label,
          description: customTagForm.description.trim() || null,
        }),
      });
      setTags((current) => mergeTagRows(current, [created]));
      setSelectedTagIds((current) => current.includes(created.id) ? current : [...current, created.id]);
      setCustomTagForm({ kind: "custom", label: "", description: "" });
      setStatus("success", `已添加自定义标签：${created.label}`);
    } catch (error) {
      setStatus("error", errorMessage(error, "自定义标签保存失败"));
    }
  }

  function selectedGenerationTagNotes() {
    return selectedTags
      .filter((tag) => tag.description)
      .map((tag) => `${tag.label}：${tag.description}`)
      .join("\n");
  }

  function generationTargetTags() {
    const explicitTags = batchRequest.target_tags.split(/[,，;\s]+/).map((item) => item.trim()).filter(Boolean);
    const basketTags = selectedTags.map((tag) => tag.label);
    return Array.from(new Set([...basketTags, ...explicitTags]));
  }

  function updateRole(role: string, model: string) {
    setModelSettings((current) => ({ ...current, roles: { ...current.roles, [role]: model } }));
  }

  function updateStarGeneratorModel(bucket: string, model: string) {
    setModelSettings((current) => ({
      ...current,
      star_generator_models: { ...(current.star_generator_models || {}), [bucket]: model },
    }));
  }

  function applyRecommendedRoles() {
    const capability = modelCapabilities[modelSettings.default_model];
    const recommended = capability?.recommended_roles || Object.keys(ROLE_LABELS);
    setModelSettings((current) => {
      const nextRoles = { ...current.roles };
      recommended.forEach((role) => {
        nextRoles[role] = current.default_model;
      });
      return {
        ...current,
        roles: nextRoles,
        star_generator_models: {
          ...(current.star_generator_models || {}),
          le_3: "gpt-5.6-terra",
          star_4: "gpt-5.6-sol",
          star_5: "gpt-5.6-sol",
        },
      };
    });
  }

  function timelineRows(detail: EvidenceDetail) {
    return [
      ...detail.attempts.map((attempt) => ({
        id: attempt.submission_id,
        time: attempt.created_at,
        type: "提交",
        text: `${attempt.is_correct === true ? "正确" : attempt.is_correct === false ? "错误" : "待复核"} · ${displayErrorType(attempt.error_type)} · ${Math.round(attempt.elapsed_ms / 1000)} 秒${attempt.regrade?.is_regraded ? " · 已按修复重判" : ""}`,
      })),
      ...detail.events.map((eventRow) => ({
        id: eventRow.id,
        time: eventRow.created_at,
        type: EVENT_LABELS[eventRow.event_type] || eventRow.event_type,
        text: eventRow.event_type,
      })),
      ...detail.interventions.map((row) => ({
        id: row.id,
        time: row.created_at,
        type: "启发题",
        text: `${row.intervention_type} · ${row.status}`,
      })),
    ].sort((left, right) => left.time.localeCompare(right.time));
  }

  function taskStatusLabel(status: string) {
    const labels: Record<string, string> = {
      queued: "等待中",
      running: "运行中",
      generating: "生成中",
      cancelling: "取消中",
      cancelled: "已取消",
      completed: "已完成",
      partial: "部分完成",
      failed: "失败",
      timed_out: "超时",
      accepted: "已通过",
      rejected: "已拒绝",
      ready: "已就绪",
    };
    return labels[status] || status || "未知";
  }

  function taskStatusClass(status: string) {
    if (["completed", "accepted", "ready"].includes(status)) return "success";
    if (["failed", "timed_out", "rejected"].includes(status)) return "error";
    if (["running", "generating", "cancelling"].includes(status)) return "warning";
    if (status === "queued") return "queued";
    return "muted";
  }

  function taskTitleText(task: SystemTaskSummary) {
    if (task.target_type === "generation_batch") {
      const knownBatch = batch?.id === task.target_id ? batch : batchHistory.find((row) => row.id === task.target_id);
      const request = asRecord(knownBatch?.request);
      if (request.display_name || request.title) return task.title;
    }
    const builtins = SYSTEM_TASK_TITLES[task.target_type || ""] || [];
    return builtins.includes(task.title) ? t(task.title) : task.title;
  }

  function taskDescriptionText(task: SystemTaskSummary) {
    const description = task.description || "";
    if (task.target_type === "generation_batch") {
      const match = /^(\d+)\/(\d+) 可练[，,]\s*失败\/拒绝 (\d+)$/.exec(description);
      if (match) return tf("common.value_value_available_failed_rejected_value", match.slice(1));
    }
    if (task.target_type === "intervention_record") {
      const match = /^session (\S+) · 原题 (\S+)$/.exec(description);
      if (match) return tf("practice.session_value_original_question_value", match.slice(1));
    }
    if (task.target_type === "recovery_agent_run") {
      const match = /^(\d+)\/(\d+) 轮 · (.+)$/.exec(description);
      if (match) return tf("common.value_value_rounds_value", match.slice(1));
    }
    return description;
  }

  function taskProgressText(task: SystemTaskSummary) {
    const progress = task.progress || {};
    const accepted = Number(progress.accepted_count ?? 0);
    const target = Number(progress.target_count ?? 0);
    const staleSeconds = Number(progress.stale_seconds ?? 0);
    const pieces = [];
    if (target > 0) pieces.push(`${accepted}/${target}`);
    if (typeof progress.concurrency === "number") pieces.push(tf("common.concurrency_value", [progress.concurrency]));
    if (staleSeconds >= 300) pieces.push(t("common.possibly_stuck_recover_or_cancel"));
    return pieces.join(" · ");
  }

  function renderTaskCenter() {
    const tasks = taskCenter?.tasks || [];
    const visibleTasks = tasks.slice(0, 8);
    const agentActive = Boolean(recoveryAgentRun && ["queued", "running"].includes(recoveryAgentRun.status));
    return (
      <section className="taskCenter" aria-label={t("admin.task_center")}>
        <div className="taskCenterHead">
          <div>
            <h2>{t("admin.task_center")}</h2>
            <p>{t("generation.heavy_ai_tasks_queue_automatically_inspect_batch_generation_similar")}</p>
          </div>
          <div className="taskCenterActions">
            <span className="taskChip queued">{t("common.queued")} {(taskCenter?.queued_count ?? 0)}</span>
            <span className="taskChip warning">{t("common.running_75b26949")} {(taskCenter?.running_count ?? 0)}</span>
            <span className="taskChip error">{t("common.failed")} {(taskCenter?.failed_count ?? 0)}</span>
            <span className="taskChip muted">{t("common.possibly_stuck")} {(taskCenter?.stale_count ?? 0)}</span>
            <button type="button" onClick={() => loadTaskCenter(true)}><RefreshCcw size={14} />{t("common.refresh_tasks")}</button>
            <button type="button" onClick={recoverStaleState}><AlertTriangle size={14} />{t("common.repair_stuck_states")}</button>
            <button type="button" onClick={() => startRecoveryAgent(false)} disabled={agentActive}><BrainCircuit size={14} />{t("common.advanced_ai_diagnostics_repair")}</button>
            {agentActive && <button type="button" className="dangerSoft" onClick={cancelRecoveryAgent}><X size={14} />{t("common.interrupt_agent")}</button>}
            {recoveryAgentRun && !agentActive && <button type="button" onClick={() => startRecoveryAgent(true)}><RefreshCcw size={14} />{t("common.restart_agent")}</button>}
          </div>
        </div>
        <div className="taskColorLegend">
          <span><b className="legendDot done" />{t("common.attempted_completed")}</span>
          <span><b className="legendDot wrong" />{t("common.incorrect_failed")}</span>
          <span><b className="legendDot variant" />{t("history.similar_questions_completed")}</span>
          <span><b className="legendDot variantDone" />{t("history.similar_question_completed_after_an_error")}</span>
          <span><b className="legendDot queued" />{t("common.queued")}</span>
          <span><b className="legendDot running" />{t("common.running_possibly_stuck")}</span>
        </div>
        {visibleTasks.length ? (
          <div className="taskCenterList">
            {visibleTasks.map((task) => (
              <article className="taskCenterCard" key={task.task_id}>
                <div>
                  <div className="taskTitleRow">
                    <strong>{taskTitleText(task)}</strong>
                    <span className={`taskStatus ${taskStatusClass(task.status)}`}>{t(taskStatusLabel(task.status))}</span>
                  </div>
                  <p>{((taskDescriptionText(task) || task.target_id) || task.task_id)}</p>
                  <small>{(t(taskProgressText(task)) || task.task_id)}{(task.error ? ` · ${t(task.error)}` : "")}</small>
                </div>
                <div className="taskCardActions">
                  {task.can_cancel && <button type="button" className="dangerSoft" onClick={() => cancelSystemTask(task)}>{t("common.cancel")}</button>}
                  {task.can_retry && <button type="button" onClick={() => retrySystemTask(task)}>{t("common.retry")}</button>}
                  {task.target_type === "generation_batch" && task.target_id && (
                    <button type="button" onClick={() => {
                      const targetId = task.target_id || "";
                      setActiveWorkspace("generated");
                      api<GenerationBatch>(`/api/generation/batches/${targetId}`)
                        .then((row) => {
                          openBatchFromHistory(row);
                          if (!isTerminalGenerationBatchStatus(row.status)) {
                            watchBatch(row.id);
                            pollBatch(row.id);
                          }
                        })
                        .catch((error) => setStatus("error", errorMessage(error, "打开任务批次失败")));
                    }}>{t("common.open")}</button>
                  )}
                </div>
              </article>
            ))}
          </div>
        ) : (
          <p className="taskEmpty">{t("admin.no_current_or_historical_tasks_try_repair_stuck_states")}</p>
        )}
        {recoveryAgentRun?.report && (
          <div className="agentReport">
            <strong>{t("reports.advanced_ai_diagnostic_report")}</strong>
            <label>
              {t("diagnostics.select_report")}
              <select aria-label={t("diagnostics.select_report")} value={recoveryAgentRun.id} onChange={(event) => {
                const selected = recoveryAgentHistory.find((run) => run.id === event.target.value);
                if (selected) setRecoveryAgentRun(selected);
              }}>
                {[recoveryAgentRun, ...recoveryAgentHistory.filter((run) => run.report && run.id !== recoveryAgentRun.id)].map((run) => (
                  <option key={run.id} value={run.id}>{run.output_language || t("diagnostics.language_unknown")} · {run.completed_at || run.created_at || run.id}</option>
                ))}
              </select>
            </label>
            <p><small>{t("diagnostics.report_language")}: {recoveryAgentRun.output_language || t("diagnostics.language_unknown")} · {t("diagnostics.snapshot_time")}: {recoveryAgentRun.snapshot_at ? new Date(recoveryAgentRun.snapshot_at).toLocaleString(language) : t("diagnostics.language_unknown")}</small></p>
            {recoveryAgentRun.output_language !== language && <p>{t("diagnostics.other_language")}</p>}
            <p>{String(recoveryAgentRun.report["diagnosis"] ?? recoveryAgentRun.report["next_steps"] ?? t("reports.report_generated"))}</p>
            <small>{t("common.status")} {recoveryAgentRun.status === "completed" ? t(recoveryAgentRun.diagnosis_status === "completed_actions_unverified" ? "diagnostics.completed_actions_unverified" : "diagnostics.completed_no_repairs") : t(taskStatusLabel(recoveryAgentRun.status))} · {recoveryAgentRun.loop_count}/{recoveryAgentRun.max_loops} {t("common.rounds")} {recoveryAgentRun.model}</small>
            <p><small>{t("diagnostics.snapshot_warning")}</small></p>
          </div>
        )}
      </section>
    );
  }

  function renderAssistantDrawer() {
    const drafts = assistantLastResponse?.ui_drafts || [];
    const results = assistantLastResponse?.tool_results || [];
    const warnings = assistantLastResponse?.warnings || [];
    return (
      <>
        <button type="button" className="assistantFab" onClick={() => setAssistantOpen(true)} title={t("assistant.open_project_ai_assistant")}>
          <BrainCircuit size={20} />
          <span>{t("assistant.project_ai_assistant")}</span>
          {assistantPendingConfirmations.length > 0 && <b>{assistantPendingConfirmations.length}</b>}
        </button>
        {assistantOpen && (
          <aside className="assistantDrawer" aria-label={t("assistant.project_ai_assistant")}>
            <div className="assistantPanel">
              <div className="assistantHeader">
                <div>
                  <span className="eyebrow">{t("common.project_control_center")}</span>
                  <h2>{t("assistant.project_ai_assistant")}</h2>
                  <p>{t("assistant.search_features_questions_tags_history_and_reports_drafts_come")}</p>
                </div>
                <button type="button" className="iconButton" onClick={() => setAssistantOpen(false)} aria-label={t("assistant.close_project_ai_assistant")}><X size={18} /></button>
              </div>

              <div className="assistantContextStrip">
                <span>{(t(workspaceItems.find((item) => item.id === activeWorkspace)?.label) || activeWorkspace)}</span>
                {sessionId && <span>Session {sessionId.slice(0, 8)}</span>}
                {batch?.id && <span>Batch {batch.id.slice(0, 8)}</span>}
                {selectedTags.length > 0 && <span>{selectedTags.slice(0, 2).map((tag) => tag.label).join("、")}{(selectedTags.length > 2 ? tf("common.and_value_tags", [selectedTags.length]) : "")}</span>}
              </div>

              <div className="assistantMessages">
                {assistantMessages.length === 0 && (
                  <div className="assistantEmpty">
                    <strong>{t("common.describe_your_goal")}</strong>
                    <span>{t("generation.example_generate_10_three_star_russian_competition_questions_focused")}</span>
                    <span>{t("reports.example_where_do_i_select_a_report_batch_id")}</span>
                  </div>
                )}
                {assistantMessages.map((message) => (
                  <div key={message.id} className={`assistantMessage ${message.role === "user" ? "user" : "assistant"}`}>
                    <strong>{(message.role === "user" ? t("common.you") : t("assistant.project_assistant"))}</strong>
                    <p>{message.content}</p>
                  </div>
                ))}
                {assistantBusy && <div className="assistantMessage assistant"><strong>{t("assistant.project_assistant")}</strong><p>{t("assistant.working_long_actions_will_enter_the_task_center_or")}</p></div>}
              </div>

              {(drafts.length > 0 || results.length > 0 || warnings.length > 0 || assistantPendingConfirmations.length > 0) && (
                <div className="assistantArtifacts">
                  {warnings.map((warning, index) => <p key={`warning-${index}`} className="assistantWarning">{t(warning)}</p>)}
                  {drafts.map((draft, index) => (
                    <div key={`${draft.draft_type}-${index}`} className="assistantDraftCard">
                      <div>
                        <strong>{(t(draft.title) || t("common.draft_available"))}</strong>
                        <span>{draft.draft_type} · {t("assistant.language.target")}: {String(draft.output_language || draft.payload.output_language || t("assistant.language.unspecified"))}</span>
                      </div>
                      <pre>{JSON.stringify(draft.payload, null, 2).slice(0, 900)}</pre>
                      <button type="button" onClick={() => applyAssistantDraft(draft)}>{draft.apply_label ? t(draft.apply_label) : t("common.apply_to_form")}</button>
                    </div>
                  ))}
                  {assistantPendingConfirmations.map((item) => (
                    <div key={item.confirmation_id} className="assistantConfirmCard">
                      <div>
                        <strong>{t("assistant.confirmation_required")}{t(item.title)}</strong>
                        <span>{item.tool_name} · {item.permission_level} · {t("assistant.language.target")}: {String(item.output_language || item.arguments?.output_language || t("assistant.language.unspecified"))}</span>
                      </div>
                      <pre>{JSON.stringify(item.arguments, null, 2).slice(0, 900)}</pre>
                      <textarea
                        value={assistantConfirmationNotes[item.confirmation_id] || ""}
                        onChange={(event) => setAssistantConfirmationNotes({ ...assistantConfirmationNotes, [item.confirmation_id]: event.target.value })}
                        placeholder={t("assistant.optional_note_deleting_non_ai_questions_still_requires_dynamic")}
                      />
                      <div className="assistantConfirmActions">
                        <button type="button" className="dangerSoft" onClick={() => confirmAssistantAction(item)} disabled={assistantBusy}>{t("common.confirm_action")}</button>
                        <button type="button" onClick={() => setAssistantPendingConfirmations((current) => current.filter((row) => row.confirmation_id !== item.confirmation_id))}>{t("common.cancel_this_action")}</button>
                      </div>
                    </div>
                  ))}
                  {results.map((result, index) => {
                    const payload = asRecord(result.result);
                    const challenge = asRecord(payload.challenge);
                    return (
                      <div key={`${result.tool_name}-${result.tool_run_id || index}`} className={result.error ? "assistantToolCard error" : "assistantToolCard"}>
                        <strong>{result.tool_name}</strong>
                        {Boolean(payload.requires_dynamic_authorization) && (
                          <p className="assistantWarning">{t("common.non_ai_questions_included_dynamic_authorization_required_code")}{metadataString(challenge.phrase, t("common.not_generated"))}；challenge_id：{metadataString(challenge.challenge_id, "-")}</p>
                        )}
                        <pre>{assistantResultPreview(result)}</pre>
                      </div>
                    );
                  })}
                </div>
              )}

              <form className="assistantComposer" onSubmit={(event) => { event.preventDefault(); void sendAssistantMessage(); }}>
                <textarea
                  value={assistantInput}
                  onChange={(event) => setAssistantInput(event.target.value)}
                  onKeyDown={(event) => {
                    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                      event.preventDefault();
                      void sendAssistantMessage();
                    }
                  }}
                  placeholder={t("generation.describe_your_goal_explore_features_find_questions_set_parameters")}
                />
                <div className="assistantComposerActions">
                  <button type="submit" disabled={assistantBusy || !assistantInput.trim()}><Send size={16} />{t("common.send")}</button>
                  <button type="button" onClick={cancelAssistantConversation} disabled={!assistantBusy && !assistantConversation?.id}><X size={16} />{t("common.interrupt")}</button>
                </div>
              </form>
            </div>
          </aside>
        )}
      </>
    );
  }

  function activeTaskWarningSummary() {
    const activeTasks = (taskCenter?.tasks || []).filter((task) => ["queued", "running", "generating", "cancelling"].includes(task.status));
    if (!activeTasks.length) return "";
    return activeTasks
      .slice(0, 5)
      .map((task) => `${taskTitleText(task)}(${t(taskStatusLabel(task.status))})`)
      .join(translateUi("common.separator.list", language));
  }

  function confirmWhileSystemTasksActive(actionLabel: string) {
    const summary = activeTaskWarningSummary();
    if (!summary) return true;
    return window.confirm(
      tf("history.there_are_running_or_queued_tasks_value_consider_cancelling", [summary, t(actionLabel)]),
    );
  }

  async function importData() {
    if (!confirmWhileSystemTasksActive("导入内置题库")) return;
    setStatus("loading", "正在导入题库");
    try {
      const imported = await api<Record<string, number>>("/api/import/json-files", { method: "POST", body: "{}", timeoutMs: REQUEST_TIMEOUT_MS.transfer, timeoutLabel: "导入题库" });
      setStatus("success", `导入完成：${imported.questions} 题，verified ${imported.verified}，needs_review ${imported.needs_review}`);
      await refresh();
    } catch (error) {
      setStatus("error", error instanceof Error ? error.message : "导入失败");
    }
  }

  async function createPractice() {
    if (sessionCreateLocksRef.current.practice) {
      setStatus("loading", "正在创建练习，请勿重复点击");
      return;
    }
    sessionCreateLocksRef.current.practice = true;
    const requestId = makeIdempotencyKey("practice");
    const requestedCount = clampedNumber(practiceCount, 1, 100, 5);
    const useBankFilters = activeWorkspace === "bank";
    const sourceName = (useBankFilters ? questionFilters.source_name : practiceSourceName).trim();
    const difficultyStar = useBankFilters ? questionFilters.difficulty_star : practiceDifficultyStar;
    const parsedStar = difficultyStar ? Number(difficultyStar) : 0;
    const filterSummary = [
      sourceName ? compactSource(sourceName) : "",
      parsedStar ? `${parsedStar}星` : "",
      selectedTagIds.length ? `${selectedTagIds.length} 个标签` : "",
      practiceExcludeDone ? "排除已做" : "允许已做",
    ].filter(Boolean).join(" · ");
    setStatus("loading", `正在创建 ${requestedCount} 题练习${filterSummary ? `（${filterSummary}）` : ""}`);
    try {
      const created = await api<{ id: string; question_ids: string[] }>("/api/practice/sessions", {
        method: "POST",
        body: JSON.stringify({
          learner_id: activeLearnerId || null,
          idempotency_key: requestId,
          count: requestedCount,
          verified_only: true,
          tag_ids: selectedTagIds,
          source_names: sourceName ? [sourceName] : [],
          difficulty_stars: parsedStar ? [parsedStar] : [],
          exclude_done: practiceExcludeDone,
        }),
      });
      setSessionId(created.id);
      setSessionQuestions(created.question_ids);
      setSessionState(await api<SessionState>(`/api/practice/sessions/${created.id}`));
      setSelectedQuestion(created.question_ids[0] || "");
      setResult(null);
      setQuestionOutcomes({});
      setSolutionExplanationMessage("");
      setSolutionExplanationResult(null);
      setActiveWorkspace("practice");
      setStatus("success", `练习已创建：${created.question_ids.length}/${requestedCount} 题`);
      sessionCreateLocksRef.current.practice = false;
    } catch (error) {
      sessionCreateLocksRef.current.practice = false;
      setStatus("error", error instanceof Error ? error.message : "创建练习失败");
    }
  }

  function toggleBankQuestionSelection(questionId: string, checked: boolean) {
    setSelectedBankQuestionIds((current) => {
      if (!checked) return current.filter((id) => id !== questionId);
      if (current.includes(questionId)) return current;
      if (current.length >= MAX_BANK_SELECTION_COUNT) {
        setStatus("error", `最多一次选择 ${MAX_BANK_SELECTION_COUNT} 道题`);
        return current;
      }
      return [...current, questionId];
    });
  }

  function selectVisibleBankQuestions() {
    setSelectedBankQuestionIds((current) => {
      const next = [...current];
      for (const question of questions) {
        if (!next.includes(question.id) && next.length < MAX_BANK_SELECTION_COUNT) next.push(question.id);
      }
      if (next.length === current.length && questions.length > 0) {
        setStatus("success", "当前页题目已全部选中");
      } else if (questions.length > 0) {
        setStatus("success", `已选中当前页题目，累计 ${next.length} 道`);
      }
      return next;
    });
  }

  function clearBankQuestionSelection() {
    setSelectedBankQuestionIds([]);
    setStatus("success", "已清空题库多选");
  }

  async function createPracticeFromSelectedBankQuestions() {
    const questionIds = [...selectedBankQuestionIds];
    if (!questionIds.length) {
      setStatus("error", "请先勾选题目");
      return;
    }
    const lockKey = "bank_selected_review";
    if (sessionCreateLocksRef.current[lockKey]) {
      setStatus("loading", "正在用选中题创建练习，请勿重复点击");
      return;
    }
    sessionCreateLocksRef.current[lockKey] = true;
    const requestId = makeIdempotencyKey(lockKey);
    setStatus("loading", "正在用选中题创建练习");
    try {
      const created = await api<{ id: string; question_ids: string[]; config: Record<string, unknown> }>("/api/learning/review-session", {
        method: "POST",
        body: JSON.stringify({ learner_id: activeLearnerId || null, idempotency_key: requestId, question_ids: questionIds, reason: "question_bank_selection", exclude_answered: false }),
      });
      setSessionId(created.id);
      setSessionQuestions(created.question_ids);
      setSessionState(await api<SessionState>(`/api/practice/sessions/${created.id}`));
      setSelectedQuestion(created.question_ids[0] || "");
      setResult(null);
      setQuestionOutcomes({});
      setSolutionExplanationMessage("");
      setSolutionExplanationResult(null);
      setActiveWorkspace("practice");
      const reviewSource = asRecord(asRecord(created.config).review_source);
      const skippedCount = metadataNumber(reviewSource.skipped_count) ?? Math.max(0, questionIds.length - created.question_ids.length);
      setStatus(created.question_ids.length ? "success" : "error", `选中题练习已创建：${created.question_ids.length} 题${skippedCount ? `，跳过不可练 ${skippedCount} 题` : ""}`);
      sessionCreateLocksRef.current[lockKey] = false;
    } catch (error) {
      sessionCreateLocksRef.current[lockKey] = false;
      setStatus("error", errorMessage(error, "用选中题创建练习失败"));
    }
  }

  async function createStrictTest() {
    if (sessionCreateLocksRef.current.strict_test) {
      setStatus("loading", "正在创建严格测试，请勿重复点击");
      return;
    }
    if (strictTestRequest.use_custom_quota && requestedStrictStarQuotaTotal !== requestedStrictCount) {
      setStatus("error", `自定义星级题数合计必须等于题目数量：当前 ${requestedStrictStarQuotaTotal}/${requestedStrictCount}`);
      return;
    }
    sessionCreateLocksRef.current.strict_test = true;
    const requestId = makeIdempotencyKey("strict_test");
    setStatus("loading", strictTestRequest.fill_missing_with_ai && strictSourceMode !== "bank_only" ? "正在创建严格测试；题库缺口会先用 AI 出题并审核" : "正在创建严格测试");
    try {
      const created = await api<{ id: string; question_ids: string[]; config: Record<string, unknown> }>("/api/practice/sessions", {
        method: "POST",
        body: JSON.stringify({
          mode: "strict_test",
          learner_id: activeLearnerId || null,
          idempotency_key: requestId,
          count: requestedStrictCount,
          template_name: strictTestRequest.use_custom_quota || requestedStrictCount !== 100 ? null : "strict_100_high_intensity",
          star_quota: strictTestRequest.use_custom_quota ? requestedStrictStarQuota : null,
          verified_only: true,
          include_generated: strictSourceMode !== "bank_only",
          strict_source_mode: strictSourceMode,
          fill_missing_with_ai: strictSourceMode !== "bank_only" && strictTestRequest.fill_missing_with_ai,
          ai_candidate_count: requestedCandidateCount,
          ai_source_example_count: requestedSourceExampleCount,
          ai_negative_example_count: requestedAiNegativeExampleCount,
          allow_reviewer_quality_downgrade: Boolean(modelSettings.has_api_key && batchRequest.allow_reviewer_quality_downgrade),
          exclude_done: true,
        }),
        timeoutMs: strictSourceMode !== "bank_only" && strictTestRequest.fill_missing_with_ai ? REQUEST_TIMEOUT_MS.aiGeneration : REQUEST_TIMEOUT_MS.normal,
        timeoutLabel: "严格测试创建",
      });
      setSessionId(created.id);
      setSessionQuestions(created.question_ids);
      setSelectedQuestion(created.question_ids[0] || "");
      setResult(null);
      setQuestionOutcomes({});
      setSolutionExplanationMessage("");
      setSolutionExplanationResult(null);
      setSessionState(await api<SessionState>(`/api/practice/sessions/${created.id}`));
      setActiveWorkspace("practice");
      const selection = (created.config?.selection || {}) as Record<string, unknown>;
      const warnings = Array.isArray(selection.selection_warnings) ? selection.selection_warnings.map(String).map(selectionWarningText).join("；") : "";
      setStatus(created.question_ids.length ? "success" : "error", `严格测试已创建：${created.question_ids.length} 题 · ${STRICT_SOURCE_LABELS[strictSourceMode]}${warnings ? ` · ${warnings}` : ""}`);
      sessionCreateLocksRef.current.strict_test = false;
    } catch (error) {
      sessionCreateLocksRef.current.strict_test = false;
      setStatus("error", error instanceof Error ? error.message : "创建严格测试失败");
    }
  }

  async function startTodayTask(task: TodayTask) {
    const lockKey = `today:${task.task_id}`;
    if (sessionCreateLocksRef.current[lockKey]) {
      setStatus("loading", "正在启动今日任务，请勿重复点击");
      return;
    }
    sessionCreateLocksRef.current[lockKey] = true;
    const requestId = makeIdempotencyKey(lockKey);
    setStatus("loading", "正在启动今日任务");
    try {
      if (task.task_type === "continue_unfinished_session" && task.session_id) {
        const state = await api<SessionState>(`/api/practice/sessions/${task.session_id}`);
        setSessionId(state.id);
        setSessionQuestions(state.question_ids);
        setSessionState(state);
        setSelectedQuestion(state.question_ids[0] || "");
      } else if (task.task_type === "review_due_questions" && task.question_ids?.length) {
        const created = await api<{ id: string; learner_id: string; question_ids: string[]; config: Record<string, unknown> }>("/api/learning/review-session", {
          method: "POST",
          body: JSON.stringify({ learner_id: activeLearnerId || null, idempotency_key: requestId, question_ids: task.question_ids, reason: task.task_id }),
        });
        setSessionId(created.id);
        setSessionQuestions(created.question_ids);
        setSessionState(await api<SessionState>(`/api/practice/sessions/${created.id}`));
        setSelectedQuestion(created.question_ids[0] || "");
      } else if (task.task_type === "tag_star_mastery_check") {
        const created = await api<{ id: string; learner_id: string; question_ids: string[]; config: Record<string, unknown> }>("/api/practice/sessions", {
          method: "POST",
          body: JSON.stringify({
            learner_id: activeLearnerId || null,
            idempotency_key: requestId,
            count: task.question_count || 3,
            verified_only: true,
            tag_ids: task.target_tags || [],
            difficulty_stars: task.difficulty_star ? [task.difficulty_star] : [],
          }),
        });
        setSessionId(created.id);
        setSessionQuestions(created.question_ids);
        setSessionState(await api<SessionState>(`/api/practice/sessions/${created.id}`));
        setSelectedQuestion(created.question_ids[0] || "");
      } else {
        await createPractice();
        sessionCreateLocksRef.current[lockKey] = false;
        return;
      }
      setAnswer("");
      setThought("");
      setResult(null);
      setQuestionOutcomes({});
      setSolutionExplanationMessage("");
      setSolutionExplanationResult(null);
      setActiveWorkspace("practice");
      setStatus("success", "今日任务已启动");
      sessionCreateLocksRef.current[lockKey] = false;
    } catch (error) {
      sessionCreateLocksRef.current[lockKey] = false;
      setStatus("error", errorMessage(error, "今日任务启动失败"));
    }
  }

  async function startReportReviewTask(task: ReviewPlan["next_tasks"][number]) {
    const lockKey = `report:${task.task_id}`;
    if (sessionCreateLocksRef.current[lockKey]) {
      setStatus("loading", "正在创建报告复习任务，请勿重复点击");
      return;
    }
    sessionCreateLocksRef.current[lockKey] = true;
    const requestId = makeIdempotencyKey(lockKey);
    setStatus("loading", "正在创建报告复习任务");
    try {
      const created = await api<{ id: string; learner_id: string; question_ids: string[]; config: Record<string, unknown> }>("/api/learning/review-session", {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.normal,
        timeoutLabel: "创建报告复习任务",
        body: JSON.stringify({
          learner_id: activeLearnerId || null,
          idempotency_key: requestId,
          target_tags: task.target_tags || [],
          difficulty_star: task.difficulty_star ?? null,
          question_count: task.question_count || 3,
          exclude_answered: true,
          reason: task.task_id,
        }),
      });
      setSessionId(created.id);
      setSessionQuestions(created.question_ids);
      setSessionState(await api<SessionState>(`/api/practice/sessions/${created.id}`));
      setSelectedQuestion(created.question_ids[0] || "");
      setAnswer("");
      setThought("");
      setResult(null);
      setQuestionOutcomes({});
      setAnswerJudgeResult(null);
      setQuestionSupportResult(null);
      setSolutionExplanationMessage("");
      setSolutionExplanationResult(null);
      setActiveWorkspace("practice");
      const reviewSource = (created.config?.review_source || {}) as Record<string, unknown>;
      const fallbackNote = reviewSource.fallback_used === "answered_pool" ? " · 未做新题不足，已转为重做相同范围旧题" : "";
      setStatus("success", `报告复习任务已启动：${created.question_ids.length} 题${fallbackNote}`);
      sessionCreateLocksRef.current[lockKey] = false;
    } catch (error) {
      sessionCreateLocksRef.current[lockKey] = false;
      setStatus("error", errorMessage(error, "报告复习任务创建失败"));
    }
  }

  async function refreshSessionStateOnly(IDLabel = "状态已刷新") {
    if (!sessionId) return null;
    const nextState = await api<SessionState>(`/api/practice/sessions/${sessionId}`);
    setSessionState(nextState);
    setSessionQuestions(nextState.question_ids);
    setStatus("success", IDLabel);
    return nextState;
  }

  async function requestMasteryCheck() {
    if (!sessionId || !selectedQuestion || strictFocus || currentQuestionIsMasteryCheck) return;
    const requestedCount = clampedNumber(masteryCheckCount, 1, 5, 1);
    const idempotencyKey = `mastery:${stableHash(`${sessionId}|${selectedQuestion}|${currentQuestionStateVersion}|${requestedCount}|${Date.now()}`)}`;
    setStatus("loading", modelSettings.has_api_key ? `正在生成 ${requestedCount} 道类似题/检测题` : `正在用模拟流程生成 ${requestedCount} 道类似题/检测题`);
    try {
      const nextState = await api<SessionState>(`/api/practice/sessions/${sessionId}/questions/${selectedQuestion}/mastery-check`, {
        method: "POST",
        body: JSON.stringify({ at_ms: 0, extra_tag_ids: selectedTagIds, count: requestedCount, candidate_count: 3, run_now: false, idempotency_key: idempotencyKey }),
        timeoutMs: REQUEST_TIMEOUT_MS.batchStart,
        timeoutLabel: "掌握检测题请求",
      });
      setSessionState(nextState);
      setSessionQuestions(nextState.question_ids);
      void loadTaskCenter(false).catch(() => undefined);
      setStatus("success", `${requestedCount} 道类似题已开始后台生成；每道会从 3 个候选中审核选一题，可继续做当前题，稍后刷新状态再开始`);
    } catch (error) {
      setStatus("error", errorMessage(error, "掌握检测题请求失败"));
    }
  }

  async function refreshMasteryCheckStatus() {
    try {
      const nextState = await refreshSessionStateOnly("检测题状态已刷新");
      const row = (nextState?.active_interventions || []).find(
        (item) => item.intervention_type === "mastery_check" && (item.original_question_id === selectedQuestion || item.current_question_id === selectedQuestion),
      );
      if (row && (interventionState(row) === "ready_to_start" || row.prefetch_status === "ready")) {
        setStatus("success", "检测题已就绪，可以在合适的时候开始");
      }
    } catch (error) {
      setStatus("error", errorMessage(error, "刷新检测题状态失败"));
    }
  }

  function startMasteryCheck(row: ActiveIntervention | null = currentMasteryCheck) {
    const rowQuestionIds = [
      ...interventionPayloadStringArray(row, "check_question_ids"),
      interventionPayloadString(row, "check_question_id"),
      row && row.current_question_id !== row.original_question_id ? row.current_question_id : "",
    ].filter(Boolean);
    const questionId = rowQuestionIds.find((id) => id !== selectedQuestion) || rowQuestionIds[0]
      || (row && row.current_question_id !== row.original_question_id ? row.current_question_id : "");
    if (!questionId) {
      setStatus("error", "检测题还没有生成完成");
      return;
    }
    switchPracticeQuestion(questionId, "已切换到掌握检测题");
  }

  function switchPracticeQuestion(questionId: string, label: string) {
    setSelectedQuestion(questionId);
    setAnswer("");
    setThought("");
    setResult(null);
    setAnswerJudgeResult(null);
    setQuestionSupportResult(null);
    setSolutionExplanationMessage("");
    setSolutionExplanationResult(null);
    setHintAnswer("");
    setHintThought("");
    setHintAnswerResult(null);
    setLastSubmissionId("");
    setHelpState(null);
    setStatus("success", label);
  }

  function startHintIntervention(row: ActiveIntervention | null = currentHintIntervention) {
    const questionId = row?.current_question_id && row.current_question_id !== row.original_question_id ? row.current_question_id : "";
    if (!questionId) {
      setStatus("error", "启发题还没有就绪");
      return;
    }
    switchPracticeQuestion(questionId, "已切换到启发题");
  }

  async function submitTemporaryHintAnswer(row: ActiveIntervention | null = currentHintIntervention) {
    if (!sessionId || !row || !currentTemporaryHintReady) {
      setStatus("error", "没有可提交的临时提示题");
      return;
    }
    if (!hintAnswer.trim()) {
      setStatus("error", "请先填写提示题答案");
      return;
    }
    setStatus("loading", "正在检查提示题答案");
    try {
      const checked = await api<TemporaryHintAnswerResponse>(`/api/practice/sessions/${sessionId}/interventions/${row.id}/hint-answer`, {
        method: "POST",
        body: JSON.stringify({
          answer_text: hintAnswer,
          thought_text: hintThought || null,
          at_ms: 0,
          idempotency_key: `hint:${stableHash(`${sessionId}|${row.id}|${hintAnswer}|${hintThought}`)}`,
        }),
        timeoutMs: REQUEST_TIMEOUT_MS.aiInteractive,
        timeoutLabel: "提示题判分",
      });
      setHintAnswerResult(checked.result);
      const nextState = await api<SessionState>(`/api/practice/sessions/${sessionId}`);
      setSessionState(nextState);
      setStatus(checked.is_correct ? "success" : "error", checked.is_correct ? "提示题答对了，可以回原题再试" : "提示题还未通过，可以再试一次");
    } catch (error) {
      setStatus("error", errorMessage(error, "提示题判分失败"));
    }
  }

  async function returnToOriginalQuestion(row: ActiveIntervention | null = currentHintIntervention || currentMasteryCheck) {
    if (!row) {
      setStatus("error", "没有可返回的原题");
      return;
    }
    if (sessionId && row.intervention_type !== "mastery_check" && row.status === "active") {
      try {
        const nextState = await api<SessionState>(`/api/practice/sessions/${sessionId}/interventions/${row.id}`, {
          method: "POST",
          body: JSON.stringify({ action: "return_original", success: null, at_ms: 0 }),
        });
        setSessionState(nextState);
        setSessionQuestions(nextState.question_ids);
      } catch (error) {
        setStatus("error", errorMessage(error, "返回原题失败"));
        return;
      }
    }
    switchPracticeQuestion(row.original_question_id, "已回到原题");
  }

  async function completeHintAndReturnOriginal(row: ActiveIntervention | null = currentHintIntervention) {
    if (!sessionId || !row) {
      setStatus("error", "没有可完成的启发题");
      return;
    }
    try {
      await api<SessionState>(`/api/practice/sessions/${sessionId}/interventions/${row.id}`, {
        method: "POST",
        body: JSON.stringify({ action: "return_original", success: true, at_ms: 0 }),
      });
      const nextState = await api<SessionState>(`/api/practice/sessions/${sessionId}/interventions/${row.id}`, {
        method: "POST",
        body: JSON.stringify({ action: "complete", success: true, at_ms: 0 }),
      });
      setSessionState(nextState);
      setSessionQuestions(nextState.question_ids);
      switchPracticeQuestion(row.original_question_id, "启发题已完成，已回到原题");
    } catch (error) {
      setStatus("error", errorMessage(error, "完成启发题失败"));
    }
  }

  async function submitCurrentAnswer() {
    if (!sessionId || !selectedQuestion) return;
    if (breakDue) {
      setStatus("error", "请先确认中场休息后继续");
      return;
    }
    if (answerLocked) {
      setStatus("error", "本题已经看过答案，回答已锁定，不能再提交或修改");
      return;
    }
    setStatus("loading", "正在判分");
    try {
      const stateVersion = questionStateVersion(sessionState, selectedQuestion);
      const idempotencyKey = `submit:${stableHash(`${sessionId}|${selectedQuestion}|${stateVersion}|${answer}|${thought}`)}`;
      const submitted = await api<{ submission_id: string; is_correct: boolean | null; result: Record<string, unknown>; state_version?: number | null }>(`/api/practice/sessions/${sessionId}/submit`, {
      method: "POST",
      body: JSON.stringify({
        question_id: selectedQuestion,
        answer_text: answer,
        thought_text: thought || null,
        elapsed_ms: 0,
        idempotency_key: idempotencyKey,
        expected_state_version: stateVersion,
      }),
      timeoutMs: REQUEST_TIMEOUT_MS.aiInteractive,
      timeoutLabel: "提交判分",
    });
    setResult(submitted.result);
    setLastSubmissionId(submitted.submission_id);
    const autoJudge = submitted.result.auto_answer_judge as AnswerJudgeResult | undefined;
    setAnswerJudgeResult(autoJudge || null);
    setQuestionOutcomes((current) => ({
      ...current,
      [selectedQuestion]: submitted.is_correct === true ? "correct" : submitted.is_correct === false ? "wrong" : "review",
    }));
    clearCurrentQuestionDraft("已提交，草稿已清理");
    setSessionState(await api<SessionState>(`/api/practice/sessions/${sessionId}`));
    await loadHelpState();
    setStatus(submitted.is_correct ? "success" : "error", submitted.is_correct ? "判分正确" : "判分错误，可继续尝试或查看帮助");
    } catch (error) {
      const message = errorMessage(error, "提交判分失败");
      setStatus("error", message.includes("answer_locked_after_reveal") ? "本题已经看过答案，回答已锁定，不能再提交或修改" : message);
    }
  }

  function moveSelectedQuestion(delta: number) {
    if (!activeSessionQueue.length) return;
    const currentIndex = activeSessionQuestionIndex >= 0 ? activeSessionQuestionIndex : 0;
    const nextIndex = Math.max(0, Math.min(activeSessionQueue.length - 1, currentIndex + delta));
    const nextId = activeSessionQueue[nextIndex];
    if (nextId) setSelectedQuestion(nextId);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    await submitCurrentAnswer();
  }

  async function requestAnswerJudge() {
    if (!sessionId || !selectedQuestion) return;
    setStatus("loading", modelSettings.has_api_key ? "正在进行 AI 答案复核" : "正在进行规则复核");
    try {
      const stateVersion = questionStateVersion(sessionState, selectedQuestion);
      const idempotencyKey = `judge:${stableHash(`${sessionId}|${selectedQuestion}|${stateVersion}|${lastSubmissionId}|${answer}|${thought}`)}`;
      const judged = await api<AnswerJudgeResult>(`/api/practice/sessions/${sessionId}/judge-answer`, {
        method: "POST",
        body: JSON.stringify({
          question_id: selectedQuestion,
          answer_text: answer,
          thought_text: thought || null,
          submission_id: lastSubmissionId || null,
          idempotency_key: idempotencyKey,
          expected_state_version: stateVersion,
        }),
        timeoutMs: REQUEST_TIMEOUT_MS.aiInteractive,
        timeoutLabel: "answer judge",
      });
      setAnswerJudgeResult(judged);
      setSessionState(await api<SessionState>(`/api/practice/sessions/${sessionId}`));
      const label = judged.verdict === "correct" ? "AI 复核：答案可接受" : judged.verdict === "incorrect" ? "AI 复核：答案不可接受" : "AI 复核：仍需人工确认";
      setStatus(judged.verdict === "correct" ? "success" : judged.verdict === "incorrect" ? "error" : "success", label);
    } catch (error) {
      setStatus("error", errorMessage(error, "AI 答案复核失败"));
    }
  }

  async function submitQuestionSupport() {
    if (!sessionId || !selectedQuestion) return;
    if (!supportMessage.trim()) {
      setStatus("error", "请先写明题目格式或题意问题");
      return;
    }
    setStatus("loading", modelSettings.has_api_key ? "正在检查题目问题" : "正在记录题目问题");
    try {
      const response = await api<QuestionSupportResult>(`/api/practice/sessions/${sessionId}/question-support`, {
        method: "POST",
        body: JSON.stringify({
          question_id: selectedQuestion,
          concern_type: supportConcernType,
          message: supportMessage,
          at_ms: 0,
        }),
        timeoutMs: REQUEST_TIMEOUT_MS.aiInteractive,
        timeoutLabel: "question support",
      });
      setQuestionSupportResult(response);
      const nextState = await api<SessionState>(`/api/practice/sessions/${sessionId}`);
      setSessionState(nextState);
      setSessionQuestions(nextState.question_ids);
      if (response.replaced && response.replacement_question_id) {
        clearCurrentQuestionDraft("题目已更换，原题草稿已清理");
        setSelectedQuestion(response.replacement_question_id);
        setAnswer("");
        setThought("");
        setResult(null);
        setAnswerJudgeResult(null);
        setSolutionExplanationMessage("");
        setSolutionExplanationResult(null);
        setLastSubmissionId("");
        setHelpState(null);
      }
      setStatus(response.replaced ? "success" : response.question_problem ? "error" : "success", response.safe_response);
    } catch (error) {
      setStatus("error", errorMessage(error, "题目质疑提交失败"));
    }
  }

  async function recordPracticeEvent(eventType: string, payload: Record<string, unknown> = {}) {
    if (!sessionId || !selectedQuestion) return;
    await api(`/api/practice/sessions/${sessionId}/events`, {
      method: "POST",
      body: JSON.stringify({ question_id: selectedQuestion, event_type: eventType, at_ms: 0, payload }),
    });
    if (eventType === "skipped") {
      setQuestionOutcomes((current) => ({ ...current, [selectedQuestion]: "review" }));
      clearCurrentQuestionDraft("已跳过，草稿已清理");
    }
    setSessionState(await api<SessionState>(`/api/practice/sessions/${sessionId}`));
    setStatus("success", EVENT_LABELS[eventType] || "事件已记录");
  }

  async function loadHelpState() {
    if (!sessionId || !selectedQuestion) return;
    const state = await api<HelpState>(`/api/practice/sessions/${sessionId}/questions/${selectedQuestion}/help-state`);
    setHelpState(state);
  }

  async function revealNextHelp() {
    if (!sessionId || !selectedQuestion || !currentHelpState?.can_reveal_next) return;
    const response = await api<{ allowed_level: number; reached_level: number; help: HelpState["current_help"] }>(`/api/practice/sessions/${sessionId}/questions/${selectedQuestion}/help/next`, {
      method: "POST",
      body: JSON.stringify({ reason: "student_requested_help", at_ms: 0 }),
    });
    void loadTaskCenter(false).catch(() => undefined);
    setHelpState({ session_id: sessionId, question_id: selectedQuestion, allowed_level: response.allowed_level, reached_level: response.reached_level, can_reveal_next: response.reached_level < response.allowed_level, solution_revealed: response.reached_level >= 5, current_help: response.help });
  }

  async function giveUpQuestion() {
    if (!sessionId || !selectedQuestion) return;
    const confirmed = window.confirm(localizeUi("history.viewing_the_answer_immediately_locks_this_question_against_submission"));
    if (!confirmed) return;
    const stateVersion = questionStateVersion(sessionState, selectedQuestion);
    const idempotencyKey = `reveal:${stableHash(`${sessionId}|${selectedQuestion}|${stateVersion}`)}`;
    const response = await api<{ allowed_level: number; reached_level: number; answer_locked: boolean; help: HelpState["current_help"]; state_version?: number | null }>(`/api/practice/sessions/${sessionId}/questions/${selectedQuestion}/reveal-answer`, {
      method: "POST",
      body: JSON.stringify({
        reason: "student_revealed_answer",
        at_ms: 0,
        idempotency_key: idempotencyKey,
        expected_state_version: stateVersion,
      }),
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "看答案",
    });
    setHelpState({ session_id: sessionId, question_id: selectedQuestion, allowed_level: response.allowed_level, reached_level: response.reached_level, can_reveal_next: false, solution_revealed: true, current_help: response.help });
    setQuestionOutcomes((current) => ({ ...current, [selectedQuestion]: "review" }));
    clearCurrentQuestionDraft("已看答案，草稿已清理");
    setSessionState(await api<SessionState>(`/api/practice/sessions/${sessionId}`));
    setStatus("success", "已展示答案并锁定本题，可继续查看讲解解答");
  }

  async function requestSolutionExplanation(overrideMessage?: string) {
    if (!sessionId || !selectedQuestion) return;
    if (!answerLocked) {
      setStatus("error", "请先点击看答案，确认锁定本题后再请求讲解");
      return;
    }
    const message = (overrideMessage || solutionExplanationMessage).trim();
    if (!message) {
      setStatus("error", "请先写下你对解答的具体疑问");
      return;
    }
    setStatus("loading", modelSettings.has_api_key ? "正在生成解答讲解" : "正在用模拟流程生成解答讲解");
    try {
      const response = await api<SolutionExplanationResult>(`/api/practice/sessions/${sessionId}/questions/${selectedQuestion}/solution-explanation`, {
        method: "POST",
        body: JSON.stringify({ question_id: selectedQuestion, message, at_ms: 0 }),
        timeoutMs: REQUEST_TIMEOUT_MS.aiInteractive,
        timeoutLabel: "讲解解答",
      });
      setSolutionExplanationResult(response);
      setStatus(response.status === "ok" ? "success" : "error", response.status === "ok" ? "讲解解答已返回" : response.refusal_reason || response.error || "讲解解答失败");
    } catch (error) {
      setStatus("error", errorMessage(error, "讲解解答失败"));
    }
  }

  async function pausePractice() {
    if (!sessionId || !selectedQuestion) return;
    if (strictFocus && !window.confirm(localizeUi("practice.pause_the_current_strict_test"))) return;
    await api(`/api/practice/sessions/${sessionId}/pause`, {
      method: "POST",
      body: JSON.stringify({ question_id: selectedQuestion, event_type: "paused", at_ms: 0, payload: {} }),
    });
    setSessionState(await api<SessionState>(`/api/practice/sessions/${sessionId}`));
    setStatus("success", "已暂停当前练习");
  }

  async function resumePractice() {
    if (!sessionId || !selectedQuestion) return;
    await api(`/api/practice/sessions/${sessionId}/resume`, {
      method: "POST",
      body: JSON.stringify({ question_id: selectedQuestion, event_type: "resumed_answer", at_ms: 0, payload: {} }),
    });
    setSessionState(await api<SessionState>(`/api/practice/sessions/${sessionId}`));
    setStatus("success", "已继续当前练习");
  }

  function exitStrictFocus() {
    if (!strictFocus) return;
    const confirmed = window.confirm(localizeUi("practice.leaving_focus_view_clears_only_browser_drafts_not_database"));
    if (!confirmed) return;
    window.localStorage.removeItem(PRACTICE_DRAFT_KEY);
    setSessionId("");
    setSessionQuestions([]);
    setSessionState(null);
    setQuestionOutcomes({});
    setSelectedQuestion("");
    setAnswer("");
    setThought("");
    setResult(null);
    setHelpState(null);
    setActiveWorkspace("practice");
    setAppMode("learner");
    setStatus("success", "已退出专注视图，历史记录仍保留在报告中");
  }

  async function confirmBreak() {
    if (!sessionId || !sessionState?.next_breakpoint) return;
    const state = await api<SessionState>(`/api/practice/sessions/${sessionId}/breaks/confirm`, {
      method: "POST",
      body: JSON.stringify({ checkpoint_index: sessionState.next_breakpoint, at_ms: 0 }),
    });
    setSessionState(state);
    setStatus("success", "休息点已确认");
  }

  async function generate() {
    setStatus("loading", modelSettings.has_api_key ? "正在创建 AI 出题任务" : "正在运行模拟出题流水线");
    try {
      const targetTags = generationTargetTags();
      const tagNotes = selectedGenerationTagNotes();
      const constraints = [generationConstraintsText || t("generation.generate_one_automatically_gradable_question"), batchRequest.custom_description, tagNotes ? `标签说明：\n${tagNotes}` : ""].filter(Boolean).join("\n\n");
      const created = await api<Record<string, unknown>>("/api/generation/jobs", {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.aiGeneration,
        timeoutLabel: "single generation job",
        body: JSON.stringify({
          target_tags: targetTags.length ? targetTags : ["数论"],
          difficulty_star: batchRequest.difficulty_star ? Number(batchRequest.difficulty_star) : 3,
          count: 1,
          constraints,
          use_mock: useMockModel(),
          prefer_reuse_existing_ai: batchRequest.prefer_reuse_existing_ai,
          candidate_count: requestedCandidateCount,
          source_example_count: requestedSourceExampleCount,
          ai_negative_example_count: requestedAiNegativeExampleCount,
          allow_budget_overrun: Boolean(modelSettings.has_api_key && batchRequest.allow_budget_overrun),
          allow_reviewer_quality_downgrade: Boolean(modelSettings.has_api_key && batchRequest.allow_reviewer_quality_downgrade),
        }),
      });
      setJob(created);
      setStatus("success", "出题任务完成");
    } catch (error) {
      setStatus("error", error instanceof Error ? error.message : "出题失败");
    }
  }

  async function retryGenerationJob() {
    if (!currentJobId || !jobRetryableStatus) return;
    if (activeRetryPreview?.retry_exhausted) {
      setStatus("error", "这条出题链路已经达到定向重试上限，不会继续调用 provider");
      return;
    }
    setStatus("loading", "正在按失败原因定向重试");
    try {
      const retried = await api<Record<string, unknown>>(`/api/generation/jobs/${currentJobId}/retry`, {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.aiGeneration,
        timeoutLabel: "generation retry",
        body: JSON.stringify({
          run_now: true,
          idempotency_key: `web-retry-${currentJobId}-${Date.now()}`,
          allow_budget_overrun: Boolean(modelSettings.has_api_key && batchRequest.allow_budget_overrun),
          allow_reviewer_quality_downgrade: Boolean(modelSettings.has_api_key && batchRequest.allow_reviewer_quality_downgrade),
        }),
      });
      setJob(retried);
      setJobRetryPreview(null);
      setStatus("success", `定向重试完成：${String(retried.status || "-")}`);
    } catch (error) {
      setStatus("error", error instanceof Error ? error.message : "定向重试失败");
    }
  }

  function isTerminalGenerationBatchStatus(status: string | null | undefined) {
    return ["completed", "partial", "failed", "cancelled", "timed_out"].includes(status || "");
  }

  function batchLabelFor(row: GenerationBatch | null | undefined) {
    const request = asRecord(row?.request);
    const progress = asRecord(row?.progress);
    const origin = metadataString(request.origin || progress.origin, "");
    return origin === "mastery_variant_batch" ? "批量类似题" : "套题出题";
  }

  function batchCanCancel(row: GenerationBatch | null | undefined) {
    return Boolean(row?.id) && !["completed", "partial", "failed", "cancelled", "cancelling", "timed_out"].includes(row?.status || "");
  }

  function activeBatchesForNewRequest(excludeBatchId?: string) {
    const seen = new Set<string>();
    return [batch, ...batchHistory]
      .filter((row): row is GenerationBatch => Boolean(row?.id))
      .filter((row) => {
        if (excludeBatchId && row.id === excludeBatchId) return false;
        if (seen.has(row.id)) return false;
        seen.add(row.id);
        return !isTerminalGenerationBatchStatus(row.status);
      });
  }

  function confirmParallelBatchStart(nextLabel: string, excludeBatchId?: string) {
    const activeRows = activeBatchesForNewRequest(excludeBatchId);
    if (!activeRows.length) return { proceed: true, allowParallel: false };
    const summary = activeRows
      .slice(0, 4)
      .map((row) => `${batchLabelFor(row)} ${row.id.slice(0, 8)}(${row.status} ${row.accepted_count}/${row.target_count}, 并发 ${row.concurrency})`)
      .join("、");
    const proceed = window.confirm(
      localizeUi(`当前还有 ${activeRows.length} 个未完成出题批次：${summary}。继续会并行创建新的${nextLabel}，不会覆盖旧批次，但会共享全局 AI 并发并可能变慢或触发 provider 限流。旧批次可在批次历史中直接中断；异常卡住可点“修复异常状态”。是否继续？`),
    );
    return { proceed, allowParallel: proceed };
  }

  function updateBatchState(nextBatch: GenerationBatch) {
    const nextRequest = asRecord(nextBatch.request);
    const nextProgress = asRecord(nextBatch.progress);
    const nextProgressSession = asRecord(nextProgress.practice_session);
    const progressSessionId = metadataString(nextProgress.practice_session_id || nextProgressSession.id, "");
    const nextLabel = metadataString(nextRequest.origin, "") === "mastery_variant_batch" ? "批量类似题" : "套题出题";
    setBatch(nextBatch);
    setBatchHistory((current) => [nextBatch, ...current.filter((row) => row.id !== nextBatch.id)].slice(0, 20));
    setReportBatchId(nextBatch.id);
    if (progressSessionId) {
      setBatchPracticeSessionId(progressSessionId);
      batchPracticeSessionRef.current = progressSessionId;
    }
    const accepted = nextBatch.accepted_count || 0;
    if (batchPracticeSessionRef.current && accepted > lastSyncedBatchAcceptedRef.current) {
      lastSyncedBatchAcceptedRef.current = accepted;
      syncGeneratedPractice(nextBatch.id, batchPracticeSessionRef.current).catch(() => undefined);
    }
    if (isTerminalGenerationBatchStatus(nextBatch.status)) {
      stopBatchPolling(nextBatch.id);
      setStatus(nextBatch.status === "completed" ? "success" : "error", `${nextLabel}${nextBatch.status === "completed" ? "完成" : "结束"}：已接受 ${nextBatch.accepted_count}/${nextBatch.target_count}`);
    }
  }

  function stopBatchPolling(batchId?: string) {
    const current = batchPollAbortRef.current;
    if (!current) return;
    if (!batchId || current.batchId === batchId) {
      current.stopped = true;
      batchPollAbortRef.current = null;
    }
  }

  async function pollBatch(batchId: string) {
    stopBatchPolling();
    const token = { batchId, stopped: false };
    batchPollAbortRef.current = token;
    for (let attempt = 0; attempt < 240; attempt += 1) {
      await new Promise<void>((resolve) => window.setTimeout(resolve, attempt === 0 ? 1200 : 4000));
      if (token.stopped || batchPollAbortRef.current !== token) return;
      try {
        const nextBatch = await api<GenerationBatch>(`/api/generation/batches/${batchId}`, {
          timeoutMs: REQUEST_TIMEOUT_MS.normal,
          timeoutLabel: "套题批次轮询",
        });
        if (token.stopped || batchPollAbortRef.current !== token) return;
        updateBatchState(nextBatch);
        if (isTerminalGenerationBatchStatus(nextBatch.status)) return;
      } catch (error) {
        if (attempt === 0 || attempt % 5 === 0) {
          setStatus("error", errorMessage(error, "套题批次轮询失败，服务可能未启动"));
        }
      }
    }
  }

  async function loadTaskCenter(showStatus = false) {
    const snapshot = await api<TaskCenterResponse>("/api/tasks", {
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "任务中心",
    });
    setTaskCenter(snapshot);
    const activeAgent = snapshot.tasks.find((task) => task.task_type === "emergency_recovery_agent" && ["queued", "running"].includes(task.status));
    if (activeAgent?.target_id && (!recoveryAgentRun || recoveryAgentRun.id !== activeAgent.target_id || ["queued", "running"].includes(recoveryAgentRun.status))) {
      void loadRecoveryAgentRun(activeAgent.target_id);
    }
    if (showStatus) {
      setStatus("success", `任务中心已刷新：运行 ${snapshot.running_count}，等待 ${snapshot.queued_count}，失败 ${snapshot.failed_count}`);
    }
    return snapshot;
  }

  async function loadRecoveryAgentRun(runId: string) {
    const response = await api<{ ok: boolean; run: RecoveryAgentRun }>(`/api/recovery/agent-runs/${encodeURIComponent(runId)}`, {
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "高级 AI 诊断状态",
    });
    setRecoveryAgentRun(response.run);
    setRecoveryAgentHistory((runs) => [response.run, ...runs.filter((run) => run.id !== response.run.id)]);
    return response.run;
  }

  async function cancelSystemTask(task: SystemTaskSummary) {
    try {
      setStatus("loading", `正在取消：${task.title}`);
      const result = await api<{ ok: boolean; message?: string; task_center?: TaskCenterResponse }>(`/api/tasks/${encodeURIComponent(task.task_id)}/cancel`, {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.normal,
        timeoutLabel: "取消任务",
      });
      if (result.task_center) setTaskCenter(result.task_center);
      if (task.target_type === "generation_batch" && task.target_id) {
        try {
          updateBatchState(await api<GenerationBatch>(`/api/generation/batches/${task.target_id}`));
        } catch {
          // Task center already shows the cancellation result.
        }
      }
      setStatus("success", result.message || "任务已收到取消请求");
    } catch (error) {
      setStatus("error", errorMessage(error, "取消任务失败"));
    }
  }

  async function retrySystemTask(task: SystemTaskSummary) {
    try {
      setStatus("loading", `正在重试：${task.title}`);
      const result = await api<{ ok: boolean; message?: string; task_center?: TaskCenterResponse }>(`/api/tasks/${encodeURIComponent(task.task_id)}/retry`, {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.normal,
        timeoutLabel: "重试任务",
      });
      if (result.task_center) setTaskCenter(result.task_center);
      setStatus("success", result.message || "任务已重新加入队列");
    } catch (error) {
      setStatus("error", errorMessage(error, "重试任务失败"));
    }
  }

  async function startRecoveryAgent(restartExisting = false) {
    try {
      setStatus("loading", restartExisting ? "正在重启高级 AI 诊断修复" : "正在启动高级 AI 诊断修复");
      const response = await api<{ ok: boolean; already_running?: boolean; run: RecoveryAgentRun }>("/api/recovery/agent-runs", {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.aiInteractive,
        timeoutLabel: "高级 AI 诊断修复启动",
        body: JSON.stringify({
          max_loops: 10,
          output_language: outputLanguage,
          restart_existing: restartExisting,
          note: operationStatus.errorCode || operationStatus.traceId || operationStatus.label || null,
        }),
      });
      setRecoveryAgentRun(response.run);
      await loadTaskCenter(false).catch(() => undefined);
      setStatus("success", response.already_running ? "高级 AI 诊断修复已在运行，已显示当前进度" : "高级 AI 诊断修复已启动，最多 10 轮，可随时中断");
    } catch (error) {
      setStatus("error", errorMessage(error, "高级 AI 诊断修复启动失败"));
    }
  }

  async function cancelRecoveryAgent() {
    if (!recoveryAgentRun?.id) return;
    try {
      setStatus("loading", "正在中断高级 AI 诊断修复");
      const response = await api<{ ok: boolean; run: RecoveryAgentRun }>(`/api/recovery/agent-runs/${encodeURIComponent(recoveryAgentRun.id)}/cancel`, {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.normal,
        timeoutLabel: "中断高级 AI 诊断",
      });
      setRecoveryAgentRun(response.run);
      await loadTaskCenter(false).catch(() => undefined);
      setStatus("success", "高级 AI 诊断修复已收到中断请求；已完成的安全动作会保留");
    } catch (error) {
      setStatus("error", errorMessage(error, "中断高级 AI 诊断失败"));
    }
  }

  function watchBatch(batchId: string) {
    batchEventsRef.current?.close();
    if (watchedBatchIdRef.current !== batchId) {
      lastBatchEventIdRef.current = "";
      lastBatchRecoveryNoticeEventIdRef.current = "";
      batchStreamInterruptedRef.current = false;
      watchedBatchIdRef.current = batchId;
    }
    const source = new LanguageEventSource(`${API_BASE}/api/generation/batches/${batchId}/events`);
    batchEventsRef.current = source;
    const acceptEvent = (event: MessageEvent) => {
      if (!event.lastEventId) {
        return true;
      }
      if (lastBatchEventIdRef.current && event.lastEventId <= lastBatchEventIdRef.current) {
        return false;
      }
      if (batchStreamInterruptedRef.current && event.lastEventId !== lastBatchRecoveryNoticeEventIdRef.current) {
        batchStreamInterruptedRef.current = false;
        lastBatchRecoveryNoticeEventIdRef.current = event.lastEventId;
        setStatus("success", `套题出题进度已从断线位置恢复，当前事件 ${event.lastEventId}`);
      }
      lastBatchEventIdRef.current = event.lastEventId;
      return true;
    };
    source.addEventListener("batch_item", (event) => {
      acceptEvent(event as MessageEvent);
    });
    source.addEventListener("batch", (event) => {
      const messageEvent = event as MessageEvent;
      if (!acceptEvent(messageEvent)) {
        return;
      }
      const nextBatch = JSON.parse(messageEvent.data) as GenerationBatch;
      updateBatchState(nextBatch);
    });
    source.addEventListener("done", (event) => {
      if (!acceptEvent(event as MessageEvent)) {
        return;
      }
      batchStreamInterruptedRef.current = false;
      source.close();
      batchEventsRef.current = null;
    });
    source.addEventListener("timeout", (event) => {
      acceptEvent(event as MessageEvent);
      batchStreamInterruptedRef.current = true;
      source.close();
      batchEventsRef.current = null;
      pollBatch(batchId);
      setStatus("error", "套题出题监听超时，已切换为轮询刷新");
    });
    source.onerror = () => {
      batchStreamInterruptedRef.current = true;
      source.close();
      batchEventsRef.current = null;
      pollBatch(batchId);
      setStatus("error", "套题出题进度连接中断，已切换为轮询刷新");
    };
  }

  async function submitBatchGenerationRequest(requestBody: Record<string, unknown>) {
    try {
      setStatus("loading", modelSettings.has_api_key ? "正在把套题出题加入任务队列" : "正在把模拟套题出题加入任务队列");
      const created = await api<GenerationBatch>("/api/generation/batches", {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.batchStart,
        timeoutLabel: "套题出题启动",
        body: JSON.stringify({ ...requestBody, allow_parallel_batches: false }),
      });
      updateBatchState(created);
      void loadTaskCenter(false).catch(() => undefined);
      setReportBatchId(created.id);
      setReportType("generation_batch");
      setActiveWorkspace("generated");
      setBatchPracticeSessionId("");
      batchPracticeSessionRef.current = "";
      lastSyncedBatchAcceptedRef.current = 0;
      watchBatch(created.id);
      pollBatch(created.id);
      setStatus("success", `批量任务已加入队列：${created.target_count} 题，批次内并发 ${created.concurrency}；如已有批次运行会自动等待，可在任务中心取消`);
    } catch (error) {
      setStatus("error", errorMessage(error, "套题出题启动失败"));
    }
  }

  async function startBatchGeneration(e?: FormEvent) {
    e?.preventDefault();
    const targetTags = generationTargetTags();
    const tagNotes = selectedGenerationTagNotes();
    const customDescription = [batchRequest.custom_description, tagNotes ? `标签说明：\n${tagNotes}` : ""].filter(Boolean).join("\n\n");
    await submitBatchGenerationRequest({
      target_tags: targetTags,
      difficulty_star: batchRequest.difficulty_star ? Number(batchRequest.difficulty_star) : null,
      count: requestedBatchCount,
      concurrency: requestedBatchConcurrency,
      constraints: generationConstraintsText || null,
      custom_description: customDescription || null,
      solution_style: "elementary_preferred",
      use_mock: useMockModel(),
      prefer_reuse_existing_ai: batchRequest.prefer_reuse_existing_ai,
      candidate_count: requestedCandidateCount,
      source_example_count: requestedSourceExampleCount,
      ai_negative_example_count: requestedAiNegativeExampleCount,
      allow_budget_overrun: Boolean(modelSettings.has_api_key && batchRequest.allow_budget_overrun),
      allow_reviewer_quality_downgrade: Boolean(modelSettings.has_api_key && batchRequest.allow_reviewer_quality_downgrade),
    });
  }

  async function refreshBatch() {
    if (!batch?.id) return;
    try {
      const nextBatch = await api<GenerationBatch>(`/api/generation/batches/${batch.id}`);
      updateBatchState(nextBatch);
      const replayHint = lastBatchEventIdRef.current ? `，已从事件 ${lastBatchEventIdRef.current} 之后恢复状态` : "";
      setStatus("success", `批次状态已刷新${replayHint}`);
      batchStreamInterruptedRef.current = false;
      if (!isTerminalGenerationBatchStatus(nextBatch.status)) {
        pollBatch(nextBatch.id);
      }
    } catch (error) {
      setStatus("error", errorMessage(error, "批次状态刷新失败"));
    }
  }

  useEffect(() => {
    if (!sessionId || sessionState?.id !== sessionId || !["practice", "generated"].includes(activeWorkspace)) return;
    const queue = [...new Set([...sessionState.question_ids, ...activeInterventionQuestionIds])];
    setSelectedQuestion((current) => queue.includes(current) ? current : queue[0] || "");
  }, [activeWorkspace, sessionId, sessionState, selectedQuestion]);

  async function refreshAllContent() {
    setStatus("loading", "正在刷新全部内容并恢复当前进度");
    const currentBatchId = batch?.id || reportBatchId;
    const currentSessionId = sessionId || batchPracticeSessionId;
    const tasks: Promise<unknown>[] = [
      refresh(activeLearnerId, questionOffset, questionFilters, selectedTagIds),
      loadBatchHistory(),
      loadHistoryGroups(activeLearnerId),
      loadTaskCenter(false).catch(() => undefined),
      loadAiUsage(false).catch(() => undefined),
      loadModelSettings(),
    ];
    if (currentBatchId) {
      tasks.push(
        api<GenerationBatch>(`/api/generation/batches/${currentBatchId}`)
          .then((nextBatch) => {
            updateBatchState(nextBatch);
            if (!isTerminalGenerationBatchStatus(nextBatch.status)) {
              watchBatch(nextBatch.id);
              pollBatch(nextBatch.id);
            }
          })
      );
    }
    if (currentSessionId) {
      tasks.push(
        api<SessionState>(`/api/practice/sessions/${currentSessionId}`)
          .then((state) => {
            setSessionState(state);
            setSessionId(state.id);
            setSessionQuestions(state.question_ids);
            setSelectedQuestion((current) => current && state.question_ids.includes(current) ? current : state.question_ids[0] || "");
          })
      );
    }
    const results = await Promise.allSettled(tasks);
    const failed = results.filter((row) => row.status === "rejected").length;
    if (failed) {
      setStatus("error", `全量刷新完成，但有 ${failed} 项读取失败；历史、当前答题和 API 配置不会被清空。`);
    } else {
      setStatus("success", "全量刷新完成：已恢复题库摘要、批次、历史、报告对象、当前答题和 API 配置。");
    }
  }

  async function recoverStaleState() {
    setStatus("loading", "正在修复异常状态：只终止卡住的运行态，不删除历史、题库、报告或密钥");
    try {
      const result = await api<StaleRecoveryResult>("/api/recovery/stale-state", {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.normal,
        timeoutLabel: "异常状态修复",
        body: JSON.stringify({ stale_after_seconds: 300, force_active_items: false }),
      });
      setRecoveryResult(result);
      if (batch?.id) {
        try {
          updateBatchState(await api<GenerationBatch>(`/api/generation/batches/${batch.id}`));
        } catch {
          // Best-effort refresh only; recovery result remains visible below.
        }
      }
      await Promise.all([loadBatchHistory(), loadHistoryGroups(activeLearnerId), loadTaskCenter(false).catch(() => undefined)]);
      const counts = result.counts || {};
      setStatus(
        "success",
        `异常状态已修复：批次 ${counts.batches_terminalized || 0}，题目任务 ${counts.items_timed_out || 0}，模型任务 ${counts.jobs_timed_out || 0}，提示题 ${counts.interventions_failed || 0}`,
      );
    } catch (error) {
      setStatus("error", errorMessage(error, "异常状态修复失败"));
    }
  }

  async function topUpCurrentBatch() {
    if (!batch?.id) return;
    if (activeBatchIsMasteryVariant) {
      setStatus("error", "批量类似题不走普通套题补齐；请回历史重新选择题目生成新批次。");
      return;
    }
    const missing = Math.max(0, batch.target_count - batch.accepted_count);
    if (!missing) {
      setStatus("success", "这套题已经达到目标数量，不需要补齐");
      return;
    }
    if (!window.confirm(localizeUi(`确认补齐这套题剩余 ${missing} 道？系统只保留已接受题，继续为缺失/失败 slot 生成，仍受尝试次数和预算上限限制。`))) return;
    setStatus("loading", "正在把补齐任务加入队列");
    try {
      const nextBatch = await api<GenerationBatch>(`/api/generation/batches/${batch.id}/top-up`, {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.batchStart,
        timeoutLabel: "套题补齐",
        body: JSON.stringify({
          run_now: true,
          allow_budget_overrun: Boolean(modelSettings.has_api_key && batchRequest.allow_budget_overrun),
          allow_reviewer_quality_downgrade: Boolean(modelSettings.has_api_key && batchRequest.allow_reviewer_quality_downgrade),
          allow_parallel_batches: false,
        }),
      });
      updateBatchState(nextBatch);
      void loadTaskCenter(false).catch(() => undefined);

      watchBatch(nextBatch.id);
      pollBatch(nextBatch.id);
      setStatus("success", `补齐任务已加入队列：当前 ${nextBatch.accepted_count}/${nextBatch.target_count}，可在任务中心取消`);
    } catch (error) {
      setStatus("error", errorMessage(error, "套题补齐失败"));
    }
  }

  async function loadBatchHistory() {
    const rows = await api<GenerationBatch[]>("/api/generation/batches?limit=20", {
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "套题批次历史",
    });
    setBatchHistory(rows);
  }

  function openBatchFromHistory(row: GenerationBatch) {
    updateBatchState(row);
    setReportBatchId(row.id);
    setReportType("generation_batch");
    if (!isTerminalGenerationBatchStatus(row.status)) {
      watchBatch(row.id);
      pollBatch(row.id);
    }
    setStatus("success", `已打开套题批次：${row.id.slice(0, 8)}`);
  }

  function copyBatchConfigToForm(row: GenerationBatch | null = batch) {
    if (!row) return;
    setGenerationConstraintsEdited(true);
    const request = asRecord(row.request);
    const progress = asRecord(row.progress);
    const targetTags = metadataStringArray(request.target_tags);
    const matchedTagIds = tags
      .filter((tag) => targetTags.includes(tag.id) || targetTags.includes(tag.label))
      .map((tag) => tag.id);
    const matchedValues = new Set(tags.filter((tag) => matchedTagIds.includes(tag.id)).flatMap((tag) => [tag.id, tag.label]));
    const unmatchedTags = targetTags.filter((value) => !matchedValues.has(value));
    const copiedCustomDescription = metadataString(request.custom_description, "");
    let copiedConstraints = metadataString(request.constraints, "");
    if (copiedCustomDescription && copiedConstraints.includes(copiedCustomDescription)) {
      copiedConstraints = copiedConstraints.replace(copiedCustomDescription, "").replace(/\n{3,}/g, "\n\n").trim();
    }
    setSelectedTagIds(Array.from(new Set(matchedTagIds)));
    setBatchRequest((current) => ({
      ...current,
      count: String(row.target_count || metadataNumber(request.count) || current.count),
      concurrency: String(row.concurrency || metadataNumber(request.batch_concurrency) || current.concurrency),
      difficulty_star: String(metadataNumber(request.difficulty_star) || current.difficulty_star),
      target_tags: unmatchedTags.join(", "),
      custom_description: copiedCustomDescription,
      constraints: copiedConstraints,
      prefer_reuse_existing_ai: request.prefer_reuse_existing_ai !== false,
      candidate_count: String(metadataNumber(request.candidate_count) ?? metadataNumber(progress.candidate_count) ?? current.candidate_count),
      source_example_count: String(metadataNumber(request.source_example_count) ?? metadataNumber(progress.source_example_count) ?? current.source_example_count),
      ai_negative_example_count: String(metadataNumber(request.ai_negative_example_count) ?? metadataNumber(progress.ai_negative_example_count) ?? current.ai_negative_example_count),
      allow_budget_overrun: Boolean(request.allow_budget_overrun),
      allow_reviewer_quality_downgrade: Boolean(request.allow_reviewer_quality_downgrade),
    }));
    setActiveWorkspace("generated");
    setStatus("success", `已复制批次 ${row.id.slice(0, 8)} 的配置到表单，请确认后再开始生成新批次`);
  }

  function applyReportTarget(target = reportTargetOptions[0]) {
    if (!target) {
      setStatus("error", "没有可选报告对象，请先做题、出套题或刷新历史");
      return;
    }
    setReportType(target.report_type);
    if (target.practice_session_id) setReportSessionId(target.practice_session_id);
    if (target.generation_batch_id) setReportBatchId(target.generation_batch_id);
    setActiveWorkspace("reports");
    setStatus("success", `已选择报告对象：${target.label}`);
  }

  async function refreshReportTargets() {
    setActiveWorkspace("reports");
    setStatus("loading", "正在刷新可选报告对象");
    try {
      await Promise.all([loadBatchHistory(), loadHistoryGroups(), loadReportSummary()]);
      setStatus("success", "可选报告对象已刷新，可以从当前练习、套题批次、批次历史或历史组中选择");
    } catch (error) {
      setStatus("error", errorMessage(error, "可选报告对象刷新失败"));
    }
  }

  function assistantPageContext(): Record<string, unknown> {
    return {
      page: activeWorkspace,
      app_mode: appMode,
      learner_id: activeLearnerId || null,
      session_id: sessionId || null,
      session_mode: sessionState?.mode || null,
      selected_question_id: selectedQuestion || null,
      selected_question_source: currentQuestion?.source_name || null,
      selected_question_star: currentQuestion?.difficulty_star || null,
      selected_answer_type: currentQuestionDetail?.answer_spec?.answer_type || null,
      answer_text_present: Boolean(answer.trim()),
      thought_text_present: Boolean(thought.trim()),
      answer_locked: answerLocked,
      batch_id: batch?.id || null,
      batch_status: batch?.status || null,
      batch_practice_session_id: batchPracticeSessionId || null,
      selected_history_group_id: selectedHistoryGroupId || null,
      selected_history_question_count: selectedHistoryQuestionIds.length,
      selected_bank_question_count: selectedBankQuestionIds.length,
      report_type: reportType,
      report_session_id: reportSessionId || null,
      report_batch_id: reportBatchId || null,
      report_filters: reportFilters,
      question_filters: questionFilters,
      selected_tag_ids: selectedTagIds,
      selected_tag_labels: selectedTags.map((tag) => tag.label),
      batch_request_summary: {
        count: batchRequest.count,
        concurrency: batchRequest.concurrency,
        difficulty_star: batchRequest.difficulty_star,
        target_tags: batchRequest.target_tags,
        prefer_reuse_existing_ai: batchRequest.prefer_reuse_existing_ai,
        candidate_count: batchRequest.candidate_count,
      },
      strict_test_request: strictTestRequest,
      task_center_counts: taskCenter ? {
        queued: taskCenter.queued_count,
        running: taskCenter.running_count,
        failed: taskCenter.failed_count,
        stale: taskCenter.stale_count || 0,
      } : null,
    };
  }

  async function ensureAssistantConversation() {
    if (assistantConversation?.id) return assistantConversation;
    const created = await api<{ conversation: AssistantConversation }>("/api/assistant/conversations", {
      method: "POST",
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "assistant conversation",
      body: JSON.stringify({
        learner_id: activeLearnerId || null,
        mode: appMode,
        title: "项目 AI 助手",
        page_context: assistantPageContext(),
      }),
    });
    setAssistantConversation(created.conversation);
    return created.conversation;
  }

  async function handleAssistantActions(response: AssistantResponse) {
    for (const action of response.actions_completed || []) {
      const result = asRecord(action.result);
      if (action.tool_name === "create_generation_batch") {
        const nextBatch = asRecord(result.batch);
        if (nextBatch.id) {
          setBatch(nextBatch as unknown as GenerationBatch);
          setActiveWorkspace("generated");
          void loadTaskCenter(false).catch(() => undefined);
          void loadBatchHistory().catch(() => undefined);
        }
      }
      if (action.tool_name === "create_practice_session") {
        const session = asRecord(result.session);
        const nextSessionId = metadataString(session.id, "");
        const questionIds = Array.isArray(session.question_ids) ? session.question_ids.map(String) : [];
        if (nextSessionId) {
          setSessionId(nextSessionId);
          setSessionQuestions(questionIds);
          setSelectedQuestion(questionIds[0] || "");
          setSessionState(await api<SessionState>(`/api/practice/sessions/${nextSessionId}`));
          setActiveWorkspace("practice");
        }
      }
      if (action.tool_name === "generate_ai_report") {
        const report = asRecord(result.report);
        if (Object.keys(report).length) {
          setAiReport(report as unknown as AiReport);
          setActiveWorkspace("reports");
        }
      }
      if (["cancel_task", "retry_task", "recover_stale_state", "delete_ai_generated_questions"].includes(action.tool_name)) {
        void loadTaskCenter(false).catch(() => undefined);
        void refresh(activeLearnerId, questionOffset, questionFilters, selectedTagIds).catch(() => undefined);
      }
    }
  }

  function mergeAssistantResponse(response: AssistantResponse) {
    if (response.conversation) setAssistantConversation(response.conversation);
    if (response.message) setAssistantMessages((current) => [...current, response.message as AssistantMessage]);
    setAssistantLastResponse(response);
    setAssistantPendingConfirmations((current) => {
      const byId = new Map(current.map((item) => [item.confirmation_id, item]));
      (response.confirmations_required || []).forEach((item) => byId.set(item.confirmation_id, item));
      return Array.from(byId.values());
    });
  }

  async function sendAssistantMessage() {
    const content = assistantInput.trim();
    if (!content || assistantBusy) return;
    setAssistantOpen(true);
    setAssistantInput("");
    setAssistantBusy(true);
    const localUserMessage: AssistantMessage = {
      id: `local-${Date.now()}`,
      role: "user",
      content,
      created_at: new Date().toISOString(),
    };
    setAssistantMessages((current) => [...current, localUserMessage]);
    try {
      const conversation = await ensureAssistantConversation();
      const response = await api<AssistantResponse>(`/api/assistant/conversations/${conversation.id}/messages`, {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.aiInteractive,
        timeoutLabel: "project assistant",
        body: JSON.stringify({
          content,
          page_context: assistantPageContext(),
        }),
      });
      mergeAssistantResponse(response);
      await handleAssistantActions(response);
      setStatus("success", response.confirmations_required?.length ? "项目助手已生成结果，需确认后才执行动作" : "项目助手已返回结果");
    } catch (error) {
      setAssistantMessages((current) => [...current, {
        id: `assistant-error-${Date.now()}`,
        role: "assistant",
        content: errorMessage(error, "项目助手请求失败"),
        created_at: new Date().toISOString(),
      }]);
      setStatus("error", errorMessage(error, "项目助手请求失败"));
    } finally {
      setAssistantBusy(false);
    }
  }

  async function confirmAssistantAction(item: AssistantConfirmation) {
    if (!assistantConversation?.id || assistantBusy) return;
    setAssistantBusy(true);
    try {
      const response = await api<AssistantResponse & { ok?: boolean; changed?: boolean }>(`/api/assistant/conversations/${assistantConversation.id}/confirmations/${item.confirmation_id}`, {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.aiGeneration,
        timeoutLabel: "assistant confirmation",
        body: JSON.stringify({
          note: assistantConfirmationNotes[item.confirmation_id] || null,
        }),
      });
      setAssistantPendingConfirmations((current) => current.filter((row) => row.confirmation_id !== item.confirmation_id));
      mergeAssistantResponse(response);
      await handleAssistantActions(response);
      setStatus("success", "项目助手确认动作已执行");
    } catch (error) {
      setStatus("error", errorMessage(error, "项目助手确认动作失败"));
    } finally {
      setAssistantBusy(false);
    }
  }

  async function cancelAssistantConversation() {
    if (!assistantConversation?.id) {
      setAssistantBusy(false);
      setStatus("success", "项目助手本次输入已中断");
      return;
    }
    try {
      await api(`/api/assistant/conversations/${assistantConversation.id}/cancel`, {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.normal,
        timeoutLabel: "assistant cancel",
        body: JSON.stringify({}),
      });
      setAssistantBusy(false);
      setStatus("success", "已请求中断项目助手；已完成的安全动作会保留记录");
    } catch (error) {
      setStatus("error", errorMessage(error, "项目助手中断失败"));
    }
  }

  function applyAssistantDraft(draft: AssistantUiDraft) {
    const payload = asRecord(draft.payload);
    if (draft.draft_type === "generation_batch") {
      if (typeof payload.constraints === "string") setGenerationConstraintsEdited(true);
      const tagIds = Array.isArray(payload.tag_ids) ? payload.tag_ids.map(String) : selectedTagIds;
      setSelectedTagIds(tagIds);
      setBatchRequest((current) => ({
        ...current,
        count: metadataString(payload.count, current.count),
        concurrency: metadataString(payload.concurrency, current.concurrency),
        difficulty_star: metadataString(payload.difficulty_star, current.difficulty_star),
        target_tags: metadataString(payload.target_tags, current.target_tags),
        custom_description: metadataString(payload.custom_description, current.custom_description),
        constraints: metadataString(payload.constraints, current.constraints),
        prefer_reuse_existing_ai: typeof payload.prefer_reuse_existing_ai === "boolean" ? payload.prefer_reuse_existing_ai : current.prefer_reuse_existing_ai,
        candidate_count: metadataString(payload.candidate_count, current.candidate_count),
        source_example_count: metadataString(payload.source_example_count, current.source_example_count),
        ai_negative_example_count: metadataString(payload.ai_negative_example_count, current.ai_negative_example_count),
        allow_budget_overrun: Boolean(payload.allow_budget_overrun ?? current.allow_budget_overrun),
        allow_reviewer_quality_downgrade: Boolean(payload.allow_reviewer_quality_downgrade ?? current.allow_reviewer_quality_downgrade),
      }));
      setActiveWorkspace("generated");
      setStatus("success", "项目助手草稿已填入套题表单，确认后再开始生成");
      return;
    }
    if (draft.draft_type === "practice_session") {
      if (Array.isArray(payload.tag_ids)) setSelectedTagIds(payload.tag_ids.map(String));
      setPracticeCount(metadataString(payload.count, practiceCount));
      setPracticeSourceName(metadataString(payload.source_name, practiceSourceName));
      setPracticeDifficultyStar(metadataString(payload.difficulty_star, practiceDifficultyStar));
      if (typeof payload.exclude_done === "boolean") setPracticeExcludeDone(payload.exclude_done);
      setActiveWorkspace("practice");
      setStatus("success", "项目助手草稿已填入普通练习表单");
      return;
    }
    if (draft.draft_type === "strict_test") {
      if (Array.isArray(payload.tag_ids)) setSelectedTagIds(payload.tag_ids.map(String));
      setStrictTestRequest((current) => ({
        ...current,
        count: metadataString(payload.count, current.count),
        use_custom_quota: Boolean(payload.use_custom_quota ?? true),
        fill_missing_with_ai: Boolean(payload.fill_missing_with_ai ?? current.fill_missing_with_ai),
        star_1: metadataString(payload.star_1, current.star_1),
        star_2: metadataString(payload.star_2, current.star_2),
        star_3: metadataString(payload.star_3, current.star_3),
        star_4: metadataString(payload.star_4, current.star_4),
        star_5: metadataString(payload.star_5, current.star_5),
      }));
      setActiveWorkspace("practice");
      setStatus("success", "项目助手草稿已填入严格测试配置");
      return;
    }
    if (draft.draft_type === "report_request") {
      const nextReportType = metadataString(payload.report_type, reportType) as typeof reportType;
      if (["history", "session", "generation_batch"].includes(nextReportType)) setReportType(nextReportType);
      setReportSessionId(metadataString(payload.practice_session_id, reportSessionId));
      setReportBatchId(metadataString(payload.generation_batch_id, reportBatchId));
      const filters = asRecord(payload.filters);
      setReportFilters((current) => ({
        ...current,
        source_name: metadataString(filters.source_name, current.source_name),
        difficulty_star: metadataString(filters.difficulty_star, current.difficulty_star),
        premium: metadataString(filters.premium, current.premium),
        mode: metadataString(filters.mode, current.mode),
      }));
      setActiveWorkspace("reports");
      setStatus("success", "项目助手草稿已填入报告页");
      return;
    }
    if (draft.draft_type === "question_filter") {
      setQuestionFilters((current) => ({
        ...current,
        q: metadataString(payload.q, current.q),
        source_name: metadataString(payload.source_name, current.source_name),
        difficulty_star: metadataString(payload.difficulty_star, current.difficulty_star),
        premium: metadataString(payload.premium, current.premium),
        status: metadataString(payload.status, current.status),
      }));
      if (Array.isArray(payload.tag_ids)) setSelectedTagIds(payload.tag_ids.map(String));
      setActiveWorkspace("bank");
      setStatus("success", "项目助手草稿已填入题库筛选");
    }
  }

  function assistantResultPreview(result: AssistantToolResult) {
    if (result.error) return result.error;
    const payload = result.result || {};
    const text = JSON.stringify(payload, null, 2);
    return text.length > 800 ? `${text.slice(0, 800)}...` : text;
  }

  function openBatchReport(batchId: string) {
    setReportType("generation_batch");
    setReportBatchId(batchId);
    setActiveWorkspace("reports");
    setStatus("success", "已切换到这套题的报告视图");
  }

  function historyQuery(path = "/api/history/groups", learnerId = activeLearnerId) {
    const params = new URLSearchParams();
    params.set("limit", "80");
    if (learnerId) params.set("learner_id", learnerId);
    if (historyFilters.group_type) params.set("group_type", historyFilters.group_type);
    if (historyFilters.date_from) params.set("date_from", historyFilters.date_from);
    if (historyFilters.date_to) params.set("date_to", historyFilters.date_to);
    if (historyFilters.source_name) params.set("source_name", historyFilters.source_name);
    if (historyFilters.difficulty_star) params.set("difficulty_star", historyFilters.difficulty_star);
    if (historyFilters.tag_id) params.append("tag_id", historyFilters.tag_id);
    if (historyFilters.weak_only) params.set("weak_only", "true");
    return `${path}?${params.toString()}`;
  }

  async function loadHistoryGroups(learnerId = activeLearnerId) {
    setStatus("loading", "正在读取历史组");
    const rows = await api<HistoryGroup[]>(historyQuery("/api/history/groups", learnerId), {
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "历史组读取",
    });
    setHistoryGroups(rows);
    const nextGroupId = selectedHistoryGroupId && rows.some((row) => row.id === selectedHistoryGroupId)
      ? selectedHistoryGroupId
      : rows[0]?.id || "";
    setSelectedHistoryGroupId(nextGroupId);
    setSelectedHistoryQuestionIds([]);
    if (nextGroupId) {
      await loadHistoryDetail(nextGroupId, learnerId);
    } else {
      setHistoryDetail(null);
    }
    setStatus("success", rows.length ? `已读取 ${rows.length} 个历史组` : "暂无历史记录");
  }

  async function loadHistoryDetail(groupId = selectedHistoryGroupId, learnerId = activeLearnerId) {
    if (!groupId) return;
    const detail = await api<HistoryDetail>(historyQuery(`/api/history/groups/${encodeURIComponent(groupId)}`, learnerId), {
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "历史组详情",
    });
    setHistoryDetail(detail);
    setSelectedHistoryGroupId(groupId);
    setSelectedHistoryQuestionIds((current) => current.filter((id) => detail.items.some((item) => item.question_id === id)));
  }

  async function applyHistoryFilters(e: FormEvent) {
    e.preventDefault();
    await loadHistoryGroups();
  }

  function openHistoryGroup(group: HistoryGroup) {
    setSelectedHistoryGroupId(group.id);
    setSelectedHistoryQuestionIds([]);
    loadHistoryDetail(group.id)
      .then(() => setStatus("success", `已打开历史组：${group.title}`))
      .catch((error) => setStatus("error", errorMessage(error, "历史组详情读取失败")));
  }

  function historyVariantSelectionKey(groupId: string, item: HistoryItem) {
    return `${groupId}::${item.position}::${item.question_id}`;
  }

  function toggleHistoryQuestion(item: HistoryItem) {
    const questionId = item.question_id;
    setSelectedHistoryQuestionIds((current) => current.includes(questionId) ? current.filter((id) => id !== questionId) : [...current, questionId]);
    if (!historyDetail) return;
    if (!item.available) {
      setStatus("error", "这道题不可练，不能加入类似题生成篮子");
      return;
    }
    const key = historyVariantSelectionKey(historyDetail.id, item);
    setHistoryVariantSelection((current) => {
      if (current[key]) {
        const next = { ...current };
        delete next[key];
        return next;
      }
      return {
        ...current,
        [key]: {
          key,
          group_id: historyDetail.id,
          group_title: historyDetail.title,
          group_type: historyDetail.group_type,
          question_id: item.question_id,
          position: item.position,
          stem_preview: item.stem_preview,
          performance: item.performance,
          performance_label: item.performance_label,
          mastery_variant_done: Boolean(item.mastery_variant_done),
          session_id: historyDetail.session_id || null,
          generation_batch_id: historyDetail.generation_batch_id || null,
        },
      };
    });
  }

  function removeHistoryVariantSelection(key: string) {
    setHistoryVariantSelection((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  function clearHistoryVariantSelection() {
    setHistoryVariantSelection({});
  }

  async function startHistoryMasteryVariantBatch() {
    if (!historyVariantSelectedRows.length) {
      setStatus("error", "请先在历史题号矩阵中选择要生成类似题的题目");
      return;
    }
    const lockKey = `history-mastery-variants:${historyVariantSelectedRows.map((row) => row.key).sort().join("|")}:${requestedHistoryVariantCount}`;
    if (sessionCreateLocksRef.current[lockKey]) {
      setStatus("loading", "正在创建批量类似题，请勿重复点击");
      return;
    }
    sessionCreateLocksRef.current[lockKey] = true;
    setStatus("loading", modelSettings.has_api_key ? "正在把批量类似题加入任务队列" : "正在把模拟批量类似题加入任务队列");
    try {
      const created = await api<GenerationBatch>("/api/mastery-variants/batches", {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.batchStart,
        timeoutLabel: "批量类似题启动",
        body: JSON.stringify({
          learner_id: activeLearnerId || null,
          source_refs: historyVariantSelectedRows.map((row) => ({
            question_id: row.question_id,
            session_id: row.session_id || null,
            group_id: row.group_id,
            group_title: row.group_title,
            group_type: row.group_type,
            position: row.position,
          })),
          variants_per_question: requestedHistoryVariantCount,
          candidate_count: 2,
          concurrency: 2,
          constraints: "根据历史作答证据生成同核心能力、表面不同的掌握检测题；不要直接暴露原题答案或学生薄弱点。",
          solution_style: "elementary_preferred",
          use_mock: useMockModel(),
          allow_budget_overrun: Boolean(modelSettings.has_api_key && batchRequest.allow_budget_overrun),
          allow_reviewer_quality_downgrade: Boolean(modelSettings.has_api_key && batchRequest.allow_reviewer_quality_downgrade),
          allow_parallel_batches: false,
        }),
      });
      updateBatchState(created);
      void loadTaskCenter(false).catch(() => undefined);
      setReportBatchId(created.id);
      setReportType("generation_batch");
      setActiveWorkspace("generated");
      setBatchPracticeSessionId("");
      batchPracticeSessionRef.current = "";
      lastSyncedBatchAcceptedRef.current = 0;
      watchBatch(created.id);
      pollBatch(created.id);
      setStatus("success", `批量类似题已加入队列：${historyVariantSelectedCount} 道来源题，目标 ${historyVariantTargetCount} 题；可在任务中心取消或查看状态`);
    } catch (error) {
      setStatus("error", errorMessage(error, "批量类似题启动失败"));
    } finally {
      sessionCreateLocksRef.current[lockKey] = false;
    }
  }

  async function createReviewSession(scope: "all" | "wrong_only" | "weak_only" | "selected") {
    if (!historyDetail) return;
    if (scope === "selected" && !selectedHistoryQuestionIds.length) {
      setStatus("error", "请先在题号矩阵中选择要重做的题");
      return;
    }
    const lockKey = `history:${historyDetail.id}:${scope}:${selectedHistoryQuestionIds.join(",")}`;
    if (sessionCreateLocksRef.current[lockKey]) {
      setStatus("loading", "正在创建历史重做会话，请勿重复点击");
      return;
    }
    sessionCreateLocksRef.current[lockKey] = true;
    const requestId = makeIdempotencyKey(lockKey);
    setStatus("loading", "正在创建历史重做会话");
    try {
      const created = await api<{ id: string; question_ids: string[]; config: Record<string, unknown> }>(`/api/history/groups/${encodeURIComponent(historyDetail.id)}/review-session`, {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.normal,
        timeoutLabel: "创建历史重做会话",
        body: JSON.stringify({ scope, selected_question_ids: selectedHistoryQuestionIds, learner_id: activeLearnerId || null, idempotency_key: requestId }),
      });
      setSessionId(created.id);
      setSessionQuestions(created.question_ids);
      setSessionState(await api<SessionState>(`/api/practice/sessions/${created.id}`));
      setSelectedQuestion(created.question_ids[0] || "");
      setResult(null);
      setQuestionOutcomes({});
      setAnswer("");
      setThought("");
      setAnswerJudgeResult(null);
      setQuestionSupportResult(null);
      setSolutionExplanationMessage("");
      setSolutionExplanationResult(null);
      setActiveWorkspace("practice");
      const reviewSource = (created.config?.review_source || {}) as Record<string, unknown>;
      const deduped = Number(reviewSource.deduped_count || created.question_ids.length);
      const selectedPositions = Number(reviewSource.selected_position_count || created.question_ids.length);
      const duplicateNote = selectedPositions > deduped ? `，重复题已去重 ${selectedPositions - deduped} 道` : "";
      setStatus("success", `历史重做已创建：${created.question_ids.length} 题${duplicateNote}`);
      sessionCreateLocksRef.current[lockKey] = false;
    } catch (error) {
      sessionCreateLocksRef.current[lockKey] = false;
      setStatus("error", errorMessage(error, "历史重做创建失败"));
    }
  }

  function openHistoryReport(group: HistoryGroup | HistoryDetail) {
    if (group.report_type === "generation_batch" && group.generation_batch_id) {
      setReportType("generation_batch");
      setReportBatchId(group.generation_batch_id);
      setReportSessionId("");
      setStatus("success", "已切换到这套题的报告");
    } else if (group.session_id) {
      setReportType("session");
      setReportSessionId(group.session_id);
      setReportBatchId("");
      setStatus("success", "已切换到这组历史的单次报告");
    } else {
      setReportType("history");
      setReportSessionId("");
      setReportBatchId("");
      setStatus("success", "已切换到历史整体报告");
    }
    setActiveWorkspace("reports");
  }

  async function cancelBatch(targetBatch: GenerationBatch | null | undefined = batch) {
    if (!targetBatch?.id) return;
    const targetLabel = batchLabelFor(targetBatch);
    const isCurrentBatch = targetBatch.id === batch?.id;
    if (!window.confirm(tf("admin.interrupt_valuevalue_value_in_flight_model_calls_may_finish", [t(isCurrentBatch ? "当前" : "历史"), t(targetLabel), targetBatch.id.slice(0, 8)]))) return;
    try {
      const nextBatch = await api<GenerationBatch>(`/api/generation/batches/${targetBatch.id}/cancel`, { method: "POST", body: "{}" });
      void loadTaskCenter(false).catch(() => undefined);
      setBatchHistory((rows) => rows.map((row) => (row.id === nextBatch.id ? nextBatch : row)));
      if (isCurrentBatch) {
        updateBatchState(nextBatch);
        batchEventsRef.current?.close();
        batchEventsRef.current = null;
      }
      if (isTerminalGenerationBatchStatus(nextBatch.status)) {
        stopBatchPolling(targetBatch.id);
        setStatus("success", `${targetLabel} ${targetBatch.id.slice(0, 8)} 已中断。旧在途 provider 晚返回只会写日志或隔离，不会进入可练题池，也不会妨碍下一次生成。`, {
          key: "{0} {1} 已中断。旧在途 provider 晚返回只会写日志或隔离，不会进入可练题池，也不会妨碍下一次生成。",
          values: [targetLabel, targetBatch.id.slice(0, 8)], uiSlots: [0],
        });
      } else {
        if (isCurrentBatch) {
          watchBatch(nextBatch.id);
          pollBatch(nextBatch.id);
        }
        setStatus("success", `已发送${targetLabel} ${targetBatch.id.slice(0, 8)} 中断请求；不会继续派发新题，运行中的 provider 请求返回后会进入 cancelled，不会妨碍下一次生成。`, {
          key: "已发送{0} {1} 中断请求；不会继续派发新题，运行中的 provider 请求返回后会进入 cancelled，不会妨碍下一次生成。",
          values: [targetLabel, targetBatch.id.slice(0, 8)], uiSlots: [0],
        });
      }
    } catch (error) {
      const fallback = `${targetLabel}中断失败`;
      const label = errorMessage(error, fallback);
      setStatus("error", label, label === fallback ? { key: "{0}中断失败", values: [targetLabel], uiSlots: [0] } : undefined);
    }
  }

  async function syncGeneratedPractice(batchId = batch?.id, session = batchPracticeSessionId) {
    if (!batchId) return;
    try {
      const created = await api<{ id: string; question_ids: string[]; config: Record<string, unknown> }>(`/api/generation/batches/${batchId}/practice`, {
        method: "POST",
        body: JSON.stringify({ session_id: session || null, learner_id: activeLearnerId || null }),
      });
      setBatchPracticeSessionId(created.id);
      batchPracticeSessionRef.current = created.id;
      setSessionId(created.id);
      setSessionQuestions(created.question_ids);
      setSessionState(await api<SessionState>(`/api/practice/sessions/${created.id}`));
      setSelectedQuestion((current) => current && created.question_ids.includes(current) ? current : created.question_ids[0] || "");
      setSolutionExplanationMessage("");
      setSolutionExplanationResult(null);
      setStatus("success", `套题练习已同步：${created.question_ids.length} 题可作答`);
    } catch (error) {
      setStatus("error", errorMessage(error, "套题练习同步失败"));
    }
  }

  async function loadAiUsage(showStatus = true) {
    if (showStatus) setStatus("loading", "正在刷新 AI 用量");
    const usage = await api<AiUsage>("/api/settings/ai-usage", {
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "AI usage",
    });
    setAiUsage(usage);
    if (showStatus) {
      const today = usage.today;
      const warning = today.budget_exceeded || usage.budget_exceeded ? "，预算已超出" : "";
      setStatus(today.budget_exceeded || usage.budget_exceeded ? "error" : "success", `AI 用量已刷新：今日 ${today.calls} 次，估算 ${formatUsd(today.estimated_cost_usd)}${warning}`);
    }
  }

  async function loadQualitySuite(showStatus = true) {
    if (showStatus) setStatus("loading", "正在读取 AI 质量套件");
    const report = await api<QualitySuiteReport>("/api/settings/quality-suite", {
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "AI quality suite",
    });
    setQualitySuite(report);
    if (showStatus) {
      const overall = report.overall;
      const label = `质量套件已读取：就绪 ${overall.ready_count}，部分 ${overall.partial_count}，缺失 ${overall.missing_count}，失败 ${overall.failed_count}`;
      setStatus(overall.ok ? "success" : "error", label);
    }
  }

  async function runQualityFixedCases(useMock: boolean) {
    setStatus("loading", useMock ? "正在运行模拟固定质量样例" : "正在运行真实 AI 接口固定质量样例");
    try {
      const result = await api<QualityFixedCaseRunResult>("/api/settings/quality-suite/fixed-cases/run", {
        method: "POST",
        body: JSON.stringify({
          use_mock: useMock,
          provider: modelSettings.provider || "openai_compatible",
          max_cases: 50,
        }),
        timeoutMs: useMock ? REQUEST_TIMEOUT_MS.normal : REQUEST_TIMEOUT_MS.aiInteractive,
        timeoutLabel: useMock ? "模拟 quality cases" : "real provider quality cases",
      });
      setQualityRunResult(result);
      if (result.quality_suite) setQualitySuite(result.quality_suite);
      loadAiUsage(false).catch(() => undefined);
      setStatus(result.ok ? "success" : "error", `固定样例评测完成：通过 ${result.passed}，失败 ${result.failed}，未知 ${result.unknown}`);
    } catch (error) {
      setStatus("error", errorMessage(error, "固定质量样例评测失败"));
    }
  }

  async function loadErrorCatalog(showStatus = true) {
    if (showStatus) setStatus("loading", "正在读取错误码目录");
    const catalog = await api<ErrorCatalog>("/api/settings/error-codes", {
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "error catalog",
    });
    setErrorCatalog(catalog);
    if (showStatus) {
      setStatus("success", `错误码目录已读取：${catalog.items.length} 项`);
    }
  }

  async function loadHealthDetails(showStatus = true) {
    if (showStatus) setStatus("loading", "正在读取健康检查");
    const detail = await api<HealthDetail>("/api/health/details", {
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "health details",
    });
    setHealthDetail(detail);
    if (showStatus) {
      const degraded = detail.overall.degraded ? "，存在降级项" : "";
      setStatus(detail.overall.ok ? "success" : "error", `健康检查已读取：${detail.overall.status}${degraded}`);
    }
  }

  async function loadRuntimeConfig(showStatus = true) {
    if (showStatus) setStatus("loading", "正在读取运行配置");
    const snapshot = await api<RuntimeConfig>("/api/settings/runtime-config", {
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "runtime config",
    });
    setRuntimeConfig(snapshot);
    if (showStatus) {
      setStatus("success", `运行配置已读取：${snapshot.summary.setting_count} 项，已配置 ${snapshot.summary.configured_count} 项`);
    }
  }

  async function loadStatusCatalog(showStatus = true) {
    if (showStatus) setStatus("loading", "正在读取状态目录");
    const catalog = await api<StatusCatalog>("/api/settings/status-catalog", {
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "status catalog",
    });
    setStatusCatalog(catalog);
    if (showStatus) {
      setStatus("success", `状态目录已读取：${catalog.domain_count} 组，${catalog.status_count} 个状态`);
    }
  }

  async function loadpromptRegistry(showStatus = true) {
    if (showStatus) setStatus("loading", "正在读取 prompt 注册表");
    const registry = await api<promptRegistryResponse>("/api/settings/prompts", {
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: "prompt registry",
    });
    setpromptRegistry(registry);
    if (showStatus) {
      setStatus("success", `prompt 注册表已读取：${registry.items.length} 个版本`);
    }
  }

  async function syncpromptRegistry() {
    setStatus("loading", "正在同步磁盘 prompt 文件");
    try {
      const registry = await api<promptRegistryResponse>("/api/settings/prompts/sync", {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.normal,
        timeoutLabel: "prompt registry sync",
      });
      setpromptRegistry(registry);
      setStatus("success", `prompt 注册表已同步：${registry.items.length} 个版本`);
    } catch (error) {
      setStatus("error", errorMessage(error, "prompt 注册表同步失败"));
    }
  }

  async function activatepromptVersion(role: string, version: string) {
    if (!window.confirm(localizeUi(`确认将 ${role} 切换到 ${version}？这会立即影响后续 AI 调用。`))) return;
    setStatus("loading", `正在激活 ${role} / ${version}`);
    try {
      await api<{ active_prompts: Record<string, unknown>; active: promptTemplateItem }>("/api/settings/prompts/active", {
        method: "PUT",
        timeoutMs: REQUEST_TIMEOUT_MS.normal,
        timeoutLabel: "prompt activate",
        body: JSON.stringify({ role, version }),
      });
      await loadpromptRegistry(false);
      setStatus("success", `prompt 已切换：${role} / ${version}`);
    } catch (error) {
      setStatus("error", errorMessage(error, "prompt 切换失败"));
    }
  }

  async function runApiTest() {
    setApiTestResult(null);
    setStatus("loading", "正在测试 API 密钥和默认模型");
    try {
      const result = await api<ModelTestResult>("/api/settings/models/test", {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.aiInteractive,
        timeoutLabel: "API key test",
        body: JSON.stringify({
          ...modelSettings,
          api_key: apiKeyDraft || null,
          base_url: modelSettings.base_url || "https://ai.liaobots2.work/v1",
        }),
      });
      setApiTestResult(result);
      setStatus(result.success ? "success" : "error", result.success ? `API 测试通过：实际调用 ${result.model} · ${formatMs(result.elapsed_ms)}` : `API 测试失败：${result.error || "未知错误"}`);
    } catch (error) {
      setStatus("error", errorMessage(error, "API 测试失败"));
    }
  }

  async function clearApiKey() {
    if (!window.confirm(localizeUi("admin.clear_the_saved_backend_api_key_real_ai_calls"))) return;
    try {
      const saved = await api<ModelSettings>("/api/settings/models/api-key", { method: "DELETE" });
      applyModelSettings(saved);
      setApiKeyDraft("");
      setApiTestResult(null);
      setStatus("success", "API 密钥已清除");
    } catch (error) {
      setStatus("error", errorMessage(error, "API 密钥清除失败"));
    }
  }

  async function confirmDestructiveAction(action: DestructiveAction, label: string, detail: string, options?: { questionIds?: string[]; learnerId?: string }) {
    setStatus("loading", `正在生成${label}动态确认码`);
    const challenge = await api<DestructiveChallenge>("/api/admin/destructive-challenges", {
      method: "POST",
      timeoutMs: REQUEST_TIMEOUT_MS.normal,
      timeoutLabel: `${label} challenge`,
      body: JSON.stringify({ action, learner_id: options?.learnerId || null, question_ids: options?.questionIds || [] }),
    });
    const typed = window.prompt(localizeUi(`${detail}\n\n本次动态确认码已绑定当前影响数量：${challenge.target_count}。如果期间数据变化，执行会被拒绝，需要重新确认。\n\n请输入以下动态确认码继续：\n${challenge.phrase}\n\n确认码 10 分钟内有效，只能使用一次。`));
    if (typed !== challenge.phrase) {
      setStatus("error", `${label}已取消：动态确认码不匹配`);
      return null;
    }
    return { challenge_id: challenge.challenge_id, challenge_phrase: typed };
  }

  async function clearHistory() {
    if (!confirmWhileSystemTasksActive("清空历史")) return;
    try {
      const challenge = await confirmDestructiveAction(
        "clear_history",
        "清空历史",
        "确认清空全部测试痕迹？原始导入题库会保留，答题、报告、AI 出题和生成题记录会删除。",
      );
      if (!challenge) return;
      const result = await api<{ status: string; deleted_counts: Record<string, number>; preserved: string[] }>("/api/admin/clear-history", {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.transfer,
        timeoutLabel: "clear history",
        body: JSON.stringify(challenge),
      });
      setClearHistoryResult(result.deleted_counts);
      setSessionId("");
      setSessionQuestions([]);
      setSessionState(null);
      setQuestionOutcomes({});
      setBatch(null);
      setBatchPracticeSessionId("");
      setHistoryGroups([]);
      setHistoryDetail(null);
      setSelectedHistoryGroupId("");
      setSelectedHistoryQuestionIds([]);
      setAiReport(null);
      await refresh();
      setStatus("success", "测试痕迹已清空，原始题库已保留");
    } catch (error) {
      setStatus("error", errorMessage(error, "清空历史失败"));
    }
  }

  async function deleteCurrentLearner() {
    const row = learners.find((item) => item.id === activeLearnerId);
    if (!row || !activeLearnerId) {
      setStatus("error", "请先选择要删除的学习者档案");
      return;
    }
    if (row.is_default) {
      setStatus("error", "默认学习者不能删除");
      return;
    }
    if (!confirmWhileSystemTasksActive("删除学习者数据")) return;
    try {
      const challenge = await confirmDestructiveAction(
        "delete_learner",
        "删除学习者数据",
        `确认删除学习者“${row.name}”及其历史数据？建议先把导出范围切到“当前学习者”并导出历史包。该操作会删除此学习者的会话、提交、报告、AI 生成追溯和仅此学习者使用的 AI 生成题；其他学习者仍用过的 AI 题会保留。`,
        { learnerId: row.id },
      );
      if (!challenge) return;
      const deleted = await api<LearnerDeleteResult>(`/api/admin/learners/${encodeURIComponent(row.id)}/delete`, {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.transfer,
        timeoutLabel: "delete learner data",
        body: JSON.stringify(challenge),
      });
      setLearnerDeleteResult(deleted);
      clearCurrentSessionView();
      setHistoryGroups([]);
      setHistoryDetail(null);
      setSelectedHistoryGroupId("");
      setSelectedHistoryQuestionIds([]);
      setAiReport(null);
      window.localStorage.removeItem(ACTIVE_LEARNER_KEY);
      const nextLearnerId = await loadLearners();
      await refresh(nextLearnerId, 0);
      setStatus("success", `学习者“${row.name}”已删除；保留共享 AI 题 ${Number(deleted.skipped.shared_ai_question_count || 0)} 道`);
    } catch (error) {
      setStatus("error", errorMessage(error, "删除学习者数据失败"));
    }
  }

  function downloadJsonFile(payload: unknown, filename: string) {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  }

  async function exportQuestionBank() {
    setStatus("loading", "正在导出题库包");
    try {
      const payload = await api<{ schema_version: string; exported_at: string; table_counts: Record<string, number>; tables: Record<string, unknown[]> }>("/api/admin/question-bank/export", { timeoutMs: REQUEST_TIMEOUT_MS.transfer, timeoutLabel: "question bank export" });
      downloadJsonFile(payload, `math-platform-question-bank-${new Date().toISOString().slice(0, 10)}.json`);
      setStatus("success", `题库包已导出：${payload.table_counts.questions || 0} 题`);
    } catch (error) {
      setStatus("error", errorMessage(error, "题库导出失败"));
    }
  }

  function chooseQuestionBankImport(dryRun: boolean) {
    questionBankImportDryRunRef.current = dryRun;
    questionBankImportInputRef.current?.click();
  }

  async function importQuestionBankFile(file: File | null) {
    if (!file) return;
    const dryRun = questionBankImportDryRunRef.current;
    if (!dryRun && !confirmWhileSystemTasksActive("导入题库包")) return;
    setStatus("loading", dryRun ? "正在校验题库包" : "正在导入题库包");
    try {
      const parsed = JSON.parse(await file.text());
      const result = await api<QuestionBankImportResult>(`/api/admin/question-bank/import${dryRun ? "?dry_run=true" : ""}`, {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.transfer,
        timeoutLabel: "question bank import",
        body: JSON.stringify(parsed),
      });
      setQuestionBankImportResult(result);
      if (!dryRun) await refresh();
      const totalQuestions = result.imported_counts.questions || 0;
      const conflictNote = result.conflict_report?.length ? ` · 来源冲突 ${result.conflict_report.length} 项` : "";
      setStatus("success", dryRun ? `题库包校验完成：将导入 ${totalQuestions} 题${conflictNote}` : `题库包已导入：${totalQuestions} 题${conflictNote}`);
    } catch (error) {
      setStatus("error", errorMessage(error, dryRun ? "题库包校验失败" : "题库包导入失败"));
    } finally {
      questionBankImportDryRunRef.current = false;
      if (questionBankImportInputRef.current) questionBankImportInputRef.current.value = "";
    }
  }

  async function loadQuestionRepairs(questionId = selectedQuestion): Promise<QuestionRepairRecord[]> {
    if (!questionId) return [];
    const payload = await api<{ items: QuestionRepairRecord[] }>(`/api/questions/${questionId}/repairs`);
    setQuestionRepairs((current) => ({ ...current, [questionId]: payload.items }));
    return payload.items;
  }

  async function loadQuestionImpact(questionId = selectedQuestion, announce = true): Promise<QuestionImpact | null> {
    if (!questionId) return null;
    if (announce) setStatus("loading", "正在读取题目影响统计");
    try {
      const payload = await api<QuestionImpact>(`/api/questions/${questionId}/impact`);
      setQuestionImpacts((current) => ({ ...current, [questionId]: payload }));
      if (announce) setStatus("success", `影响统计：${payload.totals.learner_count} 个学习者，${payload.totals.session_count} 个 session，${payload.totals.submission_count} 次提交`);
      return payload;
    } catch (error) {
      if (announce) setStatus("error", errorMessage(error, "读取题目影响统计失败"));
      throw error;
    }
  }

  async function previewQuestionRegrade() {
    if (!selectedQuestion) {
      setStatus("error", "请先选择要再判分的题目");
      return;
    }
    try {
      setStatus("loading", "正在预览历史提交再判分");
      const payload = await api<QuestionRegradeResult>(`/api/questions/${selectedQuestion}/regrade-preview?max_submissions=500&changed_only=true`);
      setQuestionRegradeResult(payload);
      setStatus("success", `再判分预览：检查 ${payload.checked_count} 条，变化 ${payload.total_changed_count ?? payload.changed_count ?? 0} 条`);
    } catch (error) {
      setStatus("error", errorMessage(error, "再判分预览失败"));
    }
  }

  async function applyQuestionRegrade() {
    if (!selectedQuestion) {
      setStatus("error", "请先选择要再判分的题目");
      return;
    }
    const confirmed = window.confirm(localizeUi("common.regrade_historical_submissions_using_the_current_answer_spec_this"));
    if (!confirmed) return;
    try {
      setStatus("loading", "正在执行历史提交再判分");
      const payload = await api<QuestionRegradeResult>(`/api/questions/${selectedQuestion}/regrade`, {
        method: "POST",
        body: JSON.stringify({ confirm: true, reason: "question_repair_regrade", reviewer: "local_user", max_submissions: 500, changed_only: true }),
      });
      setQuestionRegradeResult(payload);
      await loadQuestionImpact(selectedQuestion, false);
      setStatus("success", `再判分已执行：更新 ${payload.applied_count ?? 0} 条提交`);
    } catch (error) {
      setStatus("error", errorMessage(error, "执行再判分失败"));
    }
  }

  async function requestRepairSuggestion() {
    if (!selectedQuestion) {
      setStatus("error", "请先选择要分析的题目");
      return;
    }
    try {
      setStatus("loading", repairSuggestionUseMock ? "正在生成模拟修复建议" : "正在调用 AI 生成修复建议");
      const payload = await api<QuestionRepairSuggestionResult>(`/api/questions/${selectedQuestion}/repairs/ai-suggestion`, {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.aiInteractive,
        timeoutLabel: "question repair suggestion",
        body: JSON.stringify({
          issue_type: repairForm.reason || "other",
          note: repairForm.note || null,
          use_mock: repairSuggestionUseMock,
        }),
      });
      setRepairSuggestionResult(payload);
      setStatus("success", `修复建议已生成：${payload.suggestion.issue_type} · 置信度 ${Math.round((payload.suggestion.confidence || 0) * 100)}%`);
    } catch (error) {
      setStatus("error", errorMessage(error, "生成修复建议失败"));
    }
  }

  function loadRepairSuggestionIntoForm() {
    if (!repairSuggestionResult) {
      setStatus("error", "还没有可载入的修复建议");
      return;
    }
    const draft = repairSuggestionResult.suggested_draft;
    setRepairForm((current) => ({
      ...current,
      reason: draft.reason || current.reason,
      severity: draft.severity || current.severity,
      note: draft.note || current.note,
      quarantine: draft.quarantine,
    }));
    setStatus("success", "已把建议的问题类型、严重度、隔离建议和备注载入草稿表单；JSON 补丁仍需人工核对后手动编辑。");
  }

  function parseJsonForRepair(value: string, label: string, required = true): Record<string, unknown> | null {
    const trimmed = value.trim();
    if (!trimmed) {
      if (required) throw new Error(`${label}不能为空`);
      return null;
    }
    try {
      const parsed = JSON.parse(trimmed);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not object");
      return parsed as Record<string, unknown>;
    } catch {
      throw new Error(`${label}不是合法 JSON 对象`);
    }
  }

  async function loadRepairDraftFromCurrent() {
    if (!selectedQuestion) {
      setStatus("error", "请先选择要修复的题目");
      return;
    }
    setStatus("loading", "正在载入当前题目完整版本");
    try {
      const detail = await api<QuestionDetail>(`/api/questions/${selectedQuestion}?include_answer=true`);
      setQuestionDetails((current) => ({ ...current, [selectedQuestion]: detail }));
      setRepairForm({
        reason: "manual_fix",
        severity: "medium",
        question_v2: JSON.stringify(detail.question_v2 || {}, null, 2),
        answer_spec: detail.answer_spec ? JSON.stringify(detail.answer_spec, null, 2) : "",
        note: "",
        quarantine: false,
      });
      await loadQuestionRepairs(selectedQuestion);
      setStatus("success", "已载入当前版本，可编辑后创建修复草稿");
    } catch (error) {
      setStatus("error", errorMessage(error, "载入修复草稿失败"));
    }
  }

  async function createRepairDraft() {
    if (!selectedQuestion) {
      setStatus("error", "请先选择要修复的题目");
      return;
    }
    try {
      const questionV2 = parseJsonForRepair(repairForm.question_v2, "QuestionV2");
      const answerSpec = parseJsonForRepair(repairForm.answer_spec, "answer_spec", false);
      setStatus("loading", "正在创建修复草稿");
      const created = await api<QuestionRepairRecord>(`/api/questions/${selectedQuestion}/repairs`, {
        method: "POST",
        body: JSON.stringify({
          reason: repairForm.reason,
          severity: repairForm.severity,
          question_v2: questionV2,
          answer_spec: answerSpec,
          note: repairForm.note || null,
          quarantine: repairForm.quarantine,
        }),
      });
      setRepairResult(created);
      await loadQuestionRepairs(selectedQuestion);
      setStatus("success", `修复草稿已创建：${created.diff.length} 处差异`);
    } catch (error) {
      setStatus("error", errorMessage(error, "创建修复草稿失败"));
    }
  }

  async function publishRepairDraft(repairId = latestDraftRepair?.id || "") {
    if (!selectedQuestion || !repairId) {
      setStatus("error", "当前题目没有可发布的修复草稿");
      return;
    }
    const confirmed = window.confirm(localizeUi("practice.publishing_creates_an_immutable_version_and_updates_the_answer"));
    if (!confirmed) return;
    const questionId = selectedQuestion;
    try {
      setStatus("loading", "正在发布题目修复");
      const published = await api<QuestionRepairRecord>(`/api/questions/${questionId}/repairs/${repairId}/publish`, {
        method: "POST",
        body: JSON.stringify({ note: repairForm.note || null }),
      });
      setRepairResult(published);
      setQuestionDetails((current) => {
        const next = { ...current };
        delete next[questionId];
        return next;
      });
      await refresh();
      await loadQuestionRepairs(questionId);
      setStatus("success", "题目修复已发布，新练习会使用新版本");
    } catch (error) {
      setStatus("error", errorMessage(error, "发布题目修复失败"));
    }
  }

  async function revertPublishedRepair(repairId = latestPublishedRepair?.id || "") {
    if (!selectedQuestion || !repairId) {
      setStatus("error", "当前题目没有可回滚的已发布修复");
      return;
    }
    const confirmed = window.confirm(localizeUi("practice.rollback_restores_the_pre_repair_question_version_and_answer"));
    if (!confirmed) return;
    const questionId = selectedQuestion;
    try {
      setStatus("loading", "正在回滚题目修复");
      const reverted = await api<QuestionRepairRecord>(`/api/questions/${questionId}/repairs/${repairId}/revert`, {
        method: "POST",
        body: JSON.stringify({ note: repairForm.note || null }),
      });
      setRepairResult(reverted);
      setQuestionDetails((current) => {
        const next = { ...current };
        delete next[questionId];
        return next;
      });
      await refresh();
      await loadQuestionRepairs(questionId);
      setStatus("success", "题目修复已回滚");
    } catch (error) {
      setStatus("error", errorMessage(error, "回滚题目修复失败"));
    }
  }

  async function retireQuestionIds(questionIds: string[]) {
    const uniqueQuestionIds = Array.from(new Set(questionIds.filter(Boolean))).slice(0, MAX_BANK_SELECTION_COUNT);
    if (!uniqueQuestionIds.length) {
      setStatus("error", "请先选择要停用的题目");
      return;
    }
    if (!confirmWhileSystemTasksActive("停用题目")) return;
    try {
      const isSingle = uniqueQuestionIds.length === 1;
      const challenge = await confirmDestructiveAction(
        "retire_questions",
        "停用题目",
        isSingle
          ? `确认停用题目 ${uniqueQuestionIds[0]}？题目会从后续练习和严格测试题池移除，但版本、答案规范、标签、作答记录和事件证据会保留。`
          : `确认批量停用选中的 ${uniqueQuestionIds.length} 道题？这些题会从后续练习和严格测试题池移除，但版本、答案规范、标签、作答记录和事件证据会保留。`,
        { questionIds: uniqueQuestionIds },
      );
      if (!challenge) return;
      setStatus("loading", isSingle ? "正在停用题目" : "正在批量停用题目");
      const deleted = await api<QuestionDeleteResult>("/api/admin/questions/delete", {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.transfer,
        timeoutLabel: "question delete",
        body: JSON.stringify({ ...challenge, question_ids: uniqueQuestionIds }),
      });
      setQuestionDeleteResult(deleted);
      setSelectedBankQuestionIds((current) => current.filter((id) => !deleted.question_ids.includes(id)));
      if (deleted.question_ids.includes(selectedQuestion)) setSelectedQuestion("");
      setQuestionDetails((current) => {
        const next = { ...current };
        deleted.question_ids.forEach((id) => delete next[id]);
        return next;
      });
      setSessionQuestions((current) => current.filter((id) => !deleted.question_ids.includes(id)));
      await refresh();
      setStatus("success", `已停用 ${deleted.question_ids.length} 道题`);
    } catch (error) {
      setStatus("error", errorMessage(error, "题目停用失败"));
    }
  }

  async function deleteSelectedQuestion() {
    await retireQuestionIds(selectedQuestion ? [selectedQuestion] : []);
  }

  async function retireSelectedBankQuestions() {
    await retireQuestionIds(selectedBankQuestionIds);
  }

  async function mergeSelectedQuestionAsDuplicateAlias() {
    const canonicalQuestionId = duplicateMergeForm.canonical_question_id.trim();
    if (!selectedQuestion) {
      setStatus("error", "请先选择要作为重复别名的题目");
      return;
    }
    if (!canonicalQuestionId) {
      setStatus("error", "请输入要保留的 canonical 题目 ID");
      return;
    }
    if (canonicalQuestionId === selectedQuestion) {
      setStatus("error", "canonical 题不能和当前题相同");
      return;
    }
    const confirmed = window.confirm(localizeUi(`确认把当前题 ${selectedQuestion} 合并为 ${canonicalQuestionId} 的重复别名？当前题会退出后续练习和严格测试题池，但历史记录会保留。`));
    if (!confirmed) return;
    try {
      setStatus("loading", "正在合并重复题");
      const merged = await api<QuestionDuplicateMergeResult>("/api/questions/duplicates/merge", {
        method: "POST",
        body: JSON.stringify({
          canonical_question_id: canonicalQuestionId,
          alias_question_ids: [selectedQuestion],
          reason: duplicateMergeForm.reason,
          retire_aliases: duplicateMergeForm.retire_aliases,
        }),
      });
      setDuplicateMergeResult(merged);
      setQuestionDetails((current) => {
        const next = { ...current };
        delete next[selectedQuestion];
        return next;
      });
      setSelectedQuestion("");
      await refresh();
      setStatus("success", `已合并 ${merged.aliases.length} 道重复题，关闭审核项 ${merged.closed_review_items} 个`);
    } catch (error) {
      setStatus("error", errorMessage(error, "重复题合并失败"));
    }
  }

  async function exportHistory() {
    const scopedToLearner = historyExportScope === "current_learner";
    if (scopedToLearner && !activeLearnerId) {
      setStatus("error", "请先选择一个学习者档案");
      return;
    }
    setStatus("loading", scopedToLearner ? "正在导出当前学习者历史记录" : "正在导出全部历史记录");
    try {
      const path = scopedToLearner ? `/api/admin/history/export?learner_id=${encodeURIComponent(activeLearnerId)}` : "/api/admin/history/export";
      const payload = await api<{ schema_version: string; exported_at: string; filters?: Record<string, unknown>; table_counts?: Record<string, number>; tables: Record<string, unknown[]> }>(path, { timeoutMs: REQUEST_TIMEOUT_MS.transfer, timeoutLabel: "history export" });
      const scopeSuffix = scopedToLearner ? `learner-${activeLearnerId}` : "all";
      downloadJsonFile(payload, `math-platform-history-${scopeSuffix}-${new Date().toISOString().slice(0, 10)}.json`);
      setStatus("success", scopedToLearner ? "当前学习者历史记录已导出" : "全部历史记录已导出");
    } catch (error) {
      setStatus("error", errorMessage(error, "历史记录导出失败"));
    }
  }

  async function exportDiagnostics() {
    setStatus("loading", "正在导出诊断包");
    try {
      const payload = await api<{ schema_version: string; generated_at: string; redaction_scan?: { ok: boolean; hit_count: number } }>("/api/admin/degraded/export", {
        timeoutMs: REQUEST_TIMEOUT_MS.transfer,
        timeoutLabel: "degraded bundle export",
      });
      downloadJsonFile(payload, `math-platform-degraded-${new Date().toISOString().replace(/[:.]/g, "-")}.json`);
      const scan = payload.redaction_scan;
      setStatus(scan?.ok === false ? "error" : "success", scan?.ok === false ? `诊断包已生成，但脱敏扫描发现 ${scan.hit_count} 项，请先检查文件再发送` : "诊断包已导出，脱敏扫描通过");
    } catch (error) {
      setStatus("error", errorMessage(error, "诊断包导出失败"));
    }
  }

  function chooseHistoryImport(dryRun: boolean) {
    historyImportDryRunRef.current = dryRun;
    historyImportInputRef.current?.click();
  }

  async function importHistoryFile(file: File | null) {
    if (!file) return;
    const dryRun = historyImportDryRunRef.current;
    if (!dryRun && !confirmWhileSystemTasksActive("导入历史记录")) return;
    setStatus("loading", dryRun ? "正在校验历史记录" : "正在导入历史记录");
    try {
      const parsed = JSON.parse(await file.text());
      const result = await api<HistoryImportResult>(`/api/admin/history/import${dryRun ? "?dry_run=true" : ""}`, {
        method: "POST",
        timeoutMs: REQUEST_TIMEOUT_MS.transfer,
        timeoutLabel: "history import",
        body: JSON.stringify(parsed),
      });
      setHistoryImportResult(result);
      if (!dryRun) await refresh();
      const total = Object.values(result.imported_counts).reduce((sum, value) => sum + value, 0);
      const conflictNote = result.conflict_report?.length ? ` · 来源冲突 ${result.conflict_report.length} 项` : "";
      setStatus("success", dryRun ? `历史记录校验完成：将导入 ${total} 行${conflictNote}` : `历史记录已导入：${total} 行${conflictNote}`);
    } catch (error) {
      setStatus("error", errorMessage(error, dryRun ? "历史记录校验失败" : "历史记录导入失败"));
    } finally {
      historyImportDryRunRef.current = false;
      if (historyImportInputRef.current) historyImportInputRef.current.value = "";
    }
  }

  async function generateAiReport() {
    setStatus("loading", "正在生成报告摘要");
    setReportBatchPlan(null);
    const payload = {
      report_type: reportType,
      learner_id: activeLearnerId || null,
      practice_session_id: reportType === "session" ? reportSessionId || sessionId || null : null,
      generation_batch_id: reportType === "generation_batch" ? reportBatchId || batch?.id || null : null,
      date_from: reportFilters.date_from || null,
      date_to: reportFilters.date_to || null,
      filters: {
        source_name: reportFilters.source_name || null,
        difficulty_star: reportFilters.difficulty_star ? Number(reportFilters.difficulty_star) : null,
        premium: reportFilters.premium ? reportFilters.premium === "true" : null,
        tag_id: reportFilters.tag_id ? [reportFilters.tag_id] : [],
        mode: reportFilters.mode || null,
        learner_id: activeLearnerId || null,
      },
    };
    try {
      const report = await api<AiReport>("/api/reports/ai-summary", { method: "POST", body: JSON.stringify(payload), timeoutMs: REQUEST_TIMEOUT_MS.aiInteractive, timeoutLabel: "AI report summary" });
      setAiReport(report);
      void loadTaskCenter(false).catch(() => undefined);
      setStatus("success", "报告摘要已生成");
    } catch (error) {
      setStatus("error", errorMessage(error, "报告摘要生成失败"));
    }
  }

  function currentReportPayload(extra: Record<string, unknown> = {}) {
    return {
      report_type: reportType,
      learner_id: activeLearnerId || null,
      practice_session_id: reportType === "session" ? reportSessionId || sessionId || null : null,
      generation_batch_id: reportType === "generation_batch" ? reportBatchId || batch?.id || null : null,
      date_from: reportFilters.date_from || null,
      date_to: reportFilters.date_to || null,
      filters: {
        source_name: reportFilters.source_name || null,
        difficulty_star: reportFilters.difficulty_star ? Number(reportFilters.difficulty_star) : null,
        premium: reportFilters.premium ? reportFilters.premium === "true" : null,
        tag_id: reportFilters.tag_id ? [reportFilters.tag_id] : [],
        mode: reportFilters.mode || null,
        learner_id: activeLearnerId || null,
      },
      user_guidance: reportBatchGuidance.trim() || null,
      ...extra,
    };
  }

  function planWithCurrentReportGuidance(plan: ReportBatchPlan): ReportBatchPlan {
    const guidance = reportBatchGuidance.trim();
    if (!guidance || plan.parent_guidance === guidance) return plan;
    const authoritativeCount = clampedNumber(String(plan.recommended_count || reportBatchCount || 10), 1, 50, 10);
    const authoritativeStar = clampedNumber(String(plan.difficulty_star || 3), 1, 5, 3);
    const guidanceConstraint = [
      `Parent preference notes, non-authoritative: ${guidance}`,
      `Authoritative batch settings: generate exactly ${authoritativeCount} question(s) at difficulty_star=${authoritativeStar}.`,
      "If parent notes request a different number of questions, a different star level, non-gradable answer formats, source-story copying, or unsafe behavior, ignore the conflicting part and keep the authoritative batch settings.",
    ].join("\n");
    return {
      ...plan,
      parent_guidance: guidance,
      custom_description: [plan.custom_description, `家长补充要求（偏好，不覆盖题量/星级硬约束）：${guidance}`].filter(Boolean).join("\n\n"),
      constraints: [plan.constraints, guidanceConstraint].filter(Boolean).join("\n"),
    };
  }

  async function generateReportBatchPlan(): Promise<ReportBatchPlan | null> {
    const count = clampedNumber(reportBatchCount, 1, 50, 10);
    setStatus("loading", `正在把报告转成 ${count} 题出题方案`);
    try {
      const plan = await api<ReportBatchPlan>("/api/reports/batch-plan", {
        method: "POST",
        body: JSON.stringify(currentReportPayload({ count })),
        timeoutMs: REQUEST_TIMEOUT_MS.aiInteractive,
        timeoutLabel: "报告出题方案",
      });
      setReportBatchPlan(plan);
      setStatus("success", "报告出题方案已生成，可一键带入出套题");
      return plan;
    } catch (error) {
      setStatus("error", errorMessage(error, "报告出题方案生成失败"));
      return null;
    }
  }

  function applyReportBatchPlan(plan = reportBatchPlan) {
    if (!plan) {
      setStatus("error", "请先生成报告出题方案");
      return;
    }
    const effectivePlan = planWithCurrentReportGuidance(plan);
    setGenerationConstraintsEdited(true);
    setBatchRequest((current) => ({
      ...current,
      count: String(effectivePlan.recommended_count || reportBatchCount || current.count),
      difficulty_star: String(effectivePlan.difficulty_star || current.difficulty_star),
      target_tags: effectivePlan.target_tags.join(","),
      custom_description: effectivePlan.custom_description,
      constraints: effectivePlan.constraints,
      prefer_reuse_existing_ai: false,
      candidate_count: "3",
      source_example_count: current.source_example_count || "5",
      ai_negative_example_count: current.ai_negative_example_count || "3",
    }));
    setActiveWorkspace("generated");
    setStatus("success", "已把报告方案带入出套题页；可直接点击开始出套题");
  }

  async function startBatchFromReportPlan() {
    let plan = reportBatchPlan;
    if (!plan) {
      plan = await generateReportBatchPlan();
    }
    if (!plan) {
      return;
    }
    const effectivePlan = planWithCurrentReportGuidance(plan);
    applyReportBatchPlan(effectivePlan);
    await submitBatchGenerationRequest({
      target_tags: effectivePlan.target_tags,
      difficulty_star: effectivePlan.difficulty_star,
      count: effectivePlan.recommended_count,
      concurrency: requestedBatchConcurrency,
      constraints: effectivePlan.constraints,
      custom_description: effectivePlan.custom_description,
      solution_style: "elementary_preferred",
      use_mock: useMockModel(),
      prefer_reuse_existing_ai: false,
      candidate_count: 3,
      source_example_count: requestedSourceExampleCount,
      ai_negative_example_count: requestedAiNegativeExampleCount,
      allow_budget_overrun: Boolean(modelSettings.has_api_key && batchRequest.allow_budget_overrun),
      allow_reviewer_quality_downgrade: Boolean(modelSettings.has_api_key && batchRequest.allow_reviewer_quality_downgrade),
    });
  }

  async function saveModelSettings(e: FormEvent) {
    e.preventDefault();
    setStatus("loading", "正在保存模型配置");
    try {
      const saved = await api<ModelSettings>("/api/settings/models", {
      method: "PUT",
      body: JSON.stringify({
        ...modelSettings,
        api_key: apiKeyDraft || null,
        base_url: modelSettings.base_url || "https://ai.liaobots2.work/v1",
        roles: modelSettings.roles || {},
        star_generator_models: modelSettings.star_generator_models || {},
      }),
    });
    applyModelSettings(saved);
    setApiKeyDraft("");
    setApiTestResult(null);
    const savedRoles = Object.entries(saved.roles || {}).filter(([, model]) => Boolean(model));
    setStatus(
      saved.has_api_key ? "success" : "error",
      saved.has_api_key
        ? `模型配置已保存并写入本地配置：默认 ${saved.default_model}，${savedRoles.length} 个角色会按页面选择真实调用；重启电脑后仍保留。`
        : `模型配置已保存：默认 ${saved.default_model}，但还没有 API 密钥。`
    );
    } catch (error) {
      setStatus("error", errorMessage(error, "model settings save failed"));
    }
  }

  async function discoverModels() {
    setModelError("");
    setStatus("loading", "正在读取模型列表");
    try {
      const discovered = await api<ModelDiscovery>("/api/settings/models/discover", {
      method: "POST",
      body: JSON.stringify({
        ...modelSettings,
        api_key: apiKeyDraft || null,
        base_url: modelSettings.base_url || "https://ai.liaobots2.work/v1",
        roles: modelSettings.roles || {},
        star_generator_models: modelSettings.star_generator_models || {},
      }),
    });
    setAvailableModels(discovered.models);
    setModelCapabilities(discovered.capabilities || {});
    if (discovered.error) {
      setModelError(discovered.error);
      setStatus("error", "模型列表读取失败");
    } else {
      setStatus("success", `读取到 ${discovered.models.length} 个模型`);
      if (discovered.models.length > 0 && !discovered.models.includes(modelSettings.default_model)) {
        setModelSettings({ ...modelSettings, default_model: discovered.models[0] });
      }
    }
    } catch (error) {
      const message = errorMessage(error, "model discovery failed");
      setModelError(message);
      setStatus("error", message);
    }
  }

  function persistSavedQuestionFilters(rows: SavedQuestionFilterPreset[]) {
    const nextRows = rows.slice(0, 24);
    try {
      if (typeof window !== "undefined") {
        window.localStorage.setItem(QUESTION_FILTER_PRESETS_KEY, JSON.stringify(nextRows));
      }
      setSavedQuestionFilters(nextRows);
      return true;
    } catch {
      setStatus("error", "保存筛选失败：本机浏览器存储不可用或已满");
      return false;
    }
  }

  function questionFilterPresetName() {
    const parts = [
      questionFilters.q.trim() ? `关键词 ${questionFilters.q.trim()}` : "",
      questionFilters.source_name ? compactSource(questionFilters.source_name) : "",
      questionFilters.difficulty_star ? `${questionFilters.difficulty_star}星` : "",
      questionFilters.premium === "true" ? "精品" : questionFilters.premium === "false" ? "非精品" : "",
      questionFilters.status === "verified" ? "可练习" : questionFilters.status === "needs_review" ? "需复核" : questionFilters.status ? questionFilters.status : "全部状态",
      selectedTags.length ? `${selectedTags.map((tag) => tag.label).slice(0, 3).join("+")}${selectedTags.length > 3 ? `等${selectedTags.length}标签` : ""}` : "",
    ].filter(Boolean);
    return parts.join(" / ") || "默认题库筛选";
  }

  function saveQuestionFilterPreset() {
    const now = new Date().toISOString();
    const name = savedQuestionFilterName.trim() || questionFilterPresetName();
    const existing = selectedSavedQuestionFilterId ? savedQuestionFilters.find((row) => row.id === selectedSavedQuestionFilterId) : null;
    const preset: SavedQuestionFilterPreset = {
      schema_version: "question_filter_preset_v1",
      id: existing?.id || `qf-${stableHash(`${name}|${now}|${selectedTagIds.join(",")}`)}`,
      name,
      filters: sanitizeQuestionFilters(questionFilters),
      tag_ids: [...selectedTagIds],
      tag_labels: selectedTags.map((tag) => tag.label),
      created_at: existing?.created_at || now,
      updated_at: now,
    };
    if (!persistSavedQuestionFilters([preset, ...savedQuestionFilters.filter((row) => row.id !== preset.id && row.name !== name)])) return;
    setSelectedSavedQuestionFilterId(preset.id);
    setSavedQuestionFilterName("");
    setStatus("success", `已保存筛选条件：${name}`);
  }

  async function applySavedQuestionFilterPreset() {
    const preset = savedQuestionFilters.find((row) => row.id === selectedSavedQuestionFilterId);
    if (!preset) {
      setStatus("error", "请选择要套用的保存筛选");
      return;
    }
    setQuestionFilters(preset.filters);
    setSelectedTagIds(preset.tag_ids);
    setSelectedBankQuestionIds([]);
    setQuestionOffset(0);
    await refresh(activeLearnerId, 0, preset.filters, preset.tag_ids);
    setStatus("success", `已套用保存筛选：${preset.name}`);
  }

  function deleteSavedQuestionFilterPreset() {
    const preset = savedQuestionFilters.find((row) => row.id === selectedSavedQuestionFilterId);
    if (!preset) {
      setStatus("error", "请选择要删除的保存筛选");
      return;
    }
    if (!persistSavedQuestionFilters(savedQuestionFilters.filter((row) => row.id !== preset.id))) return;
    setSelectedSavedQuestionFilterId("");
    setStatus("success", `已删除保存筛选：${preset.name}`);
  }

  async function applyQuestionFilters(e: FormEvent) {
    e.preventDefault();
    setSelectedBankQuestionIds([]);
    setQuestionOffset(0);
    await refresh(activeLearnerId, 0, questionFilters, selectedTagIds);
  }

  async function changeQuestionPage(direction: "prev" | "next") {
    const nextOffset = direction === "prev" ? Math.max(0, questionOffset - QUESTION_PAGE_SIZE) : questionOffset + QUESTION_PAGE_SIZE;
    if (nextOffset === questionOffset) return;
    setQuestionOffset(nextOffset);
    await refresh(activeLearnerId, nextOffset, questionFilters, selectedTagIds);
  }

  async function applyReportFilters(e: FormEvent) {
    e.preventDefault();
    await loadReportSummary();
    setStatus("success", "报告筛选已更新");
  }

  async function drillHeatmapCell(tagId: string, star: number) {
    const nextFilters = { ...reportFilters, tag_id: tagId, difficulty_star: String(star) };
    setReportFilters(nextFilters);
    await loadReportSummary(nextFilters);
    setStatus("success", "报告下钻已更新");
  }

  async function loadEvidenceDetail(evidenceId: string, questionId: string) {
    setSelectedQuestion(questionId);
    const detail = await api<EvidenceDetail>(`/api/reports/evidence/${evidenceId}`);
    setEvidenceDetail(detail);
    setStatus("success", "证据时间线已加载");
  }

  function renderQuestionSupportPanel() {
    if (!sessionId || !selectedQuestion) return null;
    return (
      <div className="supportPanel">
        <div className="supportHead">
          <div>
            <span className="eyebrow">{t("practice.question_concerns")}</span>
            <strong>{t("common.formatting_symbols_and_wording_only")}</strong>
          </div>
          <button type="button" onClick={submitQuestionSupport} disabled={operationStatus.state === "loading" || breakDue}>
            <HelpCircle size={16} />{t("common.submit_concern")}</button>
        </div>
        <div className="supportControls">
          <select value={supportConcernType} onChange={(event) => setSupportConcernType(event.target.value as typeof supportConcernType)}>
            <option value="format_issue">{t("common.formatting_display_issue")}</option>
            <option value="do_not_understand">{t("common.unclear_wording")}</option>
            <option value="suspected_error">{t("practice.suspected_question_error")}</option>
            <option value="other">{t("common.other")}</option>
          </select>
          <textarea
            value={supportMessage}
            onChange={(event) => setSupportMessage(event.target.value)}
            placeholder={t("common.examples_missing_figure_unclear_vertical_arithmetic_recurring_decimal_marks")}
            disabled={breakDue}
          />
        </div>
        {questionSupportResult && (
          <div className={`supportResult ${questionSupportResult.question_problem ? "problem" : "safe"}`}>
            <strong>{(questionSupportResult.replaced ? t("practice.replaced_with_a_same_star_question") : (questionSupportResult.question_problem ? t("practice.question_concern_recorded") : t("common.recorded")))}</strong>
            <span>{questionSupportResult.safe_response}</span>
            {questionSupportResult.replaced && questionSupportResult.replacement_question_id && (
              <span>{t("practice.switched_to_replacement_question")} {questionSupportResult.replacement_question_id.slice(0, 8)}{t("common.this_replaces_the_original_slot_without_increasing_the_total")}</span>
            )}
            {questionSupportResult.question_problem && !questionSupportResult.replaced && (
              <span>{t("practice.ai_found_a_possible_issue_but_no_same_star")}</span>
            )}
            <em>{questionSupportResult.provider} · {questionSupportResult.model} {t("common.confidence")} {Math.round(questionSupportResult.confidence * 100)}%</em>
            {questionSupportResult.error && <span className="errorText">{t(questionSupportResult.error)}</span>}
          </div>
        )}
      </div>
    );
  }

  function renderSolutionExplanationPanel() {
    if (!sessionId || !selectedQuestion || !answerLocked) return null;
    return (
      <div className="solutionExplainPanel">
        <div className="supportHead">
          <div>
            <span className="eyebrow">{t("common.explain_solution")}</span>
            <strong>{t("practice.explains_only_this_question_s_already_shown_answer")}</strong>
          </div>
          <div className="inlineButtonGroup">
            <button type="button" onClick={() => requestSolutionExplanation()} disabled={operationStatus.state === "loading" || !solutionExplanationMessage.trim()}>
              <BrainCircuit size={16} />{t("practice.explain_my_question")}</button>
            <button
              type="button"
              onClick={() => requestSolutionExplanation("请根据这道题、标准答案、我之前提交的答案和思路，生成一份适合小学生阅读的个性化完整解答：先分析题意，再写清楚每一步为什么这样做，最后提醒我下次如何检查。")}
              disabled={operationStatus.state === "loading"}
            >
              <BrainCircuit size={16} />{t("common.personalized_solution")}</button>
          </div>
        </div>
        <textarea
          value={solutionExplanationMessage}
          onChange={(event) => setSolutionExplanationMessage(event.target.value)}
          placeholder={t("practice.examples_why_is_the_first_transformation_valid_why_is")}
        />
        {solutionExplanationResult && (
          <div className={`supportResult ${solutionExplanationResult.status === "ok" ? "safe" : "problem"}`}>
            <strong>{(solutionExplanationResult.status === "ok" ? t("common.explanation_saved") : (solutionExplanationResult.refused ? t("common.out_of_scope_request_declined") : t("common.explanation_failed")))}</strong>
            {solutionExplanationResult.explanation && <p>{solutionExplanationResult.explanation}</p>}
            {solutionExplanationResult.key_points.length > 0 && <span>{t("common.key_points")}{solutionExplanationResult.key_points.join("；")}</span>}
            {solutionExplanationResult.common_misunderstandings.length > 0 && <span>{t("common.common_misconceptions")}{solutionExplanationResult.common_misunderstandings.join("；")}</span>}
            {solutionExplanationResult.next_review_suggestion && <span>{solutionExplanationResult.next_review_suggestion}</span>}
            {solutionExplanationResult.refusal_reason && <span>{t(solutionExplanationResult.refusal_reason)}</span>}
            <em>{solutionExplanationResult.provider} · {solutionExplanationResult.model}{(solutionExplanationResult.latency_ms != null ? ` · ${t(formatMs(solutionExplanationResult.latency_ms))}` : "")}</em>
            {solutionExplanationResult.error && <span className="errorText">{t(solutionExplanationResult.error)}</span>}
          </div>
        )}
      </div>
    );
  }

  function renderGenerationAdvancedControls(extraClass = "", targetCount = requestedBatchCount) {
    const worstCaseCalls = targetCount * requestedCandidateCount;
    const worstCaseReviewerCalls = worstCaseCalls * requestedReviewerMultiplier;
    return (
      <details className={`advancedGenerationControls ${extraClass}`}>
        <summary>
          <Settings size={16} />
          <span>{t("generation.advanced_generation_settings_a8efade5")}</span>
          <em>{requestedCandidateCount} {t("common.candidates")} {requestedSourceExampleCount} {t("common.non_ai_examples")} {requestedAiNegativeExampleCount} {t("common.ai_examples")}</em>
        </summary>
        <div className="qualityPresetBar">
          {(Object.entries(GENERATION_PRESETS) as [GenerationPreset, (typeof GENERATION_PRESETS)[GenerationPreset]][]).map(([preset, values]) => (
            <button key={preset} type="button" onClick={() => applyGenerationPreset(preset)} title={t(values.description)}>
              {t(values.label)}
            </button>
          ))}
        </div>
        <div className="advancedGrid">
          <label>
            <span>{t("practice.candidates_per_question")}</span>
            <input
              value={batchRequest.candidate_count}
              onChange={(event) => setBatchRequest({ ...batchRequest, candidate_count: event.target.value })}
              inputMode="numeric"
              min={1}
              max={5}
            />
          </label>
          <label>
            <span>{t("common.non_ai_example_count")}</span>
            <input
              value={batchRequest.source_example_count}
              onChange={(event) => setBatchRequest({ ...batchRequest, source_example_count: event.target.value })}
              inputMode="numeric"
              min={0}
              max={8}
            />
          </label>
          <label>
            <span>{t("common.ai_anti_duplicate_example_count")}</span>
            <input
              value={batchRequest.ai_negative_example_count}
              onChange={(event) => setBatchRequest({ ...batchRequest, ai_negative_example_count: event.target.value })}
              inputMode="numeric"
              min={0}
              max={5}
            />
          </label>
        </div>
        <label className="checkboxLine budgetOverrideLine">
          <input
            type="checkbox"
            checked={batchRequest.allow_budget_overrun}
            onChange={(event) => setBatchRequest({ ...batchRequest, allow_budget_overrun: event.target.checked })}
          />
          <span>{t("common.allow_real_calls_beyond_daily_monthly_cost_budgets")}</span>
        </label>
        <label className="checkboxLine budgetOverrideLine">
          <input
            type="checkbox"
            checked={batchRequest.allow_reviewer_quality_downgrade}
            onChange={(event) => setBatchRequest({ ...batchRequest, allow_reviewer_quality_downgrade: event.target.checked })}
          />
          <span>{t("generation.allow_publication_despite_reviewer_model_degradation")}</span>
        </label>
        <p className="mutedLine">{t("generation.more_candidates_allow_stricter_selection_but_take_longer_non")}{targetCount} {t("common.questions_x")} {requestedCandidateCount} {t("common.candidates_30a13ca1")} {worstCaseCalls} {t("generation.generation_calls_up_to")} {worstCaseReviewerCalls}{t("generation.review_calls_concurrency_counts_question_slots_candidate_generation_review")}</p>
      </details>
    );
  }

  return (
    <main className={`${strictFocus ? "strictFocus" : ""} ${highContrast ? "highContrast" : ""}`} style={{ "--fontScale": fontScale } as CSSProperties & Record<"--fontScale", number>}>
      <a className="skipLink" href="#mainContent">{t("common.skip_to_current_workspace")}</a>
      <header className="appHeader">
        <div>
          <h1>{t("practice.math_practice_learning_insights")}</h1>
          <div className={`statusPill ${operationStatus.state}`} role="status" aria-live="polite">
            <span>{operationStatus.ui ? tf(operationStatus.ui.key, operationStatus.ui.values.map((value, index) => operationStatus.ui?.uiSlots.includes(index) ? t(value) : value)) : t(operationStatus.label)}</span>
            {operationStatus.traceId && (
              <button type="button" className="IDMiniButton" onClick={copyStatusTraceId} title={t("common.copy_degraded_for_this_error")}>
                <Clipboard size={13} />{t("common.copy_degraded")}</button>
            )}
            {operationStatus.errorCode && (
              <button type="button" className="IDMiniButton" onClick={openDiagnosticsFromStatus} title={t("admin.open_admin_to_export_redacted_diagnostics")}>
                <Download size={13} />{t("common.diagnostics")}</button>
            )}
          </div>
          <div className={`apiReadinessBar ${apiConnection.state}`} role="status" aria-live="polite">
            <span>{t("common.local_service")}</span>
            <strong>{t(apiConnection.label)}</strong>
            <em>{(apiConnection.latency_ms != null ? `${apiConnection.latency_ms} ms` : t(apiConnection.detail))}</em>
            {apiConnection.state !== "online" && <small>{t(apiConnection.detail)}</small>}
            {apiConnection.state === "online" && <small>{modelSettingsState !== "ready"
              ? t(modelSettingsState === "error" ? "密钥状态读取失败，请点击重新检查；这不代表密钥丢失。" : "正在读取模型配置，尚未确定密钥状态。")
              : (modelSettings.has_api_key ? t("admin.api_key_saved_real_models_are_available") : t("admin.api_ready_no_api_key_saved_yet"))}</small>}
            <button type="button" onClick={() => checkApiConnection(true)}><RefreshCcw size={13} />{t("common.check_again")}</button>
          </div>
        </div>
        <div className="headerActions">
          {!strictFocus && <button type="button" disabled={modelSettingsState !== "ready" || apiConnection.state !== "online"} onClick={() => setApiSetupRequest(value => value + 1)}><KeyRound size={16} />{translateUi("admin.api_setup", language)}</button>}
          <div className="segmented" aria-label={t("common.user_mode")}>
            <button className={appMode === "learner" ? "on" : ""} onClick={() => { setAppMode("learner"); if (activeWorkspace === "admin") setActiveWorkspace("practice"); }} title={t("common.learner_mode")}><UserRound size={16} />{t("common.learner")}</button>
            <button className={appMode === "admin" ? "on" : ""} onClick={() => { setAppMode("admin"); setActiveWorkspace("admin"); }} title={t("common.administrator_mode")}><ShieldCheck size={16} />{t("common.administrator")}</button>
          </div>
          <div className="learnerSwitch" title={t("common.switch_learner_profile")}>
            <UserRound size={16} />
            <select value={activeLearnerId} onChange={(event) => { void switchLearner(event.target.value); }}>
              {learners.map((row) => (
                <option key={row.id} value={row.id}>{row.is_default && row.name === "默认学习者" ? t("common.default_learner") : row.name}{(row.is_default ? t("common.default") : "")}</option>
              ))}
              {!learners.length && <option value="">{t("common.default_learner")}</option>}
            </select>
            <input
              value={newLearnerName}
              onChange={(event) => setNewLearnerName(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") void createLearner(); }}
              placeholder={t("common.add_profile")}
              aria-label={t("common.new_learner_name")}
            />
            <button type="button" onClick={() => createLearner()} disabled={operationStatus.state === "loading"}>{t("common.add")}</button>
          </div>
          <button type="button" onClick={() => setAssistantOpen(true)} title={t("assistant.open_project_ai_assistant")}><BrainCircuit size={16} />{t("assistant.project_ai_assistant")}</button>
          <button className="iconButton" onClick={() => setHighContrast(!highContrast)} title={t("common.high_contrast")} aria-label={t("common.toggle_high_contrast")}><Contrast size={18} /></button>
          <button className="iconButton" onClick={() => setFontScale(fontScale >= 1.15 ? 1 : 1.15)} title={t("common.text_size")} aria-label={t("common.change_text_size")}><Type size={18} /></button>
          <button className="iconButton refreshButton" onClick={() => refresh()} disabled={operationStatus.state === "loading"} title={t("common.refresh")} aria-label={t("common.refresh_current_data")}><RefreshCcw size={18} /></button>
          <button type="button" onClick={refreshAllContent} disabled={operationStatus.state === "loading"} title={t("generation.refresh_all_and_restore_current_batch_history_and_practice")}><RefreshCcw size={16} />{t("common.refresh_all_recover")}</button>
          <button type="button" onClick={recoverStaleState} disabled={operationStatus.state === "loading"} title={t("generation.repair_stuck_generation_review_and_hint_states_preserve_history")}><AlertTriangle size={16} />{t("common.repair_stuck_states")}</button>
          {strictFocus && <button type="button" className="dangerSoft strictExitButton" onClick={exitStrictFocus}><X size={16} />{t("common.exit_focus")}</button>}
        </div>
      </header>

      {renderAssistantDrawer()}

      <ApiSetupDialog settings={modelSettings} ready={modelSettingsState === "ready" && apiConnection.state === "online"} enabled={!strictFocus} openRequest={apiSetupRequest} apiBase={API_BASE} onSaved={saved => { applyModelSettings(saved); setApiKeyDraft(""); setApiTestResult(null); setAvailableModels([]); }} />

      {(!strictFocus && renderTaskCenter())}

      {!strictFocus && (
        <nav className="workspaceTabs" aria-label={t("common.workspaces")}>
          {workspaceItems.map((item) => (
            <button key={item.id} className={activeWorkspace === item.id ? "on" : ""} onClick={() => setActiveWorkspace(item.id)}>
              {item.id === "practice" && <CheckCircle2 size={16} />}
              {item.id === "generated" && <BrainCircuit size={16} />}
              {item.id === "history" && <Clock3 size={16} />}
              {item.id === "bank" && <BookOpen size={16} />}
              {item.id === "reports" && <BarChart3 size={16} />}
              {item.id === "guide" && <HelpCircle size={16} />}
              {item.id === "admin" && <Settings size={16} />}
              {t(item.label)}
            </button>
          ))}
        </nav>
      )}

      <div id="mainContent" className="mainContent" tabIndex={-1}>
      {activeWorkspace === "practice" && (
        <section className={strictFocus ? "taskBoard strict" : "taskBoard"}>
          <div className="practiceMain">
            <div className="sectionHeader">
              <div>
                <span className="eyebrow">{(strictFocus ? t("practice.strict_test") : t("common.current_workspace"))}</span>
                <h2>{t("practice.practice_grading")}</h2>
              </div>
              <div className="actionRow">
                {!strictFocus && (
                  <label className="compactSelect"><span>{t("practice.practice_question_count")}</span><select value={practiceCount} onChange={(event) => setPracticeCount(event.target.value)} disabled={operationStatus.state === "loading"}>
                    {[3, 5, 10, 15, 20, 30, 50, 100].map((value) => <option key={value} value={String(value)}>{value} {t("common.questions")}</option>)}
                  </select></label>
                )}
                {!strictFocus && (
                  <label className="compactSelect"><span>{t("practice.practice_source")}</span><select value={practiceSourceName} onChange={(event) => setPracticeSourceName(event.target.value)} disabled={operationStatus.state === "loading"}>
                    <option value="">{t("common.all_sources")}</option>
                    {sourceOptions.map((source) => <option key={source} value={source}>{t(compactSource(source))}</option>)}
                  </select></label>
                )}
                {!strictFocus && (
                  <label className="compactSelect"><span>{t("practice.practice_difficulty")}</span><select value={practiceDifficultyStar} onChange={(event) => setPracticeDifficultyStar(event.target.value)} disabled={operationStatus.state === "loading"}>
                    <option value="">{t("common.all_star_levels")}</option>
                    {[1, 2, 3, 4, 5].map((star) => <option key={star} value={String(star)}>{star} {t("common.stars")}</option>)}
                  </select></label>
                )}
                {!strictFocus && (
                  <label className="compactSelect"><span>{t("common.attempted_questions")}</span><select value={practiceExcludeDone ? "exclude" : "allow"} onChange={(event) => setPracticeExcludeDone(event.target.value === "exclude")} disabled={operationStatus.state === "loading"}>
                    <option value="exclude">{t("common.exclude_attempted_questions")}</option>
                    <option value="allow">{t("common.include_attempted_questions")}</option>
                  </select></label>
                )}
                {!strictFocus && <button onClick={createPractice} disabled={operationStatus.state === "loading"}><Play size={16} />{t("practice.start_practice")}</button>}
                {!strictFocus && (
                  <div className="strictOptions" aria-label={t("practice.strict_test_source")}>
                    {(Object.keys(STRICT_SOURCE_LABELS) as StrictSourceMode[]).map((mode) => (
                      <button key={mode} type="button" className={strictSourceMode === mode ? "on" : ""} onClick={() => setStrictSourceMode(mode)}>
                        {t(STRICT_SOURCE_LABELS[mode])}
                      </button>
                    ))}
                  </div>
                )}
                {!strictFocus && (
                  <details className="strictTestConfig">
                    <summary>{t("practice.strict_test_settings")}</summary>
                    <div className="strictConfigGrid">
                      <label><span>{t("practice.question_count")}</span><input value={strictTestRequest.count} onChange={(event) => setStrictTestRequest({ ...strictTestRequest, count: event.target.value })} inputMode="numeric" /></label>
                      <label className="checkboxLine"><input type="checkbox" checked={strictTestRequest.use_custom_quota} onChange={(event) => setStrictTestRequest({ ...strictTestRequest, use_custom_quota: event.target.checked })} /><span>{t("common.custom_counts_by_star_level")}</span></label>
                      <label className="checkboxLine"><input type="checkbox" checked={strictTestRequest.fill_missing_with_ai} disabled={strictSourceMode === "bank_only"} onChange={(event) => setStrictTestRequest({ ...strictTestRequest, fill_missing_with_ai: event.target.checked })} /><span>{t("generation.fill_bank_shortfalls_with_ai_generation_and_review")}</span></label>
                    </div>
                    {strictTestRequest.use_custom_quota && (
                      <div className="strictQuotaGrid">
                        {[1, 2, 3, 4, 5].map((star) => {
                          const key = `star_${star}` as keyof typeof strictTestRequest;
                          return <label key={star}><span>{star} {t("common.stars")}</span><input value={String(strictTestRequest[key])} onChange={(event) => setStrictTestRequest({ ...strictTestRequest, [key]: event.target.value })} inputMode="numeric" /></label>;
                        })}
                        <em className={requestedStrictStarQuotaTotal === requestedStrictCount ? "correctText" : "errorText"}>{t("common.total")} {requestedStrictStarQuotaTotal}/{requestedStrictCount}</em>
                      </div>
                    )}
                    <p className="mutedLine">{t("history.defaults_to_100_questions_with_a_demanding_difficulty_mix")}</p>
                  </details>
                )}
                <button className="dangerSoft" onClick={createStrictTest} disabled={operationStatus.state === "loading"}><CheckCircle2 size={16} />{t("practice.strict_test")}</button>
              </div>
            </div>

            {!strictFocus && todayTasks && (
              <div className="todayTasksPanel">
                <div className="todayTasksHead">
                  <div>
                    <span className="eyebrow">{t("common.today_s_tasks")}</span>
                    <strong>{todayTasks.mastery_summary.due_question_count} {t("history.due_for_review")} {todayTasks.mastery_summary.observed_tag_star_count} {t("common.observed_tag_star_combinations")}</strong>
                  </div>
                  <button type="button" onClick={() => refresh()} disabled={operationStatus.state === "loading"}><RefreshCcw size={16} />{t("common.refresh")}</button>
                </div>
                <div className="todayTaskList">
                  {todayTasks.tasks.map((task) => (
                    <div key={task.task_id} className={`todayTaskCard ${task.priority}`}>
                      <div>
                        <strong>{todayTaskTitle(task.title)}</strong>
                        <span>{task.question_count} {t("common.questions_ef06789e")} {task.reason_codes.join(" / ")}</span>
                      </div>
                      <button type="button" onClick={() => startTodayTask(task)} disabled={operationStatus.state === "loading"}>
                        <Play size={16} />{t("common.start")}</button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {sessionState && (
              <div className="progressCard">
                <div>
                  <strong>{(sessionState.mode === "strict_test" ? t("practice.strict_test") : t("practice.regular_practice"))}</strong>
                  <span>{sessionState.status} {t("common.remaining")} {sessionRemaining} {t("common.questions")}</span>
                </div>
                <div className="progressTrack"><span style={{ width: `${Math.round(practiceProgress * 100)}%` }} /></div>
                <span>{sessionState.answered_count}/{sessionState.question_ids.length}</span>
                {Boolean(sessionSelection.policy) && <em>{(t(STRICT_SOURCE_LABELS[sessionStrictSourceMode]) || t("common.bank_questions"))}</em>}
                {sessionState.strict_no_ai && <em>{t("common.live_ai_disabled")}</em>}
                {sessionState.next_breakpoint && <button type="button" onClick={confirmBreak}>{t("practice.confirm_break_after_question")} {sessionState.next_breakpoint} {t("common.then_continue")}</button>}
              </div>
            )}

            {sessionSelectionWarnings.length > 0 && (
              <div className="selectionWarnings">
                <AlertTriangle size={16} />
                {sessionSelectionWarnings.map((warning) => <span key={warning}>{t(selectionWarningText(warning))}</span>)}
              </div>
            )}

            {breakDue && (
              <div className="breakPanel">
                <strong>{t("assistant.break_confirmation")}</strong>
                <span>{t("practice.reached_question")} {sessionState?.next_breakpoint} {t("common.confirm_your_break_to_continue")}</span>
                <button type="button" onClick={confirmBreak}>{t("common.continue")}</button>
              </div>
            )}

            <article className="problemSurface">
              <div className="problemMeta">
                <span>{(sessionId ? tf("practice.practice_session_value", [sessionId.slice(0, 8)]) : t("practice.no_practice_session_yet"))}</span>
                {currentQuestion && <span>{t(compactSource(currentQuestion.source_name))} · {(currentQuestion.difficulty_star || "-")} {t("common.stars")}</span>}
                {currentQuestion?.last_answered_at && <span>{t("common.last_answered")} {formatDateTime(currentQuestion.last_answered_at)}</span>}
              </div>
              <MathContent source={currentStem} />
              {currentAnswerGuide && (
                <div className="answerFormatPanel">
                  <strong>{t(currentAnswerGuide.title)}</strong>
                  {currentAnswerGuide.lines.map((line) => <span key={line}>{t(line)}</span>)}
                  {currentAnswerGuide.examples.length > 0 && <em>{t("practice.answer_format_not_the_answer")}{t(currentAnswerGuide.examples.join(" / "))}</em>}
                </div>
              )}
            </article>

            <form onSubmit={submit} className="answerDock">
              <input value={answer} onChange={(event) => setAnswer(event.target.value)} placeholder={t("practice.enter_answer")} disabled={breakDue || answerLocked} />
              <button type="submit" disabled={breakDue || answerLocked || operationStatus.state === "loading"} title={t("common.ctrl_command_enter_to_submit")}><Send size={18} />{t("common.submit")}</button>
              <textarea value={thought} onChange={(event) => setThought(event.target.value)} placeholder={t("common.reasoning_optional")} disabled={breakDue || answerLocked} />
              {questionDraftStatusText && <span className={`draftStatus ${questionDraftStatus.includes("失败") ? "error" : ""}`}>{t(questionDraftStatusText)}</span>}
            </form>

            {answerLocked && !strictFocus && (
              <div className="lockedNotice">
                <AlertTriangle size={16} />
                <span>{t("practice.answer_viewed_this_question_is_locked_against_editing_submission")}</span>
              </div>
            )}

            {result && !strictFocus && (
              <div className={`resultSummary ${result.is_correct ? "correct" : "wrong"}`}>
                <strong>{(result.is_correct ? t("practice.correct_answer_06a38c0f") : t("practice.answer_not_accepted"))}</strong>
                <span>{t("common.effective_errors")} {String(result.effective_error_count ?? 0)} {t("common.times")} {(result.duplicate_answer ? t("practice.duplicate_answer_not_counted") : t("common.submission_recorded"))}</span>
                {typeof result.reason === "string" && <span>{t(displayErrorType(result.reason))}</span>}
                <button type="button" onClick={requestAnswerJudge} disabled={operationStatus.state === "loading"} title={t("history.review_this_answer_with_the_answer_review_model")}><BrainCircuit size={16} />{t("history.ai_review")}</button>
              </div>
            )}

            {answerJudgeResult && !strictFocus && (
              <div className={`judgePanel ${answerJudgeResult.verdict}`}>
                <div>
                  <strong>{(answerJudgeResult.verdict === "correct" ? t("history.review_accepted") : (answerJudgeResult.verdict === "incorrect" ? t("history.review_not_passed") : t("history.review_uncertain")))}</strong>
                  <span>{answerJudgeResult.provider} · {answerJudgeResult.model} {t("common.confidence")} {Math.round(answerJudgeResult.confidence * 100)}%</span>
                </div>
                {answerJudgeResult.student_feedback && <p>{answerJudgeResult.student_feedback}</p>}
                {answerJudgeResult.analysis && <p>{answerJudgeResult.analysis}</p>}
                {answerJudgeResult.missing_parts.length > 0 && <span>{t("common.missing_463071aa")}{answerJudgeResult.missing_parts.join("、")}</span>}
                {answerJudgeResult.extra_parts.length > 0 && <span>{t("common.extra")}{answerJudgeResult.extra_parts.join("、")}</span>}
                {answerJudgeResult.format_issue && <span>{t("common.format")}{answerJudgeResult.format_issue}</span>}
                {answerJudgeResult.error && <span className="errorText">{t(answerJudgeResult.error)}</span>}
              </div>
            )}

            {sessionId && selectedQuestion && (
              <div className="practiceActions">
                <button type="button" onClick={() => recordPracticeEvent("skipped")} title="Alt+S"><SkipForward size={16} />{t("common.skip")}</button>
                <button type="button" onClick={() => recordPracticeEvent("bookmarked")} title="Alt+B"><Bookmark size={16} />{t("common.bookmark")}</button>
                <button type="button" onClick={() => recordPracticeEvent("marked_uncertain")} title="Alt+U"><Flag size={16} />{t("common.unsure")}</button>
                <button type="button" onClick={pausePractice}><PauseCircle size={16} />{t("common.pause")}</button>
                <button type="button" onClick={resumePractice}><Play size={16} />{t("common.continue")}</button>
                {!strictFocus && !currentQuestionIsHint && currentHintIntervention && currentHintQuestionId && (
                  <button type="button" onClick={() => startHintIntervention(currentHintIntervention)}><HelpCircle size={16} />{t("practice.try_hint_question")}</button>
                )}
                {!strictFocus && currentQuestionIsHint && (
                  <>
                    <button type="button" onClick={() => completeHintAndReturnOriginal(currentHintIntervention)}><CheckCircle2 size={16} />{t("common.complete_and_return")}</button>
                    <button type="button" onClick={() => returnToOriginalQuestion(currentHintIntervention)}><ArrowLeft size={16} />{t("common.return_to_original")}</button>
                  </>
                )}
                {!strictFocus && currentQuestionIsMasteryCheck && (
                  <button type="button" onClick={() => returnToOriginalQuestion(currentMasteryCheck)}><ArrowLeft size={16} />{t("common.return_to_original")}</button>
                )}
                {!strictFocus && !currentQuestionIsMasteryCheck && (!currentMasteryCheck || currentMasteryFailed || currentMasteryCompleted) && (
                  <>
                    <label className="compactSelect"><span>{t("history.similar_questions")}</span><select value={masteryCheckCount} onChange={(event) => setMasteryCheckCount(event.target.value)} disabled={operationStatus.state === "loading" || breakDue}>
                      {[1, 2, 3, 4, 5].map((value) => <option key={value} value={String(value)}>{value} {t("common.questions_00a9d5bd")}</option>)}
                    </select></label>
                    <button type="button" onClick={requestMasteryCheck} disabled={operationStatus.state === "loading" || breakDue} title={t("generation.generate_checks_of_the_same_core_skill_different_surface")}><BrainCircuit size={16} />{(currentMasteryFailed ? t("history.regenerate_similar_questions") : (currentMasteryCompleted ? t("generation.generate_more_similar_questions") : t("history.similar_questions_check")))}</button>
                  </>
                )}
                {!strictFocus && !currentQuestionIsMasteryCheck && currentMasteryCheck && !currentMasteryReady && !currentMasteryFailed && !currentMasteryCompleted && (
                  <>
                    <button type="button" disabled><BrainCircuit size={16} />{t("common.generating_check_questions")}</button>
                    <button type="button" onClick={refreshMasteryCheckStatus} disabled={operationStatus.state === "loading"}><RefreshCcw size={16} />{t("common.refresh_check_status")}</button>
                  </>
                )}
                {!strictFocus && !currentQuestionIsMasteryCheck && currentMasteryCheck && currentMasteryReady && (
                  <button type="button" onClick={() => startMasteryCheck(currentMasteryCheck)}><CheckCircle2 size={16} />{t("common.start_check")}</button>
                )}
              </div>
            )}

            {sessionId && selectedQuestion && !strictFocus && currentHintIntervention && (
              <div className={`masteryCheckPanel ${currentQuestionIsHint ? "active" : "ready"}`}>
                <div>
                  <span className="eyebrow">{t("practice.hint_question")}</span>
                  <strong>{(currentTemporaryHintReady ? t("practice.temporary_hint_question") : (currentQuestionIsHint ? t("practice.answering_hint_question") : t("practice.hint_question_available")))}</strong>
                </div>
                {currentTemporaryHintReady ? (
                  <div className="temporaryHintCard">
                    <span>{currentTemporaryHintIntro}</span>
                    <MathContent source={currentTemporaryHintStem} />
                    {answerFormatGuide(currentTemporaryHintAnswerSpec) && (
                      <div className="answerFormatPanel compactGuide">
                        <strong>{t(answerFormatGuide(currentTemporaryHintAnswerSpec)?.title)}</strong>
                        {answerFormatGuide(currentTemporaryHintAnswerSpec)?.lines.map((line) => <span key={line}>{t(line)}</span>)}
                        {(answerFormatGuide(currentTemporaryHintAnswerSpec)?.examples || []).length > 0 && <em>{t("practice.answer_format_not_the_answer")}{t(answerFormatGuide(currentTemporaryHintAnswerSpec)?.examples.join(" / "))}</em>}
                      </div>
                    )}
                    <div className="temporaryHintInputs">
                      <input value={hintAnswer} onChange={(event) => setHintAnswer(event.target.value)} placeholder={t("practice.hint_answer")} />
                      <textarea value={hintThought} onChange={(event) => setHintThought(event.target.value)} placeholder={t("practice.hint_reasoning_optional")} />
                      <button type="button" onClick={() => submitTemporaryHintAnswer(currentHintIntervention)} disabled={operationStatus.state === "loading"}><Send size={16} />{t("practice.submit_hint_answer")}</button>
                    </div>
                    {Boolean(hintAnswerResult || currentTemporaryHintLastAttempt.result) && (
                      <span className={Boolean(hintAnswerResult?.is_correct ?? asRecord(currentTemporaryHintLastAttempt.result).is_correct) ? "correctText" : "errorText"}>
                        {(Boolean(hintAnswerResult?.is_correct ?? asRecord(currentTemporaryHintLastAttempt.result).is_correct) ? t("practice.hint_passed_try_the_original_question_again") : t("practice.hint_not_passed_try_a_different_approach"))}
                      </span>
                    )}
                  </div>
                ) : (
                  <span>
                    {(currentQuestionIsHint ? tf("practice.return_to_original_question_value_after_finishing", [currentHintIntervention.original_question_id.slice(0, 8)]) : t("practice.after_repeated_errors_a_lighter_hint_question_on_the"))}
                  </span>
                )}
                {currentTemporaryHintReady ? (
                  <button type="button" onClick={() => returnToOriginalQuestion(currentHintIntervention)}><ArrowLeft size={16} />{t("common.return_to_original_988fc39f")}</button>
                ) : currentQuestionIsHint ? (
                  <button type="button" onClick={() => completeHintAndReturnOriginal(currentHintIntervention)}><CheckCircle2 size={16} />{t("common.complete_and_return")}</button>
                ) : currentHintQuestionId ? (
                  <button type="button" onClick={() => startHintIntervention(currentHintIntervention)}><Play size={16} />{t("practice.try_hint_now")}</button>
                ) : null}
              </div>
            )}

            {sessionId && selectedQuestion && !strictFocus && currentMasteryCheck && (
              <div className={`masteryCheckPanel ${currentMasteryReady ? "ready" : currentQuestionIsMasteryCheck ? "active" : currentMasteryFailed ? "failed" : ""}`}>
                <div>
                  <span className="eyebrow">{t("history.mastery_check")}</span>
                  <strong>{(currentQuestionIsMasteryCheck ? t("practice.answering_check_question") : t(masteryCheckStatusText(currentMasteryCheck)))}</strong>
                </div>
                <span>
                  {(currentQuestionIsMasteryCheck ? t("reports.correct_answers_provide_independent_evidence_of_the_original_core") : tf("practice.generated_from_this_question_attempts_help_usage_and_tags", [currentMasteryReadyCount, currentMasteryRequestedCount]))}
                </span>
                {currentMasteryFailed && (
                  <span className="errorText">{t("generation.generation_failed_cfdcd161")}{(currentMasteryFailedReason || t("generation.provider_rejected_review_failed_or_generation_timed_out"))}{t("common.adjust_the_count_and_try_again")}</span>
                )}
                {!currentQuestionIsMasteryCheck && currentMasteryReady && (
                  <button type="button" onClick={() => startMasteryCheck(currentMasteryCheck)}><Play size={16} />{t("common.start_now")}</button>
                )}
                {!currentQuestionIsMasteryCheck && currentMasteryFailed && (
                  <button type="button" onClick={requestMasteryCheck} disabled={operationStatus.state === "loading" || breakDue}><RefreshCcw size={16} />{t("common.regenerate")}</button>
                )}
                {currentQuestionIsMasteryCheck && (
                  <button type="button" onClick={() => returnToOriginalQuestion(currentMasteryCheck)}><ArrowLeft size={16} />{t("common.return_to_original")}</button>
                )}
              </div>
            )}

            {renderQuestionSupportPanel()}

            {sessionId && selectedQuestion && !strictFocus && (
              <div className="helpPanel">
                <div className="helpHead">
                  <div>
                    <span className="eyebrow">{t("common.staged_help")}</span>
                    <strong>{(t(currentHelpState?.current_help?.title) || t("common.self_check"))}</strong>
                  </div>
                  <div className="helpActions">
                    <button type="button" onClick={revealNextHelp} disabled={!currentHelpState?.can_reveal_next}><HelpCircle size={16} />{t("common.unlock_next_level")}</button>
                    <button type="button" className="dangerSoft" onClick={giveUpQuestion}>{t("common.view_full_solution_abb3825e")}</button>
                  </div>
                </div>
                <div className="helpLevels" aria-label={t("common.help_level")}>
                  {[0, 1, 2, 3, 4, 5].map((level) => (
                    <span key={level} className={level <= (currentHelpState?.reached_level ?? 0) ? "reached" : level <= (currentHelpState?.allowed_level ?? 0) ? "available" : ""}>{level}</span>
                  ))}
                </div>
                <div className="helpBody">
                  {((currentHelpState?.reached_level ?? 0) === 0
                    ? [t("help.check_all_conditions"), t("help.check_units"), t("help.check_whole_or_part")]
                    : currentHelpState?.current_help?.content || []).map((line, index) => <p key={index}>{line}</p>)}
                  {currentHelpState?.current_help?.final_answer && <p><strong>{t("practice.answer")}</strong>{currentHelpState.current_help.final_answer}</p>}
                </div>
                {(answerLocked && renderSolutionExplanationPanel())}
              </div>
            )}
          </div>

          <aside className="queuePanel">
            <div className="sectionHeader compactHeader">
              <div>
                <span className="eyebrow">{t("practice.question_queue")}</span>
                <h2>{(sessionQuestions.length || questions.length)} {t("common.questions")}</h2>
              </div>
            </div>
            {activeSessionQueue.length > 1 && (
              <div className="queueNav">
                <button type="button" onClick={() => moveSelectedQuestion(-1)} disabled={activeSessionQuestionIndex <= 0}>{t("practice.previous_question")}</button>
                <span>{activeSessionQuestionIndex + 1}/{activeSessionQueue.length}</span>
                <button type="button" onClick={() => moveSelectedQuestion(1)} disabled={activeSessionQuestionIndex < 0 || activeSessionQuestionIndex >= activeSessionQueue.length - 1}>{t("practice.next_question")}</button>
              </div>
            )}
            <div className="queueGrid">
              {(sessionQuestions.length ? sessionQuestions : questions.map((question) => question.id)).slice(0, 100).map((id, index) => {
                const status = questionStatus(id);
                return (
                  <button key={id} className={`queueCell ${id === selectedQuestion ? "on" : ""} ${status}`} onClick={() => setSelectedQuestion(id)} title={t(id)}>
                    <span>{index + 1}</span>
                    <em>{t(QUEUE_STATUS_LABELS[status])}</em>
                  </button>
                );
              })}
            </div>
            <div className="queueLegend">
              <span><i className="todo" />{t("common.unanswered")}</span>
              <span><i className="correct" />{t("common.correct")}</span>
              <span><i className="wrong" />{t("common.incorrect")}</span>
              <span><i className="skipped" />{t("common.skip")}</span>
              <span><i className="hint" />{t("common.using_hints")}</span>
              <span><i className="check" />{t("practice.check_question")}</span>
              <span><i className="revealed" />{t("common.solution_viewed")}</span>
            </div>
          </aside>
        </section>
      )}

      {activeWorkspace === "generated" && !strictFocus && (
        <section className="generatedLayout">
          <div className="panel">
            <div className="sectionHeader">
              <div>
                <span className="eyebrow">{t(activeBatchLabel)}</span>
                <h2>{modelSettingsState !== "ready" ? t("admin.model_settings_pending_synchronization") : (modelSettings.has_api_key ? t("admin.real_ai_api") : t("common.mock_workflow"))}</h2>
              </div>
              <div className="actionRow">
                <button type="button" onClick={refreshBatch} disabled={!batch?.id}><RefreshCcw size={16} />{t("generation.refresh_batch")}</button>
                <button type="button" onClick={topUpCurrentBatch} disabled={!batch?.id || activeBatchIsMasteryVariant || !batchTopUpEligibleStatus || !batchNeedsTopUp || operationStatus.state === "loading"} title={(activeBatchIsMasteryVariant ? t("generation.similar_question_batches_do_not_use_normal_set_filling") : t("common.only_available_for_ordinary_partial_failed_timed_out_batches"))}><RefreshCcw size={16} />{t("common.fill_remaining")}</button>
                <button type="button" onClick={loadBatchHistory}><Clock3 size={16} />{t("generation.batch_history")}</button>
                <button type="button" onClick={() => copyBatchConfigToForm()} disabled={!batch?.id}><Clipboard size={16} />{t("admin.copy_configuration")}</button>
                <button type="button" className="dangerSoft" onClick={() => cancelBatch()} disabled={!batchCanCancel(batch)}><PauseCircle size={16} />{t("common.interrupt")}</button>
              </div>
            </div>
            {batchHistory.length > 0 && (
              <div className="batchHistory">
                {batchHistory.slice(0, 6).map((row) => {
                  const rowCanCancel = batchCanCancel(row);
                  return (
                    <div key={row.id}>
                      <button type="button" onClick={() => openBatchFromHistory(row)} title={t(row.id)}>
                        <strong>{row.id.slice(0, 8)}</strong>
                        <span>{t(batchLabelFor(row))} · {row.status} · {row.accepted_count}/{row.target_count}</span>
                      </button>
                      <button type="button" onClick={() => syncGeneratedPractice(row.id, row.id === batch?.id ? batchPracticeSessionId : "")} disabled={row.accepted_count === 0}><Play size={14} />{t("practice.practice_this_set")}</button>
                      <button type="button" onClick={() => copyBatchConfigToForm(row)}><Clipboard size={14} />{t("common.copy")}</button>
                      <button type="button" onClick={() => openBatchReport(row.id)}><BarChart3 size={14} />{t("common.reports")}</button>
                      <button type="button" className="dangerSoft" onClick={() => cancelBatch(row)} disabled={!rowCanCancel}><PauseCircle size={14} />{(row.status === "cancelling" ? t("common.interrupting") : t("common.interrupt"))}</button>
                    </div>
                  );
                })}
              </div>
            )}
            <form className="batchForm" onSubmit={startBatchGeneration}>
              <label><span>{t("practice.question_count_9ce8a3c3")}</span><input value={batchRequest.count} onChange={(event) => setBatchRequest({ ...batchRequest, count: event.target.value })} inputMode="numeric" /></label>
              <label><span>{t("common.concurrency")}</span><input value={batchRequest.concurrency} onChange={(event) => setBatchRequest({ ...batchRequest, concurrency: event.target.value })} inputMode="numeric" min={1} max={8} /><small>{t("common.maximum_8_higher_values_are_submitted_as_8")}</small></label>
              <label><span>{t("common.stars_9c55a439")}</span><select value={batchRequest.difficulty_star} onChange={(event) => setBatchRequest({ ...batchRequest, difficulty_star: event.target.value })}>{[1, 2, 3, 4, 5].map((star) => <option key={star} value={star}>{star} {t("common.stars")}</option>)}</select></label>
              {batchConcurrencyWasClamped && <p className="wide batchCancelNote">{t("common.concurrency_capped_at_the_safe_limit")} {requestedBatchConcurrency}{t("common.your_input_of")} {(batchRequest.concurrency || "-")} {t("common.will_not_be_submitted_unchanged")}</p>}
              <label className="wide inlineCheck">
                <input type="checkbox" checked={batchRequest.prefer_reuse_existing_ai} onChange={(event) => setBatchRequest({ ...batchRequest, prefer_reuse_existing_ai: event.target.checked })} />
                <span>{t("generation.reuse_similar_unattempted_ai_questions_first_then_generate_the")}</span>
              </label>
              {renderGenerationAdvancedControls("wide")}
              <div className="generationTagPanel wide">
                <div className="sectionHeader compactHeader">
                  <div>
                    <span className="eyebrow">{t("generation.generation_tags")}</span>
                    <h2>{(generationTargetTags().length ? tf("common.value_tag_constraints", [generationTargetTags().length]) : t("common.choose_library_tags_or_add_custom_tags"))}</h2>
                  </div>
                  <button type="button" onClick={clearSelectedTags} disabled={!selectedTagIds.length}>{t("common.clear_tags")}</button>
                </div>
                <label className="searchBox">
                  <Search size={16} />
                  <input value={generationTagSearch} onChange={(event) => setGenerationTagSearch(event.target.value)} placeholder={t("common.search_concepts_methods_structures_or_descriptions")} />
                </label>
                <div
                  className="tagBasket generationBasket"
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => {
                    event.preventDefault();
                    const tagId = event.dataTransfer.getData("text/plain");
                    if (tagId && !selectedTagIds.includes(tagId)) toggleTag(tagId);
                  }}
                >
                  <Tags size={16} />
                  {selectedTags.length === 0 && <span>{t("common.click_tags_or_drag_them_here")}</span>}
                  {selectedTags.map((tag) => (
                    <button key={tag.id} type="button" onClick={() => removeTag(tag.id)} title={tagExplanation(tag, language)}>
                      {t(tagKindLabel(tag.kind))} · {displayTagLabel(tag, language)}<X size={12} />
                    </button>
                  ))}
                </div>
                <div className="tagKindGuide">
                  {Object.entries(TAG_KIND_META).filter(([kind]) => kind !== "custom").map(([kind, meta]) => (
                    <div key={kind}>
                      <strong>{t(meta.label)}</strong>
                      <span>{t(meta.description)}</span>
                    </div>
                  ))}
                </div>
                <div className="generationTagColumns">
                  <div className="tagTree generationTagTree">
                    {Object.entries(groupedGenerationTags).map(([kind, rows]) => (
                      <details key={kind} open={["primary", "method", "task", "custom"].includes(kind)}>
                        <summary><span>{t(tagKindLabel(kind))}</span><strong>{rows.length}</strong></summary>
                        <p>{t(tagKindDescription(kind))}</p>
                        <div>
                          {rows.slice(0, 30).map((tag) => (
                            <button
                              key={tag.id}
                              draggable
                              className={selectedTagIds.includes(tag.id) ? "tagItem on" : "tagItem"}
                              onDragStart={(event) => event.dataTransfer.setData("text/plain", tag.id)}
                              onClick={() => toggleTag(tag.id)}
                              title={`${t(tagKindLabel(tag.kind))} · ${displayTagLabel(tag, language)}\n${tagExplanation(tag, language)}`}
                              type="button"
                            >
                              <span>{displayTagLabel(tag, language)}</span><em>{tag.question_count}</em>
                            </button>
                          ))}
                        </div>
                      </details>
                    ))}
                  </div>
                  <div className="customTagBox">
                    <h3>{t("common.custom_tags")}</h3>
                    <select value={customTagForm.kind} onChange={(event) => setCustomTagForm({ ...customTagForm, kind: event.target.value })}>
                      {Object.entries(TAG_KIND_META).map(([kind, meta]) => <option key={kind} value={kind}>{t(meta.label)}</option>)}
                    </select>
                    <input value={customTagForm.label} onChange={(event) => setCustomTagForm({ ...customTagForm, label: event.target.value })} placeholder={t("common.tag_name_e_g_counterexample_construction")} />
                    <textarea value={customTagForm.description} onChange={(event) => setCustomTagForm({ ...customTagForm, description: event.target.value })} placeholder={t("practice.describe_the_constraint_e_g_practice_constructing_counterexamples_to")} />
                    <button type="button" onClick={createCustomTag}><Tags size={16} />{t("common.add_and_select")}</button>
                    <label><span>{t("common.temporary_extra_tags")}</span><input value={batchRequest.target_tags} onChange={(event) => setBatchRequest({ ...batchRequest, target_tags: event.target.value })} placeholder={t("common.comma_separated_combined_with_the_tags_above")} /></label>
                  </div>
                </div>
              </div>
              <label className="wide"><span>{t("common.custom_description")}</span><textarea value={batchRequest.custom_description} onChange={(event) => setBatchRequest({ ...batchRequest, custom_description: event.target.value })} placeholder={t("common.e_g_motion_number_theory_composite_figures_avoid_equations")} /></label>
              <label className="wide"><span>{t("common.constraints_ba0934c9")}</span><textarea value={generationConstraintsText} onChange={(event) => { setGenerationConstraintsEdited(true); setBatchRequest({ ...batchRequest, constraints: event.target.value }); }} /></label>
              <p className="wide mutedLine">
                {(batchRequest.prefer_reuse_existing_ai ? t("history.similar_unattempted_ai_questions_of_the_same_star_level") : t("generation.reuse_is_off_each_question_attempts_new_candidate_generation"))}{t("practice.concurrency_counts_question_slots_not_guaranteed_simultaneous_external_requests")}{requestedBatchCount} {t("common.questions_x")} {requestedCandidateCount} {t("common.candidates_30a13ca1")} {totalWorstCaseCandidateCalls} {t("generation.generation_calls_up_to")} {totalWorstCaseReviewerCalls} {t("history.review_calls_theoretical_peak_about")} {peakGeneratorCalls} {t("generation.generated_candidates_per_batch_then_up_to")} {peakReviewerCalls} {t("history.review_requests_actual_peaks_also_depend_on")}{(providerGlobalConcurrencyLimit ? tf("common.global_ai_concurrency_limit_value_estimated_effective_peak_value", [providerGlobalConcurrencyLimit, effectiveProviderPeak]) : t("admin.global_ai_concurrency_and_provider_rate_limits"))}{t("common.label")}</p>
              {activeBatchesForNewRequest().length > 0 && (
                <p className="wide batchCancelNote">{t("common.there_are_currently")}{activeBatchesForNewRequest().length}{t("assistant.unfinished_batches_starting_another_requires_confirmation_interrupt_old_batches")}</p>
              )}
              <button type="submit" disabled={operationStatus.state === "loading"}><BrainCircuit size={16} />{t("common.start_generating_set")}</button>
            </form>

            {batch && (
              <div className="batchStatus">
                <div className="progressCard batchProgress">
                  <div>
                    <strong>{t(activeBatchLabel)} {batch.id.slice(0, 8)}</strong>
                    <span>{batch.status} · {t(batchEtaSummary)}</span>
                  </div>
                  <div
                    className="progressTrack"
                    role="progressbar"
                    aria-label={t(batchProgressStatusText)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={batchProgress}
                  >
                    <span style={{ width: `${batchProgress}%` }} />
                  </div>
                  <span>{batch.accepted_count}/{batch.target_count}</span>
                  <em>{(activeBatchIsMasteryVariant ? tf("common.new_value_running_value_rejected_value_failed_value", [batchGeneratedCount, batchRunningCount, batch.rejected_count, batch.failed_count]) : tf("common.reused_value_new_value_running_value_rejected_value_failed", [batchReusedCount, batchGeneratedCount, batchRunningCount, batch.rejected_count, batch.failed_count]))}</em>
                  <em>{t(batchEtaDetail)}</em>
                  <div className="batchSummaryStrip">
                    <span>{t("common.available")} {batch.accepted_count}</span>
                    <span>{t("common.unfinished")} {batchRemainingTargetCount}</span>
                    <span>{t("common.attempts")} {batchCreatedCount}/{batchMaxAttempts}{(batchAttemptLimitExhausted ? t("common.limit_reached") : "")}</span>
                    <span>{t("common.quality_tier")} {t(batchQualityTier)}</span>
                    {activeBatchIsMasteryVariant ? <span>{t("generation.workflow_one_generation_and_one_batch_review_call_per")}</span> : <span>{t("common.up_to")} {batchRemainingGenerationCalls}+{batchRemainingReviewerCalls} {t("generation.generation_review_calls_remaining")}</span>}
                    <span>{t("common.failure_reasons")}{t(batchFailureSummary)}</span>
                  </div>
                  {batch.status === "cancelling" && <p className="batchCancelNote">{t("practice.interrupting_new_dispatch_has_stopped_waiting_for_in_flight")}</p>}
                  <p className="batchCancelNote">{t("practice.interruption_stops_new_dispatch_in_flight_provider_calls_may")}</p>
                  <details className="batchDetails">
                    <summary>{t("admin.configuration_snapshot")}</summary>
                    <dl>
                      {activeBatchIsMasteryVariant ? (
                        <>
                          <dt>{t("history.similar_question_workflow")}</dt>
                          <dd>{t("common.source_questions")} {metadataString(batchProgressSnapshot.variant_source_question_count, metadataString((batchRequestSnapshot.source_refs as unknown[])?.length, "0"))} {t("practice.per_question")} {metadataString(batchProgressSnapshot.variants_per_question, metadataString(batchRequestSnapshot.variants_per_question, "1"))} {t("common.questions_candidates")} {metadataString(batchRequestSnapshot.candidate_count, "2")} {t("generation.batch_concurrency")} {batch.concurrency}</dd>
                          <dt>{t("common.storage_policy")}</dt>
                          <dd>{t("generation.approved_similar_questions_enter_the_bank_batch_history_practice")}</dd>
                          <dt>{t("admin.model_source")}</dt>
                          <dd>{metadataString(batchRequestSnapshot.batch_provider, t("common.mock_openai_compatible"))}</dd>
                        </>
                      ) : (
                        <>
                          <dt>{t("common.advanced_settings")}</dt>
                          <dd>{t("common.candidates_64af28c7")} {metadataString(batchRequestSnapshot.candidate_count, "3")} {t("common.non_ai_examples_1bc28592")} {metadataString(batchRequestSnapshot.source_example_count, "5")} {t("common.ai_anti_duplicate_examples")} {metadataString(batchRequestSnapshot.ai_negative_example_count, "3")} {t("generation.concurrent_batch_slots")} {batch.concurrency}</dd>
                          <dt>{t("common.reuse_policy")}</dt>
                          <dd>{(batchRequestSnapshot.prefer_reuse_existing_ai === false ? t("generation.require_new_generation") : t("history.prefer_similar_unattempted_ai_questions"))}</dd>
                          <dt>{t("admin.model_source")}</dt>
                          <dd>{metadataString(batchRequestSnapshot.batch_provider, t("common.mock_openai_compatible"))}</dd>
                          <dt>{t("common.scope")}</dt>
                          <dd>{t("common.stars_9c55a439")} {metadataString(batchRequestSnapshot.difficulty_star, t("common.unspecified"))} {t("common.tags_445e6a4d")} {(metadataStringArray(batchRequestSnapshot.target_tags).join("、") || t("common.no_specific_tags"))}</dd>
                        </>
                      )}
                    </dl>
                  </details>
                  <span className="srOnly" role="status" aria-live="polite" aria-atomic="true">{t(batchProgressStatusText)}</span>
                  {batchCallBudget.planned_total_calls !== undefined && (
                    <em>{t("common.budget_snapshot_up_to")} {String(batchCallBudget.planned_generation_calls)} {t("generation.generation_33612ddf")} {String(batchCallBudget.planned_reviewer_calls)} {t("history.review_calls_limit")} {String(batchCallBudgetLimits.generation_calls || t("common.unlimited"))}/{String(batchCallBudgetLimits.reviewer_calls || t("common.unlimited"))}</em>
                  )}
                  {Boolean(batchTopUpPlan.schema_version) && (
                    <div className="batchBlueprintSummary">
                      <span>{t("common.fill")}{(batchTopUpPlan.eligible === false ? String(batchTopUpPlan.reason || t("common.unavailable")) : t("common.fill_requested"))}</span>
                      <span>{t("common.needed")} {metadataString(batchTopUpPlan.needed_accept_count, "0")} {t("common.questions")}</span>
                      <span>{t("common.remaining_attempts")} {metadataString(batchTopUpPlan.remaining_attempts, "0")}</span>
                      {batchTopUpCallBudget.planned_total_calls !== undefined && <span>{t("common.fill_budget")} {String(batchTopUpCallBudget.planned_generation_calls)}+{String(batchTopUpCallBudget.planned_reviewer_calls)}</span>}
                    </div>
                  )}
                  {Boolean(batchBlueprint.schema_version) && (
                    <div className="batchBlueprintSummary">
                      <span>{t("common.blueprint")}{t(blueprintCurveLabel(batchBlueprint.difficulty_curve))}</span>
                      <span>{(batchBlueprintSlots.length || metadataString(batchBlueprint.target_count, "0"))} {t("common.slots")}</span>
                      <span>{t("common.stars_9c55a439")} {t(distributionText(batchBlueprint.star_distribution))}</span>
                      {Boolean(batchBlueprintReview.schema_version) && <span>{t("common.coverage")} {metadataString(batchBlueprintReview.accepted_slot_count, "0")}/{metadataString(batchBlueprintReview.planned_slot_count, "0")} {t("common.score")} {metadataString(batchBlueprintReview.coverage_score)}</span>}
                      {batchMissingBlueprintSlots.length > 0 && <span>{t("common.gaps")} {batchMissingBlueprintSlots.slice(0, 4).join(", ")}</span>}
                    </div>
                  )}
                </div>
                <div className="batchItemGrid">
                  {batch.items.map((item) => {
                    const review = asRecord(item.similarity_review);
                    const slot = asRecord(review.blueprint_slot);
                    return (
                      <div key={item.id} className={`batchItem ${item.status}`}>
                        <strong>#{item.item_index}</strong>
                        <span>{item.status}</span>
                        {Boolean(review.blueprint_slot_id) && <small>{String(review.blueprint_slot_id)} · {t(blueprintPhaseLabel(slot.phase))} {t("practice.single_numerical_answer")} {metadataString(slot.answer_type_preference)}</small>}
                        {review.source_type === "mastery_variant" && <small>{t("practice.original_question")} {metadataString(review.base_question_id, "-").slice(0, 8)} {t("history.similar_question")} {metadataString(review.variant_index, "0")}</small>}
                        <small>{t(batchItemSourceLabel(item.source_type))}</small>
                        {batchItemReuseReason(item.reuse_status) && <small>{t(batchItemReuseReason(item.reuse_status))}</small>}
                        <em>{((item.question_id || item.rejection_reason) || t("common.waiting"))}</em>
                        {item.similarity_review?.risk && <small>{t("common.similarity")} {item.similarity_review.risk}</small>}
                        {item.duration_ms != null && <small>{t(formatMs(item.duration_ms))}</small>}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          <div className="panel generatedPracticePanel">
            <div className="sectionHeader">
              <div>
                <span className="eyebrow">{(activeBatchIsMasteryVariant ? t("history.similar_question_practice") : t("practice.practice_while_generating"))}</span>
                <h2>{(batchPracticeSessionId ? `${(activeBatchIsMasteryVariant ? t("history.similar_question_session") : t("practice.set_practice_session"))} ${batchPracticeSessionId.slice(0, 8)}` : tf("common.value_questions_available", [batchAcceptedItems.length]))}</h2>
              </div>
              <button type="button" onClick={() => syncGeneratedPractice()} disabled={!batch?.id || batchAcceptedItems.length === 0}><Play size={16} />{t("common.sync_and_start")}</button>
              {batch?.id && <button type="button" onClick={() => openBatchReport(batch.id)}><BarChart3 size={16} />{t("reports.report_for_this_set")}</button>}
            </div>
            {!batchAcceptedItems.length && (
              <div className="emptyState"><Clock3 size={18} /><span>{t("generation.approved_batch_questions_appear_here_and_can_be_answered")}</span></div>
            )}
            {batchPracticeSessionId && (
              <>
                <article className="problemSurface">
                  <div className="problemMeta">
                    <span>{(activeBatchIsMasteryVariant ? t("history.similar_question_practice") : t("practice.question_set_practice"))}</span>
                    <span>{(sessionState?.answered_count || 0)}/{(sessionState?.question_ids.length || 0)}</span>
                  </div>
                  <MathContent source={currentStem} />
                  {currentAnswerGuide && (
                    <div className="answerFormatPanel">
                      <strong>{t(currentAnswerGuide.title)}</strong>
                      {currentAnswerGuide.lines.map((line) => <span key={line}>{t(line)}</span>)}
                      {currentAnswerGuide.examples.length > 0 && <em>{t("practice.answer_format_not_the_answer")}{t(currentAnswerGuide.examples.join(" / "))}</em>}
                    </div>
                  )}
                </article>
                <form onSubmit={submit} className="answerDock">
                  <input value={answer} onChange={(event) => setAnswer(event.target.value)} placeholder={t("practice.enter_answer")} disabled={answerLocked} />
                  <button type="submit" disabled={answerLocked || operationStatus.state === "loading"} title={t("common.ctrl_command_enter_to_submit")}><Send size={18} />{t("common.submit")}</button>
                  <textarea value={thought} onChange={(event) => setThought(event.target.value)} placeholder={t("common.reasoning_optional")} disabled={answerLocked} />
                  {questionDraftStatusText && <span className={`draftStatus ${questionDraftStatus.includes("失败") ? "error" : ""}`}>{t(questionDraftStatusText)}</span>}
                </form>
                {answerLocked && (
                  <div className="lockedNotice">
                    <AlertTriangle size={16} />
                    <span>{t("practice.answer_viewed_this_question_is_locked_against_editing_or")}</span>
                  </div>
                )}
                {result && (
                  <div className={`resultSummary ${result.is_correct ? "correct" : "wrong"}`}>
                    <strong>{(result.is_correct ? t("practice.correct_answer_06a38c0f") : t("practice.answer_not_accepted"))}</strong>
                    <span>{t("common.effective_errors")} {String(result.effective_error_count ?? 0)} {t("common.times_172fb7e6")}</span>
                    {typeof result.reason === "string" && <span>{t(displayErrorType(result.reason))}</span>}
                    <button type="button" onClick={requestAnswerJudge} disabled={operationStatus.state === "loading"} title={t("history.review_this_answer_with_the_answer_review_model")}><BrainCircuit size={16} />{t("history.ai_review")}</button>
                  </div>
                )}
                {answerJudgeResult && (
                  <div className={`judgePanel ${answerJudgeResult.verdict}`}>
                    <div>
                      <strong>{(answerJudgeResult.verdict === "correct" ? t("history.review_accepted") : (answerJudgeResult.verdict === "incorrect" ? t("history.review_not_passed") : t("history.review_uncertain")))}</strong>
                      <span>{answerJudgeResult.provider} · {answerJudgeResult.model} {t("common.confidence")} {Math.round(answerJudgeResult.confidence * 100)}%</span>
                    </div>
                    {answerJudgeResult.student_feedback && <p>{answerJudgeResult.student_feedback}</p>}
                    {answerJudgeResult.analysis && <p>{answerJudgeResult.analysis}</p>}
                    {answerJudgeResult.missing_parts.length > 0 && <span>{t("common.missing_463071aa")}{answerJudgeResult.missing_parts.join("、")}</span>}
                    {answerJudgeResult.extra_parts.length > 0 && <span>{t("common.extra")}{answerJudgeResult.extra_parts.join("、")}</span>}
                    {answerJudgeResult.format_issue && <span>{t("common.format")}{answerJudgeResult.format_issue}</span>}
                    {answerJudgeResult.error && <span className="errorText">{t(answerJudgeResult.error)}</span>}
                  </div>
                )}
                {!strictFocus && !currentQuestionIsHint && currentHintIntervention && currentHintQuestionId && (
                  <div className="practiceActions compactActions">
                    <button type="button" onClick={() => startHintIntervention(currentHintIntervention)}><HelpCircle size={16} />{t("practice.try_hint_question")}</button>
                  </div>
                )}
                {!strictFocus && currentQuestionIsHint && (
                  <div className="practiceActions compactActions">
                    <button type="button" onClick={() => completeHintAndReturnOriginal(currentHintIntervention)}><CheckCircle2 size={16} />{t("common.complete_and_return")}</button>
                    <button type="button" onClick={() => returnToOriginalQuestion(currentHintIntervention)}><ArrowLeft size={16} />{t("common.return_to_original")}</button>
                  </div>
                )}
                {!strictFocus && currentQuestionIsMasteryCheck && (
                  <div className="practiceActions compactActions">
                    <button type="button" onClick={() => returnToOriginalQuestion(currentMasteryCheck)}><ArrowLeft size={16} />{t("common.return_to_original")}</button>
                  </div>
                )}
                {!strictFocus && !currentQuestionIsMasteryCheck && (!currentMasteryCheck || currentMasteryFailed || currentMasteryCompleted) && (
                  <div className="practiceActions compactActions">
                    <label className="compactSelect"><span>{t("history.similar_questions")}</span><select value={masteryCheckCount} onChange={(event) => setMasteryCheckCount(event.target.value)} disabled={operationStatus.state === "loading" || breakDue}>
                      {[1, 2, 3, 4, 5].map((value) => <option key={value} value={String(value)}>{value} {t("common.questions_00a9d5bd")}</option>)}
                    </select></label>
                    <button type="button" onClick={requestMasteryCheck} disabled={operationStatus.state === "loading" || breakDue} title={t("generation.generate_checks_of_the_same_core_skill_different_surface")}><BrainCircuit size={16} />{(currentMasteryFailed ? t("history.regenerate_similar_questions") : (currentMasteryCompleted ? t("generation.generate_more_similar_questions") : t("history.similar_questions_check")))}</button>
                  </div>
                )}
                {!strictFocus && !currentQuestionIsMasteryCheck && currentMasteryCheck && !currentMasteryReady && !currentMasteryFailed && !currentMasteryCompleted && (
                  <div className="practiceActions compactActions">
                    <button type="button" disabled><BrainCircuit size={16} />{t("history.generating_similar_questions")}</button>
                    <button type="button" onClick={refreshMasteryCheckStatus} disabled={operationStatus.state === "loading"}><RefreshCcw size={16} />{t("common.refresh_status")}</button>
                  </div>
                )}
                {!strictFocus && !currentQuestionIsMasteryCheck && currentMasteryCheck && currentMasteryReady && (
                  <div className="practiceActions compactActions">
                    <button type="button" onClick={() => startMasteryCheck(currentMasteryCheck)}><CheckCircle2 size={16} />{t("history.start_similar_questions")}</button>
                  </div>
                )}
                {renderQuestionSupportPanel()}
                <div className="helpPanel">
                  <div className="helpHead">
                    <div>
                      <span className="eyebrow">{t("common.answers_explanations")}</span>
                      <strong>{(t(currentHelpState?.current_help?.title) || t("practice.view_the_answer_when_needed"))}</strong>
                    </div>
                    <div className="helpActions">
                      <button type="button" onClick={revealNextHelp} disabled={!currentHelpState?.can_reveal_next}><HelpCircle size={16} />{t("common.unlock_next_level")}</button>
                      <button type="button" className="dangerSoft" onClick={giveUpQuestion}>{t("practice.view_answer")}</button>
                    </div>
                  </div>
                  <div className="helpBody">
                    {((currentHelpState?.reached_level ?? 0) === 0
                      ? [t("help.check_all_conditions"), t("help.check_units"), t("help.check_whole_or_part")]
                      : currentHelpState?.current_help?.content || []).map((line, index) => <p key={index}>{line}</p>)}
                    {currentHelpState?.current_help?.final_answer && <p><strong>{t("practice.answer")}</strong>{currentHelpState.current_help.final_answer}</p>}
                  </div>
                  {(answerLocked && renderSolutionExplanationPanel())}
                </div>
                {activeSessionQueue.length > 1 && (
                  <div className="queueNav">
                    <button type="button" onClick={() => moveSelectedQuestion(-1)} disabled={activeSessionQuestionIndex <= 0}>{t("practice.previous_question")}</button>
                    <span>{(activeSessionQuestionIndex >= 0 ? `${activeSessionQuestionIndex + 1}/${activeSessionQueue.length}` : `0/${activeSessionQueue.length}`)}</span>
                    <button type="button" onClick={() => moveSelectedQuestion(1)} disabled={activeSessionQuestionIndex < 0 || activeSessionQuestionIndex >= activeSessionQueue.length - 1}>{t("practice.next_question")}</button>
                  </div>
                )}
                <div className="queueGrid generatedQueue">
                  {sessionQuestions.map((id, index) => {
                    const status = questionStatus(id);
                    return (
                      <button key={id} className={`queueCell ${id === selectedQuestion ? "on" : ""} ${status}`} onClick={() => setSelectedQuestion(id)} title={t(id)}>
                        <span>{index + 1}</span>
                        <em>{t(QUEUE_STATUS_LABELS[status])}</em>
                      </button>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        </section>
      )}

      {activeWorkspace === "history" && !strictFocus && (
        <section className="historyLayout">
          <div className="panel">
            <div className="sectionHeader">
              <div>
                <span className="eyebrow">{t("history.history_groups")}</span>
                <h2>{historyGroups.length} {t("common.groups")}</h2>
              </div>
              <button type="button" onClick={() => loadHistoryGroups()} disabled={operationStatus.state === "loading"}><RefreshCcw size={16} />{t("common.refresh")}</button>
            </div>
            <form className="filterGrid historyFilters" onSubmit={applyHistoryFilters}>
              <label><span>{t("common.start_date")}</span><input type="date" value={historyFilters.date_from} onChange={(event) => setHistoryFilters({ ...historyFilters, date_from: event.target.value })} /></label>
              <label><span>{t("common.end_date")}</span><input type="date" value={historyFilters.date_to} onChange={(event) => setHistoryFilters({ ...historyFilters, date_to: event.target.value })} /></label>
              <label><span>{t("common.group_type")}</span><select value={historyFilters.group_type} onChange={(event) => setHistoryFilters({ ...historyFilters, group_type: event.target.value })}>
                <option value="">{t("common.all_group_types")}</option>
                <option value="practice">{t("practice.regular_practice")}</option>
                <option value="test_run">{t("practice.strict_test")}</option>
                <option value="generation_batch">{t("generation.generate_sets")}</option>
                <option value="mastery_variant_batch">{t("generation.batch_similar_questions")}</option>
                <option value="generated_practice">{t("practice.question_set_practice")}</option>
                <option value="review_practice">{t("history.history_review")}</option>
              </select></label>
              <label><span>{t("common.source")}</span><select value={historyFilters.source_name} onChange={(event) => setHistoryFilters({ ...historyFilters, source_name: event.target.value })}>
                <option value="">{t("common.all_sources")}</option>
                {sourceOptions.map((source) => <option key={source} value={source}>{t(compactSource(source))}</option>)}
              </select></label>
              <label><span>{t("common.stars_9c55a439")}</span><select value={historyFilters.difficulty_star} onChange={(event) => setHistoryFilters({ ...historyFilters, difficulty_star: event.target.value })}>
                <option value="">{t("common.all_star_levels")}</option>
                {[1, 2, 3, 4, 5].map((star) => <option key={star} value={star}>{star} {t("common.stars")}</option>)}
              </select></label>
              <label><span>{t("common.tags")}</span><select value={historyFilters.tag_id} onChange={(event) => setHistoryFilters({ ...historyFilters, tag_id: event.target.value })}>
                <option value="">{t("common.all_tags")}</option>
                {tags.slice(0, 240).map((tag) => <option key={tag.id} value={tag.id}>{t(tagKindLabel(tag.kind))} · {displayTagLabel(tag, language)}</option>)}
              </select></label>
              <label className="checkboxLine"><input type="checkbox" checked={historyFilters.weak_only} onChange={(event) => setHistoryFilters({ ...historyFilters, weak_only: event.target.checked })} /><span>{t("common.weak_questions_only")}</span></label>
              <button type="submit"><Search size={16} />{t("common.apply")}</button>
            </form>

            {!historyGroups.length && (
              <div className="emptyState">
                <Clock3 size={18} />
                <span>{t("history.no_history_to_review_yet_complete_practice_a_strict")}</span>
                <button type="button" onClick={createPractice}><Play size={16} />{t("practice.start_practice")}</button>
              </div>
            )}

            <div className="historyGroupList">
              {historyGroups.map((group) => (
                <button
                  key={group.id}
                  type="button"
                  className={`historyGroupCard ${selectedHistoryGroupId === group.id ? "on" : ""} ${group.mastery_variant_done_count ? "hasMasteryVariantDone" : ""}`}
                  onClick={() => openHistoryGroup(group)}
                  aria-current={selectedHistoryGroupId === group.id ? "true" : undefined}
                  aria-label={tf("common.value_value_value_value_questions_first_accuracy_value_final", [t(historyGroupLabel(group.group_type)), displayAutoTitle(group.title, language), group.answered_count, group.question_count, percent(group.first_accuracy), percent(group.final_accuracy), t(historyPerformanceDistributionText(group.performance_counts))])}
                >
                  <span>{t(historyGroupLabel(group.group_type))} · {(group.status || "-")}</span>
                  <strong>{displayAutoTitle(group.title, language)}</strong>
                  <em>{group.answered_count}/{group.question_count} {t("common.questions_first")} {percent(group.first_accuracy)} {t("common.final")} {percent(group.final_accuracy)} {t("history.similar_question_check")} {(group.mastery_variant_done_count || 0)}</em>
                  <div className="historyStatusBar" aria-hidden="true">
                    {(["green", "yellow", "red", "gray"] as HistoryPerformance[]).map((ID) => (
                      <i key={ID} className={ID} style={{ "--weight": Math.max(1, group.performance_counts?.[ID] || 0) } as CSSProperties & Record<"--weight", number>} title={`${t(historyPerformanceLabel(ID))} ${(group.performance_counts?.[ID] || 0)}`} />
                    ))}
                    {Boolean(group.mastery_variant_done_count) && (
                      <i className="masteryVariantDone" style={{ "--weight": Math.max(1, group.mastery_variant_done_count || 0) } as CSSProperties & Record<"--weight", number>} title={`${t(MASTERY_VARIANT_MARKER_LABEL)} ${(group.mastery_variant_done_count || 0)}`} />
                    )}
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="sectionHeader">
              <div>
                <span className="eyebrow">{t("practice.question_matrix")}</span>
                <h2>{(historyDetail ? tf("common.value_questions_weak_value", [historyDetail.question_count, historyDetail.weak_count]) : t("history.select_a_history_group"))}</h2>
              </div>
              <div className="actionRow">
                <button type="button" onClick={() => historyDetail && openHistoryReport(historyDetail)} disabled={!historyDetail}><BarChart3 size={16} />{t("reports.view_original_report")}</button>
              </div>
            </div>

            {historyDetail && (
              <>
                <div className="historySummary">
                  <div><span>{t("practice.first_answer_accuracy")}</span><strong>{percent(historyDetail.first_accuracy)}</strong></div>
                  <div><span>{t("common.final_accuracy")}</span><strong>{percent(historyDetail.final_accuracy)}</strong></div>
                  <div><span>{t("common.average_attempts")}</span><strong>{(historyDetail.avg_attempts == null ? "-" : historyDetail.avg_attempts.toFixed(1))}</strong></div>
                  <div><span>{t("practice.view_answer")}</span><strong>{historyDetail.reveal_count}</strong></div>
                  <div><span>{t("common.hints")}</span><strong>{historyDetail.hint_count}</strong></div>
                  <div><span>{t("common.explanations")}</span><strong>{historyDetail.explanation_count}</strong></div>
                </div>
                {Boolean(historyDetail.duplicate_question_ids?.length) && (
                  <div className="selectionWarnings">
                    <AlertTriangle size={16} />
                    <span>{t("history.this_group_has_duplicate_question_ids_review_sessions_deduplicate")}</span>
                  </div>
                )}
                <div className="historyVariantBatchPanel">
                  <div>
                    <strong>{t("generation.batch_similar_questions_checks")}</strong>
                    <span>{t("history.click_question_numbers_to_add_them_to_a_cross")}</span>
                    <span>{t("generation.if_generation_gets_stuck_interrupt_it_on_generate_sets")}</span>
                  </div>
                  <div className="historyVariantControls">
                    <label><span>{t("generation.generate_per_question")}</span><select value={historyVariantCountPerQuestion} onChange={(event) => setHistoryVariantCountPerQuestion(event.target.value)}>
                      <option value="1">{t("practice.1_question")}</option>
                      <option value="2">{t("common.2_questions")}</option>
                      <option value="3">{t("common.3_questions")}</option>
                    </select></label>
                    <button type="button" onClick={startHistoryMasteryVariantBatch} disabled={operationStatus.state === "loading" || historyVariantSelectedCount === 0}><BrainCircuit size={16} />{t("generation.generate_similar_questions")} {(historyVariantTargetCount || "")}</button>
                    <button type="button" onClick={recoverStaleState} disabled={operationStatus.state === "loading"}><AlertTriangle size={16} />{t("common.repair_stuck_states")}</button>
                    <button type="button" onClick={clearHistoryVariantSelection} disabled={historyVariantSelectedCount === 0}>{t("common.clear_basket")}</button>
                  </div>
                  <div className="historyVariantSelectionList">
                    {historyVariantSelectedRows.length ? historyVariantSelectedRows.slice(0, 18).map((row) => (
                      <span key={row.key} className={row.mastery_variant_done ? "done" : ""} title={`${row.question_id}\n${row.stem_preview}`}>
                        <strong>#{row.position}</strong>
                        <em>{t(historyGroupLabel(row.group_type))} · {t(row.performance_label)}</em>
                        <button type="button" onClick={() => removeHistoryVariantSelection(row.key)} aria-label={tf("practice.remove_question_value", [row.position])}><X size={12} /></button>
                      </span>
                    )) : <p>{t("history.no_questions_selected_clicking_a_number_selects_it_for")}</p>}
                    {historyVariantSelectedRows.length > 18 && <p>{t("common.another")} {historyVariantSelectedRows.length - 18} {t("common.selected_questions_are_not_expanded")}</p>}
                  </div>
                </div>
                <div className="historyQuestionGrid" aria-label={t("history.history_question_matrix")}>
                  {historyDetail.items.map((item) => {
                    const isSelected = selectedHistoryQuestionIds.includes(item.question_id);
                    const selectionKey = historyVariantSelectionKey(historyDetail.id, item);
                    const isVariantSelected = Boolean(historyVariantSelection[selectionKey]);
                    const itemLabel = historyItemAriaLabel(item, isSelected, language);
                    return (
                      <button
                        key={`${item.position}-${item.question_id}`}
                        type="button"
                        className={`historyQuestionCell ${item.performance} ${isSelected ? "on" : ""} ${isVariantSelected ? "variantSelected" : ""} ${item.mastery_variant_done ? "masteryVariantDone" : ""}`}
                        onClick={() => toggleHistoryQuestion(item)}
                        title={`${item.question_id}\n${itemLabel}${(item.mastery_variant_done ? `\n${t(MASTERY_VARIANT_MARKER_LABEL)}` : "")}`}
                        aria-label={itemLabel}
                        aria-pressed={isSelected}
                      >
                        <strong>{item.position}</strong>
                        <span>{t(item.performance_label)}</span>
                        <em>{item.attempt_count} {t("common.times_172fb7e6")}</em>
                        {item.mastery_variant_done && <b>{t(MASTERY_VARIANT_MARKER_LABEL)}</b>}
                        {isVariantSelected && <b>{t("history.added_to_similar_questions")}</b>}
                      </button>
                    );
                  })}
                </div>
                <div className="historyLegend">
                  {(["green", "yellow", "red", "gray"] as HistoryPerformance[]).map((ID) => (
                    <span key={ID} className={ID}><i />{t(historyPerformanceLabel(ID))}</span>
                  ))}
                  <span className="masteryVariantDone"><i />{t(MASTERY_VARIANT_MARKER_LABEL)}</span>
                </div>
                <div className="actionRow historyActions">
                  <button type="button" onClick={() => createReviewSession("all")} disabled={operationStatus.state === "loading"}><Play size={16} />{t("common.retry_whole_group")}</button>
                  <button type="button" onClick={() => createReviewSession("weak_only")} disabled={operationStatus.state === "loading" || historyDetail.weak_count === 0}><AlertTriangle size={16} />{t("common.retry_weak_questions")}</button>
                  <button type="button" onClick={() => createReviewSession("wrong_only")} disabled={operationStatus.state === "loading" || (historyDetail.performance_counts?.red || 0) === 0}>{t("common.retry_red_questions")}</button>
                  <button type="button" onClick={() => createReviewSession("selected")} disabled={operationStatus.state === "loading" || historySelectedCount === 0}>{t("common.retry_selected_questions")} {(historySelectedCount || "")}</button>
                  <button type="button" onClick={startHistoryMasteryVariantBatch} disabled={operationStatus.state === "loading" || historyVariantSelectedCount === 0}><BrainCircuit size={16} />{t("generation.generate_selected_variants")} {(historyVariantTargetCount || "")}</button>
                </div>
              </>
            )}
          </div>

          <div className="panel historyPreviewPanel">
            <div className="sectionHeader compactHeader">
              <div>
                <span className="eyebrow">{t("history.answer_history")}</span>
                <h2>{(historyFocusedItem ? tf("practice.question_value", [historyFocusedItem.position]) : t("practice.no_question_selected"))}</h2>
              </div>
              {historyFocusedItem?.question_id && (
                <button type="button" onClick={() => { setSelectedQuestion(historyFocusedItem.question_id); setActiveWorkspace("bank"); }}>
                  <BookOpen size={16} />{t("common.bank_preview")}</button>
              )}
            </div>
            {historyFocusedItem ? (
              <>
                <article className="historyItemPreview">
                  <div className="problemMeta">
                    <span>{historyFocusedItem.question_id}</span>
                    <span>{(historyFocusedItem.source_name ? compactSource(historyFocusedItem.source_name) : "-")} · {(historyFocusedItem.difficulty_star || "-")} {t("common.stars")}</span>
                  </div>
                  <MathContent source={historyFocusedItem.stem_preview} />
                </article>
                <div className="historyMetaGrid">
                  <div><span>{t("common.status")}</span><strong>{t(historyFocusedItem.performance_label)}</strong></div>
                  <div><span>{t("practice.first_answer")}</span><strong>{(historyFocusedItem.first_correct == null ? "-" : (historyFocusedItem.first_correct ? t("common.correct") : t("common.incorrect")))}</strong></div>
                  <div><span>{t("practice.final_answer")}</span><strong>{(historyFocusedItem.final_correct == null ? "-" : (historyFocusedItem.final_correct ? t("common.correct") : t("common.incorrect")))}</strong></div>
                  <div><span>{t("common.attempts_885bfce5")}</span><strong>{historyFocusedItem.attempt_count}</strong></div>
                  <div><span>{t("common.effective_errors")}</span><strong>{historyFocusedItem.effective_wrong_count}</strong></div>
                  <div><span>{t("common.last_answered_28567feb")}</span><strong>{(formatDateTime(historyFocusedItem.last_answered_at) || "-")}</strong></div>
                  <div><span>{t("common.error_type")}</span><strong>{(historyFocusedItem.error_type ? t(displayErrorType(historyFocusedItem.error_type)) : "-")}</strong></div>
                  <div><span>{t("common.hints_answers_explanations")}</span><strong>{t(historyAidText(historyFocusedItem))}</strong></div>
                  <div><span>{t("practice.question_concerns")}</span><strong>{historyFocusedItem.question_support_count}</strong></div>
                  <div><span>{t("history.similar_question_verification")}</span><strong>{(historyFocusedItem.mastery_variant_done ? t(MASTERY_VARIANT_MARKER_LABEL) : tf("common.value_generated_value_attempted", [(historyFocusedItem.mastery_variant_generated_count || 0), (historyFocusedItem.mastery_variant_attempted_count || 0)]))}</strong></div>
                </div>
                <div className="tagBasket previewTags">
                  {historyFocusedItem.tags.length ? historyFocusedItem.tags.map((tag) => <span key={tag.id}>{t(tagKindLabel(tag.kind))} · {tag.label}</span>) : <span>{t("common.no_tags")}</span>}
                </div>
                {!historyFocusedItem.available && (
                  <div className="emptyState">
                    <AlertTriangle size={18} />
                    <span>{t("history.this_question_is_deleted_under_review_or_unavailable_and")}</span>
                  </div>
                )}
              </>
            ) : (
              <div className="emptyState">
                <Clock3 size={18} />
                <span>{t("history.select_a_history_group_and_question_number_to_inspect")}</span>
              </div>
            )}
          </div>
        </section>
      )}

      {activeWorkspace === "bank" && !strictFocus && (
        <section className="workspaceLayout">
          <div className="panel">
            <div className="sectionHeader">
              <div>
                <span className="eyebrow">{t("common.bank_filters")}</span>
                <h2>{t("common.tags_filters")}</h2>
              </div>
              <div className="actionRow">
                <button onClick={importData} disabled={operationStatus.state === "loading"}><Database size={16} />{t("admin.import_bundled_json")}</button>
                <button type="button" onClick={exportQuestionBank} disabled={operationStatus.state === "loading"}><Download size={16} />{t("admin.export_bank_package")}</button>
                <button type="button" onClick={() => chooseQuestionBankImport(true)} disabled={operationStatus.state === "loading"}><ShieldCheck size={16} />{t("common.validate_bank_package")}</button>
                <button type="button" onClick={() => chooseQuestionBankImport(false)} disabled={operationStatus.state === "loading"}><Upload size={16} />{t("admin.import_bank_package")}</button>
              </div>
            </div>
            <input ref={questionBankImportInputRef} type="file" accept="application/json,.json" className="hiddenFileInput" onChange={(event) => importQuestionBankFile(event.target.files?.[0] || null)} />
            <form className="filterGrid" onSubmit={applyQuestionFilters}>
              <label><span>{t("common.keyword")}</span><input value={questionFilters.q} onChange={(event) => setQuestionFilters({ ...questionFilters, q: event.target.value })} placeholder={t("practice.id_question_text_source_section_answer_type")} /></label>
              <select value={questionFilters.source_name} onChange={(event) => setQuestionFilters({ ...questionFilters, source_name: event.target.value })}>
                <option value="">{t("common.all_sources")}</option>
                {sourceOptions.map((source) => <option key={source} value={source}>{t(compactSource(source))}</option>)}
              </select>
              <select value={questionFilters.difficulty_star} onChange={(event) => setQuestionFilters({ ...questionFilters, difficulty_star: event.target.value })}>
                <option value="">{t("common.all_star_levels")}</option>
                {[1, 2, 3, 4, 5].map((star) => <option key={star} value={star}>{star} {t("common.stars")}</option>)}
              </select>
              <select value={practiceExcludeDone ? "exclude" : "allow"} onChange={(event) => setPracticeExcludeDone(event.target.value === "exclude")}>
                <option value="exclude">{t("practice.practice_excludes_attempted")}</option>
                <option value="allow">{t("practice.practice_includes_attempted")}</option>
              </select>
              <select value={questionFilters.premium} onChange={(event) => setQuestionFilters({ ...questionFilters, premium: event.target.value })}>
                <option value="">{t("common.any_premium_rating")}</option>
                <option value="true">{t("common.premium")}</option>
                <option value="false">{t("common.non_premium")}</option>
              </select>
              <select value={questionFilters.status} onChange={(event) => setQuestionFilters({ ...questionFilters, status: event.target.value })}>
                <option value="verified">{t("practice.available_for_practice")}</option>
                <option value="">{t("common.all_statuses")}</option>
                <option value="needs_review">{t("history.needs_review_c6616472")}</option>
              </select>
              <button type="submit"><Search size={16} />{t("common.apply")}</button>
            </form>

            <div className="savedFilterPanel">
              <div className="savedFilterControls">
                <label>
                  <span>{t("common.save_filters")}</span>
                  <select value={selectedSavedQuestionFilterId} onChange={(event) => setSelectedSavedQuestionFilterId(event.target.value)}>
                    <option value="">{t("common.select_saved_filters")}</option>
                    {savedQuestionFilters.map((preset) => (
                      <option key={preset.id} value={preset.id}>{preset.name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>{t("common.new_name")}</span>
                  <input value={savedQuestionFilterName} onChange={(event) => setSavedQuestionFilterName(event.target.value)} placeholder={t(questionFilterPresetName())} />
                </label>
                <button type="button" onClick={saveQuestionFilterPreset}><Bookmark size={16} />{t("common.save")}</button>
                <button type="button" onClick={applySavedQuestionFilterPreset} disabled={!selectedSavedQuestionFilterId}><Search size={16} />{t("common.apply_filters")}</button>
                <button type="button" className="dangerSoft" onClick={deleteSavedQuestionFilterPreset} disabled={!selectedSavedQuestionFilterId}><Trash2 size={16} />{t("common.delete_preset")}</button>
              </div>
              <p>{t("common.saves_bank_filters_in_this_browser_only_keywords_source")}</p>
            </div>

            <label className="searchBox">
              <Search size={16} />
              <input value={tagSearch} onChange={(event) => setTagSearch(event.target.value)} placeholder={t("common.search_tags")} />
            </label>
            <div
              className="tagBasket"
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                const tagId = event.dataTransfer.getData("text/plain");
                if (tagId && !selectedTagIds.includes(tagId)) toggleTag(tagId);
              }}
            >
              <Tags size={16} />
              {selectedTags.length === 0 && <span>{t("common.click_tags_to_filter")}</span>}
              {selectedTags.map((tag) => (
                <button key={tag.id} type="button" onClick={() => removeTag(tag.id)}>{displayTagLabel(tag, language)}<X size={12} /></button>
              ))}
            </div>
            <div className="tagTree">
              {Object.entries(groupedTags).map(([kind, rows]) => (
                <details key={kind} open={["primary", "method", "task"].includes(kind)}>
                  <summary><span>{t(tagKindLabel(kind))}</span><strong>{rows.length}</strong></summary>
                  <div>
                    {rows.slice(0, 24).map((tag) => (
                      <button
                        key={tag.id}
                        draggable
                        className={selectedTagIds.includes(tag.id) ? "tagItem on" : "tagItem"}
                        onDragStart={(event) => event.dataTransfer.setData("text/plain", tag.id)}
                        onClick={() => toggleTag(tag.id)}
                        title={`${t(tagKindLabel(tag.kind))} · ${displayTagLabel(tag, language)}\n${tagExplanation(tag, language)}`}
                      >
                        <span>{displayTagLabel(tag, language)}</span><em>{tag.question_count}</em>
                      </button>
                    ))}
                  </div>
                </details>
              ))}
            </div>
          </div>

          <div className="panel bankListPanel">
            <div className="sectionHeader">
              <div>
                <span className="eyebrow">{t("practice.question_list")}</span>
                <h2>{(questions.length ? `${questionOffset + 1}-${questionOffset + questions.length}` : "0")} {t("common.results")}</h2>
              </div>
              <div className="actionRow">
                <button type="button" onClick={() => changeQuestionPage("prev")} disabled={operationStatus.state === "loading" || questionOffset === 0}>{t("common.previous_page")}</button>
                <button type="button" onClick={() => changeQuestionPage("next")} disabled={operationStatus.state === "loading" || questions.length < QUESTION_PAGE_SIZE}>{t("common.next_page")}</button>
                <button onClick={createPractice}><Play size={16} />{t("practice.create_practice_from_filters")}</button>
              </div>
            </div>
            <div className="bankBulkBar">
              <span>{t("common.selected_7ecf1cde")} {selectedBankQuestionIds.length} {t("common.questions_00a9d5bd")}{(visibleSelectedQuestionCount ? tf("common.on_this_page_value", [visibleSelectedQuestionCount]) : "")}</span>
              <button type="button" onClick={selectVisibleBankQuestions} disabled={!questions.length || allVisibleQuestionsSelected}><CheckCircle2 size={16} />{t("common.select_current_page")}</button>
              <button type="button" onClick={clearBankQuestionSelection} disabled={!selectedBankQuestionIds.length}><X size={16} />{t("common.clear")}</button>
              <button type="button" onClick={createPracticeFromSelectedBankQuestions} disabled={!selectedBankQuestionIds.length || operationStatus.state === "loading"}><Play size={16} />{t("practice.practice_selected")}</button>
              <button type="button" className="dangerSoft" onClick={retireSelectedBankQuestions} disabled={!selectedBankQuestionIds.length || operationStatus.state === "loading"}><Trash2 size={16} />{t("common.retire_selected")}</button>
            </div>
            <div className="questionList">
              {questions.map((question) => (
                <div key={question.id} className={`questionRow ${question.id === selectedQuestion ? "active" : ""} ${selectedBankQuestionIdSet.has(question.id) ? "selected" : ""}`}>
                  <label className="questionRowCheck" onClick={(event) => event.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedBankQuestionIdSet.has(question.id)}
                      onChange={(event) => toggleBankQuestionSelection(question.id, event.target.checked)}
                      aria-label={tf("practice.select_question_value", [question.id])}
                    />
                  </label>
                  <button type="button" className="questionRowBody" onClick={() => setSelectedQuestion(question.id)}>
                    <span>{question.id}</span>
                    <strong>{(question.difficulty_star || "-")} {t("common.stars")}</strong>
                    <em>{t(compactSource(question.source_name))}</em>
                    {question.publish_status === "duplicate_alias" && <small className="doneStamp">{t("common.duplicate_alias")}</small>}
                    {!question.eligible_for_practice && question.publish_status !== "duplicate_alias" && <small className="doneStamp">{t("common.unavailable_0a3dea74")}</small>}
                    {question.last_answered_at && <small className="doneStamp">{t("common.attempted")} {formatDateTime(question.last_answered_at)}</small>}
                    <span className="questionPreviewText">{question.stem_preview}</span>
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="panel previewPanel">
            <div className="sectionHeader compactHeader">
              <div>
                <span className="eyebrow">{t("practice.question_preview")}</span>
                <h2>{(selectedQuestion || "-")}</h2>
              </div>
              <button type="button" className="dangerSoft" onClick={deleteSelectedQuestion} disabled={!selectedQuestion || operationStatus.state === "loading"}><Trash2 size={16} />{t("practice.retire_current_question")}</button>
            </div>
            <MathContent source={currentStem} />
            {currentAnswerGuide && (
              <div className="answerFormatPanel">
                <strong>{t(currentAnswerGuide.title)}</strong>
                {currentAnswerGuide.lines.map((line) => <span key={line}>{t(line)}</span>)}
                {currentAnswerGuide.examples.length > 0 && <em>{t("practice.answer_format_not_the_answer")}{t(currentAnswerGuide.examples.join(" / "))}</em>}
              </div>
            )}
            <div className="tagBasket previewTags">
              {(currentQuestionDetail?.tags || []).map((tag) => <span key={`${tag.id}-${tag.role}`}>{t(tagKindLabel(tag.kind))} · {tag.label}</span>)}
            </div>
            {questionDeleteResult && (
              <div className="clearResult">
                {Object.entries(questionDeleteResult.updated_counts).filter(([, value]) => value > 0).map(([key, value]) => <span key={key}>{key}: {value}</span>)}
              </div>
            )}
            {appMode === "admin" && selectedQuestion && (
              <div className="repairPanel">
                <div className="sectionHeader compactHeader">
                  <div>
                    <span className="eyebrow">{t("practice.question_governance")}</span>
                    <h2>{t("common.repair_publish_roll_back")}</h2>
                  </div>
                  <button type="button" onClick={loadRepairDraftFromCurrent} disabled={operationStatus.state === "loading"}><RefreshCcw size={16} />{t("practice.load_current_question")}</button>
                </div>
                <p className="mutedLine">{t("reports.repairs_begin_as_drafts_with_diff_previews_publishing_creates")}</p>
                <div className="repairDiff">
                  <div className="sectionHeader compactHeader">
                    <div>
                      <strong>{t("common.ai_repair_suggestions")}</strong>
                      <span>{t("common.suggestions_are_for_administrators_only_no_automatic_publication_deletion")}</span>
                    </div>
                    <button type="button" onClick={requestRepairSuggestion} disabled={operationStatus.state === "loading" || !selectedQuestion}><BrainCircuit size={16} />{t("generation.generate_suggestions")}</button>
                  </div>
                  <label className="checkLine"><input type="checkbox" checked={repairSuggestionUseMock} onChange={(event) => setRepairSuggestionUseMock(event.target.checked)} />{t("common.use_mock_suggestions_uncheck_for_real_ai_calls")}</label>
                  {repairSuggestionResult ? (
                    <>
                      <div className="clearResult">
                        <span>{t("common.status_ed1eb79b")} {repairSuggestionResult.suggestion.status}</span>
                        <span>{t("common.type")} {repairSuggestionResult.suggestion.issue_type}</span>
                        <span>{t("common.severity")} {repairSuggestionResult.suggestion.severity}</span>
                        <span>{t("common.confidence_016f6945")} {Math.round((repairSuggestionResult.suggestion.confidence || 0) * 100)}%</span>
                        <span>{(repairSuggestionResult.suggestion.should_quarantine ? t("common.quarantine_recommended") : t("common.automatic_quarantine_not_recommended"))}</span>
                        <span>{(repairSuggestionResult.suggestion.should_regrade_after_publish ? t("common.regrade_after_publication_recommended") : t("common.no_regrading_recommendation"))}</span>
                      </div>
                      <span>{((repairSuggestionResult.suggestion.summary || repairSuggestionResult.suggestion.blocked_reason) || t("reports.no_summary"))}</span>
                      {repairSuggestionResult.suggestion.evidence.length > 0 && repairSuggestionResult.suggestion.evidence.slice(0, 4).map((item, index) => <span key={`repair-evidence-${index}`}>{t("reports.evidence")} {item}</span>)}
                      {repairSuggestionResult.suggestion.recommended_actions.length > 0 && repairSuggestionResult.suggestion.recommended_actions.slice(0, 4).map((item, index) => <span key={`repair-action-${index}`}>{t("common.suggestion")} {item}</span>)}
                      {(Object.keys(repairSuggestionResult.suggestion.draft_question_v2_patch || {}).length > 0 || Object.keys(repairSuggestionResult.suggestion.draft_answer_spec_patch || {}).length > 0) && (
                        <pre className="jsonPreview">{JSON.stringify({ question_v2_patch: repairSuggestionResult.suggestion.draft_question_v2_patch, answer_spec_patch: repairSuggestionResult.suggestion.draft_answer_spec_patch }, null, 2)}</pre>
                      )}
                      <button type="button" onClick={loadRepairSuggestionIntoForm} disabled={operationStatus.state === "loading"}><Clipboard size={16} />{t("common.load_into_draft_form")}</button>
                    </>
                  ) : <span>{t("generation.choose_issue_type_and_notes_to_generate_suggestions_real")}</span>}
                </div>
                <div className="repairFormGrid">
                  <label><span>{t("common.issue_type")}</span><select value={repairForm.reason} onChange={(event) => setRepairForm({ ...repairForm, reason: event.target.value })}>
                    <option value="manual_fix">{t("common.manual_repair")}</option>
                    <option value="canonical_answer_error">{t("practice.incorrect_standard_answer")}</option>
                    <option value="ambiguous_question">{t("common.ambiguous_wording")}</option>
                    <option value="missing_image">{t("common.missing_figure_formatting")}</option>
                    <option value="duplicate_question">{t("practice.duplicate_question")}</option>
                    <option value="wrong_star">{t("common.incorrect_difficulty")}</option>
                    <option value="wrong_tags">{t("common.incorrect_tags")}</option>
                    <option value="solution_unsuitable">{t("common.unsuitable_solution")}</option>
                  </select></label>
                  <label><span>{t("common.severity_367839ef")}</span><select value={repairForm.severity} onChange={(event) => setRepairForm({ ...repairForm, severity: event.target.value })}>
                    <option value="low">{t("common.low")}</option>
                    <option value="medium">{t("common.medium")}</option>
                    <option value="high">{t("common.high")}</option>
                    <option value="critical">{t("common.critical")}</option>
                  </select></label>
                  <label className="checkLine"><input type="checkbox" checked={repairForm.quarantine} onChange={(event) => setRepairForm({ ...repairForm, quarantine: event.target.checked })} />{t("practice.quarantine_from_practice_when_creating_draft")}</label>
                </div>
                <label className="repairFull"><span>{t("common.questionv2_draft_json")}</span><textarea className="jsonArea" value={repairForm.question_v2} onChange={(event) => setRepairForm({ ...repairForm, question_v2: event.target.value })} placeholder={t("practice.load_the_current_question_then_edit_content_solution_evaluation")} /></label>
                <label className="repairFull"><span>{t("common.answer_spec_draft_json")}</span><textarea className="jsonArea small" value={repairForm.answer_spec} onChange={(event) => setRepairForm({ ...repairForm, answer_spec: event.target.value })} placeholder={t("common.leave_blank_for_content_only_repairs_otherwise_include_canonical")} /></label>
                <label className="repairFull"><span>{t("common.notes")}</span><input value={repairForm.note} onChange={(event) => setRepairForm({ ...repairForm, note: event.target.value })} placeholder={t("reports.repair_evidence_manual_review_notes_or_rollback_reason")} /></label>
                <div className="actionRow">
                  <button type="button" onClick={createRepairDraft} disabled={operationStatus.state === "loading" || !repairForm.question_v2}><ShieldCheck size={16} />{t("common.create_repair_draft")}</button>
                  <button type="button" onClick={() => publishRepairDraft()} disabled={operationStatus.state === "loading" || !latestDraftRepair}><CheckCircle2 size={16} />{t("common.publish_latest_draft")}</button>
                  <button type="button" className="dangerSoft" onClick={() => revertPublishedRepair()} disabled={operationStatus.state === "loading" || !latestPublishedRepair}><RefreshCcw size={16} />{t("common.roll_back_latest_publication")}</button>
                </div>
                <div className="repairDiff">
                  <strong>{t("common.merge_duplicates")}</strong>
                  <span>{t("practice.keep_the_canonical_question_the_current_question_becomes_an")}</span>
                  <div className="repairFormGrid">
                    <label><span>{t("practice.canonical_question_id")}</span><input value={duplicateMergeForm.canonical_question_id} onChange={(event) => setDuplicateMergeForm({ ...duplicateMergeForm, canonical_question_id: event.target.value })} placeholder={t("practice.enter_the_question_id_to_keep")} /></label>
                    <label><span>{t("common.merge_reason")}</span><input value={duplicateMergeForm.reason} onChange={(event) => setDuplicateMergeForm({ ...duplicateMergeForm, reason: event.target.value })} placeholder="duplicate_question" /></label>
                    <label className="checkLine"><input type="checkbox" checked={duplicateMergeForm.retire_aliases} onChange={(event) => setDuplicateMergeForm({ ...duplicateMergeForm, retire_aliases: event.target.checked })} />{t("practice.current_question_leaves_the_pool_after_merging")}</label>
                  </div>
                  <button type="button" className="dangerSoft" onClick={mergeSelectedQuestionAsDuplicateAlias} disabled={operationStatus.state === "loading" || !selectedQuestion || !duplicateMergeForm.canonical_question_id.trim()}><Tags size={16} />{t("common.merge_as_duplicate_alias")}</button>
                </div>
                {duplicateMergeResult && (
                  <div className="clearResult">
                    <span>canonical: {(duplicateMergeResult.canonical_question?.id || "-")}</span>
                    <span>alias: {duplicateMergeResult.aliases.length}</span>
                    <span>{t("history.review_items_closed")} {duplicateMergeResult.closed_review_items}</span>
                  </div>
                )}
                <div className="repairDiff">
                  <div className="sectionHeader compactHeader">
                    <div>
                      <strong>{t("common.impact_statistics")}</strong>
                      <span>{t("history.assess_whether_quarantine_review_notification_or_historical_recalculation_is")}</span>
                    </div>
                    <button type="button" onClick={() => loadQuestionImpact()} disabled={operationStatus.state === "loading"}><RefreshCcw size={16} />{t("common.refresh")}</button>
                  </div>
                  {currentQuestionImpact ? (
                    <>
                      <div className="clearResult">
                        <span>{t("common.learners")} {currentQuestionImpact.totals.learner_count}</span>
                        <span>session: {currentQuestionImpact.totals.session_count}</span>
                        <span>{t("common.submissions")} {currentQuestionImpact.totals.submission_count}</span>
                        <span>{t("common.wrong_records")} {currentQuestionImpact.totals.wrong_record_count}</span>
                        <span>{t("practice.answer_reveals")} {currentQuestionImpact.totals.solution_revealed_count}</span>
                        <span>{t("common.open_reviews")} {currentQuestionImpact.totals.open_review_queue_count}</span>
                      </div>
                      <span>{(currentQuestionImpact.history_recalculation.recommended ? t("common.preview_regrading_first") : t("common.no_automatic_recalculation_recommendation"))} · {t(currentQuestionImpact.history_recalculation.reason)}</span>
                      {currentQuestionImpact.history_recalculation.affected_counts && (
                        <div className="clearResult">
                          <span>{t("common.definition_status")} {currentQuestionImpact.history_recalculation.status}</span>
                          <span>{t("common.strategy")} {(currentQuestionImpact.history_recalculation.strategy || "online_recompute")}</span>
                          {Object.entries(currentQuestionImpact.history_recalculation.affected_counts).map(([key, value]) => <span key={key}>{key}: {value}</span>)}
                        </div>
                      )}
                      {(currentQuestionImpact.history_recalculation.affected_scopes || []).length > 0 && (
                        <div className="repairTimeline compact">
                          {currentQuestionImpact.history_recalculation.affected_scopes?.slice(0, 4).map((scope) => (
                            <div key={scope.scope}>
                              <span>{scope.status}</span>
                              <strong>{scope.scope}</strong>
                              <em>{t(scope.description)}</em>
                            </div>
                          ))}
                        </div>
                      )}
                      {currentQuestionImpact.affected_sessions.length > 0 ? (
                        <div className="repairTimeline">
                          {currentQuestionImpact.affected_sessions.slice(0, 5).map((row) => (
                            <div key={row.session_id}>
                              <span>{row.mode}</span>
                              <strong>{(row.learner_name || row.learner_id)}</strong>
                              <em>{t("common.no")} {(row.question_index || "-")} {t("common.questions_ef06789e")} {row.attempt_count} {t("common.submissions_final")} {(row.final_correct === true ? t("common.correct") : (row.final_correct === false ? t("common.incorrect") : t("common.ungraded")))} · {(row.last_submission_at ? formatDateTime(row.last_submission_at) : t("common.not_submitted"))}</em>
                            </div>
                          ))}
                        </div>
                      ) : <span>{t("practice.no_historical_session_has_used_this_question")}</span>}
                    </>
                  ) : <span>{t("common.loads_automatically_when_selected_use_refresh_to_reload_manually")}</span>}
                </div>
                <div className="repairDiff">
                  <strong>{t("common.regrade_after_repair")}</strong>
                  <span>{t("common.preview_historical_grading_changes_under_the_current_answer_spec")}</span>
                  <div className="actionRow">
                    <button type="button" onClick={previewQuestionRegrade} disabled={operationStatus.state === "loading" || !selectedQuestion}><Search size={16} />{t("common.preview_regrading")}</button>
                    <button type="button" className="dangerSoft" onClick={applyQuestionRegrade} disabled={operationStatus.state === "loading" || !selectedQuestion || !questionRegradeResult}><ShieldCheck size={16} />{t("common.apply_changes")}</button>
                  </div>
                  {questionRegradeResult && (
                    <>
                      <div className="clearResult">
                        <span>{t("common.checked")} {questionRegradeResult.checked_count}</span>
                        <span>{t("common.changed")} {(((questionRegradeResult.total_changed_count ?? questionRegradeResult.changed_count) ?? questionRegradeResult.applied_count) ?? 0)}</span>
                        <span>{t("common.wrong_to_correct")} {questionRegradeResult.changed_wrong_to_correct}</span>
                        <span>{t("common.correct_to_wrong")} {questionRegradeResult.changed_correct_to_wrong}</span>
                        {questionRegradeResult.history_recalculation?.affected_counts && Object.entries(questionRegradeResult.history_recalculation.affected_counts).slice(0, 4).map(([key, value]) => <span key={key}>{key}: {value}</span>)}
                      </div>
                      {questionRegradeResult.history_recalculation && (
                        <span>{questionRegradeResult.history_recalculation.status} · {t(questionRegradeResult.history_recalculation.reason)}</span>
                      )}
                      {questionRegradeResult.items.length > 0 ? (
                        <div className="repairTimeline">
                          {questionRegradeResult.items.slice(0, 5).map((row) => (
                            <div key={row.submission_id}>
                              <span>{(row.changed ? t("common.changed_e329c445") : t("common.unchanged"))}</span>
                              <strong>{row.submission_id}</strong>
                              <em>{(row.previous.is_correct === true ? t("common.correct") : (row.previous.is_correct === false ? t("common.incorrect") : t("common.ungraded")))} → {(row.new.is_correct === true ? t("common.correct") : (row.new.is_correct === false ? t("common.incorrect") : t("common.ungraded")))} · {(row.previous.reason || "-")} → {(row.new.reason || "-")}</em>
                            </div>
                          ))}
                        </div>
                      ) : <span>{t("common.no_historical_submissions_need_updating")}</span>}
                    </>
                  )}
                </div>
                {(repairResult || latestDraftRepair) && (
                  <div className="repairDiff">
                    <strong>{t("common.diff_preview")}</strong>
                    {(repairResult || latestDraftRepair)?.diff.slice(0, 8).map((change, index) => (
                      <span key={`${change.path}-${index}`}>{change.change} · {change.path}</span>
                    ))}
                    {((repairResult || latestDraftRepair)?.diff.length || 0) > 8 && <em>{t("common.another")} {((repairResult || latestDraftRepair)?.diff.length || 0) - 8} {t("common.differences")}</em>}
                  </div>
                )}
                {currentQuestionRepairs.length > 0 && (
                  <div className="repairTimeline">
                    {currentQuestionRepairs.slice(0, 5).map((row) => (
                      <div key={row.id}>
                        <span>{row.status}</span>
                        <strong>{row.reason}</strong>
                        <em>{row.severity} · {row.diff.length} {t("common.differences_fe8cb9f9")} {formatDateTime(row.updated_at)}</em>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </section>
      )}

      {activeWorkspace === "reports" && !strictFocus && (
        <section className="reportsLayout">
          <div className="panel reportsMainPanel">
            <div className="sectionHeader reportHeader">
              <div>
                <span className="eyebrow">{t("common.learning_reports")}</span>
                <h2>{t("common.overview")}</h2>
              </div>
              <div className="actionRow">
                <button type="button" onClick={refreshReportTargets}><RefreshCcw size={16} />{t("common.refresh_targets")}</button>
                <button type="button" onClick={createPractice}><Play size={16} />{t("practice.create_practice_first")}</button>
              </div>
            </div>
            <div className="reportTargetPanel prominent">
              <div className="sectionHeader compactHeader">
                <div>
                  <span className="eyebrow">{t("reports.report_target")}</span>
                  <h3>{t("common.choose_an_existing_record_no_manual_id_needed")}</h3>
                  <p className="mutedLine">{t("generation.supports_current_practice_set_practice_batch_history_and_history")}</p>
                </div>
                <button type="button" onClick={() => applyReportTarget()} disabled={!reportTargetOptions.length}><Search size={16} />{t("common.use_best_match")}</button>
              </div>
              <div className="selectedReportTarget">
                <strong>{t("common.current_selection")}</strong>
                <span>
                  {(reportType === "session" ? ((reportSessionId ? `Session ${reportSessionId}` : t("common.no_session_selected"))) : (reportType === "generation_batch" ? ((reportBatchId ? `Batch ${reportBatchId}` : t("generation.no_batch_selected"))) : t("reports.all_history_report")))}
                </span>
              </div>
              <div className="reportPickerTools">
                <label className="reportTargetSearch"><span>{t("reports.search_report_targets")}</span><input value={reportTargetSearch} onChange={(event) => setReportTargetSearch(event.target.value)} placeholder={t("generation.search_title_session_id_or_batch_id")} /></label>
                <label className="reportTargetSelect"><span>{t("reports.select_id_report_target")}</span><select value="" onChange={(event) => {
                  const target = reportTargetOptions.find((row) => row.key === event.target.value);
                  if (target) applyReportTarget(target);
                }}>
                  <option value="">{t("history.choose_current_historical_sessions_batches_or_history_groups")}</option>
                  {reportTargetOptions.map((target) => <option key={target.key} value={target.key}>{target.displayLabel}</option>)}
                </select></label>
              </div>
              <p className="mutedLine">{t("generation.selection_fills_the_session_id_or_batch_id_automatically")}</p>
              {reportTargetOptions.length ? (
                <div className="batchHistory reportTargetChoices">
                  {reportTargetOptions.slice(0, 8).map((target) => (
                    <button type="button" key={target.key} onClick={() => applyReportTarget(target)}>
                      <strong>{(target.report_type === "generation_batch" ? "Batch" : (target.report_type === "session" ? "Session" : "History"))}</strong>
                      <span>{target.displayLabel}</span>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="mutedLine">{t("reports.no_targets_available_practice_generate_a_set_or_refresh")}</p>
              )}
            </div>
            <div className="reportModeBar primaryReportActions">
              <label><span>{t("reports.report_type")}</span><select value={reportType} onChange={(event) => setReportType(event.target.value as typeof reportType)}>
                <option value="history">{t("reports.all_history_report")}</option>
                <option value="session">{t("reports.session_practice_report")}</option>
                <option value="generation_batch">{t("reports.generation_batch_report")}</option>
              </select></label>
              <button type="button" onClick={generateAiReport}><BrainCircuit size={16} />{t("reports.generate_report_summary")}</button>
            </div>
            <details className="manualReportInputs">
              <summary>{t("generation.advanced_enter_session_id_batch_id")}</summary>
              <div className="reportModeBar manual">
                {reportType === "session" && <label><span>{t("common.session_id")}</span><input value={reportSessionId} onChange={(event) => setReportSessionId(event.target.value)} placeholder={(t(sessionId) || t("common.enter_session_id"))} /></label>}
                {reportType === "generation_batch" && <label><span>Batch ID</span><input value={reportBatchId} onChange={(event) => setReportBatchId(event.target.value)} placeholder={(t(batch?.id) || t("generation.enter_batch_id"))} /></label>}
                {reportType === "history" && <span className="mutedLine">{t("history.all_history_reports_do_not_require_an_id")}</span>}
              </div>
            </details>
            <form className="filterGrid reports" onSubmit={applyReportFilters}>
              <select value={reportFilters.source_name} onChange={(event) => setReportFilters({ ...reportFilters, source_name: event.target.value })}>
                <option value="">{t("common.all_sources")}</option>
                {sourceOptions.map((source) => <option key={source} value={source}>{t(compactSource(source))}</option>)}
              </select>
              <select value={reportFilters.difficulty_star} onChange={(event) => setReportFilters({ ...reportFilters, difficulty_star: event.target.value })}>
                <option value="">{t("common.all_star_levels")}</option>
                {[1, 2, 3, 4, 5].map((star) => <option key={star} value={star}>{star} {t("common.stars")}</option>)}
              </select>
              <select value={reportFilters.premium} onChange={(event) => setReportFilters({ ...reportFilters, premium: event.target.value })}>
                <option value="">{t("common.any_premium_rating")}</option>
                <option value="true">{t("common.premium")}</option>
                <option value="false">{t("common.non_premium")}</option>
              </select>
              <select value={reportFilters.mode} onChange={(event) => setReportFilters({ ...reportFilters, mode: event.target.value })}>
                <option value="">{t("common.all_modes")}</option>
                <option value="practice">{t("practice.practice")}</option>
                <option value="strict_test">{t("practice.strict_test")}</option>
                <option value="generated_practice">{t("practice.question_set_practice")}</option>
                <option value="review_practice">{t("history.history_review")}</option>
                <option value="degraded">{t("common.standard_diagnosis")}</option>
              </select>
              <label><span>{t("common.start_date")}</span><input type="date" value={reportFilters.date_from} onChange={(event) => setReportFilters({ ...reportFilters, date_from: event.target.value })} /></label>
              <label><span>{t("common.end_date")}</span><input type="date" value={reportFilters.date_to} onChange={(event) => setReportFilters({ ...reportFilters, date_to: event.target.value })} /></label>
              <button type="submit"><RefreshCcw size={16} />{t("common.apply_filters_dd0a97ab")}</button>
            </form>

            <div className="metrics dashboard">
              <div title={t(summary?.metric_definitions?.question_total?.basis)}>
                <span>{t("common.total_questions")}</span><strong>{(summary?.question_total ?? 0)}</strong><em>{t(metricScopeLine(summary?.metric_definitions?.question_total))}</em>
                <MetricDetails metric={summary?.metric_definitions?.question_total} />
              </div>
              <div title={t(summary?.metric_definitions?.first_accuracy?.basis)}>
                <span>{t("practice.first_answer_accuracy")}</span><strong>{percent(summary?.submission_summary?.first_accuracy)}</strong><em>{t(metricScopeLine(summary?.metric_definitions?.first_accuracy))}</em>
                <MetricDetails metric={summary?.metric_definitions?.first_accuracy} />
              </div>
              <div title={t(summary?.metric_definitions?.final_accuracy?.basis)}>
                <span>{t("common.final_accuracy")}</span><strong>{percent(summary?.submission_summary?.final_accuracy)}</strong><em>{t(metricScopeLine(summary?.metric_definitions?.final_accuracy))}</em>
                <MetricDetails metric={summary?.metric_definitions?.final_accuracy} />
              </div>
              <div title={t(summary?.metric_definitions?.intervention_shown?.basis)}>
                <span>{t("practice.hint_uses")}</span><strong>{(summary?.intervention_effectiveness?.shown ?? 0)}</strong><em>{t(metricScopeLine(summary?.metric_definitions?.intervention_shown))}</em>
                <MetricDetails metric={summary?.metric_definitions?.intervention_shown} />
              </div>
              <div title={t(summary?.metric_definitions?.answer_reveal_debt_created?.basis)}>
                <span>{t("history.review_due_after_answer_reveal")}</span><strong>{(summary?.intervention_effectiveness?.answer_reveal_debt_created ?? 0)}</strong><em>{t(metricScopeLine(summary?.metric_definitions?.answer_reveal_debt_created))}</em>
                <MetricDetails metric={summary?.metric_definitions?.answer_reveal_debt_created} />
              </div>
              <div title={t(summary?.metric_definitions?.question_quality_incidents?.basis)}>
                <span>{t("practice.question_incidents")}</span><strong>{(summary?.question_quality_incidents?.total ?? 0)}</strong><em>{t(metricScopeLine(summary?.metric_definitions?.question_quality_incidents))}</em>
                <MetricDetails metric={summary?.metric_definitions?.question_quality_incidents} />
              </div>
              <div title={t(summary?.metric_definitions?.answer_judged?.basis)}>
                <span>{t("common.ai_reviews")}</span><strong>{(summary?.ai_review_events?.answer_judged ?? 0)}</strong><em>{t(metricScopeLine(summary?.metric_definitions?.answer_judged))}</em>
                <MetricDetails metric={summary?.metric_definitions?.answer_judged} />
              </div>
              <div title={t(summary?.metric_definitions?.question_support_requested?.basis)}>
                <span>{t("practice.question_concerns")}</span><strong>{(summary?.question_support_events?.requested ?? 0)}</strong><em>{t(metricScopeLine(summary?.metric_definitions?.question_support_requested))}</em>
                <MetricDetails metric={summary?.metric_definitions?.question_support_requested} />
              </div>
              <div title={t(summary?.metric_definitions?.question_support_confirmed?.basis)}>
                <span>{t("practice.ai_confirmed_question_issues")}</span><strong>{(summary?.question_support_events?.problem_confirmed ?? 0)}</strong><em>{t(metricScopeLine(summary?.metric_definitions?.question_support_confirmed))}</em>
                <MetricDetails metric={summary?.metric_definitions?.question_support_confirmed} />
              </div>
              <div title={t(summary?.metric_definitions?.question_replaced?.basis)}>
                <span>{t("common.questions_replaced")}</span><strong>{(summary?.question_support_events?.replaced ?? 0)}</strong><em>{t(metricScopeLine(summary?.metric_definitions?.question_replaced))}</em>
                <MetricDetails metric={summary?.metric_definitions?.question_replaced} />
              </div>
              <div title={t(summary?.metric_definitions?.mastery_check_failed?.basis)}>
                <span>{t("generation.similar_question_generation_failures")}</span><strong>{(summary?.mastery_check_events?.failed ?? 0)}</strong><em>{t(metricScopeLine(summary?.metric_definitions?.mastery_check_failed))}</em>
                <MetricDetails metric={summary?.metric_definitions?.mastery_check_failed} />
              </div>
            </div>
            {summary?.summary_meta && (
              <p className="metricNote">{t("common.definition_b85b3eda")}{t(summary.summary_meta.answer_basis)} · {t(summary.summary_meta.confidence_rule)}
                {summary.summary_meta.history_recalculation && (
                  <>
                    <br />{t("common.regrading_basis")}{summary.summary_meta.history_recalculation.regraded_submission_count}{t("common.submissions_regraded_after_repairs")}{(summary.summary_meta.history_recalculation.latest_regrade_at ? tf("common.latest_value", [formatDateTime(summary.summary_meta.history_recalculation.latest_regrade_at)]) : "")}
                    {(summary.summary_meta.history_recalculation.exported_snapshots_may_be_stale ? t("common.old_exported_reports_may_need_re_exporting") : "")}
                  </>
                )}
              </p>
            )}

            {Boolean(summary?.mastery_check_details?.length) && (
              <div className="masteryReportList">
                <div>
                  <span className="eyebrow">{t("history.similar_question_check_records")}</span>
                  <strong>{t("generation.recent_generation_failures_and_completion")}</strong>
                </div>
                {summary?.mastery_check_details?.slice(-6).reverse().map((row, index) => (
                  <div key={`${row.event_type}-${row.record_id || row.generation_job_id || index}`} className="masteryReportRow">
                    <span className="statusPill neutral">{row.event_type.replace("mastery_check_", "")}</span>
                    <span>{t("practice.original_question")} {row.original_question_id}</span>
                    {row.check_question_id && <span>{t("practice.generated_question")} {row.check_question_id}</span>}
                    {row.terminal_reason && <em>{t("common.failure_reasons")}{t(row.terminal_reason)}</em>}
                    {row.created_at && <em>{formatDateTime(row.created_at)}</em>}
                  </div>
                ))}
              </div>
            )}

            {summary?.comparison?.available && (
              <div className="comparisonPanel">
                <div className="sectionHeader compactHeader">
                  <div>
                    <span className="eyebrow">{t("common.time_window_comparison")}</span>
                    <h2>
                      {summary.comparison.current?.window_start} {t("common.to")} {summary.comparison.current?.window_end}
                      {" "}{t("common.compared_with_the_preceding_equal_length_window")}</h2>
                  </div>
                  <span className="statusPill neutral">{(summary.comparison.window_days || 0)} {t("common.days")}</span>
                </div>
                <div className="comparisonGrid">
                  <div>
                    <span>{t("practice.change_in_first_answer_accuracy")}</span>
                    <strong>{signedPercentPoints(summary.comparison.delta?.first_accuracy)}</strong>
                    <em>{t("common.current")} {percent(summary.comparison.current?.first_accuracy)} {t("common.previous_window")} {percent(summary.comparison.previous?.first_accuracy)}</em>
                  </div>
                  <div>
                    <span>{t("common.change_in_final_accuracy")}</span>
                    <strong>{signedPercentPoints(summary.comparison.delta?.final_accuracy)}</strong>
                    <em>{t("common.current")} {percent(summary.comparison.current?.final_accuracy)} {t("common.previous_window")} {percent(summary.comparison.previous?.final_accuracy)}</em>
                  </div>
                  <div>
                    <span>{t("common.change_in_answered_questions")}</span>
                    <strong>{signedCount(summary.comparison.delta?.answered_questions)}</strong>
                    <em>{t("common.current")} {(summary.comparison.current?.answered_questions ?? 0)} {t("common.previous_window")} {(summary.comparison.previous?.answered_questions ?? 0)}</em>
                  </div>
                  <div>
                    <span>{t("practice.change_in_hint_uses")}</span>
                    <strong>{signedCount(summary.comparison.delta?.help_shown)}</strong>
                    <em>{t("common.current")} {(summary.comparison.current?.help_shown ?? 0)} {t("common.previous_window")} {(summary.comparison.previous?.help_shown ?? 0)}</em>
                  </div>
                  <div>
                    <span>{t("practice.change_in_answer_viewed_questions")}</span>
                    <strong>{signedCount(summary.comparison.delta?.answer_reveal_debt_created)}</strong>
                    <em>{t("common.current")} {(summary.comparison.current?.answer_reveal_debt_created ?? 0)} {t("common.previous_window")} {(summary.comparison.previous?.answer_reveal_debt_created ?? 0)}</em>
                  </div>
                </div>
                <p className="metricNote">{t(summary.comparison.causal_warning)}</p>
              </div>
            )}

            {summary?.review_plan && (
              <div className="reviewPlanPanel">
                <div className="sectionHeader compactHeader">
                  <div>
                    <span className="eyebrow">{t("history.rule_based_review_plan")}</span>
                    <h2>{t("common.records")} {summary.review_plan.sample_size} · {t(confidenceText(summary.review_plan.confidence))}</h2>
                  </div>
                </div>
                <div className="reviewPlanGrid">
                  <div>
                    <h3>{t("common.priority_weak_areas")}</h3>
                    {summary.review_plan.priority_weak_points.length ? summary.review_plan.priority_weak_points.slice(0, 3).map((point) => (
                      <p key={point.tag_id}>
                        <strong>{point.label}</strong>
                        <span>{t("practice.first_answer")} {percent(point.first_accuracy)} {t("common.final")} {percent(point.final_accuracy)} {t("common.average")} {(point.avg_attempts ?? "-")} {t("common.times_172fb7e6")}</span>
                      </p>
                    )) : <span>{t("common.no_clearly_weak_tags_in_the_current_filter_scope")}</span>}
                  </div>
                  <div>
                    <h3>{t("common.next_tasks")}</h3>
                    {summary.review_plan.next_tasks.map((task) => (
                      <p key={task.task_id} className="reviewTaskItem">
                        <strong>{todayTaskTitle(task.title)}</strong>
                        <span>{t(task.description)}</span>
                        <em>{t(task.success_rule)}</em>
                        <button type="button" onClick={() => startReportReviewTask(task)} disabled={operationStatus.state === "loading"}><Play size={14} />{t("practice.start_this_practice")}</button>
                      </p>
                    ))}
                  </div>
                  <div>
                    <h3>{t("history.review_schedule")}</h3>
                    {summary.review_plan.schedule.map((row) => (
                      <p key={row.when}>
                        <strong>{t(REVIEW_SCHEDULE_LABELS[row.when] || row.when)}</strong>
                        <span>{t(row.focus)}</span>
                      </p>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {aiReport && (
              <div className="aiReportPanel">
                <div className="sectionHeader compactHeader">
                  <div>
                    <span className="eyebrow">{t("reports.ai_report_summary")}</span>
                    <h2>{aiReport.report_type} {t("common.records_d7084db6")} {aiReport.sample_size}</h2>
                  </div>
                  <span className="statusPill success">{t("common.confidence_b4f01cf8")} {aiReport.confidence}</span>
                </div>
                <div className="findingList">
                  {aiReport.summary_md && (
                    <div>
                      <strong>{t("reports.parent_summary")}</strong>
                      <span>{aiReport.summary_md}</span>
                    </div>
                  )}
                  {aiReport.findings.length ? aiReport.findings.map((finding, index) => (
                    <div key={index}>
                      <strong>{finding.claim}</strong>
                      <span>{t("reports.evidence_b64148c1")}{(finding.evidence_ids.join(", ") || "-")}</span>
                    </div>
                  )) : <span>{t("practice.not_enough_answer_data_in_this_scope_to_draw")}</span>}
                  {(aiReport.next_actions || []).length > 0 && (
                    <div>
                      <strong>{t("common.next_step_suggestions")}</strong>
                      <span>{(aiReport.next_actions || []).join("；")}</span>
                    </div>
                  )}
                </div>
                <div className="reportBatchPlanner">
                  <div>
                    <span className="eyebrow">{t("reports.generate_a_set_from_report")}</span>
                    <strong>{t("common.turn_weak_tags_and_error_types_into_new_questions")}</strong>
                    <span>{t("reports.create_targeted_practice_after_reading_the_report_generation_failures")}</span>
                  </div>
                  <label className="compactSelect"><span>{t("practice.question_count")}</span><select value={reportBatchCount} onChange={(event) => setReportBatchCount(event.target.value)} disabled={operationStatus.state === "loading"}>
                    {[3, 5, 10, 15, 20, 30, 50].map((value) => <option key={value} value={String(value)}>{value} {t("common.questions")}</option>)}
                  </select></label>
                  <label className="reportGuidanceField">
                    <span>{t("common.parent_preferences")}</span>
                    <textarea
                      value={reportBatchGuidance}
                      onChange={(event) => setReportBatchGuidance(event.target.value)}
                      placeholder={t("common.e_g_more_geometry_counting_no_motion_problems_or")}
                      disabled={operationStatus.state === "loading"}
                    />
                  </label>
                  <button type="button" onClick={generateReportBatchPlan} disabled={operationStatus.state === "loading"}><BrainCircuit size={16} />{t("generation.generate_plan")}</button>
                  <button type="button" onClick={() => applyReportBatchPlan()} disabled={!reportBatchPlan || operationStatus.state === "loading"}><ArrowRight size={16} />{t("generation.apply_to_generate_sets")}</button>
                  <button type="button" onClick={startBatchFromReportPlan} disabled={operationStatus.state === "loading"}><Play size={16} />{t("reports.generate_directly_from_report")}</button>
                  {reportBatchPlan && (
                    <div className="reportPlanCard">
                      <strong>{reportBatchPlan.parent_summary}</strong>
                      <span>{t("practice.question_count")} {reportBatchPlan.recommended_count} {t("common.stars_fdd4c62c")} {reportBatchPlan.difficulty_star} {t("common.confidence")} {reportBatchPlan.confidence}</span>
                      <span>{t("common.tags_462ff3d2")}{(reportBatchPlan.target_tags.join("，") || t("common.general"))}</span>
                      <span>{t("common.keywords")}{(reportBatchPlan.keywords.join("，") || t("common.general_diagnosis"))}</span>
                      {reportBatchPlan.focus_points.length > 0 && <span>{t("common.focus")}{reportBatchPlan.focus_points.slice(0, 3).join("；")}</span>}
                    </div>
                  )}
                </div>
              </div>
            )}

            {!reportHasPracticeData && (
              <div className="emptyState">
                <AlertTriangle size={18} />
                <span>{t("practice.no_practice_submissions_yet_charts_appear_after_answering")}</span>
              </div>
            )}

            <div className="reportCharts">
              <div><h3>{t("common.error_type")}</h3><div className="chartCanvas" ref={errorChartRef} /></div>
              <div><h3>{t("common.accuracy_trend")}</h3><div className="chartCanvas" ref={trendChartRef} /></div>
              <div><h3>{t("common.average_time_by_stars")}</h3><div className="chartCanvas" ref={timeChartRef} /></div>
            </div>
          </div>

          <div className="panel">
            <h3>{t("common.tags_x_stars")}</h3>
            <div className="heatmap">
              <div className="heatmapHead"><span>{t("common.tags")}</span>{[1, 2, 3, 4, 5].map((star) => <strong key={star}>{star}{t("common.stars")}</strong>)}</div>
              {(summary?.tag_star_heatmap || []).map((row) => (
                <div className="heatmapRow" key={row.tag_id}>
                  <span title={`${t(tagKindLabel(row.kind))} · ${displayTagLabel(row, language)}`}>{displayTagLabel(row, language)}</span>
                  {[1, 2, 3, 4, 5].map((star) => {
                    const count = row.stars[String(star)] || 0;
                    return (
                      <button
                        key={star}
                        className="heatCell"
                        type="button"
                        onClick={() => drillHeatmapCell(row.tag_id, star)}
                        style={{ "--tone": heatmapTone(count, row.total) } as CSSProperties & Record<"--tone", number>}
                        title={heatmapCellAriaLabel(displayTagLabel(row, language), star, count, row.total, language)}
                        aria-label={heatmapCellAriaLabel(displayTagLabel(row, language), star, count, row.total, language)}
                      >
                        {count}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
            <h3>{t("reports.evidence_log")}</h3>
            <ul className="evidenceList">
              {(summary?.evidence || []).slice(-10).map((row) => (
                <li key={row.evidence_id}>
                  <button type="button" onClick={() => loadEvidenceDetail(row.evidence_id, row.question_id)}>{row.question_id}</button>
                  <span>{(row.mode || "-")} · {row.attempts} {t("common.times")} {(row.final_correct === true ? t("common.correct") : (row.final_correct === false ? t("common.incorrect") : t("history.needs_review")))}</span>
                </li>
              ))}
            </ul>
          </div>

          {evidenceDetail && (
            <div className="panel timelinePanel">
              <div className="sectionHeader compactHeader">
                <div>
                  <span className="eyebrow">{t("reports.evidence_timeline")}</span>
                  <h2>{(evidenceDetail.question?.id || evidenceDetail.evidence_id)}</h2>
                </div>
              </div>
              <div className="timelineList">
                {timelineRows(evidenceDetail).map((row) => (
                  <div key={`${row.type}-${row.id}`} className="timelineItem">
                    <strong>{row.type}</strong>
                    <span>{row.text}</span>
                    <em>{new Date(row.time).toLocaleString(language)}</em>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {activeWorkspace === "guide" && !strictFocus && (
        <section className="guideLayout">
          <div className="panel guideNav">
            <div className="sectionHeader compactHeader">
              <div>
                <span className="eyebrow">{t("common.guide")}</span>
                <h2>{t("common.sections")}</h2>
              </div>
            </div>
            {GUIDE_SECTIONS.map((section) => (
              <button key={section.id} className={guideSection === section.id ? "on" : ""} onClick={() => setGuideSection(section.id)}>
                <HelpCircle size={16} />{t(section.title)}
              </button>
            ))}
          </div>
          <div className="panel guideContent">
            {GUIDE_SECTIONS.filter((section) => section.id === guideSection).map((section) => (
              <article key={section.id}>
                <span className="eyebrow">{t("common.user_guide")}</span>
                <h2>{t(section.title)}</h2>
                <div className="guideSteps">
                  {section.body.map((line, index) => (
                    <p key={index}><strong>{index + 1}</strong>{t(line)}</p>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      {activeWorkspace === "admin" && !strictFocus && appMode === "admin" && (
        <section className="adminLayout">
          <div className="panel">
            <div className="sectionHeader">
              <div>
                <span className="eyebrow">{t("admin.model_settings")}</span>
                <h2>{modelSettingsState !== "ready" ? t("admin.model_settings_pending_synchronization") : (modelSettings.has_api_key ? t("admin.api_key_saved") : t("admin.api_key_not_configured"))}</h2>
              </div>
              <button onClick={importData} disabled={operationStatus.state === "loading"}><Database size={16} />{t("admin.import_bundled_json")}</button>
            </div>
            <form onSubmit={saveModelSettings} className="settingsForm">
              <label><span>Base URL</span><input value={modelSettings.base_url} onChange={(event) => setModelSettings({ ...modelSettings, base_url: event.target.value })} placeholder="https://ai.liaobots2.work/v1" /></label>
              <label><span>{t("admin.api_key")}</span><input value={apiKeyDraft} onChange={(event) => setApiKeyDraft(event.target.value)} type="password" placeholder={(modelSettings.has_api_key ? t("common.saved_leave_blank_to_keep_it") : t("admin.paste_api_key"))} /></label>
              <label className="checkboxLine"><input type="checkbox" checked={hideApiKeyAfterSave} readOnly /><span>{t("common.hide_and_clear_input_after_saving")}</span></label>
              <label><span>{t("admin.default_model")}</span>{renderModelSelect(modelSettings.default_model, (model) => setModelSettings({ ...modelSettings, default_model: model || modelSettings.default_model }), "请先拉取模型")}</label>
              <p className="mutedLine">{t("common.fetch_models_reads_liaobots_models_choose_default_role_and")}</p>
              <label>
                <span>{t("common.price_table_json")}</span>
                <textarea
                  rows={4}
                  value={modelSettings.model_price_json || ""}
                  onChange={(event) => setModelSettings({ ...modelSettings, model_price_json: event.target.value })}
                  placeholder='{"default":{"input":0.001,"output":0.002},"openai_compatible:gpt-5.6-terra":{"input":0.00015,"output":0.0006}}'
                />
              </label>
              <p className="mutedLine">{t("admin.prices_are_usd_per_1_000_tokens_supports_model")}</p>
              <label>
                <span>{t("generation.strict_mode_reviewer_allowlist")}</span>
                <textarea
                  rows={3}
                  value={modelSettings.strict_reviewer_model_allowlist || ""}
                  onChange={(event) => setModelSettings({ ...modelSettings, strict_reviewer_model_allowlist: event.target.value })}
                  placeholder="openai_compatible:gpt-5.6, openai_compatible:*"
                />
              </label>
              <p className="mutedLine">{t("admin.blank_adds_no_restriction_supports_model_provider_model_provider")}</p>
              {modelError && <p className="errorText">{t(modelError)}</p>}
              {apiTestResult && (
                <div className={`testResult ${apiTestResult.success ? "success" : "error"}`}>
                  <strong>{(apiTestResult.success ? t("admin.api_available") : t("admin.api_unavailable"))}</strong>
                  <span>{apiTestResult.model} {t("admin.model_list")} {(apiTestResult.models_ok ? t("common.passed") : t("common.failed"))} · Chat {(apiTestResult.chat_ok ? t("common.passed") : t("common.failed"))} · {t(formatMs(apiTestResult.elapsed_ms))}</span>
                  {apiTestResult.error && <em>{t(apiTestResult.error)}</em>}
                </div>
              )}
              <div className={`testResult ${aiUsage?.today.budget_exceeded || aiUsage?.budget_exceeded ? "error" : "success"}`}>
                <div className="usageHeader">
                  <strong>{t("common.ai_usage_budgets")}</strong>
                  <button type="button" onClick={() => loadAiUsage()} disabled={operationStatus.state === "loading"}><RefreshCcw size={14} />{t("common.refresh_usage")}</button>
                </div>
                {aiUsage ? (
                  <>
                    <span>{t("common.today")} {aiUsage.today.calls} {t("common.times")} {aiUsage.today.prompt_tokens + aiUsage.today.completion_tokens} {t("common.tokens_estimated")} {formatUsd(aiUsage.today.estimated_cost_usd)} {t("common.daily_budget")} {(aiUsage.daily_budget_usd ? formatUsd(aiUsage.daily_budget_usd) : t("common.not_set"))}</span>
                    <span>{t("common.lifetime")} {aiUsage.calls} {t("common.times")} {aiUsage.prompt_tokens + aiUsage.completion_tokens} {t("common.tokens_estimated")} {formatUsd(aiUsage.estimated_cost_usd)} {t("common.monthly_budget")} {(aiUsage.budget_usd ? formatUsd(aiUsage.budget_usd) : t("common.not_set"))}</span>
                    <span>{t("common.price_table")} {(aiUsage.price_table?.entry_count ?? 0)} {t("common.entries_default_source")} {(aiUsage.price_table?.default_source || "default")} {t("generation.generation_estimate")} {(aiUsage.estimated_tokens_per_call?.generator?.prompt_tokens ?? "-")}+{(aiUsage.estimated_tokens_per_call?.generator?.completion_tokens ?? "-")} {t("history.tokens_call_review_estimate")} {(aiUsage.estimated_tokens_per_call?.reviewer?.prompt_tokens ?? "-")}+{(aiUsage.estimated_tokens_per_call?.reviewer?.completion_tokens ?? "-")} {t("common.tokens_call")}</span>
                    {(aiUsage.today.budget_exceeded || aiUsage.budget_exceeded) && <em>{t("common.budget_exceeded_consider_fewer_candidates_questions_or_a_higher")}</em>}
                  </>
                ) : (
                  <span>{t("common.loads_automatically_in_admin_or_on_refresh_costs_are")}</span>
                )}
              </div>
              <div className="actionRow">
                <button type="button" onClick={discoverModels}><RefreshCcw size={16} />{t("common.fetch_models")}</button>
                <button type="button" onClick={runApiTest}><KeyRound size={16} />{t("practice.test_api")}</button>
                <button type="button" className="dangerSoft" onClick={clearApiKey} disabled={!modelSettings.has_api_key && !apiKeyDraft}><Trash2 size={16} />{t("admin.clear_api_key")}</button>
                <button type="button" onClick={applyRecommendedRoles} disabled={!modelSettings.default_model}>{t("common.apply_to_recommended_roles")}</button>
                <button type="submit"><Settings size={16} />{t("common.save_settings_for_future_real_calls")}</button>
              </div>
            </form>
          </div>

          <div className="panel">
            <div className="sectionHeader compactHeader">
              <div>
                <span className="eyebrow">{t("common.role_models")}</span>
                <h2>{t(Object.keys(ROLE_LABELS).length)} {t("common.roles")}</h2>
              </div>
            </div>
            <div className="roleGrid">
              {Object.entries(ROLE_LABELS).map(([role, label]) => (
                <label key={role}>
                  <span>{t(label)}</span>
                  {renderModelSelect(modelSettings.roles[role] || modelSettings.default_model, (model) => updateRole(role, model || modelSettings.default_model), "使用默认模型")}
                </label>
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="sectionHeader compactHeader">
              <div>
                <span className="eyebrow">{t("common.models_by_star_level")}</span>
                <h2>{t("common.generator_overrides")}</h2>
              </div>
            </div>
            <p className="mutedLine">{t("generation.affects_only_the_generation_role_review_set_review_and")}</p>
            <div className="roleGrid">
              {Object.entries(STAR_GENERATOR_LABELS).map(([bucket, label]) => (
                <label key={bucket}>
                  <span>{t(label)}</span>
                  {renderModelSelect((modelSettings.star_generator_models || {})[bucket] || "", (model) => updateStarGeneratorModel(bucket, model), "使用生成角色模型")}
                </label>
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="sectionHeader compactHeader">
              <div>
                <span className="eyebrow">{t("common.prompt_versions")}</span>
                <h2>{(promptRegistry ? tf("common.value_templates", [promptRegistry.items.length]) : t("common.not_synced"))}</h2>
              </div>
              <div className="actionRow tightActions">
                <button type="button" onClick={() => loadpromptRegistry()}><RefreshCcw size={14} />{t("common.load")}</button>
                <button type="button" onClick={syncpromptRegistry}><Settings size={14} />{t("common.sync_disk")}</button>
              </div>
            </div>
            <p className="mutedLine">{t("admin.each_prompt_version_records_input_output_schemas_required_model")}</p>
            {promptRegistry ? (
              <div className="promptRegistry">
                {Object.entries(promptGroups).map(([role, rows]) => (
                  <div className="promptRoleGroup" key={role}>
                    <div className="promptRoleHeader">
                      <strong>{role}</strong>
                      <span>{t("common.current")} {(promptRegistry.active_prompts?.prompts?.[role] || t("common.latest_by_default"))}</span>
                    </div>
                    {rows.map((item) => {
                      const metadata = item.metadata || {};
                      const release = asRecord(metadata.release);
                      const evalBaseline = asRecord(metadata.eval_baseline);
                      const compatibleModels = asRecord(metadata.compatible_models);
                      const requiredCapabilities = Array.isArray(compatibleModels.required_capabilities) ? compatibleModels.required_capabilities.map(String) : [];
                      return (
                        <div className={`promptVersionRow ${item.status === "active" ? "active" : ""}`} key={item.id}>
                          <div>
                            <strong>{item.version}</strong>
                            <span>{item.path}</span>
                            <em>{metadataString(metadata.input_schema_ref)} → {metadataString(metadata.output_schema_ref)}</em>
                            <em>{t("common.evaluation")} {metadataString(evalBaseline.suite)} · {metadataString(evalBaseline.minimum_status)} · {metadataString(release.channel)} / {metadataString(release.status_state)}</em>
                            {requiredCapabilities.length > 0 && <em>{t("common.capabilities")} {requiredCapabilities.join(", ")}</em>}
                          </div>
                          <div className="promptVersionActions">
                            <span>{(item.status === "active" ? t("common.current") : item.status)}</span>
                            <code>{item.content_hash.slice(0, 10)}</code>
                            <button type="button" disabled={item.status === "active"} onClick={() => activatepromptVersion(item.role, item.version)}>{t("common.activate")}</button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
            ) : (
              <div className="emptyState">{t("common.load_registered_prompts_or_sync_disk_to_scan_prompt")}</div>
            )}
          </div>

          <div className="panel qualitySuitePanel">
            <div className="sectionHeader compactHeader">
              <div>
                <span className="eyebrow">{t("common.ai_quality_suite")}</span>
                <h2>{(qualitySuite ? tf("common.value_ready", [qualitySuite.overall.ready_count]) : t("common.not_loaded"))}</h2>
              </div>
              <div className="actionRow tightActions">
                <button type="button" onClick={() => loadQualitySuite()} disabled={operationStatus.state === "loading"}><RefreshCcw size={14} />{t("common.refresh")}</button>
                <button type="button" onClick={() => runQualityFixedCases(true)} disabled={operationStatus.state === "loading"}>{t("common.run_mock_cases")}</button>
                <button type="button" onClick={() => runQualityFixedCases(false)} disabled={operationStatus.state === "loading"} title={(modelSettings.has_api_key ? t("admin.use_the_current_real_ai_api_and_log_fixed") : t("admin.without_an_api_key_the_backend_fails_closed_instead"))}>{t("admin.run_real_ai_api")}</button>
              </div>
            </div>
            <p className="mutedLine">{t("reports.includes_prompt_regression_injection_protection_gold_sets_duplicates_answer")}</p>
            {qualityRunResult ? (
              <div className="qualityOverview">
                <div><span>{t("common.latest_run")}</span><strong>{(qualityRunResult.use_mock ? t("common.mock") : t("common.real"))}</strong><em>{qualityRunResult.provider}</em></div>
                <div><span>{t("common.cases")}</span><strong>{qualityRunResult.evaluated_count}</strong><em>{t("common.request")} {qualityRunResult.requested_case_count}</em></div>
                <div><span>{t("common.passed")}</span><strong>{qualityRunResult.passed}</strong><em>{(qualityRunResult.ok ? t("common.all_passed") : t("common.issues_found"))}</em></div>
                <div><span>{t("common.failed_unknown")}</span><strong>{qualityRunResult.failed + qualityRunResult.unknown}</strong><em>{qualityRunResult.status}</em></div>
              </div>
            ) : null}
            {qualitySuite ? (
              <>
                <div className="qualityOverview">
                  <div><span>{t("common.overall")}</span><strong>{(qualitySuite.overall.ok ? t("common.can_continue") : t("common.action_needed"))}</strong><em>{qualitySuite.schema_version}</em></div>
                  <div><span>{t("common.ready")}</span><strong>{qualitySuite.overall.ready_count}</strong><em>{t("common.fixed_or_runnable_checks")}</em></div>
                  <div><span>{t("common.partial_coverage")}</span><strong>{qualitySuite.overall.partial_count}</strong><em>{t("common.gates_exist_case_set_missing")}</em></div>
                  <div><span>{t("common.missing_not_run")}</span><strong>{qualitySuite.overall.missing_count + qualitySuite.overall.not_run_count}</strong><em>{t("common.data_or_logs_required")}</em></div>
                  <div><span>{t("common.failed")}</span><strong>{qualitySuite.overall.failed_count}</strong><em>{t("common.changes_not_recommended_for_release")}</em></div>
                </div>
                <div className="qualitySuiteGrid">
                  {orderedQualitySuites.map((suite) => (
                    <article className={`qualitySuiteCard ${qualityStatusClass(suite)}`} key={suite.name}>
                      <div className="qualitySuiteHead">
                        <strong>{t(qualitySuiteLabel(suite.name))}</strong>
                        <span>{(suite.ok ? t("common.passed") : t("common.not_passed"))} · {t(qualityStatusLabel(suite.status))}</span>
                      </div>
                      <div className="qualityMetricList">
                        {qualityMetricHighlights(suite).map((line, index) => <em key={index}>{t(line)}</em>)}
                      </div>
                      <div className="qualityGapList">
                        {suite.gaps.length ? suite.gaps.slice(0, 3).map((gap) => <span key={gap}>{t(qualityGapLabel(gap))}</span>) : <span>{t("common.no_specific_gaps_identified")}</span>}
                      </div>
                    </article>
                  ))}
                </div>
                <p className="mutedLine">{t("common.generated")}{formatDateTime(qualitySuite.generated_at)} {t("common.prompt_directory")}{qualitySuite.prompt_dir}</p>
              </>
            ) : (
              <div className="emptyState">{t("common.admin_loads_the_quality_suite_automatically_refresh_to_inspect")}</div>
            )}
          </div>

          <div className="panel errorCatalogPanel">
            <div className="sectionHeader compactHeader">
              <div>
                <span className="eyebrow">{t("common.error_catalog")}</span>
                <h2>{(errorCatalog ? tf("common.value_entries", [errorCatalog.items.length]) : t("common.not_loaded"))}</h2>
              </div>
              <div className="actionRow tightActions">
                <input value={errorCatalogSearch} onChange={(event) => setErrorCatalogSearch(event.target.value)} placeholder={t("common.search_error_codes_messages_or_next_steps")} />
                <button type="button" onClick={() => loadErrorCatalog()} disabled={operationStatus.state === "loading"}><RefreshCcw size={14} />{t("common.load")}</button>
              </div>
            </div>
            <p className="mutedLine">{t("common.errors_include_error_code_user_message_next_step_retryability")}</p>
            {errorCatalog ? (
              <>
                <div className="errorCatalogSummary">
                  <span>{errorCatalog.schema_version}</span>
                  <span>{t("common.business_mappings")} {errorCatalog.common_count}</span>
                  <span>{t("common.http_defaults")} {errorCatalog.default_count}</span>
                  <span>{t("common.showing")} {visibleErrorCatalogItems.length}</span>
                </div>
                <div className="errorCatalogList">
                  {visibleErrorCatalogItems.map((item) => (
                    <article key={`${item.source}-${item.error_code}-${item.match_key || item.http_status || "default"}`}>
                      <div>
                        <strong>{item.error_code}</strong>
                        <span>{(item.retryable ? t("common.retryable") : t("common.not_retryable"))}{(item.http_status ? ` ? HTTP ${item.http_status}` : "")}</span>
                      </div>
                      <p>{t(item.user_message)}</p>
                      <em>{t(item.action)}</em>
                      <small>{(item.match_key || item.source)}</small>
                      <button type="button" title={t("common.copy_error_code")} onClick={() => navigator.clipboard.writeText(item.error_code)}><Clipboard size={14} /></button>
                    </article>
                  ))}
                </div>
              </>
            ) : (
              <div className="emptyState">{t("common.load_the_error_catalog_then_search_a_status_bar")}</div>
            )}
          </div>

          <div className="panel healthPanel">
            <div className="sectionHeader compactHeader">
              <div>
                <span className="eyebrow">{t("common.health_checks")}</span>
                <h2>{(healthDetail ? healthDetail.overall.status : t("common.not_loaded"))}</h2>
              </div>
              <button type="button" onClick={() => loadHealthDetails()} disabled={operationStatus.state === "loading"}><RefreshCcw size={14} />{t("common.load")}</button>
            </div>
            <p className="mutedLine">{t("admin.health_checks_cover_liveness_readiness_degradation_and_diagnostics_they")}</p>
            {healthDetail ? (
              <>
                <div className="qualityOverview">
                  <div><span>{t("common.overall_status")}</span><strong>{healthDetail.overall.status}</strong><em>{healthDetail.schema_version}</em></div>
                  <div><span>{t("common.database")}</span><strong>{t(metadataBooleanText(asRecord(healthDetail.checks.degraded).ok))}</strong><em>{metadataString(asRecord(asRecord(healthDetail.checks.degraded).database).latency_ms)} ms</em></div>
                  <div><span>{t("admin.ai_api")}</span><strong>{metadataString(asRecord(asRecord(healthDetail.checks.degraded).provider).ok)}</strong><em>{metadataString(asRecord(asRecord(healthDetail.checks.degraded).provider).provider)}</em></div>
                  <div><span>{t("common.disk")}</span><strong>{metadataString(asRecord(asRecord(healthDetail.checks.degraded).disk).free_gb)} GB</strong><em>{t("common.free_space")}</em></div>
                </div>
                {(() => {
                  const release = asRecord(asRecord(healthDetail.checks.degraded).release);
                  const associations = asRecord(release.associations);
                  const surfaces = Array.isArray(associations.surfaces) ? associations.surfaces : [];
                  const smokePhases = Array.isArray(associations.smoke_phases) ? associations.smoke_phases : [];
                  const nextActions = metadataStringArray(associations.next_actions).slice(0, 3);
                  return (
                    <article className="statusCatalogDomain releaseManifestCard">
                      <div className="statusDomainHead">
                        <div>
                          <strong>{t("common.release_manifest_associations")}</strong>
                          <span>{metadataString(associations.status || release.ok)}</span>
                        </div>
                        <span className={`statusCatalogChip ${release.ok ? "success" : "warning"}`}>{(release.ok ? "OK" : "CHECK")}</span>
                      </div>
                      <p><strong>{t("common.version")}</strong> {metadataString(release.app_version)} · manifest {t(metadataBooleanText(release.manifest_exists))}</p>
                      <p><strong>{t("common.issues")}</strong> {t(releaseProblemCountSummary(asRecord(associations.problem_counts)))}</p>
                      <p><strong>{t("common.linked_entry_points")}</strong> {t("admin.api_diagnostics_launcher_smoke_total")} {surfaces.length} {t("common.smoke_phases")} {smokePhases.length} {t("common.items")}</p>
                      {nextActions.map((action, index) => (
                        <p key={`release-action-${index}`}><strong>{t("common.recommendations")}</strong> {t(action)}</p>
                      ))}
                    </article>
                  );
                })()}
                <div className="statusCatalogGrid">
                  {Object.keys(healthDetail.checks).map((key) => {
                    const check = asRecord(healthDetail.checks[key]);
                    return (
                      <article className="statusCatalogDomain" key={key}>
                        <div className="statusDomainHead">
                          <div>
                            <strong>{key}</strong>
                            <span>{metadataString(check.ok)}</span>
                          </div>
                          <span className={`statusCatalogChip ${check.ok ? "success" : key === "degraded" ? "warning" : "error"}`}>{(check.ok ? "OK" : "CHECK")}</span>
                        </div>
                        {Object.entries(check).filter(([field]) => !["status", "ok"].includes(field)).slice(0, 4).map(([field, value]) => (
                          <p key={`${key}-${field}`}><strong>{field}</strong> {metadataString(typeof value === "object" ? asRecord(value).status || asRecord(value).ok || JSON.stringify(value).slice(0, 120) : value)}</p>
                        ))}
                      </article>
                    );
                  })}
                </div>
                <p className="mutedLine">{t("common.generated")}{formatDateTime(healthDetail.generated_at)}</p>
              </>
            ) : (
              <div className="emptyState">{t("admin.load_layered_health_status_for_the_api_database_disk")}</div>
            )}
          </div>

          <div className="panel runtimeConfigPanel">
            <div className="sectionHeader compactHeader">
              <div>
                <span className="eyebrow">{t("admin.runtime_configuration")}</span>
                <h2>{(runtimeConfig ? tf("common.value_entries", [runtimeConfig.summary.setting_count]) : t("common.not_loaded"))}</h2>
              </div>
              <button type="button" onClick={() => loadRuntimeConfig()} disabled={operationStatus.state === "loading"}><RefreshCcw size={14} />{t("common.load")}</button>
            </div>
            <p className="mutedLine">{t("admin.shows_backend_configuration_version_defaults_configured_values_and_feature")}</p>
            {runtimeConfig ? (
              <>
                <div className="qualityOverview">
                  <div><span>{t("admin.configuration_version")}</span><strong>{runtimeConfig.schema_version}</strong><em>{formatDateTime(runtimeConfig.generated_at)}</em></div>
                  <div><span>{t("common.configured")}</span><strong>{runtimeConfig.summary.configured_count}</strong><em>{t("common.explicit_env_env_settings")}</em></div>
                  <div><span>{t("common.secrets")}</span><strong>{runtimeConfig.summary.secret_configured_count}</strong><em>{t("common.counts_only")}</em></div>
                  <div><span>{t("common.feature_flags")}</span><strong>{runtimeFeatureFlags.length}</strong><em>{t("common.currently_shown")}</em></div>
                </div>
                <div className="statusCatalogGrid">
                  <article className="statusCatalogDomain">
                    <div className="statusDomainHead">
                      <div>
                        <strong>{t("admin.configuration_category")}</strong>
                        <span>category_counts</span>
                      </div>
                    </div>
                    <div className="statusFieldList">
                      {Object.entries(runtimeConfig.summary.category_counts).map(([category, count]) => <em key={category}>{category}: {count}</em>)}
                    </div>
                  </article>
                  <article className="statusCatalogDomain">
                    <div className="statusDomainHead">
                      <div>
                        <strong>{t("admin.sensitive_setting")}</strong>
                        <span>secret fields</span>
                      </div>
                    </div>
                    <div className="statusChipList">
                      {runtimeConfigSecrets.map((item) => (
                        <span className={`statusCatalogChip ${item.configured ? "success" : "muted"}`} key={item.name}>
                          <strong>{item.name}</strong>
                          <span>{(item.configured ? t("common.configured") : t("common.not_configured"))}</span>
                          <em>{(item.env_key || "-")}</em>
                        </span>
                      ))}
                    </div>
                  </article>
                  <article className="statusCatalogDomain">
                    <div className="statusDomainHead">
                      <div>
                        <strong>{t("common.key_settings")}</strong>
                        <span>snapshot</span>
                      </div>
                    </div>
                    {runtimeConfigHighlights.map((item) => (
                      <p key={item.name}><strong>{item.name}</strong> {metadataString(item.value)} <em>{item.source}</em></p>
                    ))}
                  </article>
                  <article className="statusCatalogDomain">
                    <div className="statusDomainHead">
                      <div>
                        <strong>{t("common.feature_flags")}</strong>
                        <span>feature_flags</span>
                      </div>
                    </div>
                    <div className="statusChipList">
                      {runtimeFeatureFlags.map(([name, flag]) => (
                        <span className={`statusCatalogChip ${flag.enabled ? "success" : "muted"}`} key={name} title={t(flag.description)}>
                          <strong>{name}</strong>
                          <span>{(flag.enabled ? t("common.on") : t("common.off"))}</span>
                          <em>{t(flag.description)}</em>
                        </span>
                      ))}
                    </div>
                  </article>
                </div>
              </>
            ) : (
              <div className="emptyState">{t("admin.load_defaults_explicit_settings_secret_configuration_status_and_feature")}</div>
            )}
          </div>

          <div className="panel statusCatalogPanel">
            <div className="sectionHeader compactHeader">
              <div>
                <span className="eyebrow">{t("common.status_catalog")}</span>
                <h2>{(statusCatalog ? tf("common.value_domains", [statusCatalog.domain_count]) : t("common.not_loaded"))}</h2>
              </div>
              <div className="actionRow tightActions">
                <input value={statusCatalogSearch} onChange={(event) => setStatusCatalogSearch(event.target.value)} placeholder={t("admin.search_states_descriptions_or_model_fields")} />
                <button type="button" onClick={() => loadStatusCatalog()} disabled={operationStatus.state === "loading"}><RefreshCcw size={14} />{t("common.load")}</button>
              </div>
            </div>
            <p className="mutedLine">{t("generation.central_backend_constants_cover_generation_jobs_batches_questions_answer")}</p>
            {statusCatalog ? (
              <>
                <div className="statusCatalogSummary">
                  <span>{statusCatalog.schema_version}</span>
                  <span>{t("common.status_domains")} {statusCatalog.domain_count}</span>
                  <span>{t("common.status_values")} {statusCatalog.status_count}</span>
                  <span>{t("common.showing")} {visibleStatusCatalogDomains.length}</span>
                </div>
                <div className="statusCatalogGrid">
                  {visibleStatusCatalogDomains.map((domain) => (
                    <article className="statusCatalogDomain" key={domain.domain}>
                      <div className="statusDomainHead">
                        <div>
                          <strong>{t(domain.label)}</strong>
                          <span>{domain.domain}</span>
                        </div>
                        <button type="button" title={t("common.copy_status_domain")} onClick={() => navigator.clipboard.writeText(domain.domain)}><Clipboard size={14} /></button>
                      </div>
                      <p>{t(domain.description)}</p>
                      <div className="statusFieldList">
                        {domain.model_fields.map((field) => <em key={field}>{field}</em>)}
                      </div>
                      <div className="statusChipList">
                        {domain.statuses.map((item) => (
                          <button
                            type="button"
                            className={`statusCatalogChip ${statusSeverityClass(item.severity)}`}
                            key={`${domain.domain}-${item.value}`}
                            title={`${item.value}: ${t(item.description)}`}
                            onClick={() => navigator.clipboard.writeText(item.value)}
                          >
                            <strong>{t(item.label)}</strong>
                            <span>{item.value}</span>
                            <em>
                              {([
                                item.active ? t("common.active_state") : "",
                                item.terminal ? t("common.terminal_state") : "",
                                item.blocks_practice ? t("practice.blocks_practice") : "",
                              ].filter(Boolean).join(" · ") || t("common.ordinary_state"))}
                            </em>
                            <small>{(item.allowed_next.length ? tf("common.can_transition_to_value", [item.allowed_next.join(", ")]) : t("common.no_next_state"))}</small>
                          </button>
                        ))}
                      </div>
                    </article>
                  ))}
                </div>
              </>
            ) : (
              <div className="emptyState">{t("common.load_the_status_catalog_to_compare_state_meanings_and")}</div>
            )}
          </div>

          <div className="panel generationPanel">
            <div className="sectionHeader">
              <div>
                <span className="eyebrow">{t("generation.ai_generation")}</span>
                <h2>{modelSettingsState !== "ready" ? t("admin.model_settings_pending_synchronization") : (modelSettings.has_api_key ? t("admin.real_ai_api") : t("common.mock_workflow"))}</h2>
              </div>
              <div className="actionRow">
                <button type="button" onClick={() => setActiveWorkspace("generated")}><BrainCircuit size={16} />{t("generation.generate_sets")}</button>
                <button onClick={generate} disabled={operationStatus.state === "loading"}><BrainCircuit size={16} />{t("generation.generate_one_question")}</button>
              </div>
            </div>
            <p className="mutedLine">{(batchRequest.prefer_reuse_existing_ai ? t("generation.single_question_generation_also_reuses_similar_unattempted_ai_questions") : t("generation.new_generation_required_the_single_question_task_generates_and"))}</p>
            {renderGenerationAdvancedControls("", 1)}
            {job ? (
              <>
                <div className="jobSummary">
                  <div><span>{t("admin.task_id")}</span><strong>{String(job.id || "-")}</strong></div>
                  <div><span>{t("common.status")}</span><strong>{String(job.status || "-")}</strong></div>
                  <div><span>{t("practice.question")}</span><strong>{String(job.question_id || job.accepted_question_id || "-")}</strong></div>
                  <div><span>{t("common.quality_tier")}</span><strong>{t(jobQualityTier)}</strong></div>
                  <div><span>{t("generation.reviewer_independence")}</span><strong>{(jobReviewerPolicy.same_generator_reviewer_model === undefined ? "-" : ((jobSameReviewerModel ? t("admin.same_model") : t("common.separate_models"))))}</strong></div>
                  <div><span>{t("common.quality_degradation")}</span><strong>{(jobReviewerPolicy.quality_degraded === undefined ? "-" : ((jobQualityDegraded ? t("common.yes") : t("common.no_0c70665b"))))}</strong></div>
                  <div><span>{t("common.call_budget")}</span><strong>{(jobCallBudget.planned_total_calls === undefined ? "-" : `${String(jobCallBudget.planned_generation_calls)}+${String(jobCallBudget.planned_reviewer_calls)}`)}</strong></div>
                </div>
                {jobRetryableStatus && (
                  <div className="jobRetryPanel">
                    <div className="sectionHeader compactHeader">
                      <div>
                        <span className="eyebrow">{t("common.targeted_retry")}</span>
                        <h3>{(activeRetryPreview ? tf("common.attempt_value_value", [(activeRetryPreview.regeneration_attempt || "-"), activeRetryPreview.max_regeneration_attempts]) : t("common.loading_constraints"))}</h3>
                      </div>
                      <button type="button" onClick={retryGenerationJob} disabled={operationStatus.state === "loading" || !activeRetryPreview?.retryable}>
                        <RefreshCcw size={14} />{t("common.retry_with_constraints")}</button>
                    </div>
                    {activeRetryPreview ? (
                      <>
                        <p className="mutedLine">
                          {(activeRetryPreview.retry_exhausted ? t("admin.retry_limit_reached_no_more_provider_calls") : t("practice.retries_disable_reuse_and_redesign_the_question_for_the"))}
                        </p>
                        <div className="retryReasonList">
                          {jobRetryReasonCodes.length ? jobRetryReasonCodes.slice(0, 12).map((code) => <span key={code}>{code}</span>) : <span>{t("common.no_specific_failure_code_using_general_redesign_constraints")}</span>}
                        </div>
                        <div className="retryConstraintList">
                          {jobRetryConstraints.map((constraint) => (
                            <article key={`${constraint.code}-${constraint.instruction}`}>
                              <strong>{constraint.code}</strong>
                              <p>{t(constraint.instruction)}</p>
                              {Boolean(constraint.source_codes?.length) && <em>{t("common.source_6181ed6f")}{constraint.source_codes?.join(", ")}</em>}
                            </article>
                          ))}
                        </div>
                      </>
                    ) : (
                      <div className="emptyState"><Clock3 size={18} /><span>{t("common.loading_retry_constraints_for_this_failure")}</span></div>
                    )}
                  </div>
                )}
              </>
            ) : (
              <div className="emptyState"><BrainCircuit size={18} /><span>{t("generation.without_an_api_key_use_the_mock_workflow_to")}</span></div>
            )}
          </div>

          <div className="panel">
            <div className="sectionHeader">
              <div>
                <span className="eyebrow">{t("common.bank_transfer_maintenance")}</span>
                <h2>{t("admin.export_import_remove_bank_packages")}</h2>
              </div>
              <div className="actionRow">
                <button type="button" onClick={exportQuestionBank} disabled={operationStatus.state === "loading"}><Download size={16} />{t("admin.export_bank_package")}</button>
                <button type="button" onClick={() => chooseQuestionBankImport(true)} disabled={operationStatus.state === "loading"}><ShieldCheck size={16} />{t("common.validate_bank_package")}</button>
                <button type="button" onClick={() => chooseQuestionBankImport(false)} disabled={operationStatus.state === "loading"}><Upload size={16} />{t("admin.import_bank_package")}</button>
              </div>
            </div>
            <p className="mutedLine">{t("history.packages_include_questions_versions_answer_specifications_tags_and_review")}</p>
            <input ref={questionBankImportInputRef} type="file" accept="application/json,.json" className="hiddenFileInput" onChange={(event) => importQuestionBankFile(event.target.files?.[0] || null)} />
            {questionBankImportResult && (
              <div className="clearResult">
                <span>{(questionBankImportResult.dry_run ? t("common.dry_run_validation") : questionBankImportResult.status)}</span>
                {Object.entries(questionBankImportResult.imported_counts).filter(([, value]) => value > 0).map(([key, value]) => <span key={key}>{key}: {value}</span>)}
                {Object.entries(questionBankImportResult.skipped_rows).filter(([, value]) => value > 0).map(([key, value]) => <span key={`skip-${key}`}>{key} {t("common.skipped")} {value}</span>)}
                {Boolean(questionBankImportResult.conflict_report?.length) && <span>{t("common.source_conflicts")} {questionBankImportResult.conflict_report?.length}</span>}
              </div>
            )}
          </div>

          <div className="panel">
            <div className="sectionHeader">
              <div>
                <span className="eyebrow">{t("history.history_transfer")}</span>
                <h2>{t("admin.export_or_import_local_records")}</h2>
              </div>
              <div className="actionRow">
                <label className="learnerSwitch" title={t("history.choose_history_export_scope")}>
                  <span>{t("admin.export_scope")}</span>
                  <select value={historyExportScope} onChange={(event) => setHistoryExportScope(event.target.value as "all" | "current_learner")}>
                    <option value="all">{t("history.all_history")}</option>
                    <option value="current_learner">{t("common.active_learner")}</option>
                  </select>
                </label>
                <button type="button" onClick={exportHistory} disabled={operationStatus.state === "loading"}><Download size={16} />{t("history.export_history")}</button>
                <button type="button" onClick={() => chooseHistoryImport(true)} disabled={operationStatus.state === "loading"}><ShieldCheck size={16} />{t("history.validate_history")}</button>
                <button type="button" onClick={() => chooseHistoryImport(false)} disabled={operationStatus.state === "loading"}><Upload size={16} />{t("history.import_history_adf75260")}</button>
                <button type="button" className="dangerSoft" onClick={deleteCurrentLearner} disabled={operationStatus.state === "loading" || !activeLearnerId || Boolean(learners.find((row) => row.id === activeLearnerId)?.is_default)}><Trash2 size={16} />{t("common.delete_active_learner")}</button>
              </div>
            </div>
            <input ref={historyImportInputRef} type="file" accept="application/json,.json" className="hiddenFileInput" onChange={(event) => importHistoryFile(event.target.files?.[0] || null)} />
            <p className="mutedLine">{t("generation.exports_contain_answers_reports_ai_generation_generated_questions_and")}</p>
            {historyImportResult && (
              <div className="clearResult">
                <span>{(historyImportResult.dry_run ? t("common.dry_run_validation") : historyImportResult.status)}</span>
                {Object.entries(historyImportResult.imported_counts).filter(([, value]) => value > 0).map(([key, value]) => <span key={key}>{key}: {value}</span>)}
                {Object.entries(historyImportResult.skipped_rows || {}).filter(([, value]) => value > 0).map(([key, value]) => <span key={`skip-${key}`}>{key} {t("common.skipped")} {value}</span>)}
                {Boolean(historyImportResult.conflict_report?.length) && <span>{t("common.source_conflicts")} {historyImportResult.conflict_report?.length}</span>}
              </div>
            )}
            {learnerDeleteResult && (
              <div className="clearResult">
                <span>{learnerDeleteResult.status}: {learnerDeleteResult.learner_id}</span>
                {Object.entries(learnerDeleteResult.deleted_counts).filter(([, value]) => value > 0).map(([key, value]) => <span key={key}>{key}: {value}</span>)}
                {typeof learnerDeleteResult.skipped.shared_ai_question_count === "number" && <span>{t("common.shared_ai_questions_preserved")} {learnerDeleteResult.skipped.shared_ai_question_count}</span>}
              </div>
            )}
          </div>

          <div className="panel">
            <div className="sectionHeader">
              <div>
                <span className="eyebrow">{t("common.runtime_maintenance")}</span>
                <h2>{t("admin.redacted_diagnostics_recovery")}</h2>
              </div>
              <div className="actionRow">
                <button type="button" onClick={exportDiagnostics} disabled={operationStatus.state === "loading"}><Download size={16} />{t("admin.export_diagnostics")}</button>
                <button type="button" onClick={recoverStaleState} disabled={operationStatus.state === "loading"}><AlertTriangle size={16} />{t("common.repair_stuck_states")}</button>
              </div>
            </div>
            <p className="mutedLine">{t("admin.includes_configuration_summaries_database_integrity_table_counts_failed_jobs")}</p>
            <p className="mutedLine">{t("generation.repairs_mark_generation_review_and_hint_tasks_stuck_in")}</p>
            {recoveryResult && (
              <div className="clearResult recoveryResultPanel">
                <span>{t("common.status_ed1eb79b")} {recoveryResult.status}</span>
                {Object.entries(recoveryResult.counts || {}).map(([key, value]) => <span key={key}>{key}: {value}</span>)}
                <span>{t("common.preserved")} {recoveryResult.preserved.join("、")}</span>
              </div>
            )}
          </div>

          <div className="panel dangerPanel">
            <div className="sectionHeader">
              <div>
                <span className="eyebrow">{t("practice.clear_test_activity")}</span>
                <h2>{t("assistant.dynamic_confirmation_required")}</h2>
              </div>
              <button type="button" className="dangerSoft" onClick={clearHistory} disabled={operationStatus.state === "loading"}><Trash2 size={16} />{t("history.clear_history")}</button>
            </div>
            <p className="mutedLine">{t("assistant.a_one_time_confirmation_code_appears_before_execution_it")}</p>
            {clearHistoryResult && (
              <div className="clearResult">
                {Object.entries(clearHistoryResult).filter(([, value]) => value > 0).map(([key, value]) => <span key={key}>{key}: {value}</span>)}
              </div>
            )}
          </div>
        </section>
      )}
      </div>
    </main>
  );
}

