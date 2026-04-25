# One-shot health check after a logout/login cycle.
# Run from any shell (no admin needed): powershell -ExecutionPolicy Bypass -File verify-autostart.ps1
#
# Exits 0 if everything green, non-zero if any check fails.
# Intended wait before running: 3 minutes after logon so the 90s stagger + watchdog (+3min) all settled.

param(
  [switch]$Quiet  # suppress per-check prose, only print the final summary
)

$script:fails = 0
$script:warns = 0
function Ok   { param($m) if (-not $Quiet) { Write-Host ("  [ OK ] " + $m) -ForegroundColor Green } }
function Bad  { param($m) Write-Host ("  [FAIL] " + $m) -ForegroundColor Red;    $script:fails++ }
function Warn { param($m) Write-Host ("  [warn] " + $m) -ForegroundColor Yellow; $script:warns++ }
function Sec  { param($m) Write-Host ""; Write-Host ("== " + $m + " ==") -ForegroundColor Cyan }

$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
Write-Host ("boot: {0}  |  now: {1}  |  uptime: {2}" -f $boot, (Get-Date), ((Get-Date) - $boot))

# --------- 1. scheduled tasks ---------
Sec "Scheduled tasks"
# Task Scheduler normalizes ISO8601 durations (PT0S -> '', PT60S -> PT1M,
# PT90S -> PT1M30S). Expected values use the normalized form.
$expected = @(
  @{ Name='IBGateway-isa-live';     Delay=''        }
  @{ Name='IBGateway-isa-paper';    Delay='PT30S'   }
  @{ Name='IBGateway-normal-live';  Delay='PT1M'    }
  @{ Name='IBGateway-normal-paper'; Delay='PT1M30S' }
  @{ Name='FutuOpenDAutoStart';     Delay='PT5S'    }
  @{ Name='BrokerWindowsHider';     Delay='PT15S'   }
  @{ Name='BrokerTray';             Delay='PT20S'   }
  @{ Name='BrokerWatchdog';         Delay='PT3M'    }
)
foreach ($e in $expected) {
  $t = Get-ScheduledTask -TaskName $e.Name -ErrorAction SilentlyContinue
  if (-not $t) { Bad ("task missing: " + $e.Name); continue }
  $info = $t | Get-ScheduledTaskInfo
  $delay = $t.Triggers[0].Delay
  $lastRun = $info.LastRunTime
  $lastRes = $info.LastTaskResult
  $firedSinceBoot = $lastRun -gt $boot
  # Coerce both to strings — a $null delay (PT0S normalized) must compare
  # equal to an expected '' without tripping PowerShell's $null semantics.
  if (-not [string]::Equals([string]$delay, [string]$e.Delay)) {
    Warn ("{0}: delay is '{1}', expected '{2}'" -f $e.Name, $delay, $e.Delay)
  }
  if ($e.Name -eq 'BrokerWatchdog') {
    # Watchdog repeats; just check it's fired at least once since boot (delay is 3min).
    if ($firedSinceBoot) {
      Ok  ("{0}: last fired {1} (result=0x{2:X})" -f $e.Name, $lastRun, $lastRes)
    } else {
      # May not have fired yet if we're <3min after logon.
      Warn ("{0}: not yet fired since boot (wait until 3min after logon)" -f $e.Name)
    }
  } else {
    if (-not $firedSinceBoot) {
      Bad ("{0}: did NOT fire since boot (last={1})" -f $e.Name, $lastRun)
    } elseif ($lastRes -ne 0 -and $lastRes -ne 267009) {
      # 267009 = SCHED_S_TASK_RUNNING (Tray/Hider stay running => this is expected for them)
      Warn ("{0}: fired but result=0x{1:X}" -f $e.Name, $lastRes)
    } else {
      Ok ("{0}: fired {1} result=0x{2:X}" -f $e.Name, $lastRun, $lastRes)
    }
  }
}

# --------- 2. broker ports ---------
Sec "Broker ports (127.0.0.1)"
function Test-Port {
  param([int]$Port)
  $c = New-Object Net.Sockets.TcpClient
  try {
    $iar = $c.BeginConnect('127.0.0.1', $Port, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(1500)) { return $false }
    $c.EndConnect($iar); return $true
  } catch { return $false } finally { $c.Close() }
}
function Test-IBKRHandshake {
  param([int]$Port)
  $c = New-Object Net.Sockets.TcpClient
  try {
    $iar = $c.BeginConnect('127.0.0.1', $Port, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(1500)) { return 'down' }
    $c.EndConnect($iar)
    $s = $c.GetStream(); $s.ReadTimeout = 2000
    $s.Write([byte[]](0x41,0x50,0x49,0x00), 0, 4)
    $p = [Text.Encoding]::ASCII.GetBytes('v100..176') + @([byte]0)
    $lb = [BitConverter]::GetBytes([int32]$p.Length)
    if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($lb) }
    $s.Write($lb, 0, 4); $s.Write($p, 0, $p.Length); $s.Flush()
    $buf = New-Object byte[] 64
    try { $n = $s.Read($buf, 0, 64); return $(if ($n -gt 4) { 'up' } else { 'zombie' }) }
    catch { return 'zombie' }
  } catch { return 'down' } finally { $c.Close() }
}

$ibk = @(
  @{ Label='isa-live';     Port=4001 }
  @{ Label='isa-paper';    Port=4002 }
  @{ Label='normal-live';  Port=4003 }
  @{ Label='normal-paper'; Port=4004 }
)
foreach ($g in $ibk) {
  $state = Test-IBKRHandshake -Port $g.Port
  switch ($state) {
    'up'     { Ok  ("{0,-14} port={1} handshake=up"     -f $g.Label, $g.Port) }
    'zombie' { Bad ("{0,-14} port={1} ZOMBIE (TCP up, API unresponsive — broker conn lost?)" -f $g.Label, $g.Port) }
    'down'   { Bad ("{0,-14} port={1} DOWN"             -f $g.Label, $g.Port) }
  }
}
if (Test-Port 11111) { Ok  "FutuOpenD      port=11111 listening" }
else                 { Bad "FutuOpenD      port=11111 DOWN" }

# --------- 3. hider + tray + java procs ---------
Sec "Processes"
$hider = @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" |
           Where-Object { $_.CommandLine -match 'HideBrokerWindows' })
if ($hider.Count -ge 1) { Ok ("HideBrokerWindows running (pid={0})" -f $hider[0].ProcessId) }
else                    { Bad  "HideBrokerWindows NOT running — watchdog should respawn within 5min" }

$tray = @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" |
          Where-Object { $_.CommandLine -match 'BrokerTray' })
if ($tray.Count -ge 1) { Ok ("BrokerTray running (pid={0})" -f $tray[0].ProcessId) }
else                   { Warn "BrokerTray not running — tray icons will be missing" }

$javas = @(Get-CimInstance Win32_Process -Filter "Name = 'java.exe'" |
           Where-Object { $_.CommandLine -match 'gateway-(isa|normal)-(live|paper)' })
foreach ($lbl in @('isa-live','isa-paper','normal-live','normal-paper')) {
  $m = @($javas | Where-Object { $_.CommandLine -match ("gateway-" + $lbl) })
  if ($m.Count -eq 1) { Ok  ("java.exe for {0,-14} pid={1}" -f $lbl, $m[0].ProcessId) }
  elseif ($m.Count -eq 0) { Bad ("java.exe for {0} NOT running" -f $lbl) }
  else { Warn ("{0}: multiple java.exe instances ({1}) — ghost from prior session?" -f $lbl, $m.Count) }
}

$futu = @(Get-CimInstance Win32_Process -Filter "Name = 'FutuOpenD.exe'")
if ($futu.Count -ge 1) { Ok ("FutuOpenD.exe running (pid={0})" -f $futu[0].ProcessId) }
else                   { Bad  "FutuOpenD.exe NOT running" }

# --------- 4. no 2FA dialog stuck ---------
Sec "Second-factor dialogs"
$dialogs = Get-Process -ErrorAction SilentlyContinue |
           Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -match 'Second Factor Authentication' }
if ($dialogs) {
  foreach ($d in $dialogs) { Bad ("2FA dialog still open: pid={0} title='{1}'" -f $d.Id, $d.MainWindowTitle) }
  Write-Host "    -> TOTP filler probably didn't fire. Check C:\IBC\Logs\<label>\filler.log."
} else {
  Ok "no stuck Second Factor Authentication dialogs"
}

# --------- 5. taskbar hider worked ---------
# Enumerate all top-level windows (not just Get-Process.MainWindowHandle, which
# returns 0 once SW_HIDE has been applied). The goal is to fail only if any
# broker-titled window is currently visible on the taskbar.
Sec "Broker windows hidden from taskbar"
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public class WEnum {
  public delegate bool ED(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(ED f, IntPtr l);
  [DllImport("user32.dll")] public static extern int  GetWindowLong(IntPtr h, int i);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetParent(IntPtr h);
  [DllImport("user32.dll")] public static extern int  GetWindowTextLength(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
}
"@ -ErrorAction SilentlyContinue
$GWL_EXSTYLE = -20; $WS_EX_TOOLWINDOW = 0x80; $WS_EX_APPWINDOW = 0x00040000
$brokerTitleRegex = '^IBKR Gateway$|^IB Gateway$|^C:\\FutuOpenD\\FutuOpenD\.exe$'
$brokerWindows = New-Object System.Collections.Generic.List[object]
$cb = [WEnum+ED]{
  param($h, $l)
  if ([WEnum]::GetParent($h) -ne [IntPtr]::Zero) { return $true }
  $len = [WEnum]::GetWindowTextLength($h); if ($len -eq 0) { return $true }
  $sb = New-Object System.Text.StringBuilder ($len + 1)
  [WEnum]::GetWindowText($h, $sb, $sb.Capacity) | Out-Null
  $title = $sb.ToString()
  if ($title -notmatch $brokerTitleRegex) { return $true }
  $ex = [WEnum]::GetWindowLong($h, $GWL_EXSTYLE)
  $script:brokerWindows.Add([pscustomobject]@{
    Hwnd    = $h
    Title   = $title
    Tool    = (($ex -band $WS_EX_TOOLWINDOW) -ne 0)
    App     = (($ex -band $WS_EX_APPWINDOW)  -ne 0)
    Visible = [WEnum]::IsWindowVisible($h)
  })
  return $true
}
[WEnum]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
if ($brokerWindows.Count -eq 0) {
  Ok "no broker windows found at all (ibgateway/futu hid SW_HIDE-from-birth, or processes still starting)"
} else {
  $visible = @($brokerWindows | Where-Object { $_.Visible })
  $hidden  = @($brokerWindows | Where-Object { -not $_.Visible })
  if ($visible.Count -gt 0) {
    foreach ($w in $visible) { Bad ("VISIBLE: '{0}' hwnd={1} Tool={2} App={3}" -f $w.Title, $w.Hwnd, $w.Tool, $w.App) }
  }
  Ok ("{0} broker window(s) hidden, {1} visible" -f $hidden.Count, $visible.Count)
}

# --------- 6. per-label launch logs ---------
Sec "Per-label logs (today)"
$today = Get-Date -Format 'yyyyMMdd'
foreach ($lbl in @('isa-live','isa-paper','normal-live','normal-paper')) {
  $dir = "C:\IBC\Logs\$lbl"
  $launch = Join-Path $dir 'launch.log'
  if (-not (Test-Path $launch)) { Bad ("{0}: launch.log missing" -f $lbl); continue }
  $recent = Get-Content $launch -Tail 40 -ErrorAction SilentlyContinue
  $errors = @($recent | Where-Object { $_ -match 'ERROR|Exception|failed' -and $_ -notmatch 'SendKeys sent password' })
  if ($errors.Count -gt 0) {
    Warn ("{0}: launch.log has {1} error-ish line(s) in last 40:" -f $lbl, $errors.Count)
    $errors | Select-Object -First 3 | ForEach-Object { Write-Host ("        | " + $_) }
  } else {
    Ok ("{0}: launch.log clean (tail 40)" -f $lbl)
  }
  # Check filler log only for live accounts
  if ($lbl -match 'live') {
    $fill = Join-Path $dir 'filler.log'
    if (Test-Path $fill) {
      $ftail = Get-Content $fill -Tail 20 -ErrorAction SilentlyContinue
      $okLine = @($ftail | Where-Object { $_ -match 'sent code \+ ENTER|exit 0 \(success\)' }) | Select-Object -Last 1
      if ($okLine) { Ok ("{0}: filler.log shows TOTP fired ({1})" -f $lbl, $okLine.Trim()) }
      else         { Warn ("{0}: filler.log has no 'TOTP sent' line in last 20" -f $lbl) }
    } else {
      Warn ("{0}: filler.log missing (TOTP filler may not have run)" -f $lbl)
    }
  }
}

# --------- 7. Schwab health via nginx over WG ---------
Sec "Schwab health (VPS via WireGuard)"
try {
  [Net.ServicePointManager]::ServerCertificateValidationCallback = { param($a,$b,$c,$d) $true }
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
  $req = [Net.HttpWebRequest]::Create('https://10.10.0.1/api/schwab/health')
  $req.Host = 'dashboard.kiusinghung.com'
  $req.Timeout = 4000
  $req.Method = 'GET'
  $resp = $req.GetResponse()
  $body = (New-Object IO.StreamReader $resp.GetResponseStream()).ReadToEnd()
  $resp.Close()
  $j = $body | ConvertFrom-Json
  if ($j.configured -and $j.connected) {
    Ok ("Schwab: connected, refresh_token expires in {0}d" -f $j.expires_in_days)
  } elseif ($j.configured) {
    Warn "Schwab: configured but no refresh_token — visit /api/schwab/oauth-start to finish consent"
  } else {
    Warn "Schwab: not configured"
  }
} catch {
  Bad ("Schwab health check failed: " + $_.Exception.Message)
}

# --------- 8. watchdog recent log ---------
# Log file is date-stamped, not boot-stamped, so same file can hold entries
# from multiple boots on the same day. Filter to current-boot entries only.
Sec "Watchdog log (since boot)"
$wdLog = "C:\IBC\Logs\watchdog\watchdog-$today.log"
if (Test-Path $wdLog) {
  $bootDate = $boot.Date
  $sinceBoot = Get-Content $wdLog | Where-Object {
    if ($_ -match '^\[(\d\d):(\d\d):(\d\d)\]') {
      $t = $bootDate.AddHours([int]$matches[1]).AddMinutes([int]$matches[2]).AddSeconds([int]$matches[3])
      return $t -ge $boot
    }
    return $false
  }
  $bad = @($sinceBoot | Where-Object { $_ -match 'BAD|DOWN|GHOST|ERR' })
  if ($bad.Count -gt 0) {
    Warn ("watchdog flagged {0} issues since boot:" -f $bad.Count)
    $bad | Select-Object -Last 5 | ForEach-Object { Write-Host ("        | " + $_) }
  } else {
    $okCount = @($sinceBoot | Where-Object { $_ -match 'OK' }).Count
    Ok ("watchdog quiet since boot — {0} OK lines" -f $okCount)
  }
} else {
  Warn "watchdog log for today not found (task may not have fired yet; it runs at logon+3min)"
}

# --------- summary ---------
Write-Host ""
if ($fails -eq 0 -and $warns -eq 0) {
  Write-Host "ALL GREEN" -ForegroundColor Green
  exit 0
} elseif ($fails -eq 0) {
  Write-Host ("PASSED with {0} warning(s)" -f $warns) -ForegroundColor Yellow
  exit 0
} else {
  Write-Host ("FAILED: {0} error(s), {1} warning(s)" -f $fails, $warns) -ForegroundColor Red
  exit 1
}
