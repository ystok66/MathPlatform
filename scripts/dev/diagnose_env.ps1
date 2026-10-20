param(
  [int]$WebPort = 3000,
  [int]$ApiPort = 8001
)

$ErrorActionPreference = "Continue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$webDir = Join-Path $root "apps\web"
$apiDir = Join-Path $root "apps\api"

function Write-Section([string]$Title) {
  ""
  "== $Title =="
}

function Test-Http([string]$Name, [string]$Url) {
  try {
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
    "${Name}: HTTP $($response.StatusCode)"
  } catch {
    "${Name}: FAIL $($_.Exception.Message)"
  }
}

Write-Section "PowerShell"
"Version: $($PSVersionTable.PSVersion)"

Write-Section "Node"
$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if ($nodeCommand) {
  "node: $($nodeCommand.Source)"
  try {
    & $nodeCommand.Source -p "JSON.stringify({version:process.version,platform:process.platform,arch:process.arch,execPath:process.execPath})"
  } catch {
    "node version check failed: $($_.Exception.Message)"
  }
  try {
    & $nodeCommand.Source -e "const {spawnSync}=require('child_process'); const r=spawnSync(process.execPath,['-e','console.log(123)'],{encoding:'utf8'}); console.log(JSON.stringify({status:r.status,error:r.error&&{code:r.error.code,message:r.error.message},stdout:r.stdout||null,stderr:r.stderr||null}))"
  } catch {
    "node child_process check failed: $($_.Exception.Message)"
  }
} else {
  "node: MISSING"
}

Write-Section "Next"
$buildId = Join-Path $webDir ".next\BUILD_ID"
"build: " + $(if (Test-Path -LiteralPath $buildId) { "present" } else { "missing" })
$swc = Join-Path $webDir "node_modules\@next\swc-win32-x64-msvc\next-swc.win32-x64-msvc.node"
if ((Test-Path -LiteralPath $swc) -and $nodeCommand) {
  $swcResult = & $nodeCommand.Source -e "require(process.argv[1]); console.log('swc-ok')" $swc 2>&1
  if ($LASTEXITCODE -eq 0) {
    "swc native: ok"
  } else {
    "swc native: failed"
    ($swcResult | Select-Object -First 3) -join " "
  }
} else {
  "swc native package: missing"
}

Write-Section "Python"
$venvPython = Join-Path $apiDir ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
  $venvUvicorn = & $venvPython -c "import uvicorn; print('uvicorn-ok')" 2>&1
  if ($LASTEXITCODE -eq 0) {
    "venv python: $venvPython uvicorn-ok"
  } else {
    "venv python: $venvPython uvicorn-missing"
  }
} else {
  "venv python: missing"
}
$pythonCommands = Get-Command python.exe -All -ErrorAction SilentlyContinue
foreach ($pythonCommand in $pythonCommands) {
  $uvicorn = & $pythonCommand.Source -c "import uvicorn; print('uvicorn-ok')" 2>&1
  if ($LASTEXITCODE -eq 0) {
    "python candidate: $($pythonCommand.Source) uvicorn-ok"
  } else {
    "python candidate: $($pythonCommand.Source) uvicorn-missing"
  }
}

Write-Section "Ports"
netstat -ano | Select-String ":$WebPort|:$ApiPort"

Write-Section "Health"
Test-Http "web" "http://127.0.0.1:$WebPort/"
Test-Http "api" "http://127.0.0.1:$ApiPort/api/health"
