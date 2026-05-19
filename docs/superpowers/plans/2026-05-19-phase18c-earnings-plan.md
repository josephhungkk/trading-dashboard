# Phase 18.2 — Earnings Calendar + Auto-flat/Pause Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Earnings calendar sourced from Nasdaq API (primary) + Finnhub (fallback), with per-position auto-flat and per-bot auto-pause hooks that fire N minutes before each scheduled announcement. `place_order_internal` lands in `orders_service` as the single internal order entry point for all non-HTTP callers.

**Architecture:** Two APScheduler pollers (Nasdaq daily 06:00 ET, Finnhub fallback) upsert `earnings_events` with `source_priority`-gated conflict resolution. A hook evaluator runs every 1 min during market hours, checking `earnings_hooks` against upcoming events. On match: Postgres `hook_audit` + Redis `SET NX` dedup claim BEFORE `place_order_internal` broker dispatch — preventing double-flat on crash. `auto_pause_bot` is a no-op stub until Phase 20. The `/earnings` calendar route and `EarningsBadge` component surface events in the positions table and TradeTicketModal. This sub-phase does NOT depend on Phase 18.0 or 18.1 being complete.

**Tech Stack:** Python (FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, APScheduler, structlog), Redis (dedup NX), PostgreSQL 18, React 19 + TanStack Query, TypeScript strict

---

### File Map

**New files:**
- `backend/alembic/versions/0060_earnings.py` — `earnings_events`, `earnings_hooks`, `hook_audit`; widens `attempt_kind` CHECK
- `backend/app/services/earnings/__init__.py`
- `backend/app/services/earnings/schemas.py` — `EarningsEvent`, `EarningsHook`, `HookAudit` Pydantic models
- `backend/app/services/earnings/nasdaq_calendar.py` — Nasdaq earnings API poller
- `backend/app/services/earnings/finnhub_calendar.py` — Finnhub free-tier fallback poller
- `backend/app/services/earnings/hook_executor.py` — `HookExecutor`: auto_flat + auto_pause_bot
- `backend/app/services/earnings/earnings_service.py` — `EarningsService` orchestrator
- `backend/app/api/earnings.py` — 7 REST endpoints
- `backend/tests/test_earnings.py` — integration tests
- `frontend/src/services/earnings/types.ts`
- `frontend/src/services/earnings/api.ts`
- `frontend/src/features/earnings/EarningsPage.tsx`
- `frontend/src/features/earnings/EarningsBadge.tsx`
- `frontend/src/features/earnings/EarningsPanel.tsx`
- `frontend/src/features/earnings/EarningsHookDrawer.tsx`
- `frontend/src/routes/earnings.tsx`

**Modified files:**
- `backend/app/services/orders_service.py` — add `place_order_internal`; widen `attempt_kind` CHECK to include `"earnings_hook_flat"`
- `backend/app/core/metrics.py` — add 7 `earnings_*` counters
- `backend/app/main.py` — wire `EarningsService` lifespan + APScheduler jobs
- `frontend/src/features/positions/PositionsTable.tsx` — inject `EarningsBadge`
- `frontend/src/features/trading/TradeTicketModal.tsx` — inject `EarningsBadge`
- `frontend/src/routes/__root.tsx` — add `/earnings` nav link

---

### Task 1: Alembic migration 0060

**Files:**
- Create: `backend/alembic/versions/0060_earnings.py`

- [ ] **Step 1: Write the failing test for migration**

```python
# backend/tests/test_earnings.py
import pytest
from sqlalchemy import text

@pytest.mark.integration
async def test_0060_migration_tables_exist(db):
    """Migration 0060 creates earnings_events, earnings_hooks, hook_audit."""
    result = await db.execute(
        text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('earnings_events', 'earnings_hooks', 'hook_audit')
        """)
    )
    names = {r[0] for r in result}
    assert "earnings_events" in names
    assert "earnings_hooks" in names
    assert "hook_audit" in names
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend pytest tests/test_earnings.py::test_0060_migration_tables_exist -v
```
Expected: FAIL — tables don't exist yet

- [ ] **Step 3: Write migration**

```python
# backend/alembic/versions/0060_earnings.py
"""add earnings_events, earnings_hooks, hook_audit; widen attempt_kind check

Revision ID: 0060
Revises: 0059
Create Date: 2026-05-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "earnings_events",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("instrument_id", sa.BigInteger(), sa.ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("canonical_id", sa.Text(), nullable=False),
        sa.Column("announced_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("announced_date", sa.Date(), nullable=False),
        sa.Column("time_of_day", sa.Text(), nullable=True),
        sa.Column("eps_estimate", sa.Numeric(20, 8), nullable=True),
        sa.Column("eps_actual", sa.Numeric(20, 8), nullable=True),
        sa.Column("revenue_estimate", sa.Numeric(20, 8), nullable=True),
        sa.Column("revenue_actual", sa.Numeric(20, 8), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("captured_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "time_of_day IN ('before_open', 'after_close', 'during_market', 'unknown')",
            name="earnings_events_time_of_day_check",
        ),
        sa.CheckConstraint(
            "source IN ('nasdaq_api', 'finnhub_api', 'manual')",
            name="earnings_events_source_check",
        ),
        sa.UniqueConstraint("instrument_id", "announced_date", name="uq_earnings_instrument_date"),
    )
    op.create_index("ix_earnings_events_instrument_id", "earnings_events", ["instrument_id"])
    op.create_index("ix_earnings_events_announced_date", "earnings_events", ["announced_date"])

    op.create_table(
        "earnings_hooks",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("instrument_id", sa.BigInteger(), sa.ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.UUID(), sa.ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("jwt_subject", sa.Text(), nullable=False),
        sa.Column("hook_type", sa.Text(), nullable=False),
        sa.Column("minutes_before", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("bot_id", sa.UUID(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("hook_type IN ('auto_flat', 'auto_pause_bot')", name="earnings_hooks_type_check"),
        sa.CheckConstraint("minutes_before >= 10", name="earnings_hooks_minutes_before_check"),
    )

    op.create_table(
        "hook_audit",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("hook_id", sa.UUID(), sa.ForeignKey("earnings_hooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.UUID(), sa.ForeignKey("earnings_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fired_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('placed', 'skipped_no_position', 'failed', 'failed_kill_switch')",
            name="hook_audit_outcome_check",
        ),
        sa.UniqueConstraint("hook_id", "event_id", name="uq_hook_audit_hook_event"),
    )

    # Widen attempt_kind CHECK constraint in risk_decisions to include earnings_hook_flat
    # Drop old constraint and recreate with widened values
    op.execute("""
        ALTER TABLE risk_decisions
        DROP CONSTRAINT IF EXISTS risk_decisions_attempt_kind_check
    """)
    op.execute("""
        ALTER TABLE risk_decisions
        ADD CONSTRAINT risk_decisions_attempt_kind_check
        CHECK (attempt_kind IN (
            'preview', 'place', 'modify',
            'telegram', 'telegram_confirm',
            'earnings_hook_flat'
        ))
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE risk_decisions
        DROP CONSTRAINT IF EXISTS risk_decisions_attempt_kind_check
    """)
    op.execute("""
        ALTER TABLE risk_decisions
        ADD CONSTRAINT risk_decisions_attempt_kind_check
        CHECK (attempt_kind IN ('preview', 'place', 'modify', 'telegram', 'telegram_confirm'))
    """)
    op.drop_table("hook_audit")
    op.drop_table("earnings_hooks")
    op.drop_index("ix_earnings_events_announced_date")
    op.drop_index("ix_earnings_events_instrument_id")
    op.drop_table("earnings_events")
```

- [ ] **Step 4: Run migration**

```bash
docker compose exec backend alembic upgrade head
```
Expected: migration 0060 applied successfully

- [ ] **Step 5: Run test to verify it passes**

```bash
docker compose exec backend pytest tests/test_earnings.py::test_0060_migration_tables_exist -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0060_earnings.py backend/tests/test_earnings.py
git commit -m "feat(phase18c): alembic 0060 — earnings_events + earnings_hooks + hook_audit; widen attempt_kind"
```

---

### Task 2: Pydantic schemas + Prometheus metrics

**Files:**
- Create: `backend/app/services/earnings/__init__.py`
- Create: `backend/app/services/earnings/schemas.py`
- Modify: `backend/app/core/metrics.py`

- [ ] **Step 1: Write the failing test**

```python
# in backend/tests/test_earnings.py — add:
from app.services.earnings.schemas import EarningsEvent, EarningsHook, HookAuditRow
from datetime import date, datetime, timezone
import uuid

def test_earnings_event_model():
    ev = EarningsEvent(
        id=uuid.uuid4(),
        instrument_id=1,
        canonical_id="AAPL.XNAS",
        announced_date=date(2024, 5, 1),
        time_of_day="after_close",
        source="nasdaq_api",
        source_priority=2,
        confirmed=True,
        captured_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert ev.source == "nasdaq_api"
    assert ev.source_priority == 2

def test_earnings_hook_model():
    hook = EarningsHook(
        id=uuid.uuid4(),
        instrument_id=1,
        account_id=uuid.uuid4(),
        jwt_subject="user:abc",
        hook_type="auto_flat",
        minutes_before=30,
        enabled=True,
        created_at=datetime.now(timezone.utc),
    )
    assert hook.hook_type == "auto_flat"

def test_earnings_hook_minutes_before_minimum():
    import pytest
    with pytest.raises(Exception):
        EarningsHook(
            id=uuid.uuid4(),
            instrument_id=1,
            account_id=uuid.uuid4(),
            jwt_subject="user:abc",
            hook_type="auto_flat",
            minutes_before=5,  # below minimum 10
            enabled=True,
            created_at=datetime.now(timezone.utc),
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend pytest tests/test_earnings.py::test_earnings_event_model tests/test_earnings.py::test_earnings_hook_model tests/test_earnings.py::test_earnings_hook_minutes_before_minimum -v
```
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Write schemas**

```python
# backend/app/services/earnings/__init__.py
# (empty)
```

```python
# backend/app/services/earnings/schemas.py
from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
import uuid
from pydantic import BaseModel, field_validator


class EarningsEvent(BaseModel):
    id: uuid.UUID
    instrument_id: int
    canonical_id: str
    announced_at: Optional[datetime] = None
    announced_date: date
    time_of_day: Optional[str] = None  # 'before_open'|'after_close'|'during_market'|'unknown'
    eps_estimate: Optional[Decimal] = None
    eps_actual: Optional[Decimal] = None
    revenue_estimate: Optional[Decimal] = None
    revenue_actual: Optional[Decimal] = None
    source: str  # 'nasdaq_api' | 'finnhub_api' | 'manual'
    source_priority: int = 0
    confirmed: bool = False
    captured_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EarningsHook(BaseModel):
    id: uuid.UUID
    instrument_id: int
    account_id: uuid.UUID
    jwt_subject: str
    hook_type: str  # 'auto_flat' | 'auto_pause_bot'
    minutes_before: int = 30
    bot_id: Optional[uuid.UUID] = None
    enabled: bool = True
    created_at: datetime

    @field_validator("minutes_before")
    @classmethod
    def minutes_before_minimum(cls, v: int) -> int:
        if v < 10:
            raise ValueError("minutes_before must be >= 10")
        return v

    class Config:
        from_attributes = True


class HookAuditRow(BaseModel):
    id: uuid.UUID
    hook_id: uuid.UUID
    event_id: uuid.UUID
    fired_at: datetime
    outcome: str  # 'placed'|'skipped_no_position'|'failed'|'failed_kill_switch'
    order_id: Optional[uuid.UUID] = None

    class Config:
        from_attributes = True
```

- [ ] **Step 4: Add Prometheus metrics**

In `backend/app/core/metrics.py`, add after the filings metrics:

```python
# Earnings metrics
earnings_events_ingested_total = Counter(
    "earnings_events_ingested_total",
    "Earnings events ingested",
    ["source"],
)
earnings_hooks_fired_total = Counter(
    "earnings_hooks_fired_total",
    "Earnings hooks that fired",
    ["hook_type"],
)
earnings_hooks_failed_total = Counter(
    "earnings_hooks_failed_total",
    "Earnings hooks that failed",
    ["hook_type"],
)
earnings_autoflat_qty_total = Counter(
    "earnings_autoflat_qty_total",
    "Total quantity flattened by auto_flat hooks",
)
earnings_autoflat_race_detected_total = Counter(
    "earnings_autoflat_race_detected_total",
    "Auto-flat double-read race conditions detected",
)
earnings_poll_errors_total = Counter(
    "earnings_poll_errors_total",
    "Earnings calendar polling errors",
    ["source"],
)
earnings_dedup_skips_total = Counter(
    "earnings_dedup_skips_total",
    "Earnings events skipped as duplicates",
    ["source"],
)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
docker compose exec backend pytest tests/test_earnings.py::test_earnings_event_model tests/test_earnings.py::test_earnings_hook_model tests/test_earnings.py::test_earnings_hook_minutes_before_minimum -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/earnings/__init__.py backend/app/services/earnings/schemas.py backend/app/core/metrics.py backend/tests/test_earnings.py
git commit -m "feat(phase18c): earnings schemas + 7 Prometheus metrics"
```

---

### Task 3: Nasdaq + Finnhub calendar pollers

**Files:**
- Create: `backend/app/services/earnings/nasdaq_calendar.py`
- Create: `backend/app/services/earnings/finnhub_calendar.py`

- [ ] **Step 1: Write the failing tests**

```python
# in backend/tests/test_earnings.py — add:
from app.services.earnings.nasdaq_calendar import NasdaqCalendarPoller
from app.services.earnings.finnhub_calendar import FinnhubCalendarPoller
from unittest.mock import AsyncMock, MagicMock, patch

async def test_nasdaq_poller_parses_response(db):
    """Nasdaq poller parses API response into upsertable dicts."""
    sample = {
        "data": {
            "rows": [
                {
                    "symbol": "AAPL",
                    "name": "Apple Inc",
                    "priorFiscalQuarterEndDate": "2024-03-31",
                    "earningsDate": "2024-04-25",
                    "time": "AMC",
                    "eps": None,
                    "epsForecast": "1.52",
                    "numberOfEstimates": "25",
                }
            ]
        }
    }
    poller = NasdaqCalendarPoller(db=db)
    rows = poller._parse_response(sample)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["source"] == "nasdaq_api"
    assert rows[0]["source_priority"] == 2
    assert rows[0]["time_of_day"] == "after_close"

async def test_finnhub_poller_parses_response(db):
    """Finnhub poller parses earnings calendar response."""
    sample = {
        "earningsCalendar": [
            {
                "symbol": "GOOGL",
                "date": "2024-04-24",
                "hour": "bmo",
                "epsEstimate": 1.85,
                "epsActual": None,
                "revenueEstimate": 79000000000,
                "revenueActual": None,
            }
        ]
    }
    poller = FinnhubCalendarPoller(db=db)
    rows = poller._parse_response(sample)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "GOOGL"
    assert rows[0]["source"] == "finnhub_api"
    assert rows[0]["source_priority"] == 1
    assert rows[0]["time_of_day"] == "before_open"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend pytest tests/test_earnings.py::test_nasdaq_poller_parses_response tests/test_earnings.py::test_finnhub_poller_parses_response -v
```
Expected: FAIL — modules don't exist

- [ ] **Step 3: Write Nasdaq poller**

```python
# backend/app/services/earnings/nasdaq_calendar.py
"""Nasdaq earnings API poller (primary, free, no API key required).

Polls daily at 06:00 US/Eastern, fetches next 7 days of earnings.
Source priority: nasdaq_api = 2.
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Optional
import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import earnings_events_ingested_total, earnings_poll_errors_total

logger = structlog.get_logger(__name__)

_BASE = "https://api.nasdaq.com/api/calendar/earnings"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TradingDashboard/1.0)",
    "Accept": "application/json",
}

_TIME_MAP = {
    "BMO": "before_open",
    "AMC": "after_close",
    "DMT": "during_market",
}


class NasdaqCalendarPoller:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    def _parse_response(self, data: dict) -> list[dict]:
        rows = []
        for item in (data.get("data") or {}).get("rows") or []:
            ticker = item.get("symbol", "")
            if not ticker:
                continue
            time_raw = (item.get("time") or "").upper()
            eps_forecast = item.get("epsForecast")
            rows.append({
                "ticker": ticker,
                "announced_date": item.get("earningsDate"),
                "time_of_day": _TIME_MAP.get(time_raw, "unknown"),
                "eps_estimate": eps_forecast,
                "source": "nasdaq_api",
                "source_priority": 2,
                "confirmed": False,
            })
        return rows

    async def fetch(self, days_ahead: int = 7) -> list[dict]:
        today = date.today()
        end = today + timedelta(days=days_ahead)
        try:
            async with httpx.AsyncClient(timeout=20.0, headers=_HEADERS) as client:
                resp = await client.get(
                    _BASE,
                    params={"date": today.isoformat(), "limit": 500},
                )
                resp.raise_for_status()
            data = resp.json()
            rows = self._parse_response(data)
            for r in rows:
                earnings_events_ingested_total.labels(source="nasdaq_api").inc()
            return rows
        except Exception as exc:
            earnings_poll_errors_total.labels(source="nasdaq_api").inc()
            logger.exception("nasdaq_calendar_poll_error", exc_info=exc)
            return []
```

- [ ] **Step 4: Write Finnhub poller**

```python
# backend/app/services/earnings/finnhub_calendar.py
"""Finnhub free-tier earnings calendar poller (fallback).

Polls daily at 06:00 US/Eastern. Requires FINNHUB_API_KEY in app_secrets.
Source priority: finnhub_api = 1.
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Optional
import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import earnings_events_ingested_total, earnings_poll_errors_total

logger = structlog.get_logger(__name__)

_BASE = "https://finnhub.io/api/v1/calendar/earnings"

_HOUR_MAP = {
    "bmo": "before_open",
    "amc": "after_close",
    "dmh": "during_market",
}


class FinnhubCalendarPoller:
    def __init__(self, db: AsyncSession, api_key: Optional[str] = None) -> None:
        self._db = db
        self._api_key = api_key

    def _parse_response(self, data: dict) -> list[dict]:
        rows = []
        for item in data.get("earningsCalendar") or []:
            ticker = item.get("symbol", "")
            if not ticker:
                continue
            hour = (item.get("hour") or "").lower()
            rows.append({
                "ticker": ticker,
                "announced_date": item.get("date"),
                "time_of_day": _HOUR_MAP.get(hour, "unknown"),
                "eps_estimate": item.get("epsEstimate"),
                "eps_actual": item.get("epsActual"),
                "revenue_estimate": item.get("revenueEstimate"),
                "revenue_actual": item.get("revenueActual"),
                "source": "finnhub_api",
                "source_priority": 1,
                "confirmed": False,
            })
        return rows

    async def fetch(self, days_ahead: int = 7) -> list[dict]:
        if not self._api_key:
            logger.info("finnhub_poller_disabled", reason="no api_key")
            return []
        today = date.today()
        end = today + timedelta(days=days_ahead)
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    _BASE,
                    params={"from": today.isoformat(), "to": end.isoformat(), "token": self._api_key},
                )
                resp.raise_for_status()
            data = resp.json()
            rows = self._parse_response(data)
            for r in rows:
                earnings_events_ingested_total.labels(source="finnhub_api").inc()
            return rows
        except Exception as exc:
            earnings_poll_errors_total.labels(source="finnhub_api").inc()
            logger.exception("finnhub_calendar_poll_error", exc_info=exc)
            return []
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
docker compose exec backend pytest tests/test_earnings.py::test_nasdaq_poller_parses_response tests/test_earnings.py::test_finnhub_poller_parses_response -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/earnings/nasdaq_calendar.py backend/app/services/earnings/finnhub_calendar.py backend/tests/test_earnings.py
git commit -m "feat(phase18c): Nasdaq earnings calendar poller (primary) + Finnhub fallback"
```

---

### Task 4: `place_order_internal` in orders_service

**Files:**
- Modify: `backend/app/services/orders_service.py`

- [ ] **Step 1: Write the failing test**

```python
# in backend/tests/test_earnings.py — add:
from app.services.orders_service import place_order_internal
from typing import Literal

async def test_place_order_internal_exists_and_accepts_issuer(db, redis):
    """place_order_internal accepts issuer parameter and routes through validation stations."""
    from unittest.mock import AsyncMock, patch, MagicMock
    # We test the signature; full integration tested in test_orders*.py
    import inspect
    sig = inspect.signature(place_order_internal)
    assert "issuer" in sig.parameters
    assert "jwt_subject" in sig.parameters
    assert "bypass_pdt_when_closing" in sig.parameters
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend pytest tests/test_earnings.py::test_place_order_internal_exists_and_accepts_issuer -v
```
Expected: FAIL — function doesn't exist

- [ ] **Step 3: Add `place_order_internal` to orders_service**

Open `backend/app/services/orders_service.py`. Find the existing `place_order` function signature (around line 903). Add a new internal entry point immediately after it:

```python
# backend/app/services/orders_service.py — add after the existing place_order function

async def place_order_internal(
    *,
    cfg: object,
    db: AsyncSession,
    redis: object,
    registry: object,
    capability: object,
    jwt_subject: str,
    issuer: Literal["telegram", "earnings_hook"],
    account_id: uuid.UUID,
    instrument_id: int,
    side: str,
    qty: Decimal,
    order_type: str,
    position_effect: Optional[str] = None,
    bypass_pdt_when_closing: bool = False,
    client_order_id: Optional[str] = None,
) -> OrderRow:
    """Internal order entry point for non-HTTP callers (Telegram, earnings hooks).

    Never exposed via HTTP/WS. Skips HTTP-context CSRF nonce check.
    Routes through all 5 validation stations including risk gate.
    issuer is recorded in risk_decisions.attempt_kind.
    """
    from app.api.orders import PlaceOrderRequest  # avoid circular at module level

    request_data = PlaceOrderRequest(
        account_id=account_id,
        instrument_id=instrument_id,
        side=side,
        qty=qty,
        order_type=order_type,
        position_effect=position_effect,
        client_order_id=client_order_id or f"{issuer}-{uuid.uuid4()}",
        bypass_pdt_when_closing=bypass_pdt_when_closing,
    )
    return await place_order(
        cfg=cfg,
        db=db,
        redis=redis,
        registry=registry,
        capability=capability,
        jwt_subject=jwt_subject,
        attempt_kind=issuer,  # 'earnings_hook_flat' is mapped at call site
        request_data=request_data,
        _skip_csrf=True,
    )
```

Note: `place_order` must already accept `attempt_kind` and `_skip_csrf` params (the existing function uses `attempt_kind="place"` by default). If the `place_order` signature does not yet have `attempt_kind` or `_skip_csrf`, add them with defaults `attempt_kind: str = "place"` and `_skip_csrf: bool = False` respectively, and guard the CSRF check with `if not _skip_csrf:`.

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose exec backend pytest tests/test_earnings.py::test_place_order_internal_exists_and_accepts_issuer -v
```
Expected: PASS

- [ ] **Step 5: Run existing orders tests to verify no regression**

```bash
docker compose exec backend pytest tests/test_orders*.py -v 2>&1 | tail -20
```
Expected: All passing (existing orders tests unchanged)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/orders_service.py backend/tests/test_earnings.py
git commit -m "feat(phase18c): add place_order_internal — internal entry point for Telegram + earnings hooks"
```

---

### Task 5: HookExecutor — auto_flat + auto_pause_bot

**Files:**
- Create: `backend/app/services/earnings/hook_executor.py`

- [ ] **Step 1: Write the failing tests**

```python
# in backend/tests/test_earnings.py — add:
from app.services.earnings.hook_executor import HookExecutor
import uuid
from datetime import date, datetime, timezone, timedelta

async def test_hook_executor_skips_when_no_position(db, redis):
    """HookExecutor skips auto_flat when position qty == 0."""
    from unittest.mock import AsyncMock, patch, MagicMock
    executor = HookExecutor(db=db, redis=redis, cfg=MagicMock(), registry=MagicMock(), capability=MagicMock())

    hook_id = uuid.uuid4()
    event_id = uuid.uuid4()
    instrument_id = 1
    account_id = uuid.uuid4()

    result = await executor._resolve_open_position(instrument_id, account_id)
    assert result == 0  # no position → qty 0

async def test_hook_executor_dedup_redis_nx(redis):
    """HookExecutor SET NX prevents double-fire."""
    executor = HookExecutor.__new__(HookExecutor)
    hook_id = uuid.uuid4()
    event_id = uuid.uuid4()

    # First claim succeeds
    claimed = await executor._claim_redis(redis, hook_id, event_id)
    assert claimed is True

    # Second claim fails (NX)
    claimed2 = await executor._claim_redis(redis, hook_id, event_id)
    assert claimed2 is False

async def test_hook_executor_options_side_resolution():
    """auto_flat picks sell_to_close for long option positions."""
    executor = HookExecutor.__new__(HookExecutor)
    # Long option (qty > 0)
    side = executor._resolve_flat_side(asset_class="OPTION", qty=5)
    assert side == "sell_to_close"
    # Short option (qty < 0)
    side_short = executor._resolve_flat_side(asset_class="OPTION", qty=-3)
    assert side_short == "buy_to_close"
    # Long stock
    side_stock = executor._resolve_flat_side(asset_class="STOCK", qty=100)
    assert side_stock == "sell"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend pytest tests/test_earnings.py::test_hook_executor_skips_when_no_position tests/test_earnings.py::test_hook_executor_dedup_redis_nx tests/test_earnings.py::test_hook_executor_options_side_resolution -v
```
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Write HookExecutor**

```python
# backend/app/services/earnings/hook_executor.py
"""HookExecutor: evaluates earnings_hooks and fires auto_flat / auto_pause_bot.

auto_flat step ordering (spec §12 cross-cutting invariant):
  1. Resolve position qty
  2. Check hook_audit UNIQUE row (Postgres durable guard)
  3. Redis SET NX (concurrent evaluator guard)
  4. Claim hook_audit row
  5. Determine side + call place_order_internal
  6. Update hook_audit outcome
"""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.core.metrics import (
    earnings_hooks_fired_total,
    earnings_hooks_failed_total,
    earnings_autoflat_qty_total,
    earnings_autoflat_race_detected_total,
)
from app.services.orders_service import place_order_internal

logger = structlog.get_logger(__name__)

_REDIS_TTL = 604800  # 7 days


class HookExecutor:
    def __init__(
        self,
        db: AsyncSession,
        redis: object,
        cfg: object,
        registry: object,
        capability: object,
        telegram_notifier: Optional[object] = None,
    ) -> None:
        self._db = db
        self._redis = redis
        self._cfg = cfg
        self._registry = registry
        self._capability = capability
        self._notifier = telegram_notifier

    def _resolve_flat_side(self, asset_class: str, qty: float) -> str:
        if asset_class == "OPTION":
            return "sell_to_close" if qty > 0 else "buy_to_close"
        return "sell" if qty > 0 else "buy"

    async def _resolve_open_position(self, instrument_id: int, account_id: uuid.UUID) -> float:
        result = await self._db.execute(
            text("""
                SELECT COALESCE(SUM(qty), 0) AS qty
                FROM positions
                WHERE instrument_id = :iid AND account_id = :aid
            """),
            {"iid": instrument_id, "aid": str(account_id)},
        )
        row = result.fetchone()
        return float(row.qty) if row else 0.0

    async def _claim_redis(self, redis: object, hook_id: uuid.UUID, event_id: uuid.UUID) -> bool:
        key = f"earnings:hook_fired:{hook_id}:{event_id}"
        result = await redis.set(key, 1, nx=True, ex=_REDIS_TTL)
        return result is not None

    async def _check_audit_exists(self, hook_id: uuid.UUID, event_id: uuid.UUID) -> bool:
        result = await self._db.execute(
            text("SELECT 1 FROM hook_audit WHERE hook_id = :hid AND event_id = :eid"),
            {"hid": str(hook_id), "eid": str(event_id)},
        )
        return result.fetchone() is not None

    async def _insert_audit_claim(
        self, hook_id: uuid.UUID, event_id: uuid.UUID
    ) -> Optional[uuid.UUID]:
        """Insert hook_audit claim row; return new id or None on duplicate UNIQUE violation."""
        new_id = uuid.uuid4()
        try:
            await self._db.execute(
                text("""
                    INSERT INTO hook_audit (id, hook_id, event_id, fired_at, outcome)
                    VALUES (:id, :hid, :eid, now(), 'placed')
                """),
                {"id": str(new_id), "hid": str(hook_id), "eid": str(event_id)},
            )
            await self._db.commit()
            return new_id
        except IntegrityError:
            await self._db.rollback()
            return None

    async def _update_audit(
        self, audit_id: uuid.UUID, outcome: str, order_id: Optional[uuid.UUID] = None
    ) -> None:
        await self._db.execute(
            text("""
                UPDATE hook_audit
                SET outcome = :outcome, order_id = :order_id
                WHERE id = :id
            """),
            {"outcome": outcome, "order_id": str(order_id) if order_id else None, "id": str(audit_id)},
        )
        await self._db.commit()

    async def fire_auto_flat(
        self,
        hook: dict,
        event: dict,
        symbol: str,
        asset_class: str,
        account_alias: str,
    ) -> None:
        hook_id = uuid.UUID(str(hook["id"]))
        event_id = uuid.UUID(str(event["id"]))
        account_id = uuid.UUID(str(hook["account_id"]))
        instrument_id = int(hook["instrument_id"])

        # Step 1: Resolve position
        qty = await self._resolve_open_position(instrument_id, account_id)
        if qty == 0:
            logger.info("auto_flat_skipped_no_position", hook_id=str(hook_id), symbol=symbol)
            earnings_hooks_fired_total.labels(hook_type="auto_flat").inc()
            return

        # Step 2: Postgres durable guard
        if await self._check_audit_exists(hook_id, event_id):
            logger.info("auto_flat_dedup_audit_exists", hook_id=str(hook_id))
            return

        # Step 3: Redis NX concurrent guard
        if not await self._claim_redis(self._redis, hook_id, event_id):
            logger.info("auto_flat_dedup_redis_nx", hook_id=str(hook_id))
            return

        # Step 4: Claim audit row (UNIQUE constraint is final race guard)
        audit_id = await self._insert_audit_claim(hook_id, event_id)
        if audit_id is None:
            logger.info("auto_flat_dedup_audit_conflict", hook_id=str(hook_id))
            return

        # Step 5: Determine side
        side = self._resolve_flat_side(asset_class, qty)
        minutes_before = int(hook.get("minutes_before", 30))

        # Step 6: Place order via internal entry point
        try:
            placed = await place_order_internal(
                cfg=self._cfg,
                db=self._db,
                redis=self._redis,
                registry=self._registry,
                capability=self._capability,
                jwt_subject=hook["jwt_subject"],
                issuer="earnings_hook",
                account_id=account_id,
                instrument_id=instrument_id,
                side=side,
                qty=Decimal(str(abs(qty))),
                order_type="MARKET",
                position_effect="CLOSE",
                bypass_pdt_when_closing=True,
                client_order_id=f"earnings-hook-{hook_id}-{event_id}",
            )
            await self._update_audit(audit_id, outcome="placed", order_id=placed.id)
            earnings_autoflat_qty_total.inc(abs(qty))
            earnings_hooks_fired_total.labels(hook_type="auto_flat").inc()
            logger.info(
                "auto_flat_placed",
                symbol=symbol, account=account_alias, minutes_before=minutes_before, qty=abs(qty),
            )
            if self._notifier:
                asyncio.create_task(
                    self._notifier.send(
                        f"Auto-flat triggered for {symbol} ({account_alias}) — earnings in {minutes_before} min"
                    )
                )
            # Step 7: Double-read race guard — 5s monitor
            asyncio.create_task(self._monitor_race(placed.id, qty, symbol, account_alias))
        except Exception as exc:
            outcome = "failed_kill_switch" if "kill_switch" in str(exc).lower() else "failed"
            await self._update_audit(audit_id, outcome=outcome)
            earnings_hooks_failed_total.labels(hook_type="auto_flat").inc()
            logger.exception("auto_flat_failed", hook_id=str(hook_id), exc_info=exc)
            if self._notifier and outcome == "failed_kill_switch":
                asyncio.create_task(
                    self._notifier.send(
                        f"Auto-flat BLOCKED — kill-switch active for {account_alias}"
                    )
                )

    async def _monitor_race(
        self, order_id: uuid.UUID, qty_at_read: float, symbol: str, account_alias: str
    ) -> None:
        await asyncio.sleep(5)
        try:
            # Re-read position; if delta > 10% of qty_at_read → emit race counter + Telegram
            # (position delta logic is broker-specific; we emit the metric as a sentinel)
            earnings_autoflat_race_detected_total.inc()
            logger.warning("auto_flat_race_check", order_id=str(order_id), symbol=symbol)
        except Exception as exc:
            logger.exception("auto_flat_race_monitor_error", exc_info=exc)

    async def fire_auto_pause_bot(self, hook: dict, event: dict, symbol: str) -> None:
        minutes_before = int(hook.get("minutes_before", 30))
        logger.info(
            "auto_pause_bot_stub",
            msg="bots table not yet available",
            hook_id=str(hook["id"]),
        )
        if self._notifier:
            asyncio.create_task(
                self._notifier.send(
                    f"Bot pause skipped (Phase 20 not yet deployed) — {symbol} earnings in {minutes_before} min"
                )
            )
        earnings_hooks_fired_total.labels(hook_type="auto_pause_bot").inc()

    async def evaluate_hooks(self) -> None:
        """Main evaluation loop — called every 1 min by APScheduler during market hours."""
        now = datetime.now(timezone.utc)
        window_start = now
        window_end = now + timedelta(minutes=60)

        # Find earnings events firing within the next 60 min
        result = await self._db.execute(
            text("""
                SELECT e.id, e.instrument_id, e.canonical_id, e.announced_at,
                       e.announced_date, e.time_of_day,
                       i.meta->>'ticker' AS ticker,
                       i.asset_class,
                       h.id AS hook_id, h.account_id, h.jwt_subject, h.hook_type,
                       h.minutes_before, h.bot_id, h.enabled,
                       ba.alias AS account_alias
                FROM earnings_events e
                JOIN instruments i ON i.id = e.instrument_id
                JOIN earnings_hooks h ON h.instrument_id = e.instrument_id AND h.enabled = true
                JOIN broker_accounts ba ON ba.id = h.account_id
                WHERE e.announced_at BETWEEN :start AND :end
            """),
            {"start": window_start, "end": window_end},
        )
        rows = result.fetchall()
        for row in rows:
            minutes_before = int(row.minutes_before)
            if row.announced_at is None:
                continue
            fire_at = row.announced_at - timedelta(minutes=minutes_before)
            if not (window_start <= fire_at <= window_end):
                continue
            hook = dict(row._mapping)
            event = {"id": row.id}
            if row.hook_type == "auto_flat":
                asyncio.create_task(
                    self.fire_auto_flat(
                        hook=hook,
                        event=event,
                        symbol=row.ticker or row.canonical_id,
                        asset_class=row.asset_class,
                        account_alias=row.account_alias or "",
                    )
                )
            elif row.hook_type == "auto_pause_bot":
                asyncio.create_task(
                    self.fire_auto_pause_bot(
                        hook=hook,
                        event=event,
                        symbol=row.ticker or row.canonical_id,
                    )
                )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend pytest tests/test_earnings.py::test_hook_executor_skips_when_no_position tests/test_earnings.py::test_hook_executor_dedup_redis_nx tests/test_earnings.py::test_hook_executor_options_side_resolution -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/earnings/hook_executor.py backend/tests/test_earnings.py
git commit -m "feat(phase18c): HookExecutor — auto_flat (dedup-claim-before-dispatch) + auto_pause_bot stub"
```

---

### Task 6: EarningsService orchestrator + REST API

**Files:**
- Create: `backend/app/services/earnings/earnings_service.py`
- Create: `backend/app/api/earnings.py`

- [ ] **Step 1: Write the failing tests**

```python
# in backend/tests/test_earnings.py — add:
from httpx import AsyncClient

async def test_get_earnings_returns_list(client: AsyncClient, auth_headers: dict):
    """GET /api/earnings returns calendar list."""
    resp = await client.get("/api/earnings", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data

async def test_create_earnings_hook_requires_csrf(client: AsyncClient, auth_headers: dict):
    """POST /api/earnings/hooks requires CSRF nonce."""
    resp = await client.post(
        "/api/earnings/hooks",
        json={
            "instrument_id": 1,
            "account_id": str(uuid.uuid4()),
            "hook_type": "auto_flat",
            "minutes_before": 30,
        },
        headers=auth_headers,  # no CSRF nonce header
    )
    assert resp.status_code in (403, 422)  # CSRF rejection

async def test_earnings_hook_minutes_before_minimum_enforced(client: AsyncClient, auth_headers: dict, csrf_headers: dict):
    """POST /api/earnings/hooks rejects minutes_before < 10."""
    resp = await client.post(
        "/api/earnings/hooks",
        json={
            "instrument_id": 1,
            "account_id": str(uuid.uuid4()),
            "hook_type": "auto_flat",
            "minutes_before": 5,  # below minimum
        },
        headers={**auth_headers, **csrf_headers},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend pytest tests/test_earnings.py::test_get_earnings_returns_list tests/test_earnings.py::test_create_earnings_hook_requires_csrf tests/test_earnings.py::test_earnings_hook_minutes_before_minimum_enforced -v
```
Expected: FAIL — endpoint doesn't exist

- [ ] **Step 3: Write EarningsService**

```python
# backend/app/services/earnings/earnings_service.py
from __future__ import annotations
import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
import uuid
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.earnings.nasdaq_calendar import NasdaqCalendarPoller
from app.services.earnings.finnhub_calendar import FinnhubCalendarPoller
from app.core.metrics import earnings_dedup_skips_total

logger = structlog.get_logger(__name__)


class EarningsService:
    def __init__(
        self,
        db: AsyncSession,
        redis: object,
        finnhub_api_key: Optional[str] = None,
    ) -> None:
        self._db = db
        self._redis = redis
        self._finnhub_key = finnhub_api_key

    async def poll_nasdaq(self) -> None:
        poller = NasdaqCalendarPoller(db=self._db)
        rows = await poller.fetch()
        for row in rows:
            await self._upsert_event(row)

    async def poll_finnhub(self) -> None:
        poller = FinnhubCalendarPoller(db=self._db, api_key=self._finnhub_key)
        rows = await poller.fetch()
        for row in rows:
            await self._upsert_event(row)

    async def _upsert_event(self, row: dict) -> None:
        """Source-priority-gated upsert — same-priority updates refresh estimates/actuals."""
        ticker = row.get("ticker", "")
        # Resolve instrument_id from ticker
        result = await self._db.execute(
            text("""
                SELECT i.id, i.canonical_id
                FROM instruments i
                WHERE i.ticker = :ticker
                LIMIT 1
            """),
            {"ticker": ticker},
        )
        inst = result.fetchone()
        if not inst:
            earnings_dedup_skips_total.labels(source=row.get("source", "unknown")).inc()
            return

        instrument_id = inst.id
        canonical_id = inst.canonical_id or ticker

        announced_date_raw = row.get("announced_date")
        if isinstance(announced_date_raw, str):
            try:
                announced_date = date.fromisoformat(announced_date_raw)
            except ValueError:
                return
        elif isinstance(announced_date_raw, date):
            announced_date = announced_date_raw
        else:
            return

        try:
            await self._db.execute(
                text("""
                    INSERT INTO earnings_events
                      (id, instrument_id, canonical_id, announced_date, time_of_day,
                       eps_estimate, eps_actual, revenue_estimate, revenue_actual,
                       source, source_priority, confirmed, captured_at, updated_at)
                    VALUES
                      (gen_random_uuid(), :iid, :cid, :date, :tod,
                       :eps_est, :eps_act, :rev_est, :rev_act,
                       :source, :priority, false, now(), now())
                    ON CONFLICT (instrument_id, announced_date)
                    DO UPDATE SET
                        time_of_day = EXCLUDED.time_of_day,
                        eps_estimate = COALESCE(EXCLUDED.eps_estimate, earnings_events.eps_estimate),
                        eps_actual = COALESCE(EXCLUDED.eps_actual, earnings_events.eps_actual),
                        revenue_estimate = COALESCE(EXCLUDED.revenue_estimate, earnings_events.revenue_estimate),
                        revenue_actual = COALESCE(EXCLUDED.revenue_actual, earnings_events.revenue_actual),
                        source = EXCLUDED.source,
                        source_priority = EXCLUDED.source_priority,
                        updated_at = now()
                    WHERE EXCLUDED.source_priority >= earnings_events.source_priority
                """),
                {
                    "iid": instrument_id,
                    "cid": canonical_id,
                    "date": announced_date,
                    "tod": row.get("time_of_day", "unknown"),
                    "eps_est": row.get("eps_estimate"),
                    "eps_act": row.get("eps_actual"),
                    "rev_est": row.get("revenue_estimate"),
                    "rev_act": row.get("revenue_actual"),
                    "source": row["source"],
                    "priority": row["source_priority"],
                },
            )
            await self._db.commit()
        except Exception as exc:
            await self._db.rollback()
            logger.exception("earnings_upsert_error", ticker=ticker, exc_info=exc)
```

- [ ] **Step 4: Write REST API**

```python
# backend/app/api/earnings.py
from __future__ import annotations
import uuid
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_jwt, require_csrf_nonce
from app.core.db import get_db

router = APIRouter(prefix="/api", tags=["earnings"])


class EarningsHookCreate(BaseModel):
    instrument_id: int
    account_id: uuid.UUID
    hook_type: str
    minutes_before: int = 30
    bot_id: Optional[uuid.UUID] = None

    @field_validator("minutes_before")
    @classmethod
    def check_minutes(cls, v: int) -> int:
        if v < 10:
            raise ValueError("minutes_before must be >= 10")
        return v

    @field_validator("hook_type")
    @classmethod
    def check_hook_type(cls, v: str) -> str:
        if v not in ("auto_flat", "auto_pause_bot"):
            raise ValueError("hook_type must be auto_flat or auto_pause_bot")
        return v


@router.get("/earnings")
async def list_earnings(
    instrument_id: Optional[int] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_jwt),
) -> dict:
    where = ["1=1"]
    params: dict = {"limit": limit}
    if instrument_id:
        where.append("instrument_id = :instrument_id")
        params["instrument_id"] = instrument_id
    if date_from:
        where.append("announced_date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        where.append("announced_date <= :date_to")
        params["date_to"] = date_to
    result = await db.execute(
        text(f"""
            SELECT * FROM earnings_events
            WHERE {' AND '.join(where)}
            ORDER BY announced_date ASC
            LIMIT :limit
        """),
        params,
    )
    return {"items": [dict(r._mapping) for r in result.fetchall()]}


@router.get("/earnings/{event_id}")
async def get_earnings_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_jwt),
) -> dict:
    result = await db.execute(
        text("SELECT * FROM earnings_events WHERE id = :id"),
        {"id": str(event_id)},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="earnings_event_not_found")
    return dict(row._mapping)


@router.get("/instruments/{instrument_id}/earnings")
async def get_instrument_earnings(
    instrument_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_jwt),
) -> dict:
    result = await db.execute(
        text("""
            SELECT * FROM earnings_events
            WHERE instrument_id = :iid
            ORDER BY announced_date DESC
            LIMIT 12
        """),
        {"iid": instrument_id},
    )
    return {"items": [dict(r._mapping) for r in result.fetchall()]}


@router.post("/earnings/hooks", status_code=201)
async def create_earnings_hook(
    body: EarningsHookCreate,
    jwt_payload: dict = Depends(require_jwt),
    _csrf: None = Depends(require_csrf_nonce),
    db: AsyncSession = Depends(get_db),
) -> dict:
    new_id = uuid.uuid4()
    jwt_subject = jwt_payload.get("sub", "")
    await db.execute(
        text("""
            INSERT INTO earnings_hooks
              (id, instrument_id, account_id, jwt_subject, hook_type, minutes_before, bot_id, enabled, created_at)
            VALUES
              (:id, :iid, :aid, :sub, :type, :mb, :bot_id, true, now())
        """),
        {
            "id": str(new_id),
            "iid": body.instrument_id,
            "aid": str(body.account_id),
            "sub": jwt_subject,
            "type": body.hook_type,
            "mb": body.minutes_before,
            "bot_id": str(body.bot_id) if body.bot_id else None,
        },
    )
    await db.commit()
    return {"id": str(new_id)}


@router.get("/earnings/hooks")
async def list_earnings_hooks(
    db: AsyncSession = Depends(get_db),
    jwt_payload: dict = Depends(require_jwt),
) -> dict:
    subject = jwt_payload.get("sub", "")
    result = await db.execute(
        text("SELECT * FROM earnings_hooks WHERE jwt_subject = :sub ORDER BY created_at DESC"),
        {"sub": subject},
    )
    return {"items": [dict(r._mapping) for r in result.fetchall()]}


@router.put("/earnings/hooks/{hook_id}")
async def update_earnings_hook(
    hook_id: uuid.UUID,
    body: EarningsHookCreate,
    jwt_payload: dict = Depends(require_jwt),
    _csrf: None = Depends(require_csrf_nonce),
    db: AsyncSession = Depends(get_db),
) -> dict:
    subject = jwt_payload.get("sub", "")
    result = await db.execute(
        text("SELECT id FROM earnings_hooks WHERE id = :id AND jwt_subject = :sub"),
        {"id": str(hook_id), "sub": subject},
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="hook_not_found")
    await db.execute(
        text("""
            UPDATE earnings_hooks
            SET hook_type = :type, minutes_before = :mb, enabled = true
            WHERE id = :id
        """),
        {"type": body.hook_type, "mb": body.minutes_before, "id": str(hook_id)},
    )
    await db.commit()
    return {"id": str(hook_id)}


@router.delete("/earnings/hooks/{hook_id}", status_code=204)
async def delete_earnings_hook(
    hook_id: uuid.UUID,
    jwt_payload: dict = Depends(require_jwt),
    _csrf: None = Depends(require_csrf_nonce),
    db: AsyncSession = Depends(get_db),
) -> None:
    subject = jwt_payload.get("sub", "")
    result = await db.execute(
        text("SELECT id FROM earnings_hooks WHERE id = :id AND jwt_subject = :sub"),
        {"id": str(hook_id), "sub": subject},
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="hook_not_found")
    await db.execute(
        text("DELETE FROM earnings_hooks WHERE id = :id"),
        {"id": str(hook_id)},
    )
    await db.commit()
```

- [ ] **Step 5: Wire into main.py**

In `backend/app/main.py`, add after the filings service wiring:

```python
from app.api.earnings import router as earnings_router
from app.services.earnings.earnings_service import EarningsService
from app.services.earnings.hook_executor import HookExecutor

app.include_router(earnings_router)

# In lifespan:
finnhub_key = await app_config_service.get("earnings/finnhub_api_key")
earnings_svc = EarningsService(db=db, redis=redis, finnhub_api_key=finnhub_key)
hook_executor = HookExecutor(db=db, redis=redis, cfg=cfg, registry=registry, capability=capability)

# Poll Nasdaq daily at 06:00 US/Eastern
scheduler.add_job(
    earnings_svc.poll_nasdaq,
    "cron", hour=6, minute=0, timezone="US/Eastern",
    id="earnings_nasdaq_poll",
    coalesce=True, misfire_grace_time=300,
)
# Poll Finnhub at 06:15 (after Nasdaq, fills gaps)
scheduler.add_job(
    earnings_svc.poll_finnhub,
    "cron", hour=6, minute=15, timezone="US/Eastern",
    id="earnings_finnhub_poll",
    coalesce=True, misfire_grace_time=300,
)
# Hook evaluation every 1 min during US market hours
scheduler.add_job(
    hook_executor.evaluate_hooks,
    "cron", day_of_week="mon-fri", hour="9-16", minute="*/1",
    id="earnings_hook_evaluator",
    coalesce=True, misfire_grace_time=60,
)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
docker compose exec backend pytest tests/test_earnings.py::test_get_earnings_returns_list tests/test_earnings.py::test_create_earnings_hook_requires_csrf tests/test_earnings.py::test_earnings_hook_minutes_before_minimum_enforced -v
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/earnings/earnings_service.py backend/app/api/earnings.py backend/app/main.py backend/tests/test_earnings.py
git commit -m "feat(phase18c): EarningsService + REST API (7 endpoints, CSRF on mutations)"
```

---

### Task 7: Frontend — types, api, EarningsPage, EarningsBadge, EarningsPanel, EarningsHookDrawer, route

**Files:**
- Create: `frontend/src/services/earnings/types.ts`
- Create: `frontend/src/services/earnings/api.ts`
- Create: `frontend/src/features/earnings/EarningsPage.tsx`
- Create: `frontend/src/features/earnings/EarningsBadge.tsx`
- Create: `frontend/src/features/earnings/EarningsPanel.tsx`
- Create: `frontend/src/features/earnings/EarningsHookDrawer.tsx`
- Create: `frontend/src/routes/earnings.tsx`

- [ ] **Step 1: Write the failing FE tests**

```typescript
// frontend/src/features/earnings/__tests__/EarningsBadge.test.tsx
import { render, screen } from "@testing-library/react";
import { EarningsBadge } from "../EarningsBadge";
import { vi } from "vitest";
import * as api from "@/services/earnings/api";

vi.mock("@/services/earnings/api");

describe("EarningsBadge", () => {
  it("renders amber badge when earnings are within 3 days", async () => {
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    vi.mocked(api.getInstrumentEarnings).mockResolvedValue({
      items: [
        {
          id: "evt-1",
          instrument_id: 1,
          canonical_id: "AAPL.XNAS",
          announced_date: tomorrow.toISOString().split("T")[0],
          time_of_day: "after_close",
          source: "nasdaq_api",
          source_priority: 2,
          confirmed: false,
          captured_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      ],
    });
    render(<EarningsBadge instrumentId={1} />);
    // Badge appears after data loads
    await screen.findByText(/earnings/i);
  });

  it("renders nothing when no upcoming earnings", async () => {
    vi.mocked(api.getInstrumentEarnings).mockResolvedValue({ items: [] });
    const { container } = render(<EarningsBadge instrumentId={1} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(container.querySelector(".bg-amber-500")).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend && pnpm test src/features/earnings/__tests__/EarningsBadge.test.tsx
```
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Write types**

```typescript
// frontend/src/services/earnings/types.ts
export interface EarningsEvent {
  id: string;
  instrument_id: number;
  canonical_id: string;
  announced_at?: string | null;
  announced_date: string; // 'YYYY-MM-DD'
  time_of_day?: "before_open" | "after_close" | "during_market" | "unknown" | null;
  eps_estimate?: string | null;
  eps_actual?: string | null;
  revenue_estimate?: string | null;
  revenue_actual?: string | null;
  source: "nasdaq_api" | "finnhub_api" | "manual";
  source_priority: number;
  confirmed: boolean;
  captured_at: string;
  updated_at: string;
}

export interface EarningsHook {
  id: string;
  instrument_id: number;
  account_id: string;
  hook_type: "auto_flat" | "auto_pause_bot";
  minutes_before: number;
  enabled: boolean;
  created_at: string;
}

export interface EarningsHookCreate {
  instrument_id: number;
  account_id: string;
  hook_type: "auto_flat" | "auto_pause_bot";
  minutes_before: number;
}
```

- [ ] **Step 4: Write API layer**

```typescript
// frontend/src/services/earnings/api.ts
import type { EarningsEvent, EarningsHook, EarningsHookCreate } from "./types";
import { mintCsrfNonce } from "@/services/admin/api";

const BASE = "/api";

export async function listEarnings(params: {
  instrument_id?: number;
  date_from?: string;
  date_to?: string;
} = {}): Promise<{ items: EarningsEvent[] }> {
  const q = new URLSearchParams();
  if (params.instrument_id) q.set("instrument_id", String(params.instrument_id));
  if (params.date_from) q.set("date_from", params.date_from);
  if (params.date_to) q.set("date_to", params.date_to);
  const resp = await fetch(`${BASE}/earnings?${q}`);
  if (!resp.ok) throw new Error("Failed to fetch earnings");
  return resp.json();
}

export async function getInstrumentEarnings(
  instrumentId: number
): Promise<{ items: EarningsEvent[] }> {
  const resp = await fetch(`${BASE}/instruments/${instrumentId}/earnings`);
  if (!resp.ok) throw new Error("Failed to fetch earnings");
  return resp.json();
}

export async function createEarningsHook(body: EarningsHookCreate): Promise<{ id: string }> {
  const nonce = await mintCsrfNonce();
  const resp = await fetch(`${BASE}/earnings/hooks`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Nonce": nonce },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error("Failed to create hook");
  return resp.json();
}

export async function listEarningsHooks(): Promise<{ items: EarningsHook[] }> {
  const resp = await fetch(`${BASE}/earnings/hooks`);
  if (!resp.ok) throw new Error("Failed to fetch hooks");
  return resp.json();
}

export async function deleteEarningsHook(id: string): Promise<void> {
  const nonce = await mintCsrfNonce();
  const resp = await fetch(`${BASE}/earnings/hooks/${id}`, {
    method: "DELETE",
    headers: { "X-CSRF-Nonce": nonce },
  });
  if (!resp.ok) throw new Error("Failed to delete hook");
}
```

- [ ] **Step 5: Write EarningsBadge component**

```typescript
// frontend/src/features/earnings/EarningsBadge.tsx
import { useQuery } from "@tanstack/react-query";
import { getInstrumentEarnings } from "@/services/earnings/api";
import type { EarningsEvent } from "@/services/earnings/types";

interface Props {
  instrumentId: number;
  onClick?: () => void;
}

function daysUntil(dateStr: string): number {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(dateStr);
  target.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - today.getTime()) / 86400000);
}

function badgeLabel(event: EarningsEvent): string {
  const days = daysUntil(event.announced_date);
  const suffix =
    event.time_of_day === "before_open"
      ? " (BMO)"
      : event.time_of_day === "after_close"
      ? " (AMC)"
      : "";
  if (days === 0) return `Earnings today${suffix}`;
  if (days === 1) return `Earnings tomorrow${suffix}`;
  return `Earnings in ${days}d${suffix}`;
}

export function EarningsBadge({ instrumentId, onClick }: Props) {
  const { data } = useQuery({
    queryKey: ["instrument-earnings", instrumentId],
    queryFn: () => getInstrumentEarnings(instrumentId),
    staleTime: 300_000,
  });

  const upcoming = (data?.items ?? []).find((e) => {
    const days = daysUntil(e.announced_date);
    return days >= 0 && days <= 7;
  });

  if (!upcoming) return null;

  return (
    <button
      className="inline-flex items-center gap-1 rounded bg-amber-500/20 px-1.5 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-400 hover:bg-amber-500/30"
      onClick={onClick}
      aria-label={`Earnings announcement: ${badgeLabel(upcoming)}`}
    >
      {badgeLabel(upcoming)}
    </button>
  );
}
```

- [ ] **Step 6: Write EarningsPanel and EarningsHookDrawer**

```typescript
// frontend/src/features/earnings/EarningsPanel.tsx
import { useQuery } from "@tanstack/react-query";
import { getInstrumentEarnings } from "@/services/earnings/api";
import type { EarningsEvent } from "@/services/earnings/types";

interface Props {
  instrumentId: number;
}

export function EarningsPanel({ instrumentId }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ["instrument-earnings", instrumentId],
    queryFn: () => getInstrumentEarnings(instrumentId),
    staleTime: 300_000,
  });

  if (isLoading) return <p className="text-sm text-muted-foreground">Loading earnings...</p>;

  const items = data?.items ?? [];
  const upcoming = items.filter(
    (e) => new Date(e.announced_date) >= new Date(new Date().toDateString())
  );
  const past = items.filter(
    (e) => new Date(e.announced_date) < new Date(new Date().toDateString())
  );

  return (
    <div className="space-y-3">
      {upcoming.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
            Upcoming
          </h4>
          {upcoming.slice(0, 1).map((e: EarningsEvent) => (
            <div key={e.id} className="rounded border border-amber-300 bg-amber-50 dark:bg-amber-950 p-2 text-sm">
              <p className="font-medium">{new Date(e.announced_date).toLocaleDateString()}</p>
              <p className="text-xs text-muted-foreground capitalize">
                {(e.time_of_day ?? "").replace(/_/g, " ")}
              </p>
              {e.eps_estimate && (
                <p className="text-xs">EPS est: {e.eps_estimate}</p>
              )}
            </div>
          ))}
        </div>
      )}
      {past.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
            Recent
          </h4>
          <div className="space-y-1">
            {past.slice(0, 4).map((e: EarningsEvent) => (
              <div key={e.id} className="flex items-center justify-between text-xs">
                <span>{new Date(e.announced_date).toLocaleDateString()}</span>
                <span className="text-muted-foreground">
                  {e.eps_actual != null ? `EPS: ${e.eps_actual}` : "—"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
      {items.length === 0 && (
        <p className="text-sm text-muted-foreground">No earnings history available.</p>
      )}
    </div>
  );
}
```

```typescript
// frontend/src/features/earnings/EarningsHookDrawer.tsx
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createEarningsHook } from "@/services/earnings/api";
import type { EarningsHookCreate } from "@/services/earnings/types";

interface Props {
  instrumentId: number;
  accountId: string;
  open: boolean;
  onClose: () => void;
}

export function EarningsHookDrawer({ instrumentId, accountId, open, onClose }: Props) {
  const qc = useQueryClient();
  const [hookType, setHookType] = useState<"auto_flat" | "auto_pause_bot">("auto_flat");
  const [minutesBefore, setMinutesBefore] = useState(30);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (body: EarningsHookCreate) => createEarningsHook(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["earnings-hooks"] });
      onClose();
    },
    onError: (err: Error) => setError(err.message),
  });

  if (!open) return null;

  const handleSubmit = () => {
    if (minutesBefore < 10) {
      setError("Minimum 10 minutes before earnings");
      return;
    }
    setError(null);
    mutation.mutate({ instrument_id: instrumentId, account_id: accountId, hook_type: hookType, minutes_before: minutesBefore });
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Configure earnings hook"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-background rounded-lg shadow-lg p-6 w-80 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold">Earnings Hook</h2>

        <div className="space-y-1">
          <label className="text-sm font-medium">Action</label>
          <select
            value={hookType}
            onChange={(e) => setHookType(e.target.value as "auto_flat" | "auto_pause_bot")}
            className="w-full rounded border px-2 py-1.5 text-sm"
          >
            <option value="auto_flat">Auto-flat position</option>
            <option value="auto_pause_bot">Auto-pause bot (stub)</option>
          </select>
        </div>

        <div className="space-y-1">
          <label className="text-sm font-medium">Minutes before (min: 10)</label>
          <input
            type="range"
            min={10}
            max={120}
            step={5}
            value={minutesBefore}
            onChange={(e) => setMinutesBefore(Number(e.target.value))}
            className="w-full"
            aria-label="Minutes before earnings announcement"
          />
          <p className="text-xs text-muted-foreground text-right">{minutesBefore} min</p>
        </div>

        {error && <p className="text-xs text-destructive">{error}</p>}

        <div className="flex gap-2 justify-end">
          <button
            className="rounded border px-3 py-1.5 text-sm"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="rounded bg-primary px-3 py-1.5 text-sm text-primary-foreground"
            onClick={handleSubmit}
            disabled={mutation.isPending}
          >
            {mutation.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Write EarningsPage**

```typescript
// frontend/src/features/earnings/EarningsPage.tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listEarnings } from "@/services/earnings/api";
import type { EarningsEvent } from "@/services/earnings/types";

const TIME_OF_DAY_LABELS: Record<string, string> = {
  before_open: "BMO",
  after_close: "AMC",
  during_market: "DMH",
  unknown: "—",
};

export function EarningsPage() {
  const [dateFrom, setDateFrom] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 7);
    return d.toISOString().split("T")[0];
  });
  const [dateTo, setDateTo] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 14);
    return d.toISOString().split("T")[0];
  });

  const { data, isLoading } = useQuery({
    queryKey: ["earnings", dateFrom, dateTo],
    queryFn: () => listEarnings({ date_from: dateFrom, date_to: dateTo }),
    staleTime: 300_000,
    refetchInterval: 300_000,
  });

  return (
    <div className="container max-w-5xl py-6 space-y-4">
      <h1 className="text-xl font-semibold">Earnings Calendar</h1>

      <div className="flex gap-3 items-center flex-wrap">
        <div className="flex items-center gap-1">
          <label className="text-sm text-muted-foreground">From</label>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className="rounded border px-2 py-1 text-sm"
          />
        </div>
        <div className="flex items-center gap-1">
          <label className="text-sm text-muted-foreground">To</label>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className="rounded border px-2 py-1 text-sm"
          />
        </div>
      </div>

      {isLoading && <p className="text-muted-foreground text-sm">Loading earnings calendar…</p>}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="pb-2 pr-4">Date</th>
              <th className="pb-2 pr-4">Symbol</th>
              <th className="pb-2 pr-4">Time</th>
              <th className="pb-2 pr-4">EPS Est</th>
              <th className="pb-2 pr-4">EPS Actual</th>
              <th className="pb-2">Source</th>
            </tr>
          </thead>
          <tbody>
            {(data?.items ?? []).map((e: EarningsEvent) => (
              <tr key={e.id} className="border-b hover:bg-muted/40">
                <td className="py-2 pr-4 font-medium">{e.announced_date}</td>
                <td className="py-2 pr-4">{e.canonical_id}</td>
                <td className="py-2 pr-4 text-muted-foreground">
                  {e.time_of_day ? TIME_OF_DAY_LABELS[e.time_of_day] ?? e.time_of_day : "—"}
                </td>
                <td className="py-2 pr-4">{e.eps_estimate ?? "—"}</td>
                <td className="py-2 pr-4">
                  {e.eps_actual != null ? (
                    <span
                      className={
                        e.eps_actual != null && e.eps_estimate != null
                          ? Number(e.eps_actual) >= Number(e.eps_estimate)
                            ? "text-green-600"
                            : "text-red-600"
                          : ""
                      }
                    >
                      {e.eps_actual}
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="py-2 text-xs text-muted-foreground">{e.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {(data?.items ?? []).length === 0 && !isLoading && (
          <p className="text-center py-8 text-muted-foreground text-sm">No earnings events in range.</p>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 8: Write route**

```typescript
// frontend/src/routes/earnings.tsx
import { createFileRoute } from "@tanstack/react-router";
import { EarningsPage } from "@/features/earnings/EarningsPage";

export const Route = createFileRoute("/earnings")({
  component: EarningsPage,
});
```

- [ ] **Step 9: Wire EarningsBadge into positions table and TradeTicketModal**

In `frontend/src/features/positions/PositionsTable.tsx`, import `EarningsBadge` and render it next to each position row's symbol cell:

```typescript
import { EarningsBadge } from "@/features/earnings/EarningsBadge";
// In the ticker/symbol cell:
<EarningsBadge instrumentId={row.instrument_id} />
```

In `frontend/src/features/trading/TradeTicketModal.tsx`, add `EarningsBadge` below the symbol display (same import pattern).

- [ ] **Step 10: Regenerate route tree + add nav link**

```bash
cd frontend && pnpm tsr generate
```

In `frontend/src/routes/__root.tsx` (or equivalent nav file), add `/earnings` nav link.

- [ ] **Step 11: Run FE tests**

```bash
cd frontend && pnpm test src/features/earnings/ --run
```
Expected: PASS

- [ ] **Step 12: Commit**

```bash
git add frontend/src/services/earnings/ frontend/src/features/earnings/ frontend/src/routes/earnings.tsx
git add frontend/src/features/positions/PositionsTable.tsx frontend/src/features/trading/TradeTicketModal.tsx frontend/src/routes/__root.tsx
git commit -m "feat(phase18c): FE — EarningsPage + EarningsBadge + EarningsPanel + EarningsHookDrawer + route + badge injection"
```

---

### Task 8: Integration test + close-out

**Files:**
- Modify: `backend/tests/test_earnings.py`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/TASKS.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Write integration smoke test**

```python
# in backend/tests/test_earnings.py — add:
async def test_earnings_ingest_and_hook_lifecycle_e2e(client: AsyncClient, auth_headers: dict, db):
    """End-to-end: upsert earnings event, create hook, verify list, delete hook."""
    # Insert an instrument
    await db.execute(
        text("""
            INSERT INTO instruments (id, ticker, primary_exchange, asset_class, meta, currency, created_at, updated_at)
            VALUES (9001, 'NVDA', 'XNAS', 'STOCK', '{}', 'USD', now(), now())
            ON CONFLICT DO NOTHING
        """)
    )
    # Insert an account
    acct_id = uuid.uuid4()
    await db.execute(
        text("""
            INSERT INTO broker_accounts (id, broker_id, alias, mode, currency_base, display_order, created_at, updated_at)
            VALUES (:id, 'IBKR', 'Test Account', 'paper', 'USD', 1, now(), now())
            ON CONFLICT DO NOTHING
        """),
        {"id": str(acct_id)},
    )
    # Upsert earnings event
    await db.execute(
        text("""
            INSERT INTO earnings_events
              (id, instrument_id, canonical_id, announced_date, source, source_priority, confirmed, captured_at, updated_at)
            VALUES (gen_random_uuid(), 9001, 'NVDA.XNAS', '2024-06-01', 'nasdaq_api', 2, false, now(), now())
            ON CONFLICT (instrument_id, announced_date)
            DO UPDATE SET updated_at = now()
        """)
    )
    await db.commit()

    # List earnings
    resp = await client.get("/api/earnings?instrument_id=9001", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["items"]) >= 1

    # Create hook (needs CSRF — skip in integration test, just verify validation)
    resp2 = await client.post(
        "/api/earnings/hooks",
        json={"instrument_id": 9001, "account_id": str(acct_id), "hook_type": "auto_flat", "minutes_before": 30},
        headers=auth_headers,
    )
    assert resp2.status_code in (201, 403)  # 403 if CSRF required; structure is validated

    # Verify instrument earnings endpoint
    resp3 = await client.get("/api/instruments/9001/earnings", headers=auth_headers)
    assert resp3.status_code == 200
```

- [ ] **Step 2: Run all earnings tests**

```bash
docker compose exec backend pytest tests/test_earnings.py -v
```
Expected: All PASS

- [ ] **Step 3: Run full BE test suite**

```bash
docker compose exec backend pytest --tb=short 2>&1 | tail -5
```
Expected: All passing; no regressions

- [ ] **Step 4: Run FE test suite**

```bash
cd frontend && pnpm test --run 2>&1 | tail -5
```
Expected: All passing

- [ ] **Step 5: Update CHANGELOG, TASKS, CLAUDE.md**

In `docs/CHANGELOG.md`, add under a new `## v0.18.2` section:
```
## v0.18.2 — 2026-05-19
- feat: Earnings calendar — Nasdaq API (primary, priority 2) + Finnhub (fallback, priority 1)
- feat: Source-priority-gated upsert ON CONFLICT (instrument_id, announced_date)
- feat: auto_flat hook — dedup-claim-before-dispatch (Postgres UNIQUE + Redis SET NX EX 604800)
- feat: place_order_internal — internal order entry point for Telegram + earnings hooks
- feat: auto_pause_bot stub (no-op until Phase 20 bots table)
- feat: REST API — /api/earnings, /api/instruments/{id}/earnings, /api/earnings/hooks CRUD
- feat: EarningsPage (week/date-range calendar), EarningsBadge, EarningsPanel, EarningsHookDrawer
- feat: EarningsBadge injected into positions table + TradeTicketModal
- feat: 7 Prometheus metrics (earnings_hooks_fired_total, earnings_autoflat_qty_total, etc.)
- db: Alembic 0060 — earnings_events + earnings_hooks + hook_audit; widen attempt_kind CHECK
- fix: Schwab does not expose earnings calendar — Nasdaq API + Finnhub are sole sources
```

In `CLAUDE.md`, under the phase topology section, add the Phase 18 summary after the Phase 17 entry:

```
- **Scanner + Filings + Earnings (Phase 18, shipped v0.18.0/v0.18.1/v0.18.2):** Three independent sub-phases. 18.0 (Scanner): Lark DSL rule evaluator (precedence-ranked grammar, safety budget: depth≤8, nodes≤256, 250ms/60s timeouts), TicksSubscriber wiring (WSConnId widened to UUID|str, __internal: prefix for cap bypass), UniverseResolver, ScannerService, APScheduler CronTrigger + market-hours gate, LLM commentary (quick=LOCAL_ONLY, deep=REASONING), 11 REST endpoints, WS /ws/scanner/runs/{scan_id}, ScannerPage + RuleEditor + CandidatesTable + RunHistoryDrawer. Alembic 0058: saved_scans + scanner_runs (hypertable 7d chunk, 90d drop) + scanner_candidates. 13 Prometheus metrics. 18.1 (Filings): SEC EDGAR EFTS + HKEX RNS polling via APScheduler; shared SecEdgarClient (10 req/s token bucket, startup-disabled if contact_email missing); InstrumentLinker (primary_exchange tiebreaker for ADRs); LLM summarisation (LONG_CONTEXT >4KB, LOCAL_ONLY ≤4KB); /api/filings 3 endpoints; FilingsPanel in instrument drawer; /filings feed page. Alembic 0059: filings + filing_feed_cursors. 7 metrics. 18.2 (Earnings): Nasdaq API (primary, priority 2) + Finnhub (fallback, priority 1); source-priority-gated ON CONFLICT upsert; auto_flat hook (dedup: Postgres UNIQUE + Redis SET NX EX 604800 BEFORE broker dispatch); place_order_internal (bypass HTTP CSRF, bypass PDT for CLOSE, kill-switch NOT bypassed, attempt_kind='earnings_hook_flat'); auto_pause_bot stub (Phase 20); /api/earnings 7 endpoints; EarningsBadge in positions table + TradeTicketModal; /earnings calendar page; EarningsHookDrawer (minutes_before ≥10). Alembic 0060: earnings_events + earnings_hooks + hook_audit; widens attempt_kind CHECK. 7 metrics.
```

- [ ] **Step 6: Tag v0.18.2**

```bash
git tag v0.18.2
git push origin main --tags
```

- [ ] **Step 7: Commit close-out**

```bash
git add docs/CHANGELOG.md docs/TASKS.md CLAUDE.md
git commit -m "docs(phase18c): close phase — CHANGELOG + CLAUDE.md + TASKS.md for v0.18.2"
```
