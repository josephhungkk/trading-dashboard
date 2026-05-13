# Phase 4 layout decision (2026-04-25): EXTEND. The tray builds one Windows
# NotifyIcon per entry in $targets via `foreach ($t in $targets)`, so
# appending the four sidecar entries (Task 28) is straightforward. No
# fixed-grid layout to rewrite.
#
# 6 tray icons showing broker connection status.
# - FutuOpenD   (circle)     -> port 11111 + backend futu.connected
# - Schwab      (diamond)    -> /api/admin/brokers/schwab/status
# - IBKR Live   (filled sq)  -> ports 4001 (isa) + 4003 (normal), aggregated
# - IBKR Paper  (empty sq)   -> ports 4002 (isa) + 4004 (normal), aggregated
# - Alpaca Live  (filled hex) -> backend alpaca.live.connected (sidecar on VPS)
# - Alpaca Paper (empty hex)  -> backend alpaca.paper.connected (sidecar on VPS)
#
# Status colours: green=all-up, yellow=partial, red=all-down, gray=not-configured.
# Tooltip shows per-account detail.
#
# Run hidden via scheduled task at logon (AtLogon, delay ~20s so brokers start first).

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# Sidecar health helpers (Read-SidecarHealth, Read-SidecarPair) live in
# lib/SidecarLib.ps1 so deploy/nuc/tests/SidecarLib.Tests.ps1 (Pester) can
# load + exercise them in isolation without firing Application.Run.
. (Join-Path $PSScriptRoot 'lib\SidecarLib.ps1')

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

# Pointy-top hexagons for the Alpaca pair. Filled = live, empty = paper.
# Mirrors the IBKR Live/Paper (square) and Futu (triangle) shape conventions
# so each broker family gets a distinct silhouette.
$script:_alpacaHexPts = @(
  (New-Object System.Drawing.Point  8,  1),
  (New-Object System.Drawing.Point 14,  5),
  (New-Object System.Drawing.Point 14, 11),
  (New-Object System.Drawing.Point  8, 15),
  (New-Object System.Drawing.Point  2, 11),
  (New-Object System.Drawing.Point  2,  5)
)

function Draw-HexagonFilled {
  param([string]$status)
  $bmp = New-Bitmap
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $brush = New-Object System.Drawing.SolidBrush(Get-StatusColor $status)
  $g.FillPolygon($brush, $script:_alpacaHexPts)
  $g.DrawPolygon([System.Drawing.Pens]::Black, $script:_alpacaHexPts)
  $brush.Dispose(); $g.Dispose()
  return $bmp
}

function Draw-HexagonEmpty {
  param([string]$status)
  $bmp = New-Bitmap
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $pen = New-Object System.Drawing.Pen ((Get-StatusColor $status), 2.5)
  $g.DrawPolygon($pen, $script:_alpacaHexPts)
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

# CF Access service-token loader. Tray hits the public CF path
# (https://dashboard.kiusinghung.com/...) with CF-Access-Client-Id +
# CF-Access-Client-Secret headers; backend's require_admin_jwt accepts the
# resulting service-token JWT (kind=service_token), same path CI uses.
#
# Resolution order:
#   1. $env:CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET (interactive operator
#      runs only - the Scheduled Task user context typically has neither).
#   2. C:\dashboard\secrets\cf-access-tray.env (key=value lines:
#         CF_ACCESS_CLIENT_ID=<token>.access
#         CF_ACCESS_CLIENT_SECRET=<secret>)
$script:CFHeaders = $null
function Get-CFAccessHeaders {
  if ($script:CFHeaders -ne $null) { return $script:CFHeaders }
  $clientId = $env:CF_ACCESS_CLIENT_ID
  $clientSecret = $env:CF_ACCESS_CLIENT_SECRET
  $envFile = 'C:\dashboard\secrets\cf-access-tray.env'
  if ((-not $clientId -or -not $clientSecret) -and (Test-Path $envFile)) {
    Get-Content $envFile | ForEach-Object {
      if ($_ -match '^\s*CF_ACCESS_CLIENT_ID\s*=\s*(.+?)\s*$') { $clientId = $matches[1] }
      elseif ($_ -match '^\s*CF_ACCESS_CLIENT_SECRET\s*=\s*(.+?)\s*$') { $clientSecret = $matches[1] }
    }
  }
  if (-not $clientId -or -not $clientSecret) { return $null }
  $script:CFHeaders = @{ Id = $clientId; Secret = $clientSecret }
  return $script:CFHeaders
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
  $cf = Get-CFAccessHeaders
  if (-not $cf) {
    $result.Error = 'CF service token not configured (drop creds in C:\dashboard\secrets\cf-access-tray.env)'
    $result.ErrorKind = 'auth'
    $script:AccountsCache = $result
    return $result
  }
  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
    $req = [Net.HttpWebRequest]::Create('https://dashboard.kiusinghung.com/api/brokers/accounts')
    $req.Headers.Add('CF-Access-Client-Id', $cf.Id)
    $req.Headers.Add('CF-Access-Client-Secret', $cf.Secret)
    $req.Timeout = 5000
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
  # Phase 7a: probes /api/admin/brokers/schwab/status (the only schwab status
  # endpoint that ships in v0.7.0 - the legacy /api/schwab/health does not
  # exist). Returns:
  #   { access_token_issued_at, refresh_token_issued_at,
  #     tier2_refresh_enabled, tier2_consecutive_failures }
  # Connected ::= refresh_token_issued_at is set. ExpiresDays derived from
  # the 168h Schwab refresh-token TTL (memory phase7a_schwab_topology.md).
  $cf = Get-CFAccessHeaders
  if (-not $cf) {
    return @{
      Reachable  = $false
      Configured = $false
      Connected  = $false
      ExpiresDays = $null
      Tier2Enabled = $null
      Tier2Failures = $null
      Error      = 'CF service token not configured (drop creds in C:\dashboard\secrets\cf-access-tray.env)'
      ErrorKind  = 'auth'
    }
  }
  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
    $req = [Net.HttpWebRequest]::Create('https://dashboard.kiusinghung.com/api/admin/brokers/schwab/status')
    $req.Headers.Add('CF-Access-Client-Id', $cf.Id)
    $req.Headers.Add('CF-Access-Client-Secret', $cf.Secret)
    $req.Timeout = 5000
    $req.Method  = 'GET'
    $resp = $req.GetResponse()
    $reader = New-Object IO.StreamReader $resp.GetResponseStream()
    $body = $reader.ReadToEnd(); $reader.Dispose(); $resp.Close()
    $r = $body | ConvertFrom-Json
    # Endpoint is admin-only and 200 implies the broker secret block exists,
    # so reachable+200 == configured. Connected = a refresh_token has been
    # minted at least once.
    $connected = $false
    $expiresDays = $null
    if ($r.refresh_token_issued_at) {
      try {
        $issued = [DateTime]::Parse(
          $r.refresh_token_issued_at,
          [Globalization.CultureInfo]::InvariantCulture,
          ([Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal)
        )
        $ageHours = ((Get-Date).ToUniversalTime() - $issued).TotalHours
        $expiresDays = (168 - $ageHours) / 24.0
        $connected = $true
      } catch {
        $connected = $true  # token exists, age unparseable - still connected
      }
    }
    return @{
      Reachable  = $true
      Configured = $true
      Connected  = $connected
      ExpiresDays = $expiresDays
      Tier2Enabled = $r.tier2_refresh_enabled
      Tier2Failures = $r.tier2_consecutive_failures
    }
  } catch {
    $cls = Classify-HttpError $_.Exception
    return @{
      Reachable  = $false
      Configured = $false
      Connected  = $false
      ExpiresDays = $null
      Tier2Enabled = $null
      Tier2Failures = $null
      Error      = $cls.Tip
      ErrorKind  = $cls.Kind
    }
  }
}

# Combined IBKR pair status: gateway handshake + sidecar health + backend
# registry, rolled up like FutuOpenD's probe (one icon for everything in the
# trade path). $Mode = 'live' or 'paper'. $GatewayPorts maps sidecar label
# to the IBGateway TCP port handshake target (per Phase 4 port map).
function Get-IbkrPairStatus {
  param(
    [Parameter(Mandatory)][string]$Mode,
    [Parameter(Mandatory)][hashtable]$GatewayPorts
  )
  $modeLabel = $Mode.Substring(0, 1).ToUpper() + $Mode.Substring(1)

  # 1) Per-gateway TWS API handshake.
  $gw = @{}
  foreach ($lbl in $GatewayPorts.Keys) {
    $gw[$lbl] = Test-IBKRHandshake -Port $GatewayPorts[$lbl]
  }

  # 2) Per-sidecar health.
  $side = @{}
  foreach ($lbl in $GatewayPorts.Keys) {
    $side[$lbl] = (Read-SidecarHealth -Label $lbl).Status
  }

  # 3) Backend registry view (cached 5s by Get-BrokerAccounts).
  $r = Get-BrokerAccounts
  $beReachable = $r.Reachable
  $be = @{}
  if ($beReachable) {
    $rows = @($r.Accounts | Where-Object {
      $_.broker -eq 'ibkr' -and $_.mode -eq $Mode -and $GatewayPorts.ContainsKey($_.label)
    })
    foreach ($lbl in $GatewayPorts.Keys) {
      $row = @($rows | Where-Object { $_.label -eq $lbl })
      $be[$lbl] = if ($row.Count -gt 0 -and $row[0].connected) { 'up' } else { 'down' }
    }
  }

  # Roll-up: each label is "up" iff gateway handshakes AND sidecar up AND
  # (if backend reachable) backend connected. Backend-unreachable demotes to
  # partial since we can't confirm the trade path end-to-end.
  $perLabel = @()
  $upCount = 0
  $downCount = 0
  foreach ($lbl in $GatewayPorts.Keys) {
    $g = $gw[$lbl]
    $s = $side[$lbl]
    $b = if ($beReachable) { $be[$lbl] } else { 'unknown' }
    $labelUp = ($g -eq 'up') -and ($s -eq 'up') -and (-not $beReachable -or $b -eq 'up')
    $labelDown = ($g -eq 'down') -and ($s -ne 'up')
    if ($labelUp) { $upCount++ }
    elseif ($labelDown) { $downCount++ }
    $perLabel += "{0}=gw:{1}/sc:{2}/be:{3}" -f $lbl, $g, $s, $b
  }

  $total = $GatewayPorts.Count
  $status = if (-not $beReachable -and $upCount -lt $total) { 'partial' }
            elseif ($upCount -eq $total) { 'up' }
            elseif ($downCount -eq $total) { 'down' }
            else { 'partial' }

  $tip = "IBKR {0}: {1}" -f $modeLabel, ($perLabel -join ' ')
  if (-not $beReachable -and $r.Error) { $tip += " (be:{0})" -f $r.Error }
  return @{ Status = $status; Tip = $tip }
}

# Alpaca sidecars live in Docker on the VPS, not on the NUC, so there's
# no local port to probe. Status is entirely derived from the backend's
# broker_accounts view (BrokerRegistry.connected flag), which itself
# reflects whether the alpaca-sidecar-{mode} container's Health RPC
# succeeded and the Configure-with-creds call returned OK.
function Get-AlpacaStatus {
  param([Parameter(Mandatory)][ValidateSet('live', 'paper')][string]$Mode)
  $modeLabel = if ($Mode -eq 'live') { 'Live' } else { 'Paper' }
  $r = Get-BrokerAccounts
  if (-not $r.Reachable) {
    $err = if ($r.Error) { $r.Error } else { 'unknown' }
    return @{ Status = 'partial'; Tip = ("Alpaca {0}: backend {1}" -f $modeLabel, $err) }
  }
  $rows = @($r.Accounts | Where-Object { $_.broker -eq 'alpaca' -and $_.mode -eq $Mode })
  if ($rows.Count -eq 0) {
    return @{ Status = 'gray'; Tip = ("Alpaca {0}: not configured (set alpaca-{1}.api_key / api_secret in Settings)" -f $modeLabel, $Mode) }
  }
  $connected = @($rows | Where-Object { $_.connected }).Count -gt 0
  if ($connected) {
    return @{ Status = 'up'; Tip = ("Alpaca {0}: connected ({1} acct)" -f $modeLabel, $rows.Count) }
  }
  return @{ Status = 'down'; Tip = ("Alpaca {0}: configured but not connected (check sidecar logs)" -f $modeLabel) }
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
      if (-not $r.Connected)  { return @{ Status = 'down'; Tip = 'Schwab: configured, no refresh_token yet (visit /api/admin/brokers/schwab/oauth-start)' } }
      $days = if ($r.ExpiresDays -ne $null) { [math]::Round($r.ExpiresDays, 1) } else { '?' }
      # Phase 7a refresh-token TTL is 168h; warn at 144h (24h grace) per
      # SchwabRefreshTokenExpiringSoon alert. < 1d remaining = partial.
      $st   = if ($r.ExpiresDays -ne $null -and $r.ExpiresDays -lt 1) { 'partial' } else { 'up' }
      $tier2 = if ($r.Tier2Enabled -eq 'true') {
        if ($r.Tier2Failures -and ([int]$r.Tier2Failures) -ge 3) { 'tier2:disabled' }
        elseif ($r.Tier2Failures -and ([int]$r.Tier2Failures) -gt 0) { ("tier2:fail x{0}" -f $r.Tier2Failures) }
        else { 'tier2:on' }
      } else { 'tier2:off' }
      return @{ Status = $st; Tip = ("Schwab: connected, refresh expires {0}d, {1}" -f $days, $tier2) }
    }
  }
  # Phase 7a follow-up: collapse the previous four IBKR icons (Live/Paper
  # gateway pair + Live/Paper sidecar triangles) into two combined icons
  # mirroring the FutuOpenD pattern (one icon = local + sidecar + backend
  # rolled up). Filled square = Live pair, empty square = Paper pair.
  # Logic: green requires every gateway port in the pair to handshake AND
  # the matching sidecar health files to be 'up' AND the backend registry
  # to report connected. Any mismatch -> partial; everything down -> down.
  @{
    Name = 'IBKR Live'; Shape = { param($s) Draw-SquareFilled $s }
    Probe = { Get-IbkrPairStatus -Mode 'live' -GatewayPorts @{ 'isa-live' = 4001; 'normal-live' = 4003 } }
  }
  @{
    Name = 'IBKR Paper'; Shape = { param($s) Draw-SquareEmpty $s }
    Probe = { Get-IbkrPairStatus -Mode 'paper' -GatewayPorts @{ 'isa-paper' = 4002; 'normal-paper' = 4004 } }
  }
  @{
    Name = 'Alpaca Live'; Shape = { param($s) Draw-HexagonFilled $s }
    Probe = { Get-AlpacaStatus -Mode 'live' }
  }
  @{
    Name = 'Alpaca Paper'; Shape = { param($s) Draw-HexagonEmpty $s }
    Probe = { Get-AlpacaStatus -Mode 'paper' }
  }
)

# Read-SidecarHealth + Read-SidecarPair are provided by lib/SidecarLib.ps1
# (dot-sourced near the top of this file). Pester tests for both live in
# deploy/nuc/tests/SidecarLib.Tests.ps1.

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
    @{ Label = 'Restart Futu sidecar'; Action = { Invoke-RestartTasks @('BrokerSidecarFutu') 'sidecar-futu' } }
  )
  'Schwab' = @(
    @{ Label = 'Re-authorize (OAuth flow)'; Action = { Start-Process "$dashboardUrl/api/admin/brokers/schwab/oauth-start" | Out-Null } }
  )
  # IBKR Live/Paper now combine gateway + sidecar restart options because
  # the icons themselves combine gateway + sidecar + backend status. Same
  # ordering as the rolled-up tooltip: gateway first, then sidecar.
  'IBKR Live' = @(
    @{ Label = 'Restart gateway isa-live';        Action = { Invoke-RestartTasks @('IBGateway-isa-live')    'isa-live' } }
    @{ Label = 'Restart gateway normal-live';     Action = { Invoke-RestartTasks @('IBGateway-normal-live') 'normal-live' } }
    @{ Label = 'Restart BOTH live gateways';      Action = { Invoke-RestartTasks @('IBGateway-isa-live','IBGateway-normal-live') 'both-live' } }
    @{ Label = 'Restart sidecar isa-live';        Action = { Invoke-RestartTasks @('IBKRSidecar-isa-live')    'sidecar-isa-live' } }
    @{ Label = 'Restart sidecar normal-live';     Action = { Invoke-RestartTasks @('IBKRSidecar-normal-live') 'sidecar-normal-live' } }
    @{ Label = 'Restart BOTH live sidecars';      Action = { Invoke-RestartTasks @('IBKRSidecar-isa-live','IBKRSidecar-normal-live') 'sidecar-both-live' } }
  )
  'IBKR Paper' = @(
    @{ Label = 'Restart gateway isa-paper';       Action = { Invoke-RestartTasks @('IBGateway-isa-paper')    'isa-paper' } }
    @{ Label = 'Restart gateway normal-paper';    Action = { Invoke-RestartTasks @('IBGateway-normal-paper') 'normal-paper' } }
    @{ Label = 'Restart BOTH paper gateways';     Action = { Invoke-RestartTasks @('IBGateway-isa-paper','IBGateway-normal-paper') 'both-paper' } }
    @{ Label = 'Restart sidecar isa-paper';       Action = { Invoke-RestartTasks @('IBKRSidecar-isa-paper')    'sidecar-isa-paper' } }
    @{ Label = 'Restart sidecar normal-paper';    Action = { Invoke-RestartTasks @('IBKRSidecar-normal-paper') 'sidecar-normal-paper' } }
    @{ Label = 'Restart BOTH paper sidecars';     Action = { Invoke-RestartTasks @('IBKRSidecar-isa-paper','IBKRSidecar-normal-paper') 'sidecar-both-paper' } }
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
