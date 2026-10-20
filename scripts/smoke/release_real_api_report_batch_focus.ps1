param(
  [string]$ApiBase = "http://127.0.0.1:8001",
  [string]$AiModel = "gpt-5.6-sol",
  [int]$RequestTimeoutSeconds = 90,
  [int]$GenerationTimeoutSeconds = 420
)

$ErrorActionPreference = "Stop"
$requestId = "real-api-report-batch-focus-$([guid]::NewGuid().ToString("N"))"
$headers = @{
  "X-Math-Platform-Client" = "math-platform-web"
  "X-Request-Id" = $requestId
}
$script:originalModelSettings = $null
$script:modelSettingsOverridden = $false

function Invoke-ApiJson {
  param(
    [ValidateSet("GET", "POST", "PUT")]
    [string]$Method,
    [string]$Path,
    [object]$Body = $null,
    [int]$TimeoutSec = $RequestTimeoutSeconds
  )
  $uri = if ($Path.StartsWith("http")) { $Path } else { "$ApiBase$Path" }
  $request = [System.Net.HttpWebRequest]::Create($uri)
  $request.Method = $Method
  $request.Timeout = $TimeoutSec * 1000
  $request.ReadWriteTimeout = $TimeoutSec * 1000
  $request.Accept = "application/json"
  foreach ($header in $headers.GetEnumerator()) {
    $request.Headers[$header.Key] = [string]$header.Value
  }
  if ($null -ne $Body) {
    $json = $Body | ConvertTo-Json -Depth 100
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $request.ContentType = "application/json; charset=utf-8"
    $request.ContentLength = $bytes.Length
    $stream = $request.GetRequestStream()
    try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
  }
  $response = $request.GetResponse()
  try {
    $stream = $response.GetResponseStream()
    $memory = [System.IO.MemoryStream]::new()
    try {
      $stream.CopyTo($memory)
      $text = [System.Text.Encoding]::UTF8.GetString($memory.ToArray())
    }
    finally {
      if ($stream) { $stream.Dispose() }
      $memory.Dispose()
    }
    if ([string]::IsNullOrWhiteSpace($text)) { return $null }
    return $text | ConvertFrom-Json
  }
  finally {
    $response.Dispose()
  }
}

function Escape-Part([string]$Value) {
  return [System.Uri]::EscapeDataString($Value)
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
    foreach ($role in @("generator", "reviewer", "reporter", "answer_judge", "solution_explainer", "hint_generator", "hint_reviewer")) {
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
    default_model = $defaultModel
    roles = $roles
    star_generator_models = $starModels
    model_price_json = [string]$Settings.model_price_json
    strict_reviewer_model_allowlist = [string]$Settings.strict_reviewer_model_allowlist
  }
}

function Restore-ModelSettings {
  param([hashtable]$Checks)
  if ($script:modelSettingsOverridden -and $null -ne $script:originalModelSettings) {
    $restored = Invoke-ApiJson -Method PUT -Path "/api/settings/models" -Body (ConvertTo-ModelSettingsPayload $script:originalModelSettings)
    $Checks.model_restore = @{ ok = $true; default_model = $restored.default_model }
    $script:modelSettingsOverridden = $false
  }
}

function Wait-BatchTerminal {
  param([string]$BatchId, [int]$MaxSeconds = 900)
  $terminal = @("completed", "partial", "failed", "cancelled", "canceled", "timed_out")
  $deadline = (Get-Date).AddSeconds($MaxSeconds)
  do {
    $batch = Invoke-ApiJson -Method GET -Path "/api/generation/batches/$(Escape-Part $BatchId)" -TimeoutSec $RequestTimeoutSeconds
    if ($terminal -contains [string]$batch.status) { return $batch }
    Start-Sleep -Seconds 3
  } while ((Get-Date) -lt $deadline)
  if ($batch -and $batch.id) {
    try {
      $null = Invoke-ApiJson -Method POST -Path "/api/generation/batches/$(Escape-Part $BatchId)/cancel" -Body @{
        reason = "smoke_timeout"
        source = "release_real_api_report_batch_focus"
      } -TimeoutSec ([Math]::Min($RequestTimeoutSeconds, 30))
    } catch {
      $script:lastBatchCancelError = $_.Exception.Message
    }
    try {
      $batch = Invoke-ApiJson -Method GET -Path "/api/generation/batches/$(Escape-Part $BatchId)" -TimeoutSec ([Math]::Min($RequestTimeoutSeconds, 30))
    } catch {
      $script:lastBatchRefreshError = $_.Exception.Message
    }
  }
  return $batch
}

function Finish {
  param([hashtable]$Checks, [string[]]$Failures)
  try { Restore-ModelSettings $Checks } catch { $Checks.model_restore = @{ ok = $false; error = $_.Exception.Message } }
  $result = [ordered]@{
    schema_version = "real_api_report_batch_focus_v1"
    ok = ($Failures.Count -eq 0)
    api_base = $ApiBase
    ai_model = $AiModel
    request_id = $requestId
    checks = $Checks
    failures = $Failures
  }
  $result | ConvertTo-Json -Depth 80
  if ($Failures.Count -gt 0) { exit 1 }
  exit 0
}

$checks = @{}
$failures = @()

try {
  $checks.health = Invoke-ApiJson -Method GET -Path "/api/health"
  $settings = Invoke-ApiJson -Method GET -Path "/api/settings/models"
  $script:originalModelSettings = $settings
  $override = Invoke-ApiJson -Method PUT -Path "/api/settings/models" -Body (ConvertTo-ModelSettingsPayload $settings $AiModel)
  $script:modelSettingsOverridden = $true
  $checks.model_override = @{ default_model = $override.default_model; generator = $override.roles.generator; reviewer = $override.roles.reviewer; reporter = $override.roles.reporter }

  $learner = Invoke-ApiJson -Method POST -Path "/api/learners" -Body @{ name = "report batch focus $(Get-Date -Format yyyyMMdd-HHmmss)" }
  $checks.learner = @{ id = $learner.id; name = $learner.name }
  $practice = Invoke-ApiJson -Method POST -Path "/api/practice/sessions" -Body @{
    learner_id = $learner.id
    mode = "practice"
    count = 5
    verified_only = $true
    include_generated = $false
    idempotency_key = "practice-$requestId"
  }
  $questionId = [string]@($practice.question_ids)[0]
  $question = Invoke-ApiJson -Method GET -Path "/api/questions/$(Escape-Part $questionId)?include_answer=true"
  $wrongAnswer = if ([string]$question.answer_spec.canonical_answer -eq "0") { "1" } else { "0" }
  $submit = Invoke-ApiJson -Method POST -Path "/api/practice/sessions/$(Escape-Part $practice.id)/submit" -Body @{
    question_id = $questionId
    answer_text = $wrongAnswer
    thought_text = "I am testing report-driven generation with a real model."
    elapsed_ms = 1000
    idempotency_key = "submit-$requestId"
  } -TimeoutSec $RequestTimeoutSeconds
  $checks.practice = @{ session_id = $practice.id; question_id = $questionId; submitted_correct = $submit.is_correct }

  $report = Invoke-ApiJson -Method POST -Path "/api/reports/ai-summary" -Body @{
    report_type = "session"
    learner_id = $learner.id
    practice_session_id = $practice.id
  } -TimeoutSec $RequestTimeoutSeconds
  $checks.ai_report = @{ provider = $report.provider; model = $report.model; has_summary = -not [string]::IsNullOrWhiteSpace([string]$report.summary_md) }

  $plan = Invoke-ApiJson -Method POST -Path "/api/reports/batch-plan" -Body @{
    report_type = "session"
    learner_id = $learner.id
    practice_session_id = $practice.id
    count = 1
    user_guidance = "Prefer a diagnostic 3-star numeric-answer consolidation question. Avoid copying the source story."
  } -TimeoutSec $RequestTimeoutSeconds
  $checks.report_batch_plan = @{ count = $plan.recommended_count; star = $plan.difficulty_star; tag_count = @($plan.target_tags).Count; has_parent_guidance = -not [string]::IsNullOrWhiteSpace([string]$plan.parent_guidance) }

  $batch = Invoke-ApiJson -Method POST -Path "/api/generation/batches" -Body @{
    target_tags = @($plan.target_tags)
    difficulty_star = [int]$plan.difficulty_star
    count = 1
    concurrency = 1
    constraints = [string]$plan.constraints
    custom_description = [string]$plan.custom_description
    solution_style = "elementary_preferred"
    use_mock = $false
    prefer_reuse_existing_ai = $false
    candidate_count = 1
    source_example_count = 1
    ai_negative_example_count = 1
    allow_reviewer_quality_downgrade = $true
  } -TimeoutSec $RequestTimeoutSeconds
  $batch = Wait-BatchTerminal -BatchId $batch.id -MaxSeconds $GenerationTimeoutSeconds
  $checks.report_driven_batch = @{ id = $batch.id; status = $batch.status; accepted_count = $batch.accepted_count; rejected_count = $batch.rejected_count; failed_count = $batch.failed_count }
  if ([int]$batch.accepted_count -lt 1) { $failures += "report_driven_batch_no_accepted_question" }

  Finish $checks $failures
}
catch {
  $checks.exception = @{ message = $_.Exception.Message; type = $_.Exception.GetType().FullName }
  $failures += "unhandled_exception"
  Finish $checks $failures
}
