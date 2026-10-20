param(
  [Parameter(Mandatory = $true)]
  [string]$Command,

  [int]$TimeoutSeconds = 120,

  [string]$WorkingDirectory = (Get-Location).Path,

  [string]$Name = "task"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $env:TEMP "math-platform-bounded"
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$safeName = ($Name -replace '[^A-Za-z0-9_.-]', '_')
$runId = "$safeName-$timestamp"
$stdoutPath = Join-Path $logDirectory "$runId.stdout.log"
$stderrPath = Join-Path $logDirectory "$runId.stderr.log"
$metaPath = Join-Path $logDirectory "$runId.meta.json"

function Add-ContentSafe {
  param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$Value
  )
  for ($i = 0; $i -lt 3; $i++) {
    try {
      Add-Content -LiteralPath $Path -Value $Value -Encoding UTF8 -ErrorAction Stop
      return
    } catch {
      Start-Sleep -Milliseconds 150
    }
  }
  Write-Warning "Could not append to log file after timeout: $Path"
}
$tempCmd = Join-Path $logDirectory "$runId.cmd"

$cmdBody = @"
@echo off
$Command 1>>"$stdoutPath" 2>>"$stderrPath"
exit /b %ERRORLEVEL%
"@

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($tempCmd, $cmdBody, $utf8NoBom)

$process = [System.Diagnostics.Process]::new()
$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $env:ComSpec
$startInfo.Arguments = "/d /c `"$tempCmd`""
$startInfo.WorkingDirectory = $WorkingDirectory
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$process.StartInfo = $startInfo

$startedAt = Get-Date

try {
  if (-not $process.Start()) {
    throw "Failed to start command."
  }

  $rootPid = $process.Id
  $meta = [ordered]@{
    run_id = $runId
    command = $Command
    working_directory = $WorkingDirectory
    timeout_seconds = $TimeoutSeconds
    root_pid = $rootPid
    started_at = $startedAt.ToString("o")
    stdout = $stdoutPath
    stderr = $stderrPath
    status = "running"
  }
  $meta | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $metaPath -Encoding UTF8

  if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
    $killExitCode = $null
    try {
      $killOutput = & "$env:SystemRoot\System32\taskkill.exe" /PID $rootPid /T /F 2>&1
      $killExitCode = $LASTEXITCODE
    }
    catch {
      $killOutput = $_ | Out-String
      $killExitCode = 1
    }
    if ($killOutput) {
      Add-ContentSafe -Path $stderrPath -Value ($killOutput | Out-String)
    }
    Start-Sleep -Milliseconds 750
    $meta.status = "timed_out"
    $meta.finished_at = (Get-Date).ToString("o")
    $meta.exit_code = 124
    $meta.taskkill_exit_code = $killExitCode
    $meta | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $metaPath -Encoding UTF8
    Write-Host "TIMEOUT: $Command"
    Write-Host "stdout: $stdoutPath"
    Write-Host "stderr: $stderrPath"
    exit 124
  }

  $exitCode = $process.ExitCode
  $meta.status = if ($exitCode -eq 0) { "succeeded" } else { "failed" }
  $meta.finished_at = (Get-Date).ToString("o")
  $meta.exit_code = $exitCode
  $meta | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $metaPath -Encoding UTF8

  if (Test-Path -LiteralPath $stdoutPath) {
    Get-Content -LiteralPath $stdoutPath -Encoding UTF8
  }
  if ($exitCode -ne 0 -and (Test-Path -LiteralPath $stderrPath)) {
    Get-Content -LiteralPath $stderrPath -Encoding UTF8 | Write-Error
  }
  exit $exitCode
}
finally {
  if (Test-Path -LiteralPath $tempCmd) {
    Remove-Item -LiteralPath $tempCmd -Force -ErrorAction SilentlyContinue
  }
  if ($process) {
    $process.Dispose()
  }
}
