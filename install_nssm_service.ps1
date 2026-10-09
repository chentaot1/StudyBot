# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

# Install StudyBot as a Windows Service using NSSM (run PowerShell as Administrator).
#
# Prerequisites:
#   1. NSSM on PATH or nssm.exe next to this script. Easiest on Windows:
#        winget install -e --id NSSM.NSSM
#      (Then: where.exe nssm)
#      Fallback mirrors if winget unavailable: Chocolatey `choco install nssm`, or grab nssm.exe
#      from a trusted GitHub release mirror (search "NSSM.NSSM winget" / "nssm releases").
#
# Usage (elevated prompt, from project folder):
#   powershell -ExecutionPolicy Bypass -File .\install_nssm_service.ps1
#
# After install:
#   nssm start StudyBot
#   nssm status StudyBot

$ErrorActionPreference = "Stop"
if (-not $PSScriptRoot) {
    $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}

function Find-Nssm {
    $candidates = @(
        (Join-Path $PSScriptRoot "nssm.exe"),
        "nssm"
    )
    foreach ($c in $candidates) {
        if ($c -eq "nssm") {
            $cmd = Get-Command nssm -ErrorAction SilentlyContinue
            if ($cmd) { return $cmd.Source }
        } elseif (Test-Path $c) { return $c }
    }
    return $null
}

$nssm = Find-Nssm
if (-not $nssm) {
    Write-Error "nssm.exe not found. Put nssm.exe beside this script or add NSSM to PATH. https://nssm.cc/download"
    exit 2
}

$Root = $PSScriptRoot

$Py = $null
foreach ($studyPythonCandidate in @((Join-Path $Root ".venv\Scripts\python.exe"), (Join-Path $Root "venv\Scripts\python.exe"))) {
    if (Test-Path -LiteralPath $studyPythonCandidate) {
        & $studyPythonCandidate -c "import discord, apscheduler, dotenv; print('Python environment OK: discord.py ' + discord.__version__)"
        if ($LASTEXITCODE -eq 0) { $Py = $studyPythonCandidate; break }
    }
}
if (-not $Py) {
    Write-Error "No working Python environment. Run .\setup.ps1 first."
    exit 3
}

$Bot = Join-Path $Root "bot.py"
$Stdout = Join-Path $Root "service_stdout.log"
$Stderr = Join-Path $Root "service_stderr.log"

$SERVICE_NAME = "StudyBot"

Write-Host "NSSM: $nssm"
Write-Host "Install service '$SERVICE_NAME'"
Write-Host " AppDirectory : $Root"
Write-Host " Application  : $Py"
Write-Host " Parameters   : bot.py"

& $nssm install $SERVICE_NAME $Py
if ($LASTEXITCODE -ne 0) {
    Write-Warning "install returned $LASTEXITCODE — if service exists, try: nssm remove $SERVICE_NAME confirm"
    exit $LASTEXITCODE
}

& $nssm set $SERVICE_NAME AppDirectory $Root
& $nssm set $SERVICE_NAME AppParameters "bot.py"
& $nssm set $SERVICE_NAME DisplayName "StudyBot Discord Bot"
& $nssm set $SERVICE_NAME Description "StudyBot (discord.py) — loads .env from AppDirectory"
& $nssm set $SERVICE_NAME Start SERVICE_AUTO_START
& $nssm set $SERVICE_NAME AppStdout $Stdout
& $nssm set $SERVICE_NAME AppStderr $Stderr
& $nssm set $SERVICE_NAME AppRotateFiles 1
& $nssm set $SERVICE_NAME AppStdoutCreationDisposition 4
& $nssm set $SERVICE_NAME AppStderrCreationDisposition 4
& $nssm set $SERVICE_NAME AppExit Default Restart
& $nssm set $SERVICE_NAME AppThrottle 5000

Write-Host ""
Write-Host "Done. Start with: nssm start $SERVICE_NAME"
Write-Host "Logs: $Stdout , $Stderr"
