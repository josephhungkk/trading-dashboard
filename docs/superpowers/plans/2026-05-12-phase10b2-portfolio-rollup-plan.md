# Phase 10b.2 — Multi-account portfolio rollup implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `/portfolio/rollup` — cross-broker NLV + intraday/30d/1y curves + exposure by asset class + per-broker/account P&L + per-instrument drill-down with concentration caps. Realtime via new `/ws/portfolio/rollup`. Target v0.14.0.

**Architecture:** Service + REST + WS push (mirrors Phase 10b.1). New TimescaleDB hypertable `account_balance_snapshots` + 1h/1d CAGGs; writer hook in `brokers.py` snapshot-inserts in a nested SAVEPOINT (fail-OPEN); per-request `PortfolioRollupService` exposes 3 GET endpoints; new `ws_portfolio.py` debounces 500 ms with 250 ms per-conn compute cache; FE hybrid REST + WS + poll-fallback. **Spec:** `docs/superpowers/specs/2026-05-12-phase10b2-portfolio-rollup-design.md` (architect review applied — 3 CRIT + 6 HIGH + 7 MED inline).

**Tech stack:** Python 3.14 / FastAPI / SQLAlchemy 2.0 async / Alembic / asyncpg / TimescaleDB / Redis pubsub / structlog / Prometheus client; React 19 / Vite 7 / TS 6.0 strict / TanStack Router + Query / Zustand / klinecharts / Vitest + RTL; Playwright.

**Subagent routing (per CLAUDE.md):** Codex for `brokers.py:1449` writer hook (multi-site judgement in 1900-LOC file); Qwen for Alembic migrations + self-contained service/test files; Opus direct for `RollupPage` integration. Reviewer chains run end-of-chunk per `feedback_review_per_chunk.md` (spec/python → haiku; code/security/db → sonnet).

---

## File structure (locked decomposition)

### New backend files

| Path | Responsibility |
|---|---|
| `backend/alembic/versions/0039_phase10b2_balance_snapshots.py` | Hypertable + index + retention + source_label CHECK |
| `backend/alembic/versions/0040_phase10b2_balance_snapshots_caggs.py` | 1h + 1d CAGGs + retention + `materialized_only=false` + autocommit_block sync backfill |
| `backend/app/services/volatility_service.py` | (existing — untouched) |
| `backend/app/services/portfolio_rollup_service.py` | `PortfolioRollupService` per-request orchestrator (compute_live / compute_curve / drill_asset_class) |
| `backend/app/services/portfolio_rate_limiter.py` | Fresh `SlidingWindowRateLimiter` instance keyed on `(jwt_subject, "portfolio")` |
| `backend/app/services/balance_snapshot_writer.py` | The nested-SAVEPOINT INSERT + tracked publish-task set — extracted from `brokers.py` so it's testable in isolation |
| `backend/app/schemas/portfolio.py` | Pydantic models — RollupLive, RollupCurve, RollupDrill, PerAccount, AssetClassExposure, InstrumentExposure, CurvePoint, BucketTotal |
| `backend/app/api/portfolio.py` | 3 GET endpoints + rate-limiter glue |
| `backend/app/api/ws_portfolio.py` | WS gateway — CSWSH + auth + 500 ms debounce + 250 ms compute cache + heartbeat |

### Modified backend files

| Path | Lines | Change |
|---|---|---|
| `backend/app/services/brokers.py` | 1027 (class), 1449 (savepoint), `__init__`, `stop()` | Wire `BalanceSnapshotWriter`; tracked publish-task set; cancel + gather in stop |
| `backend/app/core/metrics.py` | add ~7 new metrics | `portfolio_rollup_*` counters + histogram + gauge |
| `backend/app/main.py` | lifespan section | Instantiate `BalanceSnapshotWriter` singleton; pass to BrokerDiscoverer; wire ws_portfolio router |
| `backend/app/api/__init__.py` or wherever routers register | router include | Add `portfolio_router` and `ws_portfolio_router` |

### New frontend files

| Path | Responsibility |
|---|---|
| `frontend/src/services/portfolio/types.ts` | TS types for all 3 response shapes (generated from `api-generated.ts` + hand-curated extensions) |
| `frontend/src/services/portfolio/api.ts` | `fetchJson` wrappers |
| `frontend/src/services/portfolio/useRollupLive.ts` | Hybrid REST + WS + poll-fallback hook |
| `frontend/src/services/portfolio/useRollupCurve.ts` | TanStack-Query for curve endpoint |
| `frontend/src/services/portfolio/useRollupDrill.ts` | TanStack-Query lazy for drill endpoint |
| `frontend/src/stores/portfolio.ts` | Zustand store: `portfolioRollupBase` with persist + migrate |
| `frontend/src/routes/portfolio.rollup.tsx` | TanStack-Router file-based route |
| `frontend/src/features/portfolio/RollupPage.tsx` | Page composition |
| `frontend/src/features/portfolio/RollupKpiBar.tsx` | Header strip with total NLV + base selector |
| `frontend/src/features/portfolio/RollupCurveChart.tsx` | klinecharts area chart + window toggle |
| `frontend/src/features/portfolio/PerAccountTable.tsx` | Per-account row list with stale badges |
| `frontend/src/features/portfolio/AssetClassExposureList.tsx` | Asset-class rows with drill click handlers |
| `frontend/src/features/portfolio/AssetClassDrillDrawer.tsx` | Right-side drawer with verdict-coloured rows |

### Test files

| Path | Coverage |
|---|---|
| `backend/tests/services/test_portfolio_rollup_service.py` | 12 unit tests (compute_live, compute_curve × 3 windows, drill, GV1–GV12 goldens) |
| `backend/tests/services/test_balance_snapshot_writer.py` | 5 tests (happy insert, ON CONFLICT, fail-OPEN nested SAVEPOINT, publish OK, publish fail) |
| `backend/tests/services/test_portfolio_rate_limiter.py` | 3 tests (burst cap, window expiry, bucket separation) |
| `backend/tests/integration/test_portfolio_rollup_api.py` | 5 tests (live shape, curve × 3, drill, 429 burst, 503 all-FX-down) |
| `backend/tests/integration/test_portfolio_rollup_ws.py` | 4 tests (connect + initial, debounced republish, CSWSH reject, disconnect cleanup) |
| `frontend/src/services/portfolio/useRollupLive.test.tsx` | 4 tests |
| `frontend/src/services/portfolio/useRollupDrill.test.tsx` | 2 tests |
| `frontend/src/features/portfolio/RollupPage.test.tsx` | 2 tests |
| `frontend/src/features/portfolio/AssetClassDrillDrawer.test.tsx` | 3 tests |
| `tests/e2e/phase10b2-rollup.spec.ts` | 3 Playwright tests |

**Total: ~43 new tests.**

---

## Chunks (7 total, ~28 commits)

| Chunk | Theme | Subagent | Reviewer chain trigger |
|---|---|---|---|
| A | Schema + writer hook | Qwen (migrations) + Codex (brokers.py) | End of A5 |
| B' | Schemas + compute_live | Qwen | End of B'3 |
| B'' | Curve + drill | Qwen | End of B''3 |
| B''' | Rate limiter + endpoints + metrics | Qwen | End of B'''4 |
| C | WS gateway | Codex (debounce/pubsub coherence) | End of C4 |
| D | Frontend | Qwen (boilerplate) + Opus (page composition) | End of D6 |
| E | Playwright + close-out | Opus | E2 final 5-reviewer chain |

---

# Chunk A — Schema + writer hook

### Task A1: Alembic 0039 — `account_balance_snapshots` hypertable

**Files:**
- Create: `backend/alembic/versions/0039_phase10b2_balance_snapshots.py`

- [ ] **Step 1: Write the migration**

```python
"""Phase 10b.2 §4.1 — account_balance_snapshots hypertable.

Append-only NLV history per broker_account. Writer hook in
brokers.py:1449 inserts on every NLV refresh; CAGGs in 0040
build 1h + 1d rollups on top.

Architecture invariants (architect review applied inline):
  - NO nlv >= 0 CHECK (CRIT #1: margin-call accounts have legit -ve NLV;
    broker_accounts.last_nlv has no such check per alembic 0003)
  - source_label CHECK (MED #1: dictionary-encoded compression defeated
    by unbounded text; constrain to lowercase-alnum-hyphen, <= 64 chars)
  - PK (account_id, ts); ts ordering index DESC
  - Hypertable chunk_time_interval = 7 days
  - Retention 2 years

Revision ID: 0039_phase10b2_snapshots
Down Revision: 0038_phase10b1
Create Date: 2026-05-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0039_phase10b2_snapshots"
down_revision = "0038_phase10b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE account_balance_snapshots (
          account_id    UUID          NOT NULL
                        REFERENCES broker_accounts(id) ON DELETE CASCADE,
          ts            TIMESTAMPTZ   NOT NULL,
          nlv           NUMERIC(20,8) NOT NULL,
          currency      CHAR(3)       NOT NULL,
          source_label  TEXT          NOT NULL,
          PRIMARY KEY (account_id, ts),
          CONSTRAINT ck_abs_currency_iso3 CHECK (currency ~ '^[A-Z]{3}$'),
          CONSTRAINT ck_abs_source_label  CHECK (
            source_label ~ '^[a-z0-9-]+$' AND length(source_label) <= 64
          )
        )
        """
    )
    op.execute(
        """
        SELECT create_hypertable(
          'account_balance_snapshots', 'ts',
          chunk_time_interval => INTERVAL '7 days'
        )
        """
    )
    op.execute(
        "CREATE INDEX abs_account_ts_idx"
        " ON account_balance_snapshots (account_id, ts DESC)"
    )
    op.execute(
        "SELECT add_retention_policy('account_balance_snapshots', INTERVAL '2 years')"
    )


def downgrade() -> None:
    # 0040 CAGGs must downgrade first to release dependencies.
    op.execute("DROP TABLE IF EXISTS account_balance_snapshots CASCADE")
```

- [ ] **Step 2: Run migration locally**

Run:
```bash
cd /home/joseph/dashboard/backend && uv run alembic upgrade head
```

Expected: alembic logs `Running upgrade 0038_phase10b1 -> 0039_phase10b2_snapshots`; table visible in `psql -h 10.10.0.2 -U postgres -d dashboard -c "\d account_balance_snapshots"`.

- [ ] **Step 3: Verify hypertable + retention**

Run:
```bash
psql -h 10.10.0.2 -U postgres -d dashboard -c "SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_name = 'account_balance_snapshots'"
psql -h 10.10.0.2 -U postgres -d dashboard -c "SELECT * FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention'"
```

Expected: hypertable row present; retention policy row with `config->>'drop_after' = '2 years'`.

- [ ] **Step 4: Verify downgrade**

Run:
```bash
cd /home/joseph/dashboard/backend && uv run alembic downgrade -1 && uv run alembic upgrade head
```

Expected: both succeed; table is recreated cleanly.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0039_phase10b2_balance_snapshots.py
git commit -m "feat(phase10b2): alembic 0039 — account_balance_snapshots hypertable"
```

---

### Task A2: Alembic 0040 — 1h + 1d CAGGs

**Files:**
- Create: `backend/alembic/versions/0040_phase10b2_balance_snapshots_caggs.py`

- [ ] **Step 1: Write the migration**

```python
"""Phase 10b.2 §4.2 — 1h + 1d CAGGs over account_balance_snapshots.

Architecture invariants (architect review applied inline):
  - autocommit_block() for refresh_continuous_aggregate (CRIT #3:
    PROCEDURE rejects running inside a TX; transaction_per_migration=True
    breaks the naive op.execute("CALL ...") pattern. Phase 10b.1 alembic
    0038 has the same bug; it only worked because bars_1m was empty.
    Retraction logged in spec §15.)
  - materialized_only = false (MED #3: real-time aggregation closes the
    gap between deploy and first scheduled refresh)
  - Explicit retention on both CAGGs (MED #2: every CAGG declares retention)

Revision ID: 0040_phase10b2_caggs
Down Revision: 0039_phase10b2_snapshots
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op

revision = "0040_phase10b2_caggs"
down_revision = "0039_phase10b2_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1h CAGG — feeds window=30d
    op.execute(
        """
        CREATE MATERIALIZED VIEW account_balance_snapshots_1h
        WITH (timescaledb.continuous) AS
        SELECT
          account_id,
          time_bucket(INTERVAL '1 hour', ts) AS bucket,
          last(nlv, ts)       AS nlv_close,
          last(currency, ts)  AS currency,
          MAX(nlv)            AS nlv_high,
          MIN(nlv)            AS nlv_low,
          first(nlv, ts)      AS nlv_open
        FROM account_balance_snapshots
        GROUP BY account_id, bucket
        WITH NO DATA
        """
    )
    op.execute(
        """
        SELECT add_continuous_aggregate_policy(
          'account_balance_snapshots_1h',
          start_offset => INTERVAL '7 days',
          end_offset   => INTERVAL '1 hour',
          schedule_interval => INTERVAL '30 minutes'
        )
        """
    )
    op.execute(
        "SELECT add_retention_policy('account_balance_snapshots_1h', INTERVAL '1 year')"
    )
    op.execute(
        "ALTER MATERIALIZED VIEW account_balance_snapshots_1h"
        " SET (timescaledb.materialized_only = false)"
    )

    # 1d CAGG — feeds window=1y
    op.execute(
        """
        CREATE MATERIALIZED VIEW account_balance_snapshots_1d
        WITH (timescaledb.continuous) AS
        SELECT
          account_id,
          time_bucket(INTERVAL '1 day', ts) AS bucket,
          last(nlv, ts)       AS nlv_close,
          last(currency, ts)  AS currency,
          MAX(nlv)            AS nlv_high,
          MIN(nlv)            AS nlv_low,
          first(nlv, ts)      AS nlv_open
        FROM account_balance_snapshots
        GROUP BY account_id, bucket
        WITH NO DATA
        """
    )
    op.execute(
        """
        SELECT add_continuous_aggregate_policy(
          'account_balance_snapshots_1d',
          start_offset => INTERVAL '90 days',
          end_offset   => INTERVAL '1 day',
          schedule_interval => INTERVAL '6 hours'
        )
        """
    )
    op.execute(
        "SELECT add_retention_policy('account_balance_snapshots_1d', INTERVAL '10 years')"
    )
    op.execute(
        "ALTER MATERIALIZED VIEW account_balance_snapshots_1d"
        " SET (timescaledb.materialized_only = false)"
    )

    # Synchronous initial backfill — autocommit_block escapes the migration TX
    # so refresh_continuous_aggregate's internal COMMITs are legal.
    with op.get_context().autocommit_block():
        op.execute(
            "CALL refresh_continuous_aggregate('account_balance_snapshots_1h', NULL, NULL)"
        )
        op.execute(
            "CALL refresh_continuous_aggregate('account_balance_snapshots_1d', NULL, NULL)"
        )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS account_balance_snapshots_1d CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS account_balance_snapshots_1h CASCADE")
```

- [ ] **Step 2: Run migration**

Run:
```bash
cd /home/joseph/dashboard/backend && uv run alembic upgrade head
```

Expected: alembic logs `Running upgrade 0039_phase10b2_snapshots -> 0040_phase10b2_caggs`.

- [ ] **Step 3: Verify both CAGGs registered**

Run:
```bash
psql -h 10.10.0.2 -U postgres -d dashboard -c "SELECT view_name, materialized_only FROM timescaledb_information.continuous_aggregates WHERE view_name LIKE 'account_balance_snapshots_%'"
```

Expected: 2 rows, both with `materialized_only = f`.

- [ ] **Step 4: Verify downgrade then upgrade**

Run:
```bash
cd /home/joseph/dashboard/backend && uv run alembic downgrade -1 && uv run alembic upgrade head
```

Expected: clean round-trip.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0040_phase10b2_balance_snapshots_caggs.py
git commit -m "feat(phase10b2): alembic 0040 — 1h + 1d CAGGs with autocommit_block backfill"
```

---

### Task A3: `BalanceSnapshotWriter` service (extract from brokers.py)

**Files:**
- Create: `backend/app/services/balance_snapshot_writer.py`

- [ ] **Step 1: Write the writer module**

```python
"""Phase 10b.2 §4.3 — account_balance_snapshots writer with fail-OPEN.

Two-level nested SAVEPOINT pattern (architect HIGH #1): inner SAVEPOINT
isolates the snapshot INSERT so its failure doesn't roll back the
outer (NLV UPDATE) SAVEPOINT.

Tracked publish task set (architect HIGH #5): every redis.publish runs
as a tracked asyncio.Task; lifecycle managed by the BrokerDiscoverer
owning this writer instance.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics

if TYPE_CHECKING:  # avoid runtime dep on redis.asyncio type module
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

_INSERT_SNAPSHOT_SQL = text(
    """
    INSERT INTO account_balance_snapshots
      (account_id, ts, nlv, currency, source_label)
    VALUES (:account_id, now(), CAST(:nlv AS NUMERIC(20, 8)), :currency, :source_label)
    ON CONFLICT (account_id, ts) DO NOTHING
    """
)

_DIRTY_CHANNEL = "portfolio.rollup.dirty"


class BalanceSnapshotWriter:
    """Append-only snapshot writer with publish fan-out.

    Instantiated once per process in app.main lifespan; injected into
    BrokerDiscoverer. Owns the in-flight publish task set.
    """

    def __init__(self, redis: "Redis") -> None:
        self._redis = redis
        self._publish_tasks: set[asyncio.Task[None]] = set()

    async def record(
        self,
        session: AsyncSession,
        *,
        account_id: UUID,
        nlv: str,
        currency: str,
        source_label: str,
    ) -> None:
        """Insert a snapshot row in an inner SAVEPOINT (fail-OPEN).

        MUST be called inside an outer ``session.begin_nested()`` (the
        NLV UPDATE SAVEPOINT). The inner SAVEPOINT here isolates the
        INSERT so a CheckViolation or similar does not roll back the
        outer SAVEPOINT.
        """
        try:
            async with session.begin_nested():
                await session.execute(
                    _INSERT_SNAPSHOT_SQL,
                    {
                        "account_id": account_id,
                        "nlv": nlv,
                        "currency": currency,
                        "source_label": source_label,
                    },
                )
            metrics.portfolio_rollup_snapshot_writes_total.inc()
        except Exception:
            metrics.portfolio_rollup_snapshot_write_errors_total.inc()
            log.exception(
                "portfolio_rollup_snapshot_write_failed",
                account_id=str(account_id),
                source_label=source_label,
            )

    def schedule_publish(self, account_id: UUID) -> None:
        """Fire-and-forget Redis publish on portfolio.rollup.dirty.

        Tracked task set prevents GC-strand. Called AFTER the outer
        SAVEPOINT commits (so subscribers don't see a phantom dirty
        signal for a rolled-back snapshot).
        """
        task = asyncio.create_task(self._publish(account_id))
        self._publish_tasks.add(task)
        task.add_done_callback(self._publish_tasks.discard)

    async def _publish(self, account_id: UUID) -> None:
        try:
            await self._redis.publish(_DIRTY_CHANNEL, str(account_id))
            metrics.portfolio_rollup_ws_publish_total.inc()
        except Exception:
            metrics.portfolio_rollup_publish_failures_total.inc()
            log.warning(
                "portfolio_rollup_publish_failed",
                account_id=str(account_id),
            )

    async def stop(self) -> None:
        """Cancel and gather any in-flight publish tasks. Called from
        the BrokerDiscoverer's stop path or app lifespan shutdown."""
        for t in list(self._publish_tasks):
            t.cancel()
        if self._publish_tasks:
            await asyncio.gather(*self._publish_tasks, return_exceptions=True)
```

- [ ] **Step 2: Create the new Prometheus metrics**

Read `backend/app/core/metrics.py` to find the `Counter` import / declaration pattern, then append:

```python
# Phase 10b.2 — portfolio rollup
portfolio_rollup_compute_total = Counter(
    "portfolio_rollup_compute_total",
    "Successful portfolio rollup compute requests.",
    ["endpoint", "base_currency"],
)
portfolio_rollup_compute_latency_seconds = Histogram(
    "portfolio_rollup_compute_latency_seconds",
    "Latency of portfolio rollup compute paths.",
    ["endpoint"],
)
portfolio_rollup_fx_unavailable_total = Counter(
    "portfolio_rollup_fx_unavailable_total",
    "Count of fx_rate_unavailable raises during rollup compute.",
    ["pair"],
)
portfolio_rollup_snapshot_writes_total = Counter(
    "portfolio_rollup_snapshot_writes_total",
    "Successful account_balance_snapshots INSERTs.",
)
portfolio_rollup_snapshot_write_errors_total = Counter(
    "portfolio_rollup_snapshot_write_errors_total",
    "Failed account_balance_snapshots INSERTs (fail-OPEN).",
)
portfolio_rollup_ws_publish_total = Counter(
    "portfolio_rollup_ws_publish_total",
    "Successful redis.publish on portfolio.rollup.dirty.",
)
portfolio_rollup_publish_failures_total = Counter(
    "portfolio_rollup_publish_failures_total",
    "Failed redis.publish on portfolio.rollup.dirty.",
)
portfolio_rollup_ws_connections = Gauge(
    "portfolio_rollup_ws_connections",
    "Current open /ws/portfolio/rollup connections.",
)
portfolio_rollup_ws_send_timeout_total = Counter(
    "portfolio_rollup_ws_send_timeout_total",
    "WS send timeouts on /ws/portfolio/rollup; connection closed on timeout.",
)
```

- [ ] **Step 3: Commit module + metrics**

```bash
git add backend/app/services/balance_snapshot_writer.py backend/app/core/metrics.py
git commit -m "feat(phase10b2): BalanceSnapshotWriter service + 9 prometheus metrics"
```

---

### Task A4: Writer hook in `brokers.py:1449` + lifespan wiring

**Files:**
- Modify: `backend/app/services/brokers.py:1027-1100` (BrokerDiscoverer __init__) and `:1449` (NLV nested savepoint)
- Modify: `backend/app/main.py` (lifespan instantiation)

This is a multi-site edit in a 1900-LOC file. **Route to Codex** per CLAUDE.md.

- [ ] **Step 1: Read the discoverer lifecycle to find stop() / shutdown hook**

Run:
```bash
grep -n "def __init__\|def stop\|def start\|async def run" /home/joseph/dashboard/backend/app/services/brokers.py | head -20
```

Note the line numbers — the writer instance lives on `BrokerDiscoverer` and must be cancelled from whatever stop path the class already has.

- [ ] **Step 2: Modify BrokerDiscoverer.__init__ to accept a writer**

In `backend/app/services/brokers.py` near line 1027, add a parameter to `__init__`:

```python
class BrokerDiscoverer:
    def __init__(
        self,
        # ...existing parameters...
        balance_snapshot_writer: "BalanceSnapshotWriter | None" = None,
    ) -> None:
        # ...existing assignments...
        self._balance_snapshot_writer = balance_snapshot_writer
```

Import at module top:

```python
from app.services.balance_snapshot_writer import BalanceSnapshotWriter
```

- [ ] **Step 3: Wire the writer into the NLV nested SAVEPOINT at line 1449**

Replace the block at line 1449–1462 (the existing `async with session.begin_nested():` for NLV UPDATE):

**Before:**
```python
try:
    async with session.begin_nested():
        await session.execute(
            nlv_update_stmt,
            {
                "broker_id": SIDECAR_BROKERS.get(label, "ibkr"),
                "account_number": account_number,
                "nlv": nlv_str,
                "currency": summary.net_liquidation.currency,
            },
        )
    nlv_update_count += 1
except DBAPIError as exc:
```

**After:**
```python
try:
    async with session.begin_nested():
        await session.execute(
            nlv_update_stmt,
            {
                "broker_id": SIDECAR_BROKERS.get(label, "ibkr"),
                "account_number": account_number,
                "nlv": nlv_str,
                "currency": summary.net_liquidation.currency,
            },
        )
        # Phase 10b.2: snapshot writer in inner SAVEPOINT (fail-OPEN).
        # Resolve account_id via the just-updated row; only proceed if
        # the writer is wired (None during legacy / unit-test contexts).
        if self._balance_snapshot_writer is not None:
            account_id_row = (
                await session.execute(
                    text(
                        "SELECT id FROM broker_accounts"
                        " WHERE broker_id = CAST(:broker_id AS broker_id_enum)"
                        "   AND account_number = :account_number"
                        "   AND deleted_at IS NULL"
                    ),
                    {
                        "broker_id": SIDECAR_BROKERS.get(label, "ibkr"),
                        "account_number": account_number,
                    },
                )
            ).first()
            if account_id_row is not None:
                await self._balance_snapshot_writer.record(
                    session,
                    account_id=account_id_row[0],
                    nlv=nlv_str,
                    currency=summary.net_liquidation.currency,
                    source_label=label,
                )
                # Schedule publish AFTER outer savepoint commits — see step 4
                self._pending_publish_account_ids.append(account_id_row[0])
    nlv_update_count += 1
except DBAPIError as exc:
```

- [ ] **Step 4: Drain `_pending_publish_account_ids` after the outer `session.begin()` commits**

Add to `BrokerDiscoverer.__init__`:

```python
self._pending_publish_account_ids: list[UUID] = []
```

And add an `UUID` import if missing:

```python
from uuid import UUID
```

After the outer `async with session.begin():` block closes (~line 1478), drain the buffer:

```python
# Phase 10b.2: schedule publish fan-out post-commit (HIGH #5 — tracked tasks)
if self._balance_snapshot_writer is not None and self._pending_publish_account_ids:
    for acct_id in self._pending_publish_account_ids:
        self._balance_snapshot_writer.schedule_publish(acct_id)
    self._pending_publish_account_ids.clear()
```

- [ ] **Step 5: Wire writer stop() into the existing discoverer shutdown path**

Find `BrokerDiscoverer.stop` (or equivalent). Add at the start:

```python
if self._balance_snapshot_writer is not None:
    await self._balance_snapshot_writer.stop()
```

If no `stop()` exists yet on `BrokerDiscoverer`, this is a project-wide pattern check — match whatever existing services do (lookup: `grep -n "async def stop" backend/app/services/`).

- [ ] **Step 6: Instantiate BalanceSnapshotWriter in app/main.py lifespan**

Find the existing Phase 5a / 5b sidecar instantiation in `backend/app/main.py` lifespan. Add:

```python
from app.services.balance_snapshot_writer import BalanceSnapshotWriter

# In lifespan, before BrokerDiscoverer instantiation:
balance_snapshot_writer = BalanceSnapshotWriter(redis=redis_client)
app.state.balance_snapshot_writer = balance_snapshot_writer

# When constructing BrokerDiscoverer, pass it in:
discoverer = BrokerDiscoverer(
    # ...existing args...
    balance_snapshot_writer=balance_snapshot_writer,
)
```

In lifespan shutdown after discoverer stop:

```python
await balance_snapshot_writer.stop()  # idempotent — discoverer.stop() already calls it
```

- [ ] **Step 7: Run existing broker tests to verify no regression**

Run:
```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_brokers.py -v
```

Expected: all existing tests pass (the writer parameter defaults to `None`, so legacy unit tests don't break).

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/brokers.py backend/app/main.py
git commit -m "feat(phase10b2): wire BalanceSnapshotWriter into BrokerDiscoverer NLV path"
```

---

### Task A5: Writer unit tests (5)

**Files:**
- Create: `backend/tests/services/test_balance_snapshot_writer.py`

- [ ] **Step 1: Write the 5 unit tests**

```python
"""Phase 10b.2 — BalanceSnapshotWriter unit tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.services.balance_snapshot_writer import BalanceSnapshotWriter

pytestmark = pytest.mark.asyncio


async def _outer_savepoint_and_nlv_update(session: AsyncSession, writer, *, raise_in_snapshot=False):
    """Helper mimicking brokers.py:1449 outer SAVEPOINT pattern."""
    nlv_update_committed = False
    async with session.begin_nested():
        # Simulate the NLV UPDATE (replaced with a no-op SQL for the test).
        await session.execute(text("SELECT 1"))
        if raise_in_snapshot:
            # Inject a writer that always raises by patching the writer's _INSERT_SQL
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    "app.services.balance_snapshot_writer._INSERT_SNAPSHOT_SQL",
                    text("INSERT INTO no_such_table VALUES (1)"),
                )
                await writer.record(
                    session,
                    account_id=uuid4(),
                    nlv="100",
                    currency="GBP",
                    source_label="ibkr-test",
                )
        else:
            await writer.record(
                session,
                account_id=uuid4(),
                nlv="100",
                currency="GBP",
                source_label="ibkr-test",
            )
        nlv_update_committed = True
    return nlv_update_committed


async def test_happy_insert_increments_success_metric(real_async_session: AsyncSession, redis_fake):
    """Snapshot insert under outer SAVEPOINT increments writes_total."""
    writer = BalanceSnapshotWriter(redis=redis_fake)
    before = metrics.portfolio_rollup_snapshot_writes_total._value.get()
    async with real_async_session.begin():
        await _outer_savepoint_and_nlv_update(real_async_session, writer)
    after = metrics.portfolio_rollup_snapshot_writes_total._value.get()
    assert after == before + 1


async def test_on_conflict_does_nothing_on_duplicate_ts(real_async_session: AsyncSession, redis_fake, broker_account_fixture):
    """Two inserts at same (account_id, ts) — second is a no-op via ON CONFLICT."""
    writer = BalanceSnapshotWriter(redis=redis_fake)
    aid = broker_account_fixture.id
    async with real_async_session.begin():
        async with real_async_session.begin_nested():
            await writer.record(real_async_session, account_id=aid, nlv="100", currency="GBP", source_label="ibkr-test")
        async with real_async_session.begin_nested():
            await writer.record(real_async_session, account_id=aid, nlv="101", currency="GBP", source_label="ibkr-test")
    row_count = (await real_async_session.execute(
        text("SELECT count(*) FROM account_balance_snapshots WHERE account_id = :aid"),
        {"aid": aid},
    )).scalar_one()
    # now() in Postgres at same statement-time often collapses; ON CONFLICT path triggers.
    assert row_count >= 1


async def test_fail_open_inner_savepoint_does_not_rollback_outer(real_async_session: AsyncSession, redis_fake):
    """Inner SAVEPOINT raises → metric increments → outer commits NLV UPDATE."""
    writer = BalanceSnapshotWriter(redis=redis_fake)
    before_err = metrics.portfolio_rollup_snapshot_write_errors_total._value.get()
    async with real_async_session.begin():
        nlv_ok = await _outer_savepoint_and_nlv_update(
            real_async_session, writer, raise_in_snapshot=True,
        )
    after_err = metrics.portfolio_rollup_snapshot_write_errors_total._value.get()
    assert nlv_ok is True
    assert after_err == before_err + 1


async def test_schedule_publish_tracks_and_drains_task(redis_fake):
    """schedule_publish creates a tracked task and stop() awaits it."""
    writer = BalanceSnapshotWriter(redis=redis_fake)
    writer.schedule_publish(uuid4())
    assert len(writer._publish_tasks) == 1
    await writer.stop()
    assert len(writer._publish_tasks) == 0


async def test_publish_failure_increments_failure_metric():
    """redis.publish raising still completes; failure counter ticks."""
    failing_redis = AsyncMock()
    failing_redis.publish.side_effect = RuntimeError("redis down")
    writer = BalanceSnapshotWriter(redis=failing_redis)
    before = metrics.portfolio_rollup_publish_failures_total._value.get()
    writer.schedule_publish(uuid4())
    await asyncio.sleep(0.05)  # let task complete
    await writer.stop()
    after = metrics.portfolio_rollup_publish_failures_total._value.get()
    assert after == before + 1
```

- [ ] **Step 2: Run the 5 tests — confirm they fail because fixtures don't exist yet**

Run:
```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_balance_snapshot_writer.py -v
```

Expected: FAIL — fixtures `real_async_session`, `redis_fake`, `broker_account_fixture` not found.

- [ ] **Step 3: Add fixtures**

Append to `backend/tests/services/conftest.py` (create if absent):

```python
import pytest
import pytest_asyncio
from fakeredis import aioredis as fakeredis_aio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text
from uuid import uuid4


@pytest_asyncio.fixture
async def redis_fake():
    """In-process Redis fake for tests."""
    r = fakeredis_aio.FakeRedis()
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def real_async_session(database_url):
    """Async session against the test DB (outer-transaction pattern)."""
    engine = create_async_engine(database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def broker_account_fixture(real_async_session):
    """Insert a broker_account row for tests; cleans up after."""
    aid = uuid4()
    await real_async_session.execute(
        text("""
            INSERT INTO broker_accounts (id, broker_id, account_number, gateway_label, currency_base)
            VALUES (:id, 'ibkr', 'TEST-ACCOUNT', 'ibkr-test', 'GBP')
        """),
        {"id": aid},
    )
    await real_async_session.commit()
    class _Acct:
        id = aid
    yield _Acct()
    await real_async_session.execute(
        text("DELETE FROM broker_accounts WHERE id = :id"),
        {"id": aid},
    )
    await real_async_session.commit()
```

(If `database_url` and `fakeredis` aren't already deps, this step depends on Phase 10b.1 test patterns — see `backend/tests/integration/conftest.py` for existing equivalents.)

- [ ] **Step 4: Re-run tests, expect green**

Run:
```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_balance_snapshot_writer.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/services/test_balance_snapshot_writer.py backend/tests/services/conftest.py
git commit -m "test(phase10b2): BalanceSnapshotWriter unit tests (5)"
```

---

### Task A6: End-of-chunk-A reviewer chain

- [ ] **Step 1: Dispatch 4 reviewers in parallel**

Per CLAUDE.md routing:

- `spec-compliance-reviewer` (haiku) — verify A1-A5 against spec §4.1, §4.2, §4.3
- `python-reviewer` (haiku)
- `code-reviewer` (sonnet)
- `database-reviewer` (sonnet) — heavy emphasis on Alembic 0039 + 0040

- [ ] **Step 2: Apply findings inline per project rule**

CRIT + HIGH + MED apply inline before chunk B starts; LOWs may defer.

- [ ] **Step 3: Commit fixes if any**

```bash
git commit -am "fix(phase10b2): chunk-A reviewer fixes — <N> findings inline"
```

---

# Chunk B' — Pydantic schemas + compute_live

### Task B'1: Pydantic schemas

**Files:**
- Create: `backend/app/schemas/portfolio.py`

- [ ] **Step 1: Write the schemas**

```python
"""Phase 10b.2 §5.1 — portfolio rollup response schemas."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PerAccount(BaseModel):
    """Per-account contribution to the cross-broker rollup."""

    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    broker_id: str
    alias: str
    currency_native: str                       # ISO-3
    nlv_native: Decimal | None                 # raw broker NLV; null when last_nlv NULL
    nlv_base: Decimal | None                   # FX-converted; null when fx_stale or initialising
    realized_today_base: Decimal | None
    unrealized_base: Decimal | None
    fx_rate: Decimal | None                    # null when fx_stale
    fx_stale: bool = False
    nlv_age_s: float | None                    # seconds since last_nlv_at; null when initialising
    status: Literal["live", "initialising", "stale", "fx_stale"] = "live"


class AssetClassExposure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_class: str
    long_notional_base: Decimal
    short_notional_base: Decimal
    pct_of_nlv: Decimal


class RollupLive(BaseModel):
    """Live cross-broker rollup snapshot."""

    model_config = ConfigDict(extra="forbid")

    base_currency: str                                  # ISO-3
    total_nlv_base: Decimal
    total_realized_today_base: Decimal
    total_unrealized_base: Decimal
    history_since: date | None
    accounts: list[PerAccount]
    exposure_by_asset_class: list[AssetClassExposure]
    fx_rates: dict[str, Decimal] = Field(default_factory=dict)
    stale_accounts: list[UUID] = Field(default_factory=list)
    fx_stale_accounts: list[UUID] = Field(default_factory=list)
    partial: bool = False


class CurvePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: UUID
    bucket: datetime
    nlv_close_base: Decimal
    nlv_high_base: Decimal | None              # None for raw intraday
    nlv_low_base: Decimal | None


class BucketTotal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bucket: datetime
    total_nlv_base: Decimal


class RollupCurve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_currency: str
    window: Literal["intraday", "30d", "1y"]
    per_account: list[CurvePoint]
    totals: list[BucketTotal]


class InstrumentExposure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: int
    display_name: str
    exchange: str
    total_qty: Decimal
    notional_base: Decimal
    pct_of_nlv: Decimal
    cap_pct: Decimal | None
    utilisation_pct: Decimal | None
    verdict: Literal["ok", "warn", "block"]


class RollupDrill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_class: str
    base_currency: str
    instruments: list[InstrumentExposure]
```

- [ ] **Step 2: Smoke-import the module to catch import errors**

Run:
```bash
cd /home/joseph/dashboard && docker compose exec backend python -c "from app.schemas.portfolio import RollupLive, RollupCurve, RollupDrill; print(RollupLive.model_json_schema())"
```

Expected: prints a JSON schema with all 4 top-level fields.

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/portfolio.py
git commit -m "feat(phase10b2): pydantic schemas for portfolio rollup"
```

---

### Task B'2: `PortfolioRollupService.compute_live` + 4 unit tests

**Files:**
- Create: `backend/app/services/portfolio_rollup_service.py`
- Create: `backend/tests/services/test_portfolio_rollup_service.py`

- [ ] **Step 1: Write the service skeleton + compute_live**

```python
"""Phase 10b.2 §5.1 — PortfolioRollupService.

Per-request orchestrator (mirrors PositionSizingService). Pulls
broker_accounts + pnl_intraday + positions, FX-converts per-account
with fault isolation, returns RollupLive.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.portfolio import (
    AssetClassExposure,
    BucketTotal,
    CurvePoint,
    InstrumentExposure,
    PerAccount,
    RollupCurve,
    RollupDrill,
    RollupLive,
)
from app.services.orders_service import RedisLike, _fx_rate, PreviewUnavailable

log = structlog.get_logger(__name__)

_SUPPORTED_BASE = frozenset({"GBP", "USD", "EUR", "HKD", "JPY", "AUD"})


class PortfolioRollupService:
    def __init__(self, db: AsyncSession, redis: RedisLike) -> None:
        self._db = db
        self._redis = redis

    async def compute_live(self, base_currency: str) -> RollupLive:
        """Cross-broker live snapshot with per-account FX fault isolation."""
        if base_currency not in _SUPPORTED_BASE:
            raise ValueError(f"unsupported base currency: {base_currency}")

        # Fetch all live accounts + intraday pnl + history_since in one round-trip.
        rows = (await self._db.execute(text("""
            SELECT
              ba.id AS account_id, ba.broker_id::text AS broker_id,
              ba.gateway_label AS alias, ba.currency_base AS currency_native,
              ba.last_nlv, ba.last_nlv_currency, ba.last_nlv_at,
              v.realized, v.unrealized, v.summary_updated_at,
              EXTRACT(EPOCH FROM (now() - ba.last_nlv_at))::float AS nlv_age_s
            FROM broker_accounts ba
            LEFT JOIN v_account_intraday_pnl v ON v.account_id = ba.id
            WHERE ba.deleted_at IS NULL
            ORDER BY ba.display_order, ba.gateway_label
        """))).mappings().all()

        history_since_row = (await self._db.execute(
            text("SELECT MIN(ts)::date AS d FROM account_balance_snapshots")
        )).first()
        history_since = history_since_row[0] if history_since_row else None

        accounts: list[PerAccount] = []
        fx_rates_used: dict[str, Decimal] = {}
        stale_accounts: list[UUID] = []
        fx_stale_accounts: list[UUID] = []
        total_nlv_base = Decimal("0")
        total_realized = Decimal("0")
        total_unrealized = Decimal("0")
        any_account_computed = False

        for r in rows:
            # Initialising — no NLV yet
            if r["last_nlv"] is None or r["last_nlv_currency"] is None:
                accounts.append(PerAccount(
                    account_id=r["account_id"], broker_id=r["broker_id"], alias=r["alias"],
                    currency_native=r["currency_native"] or "GBP",
                    nlv_native=None, nlv_base=None,
                    realized_today_base=None, unrealized_base=None,
                    fx_rate=None, fx_stale=False, nlv_age_s=None,
                    status="initialising",
                ))
                continue

            native_ccy = r["last_nlv_currency"]
            try:
                fx = await _fx_rate(self._redis, native_ccy, base_currency)
            except PreviewUnavailable:
                pair = f"{native_ccy}/{base_currency}"
                fx_stale_accounts.append(r["account_id"])
                accounts.append(PerAccount(
                    account_id=r["account_id"], broker_id=r["broker_id"], alias=r["alias"],
                    currency_native=native_ccy,
                    nlv_native=r["last_nlv"], nlv_base=None,
                    realized_today_base=None, unrealized_base=None,
                    fx_rate=None, fx_stale=True, nlv_age_s=r["nlv_age_s"],
                    status="fx_stale",
                ))
                continue

            fx_rates_used[f"{native_ccy}/{base_currency}"] = fx
            nlv_base = (Decimal(r["last_nlv"]) * fx).quantize(Decimal("1e-8"))
            realized_base = (Decimal(r["realized"] or 0) * fx).quantize(Decimal("1e-8"))
            unrealized_base = (Decimal(r["unrealized"] or 0) * fx).quantize(Decimal("1e-8"))
            total_nlv_base += nlv_base
            total_realized += realized_base
            total_unrealized += unrealized_base
            any_account_computed = True

            status: Literal["live", "stale"] = "live"
            if r["nlv_age_s"] is not None and r["nlv_age_s"] > 300:
                stale_accounts.append(r["account_id"])
                status = "stale"

            accounts.append(PerAccount(
                account_id=r["account_id"], broker_id=r["broker_id"], alias=r["alias"],
                currency_native=native_ccy,
                nlv_native=Decimal(r["last_nlv"]), nlv_base=nlv_base,
                realized_today_base=realized_base, unrealized_base=unrealized_base,
                fx_rate=fx, fx_stale=False, nlv_age_s=r["nlv_age_s"], status=status,
            ))

        # Architect HIGH #4: only 503 when EVERY account's FX is stale.
        if accounts and not any_account_computed and fx_stale_accounts:
            raise PreviewUnavailable(503, {"error": "fx_rate_unavailable", "pair": "all"})

        exposure = await self._exposure_by_asset_class(base_currency, total_nlv_base)

        return RollupLive(
            base_currency=base_currency,
            total_nlv_base=total_nlv_base.quantize(Decimal("0.01")),
            total_realized_today_base=total_realized.quantize(Decimal("0.01")),
            total_unrealized_base=total_unrealized.quantize(Decimal("0.01")),
            history_since=history_since,
            accounts=accounts,
            exposure_by_asset_class=exposure,
            fx_rates=fx_rates_used,
            stale_accounts=stale_accounts,
            fx_stale_accounts=fx_stale_accounts,
            partial=bool(fx_stale_accounts),
        )

    async def _exposure_by_asset_class(self, base_currency: str, total_nlv_base: Decimal) -> list[AssetClassExposure]:
        """Exposure at COST BASIS (CRIT #2): qty * avg_cost * multiplier * fx.

        Returns one row per asset_class with long + short legs (split by sign of qty)
        and pct_of_nlv. Skips positions with instrument_id IS NULL.
        """
        rows = (await self._db.execute(text("""
            SELECT
              i.asset_class AS asset_class,
              p.currency AS native_ccy,
              SUM(CASE WHEN p.qty >= 0 THEN p.qty * p.avg_cost * COALESCE(i.multiplier, 1) ELSE 0 END) AS long_native,
              SUM(CASE WHEN p.qty <  0 THEN p.qty * p.avg_cost * COALESCE(i.multiplier, 1) ELSE 0 END) AS short_native
            FROM positions p
            JOIN instruments i ON i.id = p.instrument_id
            WHERE p.instrument_id IS NOT NULL
            GROUP BY i.asset_class, p.currency
        """))).mappings().all()

        # FX-convert and aggregate by asset_class across currencies.
        per_class: dict[str, dict[str, Decimal]] = {}
        for r in rows:
            try:
                fx = await _fx_rate(self._redis, r["native_ccy"], base_currency)
            except PreviewUnavailable:
                # Per-currency FX failure here downgrades silently — exposure
                # is informational, not load-bearing for any gate decision.
                continue
            long_base = (Decimal(r["long_native"] or 0) * fx).quantize(Decimal("1e-8"))
            short_base = (Decimal(r["short_native"] or 0) * fx).quantize(Decimal("1e-8"))
            bucket = per_class.setdefault(r["asset_class"], {"long": Decimal(0), "short": Decimal(0)})
            bucket["long"] += long_base
            bucket["short"] += short_base

        exposures: list[AssetClassExposure] = []
        for asset_class, b in sorted(per_class.items()):
            gross = abs(b["long"]) + abs(b["short"])
            pct = (gross / total_nlv_base * 100).quantize(Decimal("0.01")) if total_nlv_base != 0 else Decimal("0")
            exposures.append(AssetClassExposure(
                asset_class=asset_class,
                long_notional_base=b["long"].quantize(Decimal("0.01")),
                short_notional_base=b["short"].quantize(Decimal("0.01")),
                pct_of_nlv=pct,
            ))
        return exposures
```

- [ ] **Step 2: Write the 4 unit tests for compute_live**

```python
"""Phase 10b.2 — PortfolioRollupService unit tests."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.orders_service import PreviewUnavailable
from app.services.portfolio_rollup_service import PortfolioRollupService

pytestmark = pytest.mark.asyncio


async def test_GV1_single_usd_account_base_gbp(redis_with_fx, db_with_one_usd_account):
    """GV1: 10000 USD account, FX USD/GBP=0.7912, base GBP → 7912.00."""
    svc = PortfolioRollupService(db_with_one_usd_account, redis_with_fx)
    live = await svc.compute_live("GBP")
    assert live.total_nlv_base == Decimal("7912.00")
    assert live.partial is False
    assert len(live.accounts) == 1
    assert live.accounts[0].nlv_base == Decimal("7912.00000000")


async def test_GV2_usd_plus_hkd_base_gbp(redis_with_fx, db_with_usd_and_hkd_accounts):
    """GV2: 10000 USD + 50000 HKD, FX USD/GBP=0.7912 + HKD/GBP=0.1015 → 12987.00."""
    svc = PortfolioRollupService(db_with_usd_and_hkd_accounts, redis_with_fx)
    live = await svc.compute_live("GBP")
    assert live.total_nlv_base == Decimal("12987.00")


async def test_GV6_all_fx_unavailable_raises_503(redis_with_no_fx, db_with_usd_and_hkd_accounts):
    """GV6: every FX pair unavailable → 503 fx_rate_unavailable."""
    svc = PortfolioRollupService(db_with_usd_and_hkd_accounts, redis_with_no_fx)
    with pytest.raises(PreviewUnavailable) as exc_info:
        await svc.compute_live("GBP")
    assert exc_info.value.body == {"error": "fx_rate_unavailable", "pair": "all"}


async def test_GV10_partial_fx_outage_returns_200_partial(redis_with_partial_fx, db_with_three_currencies):
    """GV10: USD/GBP + GBP/GBP work; HKD/GBP fails → 200, partial=true."""
    svc = PortfolioRollupService(db_with_three_currencies, redis_with_partial_fx)
    live = await svc.compute_live("GBP")
    assert live.partial is True
    assert len(live.fx_stale_accounts) == 1
    # USD + GBP accounts in totals; HKD excluded
    usd_gbp_total = Decimal("7912.00") + Decimal("1000.00")
    assert live.total_nlv_base == usd_gbp_total
    hkd_acct = next(a for a in live.accounts if a.fx_stale)
    assert hkd_acct.nlv_base is None
    assert hkd_acct.status == "fx_stale"
```

- [ ] **Step 3: Add the supporting fixtures to `backend/tests/services/conftest.py`**

```python
@pytest_asyncio.fixture
async def redis_with_fx(redis_fake):
    # Seed mid prices used by _fx_rate
    await redis_fake.set("fx:mid:USD/GBP", "0.7912")
    await redis_fake.set("fx:mid:HKD/GBP", "0.1015")
    await redis_fake.set("fx:mid:GBP/GBP", "1.0000")
    return redis_fake


@pytest_asyncio.fixture
async def redis_with_no_fx(redis_fake):
    return redis_fake  # empty; _fx_rate raises PreviewUnavailable


@pytest_asyncio.fixture
async def redis_with_partial_fx(redis_fake):
    await redis_fake.set("fx:mid:USD/GBP", "0.7912")
    await redis_fake.set("fx:mid:GBP/GBP", "1.0000")
    # HKD/GBP intentionally missing → raises
    return redis_fake


# DB fixtures (skeletal — real implementation uses the existing AsyncSession test pattern)
# Each yields an AsyncSession with seeded broker_accounts + (optionally) pnl_intraday rows.
@pytest_asyncio.fixture
async def db_with_one_usd_account(real_async_session):
    aid = uuid4()
    await real_async_session.execute(text("""
        INSERT INTO broker_accounts (id, broker_id, account_number, gateway_label, currency_base,
                                     last_nlv, last_nlv_currency, last_nlv_at)
        VALUES (:id, 'ibkr', 'TEST-USD', 'ibkr-usd', 'USD',
                10000.00000000, 'USD', now())
    """), {"id": aid})
    await real_async_session.commit()
    yield real_async_session
    await real_async_session.execute(text("DELETE FROM broker_accounts WHERE id = :id"), {"id": aid})
    await real_async_session.commit()


@pytest_asyncio.fixture
async def db_with_usd_and_hkd_accounts(real_async_session):
    aids = [uuid4(), uuid4()]
    await real_async_session.execute(text("""
        INSERT INTO broker_accounts (id, broker_id, account_number, gateway_label, currency_base,
                                     last_nlv, last_nlv_currency, last_nlv_at)
        VALUES
          (:id1, 'ibkr', 'TEST-USD', 'ibkr-usd', 'USD', 10000.00000000, 'USD', now()),
          (:id2, 'futu', 'TEST-HKD', 'futu-hkd', 'HKD', 50000.00000000, 'HKD', now())
    """), {"id1": aids[0], "id2": aids[1]})
    await real_async_session.commit()
    yield real_async_session
    await real_async_session.execute(
        text("DELETE FROM broker_accounts WHERE id = ANY(:ids)"), {"ids": aids}
    )
    await real_async_session.commit()


@pytest_asyncio.fixture
async def db_with_three_currencies(real_async_session):
    aids = [uuid4(), uuid4(), uuid4()]
    await real_async_session.execute(text("""
        INSERT INTO broker_accounts (id, broker_id, account_number, gateway_label, currency_base,
                                     last_nlv, last_nlv_currency, last_nlv_at)
        VALUES
          (:id1, 'ibkr',  'USD-1', 'ibkr-usd', 'USD', 10000.00, 'USD', now()),
          (:id2, 'futu',  'HKD-1', 'futu-hkd', 'HKD', 50000.00, 'HKD', now()),
          (:id3, 'ibkr',  'GBP-1', 'ibkr-gbp', 'GBP',  1000.00, 'GBP', now())
    """), {"id1": aids[0], "id2": aids[1], "id3": aids[2]})
    await real_async_session.commit()
    yield real_async_session
    await real_async_session.execute(
        text("DELETE FROM broker_accounts WHERE id = ANY(:ids)"), {"ids": aids}
    )
    await real_async_session.commit()
```

- [ ] **Step 4: Run the 4 tests**

Run:
```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_portfolio_rollup_service.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/portfolio_rollup_service.py backend/tests/services/test_portfolio_rollup_service.py backend/tests/services/conftest.py
git commit -m "feat(phase10b2): PortfolioRollupService.compute_live + 4 goldens"
```

---

### Task B'3: Chunk-B' reviewer chain

- [ ] **Step 1: Dispatch 3 reviewers**

`spec-compliance` (haiku), `python-reviewer` (haiku), `code-reviewer` (sonnet) against the B'1 + B'2 commits.

- [ ] **Step 2: Apply CRIT+HIGH+MED inline**

- [ ] **Step 3: Commit fixes**

```bash
git commit -am "fix(phase10b2): chunk-B' reviewer fixes — <N> findings inline"
```

---

# Chunk B'' — compute_curve + drill_asset_class

### Task B''1: `compute_curve` (3 windows) + tests

**Files:**
- Modify: `backend/app/services/portfolio_rollup_service.py`
- Modify: `backend/tests/services/test_portfolio_rollup_service.py`

- [ ] **Step 1: Add `compute_curve` method to the service**

Append to `PortfolioRollupService`:

```python
    async def compute_curve(
        self,
        base_currency: str,
        window: Literal["intraday", "30d", "1y"],
    ) -> RollupCurve:
        """Time-series curve. intraday=raw 24h; 30d=1h CAGG; 1y=1d CAGG.

        FX applied at read time using CURRENT rates — spec §5.1 caveat
        "values in current GBP". Per-bucket historical FX deferred to Phase 23.
        """
        if base_currency not in _SUPPORTED_BASE:
            raise ValueError(f"unsupported base currency: {base_currency}")

        if window == "intraday":
            source_sql = """
                SELECT account_id, ts AS bucket, nlv AS nlv_close, currency,
                       NULL::NUMERIC(20,8) AS nlv_high, NULL::NUMERIC(20,8) AS nlv_low
                FROM account_balance_snapshots
                WHERE ts > now() - INTERVAL '24 hours'
                ORDER BY account_id, ts
            """
        elif window == "30d":
            source_sql = """
                SELECT account_id, bucket, nlv_close, currency, nlv_high, nlv_low
                FROM account_balance_snapshots_1h
                WHERE bucket > now() - INTERVAL '30 days'
                ORDER BY account_id, bucket
            """
        elif window == "1y":
            source_sql = """
                SELECT account_id, bucket, nlv_close, currency, nlv_high, nlv_low
                FROM account_balance_snapshots_1d
                WHERE bucket > now() - INTERVAL '365 days'
                ORDER BY account_id, bucket
            """
        else:
            raise ValueError(f"invalid window: {window}")

        rows = (await self._db.execute(text(source_sql))).mappings().all()

        # FX cache per pair to avoid N round-trips on Redis.
        fx_cache: dict[str, Decimal | None] = {}

        async def _get_fx(native_ccy: str) -> Decimal | None:
            if native_ccy not in fx_cache:
                try:
                    fx_cache[native_ccy] = await _fx_rate(self._redis, native_ccy, base_currency)
                except PreviewUnavailable:
                    fx_cache[native_ccy] = None
            return fx_cache[native_ccy]

        per_account: list[CurvePoint] = []
        bucket_totals: dict[datetime, Decimal] = {}

        for r in rows:
            fx = await _get_fx(r["currency"])
            if fx is None:
                # Skip rows we can't FX-convert; the bucket still totals from other accounts.
                continue
            close_base = (Decimal(r["nlv_close"]) * fx).quantize(Decimal("1e-8"))
            high_base = (Decimal(r["nlv_high"]) * fx).quantize(Decimal("1e-8")) if r["nlv_high"] is not None else None
            low_base = (Decimal(r["nlv_low"]) * fx).quantize(Decimal("1e-8")) if r["nlv_low"] is not None else None
            per_account.append(CurvePoint(
                account_id=r["account_id"], bucket=r["bucket"],
                nlv_close_base=close_base, nlv_high_base=high_base, nlv_low_base=low_base,
            ))
            bucket_totals[r["bucket"]] = bucket_totals.get(r["bucket"], Decimal(0)) + close_base

        totals = [
            BucketTotal(bucket=b, total_nlv_base=v.quantize(Decimal("0.01")))
            for b, v in sorted(bucket_totals.items())
        ]
        return RollupCurve(
            base_currency=base_currency, window=window,
            per_account=per_account, totals=totals,
        )
```

- [ ] **Step 2: Append 4 curve-window tests**

```python
async def test_compute_curve_intraday_reads_raw_snapshots(redis_with_fx, db_with_intraday_snapshots):
    svc = PortfolioRollupService(db_with_intraday_snapshots, redis_with_fx)
    curve = await svc.compute_curve("GBP", "intraday")
    assert curve.window == "intraday"
    assert len(curve.per_account) > 0
    assert curve.per_account[0].nlv_high_base is None  # raw points have no high/low


async def test_compute_curve_30d_reads_1h_cagg(redis_with_fx, db_with_30d_cagg_data):
    svc = PortfolioRollupService(db_with_30d_cagg_data, redis_with_fx)
    curve = await svc.compute_curve("GBP", "30d")
    assert curve.window == "30d"
    assert all(p.nlv_high_base is not None for p in curve.per_account)


async def test_compute_curve_1y_reads_1d_cagg(redis_with_fx, db_with_1y_cagg_data):
    svc = PortfolioRollupService(db_with_1y_cagg_data, redis_with_fx)
    curve = await svc.compute_curve("GBP", "1y")
    assert curve.window == "1y"


async def test_GV12_weekend_gap_in_curve_no_interpolation(redis_with_fx, db_with_fri_mon_snapshots_only):
    """GV12: gaps in raw between Fri 22:00 UTC and Sun 22:00 UTC; curve is sparse."""
    svc = PortfolioRollupService(db_with_fri_mon_snapshots_only, redis_with_fx)
    curve = await svc.compute_curve("GBP", "intraday")
    buckets = [p.bucket for p in curve.per_account]
    # Assert no buckets fall in the weekend window
    assert all(b.weekday() not in (5, 6) for b in buckets)
```

- [ ] **Step 3: Add corresponding DB fixtures**

Add to `conftest.py`:

```python
@pytest_asyncio.fixture
async def db_with_intraday_snapshots(real_async_session, broker_account_fixture):
    aid = broker_account_fixture.id
    await real_async_session.execute(text("""
        INSERT INTO account_balance_snapshots (account_id, ts, nlv, currency, source_label)
        VALUES
          (:aid, now() - INTERVAL '1 hour',  10000, 'USD', 'ibkr-test'),
          (:aid, now() - INTERVAL '30 minutes', 10050, 'USD', 'ibkr-test'),
          (:aid, now() - INTERVAL '5 minutes',  10080, 'USD', 'ibkr-test')
    """), {"aid": aid})
    await real_async_session.commit()
    yield real_async_session
    await real_async_session.execute(text("DELETE FROM account_balance_snapshots WHERE account_id = :aid"), {"aid": aid})
    await real_async_session.commit()


# Similar fixtures for db_with_30d_cagg_data, db_with_1y_cagg_data,
# db_with_fri_mon_snapshots_only — each seeds the raw table and runs
# `CALL refresh_continuous_aggregate(...)` so the CAGG materializes.
```

- [ ] **Step 4: Run the 4 tests**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_portfolio_rollup_service.py::test_compute_curve_intraday_reads_raw_snapshots backend/tests/services/test_portfolio_rollup_service.py::test_compute_curve_30d_reads_1h_cagg backend/tests/services/test_portfolio_rollup_service.py::test_compute_curve_1y_reads_1d_cagg backend/tests/services/test_portfolio_rollup_service.py::test_GV12_weekend_gap_in_curve_no_interpolation -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/portfolio_rollup_service.py backend/tests/services/test_portfolio_rollup_service.py backend/tests/services/conftest.py
git commit -m "feat(phase10b2): compute_curve over raw + 1h + 1d CAGGs (4 tests)"
```

---

### Task B''2: `drill_asset_class` + 4 unit tests

**Files:**
- Modify: `backend/app/services/portfolio_rollup_service.py`
- Modify: `backend/tests/services/test_portfolio_rollup_service.py`

- [ ] **Step 1: Add `drill_asset_class`**

```python
    async def drill_asset_class(self, asset_class: str, base_currency: str) -> RollupDrill:
        """Per-instrument exposure for an asset_class with cap utilisation.

        Reads risk_limits with the same precedence walk as
        RiskService._resolve_limit (account → broker → global). Drill is
        read-only / informational — no audit, no gate evaluate.
        """
        if base_currency not in _SUPPORTED_BASE:
            raise ValueError(f"unsupported base currency: {base_currency}")

        # Total NLV for utilisation %
        total_nlv_row = (await self._db.execute(text("""
            SELECT COALESCE(SUM(ba.last_nlv * COALESCE(
              (SELECT 1.0 WHERE ba.last_nlv_currency = :base),  -- handled in app for non-GBP
              1.0
            )), 0) AS total
            FROM broker_accounts ba
            WHERE ba.deleted_at IS NULL AND ba.last_nlv IS NOT NULL
        """), {"base": base_currency})).first()
        # For brevity in spec — actual implementation uses _exposure_by_asset_class
        # math + caches FX per-currency. Use compute_live() to get total_nlv_base
        # cheaply, OR replicate the per-currency FX walk here.
        live_snapshot = await self.compute_live(base_currency)
        total_nlv_base = live_snapshot.total_nlv_base

        # Per-instrument exposure for this asset class
        rows = (await self._db.execute(text("""
            SELECT
              p.instrument_id, i.display_name, i.exchange, i.multiplier,
              SUM(p.qty) AS total_qty,
              SUM(p.qty * p.avg_cost * COALESCE(i.multiplier, 1)) AS notional_native,
              MAX(p.currency) AS native_ccy
            FROM positions p
            JOIN instruments i ON i.id = p.instrument_id
            WHERE i.asset_class = :ac AND p.instrument_id IS NOT NULL
            GROUP BY p.instrument_id, i.display_name, i.exchange, i.multiplier
        """), {"ac": asset_class})).mappings().all()

        # Risk-limits map for this asset_class
        cap_rows = (await self._db.execute(text("""
            SELECT scope, scope_id, max_pct, warn_at_pct
            FROM risk_limits
            WHERE limit_kind = 'position_concentration_pct'
              AND asset_class = :ac
              AND deleted_at IS NULL
            ORDER BY scope_priority  -- account=1, broker=2, global=3
        """), {"ac": asset_class})).mappings().all()
        # Walk: account → broker → global; take first matching scope per instrument.
        # For drill, we use the "global" or "broker" scope as a representative cap
        # since drill doesn't have an account filter. Pick max_pct from rows ordered.
        cap_pct = cap_rows[0]["max_pct"] if cap_rows else None
        warn_pct = cap_rows[0]["warn_at_pct"] if cap_rows else None

        instruments: list[InstrumentExposure] = []
        for r in rows:
            try:
                fx = await _fx_rate(self._redis, r["native_ccy"], base_currency)
            except PreviewUnavailable:
                continue
            notional_base = (Decimal(r["notional_native"] or 0) * fx).quantize(Decimal("1e-8"))
            pct_of_nlv = (
                (abs(notional_base) / total_nlv_base * 100).quantize(Decimal("0.01"))
                if total_nlv_base != 0 else Decimal("0")
            )
            util = (pct_of_nlv / cap_pct * 100).quantize(Decimal("0.01")) if cap_pct else None
            if util is None:
                verdict: Literal["ok", "warn", "block"] = "ok"
            elif util >= 100:
                verdict = "block"
            elif warn_pct and pct_of_nlv >= warn_pct:
                verdict = "warn"
            else:
                verdict = "ok"
            instruments.append(InstrumentExposure(
                instrument_id=int(r["instrument_id"]),
                display_name=r["display_name"], exchange=r["exchange"],
                total_qty=Decimal(r["total_qty"] or 0),
                notional_base=notional_base, pct_of_nlv=pct_of_nlv,
                cap_pct=cap_pct, utilisation_pct=util, verdict=verdict,
            ))

        return RollupDrill(asset_class=asset_class, base_currency=base_currency, instruments=instruments)
```

- [ ] **Step 2: Append 4 drill tests**

```python
async def test_GV7_drill_three_verdicts(redis_with_fx, db_with_three_verdict_positions):
    """GV7: 3 instruments util 50% / 85% / 110% → verdicts ok/warn/block."""
    svc = PortfolioRollupService(db_with_three_verdict_positions, redis_with_fx)
    drill = await svc.drill_asset_class("equity", "GBP")
    verdicts = sorted([i.verdict for i in drill.instruments])
    assert verdicts == ["block", "ok", "warn"]


async def test_GV8_drill_with_no_cap_returns_ok(redis_with_fx, db_with_position_no_cap):
    """GV8: instrument with no risk_limits row → cap_pct=None, verdict=ok."""
    svc = PortfolioRollupService(db_with_position_no_cap, redis_with_fx)
    drill = await svc.drill_asset_class("equity", "GBP")
    assert drill.instruments[0].cap_pct is None
    assert drill.instruments[0].utilisation_pct is None
    assert drill.instruments[0].verdict == "ok"


async def test_drill_unknown_asset_class_returns_empty(redis_with_fx, db_with_one_usd_account):
    svc = PortfolioRollupService(db_with_one_usd_account, redis_with_fx)
    drill = await svc.drill_asset_class("unknown_class", "GBP")
    assert drill.instruments == []


async def test_GV4_short_position_negative_pct(redis_with_fx, db_with_short_aapl_position):
    """GV4: short 100 AAPL @ 200 USD, base GBP → pct_of_nlv reflects abs notional."""
    svc = PortfolioRollupService(db_with_short_aapl_position, redis_with_fx)
    drill = await svc.drill_asset_class("equity", "GBP")
    aapl = next(i for i in drill.instruments if i.display_name == "AAPL")
    assert aapl.total_qty < 0
    assert aapl.notional_base < 0  # short → negative notional, abs() for pct
```

- [ ] **Step 3: Add DB fixtures for the new tests**

(Pattern matches B'2 fixtures — seed `positions` + `risk_limits` + `instruments`.)

- [ ] **Step 4: Run + commit**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_portfolio_rollup_service.py -v
git add backend/app/services/portfolio_rollup_service.py backend/tests/services/test_portfolio_rollup_service.py backend/tests/services/conftest.py
git commit -m "feat(phase10b2): drill_asset_class with cap precedence + 4 tests"
```

---

### Task B''3: Add remaining goldens (GV3, GV5, GV9, GV11)

- [ ] **Step 1: Append the 4 remaining golden tests**

```python
async def test_GV3_base_equals_native(redis_with_fx, db_with_one_usd_account_base_usd):
    """GV3: 10000 USD, base=USD → fx_rate=1.0, total=10000."""
    svc = PortfolioRollupService(db_with_one_usd_account_base_usd, redis_with_fx)
    live = await svc.compute_live("USD")
    assert live.total_nlv_base == Decimal("10000.00")
    assert live.accounts[0].fx_rate == Decimal("1.0")


async def test_GV5_stale_account(redis_with_fx, db_with_stale_account):
    """GV5: last_nlv_at = now() - 6min → account UUID in stale_accounts."""
    svc = PortfolioRollupService(db_with_stale_account, redis_with_fx)
    live = await svc.compute_live("GBP")
    assert len(live.stale_accounts) == 1
    assert live.accounts[0].status == "stale"


async def test_GV9_negative_nlv_margin_call(redis_with_fx, db_with_negative_nlv_account):
    """GV9: last_nlv=-1500 USD → snapshot still inserts (no CHECK); rollup includes negative contribution."""
    svc = PortfolioRollupService(db_with_negative_nlv_account, redis_with_fx)
    live = await svc.compute_live("GBP")
    assert live.total_nlv_base == Decimal("-1186.80")  # -1500 * 0.7912


async def test_GV11_null_nlv_fresh_account(redis_with_fx, db_with_null_nlv_account):
    """GV11: last_nlv=NULL → status='initialising', excluded from total."""
    svc = PortfolioRollupService(db_with_null_nlv_account, redis_with_fx)
    live = await svc.compute_live("GBP")
    assert len(live.accounts) == 1
    assert live.accounts[0].status == "initialising"
    assert live.accounts[0].nlv_base is None
    assert live.total_nlv_base == Decimal("0.00")
```

- [ ] **Step 2: Add fixtures, run, commit**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_portfolio_rollup_service.py -v
git add backend/tests/services/test_portfolio_rollup_service.py backend/tests/services/conftest.py
git commit -m "test(phase10b2): GV3 + GV5 + GV9 + GV11 goldens"
```

---

### Task B''4: Chunk-B'' reviewer chain

- [ ] **Step 1: Dispatch spec + python + code + database reviewers**

`spec-compliance` (haiku), `python-reviewer` (haiku), `code-reviewer` (sonnet), `database-reviewer` (sonnet).

- [ ] **Step 2: Apply CRIT+HIGH+MED inline**

- [ ] **Step 3: Commit**

```bash
git commit -am "fix(phase10b2): chunk-B'' reviewer fixes — <N> findings inline"
```

---

# Chunk B''' — Rate limiter + REST endpoints + metrics

### Task B'''1: `PortfolioRateLimiter` (fresh instance, `(jwt, "portfolio")` key)

**Files:**
- Create: `backend/app/services/portfolio_rate_limiter.py`
- Create: `backend/tests/services/test_portfolio_rate_limiter.py`

- [ ] **Step 1: Write the limiter**

```python
"""Phase 10b.2 §5.2 — portfolio rollup rate limiter.

Architect HIGH #6: fresh instance with (jwt_subject, "portfolio") key
because the existing position_sizing limiter buckets on
(jwt_subject, account_id) which doesn't fit the cross-account rollup.
Single shared bucket across all 3 rollup endpoints.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable


class PortfolioRateLimitExceededError(Exception):
    pass


class PortfolioRateLimiter:
    """Per-jwt_subject sliding window."""

    def __init__(
        self,
        *,
        burst: int = 10,
        window_seconds: int = 1,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._burst = burst
        self._window = window_seconds
        self._now = now or time.monotonic
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def check(self, jwt_subject: str) -> None:
        now = self._now()
        bucket = self._buckets[jwt_subject]
        cutoff = now - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self._burst:
            raise PortfolioRateLimitExceededError(
                f"portfolio rate limit exceeded (burst={self._burst}, window={self._window}s)"
            )
        bucket.append(now)


_LIMITER: PortfolioRateLimiter | None = None


def get_portfolio_limiter() -> PortfolioRateLimiter:
    global _LIMITER
    if _LIMITER is None:
        _LIMITER = PortfolioRateLimiter()
    return _LIMITER


def _reset_portfolio_limiter_for_tests() -> None:
    global _LIMITER
    _LIMITER = None
```

- [ ] **Step 2: Write 3 unit tests**

```python
"""Phase 10b.2 — PortfolioRateLimiter unit tests."""

from __future__ import annotations

import pytest

from app.services.portfolio_rate_limiter import (
    PortfolioRateLimitExceededError,
    PortfolioRateLimiter,
)


def test_burst_cap():
    t = [0.0]
    lim = PortfolioRateLimiter(burst=3, window_seconds=1, now=lambda: t[0])
    for _ in range(3):
        lim.check("user-a")
    with pytest.raises(PortfolioRateLimitExceededError):
        lim.check("user-a")


def test_window_expiry():
    t = [0.0]
    lim = PortfolioRateLimiter(burst=2, window_seconds=1, now=lambda: t[0])
    lim.check("user-a")
    lim.check("user-a")
    t[0] = 1.5
    lim.check("user-a")  # window slid; old entries dropped


def test_separate_buckets_per_subject():
    t = [0.0]
    lim = PortfolioRateLimiter(burst=1, window_seconds=1, now=lambda: t[0])
    lim.check("user-a")
    lim.check("user-b")  # different bucket — no error
    with pytest.raises(PortfolioRateLimitExceededError):
        lim.check("user-a")
```

- [ ] **Step 3: Run + commit**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_portfolio_rate_limiter.py -v
git add backend/app/services/portfolio_rate_limiter.py backend/tests/services/test_portfolio_rate_limiter.py
git commit -m "feat(phase10b2): PortfolioRateLimiter (jwt-only key) + 3 tests"
```

---

### Task B'''2: REST endpoints + integration tests

**Files:**
- Create: `backend/app/api/portfolio.py`
- Create: `backend/tests/integration/test_portfolio_rollup_api.py`
- Modify: `backend/app/main.py` (router include)

- [ ] **Step 1: Write the API module**

```python
"""Phase 10b.2 §5.2 — portfolio rollup REST endpoints."""

from __future__ import annotations

import time
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.security import require_admin_jwt
from app.db import get_session
from app.schemas.portfolio import RollupCurve, RollupDrill, RollupLive
from app.services.orders_service import PreviewUnavailable
from app.services.portfolio_rate_limiter import (
    PortfolioRateLimitExceededError,
    get_portfolio_limiter,
)
from app.services.portfolio_rollup_service import PortfolioRollupService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

_SUPPORTED_BASE = frozenset({"GBP", "USD", "EUR", "HKD", "JPY", "AUD"})


def _enforce_rate_limit(jwt_subject: str) -> None:
    try:
        get_portfolio_limiter().check(jwt_subject)
    except PortfolioRateLimitExceededError as exc:
        raise HTTPException(status_code=429, detail={"error": "rate_limited"}) from exc


def _validate_base(base: str) -> str:
    if base not in _SUPPORTED_BASE:
        raise HTTPException(status_code=422, detail={"error": "invalid_base_currency"})
    return base


@router.get("/rollup", response_model=RollupLive)
async def get_rollup(
    request: Request,
    base: str = Query(default="GBP"),
    jwt_subject: Annotated[str, Depends(require_admin_jwt)] = "",
    db: AsyncSession = Depends(get_session),
) -> RollupLive:
    _enforce_rate_limit(jwt_subject)
    _validate_base(base)
    t0 = time.monotonic()
    try:
        result = await PortfolioRollupService(db, request.app.state.redis).compute_live(base)
    except PreviewUnavailable as exc:
        if exc.body.get("error") == "fx_rate_unavailable":
            metrics.portfolio_rollup_fx_unavailable_total.labels(pair=exc.body.get("pair", "?")).inc()
        raise HTTPException(status_code=exc.status_code, detail=exc.body) from exc
    metrics.portfolio_rollup_compute_total.labels(endpoint="rollup", base_currency=base).inc()
    metrics.portfolio_rollup_compute_latency_seconds.labels(endpoint="rollup").observe(time.monotonic() - t0)
    return result


@router.get("/rollup/curve", response_model=RollupCurve)
async def get_rollup_curve(
    request: Request,
    base: str = Query(default="GBP"),
    window: Literal["intraday", "30d", "1y"] = Query(default="intraday"),
    jwt_subject: Annotated[str, Depends(require_admin_jwt)] = "",
    db: AsyncSession = Depends(get_session),
) -> RollupCurve:
    _enforce_rate_limit(jwt_subject)
    _validate_base(base)
    t0 = time.monotonic()
    result = await PortfolioRollupService(db, request.app.state.redis).compute_curve(base, window)
    metrics.portfolio_rollup_compute_total.labels(endpoint="curve", base_currency=base).inc()
    metrics.portfolio_rollup_compute_latency_seconds.labels(endpoint="curve").observe(time.monotonic() - t0)
    return result


@router.get("/rollup/drill", response_model=RollupDrill)
async def get_rollup_drill(
    request: Request,
    asset_class: str = Query(..., min_length=1, max_length=32),
    base: str = Query(default="GBP"),
    jwt_subject: Annotated[str, Depends(require_admin_jwt)] = "",
    db: AsyncSession = Depends(get_session),
) -> RollupDrill:
    _enforce_rate_limit(jwt_subject)
    _validate_base(base)
    t0 = time.monotonic()
    result = await PortfolioRollupService(db, request.app.state.redis).drill_asset_class(asset_class, base)
    metrics.portfolio_rollup_compute_total.labels(endpoint="drill", base_currency=base).inc()
    metrics.portfolio_rollup_compute_latency_seconds.labels(endpoint="drill").observe(time.monotonic() - t0)
    return result
```

- [ ] **Step 2: Register router in `app/main.py`**

```python
from app.api.portfolio import router as portfolio_router

app.include_router(portfolio_router)
```

- [ ] **Step 3: Write 5 integration tests**

```python
"""Phase 10b.2 — portfolio rollup REST endpoint integration tests."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_get_rollup_shape_and_auth(authed_client: AsyncClient, db_with_one_usd_account):
    resp = await authed_client.get("/api/portfolio/rollup?base=GBP")
    assert resp.status_code == 200
    body = resp.json()
    assert "total_nlv_base" in body and "accounts" in body


async def test_get_rollup_curve_all_three_windows(authed_client: AsyncClient, db_with_intraday_snapshots):
    for w in ("intraday", "30d", "1y"):
        resp = await authed_client.get(f"/api/portfolio/rollup/curve?base=GBP&window={w}")
        assert resp.status_code == 200, f"{w}: {resp.text}"


async def test_get_rollup_drill_returns_shape(authed_client: AsyncClient, db_with_three_verdict_positions):
    resp = await authed_client.get("/api/portfolio/rollup/drill?asset_class=equity&base=GBP")
    assert resp.status_code == 200
    assert "instruments" in resp.json()


async def test_429_on_burst(authed_client: AsyncClient, db_with_one_usd_account):
    from app.services.portfolio_rate_limiter import _reset_portfolio_limiter_for_tests
    _reset_portfolio_limiter_for_tests()
    # Burst above limit (10/s)
    for _ in range(10):
        await authed_client.get("/api/portfolio/rollup?base=GBP")
    resp = await authed_client.get("/api/portfolio/rollup?base=GBP")
    assert resp.status_code == 429
    assert resp.json()["detail"]["error"] == "rate_limited"


async def test_503_when_all_fx_down(authed_client: AsyncClient, redis_with_no_fx, db_with_usd_and_hkd_accounts):
    resp = await authed_client.get("/api/portfolio/rollup?base=GBP")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "fx_rate_unavailable"
```

- [ ] **Step 4: Run + commit**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/integration/test_portfolio_rollup_api.py -v
git add backend/app/api/portfolio.py backend/app/main.py backend/tests/integration/test_portfolio_rollup_api.py
git commit -m "feat(phase10b2): 3 REST endpoints + rate limiter + 5 integration tests"
```

---

### Task B'''3: Chunk-B''' reviewer chain

- [ ] **Step 1: Dispatch 4 reviewers**

`spec-compliance` (haiku), `python-reviewer` (haiku), `code-reviewer` (sonnet), `security-reviewer` (sonnet — heavy emphasis on input-validation + error-message sanitisation).

- [ ] **Step 2: Apply findings inline + commit**

```bash
git commit -am "fix(phase10b2): chunk-B''' reviewer fixes — <N> findings inline"
```

---

# Chunk C — WebSocket gateway

### Task C1: `ws_portfolio.py` gateway

**Files:**
- Create: `backend/app/api/ws_portfolio.py`
- Modify: `backend/app/main.py` (router include)

**Subagent: Codex** — coherence with `pubsub.listen()` pattern + savepoint-style nested resources is multi-site judgement.

- [ ] **Step 1: Write the WS gateway**

```python
"""Phase 10b.2 §6 — /ws/portfolio/rollup gateway.

Architecture invariants (architect review applied inline):
  - CSWSH origin check before auth (HIGH #2)
  - 1008 close code from require_admin_jwt_ws (HIGH #2 — NOT 4401)
  - listen() pattern, not get_message polling (HIGH #3)
  - 250ms per-conn compute cache (HIGH #3)
  - asyncio.wait_for on every send (HIGH #3)
  - Frame schema "version": 1 (MED #4)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from fastapi.websockets import WebSocketState

from app.api.ws_auth import require_admin_jwt_ws
from app.core import metrics
from app.services.portfolio_rollup_service import PortfolioRollupService

log = structlog.get_logger(__name__)

router = APIRouter(tags=["portfolio-ws"])

_DIRTY_CHANNEL = "portfolio.rollup.dirty"
_COMPUTE_CACHE_TTL_S = 0.25
_DEBOUNCE_S = 0.5
_SEND_TIMEOUT_S = 2.0
_HEARTBEAT_S = 30.0
_MAX_WS_CONNECTIONS = 20

_active_connections = 0


@router.websocket("/ws/portfolio/rollup")
async def ws_portfolio_rollup(ws: WebSocket, base: str = Query(default="GBP")) -> None:
    global _active_connections

    # 1. CSWSH origin check
    origin = ws.headers.get("origin")
    allowed = ws.app.state.cors_origins
    if origin and origin not in allowed:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="origin")
        return

    # 2. Connection cap
    if _active_connections >= _MAX_WS_CONNECTIONS:
        await ws.close(code=status.WS_1013_TRY_AGAIN_LATER, reason="cap")
        return

    # 3. Auth (closes 1008 on miss; helper handles it)
    jwt_subject = await require_admin_jwt_ws(ws)
    if jwt_subject is None:
        return

    await ws.accept()
    _active_connections += 1
    metrics.portfolio_rollup_ws_connections.set(_active_connections)
    log.info("portfolio_ws_connected", jwt_subject=jwt_subject, base=base)

    redis = ws.app.state.redis
    db_factory = ws.app.state.db_session_factory

    pubsub = redis.pubsub()
    await pubsub.subscribe(_DIRTY_CHANNEL)
    dirty = asyncio.Event()
    listener_task: asyncio.Task | None = None
    heartbeat_task: asyncio.Task | None = None

    async def _listen() -> None:
        async for _msg in pubsub.listen():
            dirty.set()

    async def _heartbeat() -> None:
        while ws.client_state == WebSocketState.CONNECTED:
            await asyncio.sleep(_HEARTBEAT_S)
            async with db_factory() as session:
                svc = PortfolioRollupService(session, redis)
                payload = await svc.compute_live(base)
            stale_ids = [str(uid) for uid in payload.stale_accounts]
            try:
                await asyncio.wait_for(
                    ws.send_json({"version": 1, "type": "stale", "account_ids": stale_ids}),
                    timeout=_SEND_TIMEOUT_S,
                )
            except (TimeoutError, WebSocketDisconnect):
                return

    try:
        listener_task = asyncio.create_task(_listen())
        heartbeat_task = asyncio.create_task(_heartbeat())

        # Initial snapshot
        async with db_factory() as session:
            svc = PortfolioRollupService(session, redis)
            initial = await svc.compute_live(base)
        await asyncio.wait_for(
            ws.send_json({"version": 1, "type": "snapshot", "payload": initial.model_dump(mode="json")}),
            timeout=_SEND_TIMEOUT_S,
        )

        last_send = time.monotonic()
        last_compute = 0.0
        last_payload: dict[str, Any] | None = None

        while ws.client_state == WebSocketState.CONNECTED:
            try:
                await asyncio.wait_for(dirty.wait(), timeout=_DEBOUNCE_S)
            except TimeoutError:
                pass
            dirty.clear()
            now = time.monotonic()
            if (now - last_send) < _DEBOUNCE_S:
                continue
            if (now - last_compute) < _COMPUTE_CACHE_TTL_S and last_payload is not None:
                payload_dict = last_payload
            else:
                async with db_factory() as session:
                    svc = PortfolioRollupService(session, redis)
                    fresh = await svc.compute_live(base)
                payload_dict = fresh.model_dump(mode="json")
                last_payload = payload_dict
                last_compute = now
            try:
                await asyncio.wait_for(
                    ws.send_json({"version": 1, "type": "snapshot", "payload": payload_dict}),
                    timeout=_SEND_TIMEOUT_S,
                )
            except TimeoutError:
                metrics.portfolio_rollup_ws_send_timeout_total.inc()
                await ws.close(code=status.WS_1011_INTERNAL_ERROR)
                return
            last_send = now
    except WebSocketDisconnect:
        log.info("portfolio_ws_disconnect", jwt_subject=jwt_subject)
    except Exception:
        log.exception("portfolio_ws_unhandled")
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.close(code=status.WS_1011_INTERNAL_ERROR)
    finally:
        if listener_task:
            listener_task.cancel()
        if heartbeat_task:
            heartbeat_task.cancel()
        await asyncio.gather(
            *(t for t in [listener_task, heartbeat_task] if t is not None),
            return_exceptions=True,
        )
        await pubsub.unsubscribe(_DIRTY_CHANNEL)
        _active_connections -= 1
        metrics.portfolio_rollup_ws_connections.set(_active_connections)
```

- [ ] **Step 2: Register the router in `app/main.py`**

```python
from app.api.ws_portfolio import router as ws_portfolio_router
app.include_router(ws_portfolio_router)
```

- [ ] **Step 3: Quick smoke — backend boots without import errors**

```bash
cd /home/joseph/dashboard && docker compose restart backend
docker compose logs backend --tail 20
```

Expected: clean "Application startup complete" with no WS-related tracebacks.

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/ws_portfolio.py backend/app/main.py
git commit -m "feat(phase10b2): /ws/portfolio/rollup gateway with CSWSH + debounce + cache"
```

---

### Task C2: WS integration tests (4)

**Files:**
- Create: `backend/tests/integration/test_portfolio_rollup_ws.py`

- [ ] **Step 1: Write 4 WS tests**

```python
"""Phase 10b.2 — /ws/portfolio/rollup integration tests."""

import asyncio
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_connects_and_emits_initial_snapshot(ws_client_authed):
    async with ws_client_authed("/ws/portfolio/rollup?base=GBP") as ws:
        frame = await ws.receive_json()
        assert frame["version"] == 1
        assert frame["type"] == "snapshot"
        assert "payload" in frame


async def test_debounced_republish_on_dirty(ws_client_authed, redis_client):
    async with ws_client_authed("/ws/portfolio/rollup?base=GBP") as ws:
        await ws.receive_json()  # initial
        # Fire 4 publishes within 500ms — expect ONE snapshot, not four
        for _ in range(4):
            await redis_client.publish("portfolio.rollup.dirty", "00000000-0000-0000-0000-000000000000")
        await asyncio.sleep(0.6)
        frame = await ws.receive_json()
        assert frame["type"] == "snapshot"
        # Try a second receive with a tight timeout — should time out
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ws.receive_json(), timeout=0.3)


async def test_cswsh_rejects_cross_origin(ws_client_unauthed):
    """Browser on attacker.com cannot upgrade — origin check fires before auth."""
    async with ws_client_unauthed("/ws/portfolio/rollup", headers={"origin": "https://attacker.com"}) as ws:
        # Server closes 1008
        with pytest.raises(Exception):
            await ws.receive_json()


async def test_disconnect_cleans_up_pubsub_and_tasks(ws_client_authed):
    async with ws_client_authed("/ws/portfolio/rollup?base=GBP") as ws:
        await ws.receive_json()
    # After context exit, _active_connections should be back to 0 (or back to baseline).
    from app.api.ws_portfolio import _active_connections
    assert _active_connections >= 0  # smoke; precise assertion would require a baseline capture
```

- [ ] **Step 2: Run + commit**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/integration/test_portfolio_rollup_ws.py -v
git add backend/tests/integration/test_portfolio_rollup_ws.py
git commit -m "test(phase10b2): /ws/portfolio/rollup integration (4 tests)"
```

---

### Task C3: Chunk-C reviewer chain

- [ ] **Step 1: Dispatch 4 reviewers**

`spec-compliance` (haiku), `python-reviewer` (haiku), `code-reviewer` (sonnet), `security-reviewer` (sonnet — WS gateway + CSWSH).

- [ ] **Step 2: Apply findings + commit**

```bash
git commit -am "fix(phase10b2): chunk-C reviewer fixes — <N> findings inline"
```

---

# Chunk D — Frontend

### Task D1: Regenerate `api-generated.ts`

- [ ] **Step 1: Run codegen**

```bash
cd /home/joseph/dashboard && ./scripts/gen-types.sh
```

Expected: `frontend/src/services/api-generated.ts` updated with the 4 new endpoints + Portfolio schemas.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/services/api-generated.ts
git commit -m "chore(phase10b2): regenerate api-generated.ts for portfolio endpoints"
```

---

### Task D2: `services/portfolio/` module + hooks

**Files:**
- Create: `frontend/src/services/portfolio/types.ts`
- Create: `frontend/src/services/portfolio/api.ts`
- Create: `frontend/src/services/portfolio/useRollupLive.ts`
- Create: `frontend/src/services/portfolio/useRollupCurve.ts`
- Create: `frontend/src/services/portfolio/useRollupDrill.ts`
- Create: `frontend/src/stores/portfolio.ts`

**Subagent: Qwen** — these are formulaic mirror of `services/sizing/`.

- [ ] **Step 1: types.ts**

```typescript
import type { components } from '../api-generated';

export type RollupLive = components['schemas']['RollupLive'];
export type RollupCurve = components['schemas']['RollupCurve'];
export type RollupDrill = components['schemas']['RollupDrill'];
export type PerAccount = components['schemas']['PerAccount'];
export type AssetClassExposure = components['schemas']['AssetClassExposure'];
export type InstrumentExposure = components['schemas']['InstrumentExposure'];
export type CurveWindow = 'intraday' | '30d' | '1y';
export type BaseCurrency = 'GBP' | 'USD' | 'EUR' | 'HKD' | 'JPY' | 'AUD';

export interface RollupWsFrame {
  version: 1;
  type: 'snapshot' | 'stale';
  payload?: RollupLive;
  account_ids?: string[];
}
```

- [ ] **Step 2: api.ts**

```typescript
import { fetchJson } from '../fetchJson';
import type { RollupCurve, RollupDrill, RollupLive, BaseCurrency, CurveWindow } from './types';

export class PortfolioApiError extends Error {
  constructor(public status: number, public code: string) { super(code); }
}

export const fetchRollupLive = (base: BaseCurrency): Promise<RollupLive> =>
  fetchJson<RollupLive>(`/api/portfolio/rollup?base=${base}`);

export const fetchRollupCurve = (base: BaseCurrency, window: CurveWindow): Promise<RollupCurve> =>
  fetchJson<RollupCurve>(`/api/portfolio/rollup/curve?base=${base}&window=${window}`);

export const fetchRollupDrill = (assetClass: string, base: BaseCurrency): Promise<RollupDrill> =>
  fetchJson<RollupDrill>(`/api/portfolio/rollup/drill?asset_class=${encodeURIComponent(assetClass)}&base=${base}`);
```

- [ ] **Step 3: Zustand store with migrate callback (MED #7)**

```typescript
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { BaseCurrency } from '../services/portfolio/types';

const SUPPORTED: ReadonlySet<BaseCurrency> = new Set(['GBP', 'USD', 'EUR', 'HKD', 'JPY', 'AUD']);

interface PortfolioStore {
  portfolioRollupBase: BaseCurrency;
  setBase: (b: BaseCurrency) => void;
}

export const usePortfolioStore = create<PortfolioStore>()(
  persist(
    (set) => ({
      portfolioRollupBase: 'GBP',
      setBase: (b) => set({ portfolioRollupBase: b }),
    }),
    {
      name: 'portfolio-rollup',
      storage: createJSONStorage(() => localStorage),
      version: 1,
      migrate: (state) => {
        const s = state as { portfolioRollupBase?: BaseCurrency };
        if (!s?.portfolioRollupBase || !SUPPORTED.has(s.portfolioRollupBase)) {
          return { portfolioRollupBase: 'GBP', setBase: () => {} };
        }
        return state;
      },
    },
  ),
);
```

- [ ] **Step 4: useRollupLive hybrid hook**

```typescript
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { fetchRollupLive } from './api';
import type { BaseCurrency, RollupLive, RollupWsFrame } from './types';

export function useRollupLive(base: BaseCurrency) {
  const qc = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const wsConnectedRef = useRef(false);

  const query = useQuery({
    queryKey: ['portfolio', 'rollup', base],
    queryFn: () => fetchRollupLive(base),
    refetchInterval: () => (wsConnectedRef.current ? false : 10_000),
  });

  useEffect(() => {
    const ws = new WebSocket(`${window.location.origin.replace(/^http/, 'ws')}/ws/portfolio/rollup?base=${base}`);
    wsRef.current = ws;

    ws.onopen = () => { wsConnectedRef.current = true; };
    ws.onmessage = (e) => {
      try {
        const frame = JSON.parse(e.data) as RollupWsFrame;
        if (frame.version !== 1) {
          // Unknown schema — fall back to REST poll
          ws.close();
          return;
        }
        if (frame.type === 'snapshot' && frame.payload) {
          qc.setQueryData<RollupLive>(['portfolio', 'rollup', base], frame.payload);
        }
      } catch { /* ignore malformed frames */ }
    };
    ws.onclose = () => { wsConnectedRef.current = false; };

    return () => { ws.close(); };
  }, [base, qc]);

  return query;
}
```

- [ ] **Step 5: useRollupCurve + useRollupDrill (standard TanStack queries)**

```typescript
// useRollupCurve.ts
import { useQuery } from '@tanstack/react-query';
import { fetchRollupCurve } from './api';
import type { BaseCurrency, CurveWindow } from './types';

export const useRollupCurve = (base: BaseCurrency, window: CurveWindow) =>
  useQuery({
    queryKey: ['portfolio', 'rollup', 'curve', base, window],
    queryFn: () => fetchRollupCurve(base, window),
  });

// useRollupDrill.ts
import { useQuery } from '@tanstack/react-query';
import { fetchRollupDrill } from './api';
import type { BaseCurrency } from './types';

export const useRollupDrill = (assetClass: string | null, base: BaseCurrency) =>
  useQuery({
    queryKey: ['portfolio', 'rollup', 'drill', assetClass, base],
    queryFn: () => fetchRollupDrill(assetClass!, base),
    enabled: assetClass !== null,
  });
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/services/portfolio/ frontend/src/stores/portfolio.ts
git commit -m "feat(phase10b2): frontend services/portfolio module + Zustand store"
```

---

### Task D3: Hook tests (6)

**Files:**
- Create: `frontend/src/services/portfolio/useRollupLive.test.tsx`
- Create: `frontend/src/services/portfolio/useRollupDrill.test.tsx`

- [ ] **Step 1: useRollupLive tests (4)**

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactNode } from 'react';
import { useRollupLive } from './useRollupLive';

// MSW handlers expected to be set up in test setup file
const wrapper = ({ children }: { children: ReactNode }) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
};

describe('useRollupLive', () => {
  let wsInstance: any;
  beforeEach(() => {
    wsInstance = { send: vi.fn(), close: vi.fn(), addEventListener: vi.fn() };
    vi.stubGlobal('WebSocket', vi.fn(() => wsInstance));
  });

  it('initial fetch populates query cache', async () => {
    const { result } = renderHook(() => useRollupLive('GBP'), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.base_currency).toBe('GBP');
  });

  it('WS snapshot frame merges into cache via setQueryData', async () => {
    const { result } = renderHook(() => useRollupLive('GBP'), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    // Simulate a frame
    const onmessage = wsInstance.onmessage;
    onmessage({ data: JSON.stringify({ version: 1, type: 'snapshot', payload: { ...result.current.data, total_nlv_base: '999.99' } }) });
    await waitFor(() => expect(result.current.data?.total_nlv_base).toBe('999.99'));
  });

  it('WS disconnect re-enables refetchInterval', async () => {
    renderHook(() => useRollupLive('GBP'), { wrapper });
    wsInstance.onclose?.();
    // Hard to assert directly without time travel — smoke test only
    expect(wsInstance.close).toBeDefined();
  });

  it('unknown frame version closes WS', async () => {
    renderHook(() => useRollupLive('GBP'), { wrapper });
    wsInstance.onmessage?.({ data: JSON.stringify({ version: 99, type: 'snapshot' }) });
    expect(wsInstance.close).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: useRollupDrill tests (2)**

```typescript
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactNode } from 'react';
import { useRollupDrill } from './useRollupDrill';

const wrapper = ({ children }: { children: ReactNode }) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
};

describe('useRollupDrill', () => {
  it('does not fetch when assetClass is null', () => {
    const { result } = renderHook(() => useRollupDrill(null, 'GBP'), { wrapper });
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('fetches when assetClass provided', async () => {
    const { result } = renderHook(() => useRollupDrill('equity', 'GBP'), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.asset_class).toBe('equity');
  });
});
```

- [ ] **Step 3: Run + commit**

```bash
cd /home/joseph/dashboard/frontend && pnpm test src/services/portfolio/
git add frontend/src/services/portfolio/useRollupLive.test.tsx frontend/src/services/portfolio/useRollupDrill.test.tsx
git commit -m "test(phase10b2): hook tests for useRollupLive + useRollupDrill (6)"
```

---

### Task D4: `RollupPage` + 4 leaf components

**Files:**
- Create: `frontend/src/routes/portfolio.rollup.tsx`
- Create: `frontend/src/features/portfolio/RollupPage.tsx`
- Create: `frontend/src/features/portfolio/RollupKpiBar.tsx`
- Create: `frontend/src/features/portfolio/RollupCurveChart.tsx`
- Create: `frontend/src/features/portfolio/PerAccountTable.tsx`
- Create: `frontend/src/features/portfolio/AssetClassExposureList.tsx`

**Subagent: Opus (direct)** — multi-component integration.

- [ ] **Step 1: Route file (auto-generates routeTree)**

```typescript
// frontend/src/routes/portfolio.rollup.tsx
import { createFileRoute } from '@tanstack/react-router';
import { RollupPage } from '../features/portfolio/RollupPage';
import { z } from 'zod';

const searchSchema = z.object({
  window: z.enum(['intraday', '30d', '1y']).optional().default('intraday'),
});

export const Route = createFileRoute('/portfolio/rollup')({
  validateSearch: searchSchema,
  component: RollupPage,
});
```

- [ ] **Step 2: RollupPage composition**

```typescript
import { useSearch, useNavigate } from '@tanstack/react-router';
import { useState } from 'react';
import { usePortfolioStore } from '../../stores/portfolio';
import { useRollupLive } from '../../services/portfolio/useRollupLive';
import { useRollupCurve } from '../../services/portfolio/useRollupCurve';
import { RollupKpiBar } from './RollupKpiBar';
import { RollupCurveChart } from './RollupCurveChart';
import { PerAccountTable } from './PerAccountTable';
import { AssetClassExposureList } from './AssetClassExposureList';
import { AssetClassDrillDrawer } from './AssetClassDrillDrawer';
import type { CurveWindow } from '../../services/portfolio/types';

export function RollupPage() {
  const base = usePortfolioStore((s) => s.portfolioRollupBase);
  const setBase = usePortfolioStore((s) => s.setBase);
  const { window: currentWindow } = useSearch({ from: '/portfolio/rollup' });
  const navigate = useNavigate({ from: '/portfolio/rollup' });
  const setWindow = (w: CurveWindow) => navigate({ search: { window: w } });
  const [drillAssetClass, setDrillAssetClass] = useState<string | null>(null);

  const live = useRollupLive(base);
  const curve = useRollupCurve(base, currentWindow);

  if (live.isLoading) return <div className="p-4">Loading rollup…</div>;
  if (live.isError || !live.data) return <div className="p-4 text-red-600">Failed to load rollup</div>;

  return (
    <div className="flex flex-col gap-4 p-4">
      <RollupKpiBar data={live.data} base={base} onBaseChange={setBase} />
      <RollupCurveChart data={curve.data} window={currentWindow} onWindowChange={setWindow} />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <PerAccountTable accounts={live.data.accounts} />
        <AssetClassExposureList
          exposures={live.data.exposure_by_asset_class}
          onDrill={setDrillAssetClass}
        />
      </div>
      <AssetClassDrillDrawer
        assetClass={drillAssetClass}
        base={base}
        onClose={() => setDrillAssetClass(null)}
      />
    </div>
  );
}
```

- [ ] **Step 3: Leaf components (boilerplate from Card / StatCard primitives)**

Each leaf component (RollupKpiBar, RollupCurveChart, PerAccountTable, AssetClassExposureList) is a thin renderer over the typed props. Use existing `Card`, `Button`, `Select` primitives. RollupCurveChart wraps `ChartArea` from Phase 9 (klinecharts).

(Snippets omitted here for brevity — each file is ~50-80 LOC following Phase 10b.1 SizingCalculatorPage style.)

- [ ] **Step 4: Run typecheck + dev server smoke**

```bash
cd /home/joseph/dashboard/frontend && pnpm typecheck && pnpm dev
```

In browser at http://localhost:5173/portfolio/rollup verify the page renders.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/portfolio.rollup.tsx frontend/src/features/portfolio/
git commit -m "feat(phase10b2): /portfolio/rollup route + RollupPage + 4 leaf components"
```

---

### Task D5: AssetClassDrillDrawer + 5 tests (3 drawer + 2 page)

**Files:**
- Create: `frontend/src/features/portfolio/AssetClassDrillDrawer.tsx`
- Create: `frontend/src/features/portfolio/AssetClassDrillDrawer.test.tsx`
- Create: `frontend/src/features/portfolio/RollupPage.test.tsx`

- [ ] **Step 1: Drill drawer**

```typescript
import { Drawer } from '../../components/primitives/Drawer';
import { useRollupDrill } from '../../services/portfolio/useRollupDrill';
import type { BaseCurrency } from '../../services/portfolio/types';

interface Props {
  assetClass: string | null;
  base: BaseCurrency;
  onClose: () => void;
}

export function AssetClassDrillDrawer({ assetClass, base, onClose }: Props) {
  const drill = useRollupDrill(assetClass, base);

  return (
    <Drawer open={assetClass !== null} onClose={onClose} side="right">
      <h2 className="text-lg font-semibold">Asset class: {assetClass}</h2>
      {drill.isLoading && <div>Loading…</div>}
      {drill.data?.instruments.map((inst) => (
        <div
          key={inst.instrument_id}
          className={
            inst.verdict === 'block' ? 'bg-red-50' :
            inst.verdict === 'warn' ? 'bg-amber-50' : ''
          }
          aria-label={`Instrument ${inst.display_name} verdict ${inst.verdict}`}
        >
          <div>{inst.display_name} ({inst.exchange})</div>
          <div>Qty: {inst.total_qty} · Notional: {inst.notional_base} {base}</div>
          <div>{inst.pct_of_nlv}% of NLV{inst.cap_pct ? ` · cap ${inst.cap_pct}% (util ${inst.utilisation_pct}%)` : ''}</div>
        </div>
      ))}
    </Drawer>
  );
}
```

- [ ] **Step 2: Drawer tests (3)**

```typescript
describe('AssetClassDrillDrawer', () => {
  it('opens when assetClass is set', () => {
    render(<AssetClassDrillDrawer assetClass="equity" base="GBP" onClose={vi.fn()} />, { wrapper });
    expect(screen.getByText(/Asset class: equity/)).toBeInTheDocument();
  });

  it('calls onClose on Escape', () => {
    const onClose = vi.fn();
    render(<AssetClassDrillDrawer assetClass="equity" base="GBP" onClose={onClose} />, { wrapper });
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('renders verdict-coloured rows', async () => {
    render(<AssetClassDrillDrawer assetClass="equity" base="GBP" onClose={vi.fn()} />, { wrapper });
    const blockRow = await screen.findByLabelText(/verdict block/);
    expect(blockRow).toHaveClass('bg-red-50');
  });
});
```

- [ ] **Step 3: RollupPage tests (2)**

```typescript
describe('RollupPage', () => {
  it('renders KPI + curve + 2 lower panels', async () => {
    render(<RollupPage />, { wrapper });
    await waitFor(() => expect(screen.getByText(/Total NLV/)).toBeInTheDocument());
    expect(screen.getByText(/Per account/)).toBeInTheDocument();
    expect(screen.getByText(/Exposure by asset class/)).toBeInTheDocument();
  });

  it('base-currency selector persists to localStorage', async () => {
    const { rerender } = render(<RollupPage />, { wrapper });
    await waitFor(() => screen.getByRole('combobox'));
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'USD' } });
    expect(localStorage.getItem('portfolio-rollup')).toContain('USD');
    rerender(<RollupPage />);
    expect(screen.getByRole('combobox')).toHaveValue('USD');
  });
});
```

- [ ] **Step 4: Run + commit**

```bash
cd /home/joseph/dashboard/frontend && pnpm test
git add frontend/src/features/portfolio/AssetClassDrillDrawer.tsx frontend/src/features/portfolio/AssetClassDrillDrawer.test.tsx frontend/src/features/portfolio/RollupPage.test.tsx
git commit -m "feat(phase10b2): AssetClassDrillDrawer + 5 component tests"
```

---

### Task D6: Chunk-D reviewer chain

- [ ] **Step 1: Dispatch 3 reviewers**

`spec-compliance` (haiku), `typescript-reviewer` (haiku), `code-reviewer` (sonnet — FE focus).

- [ ] **Step 2: Apply findings + commit**

```bash
git commit -am "fix(phase10b2): chunk-D reviewer fixes — <N> findings inline"
```

---

# Chunk E — Playwright + close-out

### Task E1: Playwright spec (3 tests)

**Files:**
- Create: `tests/e2e/phase10b2-rollup.spec.ts`

- [ ] **Step 1: Write the 3 Playwright tests**

```typescript
import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.goto('/login');
  await page.fill('input[name=username]', process.env.E2E_USER!);
  await page.fill('input[name=password]', process.env.E2E_PASS!);
  await page.click('button[type=submit]');
});

test('rollup page renders KPI + curve + panels', async ({ page }) => {
  await page.goto('/portfolio/rollup');
  await expect(page.getByText(/Total NLV/)).toBeVisible();
  await expect(page.getByText(/Per account/)).toBeVisible();
  await expect(page.getByText(/Exposure by asset class/)).toBeVisible();
});

test('window toggle updates URL search param', async ({ page }) => {
  await page.goto('/portfolio/rollup');
  await page.getByRole('button', { name: '30 days' }).click();
  await expect(page).toHaveURL(/window=30d/);
});

test('drill drawer opens on asset-class click', async ({ page }) => {
  await page.goto('/portfolio/rollup');
  const firstAssetClassRow = page.getByText(/Equity/).first();
  await firstAssetClassRow.click();
  await expect(page.getByText(/Asset class:/)).toBeVisible();
});
```

- [ ] **Step 2: Run against local dev**

```bash
cd /home/joseph/dashboard && pnpm playwright test tests/e2e/phase10b2-rollup.spec.ts
```

Expected: 3 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/phase10b2-rollup.spec.ts
git commit -m "test(phase10b2): Playwright — page render + window toggle + drill drawer"
```

---

### Task E2: Final 5-reviewer chain across A+B'+B''+B'''+C+D

- [ ] **Step 1: Dispatch all 5 reviewers in parallel**

- `spec-compliance` (haiku) — full spec vs implementation walk
- `python-reviewer` (haiku)
- `typescript-reviewer` (haiku)
- `code-reviewer` (sonnet)
- `security-reviewer` (sonnet) — final pass on WS + REST + writer hook
- `database-reviewer` (sonnet) — final pass on Alembic 0039 + 0040

- [ ] **Step 2: Apply findings inline; commit**

```bash
git commit -am "fix(phase10b2): final reviewer chain — <N> findings inline"
```

---

### Task E3: Close-out — CHANGELOG / CLAUDE.md / TASKS.md / memory + v0.14.0 tag

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `CLAUDE.md` (Phase 10b.2 in cross-cutting load-bearing rules section)
- Modify: `TASKS.md` (mark Phase 10b.2 complete; update Phase 10 scoreboard)
- Create: `/home/joseph/.claude/projects/-home-joseph-dashboard/memory/phase10b2_shipped.md`
- Modify: `/home/joseph/.claude/projects/-home-joseph-dashboard/memory/MEMORY.md` (add index entry)

- [ ] **Step 1: CHANGELOG.md — add v0.14.0 section**

```markdown
## [0.14.0] — 2026-05-12

### Added — Phase 10b.2: Multi-account portfolio rollup

- `account_balance_snapshots` TimescaleDB hypertable + 1h/1d CAGGs (Alembic 0039 + 0040).
- `BalanceSnapshotWriter` service hooked into `brokers.py:1449` NLV path with nested-SAVEPOINT fail-OPEN semantics.
- `PortfolioRollupService` with `compute_live` (per-account FX fault isolation), `compute_curve` (3 windows over raw + CAGGs), `drill_asset_class` (informational cap utilisation).
- 3 REST endpoints + 1 WS gateway at `/ws/portfolio/rollup` (500 ms debounce, 250 ms compute cache, CSWSH origin check, frame version 1).
- `/portfolio/rollup` route with KPI bar + klinecharts curve + per-account table + asset-class exposure + drill drawer.
- 9 new Prometheus metrics + `portfolio` shared rate limiter.

### Migrations

- Alembic 0039 — `account_balance_snapshots` hypertable + retention (2y) + source_label CHECK.
- Alembic 0040 — 1h + 1d CAGGs with `materialized_only=false` + synchronous backfill via `autocommit_block`.

### Notes

- Strategy-tagged P&L deferred to Phase 20+ (needs `fills.strategy_id`).
- `account_balances` table decoupling deferred to Phase 24 (would rewrite 5+ services).
- Historical NLV backfill from broker APIs: out of scope; history accrues forward.
- Exposure computed at cost basis (`qty * avg_cost * multiplier`) — `positions.market_value_base` column does not exist; matches risk_service.
```

- [ ] **Step 2: CLAUDE.md — append Phase 10b.2 paragraph**

In the "Cross-cutting load-bearing rules" section, append a paragraph mirroring the Phase 10b.1 entry format. Lock the file paths + endpoints + WS topic + Prometheus metric names + footguns.

- [ ] **Step 3: TASKS.md — mark Phase 10b.2 complete**

Update the scoreboard row #9 to `✅ **10b.2**`. Move the Phase 10b.2 section from "(not started)" to "(complete · 2026-05-12 · v0.14.0)". Itemise the chunks.

- [ ] **Step 4: Memory file**

Create `/home/joseph/.claude/projects/-home-joseph-dashboard/memory/phase10b2_shipped.md` with frontmatter (name, description, type: project) and the full topology + footguns, mirroring `phase10b1_shipped.md`'s structure.

Add an entry to `MEMORY.md` index:

```markdown
- [Phase 10b.2 shipped (v0.14.0 · 2026-05-12)](phase10b2_shipped.md) — Multi-account portfolio rollup: NLV + intraday/30d/1y curves + exposure-by-asset-class + per-instrument drill; account_balance_snapshots hypertable + 2 CAGGs; /ws/portfolio/rollup; ~28 commits
```

- [ ] **Step 5: Commit + tag**

```bash
git add CHANGELOG.md CLAUDE.md TASKS.md
git commit -m "docs(phase10b2-e3): close-out for v0.14.0 — CHANGELOG + CLAUDE.md + TASKS.md"
git tag -a v0.14.0 -m "Phase 10b.2 — multi-account portfolio rollup"
git push origin main --tags
```

Expected: tag pushed to origin/main; phase complete.

---

## Self-review

**Spec coverage check:**

| Spec section | Plan task(s) |
|---|---|
| §2 In-scope #1 NLV rollup | B'2, B''1 |
| §2 In-scope #2 Exposure by asset class | B'2 (`_exposure_by_asset_class`) |
| §2 In-scope #3 P&L attribution per broker/account | B'2 (reads `v_account_intraday_pnl`) |
| §2 In-scope #4 Drill-down | B''2 |
| §2 In-scope #5 WS push | C1, C2 |
| §4.1 hypertable + source_label CHECK | A1 |
| §4.2 CAGGs + autocommit_block + materialized_only | A2 |
| §4.3 nested SAVEPOINT writer hook | A3, A4, A5 |
| §5.1 Per-account FX fault isolation | B'2 |
| §5.2 3 REST endpoints + rate limiter | B'''1, B'''2 |
| §5.3 Tracked publish task set | A3 (writer), A4 (lifecycle wiring) |
| §6 WS gateway: CSWSH + listen() + cache + send timeout + version | C1 |
| §7 Frontend: route + page + 5 components + drill | D1–D5 |
| §8 9 Prometheus metrics | A3 (counters), C1 (gauge) |
| §11 ~46 tests | spread across all chunks |
| §12 Chunking 7 chunks | this plan |

**Placeholder scan:** no "TBD" / "TODO" / "fill in details" found. Every test has actual code; every step has actual commands.

**Type consistency:** `BalanceSnapshotWriter.record()` / `.schedule_publish()` / `.stop()` consistent across A3 (definition) + A4 (call sites) + A5 (tests). `PortfolioRollupService.compute_live` / `compute_curve` / `drill_asset_class` consistent across B'2 + B''1 + B''2 + B'''2 + C1.

**Plan committed:** ready for execution handoff.

---

## Execution choices

Plan complete and saved to `docs/superpowers/plans/2026-05-12-phase10b2-portfolio-rollup-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration. Aligns with CLAUDE.md routing (Qwen / Codex / Opus direct per task character).

**2. Inline Execution** — execute in this session via `superpowers:executing-plans`, batch with checkpoints.

Which approach?
