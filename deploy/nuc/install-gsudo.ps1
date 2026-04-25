#Requires -Version 5.1
<#
.SYNOPSIS
    Install gsudo (sudo for Windows) so operator scripts can elevate
    individual commands without spawning a new admin shell.

.DESCRIPTION
    Phase 4.5 plan addendum (TASKS #21). Pairs with
    deploy/nuc/register-admin-helpers.ps1 which registers Scheduled-Task
    trampolines for the most common elevated operations (restart-tray,
    kill stuck sidecar/tray processes). gsudo is the ad-hoc escape hatch
    when the trampolines don't fit; the trampolines are the steady-state
    path.

    Idempotent: skips install if gsudo is already on PATH.

.PARAMETER Force
    Re-run the winget install even if gsudo is already on PATH (e.g. to
    upgrade to the latest version).

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). ASCII-only.
    Run from any PowerShell prompt - winget itself prompts for elevation
    via UAC when it needs to. Once installed, gsudo invocations work from
    any context (interop or interactive).
#>
[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = 'Stop'

$existing = Get-Command gsudo -ErrorAction SilentlyContinue
if ($existing -and -not $Force) {
    Write-Host "[gsudo] already installed at $($existing.Source); skipping." -ForegroundColor Green
    & $existing.Source --version 2>&1 | Select-Object -First 2 | ForEach-Object { Write-Host "  $_" }
    exit 0
}

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "winget not found. Install App Installer from the Microsoft Store first."
}

Write-Host "[gsudo] installing via winget (gerardog.gsudo)..." -ForegroundColor Cyan
& winget install --id gerardog.gsudo -e --accept-source-agreements --accept-package-agreements --silent
if ($LASTEXITCODE -ne 0) {
    throw "winget install failed with exit code $LASTEXITCODE"
}

# winget puts the shim in WindowsApps which is on the user PATH; refresh
# the current session's PATH so the gsudo we just installed is reachable
# without restarting the shell.
$env:PATH = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' +
            [System.Environment]::GetEnvironmentVariable('PATH', 'User')

$installed = Get-Command gsudo -ErrorAction SilentlyContinue
if (-not $installed) {
    Write-Warning "[gsudo] install reported success but gsudo is still not on PATH. Restart your shell."
    exit 1
}

Write-Host "[gsudo] installed: $($installed.Source)" -ForegroundColor Green
& $installed.Source --version 2>&1 | Select-Object -First 2 | ForEach-Object { Write-Host "  $_" }
Write-Host ""
Write-Host "Usage: gsudo <command>     # elevates a single command via UAC" -ForegroundColor Yellow
Write-Host "       gsudo cache on      # cache UAC for 5 min so subsequent gsudos don't re-prompt" -ForegroundColor Yellow
Write-Host "       gsudo cache off     # drop the cached token" -ForegroundColor Yellow
