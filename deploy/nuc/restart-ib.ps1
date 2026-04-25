# restart-ib.ps1 — restart IB Gateway on the NUC.
# The dashboard's /api/brokers/ibkr/restart endpoint SSHes to the NUC and runs this.
#
# Install location: copy this file to C:\trader-ops\restart-ib.ps1 on the NUC.
# Assumes IB Gateway is installed at the default C:\Jts\ path.

$ErrorActionPreference = "Stop"
$logFile = "C:\trader-ops\logs\restart-ib.log"
New-Item -ItemType Directory -Force -Path (Split-Path $logFile) | Out-Null

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Add-Content -Path $logFile -Value $line
    Write-Output $line
}

Log "restart-ib: stopping existing process"
Get-Process -Name ibgateway -ErrorAction SilentlyContinue | ForEach-Object {
    Log "  killing PID $($_.Id)"
    Stop-Process -Id $_.Id -Force
}

Start-Sleep -Seconds 3

# Find the newest ibgateway.exe under C:\Jts\ibgateway\<version>\
$exe = Get-ChildItem -Path "C:\Jts\ibgateway" -Filter "ibgateway.exe" -Recurse |
       Sort-Object LastWriteTime -Descending |
       Select-Object -First 1

if (-not $exe) {
    Log "restart-ib: ERROR could not locate ibgateway.exe under C:\Jts\ibgateway\"
    exit 1
}

Log "restart-ib: starting $($exe.FullName)"
Start-Process -FilePath $exe.FullName

Log "restart-ib: launched"
exit 0
