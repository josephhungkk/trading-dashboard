# Phase 17 — IBKR Algo Orders Design

**Date:** 2026-05-18
**Version target:** v0.17.0
**Status:** Draft — pending architect review

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
- `HIDDEN` — show zero quantity (fully dark)
- `RESERVE` — show a fixed `display_size` with optional randomisation

**In scope:** IBKR only. All IBKR-supported asset classes (stocks, ETFs, options, futures, forex, bonds, CFDs). Telegram algo order support. Enriched order event stream (algo strategy + status visible in Orders page).

**Out of scope:** Algo support for Futu / Schwab / Alpaca (zero rows in `broker_algo_capability`). Benchmark comparison stream (VWAP vs market VWAP, pct_ahead). Estimated completion time. Per-slice child order detail view. These can be added in a later phase once real usage data informs requirements.

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
      'ICEBERG','HIDDEN','RESERVE'
    ));
```

All three columns are nullable. Non-algo orders leave them NULL.

### 2.2 Alembic 0057 — `broker_algo_capability` table

```sql
CREATE TABLE broker_algo_capability (
  broker_id     TEXT    NOT NULL,
  asset_class   TEXT    NOT NULL,
  algo_strategy TEXT    NOT NULL,
  enabled       BOOLEAN NOT NULL DEFAULT TRUE,
  notes         TEXT,
  PRIMARY KEY (broker_id, asset_class, algo_strategy)
);
```

Seeded at migration time with all 7 strategies × all IBKR asset classes (`STOCK`, `ETF`, `OPTION`, `FUTURE`, `FOREX`, `BOND`, `CFD`, `CRYPTO`, `MUTUAL_FUND`). All other brokers (`schwab`, `futu`, `alpaca`) get zero rows — the FE algo section will not render for them.

### 2.3 `algo_params` shapes

| Strategy | Required | Optional |
|---|---|---|
| `ADAPTIVE` | `urgency: PATIENT\|NORMAL\|URGENT` | — |
| `TWAP` | `start_time: HH:MM`, `end_time: HH:MM` | `allow_past_end_time: bool` |
| `VWAP` | `start_time: HH:MM`, `end_time: HH:MM` | `max_pct_vol: int (0–100)`, `no_take_liq: bool` |
| `ARRIVAL_PRICE` | `urgency: PATIENT\|NORMAL\|URGENT` | `max_pct_vol: int (0–100)` |
| `ICEBERG` | `display_size: Decimal` | — |
| `HIDDEN` | *(none)* | — |
| `RESERVE` | `display_size: Decimal` | `randomize_size: bool` |

---

## 3. Proto Changes

File: `proto/broker/v1/broker.proto`

### 3.1 `PlaceOrderRequest` additions

```protobuf
optional string algo_strategy = 26;  // "ADAPTIVE"|"TWAP"|"VWAP"|"ARRIVAL_PRICE"|"ICEBERG"|"HIDDEN"|"RESERVE"
optional string algo_params   = 27;  // JSON-encoded param dict
```

### 3.2 `PlaceOrderResponse` + `Order` message additions

```protobuf
optional string algo_strategy = 26;
optional string algo_status   = 27;  // last IBKR algo status string e.g. "PreSubmitted"
```

No new RPCs. Algo orders go through the existing `PlaceOrder` RPC. `CancelOrder` works unchanged — IBKR cancels the parent order and all child slices.

---

## 4. IBKR Sidecar

### 4.1 IBKR algo strategy string mapping

IBKR's TWS API uses different string identifiers than our internal enum. Static mapping in `order_builder.py`:

```python
_ALGO_STRATEGY_MAP = {
    "ADAPTIVE":      "Adaptive",
    "TWAP":          "Twap",
    "VWAP":          "Vwap",
    "ARRIVAL_PRICE": "ArrivalPx",
    "ICEBERG":       "Iceberg",    # TWS API algoStrategy string for plain iceberg
    "HIDDEN":        "DarkIce",    # TWS API: fully-dark iceberg variant
    "RESERVE":       "PctVol",     # TWS API: percentage-volume display with reserveSize
}
```

### 4.2 `build_ib_algo_order()` function

Called after the base order is built when `request.algo_strategy` is non-empty. Sets `order.algoStrategy` and populates `order.algoParams` as `[TagValue(k, v) for k, v in params.items()]`.

IBKR `TagValue` key mapping per strategy:

| Strategy | TagValue keys |
|---|---|
| `ADAPTIVE` | `adaptPriority` → urgency string |
| `TWAP` | `startTime` HH:MM:SS, `endTime` HH:MM:SS, `allowPastEndTime` 0/1 |
| `VWAP` | `startTime`, `endTime`, `maxPctVol`, `noTakeLiq` 0/1 |
| `ARRIVAL_PRICE` | `adaptPriority`, `maxPctVol` |
| `ICEBERG` | `displaySize` Decimal string |
| `HIDDEN` | `displaySize` = "0" |
| `RESERVE` | `displaySize`, `randomizeSize` 0/1 |

**Constraint enforcement in builder:**
- ICEBERG / HIDDEN / RESERVE: base `orderType` must be `LMT`; raises `ValueError` if `MKT` — caught by `orders_service` and returned as 422.
- TWAP / VWAP / ARRIVAL_PRICE / ADAPTIVE: base `orderType` must be `MKT`; coerced automatically (same as how IBKR handles it).

### 4.3 Enriched order event

`order_event_consumer.py`: when an `orderStatus` callback arrives for an order with `algo_strategy IS NOT NULL`, update `orders.algo_status` and include `algo_strategy` + `algo_status` in the existing order event WS push payload. No new WS endpoint.

---

## 5. Backend API

### 5.1 `OrderRequest` Pydantic model additions

```python
algo_strategy: AlgoStrategy | None = None
algo_params:   dict[str, Any] | None = None
```

`AlgoStrategy` is a `StrEnum` mirroring the 7 values.

`orders_service.preview_order` and `place_order` pass these through to `PlaceOrderRequest` after capability validation.

### 5.2 New endpoint: `GET /api/algo/capabilities/{broker_id}/{asset_class}`

Returns enabled strategies + parameter schemas for the FE to render the dynamic form. The parameter schema is sourced from `ALGO_PARAM_SCHEMAS` — a static dict in `app/services/algo/algo_capability_service.py`. Response:

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

JWT required. Rate-limited at 60/min (same as other read endpoints). Result cached in Redis for 5 minutes per `(broker_id, asset_class)` key.

### 5.3 `validate_pre_dispatch` extension

Queries `broker_algo_capability` when `algo_strategy` is present. Returns `422 unsupported_algo_strategy` if no enabled row found for `(broker_id, asset_class, algo_strategy)`.

### 5.4 Risk gate additions (`risk_service.py`)

Both checks fail-OPEN (gate does not block on DB/Redis error).

**`_check_algo_capability`** — BLOCK if `broker_algo_capability` has no enabled row for this broker + asset class + strategy.

**`_check_iceberg_display_size`** — applies when `algo_strategy IN ('ICEBERG', 'RESERVE')`:
- BLOCK if `display_size >= order_qty` (display must be less than total)
- WARN if `display_size < Decimal("1")` (display size below 1 lot — likely exchange-rejected)

---

## 6. Telegram

`order_flow.py` parser extended. Syntax:

```
/place_order AAPL BUY 100 ADAPTIVE urgency=URGENT
/place_order AAPL BUY 1000 TWAP start_time=10:00 end_time=14:00
/place_order AAPL BUY 1000 VWAP start_time=10:00 end_time=14:00 max_pct_vol=15
/place_order AAPL BUY 500 ARRIVAL_PRICE urgency=NORMAL
/place_order AAPL BUY 500 ICEBERG display_size=50
/place_order AAPL BUY 500 HIDDEN
/place_order AAPL BUY 500 RESERVE display_size=50 randomize_size=true
```

- 4th positional token is checked against the `AlgoStrategy` enum. Detection is unambiguous: algo tokens are all-uppercase alphabetic strings (`ADAPTIVE`, `TWAP`, etc.); price tokens are numeric strings (contain digits/decimal point). If the 4th token is alphabetic and matches an `AlgoStrategy` value, algo mode is active. If it is numeric or absent, the existing price-parsing behaviour is unchanged.
- `key=value` pairs after the strategy token are parsed into `algo_params`.
- Unknown keys → reject with Telegram error reply listing valid keys for the strategy.
- Missing required params → reject with hint listing required keys.
- Required param validation reuses `ALGO_PARAM_SCHEMAS` dict (single source of truth).

Existing `check_trade` rate-limit bucket (5/min, fail-CLOSED) applies unchanged.

---

## 7. Frontend

### 7.1 `AlgoSection` component

**Location:** `frontend/src/features/orders/AlgoSection.tsx`

Inserted into `TradeTicketModal` below the TIF row, above the sizing section.

**Render condition:** only renders when `getAlgoCapabilities(brokerId, assetClass)` returns at least one strategy. For non-IBKR brokers this returns an empty list → section is hidden entirely.

**Collapsed state:** "Algo Execution — Off" chip.

**Expanded state:**
1. Strategy `<Select>` — options drawn from capability response, each with a short description
2. Dynamic param form — field type driven by param schema:
   - `enum` → `<Select>`
   - `time` → `<Input type="time">`
   - `decimal` → `<NumericInput>`
   - `boolean` → `<Switch>`
3. Inline constraint note for ICEBERG/RESERVE: "Display size must be less than order quantity"
4. Auto-coercion notice:
   - ICEBERG/HIDDEN/RESERVE selected → `order_type` forced to `LIMIT`, user notified
   - TWAP/VWAP/ARRIVAL_PRICE/ADAPTIVE → `order_type` forced to `MARKET`, user notified

### 7.2 Orders page enrichment

- New `Algo` column in orders `DataTable` — hidden by default, toggleable via `ColumnCustomizerDialog`
- Shows strategy badge (e.g. `TWAP`) when `algo_strategy` is non-null
- `algo_status` shown as tooltip on the badge (e.g. "PreSubmitted → Submitted → Filled")

### 7.3 Services layer

- `frontend/src/services/algo/types.ts` — `AlgoStrategy` enum, `AlgoCapability`, `AlgoParamSchema`, `AlgoOrderFields`
- `frontend/src/services/algo/api.ts` — `getAlgoCapabilities(brokerId, assetClass): Promise<AlgoCapability[]>`

---

## 8. Prometheus Metrics

6 new counters in `app/api/orders.py` and `sidecar_ibkr/metrics.py`:

| Metric | Labels | Where |
|---|---|---|
| `algo_orders_submitted_total` | `strategy`, `broker_id`, `asset_class` | orders_service on place |
| `algo_orders_cancelled_total` | `strategy`, `broker_id` | order_event_consumer on cancel |
| `algo_capability_cache_hits_total` | `broker_id` | algo_capability_service |
| `algo_capability_cache_misses_total` | `broker_id` | algo_capability_service |
| `algo_risk_blocks_total` | `check`, `strategy` | risk_service |
| `algo_sidecar_errors_total` | `strategy`, `error_type` | sidecar order_builder |

---

## 9. Testing

**Backend (pytest):**
- `test_algo_capability_service.py` — capability query, cache hit/miss, unsupported broker returns empty
- `test_algo_order_builder.py` — unit tests for each strategy's TagValue output; constraint enforcement (ICEBERG requires LMT, etc.)
- `test_risk_service_algo.py` — `_check_algo_capability` BLOCK, `_check_iceberg_display_size` BLOCK + WARN
- `test_orders_service_algo.py` — preview + place with algo fields; 422 on unsupported strategy
- `test_telegram_algo.py` — parser tests for all 7 strategies, unknown key rejection, missing required param rejection

**Frontend (Vitest):**
- `AlgoSection.test.tsx` — renders for IBKR, hidden for Schwab; strategy picker populates form; order_type coercion notice
- `services/algo/api.test.ts` — getAlgoCapabilities happy path + empty list

---

## 10. Deferred

- Benchmark comparison stream (VWAP vs market VWAP, bps ahead/behind)
- Estimated algo completion time
- Per-slice child order detail view
- Algo support for Futu / Schwab / Alpaca
- Admin UI for enabling/disabling specific algo strategies per broker (currently migration-seeded only)
