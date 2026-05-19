# Phase 20 — Backtesting Harness Design

**Date:** 2026-05-19  
**Version target:** v0.20.0  
**Status:** Approved, ready for implementation planning

---

## 1. Overview

Phase 20 adds a backtesting harness that replays historical OHLCV bars through existing `BaseStrategy` plugin code, simulates fills with configurable slippage and per-broker commission, and produces a standard performance report (PnL curve, drawdown, Sharpe, MAR, trade list).

Walk-forward and Monte Carlo analysis are deferred to Phase 21 (LLM-in-loop parameter tuning), which is their natural consumer.

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

---

## 3. Data Model

### 3.1 Alembic migration `0062_phase20_backtests.py`

```sql
CREATE TABLE backtests (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id           UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    status           TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued','running','done','failed')),
    timeframe        TEXT NOT NULL,          -- '1m','5m','15m','1h','1d'
    start_date       DATE NOT NULL,
    end_date         DATE NOT NULL,
    slippage_bps     NUMERIC(8,2),           -- NULL when atr_pct used
    slippage_atr_pct NUMERIC(8,4),           -- NULL when bps used
    commission_cfg   JSONB NOT NULL,         -- broker schedule snapshot at submit time
    params_snapshot  JSONB NOT NULL,         -- strategy params frozen at submit time
    bars_source      TEXT NOT NULL CHECK (bars_source IN ('db','backfill','csv')),
    csv_path         TEXT,                   -- server-side path if CSV uploaded
    progress_pct     SMALLINT NOT NULL DEFAULT 0,
    error_msg        TEXT,
    report           JSONB,                  -- written atomically on completion
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at     TIMESTAMPTZ
);

CREATE INDEX ix_backtests_bot_id ON backtests(bot_id);
CREATE INDEX ix_backtests_status  ON backtests(status) WHERE status IN ('queued','running');

CREATE TABLE backtest_bar_uploads (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    backtest_id    UUID REFERENCES backtests(id) ON DELETE SET NULL,
    canonical_id   TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    row_count      INTEGER NOT NULL,
    uploaded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.2 `report` JSONB schema

```jsonc
{
  "sharpe":            1.42,          // annualised Sharpe ratio (risk-free = 0)
  "mar":               0.87,          // CAGR / max_drawdown
  "max_drawdown_pct":  12.3,
  "total_return_pct":  34.1,
  "total_trades":      47,
  "win_rate":          0.61,          // winning trades / total closed trades
  "avg_trade_pnl":     142.30,        // net of commission + slippage
  "pnl_curve":   [[iso_ts, cumulative_pnl_base], ...],   // one point per bar
  "drawdown_curve": [[iso_ts, drawdown_pct], ...],
  "trades": [
    {
      "canonical_id": "AAPL",
      "side":         "BUY",
      "qty":          100,
      "entry_price":  182.50,
      "exit_price":   191.20,
      "slippage":     0.09,
      "commission":   1.00,
      "pnl":          866.91,
      "opened_at":    "2024-03-15T09:30:00Z",
      "closed_at":    "2024-03-22T14:00:00Z"
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
- Rows inserted into `bars_1m` (or appropriate CAGG bucket) with `source='csv_upload'`
- `canonical_id` provided as a query param on the upload endpoint

---

## 4. Backend API

### 4.1 Router: `app/api/backtests.py`

Mounted at `/api/bots/{bot_id}/backtests`. All endpoints require JWT.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/bots/{bot_id}/backtests` | Submit job — validate params, INSERT status=queued, publish `backtest:submit:{job_id}` to Redis, return `{job_id}` 202 |
| `GET` | `/api/bots/{bot_id}/backtests` | List backtests (id, status, timeframe, start_date, end_date, progress_pct, created_at, completed_at) — cursor paginated by created_at DESC |
| `GET` | `/api/bots/{bot_id}/backtests/{job_id}` | Fetch single backtest including full `report` when done |
| `DELETE` | `/api/bots/{bot_id}/backtests/{job_id}` | Cancel queued/running (publish cancel signal); hard-delete done/failed rows |
| `POST` | `/api/bots/{bot_id}/backtests/upload-bars` | CSV upload — multipart/form-data; `canonical_id` + `timeframe` query params; returns `{canonical_id, row_count}` |

**Submit request body (`BacktestSubmitRequest`):**
```jsonc
{
  "canonical_id":     "AAPL",         // single instrument per backtest run
  "timeframe":        "1d",
  "start_date":       "2024-01-01",
  "end_date":         "2025-01-01",
  "slippage_bps":     5.0,            // mutually exclusive with slippage_atr_pct
  "slippage_atr_pct": null,
  "bars_source":      "backfill"      // db | backfill | csv
}
```

**Validation rules:**
- `end_date > start_date` required
- Each backtest run covers a single instrument (`canonical_id` in the submit body — required field)
- `slippage_bps` and `slippage_atr_pct` are mutually exclusive; at least one must be non-null (0.0 is valid for zero slippage)
- `bars_source=csv` requires a prior `upload-bars` call for the same `canonical_id` + `timeframe` within 24h; otherwise 422
- `bot_id` must exist and `jwt_subject` must own the bot (same as bot endpoints)
- Commission snapshot captured at submit time from `app_config` namespace `backtest/commission`

### 4.2 WS endpoint: `app/api/ws_backtests.py`

```
WS /ws/bots/{bot_id}/backtest/{job_id}
```

- Authenticates via JWT (same CSWSH origin check as existing WS endpoints)
- Subscribes to `backtest:progress:{job_id}` Redis psubscribe
- 50-connection cap (global, shared with bot status WS)
- Worker publishes progress frames every 500 bars:
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
- Client receives disconnect after `done` / `failed` frame (server closes)

---

## 5. Backtest Worker

### 5.1 Docker service

New service `backtest_worker` in `docker-compose.yml`:
- Separate `Dockerfile` (same base image as `bot_worker`)
- Own DB connection pool (max 5 connections)
- Own Redis connection
- Shares `strategies/` volume read-only
- Entrypoint: `python -m app.backtest.worker_main` — polls `backtest:submit:*` Redis keyspace or a `backtests` queue channel

### 5.2 Module layout: `app/backtest/`

```
app/backtest/
  __init__.py
  worker_main.py       # lifespan: listen for backtest:submit:* pubsub, dispatch runner
  runner.py            # BacktestRunner — top-level orchestrator
  bar_feed.py          # BarFeed — DB + backfill + CSV bar loading and merging
  fill_simulator.py    # FillSimulator — pending order queue, next-bar-open fill
  metrics.py           # MetricsComputer — Sharpe, MAR, drawdown, trade stats
  commission.py        # CommissionSchedule — reads app_config broker schedules
  context.py           # BacktestContext — BotContext-compatible interface for replay
  progress.py          # ProgressPublisher — Redis pubsub frames every 500 bars
```

### 5.3 Pipeline: `BacktestRunner.run(backtest_id)`

```
1.  Load backtests row from DB; set status=running
2.  Validate strategy module via sandbox.py DenylistFinder (same check as bot-create)
3.  BarFeed.load(canonical_id, timeframe, start_date, end_date, bars_source)
      → gap-detect against bars_1m / CAGG
      → if bars_source=backfill: trigger bar_service backfill for missing ranges
      → if bars_source=csv: merge csv_upload bars (source='csv_upload') with DB bars
      → return sorted []BarEvent slice
4.  CommissionSchedule.load() — read app_config backtest/commission snapshot
5.  FillSimulator.init(slippage_bps, slippage_atr_pct, commission_schedule)
      → if slippage_atr_pct: pre-compute ATR(14) over full bar slice
6.  Instantiate strategy with params_snapshot; inject BacktestContext
7.  strategy.on_start()
8.  for i, bar in enumerate(bars):
      a. FillSimulator.process_pending_orders(bar)  — fill at bar.open ± slippage; deduct commission
      b. strategy.on_bar(bar)                        — may call ctx.place_order()
      c. ProgressPublisher.maybe_publish(i, total)   — every 500 bars
9.  strategy.on_stop()
10. Close any open positions at final bar's *close* price (mark as forced-close in trade list; no next bar exists so next-bar-open fill rule does not apply)
11. MetricsComputer.compute(filled_trades, bar_timestamps) → report dict
12. UPDATE backtests SET status=done, report=..., progress_pct=100, completed_at=now()
13. ProgressPublisher.publish_done(report)
```

On any unhandled exception: UPDATE status=failed, error_msg=str(exc), publish failed frame.

### 5.4 `BacktestContext`

Implements the same interface as `BotContext` (duck-typed, not subclassed — `bot_worker` and `backtest_worker` are separate Docker services):

| Method | Backtest behaviour |
|---|---|
| `place_order(canonical_id, side, qty, order_type, ...)` | Queues a `PendingOrder` into `FillSimulator`; returns a synthetic UUID |
| `get_position(canonical_id)` | Reads from in-memory position tracker updated by `FillSimulator` |
| `subscribe(canonical_id, timeframe)` | No-op — bars fed sequentially |
| `cancel_order(order_id)` | Removes matching entry from `FillSimulator` pending queue |

No DB writes, no Redis pubsub, no broker sidecar calls.

### 5.5 `FillSimulator`

- Maintains `_pending: list[PendingOrder]` queue
- On each `process_pending_orders(bar)`:
  - For each pending order: fill price = `bar.open ± slippage`
  - Slippage: `price × slippage_bps / 10_000` OR `ATR14 × slippage_atr_pct`
  - BUY orders: fill price + slippage (adverse)
  - SELL orders: fill price - slippage (adverse)
  - Deduct commission per fill from `CommissionSchedule`
  - Emit `FillEvent` back to strategy via `strategy.on_fill(fill)`
  - Update in-memory position tracker

### 5.6 `MetricsComputer`

Inputs: list of closed trades (entry/exit price, qty, commission, slippage, timestamps), full bar timestamp list.

Outputs:
- **Sharpe** — annualised: `mean(daily_returns) / std(daily_returns) × sqrt(252)` (risk-free = 0)
- **MAR** — `CAGR / abs(max_drawdown_pct)`; CAGR from first bar to last bar
- **Max drawdown** — running peak-to-trough on cumulative PnL curve
- **Win rate** — closed winning trades / total closed trades
- **Avg trade PnL** — net of commission + slippage
- **PnL curve** — one data point per bar (cumulative net PnL in account base currency)
- **Drawdown curve** — one data point per bar (drawdown % from running peak)

### 5.7 `CommissionSchedule`

Reads from `app_config` namespace `backtest/commission`:

| Key | Default | Description |
|---|---|---|
| `ibkr_fixed_per_share` | `0.005` | USD per share (IBKR fixed plan) |
| `ibkr_min_per_order` | `1.00` | USD minimum per order |
| `ibkr_tiered_per_share` | `0.0035` | USD per share (IBKR tiered plan) |
| `futu_per_trade_hkd` | `30.0` | HKD per trade |
| `schwab_us_equity` | `0.0` | Zero commission |
| `alpaca_us_equity` | `0.0` | Zero commission |

Broker determined from `bot_accounts` → `broker_accounts.broker_id`. Commission snapshot stored in `backtests.commission_cfg` at submit time so re-runs reproduce results even after config changes.

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
- Instrument (canonical_id search/select — reuse existing instrument picker)
- Timeframe (select: 1m / 5m / 15m / 1h / 1d)
- Date range (start / end date pickers)
- Bars source (radio: DB only / Backfill / CSV upload)
- CSV upload (shown only when bars_source=csv; multipart POST to `upload-bars`)
- Slippage (radio toggle: Fixed bps input OR % of ATR input)
- Commission (read-only display from `app_config` — "auto from broker schedule")

Submit → POST `/api/bots/{bot_id}/backtests` → transition to State 2.

**State 2 — Running**

- Progress bar (`progress_pct` from WS frames)
- Current bar timestamp
- Trades-so-far counter
- Cancel button (DELETE endpoint)
- WS managed by `useBacktestStream` hook (same bounded-backoff reconnect pattern as `useBotStatus`)

**State 3 — Done**

- KPI bar: Sharpe / MAR / Max Drawdown / Total Return / Trades / Win Rate
- PnL curve chart (SVG, same pattern as portfolio sparkline — cumulative PnL over time)
- Drawdown chart (SVG, shaded area below zero — drawdown % over time)
- Trade list (`DataTable` with columns: symbol, side, qty, entry, exit, slippage, commission, PnL, opened, closed)
- "New Backtest" button → reset to State 1
- Collapsible "Previous backtests" list at bottom (fetched via GET list endpoint) — id, date range, Sharpe, status; clicking loads that result into State 3

**State 4 — Failed**

- Error message display
- "New Backtest" button

### 6.4 `useBacktestStream` hook

Same pattern as `useBotStatus`:
- Module-level `RETRY_DELAYS = [500, 1500, 5000, 15000]` ms
- Connects to `WS /ws/bots/{bot_id}/backtest/{job_id}`
- On `done` frame: updates local state with report, closes WS
- On `failed` frame: sets error state, closes WS
- `mountedRef` guard to prevent state updates after unmount
- Same-origin WS URL construction

---

## 7. Deferred to Phase 21

- Walk-forward analysis (rolling in-sample / out-of-sample window splits)
- Monte Carlo (resample trade returns N times, report percentile outcomes)
- LLM-driven parameter comparison across multiple backtest runs
- Streaming intermediate chart updates during long walk-forward runs
- Broker-aware commission schedule admin UI (Phase 21 needs this for parameter tuning)

---

## 8. Test surface

### Backend
- `tests/backtest/test_bar_feed.py` — gap detection, backfill trigger, CSV merge, sort order
- `tests/backtest/test_fill_simulator.py` — next-bar fill, bps slippage, ATR slippage, commission deduction, BUY/SELL adverse fill direction
- `tests/backtest/test_metrics.py` — Sharpe, MAR, drawdown, win rate, edge cases (zero trades, single trade, all-loss)
- `tests/backtest/test_commission.py` — per-broker schedule lookup, snapshot at submit
- `tests/backtest/test_runner.py` — full pipeline with fixture strategy, assert report shape
- `tests/backtest/test_api.py` — submit, list, get, delete, upload-bars endpoints; 422 validation cases
- `tests/backtest/test_ws_backtest.py` — WS progress frames, done frame, cancel, 50-conn cap (minimal-FastAPI pattern)

### Frontend
- `BacktestConfigForm.test.tsx` — form validation, CSV upload state, slippage toggle
- `BacktestProgressBar.test.tsx` — progress display
- `BacktestReportKpis.test.tsx` — KPI rendering, edge cases (null Sharpe when zero variance)
- `BacktestTradeTable.test.tsx` — DataTable render, sort
- `BacktestPage.test.tsx` — state machine transitions (configure → running → done → new)
- `useBacktestStream.test.ts` — WS lifecycle, retry, done/failed frame handling

---

## 9. Versioning

- Alembic: `0062_phase20_backtests.py`
- Tag: `v0.20.0` on phase close
- Sub-phases (`v0.20.1`, `v0.20.2`) if chunks split during implementation
