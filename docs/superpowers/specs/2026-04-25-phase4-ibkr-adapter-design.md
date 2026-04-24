# Phase 4 — IBKR adapter (read-only) + broker_accounts + gRPC sidecars

**Status:** design (2026-04-25). Pending architect review.
**Targets:** v0.4.0.
**Predecessor:** v0.3.0 (Phase 3 frontend shell, mocks).
**Successor:** Phase 4.5 (tick streams + historical bars), then Phase 5 (trade execution).

## 1. Goal

Replace Phase 3's mocked `accounts`/`positions`/`orders` services with **real, read-only** data from the 4 IBKR Gateways already running on the NUC. Lay the foundation (`BrokerAdapter` contract, sidecar topology, `broker_accounts` table, mTLS plumbing) that Phases 5–8 will reuse for trade execution and Futu/Schwab adapters.

## 2. Scope

**In:**

- gRPC contract (`proto/broker/v1.proto`) covering: health, list managed accounts, account summary, positions, orders (open + today's filled), contract lookup.
- One Python sidecar process per IBKR gateway (4 instances), launched via Windows Task Scheduler on the NUC. Each sidecar wraps a single `ib_async.IB()` and serves the gRPC contract.
- Self-signed mTLS between FastAPI backend (VPS) and the 4 sidecars (NUC), over WireGuard.
- Alembic migration `0002_broker_accounts.py` + the `broker_accounts` table.
- Backend service layer (`app/services/brokers.py`) + REST routes (`app/api/accounts.py`).
- Frontend service flip from fixtures to REST behind `VITE_USE_MOCKS` flag (default `false` in prod, `true` in Storybook).
- Watchdog + tray extensions on the NUC: ports + adapts the existing `BrokerWatchdog.ps1` / `BrokerTray.ps1` from `/mnt/c/Dashboard_old/deploy/nuc/`, adds sidecar-health probes, preserves IBKR maintenance-window awareness.
- Playwright smoke extended with `/api/accounts/*` round-trip via CF Access service token.

**Out:**

- Order placement / modify / cancel — Phase 5.
- Real-time tick streaming + historical bars — Phase 4.5 (gRPC bidi on the same contract).
- FutuOpenD sidecar — Phase 6 (the proto's `broker_id` enum already covers it; the sidecar template ports cleanly).
- Schwab — Phase 8.
- Portfolio snapshots + historical NLV chart — Phase 4.5.
- Frontend WebSocket push — Phase 4.5.

## 3. Architecture

### 3.1 Topology

```
                                 Cloudflare Tunnel
  VPS  : FastAPI backend  ──────────────────────────►  (human + service tokens)
            │
            │ gRPC + mTLS over WireGuard (10.10.0.2:18001-18004)
            ▼
  NUC  : 4 Windows-Task-Scheduler-launched Python sidecars
            │  ibkr-sidecar-isa-live      (port 18001)
            │  ibkr-sidecar-isa-paper     (port 18002)
            │  ibkr-sidecar-normal-live   (port 18003)
            │  ibkr-sidecar-normal-paper  (port 18004)
            │
            │ ib_async on 127.0.0.1:4001-4004 (gateway API sockets, already authenticated)
            ▼
  4× IB Gateway java processes (already running, no change in Phase 4)
```

Sidecars are **pure protocol translators**: gRPC request → `ib_async` call → gRPC response. They hold no broker credentials of their own — IBC has already authenticated the Gateway socket on the same machine.

Backend talks to *all 4* sidecars; aggregation (e.g., "positions across all live accounts") happens in the FastAPI service layer, not in any sidecar.

### 3.2 Data flow — `GET /api/accounts/{id}/positions`

```
1. Frontend       : GET /api/accounts/<uuid>/positions
2. FastAPI dep    : require_admin_jwt() → CF Access identity
3. AccountService : look up broker_accounts by uuid
                    → resolve (broker_id, gateway_label, account_number)
4. BrokerRegistry : pick sidecar for gateway_label
5. gRPC client    : Broker.GetPositions(AccountRef{account_number})
6. Sidecar        : ib_async ib.reqPositionsAsync() filtered by account
                    + ib.reqPnLSingle for unrealized PnL (per stock_splits memory)
7. Response       : Position[] with normalized money (UK-pence handled in sidecar)
8. FastAPI        : map proto → API JSON, return
```

## 4. Components

### 4.1 gRPC contract — `proto/broker/v1.proto`

```proto
syntax = "proto3";
package broker.v1;

import "google/protobuf/timestamp.proto";

service Broker {
  rpc Health(HealthRequest)             returns (HealthResponse);
  rpc ListManagedAccounts(Empty)        returns (AccountsResponse);
  rpc GetAccountSummary(AccountRef)     returns (SummaryResponse);
  rpc GetPositions(AccountRef)          returns (PositionsResponse);
  rpc GetOrders(AccountRef)             returns (OrdersResponse);
  rpc GetContract(ContractRef)          returns (ContractResponse);
}

enum BrokerId      { BROKER_UNSPECIFIED = 0; IBKR = 1; FUTU = 2; SCHWAB = 3; }
enum TradingMode   { MODE_UNSPECIFIED = 0; LIVE = 1; PAPER = 2; }
enum AssetClass    { ASSET_UNSPECIFIED = 0; STOCK = 1; ETF = 2; OPTION = 3; FUTURE = 4; FOREX = 5; CRYPTO = 6; BOND = 7; MUTUAL_FUND = 8; WARRANT = 9; }
enum OrderSide     { SIDE_UNSPECIFIED = 0; BUY = 1; SELL = 2; }
enum OrderType     { TYPE_UNSPECIFIED = 0; MARKET = 1; LIMIT = 2; STOP = 3; STOP_LIMIT = 4; }
enum TimeInForce   { TIF_UNSPECIFIED = 0; DAY = 1; GTC = 2; IOC = 3; FOK = 4; }
enum OrderStatus   { STATUS_UNSPECIFIED = 0; PENDING = 1; SUBMITTED = 2; PARTIAL = 3; FILLED = 4; CANCELLED = 5; REJECTED = 6; }

message Empty {}

message HealthRequest  {}
message HealthResponse {
  string label              = 1;   // "isa-live", etc.
  bool   gateway_connected  = 2;   // ib_async.isConnected()
  string gateway_version    = 3;   // ib_async.client.serverVersion()
  google.protobuf.Timestamp last_tick_at = 4;  // useful for staleness
  string sidecar_version    = 5;   // git SHA at sidecar build time
}

message Account {
  string       account_number = 1;
  TradingMode  mode           = 2;
  string       gateway_label  = 3;
  string       currency_base  = 4;   // 'USD', 'GBP'…
}
message AccountsResponse { repeated Account accounts = 1; }
message AccountRef       { string account_number = 1; }

message Money { string value = 1; string currency = 2; }   // decimal-as-string

message Summary {
  Money net_liquidation = 1;
  Money total_cash      = 2;
  Money realized_pnl    = 3;
  Money unrealized_pnl  = 4;
  Money buying_power    = 5;
  google.protobuf.Timestamp updated_at = 6;
}
message SummaryResponse { Summary summary = 1; }

message Contract {
  string      symbol       = 1;
  string      exchange     = 2;
  string      currency     = 3;
  AssetClass  asset_class  = 4;
  string      conid        = 5;     // IBKR contract id, as string
  string      local_symbol = 6;
}
message ContractRef      { string conid = 1; }
message ContractResponse { Contract contract = 1; }

message Position {
  Contract contract           = 1;
  string   quantity           = 2;   // decimal-as-string
  Money    avg_cost           = 3;
  Money    market_price       = 4;
  Money    market_value       = 5;
  Money    unrealized_pnl     = 6;   // from reqPnLSingle, NOT computed naively
  Money    realized_pnl_today = 7;
  Money    daily_pnl          = 8;
}
message PositionsResponse { repeated Position positions = 1; }

message Order {
  string       order_id      = 1;
  Contract     contract      = 2;
  OrderSide    side          = 3;
  OrderType    order_type    = 4;
  string       quantity      = 5;
  Money        limit_price   = 6;
  Money        stop_price    = 7;
  TimeInForce  time_in_force = 8;
  OrderStatus  status        = 9;
  string       quantity_filled = 10;
  Money        avg_fill_price  = 11;
  google.protobuf.Timestamp submitted_at = 12;
  google.protobuf.Timestamp updated_at   = 13;
}
message OrdersResponse { repeated Order orders = 1; }
```

**Invariants:**
- All monetary values are `Money` (decimal-as-string + currency). Never float across the wire.
- All quantities are decimal-as-string. Fractional shares (Schwab + IBKR) supported by default.
- Unrealized PnL on `Position` comes from `IB.reqPnLSingle()` per the v1 fix recorded in `stock_splits.md` — NOT from `(market_price − avg_cost) × quantity` which breaks on splits.
- UK-listed-stock pence-denominated quotes are normalized to pounds **inside the sidecar** before emitting (per `ibkr_uk_pence_units.md` empirical follow-up: only quotes need scaling; `avg_cost` stays as IBKR returns it).
- Codegen targets: Python (`grpcio-tools`) for backend + sidecar; TS (`@bufbuild/protoc-gen-es`) generated to `frontend/src/proto-gen/` for Phase 4.5+ (gitignored, regenerated by `pnpm proto:gen`).

### 4.2 Sidecar entrypoint — `sidecar/ibkr_sidecar.py`

```
inputs (CLI flags or env):
  --label             (e.g., "isa-live")
  --gateway-port      (4001..4004)
  --grpc-port         (18001..18004)
  --tls-cert-pem      (path to sidecar's server cert)
  --tls-key-pem       (path to sidecar's server private key)
  --tls-ca-bundle-pem (path to client-cert CA bundle)
  --log-dir           (defaults to %ProgramData%\dashboard\sidecar-<label>\)
```

Lifecycle:
1. Load cert material; refuse to start if any path missing.
2. Build async gRPC server bound to `10.10.0.2:<grpc-port>`, mTLS-required.
3. Connect `ib_async.IB()` to `127.0.0.1:<gateway-port>` with `clientId` derived from label hash (deterministic but non-colliding across labels — IBKR rejects duplicate clientIds on the same gateway).
4. Subscribe to `accountSummaryEvent` + `pnlSingleEvent` (passive, just keeps the subscriptions warm).
5. Serve until `SIGTERM`/`SIGINT`.
6. On shutdown: `cancelPnLSingle` for every cached subscription, `IB.disconnect()`, drain gRPC server.

Failure modes:
- Gateway socket dies → `ib_async` raises → sidecar logs WARN, gRPC `Health` returns `gateway_connected=false`. Sidecar **does not** auto-restart the Gateway (that's `BrokerWatchdog.ps1`'s job).
- Gateway returns `clientId already in use` → fatal, sidecar exits 64 — Task Scheduler restart kicks in. Watchdog sees the new instance.
- IBKR daily reset window (`Test-InResetWindow` true) → backend swallows `gateway_connected=false`, watchdog skips kill+restart.

Packaging: `pyinstaller --onefile sidecar/ibkr_sidecar.py` produces a Windows `.exe` checked into release artifacts (NOT git). Build step in `scripts/build-sidecar.ps1` (NUC-side); emits `dist/ibkr-sidecar.exe`. Avoids needing a Python install on the NUC just for sidecars (uv-on-Windows works but adds an install dependency for ops).

### 4.3 Backend service layer — `app/services/brokers.py`

```python
class BrokerSidecarClient:
    """One per (broker_id, gateway_label). Owns a gRPC AsyncStub."""
    async def health(self) -> HealthResponse: ...
    async def list_managed_accounts(self) -> list[Account]: ...
    async def get_account_summary(self, account_number: str) -> Summary: ...
    async def get_positions(self, account_number: str) -> list[Position]: ...
    async def get_orders(self, account_number: str) -> list[Order]: ...
    async def get_contract(self, conid: str) -> Contract: ...

class BrokerRegistry:
    """4 clients, lifespan-managed. Reconnect with exponential backoff."""
    async def get_client(self, label: str) -> BrokerSidecarClient: ...
    async def healthy_clients(self) -> list[BrokerSidecarClient]: ...
    async def discover_loop(self) -> None:
        """Every 60s: ListManagedAccounts on each healthy client → upsert broker_accounts."""

class AccountService:
    """Thin DB + registry orchestration."""
    async def list_accounts(self) -> list[AccountRow]: ...
    async def get_summary(self, account_id: UUID) -> Summary: ...
    async def get_positions(self, account_id: UUID) -> list[Position]: ...
    async def get_orders(self, account_id: UUID) -> list[Order]: ...
```

`BrokerRegistry.discover_loop` is started by FastAPI lifespan; cancelled at shutdown. Failure to reach a sidecar logs WARN + bumps `adapter_sidecar_health{label,result="unreachable"}` Prometheus metric, does NOT raise.

### 4.4 broker_accounts schema — Alembic migration `0002_broker_accounts.py`

```sql
CREATE TYPE broker_id_enum   AS ENUM ('ibkr', 'futu', 'schwab');
CREATE TYPE trading_mode_enum AS ENUM ('live', 'paper');

CREATE TABLE broker_accounts (
  id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_id       broker_id_enum    NOT NULL,
  account_number  TEXT              NOT NULL,
  alias           TEXT              NULL,
  mode            trading_mode_enum NOT NULL,
  gateway_label   TEXT              NOT NULL,
  currency_base   TEXT              NOT NULL DEFAULT 'USD',
  display_order   INT               NOT NULL DEFAULT 0,
  first_seen_at   TIMESTAMPTZ       NOT NULL DEFAULT now(),
  last_seen_at    TIMESTAMPTZ       NOT NULL DEFAULT now(),
  deleted_at      TIMESTAMPTZ       NULL,
  created_at      TIMESTAMPTZ       NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ       NOT NULL DEFAULT now(),
  CONSTRAINT broker_accounts_natural_uq UNIQUE (broker_id, account_number)
);

CREATE INDEX ix_broker_accounts_active
  ON broker_accounts (broker_id, mode)
  WHERE deleted_at IS NULL;
```

Soft-delete rule: rows where `last_seen_at < now() - INTERVAL '30 minutes'` AND `deleted_at IS NULL` are stamped with `deleted_at = now()` by the discover loop. Re-appearance clears `deleted_at` and bumps `last_seen_at`.

### 4.5 REST routes — `app/api/accounts.py`

```
GET /api/accounts                          → 200 [Account]
GET /api/accounts/{id}/summary             → 200 Summary | 404
GET /api/accounts/{id}/positions           → 200 [Position] | 404
GET /api/accounts/{id}/orders              → 200 [Order] | 404
PATCH /api/accounts/{id}                   → 200 Account     # alias-only update
```

All gated by `require_admin_jwt` (existing CF-Access dep from Phase 2). Errors:
- `404 Not Found` if account UUID unknown OR soft-deleted.
- `503 Service Unavailable` if the sidecar for this account is unreachable AND we're outside an IBKR reset window.
- `204 No Content` for `summary`/`positions`/`orders` during a confirmed IBKR reset window — frontend renders a "broker maintenance — back at HH:MM" banner instead of an error.

### 4.6 Frontend wiring — `services/{accounts,positions,orders}.ts`

Each service grows:
```ts
const USE_MOCKS = (import.meta.env.VITE_USE_MOCKS as string | undefined) === 'true';

async function listAccounts(): Promise<Account[]> {
  if (USE_MOCKS) return MOCK_ACCOUNTS;
  const r = await fetch('/api/accounts', { credentials: 'include' });
  if (!r.ok) throw new Error(`accounts ${r.status}`);
  return (await r.json()) as Account[];
}
```

Storybook decorators set `VITE_USE_MOCKS=true` in `.storybook/preview.ts`. Vitest unit tests override per-describe via `vi.stubEnv`. Production / dev frontend points at REST.

Zustand stores unchanged — they consume the service's `Account[]`/`Position[]`/`Order[]` shape, agnostic to source.

### 4.7 Watchdog + Tray — port + extend `Dashboard_old/deploy/nuc/`

Port verbatim from `/mnt/c/Dashboard_old/deploy/nuc/` (no rewrite — Windows ops glue is precious):
- `BrokerWatchdog.ps1` (299 lines, including `Test-InResetWindow` for IBKR daily + weekend resets)
- `BrokerTray.ps1` (559 lines)
- `DailyRestart.ps1` (56 lines) + `Launch-DailyRestart.vbs` + `register-daily-restart.ps1`
- `Launch-Watchdog.vbs` + `register-watchdog.ps1`
- `Launch-Tray.vbs`
- `HideBrokerWindows.ps1` + `Launch-Hider.vbs`
- `start-gateways.ps1`, per-service restart scripts, pause/resume scripts
- `encrypt-ib-secrets.ps1`, `harden-post-install.ps1`

Phase-4 additions (new files):
- `Launch-IBKRSidecar.vbs` — wscript wrapper, hides console (per `feedback_ibc_gotchas.md` issue 6)
- `register-ibkr-sidecar.ps1` — registers 4 scheduled tasks (one per label) with the same 0/30/60/90s stagger as gateways, fires at-logon, restarts on failure, runs whether-user-logged-on (S4U)
- `provision-sidecar-mtls.ps1` — generates self-signed CA + 4 server certs + 1 client cert, idempotent, writes to `C:\IBC\secrets\` with restrictive ACLs, prints client cert + key + CA to stdout for manual transfer into `app_secrets`
- `renew-sidecar-mtls.ps1` — annual cert rotation, same shape as provision

Watchdog extension to `BrokerWatchdog.ps1`:
- Add an `Adapt-SidecarHealth` block that runs after `Adapt-IBGatewayHealth`. For each `(label, sidecar-port, sidecar-cert)`, invoke a small companion `Probe-Sidecar.ps1` that opens a gRPC client (PowerShell can use `dotnet add package Grpc.Net.Client` or shell out to a tiny `probe-sidecar.exe`); 2 consecutive BAD outside a reset window → kill sidecar PID → re-fire scheduled task.
- Same `Test-InResetWindow` reset-aware short-circuit: skip sidecar probes during weekend reset (Fri 23:00 ET → Sat 03:00 ET) and during the daily resets (Sun-Fri per-region windows).
- Tray extension to `BrokerTray.ps1`: 4 sidecar dots stacked beside the 4 gateway dots; status pulled from the watchdog state file.

### 4.8 mTLS provisioning

- **Root CA** generated once on the NUC. 10-year. Private key stays at `C:\IBC\secrets\ca.key` with ACL `SYSTEM:F + <runtime user>:R` (no other reads). CA cert at `C:\IBC\secrets\ca.pem` (world-readable).
- **Server certs** (4 of them, one per sidecar) — CN = `sidecar-<label>`, SAN includes `10.10.0.2`. 1-year validity.
- **Client cert** (1, for the FastAPI backend) — CN = `dashboard-backend`. 1-year.
- **Distribution to backend:** `provision-sidecar-mtls.ps1` outputs `client.crt`, `client.key`, `ca.pem` PEMs to stdout. Operator pipes into Phase-2 admin API:
  ```
  POST /api/admin/secrets/broker/mtls.client_cert_pem  (str, encrypted)
  POST /api/admin/secrets/broker/mtls.client_key_pem   (str, encrypted)
  POST /api/admin/secrets/broker/mtls.ca_bundle_pem    (str, encrypted)
  ```
  `ConfigService.reveal_secret(...)` loads them at backend lifespan startup, hands to the gRPC client builder.
- **Renewal:** annual; calendar event. `renew-sidecar-mtls.ps1` rotates client cert in place + restarts sidecars. Server cert rotation requires sidecar restart per gateway, but doesn't disturb the gateway itself.

## 5. Data model

### 5.1 account_id resolution

Frontend always sees `account_id` (UUID, our own). The DB row maps to `(broker_id, gateway_label, account_number)`. Sidecar calls take `account_number` because that's what `ib_async` understands. Backend `AccountService` is the single chokepoint that translates uuid → tuple. Frontend never sees `gateway_label` or `account_number` (avoids leaking a v1-style "ISA / Normal" abstraction into the UI; aliases handle display).

### 5.2 Money + decimal types

- Wire format: `Money { value: string, currency: string }`. `value` is a Python `Decimal` `str()`-formatted. Backend uses `decimal.Decimal` everywhere internally; SQLAlchemy column `NUMERIC(20, 8)` per CLAUDE.md SQL conventions.
- Frontend uses `string` directly in the type, formats via `Intl.NumberFormat` for display. The existing `NumericCell` primitive stays unchanged — it already takes `value: number | null | undefined`, but Phase 4 adds a `valueString` overload that parses safely.

### 5.3 UK-pence + split handling — sidecar-internal

- `_normalize_quote_currency(symbol, exchange, price, currency)` — divides by 100 if `currency == 'GBP'` AND `exchange ∈ {'LSE', 'LSEETF', 'IBIS', ...}` (full list lifted from v1). Applied at every quote emission.
- `_pnl_for_position(account, conid)` — uses `IB.reqPnLSingle()` cache (same pattern as v1's `IBKRAdapter._pnl_subs`); caches the `PnLSingle` object until disconnect. NEVER computes naive `(mp - avg) * qty`.
- `avg_cost` on positions emitted as-is (no division). Per the empirical follow-up in `ibkr_uk_pence_units.md`: this user's IBKR returns avg_cost in pounds even for UK stocks; only quotes need pence-scaling.

## 6. Operational concerns

### 6.1 Maintenance-window awareness

`app/services/ibkr_maintenance.py` (port from v1) — single source of truth for IBKR daily + weekend reset windows in ET local time:

```python
def in_weekend_reset(now: datetime) -> bool:
    et = now.astimezone(ZoneInfo("America/New_York"))
    if et.weekday() == 4 and et.hour >= 23: return True   # Fri >= 23:00
    if et.weekday() == 5 and et.hour < 3:   return True   # Sat < 03:00
    return False

def in_daily_reset(now: datetime) -> bool:
    # Sun-Fri only; per-region windows (NA, EU, APAC).
    # See ibkr_maintenance_schedule.md memory.
    ...
```

Used in two places:
- **Backend REST routes:** convert `503 Service Unavailable` into `204 No Content` + `X-Maintenance-Window: weekend|daily` header during a reset window. Frontend renders maintenance banner.
- **Watchdog (`BrokerWatchdog.ps1` already implements `Test-InResetWindow`):** skip sidecar probes during weekend reset; tolerate daily reset BAD reads without kill+restart.

### 6.2 Reconnect / backoff

- Sidecar → Gateway: `ib_async`'s built-in reconnect handles the common case. On hard error (e.g., `clientId in use`), sidecar exits 64 → scheduled task restart.
- Backend → Sidecar: gRPC client uses exponential backoff. 1s → 2s → 4s → 8s → 16s → 30s cap. Health probe every 5s when in degraded state, 60s when healthy.
- Frontend → Backend: existing `services/api.ts` pattern, no change.

### 6.3 Multi-account discovery cadence

`BrokerRegistry.discover_loop` runs every 60s. Cheap operation (`reqManagedAccts` is a single TWS API call). Soft-delete after 30 min absence — gives 30 attempts before declaring an account gone. Re-appearance clears `deleted_at`.

### 6.4 Logging + metrics

- **Sidecar log dir:** `%ProgramData%\dashboard\sidecar-<label>\` (NOT `C:\IBC\Logs\` to keep IBC's logs clean).
- **Format:** structlog JSON; key fields: `ts`, `label`, `event`, `gateway_port`, `grpc_port`, `request_id`, `latency_ms`. Redaction processor drops anything matching `password|token|secret|cert|key`.
- **Backend metrics (Prometheus, exposed on existing `/metrics`):**
  - `adapter_sidecar_health{label, result}` — `result ∈ ok|unreachable|degraded|gateway_down`
  - `adapter_rpc_latency_ms{label, method}` — histogram
  - `broker_accounts_count{broker_id, mode, deleted}` — gauge
  - `account_discover_loop_runs_total{result}` — counter
- **Tray:** 4 gateway dots + 4 sidecar dots, color-coded green / amber (degraded) / red (BAD).

## 7. Security boundaries

- WG-only: sidecars bind `10.10.0.2:18001-18004`, refuse anything off the WireGuard interface (verify with `netstat -an` post-deploy).
- mTLS-required: gRPC server config rejects connections without a valid client cert signed by the local CA.
- No secret state in sidecars: gateway socket is already authenticated; sidecar holds only its own server cert + the CA bundle (read-only). DPAPI-encrypted IBKR creds at `C:\IBC\secrets\<label>.{login,password,totp}.enc` are NOT touched by sidecars — only by `Launch-Gateway.ps1`.
- Backend `app_secrets` holds the client cert + key (Fernet-encrypted via Phase 2). Loaded once at lifespan startup; never logged.
- Failed mTLS handshakes log `cert_verify_fail` + the SHA256 of the offered cert (NOT the cert itself); after 5 consecutive failures from the same peer, sidecar drops to a 30s sleep before accepting again (basic anti-flood).

## 8. Testing strategy

### 8.1 Unit tests

- Sidecar (`tests/sidecar/`): `pytest` + `pytest-asyncio` + a handwritten `FakeIB` (mocks `ib_async.IB`) + `pytest-grpc` for in-process gRPC server. Coverage target 80% on `sidecar/`.
- Backend (`tests/api/test_accounts.py`, `tests/services/test_brokers.py`): mock `BrokerSidecarClient`; test discover loop, soft-delete, route auth, maintenance-window short-circuits.
- Frontend (`frontend/src/services/{accounts,positions,orders}.test.ts`): existing tests stay; add 1 each that flips `VITE_USE_MOCKS=false` and verifies real fetch path with `MSW` interception.

### 8.2 Contract tests

A real sidecar instance pointed at IBKR's paper Gateway (port 4002 on the NUC). One test per RPC, asserts shape + decoder edge cases (empty positions, multi-account, UK-pence ticker). Runs in CI only when `CI_USE_REAL_IBKR=1` is set, gated behind a manual workflow dispatch (avoid hammering paper Gateway on every PR).

### 8.3 Integration / smoke

Extend `tests/e2e/smoke.spec.ts` with:
- `GET /api/accounts` returns ≥1 row matching `(broker_id="ibkr", deleted_at=null)`
- `GET /api/accounts/{id}/positions` returns proto-shaped JSON
- `GET /api/accounts/{id}/summary` returns Money objects with `currency` set

These run against prod via CF service token, same as existing Phase 2 admin smoke.

### 8.4 Coverage gate

- Backend: `cd backend && uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=80`
- Sidecar: `cd sidecar && uv run pytest --cov=sidecar --cov-fail-under=80`
- CI fails if either is below 80%.

## 9. File changes

### 9.1 New (created)

```
proto/broker/v1.proto                                  ~250 lines
proto/buf.yaml                                         ~15
proto/buf.gen.yaml                                     ~20
backend/app/brokers/base.py                            ~100   (Pydantic mirror of proto types)
backend/app/services/brokers.py                        ~250
backend/app/services/ibkr_maintenance.py               ~80    (ported from v1)
backend/app/api/accounts.py                            ~150
backend/migrations/versions/0002_broker_accounts.py    ~80
backend/tests/services/test_brokers.py                 ~200
backend/tests/api/test_accounts.py                     ~200
backend/tests/services/test_ibkr_maintenance.py        ~80
sidecar/ibkr_sidecar.py                                ~400
sidecar/handlers.py                                    ~300
sidecar/normalize.py                                   ~120   (UK-pence + decimals)
sidecar/pnl_cache.py                                   ~80    (reqPnLSingle subs)
sidecar/tests/test_handlers.py                         ~250
sidecar/tests/test_normalize.py                        ~80
sidecar/pyproject.toml                                 ~40
sidecar/scripts/build-windows.ps1                      ~30    (PyInstaller wrapper)
deploy/nuc/Launch-IBKRSidecar.vbs                      ~10
deploy/nuc/register-ibkr-sidecar.ps1                   ~80
deploy/nuc/provision-sidecar-mtls.ps1                  ~150
deploy/nuc/renew-sidecar-mtls.ps1                      ~80
deploy/nuc/Probe-Sidecar.ps1                           ~80    (gRPC health probe for watchdog)
frontend/src/services/{accounts,positions,orders}.ts   ~+50 each (USE_MOCKS branch)
```

### 9.2 Ported from `/mnt/c/Dashboard_old/deploy/nuc/` (verbatim or near-verbatim)

```
BrokerWatchdog.ps1         (299 lines, with sidecar-probe block added)
BrokerTray.ps1             (559 lines, with sidecar dots added)
DailyRestart.ps1           (56)
Launch-DailyRestart.vbs
Launch-Watchdog.vbs
Launch-Tray.vbs
Launch-Hider.vbs
HideBrokerWindows.ps1
register-daily-restart.ps1
register-watchdog.ps1
register-autostart.ps1
verify-autostart.ps1
restart-{ib,futu,tray}.ps1
pause-brokers.ps1, pause-paper-brokers.ps1, resume-*.ps1
encrypt-ib-secrets.ps1
harden-post-install.ps1
start-gateways.ps1
```

### 9.3 Modified

```
backend/pyproject.toml                                 + grpcio, grpcio-tools, ib_async
backend/app/main.py                                    lifespan: BrokerRegistry start/stop + discover_loop
backend/app/core/deps.py                               + get_account_service, get_broker_registry
backend/app/api/__init__.py                            + accounts router
docker-compose.prod.yml                                (no change — sidecars run on NUC, not VPS)
docker-compose.yml                                     (no change for Phase 4)
.env.example                                           + BROKER_SIDECAR_HOSTS (host:port list); mTLS material lives in app_secrets, not .env
frontend/package.json                                  + @bufbuild/protoc-gen-es (devDep), proto:gen script
frontend/.gitignore                                    + src/proto-gen/
frontend/.storybook/preview.ts                         set VITE_USE_MOCKS=true
.github/workflows/ci.yml                               + buf lint + buf generate + sidecar tests + sidecar coverage gate
.github/workflows/deploy.yml                           + accounts smoke
TASKS.md, CHANGELOG.md, CLAUDE.md                      Phase 4 close-out (Phase 4 plan adds these in close-out task)
```

## 10. Exit criteria (definition of done)

- All 4 IBKR sidecars register as scheduled tasks on the NUC, start at-logon, survive logoff, log to per-label dirs.
- `GET /api/accounts` returns the 4 IBKR accounts (the user's actual ISA-live, ISA-paper, normal-live, normal-paper) — verified manually.
- `GET /api/accounts/{id}/{summary,positions,orders}` round-trips for all 4 accounts in prod via CF service token.
- Frontend with `VITE_USE_MOCKS=false` renders the real account picker, real positions table on `/positions`, real orders on `/orders`.
- Watchdog kills + restarts a stuck sidecar within 10 min outside a reset window; does NOT touch sidecars during the Fri 23:00 ET → Sat 03:00 ET weekend reset.
- 80%+ test coverage on backend `app/brokers/`, `app/services/brokers.py`, `app/services/ibkr_maintenance.py`, sidecar `sidecar/`.
- Playwright smoke green: 11 prior tests + 3 new (`/api/accounts/*`).
- mTLS proven: random tampered client cert is rejected by sidecar; backend with valid client cert passes.
- `CHANGELOG.md`, `TASKS.md`, `CLAUDE.md` updated; `v0.4.0` tagged after `gh run watch` greens.

## 11. Risks + open questions

| Risk | Mitigation |
|---|---|
| `clientId` collision across simultaneously-restarting sidecars | Derive `clientId` deterministically from `hash(label) % 1000`; sidecar logs WARN + exits 64 if rejected, scheduled task restart with exponential backoff |
| Single sidecar zombie wedges Gateway socket | Watchdog probes Gateway socket directly (existing v1 logic); if Gateway is fine but sidecar is stuck, kill sidecar; if Gateway is stuck, restart Gateway via existing v1 chain |
| mTLS cert rotation breaks prod between cert install + sidecar restart | `provision-sidecar-mtls.ps1` is idempotent + atomic; `renew-sidecar-mtls.ps1` rolls one sidecar at a time |
| Frontend hits real REST in Storybook by accident | `VITE_USE_MOCKS` defaults to `true` in `.storybook/preview.ts` global decorator |
| ib_async version drift across sidecars on concurrent NUC update | Sidecars are PyInstaller-frozen — version is baked at build time. Build SHA in `HealthResponse.sidecar_version` makes drift visible in `/api/accounts` health endpoint |
| IBKR returns multi-account positions across `reqPositionsAsync` even when `account` filter is set | Per `ibkr_uk_pence_units.md` v1 fix — sidecar uses `reqPositionsAsync(account=...)` per IBKR docs but client-side-filters by account prefix; emits a WARN if the filter trimmed any rows |
| PyInstaller build broken on Windows | First Phase 4 task validates the build before any sidecar code lands. If broken, fall back to "uv on the NUC" install |

**Open questions:**

1. Do we want backend → sidecar to fail-open (treat unreachable sidecar as "no accounts") or fail-closed (`503` on every related route until sidecar back)? My default: **fail-open with a banner**, so a single dead sidecar doesn't blank the dashboard for accounts on the other 3.
2. Account picker order: alphabetical by alias, or grouped by mode (live first, paper second), or user-set `display_order`? My default: **mode-grouped, alphabetical-within-group**, with `display_order` available but unused in v0.4.0 (no UI to set it).
3. Account aliases: editable from `/admin/config` (Phase 2 surface) or a dedicated `/admin/accounts` route in Phase 4? My default: **dedicated route** — consistent with the per-broker UX expected in Phase 5+.

These are tagged in the plan for explicit decision before implementation begins.

## 12. Architect review — applied

(Pending — `ARCHITECT-REVIEW` skill runs next per CLAUDE.md phase workflow step 3. Findings + applied changes will be recorded here before user spec approval.)
