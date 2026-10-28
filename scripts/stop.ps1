#Requires -Version 5.1
<#
.SYNOPSIS
  Stops anything scripts\dev.ps1 left running.

.DESCRIPTION
  Three passes, in order of precision:
    1. the background PowerShell job named 'duet-api'
    2. python/node processes whose command line mentions this repo, uvicorn
       duet.main:app, or the Next.js dev server
    3. whatever still holds the API (8787) or web (3000) port

  Matching is scoped to this repository path wherever a command line exposes it,
  so an unrelated Next.js or uvicorn project on the same machine is left alone.
  Pass -AnyPath to drop that restriction, or -WhatIf to see the plan only.

  Written for Windows PowerShell 5.1: no '&&', no ternary, no null-coalescing.

.EXAMPLE
  .\scripts\stop.ps1

.EXAMPLE
  .\scripts\stop.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [int]$ApiPort = 8787,
    [int]$WebPort = 3000,
    [switch]$AnyPath
)

$ErrorActionPreference = 'Continue'

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
}
$Root = Split-Path -Parent $ScriptDir

function Write-Step { param([string]$m) Write-Host ''; Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok   { param([string]$m) Write-Host "    $m" -ForegroundColor Green }
function Write-Note { param([string]$m) Write-Host "    $m" -ForegroundColor Yellow }

$killed = 0

function Stop-DuetProcess {
    param(
        [int]$ProcessId,
        [string]$Reason
    )
    if ($ProcessId -le 4) {
        return
    }
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $proc) {
        return
    }
    if ($ProcessId -eq $PID) {
        return
    }
    $label = "$($proc.ProcessName) (pid $ProcessId)"
    if ($PSCmdlet.ShouldProcess($label, "Stop-Process - $Reason")) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        if ($?) {
            Write-Ok "stopped $label - $Reason"
            $script:killed = $script:killed + 1
        }
        else {
            Write-Note "could not stop $label (try an elevated prompt)"
        }
    }
}

Write-Host 'Claude Duet - stop stray dev processes' -ForegroundColor Magenta
Write-Host "repo: $Root"

# --- 1. the background job ---------------------------------------------------
Write-Step "Background job 'duet-api'"
$jobs = Get-Job -Name 'duet-api' -ErrorAction SilentlyContinue
if ($jobs) {
    foreach ($job in $jobs) {
        if ($PSCmdlet.ShouldProcess("job $($job.Id)", 'Stop-Job / Remove-Job')) {
            Stop-Job   -Id $job.Id -ErrorAction SilentlyContinue
            Remove-Job -Id $job.Id -Force -ErrorAction SilentlyContinue
            Write-Ok "removed job $($job.Id)"
        }
    }
}
else {
    Write-Ok 'none in this session'
}

# --- 2. python / node processes that look like ours --------------------------
Write-Step 'Dev server processes (uvicorn / next dev)'

$rootPattern = [regex]::Escape($Root)
$procs = @()
try {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='node.exe'" -ErrorAction Stop
}
catch {
    Write-Note "could not query Win32_Process: $($_.Exception.Message)"
}

$matched = 0
foreach ($p in $procs) {
    $cmd = $p.CommandLine
    if (-not $cmd) {
        continue
    }

    $looksLikeDuet = $false
    if ($cmd -match 'uvicorn'   -and $cmd -match 'duet\.main') { $looksLikeDuet = $true }
    if ($cmd -match 'duet\.main:app')                          { $looksLikeDuet = $true }
    if ($cmd -match 'next'      -and $cmd -match 'dev')        { $looksLikeDuet = $true }
    if ($cmd -match 'next-server')                             { $looksLikeDuet = $true }
    if (-not $looksLikeDuet) {
        continue
    }

    # Keep the blast radius inside this repo unless the caller opts out.
    if (-not $AnyPath) {
        if ($cmd -notmatch $rootPattern) {
            Write-Note "skipping pid $($p.ProcessId) - outside $Root (use -AnyPath to include it)"
            continue
        }
    }

    $matched = $matched + 1
    Stop-DuetProcess -ProcessId $p.ProcessId -Reason 'dev server'
}
if ($matched -eq 0) {
    Write-Ok 'none found'
}

# --- 3. whatever still holds the ports ---------------------------------------
Write-Step "Listeners on ports $ApiPort and $WebPort"
$portsCleared = 0
foreach ($port in @($ApiPort, $WebPort)) {
    $pids = @()
    $cmdlet = Get-Command 'Get-NetTCPConnection' -ErrorAction SilentlyContinue
    if ($cmdlet) {
        $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            $pids += $c.OwningProcess
        }
    }
    else {
        # Fallback for hosts without the NetTCPIP module.
        $lines = netstat -ano | Select-String -Pattern ":$port\s" -ErrorAction SilentlyContinue
        foreach ($line in $lines) {
            $parts = ($line.ToString().Trim() -split '\s+')
            if ($parts.Length -ge 5 -and $parts[3] -eq 'LISTENING') {
                $pids += [int]$parts[4]
            }
        }
    }

    $pids = $pids | Sort-Object -Unique
    if (-not $pids) {
        Write-Ok "port $port is free"
        continue
    }
    foreach ($procId in $pids) {
        $portsCleared = $portsCleared + 1
        Stop-DuetProcess -ProcessId ([int]$procId) -Reason "listening on $port"
    }
}

Write-Host ''
if ($killed -gt 0) {
    Write-Host "Done - stopped $killed process(es)." -ForegroundColor Green
}
else {
    Write-Host 'Done - nothing left to stop.' -ForegroundColor Green
}
if ($portsCleared -eq 0 -and $killed -eq 0) {
    Write-Host 'Both ports were already free.' -ForegroundColor DarkGray
}
