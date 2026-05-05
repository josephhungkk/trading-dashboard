# Phase 7c — Alpaca Adapter (sidecar_alpaca/) Design

> **Version**: v0.7.3 target
> **Phase**: 7c (after 7b.1 streaming quotes + 7b.1.5 instruments seed; before existing Phase 8 trade execution)
> **Status**: post-architect-review (15 CRIT+HIGH+MED applied inline; 3 of 4 LOWs applied) — pending user approval
> **Reference memories**: `phase7c_alpaca_scope.md`, `phase7a_schwab_topology.md`, `phase6_futu_topology.md`, `phase7b1_shipped.md`, `codex_defaults.md`

## 1. Goal

Add a 4th broker stack — Alpaca — as a read-only adapter contributing two roles:

1. **`crypto.US` primary quote source** — replaces the placeholder `7b.2 coinbase` slot in the Phase 7b.1 source-router default. Free realtime via `wss://stream.data.alpaca.markets/v1beta3/crypto/us` (30-symbol hard cap; 25-symbol soft cap with two-layer enforcement).
2. **`stock.US` / `etf.US` quote fallback** — registered after Schwab in the source-router. Used only when Schwab is `UNHEALTHY` per the Phase 7b.1 health window (≥3 errors in 60s). Free realtime via `wss://stream.data.alpaca.markets/v2/iex` (30-symbol cap, same enforcement).
3. **Read-only account / position / order surfaces** — same shape as `sidecar_schwab` (paper + live).

Trade execution stays out of scope this phase; lands in Phase 8 alongside Schwab `PlaceOrder`.

## 2. Non-Goals

- **Trade execution** (Phase 8).
- **Algo Trader Plus subscription ($99/mo)** — Schwab already provides free unlimited US equity SIP-equivalent; Alpaca free's 30-symbol cap is acceptable as fallback.
- **Options scaffolding** — Alpaca added options April 2024 but our Phase 12 hasn't reached options yet; no payoff in scaffolding now.
- **Multi-Alpaca-account fan-out** — single account in app_secrets per mode (paper / live) this phase. Forward-compat schema reserves a per-account-label key shape so a future 2nd account does NOT need a migration (see §3.2).

## 3. Architecture

### 3.1 Topology

```
┌────────────────────────────────────────────────────────────────────┐
│ docker-compose.prod.yml                                            │
│                                                                     │
│  ┌──────────────┐   gRPC (insecure, td-net)   ┌──────────────────┐ │
│  │   backend    │◄────────────────────────────│  alpaca-sidecar  │ │
│  └──────┬───────┘    Configure / Stream...    │   -live (Docker) │ │
│         │                                      │  port 9091       │ │
│         │            ── + ──                   ├──────────────────┤ │
│         │                                      │  alpaca-sidecar  │ │
│         │                                      │   -paper (Docker)│ │
│         │   /api/admin/secrets                 │  port 9092       │ │
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

- **Sidecar**: two `alpaca-sidecar-{live,paper}` Docker services (`docker-compose.prod.yml`), insecure-port `0.0.0.0:9091` and `0.0.0.0:9092` respectively. Both built from `sidecar_alpaca/Dockerfile`.
- **Backend dialing**: registered as broker label `"alpaca"` in `BrokerRegistry`; the **gateway labels** `alpaca-live` and `alpaca-paper` distinguish instances (same broker_id-vs-gateway-label split as IBKR's 4 gateways). Dial address resolution via app_config (see §4.2).
- **No mTLS to Alpaca itself**: Alpaca's API is plain HTTPS+WSS over public internet. Sidecar terminates against `api.alpaca.markets` / `paper-api.alpaca.markets` / `stream.data.alpaca.markets` directly.

### 3.2 Auth (per-mode credential routing — HIGH-5)

**Single layer** — long-lived API keys in `app_secrets`. No OAuth, no token rotation, no `BackendCallback`, no refresher container. This is the simplest broker auth in the project.

**Forward-compat schema** (MED-2):

```
app_secrets.broker.alpaca.<account_label>.<mode>.api_key       (Fernet-encrypted)
app_secrets.broker.alpaca.<account_label>.<mode>.api_secret    (Fernet-encrypted)
```

This phase populates only `<account_label>="default"`. The lookup helper resolves `broker.alpaca.<account_label>.<mode>.api_key` first and falls back to `broker.alpaca.<mode>.api_key` for backward compat. Sidecar reads `ALPACA_ACCOUNT_LABEL` env (default `"default"`) and consults the labeled secret. Adding a 2nd Alpaca account is then a pure config change — no schema migration.

**Mode toggle**: same `live` / `paper` split as IBKR. Sidecar reads `MODE` env (`live` or `paper`) at boot — Compose sets it explicitly per service. Two `alpaca-sidecar` containers run simultaneously: `alpaca-sidecar-live` and `alpaca-sidecar-paper`. They share the image but differ in env + which app_secret they read.

**Per-mode Configure routing**:

- Each sidecar's MODE env (`live`|`paper`) is read by `auth.py` at boot AND echoed in the sidecar's `Health` response as a `mode` field. Backend's BrokerRegistry derives the mode from the gateway_label, validates the sidecar's reported mode matches, and on mismatch emits `alpaca_mode_mismatch_total{label}` and refuses to send Configure (paper sidecar must NEVER see live creds).
- Configure RPC payload to `alpaca-sidecar-live` carries ONLY `alpaca.<label>.live.api_key` + `.api_secret`. Same for paper. Backend never bundles both into one payload.
- Trigger 5 (Health.started_at delta) — if EITHER sidecar restarts, Configure fires only to that one sidecar.
- Secret rotation — `POST /api/admin/secrets/broker/alpaca.default.live.api_key` fires Configure ONLY to `alpaca-sidecar-live`; the paper sidecar's in-memory cred is unchanged.

### 3.3 Source-router default (updated for v0.7.3 — HIGH-3 precedence rule explicit)

| `<asset_class>.<country>` | Primary | Fallback | Notes |
|---|---|---|---|
| stock.US, etf.US | schwab | **alpaca** (new), then ibkr (paid bundles) | Schwab still primary; Alpaca free is 30-symbol cap, IEX-only — fallback only |
| index.US | schwab `$`-symbology | ibkr Cboe Streaming Indexes (paid $3.50) | Unchanged |
| **crypto.US** | **alpaca** (new) | (Phase 7b.2 coinbase) | Was `7b.2 coinbase` placeholder; now Alpaca — operator already has account |
| stock.UK | ibkr (LSE GBP 2/mo) | yfinance (delayed) | Unchanged |
| (everything else) | unchanged from 7b.1 default | | |

**Precedence rule (explicit)**: `app/services/quotes/router.py::_priority_list_for` keeps reading `self._config["quote_source_priority"]`. The defaults are merged in at `ConfigService` load time, NOT in `router.py`'s fallback path. The merge is **per-key**, not whole-table:

```
effective_priority[k] = override[k] if k in override else default[k]
```

A new constant module `app/services/config_defaults.py` holds the post-7c default table. An operator who has previously overridden `stock.UK` but not `crypto.US` still gets the new alpaca-primary default for `crypto.US` (no silent regression). Test: `test_quote_source_priority_per_key_merge.py`.

## 4. Components

### 4.1 New: `sidecar_alpaca/`

Mirrors `sidecar_schwab/` layout — same files, same conventions:

```
sidecar_alpaca/
  Dockerfile
  pyproject.toml
  uv.lock
  __init__.py
  main.py            # gRPC server boot, port from GRPC_PORT env
  config.py          # MODE env + base URLs (live vs paper) + endpoint resolution
  client.py          # AlpacaClient — wraps alpaca-py SDK; sole import surface (M3 isolation)
  auth.py            # api_key/secret cache + atomic swap on Configure
  handlers.py        # Broker servicer impls: Configure, ListManagedAccounts, GetAccountSummary,
                     # GetPositions, GetOrders, StreamQuotes
  streamer.py        # AlpacaStreamer for IEX equity + crypto WS, two upstream conns per sidecar
  normalize.py       # Alpaca dict → proto Account/Position/Order/QuoteMessage; populates source_meta
                     # (stripped at engine boundary unless OPERATOR_TRACE_QUOTES=1, INV-Q-2)
  metrics.py         # alpaca_* prometheus metrics
  tests/
  scripts/
```

**SDK isolation (M3 from Phase 7a)**: ONLY `client.py` imports `alpaca`. The rest of the sidecar consumes `client.py`'s normalized return types. Bumps to `alpaca-py` only need to touch one file.

**Two-WS supervisor with per-task isolation (HIGH-1)**:

`streamer.py` owns a top-level supervisor task that spawns two child tasks (`_iex_loop`, `_crypto_loop`), each with its own reconnect loop and exception boundary (Codex pattern C). Failure of one child MUST NOT cancel the other — the supervisor catches per-child exceptions, increments `alpaca_ws_reconnect_total{endpoint=..., reason="loop_crash"}`, sleeps the backoff, and restarts only that child. Verified by `test_streamer_isolation.py`: simulated IEX 5xx storm asserts crypto ticks continue to flow.

Both endpoints feed a single `tick_callback: Callable[[QuoteMessage], None]`. Per-callback isolation per Codex pattern C — one slow consumer must not block the other endpoint.

### 4.1.1 Reconnect contract — Subscribe vs Resync (CRIT-2)

Phase 7b.1 HIGH-1 carry-forward: AlpacaStreamer distinguishes the two backend-initiated reconnects:

- **`Subscribe` op** (sidecar restart, backend sees Health.started_at delta) → full upstream WS reconnect, replay all symbols.
- **`Resync` op** (gRPC-only reconnect, sidecar process intact) → reconcile only; send Alpaca WS subscribe/unsubscribe ONLY for the diff between backend's declared set and the streamer's `_upstream_active` set. NO disconnect of the upstream WS.

This keeps Alpaca's per-endpoint single-connection invariant safe across gRPC churn (avoids triggering Alpaca's 429 / connection-storm protection).

### 4.2 Mode-split deployment + dial resolution (HIGH-4)

`docker-compose.prod.yml` adds two services:

```yaml
alpaca-sidecar-live:
  build: { context: ./sidecar_alpaca, dockerfile: Dockerfile }
  environment:
    MODE: live
    BACKEND_ADMIN_GRPC: backend:8001
    GRPC_PORT: "9091"
    ALPACA_ACCOUNT_LABEL: default
  networks: [td-net]

alpaca-sidecar-paper:
  build: { context: ./sidecar_alpaca, dockerfile: Dockerfile }
  environment:
    MODE: paper
    BACKEND_ADMIN_GRPC: backend:8001
    GRPC_PORT: "9092"
    ALPACA_ACCOUNT_LABEL: default
  networks: [td-net]
```

**Backend dial resolution**: `BrokerRegistry` introduces a new "labeled docker sidecar" sub-pattern, between IBKR's 4-NUC-mTLS and Schwab's 1-docker-no-label:

```
app_config.broker_gateway_dial = {
  "alpaca-live":   "alpaca-sidecar-live:9091",
  "alpaca-paper":  "alpaca-sidecar-paper:9092",
  "schwab":        "schwab-sidecar:9090",            # existing, unchanged
  "ibkr-isa-live": "10.10.0.2:18001",                # existing WG IP, mTLS
  ...
}
```

The registry resolves `(broker_id="alpaca", gateway_label="alpaca-live")` → `"alpaca-sidecar-live:9091"`. `gateway_label` is the same key used in `broker_accounts.last_seen_via` (Phase 4 invariant), so the open-set is consistent. `last_seen_via` accepts `alpaca-live` and `alpaca-paper` as valid labels — no schema change (column is text). Schwab's existing fixed dial is **not migrated** into this table this phase (no behavior change).

### 4.3 Quote-source registration + two-layer cap (CRIT-1)

`alpaca` is **already registered** in the open-set quote source enum (`backend/app/services/quotes/base.py:66`, designed-for slot from Phase 7b.1). This phase only wires the actual streamer.

**The cap is enforced at two layers** to close the TOCTOU window between `SubscriptionRegistry` accounting and the upstream Alpaca WS subscribe:

1. **Backend-side soft cap (25)** in `SubscriptionRegistry`. New field `_per_source_refs: dict[str, int]` keyed on the resolved source from `SourceRouter.set_route()`; incremented on 0→1 transition, decremented on 1→0, gated by the existing `self._lock`. New constant `_MAX_SOURCES = 32` caps the dict (Codex pattern D — bounded refcount tables). Rejects new WS subscribe ops upfront with `op:"err", code:"SOURCE_CAP"` before dispatching to the streamer.

2. **Sidecar-side hard cap (30)** in `sidecar_alpaca/streamer.py`. `_upstream_active: set[str]` per endpoint; on Subscribe RPC, if `len(_upstream_active | new_symbols) > 30`, returns `op:"err", code:"SIDECAR_SOURCE_CAP"` for the overflow subset. This catches reconnect-replay races where the registry believed capacity was free but a stale subscribe is still in-flight.

The two-layer design ensures: (a) normal traffic never hits the sidecar cap; (b) reconnect-storm or split-brain races degrade gracefully; (c) the engine's `SourceRouter.reroute()` falls through correctly on either rejection (both error codes route to the same fallthrough branch).

The `cap_kind` label on the existing `quote_subscription_cap_rejected_total{cap_kind=per_ws|global|rate_limit}` metric (verified at `backend/app/services/quotes/registry.py:29`) is **extended**, not split (MED-4): cap_kind takes new value `per_source` for this phase's rejections, with additional labels `source` and `asset_class`. Existing alert `QuoteSubscriptionCapRejection` continues to fire across all cap kinds.

### 4.4 30-symbol cap visibility + drift detection (HIGH-6)

Operator metric `alpaca_subscription_active{endpoint="iex"|"crypto", mode}` — gauge, current symbol count per upstream WS. Alert `AlpacaSymbolCapNear` fires at ≥25 (5-symbol buffer below the documented 30 hard cap).

The 5-symbol buffer covers (a) reconnect-replay window where in-flight ops haven't yet decremented, and (b) operator's manual debug subscriptions via `/ws/quotes` test client.

**Subscribe-rejection detection (HIGH-6)**: Alpaca can silently lower the documented 30-symbol cap. Detection mechanism:

- When Alpaca's WS responds to a `subscribe` action with an error frame OR omits the symbol from the next data frame within 5s, the streamer emits `alpaca_upstream_subscribe_rejected_total{endpoint, reason}` (`reason ∈ {cap_exceeded, entitlement, unknown}`).
- The streamer also returns a backend gRPC error so `SubscriptionRegistry` can decrement its per-source counter (prevents "ghost" subscribed symbols accumulating until reconnect).
- Alert `AlpacaUpstreamSubscribeRejection` (warning, >0 in 5m, 5m): operator's action is to lower the soft cap until matched by Alpaca's actual ceiling, then root-cause via the runbook.

### 4.5 Backend changes (boundary strip — HIGH-2)

**Minimal**:

- `app/brokers/registry.py` — add `alpaca` to broker_id enum / Literal type.
- `app/services/config_defaults.py` (NEW) — holds the v0.7.3 default `quote_source_priority` table with alpaca routes.
- `app/services/quotes/router.py` — consume defaults via merge (HIGH-3 above); no inline change to default semantics.
- Frontend broker picker — `alpaca` appears as a 4th broker option (live/paper).

**`account_id` boundary strip (M22)**:

Alpaca's `Account` proto carries an `account_id` field (UUID, broker-side identifier). Reuse the Phase 7a M22 strip pattern at the same single chokepoint:

1. Extend `AccountResponse` Pydantic model to NOT include `account_id`.
2. `AccountService._resolve_account` already maps FE UUID → (gateway_label, account_number); add Alpaca's `account_id` alongside Schwab's `account_hash` into the internal-only mapping table.
3. Add unit test `backend/tests/api/test_accounts_boundary_strip.py::test_account_response_strips_alpaca_account_id` — asserts no field starting with `alpaca_account_` or matching the UUID hex pattern leaks in the JSON response.

The discoverer's `last_seen_via` path is already covered by the existing M22 invariant (gateway_label is the only broker-side handle stored).

### 4.6 Tests (MED-1, MED-6 — coverage breadth)

- `sidecar_alpaca/tests/test_streamer.py` — IEX subscribe/unsubscribe + crypto subscribe/unsubscribe + cap-hit; mock the upstream WS.
- `sidecar_alpaca/tests/test_streamer_isolation.py` — simulated IEX 5xx storm asserts crypto ticks continue (HIGH-1).
- `sidecar_alpaca/tests/test_streamer_resync.py` — Subscribe (full reconnect) vs Resync (diff-only) contract (CRIT-2).
- `sidecar_alpaca/tests/test_handlers.py` — Configure round-trip; ListManagedAccounts; GetPositions normalize.
- `backend/tests/integration/test_alpaca_routing.py` — exercises the SourceRouter integration for alpaca, **four cases**:
  1. Happy path: `crypto:BTC:US` → alpaca; `stock.US`/`etf.US` still → schwab (alpaca only fallback for equity).
  2. Schwab DOWN → engine reroutes `stock.US` to alpaca.
  3. Both schwab + alpaca DOWN for `stock.US` → engine returns None (no coinbase fallback for equity); operator alert fires.
  4. Per-source soft cap hit (25 alpaca crypto subs already) → 26th subscribe rejected at registry; SourceRouter NOT consulted (cap is post-route guard).
- `backend/tests/integration/test_quote_source_priority_per_key_merge.py` — verifies the per-key fallback merge (HIGH-3).
- `backend/tests/api/test_accounts_boundary_strip.py::test_account_response_strips_alpaca_account_id` — HIGH-2.
- `backend/tests/api/test_alpaca_secrets.py` (MED-6 — full coverage):
  1. Seed `broker.alpaca.default.live.api_key` + `.api_secret` → assert one Configure RPC dispatched to `alpaca-sidecar-live`, ZERO to paper.
  2. Rotate live → assert Configure refires to live only.
  3. Seed paper → assert Configure dispatched to paper only.
  4. Cross-mode pollution probe: with both seeded, mock a "send paper creds to live sidecar" attempt at the registry — assert RPC is refused and `alpaca_mode_mismatch_total{label="alpaca-live"}` increments.

### 4.7 Operator runbook

`deploy/runbook-alpaca-setup.md` — 5 steps + a step 0:

0. **No CF Access bypass needed** (MED-7): unlike Schwab, Alpaca uses long-lived API keys with no OAuth redirect. All sidecar↔Alpaca traffic is outbound from the docker network; nothing on the public callback surface to bypass.
1. Operator generates Alpaca live + paper API keys at `app.alpaca.markets/account/api-keys`.
2. `PUT /api/admin/secrets/broker/alpaca.default.live.api_key` + `.api_secret` (and paper).
3. `docker compose --profile default up -d alpaca-sidecar-live alpaca-sidecar-paper`.
4. Smoke: `GET /api/accounts` shows Alpaca rows; `GET /api/accounts/{id}/positions` returns ≥0 rows.
5. Quote smoke: subscribe `crypto:BTC:US` via `/ws/quotes`, verify ticks within 5s.

## 5. Critical numbers

- **Free tier hard caps** (Alpaca Basic, $0/mo): 30 symbols per WS endpoint (equity, crypto separately), 1 concurrent connection per endpoint, 200 REST calls/min.
- **Soft cap** (backend-side, in `SubscriptionRegistry._per_source_refs`): **25** symbols per upstream — 5-symbol buffer below Alpaca's 30 hard cap.
- **Symbol-cap alert threshold**: `AlpacaSymbolCapNear` at ≥25 (warning), 5min for-duration.
- **Sidecar-side hard cap**: **30** symbols per endpoint in `streamer.py._upstream_active` (Codex pattern D — bounded set; CRIT-1).
- **Reconnect**: bounded exponential `min(2**n, 60)` seconds, full subscription replay only on Subscribe (NOT Resync — CRIT-2).
- **REST rate-limit**: 200/min hard. Discoverer fan-out for ListManagedAccounts/GetPositions runs every 30s — well under limit. Tracked via `alpaca_http_rate_limit_window_seconds` (MED-3).

## 6. Metrics

Add to `backend/app/core/metrics.py`:

| Metric | Type | Labels |
|---|---|---|
| `alpaca_sidecar_uptime_seconds` | Gauge | `mode` (live/paper) |
| `alpaca_quote_ticks_total` | Counter | `endpoint` (iex/crypto), `mode` |
| `alpaca_ws_reconnect_total` | Counter | `endpoint`, `reason` (`ws_close` / `idle` / `error` / `loop_crash` / `subscribe_replay`) |
| `alpaca_subscription_active` | Gauge | `endpoint`, `mode` |
| `alpaca_upstream_subscribe_rejected_total` | Counter | `endpoint`, `reason` (`cap_exceeded`/`entitlement`/`unknown`) — HIGH-6 |
| `alpaca_http_requests_total` | Counter | `endpoint`, `status` |
| `alpaca_http_rate_limit_window_seconds` | Gauge | window=60 — current rolling count (MED-3) |
| `alpaca_http_rate_limit_remaining` | Gauge | from Alpaca response headers if exposed (MED-3) |
| `alpaca_account_read_failures_total` | Counter | `kind` (positions/orders/summary) |
| `alpaca_mode_mismatch_total` | Counter | `label` (`alpaca-live`/`alpaca-paper`) — HIGH-5 cross-mode pollution probe |
| `alpaca_endpoint_isolation_violations_total` | Counter | (HIGH-1 regression detector — never increments in steady state) |

**Existing metric extended (NOT split — MED-4)**: `quote_subscription_cap_rejected_total{cap_kind, source, asset_class}` — `cap_kind` gains new value `per_source` for this phase. SREs continue to alert via the existing `QuoteSubscriptionCapRejection` rule.

## 7. Alerts

Add to `deploy/prometheus/alerts.yml` `phase7c_alpaca` group:

- `AlpacaSymbolCapNear` (warning, ≥25 symbols, 5m) — operator should prune or upgrade.
- `AlpacaWsReconnectFlapping` (warning, >5/15min, 5m).
- `AlpacaHttpErrorRateHigh` (warning, >10% 5xx/429, 10m).
- `AlpacaSidecarDown` (page, uptime=0 for >2min, 1m).
- `AlpacaUpstreamSubscribeRejection` (warning, >0 in 5m, 5m) — HIGH-6 silent-cap-drift detector.
- `AlpacaRestRateLimitWarn` (warning, >150/min sustained for 5m, 5m) — 75% of 200/min budget; review fan-out frequency before adding new RPCs (MED-3).

## 8. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Alpaca's 30-symbol cap silently drops the 31st subscription | Two-layer cap (CRIT-1): backend soft cap at 25 in `SubscriptionRegistry`, sidecar hard cap at 30 in `streamer._upstream_active`; both reject with explicit error codes. |
| Long-lived API keys leaked → unauthorized read | Same Fernet-encrypted `app_secrets` policy as IBKR/Futu/Schwab. Rotation = `POST /api/admin/secrets` + `Configure` retrigger. No log redaction needed (api_key never logged; api_secret never logged). |
| Alpaca paper data differs from live → fake-positive testing | Both modes wired; integration test runs against paper; live smoke is operator step in runbook. |
| Crypto WS endpoint geographic restriction | Doc doesn't mention. If sidecar gets 403 on connect, alert fires. **Until 7b.2 lands, crypto.US has no fallback** — a 403 leaves crypto.US dark; operator must remove the route from `quote_source_priority` to suppress engine errors (LOW-2). |
| `account_id` UUID leaks to FE via discoverer | Single chokepoint at `AccountService._resolve_account` (HIGH-2); `test_accounts_boundary_strip.py` regression-tests no `alpaca_account_*` field or UUID-pattern field reaches the JSON response. |
| Alpaca silently lowers the 30-symbol cap to 20 | Subscribe-rejection detection (HIGH-6): streamer emits `alpaca_upstream_subscribe_rejected_total` and returns gRPC error so registry decrements counter; alert fires; operator lowers soft cap. |
| One WS endpoint failure cancels the other | Per-task supervisor with isolated reconnect loops + exception boundaries (HIGH-1); regression visible via `alpaca_endpoint_isolation_violations_total`. |

## 9. Architectural pillars (carry-forward + Codex routing — MED-5)

- **Sidecar pattern**: Phase 4 IBKR + Phase 6 Futu + Phase 7a Schwab established. New broker = new sidecar dir + 5-trigger Configure + boundary strip.
- **Single source of credentials**: `app_secrets` Fernet-encrypted; never `.env` past bootstrap.
- **Read-only first**: lessons from Phase 5/6/7a — every broker ships read-only first, trade execution in a follow-up phase. Reduces blast radius.
- **Config-driven source-router** from Phase 7b.1: routing changes are config + per-key merge, not code.

**Codex pattern routing per chunk** — each Codex dispatch prompt must inline the relevant pattern verbatim from `codex_defaults.md` (per `feedback_reviewer_spec_inline.md`):

| Chunk | Codex patterns most likely to bite |
|---|---|
| A (proto + registry) | A |
| B (sidecar skeleton) | A, E |
| C (Configure + AlpacaClient) | A, E |
| D (IEX streamer) | A, B, C, D |
| E (crypto streamer extension) | A, B, C, D |
| F (per-source cap + metric) | A, F |
| G (router defaults) | A, F |
| H (compose + tests + runbook) | — |

## 10. Chunk plan (preview — full plan lands in writing-plans phase)

- **A**. proto + broker registry: `alpaca` broker_id, `alpaca-live` / `alpaca-paper` gateway labels, `app_config.broker_gateway_dial` table.
- **B**. `sidecar_alpaca/` skeleton: Dockerfile, pyproject (alpaca-py), main.py + handlers stub returning UNIMPLEMENTED for trade. Per-mode env wiring.
- **C**. Configure RPC + AlpacaClient (read-only REST: ListAccounts/GetPositions/GetSummary/GetOrders) + per-mode credential routing (HIGH-5) + `account_id` boundary strip (HIGH-2).
- **D**. AlpacaStreamer (IEX equity WS) — supervisor + `_iex_loop` task, isolation contract (HIGH-1), Subscribe vs Resync (CRIT-2), tick → QuoteMessage normalize with `source_meta` (LOW-4).
- **E**. AlpacaStreamer extension (crypto WS) — `_crypto_loop` sibling task, same isolation/reconnect contract.
- **F**. SubscriptionRegistry per-source soft cap (CRIT-1 layer 1) + sidecar hard cap (CRIT-1 layer 2) + `cap_kind=per_source` extension on existing metric + drift-detection metric/alert (HIGH-6).
- **G**. SourceRouter default updates via `config_defaults.py` constant + per-key merge (HIGH-3) + frontend broker picker entry.
- **H**. Compose services (live + paper), tests (8 test files including HIGH-1 isolation, CRIT-2 resync, MED-6 secrets full-matrix, HIGH-3 merge, MED-1 routing 4-case), operator runbook with step 0 (MED-7), close-out.

## 11. Forward pointers

- **Phase 7b.2** (coinbase / oanda / yfinance): Coinbase becomes the `crypto.US` fallback to Alpaca. yfinance covers `stock.EU`/`JP`/`AU`/`CA` delayed. OANDA covers `forex.*`. Until 7b.2 lands, crypto.US has no fallback (LOW-2).
- **Phase 8** (trade execution): Alpaca `PlaceOrder` lands alongside Schwab. Alpaca's order shape is the simplest of the four brokers (no bracket/OCO until Phase 13).
- **Phase 12** (options): Alpaca options scaffolding can extend this sidecar; the OPRA WS is its own connection on a separate Alpaca entitlement (LOW-1 — softened from "wire OPRA via the same streamer").

## 12. Deferred

- Multi-Alpaca-account fan-out — schema is forward-compat (MED-2); enable via labeled secret + new Compose service in a future mini-phase.
- OPRA options streaming (Phase 12).
- Algo Trader Plus subscription tier (no payoff while Schwab covers stocks free).
- Crypto fallback to Coinbase (Phase 7b.2).
- crypto.US instrument-seed entries (the Phase 7b.1.5 `seed_instruments_from_positions` helper already exists and runs on lifespan startup; Alpaca-held crypto rows get seeded automatically once positions land — no extra wiring this phase).

## Architect-review findings applied inline

- **CRIT-1** — Two-layer cap (backend soft + sidecar hard); §4.3, §5, §10 chunk F.
- **CRIT-2** — Subscribe vs Resync reconnect contract; §4.1.1, §10 chunk D.
- **HIGH-1** — Per-task isolation supervisor; §4.1, §10 chunk D.
- **HIGH-2** — `account_id` strip chokepoint at `AccountService._resolve_account`; §4.5, §10 chunk C.
- **HIGH-3** — Per-key merge precedence at `ConfigService` load; new `config_defaults.py` module; §3.3, §10 chunk G.
- **HIGH-4** — `app_config.broker_gateway_dial` table for new labeled-docker pattern; §4.2, §10 chunk A.
- **HIGH-5** — Per-mode Configure routing with mode-mismatch metric; §3.2, §10 chunk C.
- **HIGH-6** — Subscribe-rejection detection metric + alert + decrement-on-reject; §4.4, §6, §7.
- **MED-1** — Routing test broadens to 4 cases; §4.6.
- **MED-2** — Forward-compat `<account_label>` schema; §3.2.
- **MED-3** — REST rate-limit metrics + alert; §6, §7.
- **MED-4** — Extend existing `quote_subscription_cap_rejected_total` with `cap_kind=per_source`; §4.3, §6.
- **MED-5** — Codex pattern routing per chunk; §9.
- **MED-6** — `test_alpaca_secrets.py` full-matrix coverage (4 cases incl. cross-mode probe); §4.6.
- **MED-7** — Step 0 in runbook re: no CF Access bypass; §4.7.
- **LOW-2** — Until 7b.2: crypto.US has no fallback; §8 row 4, §11.
- **LOW-3** — 5-buffer rationale; §4.4.
- **LOW-4** — `source_meta` populated by normalize.py, INV-Q-2 stripping; §4.1, §10 chunk D.
- LOW-1 — Phase 12 OPRA pointer softened; §11.

---

**End of design — implementation plan to be written separately via `writing-plans` after user approval.**
