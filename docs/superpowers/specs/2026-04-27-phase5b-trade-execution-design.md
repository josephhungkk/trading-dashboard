# Phase 5b — IBKR trade execution — Design

**Date:** 2026-04-27
**Successor of:** v0.5.0 (Phase 5a NLV caching, 2026-04-27)
**Target tag:** v0.5.1
**Estimated duration:** ~2 weeks
**Deferred to 5c:** modify, bracket/OCO, stop-limit, IOC/FOK, simulator mode, daily-notional cap, position-sanity check, fills history page, account-NLV-impact preview, e2e Playwright flow.

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
  string client_order_id = 2;        // UUID; sidecar uses for ib_async permId mapping
  string conid = 3;                   // resolved by SearchContracts upstream
  string side = 4;                    // "BUY" | "SELL"
  string order_type = 5;              // "MARKET" | "LIMIT" | "STOP"
  string tif = 6;                     // "DAY" | "GTC"
  string qty = 7;                     // decimal-as-string, fixed-point 8 digits
  string limit_price = 8;             // decimal-as-string, "" for MARKET
  string stop_price = 9;              // decimal-as-string, "" unless STOP
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
- `client_order_id` is the dedup key sidecar↔backend; sidecar maps to ib_async `permId`.
- `OrderEvent` is account-scoped; sidecar filters by `account_number` at the source.
- `SearchContracts` rate-limited at the sidecar (1 req/sec) with 5-min TTL cache.
- `raw_payload` is the full ib_async Trade snapshot as JSON.

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
  id                UUID PRIMARY KEY,                    -- == client_order_id
  account_id        UUID NOT NULL REFERENCES broker_accounts(id),
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
  notional          NUMERIC(20, 8) NOT NULL,             -- qty × price; cached for UI
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

CREATE INDEX ix_orders_account_status ON orders (account_id, status)
  WHERE status NOT IN ('filled', 'cancelled', 'rejected', 'expired');
CREATE INDEX ix_orders_broker_order_id ON orders (broker_order_id);
CREATE INDEX ix_orders_account_created ON orders (account_id, created_at DESC);

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

**Invariants:**
- `orders.id == client_order_id` — primary key IS the dedup key.
- `orders.broker_order_id` populated by first OrderEvent.
- `order_events.order_id` nullable so TWS-placed/external orders' events are recorded as audit-only rows.
- Active-orders index is partial — keeps it tiny.
- `notional` denormalized at insert time.
- `raw_payload` JSONB for `jsonb_path_query` debugging.

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
    notional: str
    notional_currency: str            # ISO-3
    max_notional_per_order: str
    cap_status: Literal["ok", "near", "exceeded"]   # near = >80%
    contract_summary: ContractSummary
    warnings: list[str]

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

- **`POST /preview`** — validate Pydantic; resolve contract via cached `SearchContracts(conid)`; compute notional from `limit_price` or sidecar's last MID for MARKET; lookup `app_config.broker.<account>.max_notional_per_order` (default e.g. £10K equiv); check `app_config.broker.kill_switch_enabled`; mint UUID nonce in Redis under `nonce:order:<account>:<nonce>` (TTL 30s, payload-bound) so the POST can't smuggle different fields; rate-limited 10/min/user.

- **`POST /orders`** — atomic flow:
  1. validate Pydantic
  2. `GETDEL` Redis nonce — must exist AND match the frozen request payload
  3. `INSERT INTO orders ... ON CONFLICT (id) DO NOTHING RETURNING *` — zero rows = idempotent retry, return existing row 200
  4. forward `PlaceOrderRequest` to sidecar via `BrokerSidecarClient.place_order`
  5. on sidecar 503/timeout: orders row stays `pending_submit`
  6. on sidecar success: returns OrderResponse status `submitted`

- **`DELETE /orders/{id}`** — load order; if terminal status → 409; else forward `CancelOrderRequest`; status update arrives via stream.

- **`GET /orders/events`** — SSE; subscribes to Redis pubsub `orders:events:<scope>` (scope = `fleet` or `account:<id>`); `event: order.update\ndata: <OrderResponse JSON>\n\n`; heartbeat every 15s; closes on client disconnect.

- **`GET /contracts/search`** — query param `q` + optional `asset_class`; forwards to one healthy sidecar's `SearchContracts`; caches in Redis `contracts:search:<sha256(q,class)>` 5-min TTL; rate-limited 5 req/sec/user.

### Maintenance + kill-switch

All mutating endpoints check `compute_broker_maintenance(now)` (5a helper); active → 503 + Retry-After + `broker_maintenance` envelope. `kill_switch_active` is a separate gate via `app_config.broker.kill_switch_enabled`; operator-flippable; mutating endpoints 503 with `{"error":"kill_switch_active"}`.

### Boundary stripping (5a R12)

`OrderResponse` exposes only `account_id` (UUID), never `gateway_label`/`account_number`. `AccountService._resolve_account` translates when forwarding to sidecar.

---

## §5 BrokerOrderEventConsumer

Per-`(sidecar, account_number)` event consumer, mirroring `BrokerDiscoverer`'s lifecycle pattern. New module: `backend/app/services/order_event_consumer.py`.

### Lifecycle

- **Lifespan**: after `build_broker_registry()` succeeds, start one supervisor task per sidecar; supervisor enumerates accounts and spawns child tasks per `(sidecar, account_number)` pair (~22 child tasks total).
- **Per-child loop**: open `OrderEvent(AccountRef)` server-stream; consume events; on error, exponential-backoff reconnect (1s → 30s cap); BEFORE re-consuming, run `GetOrders` resync to emit synthetic events for any transitions missed during downtime.
- **Per-event handler** (`_process_event`):
  1. INSERT into `order_events` (always succeeds, includes events for orders we didn't place)
  2. UPSERT `orders` materialized state (only if `client_order_id` matches an existing row)
  3. PUBLISH OrderResponse JSON to Redis pubsub on `orders:events:fleet` AND `orders:events:account:<id>`
  All three steps wrapped in `session.begin_nested()` savepoint; per-event failure logs + counter, doesn't poison the consumer.
- **`asyncio.Lock` re-entrancy guard** on the supervisor's iteration boundary (mirrors 5a discoverer C1).
- **Cancellation discipline**: on lifespan teardown, supervisor cancels all child tasks; each child catches `CancelledError`, closes the gRPC stream cleanly, returns. Tested via the same lifespan-shutdown test pattern as Phase 4.

### Reconnect-and-resync

On every (re)connect, before consuming the live stream, call `GetOrders(account)` and diff against the orders table (by `broker_order_id`). For each transition the table doesn't already have, emit a synthetic event into the consumer's normal `_process_event` path. Synthetic events have `observed_at = now()` but `broker_event_at` from the snapshot. This means "backend was down for 5 min" → on reconnect, all the fills that happened during the outage land in `order_events` correctly.

### Prometheus metrics

- `broker_order_events_received_total{label}` — counter
- `broker_order_events_dropped_total{label, reason}` — counter
- `broker_order_event_lag_ms` — histogram (broker_event_at to observed_at)
- `broker_order_stream_reconnects_total{label}` — counter
- `broker_order_stream_resync_synthetic_events_total{label}` — counter

---

## §6 Stream-shape decision: 22 streams

**Decision: one stream per `(sidecar, account_number)`.** Rationale:
- **Per-account reconnect surface** — one bad account stalls only its own stream, not 5 sibling accounts.
- **Clean handover from 5a's discover** — discoverer enumerates the 22 accounts every 30s; consumer subscribes to add/remove and spawns/cancels child tasks.
- **Sidecar simplicity** — `OrderEvent(AccountRef)` filters at the source via ib_async's `Trade.contract.account` field.
- **Idle cost negligible** — 22 idle gRPC streams over a multiplexed HTTP/2 connection = ~22KB total per sidecar. We're already paying the TCP cost.

### Sidecar `handlers.py` additions

```python
class BrokerHandlers:
    async def PlaceOrder(self, request, context):
        contract = await self._resolve_contract(request.conid)
        ib_order = self._build_ib_order(request)  # MarketOrder/LimitOrder/StopOrder
        # Dedup: if client_order_id seen before, return existing permId
        if request.client_order_id in self._client_order_id_by_perm_id_inv:
            perm_id = self._client_order_id_by_perm_id_inv[request.client_order_id]
            return broker_pb2.PlaceOrderResponse(broker_order_id=str(perm_id), status="Submitted")
        trade = self._ib.placeOrder(contract, ib_order)
        self._client_order_id_by_perm_id[trade.order.permId] = request.client_order_id
        self._client_order_id_by_perm_id_inv[request.client_order_id] = trade.order.permId
        return broker_pb2.PlaceOrderResponse(
            broker_order_id=str(trade.order.permId),
            status=trade.orderStatus.status,
        )

    async def CancelOrder(self, request, context):
        trade = self._find_open_trade(int(request.broker_order_id))
        if trade is None:
            return broker_pb2.CancelOrderResponse(accepted=False)
        self._ib.cancelOrder(trade.order)
        return broker_pb2.CancelOrderResponse(accepted=True)

    async def OrderEvent(self, request, context):
        queue: asyncio.Queue[broker_pb2.OrderEventMessage] = asyncio.Queue()
        def _on_status(trade):
            if trade.contract.account != request.account_number:
                return
            queue.put_nowait(self._proto_event_from_trade(trade))
        self._ib.orderStatusEvent += _on_status
        self._ib.execDetailsEvent += _on_status
        try:
            while not context.cancelled():
                yield await queue.get()
        finally:
            self._ib.orderStatusEvent -= _on_status
            self._ib.execDetailsEvent -= _on_status

    async def SearchContracts(self, request, context):
        # ib_async reqContractDetailsAsync; cache by (query, asset_class) 5min TTL;
        # rate-limit per sidecar process to 1 req/sec.
        ...
```

### Idempotency at the sidecar

- `client_order_id` is the dedup key. Sidecar maintains in-memory bidirectional map for the process lifetime.
- Duplicate PlaceOrder → return existing permId without calling `placeOrder()` again.
- On sidecar restart the map is gone, but backend's `ON CONFLICT DO NOTHING` on `orders.id` prevents the retry from being sent in the first place.

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
- **`tests/services/test_orders.py`** (~10 tests, outer-rollback fixture): same `client_order_id` returns existing row; preview-then-place consumes nonce; nonce reused → 409; nonce vs different payload → 422; preview cap `near` at 81%, `exceeded` at 101%; kill-switch → 503; maintenance → 503 + Retry-After; cancel terminal → 409; cancel forwarded with right `broker_order_id`; OrderResponse strips `gateway_label`.
- **`tests/services/test_order_event_consumer.py`** (~8 tests, fake gRPC stream): single event → INSERT order_event + UPSERT orders + PUBLISH; partial-fill updates filled_qty + avg_fill_price; terminal sets status; malformed event → savepoint rollback + counter; reconnect-and-resync emits synthetic events; stream cancellation closes cleanly; account-removed stops child stream; one stream death doesn't affect siblings.
- **`tests/api/test_orders.py`** (~12 tests, dep-override stubs): preview returns nonce + cap + warnings; place consumes nonce; place dedup on retry; cancel; list filters; OpenAPI shape locked; SSE emits formatted events; SSE heartbeat fires; SSE closes on disconnect.
- **`tests/api/test_contracts.py`** (~4 tests): forward to sidecar; cache hits; rate-limit 429; sidecar 503 propagates.

### Sidecar (`sidecar/tests/`)

- **`test_handlers_orders_contract.py`** extension (~10 new tests, FakeIB): PlaceOrder MARKET/LIMIT/STOP; PlaceOrder dedup; CancelOrder forwards; OrderEvent filters by account_number; OrderEvent emits status + fill; SearchContracts caches; SearchContracts rate-limits; SearchContracts forwards asset_class; OrderEvent handles ib disconnect.
- **`tests/test_real_ibkr_smoke.py`** extension (~3 new tests, paper 4002, `clientId=998`): place tiny LIMIT well below market (won't fill), assert PendingSubmit/Submitted ≤5s; cancel same order, assert ApiCancelled ≤5s; OrderEvent stream receives both. Test cleanup cancels any leftover orders in `finally`. Idempotent.

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

## §9 Migration sequencing

- **Alembic 0004** (orders + order_events tables + 4 enums) MUST land + be applied to prod BEFORE the backend code that reads/writes them is deployed. Same gate as 5a's 0003.
- **Proto contract additions** (PlaceOrder/CancelOrder/OrderEvent/SearchContracts) ship as a coordinated trio: proto → sidecar → backend. CI's `proto` job runs `buf lint` + `buf format --diff --exit-code`; sidecar + backend depend on it.
- **Lifespan startup** changes (BrokerOrderEventConsumer task supervisors) deploy with the backend code; on first prod restart, GetOrders resync seeds the orders table from any pre-existing IBKR state.

---

## §10 Deferred to 5c

- Order modify (preserves broker_order_id, transmits new params)
- Bracket / OCO orders
- Stop-Limit, IOC, FOK
- Simulator mode (`broker.<acct>.simulator_only` config — sidecar logs but doesn't `placeOrder()`)
- Daily-notional cap per account
- Position-sanity check (would the new position exceed existing position's 10x?)
- Account-NLV-impact preview panel
- Fills history page (full reconciliation view)
- "External orders" tab on /orders (TWS-placed + other-client orders surfaced separately)
- E2E Playwright trade flow against paper gateway

---

## §11 Self-review

- **Placeholder scan:** none. All §s have concrete shapes, exact RPC signatures, and locked decisions.
- **Internal consistency:** §2 proto fields ↔ §3 schema columns ↔ §4 Pydantic models ↔ §7 frontend types — all aligned on `decimal-as-string fixed-point 8 digits` and `client_order_id == orders.id`.
- **Scope check:** sized for one implementation plan (~30-40 tasks per the prior phase pattern). Migrations 0004 + 4 RPCs + event consumer + 7 endpoints + frontend modal/page is the right unit; bigger split would push features into 5c that were promised in 5b.
- **Ambiguity check:** stream shape decision in §6 (22 streams not 4) is explicit; nonce-vs-UUID decision in §4 (BOTH, complementary) is explicit; SSE-vs-WebSocket in §4 (SSE only) is explicit.
