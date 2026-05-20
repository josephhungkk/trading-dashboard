# Phase 23 — UK CGT Awareness + Tax Tracking
**Version:** v0.23.0 (23a) → v0.23.1 (23b)
**Date:** 2026-05-20
**Status:** Design approved — architect review pass 1+2 applied

---

## Schema facts (read before implementing)

- `instruments.id` is **BIGINT** (BigInteger). All FKs referencing it must use `BIGINT`.
- `order_side_enum` is uppercase `('BUY','SELL')`. `fills.side` must be stored lowercase; backfill uses `LOWER(orders.side)`.
- `pgcrypto` extension already installed (migration 0002). No need to re-create.
- FX convention: `hmrc_fx_rates.rate_gbp` = **foreign currency units per £1** (e.g. USD ≈ 1.27). Conversion always `gbp = native_amount / fx_rate`. GBP → fx_rate=1.0. GBX → fx_rate=100.0.
- Golden FX tests: GBX 1234p → £12.34 (1234/100). USD $1000 at rate 1.27 → £787.40 (1000/1.27).
- b&b matching keys on **`cgt_class_key`**, not `instrument_id` (same share class across brokers/listings).

---

## 1. Scope & Goals

Build a complete UK Capital Gains Tax tracking system covering all asset classes traded on the dashboard. The system tracks every fill in real time, calculates S104 pool positions, matches disposals using HMRC's three-tier priority (same-day → 30-day b&b → S104 pool), and generates HMRC-compliant reports. Also tracks dividend and interest income events for SA100/SA106.

**Phase 23a (v0.23.0) — Foundation:**
- Schema + fill enrichment (Alembic 0072)
- In-house CGT engine (pool-track + derivative-track + short obligation ledger)
- `cgt_class_key` matching table for b&b rule
- HMRC monthly FX rate fetcher (APScheduler, cgt-calc pattern)
- IBKR Flex automated daily pull (trades + corp actions + dividends + interest)
- Pre-trade b&b gate (warn + acknowledge, fires on BUY after recent disposal)
- Live £3k allowance gauge + S104 pool REST + Tax page scaffold
- Prometheus metrics catalogue
- HMRC HS284 golden test fixtures

**Phase 23b (v0.23.1) — Surface + Import:**
- Schwab + Alpaca scheduled polls
- Futu CSV upload + universal CSV import
- Manual pool seed + admin CGT panel
- Full Tax page (all tabs)
- Detail report PDF + SA108 summary PDF + CSV export

**Non-goals for Phase 23:**
- HMRC online submission (manual filing from report)
- Multi-year CGT loss carry-forward automation (schema hook in 23a; manual entry)
- Stamp Duty Reserve Tax (SDRT) tracking
- Inheritance / gift disposals
- Employee share schemes (SAYE, EMI, SIP) — mark as `event_type='ess_acquisition'` with manual review flag if encountered in import
- Pre-1982 rebasing (TCGA92/S35) — out of scope; note in manual pool seed UI
- ISA/SIPP tax-wrapper exclusion — Phase 24 (`broker_accounts.is_tax_wrapper` flag)

---

## 2. HMRC Rules Reference

### 2.1 CGT tracks by asset class

Three tracks determined by `instruments.asset_class` and `instruments.meta`:

| Track | Asset classes | Mechanism |
|---|---|---|
| **pool** | STOCK, ETF, WARRANT, CBBC, FOREX, CRYPTO, BOND, MUTUAL_FUND | S104 pool + same-day + b&b + short obligation ledger |
| **pool** | OPTION where `meta->>'underlying_asset_class' != 'FUTURE'` | Same as above; exercise/assignment wires to underlying pool |
| **derivative** | FUTURE, CFD | Matched open/close cashflow pairs (TCGA92/S143) |
| **derivative** | OPTION where `meta->>'underlying_asset_class' = 'FUTURE'` | Options on futures follow derivative track |
| **exempt** | Any instrument where `meta->>'tax_exempt' = 'true'` (e.g. spread bets) | No CGT events generated; import logged to `broker_statements` only |
| **n/a** | INDEX | No CGT events — only used for quote routing |

Spread betting (UK spread bets are CGT-exempt — HMRC BIM22020): universal CSV import supports `tax_exempt=true` column; instruments tagged with `meta->>'tax_exempt'='true'` are skipped by the engine entirely.

### 2.2 Matching priority (pool-track, HMRC order)

Matching uses **`cgt_class_key`** (see §3.3), not raw `instrument_id`, to handle same-class shares across brokers and listings (e.g. ordinary shares re-acquired through different broker).

1. **Same-day rule** — disposal matched against acquisitions with the same `cgt_class_key` on the same **UK calendar date** (`uk_trade_date`). Acquisitions already consumed by earlier same-day matches are excluded.
2. **30-day b&b rule** — disposal matched against acquisitions of the same `cgt_class_key` in the 30 days *after* the disposal's `uk_trade_date` (FIFO within window). Acquisitions already consumed by same-day or prior b&b matches are excluded. Candidate set: `event_type='fill' AND side='buy' AND uk_trade_date BETWEEN disposal_date + 1 AND disposal_date + 30 AND bb_remaining_qty > 0`.
3. **S104 pool** — remainder matched at weighted average cost.

### 2.3 Short positions (pool-track)

Opening a short is **not a disposal** under HMRC rules (CG13350 — no beneficial ownership transfer). The S104 pool does not go negative. Short positions tracked in separate `short_obligations` ledger:
- Short open (side=sell, is_short_open=True) → INSERT `short_obligations(status='open')`, proceeds captured
- Short close (side=buy, is_short_close=True) → match against open obligation (FIFO), gain = open_proceeds − close_cost, INSERT `cgt_disposals(match_type='short')`

### 2.4 Short positions (derivative-track)

FUTURE/CFD shorts are the open leg of a matched pair. All cashflows (open, close, margin, settlement) sum to a single gain/loss per contract — `derivative_positions.side = 'short'`.

### 2.5 Crypto shorts

HMRC provides no guidance on crypto short selling. Treated conservatively as derivative cashflow track with `notes='crypto_short_hmrc_uncertain'` flag.

### 2.6 Corporate actions (pool adjustments)

| Event | Pool mutation | IBKR Flex code |
|---|---|---|
| Split | qty × ratio; total_cost_gbp unchanged | `FS` |
| Consolidation | qty / ratio; total_cost_gbp unchanged | `RS` |
| Scrip dividend | New acquisition at cash-equivalent; linked income_event | `SD` |
| Rights issue — subscribed | New acquisition at rights price | `RI` |
| Rights issue — nil-paid sold | Small disposal; proceeds <5% rule deducts from pool cost | `RS` (rights sale) |
| Rights issue — lapsed | Cost re-attributed to remaining pool | `RL` |
| Spin-off | Split total_cost_gbp by market value ratio; two pool mutations | `SO` |
| Takeover (share-for-share) | TCGA92/S135 rollover; cost basis carries into new instrument | `TC` / `MERGER` |
| Takeover (cash) | Disposal at cash proceeds | `CASHMERGER` |
| Takeover (mixed cash+share) | Partial disposal + partial rollover | `TC` + cash |
| B-share scheme | New acquisition (B-share) at apportioned cost | `BS` |
| Return of capital | Deduct from pool cost; excess = disposal | `RC` |
| Demerger | Pool cost split as spin-off | `DM` |
| Crypto hard fork | Split cost basis between old/new chain pro-rata to market value on fork date | n/a |
| Unknown / unhandled | `event_type='corp_action_unhandled'`; write raw payload to `tax_events.notes`; emit Telegram alert for manual review | any |

### 2.7 Option exercise / assignment / expiry

Options on equities follow pool-track but exercise/assignment requires cross-instrument events:

| Scenario | Engine action |
|---|---|
| Long call exercised | Close option pool at zero proceeds (premium lost); emit new pool-track tax_event for UNDERLYING with cost = option premium + strike × qty |
| Short call assigned | Close short-call obligation; emit disposal of UNDERLYING at strike + premium already received |
| Long put exercised | Close option pool at zero proceeds; emit disposal of UNDERLYING at strike − premium |
| Short put assigned | Emit acquisition of UNDERLYING at strike − premium received |
| Option expires worthless | Close pool at zero proceeds; loss = premium paid |

Hook: `ExerciseService` (Phase 12, `app/services/options/exercise_service.py`) emits `event_type='option_exercise'` / `'option_assignment'` / `'option_expiry'` into CGT engine.

### 2.8 Inter-spouse / connected-party transfers

TCGA92/S58: transfers between spouses/civil partners = no-gain/no-loss (cost basis carries over). Supported via:
- `event_type='transfer_out_s58'` — disposal at nil gain; `transfer_to_account_id` in notes
- `event_type='transfer_in_s58'` — acquisition at transferor's cost basis; `transfer_from_account_id` in notes

Both events must share a `transfer_group_id UUID` for reconciliation. Gate: operator must explicitly declare these via admin UI (not inferred from fills).

### 2.9 Dividends and interest (income tax, not CGT)

Tracked in `income_events` for SA100/SA106 reporting:
- UK cash dividend → income tax; £500 allowance
- Foreign cash dividend → income tax + foreign tax credit for withholding
- Scrip dividend → income tax on cash equivalent; linked `tax_event` acquisition at that same GBP value (TCGA92/S142, CG58750). Both rows share the same `fx_rate` snapshot — no drift permitted.
- DRIP → income event (gross dividend amount) + new S104 acquisition at gross dividend as cost basis (withholding NOT deductible from cost basis)
- Bond coupon / cash interest → savings income; Personal Savings Allowance applies
- Crypto staking → income tax at receipt FMV; CGT on later disposal (income_subtype='crypto')
- Crypto airdrop (for service) → income tax at receipt FMV; income_subtype='crypto'
- Crypto airdrop (unsolicited) → nil-cost CGT acquisition only; no income_event (event_type='pool_seed', price_gbp=0)
- Crypto hard fork → pool cost basis split; no income event (corp_action in §2.6)

### 2.10 FX rates

HMRC mandates GBP conversion at **transaction date** using a consistent, reasonable source (CG78310).

**Convention:** `hmrc_fx_rates.rate_gbp` = foreign currency units per £1. Conversion: `gbp = native_amount / fx_rate`. This is the single chokepoint — no other code computes GBP price.

**Rate sources (priority order):**
1. **HMRC monthly average rates** — auto-fetched from trade-tariff exchange rates API (this is the official HMRC monthly rates publication, not the customs tariff — confirmed by cgt-calc `exchange_rates.py` which uses the same endpoint from 2021 onwards). Pre-2021: legacy `hmrc.gov.uk/softwaredevelopers/rates/` endpoint.
2. **Previous month HMRC rate** (`fx_source='hmrc_monthly_prev_pending'`) — fallback until current month published. APScheduler re-resolves and recomputes on monthly rate publication.
3. **Manual override** (`fx_source='manual'`) — stored per tax_event; flagged in report.

Special cases:
- GBP → fx_rate=1.0, fx_source='none'
- GBX (UK pence) → fx_rate=100.0, fx_source='gbx_to_gbp' (1234p / 100 = £12.34)
- Crypto-to-crypto swap → price oracle (Coinbase WS quote at `executed_at` or CoinGecko historical); fx_source='crypto_spot_at_exec'

### 2.11 HMRC tax year

UK tax year: 6 April → 5 April. `tax_year` SMALLINT stores start year (e.g. 2025 = 6 Apr 2025 → 5 Apr 2026). Derived as generated column from `uk_trade_date`. Annual CGT exempt amount: £3,000 (2024/25 onwards).

---

## 3. Data Model (Alembic 0072)

First op: `op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")` — idempotent.

### 3.1 Enrich existing `fills` table

```sql
ALTER TABLE fills
  ADD COLUMN instrument_id BIGINT REFERENCES instruments(id) NOT NULL,
  ADD COLUMN side           VARCHAR(4) CHECK (side IN ('buy','sell')) NOT NULL,
  ADD COLUMN bot_id         UUID REFERENCES bots(id) NULLABLE;
```

Backfill:
```sql
UPDATE fills f
  SET instrument_id = o.instrument_id,
      side          = LOWER(o.side::text),
      bot_id        = bo.bot_id
FROM orders o
LEFT JOIN bot_orders bo ON bo.order_id = o.id
WHERE f.order_id = o.id;
```

### 3.2 New tables

```sql
-- HMRC monthly FX rates (auto-fetched; trade-tariff exchange rates API = HMRC official monthly rates)
CREATE TABLE hmrc_fx_rates (
  currency      VARCHAR(8)     NOT NULL,
  period_month  DATE           NOT NULL,   -- first day of month
  rate_gbp      NUMERIC(20,8)  NOT NULL,   -- foreign currency units per £1 (e.g. USD 1.27)
  source        VARCHAR(32)    NOT NULL DEFAULT 'hmrc_monthly',
  PRIMARY KEY (currency, period_month)
);

-- Raw broker statement blobs (never deleted — always re-parseable)
CREATE TABLE broker_statements (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_id      VARCHAR(32)    NOT NULL,
  account_id     UUID REFERENCES broker_accounts(id) NULLABLE,
    -- NULL when statement spans multiple accounts; per-event account extracted from body
  statement_type VARCHAR(32)    NOT NULL,
    -- 'flex_activity','flex_trade_confirm','schwab_tx',
    -- 'alpaca_activity','futu_csv','manual_csv'
  period_start   DATE           NOT NULL,
  period_end     DATE           NOT NULL,
  raw_content    BYTEA          NOT NULL,   -- gzip-compressed XML/JSON/CSV
  raw_format     VARCHAR(8)     NOT NULL DEFAULT 'gz_xml',  -- 'gz_xml','gz_json','gz_csv'
  raw_sha256     VARCHAR(64)    NOT NULL,   -- hex SHA-256 of raw_content for dedup
  fetched_at     TIMESTAMPTZ    NOT NULL DEFAULT now(),
  imported_at    TIMESTAMPTZ    NULLABLE,
  UNIQUE (broker_id, account_id, statement_type, period_start, period_end, raw_sha256)
);

-- cgt_class_key: maps instrument_id to a class key for b&b matching across brokers/listings
-- e.g. AAPL on IBKR + AAPL on Schwab → same cgt_class_key
CREATE TABLE cgt_class_links (
  instrument_id   BIGINT REFERENCES instruments(id) NOT NULL,
  cgt_class_key   VARCHAR(64)    NOT NULL,  -- e.g. 'US4592001014' (ISIN) or 'AAPL:USD'
  PRIMARY KEY (instrument_id),
  INDEX (cgt_class_key)
);

-- Loss carry-forward (manual entry; schema hook for Phase 23b reporting)
CREATE TABLE cgt_loss_carry_forward (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id   UUID REFERENCES broker_accounts(id) NOT NULL,
  tax_year     SMALLINT       NOT NULL,
  source_year  SMALLINT       NOT NULL,
  amount_gbp   NUMERIC(20,8)  NOT NULL,
  entered_at   TIMESTAMPTZ    NOT NULL DEFAULT now(),
  notes        TEXT           NULLABLE,
  UNIQUE (account_id, tax_year, source_year)
);

-- Single source of truth for CGT engine
CREATE TABLE tax_events (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fill_id             UUID REFERENCES fills(id) NULLABLE,
  leg_index           SMALLINT       NOT NULL DEFAULT 0,
    -- 0 = single-leg / primary; 1+ = additional legs (combo fills, delivery-settled futures)
  broker_statement_id UUID REFERENCES broker_statements(id) NULLABLE,
  external_event_id   VARCHAR(128)   NULLABLE,
    -- format: '{broker}:{statement_type}:{native_id}' for non-fill events
  source              VARCHAR(16)    NOT NULL CHECK (source IN
    ('fill_live','broker_statement','manual','corp_action')),
  account_id          UUID REFERENCES broker_accounts(id) NOT NULL,
  instrument_id       BIGINT REFERENCES instruments(id) NOT NULL,
  cgt_track           VARCHAR(12)    NOT NULL CHECK (cgt_track IN ('pool','derivative','exempt')),
  event_type          VARCHAR(32)    NOT NULL,
    -- 'fill','corp_action_split','corp_action_consolidation','corp_action_rights_subscribed',
    -- 'corp_action_rights_nil_paid','corp_action_rights_lapsed','corp_action_scrip',
    -- 'corp_action_spinoff','corp_action_takeover_share','corp_action_takeover_cash',
    -- 'corp_action_takeover_mixed','corp_action_demerger','corp_action_return_of_capital',
    -- 'corp_action_b_share','corp_action_hard_fork','corp_action_unhandled',
    -- 'option_exercise','option_assignment','option_expiry',
    -- 'transfer_in_s58','transfer_out_s58',
    -- 'pool_seed','ess_acquisition','manual'
  side                VARCHAR(4)     NOT NULL CHECK (side IN ('buy','sell')),
  is_short_open       BOOLEAN        NOT NULL DEFAULT FALSE,
  is_short_close      BOOLEAN        NOT NULL DEFAULT FALSE,
  qty                 NUMERIC(28,12) NOT NULL,
  price_gbp           NUMERIC(28,12) NOT NULL,   -- per-unit; gbp = native / fx_rate
  commission_native   NUMERIC(28,12) NOT NULL DEFAULT 0,
  commission_currency VARCHAR(8)     NOT NULL DEFAULT 'GBP',
  commission_gbp      NUMERIC(28,12) NOT NULL DEFAULT 0,
  fx_rate             NUMERIC(20,8)  NOT NULL,   -- foreign units per £1
  fx_source           VARCHAR(32)    NOT NULL,
    -- 'hmrc_monthly','hmrc_monthly_prev_pending','crypto_spot_at_exec','manual','none','gbx_to_gbp'
  original_currency   VARCHAR(8)     NOT NULL,
  cgt_class_key       VARCHAR(64)    NULLABLE,   -- denormalised from cgt_class_links at insert
  bb_remaining_qty    NUMERIC(28,12) NOT NULL DEFAULT 0,
    -- for acquisitions: qty available for b&b matching; decremented as matches consume it
  uk_trade_date       DATE GENERATED ALWAYS AS
    ((executed_at AT TIME ZONE 'Europe/London')::date) STORED,
  tax_year            SMALLINT GENERATED ALWAYS AS (
    CASE WHEN EXTRACT(MONTH FROM (executed_at AT TIME ZONE 'Europe/London')) > 4
      OR (EXTRACT(MONTH FROM (executed_at AT TIME ZONE 'Europe/London')) = 4
          AND EXTRACT(DAY FROM (executed_at AT TIME ZONE 'Europe/London')) >= 6)
    THEN EXTRACT(YEAR FROM (executed_at AT TIME ZONE 'Europe/London'))::SMALLINT
    ELSE (EXTRACT(YEAR FROM (executed_at AT TIME ZONE 'Europe/London')) - 1)::SMALLINT
    END
  ) STORED,
  executed_at         TIMESTAMPTZ    NOT NULL,
  bot_id              UUID REFERENCES bots(id) ON DELETE SET NULL NULLABLE,
  transfer_group_id   UUID           NULLABLE,  -- S58 transfer pairs
  notes               TEXT           NULLABLE,
  CONSTRAINT chk_short_flags CHECK (NOT (is_short_open AND is_short_close)),
  CONSTRAINT chk_fill_leg    UNIQUE (fill_id, event_type, leg_index),
  UNIQUE (account_id, external_event_id) -- partial; enforced via index below
);
CREATE UNIQUE INDEX tax_events_external_idx
  ON tax_events (account_id, external_event_id)
  WHERE external_event_id IS NOT NULL;
CREATE INDEX tax_events_pool_active_idx
  ON tax_events (account_id, cgt_class_key, uk_trade_date)
  WHERE cgt_track = 'pool';
CREATE INDEX tax_events_account_instrument_idx
  ON tax_events (account_id, instrument_id, executed_at);

-- S104 pool current state (long positions only; qty always >= 0)
CREATE TABLE s104_pool (
  account_id       UUID REFERENCES broker_accounts(id) NOT NULL,
  instrument_id    BIGINT REFERENCES instruments(id) NOT NULL,
  qty              NUMERIC(28,12) NOT NULL DEFAULT 0 CHECK (qty >= 0),
  total_cost_gbp   NUMERIC(28,12) NOT NULL DEFAULT 0,
  pool_avg_cost_gbp NUMERIC(28,12) GENERATED ALWAYS AS (
    CASE WHEN qty = 0 THEN 0 ELSE total_cost_gbp / qty END
  ) STORED,
  last_updated_at  TIMESTAMPTZ    NOT NULL,
  PRIMARY KEY (account_id, instrument_id)
);

-- Short obligation ledger (pool-track shorts, separate from S104)
CREATE TABLE short_obligations (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id          UUID REFERENCES broker_accounts(id) NOT NULL,
  instrument_id       BIGINT REFERENCES instruments(id) NOT NULL,
  open_tax_event_id   UUID REFERENCES tax_events(id) NOT NULL,
  close_tax_event_id  UUID REFERENCES tax_events(id) NULLABLE,
  open_qty            NUMERIC(28,12) NOT NULL,
  open_proceeds_gbp   NUMERIC(28,12) NOT NULL,
  close_qty           NUMERIC(28,12) NULLABLE,
  close_cost_gbp      NUMERIC(28,12) NULLABLE,
  gain_gbp            NUMERIC(28,12) NULLABLE,
  status              VARCHAR(8)     NOT NULL CHECK (status IN ('open','closed')),
  opened_at           TIMESTAMPTZ    NOT NULL,
  closed_at           TIMESTAMPTZ    NULLABLE
);

-- Derivative position ledger (FUTURE + CFD open/close pairs)
-- total_proceeds_gbp: sum of all inflows (open-short proceeds, close-long proceeds,
--   settlement receipts, variation margin credits).
-- total_cost_gbp: sum of all outflows (open-long cost, close-short cost, margin debits).
-- gain = total_proceeds_gbp − total_cost_gbp.
CREATE TABLE derivative_positions (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id          UUID REFERENCES broker_accounts(id) NOT NULL,
  instrument_id       BIGINT REFERENCES instruments(id) NOT NULL,
  open_tax_event_id   UUID REFERENCES tax_events(id) NOT NULL,
  close_tax_event_id  UUID REFERENCES tax_events(id) NULLABLE,
  side                VARCHAR(8)     NOT NULL CHECK (side IN ('long','short')),
  qty                 NUMERIC(28,12) NOT NULL,
  total_proceeds_gbp  NUMERIC(28,12) NOT NULL DEFAULT 0,
  total_cost_gbp      NUMERIC(28,12) NOT NULL DEFAULT 0,
  gain_gbp            NUMERIC(28,12) NULLABLE,
  status              VARCHAR(8)     NOT NULL CHECK (status IN ('open','closed')),
  opened_at           TIMESTAMPTZ    NOT NULL,
  closed_at           TIMESTAMPTZ    NULLABLE
);

-- Immutable pool event audit log
CREATE TABLE s104_pool_events (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id       UUID REFERENCES broker_accounts(id) NOT NULL,
  instrument_id    BIGINT REFERENCES instruments(id) NOT NULL,
  tax_event_id     UUID REFERENCES tax_events(id) NULLABLE,
  event_type       VARCHAR(32)    NOT NULL,
    -- 'acquisition','disposal','same_day_match','bb_match',
    -- 'short_open','corp_action','pool_seed','option_exercise'
  match_type       VARCHAR(16)    NULLABLE,   -- 'same_day','bb_30','s104'
  qty_delta        NUMERIC(28,12) NOT NULL,
  cost_delta_gbp   NUMERIC(28,12) NOT NULL,
  pool_qty_after   NUMERIC(28,12) NOT NULL,
  pool_cost_after  NUMERIC(28,12) NOT NULL,
  matched_event_id UUID REFERENCES tax_events(id) NULLABLE,
  gain_gbp         NUMERIC(28,12) NULLABLE,
  executed_at      TIMESTAMPTZ    NOT NULL
);
CREATE INDEX s104_pool_events_account_instrument_idx
  ON s104_pool_events (account_id, instrument_id, executed_at);

-- Pre-computed disposal records (report reads this directly)
CREATE TABLE cgt_disposals (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id            UUID REFERENCES broker_accounts(id) NOT NULL,
  instrument_id         BIGINT REFERENCES instruments(id) NOT NULL,
  disposal_tax_event_id UUID REFERENCES tax_events(id) NOT NULL,
  match_seq             SMALLINT       NOT NULL,  -- 0-based; multiple matches per disposal
  cgt_track             VARCHAR(12)    NOT NULL CHECK (cgt_track IN ('pool','derivative')),
  tax_year              SMALLINT       NOT NULL,
  disposal_date         DATE           NOT NULL,
  proceeds_gbp          NUMERIC(28,12) NOT NULL,
  allowable_cost_gbp    NUMERIC(28,12) NOT NULL,
  gain_gbp              NUMERIC(28,12) NOT NULL,   -- negative = loss
  match_type            VARCHAR(16)    NOT NULL,
    -- 'same_day','bb_30','s104','derivative','short'
  pool_event_id         UUID REFERENCES s104_pool_events(id) NULLABLE,
  short_obligation_id   UUID REFERENCES short_obligations(id) NULLABLE,
  derivative_id         UUID REFERENCES derivative_positions(id) NULLABLE,
  UNIQUE (disposal_tax_event_id, match_seq)
);
CREATE INDEX cgt_disposals_account_year_idx
  ON cgt_disposals (account_id, tax_year);

-- Income events (income tax, not CGT — SA100/SA106)
CREATE TABLE income_events (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id          UUID REFERENCES broker_accounts(id) NOT NULL,
  instrument_id       BIGINT REFERENCES instruments(id) NULLABLE,
  broker_statement_id UUID REFERENCES broker_statements(id) NULLABLE,
  external_event_id   VARCHAR(128)   NULLABLE,
  event_type          VARCHAR(32)    NOT NULL,
    -- 'dividend_cash','dividend_scrip','dividend_drip','airdrop_service',
    -- 'bond_coupon','cash_interest','staking'
  income_subtype      VARCHAR(16)    NOT NULL,   -- 'uk','foreign','crypto'
  gross_gbp           NUMERIC(28,12) NOT NULL,
  withholding_tax_gbp NUMERIC(28,12) NOT NULL DEFAULT 0,
  net_gbp             NUMERIC(28,12) NOT NULL,
  fx_rate             NUMERIC(20,8)  NOT NULL,
  fx_source           VARCHAR(32)    NOT NULL,
  original_currency   VARCHAR(8)     NOT NULL,
  tax_year            SMALLINT       NOT NULL,
  ex_date             DATE           NULLABLE,
  pay_date            DATE           NOT NULL,
  tax_event_id        UUID REFERENCES tax_events(id) NULLABLE,
    -- scrip/DRIP: links to the CGT acquisition; MUST share same fx_rate
  notes               TEXT           NULLABLE,
  CONSTRAINT chk_scrip_fx CHECK (
    -- enforced at application layer; belt-and-suspenders reminder
    event_type NOT IN ('dividend_scrip','dividend_drip') OR tax_event_id IS NOT NULL
  ),
  UNIQUE (account_id, external_event_id) -- partial; see index below
);
CREATE UNIQUE INDEX income_events_external_idx
  ON income_events (account_id, external_event_id)
  WHERE external_event_id IS NOT NULL;
CREATE INDEX income_events_account_year_idx
  ON income_events (account_id, tax_year);
```

### 3.3 `cgt_class_key` — b&b matching scope

The `cgt_class_key` bridges instruments that represent the same share class across different brokers or listings. Rules for key assignment:

1. **ISIN available** → key = ISIN (e.g. `US0378331005` for AAPL). Operator-asserted or extracted from IBKR Flex `isin` field.
2. **No ISIN** → key = `{ticker_root}:{currency}` (e.g. `AAPL:USD`). Ticker root strips exchange suffix.
3. **Crypto** → key = `{coin_symbol}` (e.g. `BTC`, `ETH`) regardless of exchange.
4. **Manual override** → operator asserts via `POST /api/admin/cgt/class-links`.

At `tax_event` insert time: `cgt_class_key` denormalised from `cgt_class_links` lookup. If no entry exists, key defaults to `instrument_id::text` (safe fallback — no cross-broker matching until operator links them).

---

## 4. CGT Engine

**Location:** `backend/app/services/cgt/`

```
cgt/
  __init__.py
  engine.py             # main entry point — dispatch by cgt_track; advisory lock
  pool_engine.py        # pool-track: S104 + same-day + b&b + short obligations
  derivative_engine.py  # derivative-track: FUTURE/CFD cashflow matching
  corporate.py          # corporate action pool adjustors
  fx.py                 # HMRC FX rate resolver (single chokepoint)
  hmrc_rates.py         # HMRC monthly rate fetcher + DB writer
  report.py             # trade-by-trade + SA108 summary report builder
  income_report.py      # dividend/interest income summary (SA100/SA106)
  types.py              # dataclasses: TaxEvent, PoolState, Disposal, IncomeEvent
  metrics.py            # Prometheus metrics (see §12)
```

### 4.1 engine.py — dispatch with advisory lock

```python
async def process(tax_event: TaxEvent, session: AsyncSession) -> None:
    # Per-(account_id, instrument_id) advisory lock prevents concurrent recompute races
    lock_key = hashtext(f"cgt:{tax_event.account_id}:{tax_event.instrument_id}")
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})

    if tax_event.cgt_track == "exempt":
        return  # no CGT processing; event recorded in tax_events for audit only
    elif tax_event.cgt_track == "pool":
        await pool_engine.process(tax_event, session)
    elif tax_event.cgt_track == "derivative":
        await derivative_engine.process(tax_event, session)

async def recompute(account_id: UUID, instrument_id: int, session: AsyncSession) -> None:
    # Advisory lock: same key as process() — blocks concurrent live fills
    lock_key = hashtext(f"cgt:{account_id}:{instrument_id}")
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})
    # Delete s104_pool_events + cgt_disposals + short_obligations(closed) + reset s104_pool
    # Replay all tax_events for (account_id, instrument_id) ordered by executed_at
    # Wrap in outer transaction with savepoint per event; abort outer on unrecoverable error
```

`cgt_track` resolved at `tax_event` insert from `instruments.asset_class` + `meta`:
- `exempt` → `meta->>'tax_exempt'='true'`
- `derivative` → FUTURE, CFD, or OPTION with `meta->>'underlying_asset_class'='FUTURE'`
- `pool` → all others

### 4.2 pool_engine.py

**Long acquisition:**
```
1. Check for pending same-day disposals of same cgt_class_key → apply same-day match if any
2. INSERT/UPDATE s104_pool (qty+, cost+)
3. INSERT s104_pool_events(event_type='acquisition')
4. SET bb_remaining_qty = qty (available for future b&b matches)
All in begin_nested() savepoint.
```

**Long disposal:**
```
1. Same-day: find acquisitions with same cgt_class_key + uk_trade_date, bb_remaining_qty > 0
   → match (FIFO), decrement bb_remaining_qty, write:
   s104_pool_events(match_type='same_day') + cgt_disposals(match_seq=0)
2. B&B: find acquisitions with same cgt_class_key in next 30 uk_trade_date days,
   bb_remaining_qty > 0, not already consumed
   → match (FIFO), decrement bb_remaining_qty, write:
   s104_pool_events(match_type='bb_30') + cgt_disposals(match_seq=1)
3. S104 remainder: proceeds − (pool_avg_cost_gbp × remaining_qty) → write
   s104_pool_events(match_type='s104') + cgt_disposals(match_seq=2)
   UPDATE s104_pool (qty−, cost− proportional)
All in begin_nested() savepoint.
```

**Short open (is_short_open=True, side='sell'):**
```
NOT added to s104_pool
INSERT short_obligations(status='open',
  open_proceeds_gbp = qty × price_gbp − commission_gbp)
INSERT s104_pool_events(event_type='short_open') for audit trail
```

**Short close (is_short_close=True, side='buy'):**
```
Load open short_obligations for account+instrument (FIFO by opened_at)
gain = open_proceeds_gbp − (qty × price_gbp + commission_gbp)
UPDATE short_obligations(status='closed', close_cost_gbp, gain_gbp)
INSERT cgt_disposals(match_type='short', cgt_track='pool', match_seq=0)
```

**Option exercise/assignment/expiry:**
```
event_type='option_exercise' | 'option_assignment':
  Close option pool entry (disposal at zero proceeds if exercising long)
  Emit new tax_event for UNDERLYING with correct cost basis (premium ± strike)
  Both writes in same savepoint — atomic

event_type='option_expiry':
  Close option pool at zero proceeds
  loss = premium paid (already in pool cost)
```

### 4.3 derivative_engine.py

```
Open leg (fill, side='buy' for long / 'sell' for short):
  INSERT derivative_positions(status='open', side='long'|'short')
  Accumulate to total_cost_gbp (long) or total_proceeds_gbp (short)

Close / settlement / variation margin cashflow:
  Load open derivative_positions for account+instrument (FIFO by opened_at)
  Add cashflow to total_proceeds_gbp (inflows) or total_cost_gbp (outflows)
  If closing event: mark status='closed'
  gain = total_proceeds_gbp − total_cost_gbp
  INSERT cgt_disposals(match_type='derivative', cgt_track='derivative', match_seq=0)

Delivery-settled futures (physical):
  On delivery, emit pool-track tax_event for UNDERLYING at futures contract price.
```

### 4.4 corporate.py

Full event-type handling per §2.6 table. Unhandled IBKR Flex corporate action codes → `event_type='corp_action_unhandled'`, raw payload in `notes`, Telegram alert to operator.

### 4.5 fx.py — rate resolution (single chokepoint)

```python
async def to_gbp(native_amount: Decimal, currency: str, executed_at: datetime,
                 session: AsyncSession) -> tuple[Decimal, Decimal, str]:
    """Returns (gbp_amount, fx_rate, fx_source). All callers MUST use this function."""
    if currency == 'GBP':
        return native_amount, Decimal('1'), 'none'
    if currency == 'GBX':
        return native_amount / 100, Decimal('100'), 'gbx_to_gbp'
    if currency in CRYPTO_TOKENS:
        rate = await _get_crypto_spot(currency, executed_at)
        return native_amount / rate, rate, 'crypto_spot_at_exec'

    month = executed_at.astimezone(ZoneInfo('Europe/London')).replace(day=1).date()
    row = await session.execute(
        text("SELECT rate_gbp FROM hmrc_fx_rates WHERE currency=:c AND period_month=:m"),
        {"c": currency, "m": month}
    )
    if rate := row.scalar_one_or_none():
        return native_amount / rate, rate, 'hmrc_monthly'

    # Fallback: previous month; mark for re-resolution
    prev_month = (month - timedelta(days=1)).replace(day=1)
    row = await session.execute(...)
    if rate := row.scalar_one_or_none():
        return native_amount / rate, rate, 'hmrc_monthly_prev_pending'

    raise FxRateNotFoundError(currency, month)
```

### 4.6 hmrc_rates.py — APScheduler job

`CronTrigger` first day of each month, 22:00 UTC (post-UK-close, after HMRC publishes):
```
# 2021+: trade-tariff exchange rates API (= HMRC official monthly rates)
GET https://www.trade-tariff.service.gov.uk/api/v2/exchange_rates/
    files/monthly_xml_{YYYY-MM}.xml
# Pre-2021 fallback:
GET http://www.hmrc.gov.uk/softwaredevelopers/rates/exrates-monthly-{MM}{YY}.xml

Parse XML → upsert hmrc_fx_rates for USD, HKD, EUR, JPY, CAD, AUD, CHF, CNH, CNY
After upsert: scan tax_events WHERE fx_source='hmrc_monthly_prev_pending'
  → re-resolve rates + trigger recompute queue for affected instruments
```

`coalesce=True, max_instances=1` on APScheduler job.

### 4.7 Pre-trade b&b gate

**Corrected direction (C6):** Gate fires on **BUY** orders (acquisition after recent disposal), not on SELL. This is when the b&b rule will match the incoming acquisition against an existing unmatched disposal.

Added to `risk_service.py` as `_check_bb_warning()`. Station number assigned from risk service registry constant — no magic decimals.

```python
async def _check_bb_warning(ctx: EvaluationContext) -> CheckResult:
    # Fires on BUY orders for pool-track instruments only
    # Check: is there an unmatched disposal of the same cgt_class_key in the
    # last 30 UK calendar days for this account?
    # If yes → WARN with acknowledgement_required=True
    # Message: "Acquiring {instrument} within 30 days of a disposal on {date}
    #           (qty: {n}, proceeds: £{x}). This acquisition will be b&b matched
    #           against that disposal under HMRC rules."
```

Trade ticket FE: `BbWarningBanner` requires checkbox acknowledgement before submit. Same UX as Phase 13 combo envelope confirmation.

### 4.8 Recompute queue

Corporate actions and pool seeds push to recompute queue:

```
RPUSH cgt:recompute_queue "{account_id}:{instrument_id}"
APScheduler IntervalTrigger every 5 min, coalesce=True, max_instances=1:
  BLPOP cgt:recompute_queue (timeout=4s)
  SELECT pg_advisory_xact_lock (per-instrument)
  engine.recompute()
  Outer transaction with begin_nested() per event; abort outer on unrecoverable error
```

---

## 5. Import Pipeline

**Location:** `backend/app/services/cgt/importers/`

```
importers/
  __init__.py
  ibkr_flex.py      # IBKR Flex XML parser (ibflex library)
  schwab.py         # Schwab transaction API poller
  alpaca.py         # Alpaca activities + corporate actions poller
  futu_csv.py       # Futu manual CSV parser
  universal_csv.py  # Generic canonical CSV import
  scheduler.py      # APScheduler job wiring (coalesce=True, max_instances=1 per job)
  normaliser.py     # broker-specific structs → tax_event / income_event
  reconciler.py     # reconcile live fills vs broker-statement fills (§5.6)
```

All APScheduler import jobs: `coalesce=True, max_instances=1` (prevents multi-worker duplicate pulls).

### 5.1 IBKR Flex — automated daily pull

**Setup:** operator stores `flex_token` + `flex_query_id` in `app_secrets`. One-time Flex Query template in IBKR Client Portal (Activity Statement, all sections).

**APScheduler CronTrigger — daily 22:00 UTC** (post-market, IBKR EOD processing complete):

```
1. SendRequest → reference_code
2. Poll GetStatement (max 5 attempts, 10s apart) → XML blob
3. gzip-compress blob → compute SHA-256
4. INSERT broker_statements ON CONFLICT (broker_id, account_id, statement_type,
   period_start, period_end, raw_sha256) DO NOTHING
   If conflict (already imported): skip to step 9
5. Parse via ibflex library:
   Trades           → tax_events (cgt_track from asset_class + meta)
   CorporateActions → tax_events(event_type='corp_action_*') + recompute queue
                      unhandled codes → 'corp_action_unhandled' + Telegram alert
   Dividends        → income_events('dividend_cash'|'dividend_scrip')
   Interest         → income_events('bond_coupon'|'cash_interest')
   WithholdingTax   → update matching income_event.withholding_tax_gbp
6. For each item:
   - Resolve instrument via symbol_aliases; extract ISIN for cgt_class_links
   - Resolve HMRC FX rate via fx.to_gbp()
   - external_event_id = 'ibkr:flex:{exec_id_or_action_id}'
   - INSERT ON CONFLICT (account_id, external_event_id) DO NOTHING (idempotent)
   - source = 'broker_statement'
7. engine.process() for each new tax_event
8. reconciler.reconcile() — match broker_statement fills vs live fills (§5.6)
9. broker_statements.imported_at = now()
```

### 5.2 Schwab — scheduled daily poll

**APScheduler CronTrigger — daily 22:30 UTC:**
```
GET /trader/v1/accounts/{accountHash}/transactions
    ?types=TRADE,DIVIDEND,INTEREST,CORPORATE_ACTION
    &startDate={last_imported_date}&endDate={today}
→ gzip + SHA-256 → INSERT broker_statements ON CONFLICT DO NOTHING
→ TRADE → tax_events (external_event_id='schwab:tx:{transactionId}')
→ DIVIDEND → income_events
→ CORPORATE_ACTION → tax_events(event_type='corp_action_*')
  split ratio inferred from share count delta; flag if ratio unclear
→ engine.process()
```

### 5.3 Alpaca — scheduled daily poll

**APScheduler CronTrigger — daily 22:30 UTC:**
```
GET /v2/account/activities?activity_types=FILL,DIV,SPLIT&page_size=100 (paginate)
GET /v2/corporate_actions/announcements (explicit ratio fields)
→ gzip + SHA-256 → INSERT broker_statements ON CONFLICT DO NOTHING
→ FILL → tax_events (external_event_id='alpaca:activity:{id}')
→ DIV  → income_events
→ SPLIT via announcements → tax_events(event_type='corp_action_split')
→ engine.process()
```

### 5.4 Futu — manual CSV upload

`POST /api/admin/cgt/import/futu-csv` (multipart):
```
Fills CSV columns: date, code, side, qty, price, currency, commission, exec_id
Corp actions CSV columns: date, code, event_type, ratio_or_amount, currency
Income CSV columns: date, code, event_type, gross, currency, withholding, pay_date

Commission missing → commission_gbp=0, notes='commission_missing_futu'
→ gzip + SHA-256 → INSERT broker_statements(statement_type='futu_csv')
→ normalise → tax_events / income_events
   external_event_id='futu:csv:{exec_id}'
→ engine.process()
```

### 5.5 Universal CSV import

`POST /api/admin/cgt/import/universal-csv` (multipart):

Canonical schema:
```
date, broker, account_id, symbol, asset_class, side, qty, price,
currency, commission, commission_currency, exec_id, tax_exempt, notes
```

`tax_exempt=true` → `cgt_track='exempt'`; engine skips processing.
`GET /api/admin/cgt/import/template` — downloadable blank CSV.

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
→ INSERT tax_events(event_type='pool_seed', source='manual') → INSERT s104_pool_events → UPDATE s104_pool

### 5.7 Reconciliation (live fills vs broker-statement fills)

`reconciler.reconcile()` run after each broker-statement import:

```
Match on: (broker_order_id, exec_id, executed_at ±60s, qty, side)
CGT engine prefers broker-statement value on discrepancy (more authoritative)
Discrepancy → UPDATE tax_event (source='broker_statement') + log warning
Orphaned broker-statement fill (no live fill match) → INSERT new tax_event (source='broker_statement')
Orphaned live fill (no broker-statement match within window) → flag for manual review
```

---

## 6. REST API

**Router:** `backend/app/api/cgt.py` — prefix `/api/cgt`

All endpoints must use explicit Pydantic v2 response models with `model_config = ConfigDict(extra='forbid')`. Boundary stripping: account fields follow `AccountResponse` convention (id, broker_id, alias, mode, currency_base, display_order only — no account_number, gateway_label). CI test asserts `account_number not in response.json()` for every CGT endpoint.

```
# Live state
GET  /api/cgt/summary                         # allowance gauge + YTD gain/loss + income totals
GET  /api/cgt/pool                            # all S104 pool positions + open positions section
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
POST /api/admin/cgt/class-links               # assert cgt_class_key for instrument(s)
```

**Pre-trade b&b gate** — wired into `POST /api/orders` via `risk_service._check_bb_warning()`. No new endpoint; existing `RiskResult` with `acknowledgement_required=True`.

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
    OpenPositionsPanel.tsx    # informational: open positions excluded from report
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

**AllowanceGauge:** live update via Redis pubsub `cgt:disposal:{account_id}` (same pattern as Phase 10b.2 WS gateway). On each new `cgt_disposals` insert, publish to channel; FE WS subscription re-fetches summary.

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
  CgtClassLinksPanel.tsx  # cgt_class_key management
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

Open positions (informational only — not included in gain/loss):
  MSFT 200 shares @ avg cost £285.40
```

### 8.2 SA108 summary

Maps to HMRC Self Assessment CGT supplementary pages (listed shares: boxes 23–32):
- Box 23: Proceeds (other assets — listed shares)
- Box 25: Allowable losses
- Box 26: Net gain after losses
- Box 27: Annual exempt amount used
- Box 28: Taxable gain

### 8.3 Income report

Separate section per income type (UK dividends, foreign dividends by country, bond coupons, cash interest, staking, airdrops) with gross/withholding/net columns. Maps to SA100 page 3 and SA106.

Output formats: PDF (WeasyPrint) + CSV.

---

## 9. Sub-phase Split

### Phase 23a — v0.23.0 (Foundation)

- Alembic 0072 (all tables + fills enrichment)
- `cgt/engine.py`, `pool_engine.py`, `derivative_engine.py`, `corporate.py`, `fx.py`, `hmrc_rates.py`, `types.py`, `metrics.py`
- IBKR Flex automated daily pull + full parser + reconciler
- HMRC FX rate APScheduler job (22:00 UTC)
- Pre-trade b&b gate (BUY side, cgt_class_key aware) + `BbWarningBanner` in TradeTicketModal
- REST: `/api/cgt/summary`, `/api/cgt/pool`, `/api/cgt/pool/{id}`, `/api/cgt/shorts`, `/api/cgt/derivatives`
- REST admin: pool-seed, recompute, fx-rates, ibkr-flex/trigger, statements, class-links
- Tax page scaffold + `AllowanceGauge` + `S104PoolTable` + `OpenPositionsPanel` + `TaxYearSelector`
- APScheduler: hmrc_rates monthly job, ibkr_flex daily job, recompute queue worker
- Prometheus metrics (§12)
- HMRC HS284 golden test fixtures (§11)

### Phase 23b — v0.23.1 (Surface + Import)

- Schwab + Alpaca scheduled polls
- Futu CSV upload + universal CSV import + template download
- `report.py` + `income_report.py` + WeasyPrint PDF generation
- Full Tax page: `DisposalsTable`, `IncomeTable`, `ShortsTable`, `DerivativesTable`, `ReportDownloadBar`
- REST: `/api/cgt/disposals`, `/api/cgt/income`, `/api/cgt/report/detail`, `/api/cgt/report/sa108`
- REST admin: import endpoints, statements list
- Admin CGT panel: full components
- Manual pool seed form + `CgtClassLinksPanel`

---

## 10. Key Invariants

- `s104_pool.qty` is always ≥ 0. Short positions live in `short_obligations`, never in the pool.
- All CGT engine writes use `session.begin_nested()` savepoints — atomic per event.
- `tax_events` is append-only. Corrections are made via new correcting event, not edits.
- `broker_statements.raw_content` (BYTEA, gzip-compressed) is never deleted — re-parsing must always be possible.
- HMRC monthly rate is the default FX source. `fx.to_gbp()` is the single conversion chokepoint.
- `recompute()` deletes and replays — idempotent; protected by advisory lock.
- All importers idempotent via `ON CONFLICT (account_id, external_event_id) DO NOTHING`.
- `broker_statements` deduped via SHA-256 UNIQUE — duplicate blob never inserted.
- All APScheduler import + recompute jobs: `coalesce=True, max_instances=1`.
- Crypto shorts → `cgt_track='derivative'`, `notes='crypto_short_hmrc_uncertain'`.
- Scrip dividend: `income_event` and linked `tax_event` MUST share same `fx_rate` value.
- `cgt_class_key` is the b&b matching key, not `instrument_id`.
- `cgt_disposals.cgt_track` CHECK IN ('pool','derivative') — 'short' is a `match_type`, not a track.
- Option exercise emits two atomic tax_events: option close + underlying acquisition/disposal.
- Unhandled corporate action codes → `corp_action_unhandled` + Telegram alert, never silently dropped.
- `broker_accounts.is_tax_wrapper` (ISA/SIPP exclusion) → Phase 24.

---

## 11. Test Plan

### 11.1 HMRC HS284 golden fixtures

Commit mock HMRC XML rate fixtures to `tests/fixtures/hmrc_rates/`. Reproduce HS284 worked examples as deterministic unit tests (freezegun for clock, fixture rates for FX):

| Test | Source | Expected gain |
|---|---|---|
| S104 pool average cost | HS284 Example 1 | verify avg cost calc |
| Same-day rule overrides S104 | HS284 Example 2 | same-day matched first |
| 30-day b&b overrides S104 | HS284 Example 3 | b&b matched before pool |
| Short sale gain/loss | manual (HMRC CG13350) | proceeds − close cost |
| GBX conversion | LSE pence trade | 1234p → £12.34 |
| FX conversion | USD trade | $1000 / 1.27 = £787.40 |
| UK trade date boundary | US fill at 23:55 UTC | correct next UK date |
| Tax year boundary | fill on 5 Apr / 6 Apr | correct tax_year |
| Scrip dividend pool cost | TCGA92/S142 | cost = cash equivalent |
| Corp action: split 2:1 | post-split pool | qty × 2, cost unchanged |
| B&B cross-broker match | same ISIN, different broker | matched via cgt_class_key |

### 11.2 Engine invariants

- `s104_pool.qty >= 0` asserted after every engine operation
- `recompute()` is idempotent: run twice → same `s104_pool`, `s104_pool_events`, `cgt_disposals`
- Advisory lock: concurrent `process()` + `recompute()` for same instrument produces correct result
- `ON CONFLICT DO NOTHING`: importing same Flex XML twice produces no duplicate events

---

## 12. Prometheus Metrics

All metric names must match this spec verbatim (backend/CLAUDE.md invariant):

```python
# Counters
cgt_engine_processed_total{cgt_track, event_type}
cgt_engine_failed_total{reason}
cgt_recompute_triggered_total{trigger}          # 'corp_action' | 'pool_seed' | 'manual'
cgt_disposal_inserted_total{match_type}
cgt_short_closed_total
cgt_importer_runs_total{broker, status}         # status: 'success'|'skipped'|'error'
cgt_importer_records_imported_total{broker, record_type}  # 'fill'|'income'|'corp_action'
cgt_hmrc_fx_fetch_total{status, period_month}
cgt_bb_gate_fires_total{outcome}                # 'warned'|'acknowledged'|'no_match'

# Gauges
cgt_recompute_queue_depth
cgt_short_obligation_open_count
cgt_hmrc_fx_rates_age_days                      # days since last successful fetch

# Histograms
cgt_engine_process_seconds{cgt_track}
cgt_recompute_seconds
cgt_importer_duration_seconds{broker}
```

---

## 13. Architectural Decision Records

- **ADR-23-1 — FX rate policy:** HMRC monthly average rates (trade-tariff exchange rates API for 2021+, legacy HMRC endpoint for pre-2021). Same source as cgt-calc. Operator may opt in to BoE daily spot via `app_config` (Phase 23b). All rates stored with source tag; report discloses methodology.
- **ADR-23-2 — cgt_class_key:** ISIN preferred; ticker:currency fallback; crypto by symbol. Operator-assertable via admin API. Default = `instrument_id::text` if no entry (safe, no cross-broker matching until asserted).
- **ADR-23-3 — Advisory lock:** `pg_advisory_xact_lock(hashtext('cgt:{account_id}:{instrument_id}'))` per engine operation. Released automatically on transaction commit/rollback. Prevents recompute + live-fill race without external coordination.
- **ADR-23-4 — broker_statements storage:** BYTEA gzip + SHA-256 dedup. Re-parsing always possible from stored blob. 5-year retention (HMRC requirement: tax year end + 5 years 10 months). Automated cleanup cron in Phase 24.
