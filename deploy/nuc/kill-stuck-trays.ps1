#Requires -Version 5.1
<#
.SYNOPSIS
    Kill every running BrokerTray.ps1 PowerShell host process. Designed to
    be invoked from an elevated context (Scheduled-Task trampoline or
    gsudo) so it can terminate trays whose tokens this user shell can't
    otherwise reach.

.DESCRIPTION
    Phase 4.5 plan addendum (TASKS #21). The companion register-admin-
    helpers.ps1 registers a Scheduled Task `AdminTrampoline-KillStuckTrays`
    whose Action invokes this script with RunLevel=Highest, so a non-
    elevated caller can fire it via Start-ScheduledTask without UAC.

    Targets only powershell.exe / wscript.exe processes whose CommandLine
    contains 'BrokerTray.ps1' or 'Launch-Tray.vbs', so other PowerShell
    hosts (notably the one that spawned this script) are left alone.

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). ASCII-only.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

$procs = Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='wscript.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -and
        ($_.CommandLine -match 'BrokerTray\.ps1' -or $_.CommandLine -match 'Launch-Tray\.vbs')
    }

if (-not $procs) {
    Write-Host '[kill-trays] no matching processes found.' -ForegroundColor Green
    exit 0
}

$killed = 0
foreach ($p in $procs) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Host ("[kill-trays] killed {0,-15} pid={1} session={2}" -f $p.Name, $p.ProcessId, $p.SessionId) -ForegroundColor Yellow
        $killed++
    } catch {
        Write-Host ("[kill-trays] FAILED to kill pid={0}: {1}" -f $p.ProcessId, $_.Exception.Message) -ForegroundColor Red
    }
}
Write-Host ("[kill-trays] killed {0} process(es)" -f $killed) -ForegroundColor Green
exit 0
