# Phase 17 — IBKR Algo Orders Design

**Date:** 2026-05-18
**Version target:** v0.17.0
**Status:** Architect-review findings applied (CRIT-1/2/3, HIGH-1–6, MED-1–5)

---

## 1. Scope

Ship IBKR algo order support across all asset classes. Two categories:

**Execution algos** — IBKR routes the order through a smart algorithm:
- `ADAPTIVE` — adapts between passive and aggressive based on urgency
- `TWAP` — time-weighted average price over a user-defined window
- `VWAP` — volume-weighted average price over a user-defined window
- `ARRIVAL_PRICE` — targets the mid-price at time of submission

**Display algos** — control how much quantity is shown on the book (layered on a LIMIT base order):
- `ICEBERG` — show a fixed `display_size`, refill automatically
- `RESERVE` — show a fixed `display_size` with optional randomisation
- `DARK_ICE` — dark-pool sweep variant (non-zero `display_size` required; NOT the same as `Order.hidden=True` which is a separate order field, not an algo strategy)

`HIDDEN` / `Order.hidden=True` (plain hidden orders that show zero qty on the lit book) is **not** an algo strategy in IBKR's model. It is an order field. Deferred to a future phase as a separate `hidden: bool` on `OrderRequest`.

**In scope:** IBKR only. STOCK, ETF, OPTION, FUTURE, FOREX per the explicit capability matrix in §2.2. Telegram algo order support. Enriched order event stream (algo strategy + status visible in Orders page).

**Out of scope:** Algo support for Futu / Schwab / Alpaca (zero rows in `broker_algo_capability`). BOND / CFD / CRYPTO / MUTUAL_FUND (IBKR does not support execution algos for these; explicitly excluded from capability seed). Plain `Order.hidden=True` orders (separate feature, not this phase). Benchmark comparison stream (VWAP vs market VWAP, pct_ahead). Estimated completion time. Per-slice child order detail view.

---

## 2. Data Model

### 2.1 Alembic 0057 — `orders` table additions

```sql
ALTER TABLE orders
  ADD COLUMN algo_strategy TEXT,
  ADD COLUMN algo_params   JSONB,
  ADD COLUMN algo_status   TEXT;

ALTER TABLE orders
  ADD CONSTRAINT orders_algo_strategy_check
    CHECK (algo_strategy IN (
      'ADAPTIVE','TWAP','VWAP','ARRIVAL_PRICE',
      'ICEBERG','RESERVE','DARK_ICE'
    ));

-- algo_status valid values from IBKR orderStatus.status field
-- (captured for enriched display; same value space as orders.status)
```

All three columns are nullable. Non-algo orders leave them NULL.

### 2.2 Alembic 0057 — `broker_algo_capability` table

```sql
CREATE TABLE broker_algo_capability (
  broker_id     TEXT                     NOT NULL
                  REFERENCES brokers(id),
  asset_class   instrument_asset_class   NOT NULL,
  algo_strategy TEXT                     NOT NULL,
  enabled       BOOLEAN                  NOT NULL DEFAULT TRUE,
  notes         TEXT,
  PRIMARY KEY (broker_id, asset_class, algo_strategy),
  CHECK (algo_strategy IN (
    'ADAPTIVE','TWAP','VWAP','ARRIVAL_PRICE',
    'ICEBERG','RESERVE','DARK_ICE'
  ))
);
```

`broker_id` FK to `brokers(id)` — typo in seed creates an FK violation, caught at migration time.
`asset_class` uses the existing `instrument_asset_class` PG enum — typos fail at INSERT.

**Explicit capability seed** (cartesian seed replaced; verified against TWS API algo docs):

| Asset class | ADAPTIVE | TWAP | VWAP | ARRIVAL_PRICE | ICEBERG | RESERVE | DARK_ICE |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| STOCK | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ETF | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| OPTION | ✓ | — | — | — | ✓ | — | — |
| FUTURE | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| FOREX | ✓ | ✓ | ✓ | — | — | — | — |
| BOND | — | — | — | — | — | — | — |
| CFD | — | — | — | — | — | — | — |
| CRYPTO | — | — | — | — | — | — | — |
| MUTUAL_FUND | — | — | — | — | — | — | — |

`notes` column populated for zero-rows: `'NAV-priced; no execution algo'` (MUTUAL_FUND), `'RFQ-routed; algos unsupported'` (BOND), `'algoStrategy silently ignored on Paxos crypto'` (CRYPTO), `'CFD algo support region-dependent; excluded by default'` (CFD).

All other brokers (`schwab`, `futu`, `alpaca`) get zero rows — the FE algo section will not render for them.

### 2.3 `algo_params` shapes

| Strategy | Required | Optional |
|---|---|---|
| `ADAPTIVE` | `urgency: PATIENT\|NORMAL\|URGENT` | — |
| `TWAP` | `start_time: HH:MM`, `end_time: HH:MM` | `allow_past_end_time: bool` |
| `VWAP` | `start_time: HH:MM`, `end_time: HH:MM` | `max_pct_vol: int (0–100)`, `no_take_liq: bool` |
| `ARRIVAL_PRICE` | `urgency: PATIENT\|NORMAL\|URGENT` | `max_pct_vol: int (0–100)` |
| `ICEBERG` | `display_size: Decimal` | — |
| `RESERVE` | `display_size: Decimal` | `randomize_size: bool` |
| `DARK_ICE` | `display_size: Decimal` | — |

---

## 3. Proto Changes

File: `proto/broker/v1/broker.proto`

### 3.1 `PlaceOrderRequest` additions

Tags 26/27 are the next free slots after tag 25 (`oco_group_id`). Per project R35 forward-extension convention, add a reserved block for future growth:

```protobuf
optional string algo_strategy = 26;  // "ADAPTIVE"|"TWAP"|"VWAP"|"ARRIVAL_PRICE"|"ICEBERG"|"RESERVE"|"DARK_ICE"
map<string, string> algo_params = 27;  // strategy param key→value (Decimal-as-string convention)
reserved 28 to 35;  // algo forward-extension
```

`map<string,string>` replaces JSON-encoded string — proper proto-level validation, no per-sidecar JSON parser, and TagValue conversion (already key→string) is a one-liner.

### 3.2 `PlaceOrderResponse` additions

`PlaceOrderResponse` currently ends at tag 2. Use the next sequential tags (not 26/27) per per-message numbering:

```protobuf
optional string algo_strategy = 3;
optional string algo_status   = 4;  // IBKR orderStatus.status value at last event
reserved 5 to 25;  // forward growth
```

### 3.3 `Order` message additions

`Order` message ends at tag 24 (`expiry_date`) with `reserved 16 to 20`. Use tag 25/26, extend the reserved block:

```protobuf
optional string algo_strategy = 25;
optional string algo_status   = 26;
reserved 27 to 35;  // algo + forward-extension
```

No new RPCs. Algo orders go through the existing `PlaceOrder` RPC. `CancelOrder` works unchanged — IBKR cancels the parent order and all child slices.

---

## 4. IBKR Sidecar

### 4.1 IBKR algo strategy string mapping

IBKR's TWS API uses different string identifiers than our internal enum. Static mapping in `order_builder.py`:

```python
_ALGO_STRATEGY_MAP = {
    "ADAPTIVE":      "Adaptive",
    "TWAP":          "Twap",       # verify casing against ibapi at impl time (may be "TWAP")
    "VWAP":          "Vwap",       # verify casing against ibapi at impl time (may be "VWAP")
    "ARRIVAL_PRICE": "ArrivalPx",
    "ICEBERG":       "Iceberg",    # TWS API algoStrategy string for plain iceberg
    "RESERVE":       "PctVol",     # TWS API: PctVol with reserveSize + randomise flag
    "DARK_ICE":      "DarkIce",    # TWS API: dark-pool sweep variant
}
```

**LOW-1 note:** Verify `Twap`/`Vwap` capitalisation and `Iceberg`/`PctVol`/`DarkIce` strategy strings against `ibapi/order_condition.py` or live TWS API test at impl time. If iceberg-family strategies use order fields (`Order.displaySize`) rather than `algoStrategy`, rewrite §4.1/§4.2 to populate `order.displaySize` directly — do not guess; confirm before coding.

### 4.2 `build_ib_algo_order()` function

Called after the base order is built when `request.algo_strategy` is non-empty. Sets `order.algoStrategy` and populates `order.algoParams` as `[TagValue(k, v) for k, v in request.algo_params.items()]`.

IBKR `TagValue` key mapping per strategy:

| Strategy | TagValue keys |
|---|---|
| `ADAPTIVE` | `adaptPriority` → urgency string |
| `TWAP` | `startTime` HH:MM:SS, `endTime` HH:MM:SS, `allowPastEndTime` 0/1 |
| `VWAP` | `startTime`, `endTime`, `maxPctVol`, `noTakeLiq` 0/1 |
| `ARRIVAL_PRICE` | `adaptPriority`, `maxPctVol` |
| `ICEBERG` | `displaySize` Decimal string |
| `RESERVE` | `displaySize`, `randomizeSize` 0/1 |
| `DARK_ICE` | `displaySize` Decimal string (must be > 0; non-zero required by IBKR) |

**Constraint enforcement in builder:**
- ICEBERG / RESERVE / DARK_ICE: base `orderType` must be `LMT`; raises `ValueError` if `MKT` — caught by `orders_service` and returned as 422.
- TWAP / VWAP / ARRIVAL_PRICE / ADAPTIVE: base `orderType` must be `MKT`; coerced automatically.

### 4.3 Enriched order event

`order_event_consumer.py`: when an `orderStatus` callback arrives for an order with `algo_strategy IS NOT NULL`, update `orders.algo_status` with `orderStatus.status` (same value space as the existing order status field) and include `algo_strategy` + `algo_status` in the existing order event WS push payload. No new WS endpoint.

---

## 5. Backend API

### 5.1 `OrderRequest` Pydantic model additions

```python
algo_strategy: AlgoStrategy | None = None
algo_params:   dict[str, str] | None = None  # matches map<string,string> proto convention
```

`AlgoStrategy` is a `StrEnum` with values: `ADAPTIVE`, `TWAP`, `VWAP`, `ARRIVAL_PRICE`, `ICEBERG`, `RESERVE`, `DARK_ICE`.

`orders_service.preview_order` and `place_order` pass these through to `PlaceOrderRequest` after capability validation.

### 5.2 New endpoints

**`GET /api/algo/capabilities/{broker_id}/{asset_class}`**

Returns enabled strategies + parameter schemas for the FE dynamic form. JWT required. Rate-limited at 60/min. Cached in Redis for 5 minutes per `(broker_id, asset_class)` key with a `app_config:invalidate:algo_capability` pubsub invalidation channel (consumer side wired this phase; publisher ships with the deferred admin UI). Response:

```json
{
  "strategies": [
    {
      "strategy": "ADAPTIVE",
      "params": [
        {"name": "urgency", "type": "enum", "values": ["PATIENT","NORMAL","URGENT"], "required": true}
      ]
    }
  ]
}
```

**`GET /api/algo/schemas`**

Returns the full `ALGO_PARAM_SCHEMAS` dict for all strategies. The FE fetches this once on app bootstrap to avoid duplicating the schema in TypeScript. JWT required. No caching needed (static data).

### 5.3 `validate_pre_dispatch` extension and call-site enumeration

New `algo_strategy: str | None = None` kwarg (default `None` — backward-compatible). Queries `broker_algo_capability` when non-None. Returns `422 unsupported_algo_strategy` if no enabled row found.

**Call-site behaviour:**

| Surface | Algo validated? | Notes |
|---|---|---|
| `preview_order` | Yes | fail-OPEN on DB error (preview convention) |
| `place_order` | Yes | fail-CLOSED on DB error (capability is deterministic; DB error = config issue, not transient) |
| `modify_order` | Reject | Returns `422 algo_modify_unsupported` if `algo_strategy` present — IBKR requires cancel+replace for algo changes |
| `place_bracket` parent | Yes | fail-CLOSED |
| `place_bracket` SL/TP legs | Reject | Returns `422 algo_on_bracket_leg_unsupported` if `algo_strategy` present on SL or TP |

### 5.4 `ALGO_PARAM_SCHEMAS` location

Lives in `app/services/algo/schemas.py` (leaf module — no imports from other `app/services/` modules). Both `algo_capability_service.py` and `app/services/telegram/order_flow.py` import from this module. Eliminates cross-module dependency and keeps the schema as a single source of truth.

### 5.5 Risk gate additions (`risk_service.py`)

**`_check_algo_capability`**
- BLOCK if `broker_algo_capability` has no enabled row for this broker + asset class + strategy.
- Fail-CLOSED on `place_order` (DB error → BLOCK). Fail-OPEN on `preview_order`.

**`_check_iceberg_display_size`** — applies when `algo_strategy IN ('ICEBERG', 'RESERVE', 'DARK_ICE')`:
- BLOCK if `display_size <= 0`
- BLOCK if `display_size >= order_qty` (display must be strictly less than total)
- WARN if `0 < display_size < Decimal("1")` (fractional display sizes rejected by most exchanges; legal on a few)
- Fail-OPEN both paths (pure math; no external dependency).

---

## 6. Telegram

`order_flow.py` parser extended. Syntax:

```
/place_order AAPL BUY 100 ADAPTIVE urgency=URGENT
/place_order AAPL BUY 1000 TWAP start_time=10:00 end_time=14:00
/place_order AAPL BUY 1000 VWAP start_time=10:00 end_time=14:00 max_pct_vol=15
/place_order AAPL BUY 500 ARRIVAL_PRICE urgency=NORMAL
/place_order AAPL BUY 500 ICEBERG display_size=50
/place_order AAPL BUY 500 RESERVE display_size=50 randomize_size=true
/place_order AAPL BUY 500 DARK_ICE display_size=50
```

**Closed-form token dispatch rule (replaces brittle "uppercase alphabetic" heuristic):**

```python
if len(tokens) >= 4 and tokens[3].upper() in AlgoStrategy.__members__:
    # algo path — tokens[3] is a known strategy name
elif len(tokens) >= 4 and tokens[3].replace(".", "", 1).replace(",", "", 1).isdigit():
    # price path — existing behaviour
else:
    raise TelegramParseError(
        f"Unexpected 4th token '{tokens[3]}'. Expected price or one of: "
        f"{', '.join(AlgoStrategy.__members__)}"
    )
```

This handles typos (`ARRIVAL` → error with hint) and future positional collisions (TIF tokens can never be confused).

- `key=value` pairs after the strategy token are parsed into `algo_params`.
- Unknown keys → Telegram error reply listing valid keys for the strategy (sourced from `ALGO_PARAM_SCHEMAS`).
- Missing required params → Telegram error reply with hint listing required keys.
- DARK_ICE / ICEBERG / RESERVE: `display_size` must be numeric and > 0 — parse-time validation.
- For strategies with zero params (none currently; HIDDEN was dropped): "No parameters accepted for {strategy}" error rather than empty key list.

Existing `check_trade` rate-limit bucket (5/min, fail-CLOSED) applies unchanged.

---

## 7. Frontend

### 7.1 `AlgoSection` component

**Location:** `frontend/src/features/orders/AlgoSection.tsx`

Inserted into `TradeTicketModal` below the TIF row, above the sizing section.

**Render condition:** only renders when `getAlgoCapabilities(brokerId, assetClass)` returns at least one strategy. For non-IBKR brokers this returns an empty list → section is hidden entirely. If the API call is in-flight, section shows a skeleton loader (not hidden silently).

**Collapsed state:** "Algo Execution — Off" chip.

**Expanded state:**
1. Strategy `<Select>` — options drawn from capability response schema (fetched via `GET /api/algo/schemas` on app bootstrap), each with a short description
2. Dynamic param form — field type driven by param schema:
   - `enum` → `<Select>`
   - `time` → `<Input type="time">`
   - `decimal` → `<NumericInput>`
   - `boolean` → `<Switch>`
3. Inline constraint note for ICEBERG/RESERVE/DARK_ICE: "Display size must be less than order quantity and greater than zero"
4. Auto-coercion notice:
   - ICEBERG/RESERVE/DARK_ICE selected → `order_type` forced to `LIMIT`, user notified
   - TWAP/VWAP/ARRIVAL_PRICE/ADAPTIVE → `order_type` forced to `MARKET`, user notified

**Stale capability note (LOW-3):** If `getAlgoCapabilities` returns empty after previously returning strategies (cache expiry between fetch and submit), the section hides and the order submits without an algo. This is acceptable for Phase 17; a follow-up can add a re-fetch on expand.

### 7.2 Orders page enrichment

- New `Algo` column in orders `DataTable` — hidden by default, toggleable via `ColumnCustomizerDialog`
- Shows strategy badge (e.g. `TWAP`) when `algo_strategy` is non-null
- `algo_status` shown as tooltip on the badge (mirrors existing `orders.status` value space)

### 7.3 Services layer

- `frontend/src/services/algo/types.ts` — `AlgoStrategy` enum, `AlgoCapability`, `AlgoParamSchema`, `AlgoOrderFields`
- `frontend/src/services/algo/api.ts` — `getAlgoCapabilities(brokerId, assetClass)`, `getAlgoSchemas()`

The FE `AlgoStrategy` enum and `AlgoParamSchema` are generated from `GET /api/algo/schemas` on app bootstrap — no duplicate TypeScript definitions.

---

## 8. Prometheus Metrics

7 new counters (MED-1: added modify-rejection metric):

| Metric | Labels | Where |
|---|---|---|
| `algo_orders_submitted_total` | `strategy`, `broker_id`, `asset_class` | orders_service on place |
| `algo_orders_cancelled_total` | `strategy`, `broker_id` | order_event_consumer on cancel |
| `algo_orders_modify_rejected_total` | `strategy` | orders_service on modify attempt |
| `algo_capability_cache_hits_total` | `broker_id` | algo_capability_service |
| `algo_capability_cache_misses_total` | `broker_id` | algo_capability_service |
| `algo_risk_blocks_total` | `check`, `strategy` | risk_service |
| `algo_sidecar_errors_total` | `strategy`, `error_type` | sidecar order_builder |

---

## 9. Testing

**Backend (pytest):**
- `test_algo_capability_service.py` — capability query, cache hit/miss, unsupported broker returns empty, pubsub invalidation clears cache
- `test_algo_order_builder.py` — unit tests for each strategy's TagValue output; ICEBERG/RESERVE/DARK_ICE require LMT; TWAP/VWAP coerce to MKT; `display_size=0` raises ValueError
- `test_risk_service_algo.py` — `_check_algo_capability` BLOCK (place fail-CLOSED, preview fail-OPEN), `_check_iceberg_display_size` three-tier (≤0 BLOCK, ≥qty BLOCK, <1 WARN)
- `test_orders_service_algo.py` — preview + place with algo fields; 422 on unsupported strategy; 422 on modify with algo; 422 on bracket SL with algo
- `test_telegram_algo.py` — parser tests for all 6 strategies, `ARRIVAL` typo rejected with hint, unknown key rejection, missing required param rejection, DARK_ICE display_size=0 rejected
- `tests/integration/test_algo_order_e2e.py` — happy path (TWAP on STOCK, preview→risk→place→enriched order event); rejected path (TWAP on BOND → 422 unsupported_algo_strategy)

**Frontend (Vitest):**
- `AlgoSection.test.tsx` — renders for IBKR, hidden for Schwab, skeleton while loading; strategy picker populates form; LIMIT coercion notice for ICEBERG; MARKET coercion notice for TWAP
- `services/algo/api.test.ts` — getAlgoCapabilities happy path + empty list + getAlgoSchemas

---

## 10. Deferred

- Benchmark comparison stream (VWAP vs market VWAP, bps ahead/behind)
- Estimated algo completion time
- Per-slice child order detail view
- Algo support for Futu / Schwab / Alpaca
- Admin UI for enabling/disabling specific algo strategies per broker (pubsub invalidation channel defined this phase; publisher ships with admin UI)
- Plain hidden orders (`Order.hidden=True`) — separate `hidden: bool` on `OrderRequest`, not an algo strategy
- LOW-2: Integration test coverage gap noted above is addressed by `test_algo_order_e2e.py` added to §9
