#
# resume-paper-brokers.ps1 — undoes pause-paper-brokers.ps1: clears
# the paper labels from C:\IBC\paused-labels.txt, re-enables the two
# paper launcher tasks, and fires them with a 30-s stagger so IBC's
# SendKeys don't collide on the foreground-window lock.
#
# Live gateways are not touched. Run as Administrator.
#
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal $currentUser).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Error 'Must run as Administrator.'
    return
}

$paperLabels = @('isa-paper', 'normal-paper')
$paperTasks  = $paperLabels | ForEach-Object { "IBGateway-$_" }

# ---- 1. Remove paper labels from the sentinel file ----
$pausedPath = 'C:\IBC\paused-labels.txt'
if (Test-Path $pausedPath) {
    $kept = Get-Content $pausedPath -ErrorAction SilentlyContinue |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -and -not $_.StartsWith('#') } |
            Where-Object { $paperLabels -notcontains $_ }
    if ($kept.Count -eq 0) {
        Remove-Item $pausedPath -Force -ErrorAction SilentlyContinue
        Write-Host ('→ Cleared {0} (nothing else paused)' -f $pausedPath)
    } else {
        $header = @(
            '# Labels listed here are SKIPPED by BrokerWatchdog on every tick.',
            '# Managed by pause-paper-brokers.ps1 / resume-paper-brokers.ps1.'
        )
        ($header + $kept) | Set-Content -Path $pausedPath -Encoding UTF8
        Write-Host ('→ Removed paper labels from {0}, kept {1} other entry(ies)' -f $pausedPath, $kept.Count)
    }
} else {
    Write-Host ('→ {0} not present — nothing to clear' -f $pausedPath)
}

# ---- 2. Re-enable + fire paper tasks with stagger ----
Write-Host ''
Write-Host '→ Enabling paper launcher tasks'
foreach ($t in $paperTasks) {
    try {
        Enable-ScheduledTask -TaskName $t -ErrorAction Stop | Out-Null
        Write-Host ('  enabled:  {0}' -f $t)
    } catch {
        Write-Host ('  skipped:  {0} (not registered)' -f $t) -ForegroundColor DarkGray
    }
}

Write-Host ''
Write-Host '→ Firing paper tasks with stagger'
$stagger = 30
for ($i = 0; $i -lt $paperTasks.Count; $i++) {
    $t = $paperTasks[$i]
    if ($i -gt 0) {
        Write-Host ('  waiting {0}s before {1}' -f $stagger, $t) -ForegroundColor DarkGray
        Start-Sleep -Seconds $stagger
    }
    try {
        Start-ScheduledTask -TaskName $t -ErrorAction Stop
        Write-Host ('  started:  {0}' -f $t)
    } catch {
        Write-Host ('  FAILED:   {0} — {1}' -f $t, $_.Exception.Message) -ForegroundColor Red
    }
}

Write-Host ''
Write-Host '✓ Paper gateways resumed.' -ForegroundColor Green
Write-Host '  Give IBC ~2 min to complete login + TOTP on each paper account.'
