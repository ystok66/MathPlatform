from __future__ import annotations


STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_CANCELLING = "cancelling"
STATUS_TIMED_OUT = "timed_out"
STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"

ACTIVE_TASK_STATUSES = frozenset({STATUS_QUEUED, STATUS_RUNNING})
TERMINAL_JOB_STATUSES = frozenset({STATUS_ACCEPTED, STATUS_REJECTED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMED_OUT})
TERMINAL_BATCH_STATUSES = frozenset({STATUS_COMPLETED, STATUS_PARTIAL, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMED_OUT})
CANCEL_REQUESTED_BATCH_STATUSES = frozenset({STATUS_CANCELLED, STATUS_CANCELLING})
FAILED_GENERATION_STATUSES = frozenset({STATUS_FAILED, STATUS_TIMED_OUT, STATUS_REJECTED, STATUS_CANCELLED, STATUS_CANCELLING})
GENERATION_JOB_STATUSES = ACTIVE_TASK_STATUSES | TERMINAL_JOB_STATUSES
GENERATION_BATCH_STATUSES = frozenset({STATUS_QUEUED, STATUS_RUNNING, STATUS_CANCELLING}) | TERMINAL_BATCH_STATUSES
GENERATION_ITEM_STATUSES = frozenset({STATUS_QUEUED, STATUS_RUNNING, STATUS_ACCEPTED, STATUS_REJECTED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMED_OUT})
GENERATED_CANDIDATE_STATUSES = frozenset({STATUS_ACCEPTED, STATUS_REJECTED, STATUS_FAILED, "edited", "approved", "manual_rejected"})

PUBLISH_STATUS_BATCH_REJECTED_SIMILAR = "batch_rejected_similar"
PUBLISH_STATUS_BATCH_REJECTED_CANCELLED = "batch_rejected_cancelled"
PUBLISH_STATUS_QUARANTINED = "quarantined"
PUBLISH_STATUS_RETIRED = "retired"
PUBLISH_STATUS_INTERVENTION_ONLY = "intervention_only"
PUBLISH_STATUS_DUPLICATE_ALIAS = "duplicate_alias"
PUBLISH_STATUS_RETIRED_BY_ADMIN = "retired_by_admin"
PUBLISH_STATUS_RETIRED_QUESTION = "retired_question"
INACTIVE_PUBLISH_STATUSES = (
    PUBLISH_STATUS_BATCH_REJECTED_SIMILAR,
    PUBLISH_STATUS_BATCH_REJECTED_CANCELLED,
    PUBLISH_STATUS_QUARANTINED,
    PUBLISH_STATUS_RETIRED,
    PUBLISH_STATUS_INTERVENTION_ONLY,
    PUBLISH_STATUS_DUPLICATE_ALIAS,
    PUBLISH_STATUS_RETIRED_BY_ADMIN,
    PUBLISH_STATUS_RETIRED_QUESTION,
)

STATUS_DOMAIN_GENERATION_JOB = "generation_job"
STATUS_DOMAIN_GENERATION_BATCH = "generation_batch"
STATUS_DOMAIN_GENERATION_ITEM = "generation_item"
STATUS_DOMAIN_GENERATED_CANDIDATE = "generated_candidate"
STATUS_DOMAIN_QUESTION_STATUS = "question_status"
STATUS_DOMAIN_QUESTION_PUBLISH_STATUS = "question_publish_status"
STATUS_DOMAIN_ANSWER_SPEC_STATUS = "answer_spec_status"
STATUS_DOMAIN_PRACTICE_SESSION = "practice_session"
STATUS_DOMAIN_PRACTICE_MODE = "practice_mode"
STATUS_DOMAIN_REVIEW_QUEUE = "review_queue"
STATUS_DOMAIN_QUESTION_REPAIR = "question_repair"
STATUS_DOMAIN_QUESTION_DUPLICATE_ALIAS = "question_duplicate_alias"
STATUS_DOMAIN_INTERVENTION_RECORD = "intervention_record"
STATUS_DOMAIN_INTERVENTION_PREFETCH = "intervention_prefetch"
STATUS_DOMAIN_PROMPT_TEMPLATE = "prompt_template"
STATUS_DOMAIN_WRONG_QUESTION = "wrong_question"
STATUS_DOMAIN_REVIEW_RECORD = "review_record"
STATUS_DOMAIN_LEARNER_PROFILE = "learner_profile"
STATUS_DOMAIN_ENCRYPTED_CONFIG = "encrypted_config"

QUESTION_STATUSES = frozenset({"verified", "needs_review", "quarantined", "retired"})
QUESTION_PUBLISH_STATUSES = frozenset(
    {
        "source_verified",
        "active_practice",
        "ai_generated",
        "not_published",
        *INACTIVE_PUBLISH_STATUSES,
    }
)
ANSWER_SPEC_STATUSES = frozenset({"verified", "needs_review"})
PRACTICE_SESSION_STATUSES = frozenset({"active", "paused", "completed"})
PRACTICE_MODES = frozenset({"practice", "strict_test", "generated_practice", "review_practice", "diagnostic"})
REVIEW_QUEUE_STATUSES = frozenset({"open", "resolved", "closed"})
QUESTION_REPAIR_STATUSES = frozenset({"draft", "published", "reverted", "rejected"})
QUESTION_DUPLICATE_ALIAS_STATUSES = frozenset({"active", "reverted"})
INTERVENTION_RECORD_STATUSES = frozenset({"active", "prefetched", "cancelled", "stale", "completed", "failed"})
INTERVENTION_PREFETCH_STATUSES = frozenset({"not_prefetched", "generating", "ready", "shown", "unused_prefetch", "stale", "failed", "completed"})
PROMPT_TEMPLATE_STATUSES = frozenset({"active", "available"})
WRONG_QUESTION_STATUSES = frozenset({"active", "reviewing", "resolved", "ignored"})
REVIEW_RECORD_STATUSES = frozenset({"pending", "completed", "resolved"})
LEARNER_PROFILE_STATUSES = frozenset({"active", "archived"})
ENCRYPTED_CONFIG_STATUSES = frozenset({"active", "deleted"})

STATUS_TRANSITIONS: dict[str, dict[str, frozenset[str]]] = {
    STATUS_DOMAIN_GENERATION_JOB: {
        STATUS_QUEUED: frozenset({STATUS_RUNNING, STATUS_CANCELLED, STATUS_TIMED_OUT, STATUS_FAILED, STATUS_REJECTED}),
        STATUS_RUNNING: frozenset({STATUS_ACCEPTED, STATUS_REJECTED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMED_OUT}),
        STATUS_ACCEPTED: frozenset(),
        STATUS_REJECTED: frozenset(),
        STATUS_FAILED: frozenset(),
        STATUS_CANCELLED: frozenset(),
        STATUS_TIMED_OUT: frozenset(),
    },
    STATUS_DOMAIN_GENERATION_BATCH: {
        STATUS_QUEUED: frozenset({STATUS_RUNNING, STATUS_COMPLETED, STATUS_PARTIAL, STATUS_FAILED, STATUS_CANCELLED, STATUS_CANCELLING, STATUS_TIMED_OUT}),
        STATUS_RUNNING: frozenset({STATUS_COMPLETED, STATUS_PARTIAL, STATUS_FAILED, STATUS_CANCELLED, STATUS_CANCELLING, STATUS_TIMED_OUT}),
        STATUS_CANCELLING: frozenset({STATUS_CANCELLED, STATUS_TIMED_OUT}),
        STATUS_COMPLETED: frozenset(),
        STATUS_PARTIAL: frozenset({STATUS_QUEUED, STATUS_RUNNING}),
        STATUS_FAILED: frozenset({STATUS_QUEUED, STATUS_RUNNING}),
        STATUS_CANCELLED: frozenset(),
        STATUS_TIMED_OUT: frozenset({STATUS_QUEUED, STATUS_RUNNING}),
    },
    STATUS_DOMAIN_GENERATION_ITEM: {
        STATUS_QUEUED: frozenset({STATUS_RUNNING, STATUS_ACCEPTED, STATUS_REJECTED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMED_OUT}),
        STATUS_RUNNING: frozenset({STATUS_ACCEPTED, STATUS_REJECTED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMED_OUT}),
        STATUS_ACCEPTED: frozenset({STATUS_REJECTED}),
        STATUS_REJECTED: frozenset(),
        STATUS_FAILED: frozenset(),
        STATUS_CANCELLED: frozenset(),
        STATUS_TIMED_OUT: frozenset(),
    },
}


def allowed_status_transitions(domain: str, current_status: str) -> tuple[str, ...]:
    transitions = STATUS_TRANSITIONS.get(domain)
    if transitions is None:
        return ()
    return tuple(sorted(transitions.get(current_status, ())))


def status_value_allowed(domain: str, next_status: str) -> bool:
    values = STATUS_VALUES_BY_DOMAIN.get(domain)
    if values is None:
        return True
    return next_status in values


def assert_status_value(domain: str, next_status: str) -> None:
    if not status_value_allowed(domain, next_status):
        raise ValueError(f"invalid_status_value:{domain}:{next_status}")


def checked_status_update_values(bindings: dict[str, tuple[str, str]]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field_name, (domain, next_status) in bindings.items():
        assert_status_value(domain, next_status)
        values[field_name] = next_status
    return values


def status_transition_allowed(domain: str, current_status: str | None, next_status: str) -> bool:
    if not status_value_allowed(domain, next_status):
        return False
    if current_status is None or current_status == next_status:
        return True
    transitions = STATUS_TRANSITIONS.get(domain)
    if transitions is None:
        return True
    return next_status in transitions.get(current_status, frozenset())


def assert_status_transition(domain: str, current_status: str | None, next_status: str) -> None:
    assert_status_value(domain, next_status)
    if not status_transition_allowed(domain, current_status, next_status):
        raise ValueError(f"invalid_status_transition:{domain}:{current_status}->{next_status}")


def _status(
    value: str,
    label: str,
    description: str,
    *,
    active: bool = False,
    terminal: bool = False,
    blocks_practice: bool = False,
    severity: str = "neutral",
) -> dict:
    return {
        "value": value,
        "label": label,
        "description": description,
        "active": active,
        "terminal": terminal,
        "blocks_practice": blocks_practice,
        "severity": severity,
    }


def _domain(domain: str, label: str, description: str, model_fields: list[str], statuses: list[dict]) -> dict:
    return {
        "domain": domain,
        "label": label,
        "description": description,
        "model_fields": model_fields,
        "statuses": statuses,
    }


STATUS_CATALOG_DOMAINS = (
    _domain(
        STATUS_DOMAIN_GENERATION_JOB,
        "单题 AI 出题任务",
        "GenerationJob.status 的状态机；只有 accepted/rejected/failed/cancelled/timed_out 是终态。",
        ["GenerationJob.status"],
        [
            _status(STATUS_QUEUED, "排队中", "任务已创建，尚未拿到执行租约。", active=True),
            _status(STATUS_RUNNING, "运行中", "任务正在调用 provider、校验 JSON 或审核候选。", active=True),
            _status(STATUS_ACCEPTED, "已接受", "至少一个候选通过审核并发布为可练习题。", terminal=True, severity="success"),
            _status(STATUS_REJECTED, "已拒绝", "候选均未通过质量门或相似度门禁。", terminal=True, severity="warning"),
            _status(STATUS_FAILED, "失败", "provider、校验、数据库或未捕获异常导致任务失败。", terminal=True, severity="error"),
            _status(STATUS_CANCELLED, "已取消", "用户或批次取消后，任务不得再发布题目。", terminal=True, severity="muted"),
            _status(STATUS_TIMED_OUT, "已超时", "超过 deadline 后关闭任务，晚返回结果只能写日志。", terminal=True, severity="error"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_GENERATION_BATCH,
        "套题出题批次",
        "GenerationBatch.status 的状态机；cancelling 是过渡态，取消后停止派发新 item。",
        ["GenerationBatch.status"],
        [
            _status(STATUS_QUEUED, "排队中", "批次已创建，item 尚未开始派发。", active=True),
            _status(STATUS_RUNNING, "运行中", "正在复用 AI 题或生成/审核新题。", active=True),
            _status(STATUS_CANCELLING, "取消中", "已收到取消请求，运行中的 late result 不允许发布。", active=True, severity="warning"),
            _status(STATUS_COMPLETED, "已完成", "目标数量已满足或所有可完成 item 均已结束。", terminal=True, severity="success"),
            _status(STATUS_PARTIAL, "部分完成", "有题可用但未达到目标数量；可通过补齐剩余恢复到 queued/running。", terminal=True, severity="warning"),
            _status(STATUS_FAILED, "失败", "批次整体失败，通常需要查看 ai_run_logs；若仍有剩余尝试次数，可通过补齐剩余恢复到 queued/running。", terminal=True, severity="error"),
            _status(STATUS_CANCELLED, "已取消", "批次取消完成，未完成 item 不再发布。", terminal=True, severity="muted"),
            _status(STATUS_TIMED_OUT, "已超时", "批次超过总 deadline；若仍有剩余尝试次数，可通过补齐剩余恢复到 queued/running。", terminal=True, severity="error"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_GENERATION_ITEM,
        "套题单题 item",
        "GenerationBatchItem.status 的状态机；复用 AI 题时可从 queued 直接到 accepted。",
        ["GenerationBatchItem.status"],
        [
            _status(STATUS_QUEUED, "排队中", "item 已创建，尚未被 worker 领取。", active=True),
            _status(STATUS_RUNNING, "运行中", "正在复用 AI 题或调用单题生成任务。", active=True),
            _status(STATUS_ACCEPTED, "已接受", "item 已得到可用题；批次最终集合去重前仍可能转为拒绝。", severity="success"),
            _status(STATUS_REJECTED, "已拒绝", "item 被审核、集合去重或质量门拒绝。", terminal=True, severity="warning"),
            _status(STATUS_FAILED, "失败", "item 调用或发布失败。", terminal=True, severity="error"),
            _status(STATUS_CANCELLED, "已取消", "批次取消后 item 关闭。", terminal=True, severity="muted"),
            _status(STATUS_TIMED_OUT, "已超时", "item 超过租约或批次 deadline。", terminal=True, severity="error"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_GENERATED_CANDIDATE,
        "生成候选题",
        "GeneratedCandidate.status 和 GeneratedCandidateReview.status 表示候选题审核状态；人工编辑、批准和拒绝会保留到审核记录。",
        ["GeneratedCandidate.status", "GeneratedCandidateReview.status"],
        [
            _status(STATUS_ACCEPTED, "自动接受", "候选通过自动审核并可发布。", terminal=True, severity="success"),
            _status(STATUS_REJECTED, "自动拒绝", "候选未通过自动质量门或相似度门。", terminal=True, severity="warning"),
            _status(STATUS_FAILED, "失败", "候选生成、解析或审核失败。", terminal=True, severity="error"),
            _status("edited", "人工编辑", "管理者已修改候选 payload，尚未作为自动发布结果。", active=True, severity="warning"),
            _status("approved", "人工批准", "管理者人工批准候选。", terminal=True, severity="success"),
            _status("manual_rejected", "人工拒绝", "管理者人工拒绝候选。", terminal=True, severity="muted"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_QUESTION_STATUS,
        "题目主状态",
        "Question.status 控制题目能否作为可用题进入普通练习和严格测试。",
        ["Question.status"],
        [
            _status("verified", "已验证", "题面和 answer_spec 可直接判分。", severity="success"),
            _status("needs_review", "待复核", "答案规范、题面或来源需要人工复核，不进入严格测试。", blocks_practice=True, severity="warning"),
            _status("quarantined", "已隔离", "题目被质疑或修复流程隔离，不再进入新练习池。", blocks_practice=True, severity="error"),
            _status("retired", "已停用", "题目从新练习池退出，但历史证据继续保留。", blocks_practice=True, severity="muted"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_QUESTION_PUBLISH_STATUS,
        "题目发布状态",
        "Question.publish_status 和 GeneratedCandidate.publish_status 表示题目来源、发布结果和不可练原因。",
        ["Question.publish_status", "GeneratedCandidate.publish_status"],
        [
            _status("source_verified", "题库原题", "来源题库导入并验证通过。", severity="success"),
            _status("active_practice", "可练习", "AI 生成题或修复题已发布到练习池。", severity="success"),
            _status("ai_generated", "AI 生成", "题目来自 AI 生成流程，可进入 AI 混合或纯 AI 池。", severity="success"),
            _status("not_published", "未发布", "候选题尚未发布。", blocks_practice=True, severity="muted"),
            _status(PUBLISH_STATUS_BATCH_REJECTED_SIMILAR, "批次相似拒绝", "集合审核认为与批次内题目过近。", blocks_practice=True, severity="warning"),
            _status(PUBLISH_STATUS_BATCH_REJECTED_CANCELLED, "取消拒绝", "批次取消后返回的 late result，不允许发布。", blocks_practice=True, severity="muted"),
            _status(PUBLISH_STATUS_QUARANTINED, "隔离", "质量事故或人工修复流程隔离。", blocks_practice=True, severity="error"),
            _status(PUBLISH_STATUS_RETIRED, "停用", "从新练习池退出，历史仍可查看。", blocks_practice=True, severity="muted"),
            _status(PUBLISH_STATUS_INTERVENTION_ONLY, "仅启发题", "只作为提示/启发题使用，不进入普通抽题池。", blocks_practice=True, severity="warning"),
            _status(PUBLISH_STATUS_DUPLICATE_ALIAS, "重复别名", "题目被合并到 canonical 题，不再进入新练习池，历史记录继续保留。", blocks_practice=True, severity="muted"),
            _status(PUBLISH_STATUS_RETIRED_BY_ADMIN, "管理员停用", "管理者删除题目时转为软停用，历史证据继续保留。", blocks_practice=True, severity="muted"),
            _status(PUBLISH_STATUS_RETIRED_QUESTION, "关联题已停用", "生成候选仍保留追溯，但它发布出的题目已被管理员软停用。", blocks_practice=True, severity="muted"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_ANSWER_SPEC_STATUS,
        "答案规范状态",
        "AnswerSpec.status 控制确定性判分是否可用。",
        ["AnswerSpec.status"],
        [
            _status("verified", "已验证", "可用于确定性判分和严格测试。", severity="success"),
            _status("needs_review", "待复核", "不能进入严格测试；提交后可能需要 AI/人工复核。", blocks_practice=True, severity="warning"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_PRACTICE_SESSION,
        "练习会话",
        "PracticeSession.status 和 TestRun.status 的会话级状态。",
        ["PracticeSession.status", "TestRun.status"],
        [
            _status("active", "进行中", "会话可继续作答。", active=True, severity="success"),
            _status("paused", "已暂停", "计时暂停，用户可恢复。", active=True, severity="warning"),
            _status("completed", "已完成", "会话已交卷或全部结束。", terminal=True, severity="success"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_PRACTICE_MODE,
        "练习模式",
        "PracticeSession.mode 区分普通练习、严格测试、套题练习和历史重做。",
        ["PracticeSession.mode", "TestBlueprint.mode"],
        [
            _status("practice", "普通练习", "可使用提示、题目质疑、看答案和讲解解答。"),
            _status("strict_test", "严格测试", "禁用提示、看答案、讲解解答和即时 AI 问答。"),
            _status("generated_practice", "套题练习", "来自 AI 套题批次，可边生成边做。"),
            _status("review_practice", "历史重做", "独立复习会话，不改写原测试/套题成绩。"),
            _status("diagnostic", "诊断练习", "用于规则型薄弱点或掌握度检测。"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_REVIEW_QUEUE,
        "审核队列",
        "ReviewQueueItem.status 的治理状态。",
        ["ReviewQueueItem.status"],
        [
            _status("open", "待处理", "审核队列项目仍需要处理。", active=True, severity="warning"),
            _status("resolved", "已处理", "审核队列项目已关闭。", terminal=True, severity="success"),
            _status("closed", "已关闭", "关联题被停用或队列被管理动作关闭，不再需要继续处理。", terminal=True, severity="muted"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_QUESTION_REPAIR,
        "题目修复",
        "QuestionRepairRecord.status 的修复草稿、发布和回滚状态。",
        ["QuestionRepairRecord.status"],
        [
            _status("draft", "修复草稿", "修复记录已创建但尚未发布。", active=True),
            _status("published", "已发布修复", "修复已生成不可变新版本。", terminal=True, severity="success"),
            _status("reverted", "已回滚", "题目回滚到修复前版本。", terminal=True, severity="warning"),
            _status("rejected", "修复拒绝", "修复草稿被拒绝或废弃。", terminal=True, severity="muted"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_QUESTION_DUPLICATE_ALIAS,
        "题目重复别名",
        "QuestionDuplicateAlias.status 表示重复题合并关系是否仍有效。",
        ["QuestionDuplicateAlias.status"],
        [
            _status("active", "有效别名", "alias 题仍指向 canonical 题，原题不再作为独立新题进入练习池。", active=True, severity="success"),
            _status("reverted", "已撤销合并", "重复题合并关系已撤销，后续可重新评估题目发布状态。", terminal=True, severity="warning"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_INTERVENTION_RECORD,
        "启发题与掌握检测",
        "InterventionRecord.status 表示启发题/检测题主生命周期。",
        ["InterventionRecord.status"],
        [
            _status("active", "有效", "当前干预记录仍可触发。", active=True),
            _status("prefetched", "已预生成", "检测题已提前生成但尚未展示。", active=True, severity="success"),
            _status("cancelled", "已取消", "做类似题/检测未展示即取消。", terminal=True, severity="muted"),
            _status("stale", "已过期", "预生成题已过期或不再适用。", terminal=True, severity="muted"),
            _status("completed", "已完成", "干预或掌握检测已结束。", terminal=True, severity="success"),
            _status("failed", "失败", "检测题生成或干预流程失败。", terminal=True, severity="error"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_INTERVENTION_PREFETCH,
        "启发题预生成",
        "InterventionRecord.prefetch_status 表示预生成结果是否可展示。",
        ["InterventionRecord.prefetch_status"],
        [
            _status("not_prefetched", "未预生成", "尚未启动检测题生成。"),
            _status("generating", "生成中", "正在为掌握检测预生成题。", active=True),
            _status("ready", "已就绪", "检测题已经生成，可开始。", severity="success"),
            _status("shown", "已展示", "预生成题已展示给用户。", severity="success"),
            _status("unused_prefetch", "未使用取消", "用户取消或跳过，预生成题未使用。", terminal=True, severity="muted"),
            _status("stale", "已过期", "预生成题已过期或题目上下文变化。", terminal=True, severity="muted"),
            _status("failed", "失败", "预生成调用失败。", terminal=True, severity="error"),
            _status("completed", "已完成", "做类似题/检测已完成。", terminal=True, severity="success"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_PROMPT_TEMPLATE,
        "Prompt 模板",
        "PromptTemplate.status 表示每个角色 prompt 版本是否为当前启用版本。",
        ["PromptTemplate.status"],
        [
            _status("active", "当前启用", "角色当前会使用这个 prompt 版本。", severity="success"),
            _status("available", "可用版本", "已登记但不是当前启用版本。"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_WRONG_QUESTION,
        "错题本",
        "WrongQuestionRecord.status 表示错题是否仍参与复习和报告。",
        ["WrongQuestionRecord.status"],
        [
            _status("active", "有效错题", "错题仍参与复习和报告。", active=True),
            _status("reviewing", "复习中", "错题已进入复习流程。", active=True),
            _status("resolved", "已解决", "错题已经通过复习或人工处理。", terminal=True, severity="success"),
            _status("ignored", "已忽略", "用户选择不再把该题作为错题追踪。", terminal=True, severity="muted"),
        ],
    ),
    _domain(
        STATUS_DOMAIN_REVIEW_RECORD,
        "复习记录",
        "ReviewRecord.status 表示一次复习任务的状态。",
        ["ReviewRecord.status"],
        [
            _status("pending", "待复习", "复习记录等待完成。", active=True),
            _status("resolved", "已解决", "错题或复习记录已被解决。", terminal=True, severity="success"),
            _status("completed", "已完成", "一次复习任务已完成。", terminal=True, severity="success"),
        ],
    ),
)

STATUS_VALUES_BY_DOMAIN = {
    domain["domain"]: frozenset(str(status["value"]) for status in domain["statuses"])
    for domain in STATUS_CATALOG_DOMAINS
}


def status_catalog() -> dict:
    domains = []
    for row in STATUS_CATALOG_DOMAINS:
        domain = dict(row)
        domain["model_fields"] = list(row["model_fields"])
        domain["statuses"] = [
            {**dict(status), "allowed_next": list(allowed_status_transitions(str(row["domain"]), str(status["value"])))}
            for status in row["statuses"]
        ]
        domains.append(domain)
    return {
        "schema_version": "status_catalog_v1",
        "domain_count": len(domains),
        "status_count": sum(len(domain["statuses"]) for domain in domains),
        "domains": domains,
    }
