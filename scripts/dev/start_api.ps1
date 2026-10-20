param(
  [int]$Port = 8001,
  [int]$TimeoutSec = 30,
  [switch]$Restart
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$tmp = Join-Path $root ".tmp"
$pidFile = Join-Path $tmp "dev-api.pid"
$outLog = Join-Path $tmp "dev-api.out.log"
$errLog = Join-Path $tmp "dev-api.err.log"
$envFile = Join-Path $root ".env"

New-Item -ItemType Directory -Force -Path $tmp | Out-Null
if (-not (Test-Path -LiteralPath $envFile)) {
  Copy-Item -LiteralPath (Join-Path $root ".env.example") -Destination $envFile
}

function Get-LogTail([string]$Path, [int]$Lines = 40) {
  if (Test-Path -LiteralPath $Path) {
    return (Get-Content -LiteralPath $Path -Tail $Lines -ErrorAction SilentlyContinue) -join "`n"
  }
  return ""
}

function Normalize-ProcessEnvironment([System.Diagnostics.ProcessStartInfo]$ProcessInfo) {
  $envMap = $ProcessInfo.Environment
  if (-not $envMap) {
    $envMap = $ProcessInfo.EnvironmentVariables
  }
  $pathValue = $null
  foreach ($key in @("Path", "PATH")) {
    if ($envMap.ContainsKey($key)) {
      if (-not $pathValue) {
        $pathValue = $envMap[$key]
      }
      $envMap.Remove($key) | Out-Null
    }
  }
  if (-not $pathValue) {
    $pathValue = [Environment]::GetEnvironmentVariable("Path", "Process")
  }
  if ($pathValue) {
    $envMap["Path"] = $pathValue
  }
}

function Set-ListeningPidFile([int]$ListenPort) {
  $line = netstat -ano | Select-String "127.0.0.1:$ListenPort\s+0.0.0.0:0\s+LISTENING" | Select-Object -First 1
  if ($line) {
    $listenerPid = (($line.ToString() -split "\s+") | Where-Object { $_ })[-1]
    if ($listenerPid) {
      Set-Content -LiteralPath $pidFile -Value $listenerPid -Encoding UTF8
    }
  }
}

function Test-PythonModule([string]$PythonExe, [string]$ModuleName) {
  try {
    $check = & $PythonExe -c "import $ModuleName; print('ok')" 2>$null
    return ($LASTEXITCODE -eq 0 -and $check -eq "ok")
  } catch {
    return $false
  }
}

$candidatePorts = @($Port, 8010, 8020, 8030, 8040) | Select-Object -Unique
$selectedPort = $null
foreach ($candidate in $candidatePorts) {
  try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$candidate/api/health" -TimeoutSec 2
    if ($health.status -eq "ok" -and -not $Restart) {
      Set-ListeningPidFile $candidate
      & (Join-Path $PSScriptRoot "Set-EnvValue.ps1") -Path $envFile -Key "NEXT_PUBLIC_API_BASE" -Value "http://127.0.0.1:$candidate"
      "API: http://127.0.0.1:$candidate"
      exit 0
    }
  } catch {}
}

if ($Restart) {
  & (Join-Path $PSScriptRoot "stop_api.ps1") | Out-Null
  Start-Sleep -Milliseconds 500
}

foreach ($candidate in $candidatePorts) {
  $free = & (Join-Path $PSScriptRoot "Test-PortFree.ps1") -Port $candidate
  if ($free -eq "true") {
    $selectedPort = $candidate
    break
  }
}
if (-not $selectedPort) {
  throw "No free API port found."
}

& (Join-Path $PSScriptRoot "Set-EnvValue.ps1") -Path $envFile -Key "NEXT_PUBLIC_API_BASE" -Value "http://127.0.0.1:$selectedPort"

$apiDir = Join-Path $root "apps\api"
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if ($pythonCommand) {
  $python = $pythonCommand.Source
} else {
  $python = "python"
}
$venvPython = Join-Path $apiDir ".venv\Scripts\python.exe"
if ((Test-Path -LiteralPath $venvPython) -and (Test-PythonModule $venvPython "uvicorn")) {
  $python = $venvPython
} elseif (-not (Test-PythonModule $python "uvicorn")) {
  throw "No usable Python with uvicorn found. Checked: $venvPython and PATH python. Install API dependencies or remove the incomplete apps\api\.venv."
}
Remove-Item -LiteralPath $outLog, $errLog -Force -ErrorAction SilentlyContinue
Set-Content -LiteralPath $outLog -Value "Python: $python" -Encoding UTF8

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$command = "`"$python`" -m uvicorn app.main:app --host 127.0.0.1 --port $selectedPort > `"$outLog`" 2> `"$errLog`""
$psi.FileName = "cmd.exe"
$psi.Arguments = "/d /s /c `"$command`""
$psi.WorkingDirectory = $apiDir
$psi.UseShellExecute = $true
$psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$process = [System.Diagnostics.Process]::Start($psi)
Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding UTF8

if ($TimeoutSec -lt 5) {
  $TimeoutSec = 5
}
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSec)
while ([DateTime]::UtcNow -lt $deadline) {
  Start-Sleep -Milliseconds 500
  if ($process.HasExited) {
    $errTail = Get-LogTail $errLog
    $outTail = Get-LogTail $outLog
    throw "API exited before becoming healthy. exit=$($process.ExitCode)`nSTDERR:`n$errTail`nSTDOUT:`n$outTail"
  }
  try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$selectedPort/api/health" -TimeoutSec 1
    if ($health.status -eq "ok") {
      Set-ListeningPidFile $selectedPort
      "API: http://127.0.0.1:$selectedPort"
      exit 0
    }
  } catch {}
}

$errTail = Get-LogTail $errLog
$outTail = Get-LogTail $outLog
throw "API did not become healthy within ${TimeoutSec}s. pid=$($process.Id) port=$selectedPort`nSTDERR:`n$errTail`nSTDOUT:`n$outTail"
