param(
  [string]$OutputRoot = "",
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
if (-not $OutputRoot) {
  $OutputRoot = Join-Path $root ".tmp\pack-api-dist"
}
$work = Join-Path $root ".tmp\pyinstaller-work"
$spec = Join-Path $root ".tmp\pyinstaller-spec"
$entry = Join-Path $root "apps\api\packaged_api.py"
$apiPath = Join-Path $root "apps\api"

New-Item -ItemType Directory -Force -Path $OutputRoot, $work, $spec | Out-Null

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

$excludedModules = @(
  "IPython",
  "PIL",
  "cv2",
  "dask",
  "datasets",
  "distributed",
  "huggingface_hub",
  "jupyter",
  "langchain",
  "matplotlib",
  "notebook",
  "numba",
  "numpy",
  "onnx",
  "onnxruntime",
  "openai",
  "pandas",
  "polars",
  "pyarrow",
  "pygame",
  "scipy",
  "sklearn",
  "spacy",
  "sphinx",
  "tensorboard",
  "tensorflow",
  "thinc",
  "torch",
  "torchaudio",
  "torchvision",
  "transformers",
  "wandb",
  "xarray"
)

$pyinstallerArgs = @(
  "--noconfirm",
  "--clean",
  "--onedir",
  "--name", "math-api",
  "--distpath", $OutputRoot,
  "--workpath", $work,
  "--specpath", $spec,
  "--paths", $apiPath,
  "--hidden-import", "app.main",
  "--collect-submodules", "app"
)
foreach ($moduleName in $excludedModules) {
  $pyinstallerArgs += @("--exclude-module", $moduleName)
}
$pyinstallerArgs += $entry

& $python -m PyInstaller @pyinstallerArgs

$built = Join-Path $OutputRoot "math-api\math-api.exe"
if (-not (Test-Path -LiteralPath $built)) {
  throw "PyInstaller did not create $built"
}

"API exe: $built"
