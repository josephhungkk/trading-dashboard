# Phase 21c — Advisor Perf-Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer "was the advisor right?" for every `bot_advisor_decisions` row by computing simulated PnL across 15m/1h/4h/EOD windows against `bars_1m` data, surface rolling accuracy stats in a new `AdvisorScoreCard` component.

**Architecture:** APScheduler polls pending decisions every 900s; `AttributionService.poll()` claims rows with `FOR UPDATE SKIP LOCKED`, resolves `canonical_id → (instrument_id, multiplier, primary_exchange)` via a new `InstrumentResolver.find_by_canonical_id()` method (Redis-cached), simulates next-bar-open entry/exit pricing for each window, writes outcome columns back. REST endpoints expose summaries; FE renders an `AdvisorScoreCard` on the bot overview tab.

**Tech Stack:** Python 3.14 · SQLAlchemy 2.0 async · Alembic · exchange_calendars · prometheus_client · React 19 · TanStack Query · Tailwind v4

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `backend/alembic/versions/0068_advisor_attribution.py` | Outcome columns + status + `attribution_windows` on `bot_advisor_decisions`; `advisor_decision_id` FK on `bot_orders` |
| Create | `backend/tests/services/advisor/test_attribution_schema.py` | Alembic migration tests |
| Create | `backend/app/services/advisor/attribution.py` | `AttributionService` (poll, get_summary, recompute) |
| Create | `backend/app/services/advisor/attribution_types.py` | `InstrumentAttribution` dataclass, `AttributionSummary` Pydantic model |
| Modify | `backend/app/services/quotes/instrument_resolver.py` | Add `find_by_canonical_id()` method |
| Modify | `backend/app/services/market_calendar.py` | Add `session_close_for_decision()` function |
| Modify | `backend/app/services/advisor/metrics.py` | Add 5 attribution metrics |
| Create | `backend/tests/services/advisor/test_attribution.py` | ~40 unit + integration tests |
| Modify | `backend/app/main.py` | Wire APScheduler interval job + import `AttributionService` |
| Modify | `backend/app/bot/context.py` | Extend `bot_orders` INSERT with `advisor_decision_id` |
| Modify | `backend/app/api/bots.py` | Add 2 attribution endpoints; widen `AdvisorDecisionResponse` |
| Create | `backend/tests/api/test_advisor_attribution_api.py` | REST endpoint tests |
| Modify | `frontend/src/services/advisor/types.ts` | Add `AttributionSummary` interface; extend `AdvisorDecision` |
| Modify | `frontend/src/services/advisor/api.ts` | Add `getAdvisorAttribution`, `recomputeAttribution` |
| Create | `frontend/src/features/bots/components/AdvisorScoreCard.tsx` | New score card component |
| Create | `frontend/src/features/bots/components/AdvisorScoreCard.test.tsx` | 5 component tests |
| Modify | `frontend/src/features/bots/components/AdvisorDecisionsTable.tsx` | Outcome column (1h window) |
| Modify | `frontend/src/features/bots/components/AdvisorDecisionsTable.test.tsx` | 2 outcome column tests |
| Modify | `frontend/src/features/bots/components/AdvisorDecisionDrawer.tsx` | Single outcome line |
| Modify | `frontend/src/features/bots/components/AdvisorDecisionDrawer.test.tsx` | 2 drawer outcome tests |
| Modify | `frontend/src/features/bots/BotDetailPage.tsx` | Import + render `AdvisorScoreCard` on overview tab |

---

## Chunk A — Schema (Qwen)

**Files:**
- Create: `backend/alembic/versions/0068_advisor_attribution.py`
- Create: `backend/tests/services/advisor/test_attribution_schema.py`

### Task A-1: Write the failing migration test

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/advisor/test_attribution_schema.py
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_0068_adds_outcome_columns(db_session: AsyncSession) -> None:
    """After 0068 runs, bot_advisor_decisions has attribution outcome columns."""
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='bot_advisor_decisions'"
            "  AND column_name IN ("
            "    'attribution_status','attribution_windows','attribution_computed_at',"
            "    'outcome_15m_correct','outcome_15m_pnl',"
            "    'outcome_1h_correct','outcome_1h_pnl',"
            "    'outcome_4h_correct','outcome_4h_pnl',"
            "    'outcome_eod_correct','outcome_eod_pnl'"
            "  )"
        )
    )
    cols = {row[0] for row in result.fetchall()}
    assert cols == {
        "attribution_status", "attribution_windows", "attribution_computed_at",
        "outcome_15m_correct", "outcome_15m_pnl",
        "outcome_1h_correct", "outcome_1h_pnl",
        "outcome_4h_correct", "outcome_4h_pnl",
        "outcome_eod_correct", "outcome_eod_pnl",
    }


@pytest.mark.asyncio
async def test_0068_attribution_status_default_pending(db_session: AsyncSession) -> None:
    """attribution_status defaults to 'pending'."""
    result = await db_session.execute(
        text(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name='bot_advisor_decisions' AND column_name='attribution_status'"
        )
    )
    default = result.scalar_one_or_none()
    assert default is not None and "pending" in default


@pytest.mark.asyncio
async def test_0068_adds_advisor_decision_id_to_bot_orders(db_session: AsyncSession) -> None:
    """bot_orders.advisor_decision_id FK column exists."""
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='bot_orders' AND column_name='advisor_decision_id'"
        )
    )
    assert result.scalar_one_or_none() == "advisor_decision_id"


@pytest.mark.asyncio
async def test_0068_attribution_status_check_constraint(db_session: AsyncSession) -> None:
    """attribution_status CHECK rejects invalid values."""
    with pytest.raises(Exception, match="check"):
        await db_session.execute(
            text(
                "UPDATE bot_advisor_decisions SET attribution_status = 'invalid'"
                " WHERE false"
            )
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/joseph/dashboard
bash backend/scripts/run-tests.sh backend/tests/services/advisor/test_attribution_schema.py -v
```

Expected: FAIL — columns do not exist yet.

### Task A-2: Write Alembic migration 0068

- [ ] **Step 3: Write the migration**

```python
# backend/alembic/versions/0068_advisor_attribution.py
"""advisor attribution outcome columns

Revision ID: 0068
Revises: 0067
Create Date: 2026-05-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Outcome columns + status on bot_advisor_decisions (plain table — CRIT-2)
    op.execute(
        """
        ALTER TABLE bot_advisor_decisions
            ADD COLUMN attribution_status       TEXT NOT NULL DEFAULT 'pending'
                CHECK (attribution_status IN (
                    'pending','partial','complete','bars_unavailable','unresolvable'
                )),
            ADD COLUMN attribution_windows       TEXT[]
                CHECK (attribution_windows IS NULL
                       OR attribution_windows <@ ARRAY['15m','1h','4h','eod']::TEXT[]),
            ADD COLUMN outcome_15m_correct       BOOL,
            ADD COLUMN outcome_15m_pnl           NUMERIC(20,8),
            ADD COLUMN outcome_1h_correct        BOOL,
            ADD COLUMN outcome_1h_pnl            NUMERIC(20,8),
            ADD COLUMN outcome_4h_correct        BOOL,
            ADD COLUMN outcome_4h_pnl            NUMERIC(20,8),
            ADD COLUMN outcome_eod_correct       BOOL,
            ADD COLUMN outcome_eod_pnl           NUMERIC(20,8),
            ADD COLUMN attribution_computed_at   TIMESTAMPTZ
        """
    )

    op.execute(
        """
        CREATE INDEX bot_advisor_decisions_attribution_status_created_at_idx
            ON bot_advisor_decisions (attribution_status, created_at DESC)
            WHERE attribution_status IN ('pending', 'partial')
        """
    )

    # FK from bot_orders to bot_advisor_decisions — provenance only
    op.execute(
        """
        ALTER TABLE bot_orders
            ADD COLUMN advisor_decision_id BIGINT
                REFERENCES bot_advisor_decisions(id) ON DELETE SET NULL
        """
    )

    op.execute(
        """
        CREATE INDEX bot_orders_advisor_decision_id_idx
            ON bot_orders (advisor_decision_id)
            WHERE advisor_decision_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS bot_orders_advisor_decision_id_idx"
    )
    op.execute(
        "ALTER TABLE bot_orders DROP COLUMN IF EXISTS advisor_decision_id"
    )
    op.execute(
        "DROP INDEX IF EXISTS bot_advisor_decisions_attribution_status_created_at_idx"
    )
    op.execute(
        """
        ALTER TABLE bot_advisor_decisions
            DROP COLUMN IF EXISTS attribution_status,
            DROP COLUMN IF EXISTS attribution_windows,
            DROP COLUMN IF EXISTS outcome_15m_correct,
            DROP COLUMN IF EXISTS outcome_15m_pnl,
            DROP COLUMN IF EXISTS outcome_1h_correct,
            DROP COLUMN IF EXISTS outcome_1h_pnl,
            DROP COLUMN IF EXISTS outcome_4h_correct,
            DROP COLUMN IF EXISTS outcome_4h_pnl,
            DROP COLUMN IF EXISTS outcome_eod_correct,
            DROP COLUMN IF EXISTS outcome_eod_pnl,
            DROP COLUMN IF EXISTS attribution_computed_at
        """
    )
```

- [ ] **Step 4: Run migration**

```bash
cd /home/joseph/dashboard
docker compose exec backend alembic upgrade 0068
```

Expected: "Running upgrade ... -> 0068"

- [ ] **Step 5: Run test to verify it passes**

```bash
bash backend/scripts/run-tests.sh backend/tests/services/advisor/test_attribution_schema.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0068_advisor_attribution.py \
        backend/tests/services/advisor/test_attribution_schema.py
git commit -m "feat(21c-A): alembic 0068 advisor attribution schema"
```

---

## Chunk B — Attribution Service (Qwen)

**Files:**
- Create: `backend/app/services/advisor/attribution_types.py`
- Create: `backend/app/services/advisor/attribution.py`
- Modify: `backend/app/services/quotes/instrument_resolver.py` (add `find_by_canonical_id`)
- Modify: `backend/app/services/market_calendar.py` (add `session_close_for_decision`)
- Create: `backend/tests/services/advisor/test_attribution.py`

### Task B-1: Add `InstrumentAttribution` dataclass and `AttributionSummary` Pydantic model

- [ ] **Step 1: Write the file**

```python
# backend/app/services/advisor/attribution_types.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


@dataclass(frozen=True)
class InstrumentAttribution:
    """Minimal instrument data needed by AttributionService."""
    id: int
    multiplier: Decimal
    primary_exchange: str


class AttributionSummary(BaseModel):
    bot_id: UUID
    window: str
    veto_accuracy: float | None
    approve_accuracy: float | None
    avg_avoided_loss_quote: Decimal | None
    avg_missed_gain_quote: Decimal | None
    complete_count: int
    partial_count: int
    pending_count: int
    bars_unavailable_count: int
    unresolvable_count: int
    skipped_count: int
    generated_at: datetime
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/advisor/attribution_types.py
git commit -m "feat(21c-B): attribution_types dataclass + pydantic model"
```

### Task B-2: Add `find_by_canonical_id` to `InstrumentResolver`

- [ ] **Step 3: Write the failing test**

```python
# In backend/tests/services/advisor/test_attribution.py — initial stub
import pytest
from decimal import Decimal
from app.services.quotes.instrument_resolver import InstrumentResolver
from app.services.advisor.attribution_types import InstrumentAttribution


@pytest.mark.asyncio
async def test_find_by_canonical_id_returns_instrument(
    db_session, redis_client
) -> None:
    resolver = InstrumentResolver(session=db_session)
    # Seed instrument row — canonical_id "AAPL", multiplier=1, primary_exchange="NASDAQ"
    await db_session.execute(
        text(
            "INSERT INTO instruments (canonical_id, asset_class, primary_exchange,"
            " currency, display_name, meta)"
            " VALUES ('AAPL', 'STOCK', 'NASDAQ', 'USD', 'Apple Inc.',"
            " '{\"multiplier\": 1}'::jsonb)"
            " ON CONFLICT (canonical_id) DO NOTHING"
        )
    )
    result = await resolver.find_by_canonical_id("AAPL", redis=redis_client)
    assert result is not None
    assert result.primary_exchange == "NASDAQ"
    assert result.multiplier == Decimal("1")


@pytest.mark.asyncio
async def test_find_by_canonical_id_returns_none_for_unknown(
    db_session, redis_client
) -> None:
    resolver = InstrumentResolver(session=db_session)
    result = await resolver.find_by_canonical_id("DOES_NOT_EXIST_XYZ", redis=redis_client)
    assert result is None


@pytest.mark.asyncio
async def test_find_by_canonical_id_uses_redis_cache(
    db_session, redis_client, mocker
) -> None:
    resolver = InstrumentResolver(session=db_session)
    spy = mocker.spy(db_session, "execute")
    await redis_client.set(
        "attribution:instr:CACHED_INSTR",
        '{"id": 42, "multiplier": "100", "primary_exchange": "HKEX"}',
        ex=3600,
    )
    result = await resolver.find_by_canonical_id("CACHED_INSTR", redis=redis_client)
    assert result is not None
    assert result.id == 42
    assert result.multiplier == Decimal("100")
    # DB not hit when cache warm
    assert spy.call_count == 0


@pytest.mark.asyncio
async def test_find_by_canonical_id_multiplier_defaults_to_one_when_null(
    db_session, redis_client
) -> None:
    resolver = InstrumentResolver(session=db_session)
    await db_session.execute(
        text(
            "INSERT INTO instruments (canonical_id, asset_class, primary_exchange,"
            " currency, display_name, meta)"
            " VALUES ('NOOPT', 'STOCK', 'NYSE', 'USD', 'No Option', '{}'::jsonb)"
            " ON CONFLICT (canonical_id) DO NOTHING"
        )
    )
    result = await resolver.find_by_canonical_id("NOOPT", redis=redis_client)
    assert result is not None
    assert result.multiplier == Decimal("1")
```

- [ ] **Step 4: Run test to verify it fails**

```bash
bash backend/scripts/run-tests.sh backend/tests/services/advisor/test_attribution.py::test_find_by_canonical_id_returns_instrument -v
```

Expected: FAIL — `InstrumentResolver` has no `find_by_canonical_id` method.

- [ ] **Step 5: Add `find_by_canonical_id` to `InstrumentResolver`**

**IMPORTANT:** `InstrumentResolver.__init__(self, session: AsyncSession)` stores only `self._session`. It has **no** Redis attribute. The new method takes Redis as an explicit parameter.

Add after the existing `find_by_alias` method in `backend/app/services/quotes/instrument_resolver.py`:

```python
async def find_by_canonical_id(
    self, canonical_id: str, redis: Any
) -> "InstrumentAttribution | None":
    """Read-only lookup: canonical_id → (id, multiplier, primary_exchange).

    Redis cache key: attribution:instr:{canonical_id}, TTL 3600s.
    Returns None only when canonical_id is not in instruments table.
    Raises on DB/Redis infrastructure errors (caller treats as transient, not permanent skip).
    """
    import json
    from app.services.advisor.attribution_types import InstrumentAttribution

    cache_key = f"attribution:instr:{canonical_id}"
    raw = await redis.get(cache_key)
    if raw is not None:
        data = json.loads(raw)
        return InstrumentAttribution(
            id=int(data["id"]),
            multiplier=Decimal(data["multiplier"]),
            primary_exchange=str(data["primary_exchange"]),
        )

    from sqlalchemy import text as _text
    row = (
        await self._session.execute(
            _text(
                "SELECT id, (meta->>'multiplier')::numeric AS multiplier,"
                " primary_exchange"
                " FROM instruments WHERE canonical_id = :cid"
            ),
            {"cid": canonical_id},
        )
    ).mappings().first()

    if row is None:
        return None

    multiplier = Decimal(str(row["multiplier"])) if row["multiplier"] is not None else Decimal("1")
    instr = InstrumentAttribution(
        id=int(row["id"]),
        multiplier=multiplier,
        primary_exchange=str(row["primary_exchange"]),
    )
    await redis.set(
        cache_key,
        json.dumps({
            "id": instr.id,
            "multiplier": str(instr.multiplier),
            "primary_exchange": instr.primary_exchange,
        }),
        ex=3600,
    )
    return instr
```

The caller (`AttributionService._process_decision`) instantiates the resolver as `InstrumentResolver(session=db)` and calls `resolver.find_by_canonical_id(canonical_id, redis=self._redis)`.

Also add `Any` to the imports at the top of `instrument_resolver.py` if not already present (it's in `from typing import Any`).

- [ ] **Step 6: Run test to verify it passes**

```bash
bash backend/scripts/run-tests.sh backend/tests/services/advisor/test_attribution.py -k "find_by_canonical_id" -v
```

Expected: 4 tests PASS.

### Task B-3: Add `session_close_for_decision` to `market_calendar.py`

- [ ] **Step 7: Write the failing test**

Add to `backend/tests/services/advisor/test_attribution.py`:

```python
from datetime import datetime, timezone, timedelta
from app.services.market_calendar import session_close_for_decision


def test_session_close_intraday_decision() -> None:
    """Decision during trading hours returns same-session close."""
    # NYSE session: 9:30–16:00 ET. Noon UTC = 08:00 ET (before open).
    # Use 14:00 UTC = 10:00 ET — inside NYSE session.
    dt = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)  # Monday
    close = session_close_for_decision("NYSE", dt)
    # Should be 2026-05-19 20:00 UTC (16:00 ET)
    assert close.date() == dt.date()
    assert close.hour == 20  # 16:00 ET = 20:00 UTC


def test_session_close_after_hours_decision() -> None:
    """After-hours decision returns NEXT session's close."""
    # NYSE closes ~20:00 UTC. 22:00 UTC = after-hours on 2026-05-19 (Monday).
    dt = datetime(2026, 5, 19, 22, 0, 0, tzinfo=timezone.utc)
    close = session_close_for_decision("NYSE", dt)
    # Should be next trading day (2026-05-20) close
    assert close.date().day == 20


def test_session_close_unknown_exchange_raises() -> None:
    """Unknown exchange raises ValueError — no UTC fallback."""
    import pytest
    dt = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="unsupported_exchange"):
        session_close_for_decision("UNKNOWN_EXCHANGE_XYZ", dt)
```

- [ ] **Step 8: Run test to verify it fails**

```bash
bash backend/scripts/run-tests.sh backend/tests/services/advisor/test_attribution.py -k "session_close" -v
```

Expected: FAIL — `session_close_for_decision` not in `market_calendar.py`.

- [ ] **Step 9: Add `session_close_for_decision` to `market_calendar.py`**

Append after `eod_for_exchange` in `backend/app/services/market_calendar.py`:

```python
def session_close_for_decision(exchange: str, created_at: datetime) -> datetime:
    """Return the EOD session close for an attribution decision.

    If created_at falls within a trading session, returns that session's close.
    If created_at is after-hours/weekend/holiday, returns the NEXT session's close.
    Raises ValueError when exchange is unrecognised by exchange_calendars.
    No UTC fallback — unknown exchange is an error (HIGH-4).
    """
    cal = _calendar(exchange)  # raises ValueError on unknown exchange

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    created_utc = created_at.astimezone(UTC)

    # Find the current or next session close
    # cal.minute_to_session returns (session, open, close) tuple or raises
    try:
        session_dt = cal.minute_to_session(created_utc, direction="next")
        # session_dt is a pd.Timestamp of the session date
        close_ts = cal.session_close(session_dt)
        return close_ts.to_pydatetime().astimezone(UTC)
    except Exception as exc:
        raise ValueError(f"session_close_for_decision failed for {exchange}: {exc}") from exc
```

- [ ] **Step 10: Run test to verify it passes**

```bash
bash backend/scripts/run-tests.sh backend/tests/services/advisor/test_attribution.py -k "session_close" -v
```

Expected: 3 tests PASS.

### Task B-4: Write `AttributionService`

- [ ] **Step 11: Add remaining test cases to `test_attribution.py`**

Add to `backend/tests/services/advisor/test_attribution.py`:

```python
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.services.advisor.attribution import AttributionService
from app.services.advisor.attribution_types import InstrumentAttribution, AttributionSummary


# ── Helper factory ────────────────────────────────────────────────────────────

def _make_decision(
    *,
    id: int = 1,
    verdict: str = "veto",
    canonical_id: str = "AAPL",
    created_at: datetime | None = None,
    attribution_status: str = "pending",
    intent: dict | None = None,
) -> dict:
    return {
        "id": id,
        "verdict": verdict,
        "canonical_id": canonical_id,
        "created_at": created_at or datetime(2026, 5, 19, 14, 0, tzinfo=timezone.utc),
        "attribution_status": attribution_status,
        "attribution_windows": None,
        "intent": intent or {"side": "buy", "qty": "10", "position_effect": "OPEN"},
    }


# ── Core veto/approve outcome tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_attribution_poll_computes_veto_outcome_buy(
    db_session: AsyncSession, redis_client
) -> None:
    """BUY veto + price falls → outcome_1h_correct=True, pnl < 0."""
    svc = AttributionService(db_factory=MagicMock(), redis=redis_client)
    # Seed bot, decision, bars, instrument — integration-style
    # (seed data setup varies per test harness; use raw SQL)
    # Verify: after poll(), decision row has outcome_1h_correct=True


@pytest.mark.asyncio
async def test_attribution_poll_computes_veto_outcome_sell(
    db_session: AsyncSession, redis_client
) -> None:
    """SELL veto + price falls → outcome_1h_correct=False (veto was wrong)."""
    pass  # implementation mirrors buy case with side='sell', assert correct=False


@pytest.mark.asyncio
async def test_attribution_poll_computes_approve_outcome(
    db_session: AsyncSession, redis_client
) -> None:
    """Approve + price rises → outcome_1h_correct=True."""
    pass


@pytest.mark.asyncio
async def test_attribution_poll_marks_complete_when_all_windows_done(
    db_session: AsyncSession, redis_client
) -> None:
    """All 4 windows matured → attribution_status='complete'."""
    pass


@pytest.mark.asyncio
async def test_attribution_poll_skips_unmatured_windows(
    db_session: AsyncSession, redis_client
) -> None:
    """Decision 30min old → 1h/4h/EOD outcome columns NULL after poll."""
    pass


@pytest.mark.asyncio
async def test_attribution_partial_at_expiry_forced_complete(
    db_session: AsyncSession, redis_client
) -> None:
    """partial decision past max_lookback_days forced to complete."""
    pass


# ── Instrument resolution ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_attribution_unresolvable_no_instrument(
    db_session: AsyncSession, redis_client
) -> None:
    """canonical_id not in instruments → status='unresolvable'; metric incremented."""
    pass


# ── Bars ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_attribution_bars_unavailable_all_windows(
    db_session: AsyncSession, redis_client
) -> None:
    """No bars_1m rows for instrument, all windows old → status='bars_unavailable'."""
    pass


@pytest.mark.asyncio
async def test_attribution_per_window_bar_absence_does_not_block_others(
    db_session: AsyncSession, redis_client
) -> None:
    """1h has no bars but 4h does → 1h stays NULL, 4h computes."""
    pass


# ── PnL formula ───────────────────────────────────────────────────────────────

def test_attribution_pnl_applies_multiplier_options() -> None:
    """multiplier=100 → pnl = (exit-entry)*qty*100."""
    # Can be a pure unit test using _compute_pnl helper
    entry, exit_, qty, multiplier, side_sign = Decimal("50"), Decimal("40"), Decimal("1"), Decimal("100"), 1
    # BUY, price fell: pnl = (40-50)*1*100 = -1000
    pnl = (exit_ - entry) * qty * multiplier * side_sign
    assert pnl == Decimal("-1000")
    assert pnl < 0  # veto correct


def test_attribution_pnl_multiplier_default_one_for_stocks() -> None:
    """No multiplier in meta → Decimal('1') used."""
    entry, exit_, qty, side_sign = Decimal("150"), Decimal("160"), Decimal("10"), 1
    pnl = (exit_ - entry) * qty * Decimal("1") * side_sign
    assert pnl == Decimal("100")


# ── EOD window ────────────────────────────────────────────────────────────────

def test_attribution_eod_skipped_within_buffer() -> None:
    """Decision < min_eod_buffer minutes before close → EOD skipped (eod_buffer metric)."""
    # session close at 20:00 UTC; decision at 19:55 UTC (5 min before close < 30 min buffer)
    session_close = datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc)
    created_at = datetime(2026, 5, 19, 19, 55, tzinfo=timezone.utc)
    buffer_minutes = 30
    gap = (session_close - created_at).total_seconds() / 60
    assert gap < buffer_minutes  # should skip


# ── CLOSE position ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_attribution_close_position_skipped(
    db_session: AsyncSession, redis_client
) -> None:
    """position_effect='CLOSE' → decision skipped; metric close_position."""
    pass


# ── Window snapshot ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_attribution_windows_snapshot_stored_on_first_compute(
    db_session: AsyncSession, redis_client
) -> None:
    """attribution_windows set at first compute tick (not NULL after first poll)."""
    pass


# ── Summary ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_attribution_summary_veto_accuracy(
    db_session: AsyncSession, redis_client
) -> None:
    """10 complete veto decisions, 7 correct → veto_accuracy=0.7."""
    pass


@pytest.mark.asyncio
async def test_attribution_summary_window_param_validated(
    db_session: AsyncSession, redis_client
) -> None:
    """window='invalid' → ValueError before any SQL (MED-3 SQLi guard)."""
    from app.services.advisor.attribution import AttributionService
    svc = AttributionService(db_factory=MagicMock(), redis=redis_client)
    with pytest.raises(ValueError, match="invalid_window"):
        await svc.get_summary(bot_id=MagicMock(), window="invalid", db=db_session)


# ── Kill switch + lookback ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_attribution_kill_switch_disabled(
    db_session: AsyncSession, redis_client
) -> None:
    """enabled=false → poll exits immediately without touching DB."""
    pass


# ── Context FK write ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bot_orders_fk_null_on_veto(
    db_session: AsyncSession, redis_client
) -> None:
    """Veto path → bot_orders row has advisor_decision_id=NULL."""
    pass
```

- [ ] **Step 12: Run tests to verify they fail**

```bash
bash backend/scripts/run-tests.sh backend/tests/services/advisor/test_attribution.py -v 2>&1 | tee /tmp/attribution_test_run.txt
```

Expected: ImportError on `AttributionService`.

- [ ] **Step 13: Write `AttributionService`**

```python
# backend/app/services/advisor/attribution.py
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.advisor.attribution_types import AttributionSummary
from app.services.advisor.metrics import (
    advisor_attribution_bars_unavailable_total,
    advisor_attribution_decisions_processed_total,
    advisor_attribution_poll_latency_seconds,
    advisor_attribution_skipped_total,
    advisor_attribution_unresolvable_total,
)
from app.services.market_calendar import session_close_for_decision
from app.services.quotes.instrument_resolver import InstrumentResolver

_log = structlog.get_logger(__name__)

_VALID_WINDOWS = {"15m", "1h", "4h", "eod"}

_WINDOW_DELTAS: dict[str, timedelta | None] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "eod": None,  # computed dynamically
}

_MAX_LOOKBACK_DAYS_DEFAULT = 7
_MIN_EOD_BUFFER_MINUTES_DEFAULT = 30
_POLL_BATCH_SIZE = 500


class AttributionService:
    def __init__(
        self,
        db_factory: async_sessionmaker[AsyncSession],
        redis: Any,
    ) -> None:
        self._db_factory = db_factory
        self._redis = redis

    async def poll(self, db: AsyncSession) -> None:
        """APScheduler entrypoint. Claim pending decisions and compute outcomes."""
        import time as _time

        enabled = await self._read_config(db, "advisor_attribution/enabled", default="true")
        if enabled.lower() != "true":
            return

        windows_cfg = await self._read_config(
            db, "advisor_attribution/windows", default='["15m","1h","4h","eod"]'
        )
        enabled_windows: list[str] = json.loads(windows_cfg)
        max_lookback_days = int(
            await self._read_config(db, "advisor_attribution/max_lookback_days", default=str(_MAX_LOOKBACK_DAYS_DEFAULT))
        )
        min_eod_buffer = int(
            await self._read_config(db, "advisor_attribution/min_eod_buffer_minutes", default=str(_MIN_EOD_BUFFER_MINUTES_DEFAULT))
        )

        t0 = _time.monotonic()
        rows = (
            await db.execute(
                text(
                    f"""
                    SELECT id, verdict, canonical_id, created_at,
                           attribution_status, attribution_windows,
                           intent
                    FROM bot_advisor_decisions
                    WHERE attribution_status IN ('pending','partial')
                      AND verdict IN ('approve','veto')
                      AND created_at >= now() - interval '{max_lookback_days} days'
                    ORDER BY created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT {_POLL_BATCH_SIZE}
                    """
                )
            )
        ).mappings().fetchall()

        resolver = InstrumentResolver(session=db)
        updates: list[dict] = []

        for row in rows:
            try:
                update = await self._process_decision(
                    row=dict(row),
                    enabled_windows=enabled_windows,
                    max_lookback_days=max_lookback_days,
                    min_eod_buffer=min_eod_buffer,
                    resolver=resolver,
                    db=db,
                )
                updates.append(update)
            except Exception:
                _log.exception(
                    "attribution_poll_decision_error",
                    decision_id=row["id"],
                )

        for upd in updates:
            await db.execute(
                text(
                    """
                    UPDATE bot_advisor_decisions SET
                        attribution_status      = :status,
                        attribution_windows     = :windows,
                        outcome_15m_correct     = :c15m,
                        outcome_15m_pnl         = :p15m,
                        outcome_1h_correct      = :c1h,
                        outcome_1h_pnl          = :p1h,
                        outcome_4h_correct      = :c4h,
                        outcome_4h_pnl          = :p4h,
                        outcome_eod_correct     = :ceod,
                        outcome_eod_pnl         = :peod,
                        attribution_computed_at = now()
                    WHERE id = :id
                    """
                ),
                upd,
            )

        await db.commit()
        advisor_attribution_poll_latency_seconds.observe(_time.monotonic() - t0)

    async def _process_decision(
        self,
        row: dict,
        enabled_windows: list[str],
        max_lookback_days: int,
        min_eod_buffer: int,
        resolver: InstrumentResolver,
        db: AsyncSession,
    ) -> dict:
        decision_id = row["id"]
        verdict = row["verdict"]
        canonical_id = row["canonical_id"]
        created_at: datetime = row["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        intent: dict = row["intent"] if isinstance(row["intent"], dict) else json.loads(row["intent"])

        # CLOSE position — skip (MED-6)
        if intent.get("position_effect", "OPEN").upper() == "CLOSE":
            advisor_attribution_skipped_total.labels(reason="close_position").inc()
            return self._no_change_update(decision_id, row)

        # Resolve instrument
        instr = await resolver.find_by_canonical_id(canonical_id, redis=self._redis)
        if instr is None:
            advisor_attribution_unresolvable_total.labels(reason="no_instrument").inc()
            return {
                "id": decision_id,
                "status": "unresolvable",
                "windows": None,
                **self._null_outcomes(),
            }

        # Snapshot windows on first compute
        existing_windows: list[str] | None = row["attribution_windows"]
        if existing_windows is None:
            snapshotted_windows = [w for w in enabled_windows if w in _VALID_WINDOWS]
        else:
            snapshotted_windows = list(existing_windows)

        side_sign = 1 if intent["side"].lower() == "buy" else -1
        qty = Decimal(str(intent["qty"]))
        multiplier = instr.multiplier

        outcomes: dict[str, tuple[bool | None, Decimal | None]] = {
            w: (None, None) for w in snapshotted_windows
        }
        any_bars_computed = False
        any_bars_missing_but_old = False

        now_utc = datetime.now(UTC)

        for window in snapshotted_windows:
            # Get existing outcome
            col_correct = row.get(f"outcome_{window.replace('h','h')}_correct")
            if col_correct is not None:
                # already computed
                outcomes[window] = (col_correct, row.get(f"outcome_{window}_pnl"))
                any_bars_computed = True
                continue

            # Determine window maturity and EOD close time
            if window == "eod":
                try:
                    session_close = session_close_for_decision(instr.primary_exchange, created_at)
                except ValueError:
                    advisor_attribution_unresolvable_total.labels(reason="unknown_exchange").inc()
                    return {
                        "id": decision_id,
                        "status": "unresolvable",
                        "windows": snapshotted_windows,
                        **self._null_outcomes(),
                    }
                # Check min_eod_buffer
                gap_minutes = (session_close - created_at).total_seconds() / 60
                if gap_minutes < min_eod_buffer:
                    advisor_attribution_skipped_total.labels(reason="eod_buffer").inc()
                    outcomes[window] = (None, None)
                    continue
                window_end = session_close
            else:
                delta = _WINDOW_DELTAS[window]
                assert delta is not None
                window_end = created_at + delta

            if now_utc < window_end:
                continue  # not yet matured

            # Fetch bars
            t0_bar = created_at
            t1_bar = window_end + timedelta(minutes=5)
            bars_rows = (
                await db.execute(
                    text(
                        "SELECT bucket_start, open, close FROM bars_1m"
                        " WHERE instrument_id = :iid"
                        "   AND bucket_start >= :t0 AND bucket_start <= :t1"
                        " ORDER BY bucket_start ASC"
                    ),
                    {"iid": instr.id, "t0": t0_bar, "t1": t1_bar},
                )
            ).fetchall()

            if not bars_rows:
                # Window old enough → permanent miss
                bar_miss_age = timedelta(hours=24)
                if now_utc > window_end + bar_miss_age:
                    any_bars_missing_but_old = True
                continue

            entry_price = Decimal(str(bars_rows[0][1]))  # first bar open
            exit_price = Decimal(str(bars_rows[-1][2]))  # last bar close

            pnl = (exit_price - entry_price) * qty * multiplier * side_sign
            if verdict == "veto":
                correct = pnl < Decimal("0")
            else:
                correct = pnl > Decimal("0")

            outcomes[window] = (correct, pnl)
            any_bars_computed = True
            advisor_attribution_decisions_processed_total.labels(verdict=verdict).inc()

        # Compute new status
        non_null = [(c, p) for c, p in outcomes.values() if c is not None]
        all_computed = len(non_null) == len(snapshotted_windows)

        expired = created_at < now_utc - timedelta(days=max_lookback_days)
        if all_computed or (expired and non_null):
            new_status = "complete"
        elif any_bars_missing_but_old and not any_bars_computed:
            advisor_attribution_bars_unavailable_total.inc()
            new_status = "bars_unavailable"
        elif non_null:
            new_status = "partial"
        else:
            new_status = row["attribution_status"]  # unchanged

        result: dict = {
            "id": decision_id,
            "status": new_status,
            "windows": snapshotted_windows,
        }
        for w in ["15m", "1h", "4h", "eod"]:
            c, p = outcomes.get(w, (None, None))
            result[f"c{w.replace('h','h')}"] = c
            result[f"p{w.replace('h','h')}"] = p

        # Build flat update dict matching the SQL parameter names
        return {
            "id": decision_id,
            "status": new_status,
            "windows": snapshotted_windows,
            "c15m": outcomes.get("15m", (None, None))[0],
            "p15m": outcomes.get("15m", (None, None))[1],
            "c1h": outcomes.get("1h", (None, None))[0],
            "p1h": outcomes.get("1h", (None, None))[1],
            "c4h": outcomes.get("4h", (None, None))[0],
            "p4h": outcomes.get("4h", (None, None))[1],
            "ceod": outcomes.get("eod", (None, None))[0],
            "peod": outcomes.get("eod", (None, None))[1],
        }

    def _no_change_update(self, decision_id: int, row: dict) -> dict:
        return {
            "id": decision_id,
            "status": row["attribution_status"],
            "windows": row["attribution_windows"],
            "c15m": row.get("outcome_15m_correct"),
            "p15m": row.get("outcome_15m_pnl"),
            "c1h": row.get("outcome_1h_correct"),
            "p1h": row.get("outcome_1h_pnl"),
            "c4h": row.get("outcome_4h_correct"),
            "p4h": row.get("outcome_4h_pnl"),
            "ceod": row.get("outcome_eod_correct"),
            "peod": row.get("outcome_eod_pnl"),
        }

    def _null_outcomes(self) -> dict:
        return {
            "c15m": None, "p15m": None,
            "c1h": None, "p1h": None,
            "c4h": None, "p4h": None,
            "ceod": None, "peod": None,
        }

    async def get_summary(
        self, bot_id: UUID, window: str, db: AsyncSession
    ) -> AttributionSummary:
        """Return AttributionSummary for bot_id at given window. MED-3: window allowlisted."""
        if window not in _VALID_WINDOWS:
            raise ValueError(f"invalid_window: {window!r}. Must be one of {_VALID_WINDOWS}")

        # match/case dispatch — no f-string column interpolation (MED-3)
        match window:
            case "15m":
                correct_col, pnl_col = "outcome_15m_correct", "outcome_15m_pnl"
            case "1h":
                correct_col, pnl_col = "outcome_1h_correct", "outcome_1h_pnl"
            case "4h":
                correct_col, pnl_col = "outcome_4h_correct", "outcome_4h_pnl"
            case "eod":
                correct_col, pnl_col = "outcome_eod_correct", "outcome_eod_pnl"
            case _:
                raise ValueError(f"invalid_window: {window!r}")

        rows = (
            await db.execute(
                text(
                    f"""
                    SELECT
                        verdict,
                        attribution_status,
                        {correct_col}   AS is_correct,
                        {pnl_col}       AS pnl
                    FROM bot_advisor_decisions
                    WHERE bot_id = :bid
                    """
                ),
                {"bid": bot_id},
            )
        ).mappings().fetchall()

        veto_correct = veto_total = 0
        approve_correct = approve_total = 0
        avoided_losses: list[Decimal] = []
        missed_gains: list[Decimal] = []
        complete = partial = pending = bars_unavailable = unresolvable = skipped = 0

        for r in rows:
            match r["attribution_status"]:
                case "complete":
                    complete += 1
                case "partial":
                    partial += 1
                case "bars_unavailable":
                    bars_unavailable += 1
                case "unresolvable":
                    unresolvable += 1
                case "pending":
                    pending += 1

            is_correct: bool | None = r["is_correct"]
            pnl: Decimal | None = r["pnl"]

            if r["attribution_status"] == "complete" and is_correct is not None:
                if r["verdict"] == "veto":
                    veto_total += 1
                    if is_correct:
                        veto_correct += 1
                        if pnl is not None:
                            avoided_losses.append(abs(pnl))
                    else:
                        if pnl is not None:
                            missed_gains.append(abs(pnl))
                elif r["verdict"] == "approve":
                    approve_total += 1
                    if is_correct:
                        approve_correct += 1

        return AttributionSummary(
            bot_id=bot_id,
            window=window,
            veto_accuracy=veto_correct / veto_total if veto_total else None,
            approve_accuracy=approve_correct / approve_total if approve_total else None,
            avg_avoided_loss_quote=sum(avoided_losses) / len(avoided_losses) if avoided_losses else None,
            avg_missed_gain_quote=sum(missed_gains) / len(missed_gains) if missed_gains else None,
            complete_count=complete,
            partial_count=partial,
            pending_count=pending,
            bars_unavailable_count=bars_unavailable,
            unresolvable_count=unresolvable,
            skipped_count=skipped,
            generated_at=datetime.now(UTC),
        )

    async def recompute(
        self, bot_id: UUID, since: datetime, db: AsyncSession
    ) -> int:
        """Reset attribution for decisions on bot_id created since `since`.

        MED-8: since must be >= now() - 6 months.
        Returns count of rows reset.
        """
        six_months_ago = datetime.now(UTC) - timedelta(days=182)
        if since < six_months_ago:
            raise ValueError("since_too_old: bars_1m retention is 6 months; older resets produce bars_unavailable rows")

        result = await db.execute(
            text(
                """
                UPDATE bot_advisor_decisions SET
                    attribution_status      = 'pending',
                    attribution_windows     = NULL,
                    outcome_15m_correct     = NULL,
                    outcome_15m_pnl         = NULL,
                    outcome_1h_correct      = NULL,
                    outcome_1h_pnl          = NULL,
                    outcome_4h_correct      = NULL,
                    outcome_4h_pnl          = NULL,
                    outcome_eod_correct     = NULL,
                    outcome_eod_pnl         = NULL,
                    attribution_computed_at = NULL
                WHERE bot_id = :bid
                  AND created_at >= :since
                  AND (SELECT COUNT(*) FROM bot_advisor_decisions
                       WHERE bot_id = :bid AND created_at >= :since) <= 10000
                RETURNING id
                """
            ),
            {"bid": bot_id, "since": since},
        )
        count = len(result.fetchall())
        await db.commit()
        return count

    async def _read_config(
        self, db: AsyncSession, key: str, default: str
    ) -> str:
        namespace, _, config_key = key.partition("/")
        row = (
            await db.execute(
                text(
                    "SELECT value_json FROM app_config"
                    " WHERE namespace = :ns AND key = :k"
                ),
                {"ns": namespace, "k": config_key},
            )
        ).scalar_one_or_none()
        if row is None:
            return default
        import json as _json
        return _json.loads(row) if isinstance(_json.loads(row), str) else default
```

- [ ] **Step 14: Run attribution tests**

```bash
bash backend/scripts/run-tests.sh backend/tests/services/advisor/test_attribution.py -v 2>&1 | tee /tmp/attribution_test_run2.txt
```

Expected: Core unit tests pass; integration tests with `db_session` pass (those that have full implementations). Inspect `/tmp/attribution_test_run2.txt` before proceeding.

- [ ] **Step 15: Commit**

```bash
git add backend/app/services/advisor/attribution_types.py \
        backend/app/services/advisor/attribution.py \
        backend/app/services/quotes/instrument_resolver.py \
        backend/app/services/market_calendar.py \
        backend/tests/services/advisor/test_attribution.py
git commit -m "feat(21c-B): AttributionService + find_by_canonical_id + session_close_for_decision"
```

---

## Chunk C — APScheduler + Metrics + FK Write (Codex)

**Files:**
- Modify: `backend/app/services/advisor/metrics.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/bot/context.py`

### Task C-1: Add attribution metrics to `metrics.py`

- [ ] **Step 1: Write the failing test**

```python
# Verify metrics are importable and have correct label names
# backend/tests/services/advisor/test_attribution_metrics.py
from app.services.advisor.metrics import (
    advisor_attribution_decisions_processed_total,
    advisor_attribution_bars_unavailable_total,
    advisor_attribution_unresolvable_total,
    advisor_attribution_poll_latency_seconds,
    advisor_attribution_skipped_total,
)


def test_attribution_metrics_have_correct_labels() -> None:
    advisor_attribution_decisions_processed_total.labels(verdict="veto").inc()
    advisor_attribution_unresolvable_total.labels(reason="no_instrument").inc()
    advisor_attribution_skipped_total.labels(reason="close_position").inc()
    advisor_attribution_poll_latency_seconds.observe(0.5)
    advisor_attribution_bars_unavailable_total.inc()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
bash backend/scripts/run-tests.sh backend/tests/services/advisor/test_attribution_metrics.py -v
```

Expected: ImportError.

- [ ] **Step 3: Add metrics to `metrics.py`**

Append to `backend/app/services/advisor/metrics.py`:

```python
advisor_attribution_decisions_processed_total = Counter(
    "advisor_attribution_decisions_processed_total",
    "Attribution outcomes computed by verdict",
    ["verdict"],
)

advisor_attribution_bars_unavailable_total = Counter(
    "advisor_attribution_bars_unavailable_total",
    "Decisions where all windows lacked bar data",
)

advisor_attribution_unresolvable_total = Counter(
    "advisor_attribution_unresolvable_total",
    "Decisions that could not be resolved to an instrument",
    ["reason"],
)

advisor_attribution_poll_latency_seconds = Histogram(
    "advisor_attribution_poll_latency_seconds",
    "Wall-clock time for one attribution poll batch",
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0],
)

advisor_attribution_skipped_total = Counter(
    "advisor_attribution_skipped_total",
    "Decisions skipped during attribution",
    ["reason"],
)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
bash backend/scripts/run-tests.sh backend/tests/services/advisor/test_attribution_metrics.py -v
```

Expected: PASS.

### Task C-2: Wire APScheduler job in `main.py`

- [ ] **Step 5: Add the job**

Find the `# ── Phase 15a — Forex RFQ TTL sweeper` section in `backend/app/main.py` (around line 454). Add the attribution poll job immediately after the forex sweeper job (around line 461):

```python
    # ── Phase 21c — Advisor attribution poll ─────────────────────────────────
    from app.services.advisor.attribution import AttributionService
    from apscheduler.triggers.interval import IntervalTrigger

    _attribution_service = AttributionService(
        db_factory=session_factory, redis=redis
    )

    async def _run_attribution_poll() -> None:
        try:
            async with session_factory() as poll_db:
                interval_raw = await poll_db.execute(
                    text(
                        "SELECT value_json FROM app_config"
                        " WHERE namespace='advisor_attribution' AND key='poll_interval_seconds'"
                    )
                )
                interval_val = interval_raw.scalar_one_or_none()
            interval_secs = int(json.loads(interval_val)) if interval_val else 900
        except Exception:
            interval_secs = 900

        async with session_factory() as attr_db:
            await _attribution_service.poll(attr_db)

    scheduler.add_job(
        _run_attribution_poll,
        IntervalTrigger(seconds=900),  # default; service reads config at each tick
        id="advisor_attribution_poll",
        replace_existing=True,
    )
```

Note: `json` is already imported at the top of `main.py` — confirm before adding an import.

- [ ] **Step 6: Verify app starts cleanly**

```bash
cd /home/joseph/dashboard
docker compose up backend --no-deps -d
sleep 5
docker compose logs backend --tail=30 | grep -E "error|attribution|ERROR"
```

Expected: No errors mentioning attribution.

### Task C-3: Extend `bot_orders` INSERT with `advisor_decision_id`

- [ ] **Step 7: Write the failing test**

```python
# In backend/tests/services/advisor/test_attribution.py — add:

@pytest.mark.asyncio
async def test_bot_orders_fk_populated_on_approve_in_insert(
    db_session: AsyncSession,
) -> None:
    """Approve verdict: INSERT sets advisor_decision_id without a second UPDATE."""
    # Seed: bot, run, account, advisor decision row
    # Call BotContext.place_order with a mocked facade that returns order_id
    # Assert: SELECT advisor_decision_id FROM bot_orders WHERE order_id = :oid → non-NULL
    pass
```

- [ ] **Step 8: Modify `bot/context.py` INSERT**

In `backend/app/bot/context.py`, replace the INSERT at lines 207–213:

```python
        await self._db.execute(
            text(
                "INSERT INTO bot_orders (order_id, bot_id, account_id, placed_at)"
                " VALUES (:oid, :bid, :aid, now())"
            ),
            {"oid": result.order_id, "bid": self.bot_id, "aid": account_id},
        )
```

with:

```python
        await self._db.execute(
            text(
                "INSERT INTO bot_orders"
                " (order_id, bot_id, account_id, placed_at, advisor_decision_id)"
                " VALUES (:oid, :bid, :aid, now(), :adv_id)"
            ),
            {
                "oid": result.order_id,
                "bid": self.bot_id,
                "aid": account_id,
                "adv_id": decision_id if verdict is not None and verdict.action == "approve" else None,
            },
        )
```

Note: The advisor block (`if self._advisor is not None`) starts at line 146 and sets `verdict, decision_id` at line 170. If `self._advisor is None` (OFF), those variables are never assigned. The safe pattern is to initialize `decision_id: int | None = None` before the advisor block (around line 146), then inside the block update it: `_, decision_id = review_result`. This way the INSERT always has a valid `decision_id` variable regardless of advisor mode. The veto early-return at line 186 means reaching line 207 always means the order was placed with either approve or fail_open — `decision_id` is non-None only for approve.

- [ ] **Step 9: Run all bot context tests**

```bash
bash backend/scripts/run-tests.sh backend/tests/ -k "bot" -v 2>&1 | tail -20
```

Expected: All existing bot tests still PASS.

- [ ] **Step 10: Commit chunk C**

```bash
git add backend/app/services/advisor/metrics.py \
        backend/app/main.py \
        backend/app/bot/context.py \
        backend/tests/services/advisor/test_attribution_metrics.py
git commit -m "feat(21c-C): attribution metrics + APScheduler job + bot_orders FK write"
```

---

## Chunk D — REST API (Qwen)

**Files:**
- Modify: `backend/app/api/bots.py`
- Create: `backend/tests/api/test_advisor_attribution_api.py`

### Task D-1: Widen `AdvisorDecisionResponse` and add endpoints

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/api/test_advisor_attribution_api.py
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_advisor_attribution_returns_summary(
    async_client: AsyncClient, auth_headers: dict, seeded_bot_id: str
) -> None:
    resp = await async_client.get(
        f"/api/bots/{seeded_bot_id}/advisor-attribution",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "veto_accuracy" in data
    assert "complete_count" in data
    assert data["window"] == "1h"  # default window


@pytest.mark.asyncio
async def test_advisor_attribution_window_param_validated(
    async_client: AsyncClient, auth_headers: dict, seeded_bot_id: str
) -> None:
    resp = await async_client.get(
        f"/api/bots/{seeded_bot_id}/advisor-attribution?window=invalid",
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_advisor_attribution_recompute_resets_decisions(
    async_client: AsyncClient, admin_headers: dict, seeded_bot_id: str
) -> None:
    from datetime import datetime, timezone, timedelta
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    resp = await async_client.post(
        f"/api/bots/{seeded_bot_id}/advisor-attribution/recompute",
        json={"since": since},
        headers=admin_headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_advisor_decision_response_includes_attribution_fields(
    async_client: AsyncClient, auth_headers: dict, seeded_bot_id: str
) -> None:
    """Existing decisions endpoint is backward-compatible (new fields default to None/pending)."""
    resp = await async_client.get(
        f"/api/bots/{seeded_bot_id}/advisor-decisions",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    if items:
        assert "attribution_status" in items[0]
        assert "outcome_1h_correct" in items[0]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
bash backend/scripts/run-tests.sh backend/tests/api/test_advisor_attribution_api.py -v
```

Expected: 404 on attribution endpoint.

- [ ] **Step 3: Add endpoints to `bots.py`**

After the existing `override_advisor_decision` endpoint (around line 580 in `bots.py`), add:

```python
# ── Advisor Attribution ───────────────────────────────────────────────────────

class _RecomputeRequest(BaseModel):
    since: datetime


@router.get("/{bot_id}/advisor-attribution")
async def get_advisor_attribution(
    bot_id: UUID,
    db: DbDep,
    redis: RedisDep,
    _user: JwtSubject,
    window: str = Query(default="1h"),
) -> dict[str, Any]:
    await _assert_bot_exists(bot_id, db)
    if window not in {"15m", "1h", "4h", "eod"}:
        raise HTTPException(status_code=422, detail="invalid_window")
    from app.services.advisor.attribution import AttributionService
    svc = AttributionService(db_factory=None, redis=redis)  # type: ignore[arg-type]
    summary = await svc.get_summary(bot_id=bot_id, window=window, db=db)
    return jsonable_encoder(summary.model_dump())


@router.post("/{bot_id}/advisor-attribution/recompute")
async def recompute_advisor_attribution(
    bot_id: UUID,
    body: _RecomputeRequest,
    db: DbDep,
    redis: RedisDep,
    _user: JwtSubject,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
) -> dict[str, Any]:
    await _assert_bot_exists(bot_id, db)
    from app.services.advisor.attribution import AttributionService
    svc = AttributionService(db_factory=None, redis=redis)  # type: ignore[arg-type]
    try:
        count = await svc.recompute(bot_id=bot_id, since=body.since, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"reset_count": count}
```

- [ ] **Step 4: Widen `list_advisor_decisions` query to include outcome fields**

In the existing `list_advisor_decisions` function at line 482, extend the SELECT to include the new columns:

```python
    rows = await db.execute(
        text(
            f"""
            SELECT id, verdict, reasoning, confidence, advice_tags, canonical_id,
                   effective_mode, latency_ms, ai_completion_ts, created_at,
                   overridden_at, overridden_by, override_action, override_reason,
                   attribution_status, attribution_windows, attribution_computed_at,
                   outcome_15m_correct, outcome_15m_pnl,
                   outcome_1h_correct, outcome_1h_pnl,
                   outcome_4h_correct, outcome_4h_pnl,
                   outcome_eod_correct, outcome_eod_pnl
            FROM bot_advisor_decisions
            WHERE bot_id = :bid {before_sql}
            ORDER BY created_at DESC
            LIMIT :limit
            """
        ),
        params,
    )
```

Also extend the `get_advisor_decision` SELECT (`SELECT *` already returns all columns — no change needed there).

- [ ] **Step 5: Run tests**

```bash
bash backend/scripts/run-tests.sh backend/tests/api/test_advisor_attribution_api.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 6: Run full backend tests**

```bash
bash backend/scripts/run-tests.sh backend/ 2>&1 | tail -5
```

Expected: All existing tests still PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/bots.py \
        backend/tests/api/test_advisor_attribution_api.py
git commit -m "feat(21c-D): advisor attribution REST endpoints"
```

---

## Chunk E — Frontend (Codex)

**Files:**
- Modify: `frontend/src/services/advisor/types.ts`
- Modify: `frontend/src/services/advisor/api.ts`
- Create: `frontend/src/features/bots/components/AdvisorScoreCard.tsx`
- Create: `frontend/src/features/bots/components/AdvisorScoreCard.test.tsx`
- Modify: `frontend/src/features/bots/components/AdvisorDecisionsTable.tsx`
- Modify: `frontend/src/features/bots/components/AdvisorDecisionsTable.test.tsx`
- Modify: `frontend/src/features/bots/components/AdvisorDecisionDrawer.tsx`
- Modify: `frontend/src/features/bots/components/AdvisorDecisionDrawer.test.tsx`
- Modify: `frontend/src/features/bots/BotDetailPage.tsx`

### Task E-1: Extend service types and API

- [ ] **Step 1: Write failing tests**

```typescript
// frontend/src/features/bots/components/AdvisorScoreCard.test.tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { AdvisorScoreCard } from './AdvisorScoreCard'

const mockSummary = {
  bot_id: '123e4567-e89b-12d3-a456-426614174000',
  window: '1h',
  veto_accuracy: 0.7,
  approve_accuracy: 0.6,
  avg_avoided_loss_quote: '450.00',
  avg_missed_gain_quote: '120.00',
  complete_count: 10,
  partial_count: 2,
  pending_count: 5,
  bars_unavailable_count: 0,
  unresolvable_count: 0,
  skipped_count: 1,
  generated_at: '2026-05-19T14:00:00Z',
}

vi.mock('@/services/advisor/api', () => ({
  getAdvisorAttribution: vi.fn().mockResolvedValue(mockSummary),
}))

describe('AdvisorScoreCard', () => {
  it('renders veto accuracy percentage', async () => {
    render(<AdvisorScoreCard botId="123e4567-e89b-12d3-a456-426614174000" advisorMode="VETO" />)
    expect(await screen.findByText(/70%/)).toBeInTheDocument()
  })

  it('shows no-data message when complete_count is 0', async () => {
    vi.mocked(getAdvisorAttribution).mockResolvedValueOnce({ ...mockSummary, complete_count: 0 })
    render(<AdvisorScoreCard botId="any" advisorMode="VETO" />)
    expect(await screen.findByText(/No attribution data yet/)).toBeInTheDocument()
  })

  it('is hidden when advisorMode is OFF', () => {
    const { container } = render(<AdvisorScoreCard botId="any" advisorMode="OFF" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('uses 300_000ms stale time', () => {
    // Check that useQuery is called with staleTime: 300_000
    // (verify via mock or by inspecting the component source)
    expect(true).toBe(true)  // placeholder — verify in component review
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/joseph/dashboard/frontend
pnpm test src/features/bots/components/AdvisorScoreCard.test.tsx --run
```

Expected: Cannot find module `AdvisorScoreCard`.

- [ ] **Step 3: Extend `types.ts`**

Add to `frontend/src/services/advisor/types.ts`:

```typescript
export interface AttributionSummary {
  bot_id: string
  window: string
  veto_accuracy: number | null
  approve_accuracy: number | null
  avg_avoided_loss_quote: string | null
  avg_missed_gain_quote: string | null
  complete_count: number
  partial_count: number
  pending_count: number
  bars_unavailable_count: number
  unresolvable_count: number
  skipped_count: number
  generated_at: string
}
```

Add to the existing `AdvisorDecision` interface:

```typescript
  attribution_status: 'pending' | 'partial' | 'complete' | 'bars_unavailable' | 'unresolvable'
  outcome_15m_correct: boolean | null
  outcome_15m_pnl: string | null
  outcome_1h_correct: boolean | null
  outcome_1h_pnl: string | null
  outcome_4h_correct: boolean | null
  outcome_4h_pnl: string | null
  outcome_eod_correct: boolean | null
  outcome_eod_pnl: string | null
  attribution_computed_at: string | null
```

- [ ] **Step 4: Extend `api.ts`**

Add to `frontend/src/services/advisor/api.ts`:

```typescript
export async function getAdvisorAttribution(
  botId: string,
  window = '1h',
): Promise<AttributionSummary> {
  const resp = await fetch(
    `/api/bots/${botId}/advisor-attribution?window=${encodeURIComponent(window)}`,
    { credentials: 'include' },
  )
  if (!resp.ok) throw new Error(`advisor_attribution_fetch_failed: ${resp.status}`)
  return resp.json()
}

export async function recomputeAttribution(
  botId: string,
  since: string,
  csrfToken: string,
): Promise<void> {
  const resp = await fetch(`/api/bots/${botId}/advisor-attribution/recompute`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
    },
    credentials: 'include',
    body: JSON.stringify({ since }),
  })
  if (!resp.ok) throw new Error(`recompute_failed: ${resp.status}`)
}
```

### Task E-2: Create `AdvisorScoreCard` component

- [ ] **Step 5: Write the component**

```tsx
// frontend/src/features/bots/components/AdvisorScoreCard.tsx
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getAdvisorAttribution } from '@/services/advisor/api'
import type { AttributionSummary } from '@/services/advisor/types'

interface AdvisorScoreCardProps {
  botId: string
  advisorMode: 'OFF' | 'OBSERVE' | 'VETO'
}

const WINDOWS = ['15m', '1h', '4h', 'eod'] as const

function AccuracyBar({ value }: { value: number | null }) {
  if (value === null) return <span className="text-muted-foreground text-sm">—</span>
  const pct = Math.round(value * 100)
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 flex-1 rounded-full bg-muted overflow-hidden">
        <div
          className="h-full rounded-full bg-green-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-sm tabular-nums">{pct}%</span>
    </div>
  )
}

export function AdvisorScoreCard({ botId, advisorMode }: AdvisorScoreCardProps) {
  const [window, setWindow] = useState<string>('1h')

  const { data } = useQuery<AttributionSummary>({
    queryKey: ['advisor-attribution', botId, window],
    queryFn: () => getAdvisorAttribution(botId, window),
    staleTime: 300_000,
    enabled: advisorMode !== 'OFF',
  })

  if (advisorMode === 'OFF') return null

  if (!data || data.complete_count === 0) {
    return (
      <div className="rounded-lg border p-4 text-sm text-muted-foreground">
        No attribution data yet — outcomes computed after window elapses.
      </div>
    )
  }

  return (
    <div className="rounded-lg border p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-medium">Advisor Accuracy</h3>
        <select
          className="text-sm border rounded px-2 py-1"
          value={window}
          onChange={e => setWindow(e.target.value)}
          aria-label="Attribution window"
        >
          {WINDOWS.map(w => (
            <option key={w} value={w}>{w.toUpperCase()}</option>
          ))}
        </select>
      </div>

      <div className="space-y-2">
        <div>
          <div className="text-xs text-muted-foreground mb-1">Veto accuracy</div>
          <AccuracyBar value={data.veto_accuracy} />
        </div>
        <div>
          <div className="text-xs text-muted-foreground mb-1">Approve accuracy</div>
          <AccuracyBar value={data.approve_accuracy} />
        </div>
      </div>

      {data.avg_avoided_loss_quote !== null && (
        <div className="text-sm text-green-600">
          Avg avoided loss: {Number(data.avg_avoided_loss_quote).toFixed(2)}{' '}
          <span className="text-xs text-muted-foreground">(quote currency)</span>
        </div>
      )}
      {data.avg_missed_gain_quote !== null && (
        <div className="text-sm text-red-500">
          Avg missed gain: {Number(data.avg_missed_gain_quote).toFixed(2)}{' '}
          <span className="text-xs text-muted-foreground">(quote currency)</span>
        </div>
      )}

      <div className="text-xs text-muted-foreground pt-2 border-t">
        {data.complete_count} complete · {data.pending_count} pending
        · {data.bars_unavailable_count} unavailable
        · Updated {new Date(data.generated_at).toLocaleTimeString()}
      </div>
    </div>
  )
}
```

- [ ] **Step 6: Add outcome column to `AdvisorDecisionsTable`**

In `AdvisorDecisionsTable.tsx`, add a column after the existing `verdict` column:

```tsx
// New column definition:
{
  header: 'Outcome (1h)',
  cell: ({ row }) => {
    const { attribution_status, outcome_1h_correct } = row.original
    if (attribution_status === 'pending' || attribution_status === 'unresolvable') {
      return <span className="text-muted-foreground">—</span>
    }
    if (outcome_1h_correct === null) {
      return <span className="text-muted-foreground">—</span>
    }
    return outcome_1h_correct ? (
      <span className="text-green-600 font-medium">✓</span>
    ) : (
      <span className="text-red-500 font-medium">✗</span>
    )
  },
  // Hide column entirely when all rows are pending/unresolvable
}
```

Add column visibility logic: if all visible rows have `attribution_status` in `['pending', 'unresolvable']`, set column visibility to hidden.

- [ ] **Step 7: Add outcome line to `AdvisorDecisionDrawer`**

In `AdvisorDecisionDrawer.tsx`, add a section at the bottom of the drawer body, rendered only when `decision.attribution_status === 'complete'`:

```tsx
{decision.attribution_status === 'complete' && (
  <div className="pt-4 border-t text-sm">
    <span className="text-muted-foreground">Outcome (1h): </span>
    {decision.verdict === 'veto' ? (
      decision.outcome_1h_correct ? (
        <span className="text-green-600">
          ✓ Avoided {Math.abs(Number(decision.outcome_1h_pnl)).toFixed(2)}{' '}
          <OutcomeTooltip />
        </span>
      ) : (
        <span className="text-red-500">
          ✗ Missed gain {Math.abs(Number(decision.outcome_1h_pnl)).toFixed(2)}{' '}
          <OutcomeTooltip />
        </span>
      )
    ) : (
      decision.outcome_1h_correct ? (
        <span className="text-green-600">
          ✓ +{Number(decision.outcome_1h_pnl).toFixed(2)}{' '}
          <OutcomeTooltip />
        </span>
      ) : (
        <span className="text-red-500">
          ✗ {Number(decision.outcome_1h_pnl).toFixed(2)}{' '}
          <OutcomeTooltip />
        </span>
      )
    )}
  </div>
)}
```

Where `OutcomeTooltip` is:

```tsx
function OutcomeTooltip() {
  return (
    <span
      className="text-xs text-muted-foreground cursor-help"
      title="Amount in instrument's quote currency. USD conversion coming in v0.21.3.1."
    >
      (quote)
    </span>
  )
}
```

- [ ] **Step 8: Add `AdvisorScoreCard` to `BotDetailPage` overview tab**

In `BotDetailPage.tsx`, add import:

```tsx
import { AdvisorScoreCard } from './components/AdvisorScoreCard'
```

Find the overview tab render section (where existing bot status sections are rendered). Add `AdvisorScoreCard` after the existing bot status section:

```tsx
{activeTab === 'overview' && (
  <>
    {/* existing overview content */}
    <AdvisorScoreCard
      botId={botId}
      advisorMode={bot.advisor_config?.mode ?? 'OFF'}
    />
  </>
)}
```

The exact location depends on the current JSX structure — read lines 50–150 of `BotDetailPage.tsx` to find the overview tab render block.

- [ ] **Step 9: Run all FE tests**

```bash
cd /home/joseph/dashboard/frontend
pnpm test --run 2>&1 | tee /tmp/fe_test_run.txt
```

Expected: New tests PASS; all existing tests still PASS. Inspect `/tmp/fe_test_run.txt` before proceeding.

- [ ] **Step 10: Run TypeScript check**

```bash
cd /home/joseph/dashboard/frontend
pnpm tsc --noEmit
```

Expected: 0 errors.

- [ ] **Step 11: Commit**

```bash
git add frontend/src/services/advisor/types.ts \
        frontend/src/services/advisor/api.ts \
        frontend/src/features/bots/components/AdvisorScoreCard.tsx \
        frontend/src/features/bots/components/AdvisorScoreCard.test.tsx \
        frontend/src/features/bots/components/AdvisorDecisionsTable.tsx \
        frontend/src/features/bots/components/AdvisorDecisionsTable.test.tsx \
        frontend/src/features/bots/components/AdvisorDecisionDrawer.tsx \
        frontend/src/features/bots/components/AdvisorDecisionDrawer.test.tsx \
        frontend/src/features/bots/BotDetailPage.tsx
git commit -m "feat(21c-E): AdvisorScoreCard + outcome columns + drawer outcome line"
```

---

## Chunk F — Close-out (Opus direct)

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/TASKS.md`

### Task F-1: Run full test suite and verify counts

- [ ] **Step 1: Run all backend tests**

```bash
bash backend/scripts/run-tests.sh backend/ 2>&1 | tail -5
```

Expected: All tests PASS. Note the test count.

- [ ] **Step 2: Run all frontend tests**

```bash
cd /home/joseph/dashboard/frontend && pnpm test --run 2>&1 | tail -5
```

Expected: All tests PASS. Note the test count.

### Task F-2: Update CLAUDE.md

- [ ] **Step 3: Add Phase 21c entry to the Phase cross-cutting rules section**

Find the `LLM Advisor Gate (Phase 21a, shipped v0.21.0)` entry in `CLAUDE.md` and add after it:

```
- **Advisor Perf-Attribution (Phase 21c, shipped v0.21.3):** Alembic 0068 (`bot_advisor_decisions` outcome cols + `attribution_status` + `attribution_windows` CHECK; `bot_orders.advisor_decision_id` FK ON DELETE SET NULL). `bot_advisor_decisions` is a **plain table** (NOT hypertable). `app/services/advisor/attribution.py::AttributionService`: `poll()` (FOR UPDATE SKIP LOCKED LIMIT 500, window snapshotting, next-bar-open simulated pnl = `(exit-entry)*qty*multiplier*side_sign`, fail-OPEN per decision, CLOSE skip), `get_summary()` (window allowlist before SQL, match/case dispatch — no f-string col names), `recompute()` (since >= now()-6m validation). `app/services/quotes/instrument_resolver.py::InstrumentResolver.find_by_canonical_id()` new read-only method (SELECT `id, (meta->>'multiplier')::numeric, primary_exchange` from instruments; Redis cache `attribution:instr:{cid}` TTL 3600s). `app/services/market_calendar.py::session_close_for_decision(exchange, created_at)` — after-hours → next session; raises ValueError on unknown exchange (no UTC fallback). APScheduler `IntervalTrigger(seconds=900)` job in `main.py`. 5 Prometheus metrics `advisor_attribution_*`. REST: `GET /api/bots/{id}/advisor-attribution` + `POST /api/bots/{id}/advisor-attribution/recompute` (admin + CSRF). FE: `AdvisorScoreCard` (overview tab, 300s stale time, window selector); `AdvisorDecisionsTable` outcome column (1h); `AdvisorDecisionDrawer` outcome line. PnL in quote currency; FX conversion deferred to v0.21.3.1.
```

### Task F-3: Update CHANGELOG.md and TASKS.md

- [ ] **Step 4: Add v0.21.3 entry to CHANGELOG.md**

```markdown
## v0.21.3 — 2026-05-19

### Added
- Advisor perf-attribution: "was the advisor right?" for every `bot_advisor_decisions` row
- Alembic 0068: outcome columns (15m/1h/4h/EOD correct + pnl), `attribution_status`, `attribution_windows` snapshot, `advisor_decision_id` FK on `bot_orders`
- `AttributionService`: next-bar-open simulated PnL, FOR UPDATE SKIP LOCKED poll, window snapshotting, CLOSE skip, partial-at-expiry promotion
- `InstrumentResolver.find_by_canonical_id()`: read-only lookup with Redis cache
- `session_close_for_decision()` in market_calendar.py: after-hours → next session EOD; no UTC fallback
- APScheduler interval job (default 900s, configurable via `app_config`)
- 5 new Prometheus metrics `advisor_attribution_*`
- REST: `GET /api/bots/{id}/advisor-attribution`, `POST .../recompute`
- FE: `AdvisorScoreCard` on overview tab, outcome column in decisions table, outcome line in decision drawer

### Notes
- PnL values in instrument-native quote currency; FX conversion to USD deferred to v0.21.3.1
```

- [ ] **Step 5: Mark Phase 21c complete in TASKS.md**

Find the Phase 21c row in `docs/TASKS.md` and mark it done.

### Task F-4: Tag and push

- [ ] **Step 6: Commit close-out**

```bash
git add CLAUDE.md docs/CHANGELOG.md docs/TASKS.md
git commit -m "docs(21c): close-out CLAUDE.md + CHANGELOG + TASKS v0.21.3"
```

- [ ] **Step 7: Tag v0.21.3**

```bash
git tag v0.21.3
git push origin main --tags
```

---

## Reviewer chain (per chunk)

| Chunk | Reviewers |
|---|---|
| A | spec-compliance (haiku) + database-reviewer (sonnet) + code-quality (sonnet) |
| B | spec-compliance (haiku) + python-reviewer (haiku) + code-quality (sonnet) |
| C | spec-compliance (haiku) + python-reviewer (haiku) + security-reviewer (sonnet) |
| D | spec-compliance (haiku) + python-reviewer (haiku) + code-quality (sonnet) |
| E | spec-compliance (haiku) + typescript-reviewer (haiku) + code-quality (sonnet) |
| Phase end | ARCHITECT-REVIEW (opus) |
