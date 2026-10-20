param(
  [int]$Port = 3000,
  [int]$TimeoutSec = 45,
  [ValidateSet("auto", "dev", "prod")]
  [string]$Mode = "auto",
  [switch]$Restart
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$tmp = Join-Path $root ".tmp"
$pidFile = Join-Path $tmp "dev-web.pid"
$outLog = Join-Path $tmp "dev-web.out.log"
$errLog = Join-Path $tmp "dev-web.err.log"

New-Item -ItemType Directory -Force -Path $tmp | Out-Null

function Get-LogTail([string]$Path, [int]$Lines = 60) {
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

$webDir = Join-Path $root "apps\web"
$node = "node"
$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if ($nodeCommand) {
  $node = $nodeCommand.Source
}
try {
  $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port" -UseBasicParsing -TimeoutSec 2
  if ($response.StatusCode -eq 200 -and -not $Restart) {
    Set-ListeningPidFile $Port
    "Web: http://127.0.0.1:$Port"
    exit 0
  }
} catch {}

if ($Restart) {
  & (Join-Path $PSScriptRoot "stop_web.ps1") | Out-Null
  Start-Sleep -Milliseconds 500
}

$listener = netstat -ano | Select-String "127.0.0.1:$Port\s+0.0.0.0:0\s+LISTENING" | Select-Object -First 1
if ($listener) {
  $listenerPid = (($listener.ToString() -split "\s+") | Where-Object { $_ })[-1]
  throw "Web port $Port is already occupied by PID $listenerPid, but it did not return HTTP 200. Stop that process first or choose another port."
}
Remove-Item -LiteralPath $outLog, $errLog -Force -ErrorAction SilentlyContinue

function Start-WebMode([string]$ModeName, [string]$NodeArguments, [int]$WaitSec) {
  Add-Content -LiteralPath $outLog -Value "=== starting $ModeName at $(Get-Date -Format s) ===" -Encoding UTF8
  Add-Content -LiteralPath $errLog -Value "=== starting $ModeName at $(Get-Date -Format s) ===" -Encoding UTF8

  $psi = [System.Diagnostics.ProcessStartInfo]::new()
  $command = "`"$node`" $NodeArguments > `"$outLog`" 2> `"$errLog`""
  $psi.FileName = "cmd.exe"
  $psi.Arguments = "/d /s /c `"$command`""
  $psi.WorkingDirectory = $webDir
  $psi.UseShellExecute = $true
  $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
  $process = [System.Diagnostics.Process]::Start($psi)
  Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding UTF8

  $deadline = [DateTime]::UtcNow.AddSeconds($WaitSec)
  while ([DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
      return @{
        ok = $false
        reason = "Web $ModeName exited before becoming healthy. exit=$($process.ExitCode)"
        stderr = Get-LogTail $errLog
        stdout = Get-LogTail $outLog
      }
    }
    try {
      $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port" -UseBasicParsing -TimeoutSec 1
      if ($response.StatusCode -eq 200) {
        Set-ListeningPidFile $Port
        "Web: http://127.0.0.1:$Port ($ModeName)"
        exit 0
      }
    } catch {}
  }

  try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
  return @{
    ok = $false
    reason = "Web $ModeName did not become healthy within ${WaitSec}s. pid=$($process.Id) port=$Port"
    stderr = Get-LogTail $errLog
    stdout = Get-LogTail $outLog
  }
}

if ($TimeoutSec -lt 15) {
  $TimeoutSec = 15
}
$devWaitSec = [Math]::Min(15, $TimeoutSec)
$prodWaitSec = [Math]::Max(10, $TimeoutSec - $devWaitSec)
$failures = @()

if ($Mode -eq "dev" -or $Mode -eq "auto") {
  $devResult = Start-WebMode "dev" "node_modules\next\dist\bin\next dev --hostname 127.0.0.1 --port $Port" $devWaitSec
  $failures += $devResult
  if ($Mode -eq "dev") {
    throw "$($devResult.reason)`nSTDERR:`n$($devResult.stderr)`nSTDOUT:`n$($devResult.stdout)"
  }
}

if ($Mode -eq "prod" -or $Mode -eq "auto") {
  if (-not (Test-Path -LiteralPath (Join-Path $webDir ".next\BUILD_ID"))) {
    $failures += @{
      ok = $false
      reason = "Web prod preview cannot start because .next\BUILD_ID is missing. Run npm run build in apps\web first."
      stderr = ""
      stdout = ""
    }
  } else {
    $prodResult = Start-WebMode "prod" "node_modules\next\dist\bin\next start --hostname 127.0.0.1 --port $Port" $prodWaitSec
    $failures += $prodResult
    if ($Mode -eq "prod") {
      throw "$($prodResult.reason)`nSTDERR:`n$($prodResult.stderr)`nSTDOUT:`n$($prodResult.stdout)"
    }
  }
}

$details = ($failures | ForEach-Object {
  "$($_.reason)`nSTDERR:`n$($_.stderr)`nSTDOUT:`n$($_.stdout)"
}) -join "`n---`n"
throw "Web did not become healthy in mode=$Mode within ${TimeoutSec}s.`n$details"
