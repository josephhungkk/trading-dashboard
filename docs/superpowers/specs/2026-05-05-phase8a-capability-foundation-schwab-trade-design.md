# Phase 8a — Capability-Map Foundation + Schwab Trade Write-Path

**Status:** Spec approved + architect-review applied (CRIT+HIGH+MED inline)
**Date:** 2026-05-05
**Tag target:** v0.8.0
**Predecessor specs:** `2026-04-27-phase5b-trade-execution-design.md`, `2026-04-28-phase5c-advanced-orders-design.md`, `2026-04-30-phase7a-schwab-connect-design.md`

## Architect-review applied (2026-05-05)

3 CRIT + 6 HIGH + 8 MED applied inline. 4 LOW deferred per user directive.

| Tag | Severity | Title | Applied where |
|---|---|---|---|
| C1 | CRIT | OrderPoller key drift between sidecar (`account_hash`) and backend (`gateway_label, account_id`) | §4 supervisor key |
| C2 | CRIT | Sidecar restart re-emits `submitted` for in-flight orders (in-process state cache) | §4 persistent Redis cache + backend dedup |
| C3 | CRIT | Capability check ordering would invert 5b kill-switch invariant | §2 explicit ordering (kill_switch → maintenance → capability → dispatch) |
| H1 | HIGH | Capability scope `broker_id` vs `gateway_label` ambiguity | §2 explicit per-broker rule + forward-compat note |
| H2 | HIGH | No drift detector between matrix and actual Schwab behavior | §10 weekly drift test |
| H3 | HIGH | Modify-chain link via free-form `kind` string instead of existing `parent_order_id` FK | §6 + §7 use 5c column |
| H4 | HIGH | Token refresh latency unbudgeted in histograms | §5 pre-warm + §11 extended buckets |
| H5 | HIGH | Week-on-week alert threshold meaningless during low/no activity | §11 activity-aware threshold |
| H6 | HIGH | `account_hash` rotation handling missing | §4 invalidate + tear-down on rotation event |
| M1 | MED | `notes` XSS surface (operator free text) | §3 CHECK constraint + §9 React text-node escaping |
| M2 | MED | Admin POST PATCH-vs-PUT semantics undefined | §2 PUT-semantics |
| M3 | MED | C0 outcome not captured as committed artifact | §10 JSON artifact requirement |
| M4 | MED | Admin could insert `order_types.code` not in proto | §2 admin code-set guard |
| M5 | MED | Pubsub failure leaves caches silently inconsistent | §11 `_pubsub_failures_total` metric + alert |
| M6 | MED | Drift detector quota burn | §10 weekly + quota guard |
| M7 | MED | Admin endpoint missing 5b CSRF nonce | §2 require nonce |
| M8 | MED | Coverage exemptions weak (testable with mock clocks) | §10 drop exemptions, parameterize |

---

## 0. Why this phase exists

Phase 7a shipped Schwab read-only OAuth + account/position discovery (`v0.7.0`). Schwab sidecar's six write/stream RPCs (`PlaceOrder`, `CancelOrder`, `ModifyOrder`, `PlaceBracket`, `OrderEvent`, `SearchContracts`) currently return gRPC `UNIMPLEMENTED` (`sidecar_schwab/handlers.py:241-272`).

At the same time, ROADMAP architectural pillar #3 locks: **"OrderType + TimeInForce are DB-driven enums + per-broker capability map, not Python `Literal`."** Today `app/brokers/base.py:29-30` declares both as Python `Literal`s. With Phase 8 expanding the order-type universe across IBKR + Futu + Schwab + Alpaca, the capability matrix must land before the new types — otherwise each broker would re-litigate validation logic in its own sidecar.

Phase 8a brings both into being: the capability foundation (broker-agnostic) and the Schwab single-leg trade write-path (foundation's first non-trivial consumer). 8b adds order-type expansion and Futu modify/bracket; 8c adds Alpaca trade.

---

## 1. Scope

### In scope
- New tables `order_types`, `time_in_force`, `broker_order_capability` (Alembic 0011).
- Full cross-product capability matrix seeded for IBKR + Futu + Schwab + Alpaca (200 rows total; ~24 supported on day one).
- `OrderCapabilityService` with 60s in-process cache + Redis-pubsub invalidation.
- `GET /api/brokers/{broker_id}/capabilities` endpoint.
- Capability-gate inserted into `OrderService.preview_order` / `place_order` / `modify_order` (HTTP 422 on unsupported combo).
- Schwab sidecar implementations of: `PlaceOrder`, `CancelOrder`, `ModifyOrder`, `OrderEvent` (server-streaming via adaptive poller), `SearchContracts`, `GetOrders` (extended with `from_ts`/`to_ts`).
- Adaptive order-event poller (2s active / 30s idle) per `(account_hash)` in sidecar.
- SIM-mode echo for Schwab (mirrors IBKR 5b.1 pattern).
- Frontend `useBrokerCapabilities` hook; `TradeTicketModal` lazy-disable + tooltip UX.
- 6 Schwab metrics + 6 capability metrics; 6 new alerts.
- C0 empirical paper-account script as a hard gate before frontend / capability-flip work begins.
- Operator runbook for Schwab trade canary.

### Out of scope (deferred to 8b / 8c)
- Schwab brackets (`PlaceBracket`) and Schwab `complexOrderStrategyType=TRIGGER` / `OCO`.
- TRAIL, TRAIL_LIMIT, MOC, MOO, LOC, LOO across any broker (rows seeded `is_supported=false`).
- GTD across any broker.
- IBKR / Futu capability flips for new types.
- Futu Modify + Bracket (Phase 6 deferral; lands in 8b).
- Alpaca trade write-path (8c).
- Multi-worker uvicorn (still Phase 9; single-worker assumption preserved).
- Schwab daily/weekend maintenance envelope (rely on REST 5xx propagation; revisit in 8b if canary surfaces pain).

---

## 2. Architecture & topology

### Workstream 1 — Capability foundation (broker-agnostic)
- **Tables:** `order_types(code PK, label, description, sort_order, created_at)`, `time_in_force(code PK, label, description, requires_expiry, sort_order, created_at)`, `broker_order_capability(broker_id, order_type, time_in_force, is_supported, notes, updated_at)` with composite PK.
- **Service:** `app/services/order_capability_service.py` exposes `is_supported(broker_id, order_type, tif) -> bool` + `list_capabilities(broker_id) -> CapabilitiesResponse`. 60s in-process LRU cache keyed by `broker_id`. Redis pubsub topic `app_config:invalidate:order_capabilities` busts cache.
- **API:** `GET /api/brokers/{broker_id}/capabilities` returns `{order_types: [{code, label, supported, notes}], time_in_force: [...], combos: [{order_type, time_in_force, supported}]}`. Admin write `POST /api/admin/order-capabilities` (operator-only, single-row update + pubsub notify). **PUT-semantics (MED-2):** body REQUIRES all 5 fields `{broker_id, order_type, time_in_force, is_supported, notes}`; partial updates rejected with 400. **CSRF (MED-7):** endpoint requires the same confirmation nonce as Phase 5b trade-execution endpoints (capability flips alter accept/reject of real-money orders, so they're trade-adjacent). **Admin code-set guard (MED-4):** server validates `order_type` and `time_in_force` against existing `order_types.code` / `time_in_force.code` rows; unknown codes → 400 (only Alembic may add new codes).
- **Validation gate ordering (preserves 5b kill-switch invariant — CRIT-3):** `OrderService.preview_order` / `place_order` / `modify_order` execute checks in this strict order:
  1. `broker.kill_switch_enabled` → 503 (Phase 5b H0; first, race-safe).
  2. `compute_broker_maintenance(label).active` → 503 + `Retry-After` (Phase 5b daily-window guard).
  3. `OrderCapabilityService.is_supported(broker_id, order_type, tif)` → 422 with `error.code="unsupported_order_type_for_broker"` + `details.{order_type, time_in_force, broker}` if false.
  4. Broker registry dispatch → sidecar call.
  Capability check is a backend-side validation peer of policy checks, NOT a kill-switch replacement.
- **Capability scope is per-`broker_id`, not per-`gateway_label` (HIGH-1):** all gateway labels for the same broker share capability rows. `schwab-live` and `schwab-paper` both look up `broker_id='schwab'`. If per-mode divergence emerges later (e.g. paper supports a type that live doesn't), the schema migrates by adding nullable `gateway_label_filter VARCHAR(64)` to `broker_order_capability` PK; null = "all labels for this broker". 8a leaves the column out for simplicity.
- **Tier invariant:** proto enum ⊇ DB `order_types.code` ⊇ rows where `is_supported=true`. The Python `Literal` in `app/brokers/base.py` stays as wire-type contract; DB tables are the source of truth for *capability*, not the type universe. **Admin POST cannot add new `order_types.code` rows (MED-4)** — only Alembic migrations may add codes; admin endpoint validates `order_type` against existing rows and rejects unknown codes with 400.

### Workstream 2 — Schwab trade write-path
Topology unchanged from Phase 7a: schwab-sidecar in-cluster docker on `td-net:9090`, no mTLS, no NUC, no new ports.

New / changed sidecar modules:

| Path | Purpose |
|---|---|
| `sidecar_schwab/handlers.py` (changed) | Flip 6 RPCs from `UNIMPLEMENTED` → live; delegate to client + poller + simulator. |
| `sidecar_schwab/client.py` (changed) | Add `place_order`, `cancel_order`, `replace_order`, `get_orders_since`, `get_order` schwabdev REST wrappers. |
| `sidecar_schwab/normalize.py` (changed) | Add Schwab-order → wire-`Order` and Schwab-order-status → wire-`OrderEventMessage` mappers. |
| `sidecar_schwab/order_poller.py` (new) | Per-account adaptive poll loop (2s active / 30s idle); emits synthetic events through fan-out. |
| `sidecar_schwab/order_state_cache.py` (new) | In-process `dict[client_order_id → last_known_state]`, drives diff loop. |
| `sidecar_schwab/simulator.py` (new) | SIM-prefix detection + synthetic place/cancel/modify echo. |

Backend-side changes:
- `OrderService.preview_order` / `place_order` / `modify_order` — capability gate inserted as first validation step.
- `BrokerRegistry` — add `schwab` entry with RPC-presence flags `{place_order=True, cancel_order=True, modify_order=True, place_bracket=False}`.
- `OrderEventConsumer` — **no code changes**. New gateway labels `schwab-live` / `schwab-paper` slot into existing per-`(label, account)` supervisor.

---

## 3. Data model (Alembic 0011)

```sql
CREATE TABLE order_types (
    code            VARCHAR(32)  PRIMARY KEY,
    label           VARCHAR(64)  NOT NULL,
    description     TEXT         NOT NULL DEFAULT '',
    sort_order      SMALLINT     NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE time_in_force (
    code            VARCHAR(16)  PRIMARY KEY,
    label           VARCHAR(64)  NOT NULL,
    description     TEXT         NOT NULL DEFAULT '',
    requires_expiry BOOLEAN      NOT NULL DEFAULT FALSE,
    sort_order      SMALLINT     NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE broker_order_capability (
    broker_id       VARCHAR(32)  NOT NULL REFERENCES brokers(id) ON DELETE CASCADE,
    order_type      VARCHAR(32)  NOT NULL REFERENCES order_types(code) ON DELETE RESTRICT,
    time_in_force   VARCHAR(16)  NOT NULL REFERENCES time_in_force(code) ON DELETE RESTRICT,
    is_supported    BOOLEAN      NOT NULL DEFAULT FALSE,
    notes           TEXT         NOT NULL DEFAULT ''
        CHECK (notes ~ '^[\x20-\x7E]*$' AND length(notes) <= 256),  -- printable ASCII only, ≤256 chars (MED-1: XSS defense in depth)
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (broker_id, order_type, time_in_force)
);

CREATE INDEX ix_broker_order_capability_supported
    ON broker_order_capability (broker_id) WHERE is_supported = TRUE;
```

### Initial seed (data migration in same Alembic file)
- `order_types`: MARKET, LIMIT, STOP, STOP_LIMIT, TRAIL, TRAIL_LIMIT, MOC, MOO, LOC, LOO (10 rows; `sort_order` 10..100 step 10).
- `time_in_force`: DAY, GTC, IOC, FOK, GTD (5 rows; `requires_expiry=true` only for GTD).
- `broker_order_capability`: 4 brokers × 10 × 5 = 200 rows. `is_supported=true` only for:

| Broker | Supported combos | Notes on unsupported rows |
|---|---|---|
| `ibkr`   | MARKET, LIMIT, STOP, STOP_LIMIT × DAY, GTC, IOC, FOK | "Coming in 8b" for new types; "Coming in 8b" for GTD |
| `futu`   | MARKET, LIMIT × DAY, GTC                              | "Coming in 8b" for STOP/STOP_LIMIT/new types/IOC/FOK/GTD |
| `schwab` | MARKET, LIMIT, STOP, STOP_LIMIT × DAY, GTC, IOC, FOK | "Coming in 8b" for new types; "Coming in 8b" for GTD |
| `alpaca` | none                                                  | "Trade execution lands in Phase 8c" |

### Schema invariants (LOAD-BEARING)
1. `order_types.code` and `time_in_force.code` ⊆ proto enum values — guarded by `test_capability_codes_match_proto.py`.
2. `OrderService` rejects unknown `(broker_id, order_type, tif)` triples with HTTP 422 — never trusts the FE-supplied combo.
3. Redis pubsub topic `app_config:invalidate:order_capabilities` busts the 60s in-process cache on admin write.

No changes to `orders` / `order_events` / `fills` / `pending_fills` (Phase 5b/5c schemas already store full enum strings; capability map is purely a validation/UX layer).

---

## 4. Schwab order-event poller

**Supervisor key (CRIT-1):** Single `OrderPoller` task per `(gateway_label, account_id)` — mirrors Phase 5b `OrderEventConsumer` supervisor key. The Schwab `account_hash` is resolved internally by sidecar from a `(gateway_label, account_id) → account_hash` map populated at Configure time. Backend never references `account_hash`; sidecar never references `account_id` outside the resolver. Registered at first PlaceOrder or OrderEvent stream open.

**Account-hash rotation handling (HIGH-6):** sidecar subscribes to the existing `schwab_account_hash_changed` event (Phase 7a `schwab_account_hash_refresh_total{reason='rotation'}` source). On rotation: invalidate `_PLACE_REPLAY_CACHE` entries for old hash, tear down OrderPoller for old hash, start fresh poller for new hash on next OrderEvent stream re-open. In-flight place/cancel calls during rotation return gRPC `UNAVAILABLE` → backend 503 → operator retry succeeds against new hash.

### Cadence rules
- **Fast tick (2s)** when at least one order is in non-terminal state.
- **Slow tick (30s)** when no in-flight orders. Poller stays alive (1 inactive call ≈ 1 RPM out of Schwab's 120 RPM/account).
- **429 backoff** doubles interval, capped at 60s; reset on next 200.
- Cadence transitions logged via `schwab_order_poller_cadence_changed_total{account, from, to}`.

### Diff algorithm
```
loop:
    rsp = await client.get_orders_since(account_hash, since=last_poll_ts - 5s)
    last_poll_ts = now()
    for schwab_order in rsp:
        normalized = normalize.order_event(schwab_order)
        prev = state_cache.get(normalized.client_order_id)        # Redis-backed (CRIT-2)
        events = diff(prev, normalized)
        for ev in events:
            await fan_out.publish(ev)
        state_cache.put(normalized.client_order_id, normalized)   # write-through to Redis
    sleep(active_tick if any_in_flight() else idle_tick)
```

### Persistent state cache (CRIT-2)
`order_state_cache` is **Redis-backed** (write-through), keyed `schwab:order_state:<gateway_label>:<account_id>` (HASH of client_order_id → JSON-serialized last-known state), TTL 7 days. On sidecar restart, hydrate from Redis before first poll. Defends against the "first poll after restart re-emits `submitted` for every in-flight order" failure mode.

**Backend defense in depth (CRIT-2):** `OrderEventConsumer._process_event` extends 5c rank-predicate to also no-op when `(rank_new == rank_current AND status_new == status_current AND event has no new exec_id)`. Prevents same-rank-same-status echoes (e.g. duplicate `submitted`) from creating spurious `order_events` rows. New event still recorded for audit only when it carries new fill data (exec_id) or a status change.

### Diff rules
1. New `client_order_id` → emit `submitted`.
2. Status change → emit corresponding event. Terminal-sticky enforced backend-side via Phase 5c `order_status_rank`; sidecar emits raw transitions only.
3. New `executionLeg` entries → emit `fill` event per leg with `exec_id` (`executionLeg.legId`); 5c `fills.exec_id UNIQUE` enforces backend dedup.
4. `enteredTime` newer than cache + same `client_order_id` → reset cache (replaced order).

### Window math
5s overlap on each `?fromEnteredTime=` call covers fills landing between iterations. Fan-out is idempotent per `exec_id`, so overlap doesn't double-emit.

---

## 5. RPC handler shapes

### `PlaceOrder`
1. Hash `(account_hash, client_order_id)` → check 60s `_PLACE_REPLAY_CACHE`. Hit → return cached `PlaceOrderResult`.
2. SIM mode (`client_order_id.startswith("SIM-")`) → register in `simulator.SimRegistry`, emit synthetic `submitted` event after 50ms, return placeholder `broker_order_id=f"SIM-{uuid7()}"`.
3. Live mode → `await self._ensure_fresh_token()` → `normalize.to_schwab_order(wire_order)` → `client.place_order(account_hash, payload)` → parse `Location: /orders/{broker_order_id}` header → cache → return `PlaceOrderResult`.
4. `OrderPoller.activate_fast(gateway_label, account_id)` so next tick catches the new order on fast cadence.

**Token pre-warm (HIGH-4):** sidecar lifespan kicks `_ensure_fresh_token()` once at startup AND once per fast-cadence transition (when first in-flight order arrives). Goal: keep p99 of `schwab_place_order_duration_ms` below the 5s histogram knee in steady state. Token-refresh-during-write fallback path remains as defense in depth but is the rare path, not the hot path.

### `CancelOrder`
- SIM cancels self-emit synthetic `cancelled` immediately (after 50ms simulated round-trip).
- Live cancels return `cancel_requested`; poller catches eventual `CANCELED` status.
- 5b cooldown semantics preserved (sidecar 503 → backend rolls cooldown back so operator can immediately retry).

### `ModifyOrder`
- Schwab REST is `PUT /orders/{orderId}` with full replacement body.
- Sidecar fetches current order via `client.get_order`, merges modifiable fields (price, qty, TIF), submits replacement.
- Returns NEW `broker_order_id` (Schwab assigns fresh ID on replace).
- 5c `_MODIFY_REPLAY_CACHE` on backend (keyed `(order_id, nonce)`) handles replay.

### `OrderEvent` (server-streaming)
Subscribes the gRPC client to `fan_out` channel for `(account_hash)`. Yields events as poller emits. Same shape/handshake as IBKR sidecar; backend `OrderEventConsumer` dispatches by gateway-label, no consumer changes.

### `SearchContracts`
Schwab `GET /instruments?symbol=...&projection=symbol-search`. 5min TTL × 1k entry cache. Emits same wire `ContractMatch` shape as IBKR.

### `GetOrders`
Already exists for read-only listing; extends with `from_ts` / `to_ts` filtering (already on the wire, currently unused).

---

## 6. State machine mapping

The 5c state machine (`order_status_rank` + terminal-sticky) handles Schwab natively because Schwab statuses map cleanly:

| Schwab status | Wire status | Rank | Terminal? |
|---|---|---|---|
| `AWAITING_PARENT_ORDER` | `pending_submit` | 0 | no |
| `PENDING_ACTIVATION` | `pending_submit` | 0 | no |
| `QUEUED` | `submitted` | 1 | no |
| `WORKING` | `submitted` | 1 | no |
| `PENDING_CANCEL` | `cancel_requested` | 2 | no |
| `PENDING_REPLACE` | `modify_requested` | 2 | no |
| `FILLED` | `filled` | 4 | yes |
| `CANCELED` | `cancelled` | 4 | yes |
| `REPLACED` | `cancelled` (with `kind="replaced"`; new order's row has `parent_order_id` FK to old — see HIGH-3) | 4 | yes |
| `REJECTED` | `rejected` | 4 | yes |
| `EXPIRED` | `expired` | 4 | yes |

`PENDING_REPLACE` → `modify_requested` reuses the 5c `modified` enum value for the *new* order created by replace. Schwab's "replaced" original gets `cancelled` (terminal); the replacement gets a fresh `client_order_id` and lifecycle of its own.

**Modify chain audit link (HIGH-3):** instead of the prior free-form `kind="replaced_by:<id>"` string, the replacement order's row in `orders` table sets `parent_order_id` (existing 5c self-FK column originally added for brackets) to the **old order's UUID**. The `cancelled` event on the old order uses the canonical enum value `kind="replaced"`. Audit-trail consumers (5c logic) already understand `parent_order_id` semantics — no new query patterns needed. Bracket and replace-chain coexist on the same column because a Schwab replaced order is never simultaneously a bracket parent (Schwab issues a brand-new orderId on replace; the new order would be a NEW bracket if it were one).

If C0 empirical script surfaces additional Schwab statuses, add rank rows + emit warning so we don't silently mis-classify.

---

## 7. Error handling & idempotency

### Idempotency (defense in depth)
1. Backend `OrderService.place_order` — existing 5b `client_order_id` dedup; re-POST returns original row. Single-worker assumption holds.
2. Sidecar `_PLACE_REPLAY_CACHE` — `(account_hash, client_order_id)` → `PlaceOrderResult`, 60s TTL.
3. Schwab `client_order_id` → `enteredTime` mapping — Schwab honors `clientOrderId` server-side; on true-duplicate POST Schwab returns the original orderId.
4. Modify replay — backend 5c `_MODIFY_REPLAY_CACHE` keyed `(order_id, nonce)`, unchanged.

### Error taxonomy

| Failure | Sidecar action | Backend response to FE | Cooldown? |
|---|---|---|---|
| Schwab 401 (token expired mid-call) | `_ensure_fresh_token()` retry once → re-call → if still 401, `UNAUTHENTICATED` | 503 + `Retry-After: 30` | no |
| Schwab 403 (account not authorized) | `PERMISSION_DENIED` | 403 with `error.code="schwab_account_unauthorized"` | no |
| Schwab 429 (rate limit) | per-account semaphore + exp backoff (2s→4s→8s, cap 30s); `RESOURCE_EXHAUSTED` after 3 | 503 + `Retry-After` | no |
| Schwab 4xx other (validation) | `INVALID_ARGUMENT` with parsed message | 400 with structured error body | no |
| Schwab 5xx | `UNAVAILABLE` | 503 + `Retry-After: 5` | yes (cancel only) |
| Schwab timeout (>10s) | `DEADLINE_EXCEEDED` | 503 | yes (cancel only) |
| Sidecar restart mid-flight | gRPC `UNAVAILABLE` | 503 | yes (cancel only) |
| Capability check fails (new) | n/a (caught backend-side before dispatch) | 422 with `error.code="unsupported_order_type_for_broker"` | no |

Phase 5b H2 cooldown invariant preserved: sidecar 5xx / network fail / 401 on `DELETE /api/orders/{id}` REMOVES the cooldown so operator can immediately retry.

### Schwab-specific gotchas (lock these now)
1. **Rate limit:** 120 RPM per `account_hash`. Active-cadence poller (2s) burns ~30 RPM headroom; PlaceOrder + GetOrders during a busy minute can collide. Mitigation: 429 backoff + per-account semaphore (max 4 concurrent calls).
2. **`avg_fill_price_inferred` flag (Phase 7a M2):** when `executionLeg.price` is null, `_record_fill` infers from `quantity × marketValue` and sets the flag — already in proto, surface in `OrderEventMessage.kind="fill"`.
3. **`account_hash` boundary:** sidecar receives `account_hash` from backend Configure; never returns it to FE (already stripped by `AccountService` per Phase 7a).
4. **Replace returns new `orderId`:** ModifyOrder response must carry the new `broker_order_id`. The OLD broker_order_id transitions to `REPLACED` → emit `cancelled` event with `kind="replaced"`; replacement order's `orders` row sets `parent_order_id` FK to old order UUID (HIGH-3 — uses existing 5c column).
5. **Token refresh during a write:** schwabdev's `_sync_tokens()` workaround (Phase 7a M3) must be invoked BEFORE every write call. Add `await self._ensure_fresh_token()` to PlaceOrder / CancelOrder / ModifyOrder front-doors.

---

## 8. SIM mode echo (5b.1 lesson applied)

SIM-prefixed orders never hit Schwab REST. Sidecar maintains `simulator.SimRegistry`:
- `register(client_order_id, order)` on PlaceOrder → emit synthetic `submitted` event after 50ms.
- `cancel(client_order_id)` on CancelOrder → emit synthetic `cancelled` event after 50ms.
- `modify(client_order_id, new_fields)` on ModifyOrder → emit synthetic `modified` event for old + synthetic `submitted` for new.
- No fills emitted (sim mode is for round-trip testing, not P&L).
- TTL: 1h then GC'd. Long-running stale SIM orders harmless until GC.

---

## 9. Frontend UX

### `useBrokerCapabilities(brokerId)` hook
TanStack Query with 5min `staleTime`. Cache keyed `["broker-capabilities", brokerId]`. Invalidated on broker change in modal and on `app_config:invalidate:order_capabilities` Redis pubsub event (forwarded via existing `/api/sse/config_stream`).

### `TradeTicketModal`
- On mount + on account change, call `useBrokerCapabilities(account.broker_id)`.
- Render ALL order types + TIFs in dropdowns (full universe).
- Disable unsupported items with tooltip from `notes` field ("Coming in 8b" / "Trade execution lands in Phase 8c" / etc.). **Notes rendered via React's default text-node escaping (no `dangerouslySetInnerHTML`); paired with backend CHECK constraint (MED-1) for defense in depth.**
- Combo validation: if the current `(order_type, tif)` selection is unsupported, disable Submit + show inline error "Schwab does not support STOP_LIMIT + IOC".

### Storybook
- New `TradeTicketModal--SchwabAccount` story.
- New `TradeTicketModal--CapabilityLoading` story.
- New `TradeTicketModal--CapabilityError` story.

---

## 10. Testing strategy

### Unit (pytest, no network)
- `test_capability_codes_match_proto.py` — DB ⊆ proto.
- `test_order_capability_service.py` — cache hit/miss, Redis pubsub invalidation, unknown-broker fallthrough.
- `test_order_service_capability_gate.py` — `preview_order` / `place_order` / `modify_order` 422 on unsupported. ≥1 positive + ≥1 negative per broker.
- `test_schwab_normalize_order.py` — every Schwab status from §6 → wire status; `executionLeg` → fill events with `exec_id`; `avg_fill_price_inferred` flag emission.
- `test_schwab_order_poller_diff.py` — new order, status change, new fill, replaced order, no-change.
- `test_schwab_order_poller_cadence.py` — fast→slow on terminal; slow→fast on submit; 429 backoff escalation + reset.
- `test_schwab_simulator.py` — SIM-prefix detection, synthetic event timing, modify chain (cancel-old + submit-new), GC.
- `test_schwab_handlers_replay_cache.py` — duplicate PlaceOrder within 60s returns cached; Schwab REST mock asserted called exactly once.
- `test_schwab_handlers_token_refresh.py` — 401 → `_ensure_fresh_token` → retry → success.
- `test_schwab_handlers_error_mapping.py` — 401/403/429/4xx/5xx/timeout → expected gRPC status code each.

### Integration (pytest + ASGITransport)
- `test_capabilities_api.py` — `GET /api/brokers/schwab/capabilities` shape + ordering + notes; admin POST → cache bust → next GET reflects change.
- `test_orders_capability_gate_e2e.py` — `(schwab, TRAIL, DAY)` → 422; `(schwab, MARKET, DAY)` → 201.
- `test_schwab_sidecar_mock_chain.py` — `FakeSchwabServicer` exercises full place→event→fill→list cycle.
- `test_schwab_modify_chain.py` — modify produces cancel-of-old + submit-of-new with `replaced_by:` link.
- `test_schwab_sim_cancel_echo.py` — SIM order goes through full place→cancel without strand (5b.1 regression guard).

### Frontend (Vitest + RTL)
- `useBrokerCapabilities.test.tsx` — fetch, cache per broker, refetch on broker change.
- `TradeTicketModal.capability-aware.test.tsx` — Schwab account → STOP_LIMIT enabled, TRAIL grayed; switching to IBKR → TRAIL still grayed; submit disabled when current `(type, TIF)` unsupported.

### E2E mock (`e2e-mock.yml` CI workflow)
- `test_e2e_schwab_place_cancel.py` — full chain via SSE.
- `test_e2e_schwab_modify_chain.py` — POST → modify → assert original cancelled + new submitted + audit link.
- `test_e2e_capability_gate.py` — POST unsupported combo for each of 4 brokers → 422.

### Nightly real-Schwab (`nightly-real-schwab.yml`, 12:00 UTC, marked `@pytest.mark.real_schwab`)
- `test_real_schwab_e2e_place_cancel.py` — paper account, $1 limit BUY of cheap symbol far from market → assert `submitted` arrives ≤5s → cancel → assert `cancelled` ≤30s. Finally-block hard-cancels via REST in case of mid-test crash.
- `test_real_schwab_e2e_modify.py` — place → modify price down → old cancelled + new submitted → cancel new.
- `test_real_schwab_capability_gate.py` — submit `(schwab, MARKET, FOK)` → assert Schwab REST also rejects (validates our matrix matches Schwab's actual surface).

### Weekly real-Schwab matrix drift detector (HIGH-2 + MED-6) — `weekly-real-schwab-drift.yml`, Sundays 12:00 UTC
- `test_real_schwab_capability_drift.py` — iterates `is_supported=true` rows for `broker_id='schwab'`, submits a tiny $1 limit order for each `(order_type, tif)` combo far from market, asserts Schwab REST does NOT return capability-rejection error (other rejections like "outside market hours" are accepted). Finally-block cancels each. **Quota guard:** test skips with WARN if `schwab_http_requests_total{status="429"}` from prior 1h > 50% of total. **On failure:** PAGE the operator — Schwab silently changed support for a combo we're advertising as supported.

### C0 — Empirical paper-account script (HARD GATE)
**Before any frontend work or capability-flip migration runs**, execute `scripts/empirical/schwab_place_cancel_paper.py` (raw schwabdev, no sidecar code) on a paper account. Asserts:
1. Schwab REST returns `Location: /orders/{id}` header on place (we depend on this for `broker_order_id` extraction).
2. `clientOrderId` actually round-trips (we depend on this for idempotency).
3. `executionLeg` shape matches what `normalize.py` expects (we depend on this for fill events).
4. Status string set is the 11 documented in §6 (no surprise statuses).

**Artifact (MED-3):** script writes `scripts/empirical/artifacts/schwab_c0_<UTC-timestamp>.json` capturing: actual Schwab status set encountered, executionLeg JSON shape, clientOrderId echo result, response Location header parse outcome. Artifact committed alongside the C0-gate task close-out as evidence (mirrors Phase 5b.1 C1 BASE-tag empirical script pattern). Future re-runs append new files; the most recent governs.

If C0 fails on any → re-spec before continuing implementation. If C0 surfaces additional statuses → add rank rows + emit warning.

### Coverage target
80% on new code (project minimum). **No exemptions (MED-8):** `simulator.py` GC tested via `freezegun`-driven mock clock; `order_poller.py` 429 backoff parameterized via `pytest.mark.parametrize` over (status_code, attempt, expected_delay) tuples.

### OpenAPI snapshot lock
`test_openapi_schema_lock_phase8a` extends 5b/5c lock-list with: `BrokerCapabilitiesResponse`, `OrderTypeRow`, `TimeInForceRow`, `CapabilityComboRow`. Frontend `pnpm check:types-up-to-date` CI gate catches drift.

---

## 11. Observability

### New metrics (in `app/core/metrics.py`)

Schwab-specific (6):
- `schwab_order_poller_iterations_total{gateway_label, account_id, cadence}` — counter.
- `schwab_order_poller_cadence_changed_total{gateway_label, account_id, from, to}` — counter.
- `schwab_place_order_duration_ms` — histogram (50/100/250/500/1000/2500/5000/**10000/30000** — extended per HIGH-4 to absorb token-refresh tail latency).
- `schwab_cancel_order_duration_ms` — histogram (same buckets).
- `schwab_modify_order_duration_ms` — histogram (same buckets).
- `schwab_order_event_emitted_total{kind}` — counter.

Capability foundation (6):
- `order_capability_check_total{broker, result}` — counter (`result` ∈ {supported, unsupported, unknown_broker}).
- `order_capability_cache_hits_total{broker}` — counter.
- `order_capability_cache_misses_total{broker}` — counter.
- `order_capability_admin_writes_total` — counter.
- `order_capability_pubsub_invalidations_total` — counter.
- `order_capability_pubsub_failures_total` — counter (MED-5: incremented when Redis pubsub publish raises; defends against silent cache inconsistency).

### New alerts (`deploy/prometheus/alerts.yml` `phase8a_schwab_trade` group, 6 alerts)
- `SchwabOrderPollerStalled` — fast-cadence account with no iterations in 90s — **page**.
- `SchwabPlaceOrderErrorRateHigh` — >10% non-2xx schwabdev calls over 5min — warning.
- `SchwabOrderEventGapNoActivity` — `any_in_flight()` gauge > 0 AND `schwab_order_event_emitted_total` rate == 0 for 5min — warning (HIGH-5: replaces week-on-week comparison; activity-aware so no false positives during quiet hours).
- `OrderCapabilityCacheChurn` — >100 cache invalidations in 1h — warning.
- `OrderCapabilityCheckUnknownBroker` — any `unknown_broker` result — **page**.
- `OrderCapabilityPubsubFailures` — `order_capability_pubsub_failures_total` increases over 5min — warning (MED-5: silent cache-inconsistency canary).

---

## 12. Rollout sequence (smallest-blast-radius first)

1. **Foundation lands first** — Alembic 0011 + `OrderCapabilityService` + `GET /api/brokers/{id}/capabilities` + capability gate in `OrderService`. Existing IBKR/Futu trade flows pass through new gate (their existing combos seeded `is_supported=true` → zero behavior change). Frontend modal still works.
2. **Sidecar wiring** — `sidecar_schwab/handlers.py` flips 6 RPCs UNIMPLEMENTED → live. Schwab capability rows still `is_supported=false` until step 4.
3. **C0 empirical gate** — paper-account live-fire script. PASS → continue. FAIL → re-spec.
4. **Capability flip** — Alembic 0011a (data-only follow-up) flips Schwab MARKET/LIMIT/STOP/STOP_LIMIT × DAY/GTC/IOC/FOK from `false` → `true`. Capability cache busted via Redis pubsub.
5. **Operator canary on prod paper** — place + modify + cancel round-trip; document in runbook.
6. **CHANGELOG / TASKS / CLAUDE.md / memory close-out + tag `v0.8.0`.**

Steps 1, 2, 4 are independent commits / potentially independent PRs. Step 3 is a runtime gate, not code.

---

## 13. Success criteria (close-out gates — all required to ship v0.8.0)

### Foundation gate
- 4 brokers × 10 order_types × 5 TIFs = 200 capability rows present.
- `GET /api/brokers/{id}/capabilities` returns expected combo set per broker.
- Capability check unit + integration tests green; coverage on `OrderCapabilityService` ≥ 80%.
- `test_capability_codes_match_proto.py` green.
- Existing IBKR + Futu trade E2E tests pass unchanged.

### Schwab trade gate
- C0 empirical script PASS on paper account.
- All 6 RPCs respond non-`UNIMPLEMENTED` from `sidecar_schwab/handlers.py`.
- `test_e2e_schwab_place_cancel.py` + `_modify_chain.py` green in CI.
- Nightly real-Schwab E2E green for 3 consecutive nights before tag.
- Operator canary: place + modify + cancel each round-trip in <60s with correct events in `orders` + `order_events` + `fills`.

### UX gate
- Frontend `TradeTicketModal` renders Schwab options correctly (supported = enabled, unsupported = grayed + tooltip).
- `useBrokerCapabilities` hook + Storybook stories shipped.
- No `console.error` / `console.warn` in production browser when opening modal on Schwab account.

### Ops gate
- 6 Schwab + 6 capability metrics emitting correctly (verified via `/metrics` scrape).
- 6 new alerts loaded in Prometheus + visible in Alertmanager.
- Phase 7a Schwab alerts still firing as expected (no regression).
- `deploy/runbook-schwab-trade.md` exists.

### Docs gate
- `CLAUDE.md` updated (new section: Phase 8a invariants — capability matrix authority, sidecar poller cadence rules).
- `CHANGELOG.md` updated (v0.8.0 entry).
- `TASKS.md` updated (8a closed, 8b/8c open with cross-refs).
- New memory file `phase8a_shipped.md` (next session's `phase5b_shipped.md` analogue).

---

## 14. Task estimate (refined in plan)

~28 tasks across 7 chunks:
- **A** — Schema + proto + Alembic 0011 (~4 tasks)
- **B** — `OrderCapabilityService` + capability API + capability gate (~5 tasks)
- **C** — Sidecar handlers + client + normalize (~5 tasks)
- **D** — Order poller + state cache + simulator (~4 tasks)
- **E** — E2E mock + nightly real-Schwab + C0 empirical script (~4 tasks)
- **F** — Frontend hook + TradeTicketModal + Storybook (~4 tasks)
- **G** — Metrics + alerts + runbook + close-out docs (~4 tasks)

~1 week impl, mirrors 5b/5c/6 cadence (single-worker assumption preserved).

---

## 15. Reaffirmed deferrals

- Schwab brackets / `complexOrderStrategyType=TRIGGER` / `OCO` → 8b.
- TRAIL / TRAIL_LIMIT / MOC / MOO / LOC / LOO → 8b.
- GTD support across any broker → 8b.
- IBKR / Futu capability flips for new types → 8b.
- Futu Modify / Bracket → 8b.
- Alpaca trade write-path → 8c.
- Multi-worker uvicorn → Phase 9.
- Schwab daily / weekend maintenance envelope → revisit in 8b if canary surfaces pain.
