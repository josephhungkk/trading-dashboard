# Phase 7a — Schwab connect (data + read-only) — design

**Status:** architect-reviewed 2026-04-30. CRIT + HIGH + MED applied inline; LOWs deferred to plan (see §12).
**Target tag:** v0.7.0
**Architectural pillar set in this phase:** new "cloud-broker" sidecar deployment (VPS docker-compose, no PyInstaller / NUC / mTLS).
**Architect-review distribution:** 3 CRIT + 6 HIGH + 7 MED + 5 LOW.

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
| Schwab `OrderEvent` via `ACCT_ACTIVITY` streamer | 8 | locked to 8 — requires the trade-execution path; 7b only ships read-only streamer (LEVELONE_*, CHART_EQUITY) |
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

- `Health` returns `broker_id="schwab"`, `started_at`, `gateway_connected`. **Invariant (H4):** `gateway_connected=true` iff (access_token < 25 min old AND `_account_hashes` is non-empty). False during the initial post-restart bootstrap until both conditions hold.
- `Configure` accepts `app_key` + `app_secret` + `refresh_token` + (optionally) `access_token` from `app_secrets`. **If `access_token_issued_at` < 25 min, sidecar uses it directly; otherwise sidecar calls Schwab's token endpoint to mint a new one** (one refresh-token use). Sidecar does NOT auto-refresh on its own (single-writer rule — see §3.6).
- `ListAccounts` returns one `Account` per Schwab brokerage account (one Schwab login can return N accounts). **Sidecar invariant: every Schwab `Account.mode = TradingMode.LIVE`.** Schwab Trader API has no paper-trading endpoints; paper trading is via TOS Paper Money on a separate platform. The sidecar normalizes `securitiesAccount.type` (`CASH` | `MARGIN` | `IRA` | …) to a single `LIVE` mode regardless of variant, since the dashboard's `mode` is "is this real money or simulation".
- `GetAccountSummary` returns NLV / cash / buying_power / day_pnl / `currency_base="USD"` (hardcoded — see H5 invariant below).
- `GetPositions` returns positions per account.
- `GetOrders` returns last 7 days of orders per account (read-only this phase). Status mapping table at §3.2.1; `avg_fill_price` extraction at §3.2.2.
- `SearchContracts` UNIMPLEMENTED until Phase 7b (needs market-data subscription path).
- `PlaceOrder` / `CancelOrder` / `ModifyOrder` UNIMPLEMENTED until Phase 8.
- `OrderEvent` UNIMPLEMENTED until Phase 8 (Schwab provides this via `ACCT_ACTIVITY` streamer service; locked to Phase 8 since it requires the trade-execution path).

**H5 invariant (USD-only fallback):** `currency_base='USD'` is hardcoded for Schwab as of 2026 (Schwab Trader API is USD-only). If Schwab ever returns a non-USD `securitiesAccount`, sidecar emits `broker_normalize_unknown_total{label='schwab', field='currency_base'}` and falls back to empty string (caught by the boundary handler).

**M6 — concurrency invariants:**
- Sidecar uses `asyncio.Semaphore(10)` for outbound HTTP concurrency.
- `_token_lock` (asyncio.Lock) is held only for the access_token freshness check (read-only path) — the lock is **released BEFORE** any actual HTTP call.
- 429 handling: on 429, sidecar reads `Retry-After` header, waits, retries up to 3× with exponential backoff (1s → 2s → 4s) + ±100ms jitter.
- Metric `schwab_http_requests_total{endpoint, status}` exported; alert `SchwabRateLimitNear` at ≥ 100 req/min sustained for 5m.

**H3 — `account_hash` 404 retry-once invariant:**
- On any 404 from a hash-keyed Schwab REST path (`/accounts/{hash}/...`), sidecar invalidates its in-memory `_account_hashes` cache, calls `/accountNumbers` to refresh, and retries the original request **once**. A second 404 surfaces as `NOT_FOUND` to the backend.
- Metric `schwab_account_hash_refresh_total{reason=initial|rotation_detected|404_retry}` exported.

#### 3.2.1 Schwab order status mapping (M1)

Forked verbatim from `Dashboard_old/backend/app/brokers/schwab.py:109-128`, plus Phase 5c `modified` status:

| Schwab status | Our `OrderStatus` |
|---|---|
| `WORKING`, `ACCEPTED`, `QUEUED` | `SUBMITTED` |
| `PENDING_ACTIVATION`, `AWAITING_PARENT_ORDER`, `AWAITING_CONDITION`, `AWAITING_MANUAL_REVIEW`, `AWAITING_UR_OUT`, `AWAITING_RELEASE_TIME`, `AWAITING_STOP_CONDITION`, `NEW` | `PENDING` |
| `FILLED` | `FILLED` |
| `CANCELED`, `PENDING_CANCEL`, `EXPIRED` | `CANCELLED` |
| `REJECTED` | `REJECTED` |
| `PENDING_REPLACE`, `REPLACED` | `modified` (Phase 5c `order_status_rank()` SQL function) |

Anything not listed falls through to `PENDING` so the UI flags it for the user to inspect in Schwab's app, plus `broker_normalize_unknown_total{label='schwab', field='status', value=<...>}` is emitted.

#### 3.2.2 `avg_fill_price` extraction (M2)

**Trap from Dashboard_old:** `schwab.py:638-640` says *"Schwab doesn't expose avgFillPrice separately on the order object; limitPrice is the best we have when filled === quantity."* This is wrong — Schwab DOES expose fill prices via the nested `orderActivityCollection[].executionLegs[].price`. Phase 7a fixes the bug:

- `avg_fill_price` is computed from `orderActivityCollection[*].executionLegs[*]` (sum `price * quantity` / total qty), NOT from `order.price` (which is the limit price).
- If `orderActivityCollection` is missing on a `FILLED` order, `avg_fill_price` is null and `OrderResponse.avg_fill_price_inferred=true` flag is set so the UI dims the value.
- Phase 8 expands this with the explicit fills table; Phase 7a only ships the read-side fix.

### 3.3 OAuth flow (Tier-1 manual, primary path)

**C1 — callback host topology fix:**

The callback endpoint is `/api/oauth/schwab/callback` — **NOT** under `/api/admin/`. It is **not** behind `require_admin_jwt`. It is **not** behind CF Access (the redirect from Schwab carries no Access cookie). Authentication is via the **state nonce** (HMAC-signed, single-use, 10-min TTL — see §5.3 H1 spec).

CF Access bypass policy: a path-prefix bypass rule for `/api/oauth/schwab/callback` is added to the CF Access app config so unauthenticated GETs land on the route. Route-level CSRF protection is the HMAC-signed state nonce.

```
User clicks "Connect Schwab" on Settings → Schwab card
  → frontend POST /api/admin/brokers/schwab/oauth-start (admin-JWT-gated)
  → backend mints state nonce: nonce = secrets.token_urlsafe(32)
                                signed = hmac(nonce, APP_SECRET_KEY)
                                Redis: SET schwab_oauth_nonce:{nonce} <user_email> NX EX 600
  → backend returns 302 to Schwab consent URL with client_id + redirect_uri + state=signed
User logs in to Schwab + grants access
  → Schwab redirects to https://dashboard.kiusinghung.com/api/oauth/schwab/callback?code=...&state=signed
       (PUBLIC route — CF Access bypass via path-prefix rule; no admin JWT required)
  → backend validates: GETDEL schwab_oauth_nonce:{nonce}; recompute hmac; compare
  → backend exchanges code → access_token (30min) + refresh_token (7d) via Schwab token endpoint
  → backend acquires PG advisory lock pg_try_advisory_lock(SCHWAB_REFRESH_LOCK_ID)
  → backend writes both tokens to app_secrets via ConfigService (Fernet-encrypted)
  → backend sets app_config schwab.access_token_issued_at + schwab.refresh_token_issued_at
  → backend SYNCHRONOUSLY calls schwab sidecar's Configure RPC with the new tokens
  → backend releases advisory lock
  → backend publishes Redis pub/sub: config:invalidate:schwab
  → backend returns 200 {access_token_issued_at, refresh_token_issued_at}
  → Settings card receives pub/sub via SSE → updates: green dot, expiry countdown
```

The user is the only one who can complete this — the redirect lands in their browser. Tier-1 is unavoidable for the first authorization and recoverable for any time the refresh_token expires.

### 3.4 Tier-2 Playwright refresher (opt-in, secondary path)

**C1 — Tier-2 captures auth code WITHOUT following the redirect.** The Playwright browser does NOT GET the public callback URL itself (which would require it to be on the public network); instead it intercepts the redirect via `page.on("request")` and reads the `code` query param off the URL, then POSTs `code` directly to backend admin API.

```
sidecar_schwab_refresher: docker-compose service (profile: tier2)
  cron: every 3 days at 13:00 UTC (4-day safety margin on the 7-day TTL)

  Pre-flight checks (fail-fast, no credential submission):
    - read schwab.totp_secret + username + password from app_secrets
    - launch Playwright Chromium under Xvfb (headed)
    - apply playwright-stealth fingerprint
    - navigate to Schwab OAuth consent URL
    - SELECTOR HEALTH CHECK (H2): confirm
        * username field locator resolves within 5s
        * password field locator resolves within 5s
        * MFA-prompt detector locator exists
      → if any miss, fail with metric reason=dom_changed, NO credential submission

  Execution:
    - fill username + password (typed slowly with random 80-200ms delays)
    - read TOTP code via pyotp.TOTP(secret).now() and submit
    - register page.on("request") handler that watches for redirect to
      `https://dashboard.kiusinghung.com/api/oauth/schwab/callback?code=...`
    - on the redirect request firing, capture the URL, abort the navigation
      (browser never actually GETs the callback)
    - parse the code + state from the captured URL
    - call backend POST /api/admin/brokers/schwab/oauth-callback?code=...&state=...&actor=tier2
       (admin-JWT-gated; uses the refresher's service-token JWT)
    - on success: log + emit metric schwab_tier2_refresh_total{result=success}
                  + reset consecutive_failures counter to 0
    - on failure: log + emit metric schwab_tier2_refresh_total{result=...}
                  + increment app_config.schwab.tier2_consecutive_failures
                  + Telegram alert (Phase 11; until then, structured log + email)
                  + Settings card shows red badge with "manual fix prompt"

  H2 — auto-disable on consecutive failures:
    if app_config.schwab.tier2_consecutive_failures >= 3:
      set app_config.schwab.tier2_refresh_enabled = false
      page operator with reason
      do NOT retry until manual re-enable
```

Feature flag: `app_config.schwab.tier2_refresh_enabled = false` default. Set true via admin UI explicitly. **Schwab anti-fraud risk:** running Tier-2 from a fixed VPS IP geographically distant from the user's normal login geo will likely trip Schwab's anti-fraud after some uses; this is documented in `runbook-schwab-setup.md` step 4 with mitigation suggestions (pre-register VPS IP via Schwab support if available; otherwise accept risk).

Reference implementation: github.com/QuantStrategyLab/SchwabTokenAutoRefresher (architecture pattern only — Python rewrite, runs on VPS, writes to app_secrets via backend HTTP API).

### 3.5 Token lifecycle

| Token | TTL | Owner of writes |
|---|---|---|
| `access_token` | 30 min (Schwab's TTL) | **backend only** (single-writer rule §3.6); sidecar requests refresh via gRPC, backend mints new tokens at the 25-min mark |
| `refresh_token` | 7 days (hard wall) | **backend only**; rotated by Tier-1 (`/api/oauth/schwab/callback`) or Tier-2 (`/api/admin/brokers/schwab/oauth-callback?actor=tier2`) |

Sidecar holds in-memory cache of the current `access_token` + `_account_hashes` for fast read paths. Sidecar does NOT call Schwab's token-refresh endpoint on its own. On `Configure` RPC, sidecar replaces in-memory tokens.

### 3.6 Token rotation contract (C2 — single-writer)

**Schwab rotates the refresh_token on every refresh** (verified in `Dashboard_old/backend/app/brokers/schwab.py:686-717`). To prevent the four-way race (Tier-1 user click + Tier-2 cron + sidecar restart + sidecar auto-refresh all rotating concurrently), **the backend is the single writer of `app_secrets.schwab.refresh_token`**.

#### 3.6.1 Single-writer enforcement

- Every refresh-token write is wrapped in `pg_try_advisory_lock(SCHWAB_REFRESH_LOCK_ID)`. The lock id is a constant `int` derived from `hash("schwab.refresh_token") & 0x7FFFFFFF`. Lock acquisition timeout: 5s; if not acquired, callback returns 503.
- Sidecar's `client.py` does **not** call Schwab's `/oauth/token` endpoint to refresh. Schwabdev's auto-refresh path is **disabled** (override `client.tokens.update_tokens()` to be a no-op; sidecar uses external token providers).
- Sidecar's near-expiry path: when `access_token_age > 25 min`, sidecar calls a new gRPC method `RequestTokenRefresh` (no args, returns new tokens). Backend accepts the call, takes the advisory lock, performs the refresh against Schwab's token endpoint, writes to `app_secrets`, releases the lock, returns the new tokens to the sidecar's gRPC channel. Sidecar updates its in-memory cache.

#### 3.6.2 Configure trigger table (C3)

| Trigger | When | Driver |
|---|---|---|
| **Lifespan startup** | Backend boot | `app/main.py` lifespan calls `BrokerConfigurer.configure_all()` (Phase 6 invariant 6.4#4: re-Configure dispatched per `Health.started_at` change) |
| **Sidecar restart detected** | Health probe sees `Health.started_at` increased | `BrokerConfigurer` re-Configures with current `app_secrets` |
| **OAuth callback success (Tier-1 or Tier-2)** | Inside `/api/oauth/schwab/callback` AND `/api/admin/brokers/schwab/oauth-callback` handlers | Synchronous Configure call before HTTP response returns |
| **Manual `app_secrets` rotation** | Operator hits `/api/admin/secrets` directly | `POST /api/admin/brokers/schwab/reconfigure` is the user-facing endpoint that re-issues Configure |
| **Sidecar near-expiry refresh** | Sidecar's `RequestTokenRefresh` gRPC | Backend writes new tokens + Configures the SAME sidecar with the new pair (no-op for other sidecars) |

**Invariant:** every write to `app_secrets.schwab.{access_token,refresh_token}` MUST be followed by a `/reconfigure` call before the HTTP response returns. The state is "not committed" until Configure returns OK.

Metric `schwab_sidecar_token_drift_seconds = (now() - last_configure_at)`; alert if > 60s after a known token write.

## 4. Data model

### 4.1 New `app_secrets` keys (Fernet-encrypted)

| Namespace | Key | Required | Lifecycle |
|---|---|---|---|
| `schwab` | `app_key` | ✓ | manual setup (from Schwab developer portal) |
| `schwab` | `app_secret` | ✓ | manual setup |
| `schwab` | `access_token` | ✓ after first OAuth | rotated every 25 min by **backend** (single writer) |
| `schwab` | `refresh_token` | ✓ after first OAuth | rotated weekly by Tier-1 or Tier-2 (single writer = backend) |
| `schwab` | `username` | only if Tier-2 enabled | manual setup |
| `schwab` | `password` | only if Tier-2 enabled | manual setup |
| `schwab` | `totp_secret` | only if Tier-2 enabled | manual setup (Base32 from Schwab MFA enrollment) |

**M5 — structlog redaction invariant:** `backend/app/core/logging.py` REDACTION_PATTERNS list MUST include `schwab\.password`, `schwab\.totp_secret`, `schwab\.app_secret`, `schwab\.refresh_token`, `schwab\.access_token`. `backend/tests/observability/test_logging_redaction.py` asserts these keys never appear unredacted in log output for any structlog event.

### 4.2 New `app_config` keys (cleartext)

| Namespace | Key | Type | Default |
|---|---|---|---|
| `schwab` | `callback_url` | string | `https://dashboard.kiusinghung.com/api/oauth/schwab/callback` |
| `schwab` | `access_token_issued_at` | ISO datetime | (set on every refresh) |
| `schwab` | `refresh_token_issued_at` | ISO datetime | (set on every Tier-1 / Tier-2 refresh) |
| `schwab` | `tier2_refresh_enabled` | bool | `false` |
| `schwab` | `tier2_consecutive_failures` | int | `0` |

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
-- upgrade
ALTER TABLE broker_accounts
  ADD COLUMN account_hash TEXT NULL;

COMMENT ON COLUMN broker_accounts.account_hash IS
  'Schwab-only: opaque account hash from /accountNumbers; required on all '
  'Schwab REST paths. NULL for non-Schwab brokers. Treated as PII-equivalent '
  '— never logged, boundary-stripped from REST responses (per §5.3).';

-- M4 partial index — only Schwab rows hit this lookup path
CREATE INDEX idx_broker_accounts_schwab_hash
  ON broker_accounts(broker_id, account_hash)
  WHERE account_hash IS NOT NULL;

-- downgrade
DROP INDEX IF EXISTS idx_broker_accounts_schwab_hash;
ALTER TABLE broker_accounts DROP COLUMN IF EXISTS account_hash;
```

Phase 7b's quote engine takes the next migration number (`0009_phase7b_instruments_symbol_aliases`) for `instruments + symbol_aliases` schema; no migration-number conflict.

Why a new column instead of overloading `gateway_label`: `account_hash` is per-account (varies across the Schwab login's N accounts), while `gateway_label` is per-sidecar.

**PII classification (H3):** `account_hash` is opaque per Schwab docs but deterministic per login → fingerprintable. Boundary handler strips it from `AccountResponse` (REST→frontend) just like `gateway_label` and `account_number` per Phase 4 M22 invariant. Test `tests/api/test_account_boundary_strip.py` asserts the field is absent from JSON responses.

## 5. Components

### 5.1 New: `sidecar_schwab/`

```
sidecar_schwab/
├── __init__.py
├── main.py                  # gRPC server bootstrap; reads SCHWAB_SIDECAR_PORT env
├── config.py                # CLI args, log config
├── handlers.py              # Configure, Health, ListAccounts, GetAccountSummary, GetPositions, GetOrders, RequestTokenRefresh
├── client.py                # SchwabClient — ONLY file that touches schwabdev directly
├── normalize.py             # Schwab JSON → proto Account / Position / Order mappers
├── auth.py                  # access_token freshness check; token cache; gRPC RequestTokenRefresh callback
├── stubs/                   # UNIMPLEMENTED stubs for SearchContracts/PlaceOrder/CancelOrder/ModifyOrder/OrderEvent/StreamQuotes
├── pyproject.toml           # uv-managed; pinned: schwabdev==3.0.3; deps: grpcio, grpcio-tools, structlog, pydantic
├── Dockerfile               # python:3.14-slim, grpc reflection, runs main.py
└── tests/
    ├── test_normalize.py
    ├── test_handlers_list_accounts.py
    ├── test_handlers_summary.py
    ├── test_handlers_positions.py
    ├── test_handlers_orders.py
    ├── test_auth_lifecycle.py    # mocked Schwabdev token refresh
    ├── test_configure_idempotent.py
    └── test_request_token_refresh.py  # gRPC RequestTokenRefresh round-trip
```

**M3 — Schwabdev confined to `client.py` only.** `handlers.py` and `normalize.py` interact only with our wrapper; never `import schwabdev` directly. If Schwabdev needs replacement (vendor drift, license change, abandonment), only `client.py` requires rewrite — Dashboard_old's hand-rolled httpx implementation (`schwab.py`, ~700 lines) serves as documented fallback. Schwabdev is pinned to exact version `==3.0.3` in `pyproject.toml`; upgrade procedure in `runbook-schwab-setup.md` step 7.

Mirrors the Phase 6 `sidecar_futu/` shape.

### 5.2 New: `sidecar_schwab_refresher/`

```
sidecar_schwab_refresher/
├── __init__.py
├── main.py                  # cron loop; runs once per docker-compose-defined schedule
├── refresher.py             # Playwright flow: navigate → fill → MFA → capture
├── stealth.py               # playwright-stealth bootstrap
├── selectors.py             # H2 — selector health check; documented selectors for fast hand-fix
├── totp.py                  # pyotp wrapper
├── config_writer.py         # writes new tokens to app_secrets via backend admin API
├── pyproject.toml           # deps: playwright, playwright-stealth, pyotp, httpx, structlog
├── Dockerfile               # python:3.14 + Xvfb + Playwright Chromium browser bundle
└── tests/
    ├── test_totp.py
    ├── test_refresher_unit.py    # mocked browser
    ├── test_selector_health.py   # H2 — asserts selectors pin the right fields, fail-fast on any miss
    └── test_config_writer.py
```

Feature-flagged: starts with `--enabled` only when `app_config.schwab.tier2_refresh_enabled = true`. Container exits 0 if flag is off (cron tick is a no-op).

### 5.3 Backend changes

#### `app/services/broker_registry_factory.py`

Add Schwab to the `BrokerConfigurer` lifecycle (Phase 6 introduced this for Futu). On startup + on every sidecar `started_at` delta + on `/reconfigure`:
1. Read `schwab.app_key` + `schwab.app_secret` + `schwab.refresh_token` + `schwab.access_token` (if fresh) from `app_secrets`.
2. Call sidecar `Configure` RPC.
3. Log + emit metric `broker_configure_total{label="schwab", reason}`.

#### `app/api/oauth.py` (new — public-facing)

One new public endpoint, **NOT** under `/api/admin/`, with CF Access bypass policy (path-prefix rule):

```
GET  /api/oauth/schwab/callback?code=...&state=...
       → validates state nonce: GETDEL schwab_oauth_nonce:{nonce}; recompute
         hmac(nonce, APP_SECRET_KEY); compare against query-string state; reject on mismatch (403)
       → acquires PG advisory lock pg_try_advisory_lock(SCHWAB_REFRESH_LOCK_ID)
       → exchanges code for access_token + refresh_token
       → writes both to app_secrets
       → sets app_config schwab.access_token_issued_at + refresh_token_issued_at
       → calls sidecar Configure RPC (synchronous; rolls back on Configure failure)
       → releases advisory lock
       → publishes Redis pub/sub: config:invalidate:schwab
       → returns 200 with token-issued-at timestamps (for the Settings card to refetch)
```

#### `app/api/brokers_admin.py` (new — admin-JWT-gated)

Three new admin REST endpoints, all gated by `require_admin_jwt`:

```
GET  /api/admin/brokers/schwab/oauth-start
       → mints state nonce + HMAC-signs with APP_SECRET_KEY
       → SET schwab_oauth_nonce:{nonce} <user_email> NX EX 600 (atomic; fails if exists)
       → returns 302 to Schwab consent URL with state=signed

POST /api/admin/brokers/schwab/oauth-callback?code=...&state=...&actor=tier2
       → SAME path as /api/oauth/schwab/callback BUT admin-JWT-gated for use by Tier-2 service token
       → Tier-2 calls THIS endpoint (not the public callback) since it has the service-token JWT
       → semantics identical to public callback otherwise

POST /api/admin/brokers/schwab/reconfigure
       → no payload; re-issues Configure to the schwab sidecar with current app_secrets
       → used by Tier-2 service after a successful refresh AND by manual secret rotation
```

**H1 — state nonce CSRF defense (atomic-update, HMAC-signed):**
- Generation: `nonce = secrets.token_urlsafe(32)`. `signed = base64url(hmac(nonce, APP_SECRET_KEY))`.
- Storage: `SET schwab_oauth_nonce:{nonce} <user_email> NX EX 600` (atomic write; rejects collision).
- Consumption: `GETDEL schwab_oauth_nonce:{nonce}` (Redis 6.2+ atomic single-use). Recompute HMAC and compare against query-string `state`.
- Result: Redis compromise alone doesn't allow forgery (HMAC-SHA256 over the raw nonce keeps state values un-spoofable without the app secret).

#### `app/services/account_service.py`

Already broker-agnostic per Phase 6. Adds Schwab via `SIDECAR_BROKERS` map without code changes.

`AccountResponse.account_hash` (new optional field) populated from `Account.account_hash` on the wire (proto extension below). NULL for non-Schwab brokers. **Stripped from the boundary response (not exposed to the frontend)** — same handling as `gateway_label` and `account_number` per Phase 4 M22 invariant.

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

// New RPC for sidecar→backend token-refresh callback (C2 single-writer)
service Broker {
  // ... existing RPCs ...

  rpc RequestTokenRefresh(TokenRefreshRequest) returns (TokenRefreshResponse);
}

message TokenRefreshRequest {
  string broker_id = 1;  // "schwab" — distinguishes if other brokers ever need this pattern
}

message TokenRefreshResponse {
  string access_token = 1;
  string refresh_token = 2;
  google.protobuf.Timestamp access_issued_at = 3;
}
```

No version bump required (additive field + new RPC). Regenerate `_pb2.py` for backend, sidecar_schwab, and `api-generated.ts` for frontend. Existing callers ignore unknown fields per protobuf semantics.

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

**H6 — refresh strategy:**
- Backend publishes Redis pub/sub `config:invalidate:schwab` on every successful OAuth callback.
- New SSE endpoint `/api/admin/config/stream?ns=schwab` forwards the pub/sub to subscribed clients.
- Card subscribes via SSE for instant updates. Immediate refetch on `popstate` from the OAuth tab close.
- Polling fallback: 5s for the first 60s after OAuth start (covers SSE drop), then 60s steady state.
- Red badge at <24h. Email-style nag: T-2d, T-1d, T-2h.

**M7 — Disconnect button semantics:**
- Confirms via dialog ("Disconnect Schwab? This will sign out the dashboard from Schwab and stop quoting / trading. Saved credentials [if any]: [delete] [keep]").
- DELETEs all `app_secrets.schwab.{access_token,refresh_token}` (always). Optionally also `username/password/totp_secret` per dialog choice (L5 — credential minimization).
- Calls `/reconfigure` so the sidecar enters unconfigured state (`gateway_connected=false`).
- Soft-deletes Schwab `broker_accounts` rows (`deleted_at = now()`) per Phase 5 invariant.
- Phase 8 inherits: blocks Disconnect if any open Schwab orders exist (Phase 7a is read-only, so N/A here).

#### `frontend/src/services/schwab.ts` (new)

Thin wrapper around the three new admin endpoints. Used by SchwabCard only.

#### Existing `AccountPicker`

No changes required — Schwab accounts flow through the existing `AccountResponse` contract once the sidecar discovers them. Visual: Schwab icon + ALL-LIVE label (no paper toggle for Schwab rows since `mode=LIVE` always).

## 6. Tests

### 6.1 Sidecar unit tests

| File | Coverage |
|---|---|
| `test_normalize.py` | Schwab JSON → proto Account/Position/Order mappers (~25 assertions: enum coverage for `assetType`, `orderType`, `tif`, status table at §3.2.1) |
| `test_handlers_list_accounts.py` | Configure → ListAccounts round-trip with mocked Schwabdev client |
| `test_handlers_summary.py` | NLV/cash/buying_power extraction from securitiesAccount JSON |
| `test_handlers_positions.py` | Position mapping incl. day_pnl, avg_cost |
| `test_handlers_orders.py` | 7-day window query, status mapping (18 Schwab → 6 ours), `avg_fill_price` from `orderActivityCollection` per §3.2.2 |
| `test_auth_lifecycle.py` | access_token freshness check, RequestTokenRefresh gRPC round-trip, no-self-refresh assertion |
| `test_configure_idempotent.py` | Configure called twice with same tokens is a no-op |
| `test_request_token_refresh.py` | sidecar near-expiry triggers `RequestTokenRefresh`; backend mock returns new tokens; sidecar updates cache |
| `test_account_hash_404_retry.py` | H3 — 404 → cache invalidation → `/accountNumbers` refresh → retry once → second 404 surfaces NOT_FOUND |
| `test_rate_limit_429.py` | M6 — 429 with `Retry-After` honored; 3× retry with jitter; metric emitted |

Reuse fixtures from `Dashboard_old/backend/tests/test_schwab_*.py` (504 lines of real Schwab response shapes) — fork into new `backend/tests/fixtures/schwab_test_data.py`.

### 6.2 Backend integration tests

| File | Coverage |
|---|---|
| `tests/integration/test_schwab_oauth_flow.py` | mocked Schwab token endpoint; full /oauth-start → public /api/oauth/schwab/callback round-trip; verifies state-nonce HMAC; advisory-lock contention; app_secrets writes; Configure call |
| `tests/integration/test_schwab_account_listing.py` | sidecar mock returning 3 accounts; backend `/api/brokers/accounts` returns 3 Schwab rows + IBKR/Futu rows from prior phases |
| `tests/integration/test_schwab_state_nonce.py` | mismatched HMAC → 403; reused state → 403 (GETDEL atomicity); expired state → 403; unsigned state → 403 |
| `tests/integration/test_schwab_oauth_callback_public.py` | Public callback path is reachable WITHOUT admin JWT; admin-gated callback path requires JWT |
| `tests/integration/test_account_boundary_strip.py` | H3 — `AccountResponse` JSON does NOT contain `account_hash` for any broker (boundary strip) |
| `tests/integration/test_logging_redaction.py` | M5 — schwab secrets never appear unredacted in any log event |
| `tests/integration/test_token_rotation_atomicity.py` | C2 — concurrent Tier-1 + Tier-2 callback simulation; advisory lock serializes; no torn writes |

### 6.3 Tier-2 refresher tests

| File | Coverage |
|---|---|
| `test_totp.py` | `pyotp.TOTP("BASE32SECRET").now()` produces 6-digit code; clock-skew tolerance |
| `test_refresher_unit.py` | mocked Playwright page; verifies fill → submit → URL capture sequence; no actual navigation to public callback |
| `test_selector_health.py` | H2 — selector health probe asserts username/password/MFA fields locate within 5s budget; missing field → fail without credential submission |
| `test_config_writer.py` | writes new tokens via backend admin HTTP; handles 5xx with retry |
| `test_consecutive_failures_auto_disable.py` | H2 — 3 consecutive failures flips `tier2_refresh_enabled=false`; counter resets on success |

No real Schwab login in CI (would burn refresh tokens). Tier-2 flow has a `--dry-run` mode that skips the actual login; CI exercises that.

### 6.4 Real-Schwab smoke (manual / nightly)

`backend/tests/integration/test_real_schwab_smoke.py` gated on `CI_USE_REAL_SCHWAB=1`. Hits production Schwab read-only endpoints (`/userPreference`, `/accountNumbers`, `/accounts`) and asserts non-empty response. Run nightly via `nightly-real-schwab.yml` (new GitHub Actions workflow) at **12:00 UTC** (L3 — staggered from Tier-2 cron at 13:00 UTC to avoid advisory-lock contention).

### 6.5 Coverage target

≥ 80 % per the project rule. Sidecar package: real target ~85 %. **Tier-2 refresher: ~90 %** (M5 follow-on — Tier-2 has near-zero observability in production until it fails; unit-test coverage is the safety net).

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
    REFRESH_AT_HOUR_UTC: "13"  # L3 — staggered from nightly-real-schwab at 12:00 UTC
    DRY_RUN: "false"
  networks: [internal]
  profiles: ["tier2"]  # only starts with `docker compose --profile tier2 up`
```

### 7.2 Backend wiring

`backend/app/main.py` lifespan adds Schwab to the `BrokerConfigurer` startup loop (already broker-agnostic per Phase 6). No code change beyond the `SIDECAR_BROKERS` map row.

### 7.3 CF Access bypass policy (C1)

Add a path-prefix bypass rule via `scripts/cloudflare/access-bypass-schwab-callback.sh`:

```bash
# Bypass the CF Access gate for the Schwab OAuth callback path so Schwab's
# server-issued redirects can reach the backend. The route is protected by
# the HMAC-signed single-use state nonce instead.
cf-access-app:
  policy:
    name: "Bypass /api/oauth/schwab/callback"
    decision: bypass
    include:
      - everyone: true
    require:
      - paths: ["/api/oauth/schwab/callback"]
```

Idempotent — re-runs of the script are no-ops if the policy exists.

### 7.4 Operator setup runbook (`deploy/runbook-schwab-setup.md`)

0. **Pre-deploy:** snapshot `app_secrets` table to encrypted file in `/var/backups/dashboard/app_secrets-{ts}.bin` (rollback safety per architect-review cross-cutting).
1. Register dashboard at `developer.schwab.com`. Create app: callback `https://dashboard.kiusinghung.com/api/oauth/schwab/callback`. Copy app_key + app_secret.
2. Seed `app_secrets`: `schwab.app_key` + `schwab.app_secret`.
3. Deploy sidecar: `docker compose up -d schwab-sidecar`.
4. Apply CF Access bypass: `bash scripts/cloudflare/access-bypass-schwab-callback.sh`.
5. Click "Connect Schwab" on Settings → completes Tier-1 OAuth.
6. Optional Tier-2: enable in admin UI, then seed `schwab.username` + `schwab.password` + `schwab.totp_secret` (Base32 from Schwab MFA QR — record once during MFA enrollment). **Anti-fraud risk note:** running Tier-2 from a fixed VPS IP geographically distant from your normal login geo will likely trip Schwab's anti-fraud after some uses; pre-register the VPS IP via Schwab support if available, otherwise accept the risk.
7. Optional Tier-2 deploy: `docker compose --profile tier2 up -d schwab-refresher`.
8. Verify: `curl -s https://dashboard.kiusinghung.com/api/brokers/accounts -H "$CF_HDR"` returns Schwab rows.
9. **Schwabdev upgrade procedure:** when ready to upgrade, change pin in `sidecar_schwab/pyproject.toml`, run sidecar tests, deploy, monitor `schwab_http_requests_total` for new error patterns 24h before declaring done.

## 8. Observability

### 8.1 New Prometheus metrics

| Metric | Type | Purpose |
|---|---|---|
| `broker_configure_total{label,reason}` | Counter | extends Phase 6 metric to schwab label |
| `schwab_oauth_start_total` | Counter | (no actor label — tier1 vs tier2 derive from caller path; L1) |
| `schwab_oauth_callback_total{path,result}` | Counter | path=public\|admin; result=success/state_mismatch/token_exchange_fail/lock_timeout |
| `schwab_access_token_age_seconds` | Gauge | sidecar exposes; alerts on >1700s (29min — close to TTL) |
| `schwab_refresh_token_age_hours` | Gauge | backend exposes; alerts on >168h (7d wall) |
| `schwab_refresh_token_uses_per_24h` | Gauge | H4 — restart-flapping detector |
| `schwab_account_hash_refresh_total{reason}` | Counter | H3 — `initial / rotation_detected / 404_retry` |
| `schwab_http_requests_total{endpoint, status}` | Counter | M6 — rate-limit observability |
| `schwab_sidecar_token_drift_seconds` | Gauge | C3 — (now() - last_configure_at); alerts > 60s after token write |
| `schwab_tier2_refresh_total{result}` | Counter | success / login_failed / mfa_failed / dom_changed / network_error / auto_disabled |
| `schwab_tier2_last_run_timestamp_seconds` | Gauge | for staleness detection |
| `broker_normalize_unknown_total{label,field,value}` | Counter | extends Phase 6 metric — H5 currency_base + M1 unknown statuses |

### 8.2 New Prometheus alerts

`deploy/prometheus/alerts.yml` adds group `phase7a_schwab`:

| Alert | Severity | Condition |
|---|---|---|
| `SchwabRefreshTokenExpiringSoon` | warning | `schwab_refresh_token_age_hours > 144` (T-1d) for 5m |
| `SchwabRefreshTokenExpired` | page | `schwab_refresh_token_age_hours > 168` for 5m |
| `SchwabAccessTokenStuck` | warning | `schwab_access_token_age_seconds > 1700` for 5m |
| `SchwabRefreshTokenUseFlapping` | warning | `schwab_refresh_token_uses_per_24h > 10` for 1h (H4 — sidecar restart-flapping) |
| `SchwabSidecarTokenDriftHigh` | warning | `schwab_sidecar_token_drift_seconds > 60` for 5m (C3 — Configure drift) |
| `SchwabTier2RefreshFailed` | warning (Tier-2 enabled only) | 2 consecutive `schwab_tier2_refresh_total{result!="success"}` |
| `SchwabTier2AutoDisabled` | page (Tier-2 enabled only) | `schwab_tier2_refresh_total{result="auto_disabled"} > 0` (H2 — auto-disable triggered) |
| `SchwabSidecarUnreachable` | page | `up{job="schwab-sidecar"} == 0` for 2m |
| `SchwabRateLimitNear` | warning | `rate(schwab_http_requests_total[1m]) * 60 > 100` for 5m (M6 — 120 req/min Schwab cap) |

## 9. Open risks + mitigations

| Risk | Mitigation |
|---|---|
| Schwab Developer Portal app rejection / approval delay | User confirmed access ready (pre-approved). |
| 7-day refresh wall + Tier-1 nag fatigue | Tier-2 auto-refresh + Telegram alert (Phase 11) + 4-day safety margin in Tier-2 cron. |
| Tier-2 Playwright fragility (Schwab DOM changes) | DOM-change failure → metric → alert → fall back to Tier-1 + auto-disable on 3× failures. Pre-submit selector health check prevents blind credential submission to changed DOM. |
| Schwab anti-fraud trips on Tier-2 from VPS IP | Tier-2 is opt-in. VPS is a single fixed IP. Document the geo-mismatch warning in runbook step 6. |
| Schwab account hash rotation | Sidecar refreshes `_account_hashes` on every Configure + on any 404 from a hash-keyed path; metric `schwab_account_hash_refresh_total{reason}` tracks rotation events. |
| Multi-account ambiguity | One Schwab login → N accounts is the common case; sidecar enumerates all via `/accountNumbers`. Multi-Schwab-login deferred post-v1.0. Per-account routing is in scope: `GetPositions(AccountRef{account_number})` resolves through `_account_hashes` cache. |
| Schwab `Mode=LIVE` only — no paper accounts | `AccountPicker` already handles per-account mode; Schwab rows just always show "LIVE". No special handling. |
| State-nonce CSRF surface | Single-use, 10-min TTL Redis-backed nonce + HMAC-SHA256 signature with `APP_SECRET_KEY` (H1). `GETDEL` atomicity prevents replay. Redis compromise alone insufficient to forge state. |
| Tier-2 credentials in `app_secrets` | Fernet-encrypted (Phase 2 infrastructure). Operator-explicit opt-in. Same risk profile as the Futu RSA key from Phase 6. |
| **TOTP secret in `app_secrets` defeats true MFA** (M5 explicit threat) | Storing the TOTP secret on the same VPS as the password effectively reduces 2-factor to 1-factor for the Tier-2 attacker model (anyone with VPS host access). Operator-explicit opt-in; documented threat in runbook step 6. |
| Schwab API rate limits (120 req/min standard) | Sidecar throttles via `asyncio.Semaphore(10)`. `SchwabRateLimitNear` alert at ≥100 req/min sustained. 429 → `Retry-After` honored + 3× retry with jitter. |
| Token rotation race (4-way: Tier-1, Tier-2, sidecar restart, sidecar near-expiry) | C2 single-writer rule: only backend writes `schwab.refresh_token`. PG advisory lock serializes all token-mint paths. Sidecar `RequestTokenRefresh` gRPC keeps backend as the only Schwab token-endpoint caller. |
| Schwabdev abandonment / breaking change | M3 — Schwabdev confined to `client.py`; pinned to `==3.0.3`; Dashboard_old hand-rolled httpx implementation (~700 lines) documented as fallback. |
| Sidecar restart burns refresh-token uses | H4 — `Configure` passes `access_token` if `<25 min` old to avoid burning a refresh-token use on every restart. `SchwabRefreshTokenUseFlapping` alert on >10 uses/24h flags watchdog flapping. |
| Frontend `SchwabCard` polling staleness post-OAuth | H6 — Redis pub/sub `config:invalidate:schwab` → SSE → instant card update. 5s polling fallback for the first 60s. |

## 10. Phase 7a chunk plan

| Chunk | Theme | Headline tasks |
|---|---|---|
| **A** | Proto + sidecar shell | Add `account_hash` to `Account` proto; add `RequestTokenRefresh` RPC + messages. Generate types backend + sidecar + frontend. New `sidecar_schwab/` skeleton (Dockerfile, pyproject, main, gRPC server bootstrap). |
| **B** | Sidecar core | Configure RPC, ListAccounts (via Schwabdev `/accountNumbers` + `/accounts`), GetAccountSummary, GetPositions, GetOrders (status mapping + `avg_fill_price` extraction). Auth lifecycle: token freshness check, **no self-refresh**, RequestTokenRefresh gRPC outbound. Schwabdev confined to `client.py`. M6 semaphore + 429 handling. H3 account_hash 404→retry-once. |
| **C** | Backend wiring | Alembic 0008 (`account_hash` column + partial index + downgrade). `BrokerConfigurer` extension. New public `/api/oauth/schwab/callback` (CF Access bypass). New admin `/api/admin/brokers/schwab/oauth-{start,callback}` + `/reconfigure`. State-nonce HMAC + `GETDEL`. PG advisory lock around refresh-token writes. C2 single-writer enforcement (backend's gRPC server-side handler for `RequestTokenRefresh`). C3 Configure-trigger plumbing. M5 structlog redaction. |
| **D** | Tier-1 frontend | `SettingsPage` → `SchwabCard` component. OAuth-start button. Token-expiry display + nag thresholds. Disconnect (M7). Polling + SSE strategy (H6). |
| **E** | Tier-2 refresher | `sidecar_schwab_refresher/` package + Playwright + stealth + TOTP + config_writer. H2 selector health check. H2 consecutive-failure auto-disable. docker-compose `tier2` profile. Feature-flag wiring. Tests (mocked browser + selector health + auto-disable). |
| **F** | Tests + smoke | Sidecar units (10 files). Backend integration tests (7 files). Refresher tests (5 files). `test_real_schwab_smoke.py` gated. `nightly-real-schwab.yml` at 12:00 UTC (L3 stagger). |
| **G** | Ops | `runbook-schwab-setup.md` (9 steps incl. CF Access bypass + Schwabdev upgrade procedure). `docker-compose.prod.yml` additions. Prometheus metrics + alerts (12 metrics + 9 alerts). CHANGELOG / TASKS / CLAUDE.md / memory `phase7a_schwab_topology.md`. v0.7.0 tag. |

## 11. Architectural pillars set in this phase

- **Cloud-broker sidecar deployment pattern** — sidecars don't have to live on the NUC. Cloud-only brokers run as docker-compose services on the VPS, plaintext gRPC over the internal docker network. Sets the precedent for any future cloud-only adapter (Polygon, Alpaca, Coinbase trading, …).
- **OAuth weekly-refresh pattern (two-tier)** — Tier-1 manual UI as the contract; Tier-2 headless automation as opt-in convenience. Sets the precedent for any future OAuth broker with short refresh-TTL walls.
- **`account_hash` on `broker_accounts`** — opaque-identifier-per-account pattern (PII-equivalent, boundary-stripped). Reusable for any future broker that hashes account IDs.
- **OAuth callback host topology** — public callback path under `/api/oauth/...` (CF Access bypass via path rule); admin-JWT-gated callback path mirror under `/api/admin/...` for Tier-2 service calls. Pattern reusable for any future browser-redirect OAuth broker.
- **Token single-writer rule** — backend is the only writer of refresh tokens; sidecars request refresh via gRPC. Eliminates 4-way race classes in any future broker with refresh-token semantics.
- **Configure trigger contract** (Phase 6 invariant 6.4#4 restated) — three explicit triggers: lifespan startup, sidecar restart (started_at delta), `/reconfigure` (OAuth callback or manual rotation). Every `app_secrets` write is followed by Configure before HTTP response returns.

## 12. Out-of-scope / explicitly punted

### Architect-deferred from CRIT+HIGH+MED (applied inline; only LOWs deferred)

| Finding | Disposition |
|---|---|
| **L1** — `actor=tier1\|tier2` query param spoofing | Fixed inline as part of C1 callback split: tier1 uses public path `/api/oauth/schwab/callback`; tier2 uses admin path `/api/admin/brokers/schwab/oauth-callback`; actor is derived from path, no query param. Metric `schwab_oauth_callback_total{path}` not `{actor}`. |
| **L2** — `userPreference` ping at boot | Defer to plan: optional pre-flight check in chunk B. Not blocking. |
| **L3** — nightly cron vs Tier-2 cron at 13:00 UTC | Fixed inline: nightly real-Schwab moved to 12:00 UTC; Tier-2 stays at 13:00 UTC. |
| **L4** — proto `account_hash` as `string` vs `bytes` | Defer: `string` chosen for now (matches Schwab's hex format in practice). Reconsider in Phase 14/15 when bytes-shaped tokens land. |
| **L5** — Tier-2 disable removes credentials | Fixed inline as part of M7 Disconnect: dialog asks "Saved credentials: [delete] [keep]". |

### Punted to other phases

- **StreamQuotes RPC** — Phase 7b
- **Schwab options chain** — Phase 12
- **Schwab futures contract roll** — Phase 14
- **PlaceOrder / CancelOrder / ModifyOrder** — Phase 8
- **Schwab `OrderEvent` via `ACCT_ACTIVITY` streamer** — Phase 8 (locked here, not 7b)
- **Multi-Schwab-login** — post-v1.0
- **In-process Schwab adapter** (rejected design — process-isolation was decisive)
- **NUC Schwab sidecar** (rejected design — Schwab is cloud)
