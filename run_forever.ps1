# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

# StudyBot watchdog — restart the process if Python exits (crash, kill, etc.).
# Usage (from project folder): powershell -ExecutionPolicy Bypass -File .\run_forever.ps1
# Prefer running under NSSM as a Windows Service instead of leaving this window open — see install_nssm_service.ps1

$ErrorActionPreference = "Continue"
if (-not $PSScriptRoot) {
    $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$Root = $PSScriptRoot

$Py = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    $Py = Join-Path $Root ".venv\Scripts\python.exe"
}
if (-not (Test-Path $Py)) {
    $Py = "python"
}

$Bot = Join-Path $Root "bot.py"
if (-not (Test-Path $Bot)) {
    Write-Error "bot.py not found at $Bot"
    exit 1
}

$WatchLog = Join-Path $Root "watchdog.log"
$RunLog = Join-Path $Root "run.log"

$BackoffSec = [int]$env:WATCHDOG_BACKOFF_SEC
if (-not $BackoffSec -or $BackoffSec -lt 1) { $BackoffSec = 5 }
$BackoffMax = [int]$env:WATCHDOG_BACKOFF_MAX_SEC
if (-not $BackoffMax -or $BackoffMax -lt $BackoffSec) { $BackoffMax = 300 }

Write-Host "StudyBot watchdog — Python: $Py — Root: $Root"

while ($true) {
    $ts = Get-Date -Format "o"
    Add-Content -Path $WatchLog -Value "[$ts] starting: $Py $Bot"

    # Append stdout+stderr for this run (CMD redirection is simplest on Windows PowerShell)
    cmd /c "`"$Py`" `"$Bot`" >> `"$RunLog`" 2>&1"
    $code = $LASTEXITCODE

    $ts2 = Get-Date -Format "o"
    Add-Content -Path $WatchLog -Value "[$ts2] exited code=$code — sleeping ${BackoffSec}s"

    Start-Sleep -Seconds $BackoffSec
    $BackoffSec = [Math]::Min([int]([double]$BackoffSec * 2), $BackoffMax)
}
