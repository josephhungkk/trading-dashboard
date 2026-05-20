# Phase 23 — UK CGT Awareness + Tax Tracking
**Version:** v0.23.0 (23a) → v0.23.1 (23b)
**Date:** 2026-05-20
**Status:** Design approved

---

## 1. Scope & Goals

Build a complete UK Capital Gains Tax tracking system covering all asset classes traded on the dashboard. The system tracks every fill in real time, calculates S104 pool positions, matches disposals using HMRC's three-tier priority (same-day → 30-day b&b → S104 pool), and generates HMRC-compliant reports. Also tracks dividend and interest income events for SA100/SA106.

**Phase 23a (v0.23.0) — Foundation:**
- Schema + fill enrichment
- In-house CGT engine (pool-track + derivative-track + short obligation ledger)
- HMRC monthly FX rate fetcher
- IBKR Flex automated daily pull (trades + corp actions + dividends + interest)
- Pre-trade b&b gate (station 5.9, warn + acknowledge)
- Live £3k allowance gauge + S104 pool REST + Tax page scaffold

**Phase 23b (v0.23.1) — Surface + Import:**
- Schwab + Alpaca scheduled polls
- Futu CSV upload + universal CSV import
- Manual pool seed + admin CGT panel
- Full Tax page (all tabs)
- Detail report PDF + SA108 summary PDF + CSV export

**Non-goals for Phase 23:**
- HMRC online submission (manual filing from report)
- Multi-year CGT loss carry-forward automation (flagged in report, manual entry)
- Stamp Duty Reserve Tax (SDRT) tracking
- Inheritance / gift disposals

---

## 2. HMRC Rules Reference

### 2.1 CGT tracks by asset class

Two tracks determined by `instruments.asset_class`:

| Track | Asset classes | Mechanism |
|---|---|---|
| **pool** | STOCK, ETF, WARRANT, CBBC, OPTION, FOREX, CRYPTO, BOND, MUTUAL_FUND | S104 pool + same-day + b&b + short obligation ledger |
| **derivative** | FUTURE, CFD | Matched open/close cashflow pairs (TCGA92/S143) |
| **n/a** | INDEX | No CGT events — only used for quote routing |

### 2.2 Matching priority (pool-track, HMRC order)

1. **Same-day rule** — disposal matched against acquisitions on the same UK calendar date (midnight London time)
2. **30-day b&b rule** — disposal matched against acquisitions in the 30 days *after* the disposal date (FIFO within window)
3. **S104 pool** — remainder matched at weighted average cost

### 2.3 Short positions (pool-track)

Opening a short is **not a disposal** under HMRC rules (CG13350 — no beneficial ownership transfer). The S104 pool does not go negative. Short positions are tracked in a separate `short_obligations` ledger:
- Short open (side=sell, is_short_open=True) → INSERT `short_obligations(status='open')`, proceeds captured
- Short close (side=buy, is_short_close=True) → match against open obligation (FIFO), gain = open_proceeds − close_cost, INSERT `cgt_disposals(match_type='short')`

### 2.4 Short positions (derivative-track)

FUTURE/CFD shorts are the open leg of a matched pair. All cashflows (open, close, margin, settlement) sum to a single gain/loss per contract. No separate short ledger needed — `derivative_positions.side = 'short'`.

### 2.5 Crypto shorts

HMRC provides no guidance on crypto short selling. Treated conservatively as derivative cashflow track with `notes='crypto_short_hmrc_uncertain'` flag.

### 2.6 Corporate actions (pool adjustments)

| Event | Pool mutation |
|---|---|
| Split (e.g. 2:1) | qty × ratio; total_cost_gbp unchanged; avg cost halves |
| Consolidation | qty / ratio; total_cost_gbp unchanged; avg cost rises |
| Scrip dividend | New acquisition at cash-equivalent value; linked income_event |
| Rights issue | New acquisition at rights price |
| Spin-off | Split total_cost_gbp by market value ratio on ex-date; two pool mutations |

### 2.7 Dividends and interest (income tax, not CGT)

Tracked in `income_events` for SA100/SA106 reporting:
- UK cash dividend → income tax, £500 allowance
- Foreign cash dividend → income tax + foreign tax credit for withholding
- Scrip dividend → income tax on cash equivalent; linked `tax_event` acquisition at that cost (NOT nil — TCGA92/S142, CG58750)
- DRIP → income event + new S104 acquisition at dividend amount
- Bond coupon / cash interest → savings income, Personal Savings Allowance applies
- Crypto staking → income tax at receipt FMV; CGT on later disposal

### 2.8 FX rates

HMRC mandates GBP conversion at **transaction date** using a consistent, reasonable source (CG78310). The trade-tariff exchange rates are customs/import duty rates only — not applicable to CGT.

Approved sources used in this system (in priority order):
1. **HMRC monthly average rates** — auto-fetched, officially sanctioned, default
2. **Previous month HMRC rate** — fallback when current month not yet published
3. **Manual override** — stored per tax_event, fx_source='manual'

Special cases:
- GBP trades → fx_rate=1.0, fx_source='none'
- GBX (UK pence) trades → divide by 100, fx_source='gbx_to_gbp'

### 2.9 HMRC tax year

UK tax year runs 6 April → 5 April. `tax_year` column stores the start year integer (e.g. 2025 = 6 Apr 2025 → 5 Apr 2026). Annual CGT exempt amount: £3,000 (2024/25 onwards).

---

## 3. Data Model (Alembic 0072)

### 3.1 Enrich existing `fills` table

```sql
ALTER TABLE fills
  ADD COLUMN instrument_id INTEGER REFERENCES instruments(id) NOT NULL,
  ADD COLUMN side           VARCHAR(4) CHECK (side IN ('buy','sell')) NOT NULL,
  ADD COLUMN bot_id         UUID REFERENCES bots(id) NULLABLE;
```

Backfill via JOIN: `fills → orders (order_id) → instruments (instrument_id)`, `orders (side)`, `bot_orders (bot_id)`.

### 3.2 New tables

```sql
-- HMRC monthly FX rates (auto-fetched)
CREATE TABLE hmrc_fx_rates (
  currency      VARCHAR(8)     NOT NULL,
  period_month  DATE           NOT NULL,   -- first day of month
  rate_gbp      NUMERIC(20,8)  NOT NULL,   -- foreign currency units per £1
  source        VARCHAR(32)    NOT NULL DEFAULT 'hmrc_monthly',
  PRIMARY KEY (currency, period_month)
);

-- Raw broker statement blobs
CREATE TABLE broker_statements (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_id      VARCHAR(32)    NOT NULL,
  account_id     UUID REFERENCES broker_accounts(id) NULLABLE,
  statement_type VARCHAR(32)    NOT NULL,
    -- 'flex_activity','flex_trade_confirm','schwab_tx',
    -- 'alpaca_activity','futu_csv','manual_csv'
  period_start   DATE           NOT NULL,
  period_end     DATE           NOT NULL,
  raw_content    TEXT           NOT NULL,
  fetched_at     TIMESTAMPTZ    NOT NULL DEFAULT now(),
  imported_at    TIMESTAMPTZ    NULLABLE
);

-- Single source of truth for CGT engine
CREATE TABLE tax_events (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fill_id             UUID REFERENCES fills(id) UNIQUE NULLABLE,
  broker_statement_id UUID REFERENCES broker_statements(id) NULLABLE,
  account_id          UUID REFERENCES broker_accounts(id) NOT NULL,
  instrument_id       INTEGER REFERENCES instruments(id) NOT NULL,
  cgt_track           VARCHAR(12)    NOT NULL CHECK (cgt_track IN ('pool','derivative')),
  event_type          VARCHAR(32)    NOT NULL,
    -- 'fill','corp_action_split','corp_action_consolidation',
    -- 'corp_action_rights','corp_action_scrip','corp_action_spinoff',
    -- 'pool_seed','manual'
  side                VARCHAR(4)     NOT NULL CHECK (side IN ('buy','sell')),
  is_short_open       BOOLEAN        NOT NULL DEFAULT FALSE,
  is_short_close      BOOLEAN        NOT NULL DEFAULT FALSE,
  qty                 NUMERIC(20,8)  NOT NULL,
  price_gbp           NUMERIC(20,8)  NOT NULL,
  commission_gbp      NUMERIC(20,8)  NOT NULL DEFAULT 0,
  fx_rate             NUMERIC(20,8)  NOT NULL,
  fx_source           VARCHAR(32)    NOT NULL,
    -- 'hmrc_monthly','hmrc_monthly_prev','ibkr_flex','manual','none','gbx_to_gbp'
  original_currency   VARCHAR(8)     NOT NULL,
  tax_year            SMALLINT       NOT NULL,
  executed_at         TIMESTAMPTZ    NOT NULL,
  bot_id              UUID           NULLABLE,
  notes               TEXT           NULLABLE,
  CONSTRAINT chk_short_flags CHECK (NOT (is_short_open AND is_short_close))
);
CREATE INDEX tax_events_account_instrument_idx
  ON tax_events (account_id, instrument_id, executed_at);

-- S104 pool current state (long positions only; never negative)
CREATE TABLE s104_pool (
  account_id      UUID REFERENCES broker_accounts(id) NOT NULL,
  instrument_id   INTEGER REFERENCES instruments(id) NOT NULL,
  qty             NUMERIC(20,8)  NOT NULL DEFAULT 0 CHECK (qty >= 0),
  total_cost_gbp  NUMERIC(20,8)  NOT NULL DEFAULT 0,
  last_updated_at TIMESTAMPTZ    NOT NULL,
  PRIMARY KEY (account_id, instrument_id)
);

-- Short obligation ledger (pool-track shorts, separate from S104)
CREATE TABLE short_obligations (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id          UUID REFERENCES broker_accounts(id) NOT NULL,
  instrument_id       INTEGER REFERENCES instruments(id) NOT NULL,
  open_tax_event_id   UUID REFERENCES tax_events(id) NOT NULL,
  close_tax_event_id  UUID REFERENCES tax_events(id) NULLABLE,
  open_qty            NUMERIC(20,8)  NOT NULL,
  open_proceeds_gbp   NUMERIC(20,8)  NOT NULL,
  close_qty           NUMERIC(20,8)  NULLABLE,
  close_cost_gbp      NUMERIC(20,8)  NULLABLE,
  gain_gbp            NUMERIC(20,8)  NULLABLE,
  status              VARCHAR(8)     NOT NULL CHECK (status IN ('open','closed')),
  opened_at           TIMESTAMPTZ    NOT NULL,
  closed_at           TIMESTAMPTZ    NULLABLE
);

-- Derivative position ledger (FUTURE + CFD open/close pairs)
CREATE TABLE derivative_positions (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id          UUID REFERENCES broker_accounts(id) NOT NULL,
  instrument_id       INTEGER REFERENCES instruments(id) NOT NULL,
  open_tax_event_id   UUID REFERENCES tax_events(id) NOT NULL,
  close_tax_event_id  UUID REFERENCES tax_events(id) NULLABLE,
  side                VARCHAR(8)     NOT NULL CHECK (side IN ('long','short')),
  qty                 NUMERIC(20,8)  NOT NULL,
  total_proceeds_gbp  NUMERIC(20,8)  NOT NULL DEFAULT 0,
  total_cost_gbp      NUMERIC(20,8)  NOT NULL DEFAULT 0,
  gain_gbp            NUMERIC(20,8)  NULLABLE,
  status              VARCHAR(8)     NOT NULL CHECK (status IN ('open','closed')),
  opened_at           TIMESTAMPTZ    NOT NULL,
  closed_at           TIMESTAMPTZ    NULLABLE
);

-- Immutable pool event audit log
CREATE TABLE s104_pool_events (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id       UUID REFERENCES broker_accounts(id) NOT NULL,
  instrument_id    INTEGER REFERENCES instruments(id) NOT NULL,
  tax_event_id     UUID REFERENCES tax_events(id) NULLABLE,
  event_type       VARCHAR(32)    NOT NULL,
    -- 'acquisition','disposal','same_day_match','bb_match',
    -- 'short_open','corp_action','pool_seed'
  match_type       VARCHAR(16)    NULLABLE,   -- 'same_day','bb_30','s104'
  qty_delta        NUMERIC(20,8)  NOT NULL,
  cost_delta_gbp   NUMERIC(20,8)  NOT NULL,
  pool_qty_after   NUMERIC(20,8)  NOT NULL,
  pool_cost_after  NUMERIC(20,8)  NOT NULL,
  matched_event_id UUID REFERENCES tax_events(id) NULLABLE,
  gain_gbp         NUMERIC(20,8)  NULLABLE,
  executed_at      TIMESTAMPTZ    NOT NULL
);
CREATE INDEX s104_pool_events_account_instrument_idx
  ON s104_pool_events (account_id, instrument_id, executed_at);

-- Pre-computed disposal records (report reads this table directly)
CREATE TABLE cgt_disposals (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id          UUID REFERENCES broker_accounts(id) NOT NULL,
  instrument_id       INTEGER REFERENCES instruments(id) NOT NULL,
  cgt_track           VARCHAR(12)    NOT NULL,  -- 'pool','derivative','short'
  tax_year            SMALLINT       NOT NULL,
  disposal_date       DATE           NOT NULL,
  proceeds_gbp        NUMERIC(20,8)  NOT NULL,
  allowable_cost_gbp  NUMERIC(20,8)  NOT NULL,
  gain_gbp            NUMERIC(20,8)  NOT NULL,   -- negative = loss
  match_type          VARCHAR(16)    NOT NULL,
    -- 'same_day','bb_30','s104','derivative','short'
  pool_event_id       UUID REFERENCES s104_pool_events(id) NULLABLE,
  short_obligation_id UUID REFERENCES short_obligations(id) NULLABLE,
  derivative_id       UUID REFERENCES derivative_positions(id) NULLABLE
);
CREATE INDEX cgt_disposals_account_year_idx
  ON cgt_disposals (account_id, tax_year);

-- Income events (income tax, not CGT — SA100/SA106)
CREATE TABLE income_events (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id          UUID REFERENCES broker_accounts(id) NOT NULL,
  instrument_id       INTEGER REFERENCES instruments(id) NULLABLE,
  broker_statement_id UUID REFERENCES broker_statements(id) NULLABLE,
  event_type          VARCHAR(32)    NOT NULL,
    -- 'dividend_cash','dividend_scrip','dividend_drip',
    -- 'bond_coupon','cash_interest','staking'
  income_subtype      VARCHAR(16)    NOT NULL,   -- 'uk','foreign','crypto'
  gross_gbp           NUMERIC(20,8)  NOT NULL,
  withholding_tax_gbp NUMERIC(20,8)  NOT NULL DEFAULT 0,
  net_gbp             NUMERIC(20,8)  NOT NULL,
  fx_rate             NUMERIC(20,8)  NOT NULL,
  fx_source           VARCHAR(32)    NOT NULL,
  original_currency   VARCHAR(8)     NOT NULL,
  tax_year            SMALLINT       NOT NULL,
  ex_date             DATE           NULLABLE,
  pay_date            DATE           NOT NULL,
  tax_event_id        UUID REFERENCES tax_events(id) NULLABLE,
  notes               TEXT           NULLABLE
);
CREATE INDEX income_events_account_year_idx
  ON income_events (account_id, tax_year);
```

---

## 4. CGT Engine

**Location:** `backend/app/services/cgt/`

```
cgt/
  __init__.py
  engine.py             # main entry point — dispatch by cgt_track
  pool_engine.py        # pool-track: S104 + same-day + b&b + short obligations
  derivative_engine.py  # derivative-track: FUTURE/CFD cashflow matching
  corporate.py          # corporate action pool adjustors
  fx.py                 # HMRC FX rate resolver
  hmrc_rates.py         # HMRC monthly rate fetcher + DB writer
  report.py             # trade-by-trade + SA108 summary report builder
  income_report.py      # dividend/interest income summary (SA100/SA106)
  types.py              # dataclasses: TaxEvent, PoolState, Disposal, IncomeEvent
```

### 4.1 engine.py — dispatch

```python
async def process(tax_event: TaxEvent, session: AsyncSession) -> None:
    if tax_event.cgt_track == "pool":
        await pool_engine.process(tax_event, session)
    elif tax_event.cgt_track == "derivative":
        await derivative_engine.process(tax_event, session)

async def recompute(account_id: UUID, instrument_id: int, session: AsyncSession) -> None:
    # Delete s104_pool_events + cgt_disposals + reset s104_pool
    # Replay all tax_events ordered by executed_at
    # Used after: corp action import, pool seed, historical import
```

`cgt_track` resolved at `tax_event` insert time from `instruments.asset_class`:
- `pool` → STOCK, ETF, WARRANT, CBBC, OPTION, FOREX, CRYPTO, BOND, MUTUAL_FUND
- `derivative` → FUTURE, CFD

### 4.2 pool_engine.py

**Long acquisition:**
```
1. Check for pending same-day disposals → apply same-day match if any
2. INSERT/UPDATE s104_pool (qty+, cost+)
3. INSERT s104_pool_events(event_type='acquisition')
All in begin_nested() savepoint.
```

**Long disposal:**
```
1. Same-day: find acquisitions on same UK calendar date → match, write
   s104_pool_events(match_type='same_day') + cgt_disposals
2. B&B: find acquisitions within next 30 days (FIFO) → match, write
   s104_pool_events(match_type='bb_30') + cgt_disposals
3. S104 remainder: proceeds − (pool_avg_cost × qty) → write
   s104_pool_events(match_type='s104') + cgt_disposals
   UPDATE s104_pool (qty−, cost− proportional)
All in begin_nested() savepoint.
```

**Short open (is_short_open=True, side='sell'):**
```
NOT added to s104_pool
INSERT short_obligations(status='open', open_proceeds_gbp=qty×price_gbp−commission)
INSERT s104_pool_events(event_type='short_open') for audit trail
```

**Short close (is_short_close=True, side='buy'):**
```
Load open short_obligations for account+instrument (FIFO)
gain = open_proceeds_gbp − (qty×price_gbp + commission)
UPDATE short_obligations(status='closed', close_cost_gbp, gain_gbp)
INSERT cgt_disposals(match_type='short', cgt_track='short')
```

### 4.3 derivative_engine.py

```
Open leg:
  INSERT derivative_positions(status='open', side='long'|'short')

Close / settlement / cash margin:
  Load open derivative_positions for account+instrument (FIFO)
  Accumulate all cashflows
  gain = total_proceeds_gbp − total_cost_gbp
  UPDATE derivative_positions(status='closed', gain_gbp)
  INSERT cgt_disposals(match_type='derivative', cgt_track='derivative')

Delivery-settled futures:
  On physical delivery, emit pool-track tax_event for underlying at futures price.
```

### 4.4 corporate.py

| Event | Action |
|---|---|
| Split | qty × ratio; total_cost_gbp unchanged |
| Consolidation | qty / ratio; total_cost_gbp unchanged |
| Scrip dividend | New pool acquisition at cash-equivalent value; INSERT income_events linked via tax_event_id |
| Rights issue | New pool acquisition at rights price |
| Spin-off | Split total_cost_gbp by market value ratio; two pool mutations |

### 4.5 fx.py — rate resolution order

1. GBP trade → fx_rate=1.0, fx_source='none'
2. GBX (pence) → fx_rate=100.0, fx_source='gbx_to_gbp'
3. Foreign currency → lookup `hmrc_fx_rates(currency, month of executed_at)`
4. Fallback → previous month's rate, fx_source='hmrc_monthly_prev'
5. Manual override → stored on tax_event, fx_source='manual'

### 4.6 hmrc_rates.py — APScheduler job

`CronTrigger` first day of each month, 06:00 UTC:
```
GET https://www.trade-tariff.service.gov.uk/api/v2/exchange_rates/
    files/monthly_xml_{YYYY-MM}.xml
Parse XML → upsert hmrc_fx_rates for USD, HKD, EUR, JPY, CAD, AUD, CHF, CNH
```
Same pattern as cgt-calc's rate fetcher (`cgt_calc/exchange_rates.py`).

### 4.7 Pre-trade b&b gate — station 5.9

Added to `risk_service.py` after existing station 5.75 (exposure gate):

```python
async def _check_bb_warning(ctx: TradeContext) -> RiskResult:
    # Fires on SELL orders for pool-track instruments only
    # Case A: disposal — look ahead 30 days for existing acquisition of same instrument
    # Case B: disposal — check if this disposal would be b&b matched against a
    #         prior acquisition (i.e. there was an acquisition in last 30 days)
    # If match found → return WARN with acknowledgement_required=True
    # Message: "This disposal may be matched under the 30-day b&b rule.
    #           Matched acquisition: {date}, qty: {n}, effective cost: £{x}"
```

Trade ticket FE: `BbWarningBanner` requires checkbox acknowledgement before submit. Same UX as Phase 13 combo envelope confirmation.

### 4.8 Recompute queue

Corporate actions and pool seeds trigger full `engine.recompute()` for affected `(account_id, instrument_id)`:

```
On corp action detected → RPUSH cgt:recompute_queue "{account_id}:{instrument_id}"
APScheduler IntervalTrigger every 5 min → LPOP queue → engine.recompute()
```

---

## 5. Import Pipeline

**Location:** `backend/app/services/cgt/importers/`

```
importers/
  __init__.py
  ibkr_flex.py      # IBKR Flex XML parser
  schwab.py         # Schwab transaction API poller
  alpaca.py         # Alpaca activities + corporate actions poller
  futu_csv.py       # Futu manual CSV parser
  universal_csv.py  # Generic canonical CSV import
  scheduler.py      # APScheduler job wiring
  normaliser.py     # broker-specific structs → tax_event / income_event
```

### 5.1 IBKR Flex — automated daily pull

**Setup:** operator stores `flex_token` + `flex_query_id` in `app_secrets`. One-time Flex Query template creation in IBKR Client Portal (Activity Statement, all sections).

**APScheduler CronTrigger — daily 07:00 UTC:**

```
1. SendRequest → reference_code
2. Poll GetStatement (max 5 attempts, 10s apart) → XML blob
3. INSERT broker_statements(statement_type='flex_activity', raw_content=xml)
4. Parse via ibflex library:
   Trades         → tax_events (cgt_track from asset_class)
   CorporateActions → tax_events(event_type='corp_action_*') + recompute queue
   Dividends      → income_events('dividend_cash'|'dividend_scrip')
   Interest       → income_events('bond_coupon'|'cash_interest')
   WithholdingTax → update matching income_event.withholding_tax_gbp
5. Resolve instrument via symbol_aliases
6. Resolve HMRC FX rate via fx.py
7. INSERT ON CONFLICT exec_id DO NOTHING (idempotent)
8. engine.process() for each new tax_event
9. broker_statements.imported_at = now()
```

**`ibflex`** Python library used for XML parsing.

### 5.2 Schwab — scheduled daily poll

**APScheduler CronTrigger — daily 08:00 UTC:**
```
GET /trader/v1/accounts/{accountHash}/transactions
    ?types=TRADE,DIVIDEND,INTEREST,CORPORATE_ACTION
    &startDate={last_imported_date}&endDate={today}
→ INSERT broker_statements(raw JSON)
→ TRADE → tax_events
→ DIVIDEND → income_events
→ CORPORATE_ACTION → tax_events(event_type='corp_action_*')
  NOTE: split ratio inferred from share count delta (no explicit ratio field in Schwab API)
→ engine.process()
```

### 5.3 Alpaca — scheduled daily poll

**APScheduler CronTrigger — daily 08:00 UTC:**
```
GET /v2/account/activities?activity_types=FILL,DIV,SPLIT&page_size=100 (paginate)
GET /v2/corporate_actions/announcements (explicit ratio fields)
→ INSERT broker_statements(raw JSON)
→ FILL → tax_events
→ DIV  → income_events
→ SPLIT via announcements → tax_events(event_type='corp_action_split')
→ engine.process()
```

### 5.4 Futu — manual CSV upload

No programmatic API available for corporate actions or statements.

`POST /api/admin/cgt/import/futu-csv` (multipart):
```
Expected columns: date, code, side, qty, price, currency, commission, exec_id
Corporate actions: separate CSV: date, code, event_type, ratio/amount
Commission missing → stored as 0, notes='commission_missing_futu'
→ INSERT broker_statements(statement_type='futu_csv')
→ normalise → tax_events / income_events
→ engine.process()
```

### 5.5 Universal CSV import

`POST /api/admin/cgt/import/universal-csv` (multipart):

Canonical schema:
```
date, broker, account_id, symbol, asset_class, side, qty, price,
currency, commission, commission_currency, exec_id, notes
```

`GET /api/admin/cgt/import/template` — downloadable blank CSV with headers + example row.

### 5.6 Manual pool seed

`POST /api/admin/cgt/pool-seed`:
```json
{
  "account_id": "...",
  "instrument_id": 123,
  "as_of_date": "2020-04-05",
  "qty": 500,
  "total_cost_gbp": "12500.00",
  "notes": "Opening position pre-dashboard"
}
```
→ INSERT tax_events(event_type='pool_seed') → INSERT s104_pool_events → UPDATE s104_pool

---

## 6. REST API

**Router:** `backend/app/api/cgt.py` — prefix `/api/cgt`

```
# Live state
GET  /api/cgt/summary                         # allowance gauge + YTD gain/loss + income totals
GET  /api/cgt/pool                            # all S104 pool positions
GET  /api/cgt/pool/{instrument_id}            # single instrument pool detail + event history
GET  /api/cgt/shorts                          # open short obligations
GET  /api/cgt/derivatives                     # open derivative positions

# Tax year data
GET  /api/cgt/disposals?tax_year=2025         # cgt_disposals for tax year
GET  /api/cgt/income?tax_year=2025            # income_events for tax year

# Reports (Phase 23b)
GET  /api/cgt/report/detail?tax_year=2025     # trade-by-trade PDF + CSV
GET  /api/cgt/report/sa108?tax_year=2025      # SA108-style summary PDF + CSV

# Admin — import
POST /api/admin/cgt/import/futu-csv           # multipart CSV
POST /api/admin/cgt/import/universal-csv      # multipart CSV
GET  /api/admin/cgt/import/template           # download canonical CSV template
POST /api/admin/cgt/import/ibkr-flex/trigger  # manual Flex pull trigger
GET  /api/admin/cgt/statements                # broker_statements list + import status

# Admin — pool management
POST /api/admin/cgt/pool-seed                 # manual S104 opening balance
POST /api/admin/cgt/recompute                 # trigger recompute {account_id, instrument_id}
GET  /api/admin/cgt/fx-rates                  # list hmrc_fx_rates
POST /api/admin/cgt/fx-rates/refresh          # manual HMRC rate fetch trigger
```

**Pre-trade b&b gate** — wired into existing `POST /api/orders` via `risk_service._check_bb_warning()` at station 5.9. No new endpoint; existing `RiskResult` shape with `acknowledgement_required=True`.

---

## 7. Frontend

**New route:** `/tax`
**Feature directory:** `frontend/src/features/tax/`

```
tax/
  pages/
    TaxPage.tsx
  components/
    AllowanceGauge.tsx       # live £3k CGT allowance gauge (donut/progress)
    S104PoolTable.tsx         # instrument, qty, avg cost GBP, unrealised gain
    DisposalsTable.tsx        # cgt_disposals for selected tax year
    IncomeTable.tsx           # income_events (dividends, interest)
    ShortsTable.tsx           # open short obligations
    DerivativesTable.tsx      # open derivative positions
    TaxYearSelector.tsx       # dropdown: 2022/23 … 2025/26
    ReportDownloadBar.tsx     # PDF + CSV download buttons (Phase 23b)
    BbWarningBanner.tsx       # b&b acknowledgement banner (also in TradeTicketModal)
  hooks/
    useCgtSummary.ts
    useS104Pool.ts
    useDisposals.ts
    useIncomeEvents.ts
```

**Tax page layout:**
```
┌─────────────────────────────────────────────────────┐
│ Tax                              Tax year: [2025/26] │
├──────────────┬──────────────────────────────────────┤
│ CGT Allowance│  Used: £1,240 / £3,000               │
│   gauge      │  Net gain: £1,240   Net loss: £0      │
│              │  Remaining: £1,760                    │
├──────────────┴──────────────────────────────────────┤
│ [S104 Pool] [Disposals] [Income] [Shorts] [Futures] │
├─────────────────────────────────────────────────────┤
│ (tab content)                                       │
├─────────────────────────────────────────────────────┤
│ [Download Detail PDF] [Download SA108 PDF] [CSV]    │
└─────────────────────────────────────────────────────┘
```

**Admin additions** — new tab in `/admin`:
```
features/admin/cgt/
  CgtImportPanel.tsx      # Futu CSV + universal CSV upload + IBKR Flex trigger
  CgtStatementsTable.tsx  # broker_statements: fetched_at, imported_at, status
  CgtPoolSeedForm.tsx     # manual S104 opening balance form
  CgtFxRatesPanel.tsx     # hmrc_fx_rates table + manual refresh
  CgtRecomputePanel.tsx   # trigger recompute per instrument
```

---

## 8. Report Output

### 8.1 Detail report (trade-by-trade, mirrors cgt-calc style)

```
Date        Instrument  Qty   Proceeds £   Cost £      Gain £     Match
2025-07-14  AAPL        100   8,240.00     6,100.00    2,140.00   S104
2025-08-02  TSLA         50   3,100.00     3,400.00     -300.00   B&B (matched: 2025-08-20)
2025-09-10  BTC           0.5 12,000.00    9,500.00    2,500.00   Same-day
────────────────────────────────────────────────────────────────────────
Total gains                                            24,100.00
Total losses                                           -1,800.00
Net gain                                               22,300.00
Annual exempt amount                                    3,000.00
Taxable gain                                           19,300.00
```

### 8.2 SA108 summary

Maps directly to HMRC Self Assessment CGT supplementary pages:
- Box 3: Proceeds from other assets
- Box 4: Allowable losses
- Box 5: Net gain after losses
- Box 6: Annual exempt amount used
- Box 7: Taxable gain

### 8.3 Income report

Separate section per income type (UK dividends, foreign dividends by country, bond coupons, cash interest, staking) with gross/withholding/net columns. Maps to SA100 page 3 and SA106.

Output formats: PDF (WeasyPrint) + CSV.

---

## 9. Sub-phase Split

### Phase 23a — v0.23.0 (Foundation)

- Alembic 0072 (all tables + fills enrichment)
- `cgt/engine.py`, `pool_engine.py`, `derivative_engine.py`, `corporate.py`, `fx.py`, `hmrc_rates.py`, `types.py`
- IBKR Flex automated daily pull + full parser
- HMRC FX rate APScheduler job
- Pre-trade b&b gate (station 5.9) + `BbWarningBanner` in TradeTicketModal
- REST: `/api/cgt/summary`, `/api/cgt/pool`, `/api/cgt/pool/{id}`, `/api/cgt/shorts`, `/api/cgt/derivatives`
- REST admin: pool-seed, recompute, fx-rates, ibkr-flex/trigger, statements
- Tax page scaffold + `AllowanceGauge` + `S104PoolTable` + `TaxYearSelector`
- APScheduler: hmrc_rates monthly job, ibkr_flex daily job, recompute queue worker

### Phase 23b — v0.23.1 (Surface + Import)

- Schwab + Alpaca scheduled polls
- Futu CSV upload + universal CSV import + template download
- `report.py` + `income_report.py` + WeasyPrint PDF generation
- Full Tax page: `DisposalsTable`, `IncomeTable`, `ShortsTable`, `DerivativesTable`, `ReportDownloadBar`
- REST: `/api/cgt/disposals`, `/api/cgt/income`, `/api/cgt/report/detail`, `/api/cgt/report/sa108`
- REST admin: import endpoints, statements list
- Admin CGT panel: `CgtImportPanel`, `CgtStatementsTable`, `CgtPoolSeedForm`, `CgtFxRatesPanel`, `CgtRecomputePanel`

---

## 10. Key Invariants

- `s104_pool.qty` is always ≥ 0. Short positions live in `short_obligations`, never in the pool.
- All CGT engine writes use `session.begin_nested()` savepoints — atomic per event.
- `tax_events` is append-only. Corrections are made via a new correcting event, not edits.
- `broker_statements.raw_content` is never deleted — re-parsing must always be possible.
- HMRC monthly rate is the default FX source. Manual overrides are flagged in the report.
- `recompute()` deletes and replays — idempotent; safe to call multiple times.
- `exec_id` uniqueness enforced via `ON CONFLICT DO NOTHING` — all importers are idempotent.
- Crypto shorts flagged with `notes='crypto_short_hmrc_uncertain'` pending future HMRC guidance.
- Scrip dividend cost basis = cash equivalent (TCGA92/S142), not nil.
