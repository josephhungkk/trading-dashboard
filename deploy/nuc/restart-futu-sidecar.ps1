#Requires -Version 5.1
<#
.SYNOPSIS
    Restart the Futu sidecar by bouncing its scheduled task.

.DESCRIPTION
    Phase 6 G1. Run on the NUC as Administrator. Stops the BrokerSidecarFutu
    scheduled task, kills any orphaned futu-sidecar.exe, then re-fires the
    scheduled task. Reports pid + alive status.

.NOTES
    File saved as UTF-8 with BOM and CRLF line endings per memory note
    ps1_nuc_bom_crlf.md so PowerShell 5.1 parses it correctly.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

$cu = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal $cu).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $admin) {
    Write-Error '[restart-futu] Must run as Administrator.'
    return
}

Write-Host '[restart-futu] -> Stopping BrokerSidecarFutu scheduled task' -ForegroundColor Cyan
& schtasks.exe /End /TN 'BrokerSidecarFutu' 2>$null | Out-Null

Write-Host '[restart-futu] -> Killing any orphan futu-sidecar.exe' -ForegroundColor Cyan
Get-Process | Where-Object Name -eq 'futu-sidecar' |
    Stop-Process -Force -ErrorAction SilentlyContinue

Start-Sleep -Seconds 1
Write-Host '[restart-futu] -> Re-firing BrokerSidecarFutu' -ForegroundColor Cyan
& schtasks.exe /Run /TN 'BrokerSidecarFutu'

Start-Sleep -Seconds 3
$alive = Get-Process | Where-Object Name -eq 'futu-sidecar'
if ($alive) {
    Write-Host "[OK] futu-sidecar pid=$($alive.Id) running" -ForegroundColor Green
} else {
    Write-Warning '[WARN] futu-sidecar not running after restart'
}
