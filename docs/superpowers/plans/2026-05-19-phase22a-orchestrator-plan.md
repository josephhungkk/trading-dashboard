# Phase 22a — BotOrchestrator + Auto-Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 22a: PortfolioExposureGate (pre-trade station 5.75), CorrelationService, AutoPromoteEvaluator (replace always-False stub), and NightlyRetrainJob — shipping v0.22.0.

**Architecture:** New `app/services/orchestrator/` package owns ExposureGate, CorrelationService, AutoPromoteEvaluator, NightlyRetrainJob, and metrics. A shared `app/services/fx.py` module promotes `_fx_rate` from `orders_service`. Alembic 0069 adds three schema changes: `portfolio_exposure_limits`, `portfolio_correlation_snapshots`, and `shadow_promotion_events.promoted_via` + idempotency index. REST API lives in `app/api/orchestrator.py`.

**Tech Stack:** Python 3.14 · FastAPI · SQLAlchemy 2.0 async · Alembic · Pydantic v2 · asyncpg · APScheduler · Redis Lua HINCRBYFLOAT · pytest-asyncio

---

## File Map

**New files:**
- `backend/alembic/versions/0069_phase22a_orchestrator.py` — schema migration
- `backend/app/services/fx.py` — `get_fx_rate(currency, redis) -> Decimal`
- `backend/app/services/orchestrator/__init__.py`
- `backend/app/services/orchestrator/exposure_gate.py` — `PortfolioExposureGate`
- `backend/app/services/orchestrator/exposure_gate_lua.py` — Lua script constant + helper
- `backend/app/services/orchestrator/correlation.py` — `CorrelationService`
- `backend/app/services/orchestrator/auto_promote.py` — `AutoPromoteEvaluator` + `AutoPromoteCriteria`
- `backend/app/services/orchestrator/retrain.py` — `NightlyRetrainJob`
- `backend/app/services/orchestrator/metrics.py` — Prometheus counters/histograms/gauges
- `backend/app/api/orchestrator.py` — REST router
- `backend/tests/services/orchestrator/__init__.py`
- `backend/tests/services/orchestrator/test_exposure_gate.py`
- `backend/tests/services/orchestrator/test_correlation.py`
- `backend/tests/services/orchestrator/test_auto_promote.py`
- `backend/tests/services/orchestrator/test_retrain.py`
- `backend/tests/api/test_orchestrator.py`
- `backend/tests/alembic/test_0069.py`

**Modified files:**
- `backend/app/services/orders_service.py` — update `_fx_rate` import to internal use only; no change to behaviour, but mark as deprecated in favour of `app.services.fx`
- `backend/app/services/position_sizing_service.py:34` — update import from `orders_service._fx_rate` to `app.services.fx.get_fx_rate`
- `backend/app/bot/context.py:135` — insert `PortfolioExposureGate.check()` at station 5.75 (after risk_cap_svc, before advisor)
- `backend/app/bot/fill_router.py` — call Lua exposure update on every `order:fill` event
- `backend/app/main.py` — wire APScheduler jobs (correlation daily + retrain nightly 02:00)
- `backend/app/api/__init__.py` or router registration file — register `app/api/orchestrator.py`

---

## Task A — Alembic 0069 (Schema)

**Route:** Qwen

**Files:**
- Create: `backend/alembic/versions/0069_phase22a_orchestrator.py`
- Create: `backend/tests/alembic/test_0069.py`

- [ ] **Step 1: Write the failing migration test**

```python
# backend/tests/alembic/test_0069.py
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_portfolio_exposure_limits_table(db: AsyncSession) -> None:
    await db.execute(text("SELECT 1 FROM portfolio_exposure_limits LIMIT 0"))


@pytest.mark.asyncio
async def test_portfolio_exposure_limits_unique_total(db: AsyncSession) -> None:
    """Partial unique index prevents two total_notional rows for same account."""
    await db.execute(
        text(
            "INSERT INTO broker_accounts (id, broker_id, account_number, alias, mode, currency_base)"
            " VALUES (gen_random_uuid(), 'ibkr', 'TEST001', 'test', 'paper', 'USD')"
        )
    )
    result = await db.execute(text("SELECT id FROM broker_accounts WHERE account_number='TEST001'"))
    acct_id = result.scalar_one()
    await db.execute(
        text(
            "INSERT INTO portfolio_exposure_limits (account_id, limit_type, max_notional, currency)"
            " VALUES (:aid, 'total_notional', 100000, 'USD')"
        ),
        {"aid": acct_id},
    )
    with pytest.raises(Exception, match="uq_portfolio_exposure_total"):
        await db.execute(
            text(
                "INSERT INTO portfolio_exposure_limits (account_id, limit_type, max_notional, currency)"
                " VALUES (:aid, 'total_notional', 200000, 'USD')"
            ),
            {"aid": acct_id},
        )


@pytest.mark.asyncio
async def test_portfolio_correlation_snapshots_table(db: AsyncSession) -> None:
    await db.execute(text("SELECT 1 FROM portfolio_correlation_snapshots LIMIT 0"))


@pytest.mark.asyncio
async def test_shadow_promotion_events_promoted_via_column(db: AsyncSession) -> None:
    result = await db.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='shadow_promotion_events' AND column_name='promoted_via'"
        )
    )
    assert result.scalar_one_or_none() == "promoted_via"


@pytest.mark.asyncio
async def test_uq_shadow_promotion_success_index(db: AsyncSession) -> None:
    result = await db.execute(
        text(
            "SELECT indexname FROM pg_indexes"
            " WHERE tablename='shadow_promotion_events'"
            " AND indexname='uq_shadow_promotion_success'"
        )
    )
    assert result.scalar_one_or_none() == "uq_shadow_promotion_success"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/alembic/test_0069.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: `FAILED` — tables don't exist yet.

- [ ] **Step 3: Write the Alembic migration**

```python
# backend/alembic/versions/0069_phase22a_orchestrator.py
"""Phase 22a — portfolio exposure limits + correlation snapshots + shadow promoted_via

Revision ID: 0069
Down Revision: 0068
"""
from __future__ import annotations
from alembic import op
from sqlalchemy import text

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # portfolio_exposure_limits
    op.execute(text("""
        CREATE TABLE portfolio_exposure_limits (
            id              BIGSERIAL PRIMARY KEY,
            account_id      UUID REFERENCES broker_accounts(id) ON DELETE CASCADE,
            limit_type      TEXT NOT NULL
                CHECK (limit_type IN ('total_notional','per_instrument')),
            instrument_id   BIGINT REFERENCES instruments(id) ON DELETE CASCADE,
            max_notional    NUMERIC(20,8) NOT NULL,
            currency        TEXT NOT NULL DEFAULT 'USD',
            enabled         BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(text(
        "CREATE UNIQUE INDEX uq_portfolio_exposure_total"
        " ON portfolio_exposure_limits(account_id)"
        " WHERE limit_type = 'total_notional'"
    ))
    op.execute(text(
        "CREATE UNIQUE INDEX uq_portfolio_exposure_instr"
        " ON portfolio_exposure_limits(account_id, instrument_id)"
        " WHERE limit_type = 'per_instrument'"
    ))

    # portfolio_correlation_snapshots
    op.execute(text("""
        CREATE TABLE portfolio_correlation_snapshots (
            id              BIGSERIAL PRIMARY KEY,
            account_id      UUID REFERENCES broker_accounts(id) ON DELETE CASCADE,
            computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            instrument_ids  BIGINT[] NOT NULL,
            matrix_json     JSONB NOT NULL,
            window_days     INT NOT NULL DEFAULT 30
        )
    """))
    op.execute(text(
        "CREATE INDEX portfolio_correlation_snapshots_account_computed_idx"
        " ON portfolio_correlation_snapshots (account_id, computed_at DESC)"
    ))

    # shadow_promotion_events: add promoted_via + idempotency index
    op.execute(text(
        "ALTER TABLE shadow_promotion_events"
        " ADD COLUMN promoted_via TEXT CHECK (promoted_via IN ('manual','auto'))"
    ))
    op.execute(text(
        "CREATE UNIQUE INDEX uq_shadow_promotion_success"
        " ON shadow_promotion_events(live_bot_id, shadow_bot_id)"
        " WHERE status = 'success'"
    ))

    # bots: auto_promote_criteria + last_auto_promote_check_at
    op.execute(text("""
        ALTER TABLE bots
            ADD COLUMN auto_promote_criteria JSONB
                CHECK (auto_promote_criteria IS NULL
                    OR (auto_promote_criteria ? 'min_sharpe'
                    AND auto_promote_criteria ? 'max_drawdown'
                    AND auto_promote_criteria ? 'min_win_rate')),
            ADD COLUMN last_auto_promote_check_at TIMESTAMPTZ
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE bots DROP COLUMN IF EXISTS last_auto_promote_check_at"))
    op.execute(text("ALTER TABLE bots DROP COLUMN IF EXISTS auto_promote_criteria"))
    op.execute(text("DROP INDEX IF EXISTS uq_shadow_promotion_success"))
    op.execute(text(
        "ALTER TABLE shadow_promotion_events DROP COLUMN IF EXISTS promoted_via"
    ))
    op.execute(text("DROP TABLE IF EXISTS portfolio_correlation_snapshots"))
    op.execute(text("DROP TABLE IF EXISTS portfolio_exposure_limits"))
```

Note: `shadow_promotion_events` currently has no `status` column (only `promoted_at`). The `uq_shadow_promotion_success` partial index references `WHERE status = 'success'`. Check `0066_phase21b_shadow_promotion_events.py` — if no `status` column exists, add it in this migration (TEXT NOT NULL DEFAULT 'success' for existing rows, CHECK IN ('success','reverted')).

- [ ] **Step 4: Verify shadow_promotion_events columns, add status if missing**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -c \"
from alembic.config import Config
from alembic import command
cfg = Config('alembic.ini')
command.history(cfg, verbose=False)
\""
```

Also run:
```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -c \"
import asyncio, asyncpg, os
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    rows = await conn.fetch(\\\"SELECT column_name FROM information_schema.columns WHERE table_name='shadow_promotion_events'\\\")
    print([r['column_name'] for r in rows])
    await conn.close()
asyncio.run(main())
\""
```

If `status` column is absent, add to the `upgrade()` before the index:
```python
op.execute(text(
    "ALTER TABLE shadow_promotion_events"
    " ADD COLUMN status TEXT NOT NULL DEFAULT 'success'"
    " CHECK (status IN ('success','reverted'))"
))
```

- [ ] **Step 5: Run the migration**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH alembic upgrade head"
```

Expected: no errors; `0069` appears in `alembic_version`.

- [ ] **Step 6: Run tests to verify they pass**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/alembic/test_0069.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/0069_phase22a_orchestrator.py backend/tests/alembic/test_0069.py
git commit -m "feat(22a-A): alembic 0069 — portfolio exposure limits + correlation snapshots + shadow promoted_via"
```

---

## Task B — `app/services/fx.py` + ExposureGate + Lua update

**Route:** Codex

**Files:**
- Create: `backend/app/services/fx.py`
- Create: `backend/app/services/orchestrator/__init__.py`
- Create: `backend/app/services/orchestrator/exposure_gate.py`
- Create: `backend/app/services/orchestrator/exposure_gate_lua.py`
- Modify: `backend/app/services/position_sizing_service.py:34`
- Modify: `backend/app/bot/context.py` (station 5.75 insertion)
- Modify: `backend/app/bot/fill_router.py` (Lua exposure update on fill)
- Create: `backend/tests/services/orchestrator/__init__.py`
- Create: `backend/tests/services/orchestrator/test_exposure_gate.py`

**Gate:** after Task A

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/services/orchestrator/test_exposure_gate.py
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from app.services.fx import get_fx_rate
from app.services.orchestrator.exposure_gate import ExposureOutcome, PortfolioExposureGate


class FakeRedis:
    def __init__(self, store: dict | None = None) -> None:
        self._store: dict = store or {}

    async def get(self, key: str) -> bytes | None:
        v = self._store.get(key)
        return v.encode() if isinstance(v, str) else v

    async def hgetall(self, key: str) -> dict:
        return self._store.get(key, {})

    async def evalsha(self, *args: object, **kwargs: object) -> None:
        pass

    async def eval(self, script: str, numkeys: int, *args: object) -> None:
        pass

    async def script_load(self, script: str) -> str:
        return "fake_sha"


@pytest.mark.asyncio
async def test_get_fx_rate_usd_identity() -> None:
    redis = FakeRedis()
    rate = await get_fx_rate("USD", redis)
    assert rate == Decimal("1.0")


@pytest.mark.asyncio
async def test_get_fx_rate_cache_hit() -> None:
    redis = FakeRedis({"fx:mid:GBP:USD": "1.27"})
    rate = await get_fx_rate("GBP", redis)
    assert rate == Decimal("1.27")


@pytest.mark.asyncio
async def test_get_fx_rate_cache_miss_returns_one() -> None:
    redis = FakeRedis({})
    rate = await get_fx_rate("EUR", redis)
    assert rate == Decimal("1.0")


@pytest.mark.asyncio
async def test_exposure_gate_allow_under_limit(db: AsyncMock) -> None:
    """Total notional 50k with 100k limit → allow."""
    import uuid
    account_id = uuid.uuid4()
    redis = FakeRedis({f"portfolio:exposure:{account_id}": {"total": b"50000.0"}})
    # Seed a total_notional limit
    db.execute = AsyncMock(return_value=MagicMock(
        all=MagicMock(return_value=[
            (1, "total_notional", None, Decimal("100000"), "USD", True)
        ])
    ))
    gate = PortfolioExposureGate(redis=redis)
    outcome = await gate.check(
        account_id=account_id,
        instrument_id=1,
        qty=Decimal("100"),
        price=Decimal("50"),
        currency="USD",
        db=db,
    )
    assert outcome == ExposureOutcome.ALLOW


@pytest.mark.asyncio
async def test_exposure_gate_block_over_limit(db: AsyncMock) -> None:
    """Total notional 95k + new order 10k = 105k > 100k limit → block."""
    import uuid
    account_id = uuid.uuid4()
    redis = FakeRedis({f"portfolio:exposure:{account_id}": {"total": b"95000.0"}})
    db.execute = AsyncMock(return_value=MagicMock(
        all=MagicMock(return_value=[
            (1, "total_notional", None, Decimal("100000"), "USD", True)
        ])
    ))
    gate = PortfolioExposureGate(redis=redis)
    outcome = await gate.check(
        account_id=account_id,
        instrument_id=1,
        qty=Decimal("200"),
        price=Decimal("60"),
        currency="USD",
        db=db,
    )
    assert outcome == ExposureOutcome.BLOCK


@pytest.mark.asyncio
async def test_exposure_gate_redis_miss_pg_fallback(db: AsyncMock) -> None:
    """Redis miss → PG fallback recomputes exposure; allow if under limit."""
    import uuid
    account_id = uuid.uuid4()
    redis = FakeRedis({})  # no cached exposure
    # First db.execute call: fetch limits; second: PG fallback SUM
    call_results = [
        MagicMock(all=MagicMock(return_value=[
            (1, "total_notional", None, Decimal("100000"), "USD", True)
        ])),
        MagicMock(scalar_one_or_none=MagicMock(return_value=Decimal("30000"))),
    ]
    db.execute = AsyncMock(side_effect=call_results)
    gate = PortfolioExposureGate(redis=redis)
    outcome = await gate.check(
        account_id=account_id,
        instrument_id=1,
        qty=Decimal("100"),
        price=Decimal("50"),
        currency="USD",
        db=db,
    )
    assert outcome == ExposureOutcome.ALLOW


@pytest.mark.asyncio
async def test_exposure_gate_redis_miss_pg_miss_fail_closed(db: AsyncMock) -> None:
    """Redis miss + PG unavailable → fail-CLOSED (block)."""
    import uuid
    from sqlalchemy.exc import OperationalError
    account_id = uuid.uuid4()
    redis = FakeRedis({})
    db.execute = AsyncMock(side_effect=OperationalError("conn", {}, Exception("db down")))
    gate = PortfolioExposureGate(redis=redis)
    outcome = await gate.check(
        account_id=account_id,
        instrument_id=1,
        qty=Decimal("100"),
        price=Decimal("50"),
        currency="USD",
        db=db,
    )
    assert outcome == ExposureOutcome.BLOCK


@pytest.mark.asyncio
async def test_exposure_gate_kill_switch_disabled(db: AsyncMock) -> None:
    """When kill switch config is false, gate returns ALLOW without checking."""
    import uuid
    account_id = uuid.uuid4()
    redis = FakeRedis({f"portfolio:exposure:{account_id}": {"total": b"999999.0"}})
    db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value="false")
    ))
    gate = PortfolioExposureGate(redis=redis)
    # Patch config read to return disabled
    with patch.object(gate, "_gate_enabled", return_value=False):
        outcome = await gate.check(
            account_id=account_id,
            instrument_id=1,
            qty=Decimal("1000"),
            price=Decimal("200"),
            currency="USD",
            db=db,
        )
    assert outcome == ExposureOutcome.ALLOW


@pytest.mark.asyncio
async def test_fill_router_updates_exposure(db: AsyncMock) -> None:
    """BotFillRouter.handle_event publishes fill AND triggers exposure update."""
    import uuid
    from app.bot.fill_router import BotFillRouter

    bot_id = uuid.uuid4()
    account_id = uuid.uuid4()
    redis = FakeRedis()
    redis.publish = AsyncMock()
    redis.eval = AsyncMock()

    db.execute = AsyncMock(return_value=MagicMock(
        first=MagicMock(return_value=(bot_id,)),
    ))
    router = BotFillRouter(db=db, redis=redis)
    event = json.dumps({
        "type": "order:fill",
        "order_id": str(uuid.uuid4()),
        "account_id": str(account_id),
        "side": "buy",
        "position_effect": "OPEN",
        "fill_qty": "100",
        "fill_price": "50.00",
        "multiplier": "1",
        "currency": "USD",
        "instrument_id": 42,
    })
    await router.handle_event(event)
    redis.eval.assert_called_once()  # Lua script called once
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/test_exposure_gate.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: `ImportError` — modules don't exist yet.

- [ ] **Step 3: Create `app/services/fx.py`**

```python
# backend/app/services/fx.py
"""Shared FX rate helper.

Reads from the live fx:mid:<from>:<to> key populated by the FX poller.
Returns Decimal("1.0") on cache miss (fail-safe: wrong notional is better
than blocking all orders).
"""
from __future__ import annotations
from decimal import Decimal
from typing import Any


async def get_fx_rate(currency: str, redis: Any, base: str = "USD") -> Decimal:
    if currency == base:
        return Decimal("1.0")
    cached = await redis.get(f"fx:mid:{currency}:{base}")
    if cached is None:
        return Decimal("1.0")
    return Decimal(cached.decode() if isinstance(cached, bytes) else cached)
```

- [ ] **Step 4: Update `position_sizing_service.py` import**

In `backend/app/services/position_sizing_service.py`, line 34:
```python
# Before:
from app.services.orders_service import RedisLike, _fx_rate, capability_broker_id

# After:
from app.services.fx import get_fx_rate
from app.services.orders_service import RedisLike, capability_broker_id
```

Also update all three call sites in the same file from `await _fx_rate(self._redis, asset_currency, base_currency)` to `await get_fx_rate(asset_currency, self._redis, base=base_currency)`.

- [ ] **Step 5: Create the Lua script constant**

```python
# backend/app/services/orchestrator/exposure_gate_lua.py
"""Atomic exposure update Lua script for Redis HASH."""

EXPOSURE_UPDATE_SCRIPT = """
local key = KEYS[1]
local total_delta = tonumber(ARGV[1])
local instr_key   = ARGV[2]
local instr_delta = tonumber(ARGV[3])
redis.call('HINCRBYFLOAT', key, 'total', total_delta)
if instr_key ~= '' then
    redis.call('HINCRBYFLOAT', key, instr_key, instr_delta)
end
return 1
"""
```

- [ ] **Step 6: Create `orchestrator/exposure_gate.py`**

```python
# backend/app/services/orchestrator/exposure_gate.py
from __future__ import annotations
import json
import time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.fx import get_fx_rate
from app.services.orchestrator import metrics as m
from app.services.orchestrator.exposure_gate_lua import EXPOSURE_UPDATE_SCRIPT

log = structlog.get_logger()


class ExposureOutcome(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


class PortfolioExposureGate:
    """Pre-trade station 5.75 — portfolio-level notional exposure check.

    Redis HASH portfolio:exposure:{account_id}:
      total                   → total USD notional
      instr:{instrument_id}   → per-instrument USD notional
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis
        self._lua_sha: str | None = None

    async def _ensure_lua_loaded(self) -> str:
        if self._lua_sha is None:
            self._lua_sha = await self._redis.script_load(EXPOSURE_UPDATE_SCRIPT)
        return self._lua_sha

    async def _gate_enabled(self, db: AsyncSession) -> bool:
        row = (
            await db.execute(
                text(
                    "SELECT value_json FROM app_config"
                    " WHERE namespace='orchestrator' AND key='exposure_gate_enabled'"
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            return True
        return json.loads(row) is not False

    async def check(
        self,
        account_id: UUID,
        instrument_id: int,
        qty: Decimal,
        price: Decimal,
        currency: str,
        db: AsyncSession,
        multiplier: Decimal = Decimal("1"),
    ) -> ExposureOutcome:
        t0 = time.perf_counter()
        try:
            if not await self._gate_enabled(db):
                return ExposureOutcome.ALLOW

            fx = await get_fx_rate(currency, self._redis)
            order_notional = qty * price * multiplier * fx

            exposure = await self._read_exposure(account_id, instrument_id, order_notional, db)
            limits = await self._fetch_limits(account_id, instrument_id, db)

            outcome = ExposureOutcome.ALLOW
            for limit_id, limit_type, instr_id, max_notional, _currency, enabled in limits:
                if not enabled:
                    continue
                if limit_type == "total_notional":
                    projected = exposure["total"] + order_notional
                    if projected > max_notional:
                        outcome = ExposureOutcome.BLOCK
                        break
                elif limit_type == "per_instrument" and instr_id == instrument_id:
                    projected = exposure.get(f"instr:{instrument_id}", Decimal("0")) + order_notional
                    if projected > max_notional:
                        outcome = ExposureOutcome.BLOCK
                        break

            label = outcome.value
            m.orchestrator_exposure_checks_total.labels(
                outcome=label, limit_type="total_notional"
            ).inc()
            m.orchestrator_exposure_gate_latency_seconds.observe(time.perf_counter() - t0)
            return outcome

        except SQLAlchemyError:
            m.orchestrator_exposure_gate_pg_fallback_total.labels(outcome="block").inc()
            log.exception("exposure_gate_pg_unavailable", account_id=str(account_id))
            return ExposureOutcome.BLOCK

    async def _read_exposure(
        self,
        account_id: UUID,
        instrument_id: int,
        order_notional: Decimal,
        db: AsyncSession,
    ) -> dict[str, Decimal]:
        redis_key = f"portfolio:exposure:{account_id}"
        raw = await self._redis.hgetall(redis_key)
        if raw:
            return {
                k.decode() if isinstance(k, bytes) else k: Decimal(
                    v.decode() if isinstance(v, bytes) else v
                )
                for k, v in raw.items()
            }
        # Redis miss — PG fallback
        m.orchestrator_exposure_gate_pg_fallback_total.labels(outcome="used").inc()
        row = (
            await db.execute(
                text(
                    """
                    SELECT COALESCE(SUM(ABS(notional_usd)), 0)::numeric
                    FROM bot_orders
                    WHERE account_id = :acct
                      AND status NOT IN ('cancelled', 'rejected')
                    """
                ),
                {"acct": account_id},
            )
        ).scalar_one_or_none()
        total = Decimal(str(row)) if row is not None else Decimal("0")
        exposure = {"total": total}
        # Cache result back
        try:
            await self._redis.hset(redis_key, mapping={"total": str(total)})
            await self._redis.expire(redis_key, 3600)
        except Exception:
            pass
        return exposure

    async def _fetch_limits(
        self, account_id: UUID, instrument_id: int, db: AsyncSession
    ) -> list[tuple]:
        result = await db.execute(
            text(
                """
                SELECT id, limit_type, instrument_id, max_notional, currency, enabled
                FROM portfolio_exposure_limits
                WHERE account_id = :acct AND enabled = true
                  AND (instrument_id IS NULL OR instrument_id = :iid)
                """
            ),
            {"acct": account_id, "iid": instrument_id},
        )
        return result.all()

    async def update_on_fill(
        self,
        account_id: UUID,
        instrument_id: int,
        signed_delta_usd: Decimal,
    ) -> None:
        """Atomically update exposure HASH on order fill. Call from BotFillRouter."""
        redis_key = f"portfolio:exposure:{account_id}"
        instr_key = f"instr:{instrument_id}"
        try:
            sha = await self._ensure_lua_loaded()
            await self._redis.evalsha(
                sha,
                1,
                redis_key,
                str(signed_delta_usd),
                instr_key,
                str(signed_delta_usd),
            )
        except Exception:
            # Fall back to plain EVAL if EVALSHA cache miss after Redis restart
            await self._redis.eval(
                EXPOSURE_UPDATE_SCRIPT,
                1,
                redis_key,
                str(signed_delta_usd),
                instr_key,
                str(signed_delta_usd),
            )
```

- [ ] **Step 7: Create `orchestrator/__init__.py`**

```python
# backend/app/services/orchestrator/__init__.py
```

- [ ] **Step 8: Create `orchestrator/metrics.py`**

```python
# backend/app/services/orchestrator/metrics.py
from prometheus_client import Counter, Gauge, Histogram

orchestrator_exposure_checks_total = Counter(
    "orchestrator_exposure_checks_total",
    "Portfolio exposure gate check outcomes",
    ["outcome", "limit_type"],
)
orchestrator_exposure_gate_latency_seconds = Histogram(
    "orchestrator_exposure_gate_latency_seconds",
    "Portfolio exposure gate check latency",
)
orchestrator_exposure_gate_pg_fallback_total = Counter(
    "orchestrator_exposure_gate_pg_fallback_total",
    "Exposure gate PG fallback events",
    ["outcome"],
)
orchestrator_correlation_matrix_age_seconds = Gauge(
    "orchestrator_correlation_matrix_age_seconds",
    "Age of correlation matrix in Redis",
    ["account_id"],
)
orchestrator_auto_promote_total = Counter(
    "orchestrator_auto_promote_total",
    "Auto-promote evaluation outcomes",
    ["outcome"],
)
orchestrator_retrain_bots_total = Counter(
    "orchestrator_retrain_bots_total",
    "Total bots processed by NightlyRetrainJob",
)
orchestrator_retrain_latency_seconds = Histogram(
    "orchestrator_retrain_latency_seconds",
    "NightlyRetrainJob total latency",
)
```

- [ ] **Step 9: Wire PortfolioExposureGate into `bot/context.py` at station 5.75**

In `backend/app/bot/context.py`, after the `await self._risk_cap_svc.check(...)` block (around line 144) and before the `if self._advisor is not None:` block (around line 148):

```python
        # Station 5.75 — portfolio-level exposure gate
        if self._exposure_gate is not None:
            from app.services.orchestrator.exposure_gate import ExposureOutcome
            _exp_outcome = await self._exposure_gate.check(
                account_id=account_id,
                instrument_id=instrument_id,
                qty=qty,
                price=price,
                currency=currency or "USD",
                db=self._db,
            )
            if _exp_outcome == ExposureOutcome.BLOCK:
                raise BotOrderBlocked(
                    "portfolio_exposure_gate",
                    detail={"instrument_id": instrument_id},
                )
```

Also add `exposure_gate: PortfolioExposureGate | None = None` to `BotContext.__init__` and accept it as an optional parameter.

- [ ] **Step 10: Wire exposure update into `bot/fill_router.py`**

In `BotFillRouter.handle_event`, after `await self.redis.publish(...)` (line ~64), add:

```python
        if self._exposure_gate is not None:
            try:
                from app.services.fx import get_fx_rate
                from decimal import Decimal
                side = event.get("side", "buy")
                position_effect = event.get("position_effect", "OPEN")
                side_sign = Decimal("1") if side.lower() == "buy" else Decimal("-1")
                fill_qty = Decimal(str(event.get("fill_qty", "0")))
                fill_price = Decimal(str(event.get("fill_price", "0")))
                multiplier = Decimal(str(event.get("multiplier", "1")))
                instr_id = int(event.get("instrument_id", 0))
                currency = event.get("currency", "USD")
                fx = await get_fx_rate(currency, self._redis)
                delta = side_sign * fill_qty * fill_price * multiplier * fx
                await self._exposure_gate.update_on_fill(
                    account_id=account_id,
                    instrument_id=instr_id,
                    signed_delta_usd=delta,
                )
            except Exception:
                log.exception("exposure_update_on_fill_failed", account_id=str(account_id))
```

Add `exposure_gate` optional parameter to `BotFillRouter.__init__`.

- [ ] **Step 11: Run tests**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/test_exposure_gate.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: all PASS.

- [ ] **Step 12: Run full suite to check for regressions**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/ -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

- [ ] **Step 13: Commit**

```bash
git add backend/app/services/fx.py \
        backend/app/services/orchestrator/__init__.py \
        backend/app/services/orchestrator/exposure_gate.py \
        backend/app/services/orchestrator/exposure_gate_lua.py \
        backend/app/services/orchestrator/metrics.py \
        backend/app/services/position_sizing_service.py \
        backend/app/bot/context.py \
        backend/app/bot/fill_router.py \
        backend/tests/services/orchestrator/__init__.py \
        backend/tests/services/orchestrator/test_exposure_gate.py
git commit -m "feat(22a-B): PortfolioExposureGate + fx.py + Lua fill update (station 5.75)"
```

---

## Task C — CorrelationService

**Route:** Qwen

**Files:**
- Create: `backend/app/services/orchestrator/correlation.py`
- Create: `backend/tests/services/orchestrator/test_correlation.py`

**Gate:** after Task A

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/services/orchestrator/test_correlation.py
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.services.orchestrator.correlation import CorrelationService


def _make_db_with_bars(bars_by_instrument: dict[int, list[float]]) -> AsyncMock:
    """Return a mock DB that yields bars_1d rows per instrument."""
    # bars format: {instr_id: [close1, close2, ...]} oldest-first
    async def execute_side_effect(stmt, params=None, **kwargs):
        # Detect instrument_id from params
        iid = (params or {}).get("iid")
        rows = bars_by_instrument.get(iid, [])
        mock_result = MagicMock()
        mock_result.all.return_value = [(r,) for r in rows]
        return mock_result
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute_side_effect)
    return db


@pytest.mark.asyncio
async def test_correlation_two_instruments_identical_returns() -> None:
    """Two instruments with identical returns → ρ = 1.0."""
    import uuid
    from tests.services.orchestrator.test_exposure_gate import FakeRedis

    prices = [100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0]
    db = _make_db_with_bars({1: prices, 2: prices})
    redis = FakeRedis()
    redis.set = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    account_id = uuid.uuid4()
    svc = CorrelationService(redis=redis)
    matrix = await svc.compute_and_store(
        account_id=account_id,
        instrument_ids=[1, 2],
        db=db,
        window_days=30,
    )
    assert abs(matrix["1"]["2"] - 1.0) < 1e-6
    assert abs(matrix["2"]["1"] - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_correlation_negative_rho() -> None:
    """Instruments with inverse returns → ρ ≈ -1.0."""
    import uuid
    from tests.services.orchestrator.test_exposure_gate import FakeRedis

    up = [100.0, 102.0, 104.0, 106.0, 108.0]
    down = [108.0, 106.0, 104.0, 102.0, 100.0]
    db = _make_db_with_bars({1: up, 2: down})
    redis = FakeRedis()
    redis.set = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    account_id = uuid.uuid4()
    svc = CorrelationService(redis=redis)
    matrix = await svc.compute_and_store(
        account_id=account_id,
        instrument_ids=[1, 2],
        db=db,
        window_days=30,
    )
    assert matrix["1"]["2"] < -0.95


@pytest.mark.asyncio
async def test_correlation_nan_bars_handled() -> None:
    """NaN / None close prices are skipped without crashing."""
    import uuid
    from tests.services.orchestrator.test_exposure_gate import FakeRedis

    # instrument 2 has insufficient bars → excluded from matrix
    db = _make_db_with_bars({1: [100.0, 102.0, 104.0, 103.0, 105.0], 2: [100.0]})
    redis = FakeRedis()
    redis.set = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    account_id = uuid.uuid4()
    svc = CorrelationService(redis=redis)
    matrix = await svc.compute_and_store(
        account_id=account_id,
        instrument_ids=[1, 2],
        db=db,
        window_days=30,
    )
    # instrument 2 excluded — matrix only contains instrument 1 (self-correlation = 1.0)
    assert "2" not in matrix.get("1", {}) or matrix["1"].get("2") is None


@pytest.mark.asyncio
async def test_correlation_redis_ttl_set() -> None:
    """Redis key is set with TTL=86400."""
    import uuid
    from tests.services.orchestrator.test_exposure_gate import FakeRedis

    prices = [100.0, 102.0, 104.0, 103.0, 105.0]
    db = _make_db_with_bars({1: prices})
    redis = FakeRedis()
    redis.set = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    account_id = uuid.uuid4()
    svc = CorrelationService(redis=redis)
    await svc.compute_and_store(
        account_id=account_id,
        instrument_ids=[1],
        db=db,
        window_days=30,
    )
    redis.set.assert_called_once()
    call_kwargs = redis.set.call_args
    assert call_kwargs.kwargs.get("ex") == 86400 or (
        len(call_kwargs.args) >= 3 and call_kwargs.args[2] == 86400
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/test_correlation.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `CorrelationService`**

```python
# backend/app/services/orchestrator/correlation.py
from __future__ import annotations
import json
import math
import time
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.orchestrator import metrics as m

log = structlog.get_logger()

_MIN_BARS = 10  # minimum bars required for reliable Pearson


class CorrelationService:
    """Computes Pearson correlation matrix over bars_1d for held instruments.

    Stores full symmetric matrix to Redis (TTL 86400s) and writes an audit
    snapshot to portfolio_correlation_snapshots. Raw notional is used in
    PortfolioExposureGate in 22a; this matrix is for FE heatmap + health digest.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def compute_and_store(
        self,
        account_id: UUID,
        instrument_ids: list[int],
        db: AsyncSession,
        window_days: int = 30,
    ) -> dict[str, dict[str, float]]:
        t0 = time.time()
        # Fetch returns per instrument
        returns: dict[int, list[float]] = {}
        for iid in instrument_ids:
            rows = (
                await db.execute(
                    text(
                        "SELECT close FROM bars_1d"
                        " WHERE instrument_id = :iid"
                        " ORDER BY bar_date DESC"
                        " LIMIT :n"
                    ),
                    {"iid": iid, "n": window_days + 1},
                )
            ).all()
            closes = [float(r[0]) for r in reversed(rows) if r[0] is not None]
            if len(closes) < _MIN_BARS + 1:
                log.warning("correlation_insufficient_bars", instrument_id=iid, n=len(closes))
                continue
            log_rets = [
                math.log(closes[i] / closes[i - 1])
                for i in range(1, len(closes))
                if closes[i - 1] > 0 and closes[i] > 0
            ]
            if len(log_rets) >= _MIN_BARS:
                returns[iid] = log_rets

        matrix: dict[str, dict[str, float]] = {}
        iids = list(returns.keys())
        for i, iid_i in enumerate(iids):
            matrix[str(iid_i)] = {}
            for iid_j in iids:
                if iid_i == iid_j:
                    matrix[str(iid_i)][str(iid_j)] = 1.0
                else:
                    matrix[str(iid_i)][str(iid_j)] = _pearson(returns[iid_i], returns[iid_j])

        redis_key = f"portfolio:correlation:{account_id}"
        await self._redis.set(redis_key, json.dumps(matrix), ex=86400)

        # Update age metric
        m.orchestrator_correlation_matrix_age_seconds.labels(account_id=str(account_id)).set(0)

        # Audit snapshot
        try:
            await db.execute(
                text(
                    "INSERT INTO portfolio_correlation_snapshots"
                    " (account_id, instrument_ids, matrix_json, window_days)"
                    " VALUES (:acct, :iids, :mat::jsonb, :win)"
                ),
                {
                    "acct": account_id,
                    "iids": instrument_ids,
                    "mat": json.dumps(matrix),
                    "win": window_days,
                },
            )
            await db.commit()
        except Exception:
            log.exception("correlation_snapshot_write_failed", account_id=str(account_id))

        log.info(
            "correlation_computed",
            account_id=str(account_id),
            n_instruments=len(iids),
            elapsed_s=round(time.time() - t0, 3),
        )
        return matrix

    async def read_from_redis(self, account_id: UUID) -> dict[str, dict[str, float]] | None:
        raw = await self._redis.get(f"portfolio:correlation:{account_id}")
        if raw is None:
            return None
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    xs, ys = xs[:n], ys[:n]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    std_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    std_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if std_x == 0 or std_y == 0:
        return 0.0
    return cov / (std_x * std_y)
```

- [ ] **Step 4: Run tests**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/test_correlation.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/orchestrator/correlation.py \
        backend/tests/services/orchestrator/test_correlation.py
git commit -m "feat(22a-C): CorrelationService (Pearson, Redis TTL 86400s, audit snapshot)"
```

---

## Task D — AutoPromoteEvaluator

**Route:** Qwen

**Files:**
- Create: `backend/app/services/orchestrator/auto_promote.py`
- Create: `backend/tests/services/orchestrator/test_auto_promote.py`

**Gate:** after Task A

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/services/orchestrator/test_auto_promote.py
import pytest
from pydantic import ValidationError
from app.services.orchestrator.auto_promote import AutoPromoteCriteria, AutoPromoteEvaluator


def test_auto_promote_criteria_valid() -> None:
    c = AutoPromoteCriteria(min_sharpe=0.5, max_drawdown=0.15, min_win_rate=0.5)
    assert c.auto_apply is False
    assert c.min_comparison_days == 14


def test_auto_promote_criteria_unknown_key_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_field"):
        AutoPromoteCriteria(
            min_sharpe=0.5, max_drawdown=0.15, min_win_rate=0.5, extra_field=1
        )


def test_auto_promote_criteria_missing_required_field() -> None:
    with pytest.raises(ValidationError):
        AutoPromoteCriteria(min_sharpe=0.5, max_drawdown=0.15)  # missing min_win_rate


@pytest.mark.asyncio
async def test_auto_promote_evaluator_skips_when_master_switch_off() -> None:
    from unittest.mock import AsyncMock, MagicMock
    import uuid

    db = AsyncMock()
    # Return master switch = false
    db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value='"false"')
    ))
    promoter_svc = AsyncMock()
    telegram = AsyncMock()
    evaluator = AutoPromoteEvaluator(promoter_service=promoter_svc, telegram=telegram)

    live_id = uuid.uuid4()
    shadow_id = uuid.uuid4()
    result = await evaluator.evaluate(live_id, shadow_id, db)
    assert result == "skipped_master_switch_off"
    promoter_svc.promote.assert_not_called()


@pytest.mark.asyncio
async def test_auto_promote_evaluator_already_promoted_idempotent() -> None:
    from unittest.mock import AsyncMock, MagicMock
    import uuid

    db = AsyncMock()
    call_results = [
        # master switch ON
        MagicMock(scalar_one_or_none=MagicMock(return_value='"true"')),
        # fire-once check: row exists → already promoted
        MagicMock(scalar_one_or_none=MagicMock(return_value="existing_id")),
    ]
    db.execute = AsyncMock(side_effect=call_results)
    promoter_svc = AsyncMock()
    evaluator = AutoPromoteEvaluator(promoter_service=promoter_svc, telegram=AsyncMock())

    result = await evaluator.evaluate(uuid.uuid4(), uuid.uuid4(), db)
    assert result == "skipped_already_promoted"
    promoter_svc.promote.assert_not_called()


@pytest.mark.asyncio
async def test_auto_promote_evaluator_promotes_when_all_criteria_pass() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch
    import uuid

    live_id = uuid.uuid4()
    shadow_id = uuid.uuid4()
    db = AsyncMock()
    call_results = [
        MagicMock(scalar_one_or_none=MagicMock(return_value='"true"')),  # master switch ON
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),       # not yet promoted
        # criteria: auto_apply=true, min_sharpe=0.5, max_drawdown=0.2, min_win_rate=0.4
        MagicMock(scalar_one_or_none=MagicMock(return_value=(
            '{"min_sharpe": 0.5, "max_drawdown": 0.2, "min_win_rate": 0.4, "auto_apply": true}'
        ))),
        # shadow metrics rows (window_days rows of sharpe/drawdown/win_rate)
        MagicMock(all=MagicMock(return_value=[
            (1.2, 0.05, 0.6, 1.1, 100, 14)  # (sharpe, max_dd, win_rate, mar, trades, window_days)
        ])),
    ]
    db.execute = AsyncMock(side_effect=call_results)

    promoter_svc = AsyncMock()
    telegram = AsyncMock()
    evaluator = AutoPromoteEvaluator(promoter_service=promoter_svc, telegram=telegram)

    result = await evaluator.evaluate(live_id, shadow_id, db)
    assert result == "promoted"
    promoter_svc.promote.assert_called_once_with(live_id, shadow_id, "auto", db)
    telegram.send.assert_called_once()


@pytest.mark.asyncio
async def test_auto_promote_evaluator_skips_when_criteria_fail() -> None:
    from unittest.mock import AsyncMock, MagicMock
    import uuid

    live_id = uuid.uuid4()
    shadow_id = uuid.uuid4()
    db = AsyncMock()
    call_results = [
        MagicMock(scalar_one_or_none=MagicMock(return_value='"true"')),
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        MagicMock(scalar_one_or_none=MagicMock(return_value=(
            '{"min_sharpe": 0.5, "max_drawdown": 0.2, "min_win_rate": 0.4, "auto_apply": true}'
        ))),
        # Sharpe 0.3 fails min_sharpe 0.5
        MagicMock(all=MagicMock(return_value=[
            (0.3, 0.05, 0.6, 0.8, 100, 14)
        ])),
    ]
    db.execute = AsyncMock(side_effect=call_results)
    promoter_svc = AsyncMock()
    evaluator = AutoPromoteEvaluator(promoter_service=promoter_svc, telegram=AsyncMock())
    result = await evaluator.evaluate(live_id, shadow_id, db)
    assert result == "criteria_not_met"
    promoter_svc.promote.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/test_auto_promote.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `AutoPromoteCriteria` and `AutoPromoteEvaluator`**

```python
# backend/app/services/orchestrator/auto_promote.py
from __future__ import annotations
import json
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.orchestrator import metrics as m

log = structlog.get_logger()


class AutoPromoteCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_sharpe: float
    max_drawdown: float     # 0–1; e.g. 0.15 = 15% max acceptable drawdown
    min_win_rate: float     # 0–1
    min_comparison_days: int = 14
    auto_apply: bool = False


class AutoPromoteEvaluator:
    """Replaces the always-False stub in ShadowPromoterService.check_auto_promote_eligibility().

    Fire-once guard: checks shadow_promotion_events for an existing success row
    before evaluating criteria. DB unique index uq_shadow_promotion_success
    enforces idempotency at the DB level as well.
    """

    def __init__(self, promoter_service: Any, telegram: Any) -> None:
        self._promoter = promoter_service
        self._telegram = telegram

    async def evaluate(
        self, live_bot_id: UUID, shadow_bot_id: UUID, db: AsyncSession
    ) -> str:
        # 1. Master switch
        if not await self._master_switch_on(db):
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_master_switch_off"

        # 2. Fire-once guard
        existing = (
            await db.execute(
                text(
                    "SELECT id FROM shadow_promotion_events"
                    " WHERE live_bot_id = :lid AND shadow_bot_id = :sid"
                    " AND status = 'success'"
                    " LIMIT 1"
                ),
                {"lid": live_bot_id, "sid": shadow_bot_id},
            )
        ).scalar_one_or_none()
        if existing is not None:
            log.info(
                "auto_promote_already_promoted",
                live_bot_id=str(live_bot_id),
                shadow_bot_id=str(shadow_bot_id),
            )
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_already_promoted"

        # 3. Load criteria from bots.auto_promote_criteria
        criteria_raw = (
            await db.execute(
                text(
                    "SELECT auto_promote_criteria FROM bots WHERE id = :lid LIMIT 1"
                ),
                {"lid": live_bot_id},
            )
        ).scalar_one_or_none()
        if criteria_raw is None:
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_no_criteria"
        criteria = AutoPromoteCriteria.model_validate_json(
            criteria_raw if isinstance(criteria_raw, str) else json.dumps(criteria_raw)
        )
        if not criteria.auto_apply:
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_auto_apply_false"

        # 4. Evaluate shadow metrics
        metrics_row = (
            await db.execute(
                text(
                    """
                    SELECT
                        avg(kpi_sharpe)    AS sharpe,
                        max(kpi_max_dd)    AS max_dd,
                        avg(kpi_win_rate)  AS win_rate,
                        avg(kpi_mar)       AS mar,
                        count(*)           AS trade_count,
                        :window_days       AS window_days
                    FROM bot_runs
                    WHERE bot_id = :sid
                      AND ended_at >= now() - :window_days * interval '1 day'
                    """
                ),
                {"sid": shadow_bot_id, "window_days": criteria.min_comparison_days},
            )
        ).all()

        if not metrics_row or metrics_row[0][4] is None or int(metrics_row[0][4]) == 0:
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_insufficient_data"

        sharpe = float(metrics_row[0][0] or 0)
        max_dd = float(metrics_row[0][1] or 1)
        win_rate = float(metrics_row[0][2] or 0)

        if sharpe < criteria.min_sharpe or max_dd > criteria.max_drawdown or win_rate < criteria.min_win_rate:
            log.info(
                "auto_promote_criteria_not_met",
                live_bot_id=str(live_bot_id),
                shadow_bot_id=str(shadow_bot_id),
                sharpe=sharpe,
                max_dd=max_dd,
                win_rate=win_rate,
            )
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "criteria_not_met"

        # 5. Promote
        try:
            await self._promoter.promote(live_bot_id, shadow_bot_id, "auto", db)
            m.orchestrator_auto_promote_total.labels(outcome="promoted").inc()
            await self._telegram.send(
                f"🤖 Auto-promoted shadow bot {shadow_bot_id} → live {live_bot_id}"
                f" (Sharpe={sharpe:.2f}, MaxDD={max_dd:.1%}, WinRate={win_rate:.1%})"
            )
            return "promoted"
        except Exception:
            log.exception(
                "auto_promote_failed",
                live_bot_id=str(live_bot_id),
                shadow_bot_id=str(shadow_bot_id),
            )
            m.orchestrator_auto_promote_total.labels(outcome="error").inc()
            return "error"

    async def _master_switch_on(self, db: AsyncSession) -> bool:
        row = (
            await db.execute(
                text(
                    "SELECT value_json FROM app_config"
                    " WHERE namespace='orchestrator' AND key='auto_promote_enabled'"
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        return json.loads(row) is not False and json.loads(row) != "false"
```

Also update `ShadowPromoterService.promote()` signature to accept `promoted_via: str` parameter and write it to `shadow_promotion_events`. In `backend/app/services/shadow_promoter/service.py`, the INSERT at line ~204 needs:
```python
# Add promoted_via to INSERT columns and VALUES
"INSERT INTO shadow_promotion_events (shadow_bot_id, live_bot_id, promoted_by, promoted_via, ...)"
```
And `check_auto_promote_eligibility` stub at line 241 becomes a delegation:
```python
async def check_auto_promote_eligibility(
    self, live_bot_id: UUID, shadow_bot_id: UUID, db: AsyncSession
) -> bool:
    if self._auto_promote_evaluator is None:
        return False
    result = await self._auto_promote_evaluator.evaluate(live_bot_id, shadow_bot_id, db)
    return result == "promoted"
```
Add `auto_promote_evaluator: AutoPromoteEvaluator | None = None` to `ShadowPromoterService.__init__`.

- [ ] **Step 4: Run tests**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/test_auto_promote.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/orchestrator/auto_promote.py \
        backend/app/services/shadow_promoter/service.py \
        backend/tests/services/orchestrator/test_auto_promote.py
git commit -m "feat(22a-D): AutoPromoteEvaluator + AutoPromoteCriteria (replaces always-False stub)"
```

---

## Task E — NightlyRetrainJob + APScheduler wiring

**Route:** Codex

**Files:**
- Create: `backend/app/services/orchestrator/retrain.py`
- Modify: `backend/app/main.py` (APScheduler cron jobs for correlation + retrain)
- Create: `backend/tests/services/orchestrator/test_retrain.py`

**Gate:** after Tasks B, C, D

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/services/orchestrator/test_retrain.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from app.services.orchestrator.retrain import NightlyRetrainJob


@pytest.mark.asyncio
async def test_retrain_skips_paused_bots() -> None:
    """Bots with status != 'running' are excluded."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(
        all=MagicMock(return_value=[])  # no running bots
    ))
    tuner = AsyncMock()
    telegram = AsyncMock()
    job = NightlyRetrainJob(
        db_factory=AsyncMock(return_value=db),
        param_tuner_factory=lambda db: tuner,
        telegram=telegram,
    )
    await job.run()
    tuner.trigger.assert_not_called()
    # Telegram still called (zero-bot report)
    telegram.send.assert_called_once()


@pytest.mark.asyncio
async def test_retrain_parallel_fan_out_semaphore() -> None:
    """3 bots with semaphore=1 → sequential (max one concurrent)."""
    import uuid
    from datetime import datetime

    bot_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    call_order: list[str] = []

    async def fake_trigger(bot_id, *args, **kwargs):
        call_order.append(f"start:{bot_id}")
        await asyncio.sleep(0.01)
        call_order.append(f"end:{bot_id}")
        return None

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(
        all=MagicMock(return_value=[(bid,) for bid in bot_ids])
    ))

    tuner = AsyncMock()
    tuner.trigger = AsyncMock(side_effect=fake_trigger)
    tuner.poll_backtest_results = AsyncMock(return_value=None)
    telegram = AsyncMock()

    job = NightlyRetrainJob(
        db_factory=AsyncMock(return_value=db),
        param_tuner_factory=lambda db: tuner,
        telegram=telegram,
        max_parallel=1,
    )
    await job.run()
    assert tuner.trigger.call_count == 3
    # With semaphore=1: end of bot N happens before start of bot N+1
    for i in range(len(bot_ids) - 1):
        end_idx = call_order.index(f"end:{bot_ids[i]}")
        start_next = call_order.index(f"start:{bot_ids[i+1]}")
        assert end_idx < start_next


@pytest.mark.asyncio
async def test_retrain_posts_telegram_report() -> None:
    """Telegram report is sent after all bots processed."""
    import uuid
    db = AsyncMock()
    bot_id = uuid.uuid4()
    db.execute = AsyncMock(return_value=MagicMock(
        all=MagicMock(return_value=[(bot_id,)])
    ))
    tuner = AsyncMock()
    tuner.trigger = AsyncMock(return_value=None)
    tuner.poll_backtest_results = AsyncMock(return_value=None)
    telegram = AsyncMock()

    job = NightlyRetrainJob(
        db_factory=AsyncMock(return_value=db),
        param_tuner_factory=lambda db: tuner,
        telegram=telegram,
    )
    await job.run()
    telegram.send.assert_called_once()
    msg = telegram.send.call_args[0][0]
    assert "retrain" in msg.lower() or "bot" in msg.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/test_retrain.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `NightlyRetrainJob`**

```python
# backend/app/services/orchestrator/retrain.py
from __future__ import annotations
import asyncio
import time
from typing import Any, Callable
from uuid import UUID

import structlog
from sqlalchemy import text

from app.services.orchestrator import metrics as m

log = structlog.get_logger()


class NightlyRetrainJob:
    """APScheduler job: parallel ParamTunerService.trigger across all running bots.

    Cron: "0 2 * * *" (02:00 UTC). max_instances=1, coalesce=True.
    Semaphore N = max_parallel (default 2; config key orchestrator/retrain_max_parallel).
    Worst case: ceil(N_bots / max_parallel) * retrain_timeout_seconds.
    """

    def __init__(
        self,
        db_factory: Any,
        param_tuner_factory: Callable,
        telegram: Any,
        max_parallel: int = 2,
        timeout_seconds: int = 3600,
    ) -> None:
        self._db_factory = db_factory
        self._tuner_factory = param_tuner_factory
        self._telegram = telegram
        self._max_parallel = max_parallel
        self._timeout_seconds = timeout_seconds

    async def run(self) -> None:
        t0 = time.perf_counter()
        log.info("nightly_retrain_start")
        results: list[tuple[UUID, str]] = []

        async with self._db_factory() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT id FROM bots"
                        " WHERE deleted_at IS NULL AND is_shadow = false"
                        " AND status = 'running'"
                    )
                )
            ).all()
        bot_ids = [r[0] for r in rows]

        sem = asyncio.Semaphore(self._max_parallel)

        async def _retrain_one(bot_id: UUID) -> None:
            async with sem:
                try:
                    async with self._db_factory() as bot_db:
                        tuner = self._tuner_factory(bot_db)
                        from app.services.param_tuner.types import TunerTrigger
                        await asyncio.wait_for(
                            tuner.trigger(bot_id, TunerTrigger.SCHEDULED, bot_db),
                            timeout=self._timeout_seconds,
                        )
                    results.append((bot_id, "triggered"))
                    m.orchestrator_retrain_bots_total.inc()
                except asyncio.TimeoutError:
                    log.warning("retrain_timeout", bot_id=str(bot_id))
                    results.append((bot_id, "timeout"))
                except Exception:
                    log.exception("retrain_failed", bot_id=str(bot_id))
                    results.append((bot_id, "error"))

        async with asyncio.TaskGroup() as tg:
            for bid in bot_ids:
                tg.create_task(_retrain_one(bid))

        elapsed = time.perf_counter() - t0
        m.orchestrator_retrain_latency_seconds.observe(elapsed)

        n_ok = sum(1 for _, s in results if s == "triggered")
        n_fail = len(results) - n_ok
        report = (
            f"🔄 Nightly retrain complete: {n_ok}/{len(results)} bots triggered"
            f" ({n_fail} errors). Elapsed: {elapsed:.1f}s"
        )
        await self._telegram.send(report)
        log.info("nightly_retrain_complete", n_bots=len(results), n_ok=n_ok, elapsed_s=elapsed)
```

- [ ] **Step 4: Wire APScheduler jobs in `app/main.py`**

After the Phase 21c attribution scheduler block (around line 541), add:

```python
    # ── Phase 22a — PortfolioExposureGate + Correlation + NightlyRetrain ────
    from app.services.orchestrator.correlation import CorrelationService
    from app.services.orchestrator.exposure_gate import PortfolioExposureGate
    from app.services.orchestrator.retrain import NightlyRetrainJob

    _exposure_gate = PortfolioExposureGate(redis=redis)
    _app.state.exposure_gate = _exposure_gate

    _correlation_svc = CorrelationService(redis=redis)

    async def _run_correlation_update() -> None:
        """Daily correlation matrix refresh for all accounts with active bots."""
        try:
            async with session_factory() as corr_db:
                acct_rows = await corr_db.execute(
                    text(
                        "SELECT DISTINCT b.account_id"
                        " FROM bots b"
                        " WHERE b.deleted_at IS NULL AND b.status = 'running'"
                    )
                )
                acct_ids = [r[0] for r in acct_rows.all()]
            for acct_id in acct_ids:
                try:
                    async with session_factory() as corr_db2:
                        instr_rows = await corr_db2.execute(
                            text(
                                "SELECT DISTINCT p.instrument_id"
                                " FROM positions p"
                                " WHERE p.account_id = :acct"
                                "   AND p.qty != 0"
                            ),
                            {"acct": acct_id},
                        )
                        instr_ids = [r[0] for r in instr_rows.all()]
                        if instr_ids:
                            await _correlation_svc.compute_and_store(
                                account_id=acct_id,
                                instrument_ids=instr_ids,
                                db=corr_db2,
                            )
                except Exception:
                    log.exception("correlation_update_failed", account_id=str(acct_id))
        except Exception:
            log.exception("correlation_update_outer_failed")

    scheduler.add_job(
        _run_correlation_update,
        "cron",
        hour=1,
        minute=0,
        id="orchestrator_correlation_update",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    from app.services.param_tuner.service import BacktestSubmitter, ParamTunerService
    from app.services.param_tuner.types import TunerTrigger  # noqa: F401 (used in retrain)

    def _make_tuner(db):
        return ParamTunerService(
            ai_client=_app.state.ai_client,
            redis=redis,
            db_factory=session_factory,
            backtest_submitter=BacktestSubmitter(session_factory),
        )

    _nightly_retrain = NightlyRetrainJob(
        db_factory=session_factory,
        param_tuner_factory=_make_tuner,
        telegram=_app.state.telegram if hasattr(_app.state, "telegram") else None,
    )

    async def _run_nightly_retrain() -> None:
        try:
            await _nightly_retrain.run()
        except Exception:
            log.exception("nightly_retrain_outer_failed")

    scheduler.add_job(
        _run_nightly_retrain,
        "cron",
        hour=2,
        minute=0,
        id="orchestrator_nightly_retrain",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
```

Note: Telegram integration depends on whether `_app.state.telegram` exists at this point in lifespan. Check existing pattern in `main.py` for how other services access the Telegram notifier; use the same reference or pass `None` to disable Telegram for retrain if not available.

- [ ] **Step 5: Run tests**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/test_retrain.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: all PASS.

- [ ] **Step 6: Run full suite**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/ -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/orchestrator/retrain.py \
        backend/app/main.py \
        backend/tests/services/orchestrator/test_retrain.py
git commit -m "feat(22a-E): NightlyRetrainJob + APScheduler wiring (correlation 01:00, retrain 02:00)"
```

---

## Task F — REST API `/api/orchestrator/`

**Route:** Qwen

**Files:**
- Create: `backend/app/api/orchestrator.py`
- Modify: `backend/app/main.py` or router registration file (include new router)
- Create: `backend/tests/api/test_orchestrator.py`

**Gate:** after Tasks B, C, D

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/api/test_orchestrator.py
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_exposure_limits_empty(auth_client: AsyncClient) -> None:
    resp = await auth_client.get("/api/orchestrator/exposure-limits")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_exposure_limit_total_notional(admin_client: AsyncClient) -> None:
    resp = await admin_client.post(
        "/api/orchestrator/exposure-limits",
        json={
            "account_id": None,  # will be replaced with real UUID in fixture
            "limit_type": "total_notional",
            "max_notional": "100000",
            "currency": "USD",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["limit_type"] == "total_notional"
    assert data["max_notional"] == "100000.00000000"


@pytest.mark.asyncio
async def test_create_duplicate_total_notional_returns_409(admin_client: AsyncClient) -> None:
    """Partial unique index prevents two total_notional rows for same account."""
    payload = {
        "limit_type": "total_notional",
        "max_notional": "100000",
        "currency": "USD",
    }
    resp1 = await admin_client.post("/api/orchestrator/exposure-limits", json=payload)
    assert resp1.status_code == 201
    resp2 = await admin_client.post("/api/orchestrator/exposure-limits", json=payload)
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_put_auto_promote_criteria_valid(admin_client: AsyncClient, bot_id: str) -> None:
    resp = await admin_client.put(
        f"/api/orchestrator/bots/{bot_id}/auto-promote/criteria",
        json={
            "min_sharpe": 0.5,
            "max_drawdown": 0.15,
            "min_win_rate": 0.5,
            "auto_apply": False,
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_put_auto_promote_criteria_unknown_key_422(admin_client: AsyncClient, bot_id: str) -> None:
    resp = await admin_client.put(
        f"/api/orchestrator/bots/{bot_id}/auto-promote/criteria",
        json={
            "min_sharpe": 0.5,
            "max_drawdown": 0.15,
            "min_win_rate": 0.5,
            "unknown_field": 99,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_exposure_state(auth_client: AsyncClient) -> None:
    resp = await auth_client.get("/api/orchestrator/exposure")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_post_retrain_requires_admin(auth_client: AsyncClient) -> None:
    resp = await auth_client.post("/api/orchestrator/retrain")
    assert resp.status_code in (401, 403)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/api/test_orchestrator.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: 404 errors (router not registered).

- [ ] **Step 3: Implement `app/api/orchestrator.py`**

```python
# backend/app/api/orchestrator.py
from __future__ import annotations
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin_jwt, require_jwt
from app.services.orchestrator.auto_promote import AutoPromoteCriteria

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


class ExposureLimitCreate(BaseModel):
    account_id: UUID
    limit_type: str
    instrument_id: int | None = None
    max_notional: Decimal
    currency: str = "USD"


class ExposureLimitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    account_id: UUID
    limit_type: str
    instrument_id: int | None
    max_notional: Decimal
    currency: str
    enabled: bool


@router.get("/exposure-limits", response_model=list[ExposureLimitResponse])
async def list_exposure_limits(
    db: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[dict, Depends(require_jwt)],
) -> list[ExposureLimitResponse]:
    rows = (
        await db.execute(text("SELECT * FROM portfolio_exposure_limits ORDER BY id"))
    ).mappings().all()
    return [ExposureLimitResponse(**dict(r)) for r in rows]


@router.post("/exposure-limits", response_model=ExposureLimitResponse, status_code=201)
async def create_exposure_limit(
    body: ExposureLimitCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[dict, Depends(require_admin_jwt)],
) -> ExposureLimitResponse:
    if body.limit_type not in ("total_notional", "per_instrument"):
        raise HTTPException(422, "limit_type must be total_notional or per_instrument")
    try:
        row = (
            await db.execute(
                text(
                    "INSERT INTO portfolio_exposure_limits"
                    " (account_id, limit_type, instrument_id, max_notional, currency)"
                    " VALUES (:acct, :lt, :iid, :mn, :cur)"
                    " RETURNING *"
                ),
                {
                    "acct": body.account_id,
                    "lt": body.limit_type,
                    "iid": body.instrument_id,
                    "mn": body.max_notional,
                    "cur": body.currency,
                },
            )
        ).mappings().one()
        await db.commit()
        return ExposureLimitResponse(**dict(row))
    except Exception as exc:
        await db.rollback()
        if "uq_portfolio_exposure" in str(exc):
            raise HTTPException(409, "Duplicate limit for this account/type") from exc
        raise HTTPException(500, "Failed to create limit") from exc


@router.put("/exposure-limits/{limit_id}", response_model=ExposureLimitResponse)
async def update_exposure_limit(
    limit_id: int,
    body: ExposureLimitCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[dict, Depends(require_admin_jwt)],
) -> ExposureLimitResponse:
    row = (
        await db.execute(
            text(
                "UPDATE portfolio_exposure_limits"
                " SET max_notional=:mn, currency=:cur, enabled=true, updated_at=now()"
                " WHERE id=:id RETURNING *"
            ),
            {"mn": body.max_notional, "cur": body.currency, "id": limit_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(404)
    await db.commit()
    return ExposureLimitResponse(**dict(row))


@router.delete("/exposure-limits/{limit_id}", status_code=204)
async def delete_exposure_limit(
    limit_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[dict, Depends(require_admin_jwt)],
) -> None:
    result = await db.execute(
        text("DELETE FROM portfolio_exposure_limits WHERE id=:id"),
        {"id": limit_id},
    )
    if result.rowcount == 0:
        raise HTTPException(404)
    await db.commit()


@router.get("/exposure")
async def get_exposure_state(
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    _jwt: Annotated[dict, Depends(require_jwt)],
) -> dict:
    redis = request.app.state.redis
    # Fetch all accounts with running bots
    acct_rows = (
        await db.execute(
            text(
                "SELECT DISTINCT account_id FROM bots"
                " WHERE deleted_at IS NULL AND status='running'"
            )
        )
    ).all()
    result = {}
    for (acct_id,) in acct_rows:
        raw = await redis.hgetall(f"portfolio:exposure:{acct_id}")
        result[str(acct_id)] = {
            k.decode() if isinstance(k, bytes) else k: float(
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in raw.items()
        }
    return result


@router.put("/bots/{bot_id}/auto-promote/criteria", status_code=200)
async def set_auto_promote_criteria(
    bot_id: UUID,
    body: AutoPromoteCriteria,
    db: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[dict, Depends(require_admin_jwt)],
) -> dict:
    import json
    row = (
        await db.execute(
            text(
                "UPDATE bots SET auto_promote_criteria=:c::jsonb"
                " WHERE id=:bid AND deleted_at IS NULL RETURNING id"
            ),
            {"c": json.dumps(body.model_dump()), "bid": bot_id},
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404)
    await db.commit()
    return {"status": "ok", "bot_id": str(bot_id)}


@router.post("/bots/{bot_id}/auto-promote/evaluate", status_code=200)
async def trigger_auto_promote_evaluate(
    bot_id: UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[dict, Depends(require_admin_jwt)],
) -> dict:
    evaluator = getattr(request.app.state, "auto_promote_evaluator", None)
    if evaluator is None:
        raise HTTPException(503, "AutoPromoteEvaluator not wired")
    # Find shadow bot for this live bot
    shadow_row = (
        await db.execute(
            text(
                "SELECT id FROM bots WHERE shadow_of=:lid AND is_shadow=true"
                " AND deleted_at IS NULL LIMIT 1"
            ),
            {"lid": bot_id},
        )
    ).scalar_one_or_none()
    if shadow_row is None:
        raise HTTPException(404, "No shadow bot found for this live bot")
    result = await evaluator.evaluate(bot_id, shadow_row, db)
    return {"outcome": result}


@router.post("/retrain", status_code=202)
async def trigger_retrain(
    request: Request,
    _jwt: Annotated[dict, Depends(require_admin_jwt)],
) -> dict:
    import asyncio
    retrain_job = getattr(request.app.state, "nightly_retrain", None)
    if retrain_job is None:
        raise HTTPException(503, "NightlyRetrainJob not wired")
    asyncio.ensure_future(retrain_job.run())
    return {"status": "accepted"}
```

- [ ] **Step 4: Register router in `app/main.py`** (or wherever `include_router` calls are made)

Find the block where other routers are included (e.g., `app.include_router(bots.router)`) and add:
```python
from app.api.orchestrator import router as orchestrator_router
app.include_router(orchestrator_router)
```

Also wire `_app.state.nightly_retrain = _nightly_retrain` and `_app.state.auto_promote_evaluator` after creating those objects in Task E.

- [ ] **Step 5: Run tests**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/api/test_orchestrator.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: all PASS.

- [ ] **Step 6: Run full suite**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/ -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/orchestrator.py \
        backend/app/main.py \
        backend/tests/api/test_orchestrator.py
git commit -m "feat(22a-F): REST /api/orchestrator/* (exposure limits CRUD + auto-promote + retrain trigger)"
```

---

## Task G — Reviewer Chain

**Route:** Codex (spec-compliance + code-quality) + Sonnet reviewers

**Gate:** after Task F

Run the per-chunk reviewer chain per `docs/CLAUDE.md` routing:

```
spec-compliance + python-reviewer → haiku
code-quality + security-reviewer → sonnet
database-reviewer → sonnet
```

Inline spec slice for reviewers: `docs/superpowers/specs/2026-05-19-phase22-bot-engine-v3-design.md` §3 (Phase 22a sections).

Apply all CRIT + HIGH + MED findings inline before close-out.

---

## Task H — Close-out

**Route:** Opus direct

**Gate:** after all reviews applied

- [ ] **Step 1: Update `CLAUDE.md` shipped phases table**

In `docs/CLAUDE.md`, add to the shipped phases table:
```
| 22a — BotOrchestrator + Auto-Promotion | 0.22.0 | PortfolioExposureGate (station 5.75, Lua fills, PG fallback fail-CLOSED); CorrelationService (Pearson, Redis 86400s TTL); AutoPromoteEvaluator (replaces always-False stub, fire-once guard); NightlyRetrainJob (asyncio.gather + semaphore, 02:00 UTC); fx.py (promote _fx_rate); alembic 0069; 8 REST |
```

- [ ] **Step 2: Update `CHANGELOG.md`**

Add Phase 22a entry under the new `[0.22.0]` section.

- [ ] **Step 3: Update `TASKS.md`**

Mark Phase 22a complete; add 22b as next.

- [ ] **Step 4: Tag v0.22.0**

```bash
git tag v0.22.0
git push origin main --tags
```

---

## Self-Review Checklist

**Spec coverage:**
- §3.1 (Alembic 0069) → Task A ✓
- §3.2 (ExposureGate station 5.75 + Lua + C3 fail-CLOSED + H7 fx.py) → Task B ✓
- §3.3 (CorrelationService) → Task C ✓
- §3.4 (AutoPromoteEvaluator + M1 AutoPromoteCriteria + H4 fire-once) → Task D ✓
- §3.5 (NightlyRetrainJob + H3 semaphore + max_instances) → Task E ✓
- §3.6 (Prometheus metrics) → Task B/metrics.py ✓
- §3.7 (REST API) → Task F ✓
- §3.8 chunk routing → matched (A=Qwen, B=Codex, C=Qwen, D=Qwen, E=Codex, F=Qwen) ✓

**Deferred (spec §8, confirmed not in 22a):**
- per_sector limits (C4) — no `instruments.sector` column
- marginal-variance in ExposureGate (H1) — raw notional used
- veto window for auto-promote (spec §8 last row) — 22a.1

**Type consistency:**
- `ExposureOutcome.ALLOW/WARN/BLOCK` used in context.py integration ✓
- `AutoPromoteCriteria` validated at API boundary, stored as JSONB ✓
- `PortfolioExposureGate.update_on_fill(account_id, instrument_id, signed_delta_usd)` called from BotFillRouter ✓
- `NightlyRetrainJob` uses `TunerTrigger.SCHEDULED` (matches param_tuner/types.py) ✓
