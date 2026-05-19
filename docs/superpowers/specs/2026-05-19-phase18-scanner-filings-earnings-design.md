# Phase 18 — Universe Scanner + News/Filings + Earnings

**Date:** 2026-05-19  
**Status:** approved — architect-reviewed (Pass 1 + Pass 2 applied inline)  
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

The `TicksSubscriber` lifespan wiring (deferred from Phase 11b) also lands here — scanner and alerts share the same per-canonical-id pubsub subscription pattern (see TicksSubscriber section).

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
`scanner_runs` is a TimescaleDB hypertable partitioned on `started_at` with `chunk_time_interval => INTERVAL '7 days'` and a 90-day drop policy (MED-7, MED-12). Migration 0058 calls `create_hypertable` + `add_retention_policy`.

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

Keywords are **lowercase only** (MED-11 fix — no `/i` regex; `and`, `or`, `not` only). Grammar uses precedence-ranked rules (NOT > AND > OR) to avoid LALR shift-reduce conflicts and ensure unambiguous parse of `not a and b or c` → `((not a) and b) or c` (HIGH-11 fix):

```lark
rule: or_expr

or_expr:  and_expr ("or" and_expr)*    -> or_expr
and_expr: not_expr ("and" not_expr)*   -> and_expr
not_expr: "not" not_expr               -> not_expr
        | atom
atom: comparison
    | "(" or_expr ")"

comparison: term OP term               -> cmp_expr

term: func_call
    | NUMBER                           -> number
    | NAME                             -> name

func_call: NAME "(" arglist? ")"       -> call

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

- Daily indicators (`rsi`, `sma`, `ema`, `atr`, etc.) read from `bars_1d` CAGG (Phase 10b.1; 2yr retention)
- Intraday scalars (`volume`, `close`) read from `bars_1m` (6-month retention — MED-14)
- Redis cache key: `scanner:ind:{instrument_id}:{indicator_name}:{params_hash}:{timeframe}` — 60s TTL intraday (`1m`), 5min daily (`1d`)
- At lifespan start: warm Redis for all active-scan symbols from CAGGs (replaces dropped `scanner_indicator_cache` table)
- `TicksSubscriber` feeds real-time `close`/`volume` into `scanner:tick:{canonical_id}:{field}` → invalidates intraday cache on tick

**Data freshness — indicator data source table** (MED-14):

| Indicator | Reads from | Retention | Freshness caveat |
|---|---|---|---|
| `rsi`, `sma`, `ema`, `macd`, `bb_pct`, `atr` (daily) | `bars_1d` CAGG | 2yr | Symbols added < 1d ago have no daily bar — returns `None`, evaluates false |
| `volume_ratio`, `price_vs_high`, `price_vs_low` (intraday) | `bars_1m` | 6mo | Symbols added < N min ago return `None` for N-period indicators |
| `close`, `open`, `high`, `low`, `volume` (scalar) | `bars_1m` latest | 6mo | Stale after 60s if TicksSubscriber not running |
| `mcap`, `pe`, `eps_growth` | `instruments.meta` JSONB | On-demand | May be null if not seeded; evaluates false |

Rules using intraday indicators fail gracefully (return `None` → evaluate false, not error) when data is unavailable.

### TicksSubscriber wiring (CRIT-4 fix — deferred from Phase 11b)

The `SubscriptionRegistry` API (`add(ws, symbols)` / `remove(ws, symbols)`) is per-WS refcount only — no `register_internal_subscriber` exists. `WSConnId = UUID` (confirmed: `registry.py:32`) — string identifiers do not typecheck. The correct pattern is:

**Widened `WSConnId` + per-canonical-id pubsub subscriptions:**

1. **`WSConnId` is widened to `UUID | str`** in this phase. Strings prefixed `__internal:` denote in-process consumers; they bypass the per-WS rate limiter (the rate bucket is keyed on `WSConnId` and internal consumers have no user-facing rate concept — the bulk lifespan-time subscribe of 3500 symbols must not hit the 60/min rate cap).
2. `SubscriptionRegistry` gains `cap_per_ws_override: dict[str, int]` — internal string IDs get `cap=3500` (70% of global 5000 cap); user UUID WS connections share the remaining 1500 (30%). Cap policy: `cap_internal_pct: float = 0.7` in `app_config`.
3. Scanner runs its own `asyncio.Task` subscribing to per-canonical-id patterns — mirroring the existing alerts `ticks_subscriber.py` pattern: `psubscribe quote.*.{canonical_id}` for each symbol in the active-scan set. The confirmed channel format (from `engine.py:328`) is `quote.{source}.{canonical_id}` (dot-separated). The scanner task parses `channel.split(".")` to extract `canonical_id` and filters against the active-scan set.
4. On tick arrival: updates `scanner:tick:{canonical_id}:{field}` in Redis → invalidates intraday indicator cache for that symbol.
5. When saved scans are created/updated/deleted/enabled/disabled: recompute active-scan symbol set, issue `PSUBSCRIBE`/`PUNSUBSCRIBE` on the delta.
6. Subscriptions added to registry with `WSConnId("__internal:scanner")` at lifespan start.

This is the only way internal consumers subscribe to quotes — no other internal-subscriber pattern exists. LOW-7: Phase 11b `alerts/ticks_subscriber.py` already uses the correct `psubscribe quote.*.{canonical}` pattern; it should be migrated to register via `WSConnId("__internal:alerts")` as a follow-up in Phase 18.0 Chunk D to close the Phase 11b deferral.

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
5. Collect matches; if match count > `candidate_count_cap` (default 500), sort by a salience score (e.g. RSI distance from 50) and truncate to 500; emit `scanner_candidate_cap_hit_total` (MED-13). Insert `scanner_candidates` rows.
6. Enqueue commentary job (async, non-blocking — candidates visible immediately)
7. Update `scanner_run` → `status='completed'`
8. Publish `scanner:run:{scan_id}` to Redis pubsub → WS gateway. Frame schema: `{v: 1, type: "...", ts: <server_iso8601>, ...payload}` (MED-10, LOW-8 — `ts` field for FE latency measurement, consistent with portfolio rollup envelope)
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

### Prometheus metrics (12)

| Metric | Labels |
|---|---|
| `scanner_runs_total` | `mode` (saved/adhoc), `status` |
| `scanner_candidates_total` | `scan_id` |
| `scanner_universe_size` | `scan_id` |
| `scanner_universe_stale_total` | `scan_id` |
| `scanner_candidate_cap_hit_total` | `scan_id` |
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
- 30s heartbeat, **cap per `(scan_id, jwt_subject)` = 5** (so multiple tabs on the same scan don't fan out unboundedly); **global per `jwt_subject` = 50** (accommodating sidebar + ad-hoc + multi-device usage) (HIGH-13, MED-6 fix), 2s send timeout
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

**SEC EDGAR single client** (HIGH-6, MED-15 fix) — `app/services/common/sec_edgar_client.py`:
- All SEC EDGAR HTTP traffic routes through this single client
- Global 10 req/s token bucket (`asyncio.Semaphore` + token-refill task) — shared across filing polls, ad-hoc fetches, and any future SEC consumers
- Required `User-Agent` header: `"Trading Dashboard {contact_email}"` where `contact_email` read from `app_config[filings/sec_edgar/contact_email]`
- **Startup check** (MED-15): at lifespan start, assert `app_config.get("filings/sec_edgar/contact_email")` is not None. If missing: log `logger.critical("SEC EDGAR contact_email not configured — SEC polling disabled")` + set `sec_edgar_disabled=True` flag; file poller no-ops. This prevents IP-ban from missing User-Agent. Emit `sec_edgar_no_contact_email_total` counter on each skipped poll tick.
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

**Instrument linker + backfill** (HIGH-5, CRIT-2B fix):
- `instrument_linker.py` resolves CIK/ticker → `canonical_id` via `symbol_aliases` joined to `instruments`
- `symbol_aliases` schema: `(source, raw_symbol, instrument_id, meta JSONB, created_at)` — no `confidence` column (confirmed from models). Tiebreaker for dual-listed ADRs (1 CIK, multiple rows): join to `instruments` and prefer the row where `instruments.primary_exchange` matches the filing's home exchange (e.g. SEC filing → prefer `XNYS`/`XNAS`; HKEX filing → prefer `XHKG`). `instruments.primary_exchange` is confirmed to exist (models.py).
- Failure modes:
  - New filer with no `symbol_aliases` row → insert with `instrument_id=NULL`
  - Dual-listed ADR with no clear primary-exchange match → insert with `instrument_id=NULL`; flag for human review via `filings_instrument_link_failures_total` counter
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

Earnings calendar sourced from Nasdaq earnings API (primary — free, no key required) and Finnhub free tier (fallback). Schwab's Developer API does not expose an earnings calendar endpoint (confirmed: `schwabdev` covers market-data + trade execution only; TD-Ameritrade-era earnings calendar was not migrated). Source enum reflects this (HIGH-9 fix). Per-position auto-flat and per-bot auto-pause hooks fire N minutes before a scheduled announcement. Bot pause/resume is a stub in Phase 18 (no-op until Phase 20 ships the `bots` table).

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
source            TEXT NOT NULL CHECK (source IN ('nasdaq_api', 'finnhub_api', 'manual'))
source_priority   INT NOT NULL DEFAULT 0
  -- higher = preferred: nasdaq_api=2, finnhub_api=1, manual=0 (MED-8, HIGH-9 fix)
  -- same-priority tie: last-writer-wins via `WHERE EXCLUDED.source_priority >= source_priority`
  -- (>= is intentional: same-priority updates to refresh estimates/actuals)
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
  nasdaq_calendar.py    -- Nasdaq earnings API primary poller (free, no key)
  finnhub_calendar.py   -- Finnhub free-tier fallback poller
  earnings_service.py   -- EarningsService orchestrator
  hook_executor.py      -- HookExecutor: auto_flat + auto_pause_bot
```

**Polling (APScheduler):**
- Nasdaq earnings API (primary): daily at 06:00 US/Eastern; pulls next 7 days
- Finnhub free tier (fallback): same cadence; fills gaps where Nasdaq has no data
- Hook evaluation: every 1 min during market hours
- All `add_job` calls in `earnings/hook_executor.py` and `scanner/scheduler.py` use `coalesce=True, misfire_grace_time=60` at the job level (MED-18 fix — per-job, not scheduler-level `job_defaults`)
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

### `auto_flat` flow (CRIT-1, HIGH-10, HIGH-12 fix)

The executor calls `orders_service.place_order_internal` — a new internal entry point that takes a fully-resolved request and skips the HTTP-request-context nonce check (same pattern as Phase 11d Telegram bot). The function signature accepts `issuer: Literal["telegram", "earnings_hook"]` — a typed enum, not a free string, to make accidental new callers visible in code review (MED-16). `place_order_internal` is never exposed via any HTTP/WS surface.

**Broker-native position-close semantics are not used** — IBKR and Schwab both handle closing implicitly from order sign. For **options**, the closing side differs: `SELL_TO_CLOSE` if long, `BUY_TO_CLOSE` if short (HIGH-10 fix). Race protection relies on the double-read guard below.

Step ordering (HIGH-12 fix — dedup claim before broker dispatch to prevent double-flat on crash):

1. Resolve open position for `(instrument_id, account_id)` from `positions`. If `qty == 0` → done.
2. Check `hook_audit` table: if `UNIQUE (hook_id, event_id)` row already exists → skip (Postgres-level durable guard, cross-restart safe).
3. Try `SET earnings:hook_fired:{hook_id}:{event_id} 1 NX EX 604800` on Redis. If key exists (concurrent evaluator beat us) → skip.
4. **Claim the audit row**: insert `hook_audit(hook_id, event_id, outcome='placed', order_id=NULL)` inside a transaction. The UNIQUE constraint catches any concurrent race that slipped past steps 2–3.
5. Determine side: if `instrument.asset_class == AssetClass.OPTION`: `side = 'sell_to_close' if qty > 0 else 'buy_to_close'`; else: `side = 'sell' if qty > 0 else 'buy'`.
6. Call `orders_service.place_order_internal(jwt_subject=hook.jwt_subject, issuer="earnings_hook", client_order_id=f"earnings-hook-{hook.id}-{event.id}", account_id=hook.account_id, instrument_id=hook.instrument_id, side=side, qty=abs(qty), order_type='MARKET', position_effect='CLOSE')`.
7. `place_order_internal` routes through all 5 validation stations including risk gate, with `position_effect='CLOSE'` and `issuer` recorded in `risk_decisions.attempt_kind='earnings_hook_flat'` (HIGH-8 fix); PDT check bypassed for CLOSE orders (`bypass_pdt_when_closing=True`).
8. Kill-switch check: **not bypassed** — if account kill-switch is ON, auto_flat fails-CLOSED; update `hook_audit(outcome='failed_kill_switch')`; Telegram alert: `"Auto-flat BLOCKED — kill-switch active for {account_alias}"`.
9. On broker success: update `hook_audit(order_id=placed_order.id)`.
10. On broker failure: update `hook_audit(outcome='failed')`. Hook will NOT re-fire (UNIQUE row remains); operator must manually clear `hook_audit` row to retry.
11. **Double-read race guard** (HIGH-4 fix): read `qty_at_read`; after 5s monitor fill; if `positions.qty` delta > `qty_at_read × 0.1` → emit `auto_flat_race_detected` + Telegram alert + retry once.
12. Telegram notification on success: `"Auto-flat triggered for {symbol} ({account_alias}) — earnings in {N} min"`.

**PDT bypass ADR:** `position_effect='CLOSE'` + `bypass_pdt_when_closing=True`. Risk gate's `_check_pdt` skips PDT counter increment and PDT BLOCK when both are set. Rationale: flattening before earnings is risk-reducing; PDT blocking the flatten would increase risk. Kill-switch is NOT bypassed.

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
| Migration of `alerts/ticks_subscriber.py` to synthetic-WS-id registry pattern | Phase 18.0 Chunk D follow-up (LOW-7) |

---

## Cross-cutting invariants

1. All new endpoints require JWT auth; hook mutation endpoints require CSRF nonce (same `mintCsrfNonce` pattern as Phase 13/14/15)
2. All new services fail-OPEN on non-critical paths (commentary failure does not block candidates; summarisation failure does not block filing ingest; hook evaluation failure logs + skips, does not crash the scheduler)
3. `auto_flat` is money-moving: fail-CLOSED on broker unreachable (503 → skip + Telegram alert); kill-switch is NOT bypassed
4. All Prometheus counters use verbatim label values from this spec
5. **Lark grammar is read-only in Phase 18** — indicator namespace contains no `place_order`, no state mutation, no I/O. Phase 20 extends the grammar; do not add mutation to Phase 18 evaluator
6. **`WSConnId` widened to `UUID | str`** in Phase 18.0. Strings prefixed `__internal:` bypass the per-WS rate limiter. All internal consumers use `cap_per_ws_override`. No `register_internal_subscriber` API.
7. **Scanner tick subscriptions use per-canonical-id `psubscribe quote.*.{canonical_id}` patterns** (dot-separated, matching confirmed `engine.py:328` channel format `quote.{source}.{canonical_id}`). No wildcard `quote.*.*` fan-out.
8. **Scanner DSL enforces hard wall-clock + node-count caps** at save time (AST) and run time (per-instrument 250ms, per-run 60s)
9. **Scanner scheduler is single-replica today** — multi-worker scheduling deferred to Phase 24; optional leader-election via `SET NX EX 60` on `scanner:scheduler:leader`
10. **Hook-fired orders use `client_order_id = "earnings-hook-{hook_id}-{event_id}"`** and carry `issuer: Literal["telegram", "earnings_hook"]` in `risk_decisions.attempt_kind`; `place_order_internal` is never exposed via HTTP/WS
11. **`attempt_kind` is an enum-by-CHECK-constraint** (not a PG enum) — every new gate caller widens the CHECK in its phase's migration. Phase 18 adds `"earnings_hook_flat"` in Alembic 0060
12. **`auto_flat` dedup claim (Postgres `hook_audit` + Redis `SET NX`) precedes broker dispatch** — prevents double-flat on crash; missed flatten is recoverable manually; duplicate flatten requires unwinding
13. **`auto_flat` bypasses PDT for CLOSE orders** (`bypass_pdt_when_closing=True`); kill-switch is NOT bypassed
14. **SEC EDGAR client is the single global gateway** for all SEC traffic — shared 10 req/s token bucket, required User-Agent contact email, startup-disabled if `app_config[filings/sec_edgar/contact_email]` is missing
15. **Schwab does not provide an earnings calendar endpoint** — Nasdaq API (primary) + Finnhub (fallback) are the sole calendar sources in Phase 18.2
