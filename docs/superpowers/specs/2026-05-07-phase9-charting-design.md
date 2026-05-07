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
3. **klinecharts ^10.x** integration with **~70 indicators** (27 built-ins + ~45 custom-coded TS, each carrying a citation header).
4. **All klinecharts built-in drawing tools** (~30) + **5 custom overlays** (Long Position, Short Position, Pitchfork variants ×4).
5. **`bar_aggregator/` Docker service** consuming the existing quote bus → 6 sub-1m bucket types → TimescaleDB hypertable writes; flush-ack-based WAL trim; per-channel 250ms publish coalescing; sharding key reserved (`wal:bar_aggregator:{shard}:{instrument_id}`).
6. **`GetHistoricalBars` RPC** on all 4 broker sidecars with hot-30d pre-warm + cold-lazy fetch via `BarService` orchestrator. IBKR per-client token bucket; Schwab 401-retry-once; cross-worker coalescing via `pg_notify` for Phase 24 readiness.
7. **`chart_layouts(instrument_id, payload jsonb, schema_version)` table** (single-tenant; UNIQUE on `instrument_id`; 64KB cap; read-side translator on schema drift; If-Match optimistic concurrency).
8. **TimescaleDB hypertables + retention policies** (1s 7d, 1m 6mo; 10 CAGGs with `end_offset >= bucket_width` and `start_offset < base retention`; `bars_1h`/`bars_1d` retained 5y).
9. **WS extension** — existing `/ws/quotes` gateway pattern extended with `/ws/bars/<canonical_id>/<timeframe>` channel for live tail. Token-via-subprotocol auth; idle timeout 60s; max 20 subs/conn; revision-sequenced envelope for ordering safety.
10. **Pre-requisite migrations 0023a + 0023b**: `instrument_id BIGINT` resolver columns on `positions`/`watchlist_entries`; `tick_size NUMERIC(20,8)` on `instruments` for drag-handle SL/TP precision.
11. **`POST /api/orders/nonce/modify`** endpoint to mint per-modify trade nonces (existing `/api/orders/modify` did not enforce nonces; Phase 9 adds the missing chokepoint).

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

### Pre-requisite migration 0023a — `instrument_id` resolver columns

Phase 7b.1.5's `0010` left `positions.canonical_id TEXT` and gave `watchlist_entries` only `(broker_id, symbol, exchange)` — neither has `instrument_id BIGINT`. Phase 9's active-set query needs a stable resolver. **0023a** (folded into the 0023 chunk) adds:

```sql
-- Strict, indexed FK columns
ALTER TABLE positions          ADD COLUMN instrument_id BIGINT REFERENCES instruments(id) ON DELETE SET NULL;
ALTER TABLE watchlist_entries  ADD COLUMN instrument_id BIGINT REFERENCES instruments(id) ON DELETE SET NULL;

CREATE INDEX positions_instrument_idx         ON positions(instrument_id)         WHERE instrument_id IS NOT NULL;
CREATE INDEX watchlist_entries_instrument_idx ON watchlist_entries(instrument_id) WHERE instrument_id IS NOT NULL;

-- Backfill from existing canonical_id / symbol_aliases — best-effort, NULL for unresolved rows
-- (resolver service handles lazy resolution per Phase 7b.1.5 pattern; no NOT NULL until backfill complete)
UPDATE positions p
   SET instrument_id = i.id
  FROM instruments i
 WHERE i.canonical_id = p.canonical_id
   AND p.canonical_id IS NOT NULL;

UPDATE watchlist_entries w
   SET instrument_id = sa.instrument_id
  FROM symbol_aliases sa
 WHERE sa.source     = w.broker_id
   AND sa.raw_symbol = w.symbol;
```

The columns stay nullable; rows that don't resolve (broker-specific symbols not yet in `symbol_aliases`) are simply absent from the active-set until the existing 7b.1.5 lazy resolver fills them.

### Pre-requisite migration 0023b — `tick_size` on `instruments`

Drag-handle SL/TP needs per-instrument tick precision (BTC at Alpaca = $0.01; HK equities tier from HK$0.001/0.01/0.05; penny stocks $0.0001).

```sql
ALTER TABLE instruments ADD COLUMN tick_size NUMERIC(20,8);
COMMENT ON COLUMN instruments.tick_size IS
  'Minimum price increment. NULL until first observation from broker contract spec.';
```

Sidecar contract-detail responses populate this on first `GetContract` for an instrument; chart drag-handle reads it to snap.

### Base tables

#### `bars_1s` (Alembic 0024) — populated by `bar_aggregator` from quote bus

```sql
CREATE TABLE bars_1s (
  instrument_id    BIGINT        NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  bucket_start     TIMESTAMPTZ   NOT NULL,
  source           TEXT          NOT NULL,           -- 'aggregator-{streamer-source}'
  source_priority  SMALLINT      NOT NULL DEFAULT 99,
  open             NUMERIC(20,8) NOT NULL,
  high             NUMERIC(20,8) NOT NULL,
  low              NUMERIC(20,8) NOT NULL,
  close            NUMERIC(20,8) NOT NULL,
  volume           NUMERIC(20,8),                    -- NULL when source has no trade-tape (IBKR Level-1 quote-only)
  volume_source    TEXT          NOT NULL,           -- 'tape' | 'quote_proxy' | 'none'
  trade_count      INTEGER       NOT NULL DEFAULT 0,
  PRIMARY KEY (instrument_id, bucket_start),
  CONSTRAINT bars_1s_volume_source_chk CHECK (volume_source IN ('tape', 'quote_proxy', 'none')),
  CONSTRAINT bars_1s_volume_consistent_chk CHECK (
    (volume_source = 'none' AND volume IS NULL) OR
    (volume_source <> 'none' AND volume IS NOT NULL)
  )
);
SELECT create_hypertable('bars_1s', 'bucket_start',
  chunk_time_interval => INTERVAL '6 hours');
CREATE INDEX bars_1s_inst_time_idx ON bars_1s (instrument_id, bucket_start DESC);
SELECT add_retention_policy('bars_1s', INTERVAL '7 days');
```

Note: `inserted_at` removed (architect MED #5 — unused for tie-breaking, ~500MB/yr saved at 1000 instruments). `volume` is nullable with `volume_source` discriminator (architect HIGH #1 — IBKR Level-1 doesn't carry trade-tape; aggregator-built volume from quote-only is documented `quote_proxy` and not used for HFT-grade analysis).

#### `bars_1m` (Alembic 0024) — populated by sidecar `GetHistoricalBars` + aggregator minute-emitter

```sql
CREATE TABLE bars_1m (
  instrument_id    BIGINT        NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  bucket_start     TIMESTAMPTZ   NOT NULL,
  source           TEXT          NOT NULL,           -- 'schwab', 'alpaca', 'ibkr', 'futu', 'aggregator-*'
  source_priority  SMALLINT      NOT NULL,
  open             NUMERIC(20,8) NOT NULL,
  high             NUMERIC(20,8) NOT NULL,
  low              NUMERIC(20,8) NOT NULL,
  close            NUMERIC(20,8) NOT NULL,
  volume           NUMERIC(20,8),
  volume_source    TEXT          NOT NULL,
  trade_count      INTEGER       NOT NULL DEFAULT 0,
  PRIMARY KEY (instrument_id, bucket_start),
  CONSTRAINT bars_1m_volume_source_chk CHECK (volume_source IN ('tape', 'quote_proxy', 'none')),
  CONSTRAINT bars_1m_volume_consistent_chk CHECK (
    (volume_source = 'none' AND volume IS NULL) OR
    (volume_source <> 'none' AND volume IS NOT NULL)
  ),
  CONSTRAINT bars_1m_priority_chk CHECK (source_priority IN (1, 2, 3, 4, 99))
);
SELECT create_hypertable('bars_1m', 'bucket_start',
  chunk_time_interval => INTERVAL '7 days');
CREATE INDEX bars_1m_inst_time_idx ON bars_1m (instrument_id, bucket_start DESC);
SELECT add_retention_policy('bars_1m', INTERVAL '6 months');
```

**Source priority assignment (canonical):** `schwab=1, alpaca=2, ibkr=3, futu=4, aggregator-*=99`. The CHECK constraint enforces this; BarService's `_priority_for_source()` is the single mapper:

```python
_SOURCE_PRIORITY: Final[Mapping[str, int]] = {
    "schwab": 1, "alpaca": 2, "ibkr": 3, "futu": 4,
    "aggregator-schwab": 99, "aggregator-alpaca": 99,
    "aggregator-ibkr": 99,   "aggregator-futu": 99,
}
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

### Continuous aggregates (Alembic 0025 — 10 CAGGs, **created in Chunk B-bis after aggregator validates base table shape**)

**Two correctness invariants (architect CRIT #2 + HIGH #5):**
1. `end_offset >= bucket_width` — ensures only **closed** buckets are materialized; otherwise CAGG re-materializes the same bucket on each refresh and disagrees with live-tail Redis publish.
2. `start_offset < base table retention` — prevents CAGG refresh from deleting boundary buckets when `bars_1s` retention drops the source chunk.

| CAGG | Source | Bucket | start_offset | end_offset | schedule_interval | CAGG retention |
|---|---|---|---|---|---|---|
| `bars_5s` | `bars_1s` | 5s | 6 days | 5s | 30s | (bounded by base) |
| `bars_10s` | `bars_1s` | 10s | 6 days | 10s | 30s | (bounded by base) |
| `bars_15s` | `bars_1s` | 15s | 6 days | 15s | 30s | (bounded by base) |
| `bars_30s` | `bars_1s` | 30s | 6 days | 30s | 30s | (bounded by base) |
| `bars_45s` | `bars_1s` | 45s | 6 days | 45s | 60s | (bounded by base) |
| `bars_5m` | `bars_1m` | 5m | 5 months | 5m | 1m | 5 months |
| `bars_15m` | `bars_1m` | 15m | 5 months | 15m | 1m | 5 months |
| `bars_30m` | `bars_1m` | 30m | 5 months | 30m | 5m | 5 months |
| `bars_1h` | `bars_1m` | 1h | 5 months | 1h | 5m | 5 years (own retention policy) |
| `bars_1d` | `bars_1m` | 1d | 5 months | 1d | 1h | 5 years (own retention policy) |

**Pattern (each CAGG):**

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
  sum(volume) AS volume,                 -- nulls coalesce to 0 in sum() — preserves volume_source semantics at the read layer
  sum(trade_count) AS trade_count
FROM bars_1s
GROUP BY instrument_id, time_bucket(INTERVAL '5 seconds', bucket_start);

SELECT add_continuous_aggregate_policy('bars_5s',
  start_offset      => INTERVAL '6 days',
  end_offset        => INTERVAL '5 seconds',
  schedule_interval => INTERVAL '30 seconds');
```

**For `bars_1h` and `bars_1d`** — keep them past the `bars_1m` 6-month base retention via explicit retention policy on the CAGG hypertable (architect LOW #5 syntax pin):

```sql
SELECT add_retention_policy('bars_1d', INTERVAL '5 years');
SELECT add_retention_policy('bars_1h', INTERVAL '5 years');
```

**Live-tail caveat:** the FE never reads CAGGs for the trailing window — live-tail comes from the Redis `bar.<canonical_id>.<tf>` channel. CAGGs serve scrollback only. The 5–60s refresh lag at the leading edge is therefore invisible to users.

### `chart_layouts` (Alembic 0026)

The deployment is **single-tenant** (CLAUDE.md non-goals: "multi-tenant"); no `users` table exists or is planned (CF Access + Google IdP gates the perimeter, not row-level auth). `chart_layouts` is keyed only by `instrument_id`. If multi-tenant ever happens (post-v1.0), a future migration adds `user_id` and rewrites the unique constraint.

```sql
CREATE TABLE chart_layouts (
  id              BIGSERIAL     PRIMARY KEY,
  instrument_id   BIGINT        NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  payload         JSONB         NOT NULL,         -- { indicators[], drawings[], chart_type, default_timeframe, panes }
  schema_version  INTEGER       NOT NULL DEFAULT 1,
  updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  UNIQUE (instrument_id),
  CONSTRAINT chart_layouts_payload_size_chk CHECK (octet_length(payload::text) < 65536)
);
CREATE INDEX chart_layouts_updated_at_idx ON chart_layouts (updated_at DESC);
```

- `updated_at` doubles as the recency signal for the active-set definition.
- `schema_version` evolves the JSONB shape via one-shot Alembic data migrations + a **read-side translator** (architect HIGH #8). Reads NEVER mutate the row. Forward translation happens in `_translate_chart_layout(payload, from_version, to_version)` returning the current shape; PUTs always write at the latest version.
- Hard 64KB cap (architect MED #8) — at 70 indicators × ~200 bytes config + 200 drawings × ~150 bytes = ~44KB worst-case under realistic use.

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

After 0023a backfills `instrument_id` on `positions` + `watchlist_entries` (rows that fail to resolve via canonical_id / symbol_aliases stay NULL and are filtered out). Hard `LIMIT 1000` matches the per-aggregator memory cap (architect MED #1).

```sql
WITH cfg AS (
  SELECT value::int AS recency_days
  FROM app_config
  WHERE namespace = 'charts'
    AND key = 'bar_active_set_recency_days'
)
SELECT instrument_id, MAX(recency_score) AS recency_score
FROM (
  SELECT instrument_id, EXTRACT(EPOCH FROM NOW())::bigint AS recency_score
    FROM positions WHERE instrument_id IS NOT NULL
  UNION ALL
  SELECT instrument_id, EXTRACT(EPOCH FROM NOW())::bigint
    FROM watchlist_entries WHERE instrument_id IS NOT NULL
  UNION ALL
  SELECT instrument_id, EXTRACT(EPOCH FROM updated_at)::bigint
    FROM chart_layouts
    WHERE updated_at > NOW() - (SELECT recency_days FROM cfg) * INTERVAL '1 day'
) sources
GROUP BY instrument_id
ORDER BY recency_score DESC
LIMIT 1000;
```

If real-world load ever exceeds 1000, the aggregator sharding plan (architect MED #10, see §4 below) supersedes the cap.

### Retention summary

- `bars_1s`: 7 days hard-drop
- `bars_1m`: 6 months hard-drop
- CAGGs `bars_5s`–`bars_45s`: bounded by `bars_1s` retention
- CAGGs `bars_5m`–`bars_30m`: bounded by `bars_1m` retention
- CAGGs `bars_1h`, `bars_1d`: 5 years (configured at CAGG level — cheap to keep)

### Migrations summary

| # | Change |
|---|---|
| 0023 | `CREATE EXTENSION timescaledb` (idempotent) |
| 0023a | `instrument_id BIGINT` resolver column + best-effort backfill on `positions` + `watchlist_entries` |
| 0023b | `tick_size NUMERIC(20,8)` column on `instruments` |
| 0024 | `bars_1s` + `bars_1m` hypertables, retention policies, `source_priority` + `volume_source` columns + CHECK constraints |
| 0025 | 10 continuous aggregates with `end_offset >= bucket_width` and `start_offset < base retention` (in **Chunk B-bis** after Chunk B locks aggregator schema) |
| 0026 | `chart_layouts` (single-tenant; UNIQUE on `instrument_id`; 64KB payload cap; read-side translator on schema_version drift) |
| 0027 | `bar_backfill_jobs` (partial unique on pending+in_progress) |

### Storage budget (architect MED #4)

Per-row bytes (PG row overhead ≈ 28 + columns):
- `bars_1s` row ≈ 28 + 8 (instrument_id) + 8 (bucket_start) + 24 (source text) + 2 (priority) + 5×17 (NUMERIC) + 4 (volume_source label) + 4 (trade_count) ≈ **150 bytes**.
- 1 active instrument × 86,400 s/day × 150 bytes = **~12.4 MB/day**.
- 100 instruments × 7-day retention = **~8.7 GB**.
- 1000 instruments × 7-day retention = **~87 GB**.

`bars_1m` ~150 bytes/row × 1440 rows/day × 100 instruments × 6mo = **~3.9 GB**. CAGGs add ≤30% on top.

**Hard budget for NUC PG storage: 200 GB allocated headroom; 1000-instrument operating ceiling is OK.** Above 1000 needs aggregator sharding (architect MED #10) AND/OR shorter `bars_1s` retention. The Chunk I E2E suite includes a perf-smoke that measures actual disk usage at 100-instrument steady state and projects.

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

**Crash recovery via WAL (architect CRIT #5).** Every tick writes to a Redis Stream `wal:bar_aggregator:{shard}:{instrument_id}` *before* bucket mutation. Trim semantics are **flush-ack-based, not time-based**: aggregator only `XTRIM MINID` entries the flush has confirmed in PG.

Durability bound is honestly stated:
- Tick → WAL: synchronous `XADD` round-trip (no `WAIT` since Redis is single primary; ≈1ms).
- Aggregator crash → Docker restart → replay all unacked entries from oldest WAL ID. **Zero-tick loss within Redis uptime.**
- Redis crash (Redis is non-replicated) → bounded by `redis-py` outstanding ack queue at moment of crash. Documented loss bound: ≤ 1 batch (≤ 2s × tick rate per active instrument).
- Aggregator down longer than `wal:* MAXLEN ~ 50000` per instrument (≈ 90 min at 10 ticks/s) → emit `CRITICAL` log + `bar_aggregator_wal_truncated_total{instrument}` counter; on boot, refuse replay if oldest WAL entry has gap to last-flushed entry > `2× FLUSH_INTERVAL_MS`.

Metrics:
- `bar_aggregator_wal_depth_bytes` — alert at 80% of `redis.maxmemory`.
- `bar_aggregator_wal_lag_seconds` — gauge of (now − last-flushed-entry-ts).
- `bar_aggregator_wal_truncated_total{instrument}` — counter of detected gaps.

**Per-(instrument, tf) live-tail conflation (architect CRIT #3).** Per-tick partial-bar publish is **gated by a 250ms coalescing window per channel**:

```python
class _ChannelCoalescer:
    """One per (instrument_id, tf). Coalesces N ticks within 250ms into one publish."""
    def __init__(self, channel: str, max_interval_ms: int = 250) -> None:
        self._channel = channel
        self._max_interval = max_interval_ms / 1000
        self._latest: BucketSnapshot | None = None
        self._last_publish_at: float = 0.0
        self._task: asyncio.Task[None] | None = None
```

The aggregator updates `_latest` on each tick; a deferred `_task` publishes after `max_interval` has elapsed since the last publish. **Final** (closed-bucket) publishes bypass coalescing and emit immediately with `revision = MAX_INT` (architect HIGH #2).

Metric: `bar_aggregator_partial_publish_ratio` = publishes / ticks. Target ratio ≤ 0.4 (i.e. at least 60% of ticks coalesced into a later publish).

**FE WS gateway pattern (matches `phase7b1_shipped` INV-Q-1).** The gateway has **one** Redis subscriber per worker; FE WS connections fan in-process. The aggregator's publishes are never re-subscribed by the publisher. This is the same single-worker loopback-suppression rule as `backend/app/services/quotes/engine.py`.

**Memory bounds.** In-flight buckets `dict[(instrument_id, source), BucketState]` capped to active-set size (LIMIT 1000 in §3 query). Idle entries evicted after 5 min of no quotes. Hard cap at 1000 active instruments per aggregator. Above this needs sharding (below).

**Sharding plan (architect MED #10).** WAL stream key is `wal:bar_aggregator:{shard}:{instrument_id}`. With `shard = instrument_id % N`, sharded aggregators run as `bar_aggregator_0..N-1` containers; each subscribes to the shard-modulo subset of `quote.*` topics. Phase 9 ships `N = 1` (single shard); the namespace is reserved so post-v1.0 horizontal scaling doesn't require breaking-change migration. Backend WS gateway routes by shard; pubsub key `bar.<canonical_id>.<tf>` is unsharded so subscribers don't need shard awareness.

**Backpressure.** If `bars_1s` flush falls behind, log + emit `bar_aggregator_flush_lag_seconds`. Healthcheck fails at lag > 10s; Docker restart picks it up.

**WG split tolerance (architect MED #3, new failure-matrix row).** PG sits on NUC over WG; aggregator on VPS. If WG drops, aggregator can't write PG but Redis (VPS-local) is fine. Behavior:
- Pause `bars_1s` flush on PG `OperationalError`.
- WAL accumulates (bounded by `MAXLEN ~ 50000` per instrument).
- Live-tail Redis pub/sub continues (FE charts keep painting).
- Auto-resume flush on first successful PG ping.
- Metric `bar_aggregator_pg_unreachable_seconds` increments while paused; alert at 60s.
- After 5 minutes paused, healthcheck fails → Docker restart → on-boot WAL replay handles the buffered ticks.

### `backend/app/services/bar_service.py` (new orchestrator)

```python
class BarService:
    async def get_bars(canonical_id, timeframe, start, end, limit=10000, cursor=None) -> BarPage:
        # 1. Resolve instrument_id from canonical_id via instruments + symbol_aliases
        # 2. Detect cache gap: query bars_{tf} for [start, end); compute missing ranges
        # 3. If gaps exist and tf >= '1m':
        #    a. Determine source via app_config.bar_source_priority for asset_class
        #    b. UPSERT bar_backfill_jobs row, capturing was_new (partial-unique-pending)
        #    c. If was_new: this worker fetches via sidecar GetHistoricalBars (chunked loop)
        #       Otherwise: this worker waits via _wait_for_job(job_id) — pg_notify channel
        #         'bar_backfill_done' OR poll status every 250ms (cap 16s)
        #    d. After fetch (or wait): UPSERT into bars_1m with priority encoding
        #    e. Mark job done; pg_notify 'bar_backfill_done' with payload=job_id
        # 4. Return paginated rows with next_cursor (sub-1m never backfills — cache-only)

    async def pre_warm_active_set() -> None:
        # Cron: backend startup + nightly post-close (per asset-class market clock)
        # active_set = (active-set query — see §3, LIMIT 1000)
        # for inst in active_set:
        #   for tf in ('1m','1h','1d'):  # 5m/15m/30m derived via CAGG, no fetch
        #     last_done = bar_backfill_jobs.where(...).order_by(finished_at desc).first()
        #     gap = (last_done.range_end if last_done else now-30d, now)
        #     await get_bars(inst.canonical_id, tf, *gap)  # writes through cache
        # Pacing: yields between instruments via `await asyncio.sleep(0)` to share event loop.

    async def subscribe_live_tail(canonical_id, timeframe) -> AsyncIterator[Bar]:
        # WS handler: subscribe Redis bar.<canonical_id>.<tf>
        # Yield each pubsub message as a parsed Bar payload (revision-sequenced)
```

**Cross-worker coalescing (architect CRIT #4).** Phase 24 will split backend to N>1 uvicorn workers. The pattern:

1. **Same-worker fast path:** in-process `dict[(instrument_id, tf, gap_start, gap_end), asyncio.Event]`. Concurrent first-callers on the same key wait on the shared `Event`. This is the only place a process-local primitive is acceptable, and only as an optimization.
2. **Cross-worker chokepoint:** `bar_backfill_jobs` partial-unique index. UPSERT returns `was_new`:
   - `was_new=true`: this worker is the primary fetcher.
   - `was_new=false`: this worker waits via PostgreSQL `LISTEN bar_backfill_done` channel; on notify, checks if `job_id` matches; if not, continues waiting. Bounded wait = 16s (gRPC deadline 15s + 1s grace).
3. **Notify on completion:** primary fetcher executes `pg_notify('bar_backfill_done', job_id::text)` after marking job `done` or `failed`.
4. **Fallback:** if `LISTEN` not available (e.g., during connection-pool churn), poll `bar_backfill_jobs.status` every 250ms with the same 16s ceiling.

Metric `bar_service_cross_worker_wait_seconds` (histogram) tracks contention.

**Coalescing primitive note:** the architect correctly flagged that the previous "per-account `asyncio.Lock`" reference was misleading (`Lock` = mutual exclusion; `Event` = broadcast). For request-coalescing where N first-callers want the same result, **`asyncio.Event` is the right primitive** (set on completion → all waiters proceed).

**Chunked historical fetch (architect MED #2).** `GetHistoricalBarsResponse.truncated=true` triggers BarService's chunk loop:

```python
async def _fetch_with_chunks(canonical_id, tf, start, end, sidecar) -> list[HistoricalBar]:
    bars: list[HistoricalBar] = []
    cursor = start
    for chunk_idx in range(100):                    # hard cap: 100 chunks per gap-fill
        resp = await sidecar.GetHistoricalBars(
            canonical_id=canonical_id, timeframe=tf,
            range_start=cursor, range_end=end,
            limit=1000,                              # per-broker pacing budget
        )
        bars.extend(resp.bars)
        if not resp.truncated or not resp.bars:
            return bars
        cursor = resp.bars[-1].bucket_start + tf_to_interval(tf)
    raise BarFetchTooLarge(f"chunked fetch exceeded 100 chunks for {canonical_id}/{tf}")
```

**Cursor pagination encoding (architect MED #6).** With single-row PK `(instrument_id, bucket_start)`:

```python
cursor = base64url(json.dumps({"v": 1, "last_bucket_start": "2026-04-30T15:30:00Z"}))
# Query: WHERE bucket_start < $cursor.last_bucket_start
#        ORDER BY bucket_start DESC LIMIT $limit
# next_cursor = base64url({"v": 1, "last_bucket_start": <oldest_in_page>})  if total > limit
```

`v` is the cursor schema version for forward-compat.

### `backend/app/api/bars.py` (new router)

```
GET    /api/bars?canonical_id&timeframe&start&end&limit=10000&cursor=...   → BarPage
GET    /api/chart/layouts/:instrument_id                                    → ChartLayout | 404 (read-side translator)
PUT    /api/chart/layouts/:instrument_id  (body, If-Match etag header)      → ChartLayout (etag = updated_at ISO8601)
DELETE /api/chart/layouts/:instrument_id                                    → 204
POST   /api/orders/nonce/modify  body={order_id}                            → {nonce, expires_at}  (architect HIGH #7)
WS     /ws/bars/:canonical_id/:timeframe                                    → live-tail Bar stream
```

**Auth (architect MED #9).** Existing JWT middleware on REST. WS handshake mirrors `/ws/quotes`:
- Token-on-connect via `Sec-WebSocket-Protocol: bearer.<jwt>` subprotocol.
- Idle timeout 60s (server-initiated PING; client must reply within 30s).
- Max **20 subscriptions per connection** (rate-limit knob in `app_config`).
- 429 close-frame on attempt-21st subscription with reason `subscription_limit_exceeded`.

**Modify-nonce flow (architect HIGH #7).** Drag-handle SL/TP currently piggybacks on `/api/orders/modify`'s nonce semantics, which the existing endpoint at `backend/app/api/orders.py:247` does NOT consume. Phase 9 adds the missing nonce mint:
- `POST /api/orders/nonce/modify` returns a fresh nonce keyed `nonce:modify:{order_id}` in Redis with TTL 30s.
- ConfirmDialog requests on **dialog open** (not on submit — avoids double-mint if user cancels).
- `POST /api/orders/modify` now requires the nonce; `redis.GETDEL("nonce:modify:{order_id}:{nonce}")` consumes it (matches existing OCO pattern at `orders.py:411-416`).
- 412 if nonce missing/expired.

**Optimistic concurrency on chart_layouts (architect MED #13).** PUT requires `If-Match: <etag>` (etag = `updated_at` ISO8601). Backend rejects 412 on mismatch; FE merges from server state and prompts user to reconcile (rare in single-tenant single-user but cheap to implement).

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

- **`sidecar_schwab/handlers.py`** — `schwabdev` `pricehistory` endpoint (CHART_EQUITY) — generous quota, free for US equities. Catches `401 invalid_token` once + retries after re-acquiring from `app_secrets` (architect MED #3 row 4).
- **`sidecar_alpaca/handlers.py`** — `alpaca-py` `StockHistoricalDataClient.get_stock_bars` / `CryptoHistoricalDataClient.get_crypto_bars`.
- **`sidecar_ibkr/handlers.py`** — `ib_async.reqHistoricalDataAsync` with **per-client-id token bucket** (architect HIGH #3): capacity 50, refill 50/600s. Reserves 10 reqs/window for ad-hoc cold fetches by users. BarService's pre-warm cron `await sidecar.acquire_pacing_token()` blocks if bucket empty. Pre-warm staggered across 4 IBKR clients via `instrument_id % 4`. **Source priority hard rule:** for US equities, BarService skips IBKR entirely during pre-warm if Schwab or Alpaca is healthy — IBKR is fallback only when free-tier sources fail.
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

See **Flow 6 in §6** for the full data flow including the per-leg `pending_modify_id` state machine, modify-nonce mint, and OrderEvent reconciliation. Key UX rules:

1. Open positions for the current instrument render as a Long/Short Position overlay (entry price line + draggable SL/TP boxes if a bracket exists; v0.9.0 doesn't allow drag-to-create on naked positions — that's deferred to v0.9.1).
2. **Tick snapping (architect MED #12):** drag target snaps to `instruments.tick_size` (per-instrument from broker contract spec; null until first observation). Confirm dialog displays the tick boundary explicitly: e.g. "$184.99 (rounded to $0.01 tick)".
3. **Single-flight per leg:** while `pending_modify_id[leg_id]` is set, drag is disabled, handle is yellow ghost with spinner. The state clears on (a) matching OrderEvent, (b) 5s timeout (then fallback `GET /api/orders/{leg_id}`), or (c) explicit cancel.
4. **Touch parity:** unified `pointerdown/pointermove/pointerup`. On mobile, `tick_size` snapping is more important since touch precision is coarser.
5. **Confirm dialog requests nonce on open** (not on submit) via `POST /api/orders/nonce/modify {order_id}` — avoids double-mint if user cancels.

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

Live-tail responsiveness is decoupled from durability. The aggregator publishes coalesced partial-bar updates per channel; the batch-flush to `bars_1s` writes only **closed** buckets at 1s boundaries.

**Message envelope (architect HIGH #2):**
```json
{
  "canonical_id": "AAPL.US",
  "tf":           "1s",
  "bucket_start": "2026-05-07T15:30:00Z",
  "revision":     12,                  // monotonic per (canonical_id, tf, bucket_start)
  "partial":      true,                // false ⇒ revision = MAX_INT, FE locks the bucket
  "ohlcv":        { "o": "...", "h": "...", "l": "...", "c": "...", "v": "...", "trade_count": 7 }
}
```

FE `liveTailStore` discards messages with `revision <= last_seen[bucket_start]`. On `partial=false`, store snaps to canonical close and disables further updates for that bucket.

```
sidecar_*_streamer publishes quote.<src>.<canonical_id> on Redis
 → bar_aggregator subscribes; updates in-mem 1s bucket; WAL-write tick
 ├─ COALESCED PARTIAL PUBLISH (per-channel 250ms window):
 │  publish bar.<canonical_id>.1s {partial=true, revision=N+1}
 │  → backend /ws/bars/<id>/<tf> consumer fans in-process to FE WS conns
 │  → klinecharts updateOrAddData() on the latest candle  [latency ≤ 200ms tick→paint]
 └─ EVERY 1s ON BOUNDARY (architect HIGH #4): flush only CLOSED buckets where bucket_end < now
    → batch-COPY into bars_1s; XTRIM MINID corresponding WAL entries
    → publish bar.<canonical_id>.1s {partial=false, revision=MAX_INT}
    → on flush-ack, BarService /ws/bars consumer re-emits final to FE for consolidation
```

In-progress (still-open) buckets live only in memory + WAL — they are **never** in `bars_1s`. BarService's REST `/api/bars` query for the trailing-2s window must consult `bar_aggregator`'s `/internal/in_flight_bucket?canonical_id=...&tf=...` endpoint or simply request via the live-tail pub/sub. The FE implementation only ever reads in-flight via WS, so the REST path doesn't need to handle this.

**WS reconnect repair:** on reconnect, FE refetches the trailing 2 closed buckets via REST to repair any gap-during-disconnect.

### Flow 5 — On-minute-boundary 1m emission (no broker historical for newest minute)

```
At HH:MM:00, bar_aggregator's minute_emitter:
 → for each active instrument: aggregate bars_1s in [HH:MM-1, HH:MM) → 1m bar
 → UPSERT into bars_1m with source='aggregator-{src}' priority=99
 → publish bar.<canonical_id>.1m
 → 5–30 minutes later, sidecar GetHistoricalBars (next pre-warm tick) overwrites with priority 1–4
```

### Flow 6 — Drag SL/TP on open position (architect HIGH #6 + HIGH #7)

PositionOverlay maintains a per-bracket-leg `pending_modify_id: Map<leg_id, {nonce, target_price, started_at}>`. While a leg is `pending`, its handle becomes a yellow ghost with a spinner, and dragging is disabled. Optimistic local price never persists past the next OrderEvent — server is authoritative.

```
FE: PositionOverlay sees user drag SL handle from $190 → $185
 → on pointerdown: snap target to instrument.tick_size; show ghost handle at target
 → on pointerup at $185:
   ├─ POST /api/orders/nonce/modify {order_id: <leg_id>}  → {nonce, expires_at}
   └─ open ConfirmDialog(side, qty, $190 → $185, est_loss=..., expires_at)
 → user clicks Confirm
   ├─ pending_modify_id[leg_id] = {nonce, target_price: 185, started_at: now}
   ├─ handle becomes yellow ghost + spinner; further drag disabled
   └─ POST /api/orders/modify  (with nonce — existing CSRF flow)
      body: { order_id: <bracket_sl_leg_id>, stop_price: 185.00, nonce: ... }

 → backend: nonce GETDEL; if missing → 412
   → Sidecar.ModifyOrder → broker
   → broker accepts → OrderEvent {modify_id, status=modified, stop_price=185}

 → FE: existing /ws/orders consumer dispatches OrderEvent
   ├─ if event.modify_id == pending_modify_id[leg_id].nonce:
   │   - clear pending state
   │   - snap handle to event.stop_price (server's truth)
   │   - re-enable drag
   └─ else (rejection or stale): toast broker error; revert handle to last-known-good

 Failure paths:
   - Nonce expired → 412 → toast "drag expired, try again"; revert
   - Broker rejects (e.g., stop would trigger immediately) → toast; revert
   - 5s timeout with no OrderEvent → fallthrough: GET /api/orders/{leg_id}, snap to authoritative state
```

Per-leg drag is single-flight: the `pending_modify_id` Map prevents a second drag from issuing a competing `ModifyOrder` until the first event lands or times out.

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

### A1 — klinecharts built-ins (verified against v9.8.12; **re-verify against v10 at scaffold time** — architect MED #7)

MA, SMA, EMA, BBI, AO, CCI, KDJ, MACD, MTM, PSY, ROC, RSI, TRIX, WR, BOLL, BIAS, SAR, VOL, OBV, PVT, VR, EMV, AVP, DMI, DMA, BRAR, CR.

**Verification step in Chunk E:** `grep 'name:' node_modules/klinecharts/dist/index.esm.js | sort -u` and reconcile against this list. Any klinecharts v10 built-in renames or removals get reflected in the inventory before Chunk F starts (custom indicators).

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

**Citation requirement (architect MED #7).** Each custom indicator's `*.ts` file MUST start with a header comment citing the canonical reference used to derive `calc()`. Without citation, golden-vector tests will diverge between Codex generations of the same indicator. Header format:

```ts
/**
 * VWAP — Volume-Weighted Average Price
 * Reference: https://www.tradingview.com/pine-script-reference/v5/#fun_ta.vwap
 *            https://en.wikipedia.org/wiki/Volume-weighted_average_price
 * Cross-validated against: klinecharts.VOL / OBV (volume conventions)
 */
```

Codex dispatch prompts inject this requirement; reviewer chain rejects custom indicator files without a `Reference:` line.

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
| Quote-bus producer (sidecar streamer) silent — no quotes on a previously-active symbol | `bar_aggregator_idle_seconds{instrument}` gauge > 60s | FE `liveTailStore` shows "stale" badge after 60s; chart freezes with last partial. Aggregator increments `bar_aggregator_idle_total{instrument}` counter for alerting |
| TimescaleDB extension version mismatch on PG-18 (architect risk #1) | Alembic 0023 fails on `CREATE EXTENSION` | Fallback path: feature-flag `app_config.charts.timescale_enabled = false` enables vanilla PG declarative range partitioning by `bucket_start` week; CAGGs replaced by hand-rolled materialized views refreshed via cron. Documented as ~3–5 day rework |
| WG tunnel split between VPS (backend + aggregator) and NUC (PG + IBKR/Futu sidecars) | aggregator catches `OperationalError` on PG flush | Aggregator pauses flush, accumulates ticks in WAL (bounded by `MAXLEN ~ 50000` per instrument); live-tail Redis pub/sub continues; auto-resume on PG ping success; `bar_aggregator_pg_unreachable_seconds` gauge alerts at 60s; healthcheck fails at 5min triggering Docker restart + WAL replay |
| Schwab token rotation mid-fetch | sidecar catches `401 invalid_token` during paginated chunk | Sidecar re-acquires token from `app_secrets`, retries the failed chunk once; if still 401, returns gRPC `UNAUTHENTICATED` with details; BarService marks job failed and surfaces in next pre-warm cycle |
| `pg_dump` blocks CAGG refresh window | Timescale CAGG `last_run_started_at` lag > 5min while pg_dump is running | Schedule `pg_dump` outside CAGG refresh windows in cron config (NUC PowerShell scheduled task); document the conflict in `docs/NETWORK.md`. If unavoidable, switch to `pg_basebackup` which doesn't take metadata locks |

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

**Cred isolation (architect MED #11).** Empirical scripts use a separate **paper** namespace in `app_secrets`:
- `paper.schwab.app_key` / `paper.schwab.app_secret` (distinct from `schwab.app_key`)
- `paper.alpaca.api_key` / `paper.alpaca.api_secret`
- IBKR: paper-account login already separate (4 gateways already split prod/paper).
- Futu: paper trading creds via OpenD's paper mode.
The Chunk C empirical-script PR adds these secret namespaces; nightly self-hosted GHA runner reads only `paper.*` keys.

### Per-chunk reviewer composition (architect LOW #4)

| Chunk | Reviewers (always: spec-compliance + code-reviewer + security-reviewer) | + |
|---|---|---|
| A | base | python-reviewer (sonnet), database-reviewer (sonnet) |
| B | base | python-reviewer, silent-failure-hunter (sonnet — WAL replay correctness) |
| B-bis | base | database-reviewer (CAGG correctness focus) |
| C | base | python-reviewer ×4 sidecars (parallel) |
| D | base | python-reviewer, database-reviewer (cross-worker coalescing), security-reviewer focus on nonce |
| E | base | typescript-reviewer (haiku) |
| F1 / F2 | base | typescript-reviewer (golden-vector test correctness) |
| G | base | typescript-reviewer + security-reviewer focus on drag/nonce/CSRF |
| H | base | typescript-reviewer + database-reviewer (If-Match + JSONB translator) |
| I | base | full chain + ARCHITECT-REVIEW once at end (post-shipment retrospective per `feedback_review_per_chunk`) |

Model routing per CLAUDE.md: spec-compliance/python-reviewer/typescript-reviewer → haiku; code-reviewer/security-reviewer/database-reviewer/silent-failure-hunter → sonnet; ARCHITECT-REVIEW → opus.

---

## 11. Migration & Rollout

### Alembic chain (7 new migrations)

| # | Change | Lands in chunk |
|---|---|---|
| 0023 | `CREATE EXTENSION timescaledb` (idempotent) | A |
| 0023a | `instrument_id BIGINT` resolver columns + best-effort backfill on `positions`/`watchlist_entries` | A |
| 0023b | `tick_size NUMERIC(20,8)` on `instruments` | A |
| 0024 | base hypertables `bars_1s` + `bars_1m` with retention, PK `(instrument_id, bucket_start)`, `source_priority` + `volume_source` + CHECK constraints | A |
| 0026 | `chart_layouts` (single-tenant; UNIQUE on `instrument_id`; 64KB cap) | A |
| 0027 | `bar_backfill_jobs` (partial unique on pending+in_progress) | A |
| 0025 | 10 CAGGs with `end_offset >= bucket_width` and `start_offset < base retention`; CAGG retention for `bars_1h`/`bars_1d` | **B-bis** (after Chunk B locks aggregator schema) |

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

### Rollout plan (10 chunks; resequenced per architect HIGH #9 + LOW #10)

1. **Chunk A — Foundation:** 0023, 0023a (instrument_id resolver), 0023b (tick_size), 0024 (base hypertables), 0026 (chart_layouts), 0027 (bar_backfill_jobs); BarService skeleton; TimescaleDB pinned. Reviewer chain.
2. **Chunk B — bar_aggregator service:** Docker service, WAL flush-ack-trim, per-channel coalescing window, flush-on-closed-bucket-only, sharding key `wal:bar_aggregator:{shard}:{instrument_id}` (N=1), WG-split tolerance. Reviewer chain.
3. **Chunk B-bis — CAGG creation:** 0025 (10 CAGGs with corrected `end_offset >= bucket_width` + `start_offset < base retention`); only after Chunk B's flush has been validated against real `bars_1s` data. Reviewer chain (database-reviewer focus).
4. **Chunk C — sidecar GetHistoricalBars:** all 4 sidecars, proto extension, IBKR per-client token bucket + jittered scheduling, Schwab 401-retry-once, contract tests, empirical scripts. Reviewer chain.
5. **Chunk D — Backend orchestration:** BarService cross-worker coalescing (pg_notify + status-poll fallback), gap detection, source priority hard rule (skip IBKR for US equities during pre-warm if Schwab/Alpaca healthy), pre-warm cron, `/api/bars` cursor pagination, `/api/chart/layouts/*` with read-side translator + If-Match, `/api/orders/nonce/modify`, `/ws/bars` with handshake + 20-sub limit, dual-emission revision sequencing on FE WS gateway. Reviewer chain.
6. **Chunk E — FE chart feature:** TradeChart wrapper, IndicatorPicker, DrawingTools (built-ins only), TimeframeBar, ChartPage route, inline links from positions/orders/watchlist; klinecharts v10 inventory verification (re-grep built-ins, reconcile §7); CLAUDE.md `klineschart→klinecharts` typo fix. Reviewer chain.
7. **Chunk F1 — Custom indicators (first 22):** Moving averages + Volatility/channels groups; golden-vector tests with citations enforced. Reviewer chain.
8. **Chunk F2 — Custom indicators (remaining ~23):** Momentum/oscillators + Volume/flow + Pattern/signal + Misc groups; same standards. Reviewer chain.
9. **Chunk G — Drag-handle SL/TP:** PositionOverlay with per-leg `pending_modify_id` state machine, modify-nonce mint flow, OrderEvent reconciliation, tick-size snapping, mobile touch path. Reviewer chain (security-reviewer focus on nonce flow).
10. **Chunk H — Layout persistence + mobile parity:** chart_layouts CRUD, debounced sync with If-Match, read-side translator implementation, mobile toolbar collapse (5–7 most-used drawings: Trend Line, Horizontal Line, Fib Retracement, Rectangle, Text, Long/Short Position + Indicator picker access). Reviewer chain.
11. **Chunk I — E2E + perf + close-out:** Playwright flows incl. aggregator crash injection + WG-split simulation; perf smoke tests; storage-budget projection at 100-instrument steady state; CHANGELOG/TASKS/CLAUDE.md updates; tag v0.9.0.

Estimated cadence: ~1 chunk per day with reviewers; F1 + F2 split addresses architect LOW #10 (45 indicators don't fit 1 day). **Total: ~2–2.5 weeks** (slightly longer than original 1.5–2w estimate to accommodate B-bis and split F).

### Codex delegation pattern (per `feedback_codex_delegation.md`)

Each chunk's source + tests dispatched to Codex via `gpt-5-codex`; Opus main thread orchestrates, runs reviewer chain, commits. Codex defaults patterns A–G applied (per `codex_defaults.md`).

### Feature-flag gating

None for the user surface. Phase 9 is greenfield — nothing to gate against. `app_config.charts.enabled = true` exists as a kill-switch in case of post-deploy issue.

---

## 12. Risks & Deferred Items

### Top risks

1. **TimescaleDB v2.17+ on PG-18 compatibility.** PG-18 is recent; verify support matrix at scaffold time. Mitigation: feature flag `app_config.charts.timescale_enabled = true|false`; vanilla-PG fallback path uses declarative range partitioning by `bucket_start` week + cron-refreshed materialized views (~3–5 day rework).
2. **Aggregator memory + CPU under symbol fan-out** (architect MED #10). 1000 active instruments × 6 timeframes × ~200 bytes/bucket ≈ 1.2 MB state; CPU bottleneck on per-tick traversal. Mitigation: WAL stream namespace `wal:bar_aggregator:{shard}:{instrument_id}` reserves the sharding key; Phase 9 ships N=1, but post-v1.0 horizontal scaling doesn't need a breaking-change migration. Evict idle buckets after 5 min.
3. **IBKR pacing violations** (architect HIGH #3). IBKR caps ~60 historical reqs / 10min **per client ID**, and the project runs 4 clients. Mitigation: per-sidecar token bucket (capacity 50, refill 50/600s, reserve 10 for ad-hoc), jittered scheduling, stagger pre-warm via `instrument_id % 4` across clients, **hard rule:** for US equities skip IBKR entirely during pre-warm if Schwab/Alpaca are healthy.
4. **Klinecharts custom indicator perf.** 45 custom indicators in worst-case all enabled on 30k bar dataset, ~1.4M ops/recalc. Probably fine; smoke-test in Chunks F1/F2.
5. **CAGG refresh lag during heavy ingest.** Mitigated by `end_offset >= bucket_width` + `start_offset < base retention` (architect CRIT #2 + HIGH #5).
6. **Mobile drag handle precision** (architect MED #12). Mitigation: `instruments.tick_size` snap; show snapped price during drag; explicit confirm.
7. **Trade nonce mint for modify path** (architect HIGH #7). Mitigation: new `POST /api/orders/nonce/modify` mints; ConfirmDialog requests on dialog open; Redis `GETDEL` consumes on submit; 30s TTL prevents reuse.
8. **WAL durability bound** (architect CRIT #5). Honestly stated: zero-tick loss within Redis uptime; ≤1 batch loss on Redis crash (Redis is single primary). 5-minute / `MAXLEN ~ 50000` retention is replaced by **flush-ack-based** trim. On boot, refuse replay if oldest WAL entry has gap to last-flushed > `2× FLUSH_INTERVAL_MS` (= 4s) — emit CRITICAL log + counter; document silent-gap window.
9. **CAGG retention vs base retention** (architect HIGH #5). Mitigated by `start_offset < base retention` invariant in §3 CAGG table.
10. **Drag-handle race against existing OrderEvent stream** (architect HIGH #6). Mitigated by per-leg `pending_modify_id` Map + 5s timeout fallthrough to `GET /api/orders/{leg_id}`.
11. **Cross-worker coalescing in Phase 24 multi-worker future** (architect CRIT #4). Phase 9 ships single-worker but `bar_backfill_jobs` partial-unique + `pg_notify('bar_backfill_done')` are the cross-worker chokepoint. Phase 24 inherits this without rework.
12. **Live-tail message ordering** (architect HIGH #2). Mitigated by `revision` field in WS envelope; FE discards stale revisions; `partial=false` carries `revision = MAX_INT`.

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
