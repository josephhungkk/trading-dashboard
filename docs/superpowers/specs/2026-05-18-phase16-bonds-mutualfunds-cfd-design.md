# Phase 16 — Bonds + Mutual Funds + CFD Design

**Version:** v0.16.0 (16a Bonds) · v0.16.1 (16b Mutual Funds) · v0.16.2 (16c CFD)
**Date:** 2026-05-18
**Status:** Draft — pending architect review

---

## 1. Scope

Phase 16 adds three new asset classes across three self-contained sub-phases:

- **16a (v0.16.0):** Corporate + government bonds on IBKR + Schwab. CUSIP/ISIN search, accrued-interest tracking, T+2 settlement display, yield/duration/credit-rating in `BondDetails` meta, risk gate, `/bonds` workspace page.
- **16b (v0.16.1):** Mutual funds on IBKR + Schwab. EOD NAV ordering with cut-off-time gate, NAV history hypertable, fractional units, units↔notional toggle, risk gate, `/funds` workspace page.
- **16c (v0.16.2):** CFDs on IBKR only (ex-US jurisdictions). All four underlying types: equity, index, forex, commodity. Overnight financing rate display, leverage risk gate, US-person BLOCK, `/cfd` workspace page.

Each sub-phase follows the same cross-cutting pattern established in Phase 14/15:
1. Alembic migration extends `instrument_asset_class` PG enum + Python `AssetClass` StrEnum.
2. New `*Details` discriminated-union arm added to `InstrumentMeta` in `app/services/options/types.py`.
3. New `_check_*_exposure` method wired into `RiskService.evaluate()`.
4. New proto RPCs in `proto/broker/v1/broker.proto`.
5. New `app/api/<asset>.py` REST endpoints.
6. New FE workspace page + `TradeTicketModal` section injection.

---

## 2. Cross-Cutting Architecture Decisions

- **CFD forex overlap:** Forex CFDs reuse `CFDDetails` (not `ForexDetails`). `CFDDetails.underlying_type == "forex"` causes `_check_cfd_exposure` to delegate the session check to `_check_forex_exposure` then applies leverage BLOCK on top. No hybrid `ForexCFDDetails` type.
- **Commodity CFDs:** Modelled as `CFDDetails(underlying_type="commodity", tick_size=..., multiplier=...)`. Same field semantics as `FutureDetails` — no separate `CommodityDetails` type.
- **Accrued interest:** Daily snapshot in `bonds_accrued_interest` table (not in `instruments`). Read at order-preview time. Display-only in ticket.
- **NAV history:** `fund_nav_snapshots` hypertable, EOD from daily broker sweep. Same pattern as `account_balance_snapshots`.
- **Settlement date (bonds + funds):** Computed at preview time as `trade_date + BusDayOffset(settlement_days, holidays=market_calendar.us_holidays())`. Returned in new `PreviewResponse.settlement_date: date | None` optional field. Display-only.
- **Risk gate fail-OPEN policy:** Same as Phase 14/15 — infrastructure errors increment `*_risk_check_failures_total` and pass through. Failures logged at WARNING.
- **`risk_limits` caps:** All new caps use the `limit_kind` row convention — no new typed columns. Resolution order: account → global → no cap (same as all other limits).
- **`PreviewResponse` extension:** Two new optional fields added in 16a and carried through:
  - `settlement_date: date | None = None` (16a, bonds + funds)
  - `indicative_nav: str | None = None` (16b, fund NAV as decimal string)
  - `next_nav_date: date | None = None` (16b, if past cut-off)

---

## 3. Phase 16a — Bonds (v0.16.0)

### 3.1 Data Model

**Alembic 0053:**

- `BOND` added to `instrument_asset_class` PG enum + Python `AssetClass` StrEnum.
- `BondDetails` discriminated-union arm added to `InstrumentMeta`:

```python
class BondDetails(BaseModel):
    asset_class: Literal["BOND"] = "BOND"
    cusip: str | None = None           # 9-char US CUSIP
    isin: str | None = None            # 12-char ISIN (non-US)
    coupon_rate: Decimal               # e.g. 4.250 (%)
    coupon_frequency: int              # payments/year: 2=semi-annual, 1=annual, 0=zero-coupon
    maturity_date: date
    face_value: Decimal                # par, e.g. 1000.00
    issue_date: date | None = None
    bond_type: str                     # "CORP" | "GOVT" | "MUNI" | "AGENCY"
    currency: str                      # e.g. "USD", "GBP"
    settlement_days: int = 2           # T+N; T-bills = 1, most bonds = 2
    callable: bool = False
    yield_to_maturity: Decimal | None = None   # refreshed from broker, optional
    duration: Decimal | None = None            # Macaulay duration in years, optional
    credit_rating: str | None = None           # e.g. "A+", "Baa2"
```

- New table `bonds_accrued_interest`:

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
```

- No new `risk_limits` column. Bond caps use `limit_kind` rows:
  - `bond_max_notional_per_trade` (global or account scope)
  - `bond_max_concentration_pct` (global or account scope)

### 3.2 Services

**`app/services/bonds/bond_search_service.py`** (new):
- `search_bonds(query, account_id, broker_id)` — CUSIP/ISIN/keyword search via proto `SearchBonds` RPC; upserts `instruments` rows with `BondDetails` meta; Redis-caches results 10 min per SHA256(query+broker_id).
- `resolve_bond_instrument(cusip_or_isin, broker_id)` — instrument registry lookup with sidecar fallback (mirrors `resolve_crypto_instrument` pattern).
- `get_accrued_interest(instrument_id, account_id, db)` — reads latest row from `bonds_accrued_interest` for today; if absent, calls `GetBondAccruedInterest` RPC and upserts.

**APScheduler job** — daily at 16:30 ET: sweeps all held bond positions across all accounts, calls `GetBondAccruedInterest` RPC per instrument×account, upserts `bonds_accrued_interest` rows.

### 3.3 Proto Additions

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
  string description     = 4;
  string coupon_rate     = 5;   // decimal string
  string maturity_date   = 6;   // ISO8601 date
  string bond_type       = 7;
  string currency        = 8;
  string ytm             = 9;   // decimal string, may be empty
  string credit_rating   = 10;
  int32  settlement_days = 11;
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

Sidecar change: `_resolve_contract` fallback maps `asset_class="BOND"` → `secType="BOND"`. ~5-line change, same pattern as FOREX/CRYPTO in Phase 15.

### 3.4 REST API (`app/api/bonds.py`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/bonds/search` | JWT | `?q=&broker_id=` — 20/min |
| GET | `/api/bonds/{instrument_id}` | JWT | detail + latest accrued interest |
| GET | `/api/bonds/{instrument_id}/accrued` | JWT | latest accrued for account |
| GET | `/api/bonds/positions` | JWT | account-scoped bond positions |
| GET | `/api/bonds/history` | JWT | fills + open orders, cursor pagination |

Order placement uses the standard `POST /api/orders/preview` + `POST /api/orders/place` pipeline. Bonds are not a special order flow (unlike FX RFQ).

### 3.5 Risk Gate `_check_bond_exposure`

Called when `ctx.asset_class == AssetClass.BOND`. Fail-OPEN on infrastructure errors; increments `bond_risk_check_failures_total`.

- **BLOCK:** `maturity_date <= today + timedelta(days=settlement_days)` → `bond_settling_past_maturity`
- **BLOCK:** `notional > _resolve_limit(account_id, broker_id, "bond_max_notional_per_trade")` (if set) → `bond_notional_exceeded`
- **WARN:** single issuer (grouped by `BondDetails.cusip[:6]` = issuer prefix for CORP; full ISIN issuer prefix for non-US) > `bond_max_concentration_pct` of `ctx.account_nlv_base` → `issuer_concentration_warning`. Skipped silently if `ctx.account_nlv_base is None`.
- **WARN:** `callable == True` and `maturity_date - today <= timedelta(days=30)` → `callable_bond_near_call_date`

### 3.6 Settlement Date Display

At `preview_order` time, `bonds_settlement_date` computed via `trade_date + BusDayOffset(settlement_days, holidays=market_calendar.us_holidays())`. Returned in `PreviewResponse.settlement_date`. Display-only — no DB column.

### 3.7 Prometheus Metrics (16a)

```
bond_search_requests_total{broker, outcome}
bond_search_latency_seconds{broker}
bond_accrued_interest_fetches_total{broker, outcome}
bond_accrued_sweep_total{outcome}
bond_risk_blocks_total{reason}
bond_risk_check_failures_total
```

### 3.8 Frontend

**`TradeTicketModal` injection** when `asset_class === 'BOND'`:
- `BondDetailsSection`: coupon rate, maturity date, YTM, credit rating, accrued interest (from `/api/bonds/{id}/accrued`), settlement date (from `PreviewResponse.settlement_date`), callable badge if applicable.
- Qty input: standard integer (face-value units, 1 = face_value par).

**`/bonds` workspace page** — four panels:
1. **Search** — CUSIP/ISIN/keyword input → results table (description, coupon, maturity, YTM, rating, broker).
2. **Positions** — held bond positions: market value, accrued interest, unrealised P&L.
3. **Detail panel** — selected bond: full `BondDetails` fields, price chart (klinecharts, `bond` quote source).
4. **Order history** — fills + open orders for bonds.

### 3.9 Chunk Breakdown (16a)

| Chunk | Content | Route |
|---|---|---|
| A | Alembic 0053: `BOND` enum, `BondDetails` meta arm, `bonds_accrued_interest` table | **Qwen** |
| B | `BondSearchService` + `get_accrued_interest` + APScheduler daily sweep | **Qwen** |
| C | Proto `SearchBonds` + `GetBondAccruedInterest` RPCs; `app/api/bonds.py`; `_check_bond_exposure` in `risk_service.py`; `PreviewResponse.settlement_date` field; sidecar `BOND→secType="BOND"` branch | **Codex** |
| D | FE: `services/bonds/types.ts` + `api.ts`; `BondDetailsSection`; TradeTicketModal BOND mode; `BondsPage.tsx` + `/bonds` route | **Codex** |
| E | Integration tests (search flow, accrued upsert, settling-past-maturity BLOCK, concentration WARN, settlement-date computation); Prometheus metric wiring | **Qwen** |

---

## 4. Phase 16b — Mutual Funds (v0.16.1)

### 4.1 Data Model

**Alembic 0054:**

- `MUTUAL_FUND` added to `instrument_asset_class` PG enum + Python `AssetClass` StrEnum.
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
    settlement_days: int = 1           # T+1 for most US funds; T+3 for some intl
    allows_fractional: bool = True
    cutoff_time_et: str                # e.g. "16:00" — order cut-off in ET
    expense_ratio: Decimal | None = None
    nav_currency: str                  # usually same as currency
```

- New **hypertable** `fund_nav_snapshots`:

```sql
CREATE TABLE fund_nav_snapshots (
    instrument_id  BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    nav            NUMERIC(20,8) NOT NULL,
    nav_date       DATE NOT NULL,
    source         TEXT NOT NULL DEFAULT 'ibkr',   -- 'ibkr' | 'schwab'
    captured_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
SELECT create_hypertable('fund_nav_snapshots', 'captured_at');
SELECT add_retention_policy('fund_nav_snapshots', INTERVAL '2 years');
CREATE UNIQUE INDEX fund_nav_snapshots_instrument_date_source_idx
    ON fund_nav_snapshots (instrument_id, nav_date, source);
```

Volume estimate: ~500 held funds × 1 row/day × 2 brokers = ~1000 rows/day. 2-year retention ≈ 730k rows — trivially small.

- `risk_limits` caps (row convention): `fund_max_notional_per_trade`, `fund_max_concentration_pct`.

### 4.2 Services

**`app/services/funds/fund_search_service.py`** (new):
- `search_funds(query, account_id, broker_id)` — ISIN/CUSIP/name search via proto `SearchFunds` RPC; upserts `instruments` rows with `MutualFundDetails` meta; Redis-caches 10 min.
- `resolve_fund_instrument(isin_or_cusip, broker_id)` — registry lookup with sidecar fallback.
- `get_current_nav(instrument_id, db)` — reads latest `fund_nav_snapshots` row; returns `None` if no snapshot yet.

**APScheduler job** — daily at 17:00 ET (after NAV publication): calls `GetFundNAV` RPC per held fund position across all accounts, upserts `fund_nav_snapshots`. Also refreshes `MutualFundDetails.expense_ratio` + `cutoff_time_et` if broker returns updated metadata.

### 4.3 Proto Additions

```protobuf
message FundSearchRequest {
  string account_id = 1;
  string query      = 2;   // ISIN, CUSIP, or fund name
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
  string nav             = 8;    // decimal string, latest known
  string nav_date        = 9;    // ISO8601 date
  string cutoff_time_et  = 10;
  string min_investment  = 11;   // decimal string
  string expense_ratio   = 12;   // decimal string, may be empty
  int32  settlement_days = 13;
}
message FundSearchResponse { repeated FundSearchResult results = 1; }

message GetFundNAVRequest {
  string account_id = 1;
  string conid      = 2;
}
message GetFundNAVResponse {
  string nav      = 1;   // decimal string
  string nav_date = 2;   // ISO8601 date
}

rpc SearchFunds(FundSearchRequest) returns (FundSearchResponse);
rpc GetFundNAV(GetFundNAVRequest) returns (GetFundNAVResponse);
```

Sidecar change: IBKR `secType="FUND"`, Schwab `assetType=MUTUAL_FUND` in order dispatch. ~5-line change per broker.

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

- **WARN** (not hard BLOCK): order time (ET) at or past `cutoff_time_et` → `fund_cutoff_passed` with `next_nav_date` = next business day. Broker queues for next-day NAV — this is not a risk event, it's informational. `PreviewResponse.next_nav_date` populated. Banner reads "Will execute at next-day NAV ([date])".
- **BLOCK:** `notional < MutualFundDetails.min_investment` (first purchase) or `< min_subsequent` (subsequent purchase) → `below_minimum_investment`. To distinguish: `EvaluationContext` gains a new optional field `existing_qty: Decimal | None = None` (16b Chunk C). `orders_service.preview_order` / `place_order` populate it by querying `positions` for this instrument_id + account_id before calling `evaluate()`. Gate uses `ctx.existing_qty is None or ctx.existing_qty == 0` for first-purchase logic.
- **BLOCK:** `notional > _resolve_limit(account_id, broker_id, "fund_max_notional_per_trade")` (if set) → `fund_notional_exceeded`
- **WARN:** single fund > `fund_max_concentration_pct` of `ctx.account_nlv_base` → `fund_concentration_warning`. Skipped silently if `ctx.account_nlv_base is None`.
- **WARN:** `fund_type == "CLOSED_END"` → `closed_end_fund_advisory` (closed-end funds trade like stocks; confirm intent)

### 4.6 NAV + Settlement at Preview Time

At `preview_order` time:
- `PreviewResponse.indicative_nav` = latest NAV from `fund_nav_snapshots` as decimal string; `None` if no snapshot.
- `PreviewResponse.settlement_date` = `trade_date + BusDayOffset(settlement_days)` (same helper as bonds).
- `PreviewResponse.next_nav_date` = next business day if past cut-off; `None` otherwise.

### 4.7 Prometheus Metrics (16b)

```
fund_search_requests_total{broker, outcome}
fund_nav_sweep_total{broker, outcome}
fund_nav_snapshots_stored_total{broker}
fund_risk_blocks_total{reason}
fund_risk_check_failures_total
fund_cutoff_warnings_total{broker}
```

### 4.8 Frontend

**`TradeTicketModal` injection** when `asset_class === 'MUTUAL_FUND'`:
- `FundDetailsSection`: fund family, type, current NAV (with date), expense ratio, cut-off time (amber badge if within 30 min of cut-off), min investment, settlement date.
- Qty input: `FractionalQtyInput` (reused from Phase 15 primitives, `decimals=3`) with units↔notional $ toggle. Notional input divides by current NAV to compute units client-side before submit.
- Next-day NAV WARN banner if `PreviewResponse.next_nav_date` is set.

**`/funds` workspace page** — four panels:
1. **Search** — ISIN/CUSIP/name → results table (name, family, NAV, date, expense ratio, cut-off, min investment).
2. **Positions** — held fund positions: units, cost basis, current NAV × units = market value, unrealised P&L.
3. **NAV history chart** — klinecharts line chart over `fund_nav_snapshots` (1m/3m/1y/all timeframes).
4. **Order history** — fills + open orders for funds.

### 4.9 Chunk Breakdown (16b)

| Chunk | Content | Route |
|---|---|---|
| A | Alembic 0054: `MUTUAL_FUND` enum, `MutualFundDetails` meta arm, `fund_nav_snapshots` hypertable + 2yr retention + unique index | **Qwen** |
| B | `FundSearchService` + `get_current_nav` + daily NAV sweep APScheduler job | **Qwen** |
| C | Proto `SearchFunds` + `GetFundNAV` RPCs; `app/api/funds.py`; `_check_fund_exposure` in `risk_service.py`; `PreviewResponse.indicative_nav` + `next_nav_date` fields; sidecar `MUTUAL_FUND→secType="FUND"` / Schwab `assetType=MUTUAL_FUND` branch | **Codex** |
| D | FE: `services/funds/types.ts` + `api.ts`; `FundDetailsSection`; units↔notional toggle; TradeTicketModal MUTUAL_FUND mode; `FundsPage.tsx` + `/funds` route + NAV history chart | **Codex** |
| E | Integration tests (search flow, NAV sweep upsert, cut-off WARN, min-investment BLOCK, next_nav_date population, NAV chart data); Prometheus metric wiring | **Qwen** |

---

## 5. Phase 16c — CFD (v0.16.2)

### 5.1 Data Model

**Alembic 0055:**

- `CFD` added to `instrument_asset_class` PG enum + Python `AssetClass` StrEnum.
- `country TEXT` column added to `broker_accounts` (ISO2, nullable, operator-populated). `NULL` = US-person check skipped (fail-OPEN). Default `NULL`.
- `CFDDetails` discriminated-union arm:

```python
class CFDDetails(BaseModel):
    asset_class: Literal["CFD"] = "CFD"
    underlying_type: str           # "equity" | "index" | "forex" | "commodity"
    underlying_symbol: str         # e.g. "BARC", "UK100", "EUR/USD", "GOLD"
    underlying_conid: str | None   # IBKR conid of the underlying instrument
    currency: str                  # margin + P&L currency
    tick_size: Decimal             # minimum price movement
    multiplier: Decimal            # contract multiplier (often 1 for equity CFDs)
    margin_rate: Decimal           # initial margin as fraction, e.g. 0.05 = 5%
    overnight_rate_long: Decimal   # daily financing rate for long positions
    overnight_rate_short: Decimal  # daily financing rate for short positions
    max_leverage: Decimal          # e.g. 20.0
    country: str | None = None     # ISO2, e.g. "GB" — for equity CFDs
    exchange: str = "IBCFD"        # IBKR CFD exchange
```

- No new table for overnight financing — rates live in `CFDDetails`. Actual broker-reported financing charges flow through the existing `orders`/`fills` pipeline.
- `risk_limits` caps (row convention): `cfd_max_notional_per_trade`, `cfd_max_leverage`, `cfd_max_concentration_pct`.

### 5.2 Services

**`app/services/cfd/cfd_search_service.py`** (new):
- `search_cfds(query, account_id, underlying_type)` — searches IBKR via proto `SearchCFDs` RPC; upserts `instruments` rows with `CFDDetails` meta; Redis-caches 10 min.
- `resolve_cfd_instrument(symbol, underlying_type, broker_id)` — registry lookup + sidecar fallback.
- `get_overnight_financing(instrument_id, qty, side, db)` — computes estimated daily financing cost: `abs(qty) × current_price × overnight_rate_long/short`. Display-only, not stored.

### 5.3 Proto Additions

```protobuf
message CFDSearchRequest {
  string account_id      = 1;
  string query           = 2;
  string underlying_type = 3;   // "equity"|"index"|"forex"|"commodity"|"" (all)
}
message CFDSearchResult {
  string conid                = 1;
  string symbol               = 2;
  string underlying_type      = 3;
  string underlying_symbol    = 4;
  string currency             = 5;
  string tick_size            = 6;
  string multiplier           = 7;
  string margin_rate          = 8;
  string overnight_rate_long  = 9;
  string overnight_rate_short = 10;
  string max_leverage         = 11;
  string country              = 12;
}
message CFDSearchResponse { repeated CFDSearchResult results = 1; }

rpc SearchCFDs(CFDSearchRequest) returns (CFDSearchResponse);
// PlaceOrder reuses existing RPC — asset_class=CFD routes to IBCFD exchange in sidecar.
```

Sidecar change: `_resolve_contract` fallback maps `asset_class="CFD"` → `secType="CFD"`, `exchange="IBCFD"`. ~5-line change.

### 5.4 REST API (`app/api/cfd.py`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/cfd/search` | JWT | `?q=&underlying_type=` — 20/min |
| GET | `/api/cfd/{instrument_id}` | JWT | detail + overnight financing estimate |
| GET | `/api/cfd/positions` | JWT | account-scoped CFD positions |
| GET | `/api/cfd/history` | JWT | fills + open orders, cursor pagination |

### 5.5 Risk Gate `_check_cfd_exposure`

Called when `ctx.asset_class == AssetClass.CFD`. Fail-OPEN on infrastructure errors; increments `cfd_risk_check_failures_total`.

- **BLOCK:** `broker_accounts.country == "US"` → `cfd_not_available_us`. If `country IS NULL`, skip silently (fail-OPEN). Increments `cfd_us_block_total` on BLOCK.
- **BLOCK:** implied leverage (`1 / CFDDetails.margin_rate`) > `min(_resolve_limit(..., "cfd_max_leverage"), CFDDetails.max_leverage)` → `cfd_leverage_exceeded`
- **BLOCK:** `notional > _resolve_limit(..., "cfd_max_notional_per_trade")` (if set) → `cfd_notional_exceeded`
- **BLOCK (forex CFD):** when `CFDDetails.underlying_type == "forex"`, delegates to `_check_forex_exposure(ctx)` for session check. If that returns BLOCK, propagate as-is.
- **WARN:** single CFD position > `cfd_max_concentration_pct` of `ctx.account_nlv_base` → `cfd_concentration_warning`. Skipped silently if `ctx.account_nlv_base is None`.
- **WARN:** `side == "BUY"` and `tif in ("GTC", "GTD")` (overnight hold implied) → `overnight_financing_advisory` with estimated daily cost from `get_overnight_financing()`.
- **WARN:** `underlying_type == "commodity"` → `commodity_cfd_advisory` (wide spread outside session hours).

### 5.6 Prometheus Metrics (16c)

```
cfd_search_requests_total{broker, underlying_type, outcome}
cfd_search_latency_seconds{underlying_type}
cfd_risk_blocks_total{reason}
cfd_risk_check_failures_total
cfd_overnight_advisory_total{underlying_type}
cfd_us_block_total
```

### 5.7 Frontend

**`TradeTicketModal` injection** when `asset_class === 'CFD'`:
- `CFDDetailsSection`: underlying type badge (equity/index/forex/commodity), underlying symbol, margin rate, max leverage, tick size, estimated overnight financing cost (client-side: `qty × price × overnight_rate × days`). Amber badge if GTC order with financing > 0.1% of notional/day.
- US-person BLOCK banner rendered prominently if risk gate returns `cfd_not_available_us`.
- Qty input: standard integer for equity/index CFDs; `FractionalQtyInput` for commodity CFDs where `tick_size < 1`.

**`/cfd` workspace page** — four panels:
1. **Search** — symbol/name search with underlying_type filter tabs (All / Equity / Index / Forex / Commodity). Results table: symbol, underlying, margin rate, overnight rates, max leverage.
2. **Positions** — open CFD positions: qty, entry price, current price, unrealised P&L, estimated daily financing charge.
3. **Detail panel** — selected CFD: full `CFDDetails` fields, price chart (klinecharts, CFD quote source from IBKR).
4. **Order history** — fills + open CFD orders.

### 5.8 Chunk Breakdown (16c)

| Chunk | Content | Route |
|---|---|---|
| A | Alembic 0055: `CFD` enum, `CFDDetails` meta arm, `broker_accounts.country` column | **Qwen** |
| B | `CFDSearchService`; proto `SearchCFDs` RPC; sidecar `CFD→secType="CFD"/exchange="IBCFD"` branch | **Codex** |
| C | `app/api/cfd.py`; `_check_cfd_exposure` in `risk_service.py` (incl. forex-CFD delegation + US-person check + leverage block) | **Codex** |
| D | FE: `services/cfd/types.ts` + `api.ts`; `CFDDetailsSection`; overnight financing estimate; TradeTicketModal CFD mode; `CFDPage.tsx` + `/cfd` route | **Codex** |
| E | Integration tests (search flow, US-person BLOCK, leverage BLOCK, forex-CFD session delegation, overnight advisory, commodity_cfd_advisory); Prometheus metric wiring | **Qwen** |

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
- Mutual fund exchange (fund-to-fund switch orders) — deferred post-16b; broker APIs vary significantly.
- Closed-end fund NAV discount/premium tracking — Phase 18+.
- CFD dividend adjustments — broker-reported; flows through fills pipeline automatically, no special handling needed in Phase 16.
- Commodity CFD storage/delivery risk (e.g. oil roll) — deferred to Phase 17 algos or beyond; Phase 16c only trades near-front delivery.
- OANDA as FX CFD data fallback — Phase 18+ (same as Phase 15 deferral).
- `bonds_accrued_interest` monthly retention policy — Phase 24 infra hardening.
- `broker_accounts.country` UI editor in `/admin/accounts` — Phase 16c ships the column; admin page edit deferred to Phase 17 or as a standalone patch.

---

## 8. Architect Review Findings Applied

*To be filled after architect review pass.*
