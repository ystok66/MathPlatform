param(
  [string]$ReleaseDir = ""
)

$ErrorActionPreference = "Stop"

$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
if (-not $ReleaseDir) {
  $ReleaseDir = Join-Path $root "dist\math-platform"
}

$releasePath = [System.IO.Path]::GetFullPath($ReleaseDir)
$distRoot = [System.IO.Path]::GetFullPath((Join-Path $root "dist"))
if (-not $releasePath.StartsWith($distRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to sync outside dist: $releasePath"
}

$webDir = Join-Path $root "apps\web"
$webStandalone = Join-Path $webDir ".next\standalone"
$webStatic = Join-Path $webDir ".next\static"
$releaseWeb = Join-Path $releasePath "web"

if (-not (Test-Path -LiteralPath (Join-Path $webStandalone "server.js"))) {
  throw "Missing Next standalone server.js. Run npm run build in apps\web first."
}
if (-not (Test-Path -LiteralPath $webStatic)) {
  throw "Missing Next static output. Run npm run build in apps\web first."
}
if (-not (Test-Path -LiteralPath $releaseWeb)) {
  New-Item -ItemType Directory -Force -Path $releaseWeb | Out-Null
}

Copy-Item -Path (Join-Path $webStandalone "*") -Destination $releaseWeb -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $releaseWeb ".next\static") | Out-Null
Copy-Item -Path (Join-Path $webStatic "*") -Destination (Join-Path $releaseWeb ".next\static") -Recurse -Force

$publicSource = Join-Path $webDir "public"
if (Test-Path -LiteralPath $publicSource) {
  Copy-Item -LiteralPath $publicSource -Destination (Join-Path $releaseWeb "public") -Recurse -Force
}

"Synced web release: $releaseWeb"
