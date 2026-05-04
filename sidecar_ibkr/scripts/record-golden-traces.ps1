#Requires -Version 5.1
<#
.SYNOPSIS
    Record golden-trace fixtures from a paper IBKR Gateway.

.DESCRIPTION
    Phase 4 Task 17. Operator-driven on the NUC against paper Gateway 4002.
    Connects via ib_async, calls every read-only method the sidecar uses,
    and JSON-serializes the responses to sidecar/tests/golden/<method>.json.
    Those fixtures power Task 18 replay tests so we can guard against
    proto-shape regressions without a live gateway in CI.

.PARAMETER Port
    IBKR Gateway port. Defaults to 4002 (paper). Live is 4001 -- DO NOT
    point this at a live gateway, the recorder logs real account numbers.

.PARAMETER OutDir
    Where to write the .json fixtures. Defaults to ../tests/golden/
    relative to this script's parent.

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). No em-dashes.
    Requires uv on PATH.
#>
[CmdletBinding()]
param(
    [int]$Port = 4002,
    [string]$OutDir = ''
)

$ErrorActionPreference = 'Stop'

# Resolve script paths inside the body. $PSScriptRoot is unreliable in param defaults
# on some PS 5.1 invocations (it can evaluate to empty string before the script context
# is fully bound).
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SidecarRoot = Resolve-Path (Join-Path $ScriptDir '..')
if ([string]::IsNullOrWhiteSpace($OutDir)) {
    $OutDir = Join-Path $SidecarRoot 'tests/golden'
}
Set-Location $SidecarRoot

if ($Port -eq 4001) {
    throw "Refusing to record against live gateway port 4001. Pass -Port 4002 (paper) explicitly."
}

# Resolve uv. Prefer PATH; fall back to the well-known winget shim location so the
# operator doesn't have to restart their shell after `winget install astral.uv`.
$uv = (Get-Command uv -ErrorAction SilentlyContinue)
if (-not $uv) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\uv\uv.exe",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\uv.exe",
        "$env:USERPROFILE\.cargo\bin\uv.exe"
    )
    # Also probe winget's per-package install dir, where versioned packages land
    # before the shim links are created on the next shell restart.
    $wingetPkgs = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path $wingetPkgs) {
        $found = Get-ChildItem -Path $wingetPkgs -Recurse -Filter uv.exe -ErrorAction SilentlyContinue |
                 Select-Object -First 1
        if ($found) { $candidates += $found.FullName }
    }
    $uvPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $uvPath) {
        throw "uv not found on PATH or in known winget locations. Install with: winget install astral-sh.uv (then restart shell)."
    }
    $uv = Get-Command $uvPath
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Host "[record] connecting to paper gateway on port $Port..." -ForegroundColor Cyan
Write-Host "[record] out-dir: $OutDir" -ForegroundColor Cyan
Write-Host "[record] uv: $($uv.Source)" -ForegroundColor Cyan

# Use the file-path form (not -m) because sidecar/scripts/ has no __init__.py.
& $uv.Source run python scripts/record_traces.py --port $Port --out-dir $OutDir
if ($LASTEXITCODE -ne 0) {
    throw "record_traces.py failed with exit code $LASTEXITCODE"
}

Write-Host "[record] golden fixtures written to: $OutDir" -ForegroundColor Green
Get-ChildItem -Path $OutDir -Filter "*.json" |
    ForEach-Object { Write-Host "  $($_.Name) ($([math]::Round($_.Length / 1KB, 1)) KB)" }
