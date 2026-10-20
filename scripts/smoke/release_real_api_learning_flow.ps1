param(
  [string]$ApiBase = "http://127.0.0.1:8001",
  [string]$AiModel = "gpt-5.6-sol",
  [int]$RequestTimeoutSeconds = 90,
  [int]$GenerationTimeoutSeconds = 420
)

$ErrorActionPreference = "Stop"

$requestId = "real-api-learning-flow-$([guid]::NewGuid().ToString("N"))"
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
    try {
      $stream.Write($bytes, 0, $bytes.Length)
    }
    finally {
      $stream.Dispose()
    }
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
    if ([string]::IsNullOrWhiteSpace($text)) {
      return $null
    }
    return $text | ConvertFrom-Json
  }
  finally {
    $response.Dispose()
  }
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

function Finish {
  param([hashtable]$Checks, [array]$Failures)
  if ($script:modelSettingsOverridden -and $null -ne $script:originalModelSettings) {
    try {
      Invoke-ApiJson -Method PUT -Path "/api/settings/models" -Body (ConvertTo-ModelSettingsPayload -Settings $script:originalModelSettings) | Out-Null
      $Checks.model_restore = @{ ok = $true; default_model = $script:originalModelSettings.default_model }
      $script:modelSettingsOverridden = $false
    }
    catch {
      $Checks.model_restore = @{ ok = $false; error = $_.Exception.Message }
      $Failures += "model_settings_restore_failed"
    }
  }
  $result = [ordered]@{
    schema_version = "real_api_learning_flow_v1"
    ok = $Failures.Count -eq 0
    api_base = $ApiBase
    ai_model = $AiModel
    request_id = $requestId
    checks = $Checks
    failures = $Failures
  }
  $result | ConvertTo-Json -Depth 16
  if ($Failures.Count -gt 0) { exit 1 }
}

function Escape-Part {
  param([string]$Value)
  return [System.Uri]::EscapeDataString($Value)
}

function Wait-BatchTerminal {
  param([string]$BatchId, [int]$MaxSeconds = 360)
  $terminal = @("completed", "partial", "failed", "cancelled", "canceled", "timed_out")
  $deadline = (Get-Date).AddSeconds($MaxSeconds)
  $batch = $null
  do {
    $batch = Invoke-ApiJson -Method GET -Path "/api/generation/batches/$(Escape-Part $BatchId)" -TimeoutSec $RequestTimeoutSeconds
    if ($terminal -contains [string]$batch.status) { return $batch }
    Start-Sleep -Seconds 2
  } while ((Get-Date) -lt $deadline)
  if ($batch -and $batch.id) {
    try {
      $null = Invoke-ApiJson -Method POST -Path "/api/generation/batches/$(Escape-Part $BatchId)/cancel" -Body @{
        reason = "smoke_timeout"
        source = "release_real_api_learning_flow"
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

$checks = [ordered]@{}
$failures = @()

try {
  $health = Invoke-ApiJson -Method GET -Path "/api/health"
  $checks.health = $health
  if ($health.status -ne "ok") { $failures += "api_health_not_ok"; Finish $checks $failures }

  $modelSettings = Invoke-ApiJson -Method GET -Path "/api/settings/models"
  $checks.model_settings = @{ default_model = $modelSettings.default_model; has_api_key = $modelSettings.has_api_key; provider = $modelSettings.provider }
  if ($modelSettings.has_api_key -ne $true) { $failures += "real_ai_api_key_missing"; Finish $checks $failures }
  if ($AiModel) {
    $script:originalModelSettings = $modelSettings
    $updated = Invoke-ApiJson -Method PUT -Path "/api/settings/models" -Body (ConvertTo-ModelSettingsPayload -Settings $modelSettings -OverrideModel $AiModel)
    $script:modelSettingsOverridden = $true
    $checks.model_override = @{ default_model = $updated.default_model; generator = $updated.roles.generator; reviewer = $updated.roles.reviewer; reporter = $updated.roles.reporter }
  }

  $learner = Invoke-ApiJson -Method POST -Path "/api/learners" -Body @{
    name = "real api flow $(Get-Date -Format "yyyyMMdd-HHmmss")"
    settings = @{ smoke = $true; request_id = $requestId }
  }
  $checks.learner = @{ id = $learner.id; name = $learner.name }

  $practice = Invoke-ApiJson -Method POST -Path "/api/practice/sessions" -Body @{
    learner_id = $learner.id
    mode = "practice"
    count = 10
    verified_only = $true
    include_generated = $false
    idempotency_key = "practice-$requestId"
  }
  $practiceQuestionIds = @($practice.question_ids)
  $checks.practice_session = @{ id = $practice.id; question_count = $practiceQuestionIds.Count }
  if ($practiceQuestionIds.Count -lt 1) { $failures += "practice_session_empty"; Finish $checks $failures }

  $questionId = [string]$practiceQuestionIds[0]
  $question = $null
  $singleNumericTypes = @("integer", "number", "fraction", "rational", "decimal")
  foreach ($candidateQuestionId in $practiceQuestionIds) {
    $candidateQuestion = Invoke-ApiJson -Method GET -Path "/api/questions/$(Escape-Part ([string]$candidateQuestionId))?include_answer=true"
    if ($singleNumericTypes -contains [string]$candidateQuestion.answer_spec.answer_type) {
      $questionId = [string]$candidateQuestionId
      $question = $candidateQuestion
      break
    }
  }
  if ($null -eq $question) {
    $question = Invoke-ApiJson -Method GET -Path "/api/questions/$(Escape-Part $questionId)?include_answer=true"
  }
  $answer = [string]$question.answer_spec.canonical_answer
  $wrongAnswer = if ($answer -eq "0") { "1" } else { "0" }
  $checks.practice_question = @{ id = $question.id; answer_type = $question.answer_spec.answer_type; star = $question.difficulty_star }

  $checks.current_step = "answer_judge"
  $judge = Invoke-ApiJson -Method POST -Path "/api/practice/sessions/$(Escape-Part $practice.id)/judge-answer" -Body @{
    question_id = $questionId
    answer_text = $wrongAnswer
    thought_text = "I guessed this answer and want the checker to compare it with the official answer format."
    at_ms = 0
  } -TimeoutSec $RequestTimeoutSeconds
  $checks.answer_judge = @{ provider = $judge.provider; model = $judge.model; verdict = $judge.verdict; has_error = [bool]$judge.error }

  $checks.current_step = "wrong_submissions"
  for ($i = 1; $i -le 3; $i += 1) {
    $submit = Invoke-ApiJson -Method POST -Path "/api/practice/sessions/$(Escape-Part $practice.id)/submit" -Body @{
      question_id = $questionId
      answer_text = "$wrongAnswer-$i"
      thought_text = "I may be mixing up the condition order. Attempt $i."
      elapsed_ms = 1000
      idempotency_key = "wrong-$i-$requestId"
    } -TimeoutSec $RequestTimeoutSeconds
    if ($i -eq 3) { $checks.wrong_submit = @{ is_correct = $submit.is_correct; effective_error_count = $submit.effective_error_count } }
  }

  $practiceState = Invoke-ApiJson -Method GET -Path "/api/practice/sessions/$(Escape-Part $practice.id)"
  $hintRows = @($practiceState.active_interventions | Where-Object { $_.intervention_type -ne "mastery_check" })
  $checks.temporary_hint = @{ active_count = $hintRows.Count; temporary_payload = $false }
  if ($hintRows.Count -gt 0) {
    $hint = $hintRows[0]
    $checks.temporary_hint.temporary_payload = ([string]$hint.payload.storage -eq "temporary_hint_ai")
    if ($hint.payload.hint_question.answer_spec.canonical_answer) {
      $hintAnswer = [string]$hint.payload.hint_question.answer_spec.canonical_answer
      $checks.current_step = "temporary_hint_answer"
      $hintSubmit = Invoke-ApiJson -Method POST -Path "/api/practice/sessions/$(Escape-Part $practice.id)/interventions/$(Escape-Part $hint.id)/hint-answer" -Body @{
        answer_text = $hintAnswer
        thought_text = "I solved the smaller hint question."
        at_ms = 0
        idempotency_key = "hint-$requestId"
      }
      $checks.temporary_hint_submit = @{ is_correct = $hintSubmit.is_correct; attempt_count = $hintSubmit.attempt_count }
    }
  }

  $checks.current_step = "reveal_answer"
  $reveal = Invoke-ApiJson -Method POST -Path "/api/practice/sessions/$(Escape-Part $practice.id)/questions/$(Escape-Part $questionId)/reveal-answer" -Body @{
    reason = "real_api_smoke_personalized_explanation"
    at_ms = 0
  }
  $checks.reveal_answer = @{ answer_locked = $reveal.answer_locked; reached_level = $reveal.reached_level }

  $checks.current_step = "personalized_explanation"
  $explain = Invoke-ApiJson -Method POST -Path "/api/practice/sessions/$(Escape-Part $practice.id)/questions/$(Escape-Part $questionId)/solution-explanation" -Body @{
    question_id = $questionId
    message = "Please generate a personalized explanation for an elementary student based on my attempts and thought text."
    at_ms = 0
  } -TimeoutSec $RequestTimeoutSeconds
  $checks.personalized_explanation = @{ status = $explain.status; provider = $explain.provider; model = $explain.model; has_explanation = -not [string]::IsNullOrWhiteSpace([string]$explain.explanation) }

  $checks.current_step = "mastery_check"
  $mastery = Invoke-ApiJson -Method POST -Path "/api/practice/sessions/$(Escape-Part $practice.id)/questions/$(Escape-Part $questionId)/mastery-check" -Body @{
    at_ms = 0
    extra_tag_ids = @()
    count = 1
    candidate_count = 1
    run_now = $true
    use_mock = $false
  } -TimeoutSec $GenerationTimeoutSeconds
  $masteryRows = @($mastery.active_interventions | Where-Object { $_.intervention_type -eq "mastery_check" -and $_.original_question_id -eq $questionId })
  $checks.mastery_check = @{
    requested = 1
    records = $masteryRows.Count
    ready = @($masteryRows | Where-Object { $_.prefetch_status -eq "ready" -or $_.payload.state -eq "ready_to_start" }).Count
    failed = @($masteryRows | Where-Object { $_.prefetch_status -eq "failed" -or $_.payload.state -eq "failed" }).Count
  }

  $checks.current_step = "strict_test_session"
  $strict = Invoke-ApiJson -Method POST -Path "/api/practice/sessions" -Body @{
    learner_id = $learner.id
    mode = "strict_test"
    count = 3
    star_quota = @{ "1" = 1; "2" = 1; "3" = 1 }
    verified_only = $true
    include_generated = $false
    strict_source_mode = "bank_only"
    idempotency_key = "strict-$requestId"
  }
  $strictState = Invoke-ApiJson -Method GET -Path "/api/practice/sessions/$(Escape-Part $strict.id)"
  $checks.strict_test = @{
    id = $strict.id
    question_count = @($strict.question_ids).Count
    mode = $strictState.mode
    answer_type_policy = $strictState.config.selection.policy.answer_type_policy
    breakpoints = @($strictState.breakpoints).Count
  }

  $checks.current_step = "ai_report"
  $aiReport = Invoke-ApiJson -Method POST -Path "/api/reports/ai-summary" -Body @{
    report_type = "session"
    learner_id = $learner.id
    practice_session_id = $practice.id
  } -TimeoutSec $RequestTimeoutSeconds
  $checks.ai_report = @{ provider = $aiReport.provider; model = $aiReport.model; has_summary = -not [string]::IsNullOrWhiteSpace([string]$aiReport.summary_md); confidence = $aiReport.confidence }

  $checks.current_step = "report_batch_plan"
  $plan = Invoke-ApiJson -Method POST -Path "/api/reports/batch-plan" -Body @{
    report_type = "session"
    learner_id = $learner.id
    practice_session_id = $practice.id
    count = 1
  } -TimeoutSec $RequestTimeoutSeconds
  $checks.report_batch_plan = @{ count = $plan.recommended_count; star = $plan.difficulty_star; tag_count = @($plan.target_tags).Count; has_constraints = -not [string]::IsNullOrWhiteSpace([string]$plan.constraints) }

  $checks.current_step = "report_driven_batch_start"
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
  $checks.current_step = "report_driven_batch_wait"
  $batch = Wait-BatchTerminal -BatchId $batch.id -MaxSeconds $GenerationTimeoutSeconds
  $checks.report_driven_batch = @{ id = $batch.id; status = $batch.status; accepted_count = $batch.accepted_count; rejected_count = $batch.rejected_count; failed_count = $batch.failed_count }
  if ([int]$batch.accepted_count -lt 1) { $failures += "report_driven_batch_no_accepted_question" }

  Finish $checks $failures
}
catch {
  $responseBody = $null
  try {
    if ($_.Exception.Response -and $_.Exception.Response.GetResponseStream()) {
      $reader = [System.IO.StreamReader]::new($_.Exception.Response.GetResponseStream(), [System.Text.Encoding]::UTF8)
      $responseBody = $reader.ReadToEnd()
      $reader.Dispose()
    }
  }
  catch {
    $responseBody = $null
  }
  $checks.exception = @{ message = $_.Exception.Message; type = $_.Exception.GetType().FullName; response_body = $responseBody }
  $failures += "unhandled_exception"
  Finish $checks $failures
}
