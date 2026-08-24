param(
    [Parameter(Mandatory = $true)][string]$Version
)

$ErrorActionPreference = "Stop"

if ($Version -notmatch '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$') {
    throw "'$Version' is not a valid Semantic Version. Use MAJOR.MINOR.PATCH, for example 0.2.0 or 0.2.0-rc.1."
}

$repoRoot = $PSScriptRoot
Set-Content -LiteralPath (Join-Path $repoRoot "VERSION") -Value $Version -NoNewline

Push-Location (Join-Path $repoRoot "ui")
try {
    npm version $Version --no-git-tag-version --allow-same-version
    if ($LASTEXITCODE -ne 0) { throw "npm could not update the UI package version." }
} finally {
    Pop-Location
}

Write-Host "Archimedes version set to $Version." -ForegroundColor Green
