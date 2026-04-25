# RUNBOOK — mTLS recovery after NUC compromise

**Scope:** the IBKR sidecar fleet's root CA private key (`C:\dashboard\secrets\ca.key` on the NUC) is suspected compromised, and we need to rotate root-of-trust + republish the client material to the backend's `app_secrets` store. The `revoke-cert.ps1` path is for individual leaf certs; this runbook is for the full root-CA rotation.

**Downtime budget:** ~5 minutes from step 1 to step 6 — sidecars cannot serve gRPC while their server certs are unsigned/replaced. Trades cannot be placed nor positions read during the window. Schedule outside market hours unless this is a live compromise response.

**Rehearsal cadence:** quarterly tabletop on the paper sidecars (`isa-paper`, `normal-paper`) only. Live sidecars stay untouched during rehearsal.

**Trust window:** treat all sidecar→backend traffic between `t_of_pwn` (when you suspect the compromise started) and the completion of step 5 as compromised. Audit any orders placed in that window; force-confirm any unexpected positions.

---

## Pre-flight

- [ ] Confirm WireGuard is up between VPS and NUC (`Test-NetConnection 10.10.0.1 -Port 51820`).
- [ ] Confirm CF Access service-token env vars are set in the operator shell (`$env:CF_ACCESS_CLIENT_ID`, `$env:CF_ACCESS_CLIENT_SECRET`).
- [ ] Confirm git working tree is clean (`git status` in `C:\dashboard`).
- [ ] Note the start time. Target end time: start + 5 min.

---

## Step 1 — Stop the sidecars (~30s)

Stops all four sidecars cleanly so they don't try to serve gRPC with stale certs while we rotate.

```powershell
foreach ($label in 'isa-live','isa-paper','normal-live','normal-paper') {
    Stop-ScheduledTask -TaskName "IBKR-Sidecar-$label" -ErrorAction SilentlyContinue
}
Get-Process | Where-Object { $_.ProcessName -like 'ibkr-sidecar*' } |
    Stop-Process -Force -ErrorAction SilentlyContinue
```

**Verify:** `Get-NetTCPConnection -State Listen -LocalPort 18001,18002,18003,18004` returns nothing.

---

## Step 2 — Stop the backend (~30s)

The backend caches the old `mtls.ca_bundle_pem` in memory; stop it so step 4's PUT is read fresh on restart.

On the VPS:

```bash
ssh -p 2222 trader@88.208.197.219
docker compose stop backend
```

**Verify:** `curl -sf -o /dev/null -w "%{http_code}\n" https://dashboard.kiusinghung.com/health` returns `502` (CF Tunnel can't reach upstream).

---

## Step 3 — Regenerate the root CA (~30s)

Wipe the entire secrets dir on the NUC and let the provisioner mint everything from scratch. The CA scaffold (`ca-db/`) is wiped too; the new CRL number resets to `1000`.

**Run from an elevated PowerShell so the ACL hardening actually takes effect.**

```powershell
Remove-Item -Recurse -Force C:\dashboard\secrets
& C:\dashboard\deploy\nuc\provision-sidecar-mtls.ps1
```

**Verify:** new `ca.pem` `notBefore` matches the current date (`& 'C:\Program Files\Git\usr\bin\openssl.exe' x509 -in C:\dashboard\secrets\ca.pem -noout -dates`).

---

## Step 4 — Republish the client material to the backend (~30s)

```powershell
& C:\dashboard\deploy\nuc\provision-and-publish.ps1
```

The wrapper runs the provisioner (now a no-op since step 3 just minted the certs), parses the three fenced PEM blocks, and PUTs them to `https://dashboard.kiusinghung.com/api/admin/secrets/broker/mtls.{client_cert,client_key,ca_bundle}_pem` via the CF Access service token.

**Verify:** the script prints `[publish] all three mTLS secrets published` and an `updated_at` timestamp from the backend for each PUT.

---

## Step 5 — Start the backend, then the sidecars (~2 min)

Backend first (so it has the new `app_secrets` loaded before any sidecar tries to authenticate); sidecars second.

On the VPS:

```bash
docker compose start backend
# Wait for /health to return 200 (CF Tunnel reachable + DB up).
until curl -sf -o /dev/null https://dashboard.kiusinghung.com/health \
    -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
    -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"; do sleep 2; done
```

On the NUC:

```powershell
foreach ($label in 'isa-live','isa-paper','normal-live','normal-paper') {
    Start-ScheduledTask -TaskName "IBKR-Sidecar-$label"
    Start-Sleep -Seconds 30
}
```

The 30s grace between starts gives each sidecar time to bind its gRPC port + connect to the gateway before the next one fires.

**Verify:** `Test-NetConnection 127.0.0.1 -Port 18001`, 18002, 18003, 18004 all return `TcpTestSucceeded: True`.

---

## Step 6 — End-to-end verification (~1 min)

Confirm the new trust chain works end-to-end by exercising the backend's broker endpoint, which talks to a sidecar over the new mTLS:

```bash
curl -sS https://dashboard.kiusinghung.com/api/accounts \
    -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
    -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" |
    jq '.accounts | length'
```

Expected: positive integer (number of paper + live accounts the sidecars know about).

If 0 or an HTTP error: the backend can't reach a sidecar via the new mTLS. Check backend logs (`docker compose logs backend | tail -50`) for `mtls handshake failed` / `cert verify failed` lines.

---

## After

- Note the end time. Update incident ticket with the recovery duration.
- If the compromise scope was wider than the NUC (e.g. WireGuard creds on the VPS), continue with the broader rotation per the relevant runbook.
- Add the date of this rotation to the calendar — the next planned rotation (annual via `renew-sidecar-mtls.ps1`) is one year from today.
- Rehearse this runbook on paper sidecars within the next quarter.

## Reference

- `deploy/nuc/provision-sidecar-mtls.ps1` — root CA + 4 server + 1 client provisioner. Idempotent; skips valid certs.
- `deploy/nuc/provision-and-publish.ps1` — wraps provisioner + PUTs to admin API.
- `deploy/nuc/revoke-cert.ps1` — appends a leaf serial to the CRL (single-cert response, not full root rotation).
- `deploy/nuc/renew-sidecar-mtls.ps1` — annual one-at-a-time leaf cert rotation; CA stays put.
- Phase 4 design spec §4.8 — `docs/superpowers/specs/2026-04-25-phase4-ibkr-adapter-design.md`.
