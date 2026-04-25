#
# pause-brokers.ps1 — stop the broker-watchdog + tray and kill every
# IB Gateway instance so you can reconfigure gateway settings without
# the watchdog re-launching them every 5 min.
#
# Run as Administrator:
#   powershell -ExecutionPolicy Bypass -File .\pause-brokers.ps1
#
# To resume the normal supervised setup afterwards:
#   powershell -ExecutionPolicy Bypass -File .\resume-brokers.ps1
#
# What this does (idempotent — safe to re-run):
#   1. Stops + DISABLES the watchdog / tray / hider / auto-start
#      scheduled tasks so nothing auto-restarts a gateway while
#      you're editing it.
#   2. Ends any currently-running scheduled-task instances of the
#      4 IBGateway-* tasks (daily restart etc.) and disables them.
#   3. Kills every ibgateway.exe / javaw.exe (IB Gateway process)
#      plus any lingering PowerShell launchers wearing a Broker title.
#
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

# Require admin — scheduled-task stop/disable won't work otherwise.
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal $currentUser).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Error 'Must run as Administrator.'
    return
}

$tasks = @(
    # Supervisors — disable so they don't resurrect gateways.
    'BrokerWatchdog',
    'BrokerTray',
    'BrokerWindowsHider',
    'BrokerDailyRestart',
    # Gateway launchers themselves — disable so logon / daily restart
    # doesn't re-fire them.
    'IBGateway-isa-live',
    'IBGateway-isa-paper',
    'IBGateway-normal-live',
    'IBGateway-normal-paper'
)

Write-Host '→ Stopping + disabling scheduled tasks'
foreach ($t in $tasks) {
    try {
        # schtasks /End — kills any currently-running instance.
        & schtasks.exe /End /TN $t 2>$null | Out-Null
        Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
        Disable-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue | Out-Null
        Write-Host ("  disabled: {0}" -f $t)
    } catch {
        Write-Host ("  skipped:  {0} (not registered)" -f $t) -ForegroundColor DarkGray
    }
}

Write-Host ''
Write-Host '→ Killing IB Gateway processes'
# ibgateway.exe is the IBC-launched wrapper; the actual JVM appears
# as javaw.exe. Match on the path so we don't kill unrelated JVMs
# the user might be running for their own work.
$killed = 0
Get-Process -Name ibgateway -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host ("  killing ibgateway.exe pid={0}" -f $_.Id)
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    $killed++
}
Get-Process -Name javaw -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and ($_.Path -match 'ibgateway' -or $_.Path -match '\\Jts\\')
} | ForEach-Object {
    Write-Host ("  killing javaw.exe pid={0} ({1})" -f $_.Id, $_.Path)
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    $killed++
}

Write-Host ''
Write-Host '→ Killing lingering broker PowerShell launchers'
Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='wscript.exe'" |
    Where-Object { $_.CommandLine -match 'Broker|Launch-Gateway|IBKRTotpFiller' } |
    ForEach-Object {
        Write-Host ("  killing {0} pid={1}" -f $_.Name, $_.ProcessId)
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Write-Host ''
Write-Host ('✓ Brokers paused ({0} gateway process(es) killed).' -f $killed) -ForegroundColor Green
Write-Host '  System tray icons may take a moment to vanish.'
Write-Host '  Launch IB Gateway manually (C:\Jts\ibgateway\<ver>\ibgateway.exe) to inspect API settings.'
Write-Host '  Run resume-brokers.ps1 when done.'
