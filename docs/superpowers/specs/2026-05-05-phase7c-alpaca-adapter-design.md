# Phase 7c — Alpaca Adapter (sidecar_alpaca/) Design

> **Version**: v0.7.3 target
> **Phase**: 7c (after 7b.1 streaming quotes + 7b.1.5 instruments seed; before existing Phase 8 trade execution)
> **Status**: brainstorm draft — pending architect review and user approval
> **Reference memories**: `phase7c_alpaca_scope.md`, `phase7a_schwab_topology.md`, `phase6_futu_topology.md`, `phase7b1_shipped.md`, `codex_defaults.md`

## 1. Goal

Add a 4th broker stack — Alpaca — as a read-only adapter contributing two roles:

1. **`crypto.US` primary quote source** — replaces the placeholder `7b.2 coinbase` slot in the Phase 7b.1 source-router default. Free realtime via `wss://stream.data.alpaca.markets/v1beta3/crypto/us` (30-symbol soft cap).
2. **`stock.US` / `etf.US` quote fallback** — registered after Schwab in the source-router. Used only when Schwab is `UNHEALTHY` per the Phase 7b.1 health window (≥3 errors in 60s). Free realtime via `wss://stream.data.alpaca.markets/v2/iex` (30-symbol soft cap).
3. **Read-only account / position / order surfaces** — same shape as `sidecar_schwab` (paper + live).

Trade execution stays out of scope this phase; lands in Phase 8 alongside Schwab `PlaceOrder`.

## 2. Non-Goals

- **Trade execution** (Phase 8).
- **Algo Trader Plus subscription ($99/mo)** — Schwab already provides free unlimited US equity SIP-equivalent; Alpaca free's 30-symbol cap is acceptable as fallback.
- **Options scaffolding** — Alpaca added options April 2024 but our Phase 12 hasn't reached options yet; no payoff in scaffolding now.
- **Multi-Alpaca-account fan-out** — single account in app_secrets per mode (paper / live). If the user opens a 2nd Alpaca account later, generalize then.

## 3. Architecture

### 3.1 Topology

```
┌────────────────────────────────────────────────────────────────────┐
│ docker-compose.prod.yml                                            │
│                                                                     │
│  ┌──────────────┐   gRPC (insecure, td-net)   ┌──────────────────┐ │
│  │   backend    │◄────────────────────────────│  alpaca-sidecar  │ │
│  └──────┬───────┘    Configure / Stream...    │   (Docker)       │ │
│         │                                      │  port 9091       │ │
│         │   /api/admin/secrets                 │                  │ │
│         ▼                                      └────────┬─────────┘ │
│  ┌──────────────┐                                       │           │
│  │ app_secrets  │  alpaca.{live,paper}.{api_key,secret} │ HTTPS+WSS │
│  └──────────────┘                                       │           │
└─────────────────────────────────────────────────────────┼───────────┘
                                                          │
                                                          ▼
                                          ┌──────────────────────────┐
                                          │  Alpaca data + broker    │
                                          │  api.alpaca.markets      │
                                          │  paper-api.alpaca...     │
                                          │  stream.data.alpaca...   │
                                          └──────────────────────────┘
```

Shape mirrors **Phase 7a Schwab** (in-cluster Docker, `td-net` bridge, no mTLS — peer trust is the docker-network boundary). NOT on the NUC, NOT mTLS, NOT PyInstaller.

- **Sidecar**: `alpaca-sidecar` Docker service (`docker-compose.prod.yml`), listening on `0.0.0.0:9091` insecure-port. Built from `sidecar_alpaca/Dockerfile`. Resolves to `alpaca-sidecar:9091` from the backend.
- **Backend dialing**: registered as broker label `"alpaca"` in `BrokerRegistry`; the dial address comes from app_config.
- **No mTLS to Alpaca itself**: Alpaca's API is plain HTTPS+WSS over public internet. Sidecar terminates against `api.alpaca.markets` / `paper-api.alpaca.markets` / `stream.data.alpaca.markets` directly.

### 3.2 Auth

**Single layer** — long-lived API keys in `app_secrets`. No OAuth, no token rotation, no `BackendCallback`, no refresher container. This is the simplest broker auth in the project.

Schema:

```
app_secrets.broker.alpaca.live.api_key       (Fernet-encrypted)
app_secrets.broker.alpaca.live.api_secret    (Fernet-encrypted)
app_secrets.broker.alpaca.paper.api_key      (Fernet-encrypted)
app_secrets.broker.alpaca.paper.api_secret   (Fernet-encrypted)
```

**Mode toggle**: same `live` / `paper` split as IBKR. Sidecar reads `MODE` env (`live` or `paper`) at boot — Compose sets it explicitly per service. Two `alpaca-sidecar` containers run simultaneously: `alpaca-sidecar-live` and `alpaca-sidecar-paper`. They share the image but differ in env + which app_secret they read.

**Configure RPC** ships the relevant `api_key` + `api_secret` per the existing 5-trigger Configure contract from Phase 7a. The sidecar caches them in-memory at boot; if the operator rotates keys via `POST /api/admin/secrets`, the backend fires `Configure` and the sidecar replaces creds atomically.

### 3.3 Source-router default (updated for v0.7.3)

| `<asset_class>.<country>` | Primary | Fallback | Notes |
|---|---|---|---|
| stock.US, etf.US | schwab | **alpaca** (new), then ibkr (paid bundles) | Schwab still primary; Alpaca free is 30-symbol cap, IEX-only — fallback only |
| index.US | schwab `$`-symbology | ibkr Cboe Streaming Indexes (paid $3.50) | Unchanged |
| **crypto.US** | **alpaca** (new) | (Phase 7b.2 coinbase) | Was `7b.2 coinbase` placeholder; now Alpaca — operator already has account |
| stock.UK | ibkr (LSE GBP 2/mo) | yfinance (delayed) | Unchanged |
| (everything else) | unchanged from 7b.1 default | | |

This change updates the default priority lists baked into `app/services/quotes/router.py`. Existing operator-overridden config (set via `POST /api/admin/config`) is respected — defaults only apply when no override exists.

## 4. Components

### 4.1 New: `sidecar_alpaca/`

Mirrors `sidecar_schwab/` layout — same files, same conventions:

```
sidecar_alpaca/
  Dockerfile
  pyproject.toml
  uv.lock
  __init__.py
  main.py            # gRPC server boot, port 9091
  config.py          # MODE env + base URLs (live vs paper) + endpoint resolution
  client.py          # AlpacaClient — wraps alpaca-py SDK; sole import surface
  auth.py            # api_key/secret cache + atomic swap on Configure
  handlers.py        # Broker servicer impls: Configure, ListManagedAccounts, GetAccountSummary,
                     # GetPositions, GetOrders, StreamQuotes
  streamer.py        # AlpacaStreamer for IEX equity + crypto WS, two upstream conns per sidecar
  normalize.py       # Alpaca dict → proto Account/Position/Order/QuoteMessage
  metrics.py         # alpaca_* prometheus metrics
  tests/
  scripts/
```

**SDK isolation (M3 from Phase 7a)**: ONLY `client.py` imports `alpaca`. The rest of the sidecar consumes `client.py`'s normalized return types. Bumps to `alpaca-py` only need to touch one file.

**Two upstream WS connections per sidecar** (one for IEX equity, one for crypto). Both terminate against `stream.data.alpaca.markets`. The sidecar's `streamer.py` exposes a single `tick_callback: Callable[[QuoteMessage], None]` — both endpoints feed it.

### 4.2 Mode-split deployment

`docker-compose.prod.yml` adds two services:

```yaml
alpaca-sidecar-live:
  build: { context: ./sidecar_alpaca, dockerfile: Dockerfile }
  environment:
    MODE: live
    BACKEND_ADMIN_GRPC: backend:8001
    GRPC_PORT: "9091"
  networks: [td-net]

alpaca-sidecar-paper:
  build: { context: ./sidecar_alpaca, dockerfile: Dockerfile }
  environment:
    MODE: paper
    BACKEND_ADMIN_GRPC: backend:8001
    GRPC_PORT: "9092"
  networks: [td-net]
```

Same image, different env + port. Backend `BrokerRegistry` resolves two `alpaca` labels (`alpaca-live`, `alpaca-paper`) — distinct gateway instances — same pattern as IBKR's 4 gateways under one `ibkr` broker.

### 4.3 Quote-source registration

`alpaca` is **already registered** in the open-set quote source enum (`backend/app/services/quotes/base.py:66`, designed-for slot from Phase 7b.1). This phase only wires the actual streamer.

**Per-source 25-symbol soft cap** in `SubscriptionRegistry`: when a `subscribe` request would push the per-source count above 25 (5 buffer below Alpaca's 30 hard cap), the registry rejects with `op:"err", code:"SOURCE_CAP"`. SourceRouter falls through to the next priority entry (Coinbase in 7b.2; nothing in 7c). Metric: `quote_source_cap_rejected_total{source="alpaca", asset_class}`.

### 4.4 30-symbol cap visibility

Operator metric `quote_alpaca_subscription_active{endpoint="iex"|"crypto"}` — gauge, current symbol count per upstream WS. Alert `AlpacaSymbolCapNear` fires at ≥25 (5 buffer). Operator can then either prune subscriptions or upgrade to Algo Trader Plus.

### 4.5 Backend changes

**Minimal**:
- `app/brokers/registry.py` — add `alpaca` to broker_id enum / Literal type.
- `app/services/quotes/router.py` — append `alpaca` to default `stock.US`/`etf.US` priority lists; set `alpaca` as primary for `crypto.US`.
- Frontend broker picker — `alpaca` appears as a 4th broker option (live/paper).
- `account_hash` boundary strip — Alpaca uses an `account_id` (UUID) which is essentially the same as Schwab's `account_hash`; reuse the M22 strip pattern at the API boundary.

### 4.6 Tests

- `sidecar_alpaca/tests/test_streamer.py` — IEX subscribe/unsubscribe + crypto subscribe/unsubscribe + cap-hit; mock the upstream WS.
- `sidecar_alpaca/tests/test_handlers.py` — Configure round-trip; ListManagedAccounts; GetPositions normalize.
- `backend/tests/integration/test_alpaca_routing.py` — Quote engine routes `crypto:BTC:US` to alpaca; falls through to next on source `UNHEALTHY`.
- `backend/tests/api/test_alpaca_secrets.py` — admin secret seed/rotate flow.

### 4.7 Operator runbook

`deploy/runbook-alpaca-setup.md` — 5 steps:
1. Operator generates Alpaca live + paper API keys at `app.alpaca.markets/account/api-keys`.
2. `PUT /api/admin/secrets/broker/alpaca.live.api_key` + `.api_secret` (and paper).
3. `docker compose --profile default up -d alpaca-sidecar-live alpaca-sidecar-paper`.
4. Smoke: `GET /api/accounts` shows Alpaca rows; `GET /api/accounts/{id}/positions` returns ≥0 rows.
5. Quote smoke: subscribe `crypto:BTC:US` via `/ws/quotes`, verify ticks within 5s.

## 5. Critical numbers

- **Free tier hard caps** (Alpaca Basic, $0/mo): 30 symbols per WS endpoint (equity, crypto separately), 1 concurrent connection per endpoint, 200 REST calls/min.
- **Soft cap in SubscriptionRegistry**: 25 symbols per upstream (5-symbol buffer).
- **Symbol-cap alert threshold**: `AlpacaSymbolCapNear` at ≥25 (warning), 5min for-duration.
- **Reconnect**: bounded exponential `min(2**n, 60)` seconds, full subscription replay on each reconnect.
- **REST rate-limit**: 200/min hard. Discoverer fan-out for ListManagedAccounts/GetPositions runs every 30s — well under limit.

## 6. Metrics

Add to `backend/app/core/metrics.py`:

| Metric | Type | Labels |
|---|---|---|
| `alpaca_sidecar_uptime_seconds` | Gauge | `mode` (live/paper) |
| `alpaca_quote_ticks_total` | Counter | `endpoint` (iex/crypto), `mode` |
| `alpaca_ws_reconnect_total` | Counter | `endpoint`, `reason` (ws_close/idle/error) |
| `alpaca_subscription_active` | Gauge | `endpoint`, `mode` |
| `alpaca_http_requests_total` | Counter | `endpoint`, `status` |
| `alpaca_account_read_failures_total` | Counter | `kind` (positions/orders/summary) |
| `quote_source_cap_rejected_total` | Counter | `source`, `asset_class` (NEW; spans all sources, not just alpaca) |

## 7. Alerts

Add to `deploy/prometheus/alerts.yml` `phase7c_alpaca` group:

- `AlpacaSymbolCapNear` (warning, ≥25 symbols, 5m) — operator should prune or upgrade.
- `AlpacaWsReconnectFlapping` (warning, >5/15min, 5m).
- `AlpacaHttpErrorRateHigh` (warning, >10% 5xx/429, 10m).
- `AlpacaSidecarDown` (page, uptime=0 for >2min, 1m).

## 8. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Alpaca's 30-symbol cap silently drops the 31st subscription | SubscriptionRegistry per-source soft cap at 25; reject upfront with explicit error code; SourceRouter falls through to next priority. |
| Long-lived API keys leaked → unauthorized read | Same Fernet-encrypted `app_secrets` policy as IBKR/Futu/Schwab. Rotation = `POST /api/admin/secrets` + `Configure` retrigger. No log redaction needed (api_key never logged; api_secret never logged). |
| Alpaca paper data differs from live → fake-positive testing | Both modes wired; integration test runs against paper; live smoke is operator step in runbook. |
| Crypto WS endpoint geographic restriction | Doc doesn't mention. If sidecar gets 403 on connect, alert fires; operator can drop crypto.US route to coinbase fallback (Phase 7b.2). |
| `account_hash`-equivalent UUID leaks to FE via discoverer | M22 boundary strip in `AccountService` for the alpaca account_id field — same as Schwab account_hash. |

## 9. Architectural pillars (carry-forward from prior phases)

- **Sidecar pattern**: Phase 4 IBKR + Phase 6 Futu + Phase 7a Schwab established. New broker = new sidecar dir + 5-trigger Configure + boundary strip.
- **Single source of credentials**: `app_secrets` Fernet-encrypted; never `.env` past bootstrap.
- **Read-only first**: lessons from Phase 5/6/7a — every broker ships read-only first, trade execution in a follow-up phase. Reduces blast radius.
- **Config-driven source-router** from Phase 7b.1: routing changes are config, not code.
- **Codex defaults A-G** (`codex_defaults.md`): apply by default.

## 10. Chunk plan (preview — full plan lands in writing-plans phase)

- **A**. proto + broker registry: `alpaca` broker_id, `alpaca-live` / `alpaca-paper` gateway labels.
- **B**. `sidecar_alpaca/` skeleton: Dockerfile, pyproject (alpaca-py), main.py + handlers stub returning UNIMPLEMENTED for trade.
- **C**. Configure RPC + AlpacaClient (read-only REST: ListAccounts/GetPositions/GetSummary/GetOrders).
- **D**. AlpacaStreamer (IEX equity WS) + tick → QuoteMessage normalize.
- **E**. AlpacaStreamer extension (crypto WS).
- **F**. SubscriptionRegistry per-source soft cap + `quote_source_cap_rejected_total` metric.
- **G**. SourceRouter default updates (`crypto.US` primary alpaca; `stock.US`/`etf.US` fallback alpaca).
- **H**. Compose services (live + paper), tests, operator runbook, close-out.

## 11. Forward pointers

- **Phase 7b.2** (coinbase / oanda / yfinance): Coinbase becomes the `crypto.US` fallback to Alpaca. yfinance covers `stock.EU`/`JP`/`AU`/`CA` delayed. OANDA covers `forex.*`.
- **Phase 8** (trade execution): Alpaca `PlaceOrder` lands alongside Schwab. Alpaca's order shape is the simplest of the four brokers (no bracket/OCO until Phase 13).
- **Phase 12** (options): Alpaca options scaffold becomes useful — wire OPRA via the same streamer.

## 12. Deferred

- Multi-Alpaca-account fan-out (single account per mode this phase).
- OPRA options streaming (Phase 12).
- Algo Trader Plus subscription tier (no payoff while Schwab covers stocks free).
- Crypto fallback to Coinbase (Phase 7b.2).
- crypto.US instrument-seed entries (the Phase 7b.1.5 `seed_instruments_from_positions` helper already exists and runs on lifespan startup; Alpaca-held crypto rows get seeded automatically once positions land — no extra wiring this phase).

---

**End of design — implementation plan to be written separately via `writing-plans` after architect review + user approval.**
