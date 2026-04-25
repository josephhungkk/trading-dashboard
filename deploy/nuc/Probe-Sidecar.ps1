#Requires -Version 5.1
<#
.SYNOPSIS
    Probe one IBKR sidecar over mTLS gRPC and write its health state to disk
    (Phase 4 Task 28).

.DESCRIPTION
    Wraps probe-sidecar.exe (built from sidecar/probe.py via PyInstaller in
    Task 16) and persists the result to
    C:\dashboard\state\sidecar-<label>.health as JSON. The watchdog
    (BrokerWatchdog.ps1) calls this every cycle to drive its
    Adapt-SidecarHealth restart logic; the tray (BrokerTray.ps1) reads the
    same .health file to colour the sidecar dots.

    Exit code: 0 when probe-sidecar.exe reports the sidecar is up, 1
    otherwise. Status field uses the same vocabulary as the gateway probes
    in BrokerWatchdog ("up", "degraded", "down") so consumers can switch on
    one set of strings.

.PARAMETER Label
    The sidecar label (isa-live | isa-paper | normal-live | normal-paper).
    Resolves to the canonical gRPC port via the spec port map.

.PARAMETER ProbeExe
    Path to probe-sidecar.exe. Defaults to the PyInstaller --onedir output.

.PARAMETER SecretsDir
    Where ca.pem + client-backend.{key,crt} live. Defaults to
    C:\dashboard\secrets to match provision-sidecar-mtls.ps1.

.PARAMETER StateDir
    Where to write sidecar-<label>.health. Defaults to C:\dashboard\state.

.PARAMETER TimeoutSec
    Probe timeout. Defaults to 5s - generous enough for the sidecar to
    handshake mTLS + answer Health, tight enough to keep the watchdog
    cycle responsive.

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). ASCII-only.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('isa-live', 'isa-paper', 'normal-live', 'normal-paper')]
    [string]$Label,
    [string]$ProbeExe = 'C:\dashboard\sidecar\dist\probe-sidecar\probe-sidecar.exe',
    [string]$SecretsDir = 'C:\dashboard\secrets',
    [string]$StateDir = 'C:\dashboard\state',
    [int]$TimeoutSec = 5
)

$ErrorActionPreference = 'Stop'

$portMap = @{
    'isa-live'     = 18001
    'isa-paper'    = 18002
    'normal-live'  = 18003
    'normal-paper' = 18004
}
$grpcPort = $portMap[$Label]

if (-not (Test-Path $StateDir)) {
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
}
$healthFile = Join-Path $StateDir "sidecar-$Label.health"

function Write-Health {
    param([string]$Status, [string]$Output)
    $body = @{
        label         = $Label
        status        = $Status
        last_probe_at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        probe_output  = $Output
    } | ConvertTo-Json -Compress -Depth 3

    # Atomic write: tmp + Move-Item so a concurrent reader (BrokerTray) never
    # sees a half-written file.
    $tmp = "$healthFile.tmp"
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($tmp, $body, $utf8NoBom)
    Move-Item -Path $tmp -Destination $healthFile -Force
}

if (-not (Test-Path $ProbeExe)) {
    Write-Health -Status 'down' -Output "probe-sidecar.exe not found at $ProbeExe"
    Write-Host "[probe-sidecar:$Label] EXE missing -> down" -ForegroundColor Red
    exit 1
}

$caPem = Join-Path $SecretsDir 'ca.pem'
$clientCrt = Join-Path $SecretsDir 'client-backend.crt'
$clientKey = Join-Path $SecretsDir 'client-backend.key'
foreach ($p in $caPem, $clientCrt, $clientKey) {
    if (-not (Test-Path $p)) {
        Write-Health -Status 'down' -Output "missing tls material: $p"
        Write-Host "[probe-sidecar:$Label] cert missing $p -> down" -ForegroundColor Red
        exit 1
    }
}

# Run the probe. Capture stdout+stderr so the .health file shows what the
# probe-sidecar.exe binary said - useful for diagnosing handshake failures.
$probeArgs = @(
    '--label', $Label,
    # Sidecars bind to the WG-interface IP (10.10.0.2) per spec section 4.x
    # so the backend on the VPS reaches them over WireGuard. The cert SAN is
    # IP:10.10.0.2, so 127.0.0.1 wouldn't TLS-verify even if it were reachable.
    '--host', '10.10.0.2',
    '--port', $grpcPort,
    '--client-cert', $clientCrt,
    '--client-key', $clientKey,
    '--ca', $caPem,
    '--timeout', $TimeoutSec
)

$previousPref = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $combined = & $ProbeExe @probeArgs 2>&1
    $exitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousPref
}
$probeText = ($combined | ForEach-Object { [string]$_ }) -join "`n"

if ($exitCode -eq 0) {
    Write-Health -Status 'up' -Output $probeText
    Write-Host "[probe-sidecar:$Label] up" -ForegroundColor Green
    exit 0
}

# probe-sidecar.exe exits non-zero for both "down" (no TCP) and "degraded"
# (TLS up but Health rpc returned not-connected). Distinguish on the output
# text so the .health file is honest and the watchdog can decide whether
# to act.
$status = if ($probeText -match '\[down\]') { 'down' } else { 'degraded' }
Write-Health -Status $status -Output $probeText
Write-Host "[probe-sidecar:$Label] $status (exit=$exitCode)" -ForegroundColor Yellow
exit 1
