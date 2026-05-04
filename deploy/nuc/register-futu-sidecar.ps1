#Requires -Version 5.1
<#
.SYNOPSIS
    Register the BrokerSidecarFutu Scheduled Task that auto-launches
    futu-sidecar.exe at user logon (Phase 7a follow-up).

.DESCRIPTION
    The Futu sidecar is a console-subsystem PE - if launched directly via
    schtasks /Create on the .exe, Windows shows a persistent console window
    even with `-Hidden`. This script wires the task to wscript.exe + the
    Launch-FutuSidecar.vbs hidden launcher (intWindowStyle=0) so the user
    never sees a flash. Mirrors register-ibkr-sidecar.ps1 except:
      - Single instance (no per-label fan-out).
      - Fires after the four IBKR sidecars (PT150S) so its first OpenD
        probe doesn't compete with gateway login + 2FA traffic.
      - Auto-restart loop: RestartInterval=1m, RestartCount=9999.

    Idempotent: re-running unregisters then re-registers, so script edits
    take effect on the next operator run.

.PARAMETER VbsPath
    Where Launch-FutuSidecar.vbs lives on the NUC. Defaults to the canonical
    C:\dashboard\deploy\nuc\Launch-FutuSidecar.vbs.

.PARAMETER OffsetSeconds
    Seconds-after-logon delay. Default 150 (after the four IBKR sidecars at
    30/60/90/120s per register-ibkr-sidecar.ps1).

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). ASCII-only.
    Run as the user that owns the FutuOpenD session so the spawned sidecar
    inherits the right ProgramData state dir. Requires admin to register
    tasks under S4U.
#>
[CmdletBinding()]
param(
    [string]$VbsPath = 'C:\dashboard\deploy\nuc\Launch-FutuSidecar.vbs',
    [int]$OffsetSeconds = 150
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $VbsPath)) {
    throw "Launcher VBS not found at $VbsPath. Sync deploy/nuc/ to C:\dashboard\deploy\nuc\ first."
}

$taskName = 'BrokerSidecarFutu'

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument """$VbsPath"""
$trigger = New-ScheduledTaskTrigger -AtLogon
$trigger.Delay = "PT${OffsetSeconds}S"

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType S4U `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 9999 `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings | Out-Null

Write-Host "[register] $taskName -> wscript $VbsPath, +${OffsetSeconds}s after logon" -ForegroundColor Green
Write-Host ''
Write-Host "[register] Verify with: schtasks /query /tn $taskName /v" -ForegroundColor Cyan
