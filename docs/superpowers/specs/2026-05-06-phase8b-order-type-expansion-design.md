# Phase 8b Design — Order-Type Expansion + Futu Modify/Bracket

**Status:** Draft. Brainstorm 2026-05-06. Pending user review → architect-review → plan.

**Predecessor:** Phase 8a (`v0.8.0-rc1`, tag `2026-05-06`) shipped capability foundation + Schwab single-leg trade write-path. Schwab is now `is_supported=TRUE` for 16 (type, TIF) combos via Alembic 0011a (commit `fadd92b`).

**Goal:** Expand the trade write-path to cover the full Phase 8 type/TIF universe across all 3 brokers (IBKR + Futu + Schwab), pick up Phase 6's deferred Futu Modify + Bracket, and ship cross-broker OCO non-bracket.

---

## Sequencing — per-broker (Option B from brainstorm)

```
8b-0  Schema widening               (cross-cutting; foundation)
8b-S  Schwab full universe          (highest momentum; just shipped 8a)
8b-F  Futu full universe + Modify + Bracket  (Phase 6 deferred work absorbed here)
8b-I  IBKR full universe            (most mature adapter; least urgent)
8b-OCO  OCO non-bracket             (cross-broker; lands last after all 3 brokers solid)
```

Per-chunk PR style: single-shot per broker. Net new empirical hard-gate scripts: **3** (Futu Bracket+Modify, Schwab OCO native, Futu OCO orchestrated).

---

## Section 1 — Schema widening (8b-0)

**Reject layering** (per Q1: option C).

- **Pydantic schema** (`backend/app/schemas/orders.py`) widens `PreviewRequest` / `PlaceOrderRequest` / `OrderModifyRequest` Literals to the full universe:
  - `order_type: Literal["MARKET","LIMIT","STOP","STOP_LIMIT","TRAIL","TRAIL_LIMIT","MOC","MOO","LOC","LOO"]` (10 values; UNSPECIFIED stripped).
  - `tif: Literal["DAY","GTC","IOC","FOK","GTD"]` (5 values; UNSPECIFIED stripped).
- Schema layer rejects malformed (typos, wrong shape) → HTTP 422 with Pydantic's standard error format.
- Capability gate (`orders_service`) rejects valid-but-unsupported-for-broker → HTTP 422 with `error.code="unsupported_order_type_for_broker"` and `(broker, order_type, tif, notes)` detail.

**Side-effect requirements**

- `_check_order_type_prices` `@model_validator` extends to enforce price/stop semantics for the new types:
  - `STOP_LIMIT` → both `stop_price` and `limit_price` required.
  - `TRAIL` → `trail_offset` + `trail_offset_type` required; `limit_price` and `stop_price` must be empty.
  - `TRAIL_LIMIT` → `trail_offset` + `trail_offset_type` + `trail_limit_offset` required.
  - `MOC` / `MOO` → no price fields (market-on-close/open variants).
  - `LOC` / `LOO` → require `limit_price`.
- New Pydantic field validators on `trail_offset` (decimal-as-string), `expiry_date` (ISO date `YYYY-MM-DD`).

**Cross-cutting GTD validation** (per Q3: option A).

- `tif == "GTD"` → `expiry_date` required, parseable ISO date, `today() <= expiry_date <= today() + 90d`.
- `tif != "GTD"` → `expiry_date` must be empty.
- Backend uses `exchange_calendars` library to compute EOD per `Contract.exchange` (NYSE 16:00 ET → 21:00 UTC EST / 20:00 UTC EDT; HKEX 16:00 HKT = 08:00 UTC; LSE 16:30 GMT/BST). Holidays + DST baked in. Adapters convert `(expiry_date, exchange)` → broker-native datetime at the wire boundary; never the FE.

**Proto changes** (additive — no breaking changes):

```proto
// New fields on OrderRequest, PlaceOrderRequest, ModifyOrderRequest, Order:
string trail_offset = 11;        // Decimal-as-string e.g. "0.50" or "5.0"
string trail_offset_type = 12;   // "AMOUNT" | "PERCENT"
string trail_limit_offset = 13;  // Decimal — TRAIL_LIMIT only
string expiry_date = 14;         // ISO "YYYY-MM-DD" — GTD only
```

**Tests** (new):

- `backend/tests/unit/test_orders_schema_8b.py` — widened Literals + price-rule matrix for the 10 types × validation outcomes.
- `backend/tests/unit/test_orders_schema_gtd.py` — GTD expiry edge cases (today, +90d boundary, beyond, missing, empty when not GTD).

---

## Section 2 — TRAIL parameter wire surface (per Q2: option C)

`trail_offset` + `trail_offset_type` discriminator. Adapters map verbatim:

| Broker | `AMOUNT` mapping | `PERCENT` mapping |
|---|---|---|
| Schwab | `stopPriceOffset: <amount>` + `stopPriceLinkType: "VALUE"` | `stopPriceOffset: <pct>` + `stopPriceLinkType: "PERCENT"` |
| IBKR (`ib_async`) | `Order.auxPrice = <amount>` | `Order.trailingPercent = <pct>`, `Order.trailStopPrice = None` |
| Futu | `aux_price = <amount>` | `trail_value = <pct>`, `trail_type = "RATIO"` |

`TICKS` deferred to Phase 14 (futures).

`TRAIL_LIMIT` adds `trail_limit_offset` (additional offset from trigger to limit). Schwab's `priceLinkType="VALUE"` + `priceOffset=<trail_limit_offset>`. IBKR's `Order.lmtPriceOffset`. Futu — TBD per SDK; default to absolute offset.

---

## Section 3 — 8b-S Schwab full universe

**Capability flip (Alembic 0011b):** flip Schwab's remaining 34 rows in `broker_order_capability` to `is_supported=TRUE` (TRAIL, TRAIL_LIMIT, MOC, MOO, LOC, LOO across all TIFs + GTD combos for all types). Total Schwab supported after flip: 50/50.

**Adapter changes:**

- `sidecar_schwab/normalize.py::to_schwab_order_payload` extends to handle the 6 new order types:
  - `TRAIL` / `TRAIL_LIMIT` — populate `stopPriceLinkType` + `stopPriceOffset` per the table in §2.
  - `MOC` / `MOO` / `LOC` / `LOO` — Schwab uses `orderType: "MARKET_ON_CLOSE"` / `"MARKET_ON_OPEN"` / `"LIMIT_ON_CLOSE"` / `"LIMIT_ON_OPEN"`; mapping is rename + price-required-for-LOC/LOO.
  - GTD — populate `goodTillDate` per the EOD calendar logic in §1.
- `sidecar_schwab/handlers.py` no changes (the dispatch is generic).

**Validation strategy (per Q5: option D):**

- No new empirical script. Existing C0 validated the place/cancel round-trip; type-specific validation lives in unit tests for `to_schwab_order_payload` extensions.
- The existing `nightly-real-schwab-trade.yml` workflow runs `tests/real_broker/test_real_schwab_e2e_place_cancel.py` daily. Add 2 parametrized cases: TRAIL (BUY 1 F TRAIL by $0.10) and GTD-LIMIT (BUY 1 F LIMIT $1 expiry+1d). Both immediate-cancel.

**Tests:**

- `sidecar_schwab/tests/test_normalize_orders.py` — extend with payload assertions for each new order type.
- `backend/tests/integration/test_alembic_0011b.py` — verifies post-flip count = 50.

---

## Section 4 — 8b-F Futu full universe + Modify + Bracket

**The biggest chunk.** Pulls in Phase 6's deferred Futu Modify + Bracket alongside the order-type expansion.

**Adapter changes:**

- `sidecar_futu/handlers.py::ModifyOrder` — flip from UNIMPLEMENTED to live. Uses `futu-api`'s `OpenSecTradeContext.modify_order`; same payload-translation pattern as Schwab's `_configure_schwab` flow. Per-mode (HK paper / HK live) routing already exists from Phase 6.
- `sidecar_futu/handlers.py::PlaceBracket` — flip from UNIMPLEMENTED. `futu-api` exposes attached orders via the `aux_price` + `trail_value` parameters on `place_order`; we wrap parent + 2 children into one `place_order` call with `attached_conditional_orders`.
- `sidecar_futu/normalize.py` — extend payload builder for TRAIL / MOC / etc. Per memory `reference_futu_api_docs.md`, consult Futu docs for HK session-bound order types (HKEX has different session boundaries than NYSE).

**Capability flip (Alembic 0011c):**

- Flip Futu's currently-supported 4 rows + add Modify + Bracket support (separate `broker_order_features` flip — TBD whether we add a new column or use a `notes`-keyed feature flag).
- Final Futu supported set: TBD per `futu-api` capabilities — Futu HK doesn't support all 10 types; e.g., `MOO` / `LOO` are NYSE concepts.
- The capability matrix accommodates this: rows can stay `is_supported=FALSE` with `notes="Not supported on HKEX"`, the FE's `notesFor()` from F1 already renders this.

**Validation strategy (per Q5: option D):**

- **NEW empirical script**: `scripts/empirical/futu_bracket_modify_paper.py`. Place a Futu HK paper-account LIMIT order on `HK.00700` (Tencent) at $1 below market, modify the price, then cancel. Place a bracket on the same symbol with stop-loss + take-profit; cancel parent (verify both children cancel via OCA cascade).
- 4 assertions: ModifyOrder returns new broker_order_id, parent_broker_order_id link present, Bracket parent + 2 children IDs returned, OCA cascade observed via OrderEvent stream.
- Hard-gates the 0011c flip (same pattern as Schwab C0 → 0011a).

**Tests:**

- `sidecar_futu/tests/test_handlers_modify.py` (new) + `test_handlers_bracket.py` (new).
- `backend/tests/real_broker/test_real_futu_e2e_modify.py` (new, marker `real_futu`).

---

## Section 5 — 8b-I IBKR full universe

**Lightest touch.** `ib_async` natively supports every type/TIF in the Phase 8b universe. Adapter changes are mostly proto-to-`ib_async.Order` field mapping.

**Adapter changes:**

- `sidecar_ibkr/handlers.py::PlaceOrder` — extend the `Order(...)` construction to set `orderType`, `auxPrice`, `trailingPercent`, `lmtPriceOffset`, `goodTillDate`, etc. per the new request fields.
- For session-bound types: `ib_async` accepts `orderType="MOC"` etc. directly; passes through to TWS API.
- GTD: `Order.tif="GTD"` + `Order.goodTillDate=<YYYYMMDD HH:MM:SS US/Eastern>` per market-calendar rules in §1.

**Capability flip (Alembic 0011d):** flip remaining IBKR rows. Currently 16 supported (MARKET/LIMIT/STOP/STOP_LIMIT × DAY/GTC/IOC/FOK); after flip: 50/50.

**Validation strategy (per Q5: option D):**

- No new empirical script. Adapter is mature; integration tests give high coverage.
- Existing `nightly-real-ibkr.yml` runs full E2E nightly. Add parametrized cases: TRAIL (BUY 1 SPY TRAIL by 0.5% via paper), MOC (BUY 1 SPY MOC market-close), GTD-LIMIT (BUY 1 SPY LIMIT $1 expiry+1d). All cancel-immediate.

**Tests:**

- `sidecar_ibkr/tests/test_handlers_place_extended.py` (new) — type-by-type payload assertions.
- `backend/tests/integration/test_alembic_0011d.py` — post-flip count check.

---

## Section 6 — 8b-OCO non-bracket (per Q4: option B)

**OCO = "One-Cancels-Other": 2 linked orders; when one fills, the other auto-cancels.**

**Architecture:**

- Backend-side: new `oco_links` table (Alembic 0011e):

```sql
CREATE TABLE oco_links (
  oco_group_id  UUID PRIMARY KEY,
  account_id    UUID NOT NULL REFERENCES broker_accounts(id),
  order_id_a    UUID NOT NULL,                   -- our local order id (orders.id)
  order_id_b    UUID NOT NULL,
  state         VARCHAR NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending','one_filled','both_done','manually_cancelled')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at   TIMESTAMPTZ
);
CREATE INDEX oco_links_state_idx ON oco_links(state) WHERE state='pending';
```

- New API endpoint: `POST /api/orders/oco` taking 2 `OrderRequest`s + a `nonce`. Returns `{oco_group_id, order_id_a, order_id_b}`.
- New service: `app/services/oco_orchestrator.py` — subscribes to `OrderEvent` stream; when a `pending` OCO group's leg fills (or partial-fills > threshold), cancels the survivor.
- Threshold: any fill quantity (full or partial) on one leg cancels the other. Phase 8b doesn't try to implement OCO with quantity slicing.

**Per-broker dispatch:**

- **Schwab adapter**: bundles into single `place_order` with `complexOrderStrategyType="OCO"` and 2 entries in `orderLegCollection`. Atomic at broker. `oco_links` row still written for audit + UI display.
- **IBKR adapter**: assigns shared `Order.ocaGroup=<oco_group_id>` UUID, `Order.ocaType=1` (cancel-on-fill semantics), submits both via `placeOrder` separately. Atomic per `ocaGroup`.
- **Futu adapter**: places both as independent orders, registers `oco_link` row. The `oco_orchestrator` watcher service handles cancel-on-fill.

**Race window:** Schwab + IBKR are atomic broker-side (no race). Futu has ~1-2s race window from the poller cadence; OCO orchestrator's watcher latency is bounded by `OrderPoller`'s 2s fast-cadence tick.

**Validation strategy (per Q5: option D):**

- **NEW empirical script #1**: `scripts/empirical/schwab_oco_paper.py` — place an OCO pair (BUY LIMIT $1 + SELL LIMIT $999); both orders should appear linked in the Schwab order list with `complexOrderStrategyType="OCO"`. Cancel the parent group.
- **NEW empirical script #2**: `scripts/empirical/futu_oco_orchestrated_paper.py` — place an OCO pair via the backend API; verify both order rows have a shared `oco_group_id` in the `oco_links` table; manually cancel one and verify the other gets auto-cancelled within 5s.
- IBKR OCA tested via integration test using the existing fake servicer.

**Tests:**

- `backend/tests/integration/test_oco_orchestrator.py` (new) — service-level race + threshold.
- `backend/tests/integration/test_alembic_0011e.py` (new) — table shape + CHECK constraint.

---

## Section 7 — Cross-cutting concerns

### Market calendar dependency

- New backend dep: `exchange_calendars` (or `pandas_market_calendars`). Pinned in `backend/pyproject.toml`.
- New module: `backend/app/services/market_calendar.py` — exposes `eod_for_exchange(exchange: str, expiry_date: date) -> datetime` and `is_trading_day(exchange: str, d: date) -> bool`. Used by GTD validators + adapter wire-time conversion.
- Tests: explicit cases for NYSE EDT/EST DST boundary, HKEX no-DST, LSE BST, and US holidays (Thanksgiving, July 4) in `tests/unit/test_market_calendar.py`.

### Capability matrix per-broker quirks

- The `broker_order_capability` `notes` column holds short human-readable strings rendered by the FE's `notesFor()` from F1 (e.g., `"Not supported on HKEX"`, `"Coming in Phase 8b"` already used in 8a, etc.).
- 8b-F's flip is partial: rows for `MOO` / `LOO` / `LOC` stay `is_supported=FALSE` with notes since HKEX has different session-bound semantics.

### Alembic migration plan

- 0011b — Schwab flip 34 rows (8b-S close)
- 0011c — Futu partial flip + (TBD: add `broker_order_features` table or column for Modify+Bracket capability) (8b-F close)
- 0011d — IBKR flip 34 rows (8b-I close)
- 0011e — `oco_links` table (8b-OCO open)

### Empirical script artifacts

3 new scripts in `scripts/empirical/`. Each writes a JSON artifact to `scripts/empirical/artifacts/`. Same redaction rules as C0 (no accountNumber, no real fills > 1 share). Pre-commit hook checks artifacts for accidental token/PII.

---

## Out of scope (Phase 8b)

- **Multi-leg combos**: spreads, straddles, butterflies. Phase 13.
- **Algos**: TWAP, VWAP, Adaptive, Iceberg. Phase 17.
- **Options-specific order types**: exercise, assign, complex options strategies. Phase 12.
- **Conditional orders**: trigger-on-other-symbol-price etc. Future Phase TBD.
- **Quantity slicing on OCO**: a 100-share OCO leg partial-fills 30 shares; we currently cancel the other leg in full. Phase 9 might handle re-quoting the survivor for the remaining 70.
- **Extended-hours session GTD** (e.g., expire at 18:00 ET on date X). Phase 8b uses session close only.

---

## Risks (in order of priority)

1. **HIGH — `exchange_calendars` upstream coverage for HKEX.** Need to confirm before committing 8b-F. Fallback: hand-roll a small HK calendar in `services/market_calendar.py`.
2. **HIGH — Futu Bracket attached-order semantics.** Phase 6 deferred this for a reason. The empirical script is the gate; if it fails, we punt to Phase 9.
3. **MEDIUM — IBKR `ocaGroup` UUID length limit.** TWS truncates to 32 chars; UUIDs are 36. Adapter must hash/truncate consistently.
4. **MEDIUM — Schwab native OCO error-code stability.** Schwab's `complexOrderStrategyType="OCO"` is documented but rarely used by retail; rejection codes might surprise. Mitigated by the empirical script.
5. **LOW — Capability-cache invalidation lag** when 0011b/c/d flip. The 60s LRU + Redis pubsub bust handles it; backend bounce on each migration is the conservative fallback (per the post-deploy memory note).

---

## Estimate

- 8b-0: 1 day (schema widening + tests)
- 8b-S: 2 days (extend normalize + tests + nightly add)
- 8b-F: 5 days (Modify + Bracket + type expansion + empirical script)
- 8b-I: 2 days (extend + tests + nightly add)
- 8b-OCO: 4 days (orchestrator + 2 empirical scripts + table)
- Architect review + spec polish: 1 day

**Total: ~15 working days.** Targeting `v0.9.0` release tag at 8b close.
