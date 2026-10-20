param(
  [Parameter(Mandatory=$true)][string]$Path,
  [Parameter(Mandatory=$true)][string]$Key,
  [Parameter(Mandatory=$true)][string]$Value
)

$lines = @()
if (Test-Path -LiteralPath $Path) {
  $lines = Get-Content -LiteralPath $Path -Encoding UTF8
}

$found = $false
$updated = foreach ($line in $lines) {
  if ($line -match "^\s*$([regex]::Escape($Key))=") {
    $found = $true
    "$Key=$Value"
  } else {
    $line
  }
}

if (-not $found) {
  $updated += "$Key=$Value"
}

Set-Content -LiteralPath $Path -Value $updated -Encoding UTF8
