#Requires -Version 5.1
<#
.SYNOPSIS
    Build PyInstaller --onedir bundles for the IBKR sidecar + probe.

.DESCRIPTION
    Phase 4 Task 16. Run on a Windows host (the NUC) with the project synced
    to C:\dashboard. Produces:

      <OutDir>\ibkr-sidecar\ibkr-sidecar.exe
      <OutDir>\probe-sidecar\probe-sidecar.exe
      <OutDir>\ibkr-sidecar-YYYYMMDD-HHMM.zip   (both bundles, ready to copy)

.PARAMETER OutDir
    Where the bundles + zip land. Defaults to .\dist relative to this script's
    parent (so .\sidecar\dist\). Provide an absolute path if you want it elsewhere.

.NOTES
    Requires `uv` on PATH (winget install astral.uv) and `bash` (Git Bash or
    WSL) so the proto-gen.sh fallback can run. PowerShell 5.1 compatible - we
    avoid Unicode em-dashes and the file is saved UTF-8 + BOM + CRLF
    (matches memory note ps1_nuc_bom_crlf.md).
#>
[CmdletBinding()]
param(
    [string]$OutDir = "$PSScriptRoot/../dist"
)

$ErrorActionPreference = 'Stop'
Set-Location "$PSScriptRoot/.."

Write-Host "[build] sidecar build starting..." -ForegroundColor Cyan

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

# 2. Regenerate proto bindings inline in PowerShell. proto-gen.sh did the same
# thing via bash, but WSL bash on Windows runs in its own root filesystem and
# can't see Windows-installed uv even when uv is on the parent's PATH. Call
# uv directly here so the build is bash-free.
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

# grpc_tools emits `from broker.v1 import broker_pb2` which doesn't resolve
# under the sidecar._generated.broker.v1 package layout. Rewrite to fully
# qualified so the bindings work wherever they're imported.
$grpcPath = '_generated/broker/v1/broker_pb2_grpc.py'
$content = Get-Content -Raw $grpcPath
$content = $content -replace '(?m)^from broker\.v1 import broker_pb2', 'from sidecar._generated.broker.v1 import broker_pb2'
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

# 2. Resolve dependencies (and pyinstaller) into the project venv.
Invoke-Native -Label 'uv sync' -Block { & $uv.Source sync --extra dev }

# 3. Build the long-running sidecar bundle.
Invoke-Native -Label 'pyinstaller (ibkr-sidecar)' -Block {
    & $uv.Source run pyinstaller `
        --onedir `
        --noconfirm `
        --name ibkr-sidecar `
        --distpath $OutDir `
        --paths . `
        --hidden-import grpc `
        --hidden-import ib_async `
        --collect-data ib_async `
        ibkr_sidecar.py
}

# 4. Build the probe-only client bundle (separate so the watchdog can ship
#    independently and so cold-start time stays small).
Invoke-Native -Label 'pyinstaller (probe-sidecar)' -Block {
    & $uv.Source run pyinstaller `
        --onedir `
        --noconfirm `
        --name probe-sidecar `
        --distpath $OutDir `
        --paths . `
        --hidden-import grpc `
        probe.py
}

# 5. Surface the produced .exe paths so the operator can sanity-check.
Write-Host "[build] artifacts:" -ForegroundColor Green
Get-ChildItem -Recurse -Filter "*.exe" -Path $OutDir |
    ForEach-Object { Write-Host "  $($_.FullName)" }

# 6. Stamp + zip both bundles so they can be copied to the deploy share
#    in one drop. Compress-Archive's -Path is a list, not a glob, so list both.
$timestamp = Get-Date -Format 'yyyyMMdd-HHmm'
$zip = Join-Path $OutDir "ibkr-sidecar-$timestamp.zip"
Compress-Archive `
    -Force `
    -Path (Join-Path $OutDir "ibkr-sidecar"), (Join-Path $OutDir "probe-sidecar") `
    -DestinationPath $zip

Write-Host "[build] $zip" -ForegroundColor Green
