# restart-futu.ps1 — restart FutuOpenD on the NUC.
# The dashboard's /api/brokers/futu/restart endpoint SSHes to the NUC and runs this.
#
# Install location: copy this file to C:\trader-ops\restart-futu.ps1 on the NUC.

$ErrorActionPreference = "Stop"
$logFile = "C:\trader-ops\logs\restart-futu.log"
New-Item -ItemType Directory -Force -Path (Split-Path $logFile) | Out-Null

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Add-Content -Path $logFile -Value $line
    Write-Output $line
}

Log "restart-futu: stopping existing process"
Get-Process -Name FutuOpenD -ErrorAction SilentlyContinue | ForEach-Object {
    Log "  killing PID $($_.Id)"
    Stop-Process -Id $_.Id -Force
}

Start-Sleep -Seconds 2

$exe = "C:\FutuOpenD\FutuOpenD.exe"
if (-not (Test-Path $exe)) {
    Log "restart-futu: ERROR $exe not found"
    exit 1
}

Log "restart-futu: starting $exe"
Start-Process -FilePath $exe -WorkingDirectory "C:\FutuOpenD"

Log "restart-futu: launched"
exit 0
