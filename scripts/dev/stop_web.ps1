$ErrorActionPreference = "SilentlyContinue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$pidFile = Join-Path $root ".tmp\dev-web.pid"

if (Test-Path -LiteralPath $pidFile) {
  $pidValue = (Get-Content -LiteralPath $pidFile -Encoding UTF8 | Select-Object -First 1).Trim()
  if ($pidValue) {
    Stop-Process -Id ([int]$pidValue) -Force
  }
  Remove-Item -LiteralPath $pidFile -Force
}

"web stopped"
