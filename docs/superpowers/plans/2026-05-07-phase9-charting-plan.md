# Phase 9 — Charting v1 + Bar Aggregator + Historical Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Futubull-class single-symbol charting plus the bar infrastructure (live aggregator + historical store) future phases depend on.

**Architecture:** New `bar_aggregator/` Docker service consumes existing Redis quote bus, writes to TimescaleDB (`bars_1s` + `bars_1m` hypertables + 10 CAGGs); BarService orchestrator does hot-30d pre-warm + cold-lazy fetch via new `GetHistoricalBars` RPC on all 4 sidecars; FE uses klinecharts ^10 with ~70 indicators (27 built-in + ~45 custom-coded TS) and drag-handle SL/TP via revision-sequenced WS live-tail.

**Tech Stack:** Python 3.14, FastAPI, asyncpg, Redis Streams, TimescaleDB ≥ 2.17 on PG-18, gRPC, React 19, klinecharts ^10, Vitest + Playwright.

**Spec:** `docs/superpowers/specs/2026-05-07-phase9-charting-design.md` (commit `aa006b1`).

**Codex delegation:** Per `feedback_codex_delegation.md` + `codex_defaults.md`. Codex writes source AND tests in one delegation. Each Codex prompt **must** inline the relevant spec slice (per `feedback_reviewer_spec_inline.md`). For trivial commits (test-only, doc-only, format-only), skip the reviewer chain (per `feedback_token_flow_optimisation.md`).

**Per-chunk reviewer rule** (per `feedback_review_per_chunk.md`): At end of each chunk (≥5 commits), run the full 5-reviewer chain. Composition per spec §10 table.

---

## File Structure

Per spec Appendix A. Net new components:

| Layer | Files | Responsibility |
|---|---|---|
| Service | `bar_aggregator/app/{main,aggregator,wal,flush,minute_emitter,bar_pubsub,config,metrics}.py` | Quote-bus → 1s buckets → WAL → flush hypertable → live pub/sub |
| Schema | `backend/alembic/versions/0023*.py`, `0024_phase9_bars_base.py`, `0025_phase9_bars_continuous_aggs.py`, `0026_phase9_chart_layouts.py`, `0027_phase9_bar_backfill_jobs.py` | TimescaleDB ext + hypertables + CAGGs + chart_layouts + backfill jobs |
| Backend | `backend/app/services/bar_service.py`, `backend/app/api/bars.py` | Orchestrator (gap detect, source priority, cross-worker coalesce); REST + WS routers |
| Proto | `proto/broker/v1/broker.proto` | `GetHistoricalBars` RPC + `HistoricalBar` message |
| Sidecar | `sidecar_{schwab,alpaca,ibkr,futu}/handlers.py` | `GetHistoricalBars` per-broker impl |
| FE | `frontend/src/features/chart/**`, `frontend/src/routes/chart.$canonicalId.tsx` | `<TradeChart>`, indicator/drawing/overlay/store/service modules |
| Empirical | `scripts/empirical/{schwab,alpaca,ibkr,futu}_history_paper.py` | Paper-broker validation |
| Compose | `docker-compose.yml`, `deploy/vps/docker-compose.yml` | Add `bar_aggregator` service |

---

## Task Inventory (11 chunks · 53 tasks)

| Chunk | Tasks | Description |
|---|---|---|
| A — Foundation | 1–10 | Migrations 0023/0023a/0023b/0024/0026/0027, app_config seed, BarService skeleton, lifespan wiring, active_set query |
| B — bar_aggregator service | 11–17 | Docker service, WAL, flush, coalescer, minute_emitter, sharding, WG-split tolerance |
| B-bis — CAGGs | 18 | Migration 0025 (10 CAGGs) — runs only after Chunk B validates `bars_1s` shape |
| C — Sidecars | 19–25 | proto extension, 4 sidecar handlers, IBKR token bucket, Schwab 401-retry, empirical scripts |
| D — Backend orchestration | 26–33 | gap detect, cross-worker `pg_notify`, pre-warm cron, `/api/bars`, layouts router, `/ws/bars`, modify-nonce mint, WG-split tolerance |
| E — FE chart feature | 34–40 | `klinecharts@^10` install, route, TradeChart, Toolbar, TimeframeBar, IndicatorPicker, DrawingTools (built-ins), CLAUDE.md typo fix |
| F1 — Custom indicators (22) | 41 | Moving-averages + Volatility/channels groups (Codex batch with citations) |
| F2 — Custom indicators (23) | 42 | Momentum/oscillators + Volume/flow + Pattern/signal + Misc groups |
| G — Drag-handle SL/TP | 43–45 | PositionOverlay state machine, modify-nonce + ConfirmDialog, OrderEvent reconciliation |
| H — Layout persistence + mobile | 46–48 | chart_layouts CRUD wiring, debounced sync with If-Match, mobile toolbar collapse |
| I — E2E + perf + close-out | 49–53 | 6 Playwright golden flows, perf smoke, storage projection, CHANGELOG/TASKS, tag v0.9.0, memory rename |

---

# Chunk A — Foundation

**Goal:** All Phase 9 schema lands except CAGGs (deferred to B-bis); `BarService` skeleton + lifespan registered; backend imports cleanly with `app_config.charts.enabled` kill-switch present but inert.

---

### Task 1: Alembic 0023 — TimescaleDB extension install

**Files:**
- Create: `backend/alembic/versions/0023_phase9_timescaledb_extension.py`
- Test: `backend/tests/integration/test_alembic_0023.py`

- [ ] **Step 1: Write failing integration test**

```python
# backend/tests/integration/test_alembic_0023.py
import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration]

async def test_0023_timescaledb_extension_present(pg_test_session):
    async with pg_test_session() as s:
        row = await s.execute(text(
            "SELECT extname, extversion FROM pg_extension WHERE extname='timescaledb'"
        ))
        ext = row.first()
        assert ext is not None, "timescaledb extension missing after 0023"
        major, minor = (int(p) for p in ext.extversion.split(".")[:2])
        assert (major, minor) >= (2, 17), f"Timescale {ext.extversion} < required 2.17"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend pytest backend/tests/integration/test_alembic_0023.py -v
```
Expected: FAIL — extension does not yet exist OR migration 0023 not present.

- [ ] **Step 3: Implement migration**

```python
# backend/alembic/versions/0023_phase9_timescaledb_extension.py
"""phase9: install timescaledb extension"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

def downgrade() -> None:
    # Do not auto-DROP; CAGGs and hypertables would cascade.
    # Manual ops only — leave as no-op.
    pass
```

- [ ] **Step 4: Run upgrade + test passes**

```bash
docker compose exec backend alembic upgrade 0023
docker compose exec backend pytest backend/tests/integration/test_alembic_0023.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0023_phase9_timescaledb_extension.py \
        backend/tests/integration/test_alembic_0023.py
git commit -m "feat(phase9): alembic 0023 install timescaledb extension"
```

---

### Task 2: Alembic 0023a — `instrument_id` resolver columns

**Files:**
- Create: `backend/alembic/versions/0023a_phase9_instrument_id_resolver.py`
- Test: `backend/tests/integration/test_alembic_0023a.py`

Spec slice (§3 Pre-requisite migration 0023a, lines 149-176): adds nullable `instrument_id BIGINT REFERENCES instruments(id) ON DELETE SET NULL` columns + partial indexes + best-effort backfill via `instruments.canonical_id` and `symbol_aliases`.

- [ ] **Step 1: Write failing integration test** covering: column presence (both tables, nullable); partial-index existence; backfill links a position with `canonical_id='AAPL.US'`.

```python
# backend/tests/integration/test_alembic_0023a.py
import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration]

async def test_0023a_columns_present_and_nullable(pg_test_session):
    async with pg_test_session() as s:
        cols = (await s.execute(text("""
            SELECT table_name, is_nullable
              FROM information_schema.columns
             WHERE column_name='instrument_id'
               AND table_name IN ('positions','watchlist_entries')
        """))).all()
        names = {(r.table_name, r.is_nullable) for r in cols}
        assert ("positions", "YES") in names
        assert ("watchlist_entries", "YES") in names

async def test_0023a_partial_indexes(pg_test_session):
    async with pg_test_session() as s:
        idx = (await s.execute(text("""
            SELECT indexname FROM pg_indexes
             WHERE indexname IN ('positions_instrument_idx','watchlist_entries_instrument_idx')
        """))).all()
        assert len(idx) == 2

async def test_0023a_backfill_links_positions(pg_test_session, seed_instrument_aapl):
    async with pg_test_session() as s:
        inst_id = await seed_instrument_aapl(s)
        await s.execute(text("""
            INSERT INTO positions (broker_id, account_id, canonical_id, symbol, qty)
            VALUES ('schwab', '00000000-0000-0000-0000-000000000001', 'AAPL.US', 'AAPL', 1)
        """))
        await s.execute(text("""
            UPDATE positions p SET instrument_id = i.id
              FROM instruments i WHERE i.canonical_id = p.canonical_id
               AND p.canonical_id IS NOT NULL AND p.instrument_id IS NULL
        """))
        row = (await s.execute(text(
            "SELECT instrument_id FROM positions WHERE canonical_id='AAPL.US'"
        ))).first()
        assert row.instrument_id == inst_id
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement migration**

```python
# backend/alembic/versions/0023a_phase9_instrument_id_resolver.py
"""phase9: instrument_id resolver columns + best-effort backfill"""
from alembic import op
import sqlalchemy as sa

revision = "0023a"
down_revision = "0023"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("positions",
        sa.Column("instrument_id", sa.BigInteger(),
                  sa.ForeignKey("instruments.id", ondelete="SET NULL"), nullable=True))
    op.add_column("watchlist_entries",
        sa.Column("instrument_id", sa.BigInteger(),
                  sa.ForeignKey("instruments.id", ondelete="SET NULL"), nullable=True))
    op.execute("""CREATE INDEX positions_instrument_idx ON positions(instrument_id)
                  WHERE instrument_id IS NOT NULL""")
    op.execute("""CREATE INDEX watchlist_entries_instrument_idx ON watchlist_entries(instrument_id)
                  WHERE instrument_id IS NOT NULL""")
    op.execute("""UPDATE positions p SET instrument_id = i.id
                    FROM instruments i WHERE i.canonical_id = p.canonical_id
                     AND p.canonical_id IS NOT NULL""")
    op.execute("""UPDATE watchlist_entries w SET instrument_id = sa.instrument_id
                    FROM symbol_aliases sa
                   WHERE sa.source = w.broker_id AND sa.raw_symbol = w.symbol""")

def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS watchlist_entries_instrument_idx")
    op.execute("DROP INDEX IF EXISTS positions_instrument_idx")
    op.drop_column("watchlist_entries", "instrument_id")
    op.drop_column("positions", "instrument_id")
```

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0023a_phase9_instrument_id_resolver.py \
        backend/tests/integration/test_alembic_0023a.py
git commit -m "feat(phase9): alembic 0023a instrument_id resolver columns + backfill"
```

---

### Task 3: Alembic 0023b — `tick_size` on `instruments`

**Files:**
- Create: `backend/alembic/versions/0023b_phase9_tick_size.py`
- Test: `backend/tests/integration/test_alembic_0023b.py`

- [ ] **Step 1: Write failing test** verifying `tick_size NUMERIC(20,8) NULL` exists on `instruments`.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement**

```python
# backend/alembic/versions/0023b_phase9_tick_size.py
"""phase9: tick_size on instruments"""
from alembic import op
import sqlalchemy as sa

revision = "0023b"
down_revision = "0023a"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("instruments",
        sa.Column("tick_size", sa.Numeric(20, 8), nullable=True))
    op.execute("""COMMENT ON COLUMN instruments.tick_size IS
        'Minimum price increment. NULL until first observation from broker contract spec.'""")

def downgrade() -> None:
    op.drop_column("instruments", "tick_size")
```

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0023b_phase9_tick_size.py \
        backend/tests/integration/test_alembic_0023b.py
git commit -m "feat(phase9): alembic 0023b tick_size column on instruments"
```

---

### Task 4: Alembic 0024 — `bars_1s` + `bars_1m` hypertables

**Files:**
- Create: `backend/alembic/versions/0024_phase9_bars_base.py`
- Test: `backend/tests/integration/test_alembic_0024.py`

Critical invariants (per spec §3 lines 192-260):
- PK `(instrument_id, bucket_start)` — single-row, no source in PK.
- `volume NUMERIC(20,8) NULL` + `volume_source TEXT NOT NULL CHECK ('tape','quote_proxy','none')`.
- `bars_1m` adds `source_priority SMALLINT CHECK (source_priority IN (1,2,3,4,99))`.
- `create_hypertable` chunk_time_interval = `INTERVAL '6 hours'` for 1s, `'7 days'` for 1m.
- Retention: `bars_1s` 7 days; `bars_1m` 6 months.
- No `inserted_at` column (architect MED #5).

- [ ] **Step 1: Write failing test** covering: hypertable presence, PK shape, volume_source CHECK, source_priority CHECK, retention policies.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Codex prompt**

> Implement Alembic migration `backend/alembic/versions/0024_phase9_bars_base.py`. Down-revision = `0023b`. Use `op.execute(...)` for all `CREATE TABLE`, `create_hypertable(...)`, `CREATE INDEX`, `add_retention_policy(...)`. Inline the full DDL from `docs/superpowers/specs/2026-05-07-phase9-charting-design.md` lines 192-260 verbatim. Both tables get PK `(instrument_id, bucket_start)`, FK to `instruments(id) ON DELETE CASCADE`. `bars_1s` chunk_time_interval = `INTERVAL '6 hours'`, retention 7 days. `bars_1m` chunk_time_interval = `INTERVAL '7 days'`, retention 6 months. Index `bars_{1s,1m}_inst_time_idx ON (instrument_id, bucket_start DESC)`. Volume CHECK pair (`volume_source` enum + `volume_consistent` rule). `bars_1m` extra CHECK: `source_priority IN (1,2,3,4,99)`. Apply codex_defaults A (Py3 except parens) and F. Downgrade drops both hypertables. Tests cover all invariants from spec §3.

- [ ] **Step 4: Run → PASS**

```bash
docker compose exec backend alembic upgrade 0024
docker compose exec backend pytest backend/tests/integration/test_alembic_0024.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0024_phase9_bars_base.py \
        backend/tests/integration/test_alembic_0024.py
git commit -m "feat(phase9): alembic 0024 bars_1s/bars_1m hypertables + retention + checks"
```

---

### Task 5: Alembic 0026 — `chart_layouts`

**Files:**
- Create: `backend/alembic/versions/0026_phase9_chart_layouts.py`
- Test: `backend/tests/integration/test_alembic_0026.py`

Note: `0025` (CAGGs) is intentionally skipped here; lands in **Chunk B-bis** after aggregator validates `bars_1s` shape (per spec §11 line 1061).

- [ ] **Step 1: Write failing test** covering: column shape, UNIQUE on `instrument_id`, 64KB CHECK enforcement.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement**

```python
# backend/alembic/versions/0026_phase9_chart_layouts.py
"""phase9: chart_layouts (single-tenant, 64KB cap)"""
from alembic import op

revision = "0026"
down_revision = "0024"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
        CREATE TABLE chart_layouts (
          id              BIGSERIAL     PRIMARY KEY,
          instrument_id   BIGINT        NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
          payload         JSONB         NOT NULL,
          schema_version  INTEGER       NOT NULL DEFAULT 1,
          updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
          UNIQUE (instrument_id),
          CONSTRAINT chart_layouts_payload_size_chk CHECK (octet_length(payload::text) < 65536)
        );
        CREATE INDEX chart_layouts_updated_at_idx ON chart_layouts (updated_at DESC);
    """)

def downgrade() -> None:
    op.execute("DROP TABLE chart_layouts")
```

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0026_phase9_chart_layouts.py \
        backend/tests/integration/test_alembic_0026.py
git commit -m "feat(phase9): alembic 0026 chart_layouts single-tenant + 64KB cap"
```

---

### Task 6: Alembic 0027 — `bar_backfill_jobs` + partial unique

**Files:**
- Create: `backend/alembic/versions/0027_phase9_bar_backfill_jobs.py`
- Test: `backend/tests/integration/test_alembic_0027.py`

- [ ] **Step 1: Write failing test** covering: partial unique blocks two pending rows on same (instrument, source, tf, range); allows new pending after first marked done; status CHECK rejects unknown statuses.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement** per spec §3 lines 350-374 verbatim.

```python
# backend/alembic/versions/0027_phase9_bar_backfill_jobs.py
"""phase9: bar_backfill_jobs (partial unique on pending+in_progress)"""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
        CREATE TABLE bar_backfill_jobs (
          id              BIGSERIAL   PRIMARY KEY,
          instrument_id   BIGINT      NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
          source          TEXT        NOT NULL,
          timeframe       TEXT        NOT NULL,
          range_start     TIMESTAMPTZ NOT NULL,
          range_end       TIMESTAMPTZ NOT NULL,
          status          TEXT        NOT NULL,
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
    """)

def downgrade() -> None:
    op.execute("DROP TABLE bar_backfill_jobs")
```

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0027_phase9_bar_backfill_jobs.py \
        backend/tests/integration/test_alembic_0027.py
git commit -m "feat(phase9): alembic 0027 bar_backfill_jobs + partial unique pending"
```

---

### Task 7: `app_config` seed task — Phase 9 keys

**Files:**
- Create: `backend/scripts/seed_phase9_app_config.py`
- Test: `backend/tests/integration/test_seed_phase9_app_config.py`

Keys per spec §3 lines 376-389 (charts namespace, 8 keys).

- [ ] **Step 1: Write failing test** for: 8 keys written; idempotent on second call.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement seed module** — see continuation file for full code (this task continues in plan-part-2).

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/seed_phase9_app_config.py \
        backend/tests/integration/test_seed_phase9_app_config.py
git commit -m "feat(phase9): app_config seeder for charts namespace (8 keys)"
```

**Seed implementation (Step 3):**

```python
# backend/scripts/seed_phase9_app_config.py
"""Idempotent seeder for Phase 9 app_config keys (charts namespace)."""
from __future__ import annotations
import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SEEDS: tuple[tuple[str, str, str | None, str | None], ...] = (
    ("bar_source_priority.equity_us", "json", None, json.dumps(["schwab", "alpaca", "ibkr"])),
    ("bar_source_priority.equity_hk", "json", None, json.dumps(["futu", "ibkr"])),
    ("bar_source_priority.crypto",    "json", None, json.dumps(["alpaca"])),
    ("bar_source_priority.fx",        "json", None, json.dumps(["ibkr"])),
    ("bar_pre_warm_window_days",      "int",  "30",   None),
    ("bar_active_set_recency_days",   "int",  "30",   None),
    ("chart_layout_schema_version",   "int",  "1",    None),
    ("enabled",                       "bool", "true", None),
)

async def seed_phase9_app_config(session: AsyncSession) -> None:
    for key, vtype, value, value_json in _SEEDS:
        await session.execute(text("""
            INSERT INTO app_config (namespace, key, value_type, value, value_json)
            VALUES ('charts', :k, :t, :v, CAST(:j AS JSONB))
            ON CONFLICT (namespace, key) DO NOTHING
        """), {"k": key, "t": vtype, "v": value, "j": value_json})
    await session.commit()
```

---

### Task 8: `BarService` skeleton + lifespan registration

**Files:**
- Create: `backend/app/services/bar_service.py` (skeleton; full impl in Chunk D)
- Modify: `backend/app/main.py` (register lifespan + pre-warm scheduler stub)
- Test: `backend/tests/unit/test_bar_service_skeleton.py`

- [ ] **Step 1: Write failing test** covering: `_SOURCE_PRIORITY` mapping equals canonical (schwab=1, alpaca=2, ibkr=3, futu=4, aggregator-*=99); `start()`/`stop()` are idempotent.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement skeleton**

```python
# backend/app/services/bar_service.py
"""BarService orchestrator skeleton — full impl lands in Chunk D."""
from __future__ import annotations
import asyncio
from typing import Final, Mapping

import structlog

logger = structlog.get_logger(__name__)

_SOURCE_PRIORITY: Final[Mapping[str, int]] = {
    "schwab": 1, "alpaca": 2, "ibkr": 3, "futu": 4,
    "aggregator-schwab": 99, "aggregator-alpaca": 99,
    "aggregator-ibkr": 99,   "aggregator-futu": 99,
}


def _priority_for_source(source: str) -> int:
    """Single chokepoint mapping source → source_priority for UPSERT WHERE clause."""
    if source not in _SOURCE_PRIORITY:
        raise ValueError(f"unknown bar source: {source!r}")
    return _SOURCE_PRIORITY[source]


class BarService:
    """Orchestrator skeleton; full impl arrives in Chunk D."""

    def __init__(self) -> None:
        self._started = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            logger.info("bar_service.start")
            self._started = True

    async def stop(self) -> None:
        async with self._lock:
            if not self._started:
                return
            logger.info("bar_service.stop")
            self._started = False
```

- [ ] **Step 4: Wire lifespan in `backend/app/main.py`**

In the existing FastAPI lifespan context manager, add `BarService` instantiation before `yield`, store on `app.state.bar_service`, await `start()`/`stop()`.

- [ ] **Step 5: Run → PASS + smoke-boot backend**

```bash
docker compose exec backend pytest backend/tests/unit/test_bar_service_skeleton.py -v
docker compose restart backend
docker compose logs backend --tail 50 | grep -E "bar_service\.start|bar_service\.stop|ERROR"
```

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(phase9): BarService skeleton + lifespan registration"
```

---

### Task 9: Active-set query helper

**Files:**
- Modify: `backend/app/services/bar_service.py` (add `active_set()`)
- Test: `backend/tests/integration/test_active_set_query.py`

- [ ] **Step 1: Write failing test** for: union of positions/watchlist/chart_layouts; LIMIT 1000 cap; respects `bar_active_set_recency_days` from `app_config`.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Codex prompt**

> Add `async def active_set(self, session) -> list[ActiveSetRow]:` to `backend/app/services/bar_service.py`. `ActiveSetRow = NamedTuple('ActiveSetRow', [('instrument_id', int), ('recency_score', int)])`. Execute the exact SQL from `docs/superpowers/specs/2026-05-07-phase9-charting-design.md` lines 396-417 verbatim (CTE on `app_config.value::int AS recency_days`, UNION ALL of positions/watchlist/chart_layouts, LIMIT 1000). Return list of NamedTuple rows. Add structlog `info` log with row count. Apply codex_defaults A.

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(phase9): BarService.active_set query (1000-instrument cap)"
```

---

### Task 10: Reviewer chain — Chunk A close-out

- [ ] **Step 1: Run reviewer chain** per spec §10 row "A":
  - spec-compliance (haiku)
  - python-reviewer (haiku)
  - code-reviewer (sonnet)
  - database-reviewer (sonnet)
  - security-reviewer (sonnet)

Dispatch in parallel; inline spec slices §3 (Data Model lines 143-451) + §4 (BarService lines 521-595) into each prompt per `feedback_reviewer_spec_inline.md`.

- [ ] **Step 2: Apply CRIT + HIGH + MED findings inline** per `feedback_architect_findings_apply_through_medium.md`. LOWs deferred only.

- [ ] **Step 3: Commit fixes**

```bash
git commit -m "fix(phase9): apply chunk A reviewer findings"
```

---

# Chunk B — bar_aggregator service

**Goal:** New Docker service subscribes Redis quote bus, maintains in-mem 1s buckets per instrument, writes to `bars_1s` every 1s on closed-bucket boundary, publishes coalesced live-tail per channel, durable WAL via Redis Streams with flush-ack-based trim.

---

### Task 11: Aggregator service scaffolding

**Files:**
- Create: `bar_aggregator/Dockerfile`
- Create: `bar_aggregator/pyproject.toml`
- Modify: `docker-compose.yml`
- Modify: `deploy/vps/docker-compose.yml`

- [ ] **Step 1: Create `bar_aggregator/Dockerfile`**

```dockerfile
FROM python:3.14-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml /app/
RUN uv pip install --system --no-cache .
COPY app/ /app/app/
CMD ["python", "-m", "app.main"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9100/healthz')" || exit 1
```

- [ ] **Step 2: Create `bar_aggregator/pyproject.toml`**

```toml
[project]
name = "bar_aggregator"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
  "redis[hiredis]>=5.0",
  "asyncpg>=0.29",
  "structlog>=24.0",
  "uvloop>=0.20",
  "prometheus-client>=0.20",
  "aiohttp>=3.10",
]
```

- [ ] **Step 3: Add to `docker-compose.yml`**

```yaml
  bar_aggregator:
    build: ./bar_aggregator
    restart: unless-stopped
    depends_on:
      - redis
    environment:
      REDIS_URL: redis://redis:6379/0
      DATABASE_URL: ${DATABASE_URL}
      FLUSH_INTERVAL_MS: "1000"
      AGGREGATOR_SHARD: "0"
      AGGREGATOR_SHARD_COUNT: "1"
    networks:
      - td-net
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9100/healthz')"]
      interval: 30s
      timeout: 5s
      retries: 3
```

- [ ] **Step 4: Mirror to `deploy/vps/docker-compose.yml`** with same shape.

- [ ] **Step 5: Boot smoke** (container starts; exits with NotImplementedError on main.py absence — that's expected before Task 12).

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(phase9): bar_aggregator Docker scaffold + compose entries"
```

---

### Task 12: Aggregator engine — bucket math + quote-bus subscribe

**Files:**
- Create: `bar_aggregator/app/{config,main,aggregator}.py`
- Test: `bar_aggregator/tests/test_aggregator_bucket_math.py`

- [ ] **Step 1: Write failing tests** for OHLC bucket math, quote-proxy volume tagging when no trade volume, tick-to-bucket-routing.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Codex prompt**

> Implement `bar_aggregator/app/aggregator.py` with `BucketState` dataclass and `AggregatorEngine` class. `BucketState` fields: `bucket_start: datetime`, `open/high/low/close: Decimal | None`, `volume: Decimal | None`, `volume_source: Literal['tape','quote_proxy','none']`, `trade_count: int`. Methods: `apply_tick(price, volume)` (sets/updates OHLC, accumulates volume, increments trade_count, sets `volume_source='tape'` if volume>0); `apply_quote(bid, ask)` (mid = (bid+ask)/2 used as price, `volume_source='quote_proxy'`, `trade_count` not incremented). `AggregatorEngine` holds `dict[(int, str), dict[datetime, BucketState]]` keyed by `(instrument_id, source)` then `bucket_start`. `on_tick(instrument_id, source, ts, price, volume)` truncates ts to second boundary (`ts.replace(microsecond=0)`), gets or creates BucketState, applies tick. `peek_bucket(instrument_id, bucket_start)` returns latest state for testing. Apply codex_defaults A. Inline spec §4 lines 457-520. Tests cover all bucket-math edge cases.

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(phase9): aggregator engine bucket math + quote-bus subscribe"
```

---

### Task 13: WAL — Redis Streams with flush-ack-based trim

**Files:**
- Create: `bar_aggregator/app/wal.py`
- Test: `bar_aggregator/tests/test_wal.py`

- [ ] **Step 1: Write failing tests** covering: XADD-on-tick, XTRIM-on-flush-ack (NOT time-based), replay-on-boot in xadd order, `GapDetectedError` when oldest WAL entry lags last-flushed by `> 2 × FLUSH_INTERVAL_MS`.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Codex prompt**

> Implement `bar_aggregator/app/wal.py`. WAL key format `wal:bar_aggregator:{shard}:{instrument_id}`. `append(...)` does `XADD ... MAXLEN ~ 50000` returning entry_id. `ack_flushed(instrument_id, last_entry_id)` does `XTRIM <key> MINID <last_entry_id>` (flush-ack-based trim — NOT time-based). `set_last_flushed(...)` and `get_last_flushed(...)` use a separate Redis hash key `wal:bar_aggregator:{shard}:flushed_ts` mapping `instrument_id → ISO8601 ts`. `replay(instrument_id=None)` async-yields `WALTickRecord` parsed from XRANGE; if `instrument_id` is None, scans all known shards. **Gap detection**: at start of `replay()` for a given instrument, if `(oldest_wal_ts - last_flushed_ts) > 2 × flush_interval_ms`, raise `GapDetectedError`, increment `bar_aggregator_wal_truncated_total{instrument}` counter, log CRITICAL. Implement `wal_lag_seconds()` and `wal_depth_bytes()` gauge helpers. Inline spec §4 lines 475-487. Apply codex_defaults A, B, D.

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(phase9): WAL via redis streams flush-ack-based trim + gap detect"
```

---

### Task 14: Per-channel coalescer (250ms window) + bar pub/sub

**Files:**
- Create: `bar_aggregator/app/bar_pubsub.py`
- Test: `bar_aggregator/tests/test_coalescer.py`

- [ ] **Step 1: Write failing tests** for: 20 ticks within 1 window → ≤3 publishes; final (`partial=false`) bypasses coalescer with `revision = MAX_INT`.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Codex prompt**

> Implement `bar_aggregator/app/bar_pubsub.py`. Class `_ChannelCoalescer` per spec §4 lines 488-503: holds `_latest`, `_last_publish_at`, `_task`. `update(snap)` stores latest, schedules deferred publish if no pending task. `publish_final(snap)` bypasses coalescing, emits immediately with `revision = MAX_INT` (2³¹-1). Module-level `BarPubSub` class manages `dict[str, _ChannelCoalescer]` keyed by `(instrument_id, tf)`. Publishes to Redis channel `bar.<canonical_id>.<tf>` using `redis.publish(...)`. Track `bar_aggregator_partial_publish_ratio = publishes/ticks`. Apply codex_defaults C (per-task isolation; each coalescer's task failure must not crash the engine). Inline spec §4 lines 488-509.

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(phase9): per-channel coalescer 250ms + final bypass + revision MAX_INT"
```

---

### Task 15: Flush — closed-bucket-only every 1s + minute_emitter

**Files:**
- Create: `bar_aggregator/app/{flush,minute_emitter}.py`
- Test: `bar_aggregator/tests/test_flush.py`, `bar_aggregator/tests/test_minute_emitter.py`

- [ ] **Step 1: Write failing tests** for: only buckets where `bucket_start + 1s <= now` are flushed; in-flight buckets never reach `bars_1s`; flush success triggers `wal.ack_flushed()`; PG `OperationalError` pauses flush (does not crash); flush resumes on first successful ping; minute_emitter UPSERT uses priority 99.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Codex prompts** (parallel — 2 dispatches)

> **Prompt for `flush.py`:** Implement per spec §4 (`flush.py` description) and §6 Flow 4. `Flusher.flush_once()` iterates engine's `(instrument_id, source) → bucket_start → BucketState` map; selects only `bucket_start + 1s <= now` (closed). Builds batched COPY into `bars_1s` (use asyncpg `copy_records_to_table`). On success: removes flushed buckets from in-mem dict, calls `wal.ack_flushed(instrument_id, last_entry_id_for_bucket)`, publishes final via `BarPubSub.publish_final(...)`. On `asyncpg.exceptions.OperationalError`: log warning, increment `bar_aggregator_pg_unreachable_seconds` gauge, return (do NOT crash). `flush_loop()` runs `flush_once()` every `FLUSH_INTERVAL_MS=1000` via asyncio. Inline spec §4 lines 511-519 (WG-split tolerance) + §6 Flow 4 (lines 774-807). Apply codex_defaults B, D.
>
> **Prompt for `minute_emitter.py`:** Implement per spec §6 Flow 5. `MinuteEmitter.tick()` runs at HH:MM:00 (asyncio TimerHandle aligned to next-minute boundary). For each active instrument: aggregate 60 × 1s buckets in `[HH:MM-1, HH:MM)` from in-mem state; UPSERT `bars_1m` with `source='aggregator-{src}'`, `source_priority=99`. Use the priority-encoded UPSERT pattern from spec §3 lines 263-274 (`ON CONFLICT (instrument_id, bucket_start) DO UPDATE ... WHERE EXCLUDED.source_priority < bars_1m.source_priority`). Publish `bar.<canonical_id>.1m` final. Apply codex_defaults A, B, F.

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(phase9): closed-bucket-only flush + minute emitter (priority 99 upsert)"
```

---

### Task 16: Aggregator entrypoint, healthcheck, prom metrics, shutdown

**Files:**
- Create/Complete: `bar_aggregator/app/main.py`, `bar_aggregator/app/metrics.py`
- Test: `bar_aggregator/tests/test_main_lifecycle.py`

- [ ] **Step 1: Write failing tests** for: app start subscribes + replays + shuts down cleanly; `/healthz` returns 200 OK.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Codex prompt**

> Implement `bar_aggregator/app/main.py`. `AggregatorApp` ties `AggregatorEngine`, `WAL`, `Flusher`, `MinuteEmitter`, `BarPubSub`. Lifecycle: `start()` → install `uvloop`, connect Redis + PG, run WAL replay (per-instrument; raises `GapDetectedError` are logged CRITICAL but startup continues with affected instruments paused), subscribe quote bus `quote.*` for the shard's instrument set (`instrument_id % SHARD_COUNT == SHARD`), spawn flush_loop + minute_emitter as tasks, expose `/healthz` and `/metrics` via aiohttp on port 9100. `stop()` cancels all tasks via `asyncio.gather(..., return_exceptions=True)`, closes Redis + PG. Wire signal handlers (SIGTERM, SIGINT). Inline spec §4 lines 457-520. Apply codex_defaults B, C, E.
>
> `bar_aggregator/app/metrics.py`: prometheus_client Counter/Gauge/Histogram for: ticks_consumed, buckets_flushed, wal_replayed, wal_depth_bytes, wal_lag_seconds, wal_truncated_total{instrument}, partial_publish_ratio, flush_lag_seconds, pg_unreachable_seconds, idle_total{instrument}.

- [ ] **Step 4: Run → PASS; smoke compose up + curl /healthz + /metrics.**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(phase9): aggregator entrypoint + healthz + prom metrics + lifecycle"
```

---

### Task 17: Reviewer chain — Chunk B close-out

- [ ] **Step 1: Run reviewer chain** per §10 row "B": base + python-reviewer (sonnet) + silent-failure-hunter (sonnet, focus on WAL replay correctness + flush pause-on-error).
- [ ] **Step 2: Apply CRIT + HIGH + MED inline.**
- [ ] **Step 3: Commit fixes.**

---

# Chunk B-bis — CAGGs

**Goal:** 10 continuous aggregates over `bars_1s` (5 sub-1m) and `bars_1m` (5 super-1m) with `end_offset >= bucket_width` and `start_offset < base retention`. Lands AFTER Chunk B aggregator runs successfully against real `bars_1s` data.

---

### Task 18: Alembic 0025 — 10 CAGGs + retention policies

**Files:**
- Create: `backend/alembic/versions/0025_phase9_bars_continuous_aggs.py`
- Test: `backend/tests/integration/test_alembic_0025.py`

- [ ] **Step 1: Validate Chunk B is producing data** — `SELECT count(*) FROM bars_1s WHERE bucket_start > NOW() - INTERVAL '5 min'` must return > 0. If not, do NOT proceed.

- [ ] **Step 2: Write failing test** for: 10 CAGGs created; `end_offset >= bucket_width` per CAGG; `bars_1h`/`bars_1d` have 5-year retention policy.

- [ ] **Step 3: Run → FAIL**

- [ ] **Step 4: Codex prompt**

> Implement Alembic migration `0025_phase9_bars_continuous_aggs.py`. Down-revision = `0027`. For each of the 10 CAGGs in `docs/superpowers/specs/2026-05-07-phase9-charting-design.md` §3 table (lines 284-296): emit `CREATE MATERIALIZED VIEW <name> WITH (timescaledb.continuous) AS SELECT ... FROM bars_1s|bars_1m GROUP BY instrument_id, time_bucket(...)` per spec template (lines 297-318), then `add_continuous_aggregate_policy('<name>', start_offset=>INTERVAL '<...>', end_offset=>INTERVAL '<bucket_width>', schedule_interval=>INTERVAL '<...>')` with the EXACT values from the table. **Critical invariants** (architect CRIT #2 + HIGH #5): `end_offset >= bucket_width` AND `start_offset < base table retention`. For `bars_1h` and `bars_1d`: also emit `add_retention_policy('<name>', INTERVAL '5 years')` per spec lines 320-325. All 10 CAGGs must use `first(open, bucket_start)` and `last(close, bucket_start)` for OHLC; `max(high)`, `min(low)`, `sum(volume)`, `sum(trade_count)`. Downgrade drops policies + views in reverse order. Apply codex_defaults A.

- [ ] **Step 5: Run → PASS**

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(phase9): alembic 0025 ten CAGGs (end_offset>=bucket_width, start_offset<retention)"
```

- [ ] **Step 7: Reviewer chain — Chunk B-bis close-out**

Per §10 row "B-bis": base + database-reviewer (sonnet, CAGG correctness focus). Inline spec §3 lines 278-327. Commit fixes.

---

# Chunk C — Sidecar `GetHistoricalBars`

**Goal:** All 4 sidecars implement `GetHistoricalBars` RPC with chunked fetch, pacing, and 401-retry-once where applicable.

---

### Task 19: proto extension — `GetHistoricalBars` + `HistoricalBar`

**Files:**
- Modify: `proto/broker/v1/broker.proto`
- Test: `backend/tests/contract/test_broker_proto_compat.py`

- [ ] **Step 1: Write failing test** — `buf breaking --against ".git#branch=main"` must pass after addition (RPC additions are non-breaking).

- [ ] **Step 2: Edit proto** per spec §4 lines 622-645 verbatim. Insert into existing `service Broker`.

- [ ] **Step 3: Regenerate types**

```bash
./scripts/gen-types.sh
buf breaking --against ".git#branch=main"
```

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(phase9): proto GetHistoricalBars RPC + HistoricalBar message"
```

---

### Tasks 20–23: Sidecar handlers (Schwab, Alpaca, IBKR, Futu)

Run **in parallel** as four independent Codex dispatches. Each sidecar shares the RPC surface but has broker-specific quirks.

#### Task 20: `sidecar_schwab/handlers.py` — `GetHistoricalBars`

- [ ] **Step 1: Write failing tests** with golden CSV fixture (10 days of AAPL 1m bars, real-pricehistory shape).
- [ ] **Step 2: Codex prompt**

> Implement `GetHistoricalBars` RPC handler in `sidecar_schwab/handlers.py` using `schwabdev.Client.price_history(...)` (CHART_EQUITY). Map `timeframe='1m'` → schwabdev `frequency='minute'`, `frequencyType='minute'`. Return `GetHistoricalBarsResponse` with `bars: list[HistoricalBar]` (bucket_start as proto Timestamp, OHLC as string-encoded NUMERIC(20,8)) and `truncated=True` if response had `len(resp['candles']) >= limit`. **401 retry-once**: catch `schwabdev.AuthError` once, re-acquire token via `app_secrets` (existing `_get_token()` helper), retry the chunk; if 401 persists, raise gRPC `UNAUTHENTICATED`. Apply codex_defaults A, B, C. Inline spec §4 line 650 + §6 Flow 2. Tests with golden CSV fixture under `sidecar_schwab/tests/fixtures/aapl_30d_1m.csv`.

- [ ] **Step 3: Run → PASS; commit.**

```bash
git commit -m "feat(phase9): sidecar_schwab GetHistoricalBars + 401 retry-once"
```

#### Task 21: `sidecar_alpaca/handlers.py`

> Codex prompt: Implement `GetHistoricalBars` using `alpaca-py`'s `StockHistoricalDataClient.get_stock_bars(...)` for equities and `CryptoHistoricalDataClient.get_crypto_bars(...)` for crypto. Route by `instruments.asset_class` from a 2-tier cache (in-process LRU + PG fallback). `timeframe='1m'` → `TimeFrame.Minute`. `truncated=True` if response has `next_page_token`. No auth retry needed. Inline spec §4 line 651. Apply codex_defaults A, B, C.

- [ ] Tests + commit.

#### Task 22: `sidecar_ibkr/handlers.py` — token bucket + jittered scheduling

> Codex prompt: Implement `GetHistoricalBars` using `ib_async.IB.reqHistoricalDataAsync(...)`. Map `timeframe='1m'` → `barSizeSetting='1 min'`, `whatToShow='TRADES'`, `useRTH=False`. **Per-client-id token bucket** (`asyncio.Semaphore`-style, capacity 50, refilled 50/600s; reserve 10 for ad-hoc/cold fetches; pre-warm cron uses `acquire_pacing_token(reserve=False)`, ad-hoc cold fetches use `acquire_pacing_token(reserve=True)`). On `pacingViolation` exception: 60s cooldown + raise gRPC `RESOURCE_EXHAUSTED` with `Retry-After` metadata. **Jittered scheduling**: BarService caller passes `instrument_id`; sidecar adds `(instrument_id % 4) * 50ms` jitter before issuing request. Apply codex_defaults A, B, D.

- [ ] Tests + commit.

#### Task 23: `sidecar_futu/handlers.py`

> Codex prompt: Implement using `futu.OpenQuoteContext.request_history_kline(...)`. Map `timeframe='1m'` → `KLType.K_1M`. HK only initially; raise gRPC `UNIMPLEMENTED` for non-HK canonical_id. Inline spec §4 line 653 + memory `reference_futu_api_docs.md`. Apply codex_defaults A, B.

- [ ] Tests + commit.

---

### Task 24: Empirical scripts (4) — paper-broker validation

**Files:**
- Create: `scripts/empirical/{schwab,alpaca,ibkr,futu}_history_paper.py`
- Create: `.github/workflows/nightly-real-broker-history.yml`

- [ ] **Step 1: Codex batch dispatch**

> Generate 4 empirical scripts under `scripts/empirical/`. Each connects via real broker API (paper credentials only — read from `paper.{broker}.app_key/api_key/...` keys in `app_secrets`), fetches AAPL.US (or 0700.HK for Futu) 1m bars for last 30 days, asserts ≥1 bar/min during market hours per the asset's market calendar. Scripts excluded from CI. Pattern follows `scripts/empirical/schwab_oco_paper.py` structure (chunk-S in Phase 8c). Each script writes JSONL artifact to `tmp/empirical/{broker}_history_{YYYYMMDD-HHMMSS}.jsonl` (gitignored). Apply codex_defaults A, B.

- [ ] **Step 2: GHA workflow** — nightly self-hosted runner at 02:30 UTC; loops 4 brokers; emit `::warning::` on individual failure rather than aborting the job.

- [ ] **Step 3: Smoke + commit.**

```bash
git commit -m "feat(phase9): empirical history scripts for 4 brokers (paper creds only)"
```

---

### Task 25: Reviewer chain — Chunk C close-out

Per §10 row "C": base + python-reviewer ×4 sidecars (parallel). Inline spec §4 + §10 + relevant fixture refs. Apply CRIT + HIGH + MED inline. Commit fixes.

---

# Chunk D — Backend orchestration

**Goal:** `BarService` full implementation; `/api/bars`, `/api/chart/layouts/*`, `/ws/bars/...`, `POST /api/orders/nonce/modify`.

---

### Task 26: BarService — `get_bars()` with gap detection + cross-worker coalesce

**Files:**
- Modify: `backend/app/services/bar_service.py` (replace skeleton)
- Test: `backend/tests/integration/test_bar_service_get_bars.py`

- [ ] **Step 1: Write failing tests** covering: cache hit (no fetch); cache miss with single worker (fetches); two concurrent workers race → only one fetches via `bar_backfill_jobs` partial unique; second worker waits via `pg_notify` and gets the same data.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Codex prompt**

> Replace skeleton in `backend/app/services/bar_service.py` with full implementation per `docs/superpowers/specs/2026-05-07-phase9-charting-design.md` §4 lines 521-595. `get_bars(canonical_id, timeframe, start, end, limit, cursor, session)`:
> 1. Resolve `instrument_id` from canonical_id via `instruments` + `symbol_aliases` (use existing Phase 7b.1.5 helper).
> 2. Query `bars_{tf}` for `[start, end)`. Detect missing ranges.
> 3. If gaps and `tf >= '1m'`: pick source via `app_config.bar_source_priority.{asset_class}`; `INSERT INTO bar_backfill_jobs ... ON CONFLICT DO NOTHING RETURNING id, (xmax = 0) AS was_new`. If `was_new`: this worker fetches via `_fetch_with_chunks(...)` (spec lines 569-583, 100-chunk hard cap, raises `BarFetchTooLarge`). Else: this worker waits via `_wait_for_job(job_id)` — `LISTEN bar_backfill_done`; on notify, check payload matches `job_id`; bounded wait 16s with 250ms poll fallback (spec §4 lines 553-562).
> 4. After fetch (or wait): UPSERT bars via priority-encoded UPSERT pattern from spec §3 lines 263-274. Emit `pg_notify('bar_backfill_done', job_id::text)`.
> 5. Return paginated page with `next_cursor = base64url({v:1, last_bucket_start:<oldest>})` if `count > limit`. Sub-1m never backfills.
>
> Source-priority hard rule for US equities: skip IBKR during pre-warm if Schwab or Alpaca is healthy (spec line 652).
>
> Add metric histogram `bar_service_cross_worker_wait_seconds`. Apply codex_defaults A, B, C, D, F.

- [ ] **Step 4: Run → PASS; commit.**

```bash
git commit -m "feat(phase9): BarService.get_bars + cross-worker pg_notify coalesce"
```

---

### Task 27: BarService — `pre_warm_active_set()` cron

- [ ] **Step 1: Write failing test** for: cron runs every market-close + on startup; only fetches gaps; respects IBKR token bucket.

- [ ] **Step 2: Codex prompt**

> Add `pre_warm_active_set()` to BarService per spec §4 lines 538-547 + IBKR jittered stagger via `instrument_id % 4` per spec line 652. Yields between instruments via `await asyncio.sleep(0)`. Triggered by:
> - Backend startup (lifespan after `start()`)
> - Nightly cron at per-asset-class market close (use existing `market_calendar.py` next_close)
> Wire scheduler via `apscheduler` AsyncIOScheduler. Inline spec §4 lines 538-547. Tests use frozen clock + mock sidecar.

- [ ] **Step 3: Run → PASS; commit.**

```bash
git commit -m "feat(phase9): BarService.pre_warm_active_set + cron schedule"
```

---

### Task 28: `/api/bars` router with cursor pagination

**Files:**
- Create: `backend/app/api/bars.py`
- Modify: `backend/app/api/__init__.py` (register router)
- Test: `backend/tests/integration/test_api_bars.py`

- [ ] **Step 1: Write failing tests** for: 200 OK with cached page; cursor advance returns next page; 10k row cap enforced; bogus cursor returns 400.

- [ ] **Step 2: Codex prompt**

> Implement `GET /api/bars?canonical_id&timeframe&start&end&limit=10000&cursor=...` per spec §4 line 599. Body shape:
> ```python
> class BarPage(BaseModel):
>     bars: list[Bar]                    # NUMERIC strings preserved
>     next_cursor: str | None
> ```
> Cursor encoding per spec §4 lines 585-594 (base64url JSON `{v:1, last_bucket_start: ISO8601}`). Validate cursor `v == 1`; else 400. Hard cap `limit=10000`. Auth via existing JWT middleware. Apply codex_defaults A, F.

- [ ] **Step 3: Wire router; run → PASS; commit.**

```bash
git commit -m "feat(phase9): GET /api/bars cursor pagination + 10k cap"
```

---

### Task 29: `/api/chart/layouts/*` router with read-side translator + If-Match

**Files:**
- Modify: `backend/app/api/bars.py` (or new `chart_layouts.py`)
- Create: `backend/app/services/chart_layout_translator.py`
- Test: `backend/tests/integration/test_api_chart_layouts.py`

- [ ] **Step 1: Write failing tests** for: GET 404 on unknown; GET 200 with current schema after read-side translation of older schema; PUT requires If-Match etag; 412 on mismatch; 64KB cap honored.

- [ ] **Step 2: Codex prompt**

> Implement `GET / PUT / DELETE /api/chart/layouts/:instrument_id` per spec §4 lines 600-602. Etag = `updated_at` ISO8601. Read-side translator function `_translate_chart_layout(payload, from_version, to_version)` per spec §3 line 347 — never mutates row; PUTs always write at latest version. Apply codex_defaults A, F. 412 on If-Match mismatch.

- [ ] **Step 3: Run → PASS; commit.**

```bash
git commit -m "feat(phase9): /api/chart/layouts CRUD + read-translator + If-Match"
```

---

### Task 30: `POST /api/orders/nonce/modify` endpoint

**Files:**
- Modify: `backend/app/api/orders.py` (add new route + gate `/api/orders/modify` on nonce)
- Test: `backend/tests/integration/test_modify_nonce_flow.py`

- [ ] **Step 1: Write failing tests** for: mint returns nonce + 30s expiry; submit consumes via GETDEL (single-use); reuse → 412; expired → 412.

- [ ] **Step 2: Codex prompt**

> Implement `POST /api/orders/nonce/modify` per spec §4 lines 603 + 613-618. Body `{order_id}`. Returns `{nonce, expires_at}`. Stores in Redis `nonce:modify:{order_id}:{nonce}` with TTL 30s. Add `nonce` field requirement to `POST /api/orders/modify` (currently at `backend/app/api/orders.py:247`); consume via `redis.execute_command('GETDEL', key)` (matches existing OCO pattern at orders.py:411-416). On missing/expired: 412. Apply codex_defaults A, G (nonce can't leak into logs — redact via existing structlog processor in `app/core/logging.py`).

- [ ] **Step 3: Run → PASS; commit.**

```bash
git commit -m "feat(phase9): POST /api/orders/nonce/modify + GETDEL consume"
```

---

### Task 31: `/ws/bars/<canonical_id>/<timeframe>` live-tail gateway

**Files:**
- Modify: `backend/app/api/bars.py` (add WS handler)
- Test: `backend/tests/integration/test_ws_bars.py`

- [ ] **Step 1: Write failing tests** for: handshake via `Sec-WebSocket-Protocol: bearer.<jwt>`; 60s idle PING; 20-sub limit (close-frame 4029 reason `subscription_limit_exceeded`); revision-sequenced delivery; partial=false carries revision=MAX_INT.

- [ ] **Step 2: Codex prompt**

> Implement `/ws/bars/<canonical_id>/<timeframe>` per spec §4 lines 604, 607-611 + Flow 4 envelope (lines 778-790). Reuse handshake helpers from `backend/app/api/quotes_ws.py` (Phase 7b.1, INV-Q-1 single-worker loopback suppression). One Redis subscriber per worker; FE WS connections fan in-process. Token-via-subprotocol `bearer.<jwt>`. Idle 60s PING, client must reply <30s else server-close 1000. Hard cap 20 subs/conn from `app_config.charts.ws_max_subs_per_conn` (default 20). On 21st: close-frame 4029 with reason `subscription_limit_exceeded`. Envelope per spec lines 778-787 with `revision` monotonic per `(canonical_id, tf, bucket_start)`; `partial=false` → `revision=2**31-1`. Apply codex_defaults A, B, C, G.

- [ ] **Step 3: Run → PASS; commit.**

```bash
git commit -m "feat(phase9): /ws/bars revision-sequenced live-tail + 20-sub cap"
```

---

### Task 32: WG-split simulation in BarService backfill path

- [ ] **Step 1: Write failing test** that injects PG `OperationalError` mid-fetch; assert job row has `status='failed'`, `error_message LIKE 'OperationalError%'`; pre-warm cron skips this instrument until next cycle.
- [ ] **Step 2: Codex prompt** to add the catch + retry-on-next-cycle.
- [ ] **Step 3: Run → PASS; commit.**

```bash
git commit -m "feat(phase9): BarService WG-split tolerance (mark backfill jobs failed cleanly)"
```

---

### Task 33: Reviewer chain — Chunk D close-out

Per §10 row "D": base + python-reviewer + database-reviewer (cross-worker focus) + security-reviewer (nonce focus). Apply CRIT + HIGH + MED inline. Commit fixes.

---

# Chunk E — FE chart feature

**Goal:** klinecharts ^10 wired; route `/chart/:canonicalId` rendering; toolbar + indicator picker (built-ins only); inline links from positions/orders/watchlist; mobile-functional toolbar; CLAUDE.md typo fix.

---

### Task 34: klinecharts dependency + version verify + CLAUDE.md typo fix

- [ ] **Step 1: Install + verify**

```bash
cd frontend && pnpm add klinecharts@^10
node -e "const k = require('klinecharts'); console.log('built-ins:', Object.keys(k.getSupportedIndicators ? k.getSupportedIndicators() : {}))"
```

- [ ] **Step 2: Reconcile §7.A1 list against v10** — `grep 'name:' node_modules/klinecharts/dist/index.esm.js | sort -u`; update spec inventory if v10 dropped/renamed any.

- [ ] **Step 3: Fix CLAUDE.md typo** — `klineschart` → `klinecharts` (search-replace).

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(phase9): pin klinecharts ^10 + CLAUDE.md typo fix"
```

---

### Task 35: Chart route + page shell

**Files:**
- Create: `frontend/src/routes/chart.$canonicalId.tsx`
- Create: `frontend/src/features/chart/ChartPage.tsx`
- Test: `frontend/src/features/chart/ChartPage.test.tsx`

- [ ] **Step 1: Failing component test** (Vitest + RTL): renders `<ChartPage canonicalId="AAPL.US">`; expects `data-testid="trade-chart"` present; expects layout fetch GET /api/chart/layouts/...

- [ ] **Step 2: Codex prompt**

> Create `frontend/src/routes/chart.$canonicalId.tsx` (TanStack Router file route) and `frontend/src/features/chart/ChartPage.tsx`. Page mounts `<TradeChart canonicalId={params.canonicalId}>` (stub for now — full impl Task 36). Subscribe to `chart_layouts` via `useQuery(['chart-layouts', canonicalId])`. Apply boundary rules: feature can import primitives/patterns/layout/lib. Inline spec §5 lines 659-693.

- [ ] **Step 3: Run → PASS; commit.**

```bash
git commit -m "feat(phase9): /chart/:canonicalId route + ChartPage shell"
```

---

### Task 36: `<TradeChart>` klinecharts wrapper + data adapter + WS live-tail

**Files:**
- Create: `frontend/src/features/chart/TradeChart.tsx`
- Create: `frontend/src/features/chart/services/{bars,liveTail}.ts`
- Create: `frontend/src/features/chart/stores/{chartStore,liveTailStore}.ts`
- Test: `*.test.tsx`

- [ ] **Step 1: Write failing tests**

- [ ] **Step 2: Codex prompt**

> Implement TradeChart wrapping klinecharts. Mount canvas in `useEffect`. `services/bars.ts` calls `GET /api/bars?...` with cursor pagination; converts NUMERIC strings to numbers ONLY for klinecharts data array (preserve strings everywhere else). `services/liveTail.ts` opens WS `/ws/bars/<id>/<tf>` with token-via-subprotocol; reconnect with exp-backoff (1s/2s/4s/8s/max 30s); on reconnect refetches trailing 2 closed buckets via REST. `chartStore` (Zustand): active timeframe, indicators, drawings, chart-type. `liveTailStore`: tick coalescing, revision-discard logic per Flow 4 (FE discards messages with `revision <= last_seen[bucket_start]`; on `partial=false` snap to canonical close + lock bucket). Apply CSS rem-only rule (CLAUDE.md FE invariants). No direct `@/stores/scoped/*` imports — use `useActiveStores()`. Inline spec §5 + §6 Flow 4.

- [ ] **Step 3: Run → PASS**

- [ ] **Step 4: Browser smoke** — `pnpm dev`; open http://localhost:5173/chart/AAPL.US ; verify chart paints, ticks update; check console for errors.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(phase9): TradeChart klinecharts wrapper + WS live-tail revision-sequenced"
```

---

### Task 37: ChartToolbar + TimeframeBar + IndicatorPicker (built-ins only)

**Files:**
- Create: `frontend/src/features/chart/{ChartToolbar,TimeframeBar,IndicatorPicker}.tsx`
- Test: `*.test.tsx` + Storybook stories

- [ ] **Step 1: Codex prompt**

> Implement three components with Storybook stories per CLAUDE.md FE-layer compliance.
> - `<ChartToolbar>`: top bar — chart-type dropdown, indicators button, drawings button, save, fullscreen, screenshot icon (deferred placeholder for v0.9.1).
> - `<TimeframeBar>`: bottom dual-pill: ranges (1d/5d/1m/3m/6m/1y/5y/All/Custom) + intervals (1s/5s/10s/15s/30s/45s/1m/5m/15m/30m/1h/1d/1w/1M).
> - `<IndicatorPicker>`: right-drawer modal with Favorites / Technicals / Custom tabs (Bucket-A only — 27 built-ins listed by category). Multi-select. On apply, dispatch `chartStore.setIndicators(...)`.
> All components mobile-responsive (collapse to compact form below `md`). Apply boundary + rem-only rules. Inline spec §5 lines 663-668 + §8.

- [ ] **Step 2: Run → PASS; smoke browser; commit.**

```bash
git commit -m "feat(phase9): ChartToolbar + TimeframeBar + IndicatorPicker"
```

---

### Task 38: DrawingTools (built-ins only) + ChartContextMenu

**Files:**
- Create: `frontend/src/features/chart/{DrawingTools,ChartContextMenu}.tsx`

- [ ] **Step 1: Codex prompt** — left-rail drawing selector for ~30 klinecharts built-ins per spec §8 line 942. Right-click context menu adds/removes indicator, copy snapshot.

- [ ] **Step 2: Run → PASS; smoke; commit.**

```bash
git commit -m "feat(phase9): DrawingTools (30 built-ins) + ChartContextMenu"
```

---

### Task 39: Inline "View Chart" links from positions / orders / watchlist

**Files:**
- Modify: `frontend/src/features/positions/PositionRow.tsx`
- Modify: `frontend/src/features/orders/OrderRow.tsx`
- Modify: `frontend/src/features/watchlist/WatchlistRow.tsx`

- [ ] **Step 1: Add `<Link to={`/chart/${canonicalId}`}>View Chart</Link>` per row.** Tests: row renders link; click navigates.

- [ ] **Step 2: Run → PASS; commit.**

```bash
git commit -m "feat(phase9): inline View Chart links from positions/orders/watchlist"
```

---

### Task 40: Reviewer chain — Chunk E close-out

Per §10 row "E": base + typescript-reviewer (haiku). Apply CRIT + HIGH + MED inline. Commit fixes.

---

# Chunks F1 + F2 — Custom indicators (45 total)

**Goal:** All ~45 custom-coded TS indicators registered with klinecharts; each carries citation header; each has golden-vector test; group-batched into two Codex dispatches.

---

### Task 41: F1 — Moving averages + Volatility/channels (22 indicators)

**Files:**
- Create: `frontend/src/features/chart/indicators/{vwap,ichimoku,alligator,...}.ts` (22 files)
- Create: `frontend/src/features/chart/indicators/register.ts` (bulk import + register)
- Create: golden-vector test fixtures `frontend/src/features/chart/indicators/__golden__/{name}.json`
- Create: `frontend/src/features/chart/indicators/{name}.test.ts` (×22)

Indicators in F1 (per spec §7 lines 888-892):
- **Moving averages / trend (12):** VWMA, WMA, TEMA, DEMA, HMA, LSMA, TSF, GMMA, ALLIGAT, TWAP, IC (Ichimoku), VWAP
- **Volatility / channels (10):** ATR, BBIBOLL, DC, KC, ENE, BBW, CDP, MIKE Base, PPSW, CKS

- [ ] **Step 1: Codex batch dispatch (single call)**

> Generate 22 custom indicators per spec §7 lines 902-934. Each `{name}.ts`:
> 1. Header with `Reference:` line(s) — TradingView Pine source URL + Wikipedia formula link minimum.
> 2. `IndicatorTemplate` per spec template (lines 904-916).
> 3. `calc()` implementation following the cited reference exactly.
> 4. Companion `{name}.test.ts` with golden-vector fixture under `__golden__/{name}.json` (synthetic 200-bar OHLCV input + expected output array).
>
> Register all 22 in `register.ts` calling `klinecharts.registerIndicator(t)` on app boot. Apply codex_defaults A. **Citation enforced** — reviewer chain rejects files without `Reference:` line. Use canonical references: TradingView Pine, klinecharts source, Wikipedia. Cross-validate where overlap exists.

- [ ] **Step 2: Run all golden-vector tests**

```bash
cd frontend && pnpm test src/features/chart/indicators/
```
Expected: 22/22 PASS.

- [ ] **Step 3: Browser smoke** — open chart, enable each indicator, visually compare to TradingView reference for 5 randomly-picked indicators.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/chart/indicators/
git commit -m "feat(phase9): 22 custom indicators (MA + Volatility) with citations + golden tests"
```

- [ ] **Step 5: Reviewer chain — Chunk F1**

Per §10 row "F1": base + typescript-reviewer (haiku, golden-vector test correctness focus). Reject any file without `Reference:` header. Apply fixes inline. Commit.

---

### Task 42: F2 — Momentum/oscillators + Volume/flow + Pattern/signal + Misc (23 indicators)

Indicators in F2 (per spec §7 lines 890-898):
- **Momentum / oscillators (15):** MFI, AROON, CHOP, CMO, Connors RSI, Stoch RSI, BOP, RVI, RVGI, RMI, ER, FO, Fisher Transform, OSC, RC
- **Volume / flow (5):** KO, EFI, AVGVOL, RVOL, MAVOL
- **Pattern / signal (3):** WF, NINE, HADIFF
- **Misc (4):** TTM Squeeze, SuperTrend, ZigZag, VOLAT

Same pattern as Task 41. One Codex batch + golden tests + browser smoke + reviewer chain.

```bash
git commit -m "feat(phase9): 23 custom indicators (Momentum + Volume + Pattern + Misc) with citations"
```

---

# Chunk G — Drag-handle SL/TP

**Goal:** PositionOverlay with per-leg `pending_modify_id` state machine, modify-nonce mint, OrderEvent reconciliation, tick-size snapping, mobile touch parity.

---

### Task 43: PositionOverlay rendering + tick_size snap

**Files:**
- Create: `frontend/src/features/chart/PositionOverlay.tsx`
- Create: `frontend/src/features/chart/overlays/{longPosition,shortPosition}.ts`

- [ ] **Step 1: Failing tests** (Vitest+RTL): given an open position with bracket, render Long Position overlay with entry line + SL box + TP box; drag handle snaps to `instrument.tick_size`.

- [ ] **Step 2: Codex prompt**

> Implement `PositionOverlay.tsx` + `overlays/{longPosition,shortPosition}.ts` per spec §5 lines 711-720 + §8 line 945. Custom klinecharts overlay templates render entry line + draggable SL/TP boxes. **Tick snapping**: target snaps to `instruments.tick_size` (per-instrument from broker contract spec; null until first observation — fall back to 0.01 if null). Confirm dialog displays tick boundary explicitly: e.g. "$184.99 (rounded to $0.01 tick)". Unified `pointerdown/pointermove/pointerup` for mouse + touch parity. Apply rem-only CSS rule.

- [ ] **Step 3: Run → PASS; commit.**

```bash
git commit -m "feat(phase9): PositionOverlay + Long/Short overlays + tick_size snap"
```

---

### Task 44: Modify-nonce flow + ConfirmDialog + per-leg state machine

- [ ] **Step 1: Failing tests** for: dialog open → POST /api/orders/nonce/modify mints; dialog cancel → no nonce consumed; submit → POST /api/orders/modify with nonce; pending_modify_id set; drag disabled; OrderEvent matching modify_id clears pending; 5s timeout falls through to GET /api/orders/{leg_id}.

- [ ] **Step 2: Codex prompt**

> Implement modify flow per spec §6 Flow 6 (lines 819-849) + §5 lines 711-720. Per-bracket-leg `pending_modify_id: Map<leg_id, {nonce, target_price, started_at}>` lives in `chartStore`. ConfirmDialog requests fresh nonce on dialog OPEN (not submit; spec line 719) via `POST /api/orders/nonce/modify {order_id}`. On user cancel → no nonce consumed (Redis TTL 30s expires it). On confirm → set pending_modify_id, handle becomes yellow ghost + spinner, drag disabled, POST `/api/orders/modify {order_id, stop_price, nonce}`. Existing `/ws/orders` consumer dispatches OrderEvent: if `event.modify_id == pending_modify_id[leg_id].nonce` → clear pending, snap to event.stop_price, re-enable drag. Else if rejection → toast broker error, revert to last-known-good. **5s timeout fallthrough**: `GET /api/orders/{leg_id}`, snap to authoritative state. Apply codex_defaults A, G.

- [ ] **Step 3: Run → PASS; commit.**

```bash
git commit -m "feat(phase9): modify-nonce + ConfirmDialog + per-leg pending state machine"
```

---

### Task 45: Reviewer chain — Chunk G close-out

Per §10 row "G": base + typescript-reviewer + security-reviewer (drag/nonce/CSRF focus). Apply CRIT + HIGH + MED inline. Commit fixes.

---

# Chunk H — Layout persistence + mobile parity

---

### Task 46: ChartLayoutSync — debounced PUT with If-Match

**Files:**
- Create: `frontend/src/features/chart/ChartLayoutSync.tsx`
- Create: `frontend/src/features/chart/services/layoutSync.ts`

- [ ] **Step 1: Failing tests** for: chartStore changes trigger debounced (500ms) PUT; etag tracked from last GET/PUT; concurrent edit → 412 → reconcile prompt; first PUT (no etag) succeeds.

- [ ] **Step 2: Codex prompt**

> Implement `ChartLayoutSync.tsx` + `layoutSync.ts` per spec §6 Flow 7. Subscribes to `chartStore` changes; debounce 500ms; PUT `/api/chart/layouts/<instrument_id>` with `If-Match: <etag>` header. On 412 → re-GET; toast "layout updated elsewhere — reconcile?"; show diff modal (simple JSON-diff acceptable). Apply codex_defaults A.

- [ ] **Step 3: Run → PASS; commit.**

```bash
git commit -m "feat(phase9): ChartLayoutSync debounced PUT + If-Match reconcile"
```

---

### Task 47: Mobile toolbar collapse + responsive parity

- [ ] **Step 1: Failing test** at 375×667 viewport: toolbar collapses to 5–7 most-used drawings (Trend Line, Horizontal Line, Fib Retracement, Rectangle, Text, Long/Short Position, Indicator picker). Full toolbar accessible via fullscreen button.

- [ ] **Step 2: Codex prompt** — implement responsive collapse via Tailwind v4 `@media`-driven classes; respect rem-only invariant.

- [ ] **Step 3: Browser smoke** at 375×667 + 1440×900; commit.

```bash
git commit -m "feat(phase9): mobile toolbar collapse + fullscreen passthrough"
```

---

### Task 48: Reviewer chain — Chunk H close-out

Per §10 row "H": base + typescript-reviewer + database-reviewer (If-Match + JSONB translator). Apply inline. Commit fixes.

---

# Chunk I — E2E + perf + close-out

---

### Task 49: Playwright E2E golden flows (6)

**Files:**
- Create: `frontend/e2e/phase9-charting.spec.ts`

- [ ] **Step 1: Implement 6 flows** per spec §10 lines 1003-1010:
  1. Open chart for active-set symbol → bars render ≤ 2s, RSI added → persists across refresh.
  2. Open chart for cold symbol → backfill triggers → bars render ≤ 5s → live tail updates within 10s.
  3. Scroll back 6 months → cursor pagination → klinecharts prepends bars; no duplicate fetches if scrolled twice.
  4. Drag SL on open position → ConfirmDialog → ModifyOrder → SL handle moves, position toast.
  5. Mobile viewport 375×667: chart renders, simplified toolbar visible, pinch-zoom works, tap-to-fullscreen visible.
  6. Aggregator crash injection (compose `kill bar_aggregator`) → ticks queue in WAL → restart → no bar gaps after recovery.

- [ ] **Step 2: Run E2E in compose env**

```bash
cd frontend && pnpm exec playwright test e2e/phase9-charting.spec.ts
```
Expected: 6/6 PASS.

- [ ] **Step 3: Commit**

```bash
git commit -m "test(phase9): 6 Playwright E2E golden flows"
```

---

### Task 50: Performance smoke tests

**Files:**
- Create: `backend/tests/perf/test_bars_p95.py` (BE)
- Create: `frontend/e2e/phase9-perf.spec.ts` (FE: live-tail latency, render time)

- [ ] **Step 1: Implement 3 perf gates** per spec §10 lines 1012-1016:
  - p95 `/api/bars` ≤ 100ms per page (10k row cap).
  - 5y/1m range fetch (paginated): full result ≤ 3s wall-time.
  - 100 concurrent live-tail WS subscribers on 50 instruments: no tick loss; aggregator memory < 256MB.

- [ ] **Step 2: Run; record actuals in CHANGELOG.**

- [ ] **Step 3: Commit**

```bash
git commit -m "test(phase9): perf smoke (p95 100ms, 5y fetch 3s, 100 WS subs)"
```

---

### Task 51: Storage budget projection

- [ ] **Step 1: Run measurement at 100-instrument steady state for 24h.** Record actual disk usage of `bars_1s` + `bars_1m` + 10 CAGGs.
- [ ] **Step 2: Project to 1000 instruments + 7d/6mo retention.** Confirm under 200 GB headroom (spec §3 line 451).
- [ ] **Step 3: Document actuals in CHANGELOG v0.9.0 section.**

```bash
git commit -m "docs(phase9): storage budget projection — actuals at 100-inst steady state"
```

---

### Task 52: Close-out — CHANGELOG, TASKS, CLAUDE.md, tag v0.9.0

- [ ] **Step 1: Update `CHANGELOG.md`** with v0.9.0 section: deliverables 1–11 from spec §1, deferred items from spec §12, perf actuals, storage actuals, top risks status.

- [ ] **Step 2: Update `TASKS.md`** — mark Phase 9 complete; add v0.9.1 mini-phase placeholder.

- [ ] **Step 3: Update `CLAUDE.md`** — add `phase9_charting_topology.md` memory pointer line; remove "klineschart" typo if not already (Task 34).

- [ ] **Step 4: Write phase memory file** `~/.claude/projects/-home-joseph-dashboard/memory/phase9_shipped.md`:
  - Type: project
  - Body: shipped commits, deferred items, top 3 lessons, forward pointers to v0.9.1 + Phase 11/18/19 dependencies.
  - Update `MEMORY.md` index pointer.

- [ ] **Step 5: Final ARCHITECT-REVIEW retrospective** per spec §10 row "I". Apply CRIT + HIGH + MED.

- [ ] **Step 6: Tag + push**

```bash
git commit -m "docs(v0.9.0): close-out — CHANGELOG + TASKS + memory"
git tag -a v0.9.0 -m "Phase 9 — Charting v1 + bar aggregator + historical store"
git push origin main --tags
```

---

### Task 53: Cleanup follow-up + CI debt repayment

**CI debt context** (added 2026-05-07): CI has been red since multiple prior phases — possibly all the way back to Phase 0 per user clarification. User feedback `feedback_ci_review_per_phase_owed.md` directs that CI cleanup is its own multi-phase reconciliation effort, scheduled here at the very end of Phase 9 (NOT bundled into Chunk A through I work). This task expands to absorb that debt repayment for **every phase 0 through 8** that is missing its reviewer chain pass.

- [ ] **Step 1: Rename memory** — `phase9_pg_cert_auth.md` → `phase24_pg_cert_auth.md` (PG cert auth moved Phase 9 → Phase 24 per ROADMAP §42; flagged in `phase9_brainstorm.md`).
- [ ] **Step 2: Update `MEMORY.md` index pointer accordingly.**

- [ ] **Step 3: CI debt repayment — bisect-and-fix per workflow**
  - **`.github/workflows/ci.yml` backend job:** confirm `Pytest` step runs cleanly against fresh PG-18+TimescaleDB. Most likely root cause: a missing prerequisite migration that adds `BRACKET` to `order_types` before `0021_eq_alpaca_equity_bracket` references it. Bisect to find the phase where the gap was introduced (Phase 8b/8c suspected). Add a small idempotent shim migration if needed (`INSERT INTO order_types (...) ON CONFLICT DO NOTHING`).
  - **`.github/workflows/ci.yml` sidecar job:** verify the `sidecar_ibkr` path fix from `cc4232b` actually runs through to coverage gate. If still failing, surface logs.
  - **`.github/workflows/e2e-mock.yml`:** investigate failures (separate workflow, likely related rot in test fixtures).
  - **`.github/workflows/deploy.yml`:** investigate failures (likely related, possibly mTLS or env-var drift).
- [ ] **Step 4: Per-phase missing reviewer chains (Phase 0 → Phase 8c)** — user clarified 2026-05-07 the gap may track back to Phase 0. Procedure:
  1. For each phase tag (`v0.1.0` through `v0.10.0`) where the `phase{N}_shipped.md` memory does NOT mention reviewer findings applied: enumerate the commit range (`git log <prev-tag>..<this-tag>`).
  2. Run the chunk-end 5-reviewer chain (spec-compliance + code-reviewer + security-reviewer + database-reviewer + language-reviewer per `feedback_review_per_chunk.md`) against that commit range.
  3. Apply CRIT+HIGH+MED inline per `feedback_architect_findings_apply_through_medium.md`.
  4. Update the corresponding `phase{N}_shipped.md` memory to reflect findings + commits.
  5. Commit each phase's findings under one squashed commit per phase: `fix(ci): retroactive reviewer chain for phase{N} (commits {a}..{b})`.
- [ ] **Step 5: Validate CI green end-to-end** — push + watch; confirm all 4 workflows pass.
- [ ] **Step 6: Commit**

```bash
git commit -m "chore(memory): rename phase9_pg_cert_auth → phase24_pg_cert_auth"
git commit -m "fix(ci): debt repayment — bisect + per-phase reviewer chains for phases 5-8"
```

---

## Self-Review Notes

**1. Spec coverage:** Each spec section maps to tasks:
- §1 Goals → Tasks 1–53 (all deliverables 1–11 covered)
- §2 Architecture → Tasks 1, 11–17, 26–33
- §3 Data Model → Tasks 1–7, 18 (7 migrations: 0023, 0023a, 0023b, 0024, 0026, 0027, 0025)
- §4 Backend Services → Tasks 8–10, 19–33
- §5 Frontend → Tasks 34–48
- §6 Data Flow → Flow 1+2 in 26–28; Flow 3 in 28; Flow 4 in 31, 36; Flow 5 in 15; Flow 6 in 43–44; Flow 7 in 46; Flow 8 in 16
- §7 Indicators → Tasks 41–42
- §8 Drawings → Tasks 38, 43
- §9 Error Handling → covered across all chunks via codex_defaults C + per-task tests
- §10 Testing → Task-level TDD + reviewer chain at end of each chunk + Tasks 49–50
- §11 Migration & Rollout → 11 chunks honored 1-to-1 (A=Tasks1-10, B=11-17, B-bis=18, C=19-25, D=26-33, E=34-40, F1=41, F2=42, G=43-45, H=46-48, I=49-53)
- §12 Risks → mitigations embedded in Tasks 13 (WAL bound), 22 (IBKR pacing), 26 (cross-worker), 30 (nonce), 43-44 (drag race + tick), 18 (CAGG correctness)

**2. Placeholder scan:** No "TBD" / "TODO" / "implement later" / "appropriate error handling" patterns. Every Codex prompt inlines a specific spec line range.

**3. Type consistency:**
- `_SOURCE_PRIORITY` mapping defined in Task 8, referenced in Tasks 26 (priority encoding) + 15 (minute_emitter aggregator-99 path).
- `BucketState`/`BucketSnapshot` defined in Task 12, used by Task 14 (coalescer) + Task 15 (flusher).
- `BarPage` shape defined in Task 28; consumed by FE in Task 36.
- `pending_modify_id` Map: Task 43 declares state shape, Task 44 implements lifecycle.
- Migrations chain: 0022 (existing) → 0023 → 0023a → 0023b → 0024 → 0026 → 0027 → 0025 (B-bis); 0025 down_revision = 0027.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-07-phase9-charting-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, two-stage review (spec-compliance then code-quality) between tasks, fast iteration. Note: per CLAUDE.md routing, code goes to **Codex** (`gpt-5-codex`), Anthropic subagents review.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for human review.

**Which approach?**
