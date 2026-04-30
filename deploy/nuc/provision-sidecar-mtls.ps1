#Requires -Version 5.1
<#
.SYNOPSIS
    Provision the mTLS material for the IBKR sidecar fleet (Phase 4 Task 19).

.DESCRIPTION
    Generates a self-signed root CA, four server certs (one per gateway label),
    one client cert (for the dashboard backend), and an empty CA-signed CRL.
    Output lives under C:\dashboard\secrets\ with restrictive NTFS ACLs so only
    SYSTEM, Administrators, and the current user can read the private keys.

    Idempotent. Re-running skips any artifact whose certificate is still valid
    for at least 30 more days. Use renew-sidecar-mtls.ps1 (annual) or
    revoke-cert.ps1 (compromise response) for rotations.

    Phase 4 design spec: docs/superpowers/specs/2026-04-25-phase4-ibkr-adapter-design.md
    section 4.8 (mTLS provisioning + revocation).

.PARAMETER OutDir
    Where the secret material lands. Defaults to C:\dashboard\secrets\, the
    path baked into the Python sidecar's TLS loader and into the backend's
    app_secrets keys (mtls.client_cert_pem etc.). Override only for test runs.

.PARAMETER OpenSSLPath
    Path to openssl.exe. Defaults to Git-for-Windows's bundled OpenSSL.

.PARAMETER CaCommonName
    CN for the root CA cert. Defaults to "Dashboard mTLS Root CA". Cosmetic.

.PARAMETER ServerSan
    SAN value applied to every server cert. Defaults to "IP:10.10.0.2", the
    NUC's WireGuard interface IP that the backend connects to.

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). ASCII-only.
    Run as the runtime user the sidecars + backend will execute as, so the
    ACL hardening targets the right principal.
#>
[CmdletBinding()]
param(
    [string]$OutDir = 'C:\dashboard\secrets',
    [string]$OpenSSLPath = 'C:\Program Files\Git\usr\bin\openssl.exe',
    [string]$CaCommonName = 'Dashboard mTLS Root CA',
    [string]$ServerSan = 'IP:10.10.0.2'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $OpenSSLPath)) {
    $alt = Get-Command openssl -ErrorAction SilentlyContinue
    if ($alt) { $OpenSSLPath = $alt.Source }
    else {
        throw "openssl not found at '$OpenSSLPath' and not on PATH. Install Git-for-Windows or pass -OpenSSLPath."
    }
}
Write-Host "[mtls] openssl: $OpenSSLPath" -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$caScaffoldDir = Join-Path $OutDir 'ca-db'
New-Item -ItemType Directory -Force -Path $caScaffoldDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $caScaffoldDir 'newcerts') | Out-Null

$indexFile = Join-Path $caScaffoldDir 'index.txt'
$serialFile = Join-Path $caScaffoldDir 'serial'
$crlNumFile = Join-Path $caScaffoldDir 'crlnumber'
# openssl on Windows is picky about CA database file encoding: index.txt must
# be byte-empty (PowerShell's `'' | Out-File` adds a CRLF that openssl's `ca`
# command can't parse), and serial/crlnumber must be ASCII hex with a single
# trailing LF (no CRLF, no BOM).
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
if (-not (Test-Path $indexFile)) { [System.IO.File]::WriteAllBytes($indexFile, @()) }
if (-not (Test-Path $serialFile)) { [System.IO.File]::WriteAllText($serialFile, "1000`n", $utf8NoBom) }
if (-not (Test-Path $crlNumFile)) { [System.IO.File]::WriteAllText($crlNumFile, "1000`n", $utf8NoBom) }

$LABELS = @('isa-live', 'isa-paper', 'normal-live', 'normal-paper', 'futu')

$caKeyPath = Join-Path $OutDir 'ca.key'
$caCertPath = Join-Path $OutDir 'ca.pem'

function Invoke-OpenSSL {
    # Single explicit -OpenSSLArgs param avoids PowerShell parameter-binder
    # ambiguity around openssl flags like `-out` (which would otherwise prefix-
    # match the cmdlet's -OutVariable common parameter).
    #
    # OpenSSL writes RSA-keygen progress dots and other diagnostics to stderr.
    # Under $ErrorActionPreference='Stop', PowerShell would surface those as
    # terminating errors even on success. We merge 2>&1 and rely on exit code.
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
    # Quiet on success: openssl's progress dots clutter the log without adding
    # information. Re-enable by changing ForEach-Object to Write-Host if needed.
}

function Test-CertNeedsIssue {
    # Returns $true if the cert is missing OR expires within 30 days.
    param([string]$CertPath)
    if (-not (Test-Path $CertPath)) { return $true }
    $endDate = & $OpenSSLPath x509 -enddate -noout -in $CertPath 2>$null
    if (-not $endDate) { return $true }
    $endDate = $endDate -replace 'notAfter=', ''
    try {
        $notAfter = [DateTime]::ParseExact(
            $endDate.Trim(),
            'MMM d HH:mm:ss yyyy GMT',
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AssumeUniversal
        )
    } catch {
        Write-Host "[mtls] could not parse expiry '$endDate' from $CertPath; treating as expired" -ForegroundColor Yellow
        return $true
    }
    $remaining = $notAfter - (Get-Date).ToUniversalTime()
    if ($remaining.TotalDays -lt 30) { return $true }
    Write-Host ("[mtls] {0} valid for {1:N0} more days; skipping reissue" -f (Split-Path -Leaf $CertPath), $remaining.TotalDays) -ForegroundColor Green
    return $false
}

function Write-OpenSSLConfig {
    param([string]$Path, [string]$Body)
    [System.IO.File]::WriteAllText($Path, $Body, (New-Object System.Text.UTF8Encoding $false))
}

# 1. Root CA (10y)
if (Test-CertNeedsIssue -CertPath $caCertPath) {
    Write-Host "[mtls] generating root CA (10y, RSA-4096)..." -ForegroundColor Cyan
    Invoke-OpenSSL -OpenSSLArgs @('genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:4096', '-out', $caKeyPath)
    Invoke-OpenSSL -OpenSSLArgs @('req', '-x509', '-new', '-nodes', '-key', $caKeyPath, '-sha256', '-days', '3650', '-subj', "/CN=$CaCommonName", '-out', $caCertPath)
}

# CA config used by `openssl ca -gencrl`. Forward slashes throughout because
# openssl on Windows treats backslashes as escapes inside config files.
$caConfigPath = Join-Path $caScaffoldDir 'ca.cnf'
$caConfigBody = @"
[ca]
default_ca = empty_ca

[empty_ca]
dir              = $($caScaffoldDir -replace '\\','/')
database         = `$dir/index.txt
serial           = `$dir/serial
new_certs_dir    = `$dir/newcerts
crlnumber        = `$dir/crlnumber
certificate      = $($caCertPath -replace '\\','/')
private_key      = $($caKeyPath -replace '\\','/')
default_md       = sha256
default_crl_days = 30
default_days     = 365
policy           = anything

[anything]
commonName = supplied
"@
Write-OpenSSLConfig -Path $caConfigPath -Body $caConfigBody

# 2 + 3. Leaf cert helper. Writes openssl x509 extensions to a temp file because
# `-extfile` reads them from disk (no inline form in openssl x509).
function New-LeafCert {
    param(
        [string]$KeyPath,
        [string]$CertPath,
        [string]$CommonName,
        [string]$ExtraExtensions
    )
    if (-not (Test-CertNeedsIssue -CertPath $CertPath)) { return }

    Write-Host "[mtls] issuing leaf cert: $CommonName" -ForegroundColor Cyan
    $tmpCsr = "$CertPath.csr.tmp"
    $tmpExt = "$CertPath.ext.tmp"
    try {
        Invoke-OpenSSL -OpenSSLArgs @('genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', $KeyPath)
        Invoke-OpenSSL -OpenSSLArgs @('req', '-new', '-key', $KeyPath, '-subj', "/CN=$CommonName", '-out', $tmpCsr)
        Write-OpenSSLConfig -Path $tmpExt -Body $ExtraExtensions
        Invoke-OpenSSL -OpenSSLArgs @(
            'x509', '-req', '-in', $tmpCsr,
            '-CA', $caCertPath, '-CAkey', $caKeyPath, '-CAcreateserial',
            '-out', $CertPath, '-days', '365', '-sha256',
            '-extfile', $tmpExt
        )
    }
    finally {
        Remove-Item -Force -ErrorAction SilentlyContinue $tmpCsr, $tmpExt
    }
}

foreach ($label in $LABELS) {
    $serverKey = Join-Path $OutDir "sidecar-$label.key"
    $serverCrt = Join-Path $OutDir "sidecar-$label.crt"
    $sanBody = "subjectAltName=$ServerSan,DNS:sidecar-$label`r`nextendedKeyUsage=serverAuth"
    New-LeafCert -KeyPath $serverKey -CertPath $serverCrt -CommonName "sidecar-$label" -ExtraExtensions $sanBody
}

$clientKey = Join-Path $OutDir 'client-backend.key'
$clientCrt = Join-Path $OutDir 'client-backend.crt'
New-LeafCert -KeyPath $clientKey -CertPath $clientCrt -CommonName 'dashboard-backend' -ExtraExtensions 'extendedKeyUsage=clientAuth'

# 4. Empty CRL (regenerated on every run so the file's notBefore/notAfter
# stay fresh; revoke-cert.ps1 in Task 21 will handle non-empty CRLs).
$crlPath = Join-Path $OutDir 'crl.pem'
Write-Host "[mtls] generating CRL..." -ForegroundColor Cyan
Invoke-OpenSSL -OpenSSLArgs @('ca', '-config', $caConfigPath, '-gencrl', '-out', $crlPath)

# 5. ACL hardening: SYSTEM:F + Administrators:F + current user:RW. Disable
# inheritance so anything dropped on the parent dir's ACL list (e.g. Users
# read-all) doesn't leak to private keys.
#
# Set-Acl needs SeSecurityPrivilege which requires elevation. When the
# script is run from a non-elevated shell we emit a warning and continue
# rather than aborting, because the cert material itself is still on disk
# and gitignored. Operators should re-run elevated to harden in place.
Write-Host "[mtls] tightening ACL on $OutDir (SYSTEM + Administrators + $env:USERNAME only)..." -ForegroundColor Cyan
$acl = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
    'NT AUTHORITY\SYSTEM', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'
)))
$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
    'BUILTIN\Administrators', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'
)))
$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
    "$env:USERDOMAIN\$env:USERNAME", 'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow'
)))
try {
    Set-Acl -Path $OutDir -AclObject $acl
} catch [System.Security.AccessControl.PrivilegeNotHeldException] {
    Write-Warning "[mtls] ACL hardening skipped: not running elevated (SeSecurityPrivilege not held). Re-run as admin to lock down $OutDir."
} catch {
    Write-Warning "[mtls] ACL hardening failed: $($_.Exception.Message). Re-run as admin if this is a security boundary."
}

# 6. Summary + client material echo for provision-and-publish.ps1 to capture.
Write-Host ""
Write-Host "[mtls] secrets in $OutDir :" -ForegroundColor Green
Get-ChildItem -Path $OutDir -File | ForEach-Object {
    Write-Host ("  {0,-30} {1,8} bytes" -f $_.Name, $_.Length)
}

# Emit fenced PEM blocks on the success stream (NOT Write-Host) so the wrapper
# script provision-and-publish.ps1 can capture them via `$x = & ./this.ps1`.
# Get-Content (no -Raw) emits one line per pipeline item so the wrapper's
# line-by-line regex matches the ==BEGIN==/==END== markers correctly.
Write-Output ''
Write-Output '==BEGIN CLIENT_CERT_PEM=='
Get-Content $clientCrt
Write-Output '==END CLIENT_CERT_PEM=='
Write-Output ''
Write-Output '==BEGIN CLIENT_KEY_PEM=='
Get-Content $clientKey
Write-Output '==END CLIENT_KEY_PEM=='
Write-Output ''
Write-Output '==BEGIN CA_BUNDLE_PEM=='
Get-Content $caCertPath
Write-Output '==END CA_BUNDLE_PEM=='
