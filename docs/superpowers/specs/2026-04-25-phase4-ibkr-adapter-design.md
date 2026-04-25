# Phase 4 — IBKR adapter (read-only) + broker_accounts + gRPC sidecars

**Status:** design (2026-04-25). Architect review applied — see §12. Pending user spec approval.
**Targets:** v0.4.0.
**Predecessor:** v0.3.0 (Phase 3 frontend shell, mocks).
**Successor:** Phase 4.5 (tick streams + historical bars), then Phase 5 (trade execution).

## 0. Prerequisites (must verify before any Phase 4 task lands)

The whole topology assumes Windows-native sidecars binding the WireGuard IP `10.10.0.2`. That assumption is unproven; the only existing precedent (`10.10.0.2:5432` for Postgres) is also Windows-native, but the WG client + interface placement on the NUC has never been documented in this repo. **Phase 4 task 1 runs `deploy/nuc/verify-wg-windows.ps1` and halts the rest of the phase if any check fails.** Checks:

1. WireGuard for Windows is installed (`Get-Service WireGuardTunnel$wg0` returns Running, or equivalent service name).
2. `Get-NetIPAddress -IPAddress 10.10.0.2` returns a Windows interface (NOT just the WSL `wg0`).
3. Windows Firewall has an inbound rule for TCP 18001-18004 scoped to `10.10.0.0/24` (script creates the rule if missing, idempotent).
4. A test bind succeeds: `Test-NetConnection -ComputerName 10.10.0.2 -Port 18001 -InformationLevel Quiet` from VPS over WG, while a temporary listener (`netcat` or `python -m http.server`) sits on Windows.

If WireGuard is on the WSL side instead of Windows-native, sidecars must be redesigned to run inside WSL (out of scope for Phase 4 — the spec assumes Windows). Halt and re-brainstorm.

## 1. Goal

Replace Phase 3's mocked `accounts`/`positions`/`orders` services with **real, read-only** data from the 4 IBKR Gateways already running on the NUC. Lay the foundation (`BrokerAdapter` contract, sidecar topology, `broker_accounts` table, mTLS plumbing) that Phases 5–8 will reuse for trade execution and Futu/Schwab adapters.

## 2. Scope

**In:**

- gRPC contract (`proto/broker/v1.proto`) covering: health, list managed accounts, account summary, positions, orders (open + today's filled), contract lookup.
- One Python sidecar process per IBKR gateway (4 instances), launched via Windows Task Scheduler on the NUC. Each sidecar wraps a single `ib_async.IB()` and serves the gRPC contract.
- Self-signed mTLS between FastAPI backend (VPS) and the 4 sidecars (NUC), over WireGuard. CRL-based revocation (file-based, sidecars reload every 60s).
- Alembic migration `0002_broker_accounts.py` + the `broker_accounts` table.
- Backend service layer (`app/services/brokers.py`) + REST routes (`app/api/accounts.py`).
- Frontend service flip from fixtures to REST behind `VITE_USE_MOCKS` flag (default `false` in prod, `true` in Storybook).
- Watchdog + tray extensions on the NUC: ports + adapts the existing `BrokerWatchdog.ps1` / `BrokerTray.ps1` from `/mnt/c/Dashboard_old/deploy/nuc/`, adds sidecar-health probes via a packaged `probe-sidecar.exe`, preserves IBKR maintenance-window awareness.
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
            │ gRPC + mTLS (TLS 1.3, file-CRL) over WireGuard (10.10.0.2:18001-18004)
            ▼
  NUC  : 4 Windows-Task-Scheduler-launched Python sidecars (PyInstaller --onedir)
            │  ibkr-sidecar-isa-live      (port 18001)  scheduled-task offset +30s after gateway
            │  ibkr-sidecar-isa-paper     (port 18002)  +60s
            │  ibkr-sidecar-normal-live   (port 18003)  +90s
            │  ibkr-sidecar-normal-paper  (port 18004)  +120s
            │
            │ ib_async on 127.0.0.1:4001-4004 (gateway API sockets, already authenticated)
            ▼
  4× IB Gateway java processes (already running, no change in Phase 4)
```

Sidecars are **pure protocol translators**: gRPC request → `ib_async` call → gRPC response. They hold no broker credentials of their own — IBC has already authenticated the Gateway socket on the same machine.

Backend talks to *all 4* sidecars; aggregation (e.g., "positions across all live accounts") happens in the FastAPI service layer, not in any sidecar. Partial fleet failure is **fail-open**: a single dead sidecar marks its accounts as `degraded` in the response envelope; the other three continue serving.

### 3.2 Data flow — `GET /api/accounts/{id}/positions`

```
1. Frontend       : GET /api/accounts/<uuid>/positions
2. FastAPI dep    : require_admin_jwt() → CF Access identity
3. AccountService : look up broker_accounts by uuid
                    → resolve (broker_id, gateway_label, account_number)
4. BrokerRegistry : pick sidecar for gateway_label
5. gRPC client    : Broker.GetPositions(AccountRef{account_number})
6. Sidecar        : ib_async ib.reqPositionsAsync() (returns ALL managed accounts)
                    → client-side-filter by account_number (per ibkr_uk_pence_units.md v1 fix)
                    → ib.reqPnLSingle(account, '', conid) for unrealized PnL (per stock_splits.md)
                    → _normalize_quote_currency() for UK pence (only quotes, not avg_cost)
7. Response       : Position[] with normalized money
8. FastAPI        : map proto → API JSON via AccountResponse model (strips gateway_label, account_number), return
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

// `gateway_label` and `account_number` ARE present in the proto (sidecar→backend
// hop needs them) but the backend strips both before serializing to REST. See
// §4.5 AccountResponse.
message Account {
  string       account_number = 1;
  TradingMode  mode           = 2;
  string       gateway_label  = 3;
  string       currency_base  = 4;   // 'USD', 'GBP'… sourced from accountSummary BASE CURRENCY tag, NOT defaulted
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
- UK-listed-stock pence-denominated quotes are normalized to pounds **inside the sidecar** before emitting (per `ibkr_uk_pence_units.md` empirical follow-up: only quotes need scaling). `avg_cost` unit handling is per-account-configurable — see §5.3.
- Account proto carries `gateway_label` + `account_number` for sidecar→backend resolution. Backend's `AccountResponse` Pydantic model strips both — frontend never sees them.
- Codegen targets: Python (`grpcio-tools`) for backend + sidecar; TS (`@bufbuild/protoc-gen-es`) generated to `frontend/src/proto-gen/` for Phase 4.5+ (gitignored, regenerated by `pnpm proto:gen`). `proto/buf.lock` is committed for reproducible codegen.

### 4.2 Sidecar entrypoint — `sidecar/ibkr_sidecar.py`

```
inputs (CLI flags or env):
  --label             (e.g., "isa-live")
  --gateway-port      (4001..4004)
  --grpc-port         (18001..18004)
  --tls-cert-pem      (path to sidecar's server cert)
  --tls-key-pem       (path to sidecar's server private key)
  --tls-ca-bundle-pem (path to client-cert CA bundle)
  --tls-crl-pem       (path to CRL file, reloaded every 60s)
  --log-dir           (defaults to %ProgramData%\dashboard\sidecar-<label>\)
  --state-dir         (defaults to %ProgramData%\dashboard\sidecar-<label>\state\)
```

**clientId derivation (H5):**

```
client_id = (fnv1a32(hostname || "|" || label) % 900) + 100
```

- 100..999 reserved for sidecar use; leaves 0-99 for ad-hoc human use, 1000+ free for future tooling.
- Hostname binding prevents collision when a developer accidentally runs `ib_async` against the same paper gateway from a laptop.
- Formula documented as a one-liner in `sidecar/ibkr_sidecar.py` so it isn't accidentally changed.

**Self-throttled startup backoff (H6):**

Windows Task Scheduler does NOT support exponential backoff natively (it has fixed retry intervals). The sidecar implements its own:

1. On startup, read `<state-dir>/last_fail.txt` (epoch + delay).
2. If `now - last_fail < min(prev_delay * 2, 60)`, `Sleep(remaining)` then continue.
3. On any non-clean exit, write `<state-dir>/last_fail.txt = (now, new_delay)`.
4. On clean shutdown (`SIGTERM`/`SIGINT`), delete the file.

Effect: tight crash loops self-throttle to ≤1 attempt every 60s without modifying Task Scheduler. Task Scheduler keeps relaunching the binary at its fixed cadence; the binary spends time in `Sleep` if recent failures piled up.

**Lifecycle:**

1. Apply self-throttled backoff (above) before any work.
2. Load cert material + CRL; refuse to start if any path missing or CRL signature invalid.
3. Build async gRPC server bound to `10.10.0.2:<grpc-port>`, mTLS-required, TLS 1.3 minimum, CRL-aware.
4. Connect `ib_async.IB()` to `127.0.0.1:<gateway-port>` with the FNV-derived clientId. On `clientId already in use` → fatal, exit 64 — Task Scheduler relaunches; self-throttled backoff prevents a tight loop.
5. **Active subscriptions** (H7 — these are `req*` calls, not "passive listeners"):
   - `ib.reqManagedAccountsAsync()` once at startup → cache list.
   - For each managed account: `ib.reqAccountSummaryAsync(group="All", tags="NetLiquidation,TotalCashValue,RealizedPnL,UnrealizedPnL,BuyingPower,BASE")` → kept alive.
   - `reqPnLSingle(account, '', conid)` is on-demand per first `GetPositions(account)` call; cached in `pnl_cache.py` until disconnect.
6. Serve until `SIGTERM`/`SIGINT`. CRL reload task ticks every 60s and rebuilds the SSL context with the fresh CRL.
7. On shutdown: `cancelPnLSingle` for every cached subscription, `cancelAccountSummary`, `IB.disconnect()`, drain gRPC server, write clean-shutdown marker so backoff state resets.

**Failure modes:**
- Gateway socket dies → `ib_async.disconnectedEvent` fires → sidecar logs WARN, gRPC `Health` returns `gateway_connected=false`. Sidecar **does not** auto-restart the Gateway (that's `BrokerWatchdog.ps1`'s job). If `IB.isConnected()` stays false for >30s, sidecar exits 64; Task Scheduler relaunches. (M21 — single source of truth for reconnect: `ib_async`'s internal handlers; sidecar's only escalation is exit-and-relaunch.)
- IBKR daily reset window → sidecar emits `gateway_connected=false`, backend short-circuits (see §4.5), watchdog skips kill+restart.
- Lifespan crash → sidecar exits non-zero; backoff state file ensures the next launch waits at least `prev_delay * 2` seconds.

**Packaging (M17):** `pyinstaller --onedir --noconfirm sidecar/ibkr_sidecar.py` → `dist/ibkr-sidecar/`. Slow `--onefile` extraction at every launch is replaced by direct exe execution. Distribution is a ZIP from `scripts/build-windows.ps1`. `register-ibkr-sidecar.ps1` points the scheduled task at `dist/ibkr-sidecar/ibkr-sidecar.exe`.

### 4.3 Backend service layer — `app/services/brokers.py`

```python
class BrokerSidecarClient:
    """One per (broker_id, gateway_label). Owns a gRPC AsyncStub on a long-lived channel."""
    async def health(self) -> HealthResponse: ...
    async def list_managed_accounts(self) -> list[Account]: ...
    async def get_account_summary(self, account_number: str) -> Summary: ...
    async def get_positions(self, account_number: str) -> list[Position]: ...
    async def get_orders(self, account_number: str) -> list[Order]: ...
    async def get_contract(self, conid: str) -> Contract: ...

class BrokerRegistry:
    """4 clients, lifespan-managed. Channel reconnect is gRPC's job; we only track health state."""
    async def get_client(self, label: str) -> BrokerSidecarClient: ...
    async def healthy_clients(self) -> list[BrokerSidecarClient]:
        """Return clients whose latest Health probe was ok within the last 90s."""
    async def discover_loop(self) -> None:
        """Every 60s: ListManagedAccounts on each healthy client → upsert broker_accounts.
           Per-iteration try/except so one failure cannot kill the loop."""

class AccountService:
    """Thin DB + registry orchestration."""
    async def list_accounts(self) -> tuple[list[AccountRow], list[str]]:
        """Returns (accounts, degraded_labels). `degraded_labels` is non-empty when a
           sidecar is unhealthy → backend serves cached accounts but flags them."""
    async def get_summary(self, account_id: UUID) -> Summary: ...
    async def get_positions(self, account_id: UUID) -> list[Position]: ...
    async def get_orders(self, account_id: UUID) -> list[Order]: ...
```

**Discover loop pseudocode (H13 — never let one bad iteration kill the loop):**

```python
async def discover_loop(self) -> None:
    while not self._stop.is_set():
        try:
            await self._discover_once()
            metrics.discover_runs_total.labels(result="ok").inc()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("discover_loop_iter_failed")
            metrics.discover_runs_total.labels(result="err").inc()
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass

async def _discover_once(self) -> None:
    healthy = await self.healthy_clients()
    healthy_labels = {c.label for c in healthy}
    rows_seen: dict[tuple[str, str], Account] = {}  # (broker_id, account_number) -> proto
    for client in healthy:
        try:
            for acc in await client.list_managed_accounts():
                rows_seen[(acc.broker_id_str, acc.account_number)] = acc
        except Exception:
            log.exception("list_managed_accounts_failed", label=client.label)
            healthy_labels.discard(client.label)  # treat as unhealthy this tick
    async with self.db.begin() as tx:
        await self._upsert_present(tx, rows_seen)
        await self._soft_delete_missing(tx, rows_seen, healthy_labels)
```

`BrokerRegistry.discover_loop` is started by FastAPI lifespan; cancelled at shutdown. Channel state is owned by gRPC's built-in retry policy (`grpc.keepalive_*` + service config); the registry only observes health via the `Health` RPC and exposes `healthy_clients()`. Failure to reach a sidecar logs WARN + bumps `adapter_sidecar_health{label,result="unreachable"}` Prometheus metric, does NOT raise.

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
  currency_base   TEXT              NOT NULL,
  -- display_order kept for forward-compat (Phase 5+ admin UI). Default 0; unused in v0.4.0.
  display_order   INT               NOT NULL DEFAULT 0,
  first_seen_at   TIMESTAMPTZ       NOT NULL DEFAULT now(),
  last_seen_at    TIMESTAMPTZ       NOT NULL DEFAULT now(),
  -- last_seen_via tracks WHICH sidecar saw this row last. Soft-delete is scoped
  -- to "sidecar healthy + this row missing from its response" so a backend
  -- outage cannot mass-delete accounts.
  last_seen_via   TEXT              NOT NULL,
  deleted_at      TIMESTAMPTZ       NULL,
  created_at      TIMESTAMPTZ       NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ       NOT NULL DEFAULT now(),
  CONSTRAINT broker_accounts_natural_uq UNIQUE (broker_id, account_number)
);

CREATE INDEX ix_broker_accounts_active
  ON broker_accounts (broker_id, mode)
  WHERE deleted_at IS NULL;
```

**Soft-delete rule (C1 — race-free):**

Soft-delete fires inside `_discover_once` after upserts, in the same transaction:

```sql
UPDATE broker_accounts
   SET deleted_at = now(),
       updated_at = now()
 WHERE deleted_at IS NULL
   AND last_seen_via = ANY(:healthy_labels)         -- only labels we actually queried this tick
   AND (broker_id, account_number) NOT IN :rows_seen_keys
   AND last_seen_at < now() - INTERVAL '30 minutes';  -- 30 missed loops cushion
```

The `last_seen_via = ANY(:healthy_labels)` clause means: a sidecar must have been healthy **and** have failed to mention this account this tick. If all sidecars are unhealthy (network partition, mass crash, backend GC pause), `healthy_labels` is empty and zero rows match — accounts persist. Re-appearance: upsert clears `deleted_at`, bumps `last_seen_at`, and updates `last_seen_via`.

Currency base (M16): sourced from `accountSummary` `BASE` tag in `ListManagedAccounts` response. No DEFAULT — schema change makes it `NOT NULL` and the discover loop populates explicitly. UK ISA accounts → `'GBP'`, US accounts → `'USD'`, etc.

Account number format (L30): not enforced at DB level (forward-compat for non-IBKR), but Pydantic `AccountResponse` validates the typical IBKR pattern `^[UDFu][0-9]+$` as a soft warning.

### 4.5 REST routes — `app/api/accounts.py`

```
GET   /api/accounts                          → 200 AccountListResponse
GET   /api/accounts/{id}/summary             → 200 SummaryResponse | 404 | 503 + Retry-After
GET   /api/accounts/{id}/positions           → 200 PositionsResponse | 404 | 503 + Retry-After
GET   /api/accounts/{id}/orders              → 200 OrdersResponse | 404 | 503 + Retry-After
PATCH /api/accounts/{id}                     → 200 AccountResponse  (alias-only update)
```

All gated by `require_admin_jwt` (existing CF-Access dep from Phase 2).

**Pydantic response models (M15, M22, M30):**

```python
class AccountResponse(BaseModel):
    id: UUID
    broker_id: Literal["ibkr", "futu", "schwab"]
    alias: str | None
    mode: Literal["live", "paper"]
    currency_base: str = Field(min_length=3, max_length=3)
    display_order: int
    # NB: gateway_label and account_number are NOT exposed to the frontend.

class AccountListResponse(BaseModel):
    accounts: list[AccountResponse]
    degraded_sidecars: list[str]  # M24 — non-empty when 1+ sidecar unhealthy

class AccountAliasUpdate(BaseModel):
    alias: str = Field(min_length=1, max_length=64, pattern=r"^[\w\s\-.&]+$")
```

**Error contract (C3 — no 204; clients always get parseable JSON or a 5xx):**

| Condition | Status | Body | Headers |
|---|---|---|---|
| Account UUID unknown OR soft-deleted | 404 | `{"error":"not_found","detail":"account <id>"}` | — |
| Sidecar unreachable AND outside reset window | 503 | `{"error":"sidecar_unreachable","label":"<gateway_label>"}` | `Retry-After: 30` |
| Inside IBKR maintenance window | 503 | `{"error":"broker_maintenance","window":"weekend\|daily","until":"<iso8601>"}` | `Retry-After: <seconds_until_window_end>` |
| Healthy → data | 200 | proto-mapped body | — |

Frontend's existing error handler renders the maintenance banner from the `error` + `until` body fields; no special 204 handling path needed.

**Partial fleet UX (M24):** `GET /api/accounts` always returns `200` with `accounts` listing every non-deleted row. Per-row sidecar reachability is not surfaced on list responses (would leak `gateway_label`); instead `degraded_sidecars: ["isa-live", ...]` flags the affected labels. Frontend's `ConnectedDropdown` reads this and displays a degraded pill.

### 4.6 Frontend wiring — `services/{accounts,positions,orders}.ts`

Each service grows:
```ts
const USE_MOCKS = (import.meta.env.VITE_USE_MOCKS as string | undefined) === 'true';

async function listAccounts(): Promise<AccountListResponse> {
  if (USE_MOCKS) return MOCK_ACCOUNT_LIST;
  const r = await fetch('/api/accounts', { credentials: 'include' });
  if (!r.ok) {
    const body = await r.json().catch(() => ({ error: 'unknown' }));
    if (r.status === 503 && body.error === 'broker_maintenance') {
      throw new MaintenanceError(body.window, body.until);
    }
    throw new Error(`accounts ${r.status}: ${body.error}`);
  }
  return (await r.json()) as AccountListResponse;
}
```

Storybook decorators set `VITE_USE_MOCKS=true` in `.storybook/preview.ts`. Vitest unit tests override per-describe via `vi.stubEnv`. Production / dev frontend points at REST.

Zustand stores unchanged in shape — they consume the service's `Account[]`/`Position[]`/`Order[]` shape, agnostic to source. A new `useFleetHealth()` selector reads the latest `degraded_sidecars` from the accounts store and feeds the topbar `ConnectedDropdown`.

### 4.7 Watchdog + Tray — port + extend `Dashboard_old/deploy/nuc/`

Port verbatim from `/mnt/c/Dashboard_old/deploy/nuc/` (no rewrite — Windows ops glue is precious):
- `BrokerWatchdog.ps1` (299 lines, including `Test-InResetWindow` for IBKR daily + weekend resets)
- `BrokerTray.ps1` (559 lines — first Phase 4 task: read end-to-end and decide whether 8 status dots fit the existing layout or whether the panel needs ~200 lines of additional layout work; budget allocated in §9.1.) (M19)
- `DailyRestart.ps1` (56 lines) + `Launch-DailyRestart.vbs` + `register-daily-restart.ps1`
- `Launch-Watchdog.vbs` + `register-watchdog.ps1`
- `Launch-Tray.vbs`
- `HideBrokerWindows.ps1` + `Launch-Hider.vbs`
- `start-gateways.ps1`, per-service restart scripts, pause/resume scripts
- `encrypt-ib-secrets.ps1`, `harden-post-install.ps1`

Phase-4 additions (new files):
- `Launch-IBKRSidecar.vbs` — wscript wrapper, hides console (per `feedback_ibc_gotchas.md` issue 6)
- `register-ibkr-sidecar.ps1` — registers 4 scheduled tasks (one per label) **+30s after the matching gateway** (M25 — gateway at +0/30/60/90s, sidecar at +30/60/90/120s) so the gateway has time to bind 4001-4004 before `IB.connect()` fires. Fires at-logon, restarts on failure, runs whether-user-logged-on (S4U). Self-throttled backoff inside the sidecar absorbs gateway-not-yet-up cases.
- `verify-wg-windows.ps1` — pre-flight check for §0
- `provision-sidecar-mtls.ps1` — generates self-signed CA + 4 server certs + 1 client cert + empty CRL, idempotent, writes to `C:\dashboard\secrets\` (NOT `C:\IBC\secrets\` — directory boundary, L26) with restrictive ACLs
- `provision-and-publish.ps1` (H10) — wraps `provision-sidecar-mtls.ps1` and POSTs the client cert + key + CA bundle directly to `/api/admin/secrets/broker/mtls.*` via the CF Access service token. End-to-end automated cert distribution; no manual operator pipe.
- `renew-sidecar-mtls.ps1` — annual cert rotation; rolls one sidecar at a time
- `revoke-cert.ps1` (C2) — appends a cert serial to `C:\dashboard\secrets\crl.pem` and bumps mtime; sidecars reload at next 60s tick.
- `probe-sidecar.exe` (M18) — built from `sidecar/probe.py` via PyInstaller alongside the main sidecar binary; takes `--label --port --client-cert --client-key --ca` and exits 0/1 based on `Health` RPC. PowerShell shells out to it instead of pulling in `Grpc.Net.Client` and the .NET runtime dependency.
- `Probe-Sidecar.ps1` — thin PS wrapper around `probe-sidecar.exe` that records state to `C:\dashboard\state\sidecar-<label>.health`

**Watchdog extension to `BrokerWatchdog.ps1`:**
- After existing gateway probe block, new `Adapt-SidecarHealth` block invokes `Probe-Sidecar.ps1` per label; 2 consecutive BAD outside a reset window → `Stop-Process` the sidecar PID → re-fire scheduled task. Sidecar's self-throttled backoff prevents tight relaunch loops.
- Same `Test-InResetWindow` reset-aware short-circuit: skip sidecar probes during weekend reset (Fri 23:00 ET → Sat 03:00 ET); tolerate daily reset BAD reads without kill+restart (matches the existing v1 gateway-probe behavior).
- Tray extension to `BrokerTray.ps1`: 4 sidecar dots paired with the 4 gateway dots; status read from `C:\dashboard\state\sidecar-<label>.health`. (Or full panel rewrite if the M19 first-task review finds the v1 layout can't accommodate 8 dots cleanly — budget in §9.1.)

### 4.8 mTLS provisioning + revocation

- **Root CA** generated once on the NUC. 10-year. Private key stays at `C:\dashboard\secrets\ca.key` with ACL `SYSTEM:F + <runtime user>:R` (no other reads). CA cert at `C:\dashboard\secrets\ca.pem` (world-readable).
- **Server certs** (4 of them, one per sidecar) — CN = `sidecar-<label>`, SAN includes `10.10.0.2`. 1-year validity.
- **Client cert** (1, for the FastAPI backend) — CN = `dashboard-backend`. 1-year.
- **CRL (C2):** `C:\dashboard\secrets\crl.pem` is a CA-signed CRL that sidecars **reload every 60s** and rebuild their SSL context with. Initial CRL is empty. Any compromised cert is added to the CRL via `revoke-cert.ps1 -Serial <hex>` — within 60s, sidecars reject the revoked cert. This gives the design a working revocation path without external infrastructure (no public OCSP responder needed).
- **Distribution to backend (H10):** `provision-and-publish.ps1` runs `provision-sidecar-mtls.ps1`, then `Invoke-RestMethod` POSTs `client.crt`, `client.key`, `ca.pem` directly to `/api/admin/secrets/broker/mtls.{client_cert,client_key,ca_bundle}_pem` using a CF Access service token. End-to-end automated; no manual operator pipe. The CRL reload on backend side: `BrokerRegistry` re-reads `mtls.ca_bundle_pem` (which may include CRL extension) on every channel reconnect; full reload requires backend restart only on root-CA rotation.
- **Renewal cadence:** annual. `renew-sidecar-mtls.ps1` rolls server certs one sidecar at a time (gateway socket undisturbed) + bumps client cert in `app_secrets`. Calendar event tagged.
- **Revocation runbook:** `deploy/nuc/RUNBOOK-mtls-recovery.md` walks through "NUC compromised → rotate root CA + republish CA bundle to backend `app_secrets` → restart all 4 sidecars + backend" (~5 min full-stack downtime budgeted; tabletop rehearsal at provision time).

## 5. Data model

### 5.1 account_id resolution

Frontend always sees `account_id` (UUID, our own). The DB row maps to `(broker_id, gateway_label, account_number)`. Sidecar calls take `account_number` because that's what `ib_async` understands. Backend `AccountService` is the single chokepoint that translates uuid → tuple. **`gateway_label` and `account_number` are present in the proto but stripped at the REST boundary by the `AccountResponse` Pydantic model — frontend never sees them.** This avoids leaking a v1-style "ISA / Normal" abstraction into the UI; aliases handle display.

### 5.2 Money + decimal types (H9)

- **Wire format:** `Money { value: string, currency: string }`. `value` is a Python `Decimal` `str()`-formatted. Backend uses `decimal.Decimal` everywhere internally; SQLAlchemy column `NUMERIC(20, 8)` per CLAUDE.md SQL conventions.
- **Frontend rule — string is the canonical value, Number(s) is ONLY for display:**

  ```ts
  // shared util in @/lib/decimal.ts
  export function safeParseDecimal(s: string): { display: number; precise: string; lossy: boolean } {
    const n = Number(s);
    const lossy = n.toString() !== s;  // ECMAScript number cannot round-trip
    return { display: n, precise: s, lossy };
  }
  ```

  - `precise` is the canonical value. Persisted to stores AS-STRING. Used as-is for keys, comparisons, and any future arithmetic.
  - `display` is for `Intl.NumberFormat`. If `lossy`, the cell renders with a monospace `*` suffix and a tooltip: "Display value rounded; precise value: {precise}".
  - **No frontend code is allowed to call `Number(value)` and feed the result into arithmetic.** A small ESLint rule (`no-unsafe-decimal-arithmetic`, written for this repo) flags `+ - * /` operations on `Money.value` accesses.
- `NumericCell` primitive grows a `valueString?: string` overload. When provided, it routes through `safeParseDecimal`. Existing `value: number` callers unchanged.

### 5.3 UK-pence + split handling — sidecar-internal (H11)

- **Quote currency normalization:** `_normalize_quote_currency(symbol, exchange, price, currency)` divides by 100 if `currency == 'GBP'` AND `exchange ∈ {'LSE', 'LSEETF', 'IBIS', ...}` (full list lifted from v1's `services/quotes/base.scale_gbx_if_needed`). Applied at every quote emission.
- **PnL via `reqPnLSingle`:** `_pnl_for_position(account, conid)` uses an `IB.reqPnLSingle()` cache (same pattern as v1's `IBKRAdapter._pnl_subs`); caches the `PnLSingle` object until disconnect. NEVER computes naive `(mp - avg) * qty`.
- **Per-account `avg_cost_unit` config (H11):** the empirical observation in `ibkr_uk_pence_units.md` ("this user's IBKR returns avg_cost in pounds") is not guaranteed across all IBKR accounts. Each row in `broker_accounts` carries an implicit unit assumption; we expose it via `app_config`:

  ```
  broker.<account_number>.avg_cost_unit  = "pounds"  (default)
                                         | "pence"   (if IBKR returns pence for THIS account)
  ```

  Sidecar reads via the existing Phase-2 `ConfigService` at startup and on cache invalidation. Default `pounds` matches this user's empirical baseline.

- **Invariant check:** backend's `AccountService` runs a sanity check on every `get_positions`: if `Σ(quantity × avg_cost) > 1.5 × net_liquidation` for any account, log WARN `avg_cost_unit_suspected_wrong{account}` and emit a Prometheus metric. Surfaces in `/api/admin/health` so a misconfigured `avg_cost_unit` is visible without waiting for a user to notice the wrong number on the dashboard.

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
    # Windows + DST handling ported from v1's BrokerWatchdog.ps1 Test-InResetWindow.
    ...

def seconds_until_window_ends(now: datetime) -> int:
    # Returns seconds remaining in the current reset window, or 0 if not in one.
    ...
```

Used in two places:
- **Backend REST routes (C3):** during a reset window, return `503` with body `{"error":"broker_maintenance","window":"...","until":"<iso8601>"}` and `Retry-After: <seconds_until_window_ends>`.
- **Watchdog (`BrokerWatchdog.ps1` already implements `Test-InResetWindow`):** skip sidecar probes during weekend reset; tolerate daily reset BAD reads without kill+restart.

**Edge transitions:** the granularity is the hour boundary (e.g., `et.hour >= 23` for weekend reset start). A request straddling 22:59:59 → 23:00:00 ET sees `in_weekend_reset` flip to `true` on the next call. Acceptable for a multi-hour window.

**Tzdata (M20):** `ZoneInfo("America/New_York")` requires `tzdata` on Linux. Backend Dockerfile must `RUN apt-get install -y --no-install-recommends tzdata`. Listed in §9.3 Modified.

### 6.2 Reconnect / backoff (M21 — single source of truth)

- **Sidecar → Gateway:** `ib_async`'s built-in handlers manage reconnect (via `disconnectedEvent` and internal retry). Sidecar's only escalation: if `IB.isConnected()` stays false for >30s, exit 64 → Task Scheduler relaunch + sidecar self-throttled backoff (§4.2). No competing reconnect loop in our code.
- **Backend → Sidecar:** managed gRPC channel with default `grpc.keepalive_time_ms=30000`, `grpc.keepalive_timeout_ms=10000`, and a `max_reconnect_backoff_ms=30000`. Channel reconnect is gRPC's job. `BrokerRegistry` only observes via `Health` RPC: every 5s when last probe was unhealthy, every 60s when healthy.
- **Frontend → Backend:** existing `services/api.ts` pattern, no change. Polling cadence stays at 5s for positions/orders during active dashboard use.

### 6.3 Multi-account discovery cadence

`BrokerRegistry.discover_loop` runs every 60s. Cheap operation (`reqManagedAccts` + `reqAccountSummary` are single TWS API calls per sidecar). Soft-delete fires only against accounts that a healthy sidecar reported missing (C1) — never globally.

### 6.4 Logging + metrics

- **Sidecar log dir:** `%ProgramData%\dashboard\sidecar-<label>\` (NOT `C:\IBC\Logs\` to keep IBC's logs clean).
- **Format:** structlog JSON; key fields: `ts`, `label`, `event`, `gateway_port`, `grpc_port`, `request_id`, `latency_ms`.
- **Log rotation (H8):** Python `logging.handlers.TimedRotatingFileHandler(when='midnight', backupCount=14, encoding='utf-8')` — rotates daily, keeps 14 days, gzips old files via a `Register-ScheduledJob -Name RotateSidecarLogs -DailyAt 04:30` task. Total disk per sidecar capped at ~500 MB.
- **Redaction (L29):** processor matches against **field name** patterns (not substring), specifically `^(password|secret|token|tls_key|private_key|api_key)$`. Values in `cert_path` / `crl_path` / `key_id` keys remain logged.
- **Backend metrics (Prometheus, exposed on existing `/metrics`):**
  - `adapter_sidecar_health{label, result}` — `result ∈ ok|unreachable|degraded|gateway_down`
  - `adapter_rpc_latency_ms{label, method}` — histogram
  - `broker_accounts_count{broker_id, mode, deleted}` — gauge
  - `account_discover_loop_runs_total{result}` — counter (incremented per `_discover_once` from the discover loop pseudocode in §4.3)
  - `avg_cost_unit_suspected_wrong{account}` — counter (H11)
  - `mtls_handshake_failures_total{label, reason}` — counter
- **Tray:** 4 gateway dots + 4 sidecar dots, color-coded green / amber (degraded) / red (BAD). State source: `C:\dashboard\state\*.health`.

## 7. Security boundaries

- **WG-only:** sidecars bind `10.10.0.2:18001-18004`, refuse anything off the WireGuard interface (verify with `netstat -an` post-deploy).
- **mTLS-required, TLS 1.3 minimum (M23):** gRPC server config `(min_protocol_version=TLSv1.3)`. Backend client matches. Documented to rotate to whatever the latest stable Python `grpcio` supports at each annual cert renewal.
- **CRL-aware (C2):** sidecars reload `crl.pem` every 60s; revoked certs are rejected within ≤60s of `revoke-cert.ps1`.
- **No secret state in sidecars:** gateway socket is already authenticated; sidecar holds only its own server cert + the CA bundle + the CRL (read-only). DPAPI-encrypted IBKR creds at `C:\IBC\secrets\<label>.{login,password,totp}.enc` are NOT touched by sidecars — only by `Launch-Gateway.ps1`.
- **Backend `app_secrets`** holds the client cert + key + CA bundle (Fernet-encrypted via Phase 2). Loaded once at lifespan startup; never logged.
- **Failed mTLS handshakes** log `cert_verify_fail` + the SHA256 of the offered cert (NOT the cert itself); after 5 consecutive failures from the same peer, sidecar drops to a 30s sleep before accepting again (basic anti-flood).
- **NUC compromise model (H14):** the NUC is the single trust root. If pwned: rotate root CA, republish CA bundle, restart all 4 sidecars + backend. Plan for ~5 min downtime; runbook in `deploy/nuc/RUNBOOK-mtls-recovery.md`. Treat sidecar→backend traffic between t-of-pwn and t-of-rotation as compromised.

## 8. Testing strategy

### 8.1 Unit tests

- Sidecar (`tests/sidecar/`): `pytest` + `pytest-asyncio` + a handwritten `FakeIB` (mocks `ib_async.IB`) + `pytest-grpc` for in-process gRPC server. Coverage target 80% on `sidecar/`.
- Backend (`tests/api/test_accounts.py`, `tests/services/test_brokers.py`): mock `BrokerSidecarClient`; test discover loop (happy path, partial-fleet failure, all-unhealthy → no soft-delete, single-tick error → loop survives), soft-delete invariants, route auth, maintenance-window short-circuits, AccountResponse boundary stripping.
- Frontend (`frontend/src/services/{accounts,positions,orders}.test.ts`): existing tests stay; add 1 each that flips `VITE_USE_MOCKS=false` and verifies real fetch path with `MSW` interception, including the 503-maintenance error path. ESLint test: `no-unsafe-decimal-arithmetic` rule on a small fixture.

### 8.2 Contract tests — golden traces + nightly real-IB run (H12)

Two layers ensure proto↔ib_async fidelity:

1. **Always-on golden traces (CI on every PR):** during a one-time real-paper recording run (`scripts/record-golden-traces.ps1`), capture `ib_async` responses to a JSON fixtures dir at `sidecar/tests/golden/`. Replay via `FakeIB` that returns the recorded bytes. Test asserts proto output matches an expected JSON. Catches sidecar-side regressions (broken proto mapping, missing fields) without needing IBKR connectivity.

2. **Nightly real-IB cron (workflow `.github/workflows/nightly-real-ibkr.yml`):** runs the full sidecar contract test suite against IBKR's paper Gateway 4002 on the NUC at 06:00 UTC. Surfaces ib_async breaking changes within 24h. NOT PR-gated (would hammer the gateway).

Manual workflow dispatch (`CI_USE_REAL_IBKR=1`) remains for ad-hoc runs.

### 8.3 Integration / smoke

Extend `tests/e2e/smoke.spec.ts` with:
- `GET /api/accounts` returns `AccountListResponse` shape; `accounts[].id` is a UUID; no `gateway_label` or `account_number` keys present anywhere in the body.
- `GET /api/accounts/{id}/positions` returns proto-shaped JSON; all `Money.value` fields parse as Decimal strings.
- `GET /api/accounts/{id}/summary` returns Money objects with `currency` set.
- `GET /api/accounts/{id}/orders` returns `OrdersResponse`, possibly empty.

These run against prod via CF service token, same as existing Phase 2 admin smoke. Exit criterion (§10) requires all 4 to pass.

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
proto/buf.lock                                         (generated; committed)
backend/app/brokers/base.py                            ~100   (Pydantic mirror of proto types + AccountResponse boundary)
backend/app/services/brokers.py                        ~280
backend/app/services/ibkr_maintenance.py               ~100   (ported from v1; in_weekend_reset, in_daily_reset, seconds_until_window_ends)
backend/app/api/accounts.py                            ~180   (incl. Pydantic AccountResponse, AccountListResponse, AccountAliasUpdate, error-envelope helpers)
backend/migrations/versions/0002_broker_accounts.py    ~100   (incl. last_seen_via column + scoped soft-delete)
backend/tests/services/test_brokers.py                 ~240
backend/tests/api/test_accounts.py                     ~240
backend/tests/services/test_ibkr_maintenance.py        ~100
sidecar/ibkr_sidecar.py                                ~450
sidecar/handlers.py                                    ~320
sidecar/normalize.py                                   ~140   (UK-pence + decimals + per-account avg_cost_unit)
sidecar/pnl_cache.py                                   ~80    (reqPnLSingle subs)
sidecar/probe.py                                       ~80    (Health-only client for the watchdog probe-sidecar.exe)
sidecar/tests/test_handlers.py                         ~280
sidecar/tests/test_normalize.py                        ~100
sidecar/tests/golden/                                  (recorded fixtures; ~5-10 JSON files)
sidecar/pyproject.toml                                 ~50
sidecar/scripts/build-windows.ps1                      ~40    (PyInstaller --onedir wrapper, builds ibkr-sidecar.exe + probe-sidecar.exe)
sidecar/scripts/record-golden-traces.ps1               ~60
deploy/nuc/Launch-IBKRSidecar.vbs                      ~10
deploy/nuc/register-ibkr-sidecar.ps1                   ~100   (sidecar +30s after gateway stagger)
deploy/nuc/verify-wg-windows.ps1                       ~80    (§0 prerequisite check)
deploy/nuc/provision-sidecar-mtls.ps1                  ~180   (CA + 4 server + 1 client + initial empty CRL)
deploy/nuc/provision-and-publish.ps1                   ~80    (wraps provision + POST to admin secrets API)
deploy/nuc/renew-sidecar-mtls.ps1                      ~100
deploy/nuc/revoke-cert.ps1                             ~50    (appends serial to crl.pem, bumps mtime)
deploy/nuc/Probe-Sidecar.ps1                           ~60    (PS wrapper around probe-sidecar.exe; writes state file)
deploy/nuc/RUNBOOK-mtls-recovery.md                    ~80    (NUC compromise tabletop)
frontend/src/services/{accounts,positions,orders}.ts   ~+60 each (USE_MOCKS branch + MaintenanceError handling)
frontend/src/lib/decimal.ts                            ~40    (safeParseDecimal helper)
frontend/eslint-rules/no-unsafe-decimal-arithmetic.js  ~80
.github/workflows/nightly-real-ibkr.yml                ~50
```

### 9.2 Ported from `/mnt/c/Dashboard_old/deploy/nuc/` (verbatim or near-verbatim)

```
BrokerWatchdog.ps1         (299 lines, with sidecar-probe block + reset-aware short-circuit added)
BrokerTray.ps1             (559 lines, with sidecar dots — first task reviews layout fit; budget +200 lines if rewrite needed per M19)
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
backend/Dockerfile                                     + tzdata (M20: ZoneInfo("America/New_York"))
backend/app/main.py                                    lifespan: BrokerRegistry start/stop + discover_loop
backend/app/core/deps.py                               + get_account_service, get_broker_registry
backend/app/api/__init__.py                            + accounts router
docker-compose.prod.yml                                (no change — sidecars run on NUC, not VPS)
docker-compose.yml                                     (no change for Phase 4)
.env.example                                           + BROKER_SIDECAR_HOSTS (host:port list); mTLS material lives in app_secrets, not .env
frontend/package.json                                  + @bufbuild/protoc-gen-es (devDep), proto:gen script
frontend/.gitignore                                    + src/proto-gen/
frontend/.storybook/preview.ts                         set VITE_USE_MOCKS=true
frontend/eslint.config.mjs                             + custom no-unsafe-decimal-arithmetic rule
.github/workflows/ci.yml                               + buf lint + buf generate + sidecar tests + sidecar coverage gate + frontend ESLint custom rule
.github/workflows/deploy.yml                           + accounts smoke
TASKS.md, CHANGELOG.md, CLAUDE.md                      Phase 4 close-out (Phase 4 plan adds these in close-out task)
```

## 10. Exit criteria (definition of done)

- §0 prerequisite check `verify-wg-windows.ps1` exits 0 (CRITICAL gate).
- All 4 IBKR sidecars register as scheduled tasks on the NUC, start at-logon, survive logoff, log to per-label dirs with daily rotation.
- `GET /api/accounts` returns the 4 IBKR accounts (the user's actual ISA-live, ISA-paper, normal-live, normal-paper) — verified via `scripts/verify-accounts.sh` (one-shot script, asserts shape + UUID format + presence of all 4 expected aliases).
- `GET /api/accounts/{id}/{summary,positions,orders}` round-trips for all 4 accounts in prod via CF service token.
- Frontend with `VITE_USE_MOCKS=false` renders the real account picker, real positions table on `/positions`, real orders on `/orders`. `degraded_sidecars` correctly reflected in the topbar pill on a single-sidecar kill test.
- Watchdog kills + restarts a stuck sidecar within 10 min outside a reset window; does NOT touch sidecars during the Fri 23:00 ET → Sat 03:00 ET weekend reset (verified by setting NUC clock forward to a reset boundary).
- CRL revocation drill: `revoke-cert.ps1 -Serial <client-cert-serial>` causes backend to fail mTLS within 60s; restoring (re-issuing client cert via `provision-sidecar-mtls.ps1`) restores connectivity.
- 80%+ test coverage on backend `app/brokers/`, `app/services/brokers.py`, `app/services/ibkr_maintenance.py`, sidecar `sidecar/`. Golden traces + nightly real-IBKR cron green for 7 consecutive runs before tag.
- Playwright smoke green: 11 prior tests + 4 new (`/api/accounts/*`).
- mTLS proven: random tampered client cert is rejected by sidecar; backend with valid client cert passes; revoked client cert rejected within 60s.
- `CHANGELOG.md`, `TASKS.md`, `CLAUDE.md` updated; `v0.4.0` tagged after `gh run watch` greens on both CI + Deploy + the first nightly-real-ibkr run.

## 11. Risks + open questions

| Risk | Mitigation |
|---|---|
| Discover loop dies inside FastAPI lifespan → silent stall → soft-delete wipes accounts (C1, H13) | Per-iteration `try/except` in `discover_loop`; soft-delete scoped to `last_seen_via = ANY(:healthy_labels)`; `account_discover_loop_runs_total{result=err}` Prometheus counter visible on `/metrics` |
| `clientId` collision across simultaneously-restarting sidecars or local-dev `ib_async` clients (H5) | `clientId = (FNV1a32(hostname \|\| "\|" \|\| label) % 900) + 100`; documented one-liner in `ibkr_sidecar.py`; sidecar logs WARN + exits 64 on rejection; self-throttled backoff prevents tight loop |
| Self-signed CA private key on NUC = single trust root (C2, H14) | CRL-based revocation reloaded every 60s by sidecars; `RUNBOOK-mtls-recovery.md` covers root-CA rotation; revoke-cert.ps1 + provision-and-publish.ps1 keep the rotation flow scripted; ~5 min full-stack downtime budgeted for root rotation |
| Single sidecar zombie wedges Gateway socket | Watchdog probes Gateway socket directly (existing v1 logic); if Gateway is fine but sidecar is stuck, kill sidecar; if Gateway is stuck, restart Gateway via existing v1 chain |
| mTLS cert rotation breaks prod between cert install + sidecar restart | `provision-sidecar-mtls.ps1` is idempotent + atomic; `renew-sidecar-mtls.ps1` rolls one sidecar at a time; full-fleet rotation only on root-CA compromise (runbook) |
| Frontend hits real REST in Storybook by accident | `VITE_USE_MOCKS` defaults to `true` in `.storybook/preview.ts` global decorator |
| Frontend silently rounds Decimal to JS number → wrong P&L on display (H9) | `safeParseDecimal` helper computes `lossy` flag; `NumericCell` renders `*` suffix + tooltip when lossy; ESLint `no-unsafe-decimal-arithmetic` rule blocks `+ - * /` on `Money.value` outputs |
| ib_async version drift across sidecars on concurrent NUC update | Sidecars are PyInstaller-frozen (`--onedir`) — version is baked at build time. Build SHA in `HealthResponse.sidecar_version` makes drift visible via the watchdog's `Probe-Sidecar.ps1` state file |
| IBKR returns multi-account positions across `reqPositionsAsync` even when `account` filter is set | Per `ibkr_uk_pence_units.md` v1 fix — sidecar uses `reqPositionsAsync(account=...)` per IBKR docs but client-side-filters by account prefix; emits a WARN if the filter trimmed any rows |
| `avg_cost_unit` differs per account (this user's empirical observation may not generalize) (H11) | Per-account `broker.<account_number>.avg_cost_unit` config key (default `"pounds"`); sanity invariant `Σ(qty × avg_cost) > 1.5 × NLV` triggers `avg_cost_unit_suspected_wrong{account}` metric + `/api/admin/health` surface |
| Sidecar contract regression slips through PR CI (H12) | Always-on golden-trace tests on every PR (no IBKR needed); nightly real-IBKR cron catches ib_async breaking changes within 24h |
| 503 + Retry-After during reset windows breaks frontend that expects 200 (C3) | Frontend's existing error handler reads `error` + `until` body fields and renders the maintenance banner; tests cover the path; the banner UX is identical to v1 dashboard's reset-window behavior |
| WG interface not on Windows side, sidecars can't bind 10.10.0.2 (C4) | §0 `verify-wg-windows.ps1` halts the phase if (a) WG service not running, (b) 10.10.0.2 not on a Windows interface, (c) firewall rule missing, (d) test bind fails |
| Windows Task Scheduler tight-relaunch loop on persistent failure (H6) | Sidecar self-throttles via `<state-dir>/last_fail.txt`; Sleep at startup if recent failure; clean shutdown clears the file |
| Log dir grows unboundedly on the NUC (H8) | `TimedRotatingFileHandler` with 14-day retention; nightly gzip via `Register-ScheduledJob`; ~500 MB cap per sidecar |
| PyInstaller build broken on Windows | First Phase 4 task validates the build before any sidecar code lands. If broken, fall back to "uv on the NUC" install |

**Open questions:**

1. Do we want backend → sidecar to fail-open (treat unreachable sidecar as "no accounts") or fail-closed (`503` on every related route until sidecar back)? **Resolved: fail-open with `degraded_sidecars` envelope** (M24).
2. Account picker order: alphabetical by alias, or grouped by mode (live first, paper second), or user-set `display_order`? **Resolved: mode-grouped, alphabetical-within-group**, with `display_order` available but unused in v0.4.0 (no UI to set it).
3. Account aliases: editable from `/admin/config` (Phase 2 surface) or a dedicated `/admin/accounts` route in Phase 4? **Resolved: dedicated route** — consistent with the per-broker UX expected in Phase 5+.

These are tagged in the plan for explicit decision before implementation begins.

## 12. Architect review — applied

Adversarial review run 2026-04-25 surfaced 30 findings (4 CRITICAL, 10 HIGH, 11 MEDIUM, 5 LOW). Per CLAUDE.md phase workflow step 3, all CRITICAL + HIGH applied inline; MEDIUMs fixed-or-documented; LOWs document-or-defer.

| ID | Severity | Headline | Where | Disposition |
|---|---|---|---|---|
| C1 | CRITICAL | Soft-delete logic can wipe all accounts if discover loop stalls | §4.4 + §6.3 | **Applied.** Added `last_seen_via` column; soft-delete scoped to `last_seen_via = ANY(:healthy_labels)` AND row-missing-from-tick. Discover-loop pseudocode in §4.3 makes the contract explicit. |
| C2 | CRITICAL | Self-signed CA with no revocation path | §4.8 + §7 + §11 | **Applied.** File-based CRL at `C:\dashboard\secrets\crl.pem` reloaded every 60s by sidecars. New `revoke-cert.ps1`. RUNBOOK-mtls-recovery.md added. |
| C3 | CRITICAL | `204 No Content` during maintenance breaks JSON-fetching clients | §4.5 + §6.1 | **Applied.** All maintenance responses now `503 + Retry-After` + JSON body `{error, window, until}`. Frontend error handler renders banner. |
| C4 | CRITICAL | Sidecar binding `10.10.0.2` assumes WG-on-Windows, unverified | §3.1 → new §0 | **Applied.** New §0 prerequisites + `verify-wg-windows.ps1` as Phase 4 task 1. Halt-the-phase if any check fails. |
| H5 | HIGH | `clientId` derivation collides under multi-host scenarios | §4.2 | **Applied.** Formula now `(FNV1a32(hostname || "|" || label) % 900) + 100`; documented. |
| H6 | HIGH | Windows Task Scheduler doesn't have native exponential backoff | §4.2 + §11 | **Applied.** Sidecar self-throttles via `<state-dir>/last_fail.txt`; Task Scheduler kept simple. |
| H7 | HIGH | "Subscribe to events (passive)" is wrong — `req*` calls required | §4.2 | **Applied.** Lifecycle now lists `reqManagedAccountsAsync`, `reqAccountSummaryAsync(All)`, on-demand `reqPnLSingle` per (account, conid). |
| H8 | HIGH | No log rotation = unbounded disk growth | §6.4 | **Applied.** `TimedRotatingFileHandler(when='midnight', backupCount=14)`; gzip via `Register-ScheduledJob`; 500 MB cap. |
| H9 | HIGH | Decimal string → JS number conversion loses precision | §5.2 | **Applied.** `safeParseDecimal` helper with `lossy` flag; `NumericCell` `*` suffix; ESLint `no-unsafe-decimal-arithmetic` rule. |
| H10 | HIGH | Manual operator-pipes-cert is error-prone | §4.8 | **Applied.** `provision-and-publish.ps1` runs provision + POSTs cert PEMs to admin secrets API via CF service token. |
| H11 | HIGH | avg_cost UK-pence assumption based on one user's observation | §5.3 | **Applied.** Per-account `broker.<account>.avg_cost_unit` config key; `avg_cost_unit_suspected_wrong{account}` invariant metric. |
| H12 | HIGH | Sidecar contract test gated behind manual `CI_USE_REAL_IBKR=1` | §8 | **Applied.** Two layers: (a) always-on golden traces on every PR; (b) nightly real-IBKR cron at `.github/workflows/nightly-real-ibkr.yml`. |
| H13 | HIGH | Discover loop swallowing failures inside FastAPI lifespan | §4.3 | **Applied.** Explicit pseudocode block with per-iteration try/except + `discover_runs_total{result}` counter. |
| H14 | HIGH | NUC pwn risk unmitigated single-point-of-trust | §11 | **Applied.** New risk row + RUNBOOK reference + revocation flow. |
| M15 | MEDIUM | gateway_label exposure inconsistency | §4.1 + §4.5 | **Applied.** Explicit `AccountResponse` Pydantic model strips `gateway_label`/`account_number` at REST boundary. Smoke test asserts absence. |
| M16 | MEDIUM | `currency_base` schema column has no source | §4.4 + §6.3 | **Applied.** Removed DEFAULT; sidecar populates from `accountSummary` BASE tag in `ListManagedAccounts`. |
| M17 | MEDIUM | PyInstaller `--onefile` wrong for daemon | §4.2 | **Applied.** Switched to `--onedir`; ZIP distribution; sidecar.exe + probe-sidecar.exe shipped together. |
| M18 | MEDIUM | Probe-Sidecar.ps1 implementation undecided | §4.7 | **Applied.** Picked `probe-sidecar.exe` (built from `sidecar/probe.py` via PyInstaller); avoids .NET dep. |
| M19 | MEDIUM | BrokerTray.ps1 may not flexibly support 8 dots | §4.7 + §9.1 | **Documented.** First Phase 4 task reviews v1 layout; ~200 line rewrite budget if needed. |
| M20 | MEDIUM | `ZoneInfo("America/New_York")` requires tzdata | §6.1 + §9.3 | **Applied.** Backend Dockerfile gets `apt-get install tzdata`. |
| M21 | MEDIUM | ib_async vs sidecar reconnect contention | §6.2 | **Applied.** Single source of truth: ib_async owns reconnect; sidecar's only escalation is exit-and-relaunch on >30s `isConnected()=false`. |
| M22 | MEDIUM | PATCH alias validation unspecified | §4.5 | **Applied.** Pydantic `AccountAliasUpdate(alias: str = Field(min_length=1, max_length=64, pattern=r"^[\w\s\-.&]+$"))`. |
| M23 | MEDIUM | Cipher suite for mTLS unspecified | §7 | **Applied.** TLS 1.3 minimum; documented to revisit at annual rotation. |
| M24 | MEDIUM | Partial sidecar fleet UX implicit | §3.1 + §4.5 | **Applied.** `AccountListResponse.degraded_sidecars: list[str]`; topbar `ConnectedDropdown` reads it. |
| M25 | MEDIUM | NUC cold-start ordering between Gateway and Sidecar tasks unclear | §4.7 | **Applied.** Sidecar staggered +30s after its matching gateway (gateway 0/30/60/90s → sidecar 30/60/90/120s). |
| L26 | LOW | Sidecar TLS material at `C:\IBC\secrets\` mixes concerns | §4.8 | **Applied.** Moved to `C:\dashboard\secrets\` (separate dir from IBC gateway secrets). |
| L27 | LOW | `display_order` unused in v0.4.0 | §4.4 | **Documented.** Kept for forward-compat; SQL comment notes "unused in v0.4.0; Phase 5+ admin UI". |
| L28 | LOW | Missing `proto/buf.lock` in §9 | §9.1 | **Applied.** Listed as committed. |
| L29 | LOW | Logging redaction patterns may overmatch | §6.4 | **Applied.** Pattern now per-key (`^(password|secret|token|tls_key|private_key|api_key)$`), not substring. |
| L30 | LOW | IBKR account number format invariant unstated | §4.4 + §4.5 | **Applied.** Pydantic field validator `^[UDFu][0-9]+$` on `AccountResponse` (soft warning, not DB-enforced). |

**Net effect on scope:** ~6 new/modified files (verify-wg-windows.ps1, provision-and-publish.ps1, revoke-cert.ps1, RUNBOOK-mtls-recovery.md, decimal.ts, no-unsafe-decimal-arithmetic.js, nightly-real-ibkr.yml workflow), + tzdata in Dockerfile, + `last_seen_via` column, + a few helper functions in `ibkr_maintenance.py`. ~400 additional lines vs. pre-review estimate. No locked design choices were re-litigated.
