#
# pause-paper-brokers.ps1 — scoped pause for the two IBKR paper
# gateways (isa-paper, normal-paper) while IBKR runs paper-trading
# maintenance.
#
# Leaves alone:
#   * IBGateway-isa-live + IBGateway-normal-live  (keep trading)
#   * BrokerWatchdog                              (still supervises live)
#   * BrokerTray / Hider / DailyRestart           (untouched)
#
# What it does:
#   1. Writes the two paper labels into C:\IBC\paused-labels.txt —
#      BrokerWatchdog reads this on every 5-min tick and skips any
#      label listed. Existing entries are preserved, duplicates merged.
#   2. Disables IBGateway-isa-paper + IBGateway-normal-paper scheduled
#      tasks so a logon / daily restart / manual RunNow won't relaunch
#      them.
#   3. /End any currently-running instance of those two tasks.
#   4. Kills the paper gateway processes by command-line match on
#      `gateway-<label>` so we don't touch the live JVMs.
#   5. Kills any IBKRTotpFiller instance that's targeting the paper
#      labels (so a stuck filler loop for the now-absent dialog
#      doesn't keep retrying forever).
#
# Run as Administrator:
#   powershell -ExecutionPolicy Bypass -File .\pause-paper-brokers.ps1
#
# To resume later:
#   powershell -ExecutionPolicy Bypass -File .\resume-paper-brokers.ps1
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

# ---- 1. Write / update the paused-labels sentinel ----
$pausedPath = 'C:\IBC\paused-labels.txt'
$parent = Split-Path $pausedPath -Parent
if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }

$existing = @()
if (Test-Path $pausedPath) {
    $existing = Get-Content $pausedPath -ErrorAction SilentlyContinue |
                ForEach-Object { $_.Trim() } |
                Where-Object { $_ -and -not $_.StartsWith('#') }
}
$merged = ($existing + $paperLabels) | Sort-Object -Unique
$header = @(
    '# Labels listed here are SKIPPED by BrokerWatchdog on every tick.',
    '# Managed by pause-paper-brokers.ps1 / resume-paper-brokers.ps1.'
)
($header + $merged) | Set-Content -Path $pausedPath -Encoding UTF8
Write-Host ('→ Wrote {0} ({1} label(s))' -f $pausedPath, $merged.Count)
foreach ($l in $merged) { Write-Host ("    {0}" -f $l) -ForegroundColor DarkGray }

# ---- 2. Disable the two paper launcher tasks ----
Write-Host ''
Write-Host '→ Disabling paper launcher tasks'
foreach ($t in $paperTasks) {
    try {
        & schtasks.exe /End /TN $t 2>$null | Out-Null
        Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
        Disable-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue | Out-Null
        Write-Host ('  disabled: {0}' -f $t)
    } catch {
        Write-Host ('  skipped:  {0} (not registered)' -f $t) -ForegroundColor DarkGray
    }
}

# ---- 3. Kill paper gateway processes (scoped by CommandLine) ----
Write-Host ''
Write-Host '→ Killing paper gateway processes'
$killed = 0
foreach ($label in $paperLabels) {
    # IBC launches the JVM as java.exe / javaw.exe with the config path
    # (containing `gateway-<label>`) on the command line. Matching there
    # gives us per-label scope — neighbouring live JVMs stay untouched.
    Get-CimInstance Win32_Process -Filter "Name = 'java.exe' OR Name = 'javaw.exe' OR Name = 'ibgateway.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -match ("gateway-{0}" -f [regex]::Escape($label)) } |
        ForEach-Object {
            Write-Host ('  killing {0} pid={1} [{2}]' -f $_.Name, $_.ProcessId, $label)
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            $killed++
        }
}

# ---- 4. Kill lingering TOTP fillers targeting the paper labels ----
# Two-level match: outer finds PS instances running IBKRTotpFiller, inner
# loop confirms the command line also references one of our paper labels
# so we don't clobber fillers that are working the live dialogs.
Write-Host ''
Write-Host '→ Killing paper TOTP fillers (if any)'
Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" |
    ForEach-Object {
        $cmd = $_.CommandLine
        if ($cmd -and $cmd -match 'IBKRTotpFiller') {
            foreach ($lbl in $paperLabels) {
                if ($cmd -match [regex]::Escape($lbl)) {
                    Write-Host ('  killing powershell pid={0} (TOTP filler for {1})' -f $_.ProcessId, $lbl)
                    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                    break
                }
            }
        }
    }

Write-Host ''
Write-Host ('✓ Paper gateways paused ({0} process(es) killed).' -f $killed) -ForegroundColor Green
Write-Host ''
Write-Host '  • BrokerWatchdog will skip these labels on its next 5-min tick.'
Write-Host '  • Live gateways remain supervised.'
Write-Host '  • Re-run resume-paper-brokers.ps1 to bring paper back online.'
