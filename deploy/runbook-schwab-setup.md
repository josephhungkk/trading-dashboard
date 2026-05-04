# Schwab connect — operator runbook (Phase 7a)

This runbook deploys the Schwab broker integration end-to-end. Follow steps in order.

## 0. Pre-deploy snapshot of `app_secrets`

Before any changes, snapshot the current `app_secrets` table on prod PG. This is the rollback target if anything goes sideways.

```bash
ssh -p 2222 trader@88.208.197.219
psql "$DATABASE_URL" -c "\copy (SELECT * FROM app_secrets) TO STDOUT"   > ~/backups/app_secrets-$(date +%Y%m%d-%H%M).tsv
```

## 1. Schwab Developer Portal — register the app

- Sign in at https://developer.schwab.com
- Create a new "Trader API — Individual" app
- Set redirect URL to: `https://dashboard.kiusinghung.com/api/oauth/schwab/callback`
- Wait for app approval (Schwab manually reviews — typically 1–3 business days)
- Once approved, copy the `app_key` and `app_secret`

## 2. Seed `app_secrets` (app_key, app_secret)

Via the admin API or directly:

```bash
curl -X PUT https://dashboard.kiusinghung.com/api/admin/secrets/broker/schwab.app_key   -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID"   -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"   -H "Content-Type: application/json"   -d '{"value": "YOUR_APP_KEY", "value_type": "str"}'

curl -X PUT https://dashboard.kiusinghung.com/api/admin/secrets/broker/schwab.app_secret   -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID"   -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"   -H "Content-Type: application/json"   -d '{"value": "YOUR_APP_SECRET", "value_type": "str"}'
```

## 3. Deploy schwab-sidecar

```bash
cd /home/trader/trading-dashboard
docker compose pull schwab-sidecar
docker compose up -d schwab-sidecar
docker compose logs -f schwab-sidecar  # confirm 'sidecar_schwab_starting'
```

## 4. Apply CF Access bypass for the public callback

The public callback path `/api/oauth/schwab/callback` must NOT require CF Access JWT (Schwab's redirect doesn't include one). This is idempotent:

```bash
export CF_ACCOUNT_ID=...  # from Cloudflare dashboard
export CF_ZONE_ID=...
export CF_ACCESS_API_TOKEN=...  # API token with Access:Edit
bash scripts/cloudflare/access-bypass-schwab-callback.sh
```

Verify (should return 403, not 401 — proves CF bypassed):

```bash
curl -sf -o /dev/null -w "%{http_code}"   "https://dashboard.kiusinghung.com/api/oauth/schwab/callback?code=test&state=invalid"
# Expected: 403
```

## 5. Click "Connect Schwab" → completes Tier-1 OAuth

- Open https://dashboard.kiusinghung.com/settings (admin login required)
- Click "Connect Schwab" in the Brokers section
- Authenticate at Schwab, approve the OAuth scopes
- Browser redirects back; the SchwabCard turns green ("Connected")

## 6. Optional — Tier-2 setup (Playwright auto-refresh)

**Risk note:** Tier-2 stores Schwab login credentials (username/password/TOTP secret) in `app_secrets`. Schwab anti-fraud may flag automated logins; only enable if you accept this trade-off.

Seed credentials:

```bash
curl -X PUT https://dashboard.kiusinghung.com/api/admin/secrets/broker/schwab.username   -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID"   -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"   -H "Content-Type: application/json"   -d '{"value": "YOUR_USERNAME", "value_type": "str"}'

# password and totp_secret — same shape
```

Toggle the SchwabCard "Enable Tier-2" switch; backend persists `tier2_refresh_enabled=true`.

## 7. Optional — deploy schwab-refresher

```bash
docker compose --profile tier2 up -d schwab-refresher
docker compose logs -f schwab-refresher
```

## 8. Verify accounts list returns Schwab rows

```bash
curl -sf https://dashboard.kiusinghung.com/api/brokers/accounts   -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID"   -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" | jq
```

Expected: array of accounts with `mode: "LIVE"`, `currency_base: "USD"` for Schwab rows. NO `account_hash`, `gateway_label`, or `account_number` fields (M22/H3 boundary strip).

## 9. Schwabdev upgrade procedure

`schwabdev` is pinned to `==3.0.3` per spec §M3 in `sidecar_schwab/pyproject.toml`. To upgrade:

1. Bump the pin in `sidecar_schwab/pyproject.toml`.
2. Run `cd sidecar_schwab && uv lock --upgrade-package schwabdev`.
3. Run the full sidecar test suite: `uv run pytest tests/`.
4. Verify no API surface changes break `client.py` (especially `Tokens.update_tokens` semantics).
5. Smoke-deploy on staging before prod.
