# Phase 8c Design — Alpaca Trade (US Equity + Crypto)

**Status:** Draft. Brainstorm 2026-05-06. Pending architect-review → user approval → plan.

**Predecessor:** Phase 8b (`v0.9.0`, target 2026-mid-May) brings full order-type expansion across IBKR/Futu/Schwab + Modify/Bracket/OCO. Phase 7c (`v0.7.3`, 2026-05-05) already shipped the `sidecar_alpaca` read-only adapter with per-mode Configure routing (live + paper sidecars) + 30-symbol streaming cap.

**Goal:** Bring Alpaca's trade write-path live across **US equity + crypto** with full RPC parity vs the other 3 brokers (Place/Cancel/Modify/Bracket/OCO + OrderEvent + SearchContracts). First broker in the system with a non-equity asset class — adds the asset-class dimension to the capability matrix.

---

## Brainstorm decisions (2026-05-06)

| Q | Topic | Pick | What it means |
|---|---|---|---|
| 1 | Equity vs crypto unification + matrix shape | **B** | Add `asset_class` to `broker_order_capability` PK → 4-tuple `(broker_id, asset_class, order_type, tif)`. Migration extends PK + reseeds (~800 rows). FE hook accepts optional `assetClass` param. |
| 2 | Crypto wire conventions | **A + b** | Symbol format = slashed `BTC/USD` (Alpaca native, future-proof for Coinbase). Qty regex bumped from 8dp to **10dp** to fit Alpaca crypto pairs. |
| 3 | Empirical hard-gates | **C** | New empirical scripts for crypto + bracket (both net-new code paths). Equity rides the existing `nightly-real-alpaca.yml` (extended with trade cases). |
| 4 | Notional orders ("buy $100 of AAPL") | **B** | Add optional `notional: str` field to proto/schema. `broker_features.notional_orders` capability row per (broker, asset_class). Alpaca = TRUE; others = FALSE. |
| 5 | RPC coverage scope | **D** | Full coverage — Place + Cancel + Modify + Bracket + OCO + OrderEvent + SearchContracts. Symmetric with 8a/8b. |

---

## Sequencing — per-asset-class

```
8c-0   Foundation: 4-tuple matrix migration + asset_class enum widening + 10dp regex + notional field + Alpaca proto pass-through
8c-S   Stocks: Alpaca equity write-path (Place/Cancel/Modify/Bracket/OCO/OrderEvent/SearchContracts via /v2/orders)
8c-C   Crypto: Alpaca crypto write-path (same RPCs via /v1beta3/crypto/{loc}/orders) + empirical script + capability flip
8c-B   Bracket empirical hard-gate (separate empirical script — Q3 C)
8c-OCO OCO Alpaca-native dispatch (extends 8b orchestrator with Alpaca branch)
8c-close  CHANGELOG + tag v0.10.0
```

Per-chunk PR style: single-shot per asset class. Net new empirical scripts: **3** (Alpaca crypto, Alpaca bracket, Alpaca OCO). All run against Alpaca paper (free-money sandbox).

---

## Section 1 — 4-tuple capability matrix (8c-0 foundation)

**Schema migration (Alembic 0018)**: extend `broker_order_capability` PK from 3-tuple to 4-tuple by adding `asset_class VARCHAR NOT NULL`.

```sql
-- 0018_phase8c_capability_4tuple.py upgrade()
ALTER TABLE broker_order_capability ADD COLUMN asset_class VARCHAR;
UPDATE broker_order_capability SET asset_class = 'STOCK' WHERE asset_class IS NULL;
ALTER TABLE broker_order_capability ALTER COLUMN asset_class SET NOT NULL;
ALTER TABLE broker_order_capability ADD CONSTRAINT broker_order_capability_asset_class_check
    CHECK (asset_class IN ('STOCK','CRYPTO','OPTION','FUTURE','FOREX','BOND'));
ALTER TABLE broker_order_capability DROP CONSTRAINT broker_order_capability_pkey;
ALTER TABLE broker_order_capability ADD PRIMARY KEY (broker_id, asset_class, order_type, tif);
```

**Reseed plan**: existing 200 rows (4 brokers × 10 types × 5 TIFs, all `asset_class='STOCK'` post-backfill) get extended:

- Add `(alpaca, CRYPTO, *, *)` rows = 50. Initial state: only `(MARKET|LIMIT, GTC|IOC|FOK)` = 6 supported. Rest `is_supported=FALSE` with notes (e.g. `notes="Crypto: 24/7 — DAY meaningless"`).
- Add `(schwab, CRYPTO, *, *)` rows = 50, all `is_supported=FALSE` (Schwab doesn't support crypto trade today).
- Add `(ibkr, CRYPTO, *, *)` = 50 placeholder (IBKR Paxos crypto comes in Phase 15).
- Add `(futu, CRYPTO, *, *)` = 50 placeholder.
- (OPTION/FUTURE/FOREX/BOND rows deferred to Phase 12+.)

Final matrix size: **400 rows** (4 brokers × 2 asset classes × 10 types × 5 TIFs).

**Backend changes**:

- `OrderCapabilityService` adds `asset_class` to its cache key + lookup signature.
- `GET /api/brokers/{id}/capabilities?asset_class=CRYPTO` accepts optional query param; defaults to STOCK for backward compat.
- `POST /api/admin/order-capabilities` body widens to include `asset_class`; existing rows continue to validate as STOCK.

**FE changes** (deferred to a follow-on Chunk F8c):

- `useBrokerCapabilities(brokerId, assetClass?)` adds optional second arg.
- `TradeTicketModal` passes `Contract.asset_class` to the hook.

---

## Section 2 — Crypto wire conventions (8c-0 + 8c-C)

### Symbol format (Q2-A)

Crypto pairs use **slashed format** verbatim across the wire: `BTC/USD`, `ETH/USD`, `SHIB/USD`. The `Contract.symbol` field carries it as opaque string. Adapter passes verbatim to Alpaca.

### Quantity precision (Q2-b)

Schema regex updated:
- Before: `^\d+(\.\d{1,8})?$` (8dp)
- After: `^\d+(\.\d{1,10})?$` (10dp)

Applies to `qty`, `limit_price`, `stop_price`, `trail_offset`, `trail_limit_offset`, `notional`. Existing equity orders (max 8dp in practice) unaffected.

### Notional orders (Q4-B)

New optional field `notional: str | None` on `PreviewRequest`/`PlaceOrderRequest`/`OrderModifyRequest`:

- Same regex pattern as `qty` (`^\d+(\.\d{1,10})?$`).
- Validator enforces: **exactly one of `qty` or `notional` is set** (XOR). Both = 422; neither = 422.
- Adapter for Alpaca: if `notional` set → POST body has `notional: "100"`; else `qty: "5.5"`.
- Adapter for Schwab/IBKR/Futu: if `notional` set → schema-level reject via capability gate (`broker_features.notional_orders=FALSE`).

Proto field tag 15:

```proto
// Phase 8c reserved tag 15
string notional = 15;  // Decimal-as-string; XOR with qty
```

---

## Section 3 — 8c-S Alpaca equity write-path

**Capability flip (Alembic 0019)**: flip Alpaca STOCK rows.

Initial supported: 16 combos = MARKET/LIMIT/STOP/STOP_LIMIT × DAY/GTC/IOC/FOK. Session-bound types DAY-only per HIGH-1 from 8b.

**Adapter changes** (`sidecar_alpaca/handlers.py` + `sidecar_alpaca/normalize.py`):

- Flip `PlaceOrder` from UNIMPLEMENTED to live. `alpaca-py`'s `TradingClient.submit_order()`.
- Flip `CancelOrder` to live. `TradingClient.cancel_order_by_id(order_id)`.
- Flip `ModifyOrder` to live (Q5-D). `TradingClient.replace_order_by_id(order_id, **kwargs)`.
- Flip `SearchContracts` to live. `TradingClient.get_assets()` filtered by symbol prefix.
- `OrderEvent` server-streaming — extend the existing Phase 7b/c `sidecar_alpaca` quote streamer pattern to also subscribe to `TradingStream` events.

**Per-mode routing**: Phase 7c established `alpaca-live` and `alpaca-paper` as separate sidecar containers with per-mode Configure (`phase7c_alpaca_topology.md`). Trade write-path uses the SAME pattern.

**Validation strategy (Q3-C)**: no equity-specific empirical script. Existing `nightly-real-alpaca.yml` extended with parametrized cases:
- LIMIT BUY 1 SPY at $1 (cancel immediately).
- MOC BUY 1 SPY (cancel before cutoff).
- TRAIL BUY 1 SPY by 0.5%.

**Tests**:
- `sidecar_alpaca/tests/test_handlers_place_order.py` — type-by-type payload assertions.
- `backend/tests/integration/test_alembic_0019.py` — post-flip count.

---

## Section 4 — 8c-C Alpaca crypto write-path

**Capability flip (Alembic 0020)**: flip Alpaca CRYPTO rows. Initial supported: 6 combos (per Section 1).

**Adapter changes**:

- `sidecar_alpaca/handlers.py::PlaceOrder` discriminates on `Contract.asset_class`:
  - `STOCK` → `TradingClient.submit_order()` against `/v2/orders`.
  - `CRYPTO` → crypto-specific REST call against `/v1beta3/crypto/{location}/orders`. `alpaca-py` exposes `CryptoTradingClient`.
- `Contract.symbol` carries `BTC/USD`-style slashed format verbatim.
- Notional support: if `request.notional` set, payload has `notional: ...`; else `qty: ...`.

**`location` parameter for crypto**: Alpaca crypto endpoint requires a `loc` URL segment (e.g. `us`). Read from `app_config.broker.alpaca.crypto_location` (default `us`).

**Validation strategy (Q3-C — NEW empirical script)**:

`scripts/empirical/alpaca_crypto_paper.py` — 5 assertions:
1. POST /v1beta3/crypto/us/orders returns 201 with body containing `id`.
2. Response `id` matches order id polled back via GET `/v2/orders/{id}`.
3. `client_order_id` round-trips on subsequent GET (Alpaca DOES support clientOrderId; verify empirically).
4. Cancel returns 204; subsequent poll shows `status: "canceled"` within 3s.
5. `BUY 0.0001 BTC/USD LIMIT $10` payload accepted (validates 10dp qty regex round-trip).

Hard-gates the 0020 flip.

**Tests**:
- `sidecar_alpaca/tests/test_handlers_place_order_crypto.py`.
- `backend/tests/integration/test_alembic_0020.py`.
- `backend/tests/real_broker/test_real_alpaca_crypto_e2e.py` (marker `real_alpaca`).

---

## Section 5 — 8c-B Alpaca bracket

Per Q3-C, bracket gets its own empirical hard-gate.

**Adapter changes**: `sidecar_alpaca/handlers.py::PlaceBracket` flips. Alpaca's `order_class="bracket"` accepts `take_profit{limit_price}` + `stop_loss{stop_price, limit_price}` in a SINGLE atomic POST. Adapter consolidates the proto's parent + 2 children.

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

**Validation (Q3-C empirical script)**: `scripts/empirical/alpaca_bracket_paper.py` — 4 assertions:
1. POST returns 201 with parent + child IDs (Alpaca returns parent body with `legs` array).
2. Parent + 2 child IDs all distinct, all queryable via GET /v2/orders/{id}.
3. Cancel parent → both children's status reads `canceled` within 5s on TradingStream.
4. No partial cascade.

**Capability flip**: `broker_features.bracket` for alpaca FALSE → TRUE post-empirical-PASS (Alembic 0021).

---

## Section 6 — 8c-OCO Alpaca OCO dispatch

Per Q5-D, Alpaca OCO is **broker-native** via `order_class="oco"`. The 8b OCO orchestrator already has the multi-broker dispatch pattern; 8c adds Alpaca as a **native dispatch case** (same family as Schwab + IBKR — atomic at broker, no orchestrator watcher needed).

**Adapter changes**: New helper in `oco_orchestrator.py` per spec 8b §6:

```python
def dispatch_oco_alpaca(legs: list[OrderRequest]) -> str:
    body = to_alpaca_order_payload(legs[0])
    body["order_class"] = "oco"
    body["legs"] = [to_alpaca_order_payload(leg) for leg in legs[1:]]
    # POST /v2/orders, atomic
    ...
```

**State machine**: reuses 8b's 9-state machine. Alpaca's atomic OCO means same race window as Schwab/IBKR (none broker-side).

**Validation (Q3-C empirical script #3)**: `scripts/empirical/alpaca_oco_paper.py` — 3 assertions:
1. POST returns 201 with both leg IDs in `legs` array.
2. Both legs queryable; share `order_class="oco"` flag.
3. Cancel parent group → both legs `canceled`.

**Capability flip**: `broker_features.oco` for alpaca = TRUE post-empirical (Alembic 0022).

---

## Section 7 — Cross-cutting

### `Contract.asset_class` enum widening

Phase 8a A1 proto already includes `AssetClass` enum with `STOCK`, `ETF`, `OPTION`, `FUTURE`, `FOREX`, `BOND`, **`CRYPTO`**. No proto change needed.

Backend `app/brokers/base.py::AssetClass` Literal already covers CRYPTO. No change.

### `notional` field plumbing (8c-0 chunk)

Proto field 15 + Pydantic field + adapter pass-through. Same pattern as the 8b TRAIL fields. Tests assert XOR with `qty`.

### 30-symbol streaming cap (Phase 7c carryover)

Cap applies to **streaming quotes**, NOT trade write-path. Trade is per-symbol on demand. No new constraint.

### Empirical artifact PII redaction

Same MED-5 pattern from 8b — pre-commit hook `scripts/pre-commit-check-empirical-artifacts.sh` already enforces. Alpaca paper account numbers like `PA-XXXXXX` added to the redact regex.

### Alembic migration plan

- 0018 — extend `broker_order_capability` PK with asset_class + backfill + reseed for new asset class rows (8c-0)
- 0019 — Alpaca STOCK partial flip (8c-S close)
- 0020 — Alpaca CRYPTO partial flip (8c-C close, gated on crypto empirical)
- 0021 — `broker_features.bracket` for alpaca = TRUE (8c-B close, gated on bracket empirical)
- 0022 — `broker_features.oco` for alpaca = TRUE (8c-OCO close, gated on OCO empirical)

---

## Out of scope (Phase 8c)

- **Crypto on other brokers**: Schwab/IBKR/Futu CRYPTO rows ship as all-unsupported placeholders. IBKR Paxos crypto comes in Phase 15.
- **Options trading**: `Contract.asset_class=OPTION` rows for any broker. Phase 12.
- **Notional for non-Alpaca brokers**: Phase 9+.
- **Crypto staking / yield**: Phase 16+.
- **Alpaca extended-hours sessions** (`extended_hours: true` on POST /v2/orders): defer.
- **Alpaca's OPG (opening) TIF**: doesn't map cleanly to MOO/LOO. Defer.

---

## Risks (in order of priority)

1. **HIGH — `alpaca-py` crypto SDK coverage**: the SDK has separate `CryptoTradingClient` vs `TradingClient`. Confirm crypto place/cancel/modify all expose async equivalents. Spike script in 8c-0 imports both and asserts presence of the 5 RPC methods.
2. **HIGH — Alpaca crypto-specific clientOrderId behavior**: Schwab rejected clientOrderId (Phase 8a empirical finding); Alpaca *says* it supports it. Empirical script #1 assertion 3 confirms.
3. **MEDIUM — 4-tuple matrix migration cost**: 200 → 400 rows in 0018; reseed must be idempotent + backward-compat.
4. **MEDIUM — Alpaca `loc` URL parameter for crypto**: hardcoded to `us` initially; configurable via `app_config.broker.alpaca.crypto_location`.
5. **LOW — Symbol format consistency**: Phase 7b.1 streaming quotes might already use a different format for Alpaca crypto; if so, adapter must convert to slashed at the boundary.
6. **LOW — TradingStream subscription scaling**: cap at 5 concurrent streams.

---

## Estimate

- 8c-0: 3 days (4-tuple migration + reseed + asset_class extension + 10dp regex + notional field + pass-through)
- 8c-S: 3 days (PlaceOrder/CancelOrder/ModifyOrder/SearchContracts/OrderEvent flips + tests + Alembic 0019 + nightly extension)
- 8c-C: 4 days (crypto path + empirical script + Alembic 0020 + real-broker workflow)
- 8c-B: 2 days (bracket adapter + empirical script + Alembic 0021)
- 8c-OCO: 3 days (Alpaca dispatch in orchestrator + empirical script + Alembic 0022)
- Architect-review + spec polish: 1 day
- Plan-writing + reviewer chains: 1 day

**Total: ~17 working days.** Targets `v0.10.0` release tag at 8c close. After 8c, all 4 brokers in capability matrix have parity for STOCK + CRYPTO (where applicable); platform is ready for Phase 9 charting.
