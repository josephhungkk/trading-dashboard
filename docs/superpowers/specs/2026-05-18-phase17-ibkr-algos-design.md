# Phase 17 — IBKR Algo Orders Design

**Date:** 2026-05-18
**Version target:** v0.17.0
**Status:** Architect-review Pass-2 findings applied (CRIT-A/B, HIGH-A/B/C, MED-A/B/C/D)

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

**In scope:** IBKR only. STOCK, ETF, OPTION, FUTURE, FOREX per the explicit capability matrix in §2.2. Telegram algo order support. Enriched order event stream (algo_strategy on wire, visible in Orders page).

**Out of scope:** Algo support for Futu / Schwab / Alpaca (zero rows in `broker_algo_capability`). BOND / CFD / CRYPTO / MUTUAL_FUND (IBKR does not support execution algos for these; explicitly excluded from capability seed). Plain `Order.hidden=True` orders (separate feature, not this phase). Benchmark comparison stream (VWAP vs market VWAP, pct_ahead). Estimated completion time. Per-slice child order detail view.

---

## 2. Data Model

### 2.1 Alembic 0057 — `orders` table additions

```sql
ALTER TABLE orders
  ADD COLUMN algo_strategy TEXT,
  ADD COLUMN algo_params   JSONB;

ALTER TABLE orders
  ADD CONSTRAINT orders_algo_strategy_check
    CHECK (algo_strategy IN (
      'ADAPTIVE','TWAP','VWAP','ARRIVAL_PRICE',
      'ICEBERG','RESERVE','DARK_ICE'
    ));
```

Both columns nullable. Non-algo orders leave them NULL. `algo_status` column dropped (HIGH-A: duplicates existing `orders.status`; the FE badge shows strategy name, lifecycle state comes from the existing status column).

### 2.2 Alembic 0057 — `broker_algo_capability` table

Pattern matches sibling table `broker_order_capability` (verified in test DB — no `brokers` table exists; `instrument_asset_class` PG enum does not include BOND/CFD/MUTUAL_FUND at 0056 head):

```sql
CREATE TABLE broker_algo_capability (
  broker_id     VARCHAR(32)   NOT NULL,
  asset_class   VARCHAR(16)   NOT NULL,
  algo_strategy VARCHAR(32)   NOT NULL,
  enabled       BOOLEAN       NOT NULL DEFAULT TRUE,
  notes         TEXT          NOT NULL DEFAULT '',
  updated_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
  PRIMARY KEY (broker_id, asset_class, algo_strategy),
  CONSTRAINT broker_algo_capability_broker_id_valid
    CHECK (broker_id IN ('ibkr','futu','schwab','alpaca')),
  CONSTRAINT broker_algo_capability_asset_class_valid
    CHECK (asset_class IN (
      'STOCK','ETF','OPTION','FUTURE','FOREX','BOND','CFD','CRYPTO','MUTUAL_FUND'
    )),
  CONSTRAINT broker_algo_capability_algo_strategy_valid
    CHECK (algo_strategy IN (
      'ADAPTIVE','TWAP','VWAP','ARRIVAL_PRICE','ICEBERG','RESERVE','DARK_ICE'
    )),
  CONSTRAINT broker_algo_capability_notes_printable_ascii
    CHECK (notes ~ '^[\x20-\x7E]*$' AND length(notes) <= 256)
);
```

VARCHAR+CHECK (not PG enum) avoids cross-phase migration coupling and matches the sibling table convention exactly.

**Explicit capability seed** (verified against TWS API algo docs):

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

Only `enabled=TRUE` rows are inserted (those marked ✓). Zero-capability combinations are simply absent. The `notes` column on the enabled rows is `''`; document rejection reasons in migration comments, not DB rows.

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

Tags 26/27 are the next free slots after tag 25 (`oco_group_id`). Per R35 forward-extension convention:

```protobuf
optional string     algo_strategy = 26;  // "ADAPTIVE"|"TWAP"|"VWAP"|"ARRIVAL_PRICE"|"ICEBERG"|"RESERVE"|"DARK_ICE"
map<string, string> algo_params   = 27;  // strategy param key→value (Decimal-as-string convention)
reserved 28 to 35;  // algo forward-extension
```

`map<string,string>` — proper proto-level field type, no per-sidecar JSON parser, TagValue conversion is a one-liner.

### 3.2 `PlaceOrderResponse` additions

`PlaceOrderResponse` currently ends at tag 2. Use next sequential tags per per-message numbering (not cross-message 26/27):

```protobuf
optional string algo_strategy = 3;
reserved 4 to 25;  // forward growth
```

`algo_status` dropped from this message (HIGH-A). Lifecycle state is already on the response via the existing status fields.

### 3.3 `Order` message additions

`Order` currently ends at tag 24 (`expiry_date`) with `reserved 16 to 20`. Use 25, extend reserved:

```protobuf
optional string algo_strategy = 25;
reserved 26 to 35;  // algo + forward-extension
```

### 3.4 `OrderEventMessage` additions (HIGH-B)

`OrderEventMessage` currently has tags 1–9. Add algo_strategy on the wire so `order_event_consumer.py` gets it without a DB lookup per event:

```protobuf
optional string algo_strategy = 10;  // populated by sidecar when order has algoStrategy
reserved 11 to 20;
```

No new RPCs. Algo orders go through the existing `PlaceOrder` RPC. `CancelOrder` works unchanged.

---

## 4. IBKR Sidecar

### 4.1 IBKR algo strategy string mapping

IBKR's TWS API uses different string identifiers. Static mapping in `order_builder.py`:

```python
_ALGO_STRATEGY_MAP = {
    "ADAPTIVE":      "Adaptive",
    "TWAP":          "Twap",       # LOW-A: verify casing at impl time — may be "TWAP"
    "VWAP":          "Vwap",       # LOW-A: verify casing at impl time — may be "VWAP"
    "ARRIVAL_PRICE": "ArrivalPx",
    "ICEBERG":       "Iceberg",    # LOW-A: verify — may use Order.displaySize directly
    "RESERVE":       "PctVol",     # LOW-A: verify — may use Order.displaySize + reserveSize
    "DARK_ICE":      "DarkIce",    # LOW-A: verify casing
}
```

**LOW-A acceptance criterion:** The implementer must verify all strings against `ibapi/order_condition.py` (or a live TWS paper-trading test) and update the mapping with a source citation comment (e.g. `# ibapi/order_condition.py:42` or `# TWS API docs §algos, retrieved 2026-05-xx`). If iceberg-family strategies populate `Order.displaySize` directly instead of `algoStrategy`, rewrite §4.1/§4.2 accordingly.

### 4.2 `build_ib_algo_order()` function

Called after the base order is built when `request.algo_strategy` is non-empty. Sets `order.algoStrategy` and populates `order.algoParams` as `[TagValue(k, v) for k, v in request.algo_params.items()]`.

**`algo_params` size cap (MED-A):** Validate before constructing TagValue list:
- BLOCK (raise `ValueError`) if `len(request.algo_params) > 16`
- BLOCK if any value `len(v) > 64`
- Counter: folded into `algo_sidecar_errors_total{error_type="oversize_params"}`

IBKR `TagValue` key mapping per strategy:

| Strategy | TagValue keys |
|---|---|
| `ADAPTIVE` | `adaptPriority` → urgency string |
| `TWAP` | `startTime` HH:MM:SS, `endTime` HH:MM:SS, `allowPastEndTime` 0/1 |
| `VWAP` | `startTime`, `endTime`, `maxPctVol`, `noTakeLiq` 0/1 |
| `ARRIVAL_PRICE` | `adaptPriority`, `maxPctVol` |
| `ICEBERG` | `displaySize` Decimal string |
| `RESERVE` | `displaySize`, `randomizeSize` 0/1 |
| `DARK_ICE` | `displaySize` Decimal string (non-zero required by IBKR) |

**Constraint enforcement in builder:**
- ICEBERG / RESERVE / DARK_ICE: base `orderType` must be `LMT`; raises `ValueError` if `MKT`.
- TWAP / VWAP / ARRIVAL_PRICE / ADAPTIVE: base `orderType` must be `MKT`; coerced automatically.

### 4.3 Enriched order event

`order_event_consumer.py`: reads `algo_strategy` off the `OrderEventMessage` wire (tag 10, §3.4) — no DB lookup per event. Includes `algo_strategy` in the existing WS order-event push payload so the FE can render the algo badge on incoming events. No new WS endpoint.

---

## 5. Backend API

### 5.1 `OrderRequest` Pydantic model additions

```python
algo_strategy: AlgoStrategy | None = None
algo_params:   dict[str, str] | None = Field(default=None, max_length=16)
# Individual value lengths enforced in validate_pre_dispatch (max 64 chars each)
```

`AlgoStrategy` is a `StrEnum`: `ADAPTIVE`, `TWAP`, `VWAP`, `ARRIVAL_PRICE`, `ICEBERG`, `RESERVE`, `DARK_ICE`.

### 5.2 New endpoints

**`GET /api/algo/capabilities/{broker_id}/{asset_class}`**

Returns enabled strategies + parameter schemas for the FE dynamic form. JWT required. Rate-limited at 60/min. Cached in Redis 5 minutes per `(broker_id, asset_class)` key.

Invalidation: subscribes to `app_config:invalidate:algo_capability` pubsub channel.
- Payload `{"broker_id": "ibkr", "asset_class": "STOCK"}` → invalidate that key only.
- Payload `{}` → invalidate all cached entries.
Publisher deferred to the admin UI phase; consumer wired this phase.

Response:
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

Returns the full `ALGO_PARAM_SCHEMAS` dict for all 6 strategies. FE fetches on bootstrap — eliminates duplicate TypeScript schema definitions. JWT required. No caching needed (static data).

### 5.3 `validate_pre_dispatch` extension and call-site enumeration

New `algo_strategy: str | None = None` kwarg (default `None` — backward-compatible). When non-None: validates `algo_params` size cap (≤16 keys, each value ≤64 chars), then queries `broker_algo_capability`. Returns `422 unsupported_algo_strategy` if no enabled row found.

**Call-site behaviour:**

| Surface | Algo validated? | Notes |
|---|---|---|
| `preview_order` | Yes | fail-OPEN on DB error |
| `place_order` | Yes | fail-CLOSED on DB error (capability is deterministic) |
| `modify_order` | Comparison-based (HIGH-C) | See §5.3a |
| `place_bracket` parent | Yes | fail-CLOSED |
| `place_bracket` SL/TP legs | Reject | 422 `algo_on_bracket_leg_unsupported` if `algo_strategy` present |

#### §5.3a Modify rule (HIGH-C)

IBKR does not allow changing `algoStrategy` on a live order (requires cancel+replace). The modify rule for v0.17.0:

1. Read `stored_algo = orders.algo_strategy` for the order being modified.
2. If `request.algo_strategy != stored_algo` (including NULL→non-NULL or non-NULL→NULL) → `422 algo_modify_strategy_change_unsupported`. User must cancel + re-place.
3. If `request.algo_strategy == stored_algo` AND `request.algo_params != stored_algo_params` → `422 algo_modify_params_change_unsupported` (IBKR also forbids param changes; deferred to post-v0.17.0).
4. If `request.algo_strategy == stored_algo` AND `request.algo_params == stored_algo_params` → allow modify (qty/price change only); pass `algo_strategy` + `algo_params` through to sidecar unchanged.

### 5.4 `ALGO_PARAM_SCHEMAS` location

Lives in `app/services/algo/schemas.py` (leaf module — no imports from other `app/services/` modules). Both `algo_capability_service.py` and `app/services/telegram/order_flow.py` import from here.

### 5.5 Risk gate additions (`risk_service.py`)

**`_check_algo_capability`**
- BLOCK if `broker_algo_capability` has no enabled row for this broker + asset class + strategy.
- Fail-CLOSED on `place_order`. Fail-OPEN on `preview_order`.

**`_check_iceberg_display_size`** — applies when `algo_strategy IN ('ICEBERG', 'RESERVE', 'DARK_ICE')`:

```python
display_size_str = (ctx.algo_params or {}).get("display_size")
if display_size_str is None:
    return BLOCK("display_size_required")   # defensive; required-param check runs earlier
try:
    display_size = Decimal(display_size_str)
except InvalidOperation:
    return BLOCK("display_size_malformed")
if display_size <= 0:
    return BLOCK("display_size_nonpositive")
if display_size >= ctx.qty:
    return BLOCK("display_size_gte_qty")
if display_size < Decimal("1"):
    return WARN("display_size_sub_lot")    # fractional; legal on some venues
```

Fail-OPEN both paths (pure math; no external dependency).

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

**Closed-form token dispatch rule:**

```python
if len(tokens) >= 4 and tokens[3].upper() in AlgoStrategy.__members__:
    # algo path
elif len(tokens) >= 4 and tokens[3].replace(".", "", 1).replace(",", "", 1).isdigit():
    # price path — existing behaviour
else:
    raise TelegramParseError(
        f"Unexpected 4th token '{tokens[3]}'. Expected price or one of: "
        f"{', '.join(AlgoStrategy.__members__)}"
    )
```

- `key=value` pairs after the strategy token → `algo_params`.
- Unknown keys → Telegram error reply listing valid keys (from `ALGO_PARAM_SCHEMAS`).
- Missing required params → Telegram error reply with hint.
- DARK_ICE / ICEBERG / RESERVE: `display_size` must be numeric > 0 — parse-time validation.

Existing `check_trade` rate-limit bucket (5/min, fail-CLOSED) applies unchanged.

---

## 7. Frontend

### 7.1 `AlgoSection` component

**Location:** `frontend/src/features/orders/AlgoSection.tsx`

Inserted into `TradeTicketModal` below TIF row, above sizing section.

**Render condition:** only renders when `getAlgoCapabilities(brokerId, assetClass)` returns ≥1 strategy. Shows skeleton loader while in-flight. Hidden for non-IBKR brokers (empty list).

**Collapsed state:** "Algo Execution — Off" chip.

**Expanded state:**
1. Strategy `<Select>` — options from capability response, descriptions from `GET /api/algo/schemas`
2. Dynamic param form driven by schema type:
   - `enum` → `<Select>`
   - `time` → `<Input type="time">`
   - `decimal` → `<NumericInput>`
   - `boolean` → `<Switch>`
3. Constraint note for ICEBERG/RESERVE/DARK_ICE: "Display size must be > 0 and less than order quantity"
4. Auto-coercion notice:
   - ICEBERG/RESERVE/DARK_ICE → `order_type` forced to `LIMIT`
   - TWAP/VWAP/ARRIVAL_PRICE/ADAPTIVE → `order_type` forced to `MARKET`

**Stale capability (LOW-3):** If `getAlgoCapabilities` returns empty after a cache expiry between fetch and submit, the section hides and the order submits without an algo. Acceptable for v0.17.0; re-fetch on expand deferred.

### 7.2 Orders page enrichment

- New `Algo` column in `DataTable` — hidden by default, toggleable via `ColumnCustomizerDialog`
- Strategy badge (e.g. `TWAP`) when `algo_strategy` non-null; no `algo_status` tooltip (HIGH-A: dropped)

### 7.3 Services layer

- `frontend/src/services/algo/types.ts` — `AlgoStrategy` enum, `AlgoCapability`, `AlgoParamSchema`, `AlgoOrderFields` (types derived from `GET /api/algo/schemas` response at bootstrap — no duplicate TS definitions)
- `frontend/src/services/algo/api.ts` — `getAlgoCapabilities(brokerId, assetClass)`, `getAlgoSchemas()`

---

## 8. Prometheus Metrics

7 counters:

| Metric | Labels | Where |
|---|---|---|
| `algo_orders_submitted_total` | `strategy`, `broker_id`, `asset_class` | orders_service on place |
| `algo_orders_cancelled_total` | `strategy`, `broker_id` | order_event_consumer on cancel |
| `algo_orders_modify_rejected_total` | `strategy`, `reason` | orders_service on modify attempt |
| `algo_capability_cache_hits_total` | `broker_id` | algo_capability_service |
| `algo_capability_cache_misses_total` | `broker_id` | algo_capability_service |
| `algo_risk_blocks_total` | `check`, `strategy` | risk_service |
| `algo_sidecar_errors_total` | `strategy`, `error_type` | sidecar order_builder |

---

## 9. Testing

**Backend (pytest):**
- `test_algo_capability_service.py` — capability query, cache hit/miss, pubsub invalidation, unsupported broker returns empty
- `test_algo_order_builder.py` — each strategy's TagValue output; ICEBERG/RESERVE/DARK_ICE require LMT; TWAP/VWAP coerce MKT; `display_size=0` raises ValueError; oversize params raises ValueError
- `test_risk_service_algo.py` — `_check_algo_capability` fail-CLOSED/OPEN, `_check_iceberg_display_size` all five cases (None, malformed, ≤0, ≥qty, <1)
- `test_orders_service_algo.py` — preview + place; 422 unsupported; modify strategy-change 422; modify params-change 422; modify matching algo+params+qty-only allowed; bracket SL with algo 422
- `test_telegram_algo.py` — all 6 strategies, `ARRIVAL` typo hint, unknown key, missing required, DARK_ICE display_size=0 rejected
- `tests/integration/test_algo_order_e2e.py` — happy path (TWAP on STOCK, preview→risk→place→WS event with algo_strategy); rejected path (TWAP on BOND → 422)

**Frontend (Vitest):**
- `AlgoSection.test.tsx` — renders IBKR, hidden Schwab, skeleton in-flight; LIMIT coercion for ICEBERG; MARKET coercion for TWAP
- `services/algo/api.test.ts` — getAlgoCapabilities happy + empty + getAlgoSchemas

---

## 10. Deferred

- Benchmark comparison stream (VWAP vs market VWAP, bps ahead/behind)
- Estimated algo completion time
- Per-slice child order detail view
- Algo support for Futu / Schwab / Alpaca
- Admin UI for enabling/disabling algo strategies (pubsub consumer wired this phase; publisher ships with admin UI)
- Plain hidden orders (`Order.hidden=True`) — separate `hidden: bool` on `OrderRequest`, not an algo strategy
- Modify with matching algo strategy + different params (§5.3a item 3) — currently 422; full param-change support deferred post-v0.17.0
