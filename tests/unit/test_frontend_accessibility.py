from pathlib import Path


def test_frontend_has_keyboard_focus_and_skip_link_affordances():
    page = Path("apps/web/app/page.tsx").read_text(encoding="utf-8")
    styles = Path("apps/web/app/styles.css").read_text(encoding="utf-8")

    assert 'className="skipLink"' in page
    assert 'href="#mainContent"' in page
    assert 'id="mainContent"' in page
    assert 'tabIndex={-1}' in page
    assert 'aria-label="切换高对比模式"' in page
    assert 'aria-label="切换字号大小"' in page
    assert 'aria-label="刷新当前数据"' in page

    assert ".skipLink" in styles
    assert ":focus-visible" in styles
    assert "outline-offset" in styles
    assert ".highContrast button:focus-visible" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles


def test_history_and_report_cells_have_accessible_state_labels():
    page = Path("apps/web/app/page.tsx").read_text(encoding="utf-8")
    styles = Path("apps/web/app/styles.css").read_text(encoding="utf-8")

    assert "function historyPerformanceDistributionText" in page
    assert "function historyItemAriaLabel" in page
    assert "function heatmapCellAriaLabel" in page
    assert 'aria-label={`${historyGroupLabel(group.group_type)}' in page
    assert 'aria-hidden="true"' in page
    assert 'aria-label="历史题号矩阵"' in page
    assert "aria-label={itemLabel}" in page
    assert "aria-pressed={isSelected}" in page
    assert "aria-label={heatmapCellAriaLabel(row.label, star, count, row.total)}" in page
    assert ".historyQuestionCell:focus-visible" in styles
    assert ".heatCell:focus-visible" in styles


def test_generation_batch_progress_announces_status_without_layout_text():
    page = Path("apps/web/app/page.tsx").read_text(encoding="utf-8")
    styles = Path("apps/web/app/styles.css").read_text(encoding="utf-8")

    assert "function etaSummary" in page
    assert "function etaDetail" in page
    assert "batchProgressStatusText" in page
    assert 'role="progressbar"' in page
    assert "aria-valuenow={batchProgress}" in page
    assert 'className="srOnly" role="status" aria-live="polite" aria-atomic="true"' in page
    assert "ETA_CONFIDENCE_LABELS" in page
    assert "ETA_SOURCE_LABELS" in page
    assert ".srOnly" in styles


def test_generation_batch_summary_explains_failures_and_cancel_semantics():
    page = Path("apps/web/app/page.tsx").read_text(encoding="utf-8")
    styles = Path("apps/web/app/styles.css").read_text(encoding="utf-8")

    assert "function batchFailureReasonSummary" in page
    assert "function batchQualityTierText" in page
    assert "batchRemainingGenerationCalls" in page
    assert "失败原因：{batchFailureSummary}" in page
    assert "已经发给外部 provider 的请求可能仍会产生费用" in page
    assert '<details className="batchDetails">' in page
    assert ".batchSummaryStrip" in styles
    assert ".batchCancelNote" in styles
    assert ".batchDetails" in styles


def test_generation_batch_config_can_be_copied_without_starting_new_batch():
    page = Path("apps/web/app/page.tsx").read_text(encoding="utf-8")
    styles = Path("apps/web/app/styles.css").read_text(encoding="utf-8")

    assert "function copyBatchConfigToForm" in page
    assert "setBatchRequest((current) => ({" in page
    assert "setSelectedTagIds(Array.from(new Set(matchedTagIds)))" in page
    assert "请确认后再开始生成新批次" in page
    assert '<Clipboard size={16} />复制配置' in page
    assert '<Clipboard size={14} />复制' in page
    assert "grid-template-columns: minmax(0, 1fr) auto auto auto;" in styles


def test_report_metric_cards_expose_scope_details():
    page = Path("apps/web/app/page.tsx").read_text(encoding="utf-8")
    styles = Path("apps/web/app/styles.css").read_text(encoding="utf-8")

    assert "function metricFilterSummary" in page
    assert "function MetricDetails" in page
    assert "筛选范围" in page
    assert "排除项" in page
    assert "<MetricDetails metric={summary?.metric_definitions?.first_accuracy}" in page
    assert "<MetricDetails metric={summary?.metric_definitions?.question_quality_incidents}" in page
    assert ".metricDetails" in styles


def test_question_bank_filters_can_be_saved_and_reapplied_locally():
    page = Path("apps/web/app/page.tsx").read_text(encoding="utf-8")
    styles = Path("apps/web/app/styles.css").read_text(encoding="utf-8")

    assert 'QUESTION_FILTER_PRESETS_KEY = "math-platform-question-filter-presets-v1"' in page
    assert "type SavedQuestionFilterPreset" in page
    assert "function saveQuestionFilterPreset" in page
    assert "function applySavedQuestionFilterPreset" in page
    assert "function deleteSavedQuestionFilterPreset" in page
    assert "保存的是本机浏览器中的题库筛选" in page
    assert "<Bookmark size={16} />保存" in page
    assert "<Search size={16} />套用筛选" in page
    assert "<Trash2 size={16} />删除保存" in page
    assert ".savedFilterPanel" in styles
    assert ".savedFilterControls" in styles


def test_question_bank_rows_support_bulk_selection_actions():
    page = Path("apps/web/app/page.tsx").read_text(encoding="utf-8")
    styles = Path("apps/web/app/styles.css").read_text(encoding="utf-8")

    assert "const MAX_BANK_SELECTION_COUNT = 500" in page
    assert "selectedBankQuestionIds" in page
    assert "function toggleBankQuestionSelection" in page
    assert "function createPracticeFromSelectedBankQuestions" in page
    assert '"/api/learning/review-session"' in page
    assert 'reason: "question_bank_selection"' in page
    assert "function retireSelectedBankQuestions" in page
    assert 'body: JSON.stringify({ ...challenge, question_ids: uniqueQuestionIds })' in page
    assert 'aria-label={`选择题目 ${question.id}`}' in page
    assert "<CheckCircle2 size={16} />全选当前页" in page
    assert "<Play size={16} />选中题练习" in page
    assert "<Trash2 size={16} />批量停用" in page
    assert ".bankBulkBar" in styles
    assert ".questionRowCheck" in styles
    assert ".questionRowBody" in styles
