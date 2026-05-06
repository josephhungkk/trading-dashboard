# Phase 8c Design — Alpaca Trade (US Equity + Crypto)

**Status:** Draft. Brainstorm 2026-05-06. Architect-review applied 2026-05-06. Pending user approval → plan.

**Predecessor:** Phase 8b (`v0.9.0`, target 2026-mid-May) brings full order-type expansion across IBKR/Futu/Schwab + Modify/Bracket/OCO. Phase 7c (`v0.7.3`, 2026-05-05) already shipped the `sidecar_alpaca` read-only adapter with per-mode Configure routing (live + paper sidecars) + 30-symbol streaming cap.

**Goal:** Bring Alpaca's trade write-path live across **US equity + crypto** with full RPC parity vs the other 3 brokers (Place/Cancel/Modify/Bracket/OCO + OrderEvent + SearchContracts). First broker in the system with a non-equity asset class — adds the asset-class dimension to the capability matrix.

---

## Brainstorm decisions (2026-05-06)

| Q | Topic | Pick | What it means |
|---|---|---|---|
| 1 | Equity vs crypto unification + matrix shape | **B** | Add `asset_class` to `broker_order_capability` PK → 4-tuple `(broker_id, asset_class, order_type, tif)`. Migration extends PK + reseeds (~800 rows). FE hook accepts optional `assetClass` param. |
| 2 | Crypto wire conventions | **A + b** | Symbol format = slashed `BTC/USD` (Alpaca native, future-proof for Coinbase). Qty regex bumped from 8dp to **10dp** to fit Alpaca crypto pairs. |
| 3 | Empirical hard-gates | **C** | New empirical scripts for crypto + bracket (both net-new code paths). Equity rides the existing `nightly-real-alpaca.yml` (extended with trade cases). |
| 4 | Fractional/cash-amount orders ("buy $100 of AAPL") | **B** | Add optional `cash_amount: str` field to proto/schema (proto tag 15). `broker_features.notional_orders` capability row per (broker, asset_class). Alpaca = TRUE; others = FALSE. **Note:** `notional` is an existing response field (qty × price); the new REQUEST field is named `cash_amount` to avoid collision. |
| 5 | RPC coverage scope | **D** | Full coverage — Place + Cancel + Modify + Bracket + OCO + OrderEvent + SearchContracts. Symmetric with 8a/8b. |

---

## Sequencing — per-asset-class

```
8c-0   Foundation: 4-tuple matrix migration (Alembic 0018, atomic) + asset_class enum widening + 10dp regex +
       cash_amount field + Alpaca proto pass-through + is_supported_3tuple_deprecated() shim
8c-S   Stocks: Alpaca equity write-path (Place/Cancel/Modify/Bracket/OCO/OrderEvent/SearchContracts via /v2/orders)
8c-C   Crypto: Alpaca crypto write-path (same RPCs via /v1beta3/crypto/{loc}/orders) + empirical script + capability flip
8c-B   Bracket empirical hard-gate — split into 8c-B-eq (equity, high confidence) and 8c-B-cr (crypto, gated FALSE)
8c-OCO OCO Alpaca-native dispatch (extends 8b orchestrator with per-asset-class Alpaca branches)
8c-close  CHANGELOG + tag v0.10.0
```

Per-chunk PR style: single-shot per asset class. Net new empirical scripts: **3** (Alpaca crypto, Alpaca bracket equity, Alpaca OCO). All run against Alpaca paper (free-money sandbox).

---

## Section 1 — 4-tuple capability matrix (8c-0 foundation)

### Alembic 0018 — atomic migration contract (CRIT-2, MED-2, HIGH-2)

**Alembic 0018 ships as a SINGLE PR that includes ALL of the following — no split:**

1. `broker_order_capability` PK widened to 4-tuple `(broker_id, asset_class, order_type, tif)`.
2. `broker_features` PK widened to `(broker_id, asset_class, feature)` (HIGH-2).
3. `OrderCapabilityService.is_supported()` signature widened to 4-tuple `(broker, asset_class, order_type, tif)` + every call site updated.
4. `is_supported_3tuple_deprecated(broker, order_type, tif)` shim added (defaults `asset_class='STOCK'`, emits structlog warning, increments `order_capability_legacy_3tuple_calls_total` counter).
5. Deprecation audit checklist delivered (grep targets + counter SLO ≤ 0 in 24h).

**Deprecation shim contract:**

```python
def is_supported_3tuple_deprecated(
    self, broker: str, order_type: str, tif: str
) -> bool:
    """Deprecated: use is_supported(broker, asset_class, order_type, tif).
    Defaults asset_class='STOCK'. Emits structlog warning + counter.
    SLO: counter must reach 0 within 24h of 0018 deploy.
    """
    structlog.get_logger().warning(
        "order_capability_legacy_3tuple_called",
        broker=broker, order_type=order_type, tif=tif,
    )
    metrics.counter("order_capability_legacy_3tuple_calls_total").inc()
    return self.is_supported(broker, "STOCK", order_type, tif)
```

**Grep targets for audit (run before merging 8c-0):**

```bash
grep -rn "is_supported(" app/ | grep -v "is_supported_3tuple_deprecated" | grep -v "is_supported(broker,"
# Must return 0 lines
```

**Migration atomicity guarantee:** 0018 runs in a single transaction. `op.execute("LOCK TABLE broker_order_capability IN ACCESS EXCLUSIVE MODE")` is the first statement in `upgrade()`. No FK references `broker_order_capability` (verified pre-flight — no FK constraints reference this table).

**Explicit `downgrade()` block:** reversal drops the added `asset_class` column from both tables, restores original 3-tuple PK on `broker_order_capability`, and restores original 2-tuple PK on `broker_features`. No data is recoverable after downgrade (document in migration header comment).

**Schema migration (Alembic 0018):**

```sql
-- 0018_phase8c_capability_4tuple.py upgrade()
-- Lock acquired first in Python: op.execute("LOCK TABLE broker_order_capability IN ACCESS EXCLUSIVE MODE")

-- broker_order_capability: 3-tuple → 4-tuple PK
ALTER TABLE broker_order_capability ADD COLUMN asset_class VARCHAR;
UPDATE broker_order_capability SET asset_class = 'STOCK' WHERE asset_class IS NULL;
ALTER TABLE broker_order_capability ALTER COLUMN asset_class SET NOT NULL;
ALTER TABLE broker_order_capability ADD CONSTRAINT broker_order_capability_asset_class_check
    CHECK (asset_class IN ('STOCK','CRYPTO','OPTION','FUTURE','FOREX','BOND'));
ALTER TABLE broker_order_capability DROP CONSTRAINT broker_order_capability_pkey;
ALTER TABLE broker_order_capability ADD PRIMARY KEY (broker_id, asset_class, order_type, tif);

-- All new rows use ON CONFLICT DO NOTHING for idempotency (MED-11)
-- INSERT INTO broker_order_capability (broker_id, asset_class, order_type, tif, is_supported, notes)
-- VALUES (...)
-- ON CONFLICT (broker_id, asset_class, order_type, tif) DO NOTHING;

-- broker_features: (broker_id, feature) → (broker_id, asset_class, feature) PK (HIGH-2)
ALTER TABLE broker_features ADD COLUMN asset_class VARCHAR NOT NULL DEFAULT 'STOCK';
ALTER TABLE broker_features ADD CONSTRAINT broker_features_asset_class_check
    CHECK (asset_class IN ('STOCK','CRYPTO','OPTION','FUTURE','FOREX','BOND'));
ALTER TABLE broker_features DROP CONSTRAINT broker_features_pkey;
ALTER TABLE broker_features ADD PRIMARY KEY (broker_id, asset_class, feature);

-- Backfill bracket + notional_orders per asset_class for Alpaca (HIGH-2)
-- (alpaca, STOCK, bracket, TRUE) — equity bracket: high-confidence
-- (alpaca, CRYPTO, bracket, FALSE) — crypto bracket: gated FALSE
-- (alpaca, STOCK, notional_orders, TRUE)
-- (alpaca, CRYPTO, notional_orders, TRUE)
-- All via INSERT ... ON CONFLICT DO NOTHING
```

**Reseed plan**: existing 200 rows (4 brokers × 10 types × 5 TIFs, all `asset_class='STOCK'` post-backfill) get extended:

- Add `(alpaca, CRYPTO, *, *)` rows = 50. Initial state: only `(MARKET|LIMIT, GTC|IOC|FOK)` = 6 supported. Rest `is_supported=FALSE` with notes (e.g. `notes="Crypto: 24/7 — DAY meaningless"`).
- Add `(schwab, CRYPTO, *, *)` rows = 50, all `is_supported=FALSE` (Schwab doesn't support crypto trade today).
- Add `(ibkr, CRYPTO, *, *)` = 50 placeholder (IBKR Paxos crypto comes in Phase 15).
- Add `(futu, CRYPTO, *, *)` = 50 placeholder.
- (OPTION/FUTURE/FOREX/BOND rows deferred to Phase 12+.)

Final matrix size: **400 rows** (4 brokers × 2 asset classes × 10 types × 5 TIFs).

**Idempotency:** all INSERTs use `ON CONFLICT (broker_id, asset_class, order_type, tif) DO NOTHING`. Test: `test_alembic_0018_idempotent.py` runs upgrade() twice against a test DB and asserts row count unchanged on second pass.

**Empirical FAIL outcome:** if any empirical gate fails, still ship Alembic with `is_supported=FALSE` for the relevant rows + descriptive `notes` field explaining the failure. The capability row documents reality; the flip migration arrives later. This pattern applies to all empirical-gated migrations in 8c (0019–0022).

**Backend changes:**

- `OrderCapabilityService` adds `asset_class` to its cache key + lookup signature (4-tuple).
- Cache max size bumped to **2048** (was 512) or made configurable via `app_config.broker.capability_cache_size`. Metric `order_capability_cache_evictions_total` added.
- `GET /api/brokers/{id}/capabilities?asset_class=CRYPTO` accepts optional query param.
- **Default behavior when `asset_class` missing AND broker has >1 supported asset class:** returns HTTP 200 with body `{"STOCK": [...], "CRYPTO": [...]}` — grouped by asset class. When only one asset class exists for broker, returns flat list (backward compat). Document in OpenAPI description.
- `POST /api/admin/order-capabilities` body widens to include `asset_class`; existing rows continue to validate as STOCK.

**Enum note (HIGH-7):** No `broker_id` enum changes needed in 8c — `alpaca` is already present in both `broker_order_capability` and `broker_features` CHECK constraints (shipped in Phase 7c). The Phase 7b.1 13-source streaming enum requires no change either since `alpaca` is already wired.

**FE changes** (deferred to a follow-on Chunk F8c):

- `useBrokerCapabilities(brokerId, assetClass?)` adds optional second arg.
- `TradeTicketModal` passes `Contract.asset_class` to the hook.

---

## Section 2 — Crypto wire conventions (8c-0 + 8c-C)

### Symbol format (Q2-A)

Crypto pairs use **slashed format** verbatim across the wire: `BTC/USD`, `ETH/USD`, `SHIB/USD`. The `Contract.symbol` field carries it as opaque string. Adapter passes verbatim to Alpaca.

**Symbol normalization contract (MED-8):** canonical wire format is `BTC/USD` (slashed) end-to-end across all layers. The sidecar streamer normalizes from Alpaca's WebSocket on-the-wire format (which may use `BTCUSD` without slash) on ingress via:

```python
# app/brokers/symbol_normalize.py
def canonical_crypto_symbol(s: str) -> str:
    """Normalize Alpaca WS crypto symbol to canonical slashed form.
    Examples: 'BTCUSD' → 'BTC/USD', 'BTC/USD' → 'BTC/USD'.
    """
    if "/" in s:
        return s
    # Heuristic: all known quote currencies are 3 chars (USD, EUR, GBP, BTC, ETH)
    return f"{s[:-3]}/{s[-3:]}"
```

Helper lives in `app/brokers/symbol_normalize.py` and is imported by `sidecar_alpaca/normalize.py`.

### Quantity precision (Q2-b)

Schema regex updated:
- Before: `^\d+(\.\d{1,8})?$` (8dp)
- After: `^\d+(\.\d{1,10})?$` (10dp)

Applies to `qty`, `limit_price`, `stop_price`, `trail_offset`, `trail_limit_offset`, `cash_amount`.

**10dp downstream impact (HIGH-5):**

- Alembic 0018 ALSO alters `orders.qty`, `orders.filled_qty`, `order_events.fill_qty` to `NUMERIC(20, 10)`.
- Rename `_format_decimal_8` → `_format_decimal_10` in the Alpaca adapter, OR keep both and dispatch on `asset_class` (dispatch approach preferred: `_format_decimal(value, asset_class)` calls 8dp for STOCK, 10dp for CRYPTO).
- All `Decimal("1e-8").quantize(...)` call sites for CRYPTO must become `Decimal("1e-10")`. Audit grep: `grep -rn "1e-8" app/brokers/alpaca`.
- **Boundary contract:** qty `NUMERIC(20, 10)`, money/price `NUMERIC(20, 8)`, avg_cost `NUMERIC(20, 8)`. Money columns do NOT change.

Existing equity orders (max 8dp in practice) unaffected — 10dp is a superset.

### cash_amount orders (Q4-B) — field naming

> **Naming contract:** `notional` is an **existing response field** (USD value of qty × price, reported on orders and positions). The new **request-side** field for "buy $N USD worth" is named **`cash_amount`** to avoid collision. These are different concepts: `notional` (response) ≠ `cash_amount` (request).

New optional field `cash_amount: str | None = None` on `PreviewRequest` / `PlaceOrderRequest` / `OrderModifyRequest`:

- Same regex pattern as `qty` (`^\d+(\.\d{1,10})?$`).
- `qty: str | None = None` — both are now Optional; exactly one must be set.
- Validator (via `model_validator`) enforces **XOR: exactly one of `qty` or `cash_amount` is set**. Both = 422; neither = 422.

**Additional XOR constraints (HIGH-4):**

- `cash_amount` implies `side=BUY` — Alpaca rejects notional for SELL. Backend validates and returns 422 if violated.
- `cash_amount` implies `order_type=MARKET` AND `tif=DAY`. Any other combination → 422 with descriptive message.
- Bracket children always carry `qty` — `cash_amount` flows only to the parent leg. Backend validator enforces this.
- Modify path (`replace_order_by_id`): rejects any `qty` ↔ `cash_amount` swap (i.e. a modify cannot switch between qty-based and cash-based). Returns 422 `"Cannot change between qty and cash_amount on modify"`.

Proto field tag 15:

```proto
// Phase 8c — tag 15
// Request-side fractional cash amount in USD (e.g. "100.00").
// XOR with qty. Implies side=BUY, order_type=MARKET, tif=DAY.
// Distinct from response field 'notional' (qty × price).
string cash_amount = 15;
```

**ETF → capability bucket mapping (MED-7):**

`broker_features.notional_orders` is looked up by `(broker_id, asset_class, 'notional_orders')`. ETFs are treated as STOCK for capability purposes via the EQUITY bucket adapter:

```python
# app/services/order_capability_service.py
_ASSET_CLASS_BUCKET: dict[str, str] = {
    "STOCK": "STOCK",
    "ETF": "STOCK",   # collapse into STOCK capability bucket
    "CRYPTO": "CRYPTO",
    # Phase 12+: OPTION, FUTURE, FOREX, BOND
}

def _capability_bucket(asset_class: str) -> str:
    return _ASSET_CLASS_BUCKET.get(asset_class, asset_class)
```

Capability lookups pass `_capability_bucket(asset_class)` not raw `asset_class`.

Adapter for Alpaca: if `cash_amount` set → POST body has `notional: "<cash_amount value>"`; else `qty: "5.5"`.
Adapter for Schwab/IBKR/Futu: if `cash_amount` set → schema-level reject via capability gate (`broker_features.notional_orders` per (broker_id, STOCK, 'notional_orders') = FALSE).

---

## Section 3 — 8c-S Alpaca equity write-path

**Capability flip (Alembic 0019):** flip Alpaca STOCK rows.

Initial supported: 16 combos = MARKET/LIMIT/STOP/STOP_LIMIT × DAY/GTC/IOC/FOK. Session-bound types DAY-only per HIGH-1 from 8b.

**Adapter changes** (`sidecar_alpaca/handlers.py` + `sidecar_alpaca/normalize.py`):

- Flip `PlaceOrder` from UNIMPLEMENTED to live. `alpaca-py`'s `TradingClient.submit_order()`.
- Flip `CancelOrder` to live. `TradingClient.cancel_order_by_id(order_id)`.
- Flip `ModifyOrder` to live (Q5-D). `TradingClient.replace_order_by_id(order_id, **kwargs)`.
- Flip `SearchContracts` to live. `TradingClient.get_assets()` filtered by symbol prefix.
- `OrderEvent` server-streaming via `TradingStream` — extend the existing Phase 7c `sidecar_alpaca` quote streamer pattern.

**Dual-subscription architecture (MED-10):** The sidecar maintains TWO simultaneous subscriptions fan-in'd into one `OrderEvent` gRPC server-stream:

1. **Equity `TradingStream`** (`alpaca-py` `TradingStream`) — equity order events via `/v2/stream`.
2. **Crypto stream** (`/v1beta3/crypto/{loc}/stream`) — crypto order events.

Both streams feed a single `asyncio.Queue` which the gRPC `OrderEvent` handler drains. Unit test: `test_order_event_dual_stream_interleave.py` — simulates parent (equity) + child (crypto) events arriving out-of-order and asserts correct sequence in the gRPC stream output.

**TradingStream cap (MED-9):** Maximum 5 concurrent `TradingStream` subscriptions enforced in `sidecar_alpaca/handlers.py::OrderEvent` subscribe handler. If a 6th subscription is attempted, return gRPC `RESOURCE_EXHAUSTED` with `details="trading_stream_cap_5"`. Backend surfaces this as HTTP 429 to the FE.

**`clientOrderId` behavior (MED-5):** Empirical assertion 3 in `alpaca_crypto_paper.py` confirms Alpaca supports `client_order_id` round-trips. **Chosen fallback policy:**

- Empirical PASS → use `client_order_id` on every POST to both `/v2/orders` and `/v1beta3/crypto/.../orders`.
- Empirical FAIL → reject crypto submissions with 422 `"client_order_id not supported by this endpoint"` (do NOT attempt silent deduplication — fail explicitly to surface the gap).

**Per-mode routing:** Phase 7c established `alpaca-live` and `alpaca-paper` as separate sidecar containers with per-mode Configure (`phase7c_alpaca_topology.md`). Trade write-path uses the SAME pattern.

**Validation strategy (Q3-C):** no equity-specific empirical script. Existing `nightly-real-alpaca.yml` extended with parametrized cases:
- LIMIT BUY 1 SPY at $1 (cancel immediately).
- MOC BUY 1 SPY (cancel before cutoff).
- TRAIL BUY 1 SPY by 0.5%.

**Tests:**
- `sidecar_alpaca/tests/test_handlers_place_order.py` — type-by-type payload assertions.
- `backend/tests/integration/test_alembic_0019.py` — post-flip count.
- `sidecar_alpaca/tests/test_order_event_dual_stream_interleave.py` — dual-stream fan-in (MED-10).

---

## Section 4 — 8c-C Alpaca crypto write-path

**Capability flip (Alembic 0020):** flip Alpaca CRYPTO rows. Initial supported: 6 combos (per Section 1).

**crypto_location config (HIGH-6):** `location` parameter (e.g. `us`) for `/v1beta3/crypto/{location}/orders` is read from `app_config.broker.alpaca.crypto_location` (default `"us"`). Per-account `crypto_location` override (for future international expansion) is deferred to Phase 16+ — it would live in `broker_accounts.metadata JSONB` when implemented. Callers must not hardcode `"us"`.

**Adapter changes:**

- `sidecar_alpaca/handlers.py::PlaceOrder` discriminates on `Contract.asset_class`:
  - `STOCK` → `TradingClient.submit_order()` against `/v2/orders`.
  - `CRYPTO` → `CryptoTradingClient` against `/v1beta3/crypto/{location}/orders`.
- `Contract.symbol` carries `BTC/USD`-style slashed format verbatim (see Section 2 normalization).
- `cash_amount` support: if `request.cash_amount` set, payload has `notional: <cash_amount value>`; else `qty: ...`.
- Dual-subscription: crypto order events come from `/v1beta3/crypto/{loc}/stream`, fan-in'd into the same `OrderEvent` queue as equity `TradingStream` events (see Section 3).

**Validation strategy (Q3-C — NEW empirical script):**

`scripts/empirical/alpaca_crypto_paper.py` — 5 assertions:
1. POST /v1beta3/crypto/us/orders returns 201 with body containing `id`.
2. Response `id` matches order id polled back via GET `/v2/orders/{id}`.
3. `client_order_id` round-trips on subsequent GET (empirical confirmation — determines fallback policy per Section 3).
4. Cancel returns 204; subsequent poll shows `status: "canceled"` within 3s.
5. `BUY 0.0001 BTC/USD LIMIT $10` payload accepted (validates 10dp qty regex round-trip).

**Empirical FAIL outcome:** if script fails, Alembic 0020 still ships with `is_supported=FALSE` for the relevant rows + `notes` documenting the failure reason. The capability row documents reality.

Hard-gates the 0020 flip.

**Tests:**
- `sidecar_alpaca/tests/test_handlers_place_order_crypto.py`.
- `backend/tests/integration/test_alembic_0020.py`.
- `backend/tests/real_broker/test_real_alpaca_crypto_e2e.py` (marker `real_alpaca`).
- `backend/tests/unit/test_orders_service_crypto_bypasses_market_calendar.py` (CRIT-3 — see Section 7).

---

## Section 5 — 8c-B Alpaca bracket

Per Q3-C, bracket gets its own empirical hard-gate. Bracket is split into two sub-tracks by asset class (HIGH-1, MED-6):

### 8c-B-eq: Equity bracket (high confidence)

`broker_features` row `(alpaca, STOCK, bracket)` = TRUE (shipped in 0018 backfill). Micro-empirical confirms it before 8c-B-eq PR merges (not a hard-gate — equity bracket is well-supported by Alpaca).

**Adapter changes:** `sidecar_alpaca/handlers.py::PlaceBracket` flips for STOCK. Alpaca's `order_class="bracket"` accepts `take_profit{limit_price}` + `stop_loss{stop_price, limit_price}` in a SINGLE atomic POST.

```python
# Conceptual:
def to_alpaca_bracket_payload(parent: Order, sl: Order | None, tp: Order | None) -> dict:
    body = to_alpaca_order_payload(parent)
    body["order_class"] = "bracket"
    if tp:
        body["take_profit"] = {"limit_price": tp.limit_price}
    if sl:
        body["stop_loss"] = {"stop_price": sl.stop_price}
        if sl.order_type == "STOP_LIMIT":
            body["stop_loss"]["limit_price"] = sl.limit_price
    return body
```

**Bracket children always carry `qty`** (not `cash_amount`) — enforced by `model_validator` (HIGH-4).

**Validation:** `scripts/empirical/alpaca_bracket_paper.py` — 4 assertions:
1. POST returns 201 with parent + child IDs (Alpaca returns parent body with `legs` array).
2. Parent + 2 child IDs all distinct, all queryable via GET /v2/orders/{id}.
3. Cancel parent → both children's status reads `canceled` within 5s on TradingStream.
4. No partial cascade.

**Capability flip:** `broker_features` row `(alpaca, STOCK, bracket)` is pre-seeded TRUE in Alembic 0018. Alembic 0021 confirms and potentially enables additional combos post-empirical.

### 8c-B-cr: Crypto bracket (gated FALSE)

`broker_features` row `(alpaca, CRYPTO, bracket)` = FALSE (default in 0018 backfill). Empirical script required before any flip — Alpaca crypto bracket support is NOT confirmed. Separate empirical script: `scripts/empirical/alpaca_crypto_bracket_paper.py`. Capability flip migration (also in 0021 or a separate 0021b) only if empirical PASS.

**Empirical FAIL outcome:** row stays FALSE. Sidecar returns `UNIMPLEMENTED` for CRYPTO bracket. Backend gates via `broker_features` check before forwarding.

---

## Section 6 — 8c-OCO Alpaca OCO dispatch

Per Q5-D, Alpaca OCO is **broker-native** via `order_class="oco"`. The 8b OCO orchestrator already has the multi-broker dispatch pattern; 8c adds Alpaca as a **native dispatch case** — but split by asset class (HIGH-3):

### dispatch_oco_alpaca_equity (native)

Uses Alpaca's native `order_class="oco"` — atomic at broker, no orchestrator watcher needed. Same race-free guarantee as Schwab + IBKR.

```python
def dispatch_oco_alpaca_equity(legs: list[OrderRequest]) -> str:
    body = to_alpaca_order_payload(legs[0])
    body["order_class"] = "oco"
    body["legs"] = [to_alpaca_order_payload(leg) for leg in legs[1:]]
    # POST /v2/orders, atomic
    ...
```

### dispatch_oco_alpaca_crypto (likely UNSUPPORTED)

Alpaca crypto OCO support is empirically unconfirmed. The micro-empirical script (`alpaca_oco_paper.py`) MUST test both equity and crypto OCO. If crypto path returns non-201 or is missing from the API, `dispatch_oco_alpaca_crypto` returns `UNIMPLEMENTED`. The capability flip 0022 is **per-asset-class**:

- `(alpaca, STOCK, oco, TRUE)` — post equity empirical PASS.
- `(alpaca, CRYPTO, oco, FALSE or TRUE)` — post crypto empirical result.

```python
def dispatch_oco_alpaca_crypto(legs: list[OrderRequest]) -> str:
    # Orchestrator fallback: if Alpaca crypto OCO not supported,
    # return gRPC UNIMPLEMENTED; backend returns 422 to FE.
    raise NotImplementedError("Alpaca crypto OCO not empirically confirmed")
```

**State machine:** reuses 8b's 9-state machine. Alpaca's atomic equity OCO means same race window as Schwab/IBKR (none broker-side).

**Validation (Q3-C empirical script #3):** `scripts/empirical/alpaca_oco_paper.py` — must run BOTH branches:

Equity assertions (3):
1. POST returns 201 with both leg IDs in `legs` array.
2. Both legs queryable; share `order_class="oco"` flag.
3. Cancel parent group → both legs `canceled`.

Crypto assertions (2):
4. POST to `/v1beta3/crypto/us/orders` with `order_class="oco"` — record HTTP status.
5. If 201: confirm leg IDs. If 4xx: document error body verbatim. Outcome drives 0022 crypto flip.

**Capability flip:** Alembic 0022 is per-asset-class. `(alpaca, STOCK, oco)` = TRUE post-equity-PASS. `(alpaca, CRYPTO, oco)` flipped only on crypto empirical PASS; stays FALSE otherwise.

---

## Section 7 — Crypto bypass contract (CRIT-3)

### Market calendar bypass for CRYPTO asset class

`Contract.asset_class == "CRYPTO"` short-circuits ALL `market_calendar.*` calls in `orders_service.py` validation. Rationale: crypto markets are 24/7; market calendar concepts (open/close, session types, trading halts) do not apply.

**Implementation contract:** wrap every `market_calendar` call site in `orders_service.py`:

```python
# Pattern to apply at every market_calendar call site:
if asset_class != "CRYPTO":
    market_calendar.assert_market_open(exchange, order_type)
    # ... other calendar checks ...
```

**GTD on crypto = naive UTC EOD:**

```python
# app/services/market_calendar.py
def crypto_eod(expiry_date: date) -> datetime:
    """Return naive UTC end-of-day for a crypto GTD order.
    Crypto has no exchange close, so EOD is 23:59:59 UTC on expiry_date.
    """
    return datetime.combine(expiry_date, time(23, 59, 59), timezone.utc)
```

Used in `orders_service.py` when `asset_class == "CRYPTO"` and `tif == "GTD"`.

**Unit test:** `backend/tests/unit/test_orders_service_crypto_bypasses_market_calendar.py` — patches `market_calendar` to raise if called, then submits a CRYPTO order and asserts no exception. Separate test case for GTD crypto: asserts `crypto_eod()` is used instead of calendar expiry.

---

## Section 8 — Cross-cutting

### `Contract.asset_class` enum widening

Phase 8a A1 proto already includes `AssetClass` enum with `STOCK`, `ETF`, `OPTION`, `FUTURE`, `FOREX`, `BOND`, **`CRYPTO`**. No proto change needed.

Backend `app/brokers/base.py::AssetClass` Literal already covers CRYPTO. No change.

**Broker_id enum and source-enum note (HIGH-7):** No `broker_id` enum changes needed in 8c — `alpaca` is already present in both `broker_order_capability` and `broker_features` CHECK constraints (shipped in Phase 7c). The Phase 7b.1 13-source streaming enum requires no change since `alpaca` is already wired.

### cash_amount field plumbing (8c-0 chunk)

Proto tag 15 (`cash_amount`) + Pydantic field (`cash_amount: str | None = None`) + adapter pass-through (maps to `notional` key in Alpaca REST body). Same scaffold pattern as the 8b TRAIL fields. Tests assert XOR with `qty` and all 4 additional constraints from Section 2.

### 30-symbol streaming cap (Phase 7c carryover)

Cap applies to **streaming quotes**, NOT trade write-path. Trade is per-symbol on demand. No new constraint.

### Empirical artifact PII redaction

Same MED-5 pattern from 8b — pre-commit hook `scripts/pre-commit-check-empirical-artifacts.sh` already enforces. Alpaca paper account numbers like `PA-XXXXXX` added to the redact regex.

### Alembic migration plan

- 0018 — (ATOMIC PR) extend `broker_order_capability` + `broker_features` PK with asset_class; backfill; reseed for new rows; `is_supported_3tuple_deprecated()` shim; 10dp column widening (qty/filled_qty/fill_qty) — all in one transaction
- 0019 — Alpaca STOCK partial flip (8c-S close)
- 0020 — Alpaca CRYPTO partial flip (8c-C close, gated on crypto empirical)
- 0021 — `broker_features.(alpaca, STOCK, bracket)` = TRUE (8c-B-eq close); `(alpaca, CRYPTO, bracket)` flip only on crypto bracket empirical PASS
- 0022 — `broker_features.(alpaca, STOCK, oco)` = TRUE (8c-OCO equity close); `(alpaca, CRYPTO, oco)` flip only on OCO crypto empirical PASS

---

## Out of scope (Phase 8c)

- **Crypto on other brokers**: Schwab/IBKR/Futu CRYPTO rows ship as all-unsupported placeholders. IBKR Paxos crypto comes in Phase 15.
- **Options trading**: `Contract.asset_class=OPTION` rows for any broker. Phase 12.
- **cash_amount for non-Alpaca brokers**: Phase 9+.
- **Crypto staking / yield**: Phase 16+.
- **Per-account `crypto_location`**: Phase 16+ (would live in `broker_accounts.metadata JSONB`).
- **Alpaca extended-hours sessions** (`extended_hours: true` on POST /v2/orders): defer.

---

## Risks (in order of priority)

1. **HIGH — `alpaca-py` crypto SDK coverage**: the SDK has separate `CryptoTradingClient` vs `TradingClient`. Confirm crypto place/cancel/modify all expose async equivalents. Spike script in 8c-0 imports both and asserts presence of the 5 RPC methods.
2. **HIGH — Alpaca crypto-specific clientOrderId behavior**: Schwab rejected clientOrderId (Phase 8a empirical finding); Alpaca *says* it supports it. Empirical script assertion 3 confirms; FAIL → explicit reject policy (Section 3).
3. **HIGH — 4-tuple matrix migration + broker_features PK widening**: must be atomic single PR; `is_supported_3tuple_deprecated()` shim required; SLO ≤ 0 legacy calls within 24h.
4. **HIGH — Crypto bracket support**: unconfirmed. 8c-B-cr defaults FALSE; separate empirical gating.
5. **MEDIUM — Alpaca `loc` URL parameter for crypto**: configurable via `app_config.broker.alpaca.crypto_location`; per-account override deferred to Phase 16+.
6. **MEDIUM — Dual-stream fan-in complexity**: equity TradingStream + crypto stream must interleave correctly; unit test required (MED-10).

---

## Estimate

- 8c-0: 3 days (4-tuple migration + broker_features PK widening + deprecation shim + reseed + asset_class extension + 10dp + cash_amount + pass-through + symbol_normalize helper)
- 8c-S: 3 days (PlaceOrder/CancelOrder/ModifyOrder/SearchContracts/OrderEvent flips + dual-stream fan-in + tests + Alembic 0019 + nightly extension)
- 8c-C: 4 days (crypto path + empirical script + Alembic 0020 + real-broker workflow + crypto bypass contract)
- 8c-B: 3 days (equity bracket adapter + crypto bracket empirical + Alembic 0021 per-asset-class flips)
- 8c-OCO: 3 days (per-asset-class Alpaca dispatch in orchestrator + both OCO empirical branches + Alembic 0022)
- Architect-review + spec polish: 1 day
- Plan-writing + reviewer chains: 1 day
- Buffer: 2 days

**Total: ~20 working days.** Targets `v0.10.0` release tag at 8c close. After 8c, all 4 brokers in capability matrix have parity for STOCK + CRYPTO (where applicable); platform is ready for Phase 9 charting.

---

## Architect-Review Applied

21 findings applied inline (3 CRIT + 7 HIGH + 11 MED). 5 LOWs deferred — see footer.

---

## Deferred LOWs

- **LOW-1:** Reference a canonical proto tag map (listing all tag numbers across phases) — defer to a standalone `docs/proto-tag-map.md` created at Phase 8c close.
- **LOW-2:** 17d → 20d estimate buffer — **applied** (updated estimate to 20 days including 2-day buffer; applied despite LOW classification as it is a factual correction with zero risk).
- **LOW-3:** Crypto quote vs trade cap distinction — the 30-symbol cap is for streaming quotes only; trade write-path has no symbol cap. Document more explicitly in `phase7c_alpaca_topology.md` at 8c close.
- **LOW-4:** OPG TIF (opening-session `OPG`) handling — Alpaca's OPG doesn't map cleanly to MOO/LOO. Rejected by capability gate (`is_supported=FALSE` for all `(alpaca, STOCK, *, OPG)` rows). Document in `notes` column of those rows.
- **LOW-5:** Pin F8c FE chunk to `v0.10.0` close — confirm at plan-writing stage; add to `TASKS.md` under v0.10.0 milestone.
