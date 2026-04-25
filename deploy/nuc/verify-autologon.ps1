#Requires -Version 5.1
<#
.SYNOPSIS
    Read-only verification that Sysinternals Autologon configured the NUC
    correctly (Phase 4.5 -paired with setup-autologon.ps1).

.DESCRIPTION
    Inspects the Winlogon registry keys without printing secret values.
    Confirms:

      * AutoAdminLogon          == "1"
      * DefaultUserName         is set + matches the operator's expectation
      * DefaultDomainName       is set
      * DefaultPassword         is NOT in the registry (good -Sysinternals
                                Autologon stores it in LSA Secrets instead;
                                a value here means someone configured plain
                                AutoAdminLogon by hand and the password is
                                exposed cleartext)

    Prints a PASS/FAIL summary. Non-zero exit on failure so this is safe
    to wire into CI / pre-flight scripts later.

.PARAMETER ExpectedUserName
    Expected DefaultUserName. Defaults to $env:USERNAME (the operator
    running the verifier). Pass the real autologon target if the operator
    runs the verify under a different account than the autologin one.

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). ASCII-only.
    Read-only -does not require elevation. The DefaultPassword value
    under HKLM\SECURITY can only be inspected from SYSTEM context, so we
    deliberately do NOT try to read it; "not in plain Winlogon" + "EXE
    succeeded" is the strongest signal we can give.
#>
[CmdletBinding()]
param(
    [string]$ExpectedUserName = $env:USERNAME
)

$ErrorActionPreference = 'Stop'

$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'

function Get-WinlogonValue {
    param([string]$Name)
    try {
        return (Get-ItemProperty -Path $winlogon -Name $Name -ErrorAction Stop).$Name
    } catch {
        return $null
    }
}

$autoAdminLogon = Get-WinlogonValue -Name 'AutoAdminLogon'
$defaultUser = Get-WinlogonValue -Name 'DefaultUserName'
$defaultDomain = Get-WinlogonValue -Name 'DefaultDomainName'
$defaultPassword = Get-WinlogonValue -Name 'DefaultPassword'

$failures = @()

if ($autoAdminLogon -ne '1') {
    $failures += "AutoAdminLogon != '1' (got '$autoAdminLogon'). Run setup-autologon.ps1."
}
if ([string]::IsNullOrWhiteSpace($defaultUser)) {
    $failures += "DefaultUserName empty. Run setup-autologon.ps1."
} elseif ($defaultUser -ne $ExpectedUserName) {
    $failures += "DefaultUserName='$defaultUser' but expected '$ExpectedUserName'. Re-run setup-autologon.ps1 -UserName <correct>."
}
if ([string]::IsNullOrWhiteSpace($defaultDomain)) {
    $failures += "DefaultDomainName empty. Re-run setup-autologon.ps1 -Domain <name>."
}
if ($defaultPassword) {
    # If this exists at all, it means someone wrote a plain AutoAdminLogon
    # config bypassing Sysinternals Autologon -the password is sitting in
    # cleartext in the registry. Loud warning, but we don't print the value.
    $failures += "DefaultPassword IS present in cleartext under $winlogon. Wipe it with: Remove-ItemProperty -Path '$winlogon' -Name DefaultPassword (then re-run setup-autologon.ps1 to put it in LSA Secrets instead)."
}

# Compatibility: the ?? null-coalescing operator landed in PowerShell 7.
# This script targets PS 5.1 (matches the rest of deploy/nuc), so use a
# helper that works on both.
function Display { param($v) if ($null -eq $v -or $v -eq '') { '<unset>' } else { $v } }

Write-Host "[verify-autologon] Winlogon registry state:" -ForegroundColor Cyan
Write-Host ("  AutoAdminLogon    = {0}" -f (Display $autoAdminLogon))
Write-Host ("  DefaultUserName   = {0}" -f (Display $defaultUser))
Write-Host ("  DefaultDomainName = {0}" -f (Display $defaultDomain))
Write-Host ("  DefaultPassword   = {0}" -f $(if ($defaultPassword) { "<CLEARTEXT -REMOVE>" } else { "<not present -good (LSA Secrets path)>" }))

if ($failures.Count -gt 0) {
    Write-Host ""
    Write-Host "[verify-autologon] FAIL ($($failures.Count) issue(s)):" -ForegroundColor Red
    foreach ($f in $failures) { Write-Host "  - $f" -ForegroundColor Red }
    exit 1
}

Write-Host ""
Write-Host "[verify-autologon] PASS -autologon configured for $defaultDomain\$defaultUser via LSA Secrets." -ForegroundColor Green
exit 0
