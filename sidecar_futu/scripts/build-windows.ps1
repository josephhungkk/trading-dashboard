#Requires -Version 5.1
<#
.SYNOPSIS
    Build PyInstaller --onefile bundle for the Futu sidecar.

.DESCRIPTION
    Run on a Windows host with the project synced to C:\dashboard. Produces:

      <OutDir>\futu-sidecar.exe
      <OutDir>\futu-sidecar-YYYYMMDD-HHMM.zip

.PARAMETER OutDir
    Where the bundle + zip land. Defaults to .\dist relative to this script's
    parent (so .\sidecar_futu\dist\). Provide an absolute path if you want it elsewhere.

.NOTES
    Requires `uv` on PATH (winget install astral.uv). PowerShell 5.1 compatible - we
    avoid Unicode em-dashes and the file is saved UTF-8 + BOM + CRLF
    (matches memory note ps1_nuc_bom_crlf.md).
#>
[CmdletBinding()]
param(
    [string]$OutDir = "$PSScriptRoot/../dist"
)

$ErrorActionPreference = 'Stop'
Set-Location "$PSScriptRoot/.."

Write-Host "[build] futu sidecar build starting..." -ForegroundColor Cyan

# 1. Resolve uv (needed for proto codegen and the pyinstaller build below).
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\uv\uv.exe",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\uv.exe",
        "$env:USERPROFILE\.cargo\bin\uv.exe"
    )
    $wingetPkgs = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path $wingetPkgs) {
        $found = Get-ChildItem -Path $wingetPkgs -Recurse -Filter uv.exe -ErrorAction SilentlyContinue |
                 Select-Object -First 1
        if ($found) { $candidates += $found.FullName }
    }
    $uvPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $uvPath) {
        throw "uv not found. Install with: winget install astral-sh.uv (then restart shell)."
    }
    $uv = Get-Command $uvPath
}
Write-Host "[build] uv: $($uv.Source)" -ForegroundColor DarkGray

# 2. Regenerate proto bindings inline in PowerShell.
New-Item -ItemType Directory -Force -Path '_generated/broker/v1' | Out-Null
foreach ($init in '_generated/__init__.py', '_generated/broker/__init__.py', '_generated/broker/v1/__init__.py') {
    if (-not (Test-Path $init)) { New-Item -ItemType File -Path $init | Out-Null }
}
& $uv.Source run python -m grpc_tools.protoc `
    --proto_path=../proto `
    --python_out=_generated `
    --grpc_python_out=_generated `
    --pyi_out=_generated `
    broker/v1/broker.proto
if ($LASTEXITCODE -ne 0) {
    throw "grpc_tools.protoc failed with exit code $LASTEXITCODE"
}

# grpc_tools emits imports that don't resolve under the
# sidecar_futu._generated.broker.v1 package layout. Rewrite to fully qualified
# so the bindings work wherever they're imported.
$grpcPath = '_generated/broker/v1/broker_pb2_grpc.py'
$content = Get-Content -Raw $grpcPath
$content = $content -replace '(?m)^from broker\.v1 import broker_pb2', 'from sidecar_futu._generated.broker.v1 import broker_pb2'
$content = $content -replace '(?m)^from v1 import broker_pb2', 'from sidecar_futu._generated.broker.v1 import broker_pb2'
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText((Resolve-Path $grpcPath).Path, $content, $utf8NoBom)
Write-Host "[build] proto codegen complete (grpc_tools.protoc, native PS)" -ForegroundColor Green

# Helper: run a native command (uv / pyinstaller) without letting its stderr
# progress lines (e.g. uv's "Resolved 36 packages") trigger PowerShell's
# Stop-preference auto-throw. Exit code is the source of truth.
function Invoke-Native {
    param([Parameter(Mandatory)][scriptblock]$Block, [string]$Label)
    $previousPref = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Block }
    finally { $ErrorActionPreference = $previousPref }
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

# 3. Resolve dependencies (and pyinstaller) into the project venv.
Invoke-Native -Label 'uv sync' -Block { & $uv.Source sync --extra dev }

# 4. Build the Futu sidecar bundle.
Invoke-Native -Label 'pyinstaller (futu-sidecar)' -Block {
    & $uv.Source run pyinstaller `
        --onefile `
        --noconfirm `
        --name futu-sidecar `
        --distpath $OutDir `
        --paths . `
        --hidden-import grpc `
        --hidden-import futu `
        --hidden-import google.protobuf `
        --hidden-import cryptography `
        --hidden-import prometheus_client `
        --hidden-import structlog `
        --collect-data futu `
        futu_sidecar.py
}

# 5. Surface the produced .exe paths so the operator can sanity-check.
Write-Host "[build] artifacts:" -ForegroundColor Green
Get-ChildItem -Recurse -Filter "*.exe" -Path $OutDir |
    ForEach-Object { Write-Host "  $($_.FullName)" }

# 6. Stamp + zip the bundle so it can be copied to the deploy share.
$timestamp = Get-Date -Format 'yyyyMMdd-HHmm'
$zip = Join-Path $OutDir "futu-sidecar-$timestamp.zip"
Compress-Archive -Force -Path (Join-Path $OutDir "futu-sidecar.exe") -DestinationPath $zip

Write-Host "[build] $zip" -ForegroundColor Green
