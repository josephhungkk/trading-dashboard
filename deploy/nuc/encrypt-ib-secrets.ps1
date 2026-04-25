param(
  [string]$CredsPath = 'C:\dashboard\.ib_creds_all.tmp',
  [string]$TotpPath  = 'C:\dashboard\.ib_totp.tmp',
  [string]$OutDir    = 'C:\IBC\secrets'
)

$ErrorActionPreference = 'Stop'

function Normalize-Label {
  param([string]$raw)
  ($raw.Trim() -replace '[\s=]+','-').ToLower()
}

function Write-DPAPI {
  param([string]$Path, [string]$Plaintext)
  $sec = ConvertTo-SecureString $Plaintext -AsPlainText -Force
  $cipher = ConvertFrom-SecureString -SecureString $sec
  Set-Content -Path $Path -Value $cipher -Encoding ASCII -NoNewline
}

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

# ---- parse creds (blank-line separated blocks of 3 lines: label, login, password) ----
$raw = (Get-Content $CredsPath -Raw) -replace "`r`n","`n" -replace "`r","`n"
$blocks = $raw -split "`n{2,}" | Where-Object { $_.Trim() -ne '' }

$labels = New-Object System.Collections.ArrayList
$logins = @{}
foreach ($blk in $blocks) {
  $lines = $blk -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }
  if ($lines.Count -ne 3) { throw "bad block (expected 3 lines, got $($lines.Count)): $($lines -join ' | ')" }
  $label = Normalize-Label $lines[0]
  $login = $lines[1]
  $pw    = $lines[2]
  $expected = @('isa-live','isa-paper','normal-live','normal-paper')
  if ($expected -notcontains $label) { throw "unexpected label '$label' (valid: $expected)" }
  Write-DPAPI -Path (Join-Path $OutDir "$label.password.enc") -Plaintext $pw
  Write-DPAPI -Path (Join-Path $OutDir "$label.login.enc")    -Plaintext $login
  $labels.Add($label) | Out-Null
  $logins[$label] = $login.Length
  Write-Host ("cred encrypted: {0,-14} login_len={1} pw_len={2}" -f $label, $login.Length, $pw.Length)
}

# ---- parse TOTP (one line per live account: "label <base32>") ----
$totpRaw = (Get-Content $TotpPath -Raw) -replace "`r`n","`n" -replace "`r","`n"
foreach ($line in ($totpRaw -split "`n")) {
  $line = $line.Trim()
  if ($line -eq '') { continue }
  $parts = $line -split '\s+',2
  if ($parts.Count -ne 2) { throw "bad totp line: $line" }
  $label  = Normalize-Label $parts[0]
  $secret = ($parts[1] -replace '[\s-]','').ToUpper()
  if ($secret -notmatch '^[A-Z2-7]+=*$') { throw "not valid base32: $label" }
  if (@('isa-live','normal-live') -notcontains $label) {
    Write-Warning "totp label '$label' is not a live account - skipping"
    continue
  }
  Write-DPAPI -Path (Join-Path $OutDir "$label.totp.enc") -Plaintext $secret
  Write-Host ("totp encrypted: {0,-14} secret_len={1}" -f $label, $secret.Length)
}

# ---- emit labels.txt (consumed by TOTP filler + hider) ----
Set-Content -Path (Join-Path $OutDir 'labels.txt') -Value ($labels -join "`n") -Encoding ASCII
Write-Host ''
Get-ChildItem $OutDir | Format-Table Name, Length -AutoSize

# ---- shred temp files ----
foreach ($p in @($CredsPath, $TotpPath)) {
  if (Test-Path $p) {
    # overwrite before delete
    $len = (Get-Item $p).Length
    [System.IO.File]::WriteAllBytes($p, (New-Object byte[] $len))
    Remove-Item $p -Force
    Write-Host ("shredded: {0}" -f $p)
  }
}
