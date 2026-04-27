# Phase 5b — IBKR trade execution — Design

**Date:** 2026-04-27
**Successor of:** v0.5.0 (Phase 5a NLV caching, 2026-04-27)
**Target tag:** v0.5.1
**Estimated duration:** ~2 weeks
**Deferred to 5c:** modify, bracket/OCO, stop-limit, IOC/FOK, fills history page, account-NLV-impact preview, "external orders" tab on /orders, e2e Playwright flow, multi-worker backend (5b is single-worker; horizontal scale = Phase 9).

**Pulled INTO 5b** (architect-review R13 + R39): position-sanity warning at preview, daily-notional cap per account, simulator-mode default-on for live gateways. These are safety controls a real-money trade-execution release must not ship without.

## Goal

Add the **write path** to the broker stack: place market/limit/stop orders, cancel orders, see live status updates. Brand-new-symbol contract search included.

## Architecture

Three RPCs added to `proto/broker/v1/broker.proto`: `PlaceOrder` (unary), `CancelOrder` (unary), `OrderEvent(AccountRef)` (server-streaming). One contract-search RPC: `SearchContracts(query)` (unary). Backend gains an `orders` table + an append-only `order_events` audit table (Alembic 0004). A new `BrokerOrderEventConsumer` runs as a per-`(sidecar, account)` `asyncio.Task` in lifespan (22 streams across 4 sidecars), consumes the OrderEvent stream, INSERTs into `order_events`, UPSERTs `orders` materialized state, and PUBLISHes onto Redis channel `orders:events:<account_id>`. Backend exposes `POST /api/orders/preview` (mints nonce + validates notional cap), `POST /api/orders` (consumes nonce + UUID dedup + forwards to sidecar), `DELETE /api/orders/{id}` (cancel), `GET /api/orders` (list), `GET /api/orders/{id}` (single + events), `GET /api/orders/events` (SSE; subscribes to Redis pubsub fan-out), `GET /api/contracts/search` (autocomplete proxy). Frontend gains a trade-ticket modal, a contract-search autocomplete, and the existing `/orders` page is extended with active-orders + cancel buttons + an EventSource that updates rows as fills land.

**Tech stack:** SQLAlchemy 2.0 async + Alembic + asyncpg + Pydantic v2 (backend); ib_async + grpc.aio (sidecar); React 19 + Zustand + TS strict (frontend).

---

## §1 Scope decisions (locked from brainstorming Q&A)

| Question | Decision |
|---|---|
| Order types | **Market + Limit + Stop** (D from Q1). Stop-Limit deferred to 5c. |
| TIF | **DAY + GTC** (B from Q2). IOC/FOK deferred to 5c. |
| Safety guardrails | **Confirmation token + per-order notional cap + daily kill-switch** (C from Q3). |
| Sidecar surface | **Unary `PlaceOrder` + Unary `CancelOrder` + server-streaming `OrderEvent`** (B from Q4, matches 5a R14). |
| Backend storage | **`orders` table + `order_events` audit table** (C from Q5). |
| Frontend UX | **Modal for placement + extend `/orders` page for active-orders** (B from Q6). |
| Live status transport | **SSE via Redis pubsub fan-out** (B from Q7). |
| Idempotency | **Client-generated `client_order_id` UUID + preview nonce** (D from Q8). |
| Demo-value scope ladder | **A + brand-new-symbol contract picker** (B from Q9). |
| Stream shape | **22 streams (one per `(sidecar, account_number)`)** — chosen in §6 to bound reconnect blast radius per account. |
| Identifier model (post-review) | **Server-side `orders.id` UUIDv7 + frontend `client_order_id` UUID4 scoped per `(account_id, client_order_id)` + IBKR `orderRef` carries `client_order_id` round-trip natively.** See §3 + §6. |
| Backend deployment topology | **Single-worker uvicorn** (not multi-worker gunicorn) for 5b. Documented in §9; multi-worker is Phase 9 work. |
| Lost-order recovery | **Stuck-pending-submit watchdog + startup reconcile + UI "submission state unknown" distinction.** See §4 + §5. |
| Simulator mode | **Default-on for `mode=live` gateways**; operator flips per-gateway via `app_config.broker.<label>.trade_enabled`. See §6 + §10. |

---

## §2 Proto contract additions

Append to `proto/broker/v1/broker.proto`:

```proto
service Broker {
  // ... existing 6 RPCs preserved ...
  rpc PlaceOrder(PlaceOrderRequest) returns (PlaceOrderResponse);
  rpc CancelOrder(CancelOrderRequest) returns (CancelOrderResponse);
  rpc OrderEvent(AccountRef) returns (stream OrderEventMessage);
  rpc SearchContracts(SearchContractsRequest) returns (SearchContractsResponse);
}

message PlaceOrderRequest {
  string account_number = 1;
  string client_order_id = 2;        // UUID4 from frontend; sidecar sets ib_order.orderRef = this
  string conid = 3;                   // resolved by SearchContracts upstream
  string side = 4;                    // "BUY" | "SELL"
  string order_type = 5;              // "MARKET" | "LIMIT" | "STOP"
  string tif = 6;                     // "DAY" | "GTC"
  string qty = 7;                     // decimal-as-string, fixed-point 8 digits
  string limit_price = 8;             // decimal-as-string, "" for MARKET
  string stop_price = 9;              // decimal-as-string, "" unless STOP
  reserved 10 to 20;                  // forward-extension reservations (R35)
}

message PlaceOrderResponse {
  string broker_order_id = 1;         // ib_async permId, populated within ack window
  string status = 2;                  // initial status from ib_async (PendingSubmit etc.)
}

message CancelOrderRequest {
  string account_number = 1;
  string broker_order_id = 2;
}

message CancelOrderResponse {
  bool accepted = 1;
}

message OrderEventMessage {
  string broker_order_id = 1;
  string client_order_id = 2;         // echoed back if known
  string status = 3;                  // PendingSubmit|Submitted|PreSubmitted|Filled|Cancelled|ApiCancelled|Inactive
  string filled_qty = 4;              // decimal-as-string, cumulative
  string avg_fill_price = 5;
  google.protobuf.Timestamp event_at = 6;
  string raw_payload = 7;             // jsonb-friendly debug blob (ib_async Trade snapshot)
}

message SearchContractsRequest {
  string query = 1;                   // symbol fragment, e.g. "AAPL", "0700.HK"
  string asset_class = 2;             // "STK" | "FUT" | "OPT" | "" (any)
}

message SearchContractsResponse {
  repeated Contract contracts = 1;    // existing Contract message reused
}
```

**Invariants:**
- All money/qty fields are decimal-as-string fixed-point 8 digits (R3+R4 wire format from 5a).
- `client_order_id` is the dedup key END-TO-END: backend INSERTs orders row keyed on `(account_id, client_order_id)`; sidecar sets `ib_order.orderRef = client_order_id` so IBKR persists it and echoes it back on every event natively (R5 fix). No fragile in-memory sidecar dedup map needed.
- `OrderEvent` is account-scoped; sidecar filters via `trade.order.account == account_number` (NOT `trade.contract.account` — that field doesn't exist; R4 fix). Test must explicitly cover cross-account leak prevention.
- `SearchContracts` rate-limited at the BACKEND (5 req/sec/user) with 5-min TTL cache; sidecar applies a softer 5 req/sec process-wide ceiling against IBKR's `reqContractDetailsAsync` (R20 fix; old "1 req/sec sidecar" was UX-killing).
- `raw_payload` is a WHITELIST-serialized JSON of the Trade snapshot (R16 fix — `Trade` object has circular refs + Decimal + datetime; naive `json.dumps` corrupts). Sidecar uses a `_serialize_trade(trade) -> dict` helper that explicitly extracts safe fields.

---

## §3 Database schema (Alembic 0004)

```sql
-- enums
CREATE TYPE order_side_enum AS ENUM ('BUY', 'SELL');
CREATE TYPE order_type_enum AS ENUM ('MARKET', 'LIMIT', 'STOP');
CREATE TYPE order_tif_enum AS ENUM ('DAY', 'GTC');
CREATE TYPE order_status_enum AS ENUM (
  'pending_submit',  -- accepted by backend, not yet acked by sidecar
  'submitted',       -- IBKR PendingSubmit / Submitted / PreSubmitted
  'partial',         -- filled_qty > 0 AND filled_qty < qty
  'filled',          -- terminal: filled_qty == qty
  'cancelled',       -- terminal: user-initiated cancel
  'rejected',        -- terminal: broker rejected
  'expired',         -- terminal: DAY order at session close
  'inactive'         -- broker marked Inactive
);

CREATE TABLE orders (
  id                UUID PRIMARY KEY,                    -- server-generated UUIDv7 (NOT frontend-controlled; R2 fix)
  account_id        UUID NOT NULL REFERENCES broker_accounts(id),
  client_order_id   UUID NOT NULL,                       -- frontend-generated UUID4; dedup key
  broker_order_id   TEXT,                                -- IBKR permId; nullable until ack
  conid             TEXT NOT NULL,
  symbol            TEXT NOT NULL,                       -- denormalized for UI
  side              order_side_enum NOT NULL,
  order_type        order_type_enum NOT NULL,
  tif               order_tif_enum NOT NULL,
  qty               NUMERIC(20, 8) NOT NULL,
  limit_price       NUMERIC(20, 8),                      -- NULL for MARKET
  stop_price        NUMERIC(20, 8),                      -- NULL unless STOP
  status            order_status_enum NOT NULL DEFAULT 'pending_submit',
  filled_qty        NUMERIC(20, 8) NOT NULL DEFAULT 0,
  avg_fill_price    NUMERIC(20, 8),
  notional          NUMERIC(20, 8) NOT NULL,             -- qty × price (limit) OR qty × mid × 1.05 (market; R12 slippage buffer)
  notional_filled   NUMERIC(20, 8) NOT NULL DEFAULT 0,   -- filled_qty × avg_fill_price; updated on every fill (R12)
  cancel_requested_at TIMESTAMPTZ,                       -- DELETE idempotency cooldown (R31)
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_event_at     TIMESTAMPTZ,
  CHECK (
    (order_type = 'MARKET' AND limit_price IS NULL AND stop_price IS NULL) OR
    (order_type = 'LIMIT'  AND limit_price IS NOT NULL AND stop_price IS NULL) OR
    (order_type = 'STOP'   AND limit_price IS NULL AND stop_price IS NOT NULL)
  ),
  CHECK (filled_qty >= 0 AND filled_qty <= qty),
  CHECK (qty > 0)
);

-- Composite UNIQUE constraints (R2 + R19 fixes):
CREATE UNIQUE INDEX uq_orders_account_client_order_id ON orders (account_id, client_order_id);
CREATE UNIQUE INDEX uq_orders_account_broker_order_id ON orders (account_id, broker_order_id)
  WHERE broker_order_id IS NOT NULL;

CREATE INDEX ix_orders_account_status ON orders (account_id, status)
  WHERE status NOT IN ('filled', 'cancelled', 'rejected', 'expired');
CREATE INDEX ix_orders_account_created ON orders (account_id, created_at DESC);
CREATE INDEX ix_orders_pending_submit_watchdog ON orders (created_at)
  WHERE status = 'pending_submit';   -- watchdog scan path (R1+R9 fix)

CREATE TABLE order_events (
  id                BIGSERIAL PRIMARY KEY,
  order_id          UUID REFERENCES orders(id),          -- nullable for TWS-placed orders
  account_id        UUID NOT NULL REFERENCES broker_accounts(id),
  broker_order_id   TEXT,
  status            order_status_enum NOT NULL,
  filled_qty        NUMERIC(20, 8),
  avg_fill_price    NUMERIC(20, 8),
  broker_event_at   TIMESTAMPTZ NOT NULL,
  observed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_payload       JSONB
);
CREATE INDEX ix_order_events_order_id ON order_events (order_id, broker_event_at DESC);
CREATE INDEX ix_order_events_account ON order_events (account_id, broker_event_at DESC);
```

**Invariants (post-architect-review):**
- `orders.id` is server-generated UUIDv7 (insert-ordered, never frontend-controlled — R2 fix).
- `(account_id, client_order_id)` is the dedup key. Cross-account collision returns 422 not the foreign row.
- `(account_id, broker_order_id)` is UNIQUE-when-present (R19 fix); cancel queries always filter on both.
- `orders.broker_order_id` populated by first OrderEvent (echoed back via IBKR `orderRef` round-trip — R5 fix).
- `order_events.order_id` nullable so TWS-placed/external orders' events are recorded as audit-only rows (R18 explicit).
- Active-orders index is partial — keeps it tiny.
- `notional` denormalized at insert time; for MARKET orders includes 5% slippage buffer (R12 fix).
- `notional_filled` updated on every fill so UI can show "Order notional (max)" vs "Filled notional" distinctly (R12 fix).
- `raw_payload` JSONB for `jsonb_path_query` debugging — sidecar produces this via WHITELIST serialization (R16 fix).
- Pending-submit watchdog index supports the recovery scan in §5.

---

## §4 Backend API surface

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/orders/preview` | Mint nonce + validate notional cap |
| POST | `/api/orders` | Consume nonce + place order |
| GET | `/api/orders` | List active by default; `?status=` filter |
| GET | `/api/orders/{id}` | Single order with events |
| DELETE | `/api/orders/{id}` | Cancel; status arrives via stream |
| GET | `/api/orders/events` | SSE; live order-event push |
| GET | `/api/contracts/search` | Autocomplete proxy to sidecar |
| GET | `/api/orders/policy/{account_id}` | Per-account safety policy (caps + position context for preview) |

### Pydantic shapes

```python
class PreviewRequest(BaseModel):
    account_id: UUID
    conid: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT", "STOP"]
    tif: Literal["DAY", "GTC"]
    qty: str = Field(pattern=r"^\d+(\.\d{1,8})?$")
    limit_price: str | None = Field(default=None, pattern=r"^\d+(\.\d{1,8})?$")
    stop_price: str | None = Field(default=None, pattern=r"^\d+(\.\d{1,8})?$")

class PreviewResponse(BaseModel):
    nonce: str
    notional: str                     # for MARKET: qty × mid × 1.05 (slippage buffer); LIMIT/STOP: qty × price (R12)
    notional_currency: str            # ISO-3 (= account currency_base; FX-converted via IBKR mid-rate cached 1h — R24)
    notional_filled_today: str        # daily cap context
    daily_notional_cap: str           # config: app_config.broker.<account>.daily_notional_cap
    max_notional_per_order: str
    cap_status: Literal["ok", "near", "exceeded"]   # near = >80%
    daily_cap_status: Literal["ok", "near", "exceeded"]
    position_sanity: PositionSanityResult           # R13: position-sanity check
    contract_summary: ContractSummary
    warnings: list[str]
    # NB: every check is rerun in POST /orders too (R29 — RTH may have changed).

class PositionSanityResult(BaseModel):
    current_qty: str                  # 0 for new symbol
    new_qty_after_fill: str            # current_qty + qty (for BUY) or current_qty - qty (for SELL)
    sanity_multiplier: str             # new_qty_after_fill / max(current_qty, 1)
    status: Literal["ok", "high", "extreme"]   # > 5x = high, > 10x = extreme
    requires_extra_attestation: bool   # true when status == "extreme"

class PlaceOrderRequest(BaseModel):
    client_order_id: UUID
    nonce: str
    # ... rest mirrors PreviewRequest ...

class OrderResponse(BaseModel):
    id: UUID
    account_id: UUID
    broker_order_id: str | None
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT", "STOP"]
    tif: Literal["DAY", "GTC"]
    qty: str                           # fixed-point 8 digits per 5a wire format
    limit_price: str | None
    stop_price: str | None
    status: OrderStatusEnum
    filled_qty: str
    avg_fill_price: str | None
    notional: str
    created_at: datetime
    updated_at: datetime
    last_event_at: datetime | None
    events: list[OrderEvent] = []      # included on /{id}; omitted from list

class OrderListResponse(BaseModel):
    orders: list[OrderResponse]
    broker_maintenance: BrokerMaintenance  # mirrors 5a AccountListResponse
    kill_switch_active: bool
```

### Endpoint behaviour

- **`POST /preview`** — order matters (R6: kill-switch first, then nonce mint last):
  1. check `app_config.broker.kill_switch_enabled` → 503 + `kill_switch_active` if on
  2. check `compute_broker_maintenance(now).active` → 503 + Retry-After if active
  3. validate Pydantic; canonicalize `qty` string (R33 — strip leading zeros) before any hashing
  4. resolve contract via cached `SearchContracts(conid)`
  5. compute notional: LIMIT/STOP from `limit_price`; MARKET from sidecar's last MID × 1.05 slippage buffer (R12)
  6. FX-convert to account `currency_base` via cached IBKR mid-rate (R24)
  7. lookup `app_config.broker.<account>.{max_notional_per_order, daily_notional_cap}`
  8. SUM today's `notional` from `orders WHERE account_id=:a AND created_at > date_trunc('day', now()) AND status NOT IN ('cancelled','rejected')` for daily-cap context
  9. compute `position_sanity` (R13) — SELECT current `qty FROM positions WHERE account_id=:a AND conid=:c`; classify `multiplier = (current+new)/max(current,1)`
  10. mint UUID nonce in Redis under `nonce:order:<account>:<nonce>` (TTL 30s, payload-bound to canonicalized request) so POST can't smuggle different fields
  11. rate-limited 10/min/user

- **`POST /orders`** — order matters (R6 + R17):
  1. validate Pydantic; canonicalize `qty` (R33)
  2. **kill-switch check FIRST** (R6) → 503 if on (don't waste the nonce)
  3. **maintenance window check** → 503 + Retry-After
  4. **re-evaluate RTH/warnings** vs preview (R29) — if `outside_rth` flipped, return 422 with re-preview prompt
  5. `GETDEL` Redis nonce — must exist AND match canonicalized payload
  6. INSERT atomic flow (R17 — write the path explicitly):
     ```python
     row_id = uuid7()
     await session.execute(text("""
       INSERT INTO orders (id, account_id, client_order_id, ...)
       VALUES (:id, :account_id, :client_order_id, ...)
       ON CONFLICT (account_id, client_order_id) DO NOTHING
       RETURNING *
     """), params)
     row = result.first()
     if row is None:
       row = await session.execute(text("""
         SELECT * FROM orders
         WHERE account_id=:account_id AND client_order_id=:client_order_id
       """)).first()
       if row is None:
         raise HTTPException(422, "order state inconsistent — retry preview")
       return row_to_response(row, state="idempotent_retry")  # 200
     ```
  7. forward `PlaceOrderRequest` to sidecar via `BrokerSidecarClient.place_order` (with simulator-mode check — R39)
  8. on sidecar 503/timeout (R1 lost-order recovery):
     - orders row stays `pending_submit`
     - return OrderResponse with `submission_state: "pending_unknown"` (distinct from sidecar-acked `submitted`)
     - frontend renders "Submission state unknown — check broker terminal"
     - the watchdog (§5) will reconcile within 60s
  9. on sidecar success: returns OrderResponse status `submitted` with `submission_state: "submitted"`
  10. final defense-in-depth: if kill-switch is now ON post-sidecar-success, attempt `CancelOrder` immediately (best-effort recovery — R6)

- **`DELETE /orders/{id}`** — idempotent (R31):
  1. load order WITH row lock (`SELECT ... FOR UPDATE`)
  2. if terminal status → 409 already-finalized
  3. if `cancel_requested_at` set within last 5s → 202 "cancel already in flight"
  4. else: SET `cancel_requested_at = now()`; forward `CancelOrderRequest((account_number, broker_order_id))`; return 202
  5. status update arrives via stream
  6. if cancel races a partial fill (R15), enum extension covers `cancelled` post-`partial` (status models cancelled-after-partial as `cancelled` with `filled_qty < qty`)

- **`GET /orders/policy/{account_id}`** (new) — returns:
  ```json
  {
    "account_id": "...",
    "max_notional_per_order": "10000.00000000",
    "daily_notional_cap": "50000.00000000",
    "notional_filled_today": "12340.00000000",
    "trade_enabled": true,                  // operator-flippable
    "simulator_only": false,                // operator-flippable; default true for live
    "position_count": 4
  }
  ```
  Frontend hits this once when opening the trade modal to render the cap context BEFORE preview.

- **`GET /orders/events`** — SSE (R10 + R25 fixes):
  - query param `?account_id=<uuid>` (optional); if present, subscribes to `orders:events:account:<id>`; if absent, subscribes to `orders:events:fleet`
  - heartbeat every **10s** (was 15s; CF Tunnel idle-close threshold)
  - response headers: `Content-Type: text/event-stream; charset=utf-8`, `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`, `Connection: keep-alive`
  - emits `id: <BIGSERIAL>\nevent: order.update\ndata: <OrderResponse JSON>\n\n` so frontend EventSource can resume via `Last-Event-ID` after reconnect
  - on resume: replay events from `order_events` where `id > Last-Event-ID` for the requesting scope before tailing live
  - closes on client disconnect or backend shutdown
  - nginx config snippet (defense-in-depth; CLAUDE.md mentions nginx as DiD layer): `location /api/orders/events { proxy_buffering off; proxy_cache off; proxy_read_timeout 86400; proxy_set_header Connection ''; chunked_transfer_encoding off; }`

- **`GET /contracts/search`** — query param `q` + optional `asset_class`; forwards to one healthy sidecar's `SearchContracts`; caches in Redis `contracts:search:<sha256(q,class)>` 5-min TTL; rate-limited 5 req/sec/user.

### Maintenance + kill-switch + simulator + per-account trade gate

All mutating endpoints check, in this order (R6 fix):
1. `kill_switch_active` (`app_config.broker.kill_switch_enabled`) → 503 `{"error":"kill_switch_active"}`
2. `compute_broker_maintenance(now)` (5a helper) → 503 + Retry-After + envelope
3. per-account `app_config.broker.<label>.trade_enabled` (default `false` for new accounts, must be operator-flipped after canary) → 503 `{"error":"trade_disabled_for_account"}`

Per-account `app_config.broker.<label>.simulator_only` (R39 fix; default `true` for any `mode=live` gateway): when true, sidecar logs the order intent + returns a simulated `broker_order_id = "SIM-<uuid7>"` + status `Submitted` but does NOT call `self._ib.placeOrder()`. Backend writes the orders row normally; SSE delivers a synthetic event chain. Operator flips to false explicitly per-gateway after canary verification.

### Boundary stripping (5a R12)

`OrderResponse` exposes only `account_id` (UUID), never `gateway_label`/`account_number`. `AccountService._resolve_account` translates when forwarding to sidecar.

---

## §5 BrokerOrderEventConsumer

Per-`(sidecar, account_number)` event consumer, mirroring `BrokerDiscoverer`'s lifecycle pattern. New module: `backend/app/services/order_event_consumer.py`.

### Lifecycle

- **Lifespan**: after `build_broker_registry()` succeeds, start one supervisor task per sidecar; supervisor enumerates accounts and spawns child tasks per `(sidecar, account_number)` pair (~22 child tasks total).
- **Per-child loop**: open `OrderEvent(AccountRef)` server-stream; consume events; on error, exponential-backoff reconnect (1s → 30s cap); BEFORE re-consuming, run `GetOrders` resync to emit synthetic events for any transitions missed during downtime.
- **Per-event handler** (`_process_event`) — order matters (R11 + R23 fixes):
  1. INSERT into `order_events` (always succeeds, includes events for orders we didn't place — R18 audit-only path).
  2. UPSERT `orders` materialized state, but ONLY apply if event is newer than current state:
     ```sql
     UPDATE orders SET
       status = CASE
         WHEN orders.status IN ('filled','cancelled','rejected','expired')
           THEN orders.status                              -- terminal sticks (R11)
         ELSE :new_status
       END,
       broker_order_id = COALESCE(orders.broker_order_id, :broker_order_id),
       filled_qty = GREATEST(orders.filled_qty, :filled_qty),
       avg_fill_price = :avg_fill_price,
       notional_filled = :filled_qty * :avg_fill_price,    -- R12 fix
       last_event_at = GREATEST(orders.last_event_at, :broker_event_at),  -- R23 fix
       updated_at = now()
     WHERE account_id = :account_id
       AND client_order_id = :client_order_id              -- echoed via orderRef (R5)
       AND :broker_event_at >= COALESCE(orders.last_event_at, '-infinity'::timestamptz);
     ```
     If the event has no `client_order_id` (TWS-placed; R18) → no UPSERT, audit-only.
  3. PUBLISH OrderResponse JSON to Redis pubsub on `orders:events:fleet` AND `orders:events:account:<id>`. Include `id: <order_events.id>` so SSE can use it as Last-Event-ID.
  All three steps wrapped in `session.begin_nested()` savepoint; per-event failure logs + counter, doesn't poison the consumer.
- **`asyncio.Lock` re-entrancy guard** on the supervisor's iteration boundary (mirrors 5a discoverer C1).
- **Supervisor re-enumeration cadence (R8 fix):** supervisor subscribes to discoverer's account-add/remove notifications via `BrokerRegistry.account_changed` events; on add, spawns a new child stream task; on remove (soft-delete), cancels the corresponding child cleanly. Also re-enumerates every 60s as a safety belt-and-suspenders. Adds `test_consumer_handles_account_added_mid_run` and `test_consumer_handles_account_removed_mid_run`.
- **Single-worker invariant (R7 fix):** backend runs **single-worker uvicorn** for 5b. Documented in §9. Multi-worker support requires Redis SETNX leader election on `consumer:lease:<sidecar>:<account>` keys with 60s TTL renewed every 30s; this is Phase 9 work. CI test asserts gunicorn worker count == 1 in `docker-compose.prod.yml`.
- **Cancellation discipline**: on lifespan teardown, supervisor cancels all child tasks; each child catches `CancelledError`, closes the gRPC stream cleanly, returns. uvicorn `--timeout-graceful-shutdown 30` lets in-flight POST /orders complete (R9 fix). Tested via the same lifespan-shutdown test pattern as Phase 4.

### Stuck-pending-submit watchdog (R1 + R9 fix)

A second per-account asyncio task (`PendingSubmitWatchdog`) runs alongside the OrderEvent consumer. Every 30s it queries:
```sql
SELECT id, account_id, client_order_id, broker_order_id, created_at
FROM orders
WHERE status = 'pending_submit'
  AND created_at < now() - interval '60s'
ORDER BY created_at;
```
For each stuck row, it calls `BrokerSidecarClient.GetOrders(account_number)` and matches by `orderRef = client_order_id` (the field IBKR round-trips). If the order is found at the broker:
- emit a synthetic OrderEvent through the normal `_process_event` path (which UPDATEs `broker_order_id` + `status`)
- Prometheus: `broker_order_pending_submit_recovered_total{label}`

If NOT found at broker after 5 minutes:
- transition the row to `rejected` with `raw_payload.recovery_outcome = "broker_no_match_after_5min"`
- alert: `broker_order_pending_submit_orphan_total{label}`
- frontend renders the row with status `rejected` + tooltip "Submission lost; if the broker shows this order, contact the operator."

### Startup reconciliation (R9 fix)

On lifespan startup, before starting consumer tasks, run a one-shot reconcile pass:
```sql
SELECT account_id, client_order_id FROM orders WHERE status = 'pending_submit' AND created_at < now() - interval '60s';
```
For each row, call `GetOrders(account_number)` and resolve the same way the watchdog does. Eliminates the "backend bounced mid-order" gap.

### Reconnect-and-resync (R11 fix)

On every (re)connect, BEFORE consuming the live stream:
1. Open the gRPC stream and immediately put incoming events into a buffer queue (don't process yet).
2. Call `GetOrders(account)` and emit synthetic events for every transition the orders table doesn't already have, by sending them into `_process_event` in `broker_event_at` order.
3. Drain the buffer queue through `_process_event` (now post-resync; UPSERT logic from §5 step 2 prevents older synthetic events from overwriting newer live events because of `:broker_event_at >= last_event_at` predicate).
4. Continue tailing the live stream normally.

Synthetic events have `observed_at = now()` but `broker_event_at` from the snapshot. The buffer-then-drain pattern ensures the live stream is never lost during resync, and the UPDATE predicate ensures out-of-order delivery doesn't revert terminal status.

### Prometheus metrics

- `broker_order_events_received_total{label}` — counter
- `broker_order_events_dropped_total{label, reason}` — counter
- `broker_order_event_lag_ms` — histogram (broker_event_at to observed_at)
- `broker_order_stream_reconnects_total{label}` — counter
- `broker_order_stream_resync_synthetic_events_total{label}` — counter
- `broker_order_pending_submit_recovered_total{label}` — counter (watchdog R1)
- `broker_order_pending_submit_orphan_total{label}` — counter (watchdog 5-min escalation R1)
- `consumer_alive{label, account_id}` — gauge (R27 health alert)
- `sse_active_connections` — gauge (R26 capacity awareness)
- Alert: `rate(broker_order_events_dropped_total[5m]) > 0.5 * rate(broker_order_events_received_total[5m])` — pages on consumer poisoning (R27)

---

## §6 Stream-shape decision: 22 streams

**Decision: one stream per `(sidecar, account_number)`.** Rationale:
- **Per-account reconnect surface** — one bad account stalls only its own stream, not 5 sibling accounts.
- **Clean handover from 5a's discover** — discoverer enumerates the 22 accounts every 30s; consumer subscribes to add/remove and spawns/cancels child tasks.
- **Sidecar simplicity** — `OrderEvent(AccountRef)` filters at the source via ib_async's `Trade.contract.account` field.
- **Idle cost negligible** — 22 idle gRPC streams over a multiplexed HTTP/2 connection = ~22KB total per sidecar. We're already paying the TCP cost.

### Sidecar `handlers.py` additions (post-architect-review)

```python
class BrokerHandlers:
    _place_locks: dict[str, asyncio.Lock]  # keyed by client_order_id (R3 fix)

    async def PlaceOrder(self, request, context):
        # Simulator-mode short-circuit (R39 fix)
        if self._simulator_only:
            sim_id = f"SIM-{uuid7()}"
            log.info("place_order_simulated", client_order_id=request.client_order_id, sim_id=sim_id)
            return broker_pb2.PlaceOrderResponse(broker_order_id=sim_id, status="Submitted")

        # Per-client_order_id lock prevents concurrent-double-place race (R3)
        async with self._place_locks.setdefault(request.client_order_id, asyncio.Lock()):
            # Dedup: scan ib.openTrades() AND ib.trades() for matching orderRef
            # (orderRef is the IBKR-blessed round-trippable field — R5)
            for trade in self._ib.trades():
                if trade.order.orderRef == request.client_order_id:
                    return broker_pb2.PlaceOrderResponse(
                        broker_order_id=str(trade.order.permId),
                        status=trade.orderStatus.status,
                    )

            contract = await self._resolve_contract(request.conid)
            ib_order = self._build_ib_order(request)  # MarketOrder/LimitOrder/StopOrder
            ib_order.orderRef = request.client_order_id  # CRITICAL — round-trips via IBKR (R5)
            ib_order.account = request.account_number    # explicit (don't rely on default)
            trade = self._ib.placeOrder(contract, ib_order)
            return broker_pb2.PlaceOrderResponse(
                broker_order_id=str(trade.order.permId),
                status=trade.orderStatus.status,
            )

    async def CancelOrder(self, request, context):
        # Filter by BOTH account_number AND broker_order_id (R19 defense-in-depth)
        for trade in self._ib.openTrades():
            if (trade.order.permId == int(request.broker_order_id)
                and trade.order.account == request.account_number):
                self._ib.cancelOrder(trade.order)
                return broker_pb2.CancelOrderResponse(accepted=True)
        return broker_pb2.CancelOrderResponse(accepted=False)

    async def OrderEvent(self, request, context):
        # Bounded queue prevents OOM on slow consumer (R30)
        queue: asyncio.Queue[broker_pb2.OrderEventMessage] = asyncio.Queue(maxsize=10_000)

        def _on_status(trade):
            # Filter on trade.order.account, NOT trade.contract.account (R4 — that field doesn't exist!)
            if trade.order.account != request.account_number:
                return
            try:
                queue.put_nowait(self._proto_event_from_trade(trade))
            except asyncio.QueueFull:
                # Drop + counter; sidecar must NEVER OOM
                metrics.broker_order_events_dropped_total.labels(reason="queue_full").inc()

        self._ib.orderStatusEvent += _on_status
        self._ib.execDetailsEvent += _on_status
        try:
            while not context.cancelled():
                yield await queue.get()
        finally:
            self._ib.orderStatusEvent -= _on_status
            self._ib.execDetailsEvent -= _on_status

    async def SearchContracts(self, request, context):
        # Backend rate-limits per-user (5 req/sec); sidecar process-wide 5 req/sec (R20)
        # Cache by (query, asset_class) 5-min TTL.
        ...

    @staticmethod
    def _serialize_trade(trade) -> dict:
        """Whitelist serialization for raw_payload (R16 fix). NEVER pass raw Trade
        to JSON — it has circular refs, Decimals, datetimes."""
        return {
            "perm_id": trade.order.permId,
            "order_ref": trade.order.orderRef,
            "account": trade.order.account,
            "status": trade.orderStatus.status,
            "filled": str(trade.orderStatus.filled),
            "remaining": str(trade.orderStatus.remaining),
            "avg_fill_price": str(trade.orderStatus.avgFillPrice or 0),
            "last_fill_price": str(trade.orderStatus.lastFillPrice or 0),
            "why_held": trade.orderStatus.whyHeld or "",
            "log": [
                {"time": e.time.isoformat(), "status": e.status, "message": e.message,
                 "error_code": e.errorCode}
                for e in trade.log
            ],
        }
```

### Idempotency at the sidecar (post-architect-review)

- `client_order_id` is the dedup key END-TO-END via IBKR's `orderRef` field — IBKR persists it on the order and echoes it back on every event natively (R5 fix). No fragile in-memory map needed.
- Concurrent PlaceOrder protection via per-`client_order_id` `asyncio.Lock` (R3 fix).
- Restart safety: sidecar restart loses the lock dict, but the dedup key is now in IBKR's order book — `ib.trades()` scan finds it on retry. The backend's `ON CONFLICT (account_id, client_order_id) DO NOTHING` is the second line of defense.

### TWS-placed / external orders

ib_async emits status events for ALL orders on the account, including TWS-placed. OrderEvent forwards them with `client_order_id = ""`. Backend records them in `order_events` with `order_id = NULL`. UI surfaces them in an "external orders" tab on `/orders` (low priority — can defer to 5c if needed).

### Reset-window interaction

Streams stay open during IBKR maintenance windows BUT events stop arriving (gateway disconnects). Sidecar emits a `Disconnected` synthetic event the consumer logs to audit. Pre-maintenance orders keep their `submitted` status; on reconnect, GetOrders resync catches up.

---

## §7 Frontend — trade ticket modal + extended Orders page

### File layout

```
frontend/src/features/orders/
  OrdersPage.tsx                 (extend — add active-orders table + cancel + SSE)
  OrdersPage.test.tsx            (extend)
  OrdersPage.stories.tsx         (extend)
  TradeTicketModal.tsx           (new — placement UI)
  TradeTicketModal.test.tsx      (new)
  TradeTicketModal.stories.tsx   (new)
  ContractSearchInput.tsx        (new — autocomplete)
  ContractSearchInput.test.tsx   (new)
  ContractSearchInput.stories.tsx (new)
  use-trade-ticket.ts            (new — Zustand slice for in-flight ticket state)
  use-orders-stream.ts           (new — SSE subscription hook)

frontend/src/services/orders.ts  (extend — placeOrder, cancelOrder, previewOrder, searchContracts)
frontend/src/services/types.ts   (extend — Order, OrderEvent, ContractSummary types)
frontend/src/stores/global/
  orders.ts                      (new — Zustand: orders by id, last_event_at, kill_switch_active)
  orders.test.ts                 (new)
frontend/src/hooks/
  useOrdersList.ts               (new — composes service + store, parallel to useAccountsList.ts)
```

### TradeTicketModal flow

1. **Trigger** — "Trade" button in AccountPicker row OR positions table opens the modal pre-populated with `account_id` (and `conid`+`symbol` if launched from a position row).
2. **Form layout** (Tailwind grid, mobile-first):
   - Side toggle (BUY/SELL segmented control)
   - Contract field (`ContractSearchInput` autocomplete, debounce 300ms)
   - Order type dropdown (MARKET/LIMIT/STOP)
   - Qty numeric input (decimal-as-string, validated > 0)
   - Limit price (only when LIMIT)
   - Stop price (only when STOP)
   - TIF dropdown (DAY/GTC)
3. **Preview button** (disabled until form valid) — `POST /preview` → opens inline confirm panel:
   - Notional, formatted with `nlv_currency`
   - Cap status: ✅ ok / ⚠ near (>80%) / ❌ exceeded (Confirm disabled if exceeded)
   - Account NLV reference (from existing `useAccountsScoped`)
   - Warnings list ("outside RTH", "first time trading this symbol")
   - Confirmation checkbox: "I have reviewed the trade details" (required; explicit-attestation invariant)
4. **Confirm button** — generates `client_order_id = crypto.randomUUID()`, calls `POST /orders` with `{nonce, client_order_id, ...}`. On 200, closes modal + emits toast linking to `/orders/{id}`.
5. **Error handling**:
   - 503 broker maintenance → "Broker maintenance" with `Retry-After` countdown
   - 503 kill-switch → red banner "Trading paused by operator"
   - 429 → rate-limit toast
   - 409 (duplicate `client_order_id`) → silently treat as success (idempotent retry)

### OrdersPage extension

- **Active-orders table** (top): ID, symbol, side, qty, type, status, filled_qty, avg_fill_price, [Cancel] button (disabled for terminal-status rows).
- **Recent history** (bottom): same columns, terminal statuses only, paginated.
- **SSE subscription**: `useOrdersStream()` opens `EventSource('/api/orders/events')` on mount; pipes events into `useOrdersStore` Zustand slice; UI auto-updates within ~50ms.
- **Cancel flow**: button → confirm modal → `DELETE /api/orders/{id}` → toast on success, status update via SSE.
- **Kill-switch banner**: sticky red banner across `/orders` while `kill_switch_active=true`.

### ContractSearchInput

- Native HTML `<input role="combobox">` + popover listbox (Phase 3 a11y patterns); aria attributes wired correctly.
- Debounced 300ms; AbortController on new keystroke.
- Result row: `<symbol> · <exchange> · <asset_class>` — selecting populates parent's `conid` + `symbol`.
- Empty/error states: "No matches", "Search failed; retry".
- Local memo for the modal's lifetime.

### Boundary discipline (5a pattern)

- `services/orders.ts` returns pure data `{ orders, brokerMaintenance, killSwitchActive }`; never imports from `stores`.
- `frontend/src/hooks/useOrdersList.ts` composes service + maintenance/kill-switch publish to global store (parallel to `useAccountsList.ts`).

### Stories

- `OrdersPage.stories.tsx`: `Empty`, `WithActiveOrders`, `WithFilledHistory`, `KillSwitchActive`, `MaintenanceWindow`.
- `TradeTicketModal.stories.tsx`: `Empty`, `LimitOrderValid`, `MarketOrderValid`, `StopOrderValid`, `CapNearWarning`, `CapExceeded`, `MaintenanceBlocked`, `KillSwitchBlocked`.
- `ContractSearchInput.stories.tsx`: `Empty`, `LoadingResults`, `WithResults`, `NoMatches`, `RateLimited`.

### Mobile

Modal full-screen below `md` breakpoint with bottom-sheet feel; price inputs use `inputmode="decimal"` for iOS numeric keyboard.

---

## §8 Testing surface

### Backend (`backend/tests/`)

- **`tests/migrations/test_0004.py`** (~6 tests): enums created; CHECK constraints reject impossible state; partial index exists; FK cascades.
- **`tests/services/test_orders.py`** (~16 tests, outer-rollback fixture): same `(account_id, client_order_id)` returns existing row; cross-account same `client_order_id` returns 422 not foreign row (R2); concurrent place race produces exactly one row (R3+R41); preview-then-place consumes nonce; nonce reused → 409; nonce vs different payload → 422; preview cap `near` at 81%, `exceeded` at 101%; daily-cap warning + reject (R13); position-sanity high (>5x) and extreme (>10x) classifications (R13); kill-switch FIRST then nonce (R6 ordering); maintenance → 503 + Retry-After; RTH change between preview and POST → 422 (R29); cancel terminal → 409; cancel idempotent within 5s window (R31); cancel forwarded with `(account_number, broker_order_id)` filter (R19); OrderResponse strips `gateway_label`; partial-then-cancel models correctly (R15).
- **`tests/services/test_pending_submit_watchdog.py`** (~6 tests, R1 fix): row stuck >60s + GetOrders matches → recovered to `submitted`; row stuck >60s + GetOrders doesn't match → still pending; row stuck >5min + still no match → escalated to `rejected` with raw_payload.recovery_outcome; startup reconciliation runs the same pass; Prometheus counters increment correctly.
- **`tests/services/test_order_event_consumer.py`** (~14 tests, fake gRPC stream): single event → INSERT order_event + UPSERT orders + PUBLISH; partial-fill updates filled_qty + avg_fill_price + notional_filled (R12); terminal sets status; out-of-order events don't revert terminal status (R11); UPSERT with stale `broker_event_at` is no-op (R23); malformed event → savepoint rollback + counter; reconnect resyncs FIRST then drains buffer (R11); resync emits synthetic events for missed transitions; stream cancellation closes cleanly; account-added spawns new child (R8); account-removed stops child cleanly (R8); one stream death doesn't affect siblings; circuit breaker fires after 50 consecutive failures (R27); TWS-placed event has `client_order_id=""` → audit-only, no `orders` row (R18).
- **`tests/api/test_orders.py`** (~18 tests, dep-override stubs): preview returns nonce + cap + warnings + position_sanity (R13); place consumes nonce; place returns `submission_state="submission_pending"` on sidecar timeout (R1); place dedup returns `submission_state="idempotent_retry"`; cancel idempotent; list filters; GET /orders/policy/{account_id} returns the cap context; OpenAPI shape locked for OrderResponse + OrderListResponse + PreviewResponse + ContractSummary + PolicyResponse; SSE emits formatted events with `id:` field (R10); SSE heartbeat at 10s (R10); SSE supports Last-Event-ID resume (R10); SSE scoped subscription delivers only relevant events (R25); SSE closes on disconnect.
- **`tests/api/test_simulator_mode.py`** (~3 tests, R39): default `simulator_only=true` for live gateways; sidecar returns `SIM-<uuid>` broker_order_id; backend persists normally; SSE delivers synthetic event chain.
- **`tests/api/test_contracts.py`** (~4 tests): forward to sidecar; cache hits; rate-limit 429; sidecar 503 propagates.

### Sidecar (`sidecar/tests/`)

- **`test_handlers_orders_contract.py`** extension (~16 new tests, FakeIB): PlaceOrder MARKET/LIMIT/STOP each builds correct ib order class; PlaceOrder sets `ib_order.orderRef = client_order_id` (R5); PlaceOrder per-client_order_id lock prevents concurrent double-place (R3); restarted sidecar finds existing order via `ib.trades()` orderRef scan (R3); simulator mode returns `SIM-<uuid>` without calling `placeOrder()` (R39); CancelOrder filters by BOTH account + broker_order_id (R19); OrderEvent filters on `trade.order.account` (R4 — not `trade.contract.account`); cross-account events do NOT leak (R4 explicit); OrderEvent queue bounded at 10K + drops on overflow (R30); OrderEvent emits status + fill; `_serialize_trade` handles circular refs + Decimals + datetimes safely (R16); SearchContracts caches; SearchContracts rate-limits at 5/sec (R20); SearchContracts forwards asset_class; OrderEvent handles ib disconnect with synthetic Disconnected event.
- **`tests/test_real_ibkr_smoke.py`** extension (~3 new tests, paper 4002, `clientId=998` per R37 registry): place tiny LIMIT @ 0.01 well below market (DAY-only — won't fill, expires at session close per R43), assert PendingSubmit/Submitted ≤5s; cancel same order, assert ApiCancelled ≤5s; OrderEvent stream receives both events with matching `orderRef`. Session-fixture cleanup runs `ib.reqGlobalCancel()` AND scans + cancels any orderRef-prefixed leftovers on entry AND exit, regardless of test failure (R43). Idempotent.

### Frontend (`frontend/src/`)

- **`services/orders.test.ts`** (~12 tests): preview → wire shape; place generates client_order_id; cancel POSTs DELETE; search debounces; service-side error mapping for 503/429/409.
- **`stores/global/orders.test.ts`** (~6 tests): order added by SSE event; status updated; terminal-status filtered out; kill_switch_active toggle.
- **`features/orders/{TradeTicketModal,OrdersPage,ContractSearchInput}.test.tsx`** (~18 tests):
  - TradeTicketModal: form validation per type; preview disabled while invalid; cap-exceeded disables Confirm; nonce-bound retry; mobile breakpoint full-screen; idempotency on double-click.
  - OrdersPage: cancel disabled for terminal; SSE event triggers row update; kill-switch banner; maintenance banner.
  - ContractSearchInput: 300ms debounce; abort in-flight; aria-combobox attrs; selecting populates parent.

### Coverage gates

- Backend `app/` ≥80%; Sidecar `sidecar/` ≥80%; frontend typecheck + lint + 100% pass.

### Test-isolation discipline (5a lessons)

- Backend integration tests use outer-rollback `session` fixture + `s.begin_nested()` savepoints (per `feedback_pytest_session_begin_commits.md`).
- `test_admin_api.py` `clean_tables` autouse extends to include `orders` + `order_events` (per `feedback_pytest_prod_db_wipe.md` safety guard).
- Real_ibkr smoke uses `clientId=998` (different from G1's `999` to avoid collision).

---

## §9 Migration sequencing + deployment topology

- **Alembic 0004** (orders + order_events tables + 4 enums + composite UNIQUE constraints) MUST land + be applied to prod BEFORE the backend code that reads/writes them is deployed. Same gate as 5a's 0003.
- **Proto contract additions** (PlaceOrder/CancelOrder/OrderEvent/SearchContracts) ship as a coordinated trio: proto → sidecar → backend. CI's `proto` job runs `buf lint` + `buf format --diff --exit-code`; sidecar + backend depend on it.
- **Lifespan startup** changes (BrokerOrderEventConsumer task supervisors + PendingSubmitWatchdog + startup reconciliation) deploy with the backend code; on first prod restart, the reconcile pass + GetOrders resync seed the orders table from any pre-existing IBKR state.
- **Backend deployment topology (R7 explicit):** backend runs **single-worker uvicorn** for 5b. `docker-compose.prod.yml` MUST have `--workers 1` and the CI test asserts this. Multi-worker requires Redis SETNX leader election on consumer streams + deduplication of audit writes — Phase 9 work.
- **Canary rollout (R38):** per-account `app_config.broker.<label>.trade_enabled` defaults to `false`. Operator flips ONE paper account first (`isa-paper` or `normal-paper`), validates end-to-end via simulator mode + then real placeOrder, then expands. The kill-switch is the immediate-stop control.
- **Simulator-mode default-on (R39):** all live-mode gateways default `app_config.broker.<label>.simulator_only=true`. Operator must explicitly flip to false per gateway after canary verification. Defends against "I ran pytest against the wrong gateway" recurrence (cross-references `feedback_pytest_prod_db_wipe.md`).
- **nginx + CF Tunnel SSE config (R10):** `docker-compose.prod.yml` nginx must include the `proxy_buffering off` block for `/api/orders/events`; CF Tunnel `httpHostHeader` config disables buffering for the events route. Verified by post-deploy SSE smoke (idle 5 min, heartbeats received).

---

## §10 Deferred to 5c (post-architect-review revision)

- Order modify (preserves broker_order_id, transmits new params)
- Bracket / OCO orders
- Stop-Limit, IOC, FOK
- Account-NLV-impact preview panel (5a NLV + buying_power surfaced as pre-trade impact)
- Fills history page (full reconciliation view)
- "External orders" tab on /orders (TWS-placed + other-client orders surfaced separately)
- E2E Playwright trade flow against paper gateway
- Multi-worker backend (Phase 9 — requires Redis SETNX leader election)

**Pulled INTO 5b from previous deferral list:**
- ✅ Position-sanity check (R13) — preview-stage warning at 5x/10x multipliers
- ✅ Daily-notional cap per account (R13) — per-account config + reject path
- ✅ Simulator mode (R39) — default-on for live gateways

---

## §11 Architect-review applied

The first architect-review pass (2026-04-27) returned 5 CRITICAL + 15 HIGH + 14 MEDIUM + 5 LOW findings. All CRITICAL + HIGH findings are folded into this spec. MEDIUMs are addressed inline or explicitly noted as deferred. LOWs are deferred or noted.

| ID | Severity | Topic | Resolution |
|---|---|---|---|
| R1 | CRITICAL | Lost-order window between INSERT and sidecar timeout | §4 POST /orders step 8 + §5 PendingSubmitWatchdog + startup reconciliation; UI distinguishes `submission_pending` from `submitted` |
| R2 | CRITICAL | Frontend-generated PK leaks cross-account | §3 schema: server-generated `orders.id` UUIDv7; `(account_id, client_order_id)` UNIQUE; all queries scoped by `account_id` |
| R3 | CRITICAL | Sidecar dedup race + restart loss | §6: per-client_order_id `asyncio.Lock`; restart safety via `ib.trades()` `orderRef` scan |
| R4 | CRITICAL | OrderEvent account filter on wrong field | §6: `trade.order.account` not `trade.contract.account`; explicit cross-account-leak test |
| R5 | CRITICAL | `client_order_id` echo unreliable | §2 + §6: sidecar sets `ib_order.orderRef = client_order_id`; IBKR round-trips natively |
| R6 | HIGH | Kill-switch race ordering | §4: kill-switch checked FIRST, before nonce GETDEL |
| R7 | HIGH | Multi-worker SSE/consumer leadership | §9: single-worker invariant explicit; multi-worker → Phase 9 |
| R8 | HIGH | Supervisor re-enumeration semantics | §5: subscribes to `BrokerRegistry.account_changed` + 60s safety belt-and-suspenders |
| R9 | HIGH | Lifespan teardown + pending_submit reconciliation | §5: startup reconcile + uvicorn `--timeout-graceful-shutdown 30` |
| R10 | HIGH | CF Tunnel SSE config + Last-Event-ID | §4: 10s heartbeat, headers, BIGSERIAL `id:` field, nginx config; §9 deployment |
| R11 | HIGH | Out-of-order events overwriting terminal | §5 step 2: terminal-status sticky; `broker_event_at >= last_event_at` predicate; reconnect buffer-then-drain |
| R12 | HIGH | Notional staleness on partial fills | §3: `notional_filled` column; §4 PreviewResponse: MARKET 5% slippage buffer |
| R13 | HIGH | Position-sanity + daily-cap deferred | §1 + §10: pulled INTO 5b; §3 schema + §4 PreviewResponse `position_sanity` field |
| R15 | HIGH | Partial-then-cancel status modeling | §4 DELETE + §3 status enum: `cancelled` post-`partial` allowed via `(filled_qty < qty)` semantics |
| R16 | HIGH | Trade JSON serialization safety | §6: `_serialize_trade` whitelist helper; §2 invariant updated |
| R17 | HIGH | INSERT ON CONFLICT path explicit | §4: full SQL example with explicit SELECT-on-zero-rows fallback |
| R18 | HIGH | TWS-placed-order resync audit-only | §5 step 2: no-op on missing `client_order_id`; `order_events` audit row only |
| R19 | HIGH | Composite UNIQUE on (account_id, broker_order_id) | §3 schema: `uq_orders_account_broker_order_id` partial unique index |
| R39 | HIGH | Simulator mode default-on for live | §6 + §9 + §10: explicit; default `simulator_only=true` per live gateway |
| R20 | MEDIUM | Search rate limit at sidecar 1/sec UX | §2: backend 5/sec/user, sidecar 5/sec process-wide |
| R23 | MEDIUM | last_event_at race | §5: `GREATEST` predicate |
| R24 | MEDIUM | Notional currency conversion | §4: FX-converted via cached IBKR mid-rate |
| R25 | MEDIUM | SSE channel scope | §4: `?account_id=` query param |
| R27 | MEDIUM | Consumer poisoning circuit breaker | §5 metrics: alert at >50% drop rate; circuit breaker after 50 consecutive failures |
| R29 | MEDIUM | Outside-RTH warning re-evaluated | §4 POST /orders step 4 |
| R30 | MEDIUM | Sidecar OrderEvent queue unbounded | §6: `asyncio.Queue(maxsize=10_000)` + drop-on-full counter |
| R31 | MEDIUM | DELETE idempotency | §3: `cancel_requested_at` column; §4 DELETE 5s cooldown |
| R33 | MEDIUM | Pydantic qty regex leading zeros | §4: canonicalize before nonce hashing |
| R35, R37 | LOW | Proto reserved fields, clientId registry | §2 reserved 10-20; §8 references `clientId=998` per registry |
| R40-R43 | MEDIUM | Test-coverage gaps | §8: extra tests for partial-index assertion, concurrent place-cancel, new-symbol path, smoke cleanup safety |
| R14, R21, R22, R26, R28, R32, R34, R36, R38 | LOW/MED | Defer or document | See architect-review report; non-blocking |

## §12 Spec self-review (post-revision)

- **Placeholder scan:** none. All §s have concrete shapes, exact RPC signatures, locked decisions, and architect-review-applied table.
- **Internal consistency:** §2 proto fields ↔ §3 schema columns ↔ §4 Pydantic models ↔ §7 frontend types — all aligned on `decimal-as-string fixed-point 8 digits`, `(account_id, client_order_id)` UNIQUE, `orderRef` round-trip from §6, terminal-status-sticky semantics from §5.
- **Scope check:** sized for one implementation plan (~40-50 tasks per the prior phase pattern, expanded from 30-40 due to R13+R39 scope additions). Migrations 0004 + 4 RPCs + event consumer + watchdog + 8 endpoints + frontend modal/page is the right unit.
- **Ambiguity check:** identifier model is fully specified (R2+R5+R19); single-worker explicit (R7); kill-switch ordering explicit (R6); simulator-mode default explicit (R39); SSE Last-Event-ID resume explicit (R10).
