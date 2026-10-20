param(
  [string]$ReleaseRoot = "",
  [string]$ApiBase = "http://127.0.0.1:8001",
  [string]$DataDir = "",
  [string]$SmokeProfile = "",
  [switch]$UseRealAi,
  [string]$AiModel = "",
  [int]$GenerationTimeoutSeconds = 900,
  [int]$RequestTimeoutSeconds = 30,
  [int]$ImportTimeoutSeconds = 180,
  [int]$TransferTimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"

if (-not $ReleaseRoot) {
  $releaseCandidate = Split-Path -Parent $PSScriptRoot
  if (Test-Path -LiteralPath (Join-Path $releaseCandidate "launcher\MathPlatform.exe") -PathType Leaf) {
    $ReleaseRoot = $releaseCandidate
  }
  else {
    $projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $ReleaseRoot = Join-Path $projectRoot "dist\math-platform"
  }
}

$ReleaseRoot = (Resolve-Path -LiteralPath $ReleaseRoot -ErrorAction Stop).Path
$requestId = "release-business-smoke-$([guid]::NewGuid().ToString("N"))"
$headers = @{
  "X-Math-Platform-Client" = "math-platform-web"
  "X-Request-Id" = $requestId
}
$script:originalModelSettings = $null
$script:modelSettingsOverridden = $false

function New-Result {
  param([bool]$Ok, [hashtable]$Checks, [array]$Failures)
  return [ordered]@{
    schema_version = "release_business_smoke_v1"
    ok = $Ok
    release_root = $ReleaseRoot
    api_base = $ApiBase
    data_dir = $DataDir
    smoke_profile = $SmokeProfile
    use_real_ai = [bool]$UseRealAi
    ai_model = $AiModel
    request_id = $requestId
    generation_timeout_seconds = $GenerationTimeoutSeconds
    transfer_timeout_seconds = $TransferTimeoutSeconds
    checks = $Checks
    failures = $Failures
  }
}

function Finish {
  param([hashtable]$Checks, [array]$Failures)
  if ($script:modelSettingsOverridden -and $null -ne $script:originalModelSettings) {
    try {
      Invoke-ApiJson -Method PUT -Path "/api/settings/models" -Body (ConvertTo-ModelSettingsPayload -Settings $script:originalModelSettings) | Out-Null
      $Checks.model_restore = @{
        ok = $true
        default_model = $script:originalModelSettings.default_model
      }
      $script:modelSettingsOverridden = $false
    }
    catch {
      $Checks.model_restore = @{
        ok = $false
        error = $_.Exception.Message
      }
      $Failures += "model_settings_restore_failed"
    }
  }
  $result = New-Result -Ok ($Failures.Count -eq 0) -Checks $Checks -Failures $Failures
  $result | ConvertTo-Json -Depth 16
  if ($Failures.Count -gt 0) { exit 1 }
}

function Invoke-ApiJson {
  param(
    [ValidateSet("GET", "POST", "PUT")]
    [string]$Method,
    [string]$Path,
    [object]$Body = $null,
    [int]$TimeoutSec = $RequestTimeoutSeconds
  )
  $uri = if ($Path.StartsWith("http")) { $Path } else { "$ApiBase$Path" }
  $params = @{
    Uri = $uri
    Method = $Method
    Headers = $headers
    TimeoutSec = $TimeoutSec
    ErrorAction = "Stop"
  }
  if ($null -ne $Body) {
    $params.ContentType = "application/json"
    $params.Body = ($Body | ConvertTo-Json -Depth 100)
  }
  return Invoke-RestMethod @params
}

function Invoke-ApiRaw {
  param(
    [ValidateSet("GET", "POST")]
    [string]$Method,
    [string]$Path,
    [string]$Body = "",
    [int]$TimeoutSec = $RequestTimeoutSeconds
  )
  $uri = if ($Path.StartsWith("http")) { $Path } else { "$ApiBase$Path" }
  $params = @{
    Uri = $uri
    Method = $Method
    Headers = $headers
    TimeoutSec = $TimeoutSec
    ErrorAction = "Stop"
    UseBasicParsing = $true
  }
  if ($Body) {
    $params.ContentType = "application/json"
    $params.Body = $Body
  }
  $response = Invoke-WebRequest @params
  return [string]$response.Content
}

function ConvertTo-StringMap {
  param([object]$Value)
  $map = @{}
  if ($null -eq $Value) { return $map }
  foreach ($property in @($Value.PSObject.Properties)) {
    $map[$property.Name] = [string]$property.Value
  }
  return $map
}

function ConvertTo-ModelSettingsPayload {
  param([object]$Settings, [string]$OverrideModel = "")
  $roles = ConvertTo-StringMap $Settings.roles
  $starModels = ConvertTo-StringMap $Settings.star_generator_models
  $defaultModel = [string]$Settings.default_model
  if ($OverrideModel) {
    $defaultModel = $OverrideModel
    foreach ($role in @(
      "planner",
      "generator",
      "reviewer",
      "secondary_reviewer",
      "reporter",
      "diagnostician",
      "hint_generator",
      "hint_reviewer",
      "fault_auditor",
      "set_reviewer",
      "answer_judge",
      "solution_explainer",
      "question_repair_reviewer"
    )) {
      $roles[$role] = $OverrideModel
    }
    foreach ($bucket in @("le_3", "star_4", "star_5")) {
      $starModels[$bucket] = $OverrideModel
    }
  }
  return @{
    provider = [string]$Settings.provider
    base_url = [string]$Settings.base_url
    api_key = $null
    has_api_key = [bool]$Settings.has_api_key
    default_model = $defaultModel
    roles = $roles
    star_generator_models = $starModels
    model_price_json = [string]$Settings.model_price_json
    strict_reviewer_model_allowlist = [string]$Settings.strict_reviewer_model_allowlist
  }
}

function Escape-Part {
  param([string]$Value)
  return [System.Uri]::EscapeDataString($Value)
}

function ConvertTo-CanonicalObject {
  param([object]$Value)
  if ($null -eq $Value) { return $null }
  if ($Value -is [string] -or $Value.GetType().IsPrimitive) { return $Value }
  if ($Value -is [System.Collections.IDictionary]) {
    $ordered = [ordered]@{}
    foreach ($key in @($Value.Keys) | Sort-Object) {
      $ordered[[string]$key] = ConvertTo-CanonicalObject $Value[$key]
    }
    return $ordered
  }
  if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
    return @($Value | ForEach-Object { ConvertTo-CanonicalObject $_ })
  }
  $object = [ordered]@{}
  foreach ($property in @($Value.PSObject.Properties.Name) | Sort-Object) {
    $object[$property] = ConvertTo-CanonicalObject $Value.$property
  }
  return $object
}

function Get-Sha256Hex {
  param([string]$Text)
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
  $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
  return (($hash | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Get-TransferTable {
  param([object]$Transfer, [string]$TableName)
  if ($null -eq $Transfer.tables) { return @() }
  $value = $Transfer.tables.$TableName
  if ($null -eq $value) { return @() }
  return @($value)
}

function Get-StableTransferHash {
  param([object]$Transfer, [string[]]$ExcludedTables = @())
  $excluded = @{}
  foreach ($name in $ExcludedTables) { $excluded[$name] = $true }
  $tables = [ordered]@{}
  $counts = [ordered]@{}
  foreach ($property in @($Transfer.tables.PSObject.Properties.Name) | Sort-Object) {
    if ($excluded.ContainsKey($property)) { continue }
    $rows = Get-TransferTable -Transfer $Transfer -TableName $property
    $tables[$property] = $rows
    $counts[$property] = $rows.Count
  }
  $stable = [ordered]@{
    schema_version = $Transfer.schema_version
    table_counts = $counts
    tables = $tables
  }
  $json = ConvertTo-CanonicalObject $stable | ConvertTo-Json -Depth 100 -Compress
  return Get-Sha256Hex $json
}

function Wait-BatchTerminal {
  param([string]$BatchId, [int]$MaxPolls = 12)
  $terminal = @("completed", "partial", "failed", "cancelled", "timed_out")
  $batch = $null
  for ($index = 0; $index -lt $MaxPolls; $index += 1) {
    $batch = Invoke-ApiJson -Method GET -Path "/api/generation/batches/$(Escape-Part $BatchId)"
    if ($terminal -contains [string]$batch.status) { return $batch }
    Start-Sleep -Seconds 1
  }
  return $batch
}

$checks = [ordered]@{}
$failures = @()

try {
  $health = Invoke-ApiJson -Method GET -Path "/api/health"
  $checks.health = $health
  if ($health.status -ne "ok") {
    $failures += "api_health_not_ok"
    Finish -Checks $checks -Failures $failures
  }

  $modelSettings = Invoke-ApiJson -Method GET -Path "/api/settings/models"
  $checks.model_settings = @{
    provider = $modelSettings.provider
    base_url = $modelSettings.base_url
    default_model = $modelSettings.default_model
    has_api_key = $modelSettings.has_api_key
  }
  if ($UseRealAi) {
    if ($modelSettings.has_api_key -ne $true) {
      $failures += "real_ai_api_key_missing"
      Finish -Checks $checks -Failures $failures
    }
    if ($AiModel) {
      $script:originalModelSettings = $modelSettings
      $updatedModelSettings = Invoke-ApiJson -Method PUT -Path "/api/settings/models" -Body (ConvertTo-ModelSettingsPayload -Settings $modelSettings -OverrideModel $AiModel)
      $script:modelSettingsOverridden = $true
      $checks.model_override = @{
        requested = $AiModel
        default_model = $updatedModelSettings.default_model
        generator = $updatedModelSettings.roles.generator
        reviewer = $updatedModelSettings.roles.reviewer
        reporter = $updatedModelSettings.roles.reporter
      }
    }
  }

  $import = Invoke-ApiJson -Method POST -Path "/api/import/json-files?max_questions=1" -TimeoutSec $ImportTimeoutSeconds
  $checks.import = $import
  if ([int]$import.questions -lt 1) {
    $failures += "bounded_import_did_not_import_question"
    Finish -Checks $checks -Failures $failures
  }

  $learnerName = "release smoke $(Get-Date -Format "yyyyMMdd-HHmmss")"
  $learner = Invoke-ApiJson -Method POST -Path "/api/learners" -Body @{
    name = $learnerName
    settings = @{ smoke = $true; request_id = $requestId }
  }
  $checks.learner = @{ id = $learner.id; name = $learner.name }

  $session = Invoke-ApiJson -Method POST -Path "/api/practice/sessions" -Body @{
    learner_id = $learner.id
    mode = "practice"
    count = 1
    verified_only = $true
    include_generated = $false
  }
  $questionIds = @($session.question_ids)
  $checks.session = @{ id = $session.id; learner_id = $session.learner_id; question_count = $questionIds.Count }
  if ($questionIds.Count -lt 1) {
    $failures += "practice_session_has_no_questions"
    Finish -Checks $checks -Failures $failures
  }

  $questionId = [string]$questionIds[0]
  $question = Invoke-ApiJson -Method GET -Path "/api/questions/$(Escape-Part $questionId)?include_answer=true"
  $canonicalAnswer = [string]$question.answer_spec.canonical_answer
  $checks.question = @{
    id = $question.id
    source_name = $question.source_name
    tag_count = @($question.tags).Count
    answer_type = $question.answer_spec.answer_type
  }
  if (-not $canonicalAnswer) {
    $failures += "smoke_question_missing_canonical_answer"
    Finish -Checks $checks -Failures $failures
  }

  $submit = Invoke-ApiJson -Method POST -Path "/api/practice/sessions/$(Escape-Part $session.id)/submit" -Body @{
    question_id = $questionId
    answer_text = $canonicalAnswer
    elapsed_ms = 1000
    idempotency_key = "smoke-submit-$requestId"
  }
  $checks.submit = @{
    submission_id = $submit.submission_id
    is_correct = $submit.is_correct
    attempt_index = $submit.attempt_index
    state_version = $submit.state_version
  }
  if ($submit.is_correct -ne $true) {
    $failures += "canonical_answer_not_marked_correct"
    Finish -Checks $checks -Failures $failures
  }

  $sessionState = Invoke-ApiJson -Method GET -Path "/api/practice/sessions/$(Escape-Part $session.id)"
  $checks.session_state = @{
    answered_count = $sessionState.answered_count
    status = $sessionState.status
    current_index = $sessionState.current_index
  }
  if ([int]$sessionState.answered_count -lt 1) {
    $failures += "session_state_not_updated"
  }

  $report = Invoke-ApiJson -Method GET -Path "/api/reports/summary?learner_id=$(Escape-Part $learner.id)&practice_session_id=$(Escape-Part $session.id)"
  $checks.report = @{
    answered_questions = $report.submission_summary.answered_questions
    submissions = $report.submission_summary.submissions
    final_accuracy = $report.submission_summary.final_accuracy
  }
  if ([int]$report.submission_summary.answered_questions -lt 1) {
    $failures += "report_did_not_include_smoke_submission"
  }

  $history = Invoke-ApiJson -Method GET -Path "/api/history/groups?learner_id=$(Escape-Part $learner.id)&limit=5"
  $historyRows = @($history)
  $checks.history = @{ group_count = $historyRows.Count }
  if ($historyRows.Count -lt 1) {
    $failures += "history_group_missing_for_smoke_learner"
  }

  $batchDescription = if ($UseRealAi) {
    "release smoke real AI batch: generate one short automatically gradable elementary math question. The answer must be a single integer. Avoid proof, open response, diagrams, and unit-sensitive answers."
  }
  else {
    "release smoke mock batch"
  }
  $batch = Invoke-ApiJson -Method POST -Path "/api/generation/batches" -Body @{
    learner_id = $learner.id
    target_tags = @()
    difficulty_star = 3
    count = 1
    concurrency = 1
    custom_description = $batchDescription
    constraints = $batchDescription
    use_mock = (-not [bool]$UseRealAi)
    prefer_reuse_existing_ai = $false
    candidate_count = $(if ($UseRealAi) { 2 } else { 1 })
    source_example_count = 1
    ai_negative_example_count = 0
    allow_reviewer_quality_downgrade = $true
  }
  $batch = Wait-BatchTerminal -BatchId $batch.id -MaxPolls $GenerationTimeoutSeconds
  $checks.generation_batch = @{
    id = $batch.id
    status = $batch.status
    accepted_count = $batch.accepted_count
    rejected_count = $batch.rejected_count
    failed_count = $batch.failed_count
  }
  if ([int]$batch.accepted_count -lt 1) {
    $failures += "generation_batch_did_not_accept_question"
    Finish -Checks $checks -Failures $failures
  }

  $batchPractice = Invoke-ApiJson -Method POST -Path "/api/generation/batches/$(Escape-Part $batch.id)/practice" -Body @{
    learner_id = $learner.id
  }
  $checks.generation_batch_practice = @{
    session_id = $batchPractice.id
    question_count = @($batchPractice.question_ids).Count
  }
  if (@($batchPractice.question_ids).Count -lt 1) {
    $failures += "mock_generation_batch_practice_missing_questions"
  }

  $batchReport = Invoke-ApiJson -Method GET -Path "/api/reports/summary?learner_id=$(Escape-Part $learner.id)&generation_batch_id=$(Escape-Part $batch.id)"
  $checks.generation_batch_report = @{
    answered_questions = $batchReport.submission_summary.answered_questions
    submissions = $batchReport.submission_summary.submissions
    sample_size = $batchReport.summary_meta.sample_size
  }

  $aiReport = Invoke-ApiJson -Method POST -Path "/api/reports/ai-summary" -Body @{
    learner_id = $learner.id
    practice_session_id = $session.id
    report_type = "session"
  } -TimeoutSec $RequestTimeoutSeconds
  $checks.ai_report = @{
    provider = $aiReport.provider
    model = $aiReport.model
    has_summary = -not [string]::IsNullOrWhiteSpace([string]$aiReport.summary_md)
    error = $aiReport.error
  }
  if ([string]::IsNullOrWhiteSpace([string]$aiReport.summary_md)) {
    $failures += "ai_report_summary_missing"
  }

  $historyExportBeforeRaw = Invoke-ApiRaw -Method GET -Path "/api/admin/history/export" -TimeoutSec $TransferTimeoutSeconds
  $historyExportBefore = $historyExportBeforeRaw | ConvertFrom-Json
  $historyHashBefore = Get-StableTransferHash -Transfer $historyExportBefore -ExcludedTables @("audit_logs")
  $historyImport = (Invoke-ApiRaw -Method POST -Path "/api/admin/history/import?dry_run=true" -Body $historyExportBeforeRaw -TimeoutSec $TransferTimeoutSeconds) | ConvertFrom-Json
  $historyExportAfterRaw = Invoke-ApiRaw -Method GET -Path "/api/admin/history/export" -TimeoutSec $TransferTimeoutSeconds
  $historyExportAfter = $historyExportAfterRaw | ConvertFrom-Json
  $historyHashAfter = Get-StableTransferHash -Transfer $historyExportAfter -ExcludedTables @("audit_logs")
  $checks.history_transfer = @{
    dry_run = $historyImport.dry_run
    status = $historyImport.status
    hash_before = $historyHashBefore
    hash_after = $historyHashAfter
  }
  if ($historyImport.status -ne "dry_run" -or $historyHashBefore -ne $historyHashAfter) {
    $failures += "history_transfer_dry_run_hash_mismatch"
  }

  $bankExportBeforeRaw = Invoke-ApiRaw -Method GET -Path "/api/admin/question-bank/export" -TimeoutSec $TransferTimeoutSeconds
  $bankExportBefore = $bankExportBeforeRaw | ConvertFrom-Json
  $bankHashBefore = Get-StableTransferHash -Transfer $bankExportBefore
  $bankImport = (Invoke-ApiRaw -Method POST -Path "/api/admin/question-bank/import?dry_run=true" -Body $bankExportBeforeRaw -TimeoutSec $TransferTimeoutSeconds) | ConvertFrom-Json
  $bankExportAfterRaw = Invoke-ApiRaw -Method GET -Path "/api/admin/question-bank/export" -TimeoutSec $TransferTimeoutSeconds
  $bankExportAfter = $bankExportAfterRaw | ConvertFrom-Json
  $bankHashAfter = Get-StableTransferHash -Transfer $bankExportAfter
  $checks.question_bank_transfer = @{
    dry_run = $bankImport.dry_run
    status = $bankImport.status
    hash_before = $bankHashBefore
    hash_after = $bankHashAfter
  }
  if ($bankImport.status -ne "dry_run" -or $bankHashBefore -ne $bankHashAfter) {
    $failures += "question_bank_transfer_dry_run_hash_mismatch"
  }

  Finish -Checks $checks -Failures $failures
}
catch {
  $responseBody = $null
  try {
    if ($_.Exception.Response -and $_.Exception.Response.GetResponseStream()) {
      $reader = [System.IO.StreamReader]::new($_.Exception.Response.GetResponseStream())
      $responseBody = $reader.ReadToEnd()
      $reader.Dispose()
    }
  }
  catch {
    $responseBody = $null
  }
  $checks.exception = @{
    message = $_.Exception.Message
    type = $_.Exception.GetType().FullName
    response_body = $responseBody
  }
  $failures += "unhandled_exception"
  Finish -Checks $checks -Failures $failures
}
