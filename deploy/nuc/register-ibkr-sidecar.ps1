#Requires -Version 5.1
<#
.SYNOPSIS
    Register Scheduled Tasks that auto-launch the four IBKR sidecars on
    user logon (Phase 4 Task 27).

.DESCRIPTION
    Creates IBKRSidecar-isa-live, IBKRSidecar-isa-paper, IBKRSidecar-normal-
    live, IBKRSidecar-normal-paper. Each task fires
    `wscript.exe Launch-IBKRSidecar.vbs <label>` at logon with staggered
    delays (+30/+60/+90/+120s) so the four sidecars don't all hit the
    gateway + bind their gRPC ports simultaneously. The stagger also
    matches the Dashboard_old broker launcher chain
    (memory broker_gateways.md).

    Each task sets RestartInterval=1m + RestartCount=9999 so when a sidecar
    exits non-zero (e.g. on disconnect watchdog firing exit 64), Task
    Scheduler relaunches it within a minute. Combined with the sidecar's
    self-throttled backoff this gives a safe relaunch loop without going
    tight.

    Idempotent: re-running unregisters then re-registers, so script edits
    take effect on the next operator run.

.PARAMETER Labels
    The four sidecar labels. Defaults to the canonical four; override only
    for testing.

.PARAMETER Offsets
    Per-label seconds-after-logon delays. Must match Labels in length.

.PARAMETER VbsPath
    Where Launch-IBKRSidecar.vbs lives on the NUC. Defaults to the canonical
    C:\dashboard\deploy\nuc\Launch-IBKRSidecar.vbs.

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). ASCII-only.
    Run as the user that owns the IB Gateway sessions (so the spawned
    sidecar inherits the right ProgramData state dir). Requires admin to
    register tasks under S4U.
#>
[CmdletBinding()]
param(
    [string[]]$Labels = @('isa-live', 'isa-paper', 'normal-live', 'normal-paper'),
    [int[]]$Offsets = @(30, 60, 90, 120),
    [string]$VbsPath = 'C:\dashboard\deploy\nuc\Launch-IBKRSidecar.vbs'
)

$ErrorActionPreference = 'Stop'

if ($Labels.Length -ne $Offsets.Length) {
    throw "Labels and Offsets must be the same length (got $($Labels.Length) and $($Offsets.Length))."
}

if (-not (Test-Path $VbsPath)) {
    throw "Launcher VBS not found at $VbsPath. Sync deploy/nuc/ to C:\dashboard\deploy\nuc\ first."
}

for ($i = 0; $i -lt $Labels.Length; $i++) {
    $label = $Labels[$i]
    $offset = $Offsets[$i]
    $taskName = "IBKRSidecar-$label"

    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

    $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument """$VbsPath"" $label"
    $trigger = New-ScheduledTaskTrigger -AtLogon
    $trigger.Delay = "PT${offset}S"

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

    Write-Host "[register] $taskName -> wscript $VbsPath $label, +${offset}s after logon" -ForegroundColor Green
}

Write-Host ''
Write-Host "[register] all 4 IBKRSidecar-* tasks registered. Verify with: schtasks /query /tn IBKRSidecar-isa-live" -ForegroundColor Cyan
