# Phase 8b Design — Order-Type Expansion + Futu Modify/Bracket

**Status:** Draft + architect-reviewed (2026-05-06). 3 CRIT + 6 HIGH + 8 MED applied inline. 5 LOWs deferred to plan-time.

**Predecessor:** Phase 8a (`v0.8.0`, tag `2026-05-06`, commit `f8b8d7d`) shipped capability foundation + Schwab single-leg trade write-path. Schwab is now `is_supported=TRUE` for 16 (type, TIF) combos via Alembic 0011a (commit `fadd92b`).

**Goal:** Expand the trade write-path to cover the full Phase 8 type/TIF universe across all 3 brokers (IBKR + Futu + Schwab), pick up Phase 6's deferred Futu Modify + Bracket, and ship cross-broker OCO non-bracket.

## Architect-review findings applied inline

| Severity | ID | Topic | Resolution |
|---|---|---|---|
| CRIT | CRIT-1 | OCO state machine missing failure-mode states | §6 expanded to 9-state machine + transition table |
| CRIT | CRIT-2 | OCO orchestrator deployment topology undefined | §6 specifies single-instance via Redis advisory lock + hydrate-on-startup |
| CRIT | CRIT-3 | GTD `today()` is server-UTC, ignores exchange TZ | §1 validator uses `today_in_exchange_tz(exchange)` |
| HIGH | HIGH-1 | LOC/LOO/MOC/MOO non-DAY combos nonsensical | §1 + flips only DAY rows; invalid combos `notes="Session-bound; DAY only"` |
| HIGH | HIGH-2 | Session-bound submission window not enforced | §1 adds `session_window_closed` validator |
| HIGH | HIGH-3 | IBKR `ocaGroup` 32-char truncation unspecified | §6 canonical: `uuid_str.replace("-", "")[:32]` |
| HIGH | HIGH-4 | Futu Bracket "punt to Phase 9" lacks success criteria | §4 defines explicit 6-assertion gate; Modify ships independently |
| HIGH | HIGH-5 | OCO orchestrator subscription pattern undefined | §6 specifies 1 stream per (broker, account), capped at 100 |
| HIGH | HIGH-6 | Race: orchestrator may query stale capability cache | §6 invariant: orchestrator never queries capability for cancel decisions |
| MED | MED-1 | OCO 2-legs-only — N-leg deferred to Phase 9 | §6 documents scope rationale |
| MED | MED-2 | `broker_features` table undecided | §7 specs the table |
| MED | MED-3 | Half-day market closes missing from test plan | §7 adds Black Friday + Christmas Eve cases |
| MED | MED-4 | Schwab native OCO partial-submission cleanup undefined | §6 atomic-or-nothing; row never inserted on non-2xx |
| MED | MED-5 | Empirical PII enforcement is doc, not enforcement | §7 ships pre-commit hook |
| MED | MED-6 | IBKR TRAIL fields may need TWS API version check | §5 unit test mocking constructor |
| MED | MED-7 | Capability-cache lag during migration (pg_notify ≠ Redis) | §7 adds `postgres_listen_bridge.py`, lands in 8b-0 |
| MED | MED-8 | 90-day GTD cap wrong (Futu max is 30d) | §1 cap is 30d with per-broker override via `broker_features.gtd_max_days` |

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

**Cross-cutting GTD validation** (per Q3: option A; CRIT-3 + MED-8 corrected).

- `tif == "GTD"` → `expiry_date` required, parseable ISO date.
- **CRIT-3**: validation is exchange-local, not server-UTC. Must satisfy `today_in_exchange_tz(exchange) <= expiry_date <= today_in_exchange_tz(exchange) + max_days(broker)`. Without this, a user in HK at 09:00 HKT submitting `expiry_date=today_in_HK` rejects under server-UTC if the UTC date hasn't rolled over yet (or vice versa for NY users at 02:00 ET).
- **MED-8**: `max_days(broker)` is per-broker — Schwab 60d (retail), IBKR 90d, Futu 30d. Conservative floor across all 3 = **30d**, not 90d. Override via the new `broker_features.gtd_max_days` int column from §7.
- `tif != "GTD"` → `expiry_date` must be empty.
- Backend uses `exchange_calendars` library to compute EOD per `Contract.exchange` (NYSE 16:00 ET → 21:00 UTC EST / 20:00 UTC EDT; HKEX 16:00 HKT = 08:00 UTC; LSE 16:30 GMT/BST). Holidays + DST + half-days (early-close cases — see MED-3 in §7) baked in. Adapters convert `(expiry_date, exchange)` → broker-native datetime at the wire boundary; never the FE.

**HIGH-1 — session-bound type/TIF validity**: `MOC`/`MOO`/`LOC`/`LOO` are inherently DAY-only (tied to a specific session's open/close). The `_check_order_type_prices` validator rejects `(order_type in {MOC,MOO,LOC,LOO}) AND (tif != "DAY")` at the schema layer with a clean Pydantic 422. Without this, the capability matrix would show all 5 TIFs supported, FE renders them enabled, user picks MOC+GTC, broker rejects with cryptic 400. Per-broker capability flips in §3-§5 only flip the DAY rows for these 4 types; the 16 invalid combos stay `is_supported=FALSE` with `notes="Session-bound; DAY only"`.

**HIGH-2 — session submission window**: MOC must be submitted before exchange's MOC cutoff (NYSE 15:50 ET, IBKR 15:55 ET), MOO before market open. New backend validator in `orders_service` (lives between schema and capability gate per CRIT-3 sequence from Phase 8a) rejects with `error.code="session_window_closed"` and the next-eligible window in the response. Implementation uses the same `exchange_calendars` lib + the `broker_features.session_cutoff_minutes` override field.

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

**Capability flip (Alembic 0011b)** — corrected per HIGH-1:

- Flip TRAIL + TRAIL_LIMIT × {DAY, GTC, IOC, FOK, GTD} = 10 rows.
- Flip {MARKET, LIMIT, STOP, STOP_LIMIT} × GTD = 4 rows (existing types adding GTD support).
- Flip {MOC, MOO, LOC, LOO} × DAY ONLY = 4 rows. The 16 invalid combos (session-bound × non-DAY TIFs) stay `is_supported=FALSE` with `notes="Session-bound; DAY only"` per HIGH-1.
- Total Schwab supported after flip: 16 (already) + 10 + 4 + 4 = **34/50**, not 50. The 16 unsupported are session-bound × non-DAY (intentionally invalid).

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

- **NEW empirical script**: `scripts/empirical/futu_bracket_modify_paper.py`. Place a Futu HK paper-account LIMIT order on `HK.00700` (Tencent) at HK$10 below market, modify the price, then cancel. Place a bracket on the same symbol with stop-loss + take-profit; cancel parent (verify both children cancel via OCA cascade).

**HIGH-4 — explicit success criteria** (mirrors Phase 8a C0's 8-assertion gate):

1. ModifyOrder returns 2xx + new broker_order_id distinct from original.
2. ModifyOrder response carries `parent_broker_order_id == original_id` (HIGH-3 link).
3. Modified order's status reads `MODIFIED` or equivalent on next poll within 3s.
4. PlaceBracket returns 2xx + parent_broker_order_id + stop_loss_broker_order_id + take_profit_broker_order_id (3 distinct IDs).
5. Cancel parent → both children's status reads `CANCELED` within 5s on OrderEvent stream (OCA cascade observed).
6. No partial cascade: if any of 5 fails, the bracket Modify path is considered failed.

**Modify ships independently of Bracket**: Modify is independently testable (assertions 1-3). If Modify passes but Bracket fails (assertions 4-6), 8b-F flips Modify to supported but leaves Bracket `is_supported=FALSE` with `notes="Bracket deferred to Phase 9 — see futu_bracket_modify_paper.py artifact"`. Hard-gates the 0011c flip; partial pass = partial flip.

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

**Capability flip (Alembic 0011d)** — same HIGH-1 correction as Schwab. Currently 16 supported (MARKET/LIMIT/STOP/STOP_LIMIT × DAY/GTC/IOC/FOK); after flip: TRAIL/TRAIL_LIMIT × all TIFs (10) + {existing 4 types} × GTD (4) + {MOC/MOO/LOC/LOO} × DAY (4) = 16 + 18 = **34/50**. Session-bound × non-DAY combos stay unsupported.

**MED-6 — TWS API version guard**: `ib_async`'s TRAIL field semantics depend on TWS version. Older TWS (< 10.20) requires `Order.trailStopPrice` to be set even with `Order.trailingPercent`; newer accepts either alone. Add `sidecar_ibkr/tests/test_handlers_place_extended.py::test_trail_field_combinations_match_pinned_tws_version` that mocks the `ib_async.Order` constructor + asserts the field combination matches what the pinned TWS version expects. Cross-reference `pyproject.toml`'s `ib_async` pin.

**Validation strategy (per Q5: option D):**

- No new empirical script. Adapter is mature; integration tests give high coverage.
- Existing `nightly-real-ibkr.yml` runs full E2E nightly. Add parametrized cases: TRAIL (BUY 1 SPY TRAIL by 0.5% via paper), MOC (BUY 1 SPY MOC market-close), GTD-LIMIT (BUY 1 SPY LIMIT $1 expiry+1d). All cancel-immediate.

**Tests:**

- `sidecar_ibkr/tests/test_handlers_place_extended.py` (new) — type-by-type payload assertions.
- `backend/tests/integration/test_alembic_0011d.py` — post-flip count check.

---

## Section 6 — 8b-OCO non-bracket (per Q4: option B)

**OCO = "One-Cancels-Other": 2 linked orders; when one fills, the other auto-cancels.**

**MED-1 scope note**: Phase 8b ships 2-leg OCO only. Schwab + IBKR both natively support N-leg OCO (use case: pyramid scaling-out with 3 take-profit levels). Deferred to Phase 9 with explicit refactor: replace `(order_id_a, order_id_b)` columns with a `oco_link_legs(oco_group_id, order_id, position)` join table. 2-leg covers ~95% of retail use cases; revisit when N-leg demand surfaces.

**Architecture:**

- Backend-side: new `oco_links` table (Alembic 0011e), with **CRIT-1 expanded state machine**:

```sql
CREATE TABLE oco_links (
  oco_group_id  UUID PRIMARY KEY,
  account_id    UUID NOT NULL REFERENCES broker_accounts(id),
  order_id_a    UUID NOT NULL,                   -- our local order id (orders.id)
  order_id_b    UUID NOT NULL,
  state         VARCHAR NOT NULL DEFAULT 'pending'
                CHECK (state IN (
                    'pending',                   -- both legs live, neither filled
                    'one_filled',                -- one fully filled, survivor cancel pending
                    'one_partial_pending_cancel',-- one partial-filled, survivor cancel issued, awaiting confirmation
                    'both_done',                 -- terminal: one filled + survivor cancelled successfully
                    'both_filled_race',          -- terminal: race window lost; both filled (Futu only realistically)
                    'manually_cancelled',        -- terminal: operator cancelled before fill
                    'submission_failed',         -- terminal: one or both legs failed to place at broker
                    'cancellation_failed',       -- non-terminal: orchestrator's cancel returned non-2xx; needs operator retry
                    'inconsistent_audit'         -- terminal: backend lost track (restart loss, manual DB edit, etc.); requires operator reconciliation
                )),
  -- Diagnostic fields populated on terminal-failure transitions
  failure_reason VARCHAR(256),                   -- printable ASCII, ASCII range [\x20-\x7E]
  filled_leg_id  UUID,                           -- which leg won the race (NULL until one_filled+ states)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at   TIMESTAMPTZ                      -- NULL until terminal state
);
CREATE INDEX oco_links_pending_idx ON oco_links(state) WHERE state IN ('pending', 'one_partial_pending_cancel', 'cancellation_failed');
CREATE INDEX oco_links_account_idx ON oco_links(account_id);
```

**State transitions (legal only):**

```
pending --[leg fills]--> one_filled
        --[leg partials]--> one_partial_pending_cancel
        --[both fill same poll tick]--> both_filled_race                 (Futu only)
        --[operator cancel]--> manually_cancelled
        --[place_order non-2xx on either leg]--> submission_failed

one_filled --[survivor cancel ok]--> both_done
           --[survivor cancel non-2xx]--> cancellation_failed

one_partial_pending_cancel --[survivor cancel ok]--> both_done
                           --[survivor cancel non-2xx]--> cancellation_failed
                           --[survivor also fills before cancel]--> both_filled_race

cancellation_failed --[operator retry succeeds]--> both_done
                    --[operator gives up]--> inconsistent_audit
```

**Recovery for `cancellation_failed`**: orchestrator retries with exponential backoff (5s, 30s, 5min, 30min) for 4 hours. After that, transitions to `inconsistent_audit` and emits a paging-severity Prometheus alert + Slack-style notification (Phase 11+ uses real Slack; for now, structured log line that the existing alerting picks up).

- New API endpoint: `POST /api/orders/oco` taking 2 `OrderRequest`s + a `nonce`. Returns `{oco_group_id, order_id_a, order_id_b}`. **MED-4**: if EITHER leg's `place_order` returns non-2xx (Schwab, IBKR, or Futu), the `oco_links` row is NEVER inserted — atomic at backend. Both order_ids are returned to the user as failed-to-place. Future-proofing for the unlikely case where Schwab native OCO partial-rejects: `submission_failed` state covers it post-insert.

- New service: `app/services/oco_orchestrator.py`.

**CRIT-2 — orchestrator deployment topology:**

- Runs as part of the existing backend lifespan (NOT a separate container). Single instance per backend process.
- **Single-writer enforced via Redis advisory lock**: orchestrator startup acquires `oco_orchestrator_lead` lock with 60s TTL, refreshes every 30s. Other backend instances (multi-worker scenario from Phase 24) wait + replay leader on lock loss. Phase 8b ships single-worker uvicorn (per CLAUDE.md); the lock is forward-compatible defense.
- **Hydrate-on-startup**: on lifespan startup, queries `oco_links WHERE state IN ('pending', 'one_partial_pending_cancel', 'cancellation_failed')` and opens gRPC OrderEvent streams per row.
- **Lifecycle**: `cancel+gather` shutdown matching the Phase 8a `OrderPoller` pattern. In-flight cancellations are not retried mid-shutdown; they replay on next startup via the hydrate query.

**HIGH-5 — subscription pattern:**

- 1 gRPC `OrderEvent` stream PER `(broker_id, account_number)` that has at least one row in the hydrate query above. Multiple OCO groups for the same account share one stream.
- Cap: 100 concurrent streams. Beyond that, new OCO submissions reject with `error.code="oco_orchestrator_capacity_exhausted"` (operator-actionable; very unlikely with 1-broker-per-user).
- Streams open lazily on first OCO submission for an account; close 60s after the last `pending` row for that account terminal-transitions.

**HIGH-6 — invariant (cancel always allowed):**

The orchestrator NEVER queries `OrderCapabilityService` when deciding to cancel. Cancel is always allowed regardless of current capability matrix state — you can always cancel an order you placed, even if its order_type was retroactively flipped to `is_supported=FALSE`. This avoids a race during 0011b/c/d migrations where the cache is mid-invalidation. Documented as `services/oco_orchestrator.py` module docstring invariant + asserted by test `test_oco_orchestrator_cancels_through_capability_flip`.

**Per-broker dispatch:**

- **Schwab adapter**: bundles into single `place_order` with `complexOrderStrategyType="OCO"` and 2 entries in `orderLegCollection`. Atomic at broker. `oco_links` row inserted only on 2xx (MED-4).
- **IBKR adapter**: assigns shared `Order.ocaGroup` (HIGH-3 — see below), `Order.ocaType=1` (cancel-on-fill semantics), submits both via `placeOrder` separately. Atomic per `ocaGroup`.
- **Futu adapter**: places both as independent orders, inserts `oco_link` row. Orchestrator watcher handles cancel-on-fill.

**HIGH-3 — IBKR `ocaGroup` 32-char truncation:**

TWS API truncates `ocaGroup` to 32 chars; UUIDs are 36. Canonical transformation:

```python
def oco_group_id_for_ibkr(oco_group_id: uuid.UUID) -> str:
    return str(oco_group_id).replace("-", "")[:32]
```

Same function used on both place + cancel paths to ensure the OCA group name matches. Asserted by `tests/unit/test_oco_orchestrator.py::test_ibkr_oca_group_truncation_is_consistent`. Place + Cancel paths both import this single helper from `app/services/oco_orchestrator.py`; never re-implement.

**Race window:** Schwab + IBKR are atomic broker-side (no race within broker layer; race is only between our state-tracking and the broker's atomic action). Futu has ~3-4s race window: 2s OrderPoller cadence + 100-500ms orchestrator latency (event receive → DB query → cancel issue). For fast-moving markets, both legs CAN fill before the survivor cancel lands → state transitions to `both_filled_race` (terminal). UI surfaces this as "OCO race lost" with both fills displayed; operator decides whether to manually flatten one position.

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

- New backend dep: `exchange_calendars` (preferred over `pandas_market_calendars` because lighter — no pandas runtime dep). Pinned in `backend/pyproject.toml`.
- New module: `backend/app/services/market_calendar.py` — exposes:
  - `today_in_exchange_tz(exchange: str) -> date` (CRIT-3)
  - `eod_for_exchange(exchange: str, expiry_date: date) -> datetime` (handles half-days)
  - `is_trading_day(exchange: str, d: date) -> bool`
  - `next_session_open(exchange: str) -> datetime` (HIGH-2, for `session_window_closed` error response)
  - `is_session_window_open(exchange: str, order_type: str) -> bool` (HIGH-2, MOC cutoff aware)
- **MED-3 explicit test cases** in `tests/unit/test_market_calendar.py`:
  - NYSE EDT/EST DST boundary (mid-March + early-November Sunday at 02:00 ET).
  - HKEX no-DST consistency (HKT = UTC+8 year-round).
  - LSE BST switchover.
  - US holidays: Thanksgiving (Thursday), Christmas Day, July 4 (when on weekday).
  - **Half-day early closes**: Black Friday 2026 (2026-11-27, NYSE early close 13:00 ET = 18:00 UTC), Christmas Eve 2026 (2026-12-24, NYSE early close 13:00 ET).
  - HK holidays: Lunar New Year, Mid-Autumn Festival.

### Capability matrix per-broker quirks

- The `broker_order_capability` `notes` column holds short human-readable strings rendered by the FE's `notesFor()` from F1 (e.g., `"Not supported on HKEX"`, `"Coming in Phase 8b"` already used in 8a, `"Session-bound; DAY only"` per HIGH-1).
- 8b-F's flip is partial: rows for HK contracts × `MOO`/`LOO`/`LOC` stay `is_supported=FALSE` with notes since HKEX has different session-bound semantics.

### MED-2 — `broker_features` table (new)

Mirrors `broker_order_capability` but for non-(type, TIF) features:

```sql
CREATE TABLE broker_features (
  broker_id        VARCHAR NOT NULL CHECK (broker_id IN ('ibkr','futu','schwab','alpaca')),
  feature          VARCHAR NOT NULL CHECK (feature IN (
                       'modify','bracket','oco','gtd_max_days','session_cutoff_minutes')),
  is_supported     BOOLEAN NOT NULL DEFAULT FALSE,
  int_value        INTEGER,                       -- for numeric features (gtd_max_days, session_cutoff_minutes)
  notes            VARCHAR(256) NOT NULL DEFAULT ''
                   CHECK (notes ~ '^[\x20-\x7E]*$'),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (broker_id, feature)
);
```

Initial seed (lands in 8b-0):

| broker | feature | is_supported | int_value | notes |
|---|---|---|---|---|
| ibkr | modify | TRUE | — | (existing) |
| futu | modify | FALSE | — | "Phase 6 deferred — empirical pending" |
| schwab | modify | TRUE | — | (existing) |
| ibkr | bracket | TRUE | — | (existing) |
| futu | bracket | FALSE | — | "Phase 6 deferred — empirical pending" |
| schwab | bracket | FALSE | — | "Phase 8b — pending implementation" |
| ibkr | oco | FALSE | — | "Phase 8b" |
| futu | oco | FALSE | — | "Phase 8b" |
| schwab | oco | FALSE | — | "Phase 8b" |
| ibkr | gtd_max_days | TRUE | 90 | "TWS API limit" |
| futu | gtd_max_days | TRUE | 30 | "Futu HK trading-day cap" |
| schwab | gtd_max_days | TRUE | 60 | "retail account limit per Schwab docs" |
| nyse | session_cutoff_minutes | TRUE | 10 | "MOC cutoff: 15:50 ET = 10 min before 16:00 close" |
| hkex | session_cutoff_minutes | TRUE | 0 | "no MOC support — Phase 8b out of scope" |

(For session_cutoff_minutes the broker_id slot is reused for exchange code — pragmatic for now; revisit in Phase 9 with a separate `exchange_features` table if it grows.)

### MED-7 — Postgres → Redis pubsub bridge

`OrderCapabilityService` (Phase 8a B1) subscribes to **Redis** pubsub `app_config:invalidate:order_capabilities`. The Alembic migrations call `pg_notify('app_config:invalidate:order_capabilities', '<broker>')` — **different channels**. The 0011a migration "worked" only because the 60s LRU TTL eventually expired; for 0011b/c/d, that 60s lag is an actual user-visible UX bug.

Fix: ship `backend/app/services/postgres_listen_bridge.py` in 8b-0. Single async daemon (lifespan-managed) that:

1. Opens a `LISTEN app_config:invalidate:*` pg connection.
2. On each `NOTIFY`, republishes to the corresponding Redis channel verbatim.
3. Reconnects with exponential backoff on connection drop.

Tests: `tests/unit/test_postgres_listen_bridge.py` — assert that a `pg_notify('foo', 'bar')` event triggers `redis.publish('foo', 'bar')` within 1s.

### Alembic migration plan (corrected)

- 0012 — `broker_features` table + initial seed (8b-0; per MED-2)
- 0013 — Schwab partial flip (TRAIL+TRAIL_LIMIT all TIFs + existing types × GTD + session-bound × DAY = 18 new rows) (8b-S close; per HIGH-1)
- 0014 — Futu partial flip + `broker_features.{modify,bracket}` updates (8b-F close)
- 0015 — IBKR partial flip (same shape as 0013) (8b-I close; per HIGH-1)
- 0016 — `oco_links` table (8b-OCO open; per CRIT-1 expanded state machine)
- 0017 — `broker_features.oco = TRUE` per broker after empirical validation (8b-OCO close)

(Original spec used 0011b-e per Alembic's letter-suffix branching convention. Renumbered to 0012-0017 sequential to keep migration history linear; less surprise on `alembic history`.)

### Empirical script artifacts + MED-5 enforcement

3 new scripts in `scripts/empirical/`. Each writes a JSON artifact to `scripts/empirical/artifacts/`.

**MED-5 — pre-commit hook** (`scripts/pre-commit-check-empirical-artifacts.sh`):

```bash
#!/usr/bin/env bash
# Block any artifact commit containing PII/secrets.
set -euo pipefail
PATTERNS='accountNumber|access_token|refresh_token|app_secret|app_key|account_hash'
if git diff --cached --name-only | grep -E '^scripts/empirical/artifacts/.*\.json$' | xargs -r grep -lE "$PATTERNS" 2>/dev/null; then
    echo "ERROR: empirical artifact contains sensitive field. Redact before committing." >&2
    exit 1
fi
```

Wired into `.pre-commit-config.yaml` as a custom hook. Phase 8a's manual `accountNumber` redaction (commit `7e7f54e`) was after-the-fact; the hook catches it at commit time.

---

## Out of scope (Phase 8b)

- **Multi-leg combos**: spreads, straddles, butterflies. Phase 13.
- **Algos**: TWAP, VWAP, Adaptive, Iceberg. Phase 17.
- **Options-specific order types**: exercise, assign, complex options strategies. Phase 12.
- **Conditional orders**: trigger-on-other-symbol-price etc. Future Phase TBD.
- **Quantity slicing on OCO**: a 100-share OCO leg partial-fills 30 shares; we currently cancel the other leg in full. Phase 9 might handle re-quoting the survivor for the remaining 70.
- **Extended-hours session GTD** (e.g., expire at 18:00 ET on date X). Phase 8b uses session close only.

---

## Risks (post-architect-review, in order of priority)

1. **HIGH — `exchange_calendars` upstream coverage for HKEX.** Need to confirm before committing 8b-F. Fallback: hand-roll a small HK calendar in `services/market_calendar.py`. **Validation step: spike script in 8b-0 imports `exchange_calendars.get_calendar("XHKG")` and asserts presence of HK Lunar New Year + Mid-Autumn dates for 2026.**
2. **HIGH — Futu Bracket attached-order semantics.** Phase 6 deferred this for a reason. Empirical script is the gate per HIGH-4 above; if it fails, Modify ships independently and Bracket punts to Phase 9 with explicit `is_supported=FALSE` notes.
3. ~~**MEDIUM — IBKR `ocaGroup` UUID length limit**~~ → **resolved** by HIGH-3 canonical truncation helper. No longer a risk; covered by unit test.
4. **MEDIUM — Schwab native OCO error-code stability.** Schwab's `complexOrderStrategyType="OCO"` is documented but rarely used by retail; rejection codes might surprise. Mitigated by the empirical script + MED-4 atomic-or-nothing rule.
5. ~~**LOW — Capability-cache invalidation lag**~~ → **resolved** by MED-7 `postgres_listen_bridge` daemon. Migration fires `pg_notify`; bridge republishes to Redis; cache busts within 1s.

**New risks surfaced during architect review:**

6. **MEDIUM — OCO orchestrator single-instance bottleneck.** CRIT-2 specifies single-instance via Redis advisory lock. Forward-compatible with Phase 24 multi-worker uvicorn but not currently exercised. Test: shut down primary backend during a `pending` OCO group; verify recovery on restart hydrates state correctly.
7. **MEDIUM — Race window on Futu OCO** (~3-4s, per §6 race window note). Fast-moving markets can cause `both_filled_race` terminal state. Operator-visible via UI; documented as known limitation.
8. **LOW — `broker_features` table grows unboundedly** if every per-broker quirk gets a row. Acceptable for Phase 8b; revisit naming convention in Phase 9 if features list exceeds ~30.

**Resolved by inline architect-review fixes:**

- ~~OCO partial-success state machine gap~~ (CRIT-1 → 9-state machine).
- ~~Orchestrator deployment topology~~ (CRIT-2 → single-instance + Redis lock + hydrate-on-startup).
- ~~GTD UTC-vs-exchange-TZ bug~~ (CRIT-3 → exchange-local validation).
- ~~Session-bound type/TIF nonsense combos~~ (HIGH-1 → schema validator + matrix flips DAY-only).
- ~~MOC submission window unenforced~~ (HIGH-2 → `session_window_closed` validator).
- ~~Orchestrator subscription pattern undefined~~ (HIGH-5 → 1-stream-per-account, capped at 100).
- ~~Race during capability cache invalidation~~ (HIGH-6 → orchestrator never queries capability for cancel).
- ~~`broker_order_features` undecided~~ (MED-2 → table specced).
- ~~Half-day market closes missing~~ (MED-3 → Black Friday + Christmas Eve test cases).
- ~~Schwab OCO partial-submission cleanup~~ (MED-4 → atomic-or-nothing).
- ~~PII enforcement is doc not enforcement~~ (MED-5 → pre-commit hook).
- ~~IBKR TRAIL TWS version~~ (MED-6 → constructor mock test).
- ~~Wrong GTD cap (90d vs 30d floor)~~ (MED-8 → 30d default + per-broker override via `broker_features.gtd_max_days`).

---

## Estimate (revised post-architect-review)

- 8b-0: 2 days (schema widening + `broker_features` table + `postgres_listen_bridge` + `market_calendar` lib + pre-commit hook + tests). Up from 1d due to MED-2/5/7 additions.
- 8b-S: 2 days (extend normalize + nightly TRAIL/GTD parametrize cases + 0013 migration).
- 8b-F: 6 days (Modify + Bracket + type expansion + empirical script + HIGH-4 partial-pass logic + Futu features in `broker_features`). Up from 5d due to HIGH-4 explicit success criteria.
- 8b-I: 2 days (extend + MED-6 TWS-version constructor test + 0015 migration).
- 8b-OCO: 6 days (orchestrator with 9-state machine + Redis lock + hydrate-on-startup + 2 empirical scripts + 0016/0017 migrations + integration tests covering all state transitions). Up from 4d due to CRIT-1/CRIT-2/HIGH-3/HIGH-5/HIGH-6 expansions.
- Plan-writing + per-chunk reviewer chains: 1 day.

**Total: ~19 working days.** Realistic for the full v0.9.0 release. LOW-1 (estimate-aggressive) absorbed.

## LOW-severity findings (deferred to plan-time / implementation chunks)

- **LOW-1**: Estimate revised from 15d → 19d above.
- **LOW-2**: Feature flag for OCO during rollout. Add `app_config['broker.oco.enabled']` as a kill-switch checked at the orders router's `/api/orders/oco` entry. Default `false` in prod until empirical scripts pass; flip to `true` after 0017 migration.
- **LOW-3**: Spec "TBD" items: Futu's MOO/LOO support (no — HKEX has no equivalent), Futu's TRAIL_LIMIT semantics (verify against `futu-api` docs at empirical-script time). Both resolved at chunk-implementation time.
- **LOW-4**: Add `// Phase 8b reserved tags 11-14` comment in `proto/broker/v1/broker.proto` adjacent to the new fields. Prevents future-phase tag collisions.
- **LOW-5**: Capability matrix design extends naturally to Phase 17 algos (TWAP, VWAP, etc.) via additional `order_types` rows. Note in this spec's "Out of scope" section as a future-extension hook.

Targeting `v0.9.0` release tag at 8b close.
