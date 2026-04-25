# deploy/nuc/verify-wg-windows.ps1
# Section 0 prerequisite verifier for Phase 4 IBKR sidecars.
# Halts the phase if WireGuard isn't on the Windows side or sidecar ports cannot bind.
#
# Run with:
#   powershell -NoProfile -ExecutionPolicy Bypass -File verify-wg-windows.ps1
#
# Idempotent (creates the firewall rule if missing). Exits 0 on success, 1 on any failure.
# If FAIL, halt the phase and re-brainstorm. The sidecar topology assumes Windows-native
# binding to 10.10.0.2; if WireGuard is on the WSL side, sidecars must be redesigned.

[CmdletBinding()]
param(
    [string]$WgServiceName = 'WireGuardTunnel$wg0',  # adjust if your tunnel name differs
    [string]$WgIp = '10.10.0.2',
    [int[]]$Ports = @(18001, 18002, 18003, 18004),
    [string]$RuleName = 'Dashboard-IBKRSidecar-Inbound'
)

$ErrorActionPreference = 'Stop'
$failed = $false

function Pass([string]$msg) { Write-Host "[PASS] $msg" -ForegroundColor Green }
function Fail([string]$msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red ; $script:failed = $true }
function Info([string]$msg) { Write-Host "[INFO] $msg" -ForegroundColor Cyan }

Write-Host "==== Phase 4 prerequisite check (WireGuard on Windows + sidecar bind) ====" -ForegroundColor Cyan
Info "WG service: $WgServiceName"
Info "WG IP: $WgIp"
Info "Sidecar ports: $($Ports -join ', ')"
Write-Host ""

# (a) WireGuard service running.
# Try the explicit name first; if not found, enumerate WireGuardTunnel* and pick a running one.
$svc = Get-Service -Name $WgServiceName -ErrorAction SilentlyContinue
if (-not $svc) {
    $candidates = @(Get-Service -Name 'WireGuardTunnel*' -ErrorAction SilentlyContinue)
    $running = @($candidates | Where-Object { $_.Status -eq 'Running' })
    if ($running.Count -ge 1) {
        $svc = $running[0]
        $WgServiceName = $svc.Name
        Info "Auto-detected WireGuard service: $WgServiceName"
    } elseif ($candidates.Count -ge 1) {
        $svc = $candidates[0]
        $WgServiceName = $svc.Name
    }
}
if ($svc -and $svc.Status -eq 'Running') {
    Pass "WireGuard service '$WgServiceName' is running"
} elseif ($svc) {
    Fail "WireGuard service '$WgServiceName' exists but is not running (Status=$($svc.Status))"
} else {
    Fail "No WireGuard service found (looked for '$WgServiceName' + 'WireGuardTunnel*'). Install WG-for-Windows + import the wg0 tunnel."
}

# (b) 10.10.0.2 on a Windows interface
$ip = Get-NetIPAddress -IPAddress $WgIp -ErrorAction SilentlyContinue
if ($ip) {
    Pass "WG IP $WgIp is on Windows interface '$($ip.InterfaceAlias)' (IfIndex $($ip.InterfaceIndex))"
} else {
    Fail "WG IP $WgIp NOT on any Windows interface. WireGuard is likely on the WSL side; sidecar topology won't work as designed."
}

# (c) Firewall rule (idempotent — create if missing)
$existingRule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($existingRule) {
    Pass "Firewall rule '$RuleName' exists (Action=$($existingRule.Action), Enabled=$($existingRule.Enabled))"
} else {
    Info "Creating firewall rule '$RuleName' for ports $($Ports -join ',') from 10.10.0.0/24..."
    try {
        New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $Ports -RemoteAddress '10.10.0.0/24' `
            -Profile Any -Enabled True | Out-Null
        Pass "Firewall rule created"
    } catch {
        Fail "Failed to create firewall rule: $($_.Exception.Message). Run elevated."
    }
}

# (d) Test bind on port 18001: start a tiny TCP listener, probe locally, kill it.
$testPort = $Ports[0]
$tcp = $null
try {
    $tcp = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse($WgIp), $testPort)
    $tcp.Start()
    Start-Sleep -Milliseconds 500

    $client = [System.Net.Sockets.TcpClient]::new()
    $task = $client.ConnectAsync($WgIp, $testPort)
    $endpoint = "$($WgIp):$($testPort)"
    if ($task.Wait([TimeSpan]::FromSeconds(3))) {
        Pass "Test bind succeeded: $endpoint (listener accepted local probe)"
        $client.Close()
    } else {
        Fail "Test bind FAILED: $endpoint - listener started but local probe could not connect."
    }
} catch {
    Fail "Test bind FAILED on port $testPort - $($_.Exception.Message)"
} finally {
    if ($tcp) { try { $tcp.Stop() } catch { } }
}

Write-Host ""
if ($failed) {
    Write-Host "==== PHASE 4 PREREQUISITE CHECK FAILED ====" -ForegroundColor Red
    Write-Host "Halt: do not proceed with sidecar tasks until all checks pass." -ForegroundColor Red
    Write-Host "If WG is on the WSL side, the sidecar topology must be redesigned (out of scope for Phase 4)." -ForegroundColor Yellow
    exit 1
} else {
    Write-Host "==== PHASE 4 PREREQUISITES OK ====" -ForegroundColor Green
    Write-Host "Safe to proceed with Phase 4 implementation." -ForegroundColor Green
    exit 0
}
