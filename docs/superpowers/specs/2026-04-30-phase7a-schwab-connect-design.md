# Phase 7a — Schwab connect (data + read-only) — design

**Status:** draft 2026-04-30. Brainstorm output. Pre-architect-review.
**Target tag:** v0.7.0
**Architectural pillar set in this phase:** new "cloud-broker" sidecar deployment (VPS docker-compose, no PyInstaller / NUC / mTLS).

## 1. Goal

Connect the Schwab Trader API as a third broker — read-only this phase. Lay the OAuth foundation, account discovery, and Configure-RPC contract so that Phase 7b can wire Schwab streamer quotes into the new quote bus, and Phase 8 can light up trade execution.

After this phase ships:
- User completes a one-time browser-based OAuth flow via the dashboard's Settings → Schwab card; refresh token persists in `app_secrets`.
- Schwab accounts (taxable + IRA + joint, however many the user has linked) appear in the AccountPicker alongside IBKR + Futu.
- Live `/api/brokers/accounts` returns Schwab rows with NLV / cash / buying-power populated from Schwab's REST.
- Trade execution endpoints for Schwab return `503 Retry-After: 86400` (deferred to Phase 8). Quote streaming returns `UNIMPLEMENTED` (deferred to Phase 7b).
- Tier-1 manual re-auth UI handles the 7-day refresh-token wall reliably; Tier-2 Playwright auto-refresher is shipped as opt-in (feature-flagged off by default).

**This phase does NOT yet save IBKR data fees** — that arrives with Phase 7b's streamer wiring.

## 2. Non-goals (explicitly deferred)

| Surface | Phase | Reason |
|---|---|---|
| Schwab StreamQuotes RPC | 7b | needs the quote engine bus + WS gateway from 7b |
| Schwab PlaceOrder / CancelOrder / ModifyOrder | 8 | trade-execution rollout phase |
| Schwab option chain / option orders | 12 | options phase |
| Schwab futures | 14 | futures phase |
| Schwab paper trading | – | Schwab Trader API has no paper endpoints; paper-trading is via TOS Paper Money (separate infra). Schwab accounts are always `TradingMode.LIVE`. |
| Multi-Schwab-login support | post-v1.0 | rare for personal use; one Schwab login → many accounts is supported, but two Schwab logins isn't |

## 3. Architecture

### 3.1 Topology

| Component | Host | New? | Process |
|---|---|---|---|
| `sidecar_schwab/` | **VPS** | ✓ | docker-compose service (Python 3.14, runs as `schwab-sidecar`) |
| `sidecar_schwab_refresher/` | **VPS** | ✓ (feature-flagged) | docker-compose service running Playwright + Xvfb on a 3-day cron |
| `backend` | VPS | extend | new `BrokerConfigurer` for Schwab; new admin REST routes for OAuth flow |
| Frontend | – | extend | Settings → Schwab card with OAuth-start button + token expiry display + Tier-2 toggle |

**Why VPS, not NUC:** Schwab is cloud-only. Sidecar lives where the cloud calls go. No WireGuard hop, no PyInstaller, no Windows Scheduled Task, no mTLS (sidecar + backend on the same docker network).

### 3.2 Sidecar gRPC contract

Reuses the `Broker` service contract from Phase 4 + 6. New behaviors:

- `Health` returns `broker_id="schwab"`, `started_at`, `gateway_connected` (true when access_token is fresh + accountHashes cached).
- `Configure` accepts `app_key` + `app_secret` + `refresh_token` from `app_secrets`. Sidecar persists token state in-process; access_token refresh is automatic (Schwabdev handles it).
- `ListAccounts` returns one `Account` per Schwab brokerage account (one Schwab login can return N accounts). **Sidecar invariant: every Schwab `Account.mode = TradingMode.LIVE`.** Schwab Trader API has no paper-trading endpoints; paper trading is via TOS Paper Money on a separate platform. The sidecar normalizes `securitiesAccount.type` (`CASH` | `MARGIN` | `IRA` | …) to a single `LIVE` mode regardless of variant, since the dashboard's `mode` is "is this real money or simulation".
- `GetAccountSummary` returns NLV / cash / buying_power / day_pnl / currency_base="USD".
- `GetPositions` returns positions per account.
- `GetOrders` returns last 7 days of orders per account (read-only this phase).
- `SearchContracts` UNIMPLEMENTED until Phase 7b (needs market-data subscription path).
- `PlaceOrder` / `CancelOrder` / `ModifyOrder` UNIMPLEMENTED until Phase 8.
- `OrderEvent` UNIMPLEMENTED until Phase 8 (Schwab provides this via `ACCT_ACTIVITY` streamer service in Phase 7b/8 boundary).

### 3.3 OAuth flow (Tier-1 manual, primary path)

```
User clicks "Connect Schwab" on Settings → Schwab card
  → backend POST /api/admin/brokers/schwab/oauth-start
  → redirects to Schwab consent URL with client_id + redirect_uri + scope
User logs in to Schwab + grants access
  → Schwab redirects to {backend}/api/admin/brokers/schwab/oauth-callback?code=...
  → backend exchanges code → access_token (30min) + refresh_token (7d)
  → backend writes both to app_secrets via ConfigService (Fernet-encrypted)
  → backend sets app_config "schwab.access_token_issued_at" + "schwab.refresh_token_issued_at"
  → backend calls schwab sidecar's Configure RPC with the new tokens
  → Settings card updates: green dot, expiry countdown
```

The user is the only one who can complete this — the redirect lands in their browser. Tier-1 is unavoidable for the first authorization and recoverable for any time the refresh_token expires.

### 3.4 Tier-2 Playwright refresher (opt-in, secondary path)

```
sidecar_schwab_refresher: docker-compose service
  cron: every 3 days at 13:00 UTC (4-day safety margin on the 7-day TTL)
  on tick:
    - read schwab.refresh_token + schwab.totp_secret + schwab.username + schwab.password
      from app_secrets (Fernet-encrypted, local only)
    - launch Playwright Chromium under Xvfb (headed)
    - apply playwright-stealth fingerprint
    - navigate to Schwab OAuth consent URL
    - fill username + password (typed slowly with random delays)
    - read TOTP code via pyotp.TOTP(secret).now() and submit
    - wait for redirect to backend /oauth-callback
    - capture the auth code from the redirect URL
    - call backend POST /api/admin/brokers/schwab/oauth-callback?code=...&actor=tier2
    - on success: log + emit metric schwab_tier2_refresh_success_total
    - on failure: log + emit metric schwab_tier2_refresh_failed_total{reason=...}
                  + send Telegram alert (Phase 11) — until Phase 11, falls back to email
                  + status = unhealthy (Settings card shows red + manual fix prompt)
```

Feature flag: `app_config.schwab.tier2_refresh_enabled` = false default. Set true via admin UI explicitly.

Reference implementation: github.com/QuantStrategyLab/SchwabTokenAutoRefresher (architecture pattern only — Python rewrite, runs on VPS, writes to app_secrets via backend HTTP API).

### 3.5 Token lifecycle

| Token | TTL | Refreshed by |
|---|---|---|
| `access_token` | 30 min (Schwab's TTL) | sidecar auto-refresh at 25 min via Schwabdev (4 min headroom) |
| `refresh_token` | 7 days (hard wall) | Tier-1 manual UI (primary) or Tier-2 Playwright (opt-in) |

Sidecar holds token state in process memory (no DB round-trip per request). On `Configure` RPC, sidecar replaces in-memory tokens and clears any access_token cache. Sidecar restart re-reads from `app_secrets` via Configure (same as Phase 6 Futu pattern).

## 4. Data model

### 4.1 New `app_secrets` keys (Fernet-encrypted)

| Namespace | Key | Required | Lifecycle |
|---|---|---|---|
| `schwab` | `app_key` | ✓ | manual setup (from Schwab developer portal) |
| `schwab` | `app_secret` | ✓ | manual setup |
| `schwab` | `access_token` | ✓ after first OAuth | rotated every 25 min by sidecar |
| `schwab` | `refresh_token` | ✓ after first OAuth | rotated weekly by Tier-1 or Tier-2 |
| `schwab` | `username` | only if Tier-2 enabled | manual setup |
| `schwab` | `password` | only if Tier-2 enabled | manual setup |
| `schwab` | `totp_secret` | only if Tier-2 enabled | manual setup (Base32 from Schwab MFA enrollment) |

### 4.2 New `app_config` keys (cleartext)

| Namespace | Key | Type | Default |
|---|---|---|---|
| `schwab` | `callback_url` | string | `https://dashboard.kiusinghung.com/api/admin/brokers/schwab/oauth-callback` |
| `schwab` | `access_token_issued_at` | ISO datetime | (set on every refresh) |
| `schwab` | `refresh_token_issued_at` | ISO datetime | (set on every Tier-1 / Tier-2 refresh) |
| `schwab` | `tier2_refresh_enabled` | bool | `false` |

### 4.3 New `app_config` rows for `SIDECAR_BROKERS` mapping

The `BrokerRegistry` already has a `SIDECAR_BROKERS` map (Phase 6). Add a row pointing the `"schwab"` label at the new VPS sidecar:

```python
SIDECAR_BROKERS = {
    "isa-live":    ("ibkr", "10.10.0.2:18001"),
    "isa-paper":   ("ibkr", "10.10.0.2:18002"),
    "normal-live": ("ibkr", "10.10.0.2:18003"),
    "normal-paper":("ibkr", "10.10.0.2:18004"),
    "futu":        ("futu", "10.10.0.2:18005"),
    "schwab":      ("schwab", "schwab-sidecar:9090"),  # NEW — docker-compose internal hostname
}
```

The sidecar listens on a single port (default `9090`) inside the docker network. No mTLS (same docker network) — gRPC over plaintext like the Phase 6 Futu sidecar would do if it were on the VPS.

### 4.4 Alembic migration `0008_phase7a_schwab_account_hash`

Schwab requires the account *hash* (privacy layer) on every trader-API path. The hash is per-Schwab-login, persistent, opaque. Add a column to `broker_accounts`:

```sql
ALTER TABLE broker_accounts
  ADD COLUMN account_hash TEXT NULL;
COMMENT ON COLUMN broker_accounts.account_hash IS
  'Schwab-only: opaque account hash from /accountNumbers; required on all Schwab REST paths. NULL for non-Schwab brokers.';
```

Why a new column instead of overloading `gateway_label`: `account_hash` is per-account (varies across the Schwab login's N accounts), while `gateway_label` is per-sidecar.

## 5. Components

### 5.1 New: `sidecar_schwab/`

```
sidecar_schwab/
├── __init__.py
├── main.py                  # gRPC server bootstrap; reads SCHWAB_SIDECAR_PORT env
├── config.py                # CLI args, log config
├── handlers.py              # Configure, Health, ListAccounts, GetAccountSummary, GetPositions, GetOrders
├── client.py                # SchwabClient wrapping Schwabdev ClientAsync
├── normalize.py             # Schwab JSON → proto Account / Position / Order mappers
├── auth.py                  # OAuth refresh management; token_lock; access_token caching
├── stubs/                   # UNIMPLEMENTED stubs for SearchContracts/PlaceOrder/CancelOrder/ModifyOrder/OrderEvent/StreamQuotes
├── pyproject.toml           # uv-managed; deps: grpcio, grpcio-tools, schwabdev, structlog, pydantic
├── Dockerfile               # python:3.14-slim, grpc reflection, runs main.py
└── tests/
    ├── test_normalize.py
    ├── test_handlers_list_accounts.py
    ├── test_handlers_summary.py
    ├── test_handlers_positions.py
    ├── test_handlers_orders.py
    ├── test_auth_lifecycle.py    # mocked Schwabdev token refresh
    └── test_configure_idempotent.py
```

Mirrors the Phase 6 `sidecar_futu/` shape. Schwabdev v3.0.3 ClientAsync is the dependency.

### 5.2 New: `sidecar_schwab_refresher/`

```
sidecar_schwab_refresher/
├── __init__.py
├── main.py                  # cron loop; runs once per docker-compose-defined schedule
├── refresher.py             # Playwright flow: navigate → fill → MFA → capture
├── stealth.py               # playwright-stealth bootstrap
├── totp.py                  # pyotp wrapper
├── config_writer.py         # writes new tokens to app_secrets via backend admin API
├── pyproject.toml           # deps: playwright, playwright-stealth (or undetected-playwright), pyotp, httpx, structlog
├── Dockerfile               # python:3.14 + Xvfb + Playwright Chromium browser bundle
└── tests/
    ├── test_totp.py
    ├── test_refresher_unit.py    # mocked browser
    └── test_config_writer.py
```

Feature-flagged: starts with `--enabled` only when `app_config.schwab.tier2_refresh_enabled = true`. Container exits 0 if flag is off (cron tick is a no-op).

### 5.3 Backend changes

#### `app/services/broker_registry_factory.py`

Add Schwab to the `BrokerConfigurer` lifecycle (Phase 6 introduced this for Futu). On startup + on every sidecar `started_at` delta:
1. Read `schwab.app_key` + `schwab.app_secret` + `schwab.refresh_token` from `app_secrets`.
2. Call sidecar `Configure` RPC.
3. Log + emit metric `broker_configure_total{label="schwab", reason}`.

#### `app/api/brokers_admin.py` (new)

Three new admin REST endpoints, all gated by `require_admin_jwt`:

```
GET  /api/admin/brokers/schwab/oauth-start
       → builds Schwab consent URL with state nonce; returns 302 redirect

GET  /api/admin/brokers/schwab/oauth-callback?code=...&state=...&actor=tier1|tier2
       → validates state nonce
       → exchanges code for access_token + refresh_token
       → writes both to app_secrets
       → sets app_config schwab.access_token_issued_at + refresh_token_issued_at
       → triggers sidecar Configure RPC
       → returns 200 with token-issued-at timestamps

POST /api/admin/brokers/schwab/reconfigure
       → no-op for users (used by Tier-2 service after a successful refresh
         to nudge the sidecar to re-read tokens immediately rather than waiting
         for the next access-token expiry)
```

State nonce: stored in Redis with 10-min TTL, single-use, prevents OAuth CSRF.

#### `app/services/account_service.py`

Already broker-agnostic per Phase 6. Adds Schwab via `SIDECAR_BROKERS` map without code changes.

`AccountResponse.account_hash` (new optional field) populated from `Account.account_hash` on the wire (proto extension below). NULL for non-Schwab brokers. Stripped from the boundary response (not exposed to the frontend) — same handling as `gateway_label` and `account_number` per Phase 4 M22 invariant.

### 5.4 proto changes

```proto
// broker/v1/broker.proto

message Account {
  string account_number = 1;
  TradingMode mode = 2;
  string gateway_label = 3;
  string currency_base = 4;
  string account_hash = 5;  // NEW — Schwab only; empty for IBKR/Futu
}
```

No version bump required (additive field). Regenerate `_pb2.py` for backend, sidecar_schwab, and `api-generated.ts` for frontend. Existing callers ignore unknown fields per protobuf semantics.

### 5.5 Frontend changes

#### `frontend/src/features/Settings/SchwabCard.tsx` (new)

```
┌────────────────────────────────────────────────┐
│ Schwab                                         │
│  ● Connected                                   │
│  Last refresh: 2 days ago                      │
│  Refresh token expires: in 4 days 21 hours     │
│                                                │
│  [Re-authorize now] [Disconnect]               │
│                                                │
│  ☐ Enable Tier-2 auto-refresh                  │
│    (Playwright, headed Chrome on VPS, every    │
│     3 days; requires username/password/TOTP)   │
└────────────────────────────────────────────────┘
```

Polls `/api/admin/config?ns=schwab&key=refresh_token_issued_at` every 60s for live expiry display. Red badge at <24h. Email-style nag: T-2d, T-1d, T-2h.

#### `frontend/src/services/schwab.ts` (new)

Thin wrapper around the three new admin endpoints. Used by SchwabCard only.

#### Existing `AccountPicker`

No changes required — Schwab accounts flow through the existing `AccountResponse` contract once the sidecar discovers them. Visual: Schwab icon + ALL-LIVE label (no paper toggle for Schwab rows since `mode=LIVE` always).

## 6. Tests

### 6.1 Sidecar unit tests

| File | Coverage |
|---|---|
| `test_normalize.py` | Schwab JSON → proto Account/Position/Order mappers (~20 assertions: enum coverage for `assetType`, `orderType`, `tif`, `status`) |
| `test_handlers_list_accounts.py` | Configure → ListAccounts round-trip with mocked Schwabdev client |
| `test_handlers_summary.py` | NLV/cash/buying_power extraction from securitiesAccount JSON |
| `test_handlers_positions.py` | Position mapping incl. day_pnl, avg_cost |
| `test_handlers_orders.py` | 7-day window query, status mapping (18 Schwab → 6 ours) |
| `test_auth_lifecycle.py` | access_token refresh at 25-min mark, 401 retry once, exponential backoff on 429 |
| `test_configure_idempotent.py` | Configure called twice with same tokens is a no-op |

Reuse fixtures from `Dashboard_old/backend/tests/test_schwab_*.py` (504 lines of real Schwab response shapes) — fork into new `backend/tests/fixtures/schwab_test_data.py`.

### 6.2 Backend integration tests

| File | Coverage |
|---|---|
| `tests/integration/test_schwab_oauth_flow.py` | mocked Schwab token endpoint; full /oauth-start → /oauth-callback round-trip; verifies app_secrets writes + Configure call |
| `tests/integration/test_schwab_account_listing.py` | sidecar mock returning 3 accounts; backend `/api/brokers/accounts` returns 3 Schwab rows + IBKR/Futu rows from prior phases |
| `tests/integration/test_schwab_state_nonce.py` | mismatched state → 403; reused state → 403; expired state → 403 |

### 6.3 Tier-2 refresher tests

| File | Coverage |
|---|---|
| `test_totp.py` | `pyotp.TOTP("BASE32SECRET").now()` produces 6-digit code; clock-skew tolerance |
| `test_refresher_unit.py` | mocked Playwright page; verifies fill → submit → URL capture sequence |
| `test_config_writer.py` | writes new tokens via backend admin HTTP; handles 5xx with retry |

No real Schwab login in CI (would burn refresh tokens). Tier-2 flow has a `--dry-run` mode that skips the actual login; CI exercises that.

### 6.4 Real-Schwab smoke (manual / nightly)

`backend/tests/integration/test_real_schwab_smoke.py` gated on `CI_USE_REAL_SCHWAB=1`. Hits production Schwab paper-or-live read-only endpoints (`/userPreference`, `/accountNumbers`, `/accounts`) and asserts non-empty response. Run nightly via `nightly-real-schwab.yml` (new GitHub Actions workflow) at 13:00 UTC.

### 6.5 Coverage target

≥ 80 % per the project rule. Sidecar package: real target ~85 % (Schwabdev wrapping covers the harder paths).

## 7. Deployment

### 7.1 docker-compose.prod.yml additions

```yaml
schwab-sidecar:
  build: ./sidecar_schwab
  restart: unless-stopped
  environment:
    SCHWAB_SIDECAR_PORT: "9090"
    BACKEND_ADMIN_URL: "http://backend:8000"
    LOG_LEVEL: "INFO"
  networks: [internal]
  depends_on:
    backend: { condition: service_started }
  healthcheck:
    test: ["CMD", "python", "-c", "import grpc, broker_pb2_grpc; ch = grpc.insecure_channel('localhost:9090'); ch.close()"]
    interval: 30s
    timeout: 5s
    retries: 3

schwab-refresher:
  build: ./sidecar_schwab_refresher
  restart: unless-stopped  # cron loop runs forever; exits during off-cycle
  environment:
    BACKEND_ADMIN_URL: "http://backend:8000"
    REFRESH_INTERVAL_HOURS: "72"
    DRY_RUN: "false"
  networks: [internal]
  profiles: ["tier2"]  # only starts with `docker compose --profile tier2 up`
```

### 7.2 Backend wiring

`backend/app/main.py` lifespan adds Schwab to the `BrokerConfigurer` startup loop (already broker-agnostic per Phase 6). No code change beyond the `SIDECAR_BROKERS` map row.

### 7.3 Operator setup runbook (`deploy/runbook-schwab-setup.md`)

1. Register dashboard at `developer.schwab.com`. Create app: callback `https://dashboard.kiusinghung.com/api/admin/brokers/schwab/oauth-callback`. Copy app_key + app_secret.
2. Seed `app_secrets`: `schwab.app_key` + `schwab.app_secret`.
3. Click "Connect Schwab" on Settings → completes Tier-1 OAuth.
4. Optional Tier-2: enable in admin UI, then seed `schwab.username` + `schwab.password` + `schwab.totp_secret` (Base32 from Schwab MFA QR — record once during MFA enrollment).
5. Optional Tier-2 deploy: `docker compose --profile tier2 up -d schwab-refresher`.
6. Verify: `curl -s https://dashboard.kiusinghung.com/api/brokers/accounts -H "$CF_HDR"` returns Schwab rows.

## 8. Observability

### 8.1 New Prometheus metrics

| Metric | Type | Purpose |
|---|---|---|
| `broker_configure_total{label,reason}` | Counter | extends Phase 6 metric to schwab label |
| `schwab_oauth_start_total{actor}` | Counter | tier1 vs tier2 OAuth flow initiations |
| `schwab_oauth_callback_total{actor,result}` | Counter | success / state_mismatch / token_exchange_fail |
| `schwab_access_token_age_seconds` | Gauge | sidecar exposes; alerts on >1700s (29min — close to TTL) |
| `schwab_refresh_token_age_hours` | Gauge | backend exposes; alerts on >168h (7d wall) |
| `schwab_tier2_refresh_total{result}` | Counter | success / login_failed / mfa_failed / dom_changed / network_error |
| `schwab_tier2_last_run_timestamp_seconds` | Gauge | for staleness detection |

### 8.2 New Prometheus alerts

`deploy/prometheus/alerts.yml` adds group `phase7a_schwab`:

| Alert | Severity | Condition |
|---|---|---|
| `SchwabRefreshTokenExpiringSoon` | warning | `schwab_refresh_token_age_hours > 144` (T-1d) for 5m |
| `SchwabRefreshTokenExpired` | page | `schwab_refresh_token_age_hours > 168` for 5m |
| `SchwabAccessTokenStuck` | warning | `schwab_access_token_age_seconds > 1700` for 5m |
| `SchwabTier2RefreshFailed` | warning (Tier-2 enabled only) | 2 consecutive `schwab_tier2_refresh_total{result!="success"}` |
| `SchwabSidecarUnreachable` | page | `up{job="schwab-sidecar"} == 0` for 2m |

## 9. Open risks + mitigations

| Risk | Mitigation |
|---|---|
| Schwab Developer Portal app rejection / approval delay | User confirmed access ready (pre-approved). |
| 7-day refresh wall + Tier-1 nag fatigue | Tier-2 auto-refresh + Telegram alert (Phase 11) + 4-day safety margin in Tier-2 cron. |
| Tier-2 Playwright fragility (Schwab DOM changes) | DOM-change failure → metric → alert → fall back to Tier-1 + don't auto-retry. Selectors documented in `refresher.py` for fast hand-fix. |
| Schwab anti-fraud trips on Tier-2 from VPS IP | Tier-2 is opt-in. VPS is a single fixed IP (closer to "user's machine" pattern than GitHub Actions runner IPs). Document the geo-mismatch warning in runbook. |
| Schwab account hash rotation (per docs: stable, but theoretically rotatable) | Sidecar refreshes `_account_hashes` on every Configure + on any 404 from a hash-keyed path; eventual consistency. |
| Multi-account ambiguity | One Schwab login → N accounts is the common case; sidecar enumerates all via `/accountNumbers`. Multi-Schwab-login deferred post-v1.0. |
| Schwab `Mode=LIVE` only — no paper accounts | `AccountPicker` already handles per-account mode; Schwab rows just always show "LIVE". No special handling. |
| State-nonce CSRF surface | Single-use, 10-min TTL Redis-backed nonce; backend validates on `/oauth-callback`. |
| Tier-2 credentials in `app_secrets` | Fernet-encrypted (Phase 2 infrastructure). Operator-explicit opt-in. Same risk profile as the Futu RSA key from Phase 6. |
| Schwab API rate limits (120 req/min standard) | Sidecar throttles via async semaphore (10 concurrent calls). Phase 7b's streamer eliminates the polling pressure entirely. |

## 10. Phase 7a chunk plan

| Chunk | Theme | Headline tasks |
|---|---|---|
| **A** | Proto + sidecar shell | Add `account_hash` to `Account` proto. Generate types backend + sidecar + frontend. New `sidecar_schwab/` skeleton (Dockerfile, pyproject, main, gRPC server bootstrap). |
| **B** | Sidecar core | Configure RPC, ListAccounts (via Schwabdev `/accountNumbers` + `/accounts`), GetAccountSummary, GetPositions, GetOrders. Auth lifecycle (token_lock, 25-min refresh). |
| **C** | Backend wiring | Alembic 0008 (`account_hash` column). `BrokerConfigurer` extension. New `/api/admin/brokers/schwab/oauth-{start,callback}` + `/reconfigure`. State-nonce validation. `SIDECAR_BROKERS` map update. |
| **D** | Tier-1 frontend | `SettingsPage` → `SchwabCard` component. OAuth-start button. Token-expiry display + nag thresholds. Disconnect. Polling loop. |
| **E** | Tier-2 refresher | `sidecar_schwab_refresher/` package + Playwright + stealth + TOTP + config_writer. docker-compose `tier2` profile. Feature-flag wiring. Tests (mocked browser). |
| **F** | Tests + smoke | Sidecar units (~7 files). Backend integration tests. Refresher tests. `test_real_schwab_smoke.py` gated. `nightly-real-schwab.yml`. |
| **G** | Ops | `runbook-schwab-setup.md`. docker-compose.prod.yml additions. Prometheus metrics + alerts. CHANGELOG / TASKS / CLAUDE.md / memory. v0.7.0 tag. |

## 11. Architectural pillars set in this phase

- **Cloud-broker sidecar deployment pattern** — sidecars don't have to live on the NUC. Cloud-only brokers run as docker-compose services on the VPS, plaintext gRPC over the internal docker network. Sets the precedent for any future cloud-only adapter (Polygon, Alpaca, Coinbase trading, …).
- **OAuth weekly-refresh pattern (two-tier)** — Tier-1 manual UI as the contract; Tier-2 headless automation as opt-in convenience. Sets the precedent for any future OAuth broker with short refresh-TTL walls.
- **`account_hash` on `broker_accounts`** — opaque-identifier-per-account pattern. Reusable for any future broker that hashes account IDs.

## 12. Out-of-scope / explicitly punted

- **StreamQuotes RPC** — Phase 7b
- **Schwab options chain** — Phase 12
- **Schwab futures contract roll** — Phase 14
- **PlaceOrder / CancelOrder / ModifyOrder** — Phase 8
- **Schwab `OrderEvent` via `ACCT_ACTIVITY` streamer** — Phase 8 (boundary-shared with 7b)
- **Multi-Schwab-login** — post-v1.0
- **In-process Schwab adapter** (rejected design — process-isolation was decisive)
- **NUC Schwab sidecar** (rejected design — Schwab is cloud)
