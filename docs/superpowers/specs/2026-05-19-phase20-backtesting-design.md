# Phase 20 — Backtesting Harness Design

**Date:** 2026-05-19  
**Version target:** v0.20.0  
**Status:** Approved, ready for implementation planning

---

## 1. Overview

Phase 20 adds a backtesting harness that replays historical OHLCV bars through existing `BaseStrategy` plugin code, simulates fills with configurable slippage and per-broker commission, and produces a standard performance report (PnL curve, drawdown, Sharpe, MAR, trade list).

Walk-forward and Monte Carlo analysis are deferred to Phase 21 (LLM-in-loop parameter tuning), which is their natural consumer.

### 1.1 Asset-class scope

Phase 20 supports **STOCK, ETF, FUTURE, OPTION (single-leg), CRYPTO** — any instrument with OHLCV bars.

FOREX (IDEALPRO RFQ, no canonical bars), BOND, MUTUAL_FUND, CFD, and multi-leg COMBOS are out of scope. The submit endpoint returns `422 asset_class_not_backtestable` when the resolved instrument's `asset_class` is unsupported. This matches the OHLCV-only fill model (§5.5).

**Known limitation — corporate actions:** Phase 20 does NOT adjust for splits or dividend events. `bars_1m` stores raw OHLCV from broker feeds and is not split-adjusted. Backtests spanning corporate actions (stock splits, special dividends) will produce misleading results — the simulator treats a post-split price halving as a genuine -50% move. Users must either choose date ranges that avoid corporate actions, or upload split-adjusted CSV bars via the `upload-bars` endpoint. The FE shows an amber warning on the config form when the selected date range exceeds 6 months for STOCK/ETF asset classes. Phase 21 may add adjustment factors.

---

## 2. Decisions

| Dimension | Decision |
|---|---|
| Result storage | Async job pattern — dedicated `backtests` table, own endpoints |
| Execution model | Dedicated `backtest_worker` Docker service (same pattern as `bot_worker`) |
| Data source | DB + broker backfill + CSV upload |
| Report scope | Standard report only: PnL curve, drawdown, Sharpe, MAR, trade list |
| Fill simulation | Next-bar open + configurable slippage (fixed bps OR % of ATR) |
| Commission | Per-broker schedule read from `app_config` namespace `backtest/commission` |
| UI | Standalone `/bots/$botId/backtest` page |
| Progress | Streaming via `WS /ws/bots/{bot_id}/backtest/{job_id}` (Redis pubsub) |
| Architecture | Fat worker owns full pipeline; backend is thin pass-through |
| Async model | Worker runs under `asyncio.run`; `on_bar` awaited if coroutine (§5.3) |
| Worker concurrency | `MAX_CONCURRENT_BACKTESTS=2` via asyncio.Semaphore (§5.1) |

---

## 3. Data Model

### 3.1 Alembic migration `0062_phase20_backtests.py`

```sql
CREATE TABLE backtests (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id             UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    status             TEXT NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued','running','done','failed')),
    timeframe          TEXT NOT NULL,          -- '1m','5m','15m','1h','1d'
    canonical_id       TEXT NOT NULL,          -- single instrument per run
    start_date         DATE NOT NULL,
    end_date           DATE NOT NULL,
    slippage_bps       NUMERIC(8,2),           -- NULL when atr_pct used
    slippage_atr_pct   NUMERIC(8,4),           -- NULL when bps used
    -- enforces mutual exclusivity at DB layer (CRIT-3 / HIGH-5)
    CONSTRAINT backtests_slippage_xor CHECK (
        (slippage_bps IS NOT NULL AND slippage_atr_pct IS NULL) OR
        (slippage_bps IS NULL AND slippage_atr_pct IS NOT NULL)
    ),
    commission_cfg     JSONB NOT NULL,         -- broker schedule snapshot at submit time (schema in §5.7)
    params_snapshot    JSONB NOT NULL,         -- strategy params frozen at submit time
    bars_source        TEXT NOT NULL CHECK (bars_source IN ('db','backfill','csv')),
    parent_backtest_id UUID REFERENCES backtests(id) ON DELETE CASCADE,  -- for Phase 21 walk-forward groups
    params_schema_hash TEXT,                   -- SHA-256 of params_schema at submit; worker re-checks at pickup (MED-4)
    progress_pct       SMALLINT NOT NULL DEFAULT 0,
    error_msg          TEXT,
    report             JSONB,                  -- written atomically on completion
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at         TIMESTAMPTZ,            -- set when worker picks up job; NULL while queued (HIGH-1')
    completed_at       TIMESTAMPTZ
);

CREATE INDEX ix_backtests_bot_id_created ON backtests(bot_id, created_at DESC);  -- covers LIST cursor-paginated query (LOW-2)
CREATE INDEX ix_backtests_parent_id      ON backtests(parent_backtest_id) WHERE parent_backtest_id IS NOT NULL;
CREATE INDEX ix_backtests_running_stale  ON backtests(started_at) WHERE status = 'running';  -- orphan sweep (HIGH-1')

-- CSV upload metadata; actual bar rows live in backtest_bars (CRIT-2)
CREATE TABLE backtest_bar_uploads (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_id   TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    bar_count      INTEGER NOT NULL,
    uploaded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Lookup index for the 24h CSV-validity check at submit
CREATE INDEX ix_bbu_canonical_tf_uploaded ON backtest_bar_uploads(canonical_id, timeframe, uploaded_at DESC);

-- Isolated bar store for CSV uploads; never pollutes bars_1m (CRIT-2)
CREATE TABLE backtest_bars (
    upload_id      UUID NOT NULL REFERENCES backtest_bar_uploads(id) ON DELETE CASCADE,
    instrument_id  BIGINT NOT NULL REFERENCES instruments(id),
    bucket_start   TIMESTAMPTZ NOT NULL,
    open           NUMERIC(20,8) NOT NULL,
    high           NUMERIC(20,8) NOT NULL,
    low            NUMERIC(20,8) NOT NULL,
    close          NUMERIC(20,8) NOT NULL,
    volume         NUMERIC(20,8),              -- nullable: CSV rows missing volume are accepted; bars_1m.volume is also nullable (LOW-1)
    PRIMARY KEY (upload_id, instrument_id, bucket_start)
);
CREATE INDEX ix_backtest_bars_instrument ON backtest_bars(instrument_id, bucket_start);
```

### 3.2 `report` JSONB schema

```jsonc
{
  "sharpe":               1.42,    // annualised; computed on daily-bucketed PnL (see §5.6)
  "mar":                  0.87,    // CAGR / abs(max_drawdown_pct)
  "max_drawdown_pct":     12.3,    // non-negative, percentage points (12.3 = 12.3% peak-to-trough)
  "total_return_pct":     34.1,
  "total_trades":         47,
  "win_rate":             0.61,    // closed winning trades / total closed trades
  "avg_trade_pnl":        142.30,  // net of commission + slippage (both fills)
  "forced_close_pnl":     -18.50,  // aggregate PnL from forced end-of-range closes; account base currency, net of slippage+commission, signed (positive=profit, negative=loss); 0 when no forced closes
  "pnl_curve":   [[iso_ts, cumulative_pnl_base], ...],  // one point per bar
  "drawdown_curve": [[iso_ts, drawdown_pct], ...],       // non-negative; one point per bar
  "trades": [
    {
      "canonical_id":  "AAPL",
      "side":          "BUY",
      "qty":           100,
      "entry_price":   182.50,
      "exit_price":    191.20,
      "entry_slippage": 0.09,   // adverse slippage on entry fill
      "exit_slippage":  0.09,   // adverse slippage on exit fill
      "commission":    1.00,    // total for both fills
      "pnl":           866.91,  // net of commission + both slippages
      "forced_close":  false,   // true if closed because end_date reached
      "opened_at":     "2024-03-15T09:30:00Z",
      "closed_at":     "2024-03-22T14:00:00Z"
    }
  ]
}
```

### 3.3 CSV bar upload format

Required columns (case-insensitive headers):
```
timestamp, open, high, low, close, volume
```
- `timestamp`: ISO 8601 or Unix epoch ms
- Numeric columns: decimal, no currency symbols
- `canonical_id` and `timeframe` provided as query params on the upload endpoint
- Rows are stored in `backtest_bars` keyed by `upload_id` — **never written into `bars_1m`** (preserves production bar integrity)
- `BarFeed` merges `backtest_bars` rows with `bars_1m` in-memory during replay (§5.3 step 3)

---

## 4. Backend API

### 4.1 Router: `app/api/backtests.py`

Mounted at `/api/bots/{bot_id}/backtests`. All endpoints require JWT.

**Access control:** All endpoints scope by `bots.jwt_subject = current_jwt_subject`. Both "no such bot" AND "bot owned by another subject" return **404**, never 403 (existence-oracle defence — same pattern as Phase 11a AI jobs). The same applies to backtest job IDs under another subject's bot.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/bots/{bot_id}/backtests` | Submit job — validate params, INSERT status=queued, RPUSH `backtest:queue`, return `{job_id}` 202 |
| `GET` | `/api/bots/{bot_id}/backtests` | List backtests (id, status, timeframe, canonical_id, start_date, end_date, progress_pct, created_at, completed_at) — cursor paginated by created_at DESC |
| `GET` | `/api/bots/{bot_id}/backtests/{job_id}` | Fetch single backtest including full `report` when done; 404 if not owned |
| `DELETE` | `/api/bots/{bot_id}/backtests/{job_id}` | Cancel queued/running (SET `backtest:cancel:{job_id}` EX 3600; worker polls this key each cadence cycle — see §5.3); hard-delete done/failed rows. If row has children (`parent_backtest_id` FK), returns 409 with `{"children": N}` — caller must pass `?cascade=true` to confirm deletion of child rows (MED-1) |
| `POST` | `/api/bots/{bot_id}/backtests/upload-bars` | CSV upload — multipart/form-data; `canonical_id` + `timeframe` query params; inserts into `backtest_bars`; returns `{upload_id, canonical_id, bar_count}` |

**Submit request body (`BacktestSubmitRequest`):**
```jsonc
{
  "canonical_id":     "AAPL",         // single instrument per backtest run; asset_class validated
  "timeframe":        "1d",
  "start_date":       "2024-01-01",
  "end_date":         "2025-01-01",
  "slippage_bps":     5.0,            // mutually exclusive with slippage_atr_pct (DB XOR CHECK)
  "slippage_atr_pct": null,
  "bars_source":      "backfill"      // db | backfill | csv
}
```

**Validation rules:**
- `end_date > start_date` required
- `canonical_id` resolved to instrument; if `asset_class` not in `{STOCK, ETF, FUTURE, OPTION, CRYPTO}` → 422 `asset_class_not_backtestable`
- `slippage_bps` / `slippage_atr_pct` mutually exclusive; at least one non-null (0.0 = zero slippage, valid)
- `bars_source=csv` requires a `backtest_bar_uploads` row for matching `canonical_id` + `timeframe` with `uploaded_at >= now() - 24h`; otherwise 422
- `bot_id` must exist and be owned by `jwt_subject` (404 otherwise)
- Validate `params_snapshot` (current bot params) against current strategy `params_schema` via `sandbox.py` at submit time; return 422 with field-level errors if missing required keys — fail fast before queuing
- Compute `params_schema_hash = SHA-256(json.dumps(params_schema, sort_keys=True))` at submit; store in `backtests.params_schema_hash`; worker re-extracts schema at pickup and compares — mismatch fails with `error_code=params_schema_drift` (MED-4)
- Commission snapshot captured at submit: read all keys from `app_config[backtest/commission]` + derive `active_broker_id` from `bot_accounts`; store as `commission_cfg` (schema in §5.7)

### 4.2 WS endpoint: `app/api/ws_backtests.py`

```
WS /ws/bots/{bot_id}/backtest/{job_id}
```

- Authenticates via JWT (same CSWSH origin check as existing WS endpoints)
- **404 not 403** if job not owned by `jwt_subject` (close with 1008 policy violation — indistinguishable from "not found")
- **Per-`jwt_subject` cap: 10 concurrent connections**; global ceiling: 100. Two Redis counters: `backtest:ws:count:{jwt_subject}` (per-user) and `backtest:ws:count:global` (global). Both incremented on accept, decremented on disconnect. Caps are approximate under concurrent accepts (no Lua script); `backtest_ws_cap_rejections_total{scope=jwt|global}` metric counts rejections (MED-2)
- Subscribes to `backtest:progress:{job_id}` Redis psubscribe
- Worker publishes progress frames every `max(1, total_bars // 200)` bars (MED-3):
  ```jsonc
  {"type": "progress", "pct": 67, "trades_so_far": 23, "current_bar_ts": "2024-09-14T00:00:00Z"}
  ```
- On completion worker publishes:
  ```jsonc
  {"type": "done", "report": {...}}
  ```
- On failure:
  ```jsonc
  {"type": "failed", "error_msg": "StrategyError: ..."}
  ```
- **WS gateway pattern** (same as portfolio-rollup WS):
  - 2s `asyncio.wait_for` send timeout
  - recv-drain task for disconnect detection
  - Coalesce successive progress frames within 100ms window (send latest, drop older)
  - Heartbeat every 30s emits `{"type": "heartbeat"}`
  - v=1 frame schema
- Server closes connection after `done` / `failed` frame

---

## 5. Backtest Worker

### 5.1 Docker service

New service `backtest_worker` in `docker-compose.yml` — same pattern as `bot_worker` (see `phase19_shipped.md` for the bot_worker analogue):
- Separate `Dockerfile` (same base image as `bot_worker`)
- Own DB connection pool (max 5 connections)
- Own Redis connection
- Shares `strategies/` volume read-only
- Entrypoint: `python -m app.backtest.worker_main`
- **Job discovery (atomic, at-least-once):** Submit endpoint uses `RPUSH backtest:queue` (push to tail — FIFO). Worker uses `BLMOVE backtest:queue backtest:pending:{worker_id} LEFT RIGHT 0` — atomically pops from the head of the main queue and pushes to the tail of the worker's pending list in one Redis round-trip (no race window between pop and visibility). On completion, worker does `LREM backtest:pending:{worker_id} 1 {job_id}`. Orphan sweep re-queues via `RPUSH backtest:queue` (retries go to tail, same as new submits) (HIGH-2').
- **Orphan sweep:** on startup and every 60s, query `backtests WHERE status='running' AND started_at < now() - interval '5 minutes'` using `ix_backtests_running_stale`; re-queue each via `RPUSH backtest:queue`, UPDATE status=queued, started_at=NULL. Increment `backtest_orphans_recovered_total`. Mirrors `orphan_sweeper.py` from Phase 11a.
- **Concurrency:** `MAX_CONCURRENT_BACKTESTS=2` controlled by `asyncio.Semaphore`. Env var `BACKTEST_WORKER_CONCURRENCY` overrides. Prometheus gauge `backtest_workers_active` tracks in-flight count.

### 5.2 Module layout: `app/backtest/`

```
app/backtest/
  __init__.py
  worker_main.py       # lifespan: BLPOP backtest:queue, orphan sweep, dispatch runner
  runner.py            # BacktestRunner — top-level orchestrator
  bar_feed.py          # BarFeed — DB + backfill + backtest_bars merge
  fill_simulator.py    # FillSimulator — pending order queue, next-bar-open fill, TIF semantics
  metrics.py           # MetricsComputer — Sharpe (daily-bucketed), MAR, drawdown, trade stats
  commission.py        # CommissionSchedule — reads commission_cfg snapshot
  context.py           # BacktestContext — async duck-typed BotContext for replay
  progress.py          # ProgressPublisher — Redis pubsub frames, dynamic cadence
```

### 5.3 Pipeline: `BacktestRunner.run(backtest_id)`

The entire pipeline runs under `asyncio.run(self._replay(backtest_id))`. `on_bar` is awaited if it is a coroutine (`asyncio.iscoroutinefunction`), allowing strategies written with `async def on_bar` to run unmodified in both live and backtest contexts. This is the simplest approach — no strategy migration required (CRIT-3).

```
1.  Load backtests row from DB; UPDATE status=running, started_at=now()
2.  Validate strategy module via sandbox.py DenylistFinder (same check as bot-create)
    Re-extract params_schema; SHA-256 hash it; compare with backtests.params_schema_hash
    → mismatch: fail with error_msg='params_schema_drift'; publish failed frame; return
3.  BarFeed.load(canonical_id, timeframe, start_date, end_date, bars_source)
      → query bars_1m / CAGG for date range
      → if bars_source=backfill: trigger bar_service backfill for missing ranges
      → if bars_source=csv: load matching backtest_bars rows by canonical_id+timeframe
        (resolved to instrument_id). Merge semantics:
          1. `backtest_bars` upload must match the requested `timeframe` exactly;
             mismatched timeframe raises `BarFeedError` caught at submit-time validation.
          2. Per `(instrument_id, bucket_start)`, `backtest_bars` row replaces the DB row.
          3. DB buckets not in `backtest_bars` are kept as-is (CSV may be partial).
          4. `backtest_bars` rows outside `[start_date, end_date]` are ignored.
      → return sorted []BarEvent slice
4.  CommissionSchedule.init(commission_cfg)  — use snapshot from backtests row
5.  FillSimulator.init(slippage_bps, slippage_atr_pct, commission_schedule)
      → if slippage_atr_pct: pre-compute ATR(14) over full bar slice before replay
6.  Instantiate strategy with params_snapshot; inject BacktestContext(mode='backtest')
7.  await strategy.on_start()  (or call if not coroutine)
8.  total = len(bars); cadence = max(1, total // 200)
    for i, bar in enumerate(bars):
      a. FillSimulator.process_pending_orders(bar)
           — fills at bar.open ± slippage (adverse direction per side)
           — slippage applied to BOTH entry and exit fills
           — commission deducted per fill from CommissionSchedule
           — emits FillEvent to strategy via on_fill(fill)
           — updates in-memory position tracker
      b. await strategy.on_bar(bar)  (or call if not coroutine)
      c. if i % cadence == 0: ProgressPublisher.publish(i, total, trades_so_far)
      d. if EXISTS backtest:cancel:{backtest_id}: abort loop → status=failed, error_msg='cancelled by user'; publish failed frame; return
9.  await strategy.on_stop()  (or call if not coroutine)
10. FillSimulator.force_close_open_positions(final_bar)
      — applies only to OPEN POSITIONS (filled trades); unfilled pending orders are silently discarded (not reported as trades)
      — close price = final_bar.close ± slippage (same adverse direction rule)
      — mark these trades as forced_close=True in the trade list
11. MetricsComputer.compute(filled_trades, bar_timestamps) → report dict
      — includes forced_close_pnl aggregate
12. UPDATE backtests SET status=done, report=..., progress_pct=100, completed_at=now()
13. ProgressPublisher.publish_done(report)
14. Remove job from backtest:pending:{worker_id}; release Semaphore slot
```

On any unhandled exception: UPDATE status=failed, error_msg=str(exc), publish failed frame, release Semaphore.

### 5.4 `BacktestContext`

Async duck-typed — satisfies the same interface as `BotContext` without subclassing (`bot_worker` and `backtest_worker` are separate Docker services with no shared import path):

| Method | Backtest behaviour |
|---|---|
| `async place_order(canonical_id, side, qty, order_type, tif='DAY', ...)` | Queues a `PendingOrder` into `FillSimulator`; returns synthetic UUID immediately |
| `async get_position(canonical_id)` | Reads from in-memory position tracker updated by `FillSimulator` |
| `async subscribe(canonical_id, timeframe)` | No-op — bars fed sequentially by runner |
| `async cancel_order(order_id)` | Removes matching entry from `FillSimulator` pending queue |
| `mode` property | Returns literal `'backtest'` — strategies that branch on mode must handle this third value |

**Mode semantics:** `BacktestContext.mode = 'backtest'`. Strategies that branch on `ctx.mode` (e.g. `if ctx.mode == 'paper': log_debug(...)`) must handle the third `'backtest'` value or use an else-clause. The bot's `bot_accounts` association is used only for `active_broker_id` → commission schedule lookup; no real orders are placed.

No DB writes, no Redis pubsub, no broker sidecar calls during replay.

### 5.5 `FillSimulator`

- Maintains `_pending: list[PendingOrder]` queue
- **TIF semantics:**
  - `DAY` — order cancelled at the next session-close bar (from `MarketCalendar`); if not filled by then, removed from queue
  - `GTC` — persists across session boundaries until filled or explicitly cancelled, up to `GTC_MAX_DAYS=90` calendar days from placement; beyond this the order is cancelled with reason `gtc_expired` and excluded from the trade list (not-filled, not forced-close)
  - `IOC` / `FOK` — fill-or-cancel on the very next bar; if bar open matches, fill; otherwise cancel immediately
  - Other TIF values → raise `NotImplementedError` (loud failure, not silent skip)
- On each `process_pending_orders(bar)`:
  - For each eligible pending order (respecting TIF): fill price = `bar.open ± slippage`
  - **Slippage (adverse direction):**
    - Fixed bps: `price × slippage_bps / 10_000`
    - ATR pct: `ATR14 × slippage_atr_pct` (ATR pre-computed over full bar slice before replay)
    - BUY entry: `bar.open + slippage` (pays more)
    - SELL entry: `bar.open - slippage` (receives less)
    - BUY exit (closing short): same adverse rule
    - SELL exit (closing long): same adverse rule
  - Deduct commission per fill from `CommissionSchedule`
  - Emit `FillEvent` back to strategy via `strategy.on_fill(fill)`
  - Update in-memory position tracker
- **Forced close** (step 10): uses `final_bar.close ± slippage` with same adverse direction; `forced_close=True` in trade record

### 5.6 `MetricsComputer`

Inputs: list of closed trades (entry/exit price, qty, commission, slippage per fill, timestamps, forced_close flag), full bar timestamp list.

**Sharpe** — always computed on **daily-bucketed PnL**, regardless of input timeframe:
1. Use `MarketCalendar` to identify session-close timestamps within the date range
2. Sample the cumulative PnL curve at each session-close
3. Compute daily returns from consecutive session-close PnL values
4. `Sharpe = mean(daily_returns) / std(daily_returns) × sqrt(252)` (risk-free = 0)
5. If `std == 0` (zero-variance, e.g. no trades): `Sharpe = None` (not 0 or ∞)

**MAR** — `CAGR / abs(max_drawdown_pct)`; CAGR from first bar timestamp to last bar timestamp

**Max drawdown** — running peak-to-trough on cumulative PnL curve (per-bar, not daily)

**Drawdown curve** — one point per bar; always **non-negative percentage points** (e.g. 12.3 means a 12.3% peak-to-trough decline from running peak)

**Win rate** — closed winning trades (net PnL > 0) / total closed trades (excluding forced-closes that are separately flagged)

**`forced_close_pnl`** — sum of `pnl` across all trades with `forced_close=True`

### 5.7 `CommissionSchedule`

`commission_cfg` JSONB schema (snapshotted from `app_config[backtest/commission]` at submit time):

```jsonc
{
  "captured_at":      "2026-05-19T14:20:00Z",
  "active_broker_id": "ibkr",   // derived from bot_accounts at submit; drives which schedule is used
  "schedules": {
    "ibkr":   {"per_share": 0.005, "min_per_order": 1.00, "tier": "fixed"},
    "futu":   {"per_trade_hkd": 30.0},
    "schwab": {"us_equity": 0.0},
    "alpaca": {"us_equity": 0.0}
  }
}
```

`active_broker_id` is what `FillSimulator` uses for each fill. The full `schedules` map is preserved in the snapshot for forensic re-run even after `app_config` changes. Default values (used when key is absent from `app_config`):

| Key | Default |
|---|---|
| `ibkr.per_share` | `0.005` USD |
| `ibkr.min_per_order` | `1.00` USD |
| `ibkr.tier` | `"fixed"` |
| `futu.per_trade_hkd` | `30.0` HKD |
| `schwab.us_equity` | `0.0` |
| `alpaca.us_equity` | `0.0` |

### 5.8 Prometheus metrics

All under `backtest_*`:

| Metric | Type | Labels |
|---|---|---|
| `backtest_jobs_total` | Counter | `status` (queued/done/failed) |
| `backtest_duration_seconds` | Histogram | — |
| `backtest_bars_replayed_total` | Counter | — |
| `backtest_fills_simulated_total` | Counter | — |
| `backtest_bar_feed_source_total` | Counter | `source` (db/backfill/csv) |
| `backtest_worker_errors_total` | Counter | `kind` (strategy_error/feed_error/metrics_error/cancel) |
| `backtest_progress_publishes_total` | Counter | — |
| `backtest_workers_active` | Gauge | — |
| `backtest_orphans_recovered_total` | Counter | — |
| `backtest_ws_cap_rejections_total` | Counter | `scope` (jwt\|global) |

---

## 6. Frontend

### 6.1 New files

```
services/backtests/types.ts
services/backtests/api.ts
features/bots/hooks/useBacktestStream.ts
features/bots/components/BacktestConfigForm.tsx
features/bots/components/BacktestProgressBar.tsx
features/bots/components/BacktestReportKpis.tsx
features/bots/components/BacktestPnlChart.tsx
features/bots/components/BacktestDrawdownChart.tsx
features/bots/components/BacktestTradeTable.tsx
features/bots/pages/BacktestPage.tsx
frontend/src/routes/bots.$botId.backtest.tsx
```

### 6.2 Route

`/bots/$botId/backtest` — TanStack Router file-based route. Accessed via "Run Backtest" button in `BotDetailPage` header.

### 6.3 Page states

**State 1 — Configure**

Form fields:
- Instrument (canonical_id search/select — reuse existing instrument picker; only asset classes in §1.1 selectable)
- Timeframe (select: 1m / 5m / 15m / 1h / 1d)
- Date range (start / end date pickers)
- Bars source (radio: DB only / Backfill / CSV upload)
- CSV upload (shown only when bars_source=csv; multipart POST to `upload-bars`; inline error shown on 4xx — Submit disabled until a successful upload exists for the chosen `canonical_id` + `timeframe`)
- Slippage (radio toggle: Fixed bps input OR % of ATR input)
- Commission (read-only display from `app_config` — "auto from broker schedule")
- Corporate action warning (amber, shown when asset class is STOCK/ETF AND date range > 6 months): "This range may span splits or dividends. Results will be misleading unless you upload split-adjusted bars."

Submit → POST `/api/bots/{bot_id}/backtests` → transition to State 2.

**State 2 — Running**

- Progress bar (`progress_pct` from WS frames)
- Current bar timestamp
- Trades-so-far counter
- Cancel button (DELETE endpoint)
- WS managed by `useBacktestStream` hook (same bounded-backoff reconnect pattern as `useBotStatus`)

**State 3 — Done**

- KPI bar: Sharpe / MAR / Max Drawdown / Total Return / Trades / Win Rate
- If `forced_close_pnl != 0`: amber notice "Includes £X from forced end-of-range closes"
- PnL curve chart (SVG, same pattern as portfolio sparkline — cumulative PnL over time)
- Drawdown chart (SVG, shaded area — drawdown % over time, non-negative y-axis, shaded downward)
- Trade list (`DataTable` with columns: symbol, side, qty, entry, exit, entry slippage, exit slippage, commission, PnL, forced, opened, closed)
- "New Backtest" button → reset to State 1
- Collapsible "Previous backtests" list at bottom (fetched via GET list endpoint) — id, canonical_id, date range, Sharpe, status; clicking loads that result into State 3. Delete button on each row: if the row has children (walk-forward group from Phase 21), shows confirmation dialog "Delete this backtest and N child runs?" before sending `DELETE ?cascade=true`

**State 4 — Failed**

- Error message display
- "New Backtest" button

### 6.4 `useBacktestStream` hook

Same pattern as `useBotStatus`:
- Module-level `RETRY_DELAYS = [500, 1500, 5000, 15000]` ms
- Connects to `WS /ws/bots/{bot_id}/backtest/{job_id}`
- On `done` frame: updates local state with report, closes WS
- On `failed` frame: sets error state, closes WS
- On `heartbeat` frame: no-op (keeps connection alive)
- `mountedRef` guard to prevent state updates after unmount
- Same-origin WS URL construction

---

## 7. Deferred to Phase 21

- Walk-forward analysis (rolling in-sample / out-of-sample window splits) — `parent_backtest_id` column is already in schema
- Monte Carlo (resample trade returns N times, report percentile outcomes)
- LLM-driven parameter comparison across multiple backtest runs
- Streaming intermediate chart updates during long walk-forward runs
- Broker-aware commission schedule admin UI

---

## 8. Test surface

### Backend
- `tests/backtest/test_bar_feed.py` — gap detection, backfill trigger, backtest_bars merge, sort order, csv-over-db collision preference
- `tests/backtest/test_fill_simulator.py` — next-bar fill, bps slippage, ATR slippage, commission deduction, BUY/SELL adverse direction, TIF semantics (DAY/GTC/IOC/FOK), forced-close at final bar close
- `tests/backtest/test_metrics.py` — Sharpe daily-bucketing (1m bars → daily), MAR, drawdown, win rate, forced_close_pnl, edge cases (zero trades → Sharpe=None, single trade, all-loss)
- `tests/backtest/test_commission.py` — per-broker schedule lookup from commission_cfg snapshot, active_broker_id routing
- `tests/backtest/test_runner.py` — full async pipeline with fixture strategy (async def on_bar), assert report shape; orphan sweep re-queues stale running jobs
- `tests/backtest/test_api.py` — submit, list, get, delete, upload-bars endpoints; 422 asset_class_not_backtestable; 422 params_snapshot validation; 404 cross-jwt isolation; slippage XOR validation
- `tests/backtest/test_ws_backtest.py` — WS progress frames, done frame, cancel, per-jwt cap, heartbeat, coalescing (minimal-FastAPI pattern)

### Frontend
- `BacktestConfigForm.test.tsx` — form validation, CSV upload state + error display, slippage toggle, Submit disabled until upload succeeds
- `BacktestProgressBar.test.tsx` — progress display
- `BacktestReportKpis.test.tsx` — KPI rendering, Sharpe=null displayed as "—", forced_close_pnl notice
- `BacktestTradeTable.test.tsx` — DataTable render, sort, forced_close column
- `BacktestPage.test.tsx` — state machine transitions (configure → running → done → new → configure)
- `useBacktestStream.test.ts` — WS lifecycle, retry, done/failed/heartbeat frame handling

---

## 9. Versioning

- Alembic: `0062_phase20_backtests.py`
- Tag: `v0.20.0` on phase close
- Sub-phases (`v0.20.1`, `v0.20.2`) if chunks split during implementation
