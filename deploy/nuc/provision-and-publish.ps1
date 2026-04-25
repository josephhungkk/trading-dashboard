#Requires -Version 5.1
<#
.SYNOPSIS
    Provision the IBKR sidecar mTLS material and publish the client material
    to the backend's app_secrets store via the admin API (Phase 4 Task 20).

.DESCRIPTION
    Wraps deploy/nuc/provision-sidecar-mtls.ps1 (which generates the CA, four
    server certs, one client cert, and the CRL on the NUC), parses the three
    fenced PEM blocks the provisioner emits to stdout, then PUTs each one
    to the backend's /api/admin/secrets/broker/mtls.{...}_pem endpoint via
    Invoke-RestMethod with the CF Access service-token headers.

    End-to-end automated; no manual operator pipe between cert generation
    and backend secret distribution. Idempotent: PUT is upsert, the
    provisioner is a no-op when certs are still valid.

.PARAMETER ApiBaseUrl
    Backend base URL. Defaults to https://dashboard.kiusinghung.com (CF
    Tunnel hostname). Override for a non-prod target. Trailing slash optional.

.PARAMETER CfClientId
    CF Access service-token client ID. Defaults to $env:CF_ACCESS_CLIENT_ID.

.PARAMETER CfClientSecret
    CF Access service-token client secret. Defaults to
    $env:CF_ACCESS_CLIENT_SECRET.

.PARAMETER ProvisionerPath
    Path to provision-sidecar-mtls.ps1. Defaults to a sibling file resolved
    via $MyInvocation at runtime.

.PARAMETER DryRun
    Parse the PEM blocks but skip the API PUT calls. For testing the script
    against a freshly provisioned secrets dir without touching prod.

.NOTES
    Saved as UTF-8 + BOM + CRLF (memory ps1_nuc_bom_crlf.md). ASCII-only.
    The provisioner's emit format and this parser are tightly coupled by
    fence names; both must change in lockstep.
#>
[CmdletBinding()]
param(
    [string]$ApiBaseUrl = 'https://dashboard.kiusinghung.com',
    [string]$CfClientId = $env:CF_ACCESS_CLIENT_ID,
    [string]$CfClientSecret = $env:CF_ACCESS_CLIENT_SECRET,
    [string]$ProvisionerPath = '',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

if (-not $ProvisionerPath) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProvisionerPath = Join-Path $scriptDir 'provision-sidecar-mtls.ps1'
}
if (-not (Test-Path $ProvisionerPath)) {
    throw "provisioner not found at '$ProvisionerPath'. Pass -ProvisionerPath if it lives elsewhere."
}

if (-not $DryRun) {
    if ([string]::IsNullOrWhiteSpace($CfClientId) -or [string]::IsNullOrWhiteSpace($CfClientSecret)) {
        throw "CF Access service-token missing. Set CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET, or pass -CfClientId / -CfClientSecret. Use -DryRun to skip publishing."
    }
}

$ApiBaseUrl = $ApiBaseUrl.TrimEnd('/')

Write-Host "[publish] running provisioner: $ProvisionerPath" -ForegroundColor Cyan
$provisionerOutput = & $ProvisionerPath
if ($LASTEXITCODE -ne 0) {
    throw "provisioner exited non-zero ($LASTEXITCODE); aborting publish."
}

# Parse the three fenced PEM blocks. Each block is delimited by lines exactly
# matching ==BEGIN <NAME>== and ==END <NAME>==. Stash everything between into
# the corresponding hashtable slot. Anything else (status lines, file size
# rows) is ignored. The fences are unique enough that ambiguity is impossible.
$pems = @{ CLIENT_CERT_PEM = $null; CLIENT_KEY_PEM = $null; CA_BUNDLE_PEM = $null }
$current = $null
$buffer = New-Object System.Text.StringBuilder

foreach ($line in $provisionerOutput) {
    $text = [string]$line
    if ($text -match '^==BEGIN (\w+)==$') {
        $current = $Matches[1]
        [void]$buffer.Clear()
        continue
    }
    if ($text -match '^==END (\w+)==$') {
        if ($current -and $pems.ContainsKey($current)) {
            $pems[$current] = $buffer.ToString().TrimEnd("`r", "`n")
        }
        $current = $null
        [void]$buffer.Clear()
        continue
    }
    if ($current) {
        [void]$buffer.AppendLine($text)
    }
}

foreach ($name in @($pems.Keys)) {
    if ([string]::IsNullOrWhiteSpace($pems[$name])) {
        throw "could not find $name block in provisioner output. The provisioner's emit format may have changed."
    }
    Write-Host ("[publish] parsed {0,-18} ({1} bytes)" -f $name, $pems[$name].Length) -ForegroundColor Cyan
}

# Fence-name -> backend admin-API key. Backend convention: `broker` namespace,
# dotted key like `mtls.client_cert_pem`.
$keyMap = [ordered]@{
    CLIENT_CERT_PEM = 'mtls.client_cert_pem'
    CLIENT_KEY_PEM  = 'mtls.client_key_pem'
    CA_BUNDLE_PEM   = 'mtls.ca_bundle_pem'
}

if ($DryRun) {
    Write-Host "[publish] -DryRun set; skipping API PUT calls." -ForegroundColor Yellow
    return
}

$headers = @{
    'CF-Access-Client-Id'     = $CfClientId
    'CF-Access-Client-Secret' = $CfClientSecret
    'Content-Type'            = 'application/json'
}

foreach ($fenceName in $keyMap.Keys) {
    $key = $keyMap[$fenceName]
    $url = "$ApiBaseUrl/api/admin/secrets/broker/$key"
    # Backend's ValueType is Literal["str","int","bool","json"] — must be "str"
    # not "string" or Pydantic returns 422 Unprocessable Entity.
    $body = @{
        value      = $pems[$fenceName]
        value_type = 'str'
    } | ConvertTo-Json -Depth 3 -Compress

    Write-Host "[publish] PUT $url" -ForegroundColor Cyan
    try {
        $resp = Invoke-RestMethod -Uri $url -Method Put -Headers $headers -Body $body -TimeoutSec 30
        Write-Host ("[publish]   -> updated_at={0}" -f $resp.updated_at) -ForegroundColor Green
    } catch {
        $msg = $_.Exception.Message
        $statusCode = $null
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        throw "PUT $url failed (status=$statusCode): $msg"
    }
}

Write-Host "[publish] all three mTLS secrets published to $ApiBaseUrl." -ForegroundColor Green
