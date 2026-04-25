#Requires -Version 5.1
<#
.SYNOPSIS
    Run the Pester unit tests for deploy/nuc/lib/SidecarLib.ps1.

.DESCRIPTION
    Phase 4.5 addendum. Ensures Pester 5+ is installed (the Pester 3.4
    bundled with Windows PowerShell 5.1 does not support the BeforeAll /
    Should -Be syntax used in the suite), then invokes Pester against
    every *.Tests.ps1 file under deploy/nuc/tests/.

    Exits non-zero if any test fails - safe to wire into a future CI job
    or a pre-commit hook.

.PARAMETER PesterMinVersion
    Minimum Pester version required. Defaults to 5.0.0.

.PARAMETER InstallScope
    Where to install Pester if missing. Defaults to CurrentUser so the
    operator does not need admin for the install path. Pester install
    itself does not require admin when -Scope CurrentUser.

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). ASCII-only.
#>
[CmdletBinding()]
param(
    [version]$PesterMinVersion = '5.0.0',
    [ValidateSet('CurrentUser', 'AllUsers')][string]$InstallScope = 'CurrentUser'
)

$ErrorActionPreference = 'Stop'

$pester = Get-Module -ListAvailable -Name Pester |
    Where-Object { $_.Version -ge $PesterMinVersion } |
    Sort-Object Version -Descending |
    Select-Object -First 1

if (-not $pester) {
    Write-Host "[tests] Pester >= $PesterMinVersion not installed; installing to $InstallScope ..." -ForegroundColor Cyan
    # NuGet provider is required for PowerShellGet's Install-Module to talk
    # to PSGallery. On a fresh Windows PowerShell 5.1 it is missing and
    # Set-PSRepository / Install-Module trip on a NullReferenceException
    # rather than a clean error. Bootstrap it explicitly first.
    $nuget = Get-PackageProvider -ListAvailable -Name NuGet -ErrorAction SilentlyContinue |
        Where-Object { $_.Version -ge '2.8.5.201' }
    if (-not $nuget) {
        Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -Scope $InstallScope | Out-Null
    }
    # PSGallery is untrusted by default on a fresh machine; flip it to
    # Trusted for this user only so Install-Module does not prompt.
    if ((Get-PSRepository -Name PSGallery).InstallationPolicy -ne 'Trusted') {
        Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
    }
    Install-Module -Name Pester -MinimumVersion $PesterMinVersion -Scope $InstallScope -Force -SkipPublisherCheck
    $pester = Get-Module -ListAvailable -Name Pester |
        Where-Object { $_.Version -ge $PesterMinVersion } |
        Sort-Object Version -Descending |
        Select-Object -First 1
    if (-not $pester) {
        throw "Failed to install Pester >= $PesterMinVersion."
    }
}
Write-Host "[tests] using Pester $($pester.Version) at $($pester.ModuleBase)" -ForegroundColor Cyan

Import-Module (Join-Path $pester.ModuleBase 'Pester.psd1') -Force

$testRoot = $PSScriptRoot
$config = New-PesterConfiguration
$config.Run.Path = $testRoot
$config.Output.Verbosity = 'Detailed'
$config.Run.PassThru = $true

$result = Invoke-Pester -Configuration $config

if ($result.FailedCount -gt 0) {
    Write-Host "[tests] FAILED: $($result.FailedCount) test(s) failed" -ForegroundColor Red
    exit 1
}
Write-Host "[tests] PASS: $($result.PassedCount) test(s) passed" -ForegroundColor Green
exit 0
