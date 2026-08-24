param(
    [Parameter(Position = 0)]
    [ValidateSet("check", "impact", "help")]
    [string]$Command = "check",

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Paths
)

$ErrorActionPreference = "Stop"
$workspaceRoot = $PSScriptRoot
$checker = Join-Path $workspaceRoot "tools\check_documentation.py"
$venvPython = Join-Path $workspaceRoot "source-codes\.venv\Scripts\python.exe"

if (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python was not found. Run source-codes\setup.ps1 or install Python 3.12+."
    }
    $python = $pythonCommand.Source
}

switch ($Command) {
    "check" {
        & $python $checker
        exit $LASTEXITCODE
    }
    "impact" {
        & $python $checker --impact @Paths
        exit $LASTEXITCODE
    }
    "help" {
        Write-Host "DataWorkbench documentation"
        Write-Host ""
        Write-Host "  .\documentation.ps1 check"
        Write-Host "  .\documentation.ps1 impact <changed-path> [more-paths]"
        Write-Host "  .\documentation.ps1 help"
        exit 0
    }
}
