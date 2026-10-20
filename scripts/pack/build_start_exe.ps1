param([string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $root '.tmp\pack-start-exe' }
$target = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not $target.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw 'OutputDirectory must be inside the project.'
}
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The Windows .NET Framework C# compiler is not available.' }
New-Item -ItemType Directory -Path $target -Force | Out-Null
$exe = Join-Path $target 'StartMathPlatform.exe'
& $compiler /nologo /target:winexe /platform:x64 /reference:System.Windows.Forms.dll "/out:$exe" (Join-Path $root 'tools\launcher\StartMathPlatform.cs')
if ($LASTEXITCODE -ne 0) { throw "Entry EXE build failed: $LASTEXITCODE" }
Write-Output "Double-click entry: $exe"
