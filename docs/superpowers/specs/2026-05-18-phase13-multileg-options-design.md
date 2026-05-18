# Phase 13 — Multi-Leg Option Combos (2-leg subset) Design

**Date:** 2026-05-18  
**Status:** Architect-reviewed (v1+v2+v3 all findings applied — APPROVED WITH FIXES)  
**Version:** v0.13.0

---

## 1. Why this phase

Phase 12 shipped single-leg options across IBKR + Schwab (chain only) + Alpaca + Futu HK. Real options users build positions as spreads to manage cost basis, cap max-loss, and reduce assignment risk. Without multi-leg ticketing every spread requires leg-by-leg entry with leg-leg fill risk, and the risk gate cannot reason about net-delta or capped max-loss.

---

## 2. Scope

### In scope (Phase 13 — this spec)

Five 2-leg strategies across IBKR + Schwab + Alpaca:

| Strategy | Legs | Constraints | IBKR BAG | Schwab complexType | Alpaca MLEG |
|---|---|---|---|---|---|
| Vertical (debit / credit) | 2 | same expiry, same P/C, opposite side, different strikes | yes | `VERTICAL` | yes |
| Calendar | 2 | same strike, same P/C, opposite side, different expiry | yes | `CALENDAR` | yes |
| Diagonal | 2 | different expiry AND different strike, same P/C, opposite side | yes | `DIAGONAL` | yes |
| Straddle | 2 | same expiry, same strike, opposite P/C, same side | yes | `STRADDLE` | yes |
| Strangle | 2 | same expiry, different strike, opposite P/C, same side | yes | `STRANGLE` | yes |

### Deferred

- **Phase 13b:** collar (requires stock leg), butterfly (3 legs), condor / iron condor / iron butterfly (4 legs)
- **Phase 13c:** Futu HK combos (no native multi-leg API — needs leg-by-leg with leg-fill rollback)
- **Future:** combo modify endpoint; combo OCA-group bracket exits; combo Greeks aggregation in risk gate; cross-currency combos (deferred — validator already rejects with `currency_mismatch` to keep the gate closed until support is explicit)

---

## 3. Architecture

Three-layer pipeline mirrors single-leg orders flow:

### Layer 1 — Strategy Validator (`combos/strategy_validator.py`)

Pure functions per strategy. Input: list of `LegSpec`. Output: validated `ComboSpec` or raises `ComboValidationError(reason=...)` with a structured reason string (`expiry_mismatch`, `same_strike_required`, `opposite_put_call_required`, `opposite_side_required`, `currency_mismatch`). All same-expiry / opposite-side / strike-spacing rules live here only.

### Layer 2 — P&L Envelope (`combos/pnl_envelope.py`)

Pure functions computing `ComboEnvelope(net_debit_credit, kind, max_loss, max_profit, break_even[])` from a `ComboSpec` + per-leg mid-prices. Bounded-vs-unbounded classification: `max_loss = None` means unbounded (short straddle/strangle is theoretically unlimited).

`break_even[]` cardinality by strategy:
- `[]` — unbounded strategies where break-even is not a fixed price (short straddle/strangle with undefined upper risk)
- `[price]` — single break-even (vertical, calendar, diagonal)
- `[lower, upper]` — two break-evens (long straddle, long strangle)

The TS mirror `computeEnvelope.ts` must use `decimal.js` and produce canonically equal decimal strings (`toFixed(8)` with `ROUND_HALF_EVEN`) matching the shared golden fixtures stored as JSON decimal strings. `ComboPayoffChart` consumes `computeEnvelope.ts` output directly — no parallel `Number` path.

### Layer 3 — Combo Service (`combos/combo_service.py`)

Orchestrator (analog of `orders_service.place_order`):

```
resolve_legs → validate → compute_envelope → evaluate_combo(ctx, mode) (risk gate)
  → mint CSRF nonce (client_combo_id = "combo-{uuid4()}")
  → return PreviewResponse (includes client_combo_id)
  ↓  (on /confirm — FE echoes client_combo_id back)
GETDEL nonce → verify _combo_preview_payload_hash → INSERT combo_orders + order_legs (1 TX)
  → BrokerSidecarClient.place_combo (20s gRPC timeout) → synthesize orders rows
  → store broker_combo_id / broker_order_id / order_legs.order_id → PDT mint (once) → return
```

`client_combo_id` is minted by BE at `/preview`, returned in `PreviewResponse`, echoed by FE in the confirm body. The `UNIQUE(account_id, client_combo_id)` constraint makes confirm idempotent on retry.

**`_combo_preview_payload_hash`**: computed **after** BE instrument resolution on both preview and confirm — the hash inputs use resolved `SymbolRef` fields (symbol + exchange + currency), not the DB `instrument_id` BIGINT (which the FE never sees). Canonicalize legs sorted by `leg_idx`, then `(side, symbol, exchange, currency, option_hint.expiry, option_hint.strike, option_hint.put_call, ratio, qty, position_effect)` per leg; SHA-256 the resulting JSON; store in Redis alongside nonce; compare on confirm. This ensures `payload_drift 409` fires only on genuine leg-content changes, not on internal ID vs SymbolRef boundary mismatches.

**gRPC timeout:** `PlaceCombo` uses a 20-second client deadline (combos are slower than single-leg on all three brokers). On sidecar 504, `combo_service` queries the sidecar with the `client_combo_id` to recover partial-submit state before returning an error.

### Risk gate extension

**`evaluate_combo(ctx: ComboContext, mode: EvalMode)`** is the new combo-specific entry point in `risk_service.py`. It:

1. Calls **`evaluate_legs_for_combo(legs, mode)`** — runs kill-switch, max-daily-loss, BP buffer, and PDT **checks** (not mints) once at aggregate combo level (aggregated `qty × multiplier × sign` across legs). This avoids double-counting BP or triggering double PDT violations. `_check_options_exposure` still runs **per-leg** and receives `combo_envelope`.

2. Calls **`_check_options_exposure(ctx, combo_envelope=envelope)`** per leg:
   - When `combo_envelope is None`: behavior is bit-for-bit Phase 12; the bounded-combo relaxation path is not entered.
   - When `combo_envelope is not None` and `combo_envelope.max_loss is not None` (bounded combo): uses `envelope.max_loss` as effective exposure instead of full notional of the short leg; relaxes naked-short ladder accordingly.
   - When `combo_envelope is not None` and `combo_envelope.max_loss is None` (unbounded): triggers existing Phase 12 naked-short ladder as before.

3. Calls **`_check_combo_envelope(ctx: ComboContext, mode: EvalMode)`**:
   - `max_combo_loss_native` cap — BLOCK if `envelope.max_loss > limit` (or if `max_loss is None` and account not flagged for naked-margin)
   - `max_combo_net_delta` cap — WARN if `abs(net_delta) > limit`
   - Unbounded strategy BLOCK on non-naked-margin accounts
   - On any BLOCK during `/preview` (i.e. `mode = EvalMode.PREVIEW`), insert a `risk_decisions` audit row with `attempt_kind = 'combo_preview'` and `side = 'combo'` — mirroring Phase 10a's BLOCK-preview audit behaviour.

**PDT:** `evaluate_legs_for_combo` only **checks** whether a PDT violation would occur (reads the counter). The actual PDT counter **mint** happens exactly once in `combo_service.confirm()` **after** `BrokerSidecarClient.place_combo` returns successfully — not before the sidecar call, to avoid minting a PDT counter for an order that never reached the broker. Key shape: `pdt:{account_id}:{underlying_canonical_id}:{ymd}` — same as Phase 12 `_check_pdt`.

**`ComboContext`** extends `EvaluationContext` with `legs: list[LegContext]` and `envelope: ComboEnvelope`. `LegContext` carries the per-leg `EvaluationContext` fields.

### `_combo_native_notional` helper

All 5 in-scope strategies are 1:1 ratio (`ratio = 1` for both legs). The `ratio` field exists for future 13b strategies; for now YAGNI: the formula ignores per-leg ratio and uses 1.

- **Debit combo:** `abs(net_debit_credit) × multiplier` — the maximum cash outflow defines the notional.
- **Credit combo:** `envelope.max_loss × multiplier` — cap-based, not gross short notional; prevents a credit vertical from triggering the BP naked-short check.

### legged_out terminal status

When a combo reaches a state where some legs are filled (positions) and others are cancelled/rejected, it is mechanically impossible to cancel the filled legs — they are now open positions. The correct handling:

1. Cancel any still-working legs via per-leg `cancel_order`.
2. Leave filled legs as open positions.
3. Transition `combo_orders.status` → `legged_out`.
4. Emit a Phase 11b alert (template: `combo_legged_out`).
5. Insert an audit row in `risk_decisions` with `side = 'combo'` and `attempt_kind = 'combo_place'`.
6. If `risk_limits.combo_legout_autoclose = TRUE` (default OFF), submit a market-close order for each filled leg and audit with `attempt_kind = 'combo_autoclose'`. This requires explicit opt-in because it bypasses the standard risk-gate preview flow.

### Fill listener — `combo_fill_listener.py` (dedicated module)

Combo fill handling lives in a **new dedicated module** `backend/app/services/combos/combo_fill_listener.py` rather than inside `oco_orchestrator`. Reason: `oco_orchestrator.process_fill_event` is gated on `_find_link` (OCO pair lookup) returning non-None; combo legs are not OCO pairs, so any branch added inside `oco_orchestrator` would never fire. Combos and OCA are orthogonal concerns — co-locating risks Phase 9 OCO regression.

`combo_fill_listener.handle_fill(order_id, filled_qty, avg_fill_price)` is called by the broker event stream dispatcher **in parallel** with `oco_orchestrator.process_fill_event` (not chained). It:

1. Fetches `orders.combo_id` for the given `order_id`. If `combo_id IS NULL`, returns immediately (not a combo fill).
2. Within a single transaction, acquires `SELECT ... FOR UPDATE` on the `combo_orders` row (standard Postgres row-level lock) to serialise concurrent fills for the same combo.
3. Updates `order_legs.filled_qty` and `order_legs.avg_fill_price` for the matching leg (joined via `order_legs.order_id = order_id`).
4. Recomputes `combo_orders.status` per §4 state machine.
5. If resulting status is `legged_out`, triggers the legged_out handling described above.

### GetSupportedComboStrategies RPC

Each sidecar implements `GetSupportedComboStrategies()` returning the list of `strategy_type` strings it supports at runtime. BE calls this during lifespan startup and reconciles against `broker_features.py`. On mismatch, logs a `structlog` warning. The counter is defined in §10 metrics.

### Modal layout

User confirmed **Option C: stacked legs + payoff chart** inside `TradeTicketModal`. The combo mode is activated via a Strategy toggle (Single / Combo). `ComboBuilder` renders:

- `StrategyPicker` — dropdown (Vertical / Calendar / Diagonal / Straddle / Strangle)
- Two `LegSlot` components — each shows direction badge (BTO/STO), symbol/strike/expiry, bid/ask
- `ComboPayoffChart` — SVG payoff-at-expiry diagram; consumes `computeEnvelope.ts` output (decimal.js), no parallel Number path
- `ComboSummary` — net debit/credit amount, max loss, max profit, break-even price(s)
- Standard Preview → Confirm flow with WARN acknowledge gate and BLOCK rows

**FE state recovery (MED-N3):** On `ComboBuilder` mount, query `GET /api/combos?account_id={id}&status=pending_submit` to recover any in-flight combo (e.g. after browser refresh during `pending_submit`). If found, resume from the confirm step.

---

## 4. Data model — alembic 0049

Chains off `0048_fix_guard_delete_trigger`.

### `combo_orders`

```sql
CREATE TABLE combo_orders (
  id                      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id              UUID          NOT NULL REFERENCES accounts(id),
  client_combo_id         TEXT          NOT NULL,
  strategy_type           TEXT          NOT NULL CHECK (strategy_type IN
                              ('VERTICAL','CALENDAR','DIAGONAL','STRADDLE','STRANGLE')),
  underlying_symbol       TEXT          NOT NULL,
  -- underlying_canonical_id: OCC root symbol (e.g. "AAPL"), FK to instruments where asset_class='STOCK'
  underlying_canonical_id TEXT          NOT NULL,
  net_debit_credit        NUMERIC(20,8) NOT NULL,
  net_debit_credit_kind   TEXT          NOT NULL CHECK (net_debit_credit_kind IN ('DEBIT','CREDIT')),
  max_loss                NUMERIC(20,8) NULL,   -- NULL = unbounded
  max_profit              NUMERIC(20,8) NULL,   -- NULL = unbounded
  -- break_even cardinality: 0=unbounded, 1=vertical/calendar/diagonal, 2=straddle/strangle
  break_even              NUMERIC(20,8)[] NOT NULL DEFAULT '{}',
  -- tif is per-combo; IBKR/Schwab/Alpaca all apply TIF at combo level
  tif                     TEXT          NOT NULL CHECK (tif IN ('DAY','GTC','IOC','FOK')),
  status                  TEXT          NOT NULL CHECK (status IN (
                              'pending_submit','working','filled',
                              'partially_filled','cancelled','rejected','legged_out')),
  broker_combo_id         TEXT          NULL,
  created_at              TIMESTAMPTZ   NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ   NOT NULL DEFAULT now(),
  UNIQUE (account_id, client_combo_id)
);
CREATE INDEX combo_orders_account_status_idx ON combo_orders (account_id, status);
-- Defensive partial index (Phase 12 lesson): belt-and-suspenders guard on the idempotency key
CREATE UNIQUE INDEX combo_orders_client_combo_id_nn_idx
  ON combo_orders (account_id, client_combo_id)
  WHERE client_combo_id IS NOT NULL;
```

### Status transition table

| From | Event | To |
|---|---|---|
| `pending_submit` | broker accepts order | `working` |
| `pending_submit` | broker rejects immediately | `rejected` |
| `working` | first leg fill arrives (not all legs filled) | `partially_filled` |
| `working` | all legs filled simultaneously | `filled` |
| `working` | all legs cancelled with zero fills | `cancelled` |
| `working` | explicit DELETE /api/combos/{id} (no fills present) | `cancelled` |
| `partially_filled` | remaining legs fill | `filled` |
| `partially_filled` | remaining legs cancelled/rejected | `legged_out` |
| `legged_out` | terminal — no further transitions | — |
| `filled` | terminal | — |
| `cancelled` | terminal | — |
| `rejected` | terminal | — |

### `order_legs`

```sql
CREATE TABLE order_legs (
  id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  combo_id        UUID          NOT NULL REFERENCES combo_orders(id) ON DELETE CASCADE,
  -- order_id: FK to synthesized orders row for fill pipeline (Model A); NULL until broker confirms
  order_id        UUID          NULL REFERENCES orders(id),
  leg_idx         SMALLINT      NOT NULL,
  instrument_id   BIGINT        NOT NULL REFERENCES instruments(id),
  side            TEXT          NOT NULL CHECK (side IN ('buy','sell')),
  ratio           SMALLINT      NOT NULL CHECK (ratio > 0) DEFAULT 1,
  qty             NUMERIC(20,8) NOT NULL,
  position_effect TEXT          NOT NULL CHECK (position_effect IN ('OPEN','CLOSE')),
  limit_price     NUMERIC(20,8) NULL,
  broker_order_id TEXT          NULL,
  filled_qty      NUMERIC(20,8) NOT NULL DEFAULT 0,
  avg_fill_price  NUMERIC(20,8) NULL,
  status          TEXT          NOT NULL DEFAULT 'pending_submit',
  UNIQUE (combo_id, leg_idx)
);
CREATE INDEX order_legs_combo_idx      ON order_legs (combo_id);
CREATE INDEX order_legs_instrument_idx ON order_legs (instrument_id);
CREATE INDEX order_legs_broker_idx     ON order_legs (broker_order_id)
  WHERE broker_order_id IS NOT NULL;
```

### `orders` table — new `combo_id` column (CRIT-N1)

```sql
ALTER TABLE orders
  ADD COLUMN combo_id UUID NULL REFERENCES combo_orders(id);
CREATE INDEX orders_combo_id_idx ON orders (combo_id)
  WHERE combo_id IS NOT NULL;
```

### Fill pipeline integration — Model A

**Alpaca and Schwab:** `PlaceComboResponse.legs[]` contains per-leg `broker_order_id`. `combo_service.confirm` synthesizes one `orders` row **per leg**, each with a fresh `uuid4()` as `client_order_id` (matching `orders.client_order_id UUID` type). Sets `orders.combo_id = combo_orders.id`. Updates `order_legs.order_id = orders.id` and `order_legs.broker_order_id`.

**IBKR BAG:** TWS returns a single `orderId` for the entire BAG contract — there are no per-leg `broker_order_id`s. `combo_service.confirm` synthesizes exactly **one** `orders` row for the BAG (not one per leg), using the BAG `orderId` as `broker_order_id`. This avoids violating `uq_orders_account_broker_order_id` (a partial unique index on `(account_id, broker_order_id) WHERE broker_order_id IS NOT NULL`). `order_legs.filled_qty` is updated from BAG `execDetails.contract` events routed through `oco_orchestrator`, not from per-leg orders rows.

### `risk_limits` additions (same migration)

```sql
ALTER TABLE risk_limits
  ADD COLUMN max_combo_loss_native    NUMERIC(20,8) NULL,
  ADD COLUMN max_combo_net_delta      NUMERIC(20,8) NULL,
  ADD COLUMN combo_legout_autoclose   BOOLEAN       NOT NULL DEFAULT FALSE;
```

### `risk_decisions` CHECK widening (HIGH-N2)

Both CHECKs are widened in migration 0049. Each uses DROP then ADD in the same transaction — safe in Alembic's single-TX migration block; a future split must keep DROP and ADD in the same statement batch to avoid the table being briefly unconstrained.

```sql
-- Both statements execute inside the single Alembic TX for migration 0049
ALTER TABLE risk_decisions
  DROP CONSTRAINT risk_decisions_side_check,
  ADD CONSTRAINT risk_decisions_side_check
    CHECK (side IN ('buy','sell','combo'));

ALTER TABLE risk_decisions
  DROP CONSTRAINT risk_decisions_attempt_kind_check,
  ADD CONSTRAINT risk_decisions_attempt_kind_check
    CHECK (attempt_kind IN (
      'preview','place_order','modify_order',
      'combo_preview','combo_place','combo_autoclose'
    ));
```

---

## 5. API endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/combos/preview` | JWT | Validate + envelope + risk gate → return PreviewResponse + nonce |
| POST | `/api/combos/confirm/{nonce}` | JWT + nonce header | GETDEL nonce → place → return combo_id + status |
| DELETE | `/api/combos/{id}` | JWT + CSRF nonce header | Cancel working combo (only if no fills; else 409) |
| GET | `/api/combos/{id}` | JWT | Fetch combo + leg statuses |
| GET | `/api/combos` | JWT | List combos: `?account_id=&status=&limit=50&before_id=` (keyset pagination; max 200) |

`PreviewResponse` shape:
```json
{
  "client_combo_id": "combo-<uuid>",
  "strategy_type": "VERTICAL",
  "envelope": {
    "net_debit_credit": "3.20000000",
    "kind": "DEBIT",
    "max_loss": "320.00000000",
    "max_profit": "680.00000000",
    "break_even": ["253.20000000"]
  },
  "risk_warnings": [],
  "risk_blockers": [],
  "csrf_nonce": "<uuid>"
}
```

All monetary values in `envelope` are decimal strings with 8 decimal places (`toFixed(8)`, `ROUND_HALF_EVEN`). FE must parse with `decimal.js` to maintain parity.

**DELETE `/api/combos/{id}`** requires a CSRF nonce header (same pattern as other money-moving endpoints) to prevent CSRF attacks. Returns 409 `combo_has_fills` if any leg has `filled_qty > 0`.

---

## 6. Proto changes

New messages + RPC in `proto/broker/v1/broker.proto`. **`ComboLegRequest` uses `SymbolRef` + `OptionContractHint`** (Phase 12 pattern, not stringified DB ids) — BE resolves instrument to SymbolRef before dispatch.

```protobuf
// Reuses existing SymbolRef and OptionContractHint from proto/broker/v1/broker.proto:453-459

message ComboLegRequest {
  SymbolRef          symbol          = 1;
  OptionContractHint option_hint     = 2;
  string             side            = 3;  // "buy" | "sell"
  int32              ratio           = 4;
  string             position_effect = 5;  // "OPEN" | "CLOSE"
}

message PlaceComboRequest {
  string                   account_id      = 1;
  string                   strategy_type   = 2;
  repeated ComboLegRequest legs            = 3;
  string                   tif             = 4;
  string                   limit_price     = 5;  // net debit/credit limit; empty = market
  string                   client_combo_id = 6;
}

message ComboLegResult {
  int32  leg_idx         = 1;
  // broker_order_id is "" (empty string sentinel) for IBKR BAG and Alpaca MLEG — no per-leg IDs.
  // combo_service treats "" as NULL when storing to order_legs.broker_order_id.
  string broker_order_id = 2;
  string status          = 3;
}

message PlaceComboResponse {
  string                   broker_combo_id = 1;
  repeated ComboLegResult  legs            = 2;
}

message GetSupportedComboStrategiesRequest {
  string broker_id = 1;
}

message GetSupportedComboStrategiesResponse {
  repeated string strategy_types = 1;
}

service BrokerService {
  // ... existing RPCs ...
  rpc PlaceCombo                  (PlaceComboRequest)                   returns (PlaceComboResponse);
  rpc GetSupportedComboStrategies (GetSupportedComboStrategiesRequest)  returns (GetSupportedComboStrategiesResponse);
}
```

---

## 7. Broker sidecar implementations

| Broker | Mechanism | Supported strategies | Notes |
|---|---|---|---|
| IBKR | `Contract(secType="BAG", comboLegs=[ComboLeg(conid, action, ratio, exchange)])` | All 5 | Single BAG `orderId` = `broker_combo_id`; no per-leg IDs; one synthesized `orders` row total |
| Schwab | `complexOrderStrategyType` + `orderLegCollection[]` | All 5 | Existing scaffolding present; **gated behind `unsupported_runtime=True`** (Phase 12 Schwab 401 still live) |
| Alpaca | `OptionLegRequest[]` + `order_class=OrderClass.MLEG` via alpaca-py ≥ 0.43.4 | All 5 | Single `broker_combo_id`; no per-leg IDs (same as IBKR — one synthesized `orders` row total) |
| Futu | `GetSupportedComboStrategies` returns `[]`; `PlaceCombo` returns `Unimplemented` | None | Deferred to Phase 13c |

`broker_features.py` registers `combo.VERTICAL` etc. per broker. Schwab entries present with `unsupported_runtime = True`. API returns `503 combo_unsupported` with `supported_brokers[]` if broker lacks support or has `unsupported_runtime = True`.

**Lifespan reconciliation:** `GetSupportedComboStrategies` called per sidecar at startup; drift → `combo_capability_drift_total{broker}` counter + warning.

---

## 8. Data flow (end-to-end)

1. User opens `TradeTicketModal` → on mount `ComboBuilder` queries `GET /api/combos?account_id={id}&status=pending_submit` to recover any in-flight combo.
2. User toggles **Strategy = Vertical** → `ComboBuilder` renders; fills Leg 1 (BTO AAPL 250C Jan-17) + Leg 2 (STO AAPL 260C Jan-17).
3. Client computes live preview via `computeEnvelope.ts` (decimal.js): net debit $3.20, max loss $320, max profit $680, break-even $253.20. `ComboPayoffChart` and `ComboSummary` update in real time.
4. User clicks **Preview** → `POST /api/combos/preview`.
5. BE: `strategy_validator.validate` → `pnl_envelope.compute` → `evaluate_combo(ctx, mode)` → mint `client_combo_id = "combo-{uuid4()}"` + CSRF nonce → store `_combo_preview_payload_hash` in Redis alongside nonce → return `PreviewResponse` (no nonce if BLOCK).
6. FE renders WARN acknowledge gate or BLOCK rows. User acknowledges + clicks **Confirm**.
7. FE: `POST /api/combos/confirm/{nonce}` with `client_combo_id` in body; nonce in `X-CSRF-Nonce` header.
8. BE: GETDEL nonce → verify `_combo_preview_payload_hash` → INSERT `combo_orders` + `order_legs` in one TX → `BrokerSidecarClient.place_combo` (20s timeout) → synthesize `orders` row(s) (one per leg for Alpaca; one BAG row for IBKR) with fresh `uuid4()` `client_order_id` each → set `orders.combo_id` → update `order_legs.order_id` and `order_legs.broker_order_id` → PDT mint once (key: `pdt:{account_id}:{underlying_canonical_id}:{ymd}`) → return `{combo_id, status: "working"}`.
9. Fills arrive via existing fill pipeline → broker event stream dispatcher calls `combo_fill_listener.handle_fill` (in parallel with `oco_orchestrator.process_fill_event`, which is unmodified) → `combo_fill_listener` fetches `orders.combo_id`; if non-NULL, acquires `SELECT ... FOR UPDATE` row lock on `combo_orders`, updates `order_legs.filled_qty / avg_fill_price`, recomputes `combo_orders.status` per §4 state machine.
10. If `legged_out`: cancel working legs, emit `combo_legged_out` alert, insert audit row (`side='combo'`, `attempt_kind='combo_place'`); if `combo_legout_autoclose = TRUE`, submit market-close orders and audit with `attempt_kind='combo_autoclose'`.

---

## 9. Error handling

| Failure | HTTP | Error code | Notes |
|---|---|---|---|
| Validator rejects leg shape | 422 | `combo_invalid_legs` | `reason`: `expiry_mismatch`, `same_strike_required`, `opposite_put_call_required`, `opposite_side_required`, `currency_mismatch` |
| Risk gate BLOCK | 422 | `risk_gate_blocked` | `risk_blockers[]`; nonce not minted |
| Risk gate WARN | 200 | — | `risk_warnings[]`; FE acknowledge gate required |
| Nonce expired or reused | 410 | `nonce_invalid` | Redis GETDEL returns nil |
| Payload hash mismatch on confirm | 409 | `payload_drift` | Client must re-preview |
| Partial fill → legged_out | — | — | Cancel working legs; leave filled legs as positions; `legged_out`; alert + audit |
| DELETE while legs partially filled | 409 | `combo_has_fills` | Client must handle `legged_out` instead |
| DELETE without CSRF nonce | 422 | `csrf_required` | Same as other money-moving endpoints |
| Broker unsupported / `unsupported_runtime` | 503 | `combo_unsupported` | `supported_brokers[]` hint |
| Sidecar 504 / timeout on PlaceCombo | 504 | `combo_place_timeout` | Query sidecar by `client_combo_id` for recovery; if partial-submit found, set `combo_orders.status = 'legged_out'` |
| `computeEnvelope.ts` ↔ `pnl_envelope.py` drift | — | — | Golden-fixture parity test blocks CI |

---

## 10. Prometheus metrics

| Metric | Type | Labels | Notes |
|---|---|---|---|
| `combo_preview_total` | Counter | `strategy_type`, `verdict` (allow/warn/block) | Incremented in `evaluate_combo` |
| `combo_place_total` | Counter | `strategy_type`, `broker` | Incremented post-sidecar-success in `combo_service.confirm` |
| `combo_legged_out_total` | Counter | `strategy_type`, `broker` | Incremented on `legged_out` transition |
| `combo_unbounded_blocked_total` | Counter | `strategy_type` | Incremented in `_check_combo_envelope` unbounded BLOCK |
| `combo_confirm_e2e_seconds` | Histogram | `strategy_type`, `broker` | confirm-receipt to broker-ack latency (excludes user think-time) |
| `combo_fill_lag_seconds` | Histogram | `strategy_type`, `broker` | Time from `pending_submit` to first leg fill |
| `combo_capability_drift_total` | Counter | `broker` | Incremented on lifespan reconciliation mismatch |

---

## 11. File changes

### Backend — new files

| File | Responsibility |
|---|---|
| `backend/app/services/combos/__init__.py` | Package init |
| `backend/app/services/combos/types.py` | `ComboSpec`, `LegSpec`, `LegContext`, `ComboEnvelope`, `ComboContext` (extends `EvaluationContext` with `legs: list[LegContext]` + `envelope: ComboEnvelope`); Pydantic v2; discriminated union on `strategy_type` |
| `backend/app/services/combos/strategy_validator.py` | 5 pure validator functions + dispatch table |
| `backend/app/services/combos/pnl_envelope.py` | Envelope computation per strategy; `_combo_native_notional` helper (ratio=1 YAGNI) |
| `backend/app/services/combos/combo_service.py` | Orchestrator: preview + confirm + cancel; `_combo_preview_payload_hash` (SymbolRef-based); PDT mint once post-sidecar-success |
| `backend/app/services/combos/combo_fill_listener.py` | Fill event handler: detects `orders.combo_id IS NOT NULL`, updates `order_legs`, recomputes `combo_orders.status` under row lock |
| `backend/app/api/combos.py` | FastAPI router: 5 endpoints (add GET list) |
| `backend/app/models/combos.py` | SQLAlchemy `ComboOrder` + `OrderLeg` ORM models |
| `backend/alembic/versions/0049_combo_orders_order_legs.py` | Migration (chains off 0048): creates `combo_orders`, `order_legs`; `ALTER TABLE orders ADD COLUMN combo_id`; `ALTER TABLE risk_limits` (3 cols); widens `risk_decisions` side + attempt_kind CHECKs |

### Backend — modified files

| File | Change |
|---|---|
| `backend/app/services/risk_service.py` | `+evaluate_combo(ctx, mode)`, `+evaluate_legs_for_combo(legs, mode)` (check only, no PDT mint), `+_check_combo_envelope`, `+_combo_native_notional`; `_check_options_exposure` gains `combo_envelope=None` kwarg with explicit fall-through documented |
| `backend/app/services/orders_service.py` | Minor: broker event stream dispatcher calls `combo_fill_listener.handle_fill` in parallel with `oco_orchestrator.process_fill_event` |
| `backend/app/main.py` | Register `combos.router`; lifespan: call `GetSupportedComboStrategies` per sidecar + reconcile |
| `backend/app/services/broker_features.py` | Register `combo.<strategy>` capabilities; Schwab `unsupported_runtime=True`; increment `combo_capability_drift_total{broker}` on mismatch |
| `proto/broker/v1/broker.proto` | Add `ComboLegRequest`, `PlaceComboRequest`, `PlaceComboResponse`, `ComboLegResult`, `GetSupportedComboStrategiesRequest/Response`, `PlaceCombo` RPC, `GetSupportedComboStrategies` RPC |
| `sidecar_ibkr/handlers.py` | `PlaceCombo` via BAG; `GetSupportedComboStrategies` → all 5 |
| `sidecar_schwab/handlers.py` | `PlaceCombo` via `complexOrderStrategyType`; `GetSupportedComboStrategies` → all 5 (gated in BE) |
| `sidecar_alpaca/handlers.py` | `PlaceCombo` via MLEG; `GetSupportedComboStrategies` → all 5 |
| `sidecar_futu/handlers.py` | `GetSupportedComboStrategies` → `[]`; `PlaceCombo` → `Unimplemented` |

### Frontend — new files

| File | Responsibility |
|---|---|
| `frontend/src/features/options/combo/StrategyPicker.tsx` | Dropdown: Vertical / Calendar / Diagonal / Straddle / Strangle |
| `frontend/src/features/options/combo/LegSlot.tsx` | Single-leg input row: direction badge + symbol/strike/expiry + bid/ask |
| `frontend/src/features/options/combo/ComboPayoffChart.tsx` | SVG payoff-at-expiry; consumes `computeEnvelope.ts` output |
| `frontend/src/features/options/combo/ComboSummary.tsx` | Net debit/credit + max-loss/profit/break-even bar |
| `frontend/src/features/options/combo/ComboBuilder.tsx` | Orchestrator; on-mount recovery query |
| `frontend/src/features/options/combo/computeEnvelope.ts` | TS mirror of `pnl_envelope.py`; `decimal.js`; `toFixed(8)` `ROUND_HALF_EVEN` |
| `frontend/src/services/combos/api.ts` | `previewCombo`, `confirmCombo`, `cancelCombo`, `getCombo`, `listCombos` |
| `frontend/src/services/combos/types.ts` | `ComboPreviewRequest`, `PreviewResponse` (incl. `client_combo_id`), `ComboEnvelope`, `OrderLegStatus` |

### Frontend — modified files

| File | Change |
|---|---|
| `frontend/src/components/patterns/TradeTicketModal/TradeTicketModal.tsx` | Strategy toggle (Single / Combo); conditional render of `ComboBuilder` |
| `frontend/src/features/options/OptionChainTable.tsx` | Stretch goal: multi-select mode for spread building |

---

## 12. Testing

| Layer | What | File |
|---|---|---|
| Validator unit (+) | each strategy accepts valid leg shape | `tests/services/combos/test_strategy_validator.py` |
| Validator unit (−) | wrong expiry / strike / side / P/C / currency rejected with specific reason | same |
| Property-based | `hypothesis`; strikes ∈ [50, 500] step ∈ {0.5, 1, 2.5, 5}; fixed expiry set `{2026-01-17, 2026-04-17}`; validator accepts or rejects with known reason | `tests/services/combos/test_validator_hypothesis.py` |
| Envelope unit | golden fixtures: 5 strategies × {debit, credit, ATM, OTM}; values as 8dp decimal strings | `tests/services/combos/test_pnl_envelope.py` |
| Envelope parity | BE `Decimal` == FE `decimal.js` on shared JSON decimal-string fixtures | `tests/services/combos/test_envelope_parity.py` + `frontend/src/features/options/combo/__tests__/envelope.parity.test.ts` |
| Risk gate combo | max-loss cap / net-delta cap / unbounded BLOCK; bounded vertical NOT blocked by naked-short check | `tests/services/test_combo_risk_envelope.py` |
| Risk gate PDT once | `evaluate_legs_for_combo` checks but does not mint; `combo_service.confirm` mints once | same |
| `combo_envelope=None` regression | `_check_options_exposure(ctx, combo_envelope=None)` behaves identically to Phase 12 (no relaxation) | same |
| API integration | preview → confirm → broker-mock dispatch → row state; `legged_out` path; idempotent retry via `client_combo_id` | `tests/api/test_combos_api.py` |
| IBKR single-orders-row | BAG response synthesizes 1 `orders` row, not 2; no unique-constraint violation | same |
| Fill listener | `combo_fill_listener.handle_fill` updates `order_legs` + derives `combo_orders.status` for all transitions including `partially_filled` → `filled` and `partially_filled` → `legged_out` | `tests/services/combos/test_combo_fill_listener.py` |
| Concurrent fills | two simultaneous fill events; `SELECT ... FOR UPDATE` row lock prevents status race | same |
| `oco_orchestrator` isolation | confirm `oco_orchestrator.process_fill_event` is unmodified; non-combo fills route identically to Phase 12 | `tests/services/test_oco_orchestrator.py` (existing, must stay green) |
| Sidecar integration (IBKR) | place vertical via TWS paper; single `broker_combo_id`; one `orders` row synthesized | `tests/integration/test_ibkr_combo_place.py` |
| Migration round-trip | 0049 up/down; `orders.combo_id` present after up; `risk_decisions` CHECK widened | `tests/db/test_migration_0049.py` |
| FE component | StrategyPicker, LegSlot, ComboPayoffChart (decimal.js path), ComboSummary | `frontend/src/features/options/combo/__tests__/*.test.tsx` |
| FE E2E | build vertical from chain → preview → confirm → row visible | `frontend/tests/e2e/combo-vertical.spec.ts` |

Target: ≥80% coverage on new BE code; all new FE primitives/patterns have `.test.tsx`.

---

## 13. Verification (manual)

1. `docker compose exec backend alembic upgrade head` → 0049 applied; `combo_orders` + `order_legs` exist; `orders.combo_id` column present; `risk_decisions` side/attempt_kind CHECKs widened.
2. `pytest backend/tests/services/combos/ backend/tests/api/test_combos_api.py backend/tests/services/test_combo_risk_envelope.py backend/tests/db/test_migration_0049.py` → all green.
3. `cd frontend && pnpm test features/options/combo` → all green; parity test passes.
4. TWS paper: open `/options/chain` for AAPL → build vertical → Preview → Confirm → IBKR returns single `broker_combo_id`; **one** synthesized `orders` row (not two); both `order_legs` rows have `order_id` pointing to that single row.
5. Schwab paper: conditional on `unsupported_runtime` flag being cleared.
6. Alpaca paper: repeat with strangle; one synthesized `orders` row per leg.
7. Risk gate: set `risk_limits.max_combo_loss_native = 100` → vertical $320 max-loss → BLOCK; reduce to $80 → ALLOW. PDT counter increments once after place.
8. Naked-short test: credit vertical NOT blocked; naked short call (single-leg) still blocked.
9. Legged-out: mock one leg cancel mid-fill → verify `combo_orders.status = 'legged_out'`; `combo_legged_out` alert emitted; `risk_decisions` row with `side='combo'`, `attempt_kind='combo_place'`.
10. Capability drift: stop IBKR sidecar → restart BE → `combo_capability_drift_total{broker="ibkr"}` increments.
11. Full regression: `docker compose exec backend pytest` + `cd frontend && pnpm test` → green.

---

## 14. Phase 12 surfaces this spec builds on

| Surface | File | How reused |
|---|---|---|
| Options service package | `backend/app/services/options/*` | Chain + Greeks reused; `combos/` is a peer package |
| Risk gate options checks | `backend/app/services/risk_service.py:650-779` | `_check_options_exposure(ctx, combo_envelope=None)` per-leg; `evaluate_combo` new entry point |
| Proto SymbolRef + OptionContractHint | `proto/broker/v1/broker.proto:453-459` | Used directly in `ComboLegRequest` |
| TradeTicketModal | `frontend/src/components/patterns/TradeTicketModal/TradeTicketModal.tsx` | Strategy toggle injects `ComboBuilder` |
| Chain table strike pick | `frontend/src/features/options/OptionChainTable.tsx` | Stretch goal: multi-select mode |
| CSRF nonce pattern | Phase 11 `mintCsrfNonce` + Redis EX 30s | Reused for combo confirm; `_combo_preview_payload_hash` extends it; DELETE also requires nonce |
| Broker event stream dispatcher | `backend/app/services/orders_service.py` | Calls `combo_fill_listener.handle_fill` in parallel with unchanged `oco_orchestrator.process_fill_event` |
