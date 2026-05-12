# deploy/heavybox/install-idle-suspend.ps1 — Phase 11a-A2
# Auto-suspend the heavy box after 15min of no traffic to :11434.
# Runs as a scheduled task every 5min; checks netstat; suspends when
# the count of established connections has been zero for 3 consecutive
# checks (3 * 5min = 15min idle window).

#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

$watchScript = @'
$idleFile = "$env:ProgramData\dashboard-heavy-idle.txt"
$count = 0
if (Test-Path $idleFile) { $count = [int](Get-Content $idleFile) }

$conns = (netstat -an | Select-String ":11434" | Select-String "ESTABLISHED").Count
if ($conns -eq 0) { $count++ } else { $count = 0 }
Set-Content -Path $idleFile -Value $count

if ($count -ge 3) {
    Remove-Item $idleFile -Force
    # 0=sleep, 1=hibernate; first 0 = sleep, last 0 = no wake-up-immediately,
    # middle 1 = force suspend.
    rundll32.exe powrprof.dll,SetSuspendState 0,1,0
}
'@

$watchPath = "$env:ProgramData\dashboard-heavy-idle-check.ps1"
Set-Content -Path $watchPath -Value $watchScript -Encoding UTF8

$taskName = "dashboard-heavy-idle-suspend"
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$watchPath`""
$trigger = New-ScheduledTaskTrigger `
    -Once -At (Get-Date).AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Force

Write-Host "OK idle-suspend scheduled task installed (15min idle window)."
