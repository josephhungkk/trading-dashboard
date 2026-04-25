#Requires -Version 5.1
<#
.SYNOPSIS
    Register Scheduled-Task trampolines for the most common elevated NUC
    operations so non-elevated callers (WSL interop, casual PS prompts)
    can fire them via Start-ScheduledTask without UAC.

.DESCRIPTION
    Phase 4.5 plan addendum (TASKS #21). Pairs with deploy/nuc/install-
    gsudo.ps1 (the ad-hoc escape hatch). Both register tasks under
    HUNG-STOCK\<UserId> with RunLevel=Highest + LogonType=Interactive so
    when fired they run in the user's interactive desktop session with
    full admin token, which is what:

      - restart-tray.ps1 needs (its $admin guard + the WM_MOUSEMOVE
        Shell_TrayWnd refresh sweep both require admin AND the user's
        desktop)
      - kill-stuck-trays.ps1 needs (Stop-Process against trays spawned
        with RunLevel=Highest tokens)

    Registered tasks:

      AdminTrampoline-RestartTray
        -> & C:\dashboard\deploy\nuc\restart-tray.ps1
      AdminTrampoline-KillStuckTrays
        -> & C:\dashboard\deploy\nuc\kill-stuck-trays.ps1

    Idempotent: re-running unregisters then re-registers, so script edits
    take effect on the next operator run.

    Must be run from an elevated PowerShell prompt (Register-ScheduledTask
    with RunLevel=Highest needs admin to write the task definition).

.PARAMETER UserId
    Scheduled-Task principal. Defaults to $env:USERNAME (the operator
    running the registrar).

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). ASCII-only.
#>
[CmdletBinding()]
param(
    [string]$UserId = $env:USERNAME
)

$ErrorActionPreference = 'Stop'

# Refuse to run non-elevated. Register-ScheduledTask with RunLevel=Highest
# needs admin to write the task XML.
$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run from an elevated PowerShell - Register-ScheduledTask needs admin to set RunLevel=Highest."
}

# Each entry: TaskName + path of the script the action will run.
$trampolines = @(
    @{
        Name   = 'AdminTrampoline-RestartTray'
        Script = 'C:\dashboard\deploy\nuc\restart-tray.ps1'
    }
    @{
        Name   = 'AdminTrampoline-KillStuckTrays'
        Script = 'C:\dashboard\deploy\nuc\kill-stuck-trays.ps1'
    }
)

foreach ($t in $trampolines) {
    if (-not (Test-Path $t.Script)) {
        throw "Target script not found: $($t.Script). Sync deploy/nuc/ to C:\dashboard\deploy\nuc\ first."
    }

    Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue

    $action = New-ScheduledTaskAction `
        -Execute 'powershell.exe' `
        -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $t.Script)

    # Interactive principal: when the task is fired, the new process lands
    # in the user's active desktop session (where Shell_TrayWnd lives).
    # RunLevel=Highest gives it the admin token so admin-gated code paths
    # (restart-tray.ps1's $admin guard, taskkill on elevated targets)
    # actually execute.
    $taskPrincipal = New-ScheduledTaskPrincipal `
        -UserId $UserId `
        -LogonType Interactive `
        -RunLevel Highest

    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
        -StartWhenAvailable

    Register-ScheduledTask `
        -TaskName $t.Name `
        -Action $action `
        -Principal $taskPrincipal `
        -Settings $settings | Out-Null

    Write-Host "[register-admin-helpers] registered $($t.Name) -> $($t.Script)" -ForegroundColor Green
}

Write-Host ''
Write-Host '[register-admin-helpers] all trampolines registered.' -ForegroundColor Green
Write-Host ''
Write-Host 'Fire from any context (no UAC needed once registered):' -ForegroundColor Yellow
foreach ($t in $trampolines) {
    Write-Host ("  Start-ScheduledTask -TaskName '{0}'" -f $t.Name) -ForegroundColor Yellow
}
