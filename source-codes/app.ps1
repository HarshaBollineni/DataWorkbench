<#
Manage the local Archimedes development application.

Examples:
  .\app.ps1 start
  .\app.ps1 restart -Service Backend
  .\app.ps1 status
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("Start", "Stop", "Restart", "Status")]
    [string]$Action,

    [ValidateSet("All", "Backend", "Frontend")]
    [string]$Service = "All"
)

$ErrorActionPreference = "Stop"
$repoRoot = $PSScriptRoot
$runtimeDir = Join-Path $repoRoot ".runtime"

$services = @{
    Backend = @{ Port = 8001; WorkingDirectory = (Join-Path $repoRoot "backend"); PidFile = (Join-Path $runtimeDir "backend.json") }
    Frontend = @{ Port = 5175; WorkingDirectory = (Join-Path $repoRoot "ui"); PidFile = (Join-Path $runtimeDir "frontend.json") }
}

function Get-SelectedServices {
    if ($Service -eq "All") { return @("Backend", "Frontend") }
    return @($Service)
}

function Get-Listener {
    param([int]$Port)
    try {
        return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop)
    } catch {
        return @()
    }
}

function Test-PortOpen {
    param([int]$Port)
    if (@(Get-Listener -Port $Port).Count -gt 0) { return $true }

    # Get-NetTCPConnection may be unavailable/restricted in some PowerShell
    # hosts. A direct loopback connection still answers the readiness question
    # without requiring administrator rights or process-table access.
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(500)) { return $false }
        $client.EndConnect($pending)
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Get-RecordedProcess {
    param([hashtable]$Definition)
    if (-not (Test-Path $Definition.PidFile)) { return $null }
    try {
        $record = Get-Content -Raw $Definition.PidFile | ConvertFrom-Json
        $process = Get-Process -Id $record.ProcessId -ErrorAction Stop
        # A stale PID file must never make us manage an unrelated process that
        # later reused the same numeric PID. Records created before this check
        # already carry StartedAt, so the migration is naturally compatible.
        $recordedStart = [DateTimeOffset]::Parse([string]$record.StartedAt)
        $actualStart = [DateTimeOffset]$process.StartTime
        if ([Math]::Abs(($actualStart - $recordedStart).TotalSeconds) -gt 2) {
            throw "Recorded PID was reused by another process."
        }
        return [pscustomobject]@{ Record = $record; Process = $process }
    } catch {
        Remove-Item -LiteralPath $Definition.PidFile -Force -ErrorAction SilentlyContinue
        return $null
    }
}

function Get-ProcessTreeIds {
    param([int]$RootProcessId)
    $allProcesses = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $pending = [System.Collections.Generic.Queue[int]]::new()
    $pending.Enqueue($RootProcessId)
    $ids = [System.Collections.Generic.List[int]]::new()
    while ($pending.Count -gt 0) {
        $id = $pending.Dequeue()
        if ($ids.Contains($id)) { continue }
        $ids.Add($id)
        foreach ($child in @($allProcesses | Where-Object { $_.ParentProcessId -eq $id })) {
            $pending.Enqueue([int]$child.ProcessId)
        }
    }
    return @($ids)
}

function Wait-ForListener {
    param([int]$Port, [string]$Label)
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        if (Test-PortOpen -Port $Port) {
            Write-Host "$Label is ready on http://localhost:$Port" -ForegroundColor Green
            return
        }
        Start-Sleep -Seconds 1
    }
    throw "$Label did not start on port $Port. Check .runtime logs for details."
}

function Start-Service {
    param([string]$Name)
    $definition = $services[$Name]
    if (Get-RecordedProcess -Definition $definition) {
        Write-Host "$Name is already managed by this script." -ForegroundColor DarkGray
        return
    }
    if (Test-PortOpen -Port $definition.Port) {
        Write-Host "$Name cannot start: port $($definition.Port) is already in use by an unmanaged process. This script will not stop it." -ForegroundColor Yellow
        return
    }

    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    $stdout = Join-Path $runtimeDir ("{0}.stdout.log" -f $Name.ToLowerInvariant())
    $stderr = Join-Path $runtimeDir ("{0}.stderr.log" -f $Name.ToLowerInvariant())
    $stdin = Join-Path $runtimeDir "detached.stdin"
    Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $stdin)) { New-Item -ItemType File -Path $stdin -Force | Out-Null }

    if ($Name -eq "Backend") {
        $python = Join-Path $repoRoot ".venv\Scripts\python.exe"
        if (-not (Test-Path $python)) { throw "Backend environment is missing. Run .\setup.ps1 first." }
        $process = Start-Process -FilePath $python -ArgumentList @("-m", "uvicorn", "main:app", "--reload", "--port", "8001") `
            -WorkingDirectory $definition.WorkingDirectory -WindowStyle Hidden -RedirectStandardInput $stdin `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    } else {
        if (-not (Test-Path (Join-Path $definition.WorkingDirectory "node_modules"))) { throw "Frontend dependencies are missing. Run .\setup.ps1 first." }
        # Launch Vite with Node directly. npm.cmd is a short-lived cmd wrapper
        # that adds another process layer and may retain console handles.
        $node = (Get-Command node.exe -ErrorAction Stop).Source
        $vite = Join-Path $definition.WorkingDirectory "node_modules\vite\bin\vite.js"
        if (-not (Test-Path $vite)) { throw "Vite is missing. Run .\setup.ps1 first." }
        $process = Start-Process -FilePath $node -ArgumentList @($vite, "--host", "127.0.0.1", "--port", "5175", "--strictPort") `
            -WorkingDirectory $definition.WorkingDirectory -WindowStyle Hidden -RedirectStandardInput $stdin `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    }

    [pscustomobject]@{ ProcessId = $process.Id; StartedAt = (Get-Date).ToString("o") } |
        ConvertTo-Json | Set-Content -LiteralPath $definition.PidFile -Encoding UTF8
    Wait-ForListener -Port $definition.Port -Label $Name
}

function Stop-Service {
    param([string]$Name)
    $definition = $services[$Name]
    $managed = Get-RecordedProcess -Definition $definition

    # Stop both processes recorded by this script and unmanaged processes that
    # occupy the service port. This lets Stop/Restart recover from manually
    # launched or orphaned Vite/Uvicorn instances.
    $rootIds = [System.Collections.Generic.HashSet[int]]::new()
    if ($managed) {
        [void]$rootIds.Add([int]$managed.Process.Id)
    }
    foreach ($listener in @(Get-Listener -Port $definition.Port)) {
        $ownerId = [int]$listener.OwningProcess
        if ($ownerId -gt 0 -and $ownerId -ne $PID) {
            [void]$rootIds.Add($ownerId)
        }
    }

    if ($rootIds.Count -eq 0) {
        Remove-Item -LiteralPath $definition.PidFile -Force -ErrorAction SilentlyContinue
        if (Test-PortOpen -Port $definition.Port) {
            throw "$Name is listening on port $($definition.Port), but its process could not be identified. Run PowerShell as Administrator and try again."
        }
        Write-Host "$Name is not running." -ForegroundColor DarkGray
        return
    }

    $ids = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($rootId in $rootIds) {
        try {
            foreach ($processId in @(Get-ProcessTreeIds -RootProcessId $rootId)) {
                if ($processId -ne $PID) { [void]$ids.Add([int]$processId) }
            }
        } catch {
            # Process-tree discovery can require elevated process-table access.
            # The listener/root process can still be force-stopped directly.
            [void]$ids.Add([int]$rootId)
        }
    }

    $orderedIds = @($ids)
    [array]::Reverse($orderedIds)
    foreach ($processId in $orderedIds) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }

    for ($attempt = 1; $attempt -le 20 -and (Test-PortOpen -Port $definition.Port); $attempt++) {
        Start-Sleep -Milliseconds 100
    }
    if (Test-PortOpen -Port $definition.Port) {
        throw "$Name could not be stopped on port $($definition.Port). Run PowerShell as Administrator and try again."
    }

    Remove-Item -LiteralPath $definition.PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "$Name stopped." -ForegroundColor Green
}

function Show-ServiceStatus {
    param([string]$Name)
    $definition = $services[$Name]
    $managed = Get-RecordedProcess -Definition $definition
    $listeners = @(Get-Listener -Port $definition.Port)
    $portOpen = Test-PortOpen -Port $definition.Port
    if ($managed -and $portOpen) {
        Write-Host "${Name}: running (managed, port $($definition.Port), PID $($managed.Process.Id))." -ForegroundColor Green
    } elseif ($portOpen) {
        $owner = ($listeners | Select-Object -First 1).OwningProcess
        $ownerText = if ($owner) { " (PID $owner)" } else { "" }
        Write-Host "${Name}: port $($definition.Port) is in use by an unmanaged process${ownerText}." -ForegroundColor Yellow
    } else {
        Write-Host "${Name}: stopped." -ForegroundColor DarkGray
    }
}

switch ($Action) {
    "Start"   { foreach ($name in Get-SelectedServices) { Start-Service -Name $name } }
    "Stop"    { foreach ($name in Get-SelectedServices) { Stop-Service -Name $name } }
    "Restart" {
        foreach ($name in Get-SelectedServices) { Stop-Service -Name $name }
        Start-Sleep -Seconds 2
        foreach ($name in Get-SelectedServices) { Start-Service -Name $name }
    }
    "Status"  { foreach ($name in Get-SelectedServices) { Show-ServiceStatus -Name $name } }
}
