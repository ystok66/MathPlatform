param(
  [string]$ReleaseDir = "",
  [string]$NodeExe = "D:\APP\node.exe",
  [switch]$SkipWebBuild,
  [switch]$SkipApiBuild,
  [switch]$SkipLauncherBuild
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
if (-not $ReleaseDir) {
  $ReleaseDir = Join-Path $root "dist\math-platform"
}
$releasePath = [System.IO.Path]::GetFullPath($ReleaseDir)
$distRoot = [System.IO.Path]::GetFullPath((Join-Path $root "dist"))
if (-not $releasePath.StartsWith($distRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "ReleaseDir must stay under $distRoot"
}

& $NodeExe (Join-Path $root "tools\check-language-resources.cjs")
if ($LASTEXITCODE -ne 0) { throw "Language resource validation failed; release was not changed." }

if (Test-Path -LiteralPath $releasePath) {
  Remove-Item -LiteralPath $releasePath -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $releasePath | Out-Null

if (-not $SkipWebBuild) {
  Push-Location (Join-Path $root "apps\web")
  try {
    # Keep browser requests same-origin in the packaged release. The Next API
    # proxy receives the actual API port from MathPlatform.exe at runtime, so
    # the UI keeps working when 8001 is occupied and the launcher selects a
    # fallback API port.
    $env:NEXT_PUBLIC_API_BASE = ""
    $env:NEXT_IGNORE_INCORRECT_LOCKFILE = "1"
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed; packaging stopped." }
  } finally {
    Remove-Item Env:\NEXT_PUBLIC_API_BASE -ErrorAction SilentlyContinue
    Remove-Item Env:\NEXT_IGNORE_INCORRECT_LOCKFILE -ErrorAction SilentlyContinue
    Pop-Location
  }
}

if (-not $SkipApiBuild) {
  & (Join-Path $PSScriptRoot "build_api_exe.ps1") | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "API build failed; packaging stopped." }
}

if (-not $SkipLauncherBuild) {
  & (Join-Path $PSScriptRoot "build_launcher_exe.ps1") | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "Launcher build failed; packaging stopped." }
}

$apiBuilt = Join-Path $root ".tmp\pack-api-dist\math-api"
if (-not (Test-Path -LiteralPath (Join-Path $apiBuilt "math-api.exe"))) {
  throw "Missing API build at $apiBuilt"
}
$launcherBuilt = Join-Path $root ".tmp\pack-launcher-dist\MathPlatform"
if (-not (Test-Path -LiteralPath (Join-Path $launcherBuilt "MathPlatform.exe"))) {
  throw "Missing launcher build at $launcherBuilt"
}

$webStandalone = Join-Path $root "apps\web\.next\standalone"
$webStatic = Join-Path $root "apps\web\.next\static"
$webBuild = Join-Path $root "apps\web\.next"
$usingStandalone = Test-Path -LiteralPath (Join-Path $webStandalone "server.js")
if (-not $usingStandalone -and -not (Test-Path -LiteralPath $webBuild)) {
  throw "Missing Next build output. Run npm run build first."
}
if (-not (Test-Path -LiteralPath $NodeExe)) {
  throw "Missing node.exe at $NodeExe"
}

New-Item -ItemType Directory -Force -Path (Join-Path $releasePath "api"), (Join-Path $releasePath "web"), (Join-Path $releasePath "data"), (Join-Path $releasePath "tools"), (Join-Path $releasePath "logs"), (Join-Path $releasePath "backups"), (Join-Path $releasePath "launcher") | Out-Null
Copy-Item -Path (Join-Path $apiBuilt "*") -Destination (Join-Path $releasePath "api") -Recurse -Force
Copy-Item -Path (Join-Path $launcherBuilt "*") -Destination (Join-Path $releasePath "launcher") -Recurse -Force
$condaBin = "D:\APP\anaconda\Library\bin"
$apiInternal = Join-Path $releasePath "api\_internal"
$launcherInternal = Join-Path $releasePath "launcher\_internal"
if (Test-Path -LiteralPath $condaBin) {
  New-Item -ItemType Directory -Force -Path $apiInternal, $launcherInternal | Out-Null
  foreach ($dllName in @(
    "libexpat.dll",
    "libmpdec-4.dll",
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
    "ffi.dll",
    "sqlite3.dll",
    "libxml2.dll",
    "libxslt.dll",
    "libexslt.dll",
    "zstd.dll",
    "liblzma.dll",
    "tcl86t.dll",
    "tk86t.dll",
    "libzmq-mt-4_3_5.dll"
  )) {
    $dllPath = Join-Path $condaBin $dllName
    if (Test-Path -LiteralPath $dllPath) {
      Copy-Item -LiteralPath $dllPath -Destination (Join-Path $apiInternal $dllName) -Force
      Copy-Item -LiteralPath $dllPath -Destination (Join-Path $launcherInternal $dllName) -Force
    }
  }
}
if ($usingStandalone) {
  Copy-Item -Path (Join-Path $webStandalone "*") -Destination (Join-Path $releasePath "web") -Recurse -Force
  New-Item -ItemType Directory -Force -Path (Join-Path $releasePath "web\.next\static") | Out-Null
  Copy-Item -Path (Join-Path $webStatic "*") -Destination (Join-Path $releasePath "web\.next\static") -Recurse -Force
  if (Test-Path -LiteralPath (Join-Path $root "apps\web\public")) {
    Copy-Item -LiteralPath (Join-Path $root "apps\web\public") -Destination (Join-Path $releasePath "web\public") -Recurse -Force
  }
} else {
  Write-Host "Next standalone server.js not found; assembling web fallback with .next and node_modules."
  Copy-Item -LiteralPath $webBuild -Destination (Join-Path $releasePath "web\.next") -Recurse -Force
  Copy-Item -LiteralPath (Join-Path $root "apps\web\node_modules") -Destination (Join-Path $releasePath "web\node_modules") -Recurse -Force
  Copy-Item -LiteralPath (Join-Path $root "apps\web\package.json") -Destination (Join-Path $releasePath "web\package.json") -Force
  Copy-Item -LiteralPath (Join-Path $root "apps\web\next.config.js") -Destination (Join-Path $releasePath "web\next.config.js") -Force
  $fallbackNextDir = Join-Path $releasePath "web\.next"
  $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
  $buildIdPath = Join-Path $fallbackNextDir "BUILD_ID"
  $buildId = $null
  $staticRoot = Join-Path $fallbackNextDir "static"
  if (Test-Path -LiteralPath $staticRoot) {
    $buildId = Get-ChildItem -LiteralPath $staticRoot -Directory |
      Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "_buildManifest.js") } |
      Select-Object -First 1 -ExpandProperty Name
  }
  if (-not $buildId) {
    $buildId = "local-build"
  }
  if (-not (Test-Path -LiteralPath $buildIdPath)) {
    [System.IO.File]::WriteAllText($buildIdPath, $buildId, [System.Text.Encoding]::ASCII)
  }
  $appRouteMap = @{}
  $appPathsPath = Join-Path $fallbackNextDir "server\app-paths-manifest.json"
  if (Test-Path -LiteralPath $appPathsPath) {
    $appPathsJson = Get-Content -LiteralPath $appPathsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($property in $appPathsJson.PSObject.Properties) {
      $route = $property.Name -replace "/page$", ""
      if (-not $route) {
        $route = "/"
      }
      $appRouteMap[$property.Name] = $route
    }
  }
  if ($appRouteMap.Count -eq 0) {
    $appRouteMap["/page"] = "/"
  }
  [System.IO.File]::WriteAllText((Join-Path $fallbackNextDir "app-path-routes-manifest.json"), ($appRouteMap | ConvertTo-Json -Depth 10), $utf8NoBom)
  $staticRoutes = @()
  foreach ($route in ($appRouteMap.Values | Select-Object -Unique)) {
    if (-not $route -or $route -eq "/_not-found") {
      continue
    }
    $regex = if ($route -eq "/") { "^/(?:/)?$" } else { "^$([regex]::Escape($route))(?:/)?$" }
    $staticRoutes += @{ page = $route; regex = $regex; routeKeys = @{}; namedRegex = $regex }
  }
  $routesManifest = @{
    version = 3
    pages404 = $true
    caseSensitive = $false
    basePath = ""
    redirects = @()
    headers = @()
    dynamicRoutes = @()
    staticRoutes = $staticRoutes
    dataRoutes = @()
    rewrites = @{ beforeFiles = @(); afterFiles = @(); fallback = @() }
    rsc = @{
      header = "RSC"
      varyHeader = "RSC, Next-Router-State-Tree, Next-Router-Prefetch, Next-Router-Segment-Prefetch"
      prefetchHeader = "Next-Router-Prefetch"
      didPostponeHeader = "x-nextjs-postponed"
      contentTypeHeader = "text/x-component"
      suffix = ".rsc"
      prefetchSuffix = ".prefetch.rsc"
    }
    skipMiddlewareUrlNormalize = $false
  }
  [System.IO.File]::WriteAllText((Join-Path $fallbackNextDir "routes-manifest.json"), ($routesManifest | ConvertTo-Json -Depth 20), $utf8NoBom)
  $prerenderManifest = @{
    version = 4
    routes = @{}
    dynamicRoutes = @{}
    notFoundRoutes = @()
    preview = @{ previewModeId = ""; previewModeSigningKey = ""; previewModeEncryptionKey = "" }
  }
  [System.IO.File]::WriteAllText((Join-Path $fallbackNextDir "prerender-manifest.json"), ($prerenderManifest | ConvertTo-Json -Depth 10), $utf8NoBom)
  if (Test-Path -LiteralPath (Join-Path $root "apps\web\public")) {
    Copy-Item -LiteralPath (Join-Path $root "apps\web\public") -Destination (Join-Path $releasePath "web\public") -Recurse -Force
  }
  $fallbackServer = @'
const http = require("http");
const https = require("https");
const next = require("next");

const port = Number(process.env.PORT || 3000);
const hostname = process.env.HOSTNAME || "127.0.0.1";
const apiBase = (process.env.MATH_PLATFORM_API_BASE || process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8001").replace(/\/+$/, "");
const app = next({ dev: false, dir: __dirname, hostname, port });
const handle = app.getRequestHandler();

function proxyApi(req, res) {
  const target = new URL(req.url || "/api/health", apiBase);
  const client = target.protocol === "https:" ? https : http;
  const headers = { ...req.headers, host: target.host };
  delete headers.connection;
  delete headers["keep-alive"];
  delete headers["transfer-encoding"];
  const upstream = client.request(target, { method: req.method, headers }, (upstreamRes) => {
    res.writeHead(upstreamRes.statusCode || 502, upstreamRes.statusMessage, upstreamRes.headers);
    upstreamRes.pipe(res);
  });
  upstream.on("error", (error) => {
    if (!res.headersSent) {
      res.writeHead(502, { "content-type": "application/json; charset=utf-8" });
    }
    res.end(JSON.stringify({ error: "api_proxy_failed", message: String(error && error.message || error) }));
  });
  req.pipe(upstream);
}

app.prepare().then(() => {
  http.createServer((req, res) => {
    if (req.url && req.url.startsWith("/api/")) {
      proxyApi(req, res);
      return;
    }
    handle(req, res);
  }).listen(port, hostname, () => {
    console.log(`ready - started server on http://${hostname}:${port}`);
  });
});
'@
  [System.IO.File]::WriteAllText((Join-Path $releasePath "web\server.js"), $fallbackServer, [System.Text.Encoding]::ASCII)
}
Copy-Item -LiteralPath $NodeExe -Destination (Join-Path $releasePath "web\node.exe") -Force

Copy-Item -LiteralPath (Join-Path $root "math_platform_dev.db") -Destination (Join-Path $releasePath "data\math_platform_dev.db") -Force
Copy-Item -LiteralPath (Join-Path $root "prompts") -Destination (Join-Path $releasePath "prompts") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $root "packages") -Destination (Join-Path $releasePath "packages") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $root "docs") -Destination (Join-Path $releasePath "docs") -Recurse -Force
$questionDataName = -join ([char]0x505a, [char]0x9898, [char]0x9879, [char]0x76ee)
$questionDataSource = Join-Path $root $questionDataName
if (Test-Path -LiteralPath $questionDataSource) {
  Copy-Item -LiteralPath $questionDataSource -Destination (Join-Path $releasePath "question_data") -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $root "README.md") -Destination (Join-Path $releasePath "README.md") -Force
if (Test-Path -LiteralPath (Join-Path $root ".env.example")) {
  Copy-Item -LiteralPath (Join-Path $root ".env.example") -Destination (Join-Path $releasePath ".env.example") -Force
}

function Get-DotEnvValue([string]$Path, [string]$Key, [string]$DefaultValue = "") {
  if (-not (Test-Path -LiteralPath $Path)) { return $DefaultValue }
  foreach ($line in [System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8)) {
    $trimmed = $line.Trim()
    if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }
    $parts = $trimmed.Split("=", 2)
    if ($parts[0].Trim().TrimStart([char]0xFEFF) -eq $Key) {
      return $parts[1].Trim().Trim('"')
    }
  }
  return $DefaultValue
}

$sourceEnvPath = Join-Path $root ".env"
$releaseLiaobotsBaseUrl = Get-DotEnvValue $sourceEnvPath "LIAOBOTS_BASE_URL" "https://ai.liaobots2.work/v1"
$releaseLiaobotsApiKey = Get-DotEnvValue $sourceEnvPath "LIAOBOTS_API_KEY" ""
$releaseDefaultModel = "gpt-5.6-terra"

$releaseEnv = @"
APP_NAME=Math Platform
DATABASE_URL=sqlite:///./data/math_platform_dev.db
REDIS_URL=redis://localhost:6379/0
QUESTION_DATA_DIR=question_data
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
LIAOBOTS_BASE_URL=$releaseLiaobotsBaseUrl
LIAOBOTS_API_KEY=$releaseLiaobotsApiKey
MODEL_PROVIDER=openai_compatible
DEFAULT_MODEL=$releaseDefaultModel
PLANNER_MODEL=$releaseDefaultModel
GENERATOR_MODEL=$releaseDefaultModel
GENERATOR_MODEL_LE_3=gpt-5.6-terra
GENERATOR_MODEL_4=gpt-5.6-sol
GENERATOR_MODEL_5=gpt-5.6-sol
REVIEWER_MODEL=$releaseDefaultModel
SECONDARY_REVIEWER_MODEL=$releaseDefaultModel
REPORTER_MODEL=$releaseDefaultModel
DIAGNOSTICIAN_MODEL=$releaseDefaultModel
HINT_GENERATOR_MODEL=$releaseDefaultModel
FAULT_AUDITOR_MODEL=$releaseDefaultModel
SET_REVIEWER_MODEL=$releaseDefaultModel
ANSWER_JUDGE_MODEL=$releaseDefaultModel
SOLUTION_EXPLAINER_MODEL=$releaseDefaultModel
QUESTION_REPAIR_REVIEWER_MODEL=$releaseDefaultModel
EMERGENCY_RECOVERY_AGENT_MODEL=gpt-5.6-sol
PROJECT_ASSISTANT_MODEL=gpt-5.6-sol
AI_STRICT_REVIEWER_MODEL_ALLOWLIST=openai_compatible:gpt-5.6-terra,gpt-5.6-terra,openai_compatible:gpt-5.6-sol,gpt-5.6-sol
AI_MONTHLY_BUDGET_USD=0
AI_DAILY_BUDGET_USD=0
AI_MODEL_PRICE_JSON=
AI_ESTIMATED_GENERATOR_INPUT_TOKENS_PER_CALL=2500
AI_ESTIMATED_GENERATOR_OUTPUT_TOKENS_PER_CALL=1200
AI_ESTIMATED_REVIEWER_INPUT_TOKENS_PER_CALL=2000
AI_ESTIMATED_REVIEWER_OUTPUT_TOKENS_PER_CALL=800
AI_INTERACTION_TIMEOUT_SECONDS=60
AI_GENERATOR_TIMEOUT_SECONDS=180
AI_REVIEWER_TIMEOUT_SECONDS=120
AI_JOB_TIMEOUT_SECONDS=420
AI_GLOBAL_MAX_CONCURRENCY=6
AI_EMERGENCY_AGENT_MAX_CONCURRENCY=1
AI_MAX_GENERATION_CALLS_PER_JOB=5
AI_MAX_REVIEWER_CALLS_PER_JOB=5
AI_MAX_GENERATION_CALLS_PER_BATCH=500
AI_MAX_REVIEWER_CALLS_PER_BATCH=500
API_REQUEST_LOG_PATH=logs/api.jsonl
API_REQUEST_LOG_MAX_BYTES=1048576
API_REQUEST_LOG_BACKUP_COUNT=5
API_REQUEST_LOG_RETENTION_DAYS=14
API_SLOW_REQUEST_MS=2000
"@
[System.IO.File]::WriteAllText((Join-Path $releasePath ".env"), $releaseEnv, [System.Text.UTF8Encoding]::new($false))

$sanitizeScript = @'
import sqlite3
from pathlib import Path
db = Path(r"__DB_PATH__")
conn = sqlite3.connect(str(db))
try:
    for table in ("encrypted_configs", "secrets", "app_secrets"):
        exists = conn.execute("select name from sqlite_master where type='table' and name=?", (table,)).fetchone()
        if exists:
            conn.execute(f"delete from {table} where upper(key) like '%API_KEY%' or upper(key) like '%TOKEN%'")
    conn.commit()
finally:
    conn.close()
'@.Replace("__DB_PATH__", (Join-Path $releasePath "data\math_platform_dev.db"))
$sanitizePath = Join-Path $releasePath "tools\sanitize_db.py"
[System.IO.File]::WriteAllText($sanitizePath, $sanitizeScript, [System.Text.UTF8Encoding]::new($false))
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if ($pythonCommand) {
  & $pythonCommand.Source $sanitizePath
}
Remove-Item -LiteralPath $sanitizePath -Force -ErrorAction SilentlyContinue

$runApiCmd = @'
@echo off
cd /d "%~dp0.."
if not exist logs mkdir logs
set MATH_PLATFORM_ROOT=%CD%
set HOST=127.0.0.1
set PORT=8001
"api\math-api.exe" > "logs\api.out.log" 2> "logs\api.err.log"
'@
[System.IO.File]::WriteAllText((Join-Path $releasePath "tools\run-api.cmd"), $runApiCmd, [System.Text.Encoding]::ASCII)

$runWebCmd = @'
@echo off
cd /d "%~dp0.."
if not exist logs mkdir logs
set MATH_PLATFORM_ROOT=%CD%
set NODE_ENV=production
set HOSTNAME=127.0.0.1
set PORT=3000
"web\node.exe" "web\server.js" > "logs\web.out.log" 2> "logs\web.err.log"
'@
[System.IO.File]::WriteAllText((Join-Path $releasePath "tools\run-web.cmd"), $runWebCmd, [System.Text.Encoding]::ASCII)

$startPs = @'
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
& (Join-Path $root "launcher\MathPlatform.exe") start
'@
[System.IO.File]::WriteAllText((Join-Path $releasePath "tools\start-release.ps1"), $startPs, [System.Text.UTF8Encoding]::new($false))

$stopPs = @'
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
& (Join-Path $root "launcher\MathPlatform.exe") stop
'@
[System.IO.File]::WriteAllText((Join-Path $releasePath "tools\stop-release.ps1"), $stopPs, [System.Text.UTF8Encoding]::new($false))

$checkPs = @'
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
& (Join-Path $root "launcher\MathPlatform.exe") status --json
'@
[System.IO.File]::WriteAllText((Join-Path $releasePath "tools\check-release.ps1"), $checkPs, [System.Text.UTF8Encoding]::new($false))

$backupPs = @'
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
& (Join-Path $root "launcher\MathPlatform.exe") backup --json
'@
[System.IO.File]::WriteAllText((Join-Path $releasePath "tools\backup-release.ps1"), $backupPs, [System.Text.UTF8Encoding]::new($false))

$releaseSmokePhaseSource = Join-Path $root "scripts\smoke\release_smoke_phase.ps1"
if (Test-Path -LiteralPath $releaseSmokePhaseSource) {
  Copy-Item -LiteralPath $releaseSmokePhaseSource -Destination (Join-Path $releasePath "tools\release_smoke_phase.ps1") -Force
}
$releaseBusinessSmokeSource = Join-Path $root "scripts\smoke\release_business_smoke.ps1"
if (Test-Path -LiteralPath $releaseBusinessSmokeSource) {
  Copy-Item -LiteralPath $releaseBusinessSmokeSource -Destination (Join-Path $releasePath "tools\release_business_smoke.ps1") -Force
}

$startCmd = "@echo off`r`n""%~dp0launcher\MathPlatform.exe"" start`r`n"
$checkCmd = "@echo off`r`n""%~dp0launcher\MathPlatform.exe"" status --json`r`n"
$stopCmd = "@echo off`r`n""%~dp0launcher\MathPlatform.exe"" stop`r`n"
$backupCmd = "@echo off`r`n""%~dp0launcher\MathPlatform.exe"" backup --json`r`n"
$restoreCmd = @'
@echo off
if "%~1"=="" (
  echo usage: restore.cmd backups\backup-YYYYMMDD-HHMMSS [backup_id]
  echo first run without backup_id to dry-run and print required_confirmation.
  exit /b 1
)
if "%~2"=="" (
  "%~dp0launcher\MathPlatform.exe" restore --file "%~1" --dry-run --json
) else (
  "%~dp0launcher\MathPlatform.exe" restore --file "%~1" --confirm "%~2" --json
)
'@
$startHereCmd = @'
@echo off
cd /d "%~dp0"
echo Starting Math Platform...
"%~dp0launcher\MathPlatform.exe" start
if errorlevel 1 (
  echo.
  echo Start failed. Run check.cmd in this folder for details.
  timeout /t 20 >nul
  exit /b 1
)
timeout /t 2 /nobreak >nul
echo Opening the selected local web port...
"%~dp0launcher\MathPlatform.exe" open
echo.
echo Started. You can close this window. Use stop.cmd when finished.
timeout /t 5 >nul
'@
[System.IO.File]::WriteAllText((Join-Path $releasePath "start.cmd"), $startCmd, [System.Text.Encoding]::ASCII)
[System.IO.File]::WriteAllText((Join-Path $releasePath "check.cmd"), $checkCmd, [System.Text.Encoding]::ASCII)
[System.IO.File]::WriteAllText((Join-Path $releasePath "stop.cmd"), $stopCmd, [System.Text.Encoding]::ASCII)
[System.IO.File]::WriteAllText((Join-Path $releasePath "backup.cmd"), $backupCmd, [System.Text.Encoding]::ASCII)
[System.IO.File]::WriteAllText((Join-Path $releasePath "restore.cmd"), $restoreCmd, [System.Text.Encoding]::ASCII)
[System.IO.File]::WriteAllText((Join-Path $releasePath "START_HERE.cmd"), $startHereCmd, [System.Text.Encoding]::ASCII)

$readme = @"
# Math Platform Release

1. Copy this whole folder to the target Windows computer.
2. Double-click START_HERE.cmd.
3. The browser should open the local web app automatically. Default is http://127.0.0.1:3000; if that port is occupied, the launcher selects another local port and records it in data\ports.json.
4. Fill API Key in the Admin / model settings page, then click the API test button.
5. Use backup.cmd before upgrades or risky imports.
6. Use stop.cmd when finished.

This package includes the SQLite database. If the source .env has LIAOBOTS_API_KEY, the release .env keeps it so the copied folder can call the configured API immediately; otherwise configure it once in the Admin model settings page.
The launcher tries the default API/Web ports first, then up to 10 random fallback ports for each service. Use check.cmd to see the selected URLs.
backup.cmd uses SQLite online backup and writes env.sanitized, not the raw .env secrets.
restore.cmd first supports dry-run validation, then restore with the backup_id confirmation phrase.
Maintenance smoke helpers live under tools\release_smoke_phase.ps1 and tools\release_business_smoke.ps1.
"@
[System.IO.File]::WriteAllText((Join-Path $releasePath "PACKAGED_README.md"), $readme, [System.Text.UTF8Encoding]::new($false))

$manifestScript = Join-Path $root "scripts\pack\release_manifest.py"
if ($pythonCommand -and (Test-Path -LiteralPath $manifestScript)) {
  & $pythonCommand.Source $manifestScript $releasePath --app-version "local-release"
  if ($LASTEXITCODE -ne 0) { throw "Release manifest failed; candidate must not be published." }
}
else {
  Write-Warning "release manifest skipped because python.exe or release_manifest.py was not found."
}

"Release: $releasePath"
