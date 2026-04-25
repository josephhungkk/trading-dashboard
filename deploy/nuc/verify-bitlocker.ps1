#Requires -Version 5.1
<#
.SYNOPSIS
    Read-only verification that BitLocker is encrypting the system drive on
    the NUC (Phase 4.5 - paired with setup-autologon.ps1).

.DESCRIPTION
    Without BitLocker, the LSA Secrets store (which holds the AutoAdminLogon
    password set by setup-autologon.ps1) and the DPAPI-encrypted IBC
    secrets in C:\IBC\secrets\ can be extracted offline by anyone who can
    boot a recovery USB and read the SAM + SYSTEM hives. BitLocker on C:
    closes that path.

    This script does NOT enable BitLocker - that's destructive (can take
    hours, requires recovery-key handling) and the operator should drive
    it interactively. It only reports current state and points at the
    right command if encryption is missing or partial.

.PARAMETER MountPoint
    Drive to check. Defaults to C: (the system drive - the one that
    matters because it holds Windows + LSA + IBC secrets).

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). ASCII-only.
    Read-only. The BitLocker module ships with Windows 10/11 Pro and
    Enterprise; Home editions don't have it (and don't have BitLocker).
#>
[CmdletBinding()]
param(
    [string]$MountPoint = 'C:'
)

$ErrorActionPreference = 'Stop'

# Get-BitLockerVolume requires the BitLocker module. On Home editions or
# stripped Server installs it's missing entirely - surface that as a
# distinct failure mode, not as "encryption disabled".
if (-not (Get-Module -ListAvailable -Name BitLocker)) {
    Write-Host "[verify-bitlocker] FAIL - BitLocker module not available." -ForegroundColor Red
    Write-Host "  This is a Pro/Enterprise feature. Confirm Windows edition with: (Get-CimInstance Win32_OperatingSystem).Caption" -ForegroundColor Yellow
    exit 1
}

try {
    $vol = Get-BitLockerVolume -MountPoint $MountPoint -ErrorAction Stop
} catch {
    $msg = $_.Exception.Message
    if ($msg -match 'Access denied|access is denied|UnauthorizedAccess') {
        Write-Host "[verify-bitlocker] FAIL - Access denied. Re-run from an elevated PowerShell (Run as Administrator)." -ForegroundColor Red
        Write-Host "  Get-BitLockerVolume on C: requires admin even for read access." -ForegroundColor Yellow
    } else {
        Write-Host "[verify-bitlocker] FAIL - Get-BitLockerVolume failed: $msg" -ForegroundColor Red
    }
    exit 1
}

$failures = @()

# ProtectionStatus values: Off (0), On (1), Unknown (2). On is the only
# state that says "the data on disk is actually encrypted right now".
if ($vol.ProtectionStatus -ne 'On') {
    $failures += "ProtectionStatus = '$($vol.ProtectionStatus)' (expected 'On'). Enable with elevated PowerShell: Enable-BitLocker -MountPoint '$MountPoint' -EncryptionMethod XtsAes256 -UsedSpaceOnly -TpmProtector"
}

# VolumeStatus must be FullyEncrypted; EncryptionInProgress is partial
# protection (writes during the in-progress window can land cleartext on
# disk).
if ($vol.VolumeStatus -ne 'FullyEncrypted') {
    $failures += "VolumeStatus = '$($vol.VolumeStatus)' (expected 'FullyEncrypted'). Wait for encryption to complete or run: manage-bde -status $MountPoint"
}

# EncryptionPercentage 100 confirms the on-disk plaintext has been
# overwritten with ciphertext; <100 means parts of the volume are still
# cleartext underneath.
if ($vol.EncryptionPercentage -ne 100) {
    $failures += "EncryptionPercentage = $($vol.EncryptionPercentage)% (expected 100). Encryption may still be in progress; check again in 30 minutes."
}

# Need at least one persistent key protector. RecoveryPassword is the
# 48-digit numerical recovery key the operator should have stashed
# offline; TpmProtector is the unattended-boot anchor (without it, the
# operator must type a PIN at boot - incompatible with auto-login).
$protectors = $vol.KeyProtector
$tpm = $protectors | Where-Object { $_.KeyProtectorType -eq 'Tpm' }
$recovery = $protectors | Where-Object { $_.KeyProtectorType -eq 'RecoveryPassword' }

if (-not $tpm) {
    $failures += "No TPM protector. Without it, BitLocker prompts for a PIN at boot which would break headless / auto-login. Add with: Add-BitLockerKeyProtector -MountPoint '$MountPoint' -TpmProtector"
}
if (-not $recovery) {
    $failures += "No RecoveryPassword protector. If the TPM ever resets, you'll lose access without a recovery key. Add with: Add-BitLockerKeyProtector -MountPoint '$MountPoint' -RecoveryPasswordProtector"
}

Write-Host "[verify-bitlocker] BitLocker state for $MountPoint" -ForegroundColor Cyan
Write-Host ("  ProtectionStatus     = {0}" -f $vol.ProtectionStatus)
Write-Host ("  VolumeStatus         = {0}" -f $vol.VolumeStatus)
Write-Host ("  EncryptionMethod     = {0}" -f $vol.EncryptionMethod)
Write-Host ("  EncryptionPercentage = {0}%" -f $vol.EncryptionPercentage)
Write-Host ("  KeyProtectors        : {0}" -f (($protectors | ForEach-Object { $_.KeyProtectorType }) -join ', '))
if ($recovery) {
    # Print only the count + ID, never the actual recovery password. The
    # operator should have it stashed offline already.
    $kpId = if ($recovery -is [array]) { $recovery[0].KeyProtectorId } else { $recovery.KeyProtectorId }
    $kpCount = if ($recovery -is [array]) { $recovery.Count } else { 1 }
    Write-Host ("  RecoveryPassword KP  : {0} entr(y/ies); ID = {1}" -f $kpCount, $kpId)
    Write-Host "  (recovery password NOT printed - read it manually if needed)" -ForegroundColor DarkGray
}

if ($failures.Count -gt 0) {
    Write-Host ""
    Write-Host "[verify-bitlocker] FAIL ($($failures.Count) issue(s)):" -ForegroundColor Red
    foreach ($f in $failures) { Write-Host "  - $f" -ForegroundColor Red }
    exit 1
}

Write-Host ""
Write-Host "[verify-bitlocker] PASS - $MountPoint is fully encrypted with TPM + RecoveryPassword protectors." -ForegroundColor Green
exit 0
