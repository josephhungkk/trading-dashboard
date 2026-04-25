# Daily full restart of all 5 broker endpoints (4 IB Gateways + FutuOpenD).
# Runs at 23:50 London local time via scheduled task BrokerDailyRestart.
# Strategy: kill every broker process, then re-fire the at-logon scheduled
# tasks with the same 0/5/30/60/90s stagger used at boot. This re-runs
# Launch-Gateway.ps1 (and IBKRTotpFiller.ps1 for live accounts) so TOTP is
# entered freshly each day. Total runtime ~95s.

$ErrorActionPreference = 'Continue'

$logDir  = 'C:\IBC\Logs\daily-restart'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
$logFile = Join-Path $logDir ("restart-{0}.log" -f (Get-Date -Format 'yyyyMMdd'))
function DLog { param([string]$m) Add-Content -Path $logFile -Value ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) }
DLog "==== daily restart start ===="

$labels = @('isa-live','isa-paper','normal-live','normal-paper')
foreach ($lbl in $labels) {
  $procs = @(Get-CimInstance Win32_Process -Filter "Name = 'java.exe'" |
             Where-Object { $_.CommandLine -match "gateway-$lbl" })
  foreach ($p in $procs) {
    DLog ("kill java {0} pid={1}" -f $lbl, $p.ProcessId)
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  }
}
foreach ($f in @(Get-Process -Name 'FutuOpenD' -ErrorAction SilentlyContinue)) {
  DLog ("kill FutuOpenD pid={0}" -f $f.Id)
  Stop-Process -Id $f.Id -Force -ErrorAction SilentlyContinue
}

# Give killed processes 5s to release their ports + lock files before relaunch.
Start-Sleep -Seconds 5

# Same stagger as boot-time logon tasks. Cumulative delay from script start:
# 0s -> isa-live fires (live dialogs staggered to avoid ForceForeground race)
# 5s -> FutuOpenD
# 30s -> isa-paper
# 60s -> normal-live
# 90s -> normal-paper
$schedule = @(
  @{ Task='IBGateway-isa-live';     DelaySec=0  }
  @{ Task='FutuOpenDAutoStart';     DelaySec=5  }
  @{ Task='IBGateway-isa-paper';    DelaySec=25 }
  @{ Task='IBGateway-normal-live';  DelaySec=30 }
  @{ Task='IBGateway-normal-paper'; DelaySec=30 }
)
foreach ($s in $schedule) {
  if ($s.DelaySec -gt 0) { Start-Sleep -Seconds $s.DelaySec }
  try {
    Start-ScheduledTask -TaskName $s.Task
    DLog ("fired: {0}" -f $s.Task)
  } catch {
    DLog ("ERR Start-ScheduledTask '{0}': {1}" -f $s.Task, $_.Exception.Message)
  }
}

DLog "==== daily restart queued; watchdog will retry any failed relaunch ===="
