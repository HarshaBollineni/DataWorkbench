[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]*$')]
    [string]$PackageName,

    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'

$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourceRoot = Join-Path $workspaceRoot 'source-codes'
$templateRoot = Join-Path $PSScriptRoot 'deployment-package'

if (-not $OutputDirectory) {
    $OutputDirectory = Split-Path -Parent $workspaceRoot
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $OutputDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
}

$archivePath = Join-Path $OutputDirectory "$PackageName.zip"
if (Test-Path -LiteralPath $archivePath) {
    throw "Output already exists: $archivePath"
}

$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("dataworkbench-package-" + [guid]::NewGuid().ToString('N'))
$packageRoot = Join-Path $stagingRoot $PackageName

function Copy-DeploymentTree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string[]]$ExcludedDirectories = @(),
        [string[]]$ExcludedFiles = @(),
        [string[]]$ExcludedExtensions = @()
    )

    $sourcePath = (Resolve-Path -LiteralPath $Source).Path.TrimEnd('\', '/')
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null

    foreach ($file in Get-ChildItem -LiteralPath $sourcePath -Recurse -Force -File) {
        $relative = $file.FullName.Substring($sourcePath.Length).TrimStart('\', '/')
        $segments = $relative -split '[\\/]'

        if ($segments | Where-Object { $ExcludedDirectories -contains $_ }) { continue }
        if ($ExcludedFiles -contains $file.Name) { continue }
        if ($file.Name -like '.env*') { continue }
        if ($ExcludedExtensions -contains $file.Extension.ToLowerInvariant()) { continue }

        $target = Join-Path $Destination $relative
        $targetParent = Split-Path -Parent $target
        if (-not (Test-Path -LiteralPath $targetParent)) {
            New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
        }
        Copy-Item -LiteralPath $file.FullName -Destination $target
    }
}

try {
    New-Item -ItemType Directory -Path $packageRoot | Out-Null

    Copy-DeploymentTree `
        -Source (Join-Path $sourceRoot 'backend') `
        -Destination (Join-Path $packageRoot 'backend') `
        -ExcludedDirectories @('tests', 'uploads', 'kb_storage', 'analysis_artifacts', '__pycache__', '.pytest_cache') `
        -ExcludedFiles @('PLAN2_EXECUTION.md') `
        -ExcludedExtensions @('.db', '.pyc', '.pyo', '.stackdump')

    Copy-DeploymentTree `
        -Source (Join-Path $sourceRoot 'ui') `
        -Destination (Join-Path $packageRoot 'ui') `
        -ExcludedDirectories @('node_modules', 'dist', 'tests', 'e2e', 'scripts', 'playwright-report', 'test-results', '.vite') `
        -ExcludedFiles @('playwright.config.js', 'eslint.config.js', 'README.md')

    Copy-Item -LiteralPath (Join-Path $sourceRoot 'Dockerfile') -Destination $packageRoot
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'VERSION') -Destination $packageRoot
    Copy-Item -LiteralPath (Join-Path $templateRoot 'backend.dockerignore') -Destination (Join-Path $packageRoot '.dockerignore')
    Copy-Item -LiteralPath (Join-Path $templateRoot 'docker-compose.yml') -Destination $packageRoot
    Copy-Item -LiteralPath (Join-Path $templateRoot 'frontend.Dockerfile') -Destination (Join-Path $packageRoot 'ui\Dockerfile')
    Copy-Item -LiteralPath (Join-Path $templateRoot 'nginx.conf') -Destination (Join-Path $packageRoot 'ui\nginx.conf')
    Copy-Item -LiteralPath (Join-Path $templateRoot 'DEPLOY.md') -Destination $packageRoot

    $forbidden = Get-ChildItem -LiteralPath $packageRoot -Recurse -Force -File | Where-Object {
        $_.Name -like '.env*' -or
        $_.FullName -match '[\\/](node_modules|tests|e2e|playwright-report|test-results|uploads|kb_storage|analysis_artifacts|__pycache__|\.venv|\.runtime)[\\/]' -or
        $_.Extension.ToLowerInvariant() -in @('.db', '.pem', '.key', '.pyc', '.pyo')
    }
    if ($forbidden) {
        $names = ($forbidden.FullName -join [Environment]::NewLine)
        throw "Forbidden files reached the package staging directory:$([Environment]::NewLine)$names"
    }

    Compress-Archive -LiteralPath $packageRoot -DestinationPath $archivePath -CompressionLevel Optimal

    $archive = Get-Item -LiteralPath $archivePath
    $hash = Get-FileHash -LiteralPath $archivePath -Algorithm SHA256
    [PSCustomObject]@{
        Path = $archive.FullName
        SizeMB = [math]::Round($archive.Length / 1MB, 2)
        SHA256 = $hash.Hash
    }
}
finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        $resolvedStage = (Resolve-Path -LiteralPath $stagingRoot).Path
        $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
        if (-not $resolvedStage.StartsWith($tempRoot + '\dataworkbench-package-', [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean unexpected staging path: $resolvedStage"
        }
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}
