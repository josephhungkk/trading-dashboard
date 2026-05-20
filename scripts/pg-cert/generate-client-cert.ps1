#Requires -Version 5.1
# Generates the dashboard_backend client cert signed by the NUC prod CA.
# Output: C:\dashboard\pg-cert\client.key + client.crt
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CertDir = "C:\dashboard\pg-cert"
$PgUser = "dashboard_user"

if (-not (Test-Path "$CertDir\ca.key")) {
    Write-Host "Run generate-ca.ps1 first"; exit 1
}

$openssl = (Get-Command openssl -ErrorAction Stop).Source

& $openssl genrsa -out "$CertDir\client.key" 4096
& $openssl req -new `
    -key "$CertDir\client.key" `
    -out "$CertDir\client.csr" `
    -subj "/CN=$PgUser"
& $openssl x509 -req -days 3650 `
    -in "$CertDir\client.csr" `
    -CA "$CertDir\ca.crt" `
    -CAkey "$CertDir\ca.key" `
    -CAcreateserial `
    -out "$CertDir\client.crt"
Remove-Item "$CertDir\client.csr"

Write-Host "Client cert generated at $CertDir\"
Write-Host "  client.key + client.crt"
Write-Host ""
Write-Host "Transfer client.key and client.crt to VPS:"
Write-Host "  scp -P 2222 $CertDir\client.* trader@88.208.197.219:/run/secrets/"
Write-Host ""
Write-Host "Add to VPS backend/.env:"
Write-Host "  PG_SSL_CERT_PATH=/run/secrets/client.crt"
Write-Host "  PG_SSL_KEY_PATH=/run/secrets/client.key"
Write-Host "  PG_SSL_CA_PATH=/run/secrets/ca.crt"
