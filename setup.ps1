# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

# Rebuild a reproducible local environment without replacing data or secrets.
param([string]$Python = "python")
$ErrorActionPreference = "Stop"
$studySetupRoot = $PSScriptRoot
Push-Location -LiteralPath $studySetupRoot
try {
    & $Python -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10 or newer is required'; print(sys.version)"
    if ($LASTEXITCODE -ne 0) { throw "Choose an installed Python 3.10+ interpreter with -Python." }
    & $Python -m venv (Join-Path $studySetupRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create .venv." }
    $studySetupPython = Join-Path $studySetupRoot ".venv\Scripts\python.exe"
    & $studySetupPython -m pip install -r (Join-Path $studySetupRoot "requirements-lock.txt")
    if ($LASTEXITCODE -ne 0) { throw "Could not install dependencies." }
    & $studySetupPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Dependency compatibility check failed." }
    Write-Host "Environment ready. Run .\run_forever.ps1. Existing .env and study data were preserved."
} finally {
    Pop-Location
}
