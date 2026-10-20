param(
  [string]$OutputRoot = "",
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
if (-not $OutputRoot) {
  $OutputRoot = Join-Path $root ".tmp\pack-launcher-dist"
}
$work = Join-Path $root ".tmp\pyinstaller-launcher-work"
$spec = Join-Path $root ".tmp\pyinstaller-launcher-spec"
$entry = Join-Path $root "tools\launcher\math_platform_launcher.py"

New-Item -ItemType Directory -Force -Path $OutputRoot, $work, $spec | Out-Null
$oldOneFile = Join-Path $OutputRoot "MathPlatform.exe"
$oldOneDir = Join-Path $OutputRoot "MathPlatform"
Remove-Item -LiteralPath $oldOneFile -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $oldOneDir -Recurse -Force -ErrorAction SilentlyContinue

function Test-PyInstallerPython([string]$Candidate) {
  if (-not $Candidate) {
    return $false
  }
  if ($Candidate -ne "python" -and -not (Test-Path -LiteralPath $Candidate)) {
    return $false
  }
  try {
    $version = & $Candidate -m PyInstaller --version 2>$null
    return ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($version))
  } catch {
    return $false
  }
}

$pythonCandidates = @()
if ($PythonExe) {
  $pythonCandidates += $PythonExe
}
$pythonCandidates += "D:\APP\anaconda\python.exe"
$pythonCandidates += (Join-Path (Split-Path $root -Parent) "anaconda\python.exe")
Get-Command python.exe -All -ErrorAction SilentlyContinue | ForEach-Object {
  $pythonCandidates += $_.Source
}
$pythonCandidates += "python"

$python = $null
foreach ($candidate in ($pythonCandidates | Where-Object { $_ } | Select-Object -Unique)) {
  if (Test-PyInstallerPython $candidate) {
    $python = $candidate
    break
  }
}
if (-not $python) {
  throw "No usable Python with PyInstaller found. Install PyInstaller or pass -PythonExe."
}

"PyInstaller Python: $python"

& $python -m PyInstaller `
  --noconfirm `
  --clean `
  --onedir `
  --name MathPlatform `
  --distpath $OutputRoot `
  --workpath $work `
  --specpath $spec `
  --exclude-module IPython `
  --exclude-module matplotlib `
  --exclude-module numpy `
  --exclude-module pandas `
  --exclude-module pygame `
  --exclude-module scipy `
  --exclude-module torch `
  --exclude-module torchvision `
  --exclude-module transformers `
  $entry

$built = Join-Path $OutputRoot "MathPlatform\MathPlatform.exe"
if (-not (Test-Path -LiteralPath $built)) {
  throw "PyInstaller did not create $built"
}

"Launcher exe: $built"
