#Requires -Version 5.1
<#
.SYNOPSIS
    Revoke a leaf cert from the IBKR sidecar mTLS CA (Phase 4 Task 21).

.DESCRIPTION
    Adds the named certificate to the CA's revocation list and regenerates
    C:\dashboard\secrets\crl.pem. The sidecars reload the CRL every 60s and
    rebuild their gRPC SSL context, so within one minute the revoked cert
    will be rejected at TLS handshake time.

    Compromise-response path. For planned annual rotation use
    renew-sidecar-mtls.ps1; for full root-CA rotation, follow
    RUNBOOK-mtls-recovery.md.

.PARAMETER CertPath
    Path to the cert PEM to revoke. Pass either the full path
    (e.g. C:\dashboard\secrets\sidecar-isa-paper.crt) or just the leaf name
    relative to the secrets dir.

.PARAMETER SecretsDir
    Secrets directory holding the CA + the cert. Defaults to
    C:\dashboard\secrets, matching provision-sidecar-mtls.ps1.

.PARAMETER OpenSSLPath
    Path to openssl.exe. Defaults to Git-for-Windows's bundled OpenSSL.

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). ASCII-only.
    Requires the CA scaffold (ca-db/ca.cnf + index.txt + crlnumber) created
    by provision-sidecar-mtls.ps1. If the CA scaffold is missing, run the
    provisioner first.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$CertPath,
    [string]$SecretsDir = 'C:\dashboard\secrets',
    [string]$OpenSSLPath = 'C:\Program Files\Git\usr\bin\openssl.exe'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $OpenSSLPath)) {
    $alt = Get-Command openssl -ErrorAction SilentlyContinue
    if (-not $alt) {
        throw "openssl not found at '$OpenSSLPath' or on PATH."
    }
    $OpenSSLPath = $alt.Source
}

# Resolve cert path: accept either absolute or relative-to-SecretsDir.
if (-not (Test-Path $CertPath)) {
    $candidate = Join-Path $SecretsDir $CertPath
    if (Test-Path $candidate) {
        $CertPath = $candidate
    } else {
        throw "cert not found at '$CertPath' or '$candidate'."
    }
}

$caConfigPath = Join-Path $SecretsDir 'ca-db\ca.cnf'
$crlPath = Join-Path $SecretsDir 'crl.pem'
foreach ($p in $caConfigPath, $crlPath) {
    if (-not (Test-Path $p)) {
        throw "missing CA scaffold file '$p'. Run provision-sidecar-mtls.ps1 first."
    }
}

function Invoke-OpenSSL {
    param([Parameter(Mandatory)][string[]]$OpenSSLArgs)
    $previousPref = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $OpenSSLPath @OpenSSLArgs 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPref
    }
    if ($exitCode -ne 0) {
        $output | ForEach-Object { Write-Host $_ -ForegroundColor Red }
        throw "openssl failed (exit $exitCode): $($OpenSSLArgs -join ' ')"
    }
}

# 1. Confirm what's about to be revoked so the operator catches mistakes.
Write-Host "[revoke] target cert:" -ForegroundColor Cyan
$subject = & $OpenSSLPath x509 -noout -subject -in $CertPath
$serial = & $OpenSSLPath x509 -noout -serial -in $CertPath
Write-Host "  $subject"
Write-Host "  $serial"

# 2. Append the revocation row to index.txt via `openssl ca -revoke`. This
# writes a row of shape `R<tab>YYMMDDHHMMSSZ<tab>YYMMDDHHMMSSZ<tab>serial
# <tab>unknown<tab>/CN=...` to index.txt and bumps index.txt.attr.
Write-Host "[revoke] appending to CA database..." -ForegroundColor Cyan
Invoke-OpenSSL -OpenSSLArgs @('ca', '-config', $caConfigPath, '-revoke', $CertPath)

# 3. Regenerate the CRL. The sidecars' file-CRL reloader picks this up at
# its next 60s tick and rebuilds the gRPC SSL context.
Write-Host "[revoke] regenerating CRL..." -ForegroundColor Cyan
Invoke-OpenSSL -OpenSSLArgs @('ca', '-config', $caConfigPath, '-gencrl', '-out', $crlPath)

# 4. Bump the CRL file's mtime explicitly. The sidecars reload only when
# mtime changes; openssl already updates it via the -gencrl write, but doing
# it again is cheap and lets the operator know the file was touched.
(Get-Item $crlPath).LastWriteTime = Get-Date

# 5. Echo the new CRL contents so the operator can confirm what's in it.
Write-Host "[revoke] CRL now contains:" -ForegroundColor Green
& $OpenSSLPath crl -in $crlPath -text -noout |
    Select-String -Pattern '^\s*(Serial Number:|Revocation Date:|Revoked Certificates:)' |
    ForEach-Object { Write-Host "  $_" }

Write-Host "[revoke] sidecars will pick up the new CRL within 60s." -ForegroundColor Yellow
