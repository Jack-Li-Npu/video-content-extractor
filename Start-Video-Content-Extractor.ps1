[CmdletBinding()]
param([switch]$OpenBrowser)

$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$backendDir = Join-Path $appRoot 'backend'
$pythonPath = Join-Path $backendDir '.venv\Scripts\python.exe'
$runtimeDir = Join-Path $appRoot '.runtime'
$recordPath = Join-Path $runtimeDir 'windows-server.json'
$stdoutPath = Join-Path $runtimeDir 'server.stdout.log'
$stderrPath = Join-Path $runtimeDir 'server.stderr.log'
$appUrl = 'http://127.0.0.1:8000'

function Test-AppHealth {
    $response = $null
    $reader = $null
    try {
        $request = [Net.HttpWebRequest]::Create("$appUrl/health")
        $request.Proxy = $null
        $request.Timeout = 2000
        $request.ReadWriteTimeout = 2000
        $request.AllowAutoRedirect = $false
        $response = $request.GetResponse()
        $reader = New-Object IO.StreamReader($response.GetResponseStream())
        $health = $reader.ReadToEnd() | ConvertFrom-Json
        return ($health.status -eq 'healthy' -and $health.mode -eq 'captions-and-optional-local-speech')
    } catch { return $false }
    finally {
        if ($null -ne $reader) { $reader.Dispose() }
        if ($null -ne $response) { $response.Dispose() }
    }
}

function Get-PortOwners {
    return @(Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

function Test-AppProcess([int]$ProcessId) {
    $item = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $item -or [string]::IsNullOrWhiteSpace($item.CommandLine)) { return $false }
    $modulePattern = '(?i)(?:^|\s)-m\s+uvicorn\s+main:app(?:\s|$)'
    $directoryPattern = '(?i)--app-dir\s+(?:"' + [regex]::Escape($backendDir) + '"|' +
        [regex]::Escape($backendDir) + ')(?:\s|$)'
    return ($item.CommandLine -match $modulePattern -and $item.CommandLine -match $directoryPattern)
}

function Get-ProcessRecord([int]$ProcessId, [string]$Role) {
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    $item = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
    if ($null -eq $item -or [string]::IsNullOrWhiteSpace($process.Path) -or
        [string]::IsNullOrWhiteSpace($item.CommandLine)) {
        throw "Could not verify the new process identity (PID $ProcessId)."
    }
    return [ordered]@{
        role = $Role
        pid = $ProcessId
        startTimeUtcTicks = $process.StartTime.ToUniversalTime().Ticks.ToString()
        executablePath = [IO.Path]::GetFullPath($process.Path)
        commandLine = $item.CommandLine
    }
}

function Test-RecordedProcess($Item) {
    try {
        $current = Get-ProcessRecord -ProcessId ([int]$Item.pid) -Role $Item.role
        return ($current.startTimeUtcTicks -eq [string]$Item.startTimeUtcTicks -and
            $current.executablePath -eq [string]$Item.executablePath -and
            $current.commandLine -ceq [string]$Item.commandLine -and
            (Test-AppProcess -ProcessId ([int]$Item.pid)))
    } catch { return $false }
}

function Test-LaunchedDescendant([int]$ProcessId, [int]$LauncherId) {
    $candidateId = $ProcessId
    for ($depth = 0; $depth -lt 8; $depth++) {
        if ($candidateId -eq $LauncherId) { return $true }
        $item = Get-CimInstance Win32_Process -Filter "ProcessId = $candidateId" -ErrorAction SilentlyContinue
        if ($null -eq $item -or $item.ParentProcessId -le 0 -or $item.ParentProcessId -eq $candidateId) {
            return $false
        }
        $candidateId = [int]$item.ParentProcessId
    }
    return $false
}

function Save-LaunchRecord($Items) {
    $record = [ordered]@{
        schemaVersion = 2
        isolatedConsole = $true
        appRoot = $appRoot
        pythonPath = $pythonPath
        url = $appUrl
        createdAtUtc = [DateTime]::UtcNow.ToString('o')
        processes = @($Items)
    }
    $json = $record | ConvertTo-Json -Depth 6
    [IO.File]::WriteAllText($recordPath, $json, (New-Object Text.UTF8Encoding($false)))
}

function Show-Ready([string]$Message) {
    Write-Host "$Message $appUrl"
    if ($OpenBrowser) {
        try { Start-Process -FilePath $appUrl | Out-Null }
        catch { Write-Warning "Open $appUrl in your browser." }
    }
}

$startLock = $null
try {
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw 'Backend environment is missing. Complete Windows dependency setup first.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $appRoot 'frontend\dist\index.html') -PathType Leaf)) {
        throw 'The web interface is not built. Complete frontend setup first.'
    }
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    try {
        $startLock = [IO.File]::Open((Join-Path $runtimeDir 'windows-start.lock'),
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch { throw 'Another launcher is starting the app. Wait a moment and retry.' }

    $owners = @(Get-PortOwners)
    if ($owners.Count -gt 0) {
        foreach ($owner in $owners) {
            if (-not (Test-AppProcess -ProcessId ([int]$owner))) {
                throw "Port 8000 belongs to another service (PID $owner). Nothing was stopped."
            }
        }
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            if (Test-AppHealth) {
                Show-Ready 'Already running:'
                exit 0
            }
            Start-Sleep -Milliseconds 300
        }
        throw "This app already owns port 8000 but is not healthy. Review $stderrPath. No duplicate was started."
    }

    if (Test-Path -LiteralPath $recordPath -PathType Leaf) {
        $previous = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
        if ($previous.appRoot -eq $appRoot -and $previous.pythonPath -eq $pythonPath) {
            foreach ($item in @($previous.processes)) {
                if (Test-RecordedProcess $item) {
                    throw "A recorded app process is still starting (PID $($item.pid)). No duplicate was started."
                }
            }
        }
    }

    # Preload only documented runtime settings, even when automatic dotenv
    # loading is disabled by the host. Never log configuration values.
    $projectEnv = Join-Path $appRoot '.env'
    $childSettings = @{ PYTHONUNBUFFERED = '1' }
    if ([string]::IsNullOrWhiteSpace($env:PLAYWRIGHT_BROWSERS_PATH)) {
        $childSettings['PLAYWRIGHT_BROWSERS_PATH'] = Join-Path $runtimeDir 'browsers'
    }
    if (Test-Path -LiteralPath $projectEnv -PathType Leaf) {
        $settingsReader = @'
import json, sys
from dotenv import dotenv_values
keys = {'YT_PROXY', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'STT_BACKEND', 'STT_PYTHON', 'STT_MODEL_DIR', 'STT_DEVICE', 'STT_COMPUTE_TYPE', 'DOUYIN_BROWSER_CHANNEL', 'DOUYIN_BROWSER_PROFILE', 'PLAYWRIGHT_BROWSERS_PATH'}
print(json.dumps({key: value for key, value in dotenv_values(sys.argv[1]).items() if key in keys and value is not None}))
'@
        $projectSettingsText = & $pythonPath -B -X utf8 -c $settingsReader $projectEnv
        if ($LASTEXITCODE -ne 0) { throw 'Could not read project runtime settings.' }
        $projectSettings = ($projectSettingsText -join [Environment]::NewLine) | ConvertFrom-Json
        foreach ($property in $projectSettings.PSObject.Properties) {
            $childSettings[$property.Name] = [string]$property.Value
        }
    }
    # Only the child receives these overrides; shell proxy settings otherwise
    # remain inherited. An isolated hidden console permits graceful CTRL_BREAK.
    $savedSettings = @{}
    try {
        foreach ($key in $childSettings.Keys) {
            $savedSettings[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
            [Environment]::SetEnvironmentVariable($key, $childSettings[$key], 'Process')
        }
        $launchCode = @'
import subprocess, sys
startup = subprocess.STARTUPINFO()
startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
startup.wShowWindow = 0
with open(sys.argv[4], 'ab', buffering=0) as out, open(sys.argv[5], 'ab', buffering=0) as err:
    child = subprocess.Popen([sys.argv[1], '-m', 'uvicorn', 'main:app', '--app-dir', sys.argv[2], '--host', '127.0.0.1', '--port', '8000'], cwd=sys.argv[3], stdin=subprocess.DEVNULL, stdout=out, stderr=err, startupinfo=startup, creationflags=subprocess.CREATE_NEW_CONSOLE)
    print(child.pid)
'@
        $launchedId = & $pythonPath -B -X utf8 -c $launchCode $pythonPath $backendDir $appRoot $stdoutPath $stderrPath
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$launchedId)) {
            throw 'Could not create the isolated server process.'
        }
        $child = Get-Process -Id ([int]$launchedId) -ErrorAction Stop
    } finally {
        foreach ($key in $savedSettings.Keys) {
            [Environment]::SetEnvironmentVariable($key, $savedSettings[$key], 'Process')
        }
    }

    $launcher = Get-ProcessRecord -ProcessId $child.Id -Role 'launcher'
    Save-LaunchRecord @($launcher)
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    while ([DateTime]::UtcNow -lt $deadline) {
        $owners = @(Get-PortOwners)
        foreach ($owner in $owners) {
            if ((Test-AppProcess -ProcessId ([int]$owner)) -and
                (Test-LaunchedDescendant -ProcessId ([int]$owner) -LauncherId $child.Id) -and
                (Test-AppHealth)) {
                $server = Get-ProcessRecord -ProcessId ([int]$owner) -Role 'server'
                if ([int]$owner -eq $child.Id) { Save-LaunchRecord @($server) }
                else { Save-LaunchRecord @($server, $launcher) }
                Show-Ready 'Ready:'
                Write-Host "Logs: $stdoutPath and $stderrPath"
                exit 0
            }
        }
        $child.Refresh()
        if ($child.HasExited) { throw "The server exited. Review $stderrPath." }
        Start-Sleep -Milliseconds 300
    }
    throw "Startup did not become healthy within 45 seconds. Review $stderrPath; use the Stop launcher before retrying."
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    if ($null -ne $startLock) { $startLock.Dispose() }
}
