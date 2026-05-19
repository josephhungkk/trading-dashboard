# Phase 18 — Universe Scanner + News/Filings + Earnings

**Date:** 2026-05-19  
**Status:** approved  
**Versions:** v0.18.0 (scanner) · v0.18.1 (filings) · v0.18.2 (earnings)

---

## Overview

Phase 18 delivers three independent but related streams, each shipping as a sub-phase under the `v0.18.x` umbrella:

| Sub-phase | Tag | Theme |
|---|---|---|
| **18.0** | v0.18.0 | Rule-based universe scanner + Lark DSL + LLM commentary + TicksSubscriber wiring |
| **18.1** | v0.18.1 | SEC EDGAR + HKEX RNS filings ingest + LLM summarisation |
| **18.2** | v0.18.2 | Earnings calendar + auto-flat/pause hooks |

Each sub-phase ships its own Alembic migration, test suite, and FE route. Sub-phases are independent — 18.1 and 18.2 do not depend on 18.0 being complete.

---

## Sub-phase 18.0 — Universe Scanner

### Overview

A configurable universe scanner with a Lark-based DSL rule evaluator, saved + ad-hoc scan modes, background APScheduler scheduling with market-hours gating, DB-persisted run history, LLM commentary at configurable depth, and integration with the Phase 11b alerts engine.

The `TicksSubscriber` lifespan wiring (deferred from Phase 11b) also lands here — the scanner and alerts engine share the same `register_internal_subscriber` API.

### Data model (Alembic 0058)

**`saved_scans`**
```sql
id                UUID PRIMARY KEY DEFAULT gen_random_uuid()
name              TEXT NOT NULL
universe_config   JSONB NOT NULL
  -- {type: "schwab_screener"|"watchlist"|"tickers"|"instruments", params: {...}}
rule_expr         TEXT NOT NULL          -- Lark DSL expression
schedule          TEXT                   -- cron expression; null = ad-hoc only
market_hours_gate BOOLEAN DEFAULT false
exchange          TEXT                   -- e.g. "XNYS"; used by market_hours_gate
llm_depth         TEXT NOT NULL CHECK (llm_depth IN ('quick', 'deep'))
  -- quick = LOCAL_ONLY capability; deep = REASONING capability
alert_rule_id     UUID REFERENCES risk_limits(id) ON DELETE SET NULL
enabled           BOOLEAN DEFAULT true
created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
```

**`scanner_runs`**
```sql
id                  UUID PRIMARY KEY DEFAULT gen_random_uuid()
scan_id             UUID REFERENCES saved_scans(id) ON DELETE SET NULL  -- null for ad-hoc
universe_snapshot   JSONB NOT NULL    -- list of canonical_ids evaluated this run
rule_expr           TEXT NOT NULL     -- snapshot of expression at run time
candidate_count     INT NOT NULL DEFAULT 0
status              TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed'))
started_at          TIMESTAMPTZ NOT NULL DEFAULT now()
completed_at        TIMESTAMPTZ
error               TEXT
```

**`scanner_candidates`**
```sql
id                  UUID PRIMARY KEY DEFAULT gen_random_uuid()
run_id              UUID NOT NULL REFERENCES scanner_runs(id) ON DELETE CASCADE
instrument_id       BIGINT REFERENCES instruments(id) ON DELETE SET NULL
canonical_id        TEXT NOT NULL
matched_at          TIMESTAMPTZ NOT NULL DEFAULT now()
indicator_snapshot  JSONB NOT NULL    -- {rsi: 28.4, volume_ratio: 2.1, ...} at match time
llm_commentary      TEXT              -- null until commentary job completes
llm_depth           TEXT CHECK (llm_depth IN ('quick', 'deep'))
```

**`scanner_indicator_cache`**
```sql
instrument_id   BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE
indicator_name  TEXT NOT NULL
value           NUMERIC(20, 8) NOT NULL
bar_ts          TIMESTAMPTZ NOT NULL
computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
PRIMARY KEY (instrument_id, indicator_name)
```
Redis is the primary cache (60s TTL intraday, 5min daily). This table is a warm-start fallback only.

### Lark DSL — grammar

Phase 18 scope: read-only indicator expressions. No order submission, no state mutation (those extend the grammar in Phase 20).

```lark
rule: expr

expr: expr AND expr          -> and_expr
    | expr OR expr           -> or_expr
    | NOT expr               -> not_expr
    | "(" expr ")"           -> paren_expr
    | comparison

comparison: term OP term     -> cmp_expr

term: func_call
    | NUMBER                 -> number
    | NAME                   -> name

func_call: NAME "(" arglist? ")" -> call

arglist: term ("," term)*

OP:  "<" | ">" | "<=" | ">=" | "==" | "!="
AND: /and/i
OR:  /or/i
NOT: /not/i

%import common.CNAME  -> NAME
%import common.NUMBER
%import common.WS
%ignore WS
```

**Built-in indicator namespace** (injected into evaluator symbol table):

| Identifier | Type | Description |
|---|---|---|
| `rsi(period)` | func | RSI over `period` bars |
| `sma(field, period)` | func | Simple MA; field: `close`/`open`/`high`/`low`/`volume` |
| `ema(field, period)` | func | Exponential MA |
| `atr(period)` | func | Average True Range |
| `volume_ratio(period)` | func | Current volume / SMA(volume, period) |
| `price_vs_high(days)` | func | Close / N-day high |
| `price_vs_low(days)` | func | Close / N-day low |
| `macd(fast, slow, signal)` | func | MACD line value |
| `bb_pct(period, std)` | func | Bollinger Band %B |
| `close`, `open`, `high`, `low`, `volume` | scalar | Latest bar values |
| `mcap`, `pe`, `eps_growth` | scalar | Fundamental scalars (nullable; evaluates false if None) |

**Evaluator runtime** (`app/services/scanner/evaluator.py`):
- Lark parses rule string → AST at **save time** (validation, not at run time) — parse errors → 422 with line/col
- At run time, Lark transformer walks the cached AST with indicator values injected as symbol table
- No `eval()`, no `exec()`, no import path — the transformer is the only execution path
- Indicator functions are Python callables in the symbol table; they read from the indicator cache

### Indicator computation (`app/services/scanner/indicators.py`)

- Daily indicators (`rsi`, `sma`, `ema`, `atr`, etc.) read from `bars_1d` CAGG (Phase 10b.1)
- Intraday scalars (`volume`, `close`) read from `bars_1m`
- Redis cache key: `scanner:ind:{instrument_id}:{indicator_name}:{params_hash}` — 60s TTL intraday, 5min daily
- `TicksSubscriber` feeds real-time `close`/`volume` into `scanner:tick:{canonical_id}:{field}` → invalidates intraday cache on tick

### TicksSubscriber wiring (deferred from Phase 11b)

- `SubscriptionRegistry` gains `register_internal_subscriber(name: str, on_quote: Callable)` method
- `app/services/alerts/ticks_subscriber.py` uses this API to `psubscribe` `quote.*.<canonical_id>` patterns
- Scanner registers patterns for all instruments in enabled saved scans at lifespan start
- Subscriptions count against the global 5000-symbol cap
- On tick: updates `scanner:tick:{canonical_id}:{field}` in Redis

### Service architecture (`app/services/scanner/`)

```
scanner/
  __init__.py
  schemas.py          -- ScanConfig, ScanRun, CandidateRow, UniverseConfig Pydantic models
  universe.py         -- UniverseResolver
  indicators.py       -- indicator computation + Redis cache
  evaluator.py        -- Lark grammar + transformer + symbol table injection
  scanner_service.py  -- ScannerService orchestrator
  commentary.py       -- LLM commentary via AIRouterClient
  scheduler.py        -- APScheduler job registration + market-hours gate
```

**`ScannerService.run_scan(scan_id, ad_hoc_config?)` flow:**
1. Resolve universe → list of `canonical_ids` via `UniverseResolver`
2. For each instrument: fetch indicator snapshot (Redis → DB fallback)
3. Walk Lark AST against indicator symbol table → boolean match
4. Insert `scanner_run` row (`status='running'`)
5. Insert `scanner_candidates` rows for all matches
6. Enqueue commentary job (async, non-blocking — candidates visible immediately)
7. Update `scanner_run` → `status='completed'`
8. Publish `scanner:run:{scan_id}` to Redis pubsub → WS gateway
9. If `alert_rule_id` set and `candidate_count > 0` → fire Phase 11b alert path (Telegram + in-app)

**Scheduling** (`scanner/scheduler.py`):
- APScheduler `CronTrigger` built from user's cron string; validated at save time via `croniter`
- `market_hours_gate=true` → job checks `MarketCalendar.is_open(exchange)` at fire time; no-ops if closed
- Preset shortcuts: `every_5m="*/5 * * * *"`, `every_15m="*/15 * * * *"`, `hourly="0 * * * *"`, `market_open="30 9 * * 1-5"`
- Job IDs keyed by `scan_id` — update/delete saved scan → reschedule/remove APScheduler job atomically
- Scheduler wired into existing lifespan (`AsyncIOScheduler`)

**LLM commentary** (`scanner/commentary.py`):
- `quick` depth → `LOCAL_ONLY` capability; prompt: symbol + indicator snapshot → one-sentence summary
- `deep` depth → `REASONING` capability; prompt: symbol + indicators + recent filings (if 18.1 shipped) → 3–5 sentence analysis
- Commentary fires as a background asyncio task after run completes; patches `scanner_candidates.llm_commentary` in DB; publishes `commentary_ready` WS frame per candidate

### Prometheus metrics (8)

| Metric | Labels |
|---|---|
| `scanner_runs_total` | `mode` (saved/adhoc), `status` |
| `scanner_candidates_total` | `scan_id` |
| `scanner_universe_size` | `scan_id` |
| `scanner_indicator_cache_hits_total` | — |
| `scanner_indicator_cache_misses_total` | — |
| `scanner_llm_commentary_total` | `depth`, `status` |
| `scanner_scheduler_fires_total` | `scan_id` |
| `scanner_alert_fires_total` | — |

### REST API (`app/api/scanner.py`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| `POST` | `/api/scanner/scans` | JWT | Create saved scan; validates cron + Lark expr; 422 on parse error |
| `GET` | `/api/scanner/scans` | JWT | List saved scans |
| `GET` | `/api/scanner/scans/{id}` | JWT | Saved scan detail |
| `PUT` | `/api/scanner/scans/{id}` | JWT | Update; reschedules APScheduler job atomically |
| `DELETE` | `/api/scanner/scans/{id}` | JWT | Soft-delete + remove job |
| `POST` | `/api/scanner/scans/{id}/run` | JWT | Ad-hoc trigger; returns `{run_id}` 202 |
| `POST` | `/api/scanner/runs/adhoc` | JWT | Inline ad-hoc run (no saved scan) |
| `GET` | `/api/scanner/runs` | JWT | List runs; cursor pagination; filterable by `scan_id` |
| `GET` | `/api/scanner/runs/{id}` | JWT | Run detail + candidates |
| `GET` | `/api/scanner/runs/{id}/candidates` | JWT | Paginated candidates |
| `POST` | `/api/scanner/validate` | JWT | Validate Lark expr only; returns parse tree or 422 |

**Rate limits:**
- Saved scan CRUD: 10/min per `jwt_subject`
- Ad-hoc runs: 5/min per `jwt_subject`
- `/validate`: 30/min per `jwt_subject`

### WebSocket (`/ws/scanner/runs/{scan_id}`)

- CSWSH origin check pre-accept
- Subscribes to `scanner:run:{scan_id}` Redis pubsub channel
- Frame types: `run_started`, `candidate`, `run_completed`, `commentary_ready`
- Ad-hoc runs: `/ws/scanner/runs/adhoc/{session_id}` (client-generated UUID)
- 30s heartbeat, 20-connection cap, 2s send timeout
- Bounded backoff reconnect on FE: `[500, 1500, 5000, 15000]`

### Frontend (`features/scanner/`)

**Route:** `/scanner`

**Components:**
- `ScannerPage` — two-column layout: saved scans sidebar + main results panel
- `SavedScanList` — sidebar list; enabled/disabled toggle per scan
- `ScanConfigDrawer` — create/edit form: name, universe picker, rule editor, schedule (preset buttons + raw cron input), market-hours toggle, exchange selector, LLM depth selector, alert rule selector
- `RuleEditor` — textarea with syntax validation on blur (calls `/api/scanner/validate`); inline error with line/col; collapsible indicator reference cheatsheet
- `UniversePicker` — multi-source: Schwab screener params / watchlist dropdown / manual ticker input / "all instruments"
- `CandidatesTable` — DataTable: ticker, exchange, indicator snapshot, LLM commentary (streams in via `commentary_ready` WS frames), "Trade" button → TradeTicketModal
- `RunHistoryDrawer` — past runs per saved scan; each row expandable to candidate list
- `AdHocRunPanel` — inline one-shot config (no schedule fields)

**Services** (`services/scanner/`):
- `types.ts` — `SavedScan`, `ScanRun`, `ScanCandidate`, `UniverseConfig`
- `api.ts` — CRUD + run trigger + validate wrappers
- `useScannerWs.ts` — WS hook with bounded backoff; candidate streaming; commentary patch-in on `commentary_ready`

**Store:** `stores/global/scanner.ts` — Zustand; persists `savedScans` list + `activeScanId`. Candidates fetched on demand via TanStack Query (not stored — too large).

---

## Sub-phase 18.1 — News & Filings Ingest

### Overview

Background ingestion of SEC EDGAR (US 8-K/10-K/10-Q) and HKEX filing feeds, with LLM summarisation via the `LONG_CONTEXT` AI capability. Filings are linked to `instruments` via canonical_id resolution and surfaced in a `/filings` feed page and per-instrument panel.

### Data model (Alembic 0059)

**`filings`**
```sql
id               UUID PRIMARY KEY DEFAULT gen_random_uuid()
instrument_id    BIGINT REFERENCES instruments(id) ON DELETE SET NULL  -- nullable pre-resolution
canonical_id     TEXT
source           TEXT NOT NULL CHECK (source IN ('sec_edgar', 'hkex_rns'))
form_type        TEXT NOT NULL              -- '8-K', '10-K', '10-Q', 'HKEx announcement'
filing_date      TIMESTAMPTZ NOT NULL
period_of_report DATE
title            TEXT NOT NULL
url              TEXT NOT NULL UNIQUE       -- dedup key
raw_text         TEXT                       -- extracted text, truncated to 32KB
llm_summary      TEXT                       -- null until summarisation job completes
llm_summary_at   TIMESTAMPTZ
captured_at      TIMESTAMPTZ NOT NULL DEFAULT now()
```

**`filing_feed_cursors`**
```sql
source      TEXT PRIMARY KEY               -- 'sec_edgar' | 'hkex_rns'
last_cursor TEXT NOT NULL                  -- SEC: last accession number; HKEX: last seq_no
updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
```

### Ingest architecture (`app/services/filings/`)

```
filings/
  schemas.py            -- Filing Pydantic model
  sec_edgar.py          -- SEC EDGAR EFTS full-text search API poller (free, no key)
  hkex_rns.py           -- HKEX filing RSS feed poller
  instrument_linker.py  -- resolves issuer CIK/ticker → canonical_id via symbol_aliases
  summariser.py         -- LLM summarisation via AIRouterClient (LONG_CONTEXT)
  filings_service.py    -- FilingsService orchestrator
```

**Polling (APScheduler):**
- SEC EDGAR EFTS `/submissions` endpoint: every 15 min during US market hours; rate-limited to 10 req/s; no API key required
- HKEX RNS RSS (`www.hkexnews.hk`): every 10 min during HK market hours
- Cursor-based dedup: `filing_feed_cursors` tracks watermark per source; `url` UNIQUE constraint as secondary guard

**Summarisation:**
- Async job queued per new filing
- `LONG_CONTEXT` capability for documents > 4KB (heavy-box if available, fallback chain per AI router)
- `LOCAL_ONLY` for short filings < 4KB
- Patches `filings.llm_summary` + `llm_summary_at` on completion

### REST API (`app/api/filings.py`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/filings` | Feed of recent filings; cursor pagination; filterable by `source`, `form_type`, `instrument_id` |
| `GET` | `/api/filings/{id}` | Single filing detail + LLM summary |
| `GET` | `/api/instruments/{id}/filings` | Filings for a specific instrument (last 20) |

### Frontend

**Route:** `/filings`
- Feed page: source/form_type filters, filing rows with summary preview, "Read full" inline expand
- No WebSocket needed — TanStack Query with 60s poll is sufficient (low frequency)

**`FilingsPanel` component:**
- Injected into instrument detail drawer (same pattern as `OptionDetailsSection`, `FutureDetailsSection`)
- Shows last 3 filings with truncated LLM summaries + "View all" link → `/filings?instrument_id=...`

### Prometheus metrics (5)

| Metric | Labels |
|---|---|
| `filings_ingested_total` | `source`, `form_type` |
| `filings_instrument_link_failures_total` | `source` |
| `filings_summarisation_total` | `capability`, `status` |
| `filings_poll_errors_total` | `source` |
| `filings_dedup_skips_total` | `source` |

---

## Sub-phase 18.2 — Earnings Calendar + Auto-flat/Pause Hooks

### Overview

Earnings calendar sourced from Schwab (primary) and Nasdaq earnings API (free fallback), with per-position auto-flat and per-bot auto-pause hooks that fire N minutes before a scheduled announcement. Bot pause/resume is a stub in Phase 18 (no-op until Phase 20 ships the `bots` table).

### Data model (Alembic 0060)

**`earnings_events`**
```sql
id                UUID PRIMARY KEY DEFAULT gen_random_uuid()
instrument_id     BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE
canonical_id      TEXT NOT NULL
announced_at      TIMESTAMPTZ        -- scheduled time; nullable if date-only known
announced_date    DATE NOT NULL
time_of_day       TEXT CHECK (time_of_day IN ('before_open', 'after_close', 'during_market', 'unknown'))
eps_estimate      NUMERIC(20, 8)
eps_actual        NUMERIC(20, 8)     -- filled post-announcement
revenue_estimate  NUMERIC(20, 8)
revenue_actual    NUMERIC(20, 8)
source            TEXT NOT NULL CHECK (source IN ('schwab', 'nasdaq_api', 'manual'))
confirmed         BOOLEAN NOT NULL DEFAULT false  -- false = estimated date
captured_at       TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
UNIQUE (instrument_id, announced_date)
```

**`earnings_hooks`**
```sql
id             UUID PRIMARY KEY DEFAULT gen_random_uuid()
instrument_id  BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE
account_id     UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE
hook_type      TEXT NOT NULL CHECK (hook_type IN ('auto_flat', 'auto_pause_bot'))
minutes_before INT NOT NULL DEFAULT 30
bot_id         UUID    -- nullable; stub FK until Phase 20 ships bots table
enabled        BOOLEAN NOT NULL DEFAULT true
created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
```

### Ingest architecture (`app/services/earnings/`)

```
earnings/
  schemas.py            -- EarningsEvent, EarningsHook Pydantic models
  schwab_calendar.py    -- Schwab earnings calendar poller
  nasdaq_calendar.py    -- Nasdaq earnings API fallback (free)
  earnings_service.py   -- EarningsService orchestrator
  hook_executor.py      -- HookExecutor: auto_flat + auto_pause_bot
```

**Polling (APScheduler):**
- Schwab earnings calendar: daily at 06:00 US/Eastern; pulls next 7 days
- Nasdaq API fallback: same cadence; fills gaps where Schwab has no data
- Hook evaluation: every 1 min during market hours; checks `earnings_events` for announcements within `minutes_before` window; idempotent (tracks fired hooks in Redis `earnings:hook_fired:{hook_id}:{event_id}` with TTL = 24h to prevent re-trigger)

**`auto_flat` flow:**
1. Resolve open position for `(instrument_id, account_id)` from `positions`
2. If `qty != 0` → `orders_service.place_order(side=SELL if qty > 0 else BUY, qty=abs(qty), order_type=MARKET)`
3. Telegram notification: `"Auto-flat triggered for {symbol} ({account_alias}) — earnings in {N} min"`
4. Mark fired in Redis

**`auto_pause_bot` flow:**
1. Sets `bots.paused=true` (no-op stub in Phase 18 — `bots` table doesn't exist yet; log + skip)
2. Auto-resume scheduled for `announced_at + 2h` (configurable via hook config, deferred to Phase 20)
3. Telegram notification: `"Bot {bot_name} paused — {symbol} earnings in {N} min"`

### REST API (`app/api/earnings.py`)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/earnings` | Calendar feed; date range + instrument filter |
| `GET` | `/api/earnings/{id}` | Single event detail |
| `GET` | `/api/instruments/{id}/earnings` | Earnings history (last 8 quarters + upcoming) |
| `POST` | `/api/earnings/hooks` | Create hook (CSRF nonce required) |
| `GET` | `/api/earnings/hooks` | List hooks |
| `PUT` | `/api/earnings/hooks/{id}` | Update hook |
| `DELETE` | `/api/earnings/hooks/{id}` | Delete hook |

### Frontend

**Route:** `/earnings`
- Calendar view (week/month toggle); earnings badges per day; filterable by exchange/instrument
- TanStack Query with 5min poll (no WS needed)

**`EarningsBadge` component:**
- Injected into positions table row and TradeTicketModal
- Amber badge: "Earnings in 3d" / "Earnings tomorrow (BMO)" / "Earnings today (AMC)"
- Click → `EarningsPanel` drawer

**`EarningsHookDrawer`:**
- Configure auto-flat/pause per position
- `minutes_before` slider (5–120 min)
- Enabled toggle
- CSRF nonce minted via `mintCsrfNonce` on open

**`EarningsPanel`:**
- Instrument detail section: upcoming announcement + last 4 quarters EPS estimate vs actual bar chart

### Prometheus metrics (6)

| Metric | Labels |
|---|---|
| `earnings_events_ingested_total` | `source` |
| `earnings_hooks_fired_total` | `hook_type` |
| `earnings_hooks_failed_total` | `hook_type` |
| `earnings_autoflat_qty_total` | — |
| `earnings_poll_errors_total` | `source` |
| `earnings_dedup_skips_total` | `source` |

---

## Deferred to later phases

| Item | Target |
|---|---|
| Lark grammar extension for bot strategy event hooks (`on_bar`, `on_fill`, state vars) | Phase 20 |
| `auto_pause_bot` resume logic + `bots` table FK | Phase 20 |
| EPS estimate accuracy tracking / beat-miss history | Phase 19 (backtesting) |
| Scanner rules referencing filing sentiment scores | Phase 18.1 must ship first; wire-up in Phase 19 |
| Admin UI for `filing_feed_cursors` reset | Phase 24 |
| TWS string casing verification for scanner-triggered orders | Phase 17 deferred — carry to Phase 24 |

---

## Cross-cutting invariants

- All new endpoints require JWT auth; hook mutation endpoints require CSRF nonce (same `mintCsrfNonce` pattern as Phase 13/14/15)
- All new services fail-OPEN on non-critical paths (commentary failure does not block candidates; summarisation failure does not block filing ingest; hook evaluation failure logs + skips, does not crash the scheduler)
- `auto_flat` is money-moving: fail-CLOSED on broker unreachable (503 → skip + Telegram alert)
- All Prometheus counters use verbatim label values from this spec
- Lark grammar is the single source of truth for expression syntax — no `eval()`, no `exec()` anywhere in scanner path
- TicksSubscriber subscriptions respect the global 5000-symbol cap
