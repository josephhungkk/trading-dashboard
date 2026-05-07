# Phase 9 — Charting v1 + Bar Aggregator + Historical Store — Design

**Status:** brainstorm complete, ARCHITECT-REVIEW pending
**Target version:** v0.9.0
**Date:** 2026-05-07
**Predecessor:** v0.10.0 (Phase 8c · 25dd9e9 · Alpaca trade write path shipped)

---

## 1. Goals & Non-Goals

### Goal

Ship a Futubull-class single-symbol charting experience plus the bar infrastructure (live aggregator + historical store) that future phases depend on (Phase 11 Alerts, Phase 18 Scanner, Phase 19 Backtesting, Phase 23 UK CGT).

### Concrete deliverables (v0.9.0)

1. `/chart/:canonical_id` route (FE) — single chart per page, sub-panes for indicators, full-screen capable, mobile-functional with simplified toolbar.
2. Inline "View chart" links from positions, orders, and watchlist rows.
3. **klinecharts ^10.x** integration with **~70 indicators** (27 built-ins + ~45 custom-coded TS).
4. **All klinecharts built-in drawing tools** (~30) + **5 custom overlays** (Long Position, Short Position, Pitchfork variants ×4).
5. **`bar_aggregator/` Docker service** consuming the existing quote bus → 6 sub-1m bucket types (1s/5s/10s/15s/30s/45s) → TimescaleDB hypertable writes.
6. **`GetHistoricalBars` RPC** on all 4 broker sidecars with hot-30d pre-warm + cold-lazy fetch via `BarService` orchestrator.
7. **`chart_layouts(user_id, instrument_id, payload jsonb, schema_version)` table** for per-user-per-symbol indicator + drawing persistence.
8. **TimescaleDB hypertables + retention policies** (1s 7d, 1m 6mo; CAGGs for 5s/10s/15s/30s/45s/5m/15m/30m/1h/1d).
9. **WS extension** — existing `/ws/quotes` gateway pattern extended with `/ws/bars/<canonical_id>/<timeframe>` channel for live tail.

### Non-goals (explicitly deferred)

| Item | Defer to |
|---|---|
| Multi-pane chart grids (Futu's 1–10 layout picker, sync crosshair/indicators) | v0.9.1 mini-phase |
| Compare-overlay ("VS" button) — normalized %-change overlay of 1–3 additional symbols | v0.9.1 mini-phase |
| Pattern drawings (Head & Shoulders, ABCD, XABCD, Three Drives, 3/5/8 Waves) | v0.9.1 mini-phase |
| Drag-from-naked-position SL/TP **creation** (Phase 9 only edits existing brackets) | v0.9.1 mini-phase |
| Chart screenshot / share-link | v0.9.1 mini-phase |
| Fundamental indicator overlays (Gross Margin, ROE, EPS, Total Revenue, Free Cash Flow, ...) | Phase 18 |
| Options-chain indicators (OPT_VOL, OPT_OI, IV, IV_RANK, IV_PERCENTILE, VMACD) | Phase 12 |
| Futures indicators (OPENINTEREST, POSITIONCHANGE) | Phase 14 |
| Index-breadth indicators (ADR_*, HSI>MA200, McClellan) | Phase 18 |
| Money-flow / Level-2 (CAPITALINFLOW, ORDERINFLOW_XL/L/M/S) | Likely never (no broker exposes the L2 tape) |
| Custom user-defined indicator engine (Pine-Script-equivalent) | Standalone phase post-v1.0 |
| Alerts on chart price-level crossing | Phase 11 |
| Bar replay / historical playback | Phase 19 (Backtesting harness) |
| Futu sidecar VPS migration | Phase 6.1 mini-phase post-v1.0 |

### Success criteria

- Chart open p95 latency ≤ **2s** for active-set symbols (hot path), ≤ **5s** for cold fetch.
- Live tail update latency ≤ **200ms** from quote-bus tick to chart paint.
- Drag-stop on open position lands a `ModifyOrder` within **1s** after release+confirm.
- ≥ **80%** test coverage per CLAUDE.md; reviewer chain at end of each chunk.
- Aggregator memory < **256MB** with 100 active instruments.
- p95 `/api/bars` ≤ **100ms** per page (10k row cap).

---

## 2. System Architecture

```
                         ┌──────────────────────────────────────────────────────────┐
                         │                    Existing infrastructure                │
                         │  Quote bus (Redis pub/sub: quote.<source>.<canonical_id>) │
                         │   ↑ producers: sidecar_schwab_streamer / sidecar_ibkr     │
                         │     sidecar_futu_streamer / sidecar_alpaca_streamer       │
                         └────────────────┬────────────────────┬────────────────────┘
                                          │ subscribe          │ subscribe
                                          ▼                    ▼
                ┌──────────────────────────────┐  ┌──────────────────────────────┐
                │  bar_aggregator/  (NEW)       │  │  backend FastAPI (existing) │
                │  - subscribe quote bus        │  │  - /api/bars                │
                │  - 6 in-mem buckets per inst  │  │  - /api/chart/layouts       │
                │    (1s/5s/10s/15s/30s/45s)    │  │  - /ws/bars/<id>/<tf>       │
                │  - WAL→Redis Streams (replay) │  │  - BarService orchestrator  │
                │  - flush sub-1m → hypertable  │  │  - hot-30d pre-warm cron    │
                │  - emit live-bar pub/sub on   │  │  - cold lazy fetch          │
                │    bar.<canonical_id>.<tf>    │  │  - source-priority routing  │
                └──────────────────────────────┘  └────┬──────────────────┬─────┘
                          │ writes sub-1m                │ reads bars       │ RPC
                          ▼                              ▼                  ▼
                ┌────────────────────────────────────────────────┐  ┌────────────────────────┐
                │ PostgreSQL 18 + TimescaleDB extension           │  │ sidecar_*  (existing)  │
                │ - bars_1s / bars_1m  (hypertable, retention)    │  │ + GetHistoricalBars()  │
                │ - bars_5s/10s/15s/30s/45s  (CAGG from bars_1s)  │  │   per-broker historical│
                │ - bars_5m/15m/30m/1h/1d    (CAGG from bars_1m)  │  │   bar API call         │
                │ - chart_layouts (user_id, instrument_id, jsonb) │  └────────────────────────┘
                │ - bar_backfill_jobs (idempotency + dedup)        │
                │ - instruments / symbol_aliases (existing)        │
                └────────────────────────────────────────────────┘
                                                                            ▲
                                                                            │ HTTPS+WS (/api+/ws)
                                ┌───────────────────────────────────────────┴──┐
                                │  Frontend (React 19 + Vite + klinecharts ^10) │
                                │  - /chart/:canonical_id route                  │
                                │  - <TradeChart> wrapper (klinecharts canvas)   │
                                │  - indicator picker (~70) + ~45 custom TS      │
                                │  - drag SL/TP overlay (Long/Short Position)    │
                                │  - chart_layouts persistence                   │
                                │  - inline "view chart" links from positions/   │
                                │    orders/watchlist features                   │
                                └────────────────────────────────────────────────┘
```

### Component map

| Component | Tech | Owns |
|---|---|---|
| `bar_aggregator/` (new) | Python 3.14, asyncio, redis-py, asyncpg, uvloop | Quote-bus subscription, in-memory bucket aggregation for 6 sub-1m sizes, WAL via Redis Streams (replay on crash), per-tick partial-bar pub/sub for live tail, every-2s batch-flush to hypertable for durability |
| `backend/app/services/bar_service.py` (new) | Python 3.14, asyncpg, structlog | Orchestrator: hot-30d pre-warm cron, cold gap detection + lazy fetch, source-priority routing, write-through cache to hypertable, request coalescing |
| `backend/app/api/bars.py` (new) | FastAPI router | `GET /api/bars` range queries with cursor pagination, `GET/PUT/DELETE /api/chart/layouts/:instrument_id`, WS `/ws/bars/<canonical_id>/<timeframe>` for live tail |
| `proto/broker/v1/broker.proto` (extend) | gRPC | New `GetHistoricalBars(canonical_id, timeframe, start, end, limit)` RPC + `HistoricalBar` message |
| `sidecar_schwab/handlers.py` (+) | schwabdev | `GetHistoricalBars` via `pricehistory` endpoint (CHART_EQUITY) |
| `sidecar_alpaca/handlers.py` (+) | alpaca-py `StockHistoricalDataClient` / `CryptoHistoricalDataClient` | Same RPC, equity + crypto |
| `sidecar_ibkr/handlers.py` (+) | `ib_async.reqHistoricalDataAsync` | Same RPC, with pacing-violation backoff |
| `sidecar_futu/handlers.py` (+) | `futu-api request_history_kline` | Same RPC, HK-only initially |
| `frontend/src/features/chart/` (new) | React 19, klinecharts ^10.x, Zustand | `<TradeChart>` wrapper, indicator/drawing/layout state, drag-handle Long/Short Position overlay, chart_layouts sync |
| `frontend/src/services/bars.ts` (new) | TS | Range-fetch via `/api/bars`, live-tail via `/ws/bars/...`, klinecharts data adapter |

### Inter-service contracts

- **Backend ↔ bar_aggregator:** zero direct coupling. Both speak to Redis (quote bus + bar pub/sub) and PG (hypertable + WAL streams). Either restarts independently.
- **Backend → sidecar:** existing gRPC over WG/Docker network; new `GetHistoricalBars` joins existing per-account RPC surface.
- **FE → backend:** REST for ranges + history, WebSocket for live tail (extension of existing `/ws/quotes` gateway pattern).

### Network topology

Phase 9 keeps current per-broker placement (per `phase4_sidecar_topology.md`, `phase6_futu_topology.md`, `phase7a_schwab_topology.md`, `phase7c_alpaca_topology.md`):

- **VPS prod:** backend + nginx + new `bar_aggregator` co-located (Docker compose)
- **NUC dev:** same compose stack via WSL
- **IBKR/Futu sidecars:** NUC over WG (mTLS); cross-WG quote-bus + gRPC paths already validated by Phase 7b.1
- **Schwab/Alpaca sidecars:** in-cluster Docker on VPS (td-net network)
- **PG-18 + TimescaleDB:** native on NUC over WG (per `wsl_docker_pg_self_nat`)

### TimescaleDB extension install

Phase 9 first migration creates extension via `CREATE EXTENSION IF NOT EXISTS timescaledb`. PG-18 is supported by Timescale ≥ 2.17. Verify exact PG-18 support matrix at scaffold time and pin the version in the bootstrap script.

---

## 3. Data Model

### Strategy: 2 base hypertables + 10 continuous aggregates

This avoids 12 separate hypertables while letting TimescaleDB auto-derive higher timeframes from base data. CAGGs auto-refresh via `add_continuous_aggregate_policy` background jobs and queries are transparent SQL (look like ordinary tables to the FE).

### Base tables

#### `bars_1s` (Alembic 0024) — populated by `bar_aggregator` from quote bus

```sql
CREATE TABLE bars_1s (
  instrument_id    BIGINT        NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  bucket_start     TIMESTAMPTZ   NOT NULL,
  source           TEXT          NOT NULL,          -- 'aggregator-{streamer-source}'
  source_priority  SMALLINT      NOT NULL DEFAULT 99,  -- aggregator built = 99
  open             NUMERIC(20,8) NOT NULL,
  high             NUMERIC(20,8) NOT NULL,
  low              NUMERIC(20,8) NOT NULL,
  close            NUMERIC(20,8) NOT NULL,
  volume           NUMERIC(20,8) NOT NULL DEFAULT 0,
  trade_count      INTEGER       NOT NULL DEFAULT 0,
  inserted_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  PRIMARY KEY (instrument_id, bucket_start)
);
SELECT create_hypertable('bars_1s', 'bucket_start',
  chunk_time_interval => INTERVAL '6 hours');
CREATE INDEX bars_1s_inst_time_idx ON bars_1s (instrument_id, bucket_start DESC);
SELECT add_retention_policy('bars_1s', INTERVAL '7 days');
```

#### `bars_1m` (Alembic 0024) — populated by sidecar `GetHistoricalBars` + aggregator minute-emitter

```sql
CREATE TABLE bars_1m (
  instrument_id    BIGINT        NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  bucket_start     TIMESTAMPTZ   NOT NULL,
  source           TEXT          NOT NULL,          -- 'schwab', 'alpaca', 'ibkr', 'futu', 'aggregator-*'
  source_priority  SMALLINT      NOT NULL,          -- 1=schwab, 2=alpaca, 3=ibkr, 4=futu, 99=aggregator
  open             NUMERIC(20,8) NOT NULL,
  high             NUMERIC(20,8) NOT NULL,
  low              NUMERIC(20,8) NOT NULL,
  close            NUMERIC(20,8) NOT NULL,
  volume           NUMERIC(20,8) NOT NULL DEFAULT 0,
  trade_count      INTEGER       NOT NULL DEFAULT 0,
  inserted_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  PRIMARY KEY (instrument_id, bucket_start)
);
SELECT create_hypertable('bars_1m', 'bucket_start',
  chunk_time_interval => INTERVAL '7 days');
CREATE INDEX bars_1m_inst_time_idx ON bars_1m (instrument_id, bucket_start DESC);
SELECT add_retention_policy('bars_1m', INTERVAL '6 months');
```

### Priority-encoded UPSERT (BarService chokepoint)

```sql
INSERT INTO bars_1m (instrument_id, bucket_start, source, source_priority,
                     open, high, low, close, volume, trade_count)
VALUES (...)
ON CONFLICT (instrument_id, bucket_start) DO UPDATE
  SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
      close=EXCLUDED.close, volume=EXCLUDED.volume,
      trade_count=EXCLUDED.trade_count,
      source=EXCLUDED.source, source_priority=EXCLUDED.source_priority,
      inserted_at=NOW()
  WHERE EXCLUDED.source_priority < bars_1m.source_priority;
```

Single-row PK keeps reads simple (`SELECT * FROM bars_1m WHERE instrument_id=$1 AND bucket_start BETWEEN $2 AND $3`); priority enforcement is the single chokepoint at INSERT. Aggregator-built 1m bars (priority 99) get cleanly overwritten when broker historical fetches land later (priorities 1–4). No double-count, no zombie rows.

### Continuous aggregates (Alembic 0025 — 10 CAGGs)

| CAGG | Source | Bucket | Refresh policy |
|---|---|---|---|
| `bars_5s` | `bars_1s` | 5s | last 7d, refresh every 30s, end_offset 1s |
| `bars_10s` | `bars_1s` | 10s | same |
| `bars_15s` | `bars_1s` | 15s | same |
| `bars_30s` | `bars_1s` | 30s | same |
| `bars_45s` | `bars_1s` | 45s | same |
| `bars_5m` | `bars_1m` | 5m | last 6mo, refresh every 1m, end_offset 1m |
| `bars_15m` | `bars_1m` | 15m | same |
| `bars_30m` | `bars_1m` | 30m | same |
| `bars_1h` | `bars_1m` | 1h | last 5y, refresh every 5m, end_offset 1m |
| `bars_1d` | `bars_1m` | 1d | last 5y, refresh every 1h, end_offset 1m |

Pattern:

```sql
CREATE MATERIALIZED VIEW bars_5s
WITH (timescaledb.continuous) AS
SELECT
  instrument_id,
  time_bucket(INTERVAL '5 seconds', bucket_start) AS bucket_start,
  first(open, bucket_start) AS open,
  max(high) AS high,
  min(low)  AS low,
  last(close, bucket_start) AS close,
  sum(volume) AS volume,
  sum(trade_count) AS trade_count
FROM bars_1s
GROUP BY instrument_id, time_bucket(INTERVAL '5 seconds', bucket_start);

SELECT add_continuous_aggregate_policy('bars_5s',
  start_offset => INTERVAL '7 days',
  end_offset   => INTERVAL '1 second',
  schedule_interval => INTERVAL '30 seconds');
```

### `chart_layouts` (Alembic 0026)

```sql
CREATE TABLE chart_layouts (
  id              BIGSERIAL    PRIMARY KEY,
  user_id         UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  instrument_id   BIGINT       NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  payload         JSONB        NOT NULL,         -- { indicators[], drawings[], chart_type, default_timeframe, panes }
  schema_version  INTEGER      NOT NULL DEFAULT 1,
  updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, instrument_id)
);
CREATE INDEX chart_layouts_user_idx ON chart_layouts (user_id);
```

`updated_at` doubles as the recency signal for the active-set definition. `schema_version` lets us evolve the JSONB shape (e.g., add new indicator fields) with a one-shot upgrade migration.

### `bar_backfill_jobs` (Alembic 0027) — idempotency + dedup

```sql
CREATE TABLE bar_backfill_jobs (
  id              BIGSERIAL   PRIMARY KEY,
  instrument_id   BIGINT      NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  source          TEXT        NOT NULL,
  timeframe       TEXT        NOT NULL,           -- '1m'
  range_start     TIMESTAMPTZ NOT NULL,
  range_end       TIMESTAMPTZ NOT NULL,
  status          TEXT        NOT NULL,           -- 'pending', 'in_progress', 'done', 'failed'
  rows_inserted   INTEGER,
  error_message   TEXT,
  started_at      TIMESTAMPTZ,
  finished_at     TIMESTAMPTZ,
  inserted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT bbj_status_chk CHECK (status IN ('pending','in_progress','done','failed'))
);
CREATE INDEX bbj_inst_tf_status_idx ON bar_backfill_jobs (instrument_id, timeframe, status);
CREATE UNIQUE INDEX bbj_unique_pending_idx
  ON bar_backfill_jobs (instrument_id, source, timeframe, range_start, range_end)
  WHERE status IN ('pending', 'in_progress');
```

The partial unique index prevents two concurrent backend workers (Phase 24 prep) from racing the same fetch.

### `app_config` keys (no new table)

All under `namespace = 'charts'`. The existing `app_config` schema is `(namespace, key)` PK with `value` (text) for str/int/bool + `value_json` (JSONB) for json + `value_type` discriminator.

| namespace | key | value_type | value / value_json |
|---|---|---|---|
| `charts` | `bar_source_priority.equity_us` | `json` | `value_json = ["schwab","alpaca","ibkr"]` |
| `charts` | `bar_source_priority.equity_hk` | `json` | `value_json = ["futu","ibkr"]` |
| `charts` | `bar_source_priority.crypto` | `json` | `value_json = ["alpaca"]` |
| `charts` | `bar_source_priority.fx` | `json` | `value_json = ["ibkr"]` |
| `charts` | `bar_pre_warm_window_days` | `int` | `value = "30"` |
| `charts` | `bar_active_set_recency_days` | `int` | `value = "30"` |
| `charts` | `chart_layout_schema_version` | `int` | `value = "1"` |
| `charts` | `enabled` | `bool` | `value = "true"`  (kill-switch) |

### Active-set definition (consumed by hot-30d pre-warm cron)

```sql
WITH cfg AS (
  SELECT value::int AS recency_days
  FROM app_config
  WHERE namespace = 'charts'
    AND key = 'bar_active_set_recency_days'
)
SELECT DISTINCT instrument_id FROM positions
UNION
SELECT DISTINCT instrument_id FROM watchlist_entries
UNION
SELECT DISTINCT instrument_id FROM chart_layouts
  WHERE updated_at > NOW() - (SELECT recency_days FROM cfg) * INTERVAL '1 day';
```

### Retention summary

- `bars_1s`: 7 days hard-drop
- `bars_1m`: 6 months hard-drop
- CAGGs `bars_5s`–`bars_45s`: bounded by `bars_1s` retention
- CAGGs `bars_5m`–`bars_30m`: bounded by `bars_1m` retention
- CAGGs `bars_1h`, `bars_1d`: 5 years (configured at CAGG level — cheap to keep)

### Migrations summary

| # | Change |
|---|---|
| 0023 | `CREATE EXTENSION timescaledb` |
| 0024 | `bars_1s` + `bars_1m` hypertables, retention policies, source_priority column |
| 0025 | 10 continuous aggregates + refresh policies |
| 0026 | `chart_layouts` |
| 0027 | `bar_backfill_jobs` (partial unique on pending+in_progress) |

---

## 4. Backend Services

### `bar_aggregator/` (new Docker service)

```
bar_aggregator/
├─ Dockerfile                  # python:3.14-slim + uv
├─ pyproject.toml              # redis, asyncpg, structlog, uvloop
├─ app/
│  ├─ main.py                  # asyncio entrypoint, lifecycle, signal handling
│  ├─ aggregator.py            # AggregatorEngine: subscribe quote.* → 1s buckets
│  ├─ wal.py                   # Redis Streams WAL: write tick before bucket update; trim on flush-ack
│  ├─ flush.py                 # batched COPY-into bars_1s every 2s (FLUSH_INTERVAL_MS)
│  ├─ minute_emitter.py        # on-minute-boundary: aggregator-built 1m row if no broker row
│  ├─ bar_pubsub.py            # publish bar.<canonical_id>.<tf> on every tick (partial) + on flush (final)
│  ├─ config.py                # env: REDIS_URL, DATABASE_URL, FLUSH_INTERVAL_MS=2000
│  └─ metrics.py               # prom counters: ticks_consumed, buckets_flushed, wal_replayed, flush_lag
└─ tests/
```

**Crash recovery via WAL.** Every tick writes to a Redis Stream `wal:bar_aggregator:{instrument_id}` *before* bucket mutation; on startup, replay any unacked entries. Stream trimmed to last 5 minutes per instrument (`XADD MAXLEN ~`).

**Memory bounds.** In-flight buckets are a `dict[(instrument_id, source), BucketState]` capped to active-set size (~few hundred). Idle entries evicted after N minutes of no quotes. Hard cap at 1000 active instruments per aggregator; `bar_aggregator_active_instruments` metric for observability.

**Backpressure.** If `bars_1s` flush falls behind, log a structured warn + emit `bar_aggregator_flush_lag_seconds` metric. Hard-fail-loudly via healthcheck if lag > 10s (Docker restart picks it up).

### `backend/app/services/bar_service.py` (new orchestrator)

```python
class BarService:
    async def get_bars(canonical_id, timeframe, start, end, limit=10000, cursor=None) -> BarPage:
        # 1. Resolve instrument_id from canonical_id via instruments + symbol_aliases
        # 2. Detect cache gap: query bars_{tf} for [start, end); compute missing ranges
        # 3. If gaps exist and tf >= '1m':
        #    a. Determine source via app_config.bar_source_priority for asset_class
        #    b. Acquire bar_backfill_jobs row (UPSERT on partial-unique-pending index)
        #    c. Call sidecar GetHistoricalBars(canonical_id, tf, gap_start, gap_end)
        #    d. Coalesce concurrent first-callers via job lookup + asyncio.Event
        #    e. UPSERT into bars_1m with priority encoding
        #    f. Mark job done; release event
        # 4. Return paginated rows with next_cursor (sub-1m never backfills — cache-only)

    async def pre_warm_active_set() -> None:
        # Cron: backend startup + nightly post-close (per asset-class market clock)
        # active_set = positions ∪ watchlist ∪ recent chart_layouts
        # for inst in active_set:
        #   for tf in ('1m','1h','1d'):  # 5m/15m/30m derived via CAGG, no fetch
        #     last_done = bar_backfill_jobs.where(...).order_by(finished_at desc).first()
        #     gap = (last_done.range_end if last_done else now-30d, now)
        #     await get_bars(inst.canonical_id, tf, *gap)  # writes through cache

    async def subscribe_live_tail(canonical_id, timeframe) -> AsyncIterator[Bar]:
        # WS handler: subscribe Redis bar.<canonical_id>.<tf>
        # Yield each pubsub message as a parsed Bar payload
```

Coalescing pattern mirrors `sidecar_alpaca._ensure_order_event_subscription`'s per-account `asyncio.Lock` — concurrent first-callers on the same `(instrument, tf, range)` wait on a shared `asyncio.Event` rather than each spawning a duplicate fetch.

### `backend/app/api/bars.py` (new router)

```
GET    /api/bars?canonical_id&timeframe&start&end&limit=10000&cursor=...   → BarPage
GET    /api/chart/layouts/:instrument_id                                    → ChartLayout | 404
PUT    /api/chart/layouts/:instrument_id  (body=ChartLayoutPayload)         → ChartLayout
DELETE /api/chart/layouts/:instrument_id                                    → 204
WS     /ws/bars/:canonical_id/:timeframe                                    → live-tail Bar stream
```

Auth: existing JWT middleware. Trade-execution endpoints (drag-stop → ModifyOrder) reuse the existing **trade nonce** CSRF flow — no new chokepoint.

### `proto/broker/v1/broker.proto` extension

```proto
rpc GetHistoricalBars(GetHistoricalBarsRequest) returns (GetHistoricalBarsResponse);

message GetHistoricalBarsRequest {
  string canonical_id = 1;
  string timeframe    = 2;     // "1m" only — sub-1m has no historical source
  google.protobuf.Timestamp range_start = 3;
  google.protobuf.Timestamp range_end   = 4;
  int32  limit = 5;            // pacing budget; sidecar may chunk internally
}
message GetHistoricalBarsResponse {
  repeated HistoricalBar bars = 1;
  bool   truncated = 2;        // true if more bars exist past range_end (pacing cap hit)
}
message HistoricalBar {
  google.protobuf.Timestamp bucket_start = 1;
  string  open  = 2;           // string-encoded NUMERIC(20,8) per project convention
  string  high  = 3;
  string  low   = 4;
  string  close = 5;
  string  volume = 6;
  int32   trade_count = 7;
}
```

### Sidecar implementations (4 files modified)

- **`sidecar_schwab/handlers.py`** — `schwabdev` `pricehistory` endpoint (CHART_EQUITY) — generous quota, free for US equities.
- **`sidecar_alpaca/handlers.py`** — `alpaca-py` `StockHistoricalDataClient.get_stock_bars` / `CryptoHistoricalDataClient.get_crypto_bars`.
- **`sidecar_ibkr/handlers.py`** — `ib_async.reqHistoricalDataAsync` with pacing-violation backoff (IBKR caps ~60 historical reqs / 10min — needs token-bucket throttle in sidecar).
- **`sidecar_futu/handlers.py`** — `futu-api request_history_kline` — HK only initially.

---

## 5. Frontend

### `frontend/src/features/chart/` (new feature module)

```
features/chart/
├─ ChartPage.tsx               # /chart/:canonical_id route component
├─ TradeChart.tsx              # klinecharts wrapper (canvas, indicator/drawing/layout state)
├─ ChartToolbar.tsx            # top toolbar: chart-type, indicators, drawings, save, screenshot, fullscreen
├─ TimeframeBar.tsx            # bottom dual-pill bar: ranges + intervals (matches Futu)
├─ IndicatorPicker.tsx         # right-drawer modal: Favorites / Technicals / Custom tabs (Bucket-A only)
├─ DrawingTools.tsx            # left-rail tool selector (line/channel/shape/wave/fib/measure/text)
├─ PositionOverlay.tsx         # custom klinecharts overlay: Long/Short Position with drag SL/TP handles
├─ ChartContextMenu.tsx        # right-click: add/remove indicator, copy snapshot
├─ ChartLayoutSync.tsx         # debounced PUT /api/chart/layouts/:instrument_id on every change
├─ stores/
│  ├─ chartStore.ts            # active timeframe, indicators, drawings, chart-type
│  └─ liveTailStore.ts         # WS subscription, tick coalescing
├─ services/
│  ├─ bars.ts                  # GET /api/bars with cursor paging; klinecharts data adapter
│  ├─ liveTail.ts              # /ws/bars/<id>/<tf> consumer; reconnect with exponential backoff
│  └─ layoutSync.ts            # debounced PUT/GET to /api/chart/layouts
├─ overlays/
│  ├─ longPosition.ts          # klinecharts custom overlay template
│  ├─ shortPosition.ts
│  ├─ pitchfork.ts
│  ├─ schiffPitchfork.ts
│  ├─ modifiedSchiffPitchfork.ts
│  └─ insidePitchfork.ts
└─ indicators/                 # 45 custom-coded TS indicators (one file each)
   ├─ vwap.ts
   ├─ ichimoku.ts
   ├─ alligator.ts
   ├─ ... (42 more)
   └─ register.ts              # bulk-register all custom indicators with klinecharts on app boot
```

### Inline entry points (modified existing features)

- `features/positions/PositionRow.tsx` — add "View Chart" link → `/chart/:canonical_id`
- `features/orders/OrderRow.tsx` — same
- `features/watchlist/WatchlistRow.tsx` — same

### Layer compliance

Per `eslint-plugin-boundaries` config in `frontend/eslint.config.mjs`:
- `features/chart/` → `features/` layer; may import from primitives/patterns/layout/lib (free reign).
- klinecharts wrapper lives in `features/chart/`, **not** in `components/primitives/` — it's not a generic primitive, it's a feature-bound widget bound to specific stores/services.
- Custom indicator code consumes only OHLCV input; no FE side-effects.

### Mobile parity

Toolbar collapses below `md` breakpoint to **5–7 most-used drawings**: Trend Line, Horizontal Line, Fib Retracement, Rectangle, Text, Long/Short Position, Indicator picker. Full toolbar accessible via fullscreen mode on mobile. Touch pan/pinch via klinecharts native handlers. Drag-handle SL/TP works via touch — uses unified `pointerdown/pointermove/pointerup` for mouse + touch.

### Drag-handle SL/TP flow (`PositionOverlay.tsx`)

1. Open positions for the current instrument render as a Long/Short Position overlay (entry price line + draggable SL/TP boxes if a bracket exists, or a "+ add SL/TP" handle if not).
2. User drags an SL/TP handle to a new price level.
3. On `pointerup`, show confirm dialog with: instrument, side, qty, current → new SL/TP price, est P&L impact.
4. User confirms → POST to existing `/api/orders/modify` (bracket leg) or `/api/orders/bracket` (new bracket on naked position) with the existing trade-nonce CSRF flow.
5. Success → overlay updates with new SL/TP; failure → toast + revert handle.

### klinecharts version pin

`pnpm add klinecharts@^10` (latest stable at scaffold time per CLAUDE.md "latest stable" rule). Lockfile pins exact.

### CLAUDE.md typo

The line says "klineschart"; actual library is `klinecharts`. Fix in the same chunk that scaffolds the feature module.

---

## 6. Data Flow

### Flow 1 — Cold chart open (active-set, hot-30d already pre-warmed)

```
FE: navigate /chart/AAPL.US
 → GET /api/chart/layouts/<instrument_id>          (200 = restore; 404 = defaults)
 → GET /api/bars?canonical_id=AAPL.US&timeframe=1m&start=NOW-30d&end=NOW&limit=10000
   ← BarPage{bars[10000], next_cursor=null}
 → render klinecharts canvas with 30d of 1m bars
 → WS /ws/bars/AAPL.US/1m subscribe
   ← live ticks every 2s (or boundary-flushed from aggregator)
 [TTI ≤ 2s]
```

### Flow 2 — Cold chart open (out-of-active-set, lazy fetch)

```
FE: navigate /chart/MSFT.US (never opened)
 → GET /api/chart/layouts/<instrument_id>          (404 → defaults)
 → GET /api/bars?canonical_id=MSFT.US&timeframe=1m&start=NOW-30d&end=NOW
   BarService:
   - cache_check → empty
   - bar_backfill_jobs UPSERT pending row for (msft, schwab, 1m, NOW-30d, NOW)
   - sidecar_schwab.GetHistoricalBars → schwabdev pricehistory
   - UPSERT 30d × 390 = ~11700 rows into bars_1m (priority=1)
   - mark job done; cancel coalescing event
   ← BarPage
 → render
 [TTI ≤ 5s typical, ≤ 10s worst-case]
```

### Flow 3 — Scroll-back beyond cache

```
FE: user pans chart left, klinecharts emits "load more" event at left edge
 → GET /api/bars?...&cursor=<encoded_prior_window>
   - cache_check on [NOW-90d, NOW-30d) → gap detected
   - bar_backfill_jobs → fetch via sidecar
   - returns combined cached+fetched page
 → klinecharts prepends bars to canvas
```

### Flow 4 — Live tail (steady state)

Live-tail responsiveness is decoupled from durability. The aggregator publishes a partial-bar update on **every tick** (running open/high/low/close of the in-progress bucket) for instant FE paint; the 2-second batch-flush to `bars_1s` is for durability and survives independently.

```
sidecar_*_streamer publishes quote.<src>.<canonical_id> on Redis
 → bar_aggregator subscribes; updates in-mem 1s bucket; WAL-write tick
 ├─ ON EVERY TICK: publish bar.<canonical_id>.1s with current bucket's running OHLCV (partial=true)
 │  → backend /ws/bars/<id>/<tf> consumer fans to FE WS
 │  → klinecharts updateOrAddData() on the latest candle  [latency ≤ 200ms tick→paint]
 └─ EVERY 2s (independent): batch-COPY in-flight buckets → bars_1s with source_priority
    + emit bar.<canonical_id>.1s with partial=false (final closed bucket) for FE consolidation
```

The FE distinguishes partial (in-progress, still updating) from final (closed) by the `partial` flag in the message envelope and only persists final bars to its local cache for scroll-back consistency.

### Flow 5 — On-minute-boundary 1m emission (no broker historical for newest minute)

```
At HH:MM:00, bar_aggregator's minute_emitter:
 → for each active instrument: aggregate bars_1s in [HH:MM-1, HH:MM) → 1m bar
 → UPSERT into bars_1m with source='aggregator-{src}' priority=99
 → publish bar.<canonical_id>.1m
 → 5–30 minutes later, sidecar GetHistoricalBars (next pre-warm tick) overwrites with priority 1–4
```

### Flow 6 — Drag SL/TP on open position

```
FE: PositionOverlay sees user drag SL handle from $190 → $185
 → on pointerup: open ConfirmDialog(side, qty, $190 → $185, est_loss=...)
 → user clicks Confirm
 → POST /api/orders/modify  (existing endpoint + trade-nonce CSRF)
   body: { order_id: <bracket_sl_leg_id>, stop_price: 185.00 }
 → backend → Sidecar.ModifyOrder → broker
 → on success: existing OrderEvent stream pushes update
 → FE PositionOverlay updates handle position
```

### Flow 7 — Layout persistence (debounced)

```
FE: user adds RSI indicator
 → chartStore.addIndicator('RSI', defaults)
 → ChartLayoutSync debounce (500ms)
 → PUT /api/chart/layouts/<instrument_id>
   body: { payload: <full layout snapshot>, schema_version: 1 }
 → backend UPSERT chart_layouts row
```

### Flow 8 — Aggregator crash + recovery

```
bar_aggregator crashes mid-bucket
 → systemd / docker restart
 → on startup: read Redis Stream wal:bar_aggregator:* (last 5min)
 → replay each tick into bucket state
 → resume normal flush cycle
 [data loss bound: 0 ticks if WAL was synced; ≤ 1 batch if Redis crashed too]
```

---

## 7. Indicator Inventory (Bucket A, ~70 indicators)

### A1 — klinecharts built-ins (27, free)

MA, SMA, EMA, BBI, AO, CCI, KDJ, MACD, MTM, PSY, ROC, RSI, TRIX, WR, BOLL, BIAS, SAR, VOL, OBV, PVT, VR, EMV, AVP, DMI, DMA, BRAR, CR.

### A2 — custom-coded TS (~45)

**Moving averages / trend (12):** VWMA, WMA, TEMA, DEMA, HMA, LSMA, TSF, GMMA, ALLIGAT (Alligator), TWAP, IC (Ichimoku Kinko Hyo), VWAP.

**Momentum / oscillators (15):** MFI, AROON, CHOP (Choppiness Index), CMO (Chande Momentum), Connors RSI, Stoch RSI, BOP (Balance of Power), RVI (Relative Volatility Index), RVGI (Relative Vigor Index), RMI (Relative Momentum Index), ER (Efficiency Ratio), FO (Forecast Oscillator), Fisher Transform, OSC, RC (Change Rate).

**Volatility / channels (10):** ATR, BBIBOLL, DC (Donchian Channel), KC (Keltner Channel), ENE (Moving Average Envelopes), BBW (Bollinger Band Width), CDP, MIKE Base, PPSW (Pivot Points Standard - Woodie), CKS (Chande Kroll Stop).

**Volume / flow (5):** KO (Klinger Oscillator), EFI (Elder's Force Index), AVGVOL, RVOL, MAVOL.

**Pattern / signal (3):** WF (Williams Fractal), NINE (Tom DeMark Sequential 9), HADIFF (Heikin Ashi Difference).

**Misc (4):** TTM Squeeze, SuperTrend, ZigZag, VOLAT (Historical Volatility).

**Total: 27 + ~45 = ~72 indicators in v0.9.0.**

### Indicator implementation pattern

```ts
// frontend/src/features/chart/indicators/vwap.ts
import type { IndicatorTemplate } from 'klinecharts';

export const vwapTemplate: IndicatorTemplate = {
  name: 'VWAP',
  shortName: 'VWAP',
  series: 'price',                   // overlay on main pane
  precision: 2,
  calcParams: [],
  figures: [{ key: 'vwap', title: 'VWAP: ', type: 'line' }],
  calc: (kLineDataList) => { /* ~30 LOC of cumulative-(price×vol) / cumulative-vol */ }
};
```

`indicators/register.ts` bulk-imports all 45 templates and calls `klinecharts.registerIndicator(t)` on app boot.

**Per-indicator test:** `*.test.ts` with golden-vector OHLCV input → expected output array. Test set generated from canonical references (TradingView Pine sources, klinecharts source, Wikipedia formulas — cross-validated).

---

## 8. Drawing Tools

### Klinecharts built-ins (~30, free)

Trend Line, Ray, Info Line, Extended Line, Horizontal Line, Horizontal Ray, Horizontal Line Segment, Vertical Line Segment, Cross Line; Parallel Lines, Parallel Channel; Triangle, Rectangle, Parallelogram, Circle (Ellipse via radius); Fib Retracement, Fib Time Zone, Fib Speed Resistance Fan, Fib Spiral, Fib Circles; Gann Box, Gann Fan, Gann Square; Price Range, Date Range, Date and Price Range; Text, Notes, Price Label; Arrow, Up Arrow, Down Arrow.

### Custom-coded overlays (5)

- **Long Position** — entry line + draggable SL box (red) + TP box (green); shows P&L %; emits `onSLDrag`/`onTPDrag` events the PositionOverlay layer translates into ModifyOrder/PlaceBracket.
- **Short Position** — mirror of Long.
- **Pitchfork** + **Schiff Pitchfork** + **Modified Schiff Pitchfork** + **Inside Pitchfork** (Andrews-style price channels; ~80 LOC each).

### Phase 9 ships ~35 drawing tools (30 built-in + 5 custom)

Pattern/wave overlays (ABCD, XABCD, Three Drives, Head & Shoulders, 3/5/8 Waves) defer to v0.9.1 mini-phase alongside multi-pane and compare-overlay.

---

## 9. Error Handling & Resilience

| Failure | Detection | Behavior |
|---|---|---|
| `bar_aggregator` crash | Docker healthcheck fails; structlog ERROR | Auto-restart via Docker; replay WAL on boot; quote bus is durable so no tick loss within Redis Stream retention (5min) |
| Redis unavailable | `redis-py.RedisError` raised in subscribe loop | Aggregator: log + exit (1); Docker restarts. Backend: `/api/bars` falls through to PG (always available); `/ws/bars/...` returns 503 + Retry-After |
| Sidecar `GetHistoricalBars` timeout | gRPC deadline (15s) | BarService: mark `bar_backfill_jobs` row as `failed` with error_message; FE shows "data temporarily unavailable" toast; cron retries on next pre-warm |
| Sidecar pacing-violation (IBKR especially) | Sidecar catches `pacingViolation` | Sidecar applies token-bucket + 60s cooldown; returns gRPC `RESOURCE_EXHAUSTED`; BarService backs off; honors `Retry-After` |
| TimescaleDB CAGG refresh stalls | Timescale background job log + lag metric | Alert if CAGG lag > 5min; manual `CALL refresh_continuous_aggregate(...)` runbook step |
| TimescaleDB hypertable disk full | PG monitoring | Retention policies prevent runaway; alert at 80% disk; `bars_1s` 7d cap is hardest constraint |
| FE WS disconnect | `WebSocket.onclose` | `liveTail.ts` exponential backoff reconnect (1s, 2s, 4s, 8s, max 30s); on reconnect, fetch missing bars via REST gap-fill |
| FE klinecharts canvas error | error boundary | Show "Chart unavailable — refresh to retry"; report to backend `/api/client-errors` (existing endpoint) |
| Layout JSONB schema drift | `payload.schema_version` mismatch on GET | Backend runs forward-migration function; if unmigratable, return 200 with default + log warning |
| User drags SL/TP, broker rejects ModifyOrder | OrderEvent rejection or 4xx response | Toast with broker error; revert handle position; existing trade-error semantics (per Phase 5b/5c) |
| Aggregator gets ticks before instrument exists | Race on first quote subscribe | Aggregator silently drops ticks for unknown instrument_id; structlog DEBUG (not WARN — expected during seed) |

### Hard invariants enforced

- Bar inserts via UPSERT-with-priority guard (no overwrite by lower priority).
- `bar_backfill_jobs` partial unique index prevents concurrent duplicate fetches.
- `chart_layouts.schema_version` lets the JSONB shape evolve safely.
- All sidecar `GetHistoricalBars` impls use chunked fetch + pacing budget — never single-shot a 5y request.

---

## 10. Testing Strategy

**Per-chunk reviewer rule** (per `feedback_review_per_chunk.md`): full 5-reviewer chain at end of every chunk ≥5 commits — spec-compliance (haiku), python-reviewer (haiku), code-reviewer (sonnet), database-reviewer (sonnet), security-reviewer (sonnet). +typescript-reviewer (haiku) for FE chunks. ARCHITECT-REVIEW once at the start of phase per CLAUDE.md.

### Test layers

| Layer | Scope | Tools | Coverage target |
|---|---|---|---|
| Unit (BE) | BarService gap detection, source priority, UPSERT logic, aggregator bucket math, WAL replay, sidecar handler routing | pytest 9 + pytest-asyncio + asyncpg fixtures | ≥ 90% on `bar_service.py`, `bar_aggregator/aggregator.py`, sidecar `GetHistoricalBars` |
| Integration (BE) | Alembic 0023–0027 round-trip; `bars_1m` UPSERT priority semantics with real PG+Timescale; CAGG refresh; chart_layouts CRUD | pytest with shared `pg_test_session` fixture + pgtap-style assertions | ≥ 80% on migrations |
| Sidecar | Each sidecar's `GetHistoricalBars` against fake broker + golden CSV vectors | pytest + per-sidecar fixtures | ≥ 80% on handlers |
| gRPC contract | proto compatibility (no breaking field changes); `GetHistoricalBars` request/response shapes | buf breaking + golden round-trip | 100% surface |
| Indicator unit (FE) | Each of ~45 custom indicators against golden OHLCV vectors | Vitest 4 + RTL 16 | 100% per indicator |
| FE component | `<TradeChart>`, `<IndicatorPicker>`, `<TimeframeBar>`, `<PositionOverlay>` | Vitest + RTL + Storybook 10 stories | ≥ 80% per file |
| FE store | `chartStore`, `liveTailStore` reducers + side effects | Vitest with mocked services | ≥ 80% |
| E2E | golden flows below | Playwright | All flows green |

### E2E golden flows (Playwright, runs in Docker compose env with real PG+Timescale)

1. Open chart for active-set symbol → bars render ≤ 2s, RSI indicator added → persists across refresh.
2. Open chart for cold symbol (never opened) → backfill triggers → bars render ≤ 5s → live tail updates within 10s.
3. Scroll back 6 months → cursor pagination → klinecharts prepends bars; no duplicate fetches if scrolled twice.
4. Drag SL on open position → confirm dialog → ModifyOrder → SL handle moves, position toast appears.
5. Mobile viewport (375×667): chart renders, simplified toolbar visible, pinch-zoom works, tap-to-fullscreen visible.
6. Aggregator crash injection (compose `kill bar_aggregator`) → ticks queue in WAL → restart → no bar gaps after recovery.

### Performance smoke tests (run in CI on a representative subset)

- p95 `/api/bars` latency ≤ 100ms per page (10k row cap).
- 5y/1m range fetch (paginated): full result in ≤ 3s wall-time.
- 100 concurrent live-tail WS subscribers on 50 instruments: no tick loss; aggregator memory < 256MB.

### Empirical scripts (paper-broker validation, follows the chunk-S pattern in Phase 8c)

- `scripts/empirical/schwab_history_paper.py` — fetches AAPL 1m for last 30d via real Schwab API, asserts ≥1 bar/min coverage during market hours.
- Same for IBKR / Futu / Alpaca. Each excluded from CI (real-broker), runs on-demand or via nightly self-hosted GHA runner.

---

## 11. Migration & Rollout

### Alembic chain (5 new migrations)

| # | Change |
|---|---|
| 0023 | `CREATE EXTENSION timescaledb` (idempotent) |
| 0024 | base hypertables `bars_1s` + `bars_1m` with retention policies, PK `(instrument_id, bucket_start)`, source_priority column |
| 0025 | 10 continuous aggregates with refresh policies |
| 0026 | `chart_layouts` (user_id, instrument_id, payload jsonb, schema_version) |
| 0027 | `bar_backfill_jobs` (with partial unique index on pending+in_progress) |

Each migration tested via `backend/tests/integration/test_alembic_<rev>.py` round-trip on real PG+Timescale.

### `app_config` seeds (separate seed task, not Alembic)

- `bar_source_priority` per asset class (4 keys)
- `bar_pre_warm_window_days = 30`
- `bar_active_set_recency_days = 30`
- `chart_layout_schema_version = 1`
- `charts.enabled = true`

### Compose changes

- `docker-compose.yml` adds `bar_aggregator` service with healthcheck, `depends_on` Redis+PG, env vars `REDIS_URL`/`DATABASE_URL`/`FLUSH_INTERVAL_MS`.
- VPS prod compose mirror in `deploy/vps/`.
- NUC dev compose pulls from same root file; no NUC-specific change.

### Frontend additions

- `pnpm add klinecharts@^10` (lockfile pin).
- New routes auto-wired via TanStack Router file-based routing under `frontend/src/routes/chart.$canonicalId.tsx`.
- New feature module under `frontend/src/features/chart/` — boundaries config already permits `features/` reach.

### Rollout plan (9 chunks)

1. **Chunk A — TimescaleDB foundation:** migrations 0023–0025, BarService skeleton, TimescaleDB pinned dependency. Reviewer chain.
2. **Chunk B — bar_aggregator service:** new Docker service, WAL+flush+publish, sidecar streamer integration. Reviewer chain.
3. **Chunk C — sidecar GetHistoricalBars:** all 4 sidecars, proto extension, contract tests, empirical scripts. Reviewer chain.
4. **Chunk D — backend orchestration:** BarService gap detection, source priority, pre-warm cron, /api/bars + /ws/bars + /api/chart/layouts. Reviewer chain.
5. **Chunk E — FE chart feature:** TradeChart wrapper, indicator picker, drawing tools (built-ins only), TimeframeBar, ChartPage route, inline links. Reviewer chain.
6. **Chunk F — Custom indicators:** ~45 TS indicators with golden-vector unit tests. Reviewer chain.
7. **Chunk G — Drag-handle SL/TP:** PositionOverlay, ModifyOrder/PlaceBracket integration, mobile touch path. Reviewer chain.
8. **Chunk H — Layout persistence + mobile parity:** chart_layouts CRUD, debounced sync, mobile toolbar collapse. Reviewer chain.
9. **Chunk I — E2E + perf + close-out:** Playwright flows, perf smoke tests, CHANGELOG/TASKS/CLAUDE.md updates, tag v0.9.0.

Estimated cadence: ~1 chunk per day with reviewers, **~1.5–2 weeks total**. Matches Phase 8c (~10 days, 4 chunks but heavier per chunk).

### Codex delegation pattern (per `feedback_codex_delegation.md`)

Each chunk's source + tests dispatched to Codex via `gpt-5-codex`; Opus main thread orchestrates, runs reviewer chain, commits. Codex defaults patterns A–G applied (per `codex_defaults.md`).

### Feature-flag gating

None for the user surface. Phase 9 is greenfield — nothing to gate against. `app_config.charts.enabled = true` exists as a kill-switch in case of post-deploy issue.

---

## 12. Risks & Deferred Items

### Top risks

1. **TimescaleDB v2.17+ on PG-18 compatibility.** PG-18 is recent; verify Timescale's PG-18 support matrix at scaffold time. Mitigation: pin TS version via bootstrap script; if support gaps emerge, fall back to vanilla PG partitioning + cron-based aggregation (more code, no Timescale dep — adds ~3–5 days).
2. **Aggregator memory under symbol fan-out.** If the active set grows to thousands of instruments, in-flight bucket dict could balloon. Mitigation: hard cap at 1000 active instruments per aggregator; eviction policy on stale buckets; emit `bar_aggregator_active_instruments` metric.
3. **IBKR pacing violations** during pre-warm. IBKR caps ~60 historical reqs / 10min. With 100+ symbols hot-pre-warming `1m`, easy to trip. Mitigation: per-sidecar token bucket, jittered scheduling, fall through to next priority source if IBKR refuses.
4. **Klinecharts custom indicator perf.** 45 custom indicators in worst-case all enabled at once on 30k bar dataset. Each indicator is `O(n)`. Total ~1.4M ops on every recalc. Probably fine on modern hardware; smoke-test in Chunk F.
5. **CAGG refresh lag** during heavy ingest. If 1s aggregator flushes 10k rows/sec and CAGG `bars_5s` policy refreshes every 30s, refresh window can grow. Mitigation: tune `start_offset` + `end_offset`; if stalls, switch to per-flush manual `refresh_continuous_aggregate()` call.
6. **Mobile drag handle precision.** Touch points are coarser than mouse; SL price snapping on tiny price ranges (e.g. crypto sub-cent) needs careful UX. Mitigation: snap to nearest tick by default; show price as user drags; require explicit confirm.
7. **Trade nonce reuse on rapid drags.** If user drags multiple times within nonce TTL, replay defense may reject. Mitigation: each drag-release fetches a fresh nonce; existing CSRF infra already supports this.

### Deferred to v0.9.1 mini-phase (immediately after v0.9.0)

- Multi-pane chart layouts (1–10 grids, sync crosshair/indicators)
- Compare overlay ("VS" button) — up to 3 compares, normalized %
- Pattern/wave drawings (Head & Shoulders, ABCD, XABCD, 3/5/8 Waves)
- Drag-from-naked-position SL/TP **creation** (currently only edits existing brackets)
- Chart screenshot / share-link

### Deferred to natural phases

- Fundamental indicator overlays (Bucket B) → Phase 18
- Options indicators (Bucket C, including IV) → Phase 12
- Futures indicators (Bucket D) → Phase 14
- Index-breadth indicators (Bucket E) → Phase 18
- Money-flow / Level-2 (Bucket F) → likely never
- Custom user-defined indicator engine (Bucket H) → standalone phase post-v1.0

### Other deferred or dropped

- Futu sidecar VPS migration → Phase 6.1 mini-phase post-v1.0
- Alerts on chart events (price level crossing) → Phase 11 (Alerts engine)
- Bar replay/historical playback for backtesting → Phase 19

---

## Appendix A — File tree summary

### New files (greenfield Phase 9)

```
bar_aggregator/                              # NEW Docker service
├─ Dockerfile
├─ pyproject.toml
└─ app/{main,aggregator,wal,flush,minute_emitter,bar_pubsub,config,metrics}.py

backend/
├─ alembic/versions/
│  ├─ 0023_phase9_timescaledb_extension.py
│  ├─ 0024_phase9_bars_base.py
│  ├─ 0025_phase9_bars_continuous_aggs.py
│  ├─ 0026_phase9_chart_layouts.py
│  └─ 0027_phase9_bar_backfill_jobs.py
├─ app/
│  ├─ api/bars.py
│  └─ services/bar_service.py
└─ tests/
   ├─ integration/test_alembic_002[3-7].py
   └─ unit/test_bar_service.py

proto/broker/v1/broker.proto                 # extend (GetHistoricalBars + HistoricalBar)

sidecar_*/handlers.py                        # add GetHistoricalBars to all 4

frontend/src/
├─ routes/chart.$canonicalId.tsx
└─ features/chart/
   ├─ ChartPage.tsx
   ├─ TradeChart.tsx
   ├─ ChartToolbar.tsx
   ├─ TimeframeBar.tsx
   ├─ IndicatorPicker.tsx
   ├─ DrawingTools.tsx
   ├─ PositionOverlay.tsx
   ├─ ChartContextMenu.tsx
   ├─ ChartLayoutSync.tsx
   ├─ stores/{chartStore,liveTailStore}.ts
   ├─ services/{bars,liveTail,layoutSync}.ts
   ├─ overlays/{longPosition,shortPosition,pitchfork,schiffPitchfork,
   │            modifiedSchiffPitchfork,insidePitchfork}.ts
   └─ indicators/
      ├─ register.ts
      └─ {vwap,ichimoku,alligator,...}.ts    (~45 files)

scripts/empirical/
├─ schwab_history_paper.py
├─ alpaca_history_paper.py
├─ ibkr_history_paper.py
└─ futu_history_paper.py

docker-compose.yml                            # add bar_aggregator service
deploy/vps/docker-compose.yml                 # mirror

CHANGELOG.md                                  # v0.9.0 section (Chunk I)
TASKS.md                                      # mark Phase 9 complete (Chunk I)
CLAUDE.md                                     # fix klineschart→klinecharts typo (Chunk E)
```

### Modified existing files

- `frontend/src/features/positions/PositionRow.tsx` — add View Chart link
- `frontend/src/features/orders/OrderRow.tsx` — same
- `frontend/src/features/watchlist/WatchlistRow.tsx` — same
- `frontend/eslint.config.mjs` — verify `features/chart/` path is permitted by boundaries (no change expected)
- `frontend/package.json` + `pnpm-lock.yaml` — add klinecharts ^10
- `backend/app/main.py` — register `BarService` lifespan + pre-warm cron
- `backend/app/api/__init__.py` — register `bars` router
- `proto/broker/v1/broker.proto` — extend (GetHistoricalBars + HistoricalBar)
- `sidecar_schwab/handlers.py`, `sidecar_alpaca/handlers.py`, `sidecar_ibkr/handlers.py`, `sidecar_futu/handlers.py` — add GetHistoricalBars handler
