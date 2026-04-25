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

# 1. Regenerate proto bindings before bundling.
$bash = (Get-Command bash -ErrorAction SilentlyContinue)
if (-not $bash) {
    throw "bash not found on PATH; install Git Bash or enable WSL so proto-gen.sh can run."
}

# proto-gen.sh calls `uv run python -m grpc_tools.protoc`, but bash on Windows
# does not always inherit the same PATH the parent PowerShell sees. Resolve uv
# directly and prepend its directory to $env:PATH so the spawned bash finds it.
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
$uvDir = Split-Path -Parent $uv.Source
if ($env:PATH -notlike "*$uvDir*") {
    $env:PATH = "$uvDir;$env:PATH"
    Write-Host "[build] prepended uv dir to PATH: $uvDir" -ForegroundColor DarkGray
}

# Use a RELATIVE path. Absolute Windows paths cause two different breakages
# depending on which bash is on PATH:
#   - WSL bash needs /mnt/c/... (Windows form C:/... fails to resolve).
#   - Git Bash mingw can take C:/... but eats backslashes if any slip through.
# Both flavours inherit cwd from the parent PowerShell process, and we already
# Set-Location to the sidecar root above, so 'scripts/proto-gen.sh' resolves
# regardless of bash flavour.
& $bash.Source 'scripts/proto-gen.sh'
if ($LASTEXITCODE -ne 0) {
    throw "proto-gen.sh failed with exit code $LASTEXITCODE"
}

# 2. Resolve dependencies (and pyinstaller) into the project venv.
& uv sync --extra dev
if ($LASTEXITCODE -ne 0) {
    throw "uv sync failed with exit code $LASTEXITCODE"
}

# 3. Build the long-running sidecar bundle.
& uv run pyinstaller `
    --onedir `
    --noconfirm `
    --name ibkr-sidecar `
    --distpath $OutDir `
    --paths . `
    --hidden-import grpc `
    --hidden-import ib_async `
    --collect-data ib_async `
    ibkr_sidecar.py
if ($LASTEXITCODE -ne 0) {
    throw "pyinstaller (ibkr-sidecar) failed with exit code $LASTEXITCODE"
}

# 4. Build the probe-only client bundle (separate so the watchdog can ship
#    independently and so cold-start time stays small).
& uv run pyinstaller `
    --onedir `
    --noconfirm `
    --name probe-sidecar `
    --distpath $OutDir `
    --paths . `
    --hidden-import grpc `
    probe.py
if ($LASTEXITCODE -ne 0) {
    throw "pyinstaller (probe-sidecar) failed with exit code $LASTEXITCODE"
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
