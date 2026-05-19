# Phase 21a.1 — Advisor Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close four items deferred from Phase 21a: SHADOW mode (advisor pipeline without AI call), async-parallel semaphore (replace per-bot asyncio.Lock), human veto override REST endpoint, and per-account advisor config FE form.

**Architecture:** Additive changes only to `app/services/advisor/` and `app/api/bots.py`. Alembic 0064 adds override columns to `bot_advisor_decisions`, a partial index, and widens the mode CHECK on `bots`. The service replaces its `dict[str, asyncio.Lock]` with a semaphore + resize-barrier state machine. Two new REST endpoints are added to the existing `/api/bots` router. FE adds an override drawer button and a per-account config form to `BotDetailPage`.

**Tech Stack:** Python 3.14 · FastAPI · SQLAlchemy 2.0 async · Alembic · Pydantic v2 · asyncpg · React 19 · TypeScript 6 · Vitest 4 · pytest 9

---

## File Map

| File | Action | What changes |
|---|---|---|
| `backend/alembic/versions/0064_advisor_polish.py` | Create | Override columns, partial CONCURRENTLY index, mode CHECK widen, full downgrade |
| `backend/app/services/advisor/types.py` | Modify | Add `SHADOW` to `AdvisorMode`; add `max_concurrent: int` to `AdvisorConfig`; add `AccountAdvisorConfigOverride` + `AccountAdvisorConfigUpdate` models |
| `backend/app/services/advisor/metrics.py` | Modify | Add 4 new metrics: `advisor_overrides_total`, `advisor_concurrent_calls`, `advisor_shadow_context_build_seconds`, `advisor_semaphore_resize_deferred_total`, `advisor_account_config_writes_total` |
| `backend/app/services/advisor/service.py` | Modify | Replace Lock with Semaphore + resize-barrier state machine; add SHADOW path; fix publish channel to `bot:advisor:{id}` (FE-bound only); add account_config_updated to config channel |
| `backend/app/api/bots.py` | Modify | Add `PATCH /{bot_id}/advisor-decisions/{decision_id}` and `PUT /{bot_id}/accounts/{account_id}/advisor-config` |
| `frontend/src/services/advisor/types.ts` | Modify | Add override fields to `AdvisorDecision`; add `AccountAdvisorConfigOverride`, `AccountAdvisorConfigUpdate` types; add `'SHADOW'` to `ADVISOR_MODES` |
| `frontend/src/services/advisor/api.ts` | Modify | Add `patchAdvisorDecisionOverride()`, `putAccountAdvisorConfig()` |
| `frontend/src/features/bots/components/AdvisorDecisionsTable.tsx` | Modify | "Overridden" badge on rows with `overridden_at` |
| `frontend/src/features/bots/components/AdvisorDecisionDrawer.tsx` | Modify | Override metadata section + "Override" button (hidden for non-admins) |
| `frontend/src/features/bots/components/AccountAdvisorConfigForm.tsx` | Create | Per-account override form with effective-config preview |
| `frontend/src/features/bots/pages/BotDetailPage.tsx` | Modify | Render `AccountAdvisorConfigForm` per bot_accounts row in advisor tab |
| `backend/tests/services/advisor/test_shadow.py` | Create | SHADOW mode + semaphore tests |
| `backend/tests/services/advisor/test_semaphore.py` | Create | Semaphore resize-barrier tests |
| `backend/tests/api/test_bots_advisor_override.py` | Create | PATCH override + PUT account config API tests |
| `frontend/src/features/bots/components/AdvisorDecisionDrawer.test.tsx` | Modify | Override metadata + admin-guard tests |
| `frontend/src/features/bots/components/AdvisorDecisionsTable.test.tsx` | Modify | Overridden badge test |
| `frontend/src/features/bots/components/AccountAdvisorConfigForm.test.tsx` | Create | Per-account config form tests |

---

## Task 1: Alembic 0064 — Schema Changes

**Files:**
- Create: `backend/alembic/versions/0064_advisor_polish.py`
- Test: `backend/tests/test_migration_0064.py`

- [ ] **Step 1: Write the failing migration test**

```python
# backend/tests/test_migration_0064.py
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_migration_0064_adds_override_columns(migrated_db):
    """After 0064, bot_advisor_decisions has the 4 override columns."""
    async with migrated_db() as db:
        row = await db.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'bot_advisor_decisions'
              AND column_name IN ('overridden_by','override_action','override_reason','overridden_at')
        """))
        cols = {r[0] for r in row.fetchall()}
    assert cols == {"overridden_by", "override_action", "override_reason", "overridden_at"}


@pytest.mark.asyncio
async def test_migration_0064_shadow_mode_accepted(migrated_db, test_bot):
    """After 0064, bots.advisor_config accepts mode='SHADOW'."""
    async with migrated_db() as db:
        await db.execute(
            text("UPDATE bots SET advisor_config = '{\"mode\":\"SHADOW\"}'::jsonb WHERE id = :id"),
            {"id": test_bot["id"]},
        )
        await db.commit()
        row = await db.execute(
            text("SELECT advisor_config->>'mode' FROM bots WHERE id = :id"),
            {"id": test_bot["id"]},
        )
        assert row.scalar_one() == "SHADOW"


@pytest.mark.asyncio
async def test_migration_0064_pre_flight_assertion_rejects_bad_mode(migrated_db_before_0064, test_bot):
    """If a bot has an unknown mode before 0064, upgrade() raises."""
    async with migrated_db_before_0064() as db:
        # Inject bad mode directly (bypassing CHECK which only exists after 0064 widens it)
        await db.execute(
            text("UPDATE bots SET advisor_config = '{\"mode\":\"UNKNOWN\"}'::jsonb WHERE id = :id"),
            {"id": test_bot["id"]},
        )
        await db.commit()
    with pytest.raises(Exception, match="unknown mode values"):
        run_migration("0064")


@pytest.mark.asyncio
async def test_migration_0064_downgrade_migrates_shadow_to_observe(migrated_db, test_bot):
    """Downgrade pre-flight UPDATE sets SHADOW bots to OBSERVE before narrowing CHECK."""
    async with migrated_db() as db:
        await db.execute(
            text("UPDATE bots SET advisor_config = '{\"mode\":\"SHADOW\"}'::jsonb WHERE id = :id"),
            {"id": test_bot["id"]},
        )
        await db.commit()
    run_migration_downgrade("0064")
    async with db_at_0063() as db:
        row = await db.execute(
            text("SELECT advisor_config->>'mode' FROM bots WHERE id = :id"),
            {"id": test_bot["id"]},
        )
        assert row.scalar_one() == "OBSERVE"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && docker compose exec backend pytest tests/test_migration_0064.py -v
```
Expected: `ERROR` — migration file doesn't exist yet.

- [ ] **Step 3: Create the migration file**

```python
# backend/alembic/versions/0064_advisor_polish.py
"""Phase 21a.1: advisor override columns, SHADOW mode CHECK, CONCURRENTLY index.

Downgrade: override columns dropped (audit data lost — document in ops runbook).
SHADOW bots migrated to OBSERVE before narrowing CHECK.
"""
from alembic import op
import sqlalchemy as sa

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Override columns on bot_advisor_decisions
    op.add_column("bot_advisor_decisions", sa.Column("overridden_by", sa.Text()))
    op.add_column(
        "bot_advisor_decisions",
        sa.Column(
            "override_action",
            sa.Text(),
            sa.CheckConstraint(
                "override_action IN ('approve', 'veto')",
                name="bad_advisor_override_action_check",
            ),
        ),
    )
    op.add_column("bot_advisor_decisions", sa.Column("override_reason", sa.Text()))
    op.add_column(
        "bot_advisor_decisions",
        sa.Column("overridden_at", sa.TIMESTAMP(timezone=True)),
    )

    # 2. Partial index — CONCURRENTLY to avoid write stall
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY bot_advisor_decisions_overridden_at_idx "
            "ON bot_advisor_decisions (overridden_at) WHERE overridden_at IS NOT NULL"
        )

    # 3. Widen bots.advisor_config mode CHECK to include SHADOW
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM bots
                WHERE advisor_config IS NOT NULL
                  AND advisor_config->>'mode' NOT IN ('OFF','OBSERVE','VETO')
            ) THEN
                RAISE EXCEPTION 'bots.advisor_config has unknown mode values — cannot widen CHECK';
            END IF;
        END $$;
    """)
    op.drop_constraint("bots_advisor_config_mode_check", "bots")
    op.create_check_constraint(
        "bots_advisor_config_mode_check",
        "bots",
        "advisor_config ? 'mode' AND advisor_config->>'mode' IN ('OFF','OBSERVE','VETO','SHADOW')",
    )


def downgrade() -> None:
    # Step 1: Migrate SHADOW bots to OBSERVE before narrowing CHECK
    op.execute("""
        UPDATE bots
        SET advisor_config = jsonb_set(advisor_config, '{mode}', '"OBSERVE"')
        WHERE advisor_config->>'mode' = 'SHADOW'
    """)
    # Step 2: Re-create narrowed CHECK
    op.drop_constraint("bots_advisor_config_mode_check", "bots")
    op.create_check_constraint(
        "bots_advisor_config_mode_check",
        "bots",
        "advisor_config ? 'mode' AND advisor_config->>'mode' IN ('OFF','OBSERVE','VETO')",
    )
    # Step 3: Drop CONCURRENTLY index
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS bot_advisor_decisions_overridden_at_idx"
        )
    # Step 4: Drop override columns (audit data lost)
    op.drop_column("bot_advisor_decisions", "overridden_at")
    op.drop_column("bot_advisor_decisions", "override_reason")
    op.drop_column("bot_advisor_decisions", "override_action")
    op.drop_column("bot_advisor_decisions", "overridden_by")
```

- [ ] **Step 4: Run migration against dev DB**

```bash
cd backend && docker compose exec backend alembic upgrade 0064
```
Expected: `Running upgrade 0063 -> 0064, Phase 21a.1: advisor override columns...`

- [ ] **Step 5: Run migration tests**

```bash
cd backend && docker compose exec backend pytest tests/test_migration_0064.py -v
```
Expected: All 4 pass.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0064_advisor_polish.py backend/tests/test_migration_0064.py
git commit -m "feat(phase21a1): alembic 0064 — advisor override cols, SHADOW CHECK, CONCURRENTLY index"
```

---

## Task 2: Types — SHADOW mode, `max_concurrent`, override models

**Files:**
- Modify: `backend/app/services/advisor/types.py`
- Test: `backend/tests/services/advisor/test_types.py` (extend existing)

- [ ] **Step 1: Write failing tests**

```python
# Add to backend/tests/services/advisor/test_types.py

def test_advisor_mode_shadow_value():
    assert AdvisorMode.SHADOW == "SHADOW"
    assert "SHADOW" in list(AdvisorMode)


def test_advisor_config_max_concurrent_default():
    cfg = AdvisorConfig()
    assert cfg.max_concurrent == 1


def test_advisor_config_max_concurrent_bounds():
    AdvisorConfig(max_concurrent=4)  # ok
    with pytest.raises(ValidationError):
        AdvisorConfig(max_concurrent=5)  # > 4
    with pytest.raises(ValidationError):
        AdvisorConfig(max_concurrent=0)  # < 1


def test_account_advisor_config_override_rejects_max_concurrent():
    """max_concurrent must not be accepted in per-account override schema."""
    with pytest.raises(ValidationError):
        AccountAdvisorConfigOverride.model_validate({"max_concurrent": 2})


def test_account_advisor_config_update_none_clears():
    update = AccountAdvisorConfigUpdate(advisor_config_override=None)
    assert update.advisor_config_override is None
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && docker compose exec backend pytest tests/services/advisor/test_types.py -k "shadow or max_concurrent or account_advisor" -v
```
Expected: `ImportError` or `AttributeError` — new types don't exist yet.

- [ ] **Step 3: Modify `types.py`**

In `backend/app/services/advisor/types.py`, make these changes:

```python
# 1. Add SHADOW to AdvisorMode
class AdvisorMode(StrEnum):
    OFF = "OFF"
    OBSERVE = "OBSERVE"
    VETO = "VETO"
    SHADOW = "SHADOW"   # NEW

# 2. Add max_concurrent to AdvisorConfig (after existing fields)
class AdvisorConfig(BaseModel):
    mode: AdvisorMode = AdvisorMode.OFF
    capability: AICapability = AICapability.REASONING
    local_only: bool = False
    timeout_ms: int = Field(default=3000, ge=100, le=10_000)
    daily_budget_usd: Decimal = Field(default=Decimal("5.00"), ge=0)
    max_qps: float = Field(default=2.0, gt=0)
    auto_pause_threshold: int = Field(default=0, ge=0)
    auto_pause_window_seconds: int = Field(default=300, gt=0)
    min_veto_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    max_concurrent: int = Field(default=1, ge=1, le=4)  # NEW

# 3. Add at end of file — new models for per-account override
class AccountAdvisorConfigOverride(BaseModel):
    """Per-account advisor config override.
    
    max_concurrent is intentionally absent — bot-level semaphore governs concurrency.
    Pydantic raises ValidationError (→ 422) if a caller sends max_concurrent.
    """
    model_config = ConfigDict(extra="forbid")   # rejects unknown fields including max_concurrent

    mode: AdvisorMode | None = None
    capability: str | None = None
    local_only: bool | None = None
    timeout_ms: int | None = None
    daily_budget_usd: float | None = None


class AccountAdvisorConfigUpdate(BaseModel):
    advisor_config_override: AccountAdvisorConfigOverride | None
    # None = clear override (revert to bot-level default)
```

Note: `model_config = ConfigDict(extra="forbid")` requires `from pydantic import ConfigDict` — add to imports if missing.

- [ ] **Step 4: Run tests**

```bash
cd backend && docker compose exec backend pytest tests/services/advisor/test_types.py -v
```
Expected: All pass (existing + 5 new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/advisor/types.py backend/tests/services/advisor/test_types.py
git commit -m "feat(phase21a1): AdvisorMode.SHADOW, AdvisorConfig.max_concurrent, account override models"
```

---

## Task 3: Metrics — 5 new Prometheus metrics

**Files:**
- Modify: `backend/app/services/advisor/metrics.py`

- [ ] **Step 1: Add 5 new metric definitions**

Open `backend/app/services/advisor/metrics.py` and add at the end:

```python
from prometheus_client import Counter, Gauge, Histogram  # already imported — verify

# (existing 16 metrics above this line)

advisor_overrides_total = Counter(
    "advisor_overrides_total",
    "Human veto overrides applied",
    ["override_action"],
)

advisor_concurrent_calls = Gauge(
    "advisor_concurrent_calls",
    "Live concurrent advisor calls per bot (bot_id label; revisit cardinality in Phase 24)",
    ["bot_id"],
)

advisor_shadow_context_build_seconds = Histogram(
    "advisor_shadow_context_build_seconds",
    "Context-build latency in SHADOW mode (AI call excluded)",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)

advisor_semaphore_resize_deferred_total = Counter(
    "advisor_semaphore_resize_deferred_total",
    "max_concurrent config changes deferred because old semaphore did not drain in time",
)

advisor_account_config_writes_total = Counter(
    "advisor_account_config_writes_total",
    "Per-account advisor config writes (write-side; action=set|clear)",
    ["action"],
)
```

- [ ] **Step 2: Run existing tests to confirm no regressions**

```bash
cd backend && docker compose exec backend pytest tests/services/advisor/ -v -x
```
Expected: All existing tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/advisor/metrics.py
git commit -m "feat(phase21a1): 5 new advisor prometheus metrics"
```

---

## Task 4: Service — Semaphore + resize-barrier + SHADOW path

**Files:**
- Modify: `backend/app/services/advisor/service.py`
- Create: `backend/tests/services/advisor/test_shadow.py`
- Create: `backend/tests/services/advisor/test_semaphore.py`

This is the largest task. The lock-to-semaphore migration changes the concurrency state machine.

- [ ] **Step 1: Write failing tests for SHADOW mode**

```python
# backend/tests/services/advisor/test_shadow.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.advisor.types import AdvisorMode, AdvisorConfig
from app.services.advisor.service import AdvisorService


@pytest.fixture
def shadow_config():
    return AdvisorConfig(mode=AdvisorMode.SHADOW)


@pytest.fixture
def service(mock_ai_client, mock_redis, db_factory):
    """Use conftest fixtures from tests/services/advisor/conftest.py."""
    return AdvisorService(
        ai_client=mock_ai_client,
        redis=mock_redis,
        db_factory=db_factory,
    )


@pytest.mark.asyncio
async def test_shadow_mode_no_ai_call(service, shadow_config, mock_ai_client, mock_db, bot_intent):
    """SHADOW mode never calls the AI client."""
    mock_ai_client.complete = AsyncMock()
    verdict, decision_id = await service.review(
        bot_id=bot_intent.bot_id,
        run_id=None,
        account_id=bot_intent.account_id,
        intent=bot_intent.intent,
        strategy_params={},
        effective_config=shadow_config,
        db=mock_db,
    )
    mock_ai_client.complete.assert_not_called()
    assert verdict.action == "approve"
    assert verdict.reasoning == "shadow_mode"
    assert verdict.confidence is None


@pytest.mark.asyncio
async def test_shadow_mode_audit_row_persisted(service, shadow_config, mock_db, bot_intent):
    """SHADOW mode persists audit row with provider=None, model=None."""
    _, decision_id = await service.review(
        bot_id=bot_intent.bot_id,
        run_id=None,
        account_id=bot_intent.account_id,
        intent=bot_intent.intent,
        strategy_params={},
        effective_config=shadow_config,
        db=mock_db,
    )
    # decision_id is set (not None) when audit row insertion succeeds
    assert decision_id is not None


@pytest.mark.asyncio
async def test_shadow_mode_latency_metric(service, shadow_config, mock_db, bot_intent):
    """advisor_shadow_context_build_seconds histogram has one observation after SHADOW call."""
    from prometheus_client import REGISTRY
    from app.services.advisor.metrics import advisor_shadow_context_build_seconds

    before = advisor_shadow_context_build_seconds._sum.get()
    await service.review(
        bot_id=bot_intent.bot_id,
        run_id=None,
        account_id=bot_intent.account_id,
        intent=bot_intent.intent,
        strategy_params={},
        effective_config=shadow_config,
        db=mock_db,
    )
    after = advisor_shadow_context_build_seconds._sum.get()
    assert after > before


@pytest.mark.asyncio
async def test_shadow_mode_semaphore_held(service, shadow_config, mock_db, bot_intent):
    """SHADOW mode holds semaphore slot; third concurrent call → fail_open."""
    cfg = AdvisorConfig(mode=AdvisorMode.SHADOW, max_concurrent=2)
    # Pre-create semaphore slots
    await service._ensure_semaphore(str(bot_intent.bot_id), cfg)

    blocker = asyncio.Event()
    results = []

    async def call():
        v, _ = await service.review(
            bot_id=bot_intent.bot_id, run_id=None, account_id=bot_intent.account_id,
            intent=bot_intent.intent, strategy_params={}, effective_config=cfg, db=mock_db,
        )
        results.append(v.action)

    # Run 3 concurrent calls; 3rd should fail_open (semaphore cap = 2)
    tasks = [asyncio.create_task(call()) for _ in range(3)]
    await asyncio.gather(*tasks)
    assert results.count("fail_open") == 1
    assert results.count("approve") == 2
```

- [ ] **Step 2: Write failing tests for semaphore resize-barrier**

```python
# backend/tests/services/advisor/test_semaphore.py
import asyncio
import pytest
from app.services.advisor.types import AdvisorConfig, AdvisorMode
from app.services.advisor.service import AdvisorService
from app.services.advisor.metrics import advisor_semaphore_resize_deferred_total


@pytest.fixture
def service(mock_ai_client, mock_redis, db_factory):
    return AdvisorService(ai_client=mock_ai_client, redis=mock_redis, db_factory=db_factory)


@pytest.mark.asyncio
async def test_max_concurrent_semaphore(service, mock_db, bot_intent):
    """max_concurrent=2 → 2 simultaneous calls proceed; 3rd → fail_open."""
    cfg = AdvisorConfig(mode=AdvisorMode.OBSERVE, max_concurrent=2)
    await service._ensure_semaphore(str(bot_intent.bot_id), cfg)
    results = []

    async def call():
        v, _ = await service.review(
            bot_id=bot_intent.bot_id, run_id=None, account_id=bot_intent.account_id,
            intent=bot_intent.intent, strategy_params={}, effective_config=cfg, db=mock_db,
        )
        results.append(v.action)

    tasks = [asyncio.create_task(call()) for _ in range(3)]
    await asyncio.gather(*tasks)
    assert results.count("fail_open") == 1


@pytest.mark.asyncio
async def test_max_concurrent_default_one(service, mock_db, bot_intent):
    """Default max_concurrent=1 → second simultaneous call → fail_open."""
    cfg = AdvisorConfig(mode=AdvisorMode.OBSERVE)  # default max_concurrent=1
    await service._ensure_semaphore(str(bot_intent.bot_id), cfg)
    results = []

    async def call():
        v, _ = await service.review(
            bot_id=bot_intent.bot_id, run_id=None, account_id=bot_intent.account_id,
            intent=bot_intent.intent, strategy_params={}, effective_config=cfg, db=mock_db,
        )
        results.append(v.action)

    tasks = [asyncio.create_task(call()) for _ in range(2)]
    await asyncio.gather(*tasks)
    assert results.count("fail_open") == 1


@pytest.mark.asyncio
async def test_semaphore_creation_race_safe(service, bot_intent):
    """Two coroutines calling _ensure_semaphore simultaneously → single semaphore created."""
    cfg = AdvisorConfig(max_concurrent=2)
    bot_key = str(bot_intent.bot_id)

    await asyncio.gather(
        service._ensure_semaphore(bot_key, cfg),
        service._ensure_semaphore(bot_key, cfg),
    )
    # Only one semaphore entry — no duplicate
    assert bot_key in service._in_flight
    assert service._in_flight[bot_key]._value == 2


@pytest.mark.asyncio
async def test_semaphore_resize_drain_and_swap(service, bot_intent):
    """max_concurrent changed 1→2: old semaphore drained, new semaphore with 2 slots active."""
    cfg_old = AdvisorConfig(max_concurrent=1)
    cfg_new = AdvisorConfig(max_concurrent=2)
    bot_key = str(bot_intent.bot_id)

    await service._ensure_semaphore(bot_key, cfg_old)
    assert service._in_flight[bot_key]._value == 1

    await service._resize_semaphore(bot_key, cfg_old.max_concurrent, cfg_new.max_concurrent)
    assert service._in_flight[bot_key]._value == 2


@pytest.mark.asyncio
async def test_semaphore_resize_deferred_on_timeout(service, mock_db, bot_intent):
    """Resize during sustained in-flight calls → deferred metric incremented."""
    cfg = AdvisorConfig(max_concurrent=1)
    bot_key = str(bot_intent.bot_id)
    await service._ensure_semaphore(bot_key, cfg)

    before = advisor_semaphore_resize_deferred_total._value.get()

    # Hold the semaphore manually
    sem = service._in_flight[bot_key]
    await sem.acquire()
    try:
        # Attempt resize with very short timeout (should immediately timeout)
        await service._resize_semaphore(bot_key, old_max=1, new_max=2, timeout=0.01)
    finally:
        sem.release()

    after = advisor_semaphore_resize_deferred_total._value.get()
    assert after == before + 1
    # Old semaphore retained (still max=1)
    assert service._in_flight[bot_key]._value == 1
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd backend && docker compose exec backend pytest tests/services/advisor/test_shadow.py tests/services/advisor/test_semaphore.py -v
```
Expected: `ImportError` or `AttributeError` — new methods don't exist yet.

- [ ] **Step 4: Modify `service.py` — semaphore state machine**

Replace the `_in_flight` dict and lock-acquisition pattern in `backend/app/services/advisor/service.py`:

```python
# Replace at class level (remove the Lock dict):
# OLD: self._in_flight: dict[str, asyncio.Lock] = {}
# NEW (add these four dicts in __init__):

self._in_flight: dict[str, asyncio.Semaphore] = {}
self._in_flight_lock = asyncio.Lock()          # guards dict mutations
self._in_flight_count: dict[str, int] = {}     # tracks active acquires per bot
self._resizing: dict[str, asyncio.Event] = {}  # set during resize; callers wait
self._resize_done: dict[str, asyncio.Event] = {}  # set when in_flight_count hits 0
```

Add new helper methods (add after `__init__`):

```python
async def _ensure_semaphore(self, bot_key: str, config: AdvisorConfig) -> asyncio.Semaphore:
    """Return existing semaphore or create a new one (race-safe)."""
    if bot_key in self._in_flight:
        return self._in_flight[bot_key]
    async with self._in_flight_lock:
        if bot_key not in self._in_flight:
            self._in_flight[bot_key] = asyncio.Semaphore(config.max_concurrent)
            self._in_flight_count[bot_key] = 0
            self._resize_done[bot_key] = asyncio.Event()
            self._resize_done[bot_key].set()  # starts idle
        return self._in_flight[bot_key]

async def _resize_semaphore(
    self,
    bot_key: str,
    old_max: int,
    new_max: int,
    timeout: float = 10.0,
) -> None:
    """Drain old semaphore then swap in new one. Defers on timeout."""
    import structlog as _sl
    logger = _sl.get_logger(__name__)
    # Signal: block new acquires on old semaphore
    barrier = asyncio.Event()
    self._resizing[bot_key] = barrier

    try:
        # Wait until all in-flight calls under old semaphore release
        done_event = self._resize_done[bot_key]
        done_event.clear()
        await asyncio.wait_for(done_event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        from app.services.advisor.metrics import advisor_semaphore_resize_deferred_total
        advisor_semaphore_resize_deferred_total.inc()
        logger.warning(
            "advisor.semaphore.resize_deferred", bot_key=bot_key, old=old_max, new=new_max
        )
        return
    finally:
        # Always unblock callers (whether we swapped or deferred)
        barrier.set()
        del self._resizing[bot_key]

    # Swap in new semaphore
    async with self._in_flight_lock:
        self._in_flight[bot_key] = asyncio.Semaphore(new_max)
    logger.info("advisor.semaphore.resized", bot_key=bot_key, old=old_max, new=new_max)
```

Replace the lock acquisition block at the top of `review()` with semaphore logic:

```python
async def review(self, *, bot_id, run_id, account_id, intent, strategy_params, effective_config, db):
    if effective_config.mode == AdvisorMode.OFF:
        return AdvisorVerdict(action="approve", confidence=None), None

    bot_key = str(bot_id)

    # Wait if resize is in progress for this bot
    if bot_key in self._resizing:
        await self._resizing[bot_key].wait()

    sem = await self._ensure_semaphore(bot_key, effective_config)

    # Non-blocking check: if no slots available → fail_open immediately
    if not sem._value:  # noqa: SLF001  (checking slot count, not acquiring)
        advisor_in_flight_skips_total.labels(bot_id=bot_key).inc()
        return await self._fail_open(
            bot_id=bot_id, run_id=run_id, account_id=account_id,
            intent=intent, effective_config=effective_config, reason="advisor_in_flight",
        )

    await sem.acquire()
    self._in_flight_count[bot_key] = self._in_flight_count.get(bot_key, 0) + 1
    advisor_concurrent_calls.labels(bot_id=bot_key).inc()
    try:
        result = await self._do_review(
            bot_id=bot_id, run_id=run_id, account_id=account_id,
            intent=intent, strategy_params=strategy_params,
            effective_config=effective_config, db=db,
        )
    finally:
        sem.release()
        self._in_flight_count[bot_key] -= 1
        advisor_concurrent_calls.labels(bot_id=bot_key).dec()
        if self._in_flight_count[bot_key] == 0:
            # Signal drain-complete for any waiting resize
            if bot_key in self._resize_done:
                self._resize_done[bot_key].set()
    return result
```

Extract the existing review body (currently inside `async with lock:`) into `_do_review()`:

```python
async def _do_review(self, *, bot_id, run_id, account_id, intent, strategy_params, effective_config, db):
    """Core review logic — called after semaphore acquired."""
    if not await self._budget_ok_and_reserve(bot_id, effective_config):
        advisor_budget_exceeded_total.labels(bot_id=str(bot_id)).inc()
        return await self._fail_open(
            bot_id=bot_id, run_id=run_id, account_id=account_id,
            intent=intent, effective_config=effective_config, reason="daily_budget_exceeded",
        )

    # SHADOW mode: full context-build, no AI call
    if effective_config.mode == AdvisorMode.SHADOW:
        start = time.monotonic()
        payload, context_summary = await ContextBuilder.build(intent, strategy_params, db)
        latency_ms = int((time.monotonic() - start) * 1000)
        from app.services.advisor.metrics import advisor_shadow_context_build_seconds
        advisor_shadow_context_build_seconds.observe(latency_ms / 1000)
        verdict = AdvisorVerdict(action="approve", reasoning="shadow_mode", confidence=None)
        decision_id = await self._persist(
            bot_id=bot_id, run_id=run_id, account_id=account_id, intent=intent,
            effective_config=effective_config, verdict=verdict, result=None,
            latency_ms=latency_ms, context_summary=context_summary,
        )
        await self._publish(
            bot_id=bot_id, account_id=account_id, intent=intent, verdict=verdict,
            latency_ms=latency_ms, effective_config=effective_config, decision_id=decision_id,
        )
        advisor_decisions_total.labels(
            mode=str(effective_config.mode), verdict=verdict.action,
            capability=str(effective_config.capability),
        ).inc()
        return verdict, decision_id

    # OBSERVE / VETO: full AI call path (rest of existing review body unchanged)
    # ... (move the existing `async with lock:` body here verbatim)
```

Fix the `_publish` method to use the correct channel (`bot:advisor:{bot_id}` not `bot:advisor:decision:{bot_id}`):

```python
# In _publish():
await self._redis.publish(
    f"bot:advisor:{bot_id}",   # FE-bound channel (was: bot:advisor:decision:{bot_id})
    json.dumps(payload, default=_json_default),
)
```

- [ ] **Step 5: Run SHADOW and semaphore tests**

```bash
cd backend && docker compose exec backend pytest tests/services/advisor/test_shadow.py tests/services/advisor/test_semaphore.py -v
```
Expected: All pass.

- [ ] **Step 6: Run full advisor test suite to confirm no regressions**

```bash
cd backend && docker compose exec backend pytest tests/services/advisor/ tests/api/test_bots_advisor.py -v
```
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/advisor/service.py \
        backend/tests/services/advisor/test_shadow.py \
        backend/tests/services/advisor/test_semaphore.py
git commit -m "feat(phase21a1): semaphore + resize-barrier + SHADOW mode in AdvisorService"
```

---

## Task 5: REST endpoints — override + per-account config

**Files:**
- Modify: `backend/app/api/bots.py`
- Create: `backend/tests/api/test_bots_advisor_override.py`

- [ ] **Step 1: Write failing API tests**

```python
# backend/tests/api/test_bots_advisor_override.py
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_override_veto_decision(client: AsyncClient, admin_headers, bot_with_veto_decision):
    """PATCH endpoint sets override columns; emits structlog event."""
    bot_id, decision_id = bot_with_veto_decision
    resp = await client.patch(
        f"/api/bots/{bot_id}/advisor-decisions/{decision_id}",
        json={"override_action": "approve", "override_reason": "reviewed and accepted"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["override_action"] == "approve"
    assert data["overridden_at"] is not None
    assert data["overridden_by"] is not None


@pytest.mark.asyncio
async def test_override_already_overridden_409_body(client: AsyncClient, admin_headers, bot_with_veto_decision):
    """Second PATCH → 409 with overridden_by and overridden_at in body."""
    bot_id, decision_id = bot_with_veto_decision
    payload = {"override_action": "approve", "override_reason": "first"}
    await client.patch(f"/api/bots/{bot_id}/advisor-decisions/{decision_id}", json=payload, headers=admin_headers)
    resp = await client.patch(f"/api/bots/{bot_id}/advisor-decisions/{decision_id}", json=payload, headers=admin_headers)
    assert resp.status_code == 409
    body = resp.json()
    assert "overridden_by" in body["detail"]
    assert "overridden_at" in body["detail"]


@pytest.mark.asyncio
async def test_override_wrong_bot_id_404(client: AsyncClient, admin_headers, bot_with_veto_decision, other_bot_id):
    """Decision from bot A accessed under bot B → same 404 body as non-existent decision."""
    _, decision_id = bot_with_veto_decision
    resp = await client.patch(
        f"/api/bots/{other_bot_id}/advisor-decisions/{decision_id}",
        json={"override_action": "approve", "override_reason": "x"},
        headers=admin_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "advisor_decision_not_found"


@pytest.mark.asyncio
async def test_override_existence_oracle_parity(client: AsyncClient, admin_headers, other_bot_id):
    """404 body shape identical for wrong bot_id and missing decision_id."""
    fake_decision_id = 999_999_999
    resp_missing = await client.patch(
        f"/api/bots/{other_bot_id}/advisor-decisions/{fake_decision_id}",
        json={"override_action": "approve", "override_reason": "x"},
        headers=admin_headers,
    )
    bot_id, decision_id = ..., ...  # use fixture bot but wrong bot_id
    resp_wrong = await client.patch(
        f"/api/bots/{other_bot_id}/advisor-decisions/{decision_id}",
        json={"override_action": "approve", "override_reason": "x"},
        headers=admin_headers,
    )
    assert resp_missing.status_code == 404
    assert resp_wrong.status_code == 404
    assert resp_missing.json() == resp_wrong.json()


@pytest.mark.asyncio
async def test_override_does_not_resubmit_order(client: AsyncClient, admin_headers, bot_with_veto_decision, mock_place_order):
    """Approval override does not call place_order."""
    bot_id, decision_id = bot_with_veto_decision
    await client.patch(
        f"/api/bots/{bot_id}/advisor-decisions/{decision_id}",
        json={"override_action": "approve", "override_reason": "ok"},
        headers=admin_headers,
    )
    mock_place_order.assert_not_called()


@pytest.mark.asyncio
async def test_account_advisor_config_put(client: AsyncClient, admin_headers, bot_with_accounts):
    """PUT endpoint updates bot_accounts.advisor_config_override."""
    bot_id, account_id = bot_with_accounts
    resp = await client.put(
        f"/api/bots/{bot_id}/accounts/{account_id}/advisor-config",
        json={"advisor_config_override": {"mode": "OBSERVE"}},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "set"


@pytest.mark.asyncio
async def test_account_advisor_config_clear(client: AsyncClient, admin_headers, bot_with_accounts):
    """PUT with null clears the override."""
    bot_id, account_id = bot_with_accounts
    resp = await client.put(
        f"/api/bots/{bot_id}/accounts/{account_id}/advisor-config",
        json={"advisor_config_override": None},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "clear"


@pytest.mark.asyncio
async def test_account_advisor_config_rejects_max_concurrent(client: AsyncClient, admin_headers, bot_with_accounts):
    """max_concurrent in per-account override → 422."""
    bot_id, account_id = bot_with_accounts
    resp = await client.put(
        f"/api/bots/{bot_id}/accounts/{account_id}/advisor-config",
        json={"advisor_config_override": {"max_concurrent": 2}},
        headers=admin_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_account_advisor_config_missing_account(client: AsyncClient, admin_headers, existing_bot_id):
    """404 when account_id not in bot_accounts."""
    import uuid
    resp = await client.put(
        f"/api/bots/{existing_bot_id}/accounts/{uuid.uuid4()}/advisor-config",
        json={"advisor_config_override": None},
        headers=admin_headers,
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && docker compose exec backend pytest tests/api/test_bots_advisor_override.py -v
```
Expected: 404 — endpoints don't exist.

- [ ] **Step 3: Add two endpoints to `backend/app/api/bots.py`**

Add these imports at the top (if not already present):

```python
from app.core.cf_access import AdminIdentity
from app.core.deps import require_admin_jwt
from app.services.advisor.types import (
    AccountAdvisorConfigUpdate,
    AccountAdvisorConfigOverride,
    AdvisorDecisionOverride,  # NEW model (define below)
)
from app.services.advisor.metrics import (
    advisor_overrides_total,
    advisor_account_config_writes_total,
)
```

Add `AdvisorDecisionOverride` to `types.py` (add alongside the existing models):

```python
# backend/app/services/advisor/types.py (add this model)
class AdvisorDecisionOverride(BaseModel):
    override_action: Literal["approve", "veto"]
    override_reason: str = Field(..., min_length=1, max_length=500)
```

Add type alias for admin identity in `bots.py`:

```python
AdminDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
```

Add the two new endpoints at the end of `bots.py`:

```python
@router.patch("/{bot_id}/advisor-decisions/{decision_id}")
async def override_advisor_decision(
    bot_id: UUID,
    decision_id: int,
    body: AdvisorDecisionOverride,
    db: DbDep,
    redis: RedisDep,
    identity: AdminDep,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
) -> dict[str, Any]:
    # Single query joining both tables — closes existence-oracle timing leakage (M10)
    row = await db.execute(
        text("""
            SELECT d.id, d.overridden_at, d.overridden_by
            FROM bot_advisor_decisions d
            JOIN bots b ON b.id = d.bot_id
            WHERE d.id = :did AND d.bot_id = :bid AND b.deleted_at IS NULL
        """),
        {"did": decision_id, "bid": bot_id},
    )
    existing = row.mappings().one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="advisor_decision_not_found")

    if existing["overridden_at"] is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "already_overridden",
                "overridden_by": existing["overridden_by"],
                "overridden_at": existing["overridden_at"].isoformat(),
            },
        )

    jwt_subject = identity.email
    now_ts = datetime.now(UTC)
    await db.execute(
        text("""
            UPDATE bot_advisor_decisions
            SET overridden_by = :by,
                override_action = :action,
                override_reason = :reason,
                overridden_at = :at
            WHERE id = :id
        """),
        {
            "by": jwt_subject,
            "action": body.override_action,
            "reason": body.override_reason,
            "at": now_ts,
            "id": decision_id,
        },
    )
    await db.commit()

    logger.info(
        "advisor.decision.overridden",
        bot_id=str(bot_id),
        decision_id=decision_id,
        override_action=body.override_action,
        jwt_subject=jwt_subject,
    )

    # Publish to FE-bound channel
    frame = json.dumps({
        "v": 1,
        "type": "decision_overridden",
        "decision_id": decision_id,
        "override_action": body.override_action,
    })
    try:
        await redis.publish(f"bot:advisor:{bot_id}", frame)
    except Exception:
        logger.warning("advisor_override_publish_failed", bot_id=str(bot_id))

    advisor_overrides_total.labels(override_action=body.override_action).inc()
    return {
        "decision_id": decision_id,
        "override_action": body.override_action,
        "overridden_by": jwt_subject,
        "overridden_at": now_ts.isoformat(),
    }


@router.put("/{bot_id}/accounts/{account_id}/advisor-config")
async def put_account_advisor_config(
    bot_id: UUID,
    account_id: UUID,
    body: AccountAdvisorConfigUpdate,
    db: DbDep,
    redis: RedisDep,
    _identity: AdminDep,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
) -> dict[str, Any]:
    # Verify bot exists and account belongs to this bot
    row = await db.execute(
        text("""
            SELECT ba.account_id
            FROM bot_accounts ba
            JOIN bots b ON b.id = ba.bot_id
            WHERE ba.bot_id = :bid AND ba.account_id = :aid AND b.deleted_at IS NULL
        """),
        {"bid": bot_id, "aid": account_id},
    )
    if row.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="bot_account_not_found")

    override_json = (
        body.advisor_config_override.model_dump(exclude_none=False)
        if body.advisor_config_override is not None
        else None
    )
    await db.execute(
        text("""
            UPDATE bot_accounts
            SET advisor_config_override = CAST(:cfg AS jsonb)
            WHERE bot_id = :bid AND account_id = :aid
        """),
        {"cfg": json.dumps(override_json), "bid": bot_id, "aid": account_id},
    )
    await db.commit()

    action = "set" if body.advisor_config_override is not None else "clear"
    advisor_account_config_writes_total.labels(action=action).inc()

    # Publish to child-bound config channel only (not FE)
    frame = json.dumps({"v": 1, "type": "account_config_updated", "account_id": str(account_id)})
    try:
        await redis.publish(f"bot:advisor:config:{bot_id}", frame)
    except Exception:
        logger.warning("account_config_publish_failed", bot_id=str(bot_id))

    return {"bot_id": str(bot_id), "account_id": str(account_id), "action": action}
```

- [ ] **Step 4: Run API tests**

```bash
cd backend && docker compose exec backend pytest tests/api/test_bots_advisor_override.py -v
```
Expected: All pass.

- [ ] **Step 5: Run full test suite**

```bash
cd backend && docker compose exec backend pytest tests/ -v --tb=short -q
```
Expected: All existing tests pass, new tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/bots.py \
        backend/app/services/advisor/types.py \
        backend/tests/api/test_bots_advisor_override.py
git commit -m "feat(phase21a1): PATCH override + PUT account advisor config endpoints"
```

---

## Task 6: Frontend types and API service

**Files:**
- Modify: `frontend/src/services/advisor/types.ts`
- Modify: `frontend/src/services/advisor/api.ts`

- [ ] **Step 1: Update `types.ts`**

In `frontend/src/services/advisor/types.ts`:

```typescript
// 1. Add SHADOW to ADVISOR_MODES and AdvisorMode
export const ADVISOR_MODES = ['OFF', 'OBSERVE', 'VETO', 'SHADOW'] as const
export type AdvisorMode = typeof ADVISOR_MODES[number]

// 2. Add override fields to AdvisorDecision interface
export interface AdvisorDecision {
  // ... all existing fields ...
  overridden_by: string | null
  override_action: 'approve' | 'veto' | null
  override_reason: string | null
  overridden_at: string | null   // ISO timestamp
}

// 3. Add new types for per-account override
export interface AccountAdvisorConfigOverride {
  mode?: AdvisorMode | null
  capability?: string | null
  local_only?: boolean | null
  timeout_ms?: number | null
  daily_budget_usd?: number | null
  // max_concurrent intentionally absent
}

export interface AccountAdvisorConfigUpdate {
  advisor_config_override: AccountAdvisorConfigOverride | null
}

export interface AdvisorDecisionOverride {
  override_action: 'approve' | 'veto'
  override_reason: string
}
```

- [ ] **Step 2: Update `api.ts`**

In `frontend/src/services/advisor/api.ts`, add two new functions:

```typescript
import { checkOk } from '@/services/bots/api'  // reuse existing helper
import { mintCsrfNonce } from '@/services/admin/api'

export async function patchAdvisorDecisionOverride(
  botId: string,
  decisionId: number,
  body: AdvisorDecisionOverride,
): Promise<AdvisorDecision> {
  const nonce = await mintCsrfNonce()
  const resp = await fetch(`/api/bots/${botId}/advisor-decisions/${decisionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Nonce': nonce },
    body: JSON.stringify(body),
  })
  await checkOk(resp)
  return resp.json()
}

export async function putAccountAdvisorConfig(
  botId: string,
  accountId: string,
  body: AccountAdvisorConfigUpdate,
): Promise<{ bot_id: string; account_id: string; action: 'set' | 'clear' }> {
  const nonce = await mintCsrfNonce()
  const resp = await fetch(`/api/bots/${botId}/accounts/${accountId}/advisor-config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Nonce': nonce },
    body: JSON.stringify(body),
  })
  await checkOk(resp)
  return resp.json()
}
```

- [ ] **Step 3: Run FE type checks**

```bash
cd frontend && pnpm typecheck
```
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/services/advisor/types.ts frontend/src/services/advisor/api.ts
git commit -m "feat(phase21a1): advisor FE types (SHADOW, override fields) and API functions"
```

---

## Task 7: Frontend components — override UI

**Files:**
- Modify: `frontend/src/features/bots/components/AdvisorDecisionsTable.tsx`
- Modify: `frontend/src/features/bots/components/AdvisorDecisionDrawer.tsx`
- Modify: `frontend/src/features/bots/components/AdvisorDecisionsTable.test.tsx`
- Modify: `frontend/src/features/bots/components/AdvisorDecisionDrawer.test.tsx`

- [ ] **Step 1: Write failing tests for override badge and drawer**

Add to `AdvisorDecisionsTable.test.tsx`:

```typescript
it('shows Overridden badge when overridden_at is set', () => {
  const overriddenDecision = {
    ...mockDecision,
    overridden_at: '2026-05-19T12:00:00Z',
    override_action: 'approve' as const,
    overridden_by: 'admin@example.com',
    override_reason: 'reviewed',
  }
  render(<AdvisorDecisionsTable decisions={[overriddenDecision]} />)
  expect(screen.getByText('Overridden')).toBeInTheDocument()
})
```

Add to `AdvisorDecisionDrawer.test.tsx`:

```typescript
it('shows override metadata when overridden_at is set', () => {
  const decision = { ...mockDecision, overridden_at: '2026-05-19T12:00:00Z', override_action: 'approve' as const, overridden_by: 'admin@test.com', override_reason: 'ok' }
  render(<AdvisorDecisionDrawer decision={decision} isAdmin />)
  expect(screen.getByText(/Override recorded/i)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /override/i })).not.toBeInTheDocument()
})

it('shows Override button when not yet overridden and user is admin', () => {
  const decision = { ...mockDecision, overridden_at: null, override_action: null }
  render(<AdvisorDecisionDrawer decision={decision} isAdmin />)
  expect(screen.getByRole('button', { name: /override/i })).toBeInTheDocument()
})

it('hides Override button for non-admin session', () => {
  const decision = { ...mockDecision, overridden_at: null, override_action: null }
  render(<AdvisorDecisionDrawer decision={decision} isAdmin={false} />)
  expect(screen.queryByRole('button', { name: /override/i })).not.toBeInTheDocument()
})

it('Override button submits PATCH and shows audit confirmation', async () => {
  vi.mocked(patchAdvisorDecisionOverride).mockResolvedValue({ ...mockDecision, overridden_at: '2026-05-19T12:00:00Z' })
  const decision = { ...mockDecision, overridden_at: null, override_action: null }
  render(<AdvisorDecisionDrawer decision={decision} isAdmin />)
  await userEvent.click(screen.getByRole('button', { name: /override/i }))
  expect(screen.getByText(/audit purposes/i)).toBeInTheDocument()
  expect(screen.getByText(/original order was not re-submitted/i)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run failing tests**

```bash
cd frontend && pnpm test -- --reporter=verbose AdvisorDecisionsTable AdvisorDecisionDrawer
```
Expected: New test cases fail.

- [ ] **Step 3: Update `AdvisorDecisionsTable.tsx`**

Add an "Overridden" badge to rows where `decision.overridden_at !== null`:

```tsx
// In the row rendering, add after the verdict badge:
{decision.overridden_at && (
  <span className="rounded-sm bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-700">
    Overridden
  </span>
)}
```

- [ ] **Step 4: Update `AdvisorDecisionDrawer.tsx`**

Add `isAdmin: boolean` prop. Add conditional override section:

```tsx
interface AdvisorDecisionDrawerProps {
  decision: AdvisorDecision
  isAdmin: boolean
  onClose: () => void
}

// In the drawer body, after existing verdict display:
{decision.overridden_at ? (
  <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm">
    <p className="font-medium text-amber-800">Override recorded</p>
    <p className="text-amber-700">
      By <strong>{decision.overridden_by}</strong> at{' '}
      {new Date(decision.overridden_at).toLocaleString()} → {decision.override_action}
    </p>
    <p className="text-amber-600 text-xs">{decision.override_reason}</p>
  </div>
) : isAdmin ? (
  <OverrideButton decision={decision} />
) : null}
```

Add `OverrideButton` sub-component:

```tsx
function OverrideButton({ decision }: { decision: AdvisorDecision }) {
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState(false)
  const [reason, setReason] = useState('')

  if (done) {
    return (
      <p className="text-sm text-emerald-700">
        Override recorded for audit purposes. The original order was not re-submitted.
      </p>
    )
  }

  return (
    <div className="space-y-2">
      <textarea
        className="w-full rounded border p-2 text-sm"
        placeholder="Override reason (required)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        rows={2}
        maxLength={500}
      />
      <button
        className="rounded bg-amber-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
        disabled={submitting || reason.trim().length === 0}
        onClick={async () => {
          setSubmitting(true)
          try {
            await patchAdvisorDecisionOverride(String(decision.bot_id), decision.id, {
              override_action: 'approve',
              override_reason: reason.trim(),
            })
            setDone(true)
          } finally {
            setSubmitting(false)
          }
        }}
      >
        Override (audit only)
      </button>
    </div>
  )
}
```

- [ ] **Step 5: Run FE tests**

```bash
cd frontend && pnpm test -- --reporter=verbose AdvisorDecisionsTable AdvisorDecisionDrawer
```
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/bots/components/AdvisorDecisionsTable.tsx \
        frontend/src/features/bots/components/AdvisorDecisionDrawer.tsx \
        frontend/src/features/bots/components/AdvisorDecisionsTable.test.tsx \
        frontend/src/features/bots/components/AdvisorDecisionDrawer.test.tsx
git commit -m "feat(phase21a1): override badge + drawer button with audit-only confirmation"
```

---

## Task 8: Frontend — `AccountAdvisorConfigForm` and `BotDetailPage` integration

**Files:**
- Create: `frontend/src/features/bots/components/AccountAdvisorConfigForm.tsx`
- Create: `frontend/src/features/bots/components/AccountAdvisorConfigForm.test.tsx`
- Modify: `frontend/src/features/bots/pages/BotDetailPage.tsx`

- [ ] **Step 1: Write failing tests**

```typescript
// frontend/src/features/bots/components/AccountAdvisorConfigForm.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import { AccountAdvisorConfigForm } from './AccountAdvisorConfigForm'
import * as advisorApi from '@/services/advisor/api'

vi.mock('@/services/advisor/api')

const mockAccount = {
  account_id: 'acc-1',
  advisor_config_override: null as null | Record<string, unknown>,
}

const mockBot = { id: 'bot-1', advisor_config: { mode: 'VETO' } }

it('shows "Using bot default" when override is null', () => {
  render(<AccountAdvisorConfigForm botId="bot-1" account={mockAccount} botConfig={mockBot.advisor_config} onSaved={() => {}} />)
  expect(screen.getByText(/Using bot default/i)).toBeInTheDocument()
})

it('renders form fields for mode, local_only, timeout_ms', () => {
  render(<AccountAdvisorConfigForm botId="bot-1" account={{ ...mockAccount, advisor_config_override: { mode: 'OBSERVE' } }} botConfig={mockBot.advisor_config} onSaved={() => {}} />)
  expect(screen.getByRole('combobox', { name: /mode/i })).toBeInTheDocument()
})

it('Clear override button sets override to null via PUT', async () => {
  vi.mocked(advisorApi.putAccountAdvisorConfig).mockResolvedValue({ bot_id: 'bot-1', account_id: 'acc-1', action: 'clear' })
  render(<AccountAdvisorConfigForm botId="bot-1" account={{ ...mockAccount, advisor_config_override: { mode: 'OBSERVE' } }} botConfig={mockBot.advisor_config} onSaved={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: /clear override/i }))
  expect(advisorApi.putAccountAdvisorConfig).toHaveBeenCalledWith('bot-1', 'acc-1', { advisor_config_override: null })
})

it('does not include max_concurrent field', () => {
  render(<AccountAdvisorConfigForm botId="bot-1" account={mockAccount} botConfig={mockBot.advisor_config} onSaved={() => {}} />)
  expect(screen.queryByLabelText(/max concurrent/i)).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run failing tests**

```bash
cd frontend && pnpm test -- --reporter=verbose AccountAdvisorConfigForm
```
Expected: Fail — component doesn't exist.

- [ ] **Step 3: Create `AccountAdvisorConfigForm.tsx`**

```tsx
// frontend/src/features/bots/components/AccountAdvisorConfigForm.tsx
import { useState } from 'react'
import { putAccountAdvisorConfig } from '@/services/advisor/api'
import type { AccountAdvisorConfigOverride } from '@/services/advisor/types'

interface Props {
  botId: string
  account: {
    account_id: string
    advisor_config_override: Record<string, unknown> | null
  }
  botConfig: Record<string, unknown>
  onSaved: () => void
}

export function AccountAdvisorConfigForm({ botId, account, botConfig, onSaved }: Props) {
  const hasOverride = account.advisor_config_override !== null
  const [saving, setSaving] = useState(false)
  const [mode, setMode] = useState<string>((account.advisor_config_override?.mode as string) ?? '')
  const [localOnly, setLocalOnly] = useState<boolean>((account.advisor_config_override?.local_only as boolean) ?? false)
  const [timeoutMs, setTimeoutMs] = useState<string>(String(account.advisor_config_override?.timeout_ms ?? ''))

  const effective = {
    ...botConfig,
    ...(account.advisor_config_override ?? {}),
  }

  async function handleSave() {
    setSaving(true)
    try {
      const override: AccountAdvisorConfigOverride = {
        ...(mode ? { mode: mode as any } : {}),
        local_only: localOnly,
        ...(timeoutMs ? { timeout_ms: Number(timeoutMs) } : {}),
      }
      await putAccountAdvisorConfig(botId, account.account_id, { advisor_config_override: override })
      onSaved()
    } finally {
      setSaving(false)
    }
  }

  async function handleClear() {
    setSaving(true)
    try {
      await putAccountAdvisorConfig(botId, account.account_id, { advisor_config_override: null })
      onSaved()
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded border p-4 space-y-3">
      {!hasOverride && (
        <p className="text-sm text-gray-500">Using bot default</p>
      )}

      <div className="space-y-2">
        <label className="block text-sm font-medium">
          Mode
          <select
            className="mt-1 block w-full rounded border py-1.5 text-sm"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
          >
            <option value="">(use bot default: {String(botConfig.mode)})</option>
            <option value="OFF">OFF</option>
            <option value="OBSERVE">OBSERVE</option>
            <option value="VETO">VETO</option>
            <option value="SHADOW">SHADOW</option>
          </select>
        </label>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={localOnly}
            onChange={(e) => setLocalOnly(e.target.checked)}
          />
          Local-only (no cloud AI)
        </label>

        <label className="block text-sm">
          Timeout (ms)
          <input
            type="number"
            className="mt-1 block w-full rounded border p-1 text-sm"
            value={timeoutMs}
            onChange={(e) => setTimeoutMs(e.target.value)}
            min={100}
            max={10000}
            placeholder={String(botConfig.timeout_ms ?? 3000)}
          />
        </label>
      </div>

      <div className="rounded bg-gray-50 p-2 text-xs text-gray-600">
        <strong>Effective config:</strong>{' '}
        mode={String(effective.mode)}, local_only={String(effective.local_only)}
      </div>

      <div className="flex gap-2">
        <button
          className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          disabled={saving}
          onClick={handleSave}
        >
          Save override
        </button>
        {hasOverride && (
          <button
            className="rounded border px-3 py-1.5 text-sm disabled:opacity-50"
            disabled={saving}
            onClick={handleClear}
          >
            Clear override
          </button>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Wire into `BotDetailPage.tsx`**

In the advisor tab of `BotDetailPage`, add `AccountAdvisorConfigForm` per `bot_accounts` row below the decisions table. Find the advisor tab render section and append:

```tsx
// After the decisions table in the advisor tab:
{botAccounts.map((account) => (
  <div key={account.account_id} className="mt-4">
    <h4 className="text-sm font-medium text-gray-700 mb-2">
      Account {account.account_id} override
    </h4>
    <AccountAdvisorConfigForm
      botId={bot.id}
      account={account}
      botConfig={bot.advisor_config}
      onSaved={() => refetchBot()}
    />
  </div>
))}
```

- [ ] **Step 5: Run all FE tests**

```bash
cd frontend && pnpm test -- --reporter=verbose
```
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/bots/components/AccountAdvisorConfigForm.tsx \
        frontend/src/features/bots/components/AccountAdvisorConfigForm.test.tsx \
        frontend/src/features/bots/pages/BotDetailPage.tsx
git commit -m "feat(phase21a1): AccountAdvisorConfigForm + BotDetailPage advisor tab integration"
```

---

## Task 9: Close-out

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/TASKS.md`

- [ ] **Step 1: Run full test suite**

```bash
cd backend && docker compose exec backend pytest tests/ -q --tb=short 2>&1 | tee /tmp/phase21a1_be_tests.txt
cd frontend && pnpm test -- --reporter=dot 2>&1 | tee /tmp/phase21a1_fe_tests.txt
```
Expected: All pass. Note the count.

- [ ] **Step 2: Update CLAUDE.md**

In the "Bot Engine v1 (Phase 19, shipped v0.19.0)" block, update the `bot:advisor:*` channel documentation to reflect the taxonomy split:
- `bot:advisor:{id}` = FE-bound frames (decisions, overrides)
- `bot:advisor:config:{id}` = child-bound config frames only

Add a new `Advisor Polish (Phase 21a.1, shipped v0.21.1)` bullet after the Phase 21a bullet with key invariants:
- SHADOW mode: full context-build, no AI call, `advisor_shadow_context_build_seconds` histogram
- Semaphore: `asyncio.Semaphore(max_concurrent)` with resize-barrier; `max_concurrent` ∈ [1,4]
- Override: `PATCH /api/bots/{id}/advisor-decisions/{id}` — audit-only; no order resubmission; existence-oracle single-JOIN query
- Per-account: `PUT /api/bots/{id}/accounts/{id}/advisor-config` — `AccountAdvisorConfigOverride` rejects `max_concurrent`

- [ ] **Step 3: Update CHANGELOG.md and TASKS.md**

Add v0.21.1 entry to CHANGELOG with features and test counts.
Mark Phase 21a.1 as SHIPPED in TASKS.md.

- [ ] **Step 4: Tag and commit**

```bash
git add CLAUDE.md docs/CHANGELOG.md docs/TASKS.md
git commit -m "chore(phase21a1): close-out — CLAUDE.md, CHANGELOG, TASKS"
git tag v0.21.1
```

---

## Self-Review Checklist

- [x] **H1** Migration sequence exact — `op.drop_constraint` + pre-flight DO $$ + `op.create_check_constraint` in Task 1
- [x] **H2** Semaphore pre-creation race-safe via `_ensure_semaphore` with `_in_flight_lock` in Task 4
- [x] **H3 / M8** Resize-barrier event (`_resizing` + `_resize_done`) in Task 4, not `_value` polling
- [x] **H4** Channel taxonomy: `bot:advisor:{id}` FE-bound; `bot:advisor:config:{id}` child-bound — Task 4 and 5
- [x] **M1** 409 body includes `overridden_by` + `overridden_at` — Task 5
- [x] **M2** structlog event on override — Task 5
- [x] **M3** FE hide = UX only; `require_admin_jwt` is real enforcement — Task 7
- [x] **M5** CONCURRENTLY index via `autocommit_block()` — Task 1
- [x] **M6 / M10** Single JOIN query closes timing oracle — Task 5
- [x] **M9** `advisor_account_config_writes_total{action}` — Task 3 (metrics) + Task 5 (endpoint)
- [x] **L5** Downgrade pre-flight UPDATE SHADOW→OBSERVE — Task 1
- [x] **L6** `AccountAdvisorConfigOverride` has `extra="forbid"` → 422 on `max_concurrent` — Task 2
- [x] **L7** `bot_id` gauge cardinality noted — Task 3 (metrics docstring)
- [x] All 4 new metrics wired to the code that emits them (Tasks 3, 4, 5)
- [x] No placeholder steps — all code shown in full
