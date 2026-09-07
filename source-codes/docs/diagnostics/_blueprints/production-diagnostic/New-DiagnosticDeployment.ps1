[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 99)]
    [int]$TestId,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 99)]
    [int]$DiagnosticId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z][a-z0-9_]*$')]
    [string]$Slug,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Title,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Owner,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Z][A-Za-z0-9]*$')]
    [string]$ComponentName,

    [string]$SourceRoot
)

$ErrorActionPreference = 'Stop'
if (-not $SourceRoot) {
    $SourceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..\..')).Path
}
$diagnosticKey = 't{0}_d{1:D2}_{2}' -f $TestId, $DiagnosticId, $Slug
$frontendKey = ('t{0}-d{1:D2}-{2}' -f $TestId, $DiagnosticId, $Slug).Replace('_', '-')
$resolvedRoot = [System.IO.Path]::GetFullPath($SourceRoot)
$rootPrefix = $resolvedRoot.TrimEnd('\') + '\'
$targets = [ordered]@{
    Backend = Join-Path $resolvedRoot "backend\domains\test_lab\diagnostics\$diagnosticKey"
    UnitTests = Join-Path $resolvedRoot "backend\tests\unit\test_lab\diagnostics\$diagnosticKey"
    IntegrationTests = Join-Path $resolvedRoot "backend\tests\integration\test_lab\diagnostics\$diagnosticKey"
    Frontend = Join-Path $resolvedRoot "ui\src\features\test-lab\diagnostics\$frontendKey"
    Documents = Join-Path $resolvedRoot "docs\diagnostics\$Slug"
}

foreach ($entry in $targets.GetEnumerator()) {
    $fullPath = [System.IO.Path]::GetFullPath($entry.Value)
    if (-not $fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$($entry.Key) target escapes the requested source root: $fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        throw "$($entry.Key) target already exists; refusing partial overwrite: $fullPath"
    }
}

$targetSummary = ($targets.Values -join ', ')
if (-not $PSCmdlet.ShouldProcess($targetSummary, 'Create diagnostic deployment scaffold')) {
    return
}

foreach ($target in $targets.Values) {
    New-Item -ItemType Directory -Path $target | Out-Null
}

$tokens = [ordered]@{
    DIAGNOSTIC_KEY = $diagnosticKey
    FRONTEND_KEY = $frontendKey
    TEST_ID = [string]$TestId
    DIAGNOSTIC_ID = [string]$DiagnosticId
    SLUG = $Slug
    TITLE = $Title
    OWNER = $Owner
    VERSION = $Version
    COMPONENT_NAME = $ComponentName
}
$utf8 = [System.Text.UTF8Encoding]::new($false)

function Expand-BlueprintTemplate {
    param([string]$Source, [string]$Destination)
    $content = [System.IO.File]::ReadAllText($Source)
    foreach ($entry in $tokens.GetEnumerator()) {
        $content = $content.Replace('{{' + $entry.Key + '}}', [string]$entry.Value)
    }
    [System.IO.File]::WriteAllText($Destination, $content, $utf8)
}

$documentMap = [ordered]@{
    'deployment-contract.template.md' = 'contract.md'
    'implementation-checklist.template.md' = 'implementation-checklist.md'
    'acceptance-evidence.template.md' = 'acceptance-evidence.md'
    'promotion-record.template.md' = 'promotion-record.md'
    'deployment-map.template.yaml' = 'deployment-map.yaml'
}
foreach ($entry in $documentMap.GetEnumerator()) {
    Expand-BlueprintTemplate `
        -Source (Join-Path $PSScriptRoot $entry.Key) `
        -Destination (Join-Path $targets.Documents $entry.Value)
}

$backendFiles = @('__init__.py', 'manifest.py', 'runner.py', 'engine.py', 'resources.py', 'summaries.py', 'actions.py')
foreach ($fileName in $backendFiles) {
    $purpose = [System.IO.Path]::GetFileNameWithoutExtension($fileName).Replace('_', ' ')
    $content = '"""' + $Title + ' production ' + $purpose + ' boundary."""' + [Environment]::NewLine
    [System.IO.File]::WriteAllText((Join-Path $targets.Backend $fileName), $content, $utf8)
}

$backendReadme = @(
    "# $Title"
    ''
    "Production package for ``$diagnosticKey``."
    ''
    "The accepted contract is ``docs/diagnostics/$Slug/contract.md``. Keep the framework register"
    '`workflow_pending` until every acceptance gate passes.'
) -join [Environment]::NewLine
[System.IO.File]::WriteAllText((Join-Path $targets.Backend 'README.md'), $backendReadme + [Environment]::NewLine, $utf8)

$scopeComponent = @(
    "export default function ${ComponentName}ScopeGate() {"
    '  // Replace this scaffold with the contracted understand/scope/configure/preview/launch workflow.'
    '  return null;'
    '}'
) -join [Environment]::NewLine
[System.IO.File]::WriteAllText((Join-Path $targets.Frontend "${ComponentName}ScopeGate.jsx"), $scopeComponent + [Environment]::NewLine, $utf8)

$resultComponent = @(
    "export default function ${ComponentName}Results() {"
    '  // Replace this scaffold with the contracted outcome/detail/coverage/report/action workflow.'
    '  return null;'
    '}'
) -join [Environment]::NewLine
[System.IO.File]::WriteAllText((Join-Path $targets.Frontend "${ComponentName}Results.jsx"), $resultComponent + [Environment]::NewLine, $utf8)

foreach ($testRoot in @($targets.UnitTests, $targets.IntegrationTests)) {
    $content = '"""Acceptance coverage scaffold for ' + $diagnosticKey + '."""' + [Environment]::NewLine
    [System.IO.File]::WriteAllText((Join-Path $testRoot 'test_diagnostic.py'), $content, $utf8)
}

$targets.GetEnumerator() | ForEach-Object { "{0}: {1}" -f $_.Key, $_.Value }
