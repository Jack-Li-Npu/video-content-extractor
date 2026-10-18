[CmdletBinding()]
param(
    [ValidateSet('auto', 'cuda', 'cpu')][string]$Device = 'auto',
    [string]$Python = '3.12',
    [string]$Proxy = ''
)

$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$runtimeDir = Join-Path $appRoot '.runtime'
$savedSettings = @{}
$oldLocation = Get-Location

function Set-SetupEnvironment([string]$Name, [string]$Value) {
    if (-not $savedSettings.ContainsKey($Name)) {
        $savedSettings[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
    }
    [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
}

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Speech setup command failed ($LASTEXITCODE): $Program" }
}

function Read-ProjectProxy([string]$Path) {
    # Parse only this single optional key. Never execute or expand .env content.
    $value = ''
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ($line -match '^\s*(?:export\s+)?YT_PROXY\s*=\s*(.*?)\s*$') {
            $candidate = $Matches[1]
            if ($candidate.StartsWith('"')) {
                if ($candidate -notmatch '^"([^"\r\n]*)"\s*(?:#.*)?$') { throw 'Use a single-line quoted URL for YT_PROXY in .env.' }
                $value = $Matches[1]
            } elseif ($candidate.StartsWith("'")) {
                if ($candidate -notmatch "^'([^'\r\n]*)'\s*(?:#.*)?$") { throw 'Use a single-line quoted URL for YT_PROXY in .env.' }
                $value = $Matches[1]
            } else {
                $value = ($candidate -replace '\s+#.*$', '').Trim()
            }
        }
    }
    return $value
}

function Test-NvidiaGpu {
    $command = Get-Command nvidia-smi -CommandType Application -ErrorAction SilentlyContinue
    $program = if ($null -ne $command) { $command.Source } else { $null }
    if (-not $program -and $env:SystemRoot) {
        $candidate = Join-Path $env:SystemRoot 'System32\nvidia-smi.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $program = $candidate }
    }
    if (-not $program) { return $false }
    try {
        $gpuRows = @(& $program '--query-gpu=index' '--format=csv,noheader' 2>$null)
        return $LASTEXITCODE -eq 0 -and @($gpuRows | Where-Object { [string]$_ -match '^\s*\d+\s*$' }).Count -gt 0
    } catch { return $false }
}

try {
    if ($env:OS -ne 'Windows_NT') { throw 'Use setup-douyin.sh on an Apple Silicon Mac.' }
    Set-Location -LiteralPath $appRoot
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    $uv = Join-Path $runtimeDir 'installer\Scripts\uv.exe'
    if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) {
        $uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
        if ($null -eq $uvCommand) { throw 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ and retry.' }
        $uv = $uvCommand.Source
    }
    $envPath = Join-Path $appRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        Copy-Item -LiteralPath (Join-Path $appRoot '.env.example') -Destination $envPath
    }
    if (-not [string]::IsNullOrWhiteSpace($Proxy)) {
        foreach ($key in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'YT_PROXY')) {
            Set-SetupEnvironment $key $Proxy
        }
    } else {
        $projectProxy = [Environment]::GetEnvironmentVariable('YT_PROXY', 'Process')
        if ([string]::IsNullOrWhiteSpace($projectProxy)) { $projectProxy = Read-ProjectProxy $envPath }
        if (-not [string]::IsNullOrWhiteSpace($projectProxy)) {
            foreach ($key in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'YT_PROXY')) {
                if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($key, 'Process'))) {
                    Set-SetupEnvironment $key $projectProxy
                }
            }
        }
    }
    $hasNvidia = if ($Device -ne 'cpu') { Test-NvidiaGpu } else { $false }
    if ($Device -eq 'cuda' -and -not $hasNvidia) {
        throw 'CUDA setup requires an NVIDIA GPU with a working NVIDIA driver (nvidia-smi). Install/update the driver, or rerun with -Device cpu.'
    }
    $installCuda = $Device -eq 'cuda' -or ($Device -eq 'auto' -and $hasNvidia)
    Set-SetupEnvironment 'UV_CACHE_DIR' (Join-Path $runtimeDir 'uv-cache')
    Invoke-Checked $uv @('sync', '--project', 'backend', '--frozen', '--python', $Python)
    Write-Host 'Installing optional local speech recognition and a verified model (about 1.62 GB).'
    $syncArguments = @('sync', '--project', 'stt/windows', '--frozen', '--python', $Python)
    if ($installCuda) {
        Write-Host 'CUDA option: official CUDA 12 cuBLAS/cuDNN 9 runtime wheels need about 1.3 GB plus dependencies.'
        Write-Host 'A compatible NVIDIA driver and Microsoft Visual C++ 2015-2022 x64 runtime are required for CUDA.'
        $syncArguments += @('--extra', 'cuda')
    } elseif ($Device -eq 'auto') {
        Write-Host 'No responding NVIDIA GPU was found. Installing CPU speech dependencies; the saved device preference remains auto.'
    }
    Invoke-Checked $uv $syncArguments
    $speechPython = Join-Path $appRoot 'stt\windows\.venv\Scripts\python.exe'
    Invoke-Checked $speechPython @('-B', '-X', 'utf8', (Join-Path $appRoot 'backend\workers\local_subtitles.py'),
        '--check-runtime', '--backend', 'faster-whisper', '--device', $Device, '--compute-type', 'auto')
    Invoke-Checked $speechPython @('-B', '-X', 'utf8', (Join-Path $appRoot 'stt\download_windows_model.py'))
    # Only add absent keys. A user's explicit backend/device/model paths remain intact.
    $defaults = @{ STT_BACKEND = 'faster-whisper'; STT_DEVICE = $Device; STT_COMPUTE_TYPE = 'auto' }
    $existingEnv = [IO.File]::ReadAllText($envPath)
    foreach ($key in @('STT_BACKEND', 'STT_DEVICE', 'STT_COMPUTE_TYPE')) {
        if ($existingEnv -notmatch ('(?m)^\s*(?:export\s+)?' + [regex]::Escape($key) + '\s*=')) {
            [IO.File]::AppendAllText($envPath, ([Environment]::NewLine + $key + '=' + $defaults[$key] + [Environment]::NewLine),
                (New-Object Text.UTF8Encoding($false)))
        }
    }
    Write-Host 'Windows speech setup complete. Existing .env values were preserved. Restart the extractor to apply settings.'
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    Set-Location -LiteralPath $oldLocation.Path
    foreach ($key in $savedSettings.Keys) {
        [Environment]::SetEnvironmentVariable($key, $savedSettings[$key], 'Process')
    }
}
