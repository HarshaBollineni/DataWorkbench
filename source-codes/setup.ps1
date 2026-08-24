# setup.ps1 - one-time environment setup for Intelligent DQ
# PowerShell only. No && operators. Each step is guarded.

$ErrorActionPreference = "Stop"
$repoRoot = $PSScriptRoot
Set-Location $repoRoot

Write-Host "==> Intelligent DQ setup starting..." -ForegroundColor Cyan
Write-Host "Repo root: $repoRoot"

# 1. Create virtual environment
Write-Host "`n[1/5] Creating virtual environment (.venv)..." -ForegroundColor Yellow
if (Test-Path ".venv") {
    Write-Host "    .venv already exists - skipping creation."
} else {
    py -3.12 -m venv .venv
    if (-not $?) { Write-Host "    FAILED to create .venv. Is Python 3.12+ on PATH?" -ForegroundColor Red; exit 1 }
    Write-Host "    .venv created."
}

# 2. Activate virtual environment
Write-Host "`n[2/5] Activating virtual environment..." -ForegroundColor Yellow
$activate = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $activate)) { Write-Host "    Activation script not found at $activate" -ForegroundColor Red; exit 1 }
. $activate
if (-not $?) { Write-Host "    FAILED to activate .venv." -ForegroundColor Red; exit 1 }
Write-Host "    Virtual environment active."

# 3. Upgrade pip
Write-Host "`n[3/5] Upgrading pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip
if (-not $?) { Write-Host "    FAILED to upgrade pip." -ForegroundColor Red; exit 1 }
Write-Host "    pip upgraded."

# 4. Install backend dependencies
Write-Host "`n[4/5] Installing backend dependencies (backend/requirements.txt)..." -ForegroundColor Yellow
if (-not (Test-Path "backend\requirements.txt")) { Write-Host "    backend\requirements.txt not found." -ForegroundColor Red; exit 1 }
python -m pip install -r backend\requirements.txt
if (-not $?) { Write-Host "    FAILED to install backend dependencies." -ForegroundColor Red; exit 1 }
Write-Host "    Backend dependencies installed."

# 5. Install frontend dependencies
Write-Host "`n[5/5] Installing frontend dependencies (ui)..." -ForegroundColor Yellow
if (-not (Test-Path "ui")) { Write-Host "    ui directory not found." -ForegroundColor Red; exit 1 }
Push-Location ui
npm install
$npmOk = $?
Pop-Location
if (-not $npmOk) { Write-Host "    FAILED to install frontend dependencies." -ForegroundColor Red; exit 1 }
Write-Host "    Frontend dependencies installed."

Write-Host "`n==> Setup complete." -ForegroundColor Green
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Set Azure OpenAI env vars in backend\.env"
Write-Host "  2. Run:  ./app.ps1 start"
Write-Host "  3. Open http://localhost:5175"
