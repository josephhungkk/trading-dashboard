# FutuOpenD Sidecar Setup Runbook (Phase 6, v0.6.0)

One-time operator setup. ~30 minutes end-to-end.

## 1. Install FutuOpenD on the NUC

Download `FutuOpenD-Windows.zip` from `https://www.futunn.com/en-US/download/openAPI`.
Extract to `C:\FutuOpenD\`. Run `FutuOpenD.exe`.

Configure via web UI: login with Futu account, OpenD listen port = `11111`,
"Allow API connection" = ON, set Trading password (your Futu trading PIN).

```powershell
Test-NetConnection -ComputerName 127.0.0.1 -Port 11111
```

Expected: `TcpTestSucceeded: True`.

## 2. Generate 1024-bit RSA keypair

**CRITICAL:** Futu requires 1024-bit (per memory `futu_1024_rsa_key.md`); 2048-bit fails InitConnect with `ProtobufBody Parse Err!`.

```powershell
cd C:\dashboard\secrets\
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:1024 -out futu-priv-tmp.pem
openssl pkcs8 -topk8 -nocrypt -in futu-priv-tmp.pem -out futu-priv.pem
openssl rsa -in futu-priv.pem -pubout -out futu-pub.pem
Remove-Item futu-priv-tmp.pem
```

## 3. Configure OpenD with the public key

In FutuOpenD web UI: Settings -> API -> "RSA Public Key" -> paste contents of `futu-pub.pem`.
Click Save. Note your "Connection ID" (e.g. `default_conn`).

## 4. Compute MD5 of trading password

```powershell
$pwd = Read-Host -AsSecureString "Trading password"
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($pwd))
$md5 = [System.BitConverter]::ToString([Security.Cryptography.MD5]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($plain))).Replace("-","").ToLower()
Write-Host $md5
```

## 5. Seed app_secrets + app_config

From WSL with `CF_ACCESS_CLIENT_ID/SECRET` set:

```bash
RSA_PEM=$(cat /mnt/c/dashboard/secrets/futu-priv.pem)
MD5=<32-char hex from step 4>
CONN_ID=<from step 3>

# Encrypted secrets:
curl -sf -X POST https://dashboard.kiusinghung.com/api/admin/secrets \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg pem "$RSA_PEM" '{namespace:"broker", key:"futu.rsa_priv_pem", value:$pem, value_type:"string"}')"

curl -sf -X POST https://dashboard.kiusinghung.com/api/admin/secrets \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d "{\"namespace\":\"broker\",\"key\":\"futu.unlock_pwd_md5\",\"value\":\"$MD5\",\"value_type\":\"string\"}"

# Plain config:
curl -sf -X POST https://dashboard.kiusinghung.com/api/admin/config \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"namespace":"broker","key":"futu.opend_host","value":"127.0.0.1","value_type":"string"}'

curl -sf -X POST https://dashboard.kiusinghung.com/api/admin/config \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"namespace":"broker","key":"futu.opend_port","value":"11111","value_type":"int"}'

curl -sf -X POST https://dashboard.kiusinghung.com/api/admin/config \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d "{\"namespace\":\"broker\",\"key\":\"futu.connection_id\",\"value\":\"$CONN_ID\",\"value_type\":\"string\"}"
```

## 6. Wipe local plaintext

```powershell
Remove-Item C:\dashboard\secrets\futu-pub.pem
Clear-History
Remove-Item "$env:APPDATA\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt" -ErrorAction SilentlyContinue
exit
```

`Clear-History` only clears in-session `Get-History` output; the PSReadLine
file on disk and any current-session variables (`$plain`, `$pwd`, `$md5`) live
on until the shell exits, so close + reopen the terminal after this step.

(Optional: wipe `futu-priv.pem` too — it already lives in `app_secrets` after step 5.)

## 7. Trigger Configure after sidecar deploy (Chunk G)

```bash
curl -sf -X POST https://dashboard.kiusinghung.com/api/admin/brokers/futu/reconfigure \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"
```

Expected: `{"ok": true, "detail": ""}`.

## 8. Windows Defender exclusion (one-time)

```powershell
Add-MpPreference -ExclusionPath "C:\dashboard\dist-staging-*"
```

Otherwise the kanji-rich PyInstaller payload triggers a Defender scan on every restart.

## 9. Provision sidecar mTLS material

Run `deploy/nuc/provision-sidecar-mtls.ps1 -Label futu` to issue + sign the per-sidecar
cert/key pair. The script writes `C:\dashboard\secrets\futu-sidecar-cert.pem`,
`futu-sidecar-key.pem`, `ca-bundle.pem`, `crl.pem` and applies `icacls`
restrictive ACLs (administrators + SYSTEM only).

Then verify the key is not world-readable:

```powershell
icacls C:\dashboard\secrets\futu-sidecar-key.pem
```

Expected: only `BUILTIN\Administrators` and `NT AUTHORITY\SYSTEM` listed; no `Users`
or `Everyone`. The sidecar's in-process `assert_key_file_permissions` guard is a
no-op on Windows (POSIX-only mode-bit check); ACL hardening is enforced here at
provisioning time and must be re-checked after any cert rotation.
