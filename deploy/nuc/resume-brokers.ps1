#
# resume-brokers.ps1 — re-enable the broker scheduled tasks and fire
# them in the normal boot order so the whole supervised stack comes
# back up the way it does after a reboot.
#
# Run as Administrator:
#   powershell -ExecutionPolicy Bypass -File .\resume-brokers.ps1
#
# Mirrors the stagger that `Launch-Gateway.ps1` expects after a full
# restart: supervisors first, then gateways spaced out so IBC /
# TOTP fillers don't collide on the SetForegroundWindow lock.
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

# Order + delay match the at-logon staggering from Phase 1 hardening.
# Supervisors first (no gateway dependency), then gateways with 5-30 s
# gaps between starts so IBC doesn't race on the shared log file or
# the Windows foreground-window lock.
$plan = @(
    @{ Name='BrokerWatchdog';         Delay=0  },
    @{ Name='BrokerTray';              Delay=0  },
    @{ Name='BrokerWindowsHider';      Delay=0  },
    @{ Name='BrokerDailyRestart';      Delay=0  },
    @{ Name='IBGateway-isa-live';      Delay=5  },
    @{ Name='IBGateway-isa-paper';     Delay=30 },
    @{ Name='IBGateway-normal-live';   Delay=60 },
    @{ Name='IBGateway-normal-paper';  Delay=90 }
)

Write-Host '→ Enabling tasks'
foreach ($t in $plan) {
    try {
        Enable-ScheduledTask -TaskName $t.Name -ErrorAction Stop | Out-Null
        Write-Host ("  enabled:  {0}" -f $t.Name)
    } catch {
        Write-Host ("  skipped:  {0} (not registered)" -f $t.Name) -ForegroundColor DarkGray
    }
}

Write-Host ''
Write-Host '→ Firing tasks with stagger'
foreach ($t in $plan) {
    if ($t.Delay -gt 0) {
        Write-Host ("  waiting {0}s before {1}" -f $t.Delay, $t.Name) -ForegroundColor DarkGray
        Start-Sleep -Seconds $t.Delay
    }
    try {
        Start-ScheduledTask -TaskName $t.Name -ErrorAction Stop
        Write-Host ("  started:  {0}" -f $t.Name)
    } catch {
        Write-Host ("  FAILED:   {0} — {1}" -f $t.Name, $_.Exception.Message) -ForegroundColor Red
    }
}

Write-Host ''
Write-Host '✓ Brokers resumed.' -ForegroundColor Green
Write-Host '  Give IBC ~2 min to complete auto-login + TOTP on the live accounts.'
Write-Host '  Then run verify-autostart.ps1 to confirm each gateway handshakes cleanly.'
