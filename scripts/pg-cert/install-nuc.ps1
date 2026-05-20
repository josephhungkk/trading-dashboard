#Requires -Version 5.1
# Installs client-cert auth on the NUC prod PG18 instance.
# Patches pg_hba.conf and postgresql.conf, restricts key DACL.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CertDir = "C:\dashboard\pg-cert"
# PG data dir — NUC prod PG18 on Windows native
$PgData = $env:PGDATA
if (-not $PgData) {
    $PgData = "C:\Program Files\PostgreSQL\18\data"
}

if (-not (Test-Path "$CertDir\ca.crt")) {
    Write-Host "Run generate-ca.ps1 first"; exit 1
}
if (-not (Test-Path "$CertDir\client.crt")) {
    Write-Host "Run generate-client-cert.ps1 first"; exit 1
}

Write-Host "Copying CA cert to PG data dir..."
Copy-Item "$CertDir\ca.crt" "$PgData\dashboard-ca.crt" -Force

# Enable SSL in postgresql.conf
$pgConf = "$PgData\postgresql.conf"
$content = Get-Content $pgConf -Raw
if ($content -notmatch "ssl = on") {
    Add-Content $pgConf "`nssl = on"
}
if ($content -notmatch "ssl_ca_file") {
    Add-Content $pgConf "`nssl_ca_file = 'dashboard-ca.crt'"
} else {
    $content = $content -replace "#*ssl_ca_file.*", "ssl_ca_file = 'dashboard-ca.crt'"
    Set-Content $pgConf $content
}

# Patch pg_hba.conf
$hbaPath = "$PgData\pg_hba.conf"
$hba = Get-Content $hbaPath -Raw
# Comment out existing password line
$hba = $hba -replace "^(host\s+dashboard\s+dashboard_user\s+10\.10\.0\.0/24\s+scram-sha-256)", "# `$1"
# Add cert line if not present
if ($hba -notmatch "cert clientcert=verify-full") {
    $hba += "`nhostssl  dashboard  dashboard_user  10.10.0.0/24  cert  clientcert=verify-full"
    $hba += "`n# ROLLBACK: uncomment the scram-sha-256 line above and reload PG"
}
Set-Content $hbaPath $hba

Write-Host "Reloading PG configuration (pg_ctl reload)..."
$pgCtl = "C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe"
if (Test-Path $pgCtl) {
    & $pgCtl reload -D $PgData
} else {
    Write-Host "pg_ctl not found — reload PG manually via pg_ctl reload or service restart."
}

Write-Host ""
Write-Host "Done. Verify from WSL:"
Write-Host "  psql 'postgresql://dashboard_user@10.10.0.2:5432/dashboard?sslmode=verify-full' -c '\conninfo'"
Write-Host ""
Write-Host "ROLLBACK: uncomment scram-sha-256 in $hbaPath then reload PG"
