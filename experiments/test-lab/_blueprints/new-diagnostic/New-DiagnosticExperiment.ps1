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

    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = '0.1.0',

    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
if (-not $OutputRoot) {
    $OutputRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
}
$diagnosticKey = 't{0}_d{1:D2}_{2}' -f $TestId, $DiagnosticId, $Slug
$frontendKey = ('t{0}-d{1:D2}-{2}' -f $TestId, $DiagnosticId, $Slug).Replace('_', '-')
$resolvedRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$target = [System.IO.Path]::GetFullPath((Join-Path $resolvedRoot $diagnosticKey))
$rootPrefix = $resolvedRoot.TrimEnd('\') + '\'

if (-not $target.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Target escapes the requested output root: $target"
}
if (Test-Path -LiteralPath $target) {
    throw "Diagnostic directory already exists; refusing to overwrite: $target"
}
if (-not $PSCmdlet.ShouldProcess($target, 'Create diagnostic experiment scaffold')) {
    return
}

$directories = @(
    '', 'kb', 'kb\archive', 'prompts', 'schemas', 'src',
    'inputs', 'inputs\test_fixtures', 'tests', 'notebooks', 'output', 'promotion'
)
foreach ($relativePath in $directories) {
    $path = if ($relativePath) { Join-Path $target $relativePath } else { $target }
    New-Item -ItemType Directory -Path $path | Out-Null
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

$templateMap = [ordered]@{
    'intake.template.yaml' = 'intake.yaml'
    'experiment-contract.template.yaml' = 'experiment-contract.yaml'
    'process-record.template.md' = 'process-record.md'
    'promotion-readiness.template.yaml' = 'promotion\readiness.yaml'
    'artifact-inventory.template.md' = 'promotion\artifact-inventory.md'
    'known-limitations.template.md' = 'promotion\known-limitations.md'
}
foreach ($entry in $templateMap.GetEnumerator()) {
    Expand-BlueprintTemplate `
        -Source (Join-Path $PSScriptRoot $entry.Key) `
        -Destination (Join-Path $target $entry.Value)
}

$readme = @(
    "# $Title experiment"
    ''
    "- Diagnostic key: ``$diagnosticKey``"
    "- Contract version: ``$Version``"
    "- Owner: ``$Owner``"
    '- Status: experimental'
    ''
    'Complete `intake.yaml` and `experiment-contract.yaml` before implementation. Keep analytical'
    'evidence in `process-record.md`; complete the `promotion/` records before production handoff.'
) -join [Environment]::NewLine
[System.IO.File]::WriteAllText((Join-Path $target 'README.md'), $readme + [Environment]::NewLine, $utf8)

$pythonFiles = @(
    'src\engine.py', 'src\summaries.py', 'src\actions.py',
    'tests\test_engine.py', 'tests\test_invariants.py', 'tests\test_contract.py'
)
foreach ($relativePath in $pythonFiles) {
    $purpose = [System.IO.Path]::GetFileNameWithoutExtension($relativePath).Replace('_', ' ')
    $content = '"""' + $Title + ' experimental ' + $purpose + '."""' + [Environment]::NewLine
    [System.IO.File]::WriteAllText((Join-Path $target $relativePath), $content, $utf8)
}

foreach ($relativePath in @('kb\archive', 'prompts', 'schemas', 'inputs\test_fixtures', 'notebooks', 'output')) {
    [System.IO.File]::WriteAllText((Join-Path $target "$relativePath\.gitkeep"), '', $utf8)
}

Write-Output $target
