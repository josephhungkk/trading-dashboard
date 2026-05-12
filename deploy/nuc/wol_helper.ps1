# deploy/nuc/wol_helper.ps1 — Phase 11a-A2
# Tiny HTTP service on the NUC. BE on the VPS calls this over WG to
# wake the heavy box; we ARP-resolve the heavy box IP to MAC on the
# LAN side and broadcast the magic packet. The packet doesn't cross
# WG cleanly so it MUST be sent from a same-LAN host.
#
# Listen on 10.10.0.2:11900 (WG-internal). NOT exposed past WG.
# Single endpoint: POST /wake -> { "status": "sent", "mac": "..." }

$ErrorActionPreference = "Stop"
$Listener = New-Object System.Net.HttpListener
$Listener.Prefixes.Add("http://10.10.0.2:11900/")
$Listener.Start()
Write-Host "WoL helper listening on http://10.10.0.2:11900/"

# In-memory MAC cache (per-process, lost on restart — fine; ARP re-
# resolves on next wake).
$MacCache = @{}

function Resolve-MacFromArp($ip) {
    if ($MacCache.ContainsKey($ip)) { return $MacCache[$ip] }
    # Probe the host so the ARP table has a fresh entry.
    Test-Connection -ComputerName $ip -Count 1 -TimeoutSeconds 2 -Quiet | Out-Null
    $matches = (arp -a $ip | Select-String "$ip\s+([\w-]{17})").Matches
    if (-not $matches) { return $null }
    $arpLine = $matches.Groups[1].Value
    if (-not $arpLine) { return $null }
    $mac = $arpLine -replace '-', ':'
    $MacCache[$ip] = $mac
    return $mac
}

function Send-MagicPacket($macStr) {
    $macBytes = ($macStr -split ':') | ForEach-Object { [Convert]::ToByte($_, 16) }
    $packet = [byte[]](,0xFF * 6 + ($macBytes * 16))
    $udpClient = New-Object System.Net.Sockets.UdpClient
    $udpClient.EnableBroadcast = $true
    $udpClient.Send($packet, $packet.Length, "255.255.255.255", 9) | Out-Null
    $udpClient.Close()
}

while ($Listener.IsListening) {
    $ctx = $Listener.GetContext()
    $req = $ctx.Request
    $res = $ctx.Response
    try {
        if ($req.HttpMethod -ne "POST" -or $req.Url.AbsolutePath -ne "/wake") {
            $res.StatusCode = 404
            continue
        }
        $heavyIp = "192.168.50.30"
        $mac = Resolve-MacFromArp $heavyIp
        if (-not $mac) {
            $res.StatusCode = 502
            $body = '{"status":"failed","reason":"arp_resolve_failed"}'
        } else {
            Send-MagicPacket $mac
            $res.StatusCode = 200
            $body = "{`"status`":`"sent`",`"mac`":`"$mac`"}"
        }
        $buf = [System.Text.Encoding]::UTF8.GetBytes($body)
        $res.OutputStream.Write($buf, 0, $buf.Length)
    } finally {
        $res.Close()
    }
}
