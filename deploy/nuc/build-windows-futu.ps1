#Requires -Version 5.1
<#
.SYNOPSIS
    Build the Futu sidecar PyInstaller bundle and stage it for the scheduled task.

.DESCRIPTION
    Phase 6 G1. Run on the NUC under PowerShell 5.1 with the project synced to
    C:\dashboard. Invokes sidecar_futu/scripts/build-windows.ps1 (B7), then
    copies the resulting futu-sidecar.exe into C:\dashboard\dist-staging-futu
    where the BrokerSidecarFutu scheduled task expects it.

.PARAMETER Version
    Version label for the build (informational only). Defaults to 0.6.0.

.NOTES
    File saved as UTF-8 with BOM and CRLF line endings per memory note
    ps1_nuc_bom_crlf.md so PowerShell 5.1 parses it correctly.
#>
[CmdletBinding()]
param(
    [string]$Version = "0.6.0"
)

$ErrorActionPreference = 'Stop'

Set-Location 'C:\dashboard\sidecar_futu'
Write-Host "[build-futu] invoking sidecar_futu\scripts\build-windows.ps1" -ForegroundColor Cyan
& '.\scripts\build-windows.ps1'

$source = Join-Path 'C:\dashboard\sidecar_futu\dist' 'futu-sidecar.exe'
$dest   = 'C:\dashboard\dist-staging-futu'

if (-not (Test-Path $source)) {
    throw "[build-futu] expected $source after build but it was not produced"
}

if (-not (Test-Path $dest)) {
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
}

Copy-Item $source -Destination $dest -Force
Write-Host "[OK] Built v$Version -> $dest\futu-sidecar.exe" -ForegroundColor Green
Get-Item (Join-Path $dest 'futu-sidecar.exe') | Select-Object Length, LastWriteTime
