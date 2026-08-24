# ci-local.ps1 — the manual CI gate for Aegis Labs.
#
# Run this BEFORE merging dev -> main. It is your CI: the same checks a hosted
# pipeline would run, triggered by hand. If anything here fails, do NOT deploy.
#
#   Frontend : eslint + production build (vite)
#   Backend  : byte-compile every module + import-boot smoke (seeds run against
#              a THROWAWAY temp DB, so your local system_state.db is untouched)
#              + pytest suite (also runs against a throwaway temp DB)
#
# Usage:
#   .\ci-local.ps1              # run all gates (lint is advisory)
#   .\ci-local.ps1 -SkipBuild   # skip the (slower) frontend build, still lint
#   .\ci-local.ps1 -StrictLint  # make eslint a BLOCKING gate too
#
# Note: eslint remains ADVISORY by default for compatibility with the existing
# release process. Phase 0 established a clean lint baseline; use -StrictLint
# for a blocking local gate and keep new findings from entering the source.
#
param(
    [switch]$SkipBuild,
    [switch]$StrictLint,
    [switch]$SkipE2E
)

$ErrorActionPreference = "Stop"
$repoRoot = $PSScriptRoot
$results  = [System.Collections.ArrayList]::new()
$venvPy = Join-Path $repoRoot ".venv\Scripts\python.exe"

function Invoke-Gate {
    param([string]$Name, [scriptblock]$Action, [switch]$Advisory)
    Write-Host "`n===== $Name =====" -ForegroundColor Cyan
    $ok = $true
    # Gates shell out to native tools (npm, vite, playwright, python) whose
    # benign informational output on stderr (e.g. vite's chunk-size notice)
    # would otherwise be promoted into a terminating NativeCommandError by
    # the script-wide $ErrorActionPreference = "Stop" and wrongly fail the
    # gate. Exit code is the real signal — check that instead.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Action
        if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) { $ok = $false }
    } catch {
        Write-Host $_.Exception.Message -ForegroundColor Red
        $ok = $false
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    # Advisory gates report their result but never block the release.
    $blocking = -not $Advisory
    [void]$results.Add([pscustomobject]@{ Gate = $Name; Passed = $ok; Blocking = $blocking })
    if ($ok)            { Write-Host "PASS: $Name" -ForegroundColor Green }
    elseif (-not $blocking) { Write-Host "WARN: $Name (advisory — not blocking)" -ForegroundColor Yellow }
    else                { Write-Host "FAIL: $Name" -ForegroundColor Red }
}

# ── Documentation: workspace structure and current-contract consistency ──────
Invoke-Gate "documentation: consistency" {
    if (-not (Test-Path $venvPy)) { throw "repo-root .venv not found at $venvPy. Run .\setup.ps1 first." }
    & $venvPy (Join-Path $repoRoot "..\tools\check_documentation.py")
}

# ── Frontend: lint (advisory unless -StrictLint) ─────────────────────────────
Invoke-Gate "frontend: eslint" -Advisory:(-not $StrictLint) {
    Push-Location (Join-Path $repoRoot "ui")
    try {
        if (-not (Test-Path "node_modules")) {
            Write-Host "node_modules missing — running npm ci ..." -ForegroundColor Yellow
            $env:NODE_EXTRA_CA_CERTS = "$env:USERPROFILE\.azure-ca\ca-bundle.pem"
            npm ci
        }
        npm run lint
    } finally { Pop-Location }
}

# ── Frontend: production build ────────────────────────────────────────────────
if (-not $SkipBuild) {
    Invoke-Gate "frontend: vite build" {
        Push-Location (Join-Path $repoRoot "ui")
        try { npm run build } finally { Pop-Location }
    }
} else {
    Write-Host "`n(skipping frontend build — -SkipBuild)" -ForegroundColor DarkGray
}

# ── Frontend: reachability gate (RET-04, 0.5.0) ───────────────────────────────
# Blocking, not advisory: an export with no caller and no keep-reason, or a
# ui/src/** file reachable from nothing, fails the build. See
# ui/scripts/check-reachability.mjs and docs/0.5.0/01-ret-census.md.
Invoke-Gate "frontend: reachability (RET-04)" {
    Push-Location (Join-Path $repoRoot "ui")
    try { npm run check:reachability } finally { Pop-Location }
}

# ── Backend: byte-compile every module (fast syntax check, no side effects) ───
Invoke-Gate "backend: compile" {
    if (-not (Test-Path $venvPy)) { throw "repo-root .venv not found at $venvPy. Run .\setup.ps1 first." }
    & $venvPy -m compileall -q (Join-Path $repoRoot "backend")
}

# ── Backend: import-boot smoke against a THROWAWAY DB ─────────────────────────
# Importing main.py runs load_dotenv + init_schema + all seeders. Pointing
# SYSTEM_DB_PATH at a temp file means the smoke exercises the full boot path
# (schema + seeds + router wiring) without mutating your real local state.
Invoke-Gate "backend: import-boot smoke" {
    if (-not (Test-Path $venvPy)) { throw "repo-root .venv not found." }
    $tmpDb = Join-Path $env:TEMP ("aegis-ci-smoke-{0}.db" -f $PID)
    $env:SYSTEM_DB_PATH = $tmpDb
    Remove-Item Env:\SYSTEM_DB_BACKUP_PATH -ErrorAction SilentlyContinue  # no backup thread in CI
    Push-Location (Join-Path $repoRoot "backend")
    try {
        & $venvPy -c "import main; assert main.app is not None; print('app booted; routes=', len(main.app.routes))"
    } finally {
        Pop-Location
        Remove-Item Env:\SYSTEM_DB_PATH -ErrorAction SilentlyContinue
        Remove-Item $tmpDb -ErrorAction SilentlyContinue
    }
}

# ── Backend: pytest suite against a THROWAWAY DB ───────────────────────────────
Invoke-Gate "backend: pytest" {
    if (-not (Test-Path $venvPy)) { throw "repo-root .venv not found." }
    $tmpDb = Join-Path $env:TEMP ("aegis-ci-pytest-{0}.db" -f $PID)
    $env:SYSTEM_DB_PATH = $tmpDb
    Remove-Item Env:\SYSTEM_DB_BACKUP_PATH -ErrorAction SilentlyContinue
    Push-Location (Join-Path $repoRoot "backend")
    try {
        & $venvPy -m pytest tests -q
    } finally {
        Pop-Location
        Remove-Item Env:\SYSTEM_DB_PATH -ErrorAction SilentlyContinue
        Remove-Item $tmpDb -ErrorAction SilentlyContinue
    }
}

# ── Browser: Chromium workflow checks ─────────────────────────────────────────
if (-not $SkipE2E) {
    Invoke-Gate "frontend: Playwright Chromium" {
        Push-Location (Join-Path $repoRoot "ui")
        try { npm run test:e2e } finally { Pop-Location }
    }
} else {
    Write-Host "`n(skipping Playwright Chromium — -SkipE2E)" -ForegroundColor DarkGray
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host "`n================ CI SUMMARY ================" -ForegroundColor Cyan
$results | ForEach-Object {
    if ($_.Passed)         { $mark = "[PASS]"; $color = "Green" }
    elseif (-not $_.Blocking) { $mark = "[WARN]"; $color = "Yellow" }
    else                   { $mark = "[FAIL]"; $color = "Red" }
    Write-Host ("{0}  {1}" -f $mark, $_.Gate) -ForegroundColor $color
}
$failed = @($results | Where-Object { -not $_.Passed -and $_.Blocking }).Count
$warned = @($results | Where-Object { -not $_.Passed -and -not $_.Blocking }).Count
if ($failed -gt 0) {
    Write-Host "`n$failed blocking gate(s) FAILED — do not merge/deploy." -ForegroundColor Red
    exit 1
} else {
    if ($warned -gt 0) {
        Write-Host "`n$warned advisory gate(s) had findings (see above) — not blocking." -ForegroundColor Yellow
    }
    Write-Host "All blocking gates passed — safe to merge dev -> main and deploy." -ForegroundColor Green
    exit 0
}
