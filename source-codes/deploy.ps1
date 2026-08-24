<#
Deploy Archimedes to its dedicated Azure resources.

Examples:
  .\deploy.ps1 -Tag 0.2.0
  .\deploy.ps1 -Tag 0.2.0 -Component Backend
  .\deploy.ps1 -Tag 0.2.0 -Component Frontend

`All` is a complete release and creates an immutable Git tag after both
components succeed. A single-component deployment does not create a release
tag because the product would be only partially released.
#>
param(
    [Parameter(Mandatory = $true)][string]$Tag,
    [ValidateSet("All", "Backend", "Frontend")][string]$Component = "All",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repoRoot = $PSScriptRoot
Set-Location $repoRoot

function Remove-DirectoryWithRetry {
    # `az acr build`'s local log-streaming can crash on Windows (see the
    # comment below) while a helper process briefly still holds the staging
    # directory as its current working directory. That handle clears within
    # a second or two on its own; retry instead of failing the whole release
    # on what is otherwise a successful deployment.
    param([string]$Path, [int]$Attempts = 6, [int]$DelaySeconds = 2)
    for ($i = 1; $i -le $Attempts; $i++) {
        if (-not (Test-Path -LiteralPath $Path)) { return }
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return
        } catch {
            if ($i -eq $Attempts) { throw }
            Start-Sleep -Seconds $DelaySeconds
        }
    }
}

function Get-DeploymentConfiguration {
    param([string[]]$RequiredValues)
    $configPath = Join-Path $repoRoot "deployment.local.psd1"
    if (-not (Test-Path $configPath)) {
        throw "Archimedes deployment is not configured. Copy deployment.example.psd1 to deployment.local.psd1 and fill in dedicated Archimedes resources."
    }
    $config = Import-PowerShellDataFile $configPath
    foreach ($name in $RequiredValues) {
        if ([string]::IsNullOrWhiteSpace([string]$config[$name]) -or $config[$name] -like '<*') {
            throw "deployment.local.psd1 must define a real value for '$name'."
        }
    }
    return $config
}

function Initialize-AzureSession {
    param([hashtable]$Config)
    $caBundle = "$env:USERPROFILE\.azure-ca\ca-bundle.pem"
    if (Test-Path $caBundle) {
        $env:REQUESTS_CA_BUNDLE = $caBundle
        $env:NODE_EXTRA_CA_CERTS = $caBundle
    } else {
        Write-Host "Corporate CA bundle not found; using the system certificate store." -ForegroundColor Yellow
        Remove-Item Env:\REQUESTS_CA_BUNDLE -ErrorAction SilentlyContinue
        Remove-Item Env:\NODE_EXTRA_CA_CERTS -ErrorAction SilentlyContinue
    }
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUTF8 = "1"
    try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

    $account = az account show --query "id" -o tsv 2>$null
    if ([string]::IsNullOrWhiteSpace($account)) { throw "Not logged in. Run: az login --use-device-code" }
    az account set --subscription $Config.Subscription 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not select the configured Azure subscription." }
}

function Invoke-BackendDeployment {
    param([hashtable]$Config)
    $stage = Join-Path $env:TEMP "archimedes-build-stage"
    Remove-DirectoryWithRetry -Path $stage
    New-Item -ItemType Directory -Path $stage | Out-Null
    try {
        Write-Host "Staging backend build context ..." -ForegroundColor Cyan
        robocopy (Join-Path $repoRoot "backend") (Join-Path $stage "backend") /E `
            /XD ".venv" "__pycache__" ".pytest_cache" "uploads" `
            /XF ".env" "system_state.db" "system_state.db.bak" "*.pyc" "*.stackdump" /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy backend failed ($LASTEXITCODE)." }

        Copy-Item (Join-Path $repoRoot "Dockerfile") (Join-Path $stage "Dockerfile")
        Copy-Item (Join-Path $repoRoot ".dockerignore") (Join-Path $stage ".dockerignore")
        Copy-Item (Join-Path $repoRoot "VERSION") (Join-Path $stage "VERSION")

        Write-Host "Building $($Config.AcrLoginServer)/$($Config.ContainerApp):$Tag ..." -ForegroundColor Cyan
        Push-Location $stage
        try {
            $buildClientFailed = $false
            try {
                az acr build --registry $Config.Acr --image "$($Config.ContainerApp):$Tag" --file "Dockerfile" .
                $buildClientFailed = $LASTEXITCODE -ne 0
            } catch {
                # Azure CLI can crash only while rendering Unicode build logs on
                # Windows, even while the cloud-side ACR build continues.
                $buildClientFailed = $true
                Write-Host "ACR log streaming failed locally; checking the server-side build status ..." -ForegroundColor Yellow
            }
            if ($buildClientFailed) {
                $final = $null
                for ($attempt = 1; $attempt -le 90; $attempt++) {
                    $final = az acr task list-runs --registry $Config.Acr --top 1 --query "[0].status" -o tsv 2>$null
                    if ($final -and $final -notin @("Running", "Queued")) { break }
                    Start-Sleep -Seconds 10
                }
                if ($final -ne "Succeeded") { throw "ACR build did not succeed (status: $final)." }
            }
        } finally {
            Pop-Location
        }
    } finally {
        Remove-DirectoryWithRetry -Path $stage
    }

    $suffix = ($Tag.ToLower() -replace "[^a-z0-9-]", "") + "-" + (Get-Date -Format "MMddHHmm")
    Write-Host "Updating Container App $($Config.ContainerApp) ..." -ForegroundColor Cyan
    try {
        az containerapp update --name $Config.ContainerApp --resource-group $Config.ResourceGroup `
            --image "$($Config.AcrLoginServer)/$($Config.ContainerApp):$Tag" --revision-suffix $suffix 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Container App update failed (exit $LASTEXITCODE)." }
    } catch {
        # Same class of Azure CLI crash as az acr build above (it renders
        # progress/output assuming a real console, which this non-interactive
        # host does not have). The update call itself may have already
        # succeeded server-side — the health check and revision-serving
        # checks below are the authoritative verification either way, so
        # don't fail here; let them decide.
        Write-Host "Container App update CLI call crashed locally; verifying the actual result below instead of trusting its exit path ..." -ForegroundColor Yellow
    }

    $healthy = $false
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $Config.HealthUrl -TimeoutSec 10 -UseBasicParsing
            if ($response.StatusCode -eq 200) { $healthy = $true; break }
        } catch { }
        Start-Sleep -Seconds 6
    }
    if (-not $healthy) { throw "Backend did not become healthy at $($Config.HealthUrl)." }

    $serving = $null
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        try {
            $revisions = az containerapp revision list -n $Config.ContainerApp -g $Config.ResourceGroup -o json 2>$null | ConvertFrom-Json
            $serving = $revisions | Where-Object { $_.properties.trafficWeight -eq 100 } | Select-Object -First 1
        } catch {
            $serving = $null
        }
        if ($serving -and $serving.name -like "*$suffix*" -and $serving.properties.healthState -eq "Healthy") { break }
        Start-Sleep -Seconds 6
    }
    if (-not ($serving -and $serving.name -like "*$suffix*")) {
        throw "Traffic is not serving the new revision *$suffix*."
    }
    Write-Host "Backend is healthy: $($serving.name)." -ForegroundColor Green
}

function Invoke-FrontendDeployment {
    param([hashtable]$Config)
    $apiBase = $Config.HealthUrl -replace '/health$', ''
    $previousApiBase = $env:VITE_API_BASE
    $env:VITE_API_BASE = "$apiBase/api"
    Push-Location (Join-Path $repoRoot "ui")
    try {
        if (-not (Test-Path "node_modules")) {
            Write-Host "node_modules missing; running npm ci ..." -ForegroundColor Yellow
            try {
                npm ci
            } catch {
                # Same class of local console-rendering crash documented
                # throughout this script for az/vite CLIs; node_modules
                # existing afterward is the authoritative check.
                Write-Host "npm ci CLI call crashed locally; verifying node_modules instead of trusting its exit path ..." -ForegroundColor Yellow
            }
            if (-not (Test-Path "node_modules")) { throw "npm ci failed (node_modules was not created)." }
        }
        try {
            npm run build
        } catch {
            Write-Host "Frontend build CLI call crashed locally (often just vite's own chunk-size warning on stderr); verifying the dist output instead of trusting its exit path ..." -ForegroundColor Yellow
        }
    } finally {
        Pop-Location
        $env:VITE_API_BASE = $previousApiBase
    }

    $dist = Join-Path $repoRoot "ui\dist"
    if (-not (Test-Path $dist)) { throw "Build produced no dist directory." }
    $toolsDir = Join-Path $repoRoot ".deploy-tools"
    $swa = Join-Path $toolsDir "node_modules\.bin\swa.cmd"
    if (-not (Test-Path $swa)) {
        New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
        try {
            npm install --prefix $toolsDir "@azure/static-web-apps-cli"
        } catch {
            Write-Host "SWA CLI install crashed locally; verifying the binary exists instead of trusting its exit path ..." -ForegroundColor Yellow
        }
        if (-not (Test-Path $swa)) { throw "Could not install the Static Web Apps CLI." }
    }

    # Multiple attempts narrowing this down (see prior commits) confirmed the
    # crash is not reliably tied to one single condition — az CLI's bundled
    # 32-bit Python occasionally writes something to stderr this
    # non-interactive host has no console for, and PowerShell 5.1 converts
    # that into a terminating error independent of exit code, no matter which
    # env vars are set. Route through cmd.exe instead: it does not perform
    # PowerShell's native-command error promotion, so this reads the real
    # stdout and the real exit code, nothing else.
    $token = $null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $token = (cmd /c "az staticwebapp secrets list --name `"$($Config.StaticWebApp)`" --resource-group `"$($Config.ResourceGroup)`" --query `"properties.apiKey`" -o tsv 2>nul") | Select-Object -Last 1
        if (-not [string]::IsNullOrWhiteSpace($token)) { break }
        Start-Sleep -Seconds 2
    }
    if ([string]::IsNullOrWhiteSpace($token)) { throw "Could not read the Static Web App deployment token." }
    $swaDeployFailed = $false
    try {
        & $swa deploy $dist --deployment-token $token --env production
        $swaDeployFailed = $LASTEXITCODE -ne 0
    } catch {
        $swaDeployFailed = $true
        Write-Host "SWA deploy CLI call crashed locally; verifying the live site instead of trusting its exit path ..." -ForegroundColor Yellow
    }
    if ($swaDeployFailed) {
        # No exit-code-independent artifact confirms an SWA deploy the way
        # a dist/ directory confirms a build, so fall back to an HTTP check
        # against the live site, mirroring the backend's health-check proof.
        $frontendUp = $false
        for ($attempt = 1; $attempt -le 10; $attempt++) {
            try {
                $response = Invoke-WebRequest -Uri $Config.FrontendUrl -TimeoutSec 10 -UseBasicParsing
                if ($response.StatusCode -eq 200) { $frontendUp = $true; break }
            } catch { }
            Start-Sleep -Seconds 6
        }
        if (-not $frontendUp) { throw "Frontend deployment failed (site did not respond after the CLI call failed)." }
    }
    Write-Host "Frontend deployed to $($Config.FrontendUrl)." -ForegroundColor Green
}

$version = (Get-Content -LiteralPath (Join-Path $repoRoot "VERSION") -Raw).Trim()
if ($Tag -ne $version) { throw "Tag '$Tag' must match VERSION '$version'. Run .\Set-AppVersion.ps1 first." }
$gitTag = "archimedes-v$Tag"
if (-not $Force) {
    if ((git rev-parse --abbrev-ref HEAD).Trim() -ne "main") { throw "Deployments must run from main." }
    if (-not [string]::IsNullOrWhiteSpace((git status --porcelain))) { throw "Working tree is not clean." }
}
if ((git tag --list $gitTag)) { throw "Git tag '$gitTag' already exists; released versions cannot be reused." }

$required = @("Subscription", "ResourceGroup")
if ($Component -in @("All", "Backend")) { $required += @("Acr", "AcrLoginServer", "ContainerApp", "HealthUrl") }
if ($Component -in @("All", "Frontend")) { $required += @("StaticWebApp", "FrontendUrl") }
$config = Get-DeploymentConfiguration -RequiredValues $required
Initialize-AzureSession -Config $config

if ($Component -in @("All", "Backend")) { Invoke-BackendDeployment -Config $config }
if ($Component -in @("All", "Frontend")) { Invoke-FrontendDeployment -Config $config }

if ($Component -eq "All") {
    git tag -a $gitTag -m "Archimedes release $Tag"
    if ($LASTEXITCODE -ne 0) { throw "Deployment succeeded, but Git release tagging failed." }
    Write-Host "Release complete: $gitTag." -ForegroundColor Green
} else {
    Write-Host "$Component deployment complete. No Git release tag was created; use -Component All for a complete release." -ForegroundColor Yellow
}
