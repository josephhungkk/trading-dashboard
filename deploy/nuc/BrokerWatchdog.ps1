param(
  [switch]$DryRun
)

# Checks each broker port. If any is down, starts the corresponding AtLogon
# scheduled task (which re-launches the hidden VBS -> PS1 -> IBC/Futu chain).
# Intended to run as a 5-minute scheduled task.

$ErrorActionPreference = 'Continue'

$logDir  = 'C:\IBC\Logs\watchdog'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
$logFile = Join-Path $logDir ("watchdog-{0}.log" -f (Get-Date -Format 'yyyyMMdd'))
$stateFile = Join-Path $logDir 'state.json'
function WLog { param([string]$msg) Add-Content -Path $logFile -Value ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg) }

# Per-label reconnect backoff. First few probe failures retry every
# tick so a blip recovers fast; persistent failures skip progressively
# longer (60s → 1800s cap) so a server that's really down doesn't
# trigger a restart storm.
# State file survives scheduled-task reinvocation; it's just a small
# JSON map of label -> { fails, skip_until_utc }.
$BACKOFF_FREE = 3
$BACKOFF_BASE_SEC = 60
$BACKOFF_MAX_SEC  = 1800

function Load-WatchdogState {
  if (-not (Test-Path $stateFile)) { return @{} }
  try {
    $raw = Get-Content $stateFile -Raw -ErrorAction Stop
    if (-not $raw.Trim()) { return @{} }
    $obj = $raw | ConvertFrom-Json -ErrorAction Stop
    # Convert PSCustomObject → hashtable for easy mutation.
    $h = @{}
    foreach ($p in $obj.PSObject.Properties) { $h[$p.Name] = $p.Value }
    return $h
  } catch {
    WLog ("WARN  could not load state file: {0}" -f $_.Exception.Message)
    return @{}
  }
}

function Save-WatchdogState {
  param([hashtable]$State)
  try {
    ($State | ConvertTo-Json -Compress) | Set-Content -Path $stateFile -Encoding UTF8
  } catch {
    WLog ("WARN  could not save state file: {0}" -f $_.Exception.Message)
  }
}

# Converts "now" into three reference timezones and reports whether
# the current instant falls inside any published IBKR reset window.
# During a reset, the gateway's API socket is expected to be zombie /
# down; probing it triggers a kill-+-relaunch cycle that produces 5-min
# restart storms for the whole window. This function lets the main loop
# skip silently instead.
#
# Sources: IBKR published schedule (Nov 2024).
#   Weekend reset:  Fri 23:00 ET → Sat 03:00 ET — ALL regions.
#   Daily reset (Sun-Fri):
#     North America: 00:15-01:45 ET
#     Europe:        06:25-07:45 CET (CEST in summer)
#     APAC (HK):     04:45-06:05 HKT  (1st)
#                    20:15-21:15 HKT  (2nd)
function Test-InResetWindow {
  $utc = (Get-Date).ToUniversalTime()
  try {
    $et  = [System.TimeZoneInfo]::ConvertTimeFromUtc($utc,
            [System.TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time'))
    $cet = [System.TimeZoneInfo]::ConvertTimeFromUtc($utc,
            [System.TimeZoneInfo]::FindSystemTimeZoneById('Central European Standard Time'))
    $hkt = [System.TimeZoneInfo]::ConvertTimeFromUtc($utc,
            [System.TimeZoneInfo]::FindSystemTimeZoneById('China Standard Time'))
  } catch {
    # If the TZ DB is missing an ID this machine is fundamentally
    # broken — just return $false so the watchdog falls back to its
    # normal behaviour and we notice by other means.
    return @($false, 'tz-lookup-failed')
  }

  # Weekend window is ET-local. Fri 23:00 → Sat 03:00.
  if (($et.DayOfWeek -eq [DayOfWeek]::Friday   -and $et.Hour -ge 23) -or
      ($et.DayOfWeek -eq [DayOfWeek]::Saturday -and $et.Hour -lt  3)) {
    return @($true, 'weekend')
  }

  # Daily windows run Sun-Fri in each region's LOCAL time (not ET).
  # Saturday is the only day without a daily reset.
  $minutesOf = { param($d) $d.Hour * 60 + $d.Minute }

  # North America, ET
  if ($et.DayOfWeek -ne [DayOfWeek]::Saturday) {
    $m = & $minutesOf $et
    if ($m -ge (0*60+15) -and $m -le (1*60+45)) { return @($true, 'daily-NA') }
  }
  # Europe, CET
  if ($cet.DayOfWeek -ne [DayOfWeek]::Saturday) {
    $m = & $minutesOf $cet
    if ($m -ge (6*60+25) -and $m -le (7*60+45)) { return @($true, 'daily-EU') }
  }
  # APAC, HKT — two windows
  if ($hkt.DayOfWeek -ne [DayOfWeek]::Saturday) {
    $m = & $minutesOf $hkt
    if ($m -ge (4*60+45) -and $m -le (6*60+ 5)) { return @($true, 'daily-APAC-1') }
    if ($m -ge (20*60+15) -and $m -le (21*60+15)) { return @($true, 'daily-APAC-2') }
  }

  return @($false, '')
}

function Test-Port {
  param([int]$Port)
  $conn = New-Object Net.Sockets.TcpClient
  try {
    $iar = $conn.BeginConnect('127.0.0.1', $Port, $null, $null)
    $ok  = $iar.AsyncWaitHandle.WaitOne(1500)
    if (-not $ok) { return $false }
    $conn.EndConnect($iar)
    return $true
  } catch { return $false }
  finally { $conn.Close() }
}

# Deeper check for IBKR Gateway: does the API socket actually respond to the
# TWS version handshake? A Gateway that lost its upstream connection to IBKR's
# servers typically keeps listening but stops responding — "zombie" state.
# Returns 'up' / 'zombie' / 'down'.
function Test-IBKRHandshake {
  param([int]$Port)
  $client = New-Object Net.Sockets.TcpClient
  try {
    $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(1500)) { return 'down' }
    $client.EndConnect($iar)
    $s = $client.GetStream(); $s.ReadTimeout = 2000
    $s.Write([byte[]](0x41,0x50,0x49,0x00), 0, 4)
    $p = [Text.Encoding]::ASCII.GetBytes('v100..176') + @([byte]0)
    $lb = [BitConverter]::GetBytes([int32]$p.Length)
    if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($lb) }
    $s.Write($lb, 0, 4); $s.Write($p, 0, $p.Length); $s.Flush()
    $buf = New-Object byte[] 64
    try { $n = $s.Read($buf, 0, 64); return $(if ($n -gt 4) { 'up' } else { 'zombie' }) }
    catch { return 'zombie' }
  } catch { return 'down' }
  finally { $client.Close() }
}

# FutuOpenD is a proprietary protobuf protocol — skip the handshake and instead
# verify (1) the local port listens and (2) the FutuOpenD.exe process has at
# least one established OUTBOUND connection to a non-LAN address on port 443.
# When OpenD loses its link to Futu's servers, it keeps the local 11111 socket
# open but all remote 443 connections drop — a perfect zombie signature.
# Returns 'up' / 'zombie' / 'down'.
function Test-FutuConnected {
  if (-not (Test-Port 11111)) { return 'down' }
  $futu = @(Get-Process -Name 'FutuOpenD' -ErrorAction SilentlyContinue)
  if ($futu.Count -eq 0) { return 'down' }
  # Check every FutuOpenD.exe — during a relaunch there can be two briefly
  # (old zombie + new starting-up). Return 'up' if ANY has outbound 443.
  foreach ($f in $futu) {
    $outbound = @(Get-NetTCPConnection -OwningProcess $f.Id -ErrorAction SilentlyContinue |
                  Where-Object {
                    $_.State -eq 'Established' -and
                    $_.RemotePort -eq 443 -and
                    $_.RemoteAddress -notmatch '^(127\.|10\.|192\.168\.|169\.254\.|fe80:|::1)'
                  })
    if ($outbound.Count -gt 0) { return 'up' }
  }
  return 'zombie'
}

$targets = @(
  @{ Label='isa-live';     Port=4001;  Task='IBGateway-isa-live';     ProcMatch='gateway-isa-live';     Probe={ Test-IBKRHandshake -Port 4001  } }
  @{ Label='isa-paper';    Port=4002;  Task='IBGateway-isa-paper';    ProcMatch='gateway-isa-paper';    Probe={ Test-IBKRHandshake -Port 4002  } }
  @{ Label='normal-live';  Port=4003;  Task='IBGateway-normal-live';  ProcMatch='gateway-normal-live';  Probe={ Test-IBKRHandshake -Port 4003  } }
  @{ Label='normal-paper'; Port=4004;  Task='IBGateway-normal-paper'; ProcMatch='gateway-normal-paper'; Probe={ Test-IBKRHandshake -Port 4004  } }
  @{ Label='FutuOpenD';    Port=11111; Task='FutuOpenDAutoStart';     ProcMatch='FutuOpenD\.exe';       Probe={ Test-FutuConnected             } }
)

# Opt-out list — pause-paper-brokers.ps1 writes one label per line to
# `C:\IBC\paused-labels.txt` while a paper gateway is being
# reconfigured. The watchdog reads the file on every tick (so changes
# take effect within 5 minutes without a restart) and leaves matching
# labels alone. Missing file = nothing paused.
$pausedPath = 'C:\IBC\paused-labels.txt'
$paused = @()
if (Test-Path $pausedPath) {
  $paused = Get-Content $pausedPath -ErrorAction SilentlyContinue |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -and -not $_.StartsWith('#') }
}

# IBKR scheduled maintenance — only the WEEKEND reset (Fri 23:00 ET →
# Sat 03:00 ET, 4h, every region) warrants a skip. The daily resets are
# short enough (~1h20m) that the retry storm isn't worth quieting, and
# skipping them would also mask a genuine outage that happened to land
# inside a daily window. FutuOpenD doesn't share IBKR's schedule.
$resetCheck = Test-InResetWindow
$resetName  = $resetCheck[1]
$inWeekend  = $resetCheck[0] -and $resetName -eq 'weekend'
if ($inWeekend) {
  WLog ('RESET ibkr skipping probes — in weekend maintenance window')
}

$wdState = Load-WatchdogState
$nowUtc  = (Get-Date).ToUniversalTime()

# Strict: any state other than 'up' triggers a kill + relaunch immediately —
# a single 'zombie' reading is enough. No grace period or consecutive-miss
# counter; the at-logon task re-launches cleanly with TOTP + hiding.
foreach ($t in $targets) {
  if ($paused -contains $t.Label) {
    WLog ("SKIP  {0,-14} paused (paused-labels.txt)" -f $t.Label)
    continue
  }
  # Skip every IBKR gateway during the weekend reset. FutuOpenD
  # isn't on IBKR's schedule.
  if ($inWeekend -and $t.Label -ne 'FutuOpenD') {
    continue
  }

  # Honor per-label backoff — persistently failing labels have a
  # `skip_until_utc` timestamp in state.json. Before the deadline,
  # silently skip without probing / killing / restarting.
  $s = $wdState[$t.Label]
  if ($s -and $s.skip_until_utc) {
    $until = [DateTime]::Parse($s.skip_until_utc, $null,
              [System.Globalization.DateTimeStyles]::RoundtripKind)
    if ($nowUtc -lt $until) {
      $remaining = [int]($until - $nowUtc).TotalSeconds
      WLog ("BACK  {0,-14} backing off — {1}s remaining (fails={2})" -f
            $t.Label, $remaining, $s.fails)
      continue
    }
  }

  $state = & $t.Probe
  if ($state -eq 'up') {
    WLog ("OK    {0,-14} port={1} state=up" -f $t.Label, $t.Port)
    if ($s) {
      # Reset backoff on success.
      $wdState.Remove($t.Label) | Out-Null
    }
    continue
  }
  WLog ("BAD   {0,-14} port={1} state={2}" -f $t.Label, $t.Port, $state)

  # Bump failure count + update backoff.
  $fails = if ($s) { [int]$s.fails + 1 } else { 1 }
  $delay = 0
  if ($fails -gt $BACKOFF_FREE) {
    $exp   = $fails - $BACKOFF_FREE - 1
    $delay = [Math]::Min($BACKOFF_BASE_SEC * [Math]::Pow(2, $exp), $BACKOFF_MAX_SEC)
  }
  $skipUntilIso = if ($delay -gt 0) {
    $nowUtc.AddSeconds($delay).ToString('o')
  } else { $null }
  $wdState[$t.Label] = @{ fails = $fails; skip_until_utc = $skipUntilIso }
  if ($delay -gt 0) {
    WLog ("HOLD  {0,-14} fails={1} next retry in {2}s" -f $t.Label, $fails, [int]$delay)
  }

  # Kill any lingering java.exe for this label so the relaunch gets a fresh settings dir
  $ghosts = @(Get-CimInstance Win32_Process -Filter "Name = 'java.exe'" |
              Where-Object { $_.CommandLine -match $t.ProcMatch })
  foreach ($g in $ghosts) {
    WLog ("GHOST {0,-14} killing stuck java pid={1}" -f $t.Label, $g.ProcessId)
    if (-not $DryRun) { Stop-Process -Id $g.ProcessId -Force -ErrorAction SilentlyContinue }
  }
  if ($t.Label -eq 'FutuOpenD') {
    $futus = @(Get-CimInstance Win32_Process -Filter "Name = 'FutuOpenD.exe'")
    foreach ($g in $futus) {
      WLog ("GHOST FutuOpenD killing stuck pid={0}" -f $g.ProcessId)
      if (-not $DryRun) { Stop-Process -Id $g.ProcessId -Force -ErrorAction SilentlyContinue }
    }
  }

  WLog ("DOWN  {0,-14} port={1} -- restarting task '{2}'" -f $t.Label, $t.Port, $t.Task)
  if (-not $DryRun) {
    try   { Start-ScheduledTask -TaskName $t.Task }
    catch { WLog ("ERR   Start-ScheduledTask '{0}': {1}" -f $t.Task, $_.Exception.Message) }
  }
}

# Persist backoff state for the next tick.
Save-WatchdogState $wdState

# Also ensure the window-hider is running (it drops out if the last one exited)
$hiderRunning = @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" |
                  Where-Object { $_.CommandLine -match 'HideBrokerWindows' }).Count -gt 0
if (-not $hiderRunning) {
  WLog "HIDER not running -- restarting"
  if (-not $DryRun) {
    Start-Process -FilePath 'powershell.exe' `
      -ArgumentList '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\dashboard\deploy\nuc\HideBrokerWindows.ps1" -TimeoutSec 86400' `
      -WindowStyle Hidden | Out-Null
  }
}

# ---------- Adapt-SidecarHealth (Phase 4 Task 28) ----------
# Probes the four IBKR sidecars over mTLS gRPC via Probe-Sidecar.ps1 and
# restarts (Stop-ScheduledTask + Start-ScheduledTask on IBKRSidecar-<label>)
# any sidecar that reports BAD on two consecutive cycles.
#
# Skipped during the IBKR weekend reset window (Fri 23:00 ET -> Sat 03:00
# ET) because the underlying gateway is down by design and probing churns
# logs without adding signal. Daily resets do NOT trigger a skip — they're
# short enough that a real outage during one shouldn't be masked.

if (-not $inWeekend) {
  $sidecarLabels = @('isa-live', 'isa-paper', 'normal-live', 'normal-paper')
  foreach ($sLabel in $sidecarLabels) {
    if ($paused -contains "sidecar-$sLabel") {
      WLog ("SKIP  sidecar-{0} paused (paused-labels.txt)" -f $sLabel)
      continue
    }

    $probeScript = 'C:\dashboard\deploy\nuc\Probe-Sidecar.ps1'
    if (-not (Test-Path $probeScript)) {
      WLog ("SKIP  sidecar-{0} Probe-Sidecar.ps1 not found at {1}" -f $sLabel, $probeScript)
      continue
    }
    & $probeScript -Label $sLabel | Out-Null
    $probeExit = $LASTEXITCODE

    $badCountFile = "C:\dashboard\state\sidecar-$sLabel.badcount"
    if (Test-Path $badCountFile) {
      $bad = [int](Get-Content -Raw $badCountFile).Trim()
    } else {
      $bad = 0
    }

    if ($probeExit -eq 0) {
      if ($bad -ne 0) {
        WLog ("OK    sidecar-{0,-12} (badcount cleared)" -f $sLabel)
        Remove-Item -Force -ErrorAction SilentlyContinue $badCountFile
      } else {
        WLog ("OK    sidecar-{0,-12}" -f $sLabel)
      }
      continue
    }

    $bad = $bad + 1
    [System.IO.File]::WriteAllText($badCountFile, [string]$bad)

    # 2 consecutive bad ticks outside reset window -> restart. The sidecar's
    # own self-throttled backoff prevents a tight loop if relaunch keeps
    # failing.
    if ($bad -ge 2) {
      $taskName = "IBKRSidecar-$sLabel"
      WLog ("BAD   sidecar-{0,-12} 2 consecutive bad ticks -> restarting {1}" -f $sLabel, $taskName)
      if (-not $DryRun) {
        try {
          Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop
        } catch {
          WLog ("      Stop-ScheduledTask {0} failed: {1}" -f $taskName, $_.Exception.Message)
        }
        Start-Sleep -Seconds 2
        try {
          Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
          Remove-Item -Force -ErrorAction SilentlyContinue $badCountFile
        } catch {
          WLog ("      Start-ScheduledTask {0} failed: {1}" -f $taskName, $_.Exception.Message)
        }
      }
    } else {
      WLog ("BAD   sidecar-{0,-12} probe failed (count={1})" -f $sLabel, $bad)
    }
  }
}
