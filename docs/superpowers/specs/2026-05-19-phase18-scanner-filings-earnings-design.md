# Phase 18 — Universe Scanner + News/Filings + Earnings

**Date:** 2026-05-19  
**Status:** approved — architect-reviewed (Pass 1 applied inline)  
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

The `TicksSubscriber` lifespan wiring (deferred from Phase 11b) also lands here — scanner and alerts share the same synthetic-WS-id pubsub pattern (see TicksSubscriber section).

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
alert_id          BIGINT REFERENCES alerts(id) ON DELETE SET NULL
  -- CRIT-2 fix: alerts.id is BIGSERIAL; this FK fires alert_fires row on new candidates
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
`scanner_runs` is a TimescaleDB hypertable partitioned on `started_at` with a 90-day drop policy (MED-7). Migration 0058 calls `create_hypertable` + `add_retention_policy`.

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
CHECK (instrument_id IS NOT NULL OR canonical_id IS NOT NULL)  -- MED-3
```
Index: `CREATE INDEX ON scanner_candidates (canonical_id)` for re-link queries.

**`scanner_indicator_cache`** — **dropped** (HIGH-1 fix). The `(instrument_id, indicator_name)` PK cannot represent `sma(close, 50)` vs `sma(close, 200)` simultaneously, nor daily vs intraday. Redis is the sole cache; on miss, recompute from `bars_1d`/`bars_1m` CAGGs (cheap). The warm-start story is handled by populating Redis from CAGGs at lifespan start for active-scan symbols.

### Lark DSL — grammar

Phase 18 scope: read-only indicator expressions. No order submission, no state mutation (those extend the grammar in Phase 20). String literals, bitwise ops, ternary, and lambda are excluded — extend in Phase 20.

Keywords are **lowercase only** (MED-11 fix — no `/i` regex; `and`, `or`, `not` only):

```lark
rule: expr

expr: expr "and" expr          -> and_expr
    | expr "or" expr           -> or_expr
    | "not" expr               -> not_expr
    | "(" expr ")"             -> paren_expr
    | comparison

comparison: term OP term       -> cmp_expr

term: func_call
    | NUMBER                   -> number
    | NAME                     -> name

func_call: NAME "(" arglist? ")" -> call

arglist: term ("," term)*

OP: "<" | ">" | "<=" | ">=" | "==" | "!="

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

### Evaluator runtime (`app/services/scanner/evaluator.py`)

- Lark parses rule string → AST at **save time** (validation, not at run time) — parse errors → 422 with line/col
- At run time, Lark transformer walks the cached AST with indicator values injected as symbol table
- No `eval()`, no `exec()`, no import path — the transformer is the only execution path
- Indicator functions are Python callables in the symbol table; they read from the Redis indicator cache

#### Evaluator safety budget (CRIT-3 fix)

Enforced at **save time** (AST validation gate):
- Max AST depth: **8 levels**
- Max AST nodes: **256**
- Max function calls in expression: **32**
- Max sum of `period` parameters across all indicator calls: **5000** (prevents `sma(close, 100000)`)
- Violations → 422 `rule_expr_budget_exceeded` with specific limit hit

Enforced at **run time**:
- Per-instrument wall-clock: **250ms** — timeout → skip instrument, emit `scanner_eval_timeout_total` counter; do NOT fail entire run
- Per-run wall-clock: **60s** — exceeded → `status='failed'`, `error='wall_clock_exceeded'`, publish partial candidates already inserted
- APScheduler `coalesce=True, misfire_grace_time=60` — skip missed fire rather than pile up (MED-9 carry)

Additional Prometheus metrics for evaluator safety:
- `scanner_eval_timeout_total` — per-instrument eval timeouts
- `scanner_eval_node_reject_total` — save-time AST node cap violations
- `scanner_eval_indicator_budget_exhausted_total` — period-sum cap violations

### Indicator computation (`app/services/scanner/indicators.py`)

- Daily indicators (`rsi`, `sma`, `ema`, `atr`, etc.) read from `bars_1d` CAGG (Phase 10b.1)
- Intraday scalars (`volume`, `close`) read from `bars_1m`
- Redis cache key: `scanner:ind:{instrument_id}:{indicator_name}:{params_hash}:{timeframe}` — 60s TTL intraday (`1m`), 5min daily (`1d`)
- At lifespan start: warm Redis for all active-scan symbols from CAGGs (replaces dropped `scanner_indicator_cache` table)
- `TicksSubscriber` feeds real-time `close`/`volume` into `scanner:tick:{canonical_id}:{field}` → invalidates intraday cache on tick

### TicksSubscriber wiring (CRIT-4 fix — deferred from Phase 11b)

The `SubscriptionRegistry` API (`add(ws, symbols)` / `remove(ws, symbols)`) is per-WS refcount only — no `register_internal_subscriber` exists. The correct pattern is:

**Synthetic-WS-id + pubsub listener:**

1. Scanner (and alerts engine) register a synthetic WS id: `WSConnId("__internal:scanner")`, `WSConnId("__internal:alerts")` — treated as regular subscribers in the refcount machinery.
2. `SubscriptionRegistry` gains `cap_per_ws_override: dict[WSConnId, int]` — internal subscribers get `cap=3500` (70% of global 5000 cap); user WS connections share the remaining 1500 (30%). Cap policy: `cap_internal_pct: float = 0.7` in `app_config`.
3. Scanner runs its own `asyncio.Task` doing `psubscribe quote:*` on Redis, filtering against the active-scan canonical_id set (built from enabled `saved_scans`). No engine-internal hooks.
4. On tick arrival: updates `scanner:tick:{canonical_id}:{field}` in Redis → invalidates intraday indicator cache for that symbol.
5. Synthetic-WS subscriptions added at lifespan start; updated when saved scans are created/deleted/enabled/disabled.

This is the only way internal consumers subscribe to quotes — no other internal-subscriber pattern exists.

### Service architecture (`app/services/scanner/`)

```
scanner/
  __init__.py
  schemas.py          -- ScanConfig, ScanRun, CandidateRow, UniverseConfig Pydantic models
  universe.py         -- UniverseResolver
  indicators.py       -- indicator computation + Redis cache
  evaluator.py        -- Lark grammar + transformer + symbol table injection + safety budget
  scanner_service.py  -- ScannerService orchestrator
  commentary.py       -- LLM commentary via AIRouterClient
  scheduler.py        -- APScheduler job registration + market-hours gate
```

**`ScannerService.run_scan(scan_id, ad_hoc_config?)` flow:**
1. Resolve universe → list of `canonical_ids` via `UniverseResolver`; on resolver failure (e.g. Schwab OAuth mid-rotation), fall back to last-good `scanner_runs.universe_snapshot` for the scan + emit `scanner_universe_stale_total` (MED-1)
2. For each instrument: fetch indicator snapshot (Redis → recompute from CAGG on miss)
3. Walk Lark AST against indicator symbol table → boolean match (per-instrument 250ms timeout)
4. Insert `scanner_run` row (`status='running'`)
5. Insert `scanner_candidates` rows for all matches
6. Enqueue commentary job (async, non-blocking — candidates visible immediately)
7. Update `scanner_run` → `status='completed'`
8. Publish `scanner:run:{scan_id}` to Redis pubsub → WS gateway (v=1 frame envelope — MED-10)
9. If `alert_id` set and `candidate_count > 0` → insert `alert_fires` row with `fire_context` JSONB carrying `{scanner_run_id, candidate_ids, indicator_snapshots}` — this is how scanner matches flow into the Phase 11b alert path (CRIT-2 fix)

**Scheduling** (`scanner/scheduler.py`):
- APScheduler `CronTrigger` built from user's cron string; validated at save time via `croniter`
- `market_hours_gate=true` → job checks `MarketCalendar.is_open(exchange)` at fire time; no-ops if closed
- Preset shortcuts: `every_5m="*/5 * * * *"`, `every_15m="*/15 * * * *"`, `hourly="0 * * * *"`, `market_open="30 9 * * 1-5"`
- Reschedule safety (HIGH-2 fix): wrap in `try: remove_job() except JobLookupError: pass` then `add_job`; per-`scan_id` `asyncio.Lock` to serialize concurrent `PUT /scans/{id}` requests; scheduler state rebuilt from `saved_scans` at lifespan start
- Scheduler is **single-replica** today; multi-worker scheduling deferred to Phase 24 (HIGH-3 fix). Non-leaders can skip via `SET NX EX 60` on `scanner:scheduler:leader` Redis key (optional Phase 18 addition — document in Phase 24 scope if not done now)
- APScheduler `coalesce=True, misfire_grace_time=60` on all scanner jobs (MED-9 carry)

**LLM commentary** (`scanner/commentary.py`):
- `quick` depth → `LOCAL_ONLY` capability; prompt: symbol + indicator snapshot → one-sentence summary
- `deep` depth → `REASONING` capability; prompt: symbol + indicators + recent filings from `filings` table if `instrument_id` has a row in last 30 days (otherwise indicators-only — same prompt, same capability, 3–5 sentence target) (MED-2 fix)

Prompt templates (verbatim, MED-2 fix):

*Quick (LOCAL_ONLY):*
```
{symbol} scanner match. Indicators: {indicator_snapshot_json}.
Summarise in one sentence why this is a notable setup.
```

*Deep (REASONING) — no filings:*
```
{symbol} scanner match. Indicators: {indicator_snapshot_json}.
Provide a 3-5 sentence analysis of the technical setup. Be specific about the indicator readings.
```

*Deep (REASONING) — with filings:*
```
{symbol} scanner match. Indicators: {indicator_snapshot_json}.
Recent filings context: {filing_titles_and_summaries}.
Provide a 3-5 sentence analysis combining the technical setup and fundamental context.
```

Commentary fires as a background asyncio task after run completes; patches `scanner_candidates.llm_commentary` in DB; publishes `commentary_ready` WS frame per candidate (v=1 envelope).

### Prometheus metrics (11)

| Metric | Labels |
|---|---|
| `scanner_runs_total` | `mode` (saved/adhoc), `status` |
| `scanner_candidates_total` | `scan_id` |
| `scanner_universe_size` | `scan_id` |
| `scanner_universe_stale_total` | `scan_id` |
| `scanner_indicator_cache_hits_total` | — |
| `scanner_indicator_cache_misses_total` | — |
| `scanner_llm_commentary_total` | `depth`, `status` |
| `scanner_scheduler_fires_total` | `scan_id` |
| `scanner_alert_fires_total` | `scan_id` |
| `scanner_eval_timeout_total` | — |
| `scanner_eval_node_reject_total` | — |
| `scanner_eval_indicator_budget_exhausted_total` | — |

### REST API (`app/api/scanner.py`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| `POST` | `/api/scanner/scans` | JWT | Create saved scan; validates cron + Lark expr + safety budget; 422 on violation |
| `GET` | `/api/scanner/scans` | JWT | List saved scans |
| `GET` | `/api/scanner/scans/{id}` | JWT | Saved scan detail |
| `PUT` | `/api/scanner/scans/{id}` | JWT | Update; reschedules APScheduler job with per-scan-id lock |
| `DELETE` | `/api/scanner/scans/{id}` | JWT | Soft-delete + remove job |
| `POST` | `/api/scanner/scans/{id}/run` | JWT | Ad-hoc trigger; returns `{run_id}` 202 |
| `POST` | `/api/scanner/runs/adhoc` | JWT | Inline ad-hoc run (no saved scan) |
| `GET` | `/api/scanner/runs` | JWT | List runs; cursor pagination; filterable by `scan_id` |
| `GET` | `/api/scanner/runs/{id}` | JWT | Run detail + candidates |
| `GET` | `/api/scanner/runs/{id}/candidates` | JWT | Paginated candidates |
| `POST` | `/api/scanner/validate` | JWT | Validate Lark expr + safety budget; returns parse tree or 422 |

**Rate limits:**
- Saved scan CRUD: 10/min per `jwt_subject`
- Ad-hoc runs: 5/min per `jwt_subject`
- `/validate`: 30/min per `jwt_subject` (FE debounces on-blur call by 500ms — LOW-3)

### WebSocket (`/ws/scanner/runs/{scan_id}`)

- CSWSH origin check pre-accept
- Subscribes to `scanner:run:{scan_id}` Redis pubsub channel
- Frame schema: `{v: 1, type: "...", ...payload}` — versioned v=1 envelope on all frames (MED-10 fix)
- Frame types: `run_started`, `candidate`, `run_completed`, `commentary_ready`
- Ad-hoc runs: `/ws/scanner/runs/adhoc/{session_id}` (client-generated UUID)
- 30s heartbeat, **20-connection cap per `jwt_subject`** (MED-6 fix — per-user, not global), 2s send timeout
- Bounded backoff reconnect on FE: `[500, 1500, 5000, 15000]`

### Frontend (`features/scanner/`)

**Route:** `/scanner`

**Components:**
- `ScannerPage` — two-column layout: saved scans sidebar + main results panel
- `SavedScanList` — sidebar list; enabled/disabled toggle per scan
- `ScanConfigDrawer` — create/edit form: name, universe picker, rule editor, schedule (preset buttons + raw cron input), market-hours toggle, exchange selector, LLM depth selector, alert rule selector
- `RuleEditor` — textarea with syntax validation on blur (500ms debounce → `/api/scanner/validate`); inline error with line/col; collapsible indicator reference cheatsheet (lowercase `and`/`or`/`not` keywords documented)
- `UniversePicker` — multi-source: Schwab screener params / watchlist dropdown / manual ticker input / "all instruments"
- `CandidatesTable` — DataTable: ticker, exchange, indicator snapshot, LLM commentary (streams in via `commentary_ready` WS frames), "Trade" button → TradeTicketModal
- `RunHistoryDrawer` — past runs per saved scan; each row expandable to candidate list
- `AdHocRunPanel` — inline one-shot config (no schedule fields)

**Services** (`services/scanner/`):
- `types.ts` — `SavedScan`, `ScanRun`, `ScanCandidate`, `UniverseConfig`
- `api.ts` — CRUD + run trigger + validate wrappers
- `useScannerWs.ts` — WS hook with bounded backoff; candidate streaming; commentary patch-in on `commentary_ready`; expects `v=1` frame envelope

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
raw_text         TEXT                       -- extracted text; see MED-5 truncation
llm_summary      TEXT                       -- null until summarisation job completes
llm_summary_at   TIMESTAMPTZ
captured_at      TIMESTAMPTZ NOT NULL DEFAULT now()
CHECK (instrument_id IS NOT NULL OR canonical_id IS NOT NULL)
```

Raw text truncation (MED-5 fix): `text.encode('utf-8')[:32768].decode('utf-8', errors='ignore')` — byte-safe 32KB cap.

Index: `CREATE INDEX ON filings (canonical_id)` for re-link queries.

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
  sec_edgar.py          -- SEC EDGAR EFTS full-text search API poller — uses sec_edgar_client
  hkex_rns.py           -- HKEX filing RSS feed poller
  instrument_linker.py  -- resolves issuer CIK/ticker → canonical_id via symbol_aliases
  summariser.py         -- LLM summarisation via AIRouterClient (LONG_CONTEXT)
  filings_service.py    -- FilingsService orchestrator
```

**SEC EDGAR single client** (HIGH-6 fix) — `app/services/common/sec_edgar_client.py`:
- All SEC EDGAR HTTP traffic routes through this single client
- Global 10 req/s token bucket (`asyncio.Semaphore` + token-refill task) — shared across filing polls, ad-hoc fetches, and any future SEC consumers
- Required `User-Agent` header: `"Trading Dashboard {contact_email}"` where `contact_email` read from `app_config[filings/sec_edgar/contact_email]`
- 429 → exponential backoff + `sec_edgar_rate_limit_total` counter

**Polling (APScheduler):**
- SEC EDGAR EFTS: every 15 min during US market hours
- HKEX RNS RSS: every 10 min during HK market hours
- Per-source `asyncio.Lock` in `FilingsService` to serialize concurrent ticks (MED-4 fix)
- Cursor-based dedup: `filing_feed_cursors` updated with `SELECT FOR UPDATE` to prevent double-advance (MED-4 fix); `url` UNIQUE constraint as secondary guard

**Summarisation:**
- Async job queued per new filing
- `LONG_CONTEXT` capability for documents > 4KB (heavy-box if available, fallback chain per AI router)
- `LOCAL_ONLY` for short filings ≤ 4KB
- Patches `filings.llm_summary` + `llm_summary_at` on completion

**Instrument linker + backfill** (HIGH-5 fix):
- `instrument_linker.py` resolves CIK/ticker → `canonical_id` via `symbol_aliases`
- Failure modes:
  - New filer with no `symbol_aliases` row → insert with `instrument_id=NULL`
  - Dual-listed ADR (1 CIK, multiple canonical_ids) → link to the primary-exchange row (highest `confidence` in `symbol_aliases`); document ambiguity in `filings.canonical_id` ARRAY if needed (defer multi-link to Phase 24)
  - Delisted ticker → insert with `instrument_id=NULL`
- **Backfill job** (`filings_relinker`): nightly APScheduler job scans `filings WHERE instrument_id IS NULL AND captured_at > now() - interval '30 days'` and re-runs `instrument_linker`; also triggered via Redis pubsub `symbol_aliases:insert` channel on any new alias upsert
- Metric: `filings_relinked_total`

### REST API (`app/api/filings.py`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/filings` | Feed of recent filings; cursor pagination; filterable by `source`, `form_type`, `instrument_id` |
| `GET` | `/api/filings/{id}` | Single filing detail + LLM summary |
| `GET` | `/api/instruments/{id}/filings` | Filings for a specific instrument; `?limit=N` defaulting to 3 (panel) or 20 (feed) — single endpoint (LOW-5 fix) |

### Frontend

**Route:** `/filings`
- Feed page: source/form_type filters, filing rows with summary preview, "Read full" inline expand
- No WebSocket needed — TanStack Query with 60s poll is sufficient (low frequency)

**`FilingsPanel` component:**
- Injected into instrument detail drawer (same pattern as `OptionDetailsSection`, `FutureDetailsSection`)
- Calls `/api/instruments/{id}/filings?limit=3` — shows last 3 filings with truncated LLM summaries + "View all" link → `/filings?instrument_id=...`

### Prometheus metrics (7)

| Metric | Labels |
|---|---|
| `filings_ingested_total` | `source`, `form_type` |
| `filings_instrument_link_failures_total` | `source` |
| `filings_relinked_total` | — |
| `filings_summarisation_total` | `capability`, `status` |
| `filings_poll_errors_total` | `source` |
| `filings_dedup_skips_total` | `source` |
| `sec_edgar_rate_limit_total` | — |

---

## Sub-phase 18.2 — Earnings Calendar + Auto-flat/Pause Hooks

### Overview

Earnings calendar sourced from Schwab (primary) and Nasdaq earnings API (free fallback), with per-position auto-flat and per-bot auto-pause hooks that fire N minutes before a scheduled announcement. Bot pause/resume is a stub in Phase 18 (no-op until Phase 20 ships the `bots` table).

### Data model (Alembic 0060)

Alembic 0060 also widens `risk_decisions_attempt_kind_check` to include `"earnings_hook_flat"` (HIGH-8 fix).

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
source_priority   INT NOT NULL DEFAULT 0
  -- higher = preferred: schwab=2, nasdaq_api=1, manual=0 (MED-8 fix)
confirmed         BOOLEAN NOT NULL DEFAULT false  -- false = estimated date
captured_at       TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
UNIQUE (instrument_id, announced_date)
  -- ON CONFLICT (instrument_id, announced_date) DO UPDATE SET ... WHERE EXCLUDED.source_priority >= source_priority
  -- (last-writer-wins with priority: Schwab > Nasdaq > manual)
```

MED-8 fix: single-row per `(instrument_id, announced_date)` with `source_priority`-gated upsert; no multi-source rows.

**`earnings_hooks`**
```sql
id             UUID PRIMARY KEY DEFAULT gen_random_uuid()
instrument_id  BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE
account_id     UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE
jwt_subject    TEXT NOT NULL
  -- CRIT-1 fix: hook owner's jwt_subject; used as place_order_internal jwt_subject + rate-limiter bucket
hook_type      TEXT NOT NULL CHECK (hook_type IN ('auto_flat', 'auto_pause_bot'))
minutes_before INT NOT NULL DEFAULT 30 CHECK (minutes_before >= 10)
  -- MED-9 fix: minimum 10 min; prevents BMO pre-open liquidity window hazard
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
- Hook evaluation: every 1 min during market hours; APScheduler `coalesce=True, misfire_grace_time=30` (MED-9 fix)
- Idempotency: `SET earnings:hook_fired:{hook_id}:{event_id} 1 NX EX 604800` (HIGH-7 fix — 7-day TTL, atomic NX); hook fires only when `SET NX` returns `1`. Additionally, a `hook_audit` table row is inserted in Postgres for cross-restart durability (Redis-only dedup is lost on flush):

**`hook_audit`** (part of Alembic 0060):
```sql
id          UUID PRIMARY KEY DEFAULT gen_random_uuid()
hook_id     UUID NOT NULL REFERENCES earnings_hooks(id) ON DELETE CASCADE
event_id    UUID NOT NULL REFERENCES earnings_events(id) ON DELETE CASCADE
fired_at    TIMESTAMPTZ NOT NULL DEFAULT now()
outcome     TEXT NOT NULL CHECK (outcome IN ('placed', 'skipped_no_position', 'failed'))
order_id    UUID    -- FK to orders.id if placed
UNIQUE (hook_id, event_id)  -- Postgres-level idempotency guard
```

### `auto_flat` flow (CRIT-1 fix)

The executor calls `orders_service.place_order_internal` — a new internal entry point that takes a fully-resolved request and skips the HTTP-request-context nonce check (same pattern as Phase 11d Telegram bot, which mints a web nonce with `issuer="telegram"` tag):

1. Resolve open position for `(instrument_id, account_id)` from `positions`
2. If `qty == 0` → insert `hook_audit(outcome='skipped_no_position')`, done
3. Check `hook_audit` table: if `UNIQUE (hook_id, event_id)` already exists → skip (Postgres-level guard)
4. **Double-read race guard** (HIGH-4 fix): read `qty_at_read = positions.qty`; place order; after 5s monitor fill; if `positions.qty` has moved more than `qty_at_read × 0.1` (10% tolerance) before fill → emit `auto_flat_race_detected` + Telegram alert + retry once. If broker supports position-close semantics (IBKR `closePosition=True`, Schwab `closePosition` flag) — use that instead of qty-based order to avoid the race entirely
5. Call `orders_service.place_order_internal(jwt_subject=hook.jwt_subject, issuer="earnings_hook", client_order_id=f"earnings-hook-{hook.id}-{event.id}", account_id=hook.account_id, instrument_id=hook.instrument_id, side='sell' if qty > 0 else 'buy', qty=abs(qty), order_type='MARKET', position_effect='CLOSE')`
6. `place_order_internal` routes through all 5 validation stations including risk gate, with `position_effect='CLOSE'` and `issuer` recorded in `risk_decisions.attempt_kind='earnings_hook_flat'` (HIGH-8 fix); PDT check bypassed for CLOSE orders (`bypass_pdt_when_closing=True` on the call — risk gate respects this for hook-originated flatten orders; documented as an ADR in this spec)
7. Kill-switch check: **not bypassed** — if account kill-switch is ON, auto_flat fails-CLOSED with Telegram alert: `"Auto-flat BLOCKED — kill-switch active for {account_alias}"`
8. Telegram notification on success: `"Auto-flat triggered for {symbol} ({account_alias}) — earnings in {N} min"`
9. Insert `hook_audit(outcome='placed', order_id=...)` 
10. Mark Redis dedup key: `SET earnings:hook_fired:{hook_id}:{event_id} 1 NX EX 604800`

**PDT bypass ADR:** auto_flat sets `position_effect='CLOSE'`. The risk gate's `_check_pdt` must learn `bypass_pdt_when_closing: bool` flag: when `True` and `position_effect == 'CLOSE'`, skip PDT counter increment and PDT BLOCK. Rationale: flattening before earnings is risk-reducing; PDT blocking the flatten would increase risk. Kill-switch is NOT bypassed — it represents an explicit operator override.

**`auto_pause_bot` flow:**
1. No-op stub in Phase 18: log `"auto_pause_bot: bots table not yet available"` + skip
2. Telegram notification: `"Bot pause skipped (Phase 20 not yet deployed) — {symbol} earnings in {N} min"`

### REST API (`app/api/earnings.py`)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/earnings` | Calendar feed; date range + instrument filter |
| `GET` | `/api/earnings/{id}` | Single event detail |
| `GET` | `/api/instruments/{id}/earnings` | Earnings history (last 8 quarters + upcoming) |
| `POST` | `/api/earnings/hooks` | Create hook (CSRF nonce required; minted on form open per LOW-4 note) |
| `GET` | `/api/earnings/hooks` | List hooks |
| `PUT` | `/api/earnings/hooks/{id}` | Update hook |
| `DELETE` | `/api/earnings/hooks/{id}` | Delete hook |

Note (LOW-4): `EarningsHookDrawer` mints CSRF nonce on drawer open (more secure than on-submit). This is intentionally stricter than Phase 14/15 pattern; document in FE comments.

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
- `minutes_before` slider — **minimum 10 min** enforced in FE + BE (MED-9 fix)
- Enabled toggle
- CSRF nonce minted on drawer open via `mintCsrfNonce`

**`EarningsPanel`:**
- Instrument detail section: upcoming announcement + last 4 quarters EPS estimate vs actual bar chart

### Prometheus metrics (7)

| Metric | Labels |
|---|---|
| `earnings_events_ingested_total` | `source` |
| `earnings_hooks_fired_total` | `hook_type` |
| `earnings_hooks_failed_total` | `hook_type` |
| `earnings_autoflat_qty_total` | — |
| `earnings_autoflat_race_detected_total` | — |
| `earnings_poll_errors_total` | `source` |
| `earnings_dedup_skips_total` | `source` |

---

## Deferred to later phases

| Item | Target |
|---|---|
| Lark grammar extension for bot strategy event hooks (`on_bar`, `on_fill`, state vars, string literals, bitwise ops) | Phase 20 |
| `auto_pause_bot` resume logic + `bots` table FK — no-op stub in Phase 18 | Phase 20 |
| EPS estimate accuracy tracking / beat-miss history | Phase 19 (backtesting) |
| Scanner rules referencing filing sentiment scores | Phase 18.1 must ship first; wire-up in Phase 19 |
| Multi-link for dual-listed ADR filings (1 CIK → multiple canonical_ids) | Phase 24 |
| Admin UI for `filing_feed_cursors` reset | Phase 24 |
| TWS string casing verification for scanner-triggered orders | Phase 17 deferred — Phase 24 |
| Scanner scheduler leader-election (multi-worker) | Phase 24 |

---

## Cross-cutting invariants

1. All new endpoints require JWT auth; hook mutation endpoints require CSRF nonce (same `mintCsrfNonce` pattern as Phase 13/14/15)
2. All new services fail-OPEN on non-critical paths (commentary failure does not block candidates; summarisation failure does not block filing ingest; hook evaluation failure logs + skips, does not crash the scheduler)
3. `auto_flat` is money-moving: fail-CLOSED on broker unreachable (503 → skip + Telegram alert); kill-switch is NOT bypassed
4. All Prometheus counters use verbatim label values from this spec
5. **Lark grammar is read-only in Phase 18** — indicator namespace contains no `place_order`, no state mutation, no I/O. Phase 20 extends the grammar; do not add mutation to Phase 18 evaluator
6. **Synthetic-WS-id + pubsub is the only internal quote subscription pattern** — no `register_internal_subscriber` API exists; internal consumers use `WSConnId("__internal:{name}")` with `cap_per_ws_override`
7. **Scanner DSL enforces hard wall-clock + node-count caps** at save time (AST) and run time (per-instrument 250ms, per-run 60s)
8. **Scanner scheduler is single-replica today** — multi-worker scheduling deferred to Phase 24; optional leader-election via `SET NX EX 60` on `scanner:scheduler:leader`
9. **Hook-fired orders use `client_order_id = "earnings-hook-{hook_id}-{event_id}"`** and carry `issuer="earnings_hook"` in `risk_decisions.attempt_kind`; this defines the audit taxonomy
10. **`attempt_kind` is an enum-by-CHECK-constraint** (not a PG enum) — every new gate caller widens the CHECK in its phase's migration. Phase 18 adds `"earnings_hook_flat"` in Alembic 0060
11. All Prometheus metrics include the three additional evaluator-safety metrics: `scanner_eval_timeout_total`, `scanner_eval_node_reject_total`, `scanner_eval_indicator_budget_exhausted_total`
12. **`auto_flat` bypasses PDT for CLOSE orders** (`bypass_pdt_when_closing=True` on `place_order_internal` call); kill-switch is never bypassed
