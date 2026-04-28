# Phase 5c — Advanced Order Types Design

**Date:** 2026-04-28
**Target tag:** `v0.5.4`
**Estimated effort:** 5-7 working days
**Builds on:** Phase 5b (`v0.5.1`) trade execution + Phase 5b.1 (`v0.5.3.1`) canary hotfix pack
**Defers to subsequent phases:**
- Phase 5d (or 5c.1 hardening pack): `AccountResponse.position_count`, periodic BASE-tag refresh for accounts added mid-run, generic OCA group (N-leg, no parent anchor)
- Phase 9: multi-worker uvicorn (replaces in-memory nonce store + cancel cooldown set + per-client SSE queues with Redis/PG-backed equivalents)

---

## 1. Goal

Ship modify orders + brackets/OCO + execution-level fills history as **additive layers** on the Phase 5b orders/order_events foundation. No changes to the existing `POST /api/orders` or `DELETE /api/orders/{id}` wire shapes; new endpoints + one new table + one extended endpoint.

The user-visible deliverables:
1. **Modify orders** — operator can change qty / limit_price / tif / stop_price on a non-terminal order via `PUT /api/orders/{id}`.
2. **Bracket orders** — operator can submit `entry + optional stop-loss + optional take-profit` as one atomic OCA group via `POST /api/orders/bracket`.
3. **Fills history** — execution-level audit trail (per-exec id, qty, price, commission) via `GET /api/fills` with date-range pagination.
4. **Date-range filter** on `GET /api/orders` — answers "what did I trade between X and Y" without a separate endpoint.

Single-leg place/cancel and the OrderEventConsumer state machine are **unchanged**.

---

## 2. Non-goals (explicit YAGNI)

- **Generic OCA group** (N orders, no parent anchor) — deferred to Phase 5d if bracket ergonomics demand it. v1 hard-codes the parent-anchored model.
- **Modify on SIM-prefixed orders** — sidecar's simulator branch only supports `_sim_orders` map for cancel echo; modify on SIM is rejected at the sidecar with `INVALID_ARGUMENT`. Real-broker modify only.
- **Trailing stops** — IBKR's TRAIL/TRAIL_LIMIT order types deferred. v1 supports MARKET, LIMIT, STOP, STOP_LIMIT (matches Phase 5b's `OrderType` enum unchanged).
- **Inline edit on OrdersPage** — modify goes through `TradeTicketModal` only. The "raise stop in two clicks" workflow is achievable via the modal; inline edit doubles the form-validation surface for marginal UX gain.
- **Position closure / flat helpers** — "close all" / "flatten account" are higher-level workflows; v1 ships the primitives only.
- **OCA cleanup job** — `oca_group` strings persist in the orders table indefinitely. Pruning rows where all members are terminal + `> 90 days` is deferred to a 5d/5e housekeeping job.

---

## 3. Architecture overview

### 3.1 Data flow — modify

```
Frontend                          Backend (single-worker uvicorn)              Sidecar (label X)         IBKR Gateway
  |                                       |                                          |                         |
  |-- POST /api/orders/preview ---------->|                                          |                         |
  |    {qty, limit_price, ...}            |                                          |                         |
  |<------- {nonce, notional_currency} ---|                                          |                         |
  |                                       |                                          |                         |
  |-- PUT /api/orders/{id} -------------->|                                          |                         |
  |    {nonce, qty, limit_price, ...}     |-- (validate nonce + state + policy)      |                         |
  |                                       |-- INSERT order_events (status=modified)  |                         |
  |                                       |-- ModifyOrder RPC ---------------------->|                         |
  |                                       |                                          |-- placeOrder(orderId,   |
  |                                       |                                          |              contract,  |
  |                                       |                                          |              order)---->|
  |<------- 200 {id, status: modified} ---|                                          |<---- orderStatus -------|
  |                                       |<--- (existing OrderEvent stream emits    |                         |
  |                                       |      status=modified events; consumer    |                         |
  |                                       |      writes order_events rows)           |                         |
  |-- GET /api/orders/events SSE -------->|                                          |                         |
  |<------- order.update events ----------|                                          |                         |
```

### 3.2 Data flow — bracket

```
Frontend                          Backend                                       Sidecar                   IBKR Gateway
  |                                  |                                              |                          |
  |-- POST /api/orders/preview ----->|  (preview against parent leg notional only;  |                          |
  |   (mode=bracket, entry, ...)     |   children's qty == parent qty → no fresh    |                          |
  |<------- {nonce, ...} ------------|   notional check needed)                     |                          |
  |                                  |                                              |                          |
  |-- POST /api/orders/bracket ----->|-- mint 3 UUIDv7 client_order_ids             |                          |
  |   {nonce, entry, sl?, tp?}       |-- generate oca_group "BRK-<uuid8>"           |                          |
  |                                  |-- INSERT parent row only (HIGH-2 two-phase   |                          |
  |                                  |   commit): status=pending_submit,            |                          |
  |                                  |   parent_order_id=NULL, oca_group=<group>    |                          |
  |                                  |-- PlaceBracket RPC ------------------------->|                          |
  |                                  |                                              |-- placeOrder(parent,     |
  |                                  |                                              |     transmit=False)      |
  |                                  |                                              |   (ib_async assigns      |
  |                                  |                                              |    parent.orderId        |
  |                                  |                                              |    synchronously)        |
  |                                  |                                              |-- sl.parentId =          |
  |                                  |                                              |   parent.orderId         |
  |                                  |                                              |   placeOrder(sl,         |
  |                                  |                                              |     transmit=False)      |
  |                                  |                                              |-- tp.parentId =          |
  |                                  |                                              |   parent.orderId         |
  |                                  |                                              |   placeOrder(tp,         |
  |                                  |                                              |     transmit=True)       |
  |                                  |                                              |   (atomically submits    |
  |                                  |                                              |    the chain)----------->|
  |                                  |<--- {parent_broker_order_id,                 |                          |
  |                                  |      sl_broker_order_id?,                    |                          |
  |                                  |      tp_broker_order_id?}                    |                          |
  |                                  |-- (HIGH-2 step 3, single tx) INSERT 2 child  |                          |
  |                                  |   rows + UPDATE parent SET broker_order_id   |                          |
  |<--- 200 {parent: {...},          |                                              |                          |
  |          children: [{...}, ...]}-|                                              |                          |
```

### 3.3 Data flow — fills

```
Sidecar emits OrderEventMessage with exec_id populated (from Fill.execution.execId).
OrderEventConsumer's _process_event:
  IF exec_id present (kind=='exec_details'):
    Try INSERT INTO fills (...) ON CONFLICT (exec_id) DO NOTHING.
    On FK violation (orders row not yet inserted - race):
      INSERT INTO pending_fills (...) ON CONFLICT (exec_id) DO NOTHING.   # CRIT-2 fix
  Always:
    INSERT INTO order_events as before.
Both inserts share a single transaction.

After every _process_event that resolves a client_order_id -> orders.id
(both place-ack and existing rows), drain matching pending_fills:
  INSERT INTO fills SELECT ... FROM pending_fills
    WHERE broker_order_id = <just-resolved> AND ...
    ON CONFLICT (exec_id) DO NOTHING;
  DELETE FROM pending_fills WHERE broker_order_id = <just-resolved>;

A periodic sweeper (30s tick alongside PendingSubmitWatchdog):
  FOR each pending_fills row WHERE inserted_at < now() - INTERVAL '5 min':
    Resolve orders.id by broker_order_id; if found -> drain; if not -> log
    pending_fills_orphan{label} (Prometheus counter) and leave the row for
    the next sweep. Rows older than 1 hour also page via alert.

GET /api/fills query:
  SELECT * FROM fills f
    JOIN orders o ON o.id = f.order_id
   WHERE o.account_id = $1
     AND f.executed_at BETWEEN $2 AND $3
     AND ($cursor IS NULL OR (f.executed_at, f.id) < $cursor_decoded)
   ORDER BY f.executed_at DESC, f.id DESC
   LIMIT 100;
```

#### 3.3.1 Commission backfill — MED-5 architect-review fix

IBKR's commissionReport is a separate event (not part of execDetails). Sidecar's `BrokerHandlers.OrderEvent` stream extends to subscribe to `ib.commissionReportEvent`. Each commissionReport emits an `OrderEventMessage` variant with new field `kind="commission_report"` populated alongside `exec_id`, `commission`, `commission_currency`.

Consumer routing:

```
_process_event(event):
  if event.kind == "commission_report":
    UPDATE fills
       SET commission = event.commission,
           commission_currency = event.commission_currency
     WHERE exec_id = event.exec_id;
    if rowcount == 0:
      # Fill row not yet flushed (commission arrived before exec_details, OR
      # exec_details is in pending_fills awaiting drain). Stash on the
      # pending_fills row if present, else stage in a small in-memory
      # commission_buffer keyed by exec_id with a 5-min TTL. Drain alongside
      # the pending_fills sweeper.
  elif event.exec_id:
    # Normal execDetails path - see §3.3.
  else:
    # Status-only event - existing 5b path unchanged.
```

In-memory `commission_buffer` is single-process (single-worker assumption); multi-worker requires Redis hash, deferred to Phase 9. Buffer overflow (> 1000 entries) triggers `commission_buffer_overflow_total` metric + page-level alert.

---

## 4. Schema (Alembic migration 0006)

```sql
-- 4.1 Brackets - extend existing orders table
ALTER TABLE orders
  ADD COLUMN parent_order_id UUID NULL REFERENCES orders(id) ON DELETE SET NULL,
  ADD COLUMN oca_group VARCHAR(64) NULL;

CREATE INDEX orders_parent_order_id_idx
  ON orders(parent_order_id)
  WHERE parent_order_id IS NOT NULL;

-- 4.2 modified status (CRIT-1 architect review fix)
-- Add 'modified' value to the existing order_status_enum AND a status-rank
-- function that the consumer's _update_order SQL uses to reject backward
-- transitions (e.g., a broker-side "Submitted" event that arrives after we've
-- written "modified" must NOT downgrade the status).
ALTER TYPE order_status_enum ADD VALUE 'modified' AFTER 'submitted';

CREATE FUNCTION order_status_rank(s order_status_enum) RETURNS INT AS $$
  SELECT CASE s
    WHEN 'pending_submit' THEN 0
    WHEN 'submitted'      THEN 1
    WHEN 'inactive'       THEN 1   -- equivalent to submitted for ordering
    WHEN 'modified'       THEN 2
    WHEN 'partial'        THEN 3
    WHEN 'filled'         THEN 4   -- terminal
    WHEN 'cancelled'      THEN 5   -- terminal
    WHEN 'rejected'       THEN 5   -- terminal
    WHEN 'expired'        THEN 5   -- terminal
  END;
$$ LANGUAGE SQL IMMUTABLE;

-- 4.3 Fills - new table
CREATE TABLE fills (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id            UUID NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
  exec_id             VARCHAR(64) NOT NULL UNIQUE,    -- IBKR execId is ~31 chars; 64 leaves headroom for Schwab/Futu
  qty                 NUMERIC(20,8) NOT NULL CHECK (qty > 0),
  price               NUMERIC(20,8) NOT NULL,
  currency            CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  executed_at         TIMESTAMPTZ NOT NULL,
  commission          NUMERIC(20,8) NULL,           -- signed; negative for rebates
  commission_currency CHAR(3) NULL CHECK (commission_currency IS NULL OR commission_currency ~ '^[A-Z]{3}$'),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX fills_order_id_executed_at_idx ON fills(order_id, executed_at DESC);
CREATE INDEX fills_executed_at_idx ON fills(executed_at);

-- 4.4 pending_fills buffer (CRIT-2 architect review fix)
-- Some execDetails events arrive before the parent orders row exists (race
-- between the place RPC's response landing in the consumer and the broker's
-- first execDetails on the order). The consumer attempts INSERT into fills
-- first; on FK violation it falls back to INSERT into pending_fills. A
-- 30-second sweeper task (alongside the watchdog) drains rows whose
-- broker_order_id has since resolved to an orders.id.
CREATE TABLE pending_fills (
  exec_id             VARCHAR(64) PRIMARY KEY,
  broker_order_id     VARCHAR(64) NOT NULL,         -- not FK; resolved on drain
  account_id          UUID NOT NULL REFERENCES broker_accounts(id),
  qty                 NUMERIC(20,8) NOT NULL CHECK (qty > 0),
  price               NUMERIC(20,8) NOT NULL,
  currency            CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  executed_at         TIMESTAMPTZ NOT NULL,
  commission          NUMERIC(20,8) NULL,
  commission_currency CHAR(3) NULL,
  raw_payload         JSONB NOT NULL,
  inserted_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX pending_fills_broker_order_id_idx ON pending_fills(broker_order_id);
CREATE INDEX pending_fills_inserted_at_idx ON pending_fills(inserted_at);   -- sweeper scan
```

**Notes:**
- `parent_order_id ON DELETE SET NULL` — if the parent row is hard-deleted (housekeeping job in 5d/5e), children survive as standalone orders. Soft-delete is the normal path; this is defence-in-depth.
- `fills.order_id ON DELETE RESTRICT` — fills must be orphaned, not deleted. Audit trail is sacred.
- `exec_id UNIQUE` — IBKR's `execId` is globally unique per fill. Idempotent ON CONFLICT DO NOTHING in the consumer.
- `commission NULL allowed` — IBKR can emit fills before commissions are confirmed; consumer writes the fill row first, then a separate commission report event back-fills the column via a `commissionReport` callback. Separate INSERT/UPDATE in the consumer (see §3.3.1).
- `pending_fills` has no FK on `broker_order_id` because the order row may not exist yet — that's the entire point. The sweeper resolves it lazily.
- `order_status_rank()` is `IMMUTABLE` so PostgreSQL can use it in CHECK / WHERE expressions without re-evaluating.

---

## 5. HTTP surface

### 5.1 `PUT /api/orders/{id}` — modify

**Request:**

```json
{
  "nonce": "01913a4b-ece0-75d3-...",
  "qty": "100",
  "limit_price": "150.00",
  "tif": "DAY",
  "stop_price": "145.00"
}
```

**Constraints (immutable on modify):** `account_id`, `conid`, `side`, `order_type`, `client_order_id`, `parent_order_id`. Attempting to send any of these → 422.

**Response (200):** Full updated `OrderResponse` (same shape as existing `GET /api/orders/{id}`).

**Errors:**

| Status | Reason code | Trigger |
|---|---|---|
| 409 | `terminal_status` | order is `cancelled/rejected/expired/filled` |
| 409 | `bracket_parent_partial` | order has `parent_order_id IS NULL` AND `filled_qty > 0` AND children exist (modifying parent would orphan children). Children are unaffected — see §5.1.2 |
| 409 | `notional_overflow` | new (qty * price) breaches `daily_notional_cap` or `max_notional_per_order` |
| 409 | `nonce_mismatch` | supplied nonce doesn't match a fresh preview for this exact payload |
| 409 | `simulator_only_mismatch` | order live but gateway flipped to `simulator_only` post-place |
| 503 | `kill_switch` | fleet `kill_switch_enabled` set |

#### 5.1.1 Idempotency mechanism (replay-safety) — HIGH-1 architect-review fix

PUT idempotency is implemented as a per-order short-lived response cache, not a single-shot "consume once" nonce.

- Backend maintains an in-memory `dict[order_id, dict[nonce, response_json]]` (single-worker assumption — multi-worker requires Redis-backed cache, deferred to Phase 9).
- On PUT: lookup `(order_id, nonce)`. If present → return the cached response body (HTTP 200, byte-identical to first call).
- If absent → process the modify, cache `(order_id, nonce) → response` for **60 seconds**, return.

**Failure modes:**
- Backend crash mid-PUT: nonce remains in the per-order nonce-store as "consumed but no cached response". Replay → 409 `nonce_mismatch`. Operator must re-preview to get a fresh nonce, ensuring policy gates re-fire.
- 60-second cache window expires: replay → 409 `nonce_mismatch`. Same recovery as above.
- Hot replay (within 60s) → cached 200 returned, no second modify dispatched to broker.

This makes PUT replay-safe without exposing the operator to double-modify on a flaky network.

#### 5.1.2 Modify on bracket children — MED-1 architect-review fix

The 409 `bracket_parent_partial` rejection only applies when **modifying the parent** of a bracket whose `filled_qty > 0`. Modifying a CHILD (`parent_order_id IS NOT NULL`) is **always allowed** regardless of parent status — including the canonical "raise stop after favorable parent fill" workflow.

The validation logic:
```python
if order.parent_order_id is None:                # we are the parent
    if order.filled_qty > 0:                     # AND already partial
        if has_living_children(order.id):        # AND children exist
            raise PreviewUnavailable(409, "bracket_parent_partial")
# else: child or single-leg — proceed
```

### 5.2 `POST /api/orders/bracket` — bracket creation

**Request:**

```json
{
  "nonce": "01913a4b-ece0-75d3-...",
  "account_id": "<uuid>",
  "client_order_id": "<uuid7>",
  "conid": "265598",
  "side": "BUY",
  "order_type": "LIMIT",
  "tif": "DAY",
  "qty": "100",
  "limit_price": "150.00",
  "stop_price": "145.00",
  "target_price": "160.00"
}
```

**Validation (BUY case; mirror for SELL):**
- At least one of `stop_price` / `target_price` must be present (else use `POST /api/orders` instead).
- `stop_price < limit_price` (stop-loss below entry).
- `target_price > limit_price` (take-profit above entry).
- Reverse for SELL.

**Response (200):**

```json
{
  "parent": { "id": "<uuid>", "client_order_id": "...", "broker_order_id": "...", "status": "submitted" },
  "children": [
    { "id": "<uuid>", "leg": "stop_loss", "broker_order_id": "...", "status": "submitted" },
    { "id": "<uuid>", "leg": "take_profit", "broker_order_id": "...", "status": "submitted" }
  ],
  "oca_group": "BRK-019dd33b"
}
```

**Errors:**

| Status | Reason code | Trigger |
|---|---|---|
| 400 | `bracket_invalid_legs` | both stop_price and target_price absent |
| 400 | `bracket_invalid_prices` | SL/TP price doesn't bracket the entry correctly |
| 400 | `bracket_too_many_children` | > 2 children supplied (v1 cap) |
| 409/503 | (all single-leg rejections) | applied to the parent leg |

#### 5.2.1 Two-phase bracket commit — HIGH-2 architect-review fix

Pre-RPC INSERT of all 3 rows leaves 3 stale `pending_submit` rows on sidecar failure (3× the surface of single-leg place — operator confusion + watchdog scan inflated by 3×). The mitigation: **commit children only after the sidecar acknowledges**.

Flow:
1. INSERT **parent only** with status `pending_submit` and the planned `oca_group`. (Same surface as single-leg place — one stale row on failure, escalated by the existing `PendingSubmitWatchdog` after 5 min.)
2. Call `PlaceBracket` RPC with all 3 leg payloads (parent, optional SL, optional TP).
3. **On RPC success** → in one transaction: INSERT 2 children rows referencing the parent + UPDATE parent's `broker_order_id`.
4. **On RPC failure** → escalate parent only (1 stale row). Children never written.

**Operator UX trade-off:** the bracket isn't atomic at the DB level until step 3 completes (~50-100ms after step 1). Frontend's bracket detail view should render a "loading children" placeholder during this window. The SSE `order.update` stream covers the gap: parent's `pending_submit → submitted` and children's first inserts (with `submitted` status) all flow through the existing fan-out.

#### 5.2.2 Bracket idempotency — MED-4 architect-review fix

`POST /api/orders/bracket` is idempotent on the **parent's `client_order_id`** (the existing UNIQUE constraint on `(account_id, client_order_id)` catches the replay).

On replay:
- **Parent row exists with both expected children populated** → return original 200 response (lookup the bracket by parent's id, reconstruct the response shape).
- **Parent row exists but children are missing** (mid-flight crash between step 1 and step 3 of §5.2.1) → DELETE the parent row (no children to cascade-orphan since they were never written) and re-process the bracket from scratch. Safe because the first response was never sent to the client.
- **Parent row doesn't exist** → fresh bracket creation as if first call.

The §5.2.1 transaction boundary (parent INSERT + RPC + child INSERT) makes "partial child state" the only failure mode that requires recovery, and the recovery (DELETE parent + retry) is straightforward because no broker-side state has been committed yet (RPC failed before `placeOrder` reached the IBKR Gateway).

### 5.3 `GET /api/fills` — execution history

**Query params:**
- `account_id` (required, UUID) — scopes to one account
- `from` (required, ISO 8601 datetime) — inclusive lower bound on `executed_at`
- `to` (required, ISO 8601 datetime) — inclusive upper bound
- `limit` (optional, default 100, max 500) — page size
- `cursor` (optional, opaque base64) — encoded `(executed_at, id)` tuple

**Response (200):**

```json
{
  "fills": [
    {
      "id": "<uuid>",
      "order_id": "<uuid>",
      "exec_id": "0001f4a8.66c0e220.01.01",
      "qty": "50",
      "price": "150.05",
      "currency": "USD",
      "executed_at": "2026-04-28T14:30:01.234Z",
      "commission": "0.50",
      "commission_currency": "USD"
    }
  ],
  "next_cursor": "eyJleGVjdXRlZF9hdCI6IjIwMjYtMDQtMjhUMTQ6MzA6MDEuMjM0WiIsImlkIjoiLi4uIn0="
}
```

`next_cursor` absent (or null) → end of results.

### 5.4 `GET /api/orders` — extend with date-range filter

**New query params (additive — existing `account_id` + `status` still supported):**
- `from` (optional, ISO 8601 datetime) — filter on `created_at >= from`
- `to` (optional, ISO 8601 datetime) — filter on `created_at <= to`

When both `from` and `to` are present plus a `status` filter, all conditions AND'd. Default behavior (neither present) unchanged.

---

## 6. State machine — `modified` status

Add `modified` between `submitted` and `partial` in the existing 8-state enum (rank 2 — see §4 `order_status_rank` SQL function):

```
pending_submit -> submitted -> modified -> partial -> filled
                                  |          |
                                  v          v
                cancelled / rejected / expired (sticky terminals; rank 5)
```

### 6.1 Allowed transitions — MED-3 architect-review fix

The status-rank function (defined in Alembic 0006) gives a partial order the consumer's `_update_order` SQL enforces. A transition `from -> to` is allowed iff `rank(to) >= rank(from)` AND neither equals a terminal status (terminal-stickiness check stays as today).

| From | To | Conditions |
|------|-----|-----------|
| modified | modified | re-modify before broker ack (rank 2 -> 2 OK) |
| modified | partial | first fill after modify (rank 2 -> 3 OK) |
| modified | filled | order completes after modify (rank 2 -> 4 OK) |
| modified | cancelled / rejected / expired | terminal (rank 2 -> 5 OK) |
| modified | submitted | **NOT ALLOWED** (rank 2 -> 1 — rejected by `order_status_rank` check) |
| modified | pending_submit | **NOT ALLOWED** (rank 2 -> 0 — rejected) |

The "NOT ALLOWED" rows are the load-bearing addition — pre-CRIT-1, the temporal predicate alone would have permitted these backward transitions whenever the broker emitted a re-ack with a later timestamp. Post-CRIT-1, the rank check rejects them.

### 6.2 HTTP-side write semantics — HIGH-3 architect-review fix

The synchronous PUT-side write does **not** mutate `orders.status` directly. Instead:

1. PUT handler writes one `order_events` row with `status='modified'`, `broker_event_at = now()`, `raw_payload = {modify_diff}`. This is a pure audit-log append.
2. PUT handler does NOT issue an `UPDATE orders SET status='modified'` — the `orders` row stays at whatever its current status is (likely `submitted` or `working`).
3. PUT handler returns 200 with a synthesized `OrderResponse` whose `status` field is computed as `max(current_orders.status, 'modified')` by rank — for client convenience the response shows the projected state, while the canonical state in `orders.status` is broker-driven.
4. The broker stream's `Modified` (or re-acked `Submitted`) event flows through `_process_event` as today; consumer's `_update_order` then mutates `orders.status` based on the rank check.

This keeps the audit log (every operator action recorded as an `order_events` row) cleanly separated from the canonical state (broker-driven). Without this split, the HTTP-side write and the consumer-side write would race on the `orders` row's `last_event_at` temporal predicate, leaving the row stuck on whichever wrote first.

The cancel HTTP path already follows this exact pattern (§5b shipped: writes a `cancel_requested` audit hint, lets the broker's actual `Cancelled` event flip `orders.status`). Modify mirrors it.

**Migration concern:** existing rows with status from 5b are unaffected (we're inserting a new enum value via `ALTER TYPE ADD VALUE 'modified' AFTER 'submitted'` — see §4). The Pydantic `Literal` type extends; existing migrations 0004-0005 don't touch `order_status_enum`. Backwards-compatible.

---

## 7. Sidecar additions

### 7.1 Proto (`proto/broker/v1/broker.proto`)

```protobuf
service Broker {
  // ... existing rpcs ...
  rpc ModifyOrder(ModifyOrderRequest) returns (ModifyOrderResponse);
  rpc PlaceBracket(PlaceBracketRequest) returns (PlaceBracketResponse);
}

message ModifyOrderRequest {
  string broker_order_id = 1;
  string account_number = 2;
  Contract contract = 3;
  OrderSide side = 4;
  OrderType order_type = 5;
  TimeInForce tif = 6;
  string qty = 7;
  Money limit_price = 8;
  Money stop_price = 9;
  string client_order_id = 10;
}

message ModifyOrderResponse {
  string broker_order_id = 1;
  string status = 2;
}

message PlaceBracketRequest {
  PlaceOrderRequest parent = 1;
  PlaceOrderRequest stop_loss = 2;
  PlaceOrderRequest take_profit = 3;
  string oca_group = 4;
  bool has_stop_loss = 5;
  bool has_take_profit = 6;
}

message PlaceBracketResponse {
  string parent_broker_order_id = 1;
  string stop_loss_broker_order_id = 2;
  string take_profit_broker_order_id = 3;
  string status = 4;
}

message OrderEventMessage {
  // ... existing fields ...
  string exec_id = 9;
}
```

### 7.2 `sidecar/handlers.py`

**`ModifyOrder` handler:**
- Real broker path: int-parse `broker_order_id` → look up in `self.ib.openTrades()` (or `self.ib.trades()` for completed) by `permId`. Build a fresh `ib_async.Order` with the same `orderId` and the new fields. Call `self.ib.placeOrder(contract, order)`. Return new status from the trade response.
- SIM path: reject with gRPC `INVALID_ARGUMENT` ("modify on simulator orders not supported in v1"). Document in CHANGELOG.
- Errors: orderId not found → gRPC `NOT_FOUND`. ib_async raises → gRPC `UNKNOWN`.

**`PlaceBracket` handler:**
- Build 3 `ib_async.Order` objects from the protos.
- Set `parent.transmit = False`.
- Apply oca_group to children: `child.ocaGroup = oca_group; child.ocaType = 1` (cancel-on-fill — per IBKR docs OCA type 1 = "cancel all remaining orders with block").
- For SL child: `parent_id` field set to parent's IB orderId after parent's placeOrder. Same for TP child. Wire-order: place parent first (transmit=False) → IBKR assigns orderId → set children.parentId → place children (last child has transmit=True).
- SIM mode: mint 3 SIM-uuids, register each in `_sim_orders` with `parent_sim_id` field added (so cancel echo can cascade SIM children too — minor extension to B2's synthetic Trade emit).
- Return all 3 broker_order_ids.

**`_proto_event_from_trade` extension:**
- When the trade has fills, emit one OrderEventMessage per fill with `exec_id = fill.execution.execId`.
- Status-only events (PreSubmitted, Submitted, Modified, Cancelled) emit a single message with `exec_id = ""`.

---

## 8. Frontend additions

### 8.1 `TradeTicketModal` (`patterns/`)

**New `mode` prop:** `"place" | "modify" | "bracket"` (default `"place"` preserves existing behavior).

**Mode behaviors:**

| Field | place | modify | bracket |
|---|---|---|---|
| account_id | editable | disabled (pre-filled) | editable |
| conid (search) | editable | disabled | editable |
| side | editable | disabled | editable |
| order_type | editable | disabled | editable |
| qty | editable | editable | editable |
| limit_price | editable | editable | editable (= entry price) |
| tif | editable | editable | editable |
| stop_price | editable (when STOP/STOP_LIMIT) | editable (when STOP/STOP_LIMIT) | always editable (optional SL leg) |
| target_price | hidden | hidden | always editable (optional TP leg) |
| Submit button | "Place order" | "Modify" | "Submit bracket" |
| API call | POST `/orders` | PUT `/orders/{id}` | POST `/orders/bracket` |

**Always-fresh-nonce:** preview RPC re-fires on every keystroke (debounced 300ms). Submit button disabled until the latest preview's nonce is in hand.

**Validation (bracket mode, BUY example):**
- `stop_price >= limit_price` → red error "stop must be below entry"
- `target_price <= limit_price` → red error "target must be above entry"
- Both empty → red error "bracket needs at least one of stop or target"

### 8.2 `useFillsHistory` hook (`hooks/`)

```typescript
export function useFillsHistory(params: {
  accountId: string;
  from: string;
  to: string;
  pageSize?: number;
}): {
  fills: Fill[];
  isLoading: boolean;
  error: ApiError | null;
  loadMore: () => void;
  hasMore: boolean;
}
```

Cursor pagination via `next_cursor`. Stale-while-revalidate cache via Zustand store.

### 8.3 `FillsTable` (`patterns/`)

Columns: executed_at | symbol | side | qty | price | commission | total. Sticky header; date-grouped sections for multi-day ranges.

### 8.4 `OrdersPage` extension

Each non-terminal row gets a "Modify" button next to the existing "Cancel" button. Clicking opens `TradeTicketModal` in `mode="modify"` with the row's data pre-filled.

### 8.5 New TanStack route `/orders/$id/fills`

Renders `FillsTable` scoped to one order's fills. Reachable from `OrderDetailPage` via a "View executions" link.

---

## 9. Testing strategy

Mirrors 5b.1's layered approach — mock E2E on every PR, real-IBKR nightly.

### 9.1 Backend unit (pytest, prod DB, outer-rollback fixture)

- `tests/api/test_orders_modify.py` — 8 tests
- `tests/api/test_orders_bracket.py` — 6 tests
- `tests/api/test_fills.py` — 5 tests
- `tests/services/test_order_event_consumer_fills.py` — 4 tests
- `tests/migrations/test_0006_brackets_fills.py` — 7 tests

### 9.2 Sidecar unit (pytest, MagicMock)

- `tests/test_handlers_modify_order.py` — 4 tests (real path map, SIM rejection, NOT_FOUND, simulator-mode rejection)
- `tests/test_handlers_bracket.py` — 5 tests (parentId wiring, transmit ordering, OCA group propagation, > 2 children rejected, SIM mints 3 SIM-uuids)

### 9.3 Mock E2E (extends `e2e-mock.yml`)

- `backend/tests/integration/test_e2e_modify_chain.py` — 5-step: preview → place → preview-modify → PUT → cancel
- `backend/tests/integration/test_e2e_bracket_chain.py` — 4-step: preview-bracket → POST `/api/orders/bracket` → cancel parent → all 3 rows transitioned to cancelled
- Extends `backend/tests/fixtures/sidecar_servicer.py` with `ModifyOrder` + `PlaceBracket` mock implementations + OCA cascade behavior on `CancelOrder`

### 9.4 Real-IBKR E2E (extends `nightly-real-ibkr.yml` `e2e-trade` job)

- `sidecar/tests/test_real_ibkr_e2e_modify.py` — modify a paper LIMIT order's price, verify `modified` → `cancelled`
- `sidecar/tests/test_real_ibkr_e2e_bracket.py` — place a paper bracket on BARC GBP (small qty, far-from-market prices), verify all 3 broker_order_ids assigned, cancel parent, verify all 3 cancel

### 9.5 Frontend (Vitest + React Testing Library)

- `TradeTicketModal.test.tsx` — 6 new tests
- `useFillsHistory.test.ts` — 3 tests
- `OrdersPage.test.tsx` — 2 new tests

---

## 10. Prometheus alerts (new in `deploy/prometheus/alerts.yml`)

```yaml
- alert: BrokerOrderModifyP99HighWarning
  expr: histogram_quantile(0.99, rate(broker_order_modify_duration_ms_bucket[5m])) > 1500
  for: 5m
  labels: { severity: warning }
  annotations:
    summary: "Order modify p99 > 1500ms"
    description: "Modify path latency degraded; check broker channel + state-machine validation."

- alert: BrokerBracketCascadeLag
  # HIGH-4 architect-review fix: metric source defined in the consumer.
  # When _process_event handles a status='cancelled' event for a row whose
  # parent_order_id IS NOT NULL, observe the cascade latency:
  #   broker_bracket_cancel_cascade_seconds.observe(
  #     (event.broker_event_at - parent.cancel_requested_at).total_seconds()
  #   )
  # Buckets: [0.1, 0.5, 1, 2, 5, 10, 30] seconds.
  expr: |
    histogram_quantile(0.99,
      rate(broker_bracket_cancel_cascade_seconds_bucket[5m])
    ) > 5
  for: 10m
  labels: { severity: warning }
  annotations:
    summary: "Bracket cancel cascade > 5s p99"
    description: "After parent cancel, children took > 5s to reach cancelled status. Broker OCA propagation may be degraded."

- alert: BrokerPendingFillsBacklog
  # CRIT-2 architect-review fix: counter on the pending_fills sweeper.
  # Rows in pending_fills older than 5 min indicate a chronic order-row miss.
  expr: pending_fills_backlog_count > 0
  for: 5m
  labels: { severity: warning }
  annotations:
    summary: "pending_fills has rows older than 5 min"
    description: "Fills cannot be drained because their broker_order_id has no matching orders.id. Manual reconciliation may be needed."

- alert: BrokerFillsWriteFailures
  expr: increase(broker_fills_write_failed_total[15m]) > 0
  for: 1m
  labels: { severity: page }
  annotations:
    summary: "Fills write failures detected"
    description: "Consumer failed to insert fill rows (FK violation or constraint check). Audit trail at risk."
```

---

## 11. Work decomposition (rough — writing-plans skill will refine)

| Chunk | Owner | Scope |
|---|---|---|
| **A — Schema + proto** | Codex | Alembic 0006, proto extension, backend stub regen, sidecar stub regen, migration tests |
| **B — Sidecar handlers** | Codex | ModifyOrder, PlaceBracket, exec_id on events, sidecar unit tests |
| **C — Backend service + endpoints** | Codex (mechanical) + Claude (modify orchestration) | modify_order/place_bracket/list_fills services, 3 endpoints + 1 extension, OpenAPI snapshot lock, Pydantic models |
| **D — Consumer fills extension** | Codex | `_process_event` extension, idempotent ON CONFLICT, fills row, commission backfill via `commissionReport` callback |
| **E — E2E tests + workflow updates** | Claude | mock_servicer extension, mock E2E tests, real-IBKR test files, workflow YAML updates |
| **F — Frontend** | Codex (modal mechanics) + Claude (mode-prop refactor) | TradeTicketModal mode prop, useFillsHistory, FillsTable, OrdersPage Modify button, `/orders/$id/fills` route |
| **G — Prometheus alerts + close-out** | Claude | alerts.yml (incl. `BrokerBracketCascadeLag` + `BrokerPendingFillsBacklog` from architect-review), regenerate OpenAPI snapshot (rename test fn to `test_openapi_schema_lock_phase5c`, refresh syrupy file in `tests/api/snapshots/`), regenerate frontend types via `cd frontend && pnpm gen-types`, CHANGELOG, TASKS.md, CLAUDE.md (incl. Step 3 wording update: "apply CRIT+HIGH+MED inline" per `feedback_architect_findings_apply_through_medium.md`), memory phase5b_shipped → phase5c_shipped, tag v0.5.4 |

Estimated 5-7 working days, similar shape to 5b.1.

---

## 12. Risk register (pre-architect-review)

These will be the items the architect-review skill is invited to challenge / extend.

| # | Risk | Mitigation in design |
|---|---|---|
| R1 | Modify on a partial-filled bracket parent could orphan filled qty in children | 409 `bracket_parent_partial` rejection ahead of any RPC |
| R2 | Concurrent execDetails replay (R9 reconnect) duplicates fills rows | `exec_id UNIQUE` + `ON CONFLICT DO NOTHING`, no metric (normal during resync) |
| R3 | Bracket children's parentId not yet known when first child's placeOrder fires | Wire-order: parent first (transmit=False) → ib_async assigns orderId synchronously → set children.parentId → children.placeOrder |
| R4 | Modify race vs broker-side fill (operator modifies as fill is in-flight) | State-machine predicate stays — broker emits filled with higher status, beats out the modified write; final state correct |
| R5 | OCA group cleanup never happens | Document as 5d/5e housekeeping job; oca_group is a string column, no FK, infinite retention is harmless |
| R6 | Frontend always-fresh-nonce + 300ms debounce → preview spam | Backend's nonce store is in-memory single-process; expected ~10 previews per modify session, no scaling concern |
| R7 | exec_id format varies by broker | IBKR-only in v1; schema is `VARCHAR(64)` flexible enough for future Schwab/Futu when those brokers' fills land |
| R8 | Fills accumulate without retention | v1 keeps everything; 5d/5e adds optional partition-by-month for archival. Not v1's problem. |

---

## 13. Architect review — applied

ARCHITECT-REVIEW skill invoked adversarially against this spec on 2026-04-28. Per project memory `feedback_architect_findings_apply_through_medium.md`, the must-fix-inline tier is **CRITICAL + HIGH + MEDIUM** (LOWs defer or document).

| # | Severity | Finding | Resolution | Spec section |
|---|---|---|---|---|
| CRIT-1 | CRITICAL | `modified` state machine relied on a non-existent `status > current` predicate; backward transitions possible from `modified` to `submitted` | Added `order_status_rank()` SQL function in Alembic 0006; consumer's `_update_order` SET clause now rejects rank-decreasing transitions | §4 (schema), §6 (state machine), §6.1 (transition table) |
| CRIT-2 | CRITICAL | `execDetails` arriving before the order row → FK violation → fill silently lost (since `reconcile_at_startup` only runs at boot, not per-tick) | Added `pending_fills` buffer table + 30s sweeper drain alongside `PendingSubmitWatchdog`; FK fallback INSERT into `pending_fills` on `fills` violation | §3.3, §4 (schema) |
| HIGH-1 | HIGH | Modify idempotency mechanism unspecified — replay could either double-modify or 409 unhelpfully | Added §5.1.1: 60-second per-`(order_id, nonce)` response cache; hot replay returns cached 200, cold replay returns 409 (forces re-preview) | §5.1.1 |
| HIGH-2 | HIGH | Bracket pre-RPC INSERT of all 3 rows leaves 3 stale `pending_submit` rows on sidecar failure (3× single-leg surface) | Added §5.2.1: two-phase commit. INSERT parent only (1 row exposure); call PlaceBracket; on success INSERT 2 children + UPDATE parent in one tx | §5.2.1 |
| HIGH-3 | HIGH | HTTP modify handler writing `orders.status='modified'` synchronously races the broker stream's update; temporal predicate could leave the row stuck on the optimistic write | Added §6.2: HTTP-side writes only the `order_events` audit row; consumer owns `orders.status` mutation. PUT response synthesizes the projected state from the just-written event | §6.2 |
| HIGH-4 | HIGH | `BrokerBracketCascadeLag` alert referenced a metric (`broker_bracket_cancel_cascade_seconds_bucket`) that wasn't defined anywhere | Defined the metric in the consumer: on cascade-cancel events for child rows, observe `(event.broker_event_at - parent.cancel_requested_at)` into the histogram. Added supporting `BrokerPendingFillsBacklog` alert for CRIT-2 | §10 (alerts.yml YAML comment shows source) |
| MED-1 | MEDIUM | Modify on bracket child while parent is partial — design intent allowed it but spec table only enumerated parent rejection | Added §5.1.2: explicit child-modify allowance with validation pseudocode. Test `test_orders_modify.py::test_modify_child_when_parent_partial` (positive case) added to §9.1 list | §5.1, §5.1.2 |
| MED-2 | MEDIUM | Bracket §3.2 ASCII diagram showed "placeOrder x N" suggesting a batched loop; only §7.2 narrative correctly described parent-first sequencing | Replaced §3.2 diagram fragment with explicit per-leg sequencing (parent transmit=False → orderId assigned → child SL parentId set → SL transmit=False → child TP parentId set → TP transmit=True) | §3.2 |
| MED-3 | MEDIUM | `modified` allowed transitions diagram in §6 incomplete | Added §6.1 explicit transition table including the "NOT ALLOWED" backward-rank rows (`modified → submitted`, `modified → pending_submit`) | §6.1 |
| MED-4 | MEDIUM | Bracket idempotency on parent's `client_order_id` was implicit; partial-replay child duplication risk | Added §5.2.2: explicit replay paths (parent+all children present → return cached; parent only → DELETE + retry; absent → fresh) | §5.2.2 |
| MED-5 | MEDIUM | `commissionReport` callback handling under-specified — subscription source, where the SQL UPDATE lives, race when commission arrives before fill | Added §3.3.1: explicit kind=`commission_report` event variant in proto, consumer's UPDATE on `fills.exec_id`, in-memory `commission_buffer` for the inverse race | §3.3.1 |
| LOW-1 | LOW | OpenAPI snapshot lock surface needs explicit checklist item | Added to §11 chunk G: regenerate snapshot, rename test fn to `test_openapi_schema_lock_phase5c`, refresh syrupy snapshot file, regenerate frontend types | §11 chunk G |
| LOW-2 | LOW | `exec_id VARCHAR(64)` length not justified | Added schema comment: "IBKR execId is ~31 chars; 64 leaves headroom for Schwab/Futu" | §4 (fills table) |
| LOW-3 | LOW | §13 needs population | This section, populated 2026-04-28 | §13 |

**All findings resolved inline.** No deferred items beyond the LOWs (which were documented in this section).

---

## 14. Tag + close-out

- **Tag at end:** `v0.5.4`
- **CHANGELOG block:** `## [0.5.4] — 2026-05-XX`
- **TASKS.md:** flip Phase 5c row; add 5d items if architect-review surfaces deferrable scope
- **CLAUDE.md:** retitle Phase 5b/5b.1 subsection, add "Phase 5c — Advanced order types (v0.5.4)" subsection with the bullet-list pattern from 5a/5b/5b.1
- **Memory:** new `phase5c_shipped.md`; index in `MEMORY.md`
