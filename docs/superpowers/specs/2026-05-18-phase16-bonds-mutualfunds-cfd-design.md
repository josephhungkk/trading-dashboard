# Phase 16 — Bonds + Mutual Funds + CFD Design

**Version:** v0.16.0 (16a Bonds) · v0.16.1 (16b Mutual Funds) · v0.16.2 (16c CFD)
**Date:** 2026-05-18
**Status:** Approved — architect review applied

---

## 1. Scope

Phase 16 adds three new asset classes across three self-contained sub-phases:

- **16a (v0.16.0):** Corporate + government bonds on IBKR (execution) + Schwab (read-only positions/accrued). CUSIP/ISIN search, accrued-interest tracking, settlement display, yield/duration/credit-rating in `BondDetails` meta, risk gate, `/bonds` workspace page.
- **16b (v0.16.1):** Mutual funds on IBKR + Schwab. EOD NAV ordering with cut-off-time gate, NAV history hypertable, fractional units, units↔notional toggle, risk gate, `/funds` workspace page.
- **16c (v0.16.2):** CFDs on IBKR only (ex-US jurisdictions). All four underlying types: equity, index, forex, commodity. Overnight financing rate display, leverage risk gate, US-person fail-CLOSED BLOCK, `/cfd` workspace page.

Each sub-phase follows the same cross-cutting pattern established in Phase 14/15:
1. Alembic migration extends `instrument_asset_class` PG enum + Python `AssetClass` StrEnum.
2. New `*Details` discriminated-union arm added to `InstrumentMeta` in `app/services/options/types.py`.
3. New `_check_*_exposure` method wired into `RiskService.evaluate()`.
4. New proto RPCs in `proto/broker/v1/broker.proto`.
5. New `app/api/<asset>.py` REST endpoints.
6. New FE workspace page + `TradeTicketModal` section injection.

---

## 2. Cross-Cutting Architecture Decisions

- **`risk_limit_kind` enum extension (CRIT-1):** `risk_limit_kind` is a strict PG ENUM (created in alembic 0036, extended in 0051). Every new `limit_kind` literal must be added via `ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS '<kind>'` outside the transaction block (same pattern as alembic 0051:16). Each migration must include these statements before any `INSERT INTO risk_limits` seed row. Phase 16 adds seven new kinds across three migrations (listed in each sub-phase's data model section).

- **Settlement-date computation (CRIT-2):** `BusDayOffset` and `market_calendar.us_holidays()` do not exist. All settlement-date computation uses a new helper added to `app/services/market_calendar.py` in 16a Chunk B:
  ```python
  def add_business_days(exchange: str, start: date, n: int) -> date:
      """Add n business days using exchange_calendars schedule."""
      days = next_trading_days(exchange, start, n + 1)
      return days[-1]
  ```
  Exchange is resolved from instrument currency: `USD→XNYS`, `GBP→XLON`, `EUR→XTAR`, `HKD→XHKG`, `JPY→XTKS`, default `XNYS`. Spec calls become `add_business_days(exchange_for_currency(currency), trade_date, settlement_days)`.

- **`PreviewResponse` extension (MED-7):** Three new optional fields added as flat fields for Phase 16. This is the accepted deviation; consolidation into a discriminated `asset_extras` dict is deferred to Phase 17. Fields:
  - `settlement_date: date | None = None` (16a, bonds + funds)
  - `indicative_nav: str | None = None` (16b, fund NAV as decimal string)
  - `next_nav_date: date | None = None` (16b, if past cut-off)

- **CFD forex overlap:** Forex CFDs reuse `CFDDetails` (not `ForexDetails`). `CFDDetails.underlying_type == "forex"` causes `_check_cfd_exposure` to call `_forex_session_block()` (factored helper — see §5.5) for session check only, then applies CFD-specific leverage BLOCK on top. No `ForexCFDDetails` hybrid type. `_check_forex_exposure` is NOT called directly (avoids instrument_id mismatch and double-jeopardy from FX notional cap — HIGH-5).

- **Commodity CFDs:** Modelled as `CFDDetails(underlying_type="commodity", tick_size=..., multiplier=...)`. Same field semantics as `FutureDetails` — no separate `CommodityDetails` type.

- **Accrued interest (HIGH-7):** Preview-time accrued-interest lookup is **read-only from the table**. If no row exists, `PreviewResponse.accrued_interest` is `None` and UI displays "—". The broker RPC is the only writer: daily sweep at 16:30 ET plus opportunistic write on first fill (fill listener triggers one-shot `GetBondAccruedInterest` and upserts).

- **`risk_limit_kind` seed defaults (INFO-1):** Each migration seeds global default rows after the `ALTER TYPE` statements:
  - 0053: `bond_max_notional_per_trade=1_000_000`, `bond_max_concentration_pct=25`
  - 0054: `fund_max_notional_per_trade=500_000`, `fund_max_concentration_pct=25`
  - 0055: `cfd_max_notional_per_trade=250_000`, `cfd_max_leverage=20`, `cfd_max_concentration_pct=25`

- **Risk gate fail-OPEN policy:** Same as Phase 14/15 for infrastructure errors — increment `*_risk_check_failures_total` and pass through. Exception: CFD US-person check is **fail-CLOSED on NULL country** (HIGH-4) — different from all other gates.

---

## 3. Phase 16a — Bonds (v0.16.0)

### 3.1 Data Model

**Alembic 0053** (outside transaction block first):

```sql
-- Outside transaction:
ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'bond_max_notional_per_trade';
ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'bond_max_concentration_pct';
-- Then in upgrade():
ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'BOND';
```

Default seed rows (after enum additions):
```sql
INSERT INTO risk_limits (scope_type, scope_id, limit_kind, limit_value)
VALUES ('global', NULL, 'bond_max_notional_per_trade', 1000000),
       ('global', NULL, 'bond_max_concentration_pct', 25)
ON CONFLICT DO NOTHING;
```

- `BondDetails` discriminated-union arm added to `InstrumentMeta`:

```python
class CouponFrequency(IntEnum):
    ZERO_COUPON = 0
    ANNUAL = 1
    SEMI_ANNUAL = 2
    QUARTERLY = 4
    MONTHLY = 12

class BondDetails(BaseModel):
    asset_class: Literal["BOND"] = "BOND"
    cusip: str | None = None           # 9-char US CUSIP
    isin: str | None = None            # 12-char ISIN (non-US)
    issuer_id: str | None = None       # broker-supplied issuer identifier for concentration grouping
    coupon_rate: Decimal               # e.g. 4.250 (%)
    coupon_frequency: CouponFrequency  # ZERO_COUPON=0, ANNUAL=1, SEMI_ANNUAL=2, QUARTERLY=4, MONTHLY=12
    maturity_date: date
    face_value: Decimal                # par, e.g. 1000.00
    issue_date: date | None = None
    bond_type: str                     # "CORP" | "GOVT" | "MUNI" | "AGENCY"
    currency: str                      # e.g. "USD", "GBP"
    settlement_days: int = 2           # T+N; value from broker metadata, not hardcoded default
    callable: bool = False
    yield_to_maturity: Decimal | None = None
    duration: Decimal | None = None    # Macaulay duration in years
    credit_rating: str | None = None   # e.g. "A+", "Baa2"
```

- New table `bonds_accrued_interest` (with 5-year retention for UK CGT records):

```sql
CREATE TABLE bonds_accrued_interest (
    id             BIGSERIAL PRIMARY KEY,
    instrument_id  BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    account_id     UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
    accrued        NUMERIC(20,8) NOT NULL,
    as_of          DATE NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (instrument_id, account_id, as_of)
);
CREATE INDEX bonds_accrued_interest_instrument_idx
    ON bonds_accrued_interest(instrument_id, as_of DESC);
-- 5-year retention (UK CGT records require 6 years; extend to 7 in Phase 24)
SELECT add_retention_policy('bonds_accrued_interest', INTERVAL '5 years');
```

- `limit_kind` rows: `bond_max_notional_per_trade`, `bond_max_concentration_pct` (seeded above).

### 3.2 Services

**`app/services/bonds/bond_search_service.py`** (new):
- `search_bonds(query, account_id, broker_id)` — CUSIP/ISIN/keyword search via proto `SearchBonds` RPC; upserts `instruments` rows with `BondDetails` meta (including `issuer_id` from broker); Redis-caches results 10 min per SHA256(query+broker_id). **IBKR only** — `/api/bonds/search` returns HTTP 400 for `broker_id=schwab`.
- `resolve_bond_instrument(cusip_or_isin, broker_id)` — instrument registry lookup with sidecar fallback.
- `get_accrued_interest(instrument_id, account_id, db)` — **read-only** from `bonds_accrued_interest` table. Returns `None` if no row for today; does NOT call broker RPC at preview time.

**APScheduler sweep** — daily at 16:30 ET with per-broker rate caps (IBKR=10/s, Schwab=5/s via `asyncio.Semaphore`), gated on `app_config[risk/sweep_enabled]` (consistent with Phase 11a WoL gate). Sweeps all held bond positions, calls `GetBondAccruedInterest` per instrument×account with idempotency via UNIQUE constraint. Emits `bond_accrued_sweep_duration_seconds{broker}` histogram.

**Fill listener extension** — on first bond fill (no existing `bonds_accrued_interest` row for today): one-shot `GetBondAccruedInterest` RPC + upsert. Scoped to 16a Chunk B.

**`add_business_days` helper** — added to `app/services/market_calendar.py` in 16a Chunk B (see §2).

### 3.3 Sidecar + Proto Additions

**Sidecar contract resolution (HIGH-1):** Bond contracts require `secType="BOND"` plus identifier routing — add `_resolve_contract_bond` helper (separate from the existing 200+ line `_resolve_contract`):
- US corporate/govt (CUSIP present): `Contract(secType="BOND", secId=cusip, secIdType="CUSIP", exchange="SMART", currency=currency)`
- Non-US (ISIN present, no CUSIP): `Contract(secType="BOND", secId=isin, secIdType="ISIN", exchange="SMART", currency=currency)`
- Schwab: read-only positions/accrued only — no `PlaceOrder` dispatch to Schwab for BOND.

**Schwab constraint (HIGH-1):** Schwab is read-only for bonds in Phase 16a. `GET /api/bonds/positions` + `GET /api/bonds/{id}/accrued` aggregate IBKR + Schwab positions. `POST /api/orders/preview` + `place` are IBKR-only for `asset_class=BOND`; `broker_id=schwab` returns HTTP 400 `bond_execution_not_supported_schwab`.

```protobuf
message BondSearchRequest {
  string account_id = 1;
  string query      = 2;   // CUSIP, ISIN, or keyword
  string broker_id  = 3;
}
message BondSearchResult {
  string conid           = 1;
  string cusip           = 2;
  string isin            = 3;
  string issuer_id       = 4;
  string description     = 5;
  string coupon_rate     = 6;   // decimal string
  string maturity_date   = 7;   // ISO8601 date
  string bond_type       = 8;
  string currency        = 9;
  string ytm             = 10;  // decimal string, may be empty
  string credit_rating   = 11;
  int32  settlement_days = 12;
}
message BondSearchResponse { repeated BondSearchResult results = 1; }

message GetBondAccruedInterestRequest {
  string account_id = 1;
  string conid      = 2;
}
message GetBondAccruedInterestResponse {
  string accrued = 1;   // decimal string
  string as_of   = 2;   // ISO8601 date
}

rpc SearchBonds(BondSearchRequest) returns (BondSearchResponse);
rpc GetBondAccruedInterest(GetBondAccruedInterestRequest)
    returns (GetBondAccruedInterestResponse);
```

### 3.4 REST API (`app/api/bonds.py`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/bonds/search` | JWT | `?q=&broker_id=` — 20/min; 400 if broker_id=schwab |
| GET | `/api/bonds/{instrument_id}` | JWT | detail + latest accrued interest (None if not yet swept) |
| GET | `/api/bonds/{instrument_id}/accrued` | JWT | latest accrued for account |
| GET | `/api/bonds/positions` | JWT | account-scoped bond positions (IBKR + Schwab) |
| GET | `/api/bonds/history` | JWT | fills + open orders, cursor pagination |

Order placement: standard `POST /api/orders/preview` + `POST /api/orders/place` pipeline, IBKR only.

### 3.5 Risk Gate `_check_bond_exposure`

Called when `ctx.asset_class == AssetClass.BOND`. Fail-OPEN on infrastructure errors; increments `bond_risk_check_failures_total`.

- **BLOCK:** `maturity_date <= today + timedelta(days=settlement_days)` → `bond_settling_past_maturity`
- **BLOCK:** `notional > _resolve_limit(account_id, broker_id, "bond_max_notional_per_trade")` (if set) → `bond_notional_exceeded`
- **WARN (concentration):** Uses `BondDetails.issuer_id` when present. Fallback: `cusip[:6]` for US CORP bonds only (6-char CUSIP issuer prefix). If neither available: skip WARN, emit `bond_issuer_concentration_skipped_no_id_total.inc()`. If `ctx.account_nlv_base is None`: skip WARN, emit `bond_concentration_skipped_no_nlv_total.inc()`. When calculable: single issuer > `bond_max_concentration_pct` of NLV → `issuer_concentration_warning`.
- **WARN:** `callable == True` and `maturity_date - today <= timedelta(days=30)` → `callable_bond_near_call_date`

### 3.6 Settlement Date Display

At `preview_order` time: `settlement_date = add_business_days(exchange_for_currency(BondDetails.currency), trade_date, BondDetails.settlement_days)`. Returned in `PreviewResponse.settlement_date`. Display-only — no DB column. `settlement_days` comes from broker metadata populated at search time, not from `BondDetails` hardcoded default.

### 3.7 Prometheus Metrics (16a)

```
bond_search_requests_total{broker, outcome}
bond_search_latency_seconds{broker}
bond_accrued_interest_fetches_total{broker, outcome}
bond_accrued_sweep_total{outcome}
bond_accrued_sweep_duration_seconds{broker}          # histogram
bond_risk_blocks_total{reason}
bond_risk_check_failures_total
bond_issuer_concentration_skipped_no_id_total
bond_concentration_skipped_no_nlv_total
```

### 3.8 Frontend

**`TradeTicketModal` injection** when `asset_class === 'BOND'`:
- `BondDetailsSection`: coupon rate + frequency (human label from `CouponFrequency`), maturity date, YTM, credit rating, accrued interest (`None` displays "—"), settlement date (from `PreviewResponse.settlement_date`), callable badge if applicable.
- Qty input: standard integer (face-value units, 1 = face_value par).

**`/bonds` workspace page** — four panels:
1. **Search** — CUSIP/ISIN/keyword input → results table (description, coupon, maturity, YTM, rating, broker). IBKR search only; Schwab positions shown in panel 2.
2. **Positions** — held bond positions (IBKR + Schwab): market value, accrued interest, unrealised P&L.
3. **Detail panel** — selected bond: full `BondDetails` fields, price chart (klinecharts, `bond` quote source).
4. **Order history** — fills + open orders for bonds.

### 3.9 Chunk Breakdown (16a)

| Chunk | Content | Route |
|---|---|---|
| A | Alembic 0053: `ALTER TYPE risk_limit_kind` (2 values) + `instrument_asset_class` BOND; `BondDetails` meta arm with `CouponFrequency` enum + `issuer_id`; `bonds_accrued_interest` table + 5yr retention; seed `risk_limits` defaults | **Qwen** |
| B | `BondSearchService` (IBKR-only search + Schwab read-only constraint); `get_accrued_interest` (read-only); APScheduler sweep (rate-capped + sweep_enabled gate); fill-listener accrued hook; `add_business_days` + `exchange_for_currency` in `market_calendar.py` | **Codex** |
| C | Proto `SearchBonds` + `GetBondAccruedInterest` RPCs; `_resolve_contract_bond` sidecar helper; `app/api/bonds.py`; `_check_bond_exposure` in `risk_service.py`; `PreviewResponse.settlement_date` field | **Codex** |
| D | FE: `services/bonds/types.ts` + `api.ts`; `BondDetailsSection`; TradeTicketModal BOND mode; `BondsPage.tsx` + `/bonds` route | **Codex** |
| E | Integration tests (IBKR search flow, Schwab 400 rejection, accrued read-only at preview, fill-listener upsert, settling-past-maturity BLOCK, concentration WARN + no-id skip, settlement-date computation); Prometheus metric wiring | **Qwen** |

---

## 4. Phase 16b — Mutual Funds (v0.16.1)

### 4.1 Data Model

**Alembic 0054** (outside transaction block first):

```sql
-- Outside transaction:
ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'fund_max_notional_per_trade';
ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'fund_max_concentration_pct';
-- Then in upgrade():
ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'MUTUAL_FUND';
```

Default seed rows:
```sql
INSERT INTO risk_limits (scope_type, scope_id, limit_kind, limit_value)
VALUES ('global', NULL, 'fund_max_notional_per_trade', 500000),
       ('global', NULL, 'fund_max_concentration_pct', 25)
ON CONFLICT DO NOTHING;
```

- `MutualFundDetails` discriminated-union arm:

```python
class MutualFundDetails(BaseModel):
    asset_class: Literal["MUTUAL_FUND"] = "MUTUAL_FUND"
    isin: str | None = None
    cusip: str | None = None
    fund_family: str                   # e.g. "Vanguard", "Fidelity"
    fund_type: str                     # "OPEN_END" | "CLOSED_END" | "ETF_LIKE"
    currency: str
    min_investment: Decimal            # minimum initial purchase in fund currency
    min_subsequent: Decimal            # minimum subsequent purchase
    settlement_days: int = 1           # T+1 for most US funds; T+3 for some intl; from broker metadata
    allows_fractional: bool = True
    cutoff_time_et: time               # datetime.time, e.g. time(16, 0); Pydantic parses "16:00"
    expense_ratio: Decimal | None = None
    nav_currency: str                  # usually same as currency
```

`cutoff_time_et` is `datetime.time` (not `str`) — Pydantic v2 parses `"16:00"` / `"16:00:00"` natively and rejects invalid formats. Sweep coerces broker-returned strings via `time.fromisoformat()`; on parse failure WARN-log and leave existing value unchanged (do not overwrite previously-good value with `None`).

- New **hypertable** `fund_nav_snapshots`:

```sql
CREATE TABLE fund_nav_snapshots (
    instrument_id  BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    nav            NUMERIC(20,8) NOT NULL,
    nav_date       DATE NOT NULL,
    source         TEXT NOT NULL DEFAULT 'ibkr'
                   CHECK (source IN ('ibkr', 'schwab')),
    captured_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
SELECT create_hypertable('fund_nav_snapshots', 'captured_at');
SELECT add_retention_policy('fund_nav_snapshots', INTERVAL '2 years');
CREATE UNIQUE INDEX fund_nav_snapshots_instrument_date_source_idx
    ON fund_nav_snapshots (instrument_id, nav_date, source);
```

Volume estimate: ~500 held funds × 1 row/day × 2 brokers ≈ 1000 rows/day. 2-year retention ≈ 730k rows.

- `limit_kind` rows: `fund_max_notional_per_trade`, `fund_max_concentration_pct` (seeded above).

### 4.2 Services

**`app/services/funds/fund_search_service.py`** (new):
- `search_funds(query, account_id, broker_id)` — ISIN/CUSIP/name search via proto `SearchFunds` RPC; upserts `instruments` rows with `MutualFundDetails` meta; Redis-caches 10 min.
- `resolve_fund_instrument(isin_or_cusip, broker_id)` — registry lookup with sidecar fallback.
- `get_current_nav(instrument_id, db)` — reads latest `fund_nav_snapshots` row; returns `None` if no snapshot yet.

**APScheduler sweep** — daily at 17:00 ET (after NAV publication), per-broker rate caps (IBKR=10/s, Schwab=5/s), gated on `app_config[risk/sweep_enabled]`. Calls `GetFundNAV` per held fund position, upserts `fund_nav_snapshots` (idempotent via UNIQUE index). Also refreshes `MutualFundDetails.expense_ratio` + `cutoff_time_et` from broker; strict parse for `cutoff_time_et`. Emits `fund_nav_sweep_duration_seconds{broker}` histogram.

### 4.3 Proto Additions

```protobuf
message FundSearchRequest {
  string account_id = 1;
  string query      = 2;
  string broker_id  = 3;
}
message FundSearchResult {
  string conid           = 1;
  string isin            = 2;
  string cusip           = 3;
  string name            = 4;
  string fund_family     = 5;
  string fund_type       = 6;
  string currency        = 7;
  string nav             = 8;
  string nav_date        = 9;    // ISO8601 date
  string cutoff_time_et  = 10;   // "HH:MM" format
  string min_investment  = 11;
  string expense_ratio   = 12;   // may be empty
  int32  settlement_days = 13;
  bool   allows_fractional = 14;
}
message FundSearchResponse { repeated FundSearchResult results = 1; }

message GetFundNAVRequest {
  string account_id = 1;
  string conid      = 2;
}
message GetFundNAVResponse {
  string nav      = 1;
  string nav_date = 2;
}

rpc SearchFunds(FundSearchRequest) returns (FundSearchResponse);
rpc GetFundNAV(GetFundNAVRequest) returns (GetFundNAVResponse);
```

Sidecar: IBKR `secType="FUND"`, Schwab `assetType=MUTUAL_FUND` in order dispatch.

### 4.4 REST API (`app/api/funds.py`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/funds/search` | JWT | `?q=&broker_id=` — 20/min |
| GET | `/api/funds/{instrument_id}` | JWT | detail + latest NAV |
| GET | `/api/funds/{instrument_id}/nav` | JWT | NAV history, cursor pagination by `nav_date` |
| GET | `/api/funds/positions` | JWT | account-scoped fund positions |
| GET | `/api/funds/history` | JWT | fills + open orders, cursor pagination |

### 4.5 Risk Gate `_check_fund_exposure`

Called when `ctx.asset_class == AssetClass.MUTUAL_FUND`. Fail-OPEN on infrastructure errors; increments `fund_risk_check_failures_total`.

- **WARN** (not hard BLOCK): `now_et >= MutualFundDetails.cutoff_time_et` where `now_et = datetime.now(ZoneInfo('America/New_York')).time()` → `fund_cutoff_passed`. `PreviewResponse.next_nav_date` = next business day via `add_business_days(exchange_for_currency(currency), today, 1)`. Banner: "Will execute at next-day NAV ([date])."
- **BLOCK (min investment):** `notional < min_investment` (first purchase: no existing position) or `notional < min_subsequent` (existing position). Distinction: `_check_fund_exposure` executes a one-shot `SELECT qty FROM positions WHERE account_id=... AND instrument_id=...` inline (same pattern as `_check_forex_exposure` which does its own DB round-trip at risk_service.py:898–906). No `existing_qty` field on `EvaluationContext` — this avoids the CRIT-3 blast radius. → `below_minimum_investment`
- **BLOCK:** `notional > _resolve_limit(account_id, broker_id, "fund_max_notional_per_trade")` (if set) → `fund_notional_exceeded`
- **WARN:** `ctx.account_nlv_base is None` → skip, emit `fund_concentration_skipped_no_nlv_total.inc()`. Otherwise: single fund > `fund_max_concentration_pct` of NLV → `fund_concentration_warning`.
- **WARN:** `fund_type == "CLOSED_END"` → `closed_end_fund_advisory`

### 4.6 NAV + Settlement at Preview Time

- `PreviewResponse.indicative_nav` = latest NAV from `fund_nav_snapshots` as decimal string; `None` if no snapshot.
- `PreviewResponse.settlement_date` = `add_business_days(exchange_for_currency(currency), trade_date, settlement_days)`.
- `PreviewResponse.next_nav_date` = `add_business_days(exchange_for_currency(currency), today, 1)` if past cut-off; `None` otherwise.

### 4.7 Prometheus Metrics (16b)

```
fund_search_requests_total{broker, outcome}
fund_nav_sweep_total{broker, outcome}
fund_nav_sweep_duration_seconds{broker}              # histogram
fund_nav_snapshots_stored_total{broker}
fund_risk_blocks_total{reason}
fund_risk_check_failures_total
fund_cutoff_warnings_total{broker}
fund_concentration_skipped_no_nlv_total
```

### 4.8 Frontend

**`TradeTicketModal` injection** when `asset_class === 'MUTUAL_FUND'`:
- `FundDetailsSection`: fund family, type, current NAV (with date), expense ratio, cut-off time (amber badge if within 30 min of cut-off), min investment, settlement date.
- Qty input: `FractionalQtyInput` when `allows_fractional == true` (`decimals=3`); standard integer input when `allows_fractional == false` (whole units only — covers CEFs and institutional share classes). Units↔notional $ toggle when fractional; notional divides by current NAV client-side.
- Next-day NAV WARN banner if `PreviewResponse.next_nav_date` is set.

**`/funds` workspace page** — four panels:
1. **Search** — ISIN/CUSIP/name → results (name, family, NAV, date, expense ratio, cut-off, min investment).
2. **Positions** — held fund positions: units, cost basis, NAV × units = market value, unrealised P&L.
3. **NAV history chart** — klinecharts line chart over `fund_nav_snapshots` (1m/3m/1y/all).
4. **Order history** — fills + open orders for funds.

### 4.9 Chunk Breakdown (16b)

| Chunk | Content | Route |
|---|---|---|
| A | Alembic 0054: `ALTER TYPE risk_limit_kind` (2 values) + `instrument_asset_class` MUTUAL_FUND; `MutualFundDetails` meta arm (`cutoff_time_et: time`); `fund_nav_snapshots` hypertable + 2yr retention + CHECK + unique index; seed `risk_limits` defaults | **Qwen** |
| B | `FundSearchService` + `get_current_nav`; APScheduler sweep (rate-capped + idempotent + sweep_enabled gate + strict cutoff_time parse); `fund_nav_sweep_duration_seconds` histogram | **Qwen** |
| C | Proto `SearchFunds` + `GetFundNAV` RPCs; `app/api/funds.py`; `_check_fund_exposure` (incl. inline SELECT for first/subsequent, cutoff ZoneInfo comparison); `PreviewResponse.indicative_nav` + `next_nav_date` fields; sidecar `MUTUAL_FUND→secType="FUND"` / Schwab `assetType=MUTUAL_FUND` branch | **Codex** |
| D | FE: `services/funds/types.ts` + `api.ts`; `FundDetailsSection` (conditional FractionalQtyInput / integer based on `allows_fractional`); units↔notional toggle; TradeTicketModal MUTUAL_FUND mode; `FundsPage.tsx` + `/funds` route + NAV chart | **Codex** |
| E | Integration tests (search, NAV sweep upsert + idempotency, cutoff WARN + ZoneInfo, min-investment first vs subsequent BLOCK, next_nav_date, NAV chart data, fractional/whole-unit input rendering); Prometheus metric wiring | **Qwen** |

---

## 5. Phase 16c — CFD (v0.16.2)

### 5.1 Data Model

**Alembic 0055** (outside transaction block first):

```sql
-- Outside transaction:
ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'cfd_max_notional_per_trade';
ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'cfd_max_leverage';
ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'cfd_max_concentration_pct';
-- Then in upgrade():
ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'CFD';
ALTER TABLE broker_accounts ADD COLUMN IF NOT EXISTS country TEXT;
```

Default seed rows:
```sql
INSERT INTO risk_limits (scope_type, scope_id, limit_kind, limit_value)
VALUES ('global', NULL, 'cfd_max_notional_per_trade', 250000),
       ('global', NULL, 'cfd_max_leverage', 20),
       ('global', NULL, 'cfd_max_concentration_pct', 25)
ON CONFLICT DO NOTHING;
```

- `CFDDetails` discriminated-union arm:

```python
class CFDDetails(BaseModel):
    asset_class: Literal["CFD"] = "CFD"
    underlying_type: str           # "equity" | "index" | "forex" | "commodity"
    underlying_symbol: str         # e.g. "BARC", "UK100", "EUR/USD", "GOLD"
    underlying_conid: str | None   # IBKR conid of the underlying
    currency: str                  # margin + P&L currency
    tick_size: Decimal             # minimum price movement
    qty_step: Decimal = Decimal("1")  # minimum qty increment; 1 for most CFDs, <1 for some commodities
    multiplier: Decimal            # contract multiplier
    margin_rate: Decimal           # initial margin fraction, e.g. 0.05
    overnight_rate_long: Decimal
    overnight_rate_short: Decimal
    max_leverage: Decimal          # e.g. 20.0
    listed_country: str | None = None  # ISO2 where underlying equity is listed (display-only)
    exchange: str = "IBCFD"
```

`CFDDetails.country` dropped; replaced with `listed_country` (display-only, where the underlying equity is listed). The compliance question (account holder jurisdiction) is answered by `broker_accounts.country` exclusively. `qty_step` added — used by `FractionalQtyInput` for commodity CFDs; default `1` for equity/index.

- No new table for overnight financing. Broker-reported financing charges flow through `fills` pipeline.
- `limit_kind` rows: `cfd_max_notional_per_trade`, `cfd_max_leverage`, `cfd_max_concentration_pct` (seeded above).

### 5.2 Services

**`app/services/cfd/cfd_search_service.py`** (new):
- `search_cfds(query, account_id, underlying_type)` — IBKR via proto `SearchCFDs` RPC; upserts `instruments` with `CFDDetails` meta; Redis-caches 10 min.
- `resolve_cfd_instrument(symbol, underlying_type, broker_id)` — registry lookup + sidecar fallback.
- `get_overnight_financing(instrument_id, qty, side, db)` — `abs(qty) × current_price × rate`. Display-only.

### 5.3 Proto Additions

```protobuf
message CFDSearchRequest {
  string account_id      = 1;
  string query           = 2;
  string underlying_type = 3;   // "equity"|"index"|"forex"|"commodity"|""
}
message CFDSearchResult {
  string conid                = 1;
  string symbol               = 2;
  string underlying_type      = 3;
  string underlying_symbol    = 4;
  string currency             = 5;
  string tick_size            = 6;
  string qty_step             = 7;
  string multiplier           = 8;
  string margin_rate          = 9;
  string overnight_rate_long  = 10;
  string overnight_rate_short = 11;
  string max_leverage         = 12;
  string listed_country       = 13;
}
message CFDSearchResponse { repeated CFDSearchResult results = 1; }

rpc SearchCFDs(CFDSearchRequest) returns (CFDSearchResponse);
```

Sidecar: `_resolve_contract` maps `asset_class="CFD"` → `secType="CFD"`, `exchange="IBCFD"`.

### 5.4 REST API (`app/api/cfd.py`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/cfd/search` | JWT | `?q=&underlying_type=` — 20/min |
| GET | `/api/cfd/{instrument_id}` | JWT | detail + overnight financing estimate |
| GET | `/api/cfd/positions` | JWT | account-scoped CFD positions |
| GET | `/api/cfd/history` | JWT | fills + open orders, cursor pagination |

### 5.5 Risk Gate `_check_cfd_exposure`

Called when `ctx.asset_class == AssetClass.CFD`. Fail-OPEN on infrastructure errors except the US-person check (fail-CLOSED — see below).

**Factored helper `_forex_session_block(self) -> GateBlockerEntry | None`** (new private method, Chunk C sub-task): extracts the session-open check from `_check_forex_exposure`. Returns `GateBlockerEntry(reason="session_closed", ...)` iff `not is_forex_session_open()`, else `None`. Both `_check_forex_exposure` and `_check_cfd_exposure` call it. Prevents instrument_id mismatch and double-jeopardy from FX notional cap.

Risk gate checks (in order):

- **BLOCK (fail-CLOSED):** `broker_accounts.country IS NULL` → `cfd_country_unknown` ("Account country unset; CFD trading requires operator classification. Edit /admin/accounts."). `broker_accounts.country == "US"` → `cfd_not_available_us`. Increments `cfd_country_unknown_block_total` or `cfd_us_block_total` respectively. This is the ONLY gate that fails CLOSED on missing data — consistent with `_check_margin` (risk_service.py:570–580) which fails CLOSED when sidecar unreachable.
- **BLOCK:** `CFDDetails.margin_rate <= 0` → treat as `implied_leverage = CFDDetails.max_leverage` + emit `WARN cfd_margin_rate_anomalous` (not a hard block; broker placeholder — fail-OPEN). `CFDDetails.margin_rate >= 1` → `implied_leverage = 1` (cash-only, no leverage). Otherwise: `implied_leverage = 1 / CFDDetails.margin_rate`. BLOCK if `implied_leverage > min(_resolve_limit(..., "cfd_max_leverage"), CFDDetails.max_leverage)` → `cfd_leverage_exceeded`.
- **BLOCK:** `notional > _resolve_limit(..., "cfd_max_notional_per_trade")` (if set) → `cfd_notional_exceeded`
- **BLOCK (equity CFD session):** `underlying_type == "equity"` and instrument's underlying exchange not currently open (resolved from `CFDDetails.underlying_conid` → `instruments` lookup → `exchange_calendars`) → `cfd_equity_session_closed`. Index CFDs: skip (broker handles out-of-hours pricing). Commodity CFDs: BLOCK only if `tif == "DAY"` and session closed, otherwise WARN `commodity_cfd_session_advisory`.
- **BLOCK (forex CFD session):** `underlying_type == "forex"` → call `_forex_session_block()`; propagate if not None.
- **WARN:** `ctx.account_nlv_base is None` → skip, emit `cfd_concentration_skipped_no_nlv_total.inc()`. Otherwise: single CFD > `cfd_max_concentration_pct` of NLV → `cfd_concentration_warning`.
- **WARN:** `side == "BUY"` and `tif in ("GTC", "GTD")` → `overnight_financing_advisory` with estimated daily cost.
- **WARN:** `underlying_type == "commodity"` and not session-blocked above → `commodity_cfd_advisory` (wide spread outside session).

### 5.6 Prometheus Metrics (16c)

```
cfd_search_requests_total{broker, underlying_type, outcome}
cfd_search_latency_seconds{underlying_type}
cfd_risk_blocks_total{reason}
cfd_risk_check_failures_total
cfd_overnight_advisory_total{underlying_type}
cfd_us_block_total
cfd_country_unknown_block_total
cfd_concentration_skipped_no_nlv_total
```

### 5.7 Frontend

**`TradeTicketModal` injection** when `asset_class === 'CFD'`:
- `CFDDetailsSection`: underlying type badge, underlying symbol, margin rate, max leverage, tick size, estimated overnight financing (client-side). Amber badge if GTC with financing > 0.1%/day.
- `cfd_country_unknown` or `cfd_not_available_us` BLOCK → prominent red banner.
- Qty input: `FractionalQtyInput` using `CFDDetails.qty_step` (`decimals` = decimal places of `qty_step`); default step=1 renders as integer input.

**`/cfd` workspace page** — four panels:
1. **Search** — underlying_type filter tabs (All / Equity / Index / Forex / Commodity). Results: symbol, underlying, margin rate, overnight rates, max leverage.
2. **Positions** — open CFDs: qty, entry price, current price, unrealised P&L, daily financing charge.
3. **Detail panel** — selected CFD: full `CFDDetails` fields, price chart (klinecharts).
4. **Order history** — fills + open CFD orders.

**`/admin/accounts` country editor (ships in 16c Chunk D):** Simple `<select>` for ISO2 country on each account row. Needed because fail-CLOSED on NULL makes the gate unusable without an operator UI.

### 5.8 Chunk Breakdown (16c)

| Chunk | Content | Route |
|---|---|---|
| A | Alembic 0055: `ALTER TYPE risk_limit_kind` (3 values) + `instrument_asset_class` CFD; `broker_accounts.country` column; `CFDDetails` meta arm (`listed_country`, `qty_step`); seed `risk_limits` defaults | **Qwen** |
| B | `CFDSearchService`; proto `SearchCFDs` RPC; sidecar `CFD→secType="CFD"/exchange="IBCFD"` branch | **Codex** |
| C | `app/api/cfd.py`; `_forex_session_block` refactor in `risk_service.py`; `_check_cfd_exposure` (fail-CLOSED US-person, leverage formula, equity/forex/commodity session, concentration) | **Codex** |
| D | FE: `services/cfd/types.ts` + `api.ts`; `CFDDetailsSection` (qty_step-driven FractionalQtyInput); overnight financing estimate; TradeTicketModal CFD mode; `CFDPage.tsx` + `/cfd` route; `/admin/accounts` country editor | **Codex** |
| E | Integration tests (search, US-person fail-CLOSED, country-unknown fail-CLOSED, leverage BLOCK, margin_rate=0 edge, equity-session BLOCK, forex-CFD session delegation, overnight advisory, commodity advisory); Prometheus metric wiring | **Qwen** |

---

## 6. Updated `InstrumentMeta` Union (after Phase 16c)

```python
InstrumentMeta = Annotated[
    NonOptionDetails
    | OptionDetails
    | FutureDetails
    | ForexDetails
    | CryptoDetails
    | BondDetails
    | MutualFundDetails
    | CFDDetails,
    Field(discriminator="asset_class"),
]
```

---

## 7. Deferred

- Bond yield curve / duration analytics page — Phase 18+ (scanner phase).
- Mutual fund exchange (fund-to-fund switch orders) — post-16b; broker APIs vary.
- Closed-end fund NAV discount/premium tracking — Phase 18+.
- CFD dividend adjustments — broker-reported via fills pipeline; no special handling in Phase 16.
- Commodity CFD storage/delivery risk (oil roll) — Phase 17+; Phase 16c only trades near-front.
- OANDA as FX CFD data fallback — Phase 18+ (same as Phase 15 deferral).
- `bonds_accrued_interest` retention extension to 7 years — Phase 24 infra hardening.
- `PreviewResponse.asset_extras` discriminated consolidation — Phase 17 (three flat optional fields accepted in Phase 16 per MED-7 decision).
- INFO-2: proto-only additions in 16a Chunk C and 16c Chunk B could split to Qwen if Codex capacity is constrained — not blocking.

---

## 8. Architect Review Findings Applied (2026-05-18)

**Pass-1:** 3 CRIT · 7 HIGH · 8 MED applied inline. 4 LOW noted. 2 INFO noted/actioned.

- **CRIT-1** — `risk_limit_kind` is a strict PG enum. Each migration now leads with `ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS` for all new literals + default seed rows. §2, §3.1, §4.1, §5.1 updated.
- **CRIT-2** — `BusDayOffset` / `us_holidays()` do not exist. Replaced with `add_business_days(exchange, date, n)` helper (new in 16a Chunk B) + `exchange_for_currency` map. §2, §3.6, §4.6 updated.
- **CRIT-3** — `EvaluationContext.existing_qty` has 17+ construction sites. Dropped entirely; `_check_fund_exposure` does its own one-shot `SELECT qty FROM positions` inline (same pattern as `_check_forex_exposure`). §4.5 updated.
- **HIGH-1** — Bond sidecar: `_resolve_contract_bond` helper added (separate from 200-line `_resolve_contract`); CUSIP→`secIdType="CUSIP"`, ISIN→`secIdType="ISIN"`, `exchange="SMART"`. Schwab read-only constraint explicit: execution IBKR-only, search 400 for `broker_id=schwab`. §3.3, §3.4, §3.9 updated.
- **HIGH-2** — `cutoff_time_et` changed to `datetime.time`; Pydantic parses `"16:00"` natively. Comparison uses `ZoneInfo('America/New_York')`. Sweep strict-parses broker strings, WARN-logs and preserves existing value on failure. §4.1, §4.2, §4.5 updated.
- **HIGH-3** — `existing_qty` race resolved by using inline SELECT in `_check_fund_exposure` rather than a context field (aligns with CRIT-3 fix). §4.5 updated.
- **HIGH-4** — CFD US-person check flipped to fail-CLOSED on `NULL country`: returns `cfd_country_unknown` BLOCK. `broker_accounts.country` admin UI editor promoted from deferred to ships in 16c Chunk D. §5.5, §5.8, §7 updated.
- **HIGH-5** — Forex-CFD delegation refactored: new `_forex_session_block()` helper extracts session-only check. `_check_cfd_exposure` calls it directly; `_check_forex_exposure` also refactored to call it. Prevents instrument_id mismatch and FX notional cap double-jeopardy. §2, §5.5, §5.8 updated.
- **HIGH-6** — "Skip silently" for missing NLV now emits `*_concentration_skipped_no_nlv_total` counters for all three asset classes. §3.5, §4.5, §5.5, §3.7, §4.7, §5.6 updated.
- **HIGH-7** — `get_accrued_interest` changed to read-only at preview time; broker RPC moved to daily sweep + opportunistic fill-listener write. §2, §3.2 updated.
- **MED-1** — `bonds_accrued_interest` 5-year retention policy added to alembic 0053. §3.1 updated.
- **MED-2** — Issuer concentration uses `BondDetails.issuer_id` (broker-supplied) first; fallback to `cusip[:6]` for US CORP only; otherwise skip + `bond_issuer_concentration_skipped_no_id_total`. `issuer_id` added to `BondDetails` and proto. §3.1, §3.3, §3.5, §3.7 updated.
- **MED-3** — `CHECK (source IN ('ibkr', 'schwab'))` added to `fund_nav_snapshots`. §4.1 updated.
- **MED-4** — `CFDDetails.country` dropped; replaced with `listed_country` (display-only). §5.1, §5.5 updated.
- **MED-5** — `margin_rate <= 0` and `margin_rate >= 1` edge cases handled in leverage formula; `cfd_margin_rate_anomalous` WARN on anomalous value. §5.5 updated.
- **MED-6** — APScheduler sweeps (bonds + funds) now have per-broker rate caps + `sweep_enabled` gate + duration histograms. §3.2, §4.2 updated.
- **MED-7** — `PreviewResponse` flat optional fields accepted for Phase 16; consolidation to `asset_extras` deferred to Phase 17. §2 decision documented.
- **MED-8** — Equity-CFD session BLOCK added; index CFDs skip; commodity CFDs BLOCK on DAY TIF + session closed, WARN otherwise. §5.5 updated.
- **LOW-1** — `CouponFrequency(IntEnum)` added to `BondDetails`. §3.1 updated.
- **LOW-2** — `FundDetailsSection` renders `FractionalQtyInput` only when `allows_fractional == true`; integer input otherwise. §4.8 updated.
- **LOW-3** — `CFDDetails.qty_step` added; `FractionalQtyInput` uses it; eliminates `tick_size < 1` heuristic. §5.1, §5.7 updated.
- **LOW-4** — Clarified `settlement_days` comes from broker metadata, not hardcoded default. §3.1 note added.
- **INFO-1** — Default seed rows for all 7 new `limit_kind` values added to each migration. §2 sub-bullet added.
- **INFO-2** — Proto-only additions in Codex chunks noted as Qwen-splittable if needed; not blocking. §7 noted.
