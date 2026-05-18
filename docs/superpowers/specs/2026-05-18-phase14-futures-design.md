# Phase 14 — Futures Trading Design

**Date:** 2026-05-18
**Status:** Brainstorm-approved
**Version:** v0.14.0

---

## 1. Why this phase

Phase 13 shipped multi-leg option combos. Phase 14 adds futures trading: CME financial + commodity futures on IBKR + Schwab, and HKFE index futures (HSI/HHI) on Futu. Futures differ from equities in three load-bearing ways: notional is `qty × price × multiplier` (not `qty × price`), contracts expire on a fixed schedule requiring active roll management, and physical-delivery contracts carry first-notice-day risk. None of these are handled by the existing order flow.

---

## 2. Scope

### In scope (Phase 14)

- **IBKR:** all CME/CBOT/NYMEX futures (ES, NQ, RTY, YM, MES, MNQ, CL, GC, ZB, ZC, etc.)
- **Futu:** HSI + HHI on HKFE
- **Schwab:** data (`GetFutureContracts`) wired; trade execution wired if API cooperates, 503 `broker_not_wired` if not
- Contract-month picker in `TradeTicketModal` (`FutureDetailsSection`)
- Dedicated `/futures` page: positions with DTE + roll rules, settlements tab
- Roll scheduling: APScheduler → Telegram preview → `/confirm_roll` → risk gate → close + open
- Settlement events: record to `futures_settlement_events` + Telegram notify
- Physical-delivery warning (WARN at DTE ≤ 10, BLOCK at `date.today() >= first_notice_day`)
- 6 Prometheus metrics

### Deferred

- Auto-close on settlement (revisit Phase 24 infra hardening)
- Schwab futures execution (503 stub if API 401s at Phase 14 start)
- Fully autonomous roll (no confirm) — bot-engine territory, Phase 20+
- Futures options (options on futures contracts) — Phase 12 extension, post-v1
- Spread orders across contract months — Phase 17 algos

---

## 3. Schema — Alembic 0050

Single migration covering all DDL changes.

### 3.1 `instrument_asset_class` enum widening

Add `FUTURE` to the existing PG enum (same `ALTER TYPE … ADD VALUE` pattern as `OPTION` in 0047).

**Also:** add `FUTURE = "FUTURE"` to the Python `AssetClass` StrEnum in `app/models/instruments.py`. Without this, SQLAlchemy's `SAEnum` rejects any row with `asset_class = 'FUTURE'` at the ORM layer with `LookupError`. (Same pattern: `OPTION = "OPTION"` was added to the StrEnum in Phase 12 alongside migration 0047.)

### 3.2 `futures_roll_rules` table

```sql
CREATE TABLE futures_roll_rules (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id    UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
    instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    days_before   SMALLINT NOT NULL CHECK (days_before BETWEEN 1 AND 90),
    enabled       BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, instrument_id)
);
```

`updated_at` trigger added (same pattern as 0049a).

### 3.3 `futures_settlement_events` table

```sql
CREATE TABLE futures_settlement_events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id       UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE RESTRICT,
    instrument_id    BIGINT NOT NULL REFERENCES instruments(id),
    settlement_price NUMERIC(20,8) NOT NULL,
    cash_delta       NUMERIC(20,8) NOT NULL,  -- signed; negative = loss
    settlement_type  TEXT NOT NULL CHECK (settlement_type IN ('CASH','PHYSICAL')),
    broker_event_id  TEXT,                     -- broker-side dedup key; nullable
    settled_at       TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON futures_settlement_events (account_id, settled_at DESC);
-- Dedup index: prevents duplicate settlement inserts on listener restart
CREATE UNIQUE INDEX ON futures_settlement_events (account_id, broker_event_id)
    WHERE broker_event_id IS NOT NULL;
```

`ON DELETE RESTRICT` (not CASCADE) — settlement records are financial history and must not be silently wiped when an account row is removed.

`updated_at` trigger is **not** added to `futures_settlement_events` — it is an append-only table; `updated_at` is only added to `futures_roll_rules`.

### 3.4 `instruments.meta` — `FutureDetails` discriminated union arm

Added to `services/options/types.py` (the `# Extensible: FutureDetails` comment is already there):

```python
class FutureDetails(BaseModel):
    asset_class: Literal["FUTURE"] = "FUTURE"
    contract_month: str            # "202506" (YYYYMM)
    tick_size: Decimal             # e.g. Decimal("0.25")
    tick_value: Decimal            # e.g. Decimal("12.50") — USD per tick
    multiplier: Decimal            # e.g. Decimal("50") for ES; Decimal("5") for MES
    first_notice_day: date | None  # None for cash-settled contracts
    expiry: date                   # last trading day
    settlement_type: Literal["CASH", "PHYSICAL"]
    exchange: str                  # "CME", "CBOT", "NYMEX", "HKFE"
    underlying_symbol: str         # root symbol, e.g. "ES", "HSI"
```

`InstrumentMeta` union becomes `NonOptionDetails | OptionDetails | FutureDetails`.
`NonOptionAssetClass` literal stays unchanged — `FutureDetails` is a full discriminated arm.

**`multiplier` type alignment (H2):** `FutureDetails.multiplier` is `Decimal`. `OptionDetails.multiplier` is widened from `int` to `Decimal` at the same time — a non-breaking change since `Decimal("100") == 100`. `EvaluationContext.multiplier` is widened from `int = 1` to `Decimal = Decimal("1")`. `_native_notional(multiplier: int = 1)` signature becomes `multiplier: Decimal = Decimal("1")`. All existing call sites in `orders_service` cast `details.multiplier` to `Decimal` at the assignment point (already done for options via `int(details.multiplier)`; change to `Decimal(details.multiplier)`).

**Design decision:** `meta` JSONB is the correct extension point for Phase 14 (consistent with Phase 12 options). If JSONB proves painful across Phases 15–16, Phase 24 infra hardening is the right time to extract all asset-class details to typed tables in one migration. No piecemeal switch.

---

## 4. Backend services — `services/futures/`

Module mirrors `services/options/`:

```
services/futures/
  __init__.py
  types.py                # FutureDetails re-export + FutureContractMonth dataclass
  contract_resolver.py    # GetFutureContracts RPC wrapper + Redis cache + singleflight
  roll_service.py         # roll rule CRUD + APScheduler job + execute_roll()
  settlement_listener.py  # broker settlement event consumer → DB + Telegram
```

### 4.1 `contract_resolver.py`

- Given `root_symbol` (e.g. `"ES"`) and broker, calls `GetFutureContracts` RPC
- Redis cache key: `futures:contracts:{broker}:{root_symbol}`, TTL 300s market-open / 3600s market-closed
- Singleflight per `(broker, root_symbol)` key — same `asyncio.Lock` pattern as `OptionChainService`
- Returns `list[FutureContractMonth]` sorted by expiry ascending, front 6 months max (configurable via `app_config`)
- `FutureContractMonth`: `conid, contract_month, expiry, first_notice_day, tick_size, tick_value, multiplier, settlement_type, exchange`
- **`days_to_expiry` is NOT stored in the cached Redis payload.** It is computed at read time as `(expiry - date.today()).days` — both in the REST response serialiser and in the FE (`expiryDate` is cached; `daysToExpiry` is derived client-side). This prevents stale DTE values within the 300s/3600s cache TTL window, which is critical for the DTE badge threshold and roll-rule trigger logic.

### 4.2 `roll_service.py`

**CRUD:**
- `set_roll_rule(db, account_id, instrument_id, days_before)` — upsert to `futures_roll_rules`
- `get_roll_rules(db, account_id)` — list all enabled rules for account
- `delete_roll_rule(db, account_id, instrument_id)` — hard delete

**Roll rule lifecycle:** `instrument_id` in `futures_roll_rules` refers to a **specific contract-month instrument row** (e.g. ESM25, `instrument_id = 42`). After a successful roll, `execute_roll()` automatically upserts a new rule pointing at the newly opened contract's `instrument_id` (e.g. ESU25, `instrument_id = 57`) with the same `days_before`, then deletes the old rule. This keeps the roll rule perpetual without user intervention. If the new contract's instrument row does not yet exist in `instruments`, `contract_resolver` seeds it (same lazy-creation pattern as `seed_instruments_from_positions`).

**APScheduler job — `check_and_notify_rolls()`:**
- Registered in `app/main.py` lifespan alongside `mute_expiry_job`
- Two registrations: daily at 09:00 US/Central (for CME/CBOT/NYMEX rules) and 09:00 Asia/Hong_Kong (for HKFE rules). Each firing passes an `exchange_filter` parameter (`{"CME","CBOT","NYMEX"}` or `{"HKFE"}`) so only rules for that exchange group are evaluated. Without this filter, CME rules would be evaluated at HK open (~02:00 US/Central) producing misleading cost estimates from pre-market quotes. As more exchanges are added in Phases 15–16, additional `(cron_expr, exchange_filter)` tuples are registered from `app_config`.
- Queries all `enabled=true` roll rules joined to current open positions, filtered by exchange
- For each: compute DTE as `(expiry - date.today()).days` (not from cache)
- If `DTE <= days_before` and no nonce already pending for this specific instrument (see dedup below):
  - Fetch next contract month via `contract_resolver`
  - Compute estimated net cost from Redis quote bus mid spread
  - In a single Redis pipeline: SET `futures:roll:instrument:{account_id}:{instrument_id}` = nonce (24h TTL) + SET `futures:roll:pending:{account_id}:{nonce}` = `{instrument_id, close_conid, open_conid, account_id}` JSON (24h TTL). Both keys set atomically before sending Telegram.
  - Send Telegram preview (see §6)
- **Deduplication (Option A):** `EXISTS futures:roll:instrument:{account_id}:{instrument_id}` — single O(1) lookup per rule, no SCAN. Scoped to the specific instrument so a pending ESM25 roll does not suppress a NQM25 notification on the same account. On nonce consumption via GETDEL, also DELETE `futures:roll:instrument:{account_id}:{instrument_id}`. On TTL expiry (24h), both keys naturally expire together.

**`execute_roll(account_id, nonce)`:**
1. `GETDEL futures:roll:pending:{account_id}:{nonce}` — literal key, atomic single-use gate. Returns nil → 404. No wildcard.
2. Parse payload JSON: `{instrument_id, close_conid, open_conid, account_id}`. Validate `account_id` in payload matches JWT claim (prevents cross-account nonce replay).
3. Risk gate on close leg — if BLOCK, abort with Telegram error
4. `place_order(close leg)` — await fill by subscribing to existing Redis pubsub channel `orders:events:account:{account_id}`, filter messages where `order_id == close_order_id` and `status == "filled"`. Timeout: 30s (configurable via `app_config[futures/roll_fill_timeout_s]`, default 30 — tighter than 10s to handle volatile sessions).
5. If close fills: risk gate on open leg → `place_order(open leg)`
6. Partial fill path: close filled, open failed → Telegram `"⚠ Roll partially executed — {old} closed but {new} open failed. Check positions."` — no second close attempt
7. Success: Telegram `"✅ Roll executed: {old} → {new} filled @ {price}"`

**Rate limit note:** Roll confirm consumes 1 token from the `check_trade` bucket (5/min, fail-CLOSED), but places up to 2 orders. This is intentional — the roll is treated as a single atomic trade action. The asymmetry is documented here so it is not "fixed" during implementation.

### 4.3 `settlement_listener.py`

Three separate background tasks wired into lifespan — one per broker type, each with different event delivery characteristics:

- **IBKR** (`_ibkr_settlement_listener`): continuous event subscription via `ib.commissionReport` + `execDetails` filtered to `secType="FUT"` on settlement date. Real-time, event-driven.
- **Futu** (`_futu_settlement_poller`): APScheduler job, fires daily at HKFE settlement time (16:30 Asia/Hong_Kong). Polls `trade_ctx.get_history_deals()` for futures settlement fills. No real-time push from Futu API.
- **Schwab** (`_schwab_settlement_poller`): APScheduler job, fires daily at CME settlement time (15:30 US/Central). Polls `GET /trader/v1/accounts/{hash}/transactions?types=TRADE` filtered to futures.

All three share the same `_record_settlement(db, redis, telegram, event)` helper that: INSERTs `futures_settlement_events` (dedup via `broker_event_id` unique index — INSERT ON CONFLICT DO NOTHING), publishes `futures.settlement.{account_id}` Redis channel, sends Telegram notify.

Fail-open: notification failure never raises in any listener loop. Each task is independent — IBKR listener crash does not affect Futu/Schwab pollers.

---

## 5. Proto + sidecar changes

### 5.1 New proto RPCs

```protobuf
rpc GetFutureContracts(GetFutureContractsRequest) returns (GetFutureContractsResponse);
rpc StreamSettlementEvents(StreamSettlementEventsRequest) returns (stream SettlementEvent);

message GetFutureContractsRequest {
  string root_symbol = 1;
  string broker_id   = 2;
}

message FutureContractMonth {
  string conid           = 1;
  string contract_month  = 2;  // "202506"
  string expiry_date     = 3;  // "2025-06-20"
  string first_notice    = 4;  // "" (empty string) if cash-settled; sidecar MUST emit "" not null
  string exchange        = 5;
  string tick_size       = 6;  // decimal string
  string tick_value      = 7;  // decimal string
  string multiplier      = 8;  // decimal string
  string settlement_type = 9;  // "CASH" | "PHYSICAL"
}

message GetFutureContractsResponse {
  repeated FutureContractMonth contracts = 1;
}

message StreamSettlementEventsRequest {
  string account_number = 1;
}

message SettlementEvent {
  string conid            = 1;
  string symbol           = 2;
  string settlement_price = 3;  // decimal string
  string cash_delta       = 4;  // signed decimal string
  string settlement_type  = 5;
  string settled_at       = 6;  // ISO8601
  string broker_event_id  = 7;  // broker-native dedup key; "" if unavailable
                                 // IBKR → ExecDetails.execId
                                 // Futu  → deal_id (from get_history_deals)
                                 // Schwab → activityId (from transactions response)
}
```

### 5.2 IBKR sidecar (`sidecar_ibkr/handlers.py`)

- `GetFutureContracts`: `ib.reqContractDetails(Contract(secType="FUT", symbol=root_symbol, exchange="SMART"))` → map to `FutureContractMonth`. `exchange` field populated from returned `ContractDetails.contract.exchange` (IBKR resolves GLOBEX/CBOT/NYMEX via SMART routing). Front 6 months only. `first_notice` field: emit `""` (empty string, proto3 default) when `ContractDetails.details.firstNoticeDate` is absent — never omit the field, so the receiver can distinguish "cash-settled" from "unknown".
- `StreamSettlementEvents`: subscribe `ib.commissionReport` + `execDetails` filtered to `secType="FUT"` on settlement date.
- `PlaceOrder`: add `secType="FUT"` branch at line ~992 alongside existing `"OPT"` branch. Construct `Contract(secType="FUT", conid=int(request.conid))` — explicit `secType` required for whatIf margin preview to work correctly.

### 5.3 Futu sidecar (`sidecar_futu/handlers.py`)

- `GetFutureContracts`: `quote_ctx.get_future_basicinfo(market=Market.HK, security_type=SecurityType.FUTURE)` → map HSI/HHI rows to `FutureContractMonth`.
- `StreamSettlementEvents`: poll `trade_ctx.get_history_deals()` daily at expiry date (Futu has no real-time settlement push). Same `SettlementEvent` shape.
- `PlaceOrder`: add `FUT` asset class routing to `trade_ctx.place_order()` with `security_type=SecurityType.FUTURE`.

### 5.4 Schwab sidecar (`sidecar_schwab/handlers.py`)

- `GetFutureContracts`: `GET /trader/v1/instruments?symbol={root}&projection=full` — confirmed present from API schema (`activeContract`, `expirationDate`, `lastTradingDate`, `firstNoticeDate`, `multiplier` fields confirmed).
- `StreamSettlementEvents`: poll `GET /trader/v1/accounts/{hash}/transactions?types=TRADE` filtered to futures on expiry date.
- `PlaceOrder`: attempt `POST /trader/v1/accounts/{hash}/orders` with `assetType: "FUTURE"`. If 401 at Phase 14 start: insert a row in the `broker_order_capability` table for `(broker_id="schwab", asset_class="FUTURE", supported=false)` via `order_capability_service` — same table/service used for all other capability entries. `orders_service` reads this table via `OrderCapabilityService.is_supported()` and returns 503 `broker_not_wired` for execution only (data via `GetFutureContracts` still works).

---

## 6. REST API (`app/api/futures.py`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/api/futures/contracts/{root_symbol}` | JWT | Available contract months, cached |
| `GET` | `/api/futures/roll-rules` | JWT | List roll rules for JWT account |
| `POST` | `/api/futures/roll-rules` | JWT | Create/update roll rule |
| `DELETE` | `/api/futures/roll-rules/{instrument_id}` | JWT | Delete roll rule |
| `GET` | `/api/futures/settlements` | JWT | Paginated settlement history |
| `POST` | `/api/futures/roll/preview` | JWT | UI-initiated roll: mints nonce, returns next month + estimated cost |
| `POST` | `/api/futures/roll/confirm/{nonce}` | JWT + CSRF | Execute pending roll — UI callers only; CSRF header required |

`POST /api/futures/roll/confirm/{nonce}` requires `X-Csrf-Nonce` header (same pattern as `POST /api/combos/confirm/{nonce}`). This endpoint is the **UI path only**. The Telegram `/confirm_roll <nonce>` command calls `execute_roll()` directly at the service layer via `handle_confirm_roll()` in `order_flow.py` — no HTTP hop, no CSRF header (same pattern as existing `handle_confirm → place_order()` in Phase 11d). Both paths share the same underlying `execute_roll()` function. The `check_trade` rate-limit bucket (5/min, fail-CLOSED on Redis error) is consumed in both paths.

---

## 7. Risk gate — `_check_futures_exposure`

Added to `RiskService.evaluate()`, called when `asset_class == "FUTURE"`.

| Check | Level | Condition |
|---|---|---|
| Kill switch | BLOCK | Existing `_check_kill_switch` — unchanged |
| Multiplier-adjusted concentration | WARN | `qty × price × multiplier > 20% of account NLV` |
| Physical delivery warning | WARN | `settlement_type == "PHYSICAL"` and `DTE ≤ 10` |
| Physical delivery hard block | BLOCK | `settlement_type == "PHYSICAL"` and `date.today() >= first_notice_day` |
| Concentration | WARN | Same underlying root > 50% of futures exposure |
| Margin preview | async | Existing `_check_margin` sidecar path — no change |

`RiskContext` gains three optional fields, populated by `orders_service` when `asset_class == "FUTURE"` via `parse_instrument_meta()`:
- `tick_size: Decimal | None`
- `first_notice_day: date | None`
- `underlying_symbol: str | None` — root symbol (e.g. `"ES"`, `"HSI"`), populated from `FutureDetails.underlying_symbol`

The concentration check groups open positions by `instruments.meta->>'underlying_symbol'` WHERE `instruments.asset_class = 'FUTURE'` to compute total futures exposure per root. `EvaluationContext.underlying_symbol` is what drives this grouping at risk-gate time.

**Fail-open on `first_notice_day = None`:** When `first_notice_day is None` (cash-settled contract), the physical delivery BLOCK check is skipped entirely — no block, no warn. Cash-settled contracts have no delivery risk. This mirrors Phase 12's fail-open on `exchange is None`.

**Physical delivery BLOCK does not apply to closing trades:** When `position_effect == "CLOSE"`, the physical delivery BLOCK (`date.today() >= first_notice_day`) is skipped. Closing a position reduces delivery risk; blocking the close would trap a user unable to exit a past-notice-day physical contract. The physical delivery WARN (DTE ≤ 10) is also suppressed on CLOSE — it is informational for open decisions only. `execute_roll()`'s close leg must pass `position_effect="CLOSE"` to `EvaluationContext`. This mirrors Phase 12's options gate skipping the naked-short ladder for closing trades.

`_native_notional()` already multiplies by `multiplier` — no change needed.

---

## 8. Telegram integration

**New commands** registered in `commands.py`:

```
/roll_rules               — list active roll rules for your accounts
/set_roll_rule ES 5       — roll ES 5 days before expiry
/delete_roll_rule ES      — delete roll rule for ES
/confirm_roll <nonce>     — confirm a pending roll (sent by the scheduler)
```

**Roll preview message** (sent by APScheduler job):

```
📋 Roll reminder: ESM25 expires in 5 days (2025-06-20)
Next month: ESU25 (Sep 2025)
Est. net cost: $12.50 debit

To roll: /confirm_roll abc123-def456
To skip:  /delete_roll_rule ES
```

**Settlement notify messages:**
- Cash: `"💰 ESM25 settled at 5,234.25 · Cash delta: +$1,250.00 (CASH settlement)"`
- Physical: `"⚠ ESH25 physical delivery initiated — contact broker to arrange delivery"`

**`handle_confirm_roll`** added to `order_flow.py`. Calls `execute_roll(account_id, nonce)` directly at the service layer — no HTTP call to the REST endpoint. Uses existing `check_trade` rate-limit bucket.

**`handle_delete_roll_rule`** added to `order_flow.py`. The Telegram command takes a root symbol (e.g. `ES`), but `delete_roll_rule(db, account_id, instrument_id)` takes a BIGINT. Resolution: query `instruments` for rows where `asset_class = 'FUTURE'` AND `meta->>'underlying_symbol' = '{root}'` and the account has an active roll rule. If exactly one match: delete it. If multiple (two contract months both have rules, unusual post-roll but possible): reply with an ambiguity list and ask the user to use `/delete_roll_rule <instrument_id>` instead.

**`DELETE /api/futures/roll-rules/{instrument_id}` (UI path):** The FE positions tab already has the full `RollRule` object (with `instrumentId: number`) fetched from `GET /api/futures/roll-rules`. The "Edit Rule" and delete actions map directly to `instrumentId` — no root-symbol resolution needed on the FE side.

---

## 9. Frontend

### 9.1 `FutureDetailsSection` (injected into `TradeTicketModal`)

Positioned below symbol/qty, above order type selector — same slot as `OptionDetailsSection`. Activated when `asset_class === 'FUTURE'`.

Fields displayed:
- Contract month dropdown (calls `GET /api/futures/contracts/{root}` on mount via React Query, `staleTime: 60_000` — contract list changes infrequently within a session)
- Multiplier, tick size, tick value
- Expiry date, first notice date
- Physical delivery `Alert` (destructive variant) when `settlementType === 'PHYSICAL'`

Selecting a contract month updates `conid` in the trade form.

### 9.2 `/futures` route (`features/futures/FuturesPage.tsx`)

Two tabs:

**Positions tab** — filtered view of open futures positions:

```
Symbol   Contracts  Avg Cost   DTE   Roll Rule      Action
ES       2          5210.50    32    5 days before  [Edit Rule] [Roll Now]
NQ       1          18420.00   32    —              [Set Rule]  [Roll Now]
HSI      1          19800.00   11    ⚠ Roll soon!   7 days       [Edit Rule] [Roll Now]
```

- "Roll Now" opens a confirm dialog: calls `POST /api/futures/roll/preview` (server mints nonce, returns next month + estimated net cost), then calls `POST /api/futures/roll/confirm/{nonce}` with CSRF header after user clicks confirm
- DTE badge turns amber at ≤ 10 days, red at ≤ 3 days
- Physical-delivery contracts show first notice date prominently

**Settlements tab** — paginated table from `GET /api/futures/settlements`:

```
Date        Contract  Settlement Px   Cash Delta    Type
2025-03-21  ESH25     5,234.25        +$1,250.00    CASH
2025-03-28  HSIH25    19,820.00       -HK$2,400     CASH
```

### 9.3 New frontend files

```
features/futures/
  FuturesPage.tsx
  FutureDetailsSection.tsx
  RollConfirmDialog.tsx
  __tests__/
    FutureDetailsSection.test.tsx
    FuturesPage.test.tsx
    RollConfirmDialog.test.tsx
services/futures/
  types.ts
  api.ts
```

**`services/futures/types.ts`:**

```typescript
export interface FutureContractMonth {
  conid: string;
  contractMonth: string;       // "202506"
  expiryDate: string;          // "2025-06-20"
  firstNoticeDate: string | null;
  exchange: string;
  tickSize: string;            // decimal string
  tickValue: string;           // decimal string
  multiplier: string;          // decimal string
  settlementType: 'CASH' | 'PHYSICAL';
  daysToExpiry: number;
}

export interface RollRule {
  instrumentId: number;
  daysBefore: number;
  enabled: boolean;
}

export interface SettlementEvent {
  id: string;
  symbol: string;
  contractMonth: string;
  settlementPrice: string;
  cashDelta: string;
  settlementType: 'CASH' | 'PHYSICAL';
  settledAt: string;
}
```

`AssetClass` in `services/types.ts` already includes `'futures'` — no change needed.

---

## 10. Testing

### Backend

```
tests/services/futures/
  __init__.py
  test_types.py                 # FutureDetails parse/validate, discriminated union round-trip
  test_contract_resolver.py     # cache hit/miss, singleflight, broker routing
  test_roll_service.py          # CRUD, APScheduler trigger, nonce mint/GETDEL, partial-fill path
  test_settlement_listener.py   # INSERT, Redis publish, Telegram notify, physical delivery warn
tests/api/
  test_futures_api.py           # all 6 REST endpoints, JWT auth, CSRF on confirm
tests/db/
  test_migration_0050.py        # futures_roll_rules + futures_settlement_events DDL
```

**Key test cases:**

| Test | What it covers |
|---|---|
| `test_future_details_round_trip` | `FutureDetails` → JSON → `parse_instrument_meta()` → typed model; CASH + PHYSICAL |
| `test_roll_rule_nonce_single_use` | GETDEL is atomic; second `/confirm_roll` same nonce → 404 |
| `test_roll_partial_fill` | close fills, open rejected → Telegram partial-fill alert, no second close |
| `test_physical_delivery_block` | risk gate BLOCKs open when `date.today() >= first_notice_day` |
| `test_physical_delivery_warn` | risk gate WARNs at `DTE ≤ 10` and `settlement_type == PHYSICAL` |
| `test_roll_checker_deduplication` | second daily run skips re-notify when nonce already pending (same instrument) |
| `test_roll_checker_dedup_cross_instrument` | pending ESM25 roll does NOT suppress NQM25 notification on same account |
| `test_settlement_listener_cash` | cash settlement → DB insert + correct Telegram message |
| `test_settlement_listener_physical` | physical settlement → warning message variant |
| `test_multiplier_notional` | `_native_notional` × multiplier correct for ES (50), HSI (50), MES (5) |
| `test_migration_0050_ddl` | `futures_roll_rules` UNIQUE constraint + `futures_settlement_events` columns |

### Frontend

```
features/futures/__tests__/
  FutureDetailsSection.test.tsx   # contract month picker, physical delivery warning render
  FuturesPage.test.tsx            # positions tab DTE badge, roll-soon warning, settlements tab
  RollConfirmDialog.test.tsx      # CSRF nonce fetch, confirm call, partial-fill error state
```

**Coverage target:** 80%+ on `services/futures/` and `app/api/futures.py`.

**Not tested at Phase 14:**
- Live broker settlement timing (needs production traffic)
- Schwab futures execution (skipped with `pytest.mark.skip(reason="schwab_futures_execution_unverified")` if 401)
- Physical delivery actual broker workflow (dashboard records and warns; broker handles delivery)

---

## 11. Prometheus metrics

```
futures_roll_notifications_total{exchange}
futures_roll_confirms_total{exchange, result}   # result: success|partial|failed
futures_roll_e2e_seconds{exchange}
futures_settlement_events_total{broker, settlement_type}
futures_contract_resolver_cache_hits_total{broker}
futures_contract_resolver_cache_misses_total{broker}
```

---

## 12. Deferred (post Phase 14)

| Item | Target phase |
|---|---|
| Auto-close position on settlement | Phase 24 |
| Schwab futures execution (if 401) | Phase 14 retry or Phase 15 |
| Fully autonomous roll (no Telegram confirm) | Phase 20 (bot engine) |
| Futures options (options on futures) | Post-v1 |
| Spread orders across contract months | Phase 17 (algos) |
| Dedicated asset-class tables if JSONB proves wrong | Phase 24 |
