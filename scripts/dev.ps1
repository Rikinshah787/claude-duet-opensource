#Requires -Version 5.1
<#
.SYNOPSIS
  One-command development launcher for Claude Duet on Windows.

.DESCRIPTION
  Creates the Python virtualenv if missing, installs the API dependencies,
  starts uvicorn as a background job, waits for /api/health to answer, installs
  the web dependencies only when node_modules is absent, then runs the Next.js
  dev server in the foreground.

  Ctrl+C stops the web server and the background API job. If a stray process
  survives, run scripts\stop.ps1.

  Written for Windows PowerShell 5.1: no '&&', no ternary, no null-coalescing.

.EXAMPLE
  .\scripts\dev.ps1

.EXAMPLE
  .\scripts\dev.ps1 -ApiPort 8787 -HealthTimeoutSeconds 60 -SkipWeb
#>
[CmdletBinding()]
param(
    [int]$ApiPort = 8787,
    [int]$WebPort = 3000,
    [int]$HealthTimeoutSeconds = 40,
    [switch]$SkipWeb,
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'

# --- paths -------------------------------------------------------------------
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
}
$Root    = Split-Path -Parent $ScriptDir
$ApiDir  = Join-Path $Root 'apps\api'
$WebDir  = Join-Path $Root 'apps\web'
$VenvDir = Join-Path $Root '.venv'
$Py      = Join-Path $VenvDir 'Scripts\python.exe'
$Reqs    = Join-Path $ApiDir 'requirements.txt'

$JobName   = 'duet-api'
$HealthUrl = "http://127.0.0.1:$ApiPort/api/health"
$ApiJob    = $null

function Write-Step  { param([string]$m) Write-Host ''; Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok    { param([string]$m) Write-Host "    $m" -ForegroundColor Green }
function Write-Note  { param([string]$m) Write-Host "    $m" -ForegroundColor Yellow }
function Write-Bad   { param([string]$m) Write-Host "    $m" -ForegroundColor Red }

function Resolve-BasePython {
    # Prefer the py launcher pinned to 3.12, then whatever 'python' resolves to.
    $launcher = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3.12 -c 'import sys' 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @{ Exe = $launcher.Source; Args = @('-3.12') }
        }
    }
    $python = Get-Command 'python' -ErrorAction SilentlyContinue
    if ($python) {
        return @{ Exe = $python.Source; Args = @() }
    }
    return $null
}

try {
    Write-Host 'Claude Duet - dev launcher' -ForegroundColor Magenta
    Write-Host "repo: $Root"

    if (-not (Test-Path -LiteralPath $ApiDir)) {
        throw "API directory not found: $ApiDir"
    }

    # --- 1. virtualenv -------------------------------------------------------
    Write-Step 'Python environment'
    if (Test-Path -LiteralPath $Py) {
        Write-Ok "reusing $VenvDir"
    }
    else {
        $base = Resolve-BasePython
        if (-not $base) {
            throw 'No Python interpreter found on PATH. Install Python 3.12 and re-run.'
        }
        Write-Note "creating virtualenv at $VenvDir"
        $venvArgs = @()
        $venvArgs += $base.Args
        $venvArgs += @('-m', 'venv', $VenvDir)
        & $base.Exe @venvArgs
        if ($LASTEXITCODE -ne 0) {
            throw "python -m venv failed (exit $LASTEXITCODE)"
        }
        if (-not (Test-Path -LiteralPath $Py)) {
            throw "virtualenv created but $Py is missing"
        }
        Write-Ok 'virtualenv ready'
    }

    # --- 2. API dependencies -------------------------------------------------
    if ($SkipInstall) {
        Write-Step 'API dependencies (skipped: -SkipInstall)'
    }
    else {
        Write-Step 'API dependencies'
        if (-not (Test-Path -LiteralPath $Reqs)) {
            throw "requirements.txt not found: $Reqs"
        }
        & $Py -m pip install --upgrade pip --quiet --disable-pip-version-check
        if ($?) {
            Write-Ok 'pip up to date'
        }
        & $Py -m pip install -r $Reqs --quiet --disable-pip-version-check
        if ($LASTEXITCODE -ne 0) {
            throw "pip install -r $Reqs failed (exit $LASTEXITCODE)"
        }
        Write-Ok 'dependencies installed'
    }

    # --- 3. start the API as a background job --------------------------------
    Write-Step "Starting API (uvicorn duet.main:app on :$ApiPort)"

    $stale = Get-Job -Name $JobName -ErrorAction SilentlyContinue
    if ($stale) {
        Write-Note "removing a stale '$JobName' job"
        $stale | Stop-Job -ErrorAction SilentlyContinue
        $stale | Remove-Job -Force -ErrorAction SilentlyContinue
    }

    $ApiJob = Start-Job -Name $JobName -ScriptBlock {
        param($WorkDir, $PythonExe, $Port)
        Set-Location -LiteralPath $WorkDir
        $env:DUET_HOST = '127.0.0.1'
        $env:DUET_PORT = "$Port"
        $env:PYTHONUNBUFFERED = '1'
        & $PythonExe -m uvicorn duet.main:app --host 127.0.0.1 --port $Port --reload
    } -ArgumentList $ApiDir, $Py, $ApiPort

    Write-Ok "job id $($ApiJob.Id) started from $ApiDir"

    # --- 4. wait for /api/health --------------------------------------------
    Write-Step "Waiting for $HealthUrl (timeout $HealthTimeoutSeconds s)"
    $deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
    $healthy  = $false
    $attempts = 0

    while ((Get-Date) -lt $deadline) {
        $attempts = $attempts + 1
        $state = (Get-Job -Id $ApiJob.Id).State
        if ($state -eq 'Failed' -or $state -eq 'Completed' -or $state -eq 'Stopped') {
            Write-Bad "the API job ended early (state: $state)"
            break
        }
        try {
            $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -eq 200) {
                $healthy = $true
                break
            }
        }
        catch {
            # Not up yet - a refused connection is expected during startup.
        }
        Start-Sleep -Seconds 1
    }

    if (-not $healthy) {
        $finalState = (Get-Job -Id $ApiJob.Id).State
        Write-Host ''
        Write-Bad '--------------------------------------------------------------'
        Write-Bad ' API DID NOT BECOME HEALTHY'
        Write-Bad '--------------------------------------------------------------'
        Write-Bad " url      : $HealthUrl"
        Write-Bad " waited   : $HealthTimeoutSeconds s over $attempts attempts"
        Write-Bad " job state: $finalState"
        Write-Bad ''
        Write-Bad ' Output from the uvicorn job:'
        $log = Receive-Job -Id $ApiJob.Id -Keep -ErrorAction SilentlyContinue
        if ($log) {
            foreach ($line in $log) {
                Write-Host "   | $line" -ForegroundColor DarkGray
            }
        }
        else {
            Write-Host '   | (no output captured)' -ForegroundColor DarkGray
        }
        Write-Bad ''
        Write-Bad ' Common causes:'
        Write-Bad '   * duet.main:app does not exist yet, or importing it fails'
        Write-Bad '   * /api/health is not registered on the app'
        Write-Bad "   * port $ApiPort is already in use (run scripts\stop.ps1)"
        Write-Bad '   * a dependency is missing (re-run without -SkipInstall)'
        Write-Bad '--------------------------------------------------------------'
        throw 'API health check failed.'
    }

    Write-Ok 'API is healthy'

    # --- 5. web dependencies -------------------------------------------------
    if ($SkipWeb) {
        Write-Step "Web app skipped (-SkipWeb). API is running in job '$JobName'."
        Write-Host ''
        Write-Host "  API    http://127.0.0.1:$ApiPort" -ForegroundColor Green
        Write-Host "  Health $HealthUrl" -ForegroundColor Green
        Write-Host ''
        Write-Host 'Press Ctrl+C to stop the API.' -ForegroundColor DarkGray
        while ((Get-Job -Id $ApiJob.Id).State -eq 'Running') {
            Start-Sleep -Seconds 1
        }
    }
    else {
        if (-not (Test-Path -LiteralPath $WebDir)) {
            throw "Web directory not found: $WebDir"
        }
        $npm = Get-Command 'npm' -ErrorAction SilentlyContinue
        if (-not $npm) {
            throw 'npm not found on PATH. Install Node.js 22+ (or re-run with -SkipWeb).'
        }

        Write-Step 'Web dependencies'
        $nodeModules = Join-Path $WebDir 'node_modules'
        if (Test-Path -LiteralPath $nodeModules) {
            Write-Ok 'node_modules present - skipping npm install'
        }
        else {
            Write-Note 'node_modules missing - running npm install (this takes a while)'
            Push-Location -LiteralPath $WebDir
            try {
                & $npm.Source install
                if ($LASTEXITCODE -ne 0) {
                    throw "npm install failed (exit $LASTEXITCODE)"
                }
            }
            finally {
                Pop-Location
            }
            Write-Ok 'web dependencies installed'
        }

        # --- 6. run the web dev server in the foreground ---------------------
        Write-Host ''
        Write-Host '--------------------------------------------------------------' -ForegroundColor Green
        Write-Host '  Claude Duet is starting' -ForegroundColor Green
        Write-Host '--------------------------------------------------------------' -ForegroundColor Green
        Write-Host "  Web    http://localhost:$WebPort" -ForegroundColor Green
        Write-Host "  API    http://127.0.0.1:$ApiPort" -ForegroundColor Green
        Write-Host "  Docs   http://127.0.0.1:$ApiPort/docs" -ForegroundColor Green
        Write-Host "  Health $HealthUrl" -ForegroundColor Green
        Write-Host '--------------------------------------------------------------' -ForegroundColor Green
        Write-Host "  API logs:  Receive-Job -Name $JobName -Keep" -ForegroundColor DarkGray
        Write-Host '  Ctrl+C stops both. Leftovers: scripts\stop.ps1' -ForegroundColor DarkGray
        Write-Host ''

        Push-Location -LiteralPath $WebDir
        try {
            & $npm.Source run dev
        }
        finally {
            Pop-Location
        }
    }
}
catch {
    Write-Host ''
    Write-Bad "dev.ps1 failed: $($_.Exception.Message)"
    $script:DuetExitCode = 1
}
finally {
    # Runs on normal exit, on throw, and (usually) on Ctrl+C.
    if ($ApiJob) {
        Write-Host ''
        Write-Host "==> Stopping API job '$JobName'" -ForegroundColor Cyan
        Stop-Job   -Id $ApiJob.Id -ErrorAction SilentlyContinue
        Remove-Job -Id $ApiJob.Id -Force -ErrorAction SilentlyContinue
        Write-Host '    stopped. If a python/node process lingers, run scripts\stop.ps1' -ForegroundColor DarkGray
    }
}

if ($script:DuetExitCode) {
    exit $script:DuetExitCode
}
