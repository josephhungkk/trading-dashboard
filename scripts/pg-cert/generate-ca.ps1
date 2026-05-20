#Requires -Version 5.1
# Generates the NUC PROD CA for PG client-cert auth.
# Output: C:\dashboard\pg-cert\ca.key (DACL: trader only), ca.crt
# This CA must NEVER be used for the WSL dev instance.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CertDir = "C:\dashboard\pg-cert"
if (-not (Test-Path $CertDir)) {
    New-Item -ItemType Directory -Path $CertDir | Out-Null
}

if (Test-Path "$CertDir\ca.key") {
    Write-Host "Prod CA already exists at $CertDir\ca.key — remove manually to regenerate."
    exit 0
}

# Use openssl from Git for Windows or from PATH
$openssl = (Get-Command openssl -ErrorAction Stop).Source

& $openssl genrsa -out "$CertDir\ca.key" 4096
& $openssl req -new -x509 -days 3650 `
    -key "$CertDir\ca.key" `
    -out "$CertDir\ca.crt" `
    -subj "/CN=DashboardNUCProdCA/O=DashboardProd"

# Restrict ca.key to trader user only
$acl = Get-Acl "$CertDir\ca.key"
$acl.SetAccessRuleProtection($true, $false)
$trader = [System.Security.Principal.NTAccount]"$env:USERDOMAIN\$env:USERNAME"
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $trader, "FullControl", "Allow"
)
$acl.AddAccessRule($rule)
Set-Acl "$CertDir\ca.key" $acl

Write-Host "Prod CA generated at $CertDir\"
Write-Host "  ca.key: DACL restricted to $($env:USERNAME)"
Write-Host "  ca.crt: $CertDir\ca.crt"
Write-Host ""
Write-Host "IMPORTANT: Transfer ca.crt (NOT ca.key) to VPS via WireGuard SSH only."
