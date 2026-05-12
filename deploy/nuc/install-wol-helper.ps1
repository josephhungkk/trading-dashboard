# deploy/nuc/install-wol-helper.ps1 — Phase 11a-A2
# Register wol_helper.ps1 as a Windows scheduled task that starts at
# boot. Mirrors the pattern used by broker-sidecar tasks.

#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

$taskName = "dashboard-wol-helper"
$scriptPath = (Resolve-Path "$PSScriptRoot\wol_helper.ps1").Path

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Force

Start-ScheduledTask -TaskName $taskName
Write-Host "OK wol-helper scheduled task installed and started."
