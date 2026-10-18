[CmdletBinding()]
param([ValidateRange(1, 120)][int]$GracefulTimeoutSeconds = 15)

$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$pythonPath = Join-Path $appRoot 'backend\.venv\Scripts\python.exe'
$backendDir = Join-Path $appRoot 'backend'
$recordPath = Join-Path $appRoot '.runtime\windows-server.json'
$stopLock = $null

function Request-GracefulStop([int]$ProcessId) {
    if ($record.schemaVersion -ne 2 -or $record.isolatedConsole -ne $true -or
        -not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { return $false }
    # The hidden server console is isolated at launch. Permit only that checked
    # process and its current descendants in the console before signalling it.
    $allowedIds = New-Object 'Collections.Generic.HashSet[int]'
    [void]$allowedIds.Add($ProcessId)
    # A Windows venv python.exe shim may remain as the server's parent in the
    # same console. Include only launcher/server records whose identity still
    # matches, rather than admitting an unchecked parent or the whole console.
    foreach ($recorded in @($record.processes)) {
        $candidateId = [int]$recorded.pid
        $candidateProcess = Get-Process -Id $candidateId -ErrorAction SilentlyContinue
        if ($null -eq $candidateProcess) { continue }
        $candidateDetails = Get-CimInstance Win32_Process -Filter "ProcessId = $candidateId" -ErrorAction Stop
        if ($null -eq $candidateDetails -or [string]::IsNullOrWhiteSpace($candidateProcess.Path) -or
            $candidateProcess.StartTime.ToUniversalTime().Ticks.ToString() -ne [string]$recorded.startTimeUtcTicks -or
            [IO.Path]::GetFullPath($candidateProcess.Path) -ne [string]$recorded.executablePath -or
            $candidateDetails.CommandLine -cne [string]$recorded.commandLine) { return $false }
        [void]$allowedIds.Add($candidateId)
    }
    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    for ($depth = 0; $depth -lt 8; $depth++) {
        $added = $false
        foreach ($candidate in $snapshot) {
            if ($allowedIds.Contains([int]$candidate.ParentProcessId)) {
                if ($allowedIds.Add([int]$candidate.ProcessId)) { $added = $true }
            }
        }
        if (-not $added) { break }
    }
    $signalCode = @'
import ctypes, os, sys, time
from ctypes import wintypes
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.AttachConsole.argtypes = [wintypes.DWORD]
kernel.AttachConsole.restype = wintypes.BOOL
kernel.FreeConsole.argtypes = []
kernel.FreeConsole.restype = wintypes.BOOL
kernel.GetConsoleProcessList.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
kernel.GetConsoleProcessList.restype = wintypes.DWORD
kernel.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
kernel.SetConsoleCtrlHandler.argtypes = [handler_type, wintypes.BOOL]
kernel.SetConsoleCtrlHandler.restype = wintypes.BOOL
allowed = {int(value) for value in sys.argv[2].split(',')}
allowed.add(os.getpid())
kernel.FreeConsole()
if not kernel.AttachConsole(int(sys.argv[1])):
    sys.exit(2)
try:
    processes = (wintypes.DWORD * 1024)()
    count = kernel.GetConsoleProcessList(processes, len(processes))
    if not count or count > len(processes) or any(pid not in allowed for pid in processes[:count]):
        sys.exit(3)
    handler = handler_type(lambda event: True)
    if not kernel.SetConsoleCtrlHandler(handler, True):
        sys.exit(4)
    if not kernel.GenerateConsoleCtrlEvent(1, 0):
        sys.exit(5)
    time.sleep(0.25)
finally:
    kernel.FreeConsole()
'@
    $allowedText = (@($allowedIds) -join ',')
    & $pythonPath -B -X utf8 -c $signalCode $ProcessId $allowedText
    return ($LASTEXITCODE -eq 0)
}

try {
    $runtimeDir = Join-Path $appRoot '.runtime'
    if (Test-Path -LiteralPath $runtimeDir -PathType Container) {
        try {
            $stopLock = [IO.File]::Open((Join-Path $runtimeDir 'windows-start.lock'),
                [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        } catch { throw 'Another launcher is starting or stopping the app. Wait a moment and retry.' }
    }
    if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) {
        Write-Host 'There is no launch record. No process was stopped.'
        exit 0
    }
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    if ($record.schemaVersion -notin @(1, 2) -or $record.appRoot -ne $appRoot -or
        $record.pythonPath -ne $pythonPath -or @($record.processes).Count -eq 0) {
        throw 'The launch record does not identify this installation. No process was stopped.'
    }
    $modulePattern = '(?i)(?:^|\s)-m\s+uvicorn\s+main:app(?:\s|$)'
    $directoryPattern = '(?i)--app-dir\s+(?:"' + [regex]::Escape($backendDir) + '"|' +
        [regex]::Escape($backendDir) + ')(?:\s|$)'
    # Validate the entire record before acting on any PID.
    foreach ($item in @($record.processes)) {
        if ([int]$item.pid -le 0 -or [string]::IsNullOrWhiteSpace($item.startTimeUtcTicks) -or
            [string]::IsNullOrWhiteSpace($item.executablePath) -or
            $item.commandLine -notmatch $modulePattern -or $item.commandLine -notmatch $directoryPattern) {
            throw 'The launch record is incomplete. No process was stopped.'
        }
    }

    $mismatch = $false
    $stopped = 0
    foreach ($item in @($record.processes | Sort-Object @{Expression = { if ($_.role -eq 'server') { 0 } else { 1 } }})) {
        $processId = [int]$item.pid
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -eq $process) { continue }
        $details = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
        $matches = ($null -ne $details -and -not [string]::IsNullOrWhiteSpace($process.Path) -and
            $process.StartTime.ToUniversalTime().Ticks.ToString() -eq [string]$item.startTimeUtcTicks -and
            [IO.Path]::GetFullPath($process.Path) -eq [string]$item.executablePath -and
            $details.CommandLine -ceq [string]$item.commandLine)
        if (-not $matches) {
            Write-Warning "PID $processId does not match its recorded start time/path/command. It was left running."
            $mismatch = $true
            continue
        }
        if ($item.role -eq 'server') {
            try {
                if (Request-GracefulStop -ProcessId $processId) {
                    [void]$process.WaitForExit($GracefulTimeoutSeconds * 1000)
                }
            } catch { Write-Warning 'A graceful console signal was unavailable; checking the recorded process before fallback.' }
        }
        $process.Refresh()
        if (-not $process.HasExited) {
            # Revalidate immediately before a bounded, exact-process fallback.
            $details = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
            if ($null -eq $details -or $details.CommandLine -cne [string]$item.commandLine -or
                $process.StartTime.ToUniversalTime().Ticks.ToString() -ne [string]$item.startTimeUtcTicks -or
                [IO.Path]::GetFullPath($process.Path) -ne [string]$item.executablePath) {
                throw "PID $processId changed before fallback. The launch record was kept."
            }
            Write-Warning "Graceful shutdown was unavailable or timed out for PID $processId; stopping only its verified process."
            Stop-Process -InputObject $process -Force -ErrorAction SilentlyContinue
        }
        $process.WaitForExit(10000) | Out-Null
        if (-not $process.HasExited) { throw "PID $processId did not exit. The launch record was kept." }
        $stopped++
    }
    if ($mismatch) { throw 'At least one PID no longer matches. The record was kept; unrelated processes were not stopped.' }
    Remove-Item -LiteralPath $recordPath -Force
    if ($stopped -gt 0) { Write-Host 'Video Content Extractor stopped.' }
    else { Write-Host 'The recorded app processes had already exited. The stale record was removed.' }
    exit 0
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    if ($null -ne $stopLock) { $stopLock.Dispose() }
}
