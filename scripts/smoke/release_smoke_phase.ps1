param(
  [string]$ReleaseRoot = "",

  [ValidateSet("preflight", "after-start", "doctor", "after-stop")]
  [string]$Phase = "preflight",

  [string]$DataDir = "",

  [string]$SmokeProfile = "",

  [int]$TimeoutSeconds = 30
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

function New-Result {
  param([bool]$Ok, [hashtable]$Checks, [array]$Failures)
  return [ordered]@{
    schema_version = "release_smoke_phase_v1"
    phase = $Phase
    ok = $Ok
    release_root = (Resolve-Path -LiteralPath $ReleaseRoot -ErrorAction SilentlyContinue).Path
    checks = $Checks
    failures = $Failures
  }
}

function Finish {
  param([hashtable]$Checks, [array]$Failures)
  $result = New-Result -Ok ($Failures.Count -eq 0) -Checks $Checks -Failures $Failures
  $result | ConvertTo-Json -Depth 12
  if ($Failures.Count -gt 0) { exit 1 }
}

function Test-RequiredFile {
  param([string]$RelativePath)
  $path = Join-Path $ReleaseRoot $RelativePath
  return [ordered]@{
    path = $RelativePath
    exists = Test-Path -LiteralPath $path -PathType Leaf
  }
}

function Invoke-JsonCommand {
  param([string]$Exe, [string[]]$Arguments)
  $process = [System.Diagnostics.Process]::new()
  $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = $Exe
  foreach ($arg in $Arguments) {
    [void]$startInfo.ArgumentList.Add($arg)
  }
  $startInfo.WorkingDirectory = $ReleaseRoot
  $startInfo.UseShellExecute = $false
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $startInfo.CreateNoWindow = $true
  $process.StartInfo = $startInfo
  if (-not $process.Start()) {
    throw "Failed to start $Exe"
  }
  if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
    try { $process.Kill($true) } catch { $process.Kill() }
    throw "Command timed out after $TimeoutSeconds seconds: $Exe $($Arguments -join ' ')"
  }
  $stdout = $process.StandardOutput.ReadToEnd()
  $stderr = $process.StandardError.ReadToEnd()
  if ($process.ExitCode -ne 0) {
    throw "Command failed with exit code $($process.ExitCode): $stderr"
  }
  return $stdout | ConvertFrom-Json
}

function Invoke-HealthJson {
  param([string]$Url)
  try {
    return Invoke-RestMethod -Uri $Url -TimeoutSec ([Math]::Min($TimeoutSeconds, 30))
  }
  catch {
    return @{ status = "failed"; error = $_.Exception.Message }
  }
}

$ReleaseRoot = (Resolve-Path -LiteralPath $ReleaseRoot -ErrorAction Stop).Path
$launcher = Join-Path $ReleaseRoot "launcher\MathPlatform.exe"
$launcherProfileArgs = @()
if ($DataDir) {
  $launcherProfileArgs += @("--data-dir", $DataDir)
} elseif ($SmokeProfile) {
  $launcherProfileArgs += @("--smoke-profile", $SmokeProfile)
}
$checks = [ordered]@{}
$failures = @()

if ($Phase -eq "preflight") {
  $required = @(
    ".env",
    "release_manifest.json",
    "launcher\MathPlatform.exe",
    "api\math-api.exe",
    "web\node.exe",
    "web\server.js",
    "start.cmd",
    "check.cmd",
    "stop.cmd",
    "backup.cmd",
    "restore.cmd"
  )
  $checks.required_files = @()
  foreach ($relative in $required) {
    $check = Test-RequiredFile $relative
    $checks.required_files += $check
    if (-not $check.exists) {
      $failures += "missing_required_file:$relative"
    }
  }
  $checks.cmd_pause_free = @()
  foreach ($relative in @("start.cmd", "check.cmd", "stop.cmd", "backup.cmd", "restore.cmd", "START_HERE.cmd")) {
    $path = Join-Path $ReleaseRoot $relative
    if (Test-Path -LiteralPath $path -PathType Leaf) {
      $hasPause = [bool](Select-String -LiteralPath $path -Pattern '(^|\s)pause(\s|$)' -Quiet)
      $checks.cmd_pause_free += [ordered]@{ path = $relative; pause_free = -not $hasPause }
      if ($hasPause) {
        $failures += "cmd_contains_pause:$relative"
      }
    }
  }
  try {
    $manifest = Get-Content -LiteralPath (Join-Path $ReleaseRoot "release_manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $checks.release_manifest = [ordered]@{
      exists = $true
      manifest_version = $manifest.manifest_version
      file_count = @($manifest.files).Count
    }
  }
  catch {
    $checks.release_manifest = [ordered]@{ exists = $false; error = $_.Exception.Message }
    $failures += "release_manifest_unreadable"
  }
  Finish -Checks $checks -Failures $failures
}

if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
  Finish -Checks $checks -Failures @("missing_launcher")
}

if ($Phase -eq "after-start") {
  $status = Invoke-JsonCommand -Exe $launcher -Arguments (@("status", "--json") + $launcherProfileArgs)
  $apiBase = "http://127.0.0.1:8001"
  $webBase = "http://127.0.0.1:3000"
  if ($status.urls -and $status.urls.api) { $apiBase = [string]$status.urls.api }
  if ($status.urls -and $status.urls.web) { $webBase = [string]$status.urls.web }
  $apiHealth = Invoke-HealthJson "$apiBase/api/health"
  $webHealth = $false
  try {
    $webResponse = Invoke-WebRequest -Uri "$webBase/" -TimeoutSec ([Math]::Min($TimeoutSeconds, 30)) -UseBasicParsing
    $webHealth = $webResponse.StatusCode -ge 200 -and $webResponse.StatusCode -lt 500
  }
  catch {
    $webHealth = $false
  }
  $checks.status = $status
  $checks.api_health = $apiHealth
  $checks.web_health = $webHealth
  if (-not $status.api_ok) { $failures += "api_not_ready" }
  if (-not $status.web_ok) { $failures += "web_not_ready" }
  if ($apiHealth.status -ne "ok") { $failures += "api_health_failed" }
  if (-not $webHealth) { $failures += "web_health_failed" }
  Finish -Checks $checks -Failures $failures
}

if ($Phase -eq "doctor") {
  $doctor = Invoke-JsonCommand -Exe $launcher -Arguments (@("doctor", "--json") + $launcherProfileArgs)
  $checks.doctor = $doctor
  if (-not $doctor.ok) { $failures += "doctor_not_ok" }
  Finish -Checks $checks -Failures $failures
}

if ($Phase -eq "after-stop") {
  $status = Invoke-JsonCommand -Exe $launcher -Arguments (@("status", "--json") + $launcherProfileArgs)
  $checks.status = $status
  if ([int]$status.process_count -ne 0) { $failures += "release_processes_still_running" }
  Finish -Checks $checks -Failures $failures
}
