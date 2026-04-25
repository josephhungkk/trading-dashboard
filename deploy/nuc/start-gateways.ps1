# start-gateways.ps1 — bring up all broker services after a NUC reboot.
# Intended to be run once at startup (via Task Scheduler "at logon" trigger)
# or manually: powershell -File C:\trader-ops\start-gateways.ps1

$ErrorActionPreference = "Continue"
$logFile = "C:\trader-ops\logs\startup.log"
New-Item -ItemType Directory -Force -Path (Split-Path $logFile) | Out-Null

function Log($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" | Tee-Object -FilePath $logFile -Append
}

Log "=== start-gateways ==="

# --- WireGuard — should already be a service, but nudge it ---
Log "ensuring WireGuard tunnel is up"
Start-Service -Name WireGuardTunnel$Tunnel -ErrorAction SilentlyContinue

# --- Postgres ---
Log "ensuring Postgres is running"
Start-Service -Name "postgresql-x64-16" -ErrorAction SilentlyContinue

# --- Ollama ---
Log "ensuring Ollama is running"
Start-Service -Name "Ollama" -ErrorAction SilentlyContinue

# --- IB Gateway ---
& "$PSScriptRoot\restart-ib.ps1"

# --- FutuOpenD ---
& "$PSScriptRoot\restart-futu.ps1"

Log "=== start-gateways done ==="
