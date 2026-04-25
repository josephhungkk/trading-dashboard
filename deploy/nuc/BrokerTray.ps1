# Phase 4 layout decision (2026-04-25): EXTEND. The tray builds one Windows
# NotifyIcon per entry in $targets via `foreach ($t in $targets)`, so
# appending the four sidecar entries (Task 28) is straightforward. No
# fixed-grid layout to rewrite.
#
# 4 tray icons showing broker connection status.
# - FutuOpenD   (circle)   -> port 11111
# - Schwab      (diamond)  -> https://dashboard.kiusinghung.com/api/schwab/health (via origin direct)
# - IBKR Live   (filled sq) -> ports 4001 (isa) + 4003 (normal), aggregated
# - IBKR Paper  (empty sq)  -> ports 4002 (isa) + 4004 (normal), aggregated
#
# Status colours: green=all-up, yellow=partial, red=all-down, gray=not-configured.
# Tooltip shows per-account detail.
#
# Run hidden via scheduled task at logon (AtLogon, delay ~20s so brokers start first).

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$logDir = 'C:\IBC\Logs\tray'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
$logFile = Join-Path $logDir ("tray-{0}.log" -f (Get-Date -Format 'yyyyMMdd'))
function TLog { param([string]$m) Add-Content -Path $logFile -Value ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) }
TLog "==== tray start ===="

# ---------- drawing helpers ----------
# Each Draw-<shape> function returns a 16x16 Bitmap filled with the given status colour.
# Convert to Icon via GetHicon() at call site.

function New-Bitmap {
  $bmp = New-Object System.Drawing.Bitmap 16, 16, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  return $bmp
}

function Get-StatusColor {
  param([string]$s)
  switch ($s) {
    'up'          { return [System.Drawing.Color]::FromArgb(255,  46, 204, 113) }  # green
    'partial'     { return [System.Drawing.Color]::FromArgb(255, 241, 196,  15) }  # yellow
    'down'        { return [System.Drawing.Color]::FromArgb(255, 231,  76,  60) }  # red
    default       { return [System.Drawing.Color]::FromArgb(255, 149, 165, 166) }  # gray
  }
}

function Draw-Circle {
  param([string]$status)
  $bmp = New-Bitmap
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $brush = New-Object System.Drawing.SolidBrush(Get-StatusColor $status)
  $g.FillEllipse($brush, 1, 1, 14, 14)
  $g.DrawEllipse([System.Drawing.Pens]::Black, 1, 1, 14, 14)
  $brush.Dispose(); $g.Dispose()
  return $bmp
}

function Draw-Diamond {
  param([string]$status)
  $bmp = New-Bitmap
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $brush = New-Object System.Drawing.SolidBrush(Get-StatusColor $status)
  $pts = @(
    (New-Object System.Drawing.Point  8,  0),
    (New-Object System.Drawing.Point 15,  8),
    (New-Object System.Drawing.Point  8, 15),
    (New-Object System.Drawing.Point  0,  8)
  )
  $g.FillPolygon($brush, $pts)
  $g.DrawPolygon([System.Drawing.Pens]::Black, $pts)
  $brush.Dispose(); $g.Dispose()
  return $bmp
}

function Draw-SquareFilled {
  param([string]$status)
  $bmp = New-Bitmap
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $brush = New-Object System.Drawing.SolidBrush(Get-StatusColor $status)
  $g.FillRectangle($brush, 1, 1, 14, 14)
  $g.DrawRectangle([System.Drawing.Pens]::Black, 1, 1, 14, 14)
  $brush.Dispose(); $g.Dispose()
  return $bmp
}

function Draw-SquareEmpty {
  param([string]$status)
  $bmp = New-Bitmap
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $pen = New-Object System.Drawing.Pen ((Get-StatusColor $status), 2.5)
  $g.DrawRectangle($pen, 2, 2, 12, 12)
  $pen.Dispose(); $g.Dispose()
  return $bmp
}

# Point-up triangles for the two sidecar fleet icons. Filled = live pair
# (sidecar-isa-live + sidecar-normal-live), empty = paper pair. Mirrors the
# IBKR Live (filled square) / IBKR Paper (empty square) convention so live
# vs paper is visually consistent across the tray.
function Draw-TriangleFilled {
  param([string]$status)
  $bmp = New-Bitmap
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $brush = New-Object System.Drawing.SolidBrush(Get-StatusColor $status)
  $pts = @(
    (New-Object System.Drawing.Point  8,  1),
    (New-Object System.Drawing.Point 14, 14),
    (New-Object System.Drawing.Point  1, 14)
  )
  $g.FillPolygon($brush, $pts)
  $g.DrawPolygon([System.Drawing.Pens]::Black, $pts)
  $brush.Dispose(); $g.Dispose()
  return $bmp
}

function Draw-TriangleEmpty {
  param([string]$status)
  $bmp = New-Bitmap
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $pen = New-Object System.Drawing.Pen ((Get-StatusColor $status), 2.5)
  $pts = @(
    (New-Object System.Drawing.Point  8,  1),
    (New-Object System.Drawing.Point 14, 14),
    (New-Object System.Drawing.Point  1, 14)
  )
  $g.DrawPolygon($pen, $pts)
  $pen.Dispose(); $g.Dispose()
  return $bmp
}

# ---------- probes ----------

function Test-Port {
  param([int]$Port)
  $conn = New-Object Net.Sockets.TcpClient
  try {
    $iar = $conn.BeginConnect('127.0.0.1', $Port, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(800)) { return $false }
    $conn.EndConnect($iar); return $true
  } catch { return $false } finally { $conn.Close() }
}

# Deeper IBKR check - perform the TWS API version handshake.
# Returns 'up' if Gateway responds, 'zombie' if TCP accepts but API is unresponsive,
# 'down' if the port isn't even listening.
#
# Gateway can keep its listening socket open after losing its upstream connection
# to IBKR's servers, but typically stops responding to the API handshake in that
# state. A successful handshake is a much stronger signal than just port-open.
function Test-IBKRHandshake {
  param([int]$Port)
  $client = New-Object Net.Sockets.TcpClient
  try {
    $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(800)) { return 'down' }
    $client.EndConnect($iar)
    $stream = $client.GetStream()
    $stream.ReadTimeout = 1500
    # "API\0"  (prefix)
    $prefix = [byte[]](0x41, 0x50, 0x49, 0x00)
    $stream.Write($prefix, 0, 4)
    # length-prefixed "v100..176\0"
    $payload  = [Text.Encoding]::ASCII.GetBytes('v100..176') + @([byte]0)
    $lenBytes = [BitConverter]::GetBytes([int32]$payload.Length)
    if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($lenBytes) }
    $stream.Write($lenBytes, 0, 4)
    $stream.Write($payload, 0, $payload.Length)
    $stream.Flush()
    # Read response: expect 4-byte length + payload
    $buf = New-Object byte[] 64
    try {
      $n = $stream.Read($buf, 0, 64)
      if ($n -gt 4) { return 'up' }
      return 'zombie'  # socket accepted but no handshake response
    } catch {
      return 'zombie'
    }
  } catch { return 'down' }
  finally { $client.Close() }
}

# FutuOpenD upstream probe - mirrors BrokerWatchdog's Test-FutuConnected.
# Returns 'up'/'zombie'/'down' so the tray can show yellow on lost upstream
# instead of falsely flashing green when the local port is still listening.
function Test-FutuConnected {
  if (-not (Test-Port 11111)) { return 'down' }
  $futu = @(Get-Process -Name 'FutuOpenD' -ErrorAction SilentlyContinue)
  if ($futu.Count -eq 0) { return 'down' }
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

# Fetch the authoritative per-adapter connection status from the backend.
# The BrokerRegistry on the VPS owns the actual ib_async connections, so
# its `connected` flag is what ends up on the user's screen - exactly
# what we want the tray to reflect. Hits the same WG -> nginx -> backend
# path the user's browser uses, so the tray is honest about whether the
# dashboard is actually working.
#
# Classify an HTTP failure into a short, user-actionable label.
# Distinguishes "VPS actually unreachable" (TCP / timeout / DNS) from
# "nginx reachable but backend down" (HTTP 502/503/504) from auth
# problems (401/403) from backend errors (other 5xx) and from 4xx.
# Returns @{ Kind; Tip } where Kind is one of:
#   unreachable | backend_not_ready | auth | backend_error |
#   client_error | unknown
function Classify-HttpError {
  param($Exception)
  # PowerShell wraps .NET method-call exceptions in MethodInvocationException
  # whose Message is "Exception calling \"GetResponse\" with \"0\" argument(s):".
  # Walk the InnerException chain to find the real WebException so the tooltip
  # surfaces the actual cause instead of that wrapper noise.
  $probe = $Exception
  for ($i = 0; $i -lt 5 -and $probe -ne $null; $i++) {
    if ($probe -is [Net.WebException]) { break }
    $probe = $probe.InnerException
  }
  if ($probe -is [Net.WebException]) { $Exception = $probe }
  if ($Exception -is [Net.WebException]) {
    $status = $Exception.Status
    # TCP / DNS / timeout - VPS or nginx really unreachable.
    switch ($status) {
      'ConnectFailure'       { return @{ Kind = 'unreachable'; Tip = 'VPS unreachable (TCP connect failed)' } }
      'NameResolutionFailure'{ return @{ Kind = 'unreachable'; Tip = 'VPS unreachable (DNS failure)' } }
      'Timeout'              { return @{ Kind = 'unreachable'; Tip = 'VPS unreachable (request timed out)' } }
      'SendFailure'          { return @{ Kind = 'unreachable'; Tip = 'VPS unreachable (socket error)' } }
      'TrustFailure'         { return @{ Kind = 'unreachable'; Tip = 'VPS unreachable (TLS trust failure)' } }
    }
    # nginx responded but with a non-2xx status.
    $resp = $Exception.Response
    if ($resp -ne $null) {
      $code = [int]$resp.StatusCode
      if ($code -in 502, 503, 504) {
        return @{ Kind = 'backend_not_ready'; Tip = ("Backend not ready (HTTP {0})" -f $code) }
      }
      if ($code -in 401, 403) {
        return @{ Kind = 'auth'; Tip = ("Auth rejected (HTTP {0})" -f $code) }
      }
      if ($code -ge 500) {
        return @{ Kind = 'backend_error'; Tip = ("Backend error (HTTP {0})" -f $code) }
      }
      if ($code -ge 400) {
        return @{ Kind = 'client_error'; Tip = ("Request rejected (HTTP {0})" -f $code) }
      }
    }
  }
  # Catchall - walk to the innermost exception so users see the real cause,
  # not "Exception calling 'GetResponse' with '0' argument(s)...".
  $deepest = $Exception
  for ($i = 0; $i -lt 5 -and $deepest.InnerException -ne $null; $i++) {
    $deepest = $deepest.InnerException
  }
  $msg = $deepest.Message
  if ($msg.Length -gt 80) { $msg = $msg.Substring(0, 80) + '...' }
  return @{ Kind = 'unknown'; Tip = $msg }
}

# Cached for 5s so IBKR Live + IBKR Paper probes (which both read this
# endpoint) don't each make a separate HTTP call each tick.
$script:AccountsCache = @{ Accounts = $null; Reachable = $false; Error = $null; ErrorKind = $null; TickMs = 0 }
function Get-BrokerAccounts {
  $now = [Environment]::TickCount
  if ($script:AccountsCache.TickMs -gt 0 -and ($now - $script:AccountsCache.TickMs) -lt 5000) {
    return $script:AccountsCache
  }
  $result = @{ Accounts = $null; Reachable = $false; Error = $null; ErrorKind = $null; TickMs = $now }
  try {
    [Net.ServicePointManager]::ServerCertificateValidationCallback = { param($a,$b,$c,$d) $true }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
    $req = [Net.HttpWebRequest]::Create('https://10.10.0.1/api/brokers/accounts')
    $req.Host = 'dashboard.kiusinghung.com'
    $req.Timeout = 3000
    $req.Method  = 'GET'
    $resp = $req.GetResponse()
    $reader = New-Object IO.StreamReader $resp.GetResponseStream()
    $body = $reader.ReadToEnd(); $reader.Dispose(); $resp.Close()
    $parsed = $body | ConvertFrom-Json
    # Backend shape: { accounts: [{broker,label,mode,connected}, ...] }
    $result.Accounts  = @($parsed.accounts)
    $result.Reachable = $true
  } catch {
    $cls = Classify-HttpError $_.Exception
    $result.Error = $cls.Tip
    $result.ErrorKind = $cls.Kind
  }
  $script:AccountsCache = $result
  return $result
}

function Test-Schwab {
  # Hit the VPS nginx directly over WireGuard (10.10.0.1) with a Host header
  # so nginx routes to the dashboard vhost. Cert CN=dashboard.kiusinghung.com
  # won't match 10.10.0.1 - we accept any cert. Cloudflare is NOT in this path.
  #
  # Reachable vs. Configured are separate dimensions. When the VPS is
  # unreachable (WireGuard not up yet post-boot, nginx restarting, ...)
  # the tray used to flatten that into "not configured", which is a
  # misleading message - the secrets are fine, we just can't see them.
  # The caller now distinguishes the two cases and surfaces the right
  # tooltip.
  try {
    [Net.ServicePointManager]::ServerCertificateValidationCallback = { param($a,$b,$c,$d) $true }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
    $req = [Net.HttpWebRequest]::Create('https://10.10.0.1/api/schwab/health')
    $req.Host = 'dashboard.kiusinghung.com'
    $req.Timeout = 3000
    $req.Method  = 'GET'
    $resp = $req.GetResponse()
    $reader = New-Object IO.StreamReader $resp.GetResponseStream()
    $body = $reader.ReadToEnd(); $reader.Dispose(); $resp.Close()
    $r = $body | ConvertFrom-Json
    return @{
      Reachable  = $true
      Configured = $r.configured
      Connected  = $r.connected
      ExpiresDays = $r.expires_in_days
    }
  } catch {
    $cls = Classify-HttpError $_.Exception
    return @{
      Reachable  = $false
      Configured = $false
      Connected  = $false
      ExpiresDays = $null
      Error      = $cls.Tip
      ErrorKind  = $cls.Kind
    }
  }
}

# ---------- targets ----------

$targets = @(
  @{
    Name = 'FutuOpenD'; Shape = { param($s) Draw-Circle $s }
    Probe = {
      # Combines the local upstream check (OpenD has its 443 link out
      # to Futu's servers) with the backend-registry view (the Futu
      # trade adapter has successfully unlocked over the encrypted
      # channel). "Green" requires BOTH - local OpenD alone is not
      # enough to know the dashboard can actually trade.
      $localState = Test-FutuConnected
      $r = Get-BrokerAccounts

      if (-not $r.Reachable) {
        $err = if ($r.Error) { $r.Error } else { 'unknown' }
        return @{ Status = 'partial'; Tip = ("FutuOpenD local={0}; {1}" -f $localState, $err) }
      }

      $futu = @($r.Accounts | Where-Object { $_.broker -eq 'futu' })
      # Must wrap in @(...) - PowerShell unrolls a single-element Where-Object
      # result to a scalar PSCustomObject, whose .Count returns $null (not 1),
      # so `$null -gt 0` is $false and we'd falsely flag backend as disconnected.
      $backendConnected = ($futu.Count -gt 0) -and (@($futu | Where-Object { $_.connected }).Count -gt 0)

      # 4 combos of (local, backend):
      #   up + backendConnected -> green, fully trading
      #   up + NOT backend     -> partial, trade adapter not yet ready
      #   zombie + anything    -> partial, local link broken
      #   down + anything      -> red
      if ($localState -eq 'up' -and $backendConnected) {
        return @{ Status = 'up'; Tip = 'FutuOpenD: UP (local + backend trade ctx connected)' }
      }
      if ($localState -eq 'up' -and -not $backendConnected) {
        if ($futu.Count -eq 0) {
          return @{ Status = 'partial'; Tip = 'FutuOpenD: UP locally, backend trade adapter disabled (set futu.trade_enabled=true once OpenD has the RSA public key)' }
        }
        return @{ Status = 'partial'; Tip = 'FutuOpenD: UP locally, backend trade ctx NOT connected (check RSA key / unlock PIN)' }
      }
      if ($localState -eq 'zombie') {
        return @{ Status = 'partial'; Tip = 'FutuOpenD: ZOMBIE (port open, no upstream to Futu)' }
      }
      return @{ Status = 'down'; Tip = 'FutuOpenD: DOWN (local process not listening on 11111)' }
    }
  }
  @{
    Name = 'Schwab'; Shape = { param($s) Draw-Diamond $s }
    Probe = {
      $r = Test-Schwab
      # Reachable==false means WireGuard down, VPS unreachable, or
      # nginx restarting - NOT a missing secret. Show partial (yellow)
      # with a tooltip that points at the real cause so the user
      # doesn't chase a ghost OAuth issue.
      if (-not $r.Reachable) {
        $err = if ($r.Error) { $r.Error } else { 'unknown' }
        return @{ Status = 'partial'; Tip = ("Schwab: {0}" -f $err) }
      }
      if (-not $r.Configured) { return @{ Status = 'gray'; Tip = 'Schwab: not configured (set schwab.app_key / app_secret in Settings)' } }
      if (-not $r.Connected)  { return @{ Status = 'down'; Tip = 'Schwab: configured, no refresh_token yet (visit /api/schwab/oauth-start)' } }
      $days = if ($r.ExpiresDays -ne $null) { [math]::Round($r.ExpiresDays, 1) } else { '?' }
      $st   = if ($r.ExpiresDays -ne $null -and $r.ExpiresDays -lt 1) { 'partial' } else { 'up' }
      return @{ Status = $st; Tip = ("Schwab: connected (refresh_token expires in {0}d)" -f $days) }
    }
  }
  @{
    Name = 'IBKR Live'; Shape = { param($s) Draw-SquareFilled $s }
    Probe = {
      # Single source of truth: the backend registry's view. A gateway
      # that passes the local Test-IBKRHandshake but refuses API
      # connections from 10.10.0.1 (wrong Trusted-IP list) would have
      # shown green on the old local-only probe while the user's
      # dashboard was actually getting nothing. Backend-connected
      # means user-visible-connected, full stop.
      $r = Get-BrokerAccounts
      if (-not $r.Reachable) {
        $err = if ($r.Error) { $r.Error } else { 'unknown' }
        return @{ Status = 'partial'; Tip = ("IBKR Live: {0}" -f $err) }
      }
      $live = @($r.Accounts | Where-Object { $_.broker -eq 'ibkr' -and $_.mode -eq 'live' })
      if ($live.Count -eq 0) { return @{ Status = 'gray'; Tip = 'IBKR Live: no adapters registered' } }
      $connected = @($live | Where-Object { $_.connected })
      $status = if ($connected.Count -eq $live.Count) { 'up' }
                elseif ($connected.Count -eq 0)        { 'down' }
                else                                   { 'partial' }
      $detail = ($live | ForEach-Object {
        $flag = if ($_.connected) { 'up' } else { 'down' }
        "{0}={1}" -f $_.label, $flag
      }) -join ' '
      return @{ Status = $status; Tip = ("IBKR Live  {0}" -f $detail) }
    }
  }
  @{
    Name = 'IBKR Paper'; Shape = { param($s) Draw-SquareEmpty $s }
    Probe = {
      $r = Get-BrokerAccounts
      if (-not $r.Reachable) {
        $err = if ($r.Error) { $r.Error } else { 'unknown' }
        return @{ Status = 'partial'; Tip = ("IBKR Paper: {0}" -f $err) }
      }
      $paper = @($r.Accounts | Where-Object { $_.broker -eq 'ibkr' -and $_.mode -eq 'paper' })
      if ($paper.Count -eq 0) { return @{ Status = 'gray'; Tip = 'IBKR Paper: no adapters registered' } }
      $connected = @($paper | Where-Object { $_.connected })
      $status = if ($connected.Count -eq $paper.Count) { 'up' }
                elseif ($connected.Count -eq 0)         { 'down' }
                else                                    { 'partial' }
      $detail = ($paper | ForEach-Object {
        $flag = if ($_.connected) { 'up' } else { 'down' }
        "{0}={1}" -f $_.label, $flag
      }) -join ' '
      return @{ Status = $status; Tip = ("IBKR Paper {0}" -f $detail) }
    }
  }
  # ---- Phase 4 sidecar fleet (Task 28). Two icons aggregate the four
  #      sidecars by mode: live pair (filled triangle) = isa-live + normal-
  #      live, paper pair (empty triangle) = isa-paper + normal-paper.
  #      Mirrors the IBKR Live / Paper filled-vs-empty convention so the
  #      live/paper distinction is visually consistent across the tray.
  #      Status sourced from C:\dashboard\state\sidecar-<label>.health,
  #      written by Probe-Sidecar.ps1 under BrokerWatchdog.
  @{
    Name = 'Sidecar Live'; Shape = { param($s) Draw-TriangleFilled $s }
    Probe = { Read-SidecarPair -Labels @('isa-live', 'normal-live') -Mode 'Live' }
  }
  @{
    Name = 'Sidecar Paper'; Shape = { param($s) Draw-TriangleEmpty $s }
    Probe = { Read-SidecarPair -Labels @('isa-paper', 'normal-paper') -Mode 'Paper' }
  }
)

function Read-SidecarHealth {
  param([Parameter(Mandatory)][string]$Label)
  $healthFile = "C:\dashboard\state\sidecar-$Label.health"
  if (-not (Test-Path $healthFile)) {
    return @{ Status = 'gray'; Tip = "Sidecar $Label : no health file yet" }
  }
  try {
    $h = Get-Content -Raw $healthFile | ConvertFrom-Json
  } catch {
    return @{ Status = 'down'; Tip = "Sidecar $Label : malformed .health file" }
  }
  $trayStatus = switch ($h.status) {
    'up'       { 'up' }
    'degraded' { 'partial' }
    'down'     { 'down' }
    default    { 'gray' }
  }
  $tip = "Sidecar $Label : $($h.status) (probed $($h.last_probe_at))"
  return @{ Status = $trayStatus; Tip = $tip }
}

# Aggregate two sidecars (e.g. isa-live + normal-live) into one tray status.
# Logic mirrors the IBKR Live / Paper rollup:
#   both up                 -> green
#   one up, one not-up      -> partial (yellow)
#   both down               -> down (red)
#   either gray and the other not 'up' -> gray (not yet probed)
# Tip lists both sub-statuses so hovering the icon shows what's actually wrong.
function Read-SidecarPair {
  param(
    [Parameter(Mandatory)][string[]]$Labels,
    [Parameter(Mandatory)][string]$Mode
  )
  $sub = $Labels | ForEach-Object { Read-SidecarHealth -Label $_ }
  $statuses = $sub | ForEach-Object { $_.Status }
  $up = @($statuses | Where-Object { $_ -eq 'up' }).Count
  $down = @($statuses | Where-Object { $_ -eq 'down' }).Count
  $gray = @($statuses | Where-Object { $_ -eq 'gray' }).Count

  $rollup = if ($up -eq $Labels.Count) { 'up' }
            elseif ($down -eq $Labels.Count) { 'down' }
            elseif ($gray -gt 0 -and $up -eq 0) { 'gray' }
            else { 'partial' }

  $detail = for ($i = 0; $i -lt $Labels.Count; $i++) {
    "{0}={1}" -f $Labels[$i], $statuses[$i]
  }
  return @{ Status = $rollup; Tip = ("Sidecar {0}: {1}" -f $Mode, ($detail -join ' ')) }
}

# ---------- context menu actions ----------

$dashboardUrl = 'https://dashboard.kiusinghung.com'

function Invoke-OpenDashboard { Start-Process $dashboardUrl | Out-Null }

function Invoke-OpenLog {
  if (Test-Path $logFile) { Start-Process notepad.exe $logFile | Out-Null }
  else { Start-Process explorer.exe $logDir | Out-Null }
}

function Invoke-Exit {
  TLog "tray exit via menu"
  [System.Windows.Forms.Application]::Exit()
}

function Invoke-RestartTasks {
  param([string[]]$TaskNames, [string]$Label)
  TLog ("menu: restart {0}" -f $Label)
  foreach ($n in $TaskNames) {
    try {
      & schtasks.exe /End /TN $n 2>$null | Out-Null
      Start-Sleep -Milliseconds 500
      Start-ScheduledTask -TaskName $n -ErrorAction Stop
      TLog ("  restarted: {0}" -f $n)
    } catch {
      TLog ("  FAILED restart {0}: {1}" -f $n, $_.Exception.Message)
    }
  }
  # Force an immediate icon refresh so the user sees the effect.
  Update-Icons
}

# Per-target menu specs. Each entry is { Label, Action (scriptblock) }.
# Shared footer (Open Dashboard / View log / Exit) is appended by the
# icon-build loop so every tray icon has the same bottom three items.
$targetMenus = @{
  'FutuOpenD' = @(
    @{ Label = 'Restart FutuOpenD'; Action = { Invoke-RestartTasks @('FutuOpenDAutoStart') 'FutuOpenD' } }
  )
  'Schwab' = @(
    @{ Label = 'Re-authorize (OAuth flow)'; Action = { Start-Process "$dashboardUrl/api/schwab/oauth-start" | Out-Null } }
  )
  'IBKR Live' = @(
    @{ Label = 'Restart isa-live';    Action = { Invoke-RestartTasks @('IBGateway-isa-live')    'isa-live' } }
    @{ Label = 'Restart normal-live'; Action = { Invoke-RestartTasks @('IBGateway-normal-live') 'normal-live' } }
    @{ Label = 'Restart BOTH live';   Action = { Invoke-RestartTasks @('IBGateway-isa-live','IBGateway-normal-live') 'both-live' } }
  )
  'IBKR Paper' = @(
    @{ Label = 'Restart isa-paper';    Action = { Invoke-RestartTasks @('IBGateway-isa-paper')    'isa-paper' } }
    @{ Label = 'Restart normal-paper'; Action = { Invoke-RestartTasks @('IBGateway-normal-paper') 'normal-paper' } }
    @{ Label = 'Restart BOTH paper';   Action = { Invoke-RestartTasks @('IBGateway-isa-paper','IBGateway-normal-paper') 'both-paper' } }
  )
  # Phase 4 sidecar triangles. Restart options mirror the IBKR Live/Paper
  # split, but kick the IBKRSidecar-<label> scheduled tasks (registered by
  # deploy/nuc/register-ibkr-sidecar.ps1) instead of the gateway tasks.
  'Sidecar Live' = @(
    @{ Label = 'Restart sidecar isa-live';    Action = { Invoke-RestartTasks @('IBKRSidecar-isa-live')    'sidecar-isa-live' } }
    @{ Label = 'Restart sidecar normal-live'; Action = { Invoke-RestartTasks @('IBKRSidecar-normal-live') 'sidecar-normal-live' } }
    @{ Label = 'Restart BOTH live sidecars';  Action = { Invoke-RestartTasks @('IBKRSidecar-isa-live','IBKRSidecar-normal-live') 'sidecar-both-live' } }
  )
  'Sidecar Paper' = @(
    @{ Label = 'Restart sidecar isa-paper';    Action = { Invoke-RestartTasks @('IBKRSidecar-isa-paper')    'sidecar-isa-paper' } }
    @{ Label = 'Restart sidecar normal-paper'; Action = { Invoke-RestartTasks @('IBKRSidecar-normal-paper') 'sidecar-normal-paper' } }
    @{ Label = 'Restart BOTH paper sidecars';  Action = { Invoke-RestartTasks @('IBKRSidecar-isa-paper','IBKRSidecar-normal-paper') 'sidecar-both-paper' } }
  )
}

function New-ContextMenu {
  param([string]$Name)
  $menu = New-Object System.Windows.Forms.ContextMenuStrip

  $items = if ($targetMenus.ContainsKey($Name)) { $targetMenus[$Name] } else { @() }
  foreach ($m in $items) {
    $mi = $menu.Items.Add($m.Label)
    $action = $m.Action
    $mi.Add_Click({ & $action }.GetNewClosure())
  }

  if ($targetMenus.ContainsKey($Name)) {
    $menu.Items.Add('-') | Out-Null   # separator
  }

  $dash = $menu.Items.Add('Open dashboard')
  $dash.Add_Click({ Invoke-OpenDashboard })
  $log = $menu.Items.Add('View tray log')
  $log.Add_Click({ Invoke-OpenLog })
  $menu.Items.Add('-') | Out-Null
  $exit = $menu.Items.Add('Exit tray')
  $exit.Add_Click({ Invoke-Exit })
  return $menu
}

# ---------- build NotifyIcons ----------

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class IconUtil {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool DestroyIcon(IntPtr hIcon);
}
"@

foreach ($t in $targets) {
  $ni = New-Object System.Windows.Forms.NotifyIcon
  # Set a placeholder icon BEFORE Visible=true so Windows never shows the
  # default powershell/blank icon in the notification area during the first
  # probe cycle. The real status colour is applied by the first Update-Icons.
  $bmp = & $t.Shape 'default'
  $hIcon = $bmp.GetHicon()
  $ni.Icon = [System.Drawing.Icon]::FromHandle($hIcon)
  $ni.Text = $t.Name
  $t.LastHIcon = $hIcon
  $bmp.Dispose()
  $ni.ContextMenuStrip = New-ContextMenu -Name $t.Name
  # Double-click = open dashboard. Common Windows convention for tray icons.
  $ni.Add_MouseDoubleClick({ Invoke-OpenDashboard })
  $ni.Visible = $true
  $t.NI = $ni
}

function Update-Icons {
  foreach ($t in $targets) {
    try {
      $r = & $t.Probe
      $bmp = & $t.Shape $r.Status
      $hIcon = $bmp.GetHicon()
      $icon = [System.Drawing.Icon]::FromHandle($hIcon)
      $t.NI.Icon = $icon
      $t.NI.Text = if ($r.Tip.Length -gt 63) { $r.Tip.Substring(0, 63) } else { $r.Tip }  # NotifyIcon text limit
      if ($t.LastHIcon -ne [IntPtr]::Zero) { [IconUtil]::DestroyIcon($t.LastHIcon) | Out-Null }
      $t.LastHIcon = $hIcon
      $bmp.Dispose()
    } catch {
      TLog ("probe/draw error for {0}: {1}" -f $t.Name, $_.Exception.Message)
    }
  }
}

# ---------- run loop ----------

Update-Icons
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 10000   # 10s
$timer.Add_Tick({ Update-Icons })
$timer.Start()

TLog "tray running - Application.Run"
[System.Windows.Forms.Application]::Run()

# Cleanup (only hit if Application.Exit called)
foreach ($t in $targets) {
  $t.NI.Visible = $false
  $t.NI.Dispose()
  if ($t.LastHIcon -ne [IntPtr]::Zero) { [IconUtil]::DestroyIcon($t.LastHIcon) | Out-Null }
}
TLog "tray exited"
