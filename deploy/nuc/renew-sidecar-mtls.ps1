#Requires -Version 5.1
<#
.SYNOPSIS
    Annual rotation of the IBKR sidecar mTLS leaf certs (Phase 4 Task 22).

.DESCRIPTION
    Refreshes all 4 server certs + 1 client cert without touching the root
    CA. For each sidecar in turn (so the gateway socket stays up):

      1. Stop the IBKR-Sidecar-<label> scheduled task.
      2. Delete the old leaf cert + key on disk.
      3. Call provision-sidecar-mtls.ps1 which mints a fresh leaf because
         the file is missing (the provisioner's idempotency check sees no
         file and reissues with a new serial + 1-year notAfter).
      4. Start the scheduled task.
      5. Wait $StartGracePeriodSec seconds before moving to the next
         sidecar.

    Then refreshes the dashboard-backend client cert and pushes the
    updated material to the backend's app_secrets via provision-and-publish.

    For full root-CA rotation, follow RUNBOOK-mtls-recovery.md (compromise
    procedure, ~5min downtime budget).

.PARAMETER ScriptDir
    Where the provision-sidecar-mtls.ps1 + provision-and-publish.ps1
    siblings live. Defaults to this script's parent directory.

.PARAMETER SecretsDir
    Output dir for the certs. Defaults to C:\dashboard\secrets.

.PARAMETER StartGracePeriodSec
    How long to wait between Start-ScheduledTask and moving to the next
    sidecar. Defaults to 30s — long enough for the sidecar to bind its
    gRPC port + connect to the gateway before we kick the next one.

.PARAMETER SkipBackendPublish
    Skip the provision-and-publish step (leave backend's app_secrets
    untouched). Useful when only sidecar certs need rotating.

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). ASCII-only.
    The IBKR-Sidecar-<label> Scheduled Tasks land in Chunk E. Until then,
    Stop/Start-ScheduledTask report "task not found" warnings which the
    script tolerates so it can be exercised early.
#>
[CmdletBinding()]
param(
    [string]$ScriptDir = '',
    [string]$SecretsDir = 'C:\dashboard\secrets',
    [int]$StartGracePeriodSec = 30,
    [switch]$SkipBackendPublish
)

$ErrorActionPreference = 'Stop'

if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$provisioner = Join-Path $ScriptDir 'provision-sidecar-mtls.ps1'
$publisher = Join-Path $ScriptDir 'provision-and-publish.ps1'
foreach ($p in $provisioner, $publisher) {
    if (-not (Test-Path $p)) { throw "missing sibling script '$p'" }
}

$LABELS = @('isa-live', 'isa-paper', 'normal-live', 'normal-paper')

function Invoke-Reissue {
    param([string]$LeafBaseName)
    $key = Join-Path $SecretsDir "$LeafBaseName.key"
    $crt = Join-Path $SecretsDir "$LeafBaseName.crt"
    Remove-Item -Force -ErrorAction SilentlyContinue $key, $crt
    & $provisioner -OutDir $SecretsDir | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "provisioner failed reissuing $LeafBaseName (exit $LASTEXITCODE)"
    }
    if (-not (Test-Path $crt)) {
        throw "expected $crt after provisioner run; not found"
    }
}

# 1. Roll the four sidecar certs one at a time.
foreach ($label in $LABELS) {
    Write-Host ''
    Write-Host "[renew] === sidecar-$label ===" -ForegroundColor Cyan

    $taskName = "IBKR-Sidecar-$label"
    try {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop
        Write-Host "[renew] stopped scheduled task: $taskName" -ForegroundColor Yellow
    } catch {
        Write-Warning "[renew] $taskName not stopped (probably not registered yet): $($_.Exception.Message)"
    }

    Invoke-Reissue -LeafBaseName "sidecar-$label"
    Write-Host "[renew] reissued sidecar-$label.crt" -ForegroundColor Green

    try {
        Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
        Write-Host "[renew] started scheduled task: $taskName" -ForegroundColor Green
        Write-Host "[renew] grace period ${StartGracePeriodSec}s before next sidecar..." -ForegroundColor Cyan
        Start-Sleep -Seconds $StartGracePeriodSec
    } catch {
        Write-Warning "[renew] $taskName not started (probably not registered yet): $($_.Exception.Message)"
    }
}

# 2. Roll the client cert.
Write-Host ''
Write-Host "[renew] === client-backend ===" -ForegroundColor Cyan
Invoke-Reissue -LeafBaseName 'client-backend'
Write-Host "[renew] reissued client-backend.crt" -ForegroundColor Green

# 3. Republish the bundle to the backend.
if ($SkipBackendPublish) {
    Write-Host "[renew] -SkipBackendPublish set; backend secrets not refreshed." -ForegroundColor Yellow
    return
}
Write-Host ''
Write-Host "[renew] publishing fresh client material to backend..." -ForegroundColor Cyan
& $publisher -ProvisionerPath $provisioner
if ($LASTEXITCODE -ne 0) {
    throw "publisher failed (exit $LASTEXITCODE); backend still holds the previous client material."
}

Write-Host "[renew] all leaf certs rotated; CA + CRL untouched." -ForegroundColor Green
