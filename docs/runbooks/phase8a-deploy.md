# Phase 8a — Schwab Trade Enablement: Operator Runbook

Covers post-deploy activation of the Phase 8a capability matrix + Schwab write-path
(`v0.8.0`). Run steps in order. Schwab OAuth must already be working (Phase 7a
runbook at `/home/joseph/dashboard/deploy/runbook-schwab-setup.md` covers that).

**Scope:** Alembic migrations 0011 + 0011a, schwab-sidecar trade RPCs, capability-gate
activation, paper-account canary, rollback procedure.

---

## 1. Pre-deploy checklist

Work through this before running any migration or docker command.

| # | Check | How to verify |
|---|-------|---------------|
| 1 | Schwab OAuth tokens fresh — access token < 25 min old | `GET /api/admin/brokers/schwab/status` — `access_token_age_seconds < 1500` |
| 2 | `schwab.app_key` + `schwab.app_secret` in `app_secrets` | `GET /api/admin/secrets/broker?prefix=schwab.` lists both |
| 3 | `schwab.refresh_token` in `app_secrets` (set by Tier-1 OAuth callback) | Same endpoint — `schwab.refresh_token` row present |
| 4 | DB is on revision `0010` (pre-Phase 8a) | `docker compose exec backend uv run alembic current` prints `0010` |
| 5 | No other Alembic migration running | No outstanding `alembic upgrade` processes on the VPS |
| 6 | C0 empirical artifact exists and is `PASS` | `cat scripts/empirical/artifacts/schwab_c0_20260506T155600Z.json \| python3 -c "import json,sys; d=json.load(sys.stdin); print(d['outcome'])"` → `PASS` |
| 7 | `nightly-real-schwab-trade.yml` green for 3 consecutive runs | GitHub Actions → `nightly-real-schwab-trade` workflow history |

**Important:** do NOT run 0011a before 0011. Do NOT flip capabilities before the C0
gate passes. See §4 for sequencing.

---

## 2. Deploy procedure

### 2.1 — Push to main / pull on VPS

```bash
# On NUC (WSL):
git push origin main

# On VPS:
ssh -p 2222 trader@88.208.197.219
cd /home/trader/trading-dashboard && ./scripts/deploy.sh
```

`deploy.sh` runs rsync + docker compose build + docker compose up -d + nginx reload.
The `schwab-sidecar` image is rebuilt as part of the normal compose build — no
separate step needed. This is an in-cluster Docker container on `td-net`, not a NUC
artifact.

### 2.2 — Run Alembic migration 0011 (foundation tables)

```bash
# On VPS, inside the backend container:
ssh -p 2222 trader@88.208.197.219
docker compose -f docker-compose.prod.yml exec backend \
  uv run alembic upgrade 0011_phase8a_order_capability
```

This creates `order_types`, `time_in_force`, and `broker_order_capability` and seeds
200 rows (4 brokers x 10 order types x 5 TIFs). All Schwab rows start
`is_supported=false` at this revision — no behavior change for existing IBKR/Futu
trade flows.

Verify:

```bash
docker compose -f docker-compose.prod.yml exec backend uv run alembic current
# Expected output includes: 0011_phase8a_order_capability (head)

# Check row count on NUC PG:
psql -h 10.10.0.2 -U trader dashboard -c \
  "SELECT broker_id, COUNT(*) FROM broker_order_capability GROUP BY broker_id ORDER BY broker_id;"
# Expected: alpaca 50 | futu 50 | ibkr 50 | schwab 50
```

### 2.3 — Bounce the backend

After any migration, bounce the backend so the lifespan re-initializes the
`OrderCapabilityService` cache:

```bash
ssh -p 2222 trader@88.208.197.219
docker compose -f docker-compose.prod.yml restart backend
```

Wait ~90 s for health probe to settle, then verify:

```bash
curl -sf https://dashboard.kiusinghung.com/api/brokers/schwab/capabilities \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" | python3 -m json.tool | head -30
# Expected: combos array present; all is_supported=false for schwab at this stage
```

### 2.4 — NUC ops: no new steps for Phase 8a

The Schwab sidecar is in-cluster on the VPS (`td-net`), not on the NUC. No NUC
`schtasks /Run` calls are needed for Schwab specifically. IBKR sidecars (ports
18001-18004) are NUC-resident and unaffected by this migration.

If the post-deploy broker-layer shows `503` (unrelated to Schwab), the standard
recovery is:

```bash
# 1. Re-publish mTLS from NUC (IBKR only):
. ~/.secrets/cf-access-env && \
  WSLENV=CF_ACCESS_CLIENT_ID:CF_ACCESS_CLIENT_SECRET \
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \
    "cd 'C:\dashboard\deploy\nuc'; .\provision-and-publish.ps1"

# 2. Restart IBKR sidecar tasks on NUC if not running:
cmd.exe /c "schtasks /Run /TN IBKRSidecar-isa-live & schtasks /Run /TN IBKRSidecar-isa-paper & schtasks /Run /TN IBKRSidecar-normal-live & schtasks /Run /TN IBKRSidecar-normal-paper"

# 3. Bounce backend:
ssh -p 2222 trader@88.208.197.219 \
  "docker compose -f docker-compose.prod.yml restart backend"
```

See `feedback_post_deploy_broker_recovery.md` for the full explanation.

---

## 3. C0 empirical gate

The C0 script (`scripts/empirical/schwab_place_cancel_paper.py`) validates eight
Schwab REST assumptions that the sidecar's order flow depends on.

**This gate has already passed** (artifact at
`/home/joseph/dashboard/scripts/empirical/artifacts/schwab_c0_20260506T155600Z.json`,
outcome `PASS`, 2026-05-06T15:55Z). You only need to re-run it if:

- schwabdev is upgraded
- The sidecar `to_schwab_order_payload` logic changes materially
- Schwab changes their API (e.g., a new undocumented field becomes required)

To re-run:

```bash
# Requires: SCHWAB_APP_KEY, SCHWAB_APP_SECRET, SCHWAB_PAPER_ACCOUNT_HASH, SCHWAB_REFRESH_TOKEN
export SCHWAB_APP_KEY=...
export SCHWAB_APP_SECRET=...
export SCHWAB_PAPER_ACCOUNT_HASH=...   # from app_secrets or Schwab portal
export SCHWAB_REFRESH_TOKEN=...        # from app_secrets

cd /home/joseph/dashboard
uv run python scripts/empirical/schwab_place_cancel_paper.py
# PASS: artifact=scripts/empirical/artifacts/schwab_c0_<timestamp>.json
```

**If C0 FAIL:** stop here, do not run 0011a. Inspect `schwab_c0_<timestamp>.json`
for which assertion failed, fix the normalizer or sidecar, re-run until PASS. Commit
the passing artifact alongside the fix.

---

## 4. Capability-flip activation (migration 0011a)

0011a is a data-only migration: it sets `is_supported=true` for 16 Schwab capability
rows (`{MARKET, LIMIT, STOP, STOP_LIMIT} x {DAY, GTC, IOC, FOK}`) and fires a
Redis pubsub notification to bust in-process caches.

### Pre-flight gate for 0011a

Before applying:

1. C0 empirical gate PASS (§3 above — already satisfied at `v0.8.0`).
2. `nightly-real-schwab-trade.yml` green for 3 consecutive nights.
3. `schwab-sidecar` container healthy and all 6 new RPCs responding non-`UNIMPLEMENTED`.

Check the sidecar is alive:

```bash
ssh -p 2222 trader@88.208.197.219
docker compose -f docker-compose.prod.yml ps schwab-sidecar
# Expected: running (healthy)
docker compose -f docker-compose.prod.yml logs --tail=20 schwab-sidecar
# Expected: no UNIMPLEMENTED log lines for PlaceOrder/CancelOrder/ModifyOrder
```

### Apply 0011a

```bash
ssh -p 2222 trader@88.208.197.219
docker compose -f docker-compose.prod.yml exec backend \
  uv run alembic upgrade 0011a_phase8a_schwab_flip
```

Verify rows flipped:

```bash
psql -h 10.10.0.2 -U trader dashboard -c \
  "SELECT order_type, time_in_force, is_supported, notes
     FROM broker_order_capability
    WHERE broker_id = 'schwab'
      AND is_supported = true
    ORDER BY order_type, time_in_force;"
# Expected: 16 rows
```

Verify cache busted (within 5 s of migration, the API must reflect new values):

```bash
curl -sf "https://dashboard.kiusinghung.com/api/brokers/schwab/capabilities" \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(sum(1 for c in d['combos'] if c['supported']))"
# Expected: 16
```

### Roll back 0011a

If any capability needs to be revoked (e.g., Schwab changes behavior for a combo):

```bash
ssh -p 2222 trader@88.208.197.219
docker compose -f docker-compose.prod.yml exec backend \
  uv run alembic downgrade 0011_phase8a_order_capability
```

This resets ALL Schwab `is_supported` rows to `false` and fires pubsub. It does
NOT affect IBKR or Futu rows. The migration can be re-applied once the issue is
resolved.

To disable Schwab entirely without touching IBKR/Futu:

```bash
# Backend kill-switch (Phase 5b H0 — fastest path):
curl -X POST "https://dashboard.kiusinghung.com/api/admin/brokers/schwab/kill-switch" \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
# All /api/orders POSTs for schwab accounts 503 immediately (before DB or capability check).
```

---

## 5. Verification steps

Run these after 0011a is applied and the backend is healthy.

### 5.1 — Capability rows visible

```bash
curl -sf "https://dashboard.kiusinghung.com/api/brokers/schwab/capabilities" \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" | \
  python3 -c "
import json, sys
d = json.load(sys.stdin)
supported = [(c['order_type'], c['time_in_force']) for c in d['combos'] if c['supported']]
print(f'{len(supported)} supported combos:')
for combo in supported:
    print(' ', combo)
"
```

Expected: 16 combos — `{MARKET, LIMIT, STOP, STOP_LIMIT}` x `{DAY, GTC, IOC, FOK}`.

### 5.2 — Paper canary: place, modify, cancel round-trip

Use the UI (`TradeTicketModal` with a Schwab paper account selected) or the API
directly. Target: a $1 LIMIT BUY of a cheap liquid stock far from market (e.g., Ford
`F` at `$1.00` limit):

```bash
# Confirm a Schwab paper account UUID:
curl -sf "https://dashboard.kiusinghung.com/api/brokers/accounts" \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" | \
  python3 -c "
import json, sys
accounts = json.load(sys.stdin)
for a in accounts:
    if a.get('broker_id') == 'schwab' and a.get('mode') == 'PAPER':
        print(a['id'], a.get('alias'))
"
```

After placing via the UI, confirm in `GET /api/orders?account_id=<uuid>` that:

1. Order appears with `status=submitted` within 5 s.
2. Modify price via UI — old order shows `status=cancelled` with `kind=replaced`,
   new order shows `status=submitted`.
3. Cancel new order — `status=cancelled` within 30 s.

Verify in the DB:

```sql
-- Run on NUC: psql -h 10.10.0.2 -U trader dashboard
SELECT o.client_order_id, o.status, o.parent_order_id, o.broker_order_id
  FROM orders o
  JOIN broker_accounts ba ON ba.id = o.account_id
 WHERE ba.broker_id = 'schwab'
 ORDER BY o.created_at DESC
 LIMIT 10;
```

### 5.3 — Prometheus metrics ticking

```bash
curl -sf "https://dashboard.kiusinghung.com/metrics" | grep -E \
  "schwab_order_poller_iterations_total|schwab_order_event_emitted_total|order_capability_check_total"
```

Expected: `schwab_order_poller_iterations_total` counter incrementing. After the
paper canary, `schwab_order_event_emitted_total{kind="submitted"}` and
`{kind="cancelled"}` present. Capability gate counter visible after placing any
Schwab order:

```
order_capability_check_total{broker="schwab",result="supported"} > 0
```

### 5.4 — OrderEvent stream stays alive > 5 min

With the paper canary order left open (skip the cancel step temporarily):

```bash
ssh -p 2222 trader@88.208.197.219
docker compose -f docker-compose.prod.yml logs -f schwab-sidecar 2>&1 | \
  grep -E "cadence|poller"
```

With no order transitions over 5 min: poller should downgrade to 30 s idle cadence.
After placing another order: poller upgrades to 2 s active cadence. Log lines contain
`cadence=active` / `cadence=idle`.

### 5.5 — Unsupported-combo gate

Confirm capability gate rejects unsupported combos with HTTP 422:

```bash
# TRAIL+DAY is unsupported for Schwab — should 422:
curl -sf -w "\n%{http_code}" -X POST \
  "https://dashboard.kiusinghung.com/api/orders" \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "<schwab-paper-account-uuid>",
    "order_type": "TRAIL",
    "time_in_force": "DAY",
    "symbol": "F",
    "side": "BUY",
    "quantity": 1,
    "limit_price": null,
    "confirmation_nonce": "test-nonce"
  }'
# Expected last line: 422
# Expected body: error.code = "unsupported_order_type_for_broker"
```

---

## 6. Rollback procedures

### Roll back capability flip only (0011a)

```bash
ssh -p 2222 trader@88.208.197.219
docker compose -f docker-compose.prod.yml exec backend \
  uv run alembic downgrade 0011_phase8a_order_capability
```

All Schwab rows return to `is_supported=false`. Redis pubsub notified. No backend
restart needed — cache busted by pubsub within 60 s.

### Roll back full foundation (0011 + 0011a)

```bash
ssh -p 2222 trader@88.208.197.219
docker compose -f docker-compose.prod.yml exec backend \
  uv run alembic downgrade 0010
```

Drops `broker_order_capability`, `time_in_force`, `order_types`. Existing IBKR/Futu
orders are unaffected (those tables are independent). Restart backend after downgrade
so `OrderCapabilityService` detects the missing tables at lifespan init.

### Emergency kill-switch (Schwab only, no migration)

```bash
curl -X POST "https://dashboard.kiusinghung.com/api/admin/brokers/schwab/kill-switch" \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

Takes effect immediately (Phase 5b H0 — first check before capability gate or
dispatch). IBKR and Futu are unaffected. Undo with `{"enabled": false}`.

---

## 7. Common pitfalls

| # | Trap | Resolution |
|---|------|-----------|
| 1 | **`oauth-start` returns 400 "schwab.app_key not configured"** | Seed `schwab.app_key` and `schwab.app_secret` via `PUT /api/admin/secrets/broker/schwab.app_key` BEFORE clicking re-authorize. Order matters. |
| 2 | **Schwab authorize URL rejected ("contact customer support" or silent 500)** | Do not add `state=` or `response_type=code` to the authorize URL — Schwab rejects both. Also ensure `redirect_uri` is quoted with `safe=':/'` (default `urllib.parse.quote` encodes colons and slashes, breaking the byte-match). |
| 3 | **`/api/accounts` returns 503 after redeploy** | Broker layer did not init. Run `provision-and-publish.ps1`, restart IBKR sidecar tasks via `schtasks /Run`, bounce backend. See `feedback_post_deploy_broker_recovery.md`. |
| 4 | **Migration fails: "table order_types already exists"** | 0011 was partially applied. Run `alembic downgrade 0010` then retry `alembic upgrade 0011_phase8a_order_capability`. |
| 5 | **0011a applied but `GET /capabilities` still shows all unsupported** | In-process cache takes up to 60 s to expire even after pubsub notification. Wait or restart backend to force cache rebuild. |
| 6 | **schwab-sidecar logs show `UNIMPLEMENTED` for PlaceOrder** | Sidecar image is stale — compose pulled cached layers. Run `docker compose -f docker-compose.prod.yml build --no-cache schwab-sidecar && docker compose -f docker-compose.prod.yml up -d schwab-sidecar`. |
| 7 | **Paper canary order never reaches `submitted`** | Check `schwab_access_token_age_seconds` metric. If > 1500 s, re-authorize via Tier-1 OAuth flow and restart schwab-sidecar. |
| 8 | **ModifyOrder creates a second `submitted` row instead of cancel+new** | Schwab assigns a new `broker_order_id` on replace. The old order closes as `cancelled (kind=replaced)`; new order sets `parent_order_id` FK to old UUID. If you see two submitted rows, check that `order_status_rank()` is installed in the DB (`\df order_status_rank` in psql). |

---

## 8. References

| Resource | Location |
|----------|----------|
| Phase 8a spec | `/home/joseph/dashboard/docs/superpowers/specs/2026-05-05-phase8a-capability-foundation-schwab-trade-design.md` |
| Phase 7a Schwab setup runbook | `/home/joseph/dashboard/deploy/runbook-schwab-setup.md` |
| Schwab trade alert runbook | `/home/joseph/dashboard/deploy/runbook-schwab-trade.md` |
| C0 empirical script | `/home/joseph/dashboard/scripts/empirical/schwab_place_cancel_paper.py` |
| C0 passing artifact | `/home/joseph/dashboard/scripts/empirical/artifacts/schwab_c0_20260506T155600Z.json` |
| Foundation migration | `/home/joseph/dashboard/backend/alembic/versions/0011_phase8a_order_capability.py` |
| Capability flip migration | `/home/joseph/dashboard/backend/alembic/versions/0011a_phase8a_schwab_flip.py` |
| Nightly read smoke CI (Phase 7a) | `/home/joseph/dashboard/.github/workflows/nightly-real-schwab.yml` |
| Nightly trade E2E CI (Phase 8a) | `/home/joseph/dashboard/.github/workflows/nightly-real-schwab-trade.yml` |
| Weekly capability drift detector | `/home/joseph/dashboard/.github/workflows/weekly-real-schwab-drift.yml` |
| NUC provision script | `/home/joseph/dashboard/deploy/nuc/provision-and-publish.ps1` |

---

## Last updated

2026-05-08 — Phase 9.7 backlog item G3.

**Not covered in this runbook:**

- Schwab Tier-2 (Playwright auto-refresh) setup and failure recovery — covered in
  `runbook-schwab-setup.md` §6-7.
- Schwab bracket / OCO orders — deferred to Phase 8b; not yet implemented.
- `weekly-real-schwab-drift.yml` failure response — `TODO(phase9.7)`: add a dedicated
  alert runbook section when that workflow's failure mode is characterized from
  production data.
- Frontend `TradeTicketModal` capability-aware UX testing — E2E; out of scope for
  this ops runbook.
- Schwab daily/weekend maintenance envelope — deferred to Phase 8b; rely on REST
  5xx propagation until then.
