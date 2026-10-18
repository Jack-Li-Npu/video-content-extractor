[CmdletBinding()]
param(
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
    if ($LASTEXITCODE -ne 0) { throw "Setup command failed ($LASTEXITCODE): $Program" }
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

try {
    if ($env:OS -ne 'Windows_NT') { throw 'Use setup.sh on macOS or Linux.' }
    Set-Location -LiteralPath $appRoot
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    $uv = Join-Path $runtimeDir 'installer\Scripts\uv.exe'
    if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) {
        $uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
        if ($null -eq $uvCommand) {
            throw 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ and retry. No global Python path is required.'
        }
        $uv = $uvCommand.Source
    }
    $node = Get-Command node -CommandType Application -ErrorAction SilentlyContinue
    $npm = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $node -or $null -eq $npm) { throw 'Install Node.js 22.12 or newer (including npm), then retry.' }
    $nodeVersionText = [string](& $node.Source --version)
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the Node.js version.' }
    $nodeVersion = [version]($nodeVersionText.Trim().TrimStart('v').Split('-')[0])
    if ($nodeVersion -lt [version]'22.12.0') { throw "Node.js 22.12 or newer is required; found $nodeVersionText." }

    $envPath = Join-Path $appRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        Copy-Item -LiteralPath (Join-Path $appRoot '.env.example') -Destination $envPath
    }
    if (-not [string]::IsNullOrWhiteSpace($Proxy)) {
        Set-SetupEnvironment 'HTTP_PROXY' $Proxy
        Set-SetupEnvironment 'HTTPS_PROXY' $Proxy
        Set-SetupEnvironment 'ALL_PROXY' $Proxy
        Set-SetupEnvironment 'YT_PROXY' $Proxy
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
    Set-SetupEnvironment 'UV_CACHE_DIR' (Join-Path $runtimeDir 'uv-cache')
    Set-SetupEnvironment 'npm_config_cache' (Join-Path $runtimeDir 'npm-cache')
    Set-SetupEnvironment 'PLAYWRIGHT_BROWSERS_PATH' (Join-Path $runtimeDir 'browsers')
    Invoke-Checked $uv @('sync', '--project', 'backend', '--frozen', '--python', $Python)
    $backendPython = Join-Path $appRoot 'backend\.venv\Scripts\python.exe'
    Invoke-Checked $backendPython @('-m', 'playwright', 'install', 'chromium')
    Set-Location -LiteralPath (Join-Path $appRoot 'frontend')
    Invoke-Checked $npm.Source @('ci', '--no-audit', '--no-fund')
    Invoke-Checked $npm.Source @('run', 'build')
    Write-Host 'Ready. Run Start-Video-Content-Extractor.cmd. Optional Windows speech: setup-douyin.cmd.'
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    Set-Location -LiteralPath $oldLocation.Path
    foreach ($key in $savedSettings.Keys) {
        [Environment]::SetEnvironmentVariable($key, $savedSettings[$key], 'Process')
    }
}
