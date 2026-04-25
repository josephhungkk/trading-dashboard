#Requires -Version 5.1
<#
.SYNOPSIS
    Configure Windows AutoAdminLogon via Sysinternals Autologon so the NUC
    signs in unattended at boot (Phase 4.5 - operator pre-flight before the
    sidecar fleet runs headless).

.DESCRIPTION
    Downloads Sysinternals Autologon to C:\dashboard\tools\AutoLogon\,
    accepts the EULA once (per HKCU), then runs Autologon64.exe with
    user + domain + password so the password lands in LSA Secrets
    (HKLM\SECURITY\Policy\Secrets\DefaultPassword) instead of as plain
    text under HKLM\...\Winlogon\DefaultPassword. LSA Secrets are
    encrypted with the machine LSA key - anyone with admin + physical
    access can still extract them via mimikatz, but they're not visible
    to a normal regedit walk.

    The password is read from the operator at runtime via
    Read-Host -AsSecureString and passed to Autologon64.exe through a
    plaintext ConvertTo-... step in this process's memory only - never
    written to disk, never echoed to history.

    Re-running with a new password rotates the stored secret. Disable
    with: & Autologon64.exe -d  (wipes the LSA secret + sets
    AutoAdminLogon back to 0).

.PARAMETER UserName
    The Windows account that should auto-login. Defaults to
    $env:USERNAME (the operator running the script). For a domain
    account pass the SAM name without the prefix; pair with -Domain.

.PARAMETER Domain
    Domain or computer name. Defaults to $env:COMPUTERNAME for local
    accounts. For a domain account pass the NetBIOS domain name.

.PARAMETER ToolsDir
    Where to land Autologon64.exe. Defaults to C:\dashboard\tools\AutoLogon\.

.PARAMETER DownloadUrl
    Sysinternals AutoLogon ZIP URL. Defaults to the official Microsoft
    download host; override only if you've staged a mirror.

.PARAMETER Reuse
    Skip the download step if Autologon64.exe is already present locally.
    Useful when re-running to rotate the password.

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). ASCII-only.
    Run elevated. AppLocker / WDAC may block the EXE; pre-stage if so.
    BitLocker on C: is strongly recommended - without disk encryption,
    LSA secrets can be extracted offline by anyone who can boot a
    recovery USB.

    Verify with deploy/nuc/verify-autologon.ps1 (read-only check).
#>
[CmdletBinding()]
param(
    [string]$UserName = $env:USERNAME,
    [string]$Domain = $env:COMPUTERNAME,
    [string]$ToolsDir = 'C:\dashboard\tools\AutoLogon',
    [string]$DownloadUrl = 'https://download.sysinternals.com/files/AutoLogon.zip',
    [switch]$Reuse
)

$ErrorActionPreference = 'Stop'

# Refuse to run from a non-elevated shell - Autologon64.exe needs SeTcbPrivilege
# (effectively admin) to write LSA Secrets. Running unprivileged silently
# fails to write the secret while still toggling AutoAdminLogon=1, which
# leaves the system in a broken state where Winlogon expects a password it
# can't read.
$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run from an elevated PowerShell - Autologon64.exe needs admin to write LSA Secrets."
}

$exePath = Join-Path $ToolsDir 'Autologon64.exe'

if ($Reuse -and (Test-Path $exePath)) {
    Write-Host "[autologon] reusing existing $exePath" -ForegroundColor Cyan
} else {
    Write-Host "[autologon] downloading Sysinternals AutoLogon..." -ForegroundColor Cyan
    if (-not (Test-Path $ToolsDir)) {
        New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
    }
    $zipPath = Join-Path $ToolsDir 'AutoLogon.zip'
    Invoke-WebRequest -Uri $DownloadUrl -OutFile $zipPath -UseBasicParsing
    Expand-Archive -Path $zipPath -DestinationPath $ToolsDir -Force
    Remove-Item -Force $zipPath
    if (-not (Test-Path $exePath)) {
        throw "Autologon64.exe not found in $ToolsDir after extract. Inspect the ZIP contents manually."
    }
}

# Accept the Sysinternals EULA once (writes to HKCU under SOFTWARE\Sysinternals).
# Without this, Autologon64.exe pops a blocking dialog on first run.
& $exePath /accepteula | Out-Null

Write-Host "[autologon] target: $Domain\$UserName" -ForegroundColor Cyan
$secure = Read-Host -Prompt "Password for $Domain\$UserName" -AsSecureString
if ($secure.Length -eq 0) {
    throw "Empty password rejected - would brick AutoAdminLogon. Pass a real password or run -Reuse to skip."
}

# Convert SecureString -> plaintext only at the moment of invocation. The
# plaintext lives in process memory just long enough to call the EXE, then
# we zero the BSTR explicitly. PowerShell's GC is async; the explicit
# zeroing is the strongest mitigation we have here.
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    # Autologon64.exe positional args: <user> <domain> <password>
    $output = & $exePath $UserName $Domain $plain 2>&1
    $exit = $LASTEXITCODE
    $plain = $null
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
[GC]::Collect()

# Autologon64.exe is a GUI app even when invoked headless - it returns 0
# on success and a non-zero on failure but emits little to stdout. Treat
# any non-zero exit as a hard failure and surface whatever it did say.
if ($exit -ne 0) {
    Write-Host ($output | Out-String) -ForegroundColor Red
    throw "Autologon64.exe failed (exit=$exit). LSA secret may be partially written; run with -d to wipe."
}

Write-Host "[autologon] LSA secret written for $Domain\$UserName." -ForegroundColor Green
Write-Host "[autologon] verify with: powershell -ExecutionPolicy Bypass -File C:\dashboard\deploy\nuc\verify-autologon.ps1" -ForegroundColor Yellow
Write-Host "[autologon] reboot to confirm. Disable later with: & '$exePath' -d" -ForegroundColor Yellow
