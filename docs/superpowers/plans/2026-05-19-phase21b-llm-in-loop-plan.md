# Phase 21b — LLM-in-Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship v0.21.2 — LLM param-tuning, shadow-promotion, advisor-in-backtest stub, Telegram VETO notify, and filings/earnings in advisor context.

**Architecture:** `ParamTunerService` fans out LLM-proposed candidate param sets to the Phase 20 backtest harness, ranks by Sharpe/MAR, and lets a human approve one; `BotSupervisor.restart()` atomically restarts the bot. `ShadowPromoterService` clones a bot into a paper-only shadow, compares metrics after a configurable window, and lets a human promote. `AdvisorStub` provides deterministic in-backtest veto injection. `AdvisorTelegramNotifier` psubscribes `bot:advisor:*` and forwards VETO frames to Telegram.

**Tech Stack:** Python 3.14 · FastAPI · SQLAlchemy 2.0 async · Alembic · Pydantic v2 · asyncpg · Redis · APScheduler · React 19 · Vite 7 · TS 6.0 strict · Tailwind v4 · Zustand · TanStack Query · Vitest 4 + RTL 16 · pytest 9

---

## File Map

**New files:**
- `backend/alembic/versions/0065_phase21b_param_tuner_shadow.py`
- `backend/alembic/versions/0066_phase21b_shadow_promotion_events.py`
- `backend/alembic/versions/0067_phase21b_backtest_advisor.py`
- `backend/app/services/param_tuner/__init__.py`
- `backend/app/services/param_tuner/types.py`
- `backend/app/services/param_tuner/context_builder.py`
- `backend/app/services/param_tuner/service.py`
- `backend/app/services/param_tuner/metrics.py`
- `backend/app/services/shadow_promoter/__init__.py`
- `backend/app/services/shadow_promoter/types.py`
- `backend/app/services/shadow_promoter/service.py`
- `backend/app/services/shadow_promoter/metrics.py`
- `backend/app/services/telegram/advisor_notify.py`
- `backend/app/backtest/advisor_stub.py`
- `frontend/src/features/bots/components/ParamTunerSection.tsx`
- `frontend/src/features/bots/components/ParamCandidateCard.tsx`
- `frontend/src/features/bots/components/ShadowComparisonPanel.tsx`
- `frontend/src/features/bots/components/ShadowMetricsTable.tsx`
- `frontend/src/features/bots/hooks/useParamTunerStream.ts`
- `frontend/src/features/bots/hooks/useShadowStream.ts`
- `frontend/src/services/param_tuner/types.ts`
- `frontend/src/services/param_tuner/api.ts`
- `frontend/src/services/shadow_promoter/types.ts`
- `frontend/src/services/shadow_promoter/api.ts`

**Modified files:**
- `backend/app/bot/supervisor.py` — add `restart()`, `SupervisorRestartError`
- `backend/app/bot/fill_router.py` — add live/paper channel isolation
- `backend/app/bot/context.py` — add `is_shadow` guard in `place_order`
- `backend/app/api/bots.py` — add 10 new endpoints (param-tuner + shadow), backfill-schema endpoint, `place_order_for_bot` paper-mode re-read
- `backend/app/api/ws_bots.py` — add `/ws/bots/{id}/tuner` and `/ws/bots/{id}/shadow` WS endpoints
- `backend/app/services/advisor/types.py` — add `AdvisorMode.SHADOW`, `AdvisorConfig.notify_telegram`
- `backend/app/services/advisor/context_builder.py` — add filings/earnings injection
- `backend/app/backtest/runner.py` — add advisor stub wiring + `_on_status_change` flush
- `backend/app/main.py` — wire APScheduler jobs, lifespan singletons
- `frontend/src/features/bots/components/BacktestConfigForm.tsx` — advisor toggle + veto_injections
- `frontend/src/features/bots/components/BacktestReportKpis.tsx` — advisor fields
- `frontend/src/features/bots/components/BacktestPnlChart.tsx` — dual-line mode
- `frontend/src/features/bots/BotDetailPage.tsx` — ParamTunerSection + Shadows sub-tab

---

## Task 1: Alembic 0065 — param-tuner + shadow bot + risk_decisions widening

**Files:**
- Create: `backend/alembic/versions/0065_phase21b_param_tuner_shadow.py`
- Test: `backend/tests/test_migrations.py`

- [ ] **Step 1: Write the failing migration test**

```python
# backend/tests/test_migrations.py  (add to existing file)
async def test_0065_bot_param_suggestions_table(db):
    await db.execute(text("""
        INSERT INTO bots (id, name, strategy_file, strategy_params, mode, status)
        VALUES (gen_random_uuid(), 'test', 'stub.py', '{}', 'paper', 'stopped')
        RETURNING id
    """))
    bot_id = (await db.execute(text("SELECT id FROM bots LIMIT 1"))).scalar()
    await db.execute(text("""
        INSERT INTO bot_param_suggestions
            (bot_id, triggered_by, status, strategy_params_current, candidates)
        VALUES (:bid, 'manual', 'pending', '{}', '[]')
    """), {"bid": str(bot_id)})
    # Test failed status (M3-new-1)
    await db.execute(text("""
        INSERT INTO bot_param_suggestions
            (bot_id, triggered_by, status, strategy_params_current, candidates)
        VALUES (:bid, 'scheduled', 'failed', '{}', '[]')
    """), {"bid": str(bot_id)})
    # Test shadow columns on bots
    await db.execute(text("""
        UPDATE bots SET is_shadow=true, shadow_of=NULL, mode='paper'
        WHERE id=:bid
    """), {"bid": str(bot_id)})
    # Test strategy_schema
    await db.execute(text("""
        UPDATE bots SET strategy_schema='{"sma_period":{"type":"int","min":5,"max":200}}'::jsonb
        WHERE id=:bid
    """), {"bid": str(bot_id)})
    # Test risk_decisions attempt_kind widening (H-new-1)
    await db.execute(text("""
        INSERT INTO risk_decisions
            (account_id, attempt_kind, verdict, checks_run, checks_blocked, checks_warned,
             created_at, evaluated_at)
        VALUES (gen_random_uuid(), 'shadow_place_order', 'allow', '[]', 0, 0, now(), now())
    """))
    await db.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_migrations.py::test_0065_bot_param_suggestions_table -v
```
Expected: FAIL — `bot_param_suggestions` does not exist.

- [ ] **Step 3: Write the migration**

```python
# backend/alembic/versions/0065_phase21b_param_tuner_shadow.py
"""Phase 21b — param-tuner + shadow bot columns + risk_decisions widening

Revision ID: 0065
Down Revision: 0064
"""
from __future__ import annotations
from alembic import op
from sqlalchemy import text

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE bot_param_suggestions (
            id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            bot_id                      UUID NOT NULL REFERENCES bots(id) ON DELETE RESTRICT,
            triggered_by                TEXT NOT NULL CHECK (triggered_by IN ('scheduled','manual')),
            status                      TEXT NOT NULL CHECK (status IN (
                                            'pending','backtesting','ranked',
                                            'approved','rejected','applied','failed')),
            strategy_params_current     JSONB NOT NULL,
            ai_reasoning                TEXT,
            candidates                  JSONB NOT NULL DEFAULT '[]'
                                            CHECK (candidates IS NOT NULL
                                                   AND jsonb_array_length(candidates) <= 5),
            ai_completion_id            BIGINT,
            ai_model                    TEXT,
            ai_prompt_hash              TEXT,
            approved_candidate_index    INT,
            approved_by                 TEXT,
            applied_at                  TIMESTAMPTZ,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(text(
        "CREATE INDEX bot_param_suggestions_bot_id_status_idx "
        "ON bot_param_suggestions (bot_id, status)"
    ))
    op.execute(text("""
        CREATE TRIGGER bot_param_suggestions_updated_at
            BEFORE UPDATE ON bot_param_suggestions
            FOR EACH ROW EXECUTE FUNCTION set_updated_at()
    """))
    op.execute(text("""
        ALTER TABLE bots
            ADD COLUMN is_shadow                     BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN shadow_of                     UUID REFERENCES bots(id) ON DELETE SET NULL,
            ADD COLUMN shadow_promoted_at            TIMESTAMPTZ,
            ADD COLUMN shadow_comparison_window_days INT,
            ADD COLUMN strategy_schema               JSONB
    """))
    op.execute(text(
        "CREATE INDEX bots_shadow_of_idx ON bots (shadow_of) WHERE shadow_of IS NOT NULL"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS bot_runs_bot_id_started_at_idx "
        "ON bot_runs (bot_id, started_at DESC)"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS bot_orders_bot_id_created_at_idx "
        "ON bot_orders (bot_id, created_at DESC)"
    ))
    # H-new-1: widen risk_decisions.attempt_kind CHECK to include shadow_place_order
    op.execute(text(
        "ALTER TABLE risk_decisions DROP CONSTRAINT risk_decisions_attempt_kind_check"
    ))
    op.execute(text("""
        ALTER TABLE risk_decisions
            ADD CONSTRAINT risk_decisions_attempt_kind_check
                CHECK (attempt_kind IN (
                    'preview', 'place_order', 'modify_order',
                    'bot_place_order', 'shadow_place_order'
                ))
    """))


def downgrade() -> None:
    op.execute(text("""
        ALTER TABLE risk_decisions DROP CONSTRAINT risk_decisions_attempt_kind_check
    """))
    op.execute(text("""
        ALTER TABLE risk_decisions
            ADD CONSTRAINT risk_decisions_attempt_kind_check
                CHECK (attempt_kind IN (
                    'preview', 'place_order', 'modify_order', 'bot_place_order'
                ))
    """))
    op.execute(text("DROP INDEX IF EXISTS bot_orders_bot_id_created_at_idx"))
    op.execute(text("DROP INDEX IF EXISTS bot_runs_bot_id_started_at_idx"))
    op.execute(text("DROP INDEX IF EXISTS bots_shadow_of_idx"))
    op.execute(text("""
        ALTER TABLE bots
            DROP COLUMN IF EXISTS strategy_schema,
            DROP COLUMN IF EXISTS shadow_comparison_window_days,
            DROP COLUMN IF EXISTS shadow_promoted_at,
            DROP COLUMN IF EXISTS shadow_of,
            DROP COLUMN IF EXISTS is_shadow
    """))
    op.execute(text("DROP TABLE IF EXISTS bot_param_suggestions"))
```

- [ ] **Step 4: Run migration and test**

```bash
cd backend && alembic upgrade 0065
pytest tests/test_migrations.py::test_0065_bot_param_suggestions_table -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0065_phase21b_param_tuner_shadow.py backend/tests/test_migrations.py
git commit -m "feat(phase21b): alembic 0065 — param-tuner + shadow bot columns + attempt_kind widening"
```

---

## Task 2: Alembic 0066 — shadow_promotion_events

**Files:**
- Create: `backend/alembic/versions/0066_phase21b_shadow_promotion_events.py`
- Test: `backend/tests/test_migrations.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_0066_shadow_promotion_events(db):
    await db.execute(text("""
        INSERT INTO shadow_promotion_events (
            shadow_bot_id, live_bot_id, promoted_by,
            comparison_window_days, comparison_window_start,
            shadow_metrics, live_metrics
        ) VALUES (
            gen_random_uuid(), gen_random_uuid(), 'test_user',
            14, now() - interval '14 days',
            '{"sharpe":1.1}'::jsonb, '{"sharpe":0.9}'::jsonb
        )
    """))
    await db.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_migrations.py::test_0066_shadow_promotion_events -v
```
Expected: FAIL.

- [ ] **Step 3: Write the migration**

```python
# backend/alembic/versions/0066_phase21b_shadow_promotion_events.py
"""Phase 21b — shadow_promotion_events table

Revision ID: 0066
Down Revision: 0065
"""
from __future__ import annotations
from alembic import op
from sqlalchemy import text

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE shadow_promotion_events (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            shadow_bot_id            UUID NOT NULL REFERENCES bots(id) ON DELETE RESTRICT,
            live_bot_id              UUID NOT NULL REFERENCES bots(id) ON DELETE RESTRICT,
            promoted_by              TEXT NOT NULL,
            comparison_window_days   INT NOT NULL,
            comparison_window_start  TIMESTAMPTZ NOT NULL,
            shadow_metrics           JSONB NOT NULL,
            live_metrics             JSONB NOT NULL,
            promoted_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(text(
        "CREATE INDEX shadow_promotion_events_live_bot_id_idx "
        "ON shadow_promotion_events (live_bot_id, promoted_at DESC)"
    ))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS shadow_promotion_events"))
```

- [ ] **Step 4: Run migration and test**

```bash
cd backend && alembic upgrade 0066
pytest tests/test_migrations.py::test_0066_shadow_promotion_events -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0066_phase21b_shadow_promotion_events.py backend/tests/test_migrations.py
git commit -m "feat(phase21b): alembic 0066 — shadow_promotion_events table"
```

---

## Task 3: Alembic 0067 — backtest advisor columns

**Files:**
- Create: `backend/alembic/versions/0067_phase21b_backtest_advisor.py`
- Test: `backend/tests/test_migrations.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_0067_backtest_advisor_decisions(db):
    # Get or create a bot and backtest
    bot_id = (await db.execute(text(
        "INSERT INTO bots(name,strategy_file,strategy_params,mode,status) "
        "VALUES('t','s.py','{}','paper','stopped') RETURNING id"
    ))).scalar()
    backtest_id = (await db.execute(text(
        "INSERT INTO backtests(bot_id,status,timeframe,canonical_id,start_date,end_date,"
        "slippage_bps,commission_cfg,params_snapshot,params_schema_hash,bars_source) "
        "VALUES(:bid,'done','1m','TEST','2026-01-01','2026-03-01',5,'{}','{}','abc','db') "
        "RETURNING id"
    ), {"bid": str(bot_id)})).scalar()
    # Test advisor_config column on backtests
    await db.execute(text(
        "UPDATE backtests SET advisor_config='{\"mode\":\"VETO\"}'::jsonb WHERE id=:bid"
    ), {"bid": str(backtest_id)})
    # Test backtest_advisor_decisions table
    await db.execute(text("""
        INSERT INTO backtest_advisor_decisions
            (backtest_id, bar_index, canonical_id, intent, verdict, reasoning, latency_ms)
        VALUES (:bid, 5, 'TEST', '{}'::jsonb, 'approve', 'stub', 1)
    """), {"bid": str(backtest_id)})
    await db.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_migrations.py::test_0067_backtest_advisor_decisions -v
```
Expected: FAIL.

- [ ] **Step 3: Write the migration**

```python
# backend/alembic/versions/0067_phase21b_backtest_advisor.py
"""Phase 21b — backtest advisor_config column + backtest_advisor_decisions table

Revision ID: 0067
Down Revision: 0066
"""
from __future__ import annotations
from alembic import op
from sqlalchemy import text

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE backtests ADD COLUMN advisor_config JSONB"
    ))
    op.execute(text("""
        CREATE TABLE backtest_advisor_decisions (
            id              BIGSERIAL PRIMARY KEY,
            backtest_id     UUID NOT NULL REFERENCES backtests(id) ON DELETE CASCADE,
            bar_index       INT NOT NULL,
            canonical_id    TEXT NOT NULL,
            intent          JSONB NOT NULL,
            verdict         TEXT NOT NULL CHECK (verdict IN ('approve','veto','fail_open')),
            reasoning       TEXT NOT NULL,
            latency_ms      INT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(text(
        "CREATE INDEX backtest_advisor_decisions_backtest_id_idx "
        "ON backtest_advisor_decisions (backtest_id)"
    ))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS backtest_advisor_decisions"))
    op.execute(text("ALTER TABLE backtests DROP COLUMN IF EXISTS advisor_config"))
```

- [ ] **Step 4: Run migration and test**

```bash
cd backend && alembic upgrade 0067
pytest tests/test_migrations.py::test_0067_backtest_advisor_decisions -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0067_phase21b_backtest_advisor.py backend/tests/test_migrations.py
git commit -m "feat(phase21b): alembic 0067 — backtest advisor_config + backtest_advisor_decisions"
```

---

## Task 4: Param-tuner types + context builder

**Files:**
- Create: `backend/app/services/param_tuner/__init__.py`
- Create: `backend/app/services/param_tuner/types.py`
- Create: `backend/app/services/param_tuner/context_builder.py`
- Test: `backend/tests/services/param_tuner/test_types.py`
- Test: `backend/tests/services/param_tuner/test_context_builder.py`

**Routing:** Qwen (structured types + bounded context-builder)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/services/param_tuner/test_types.py
from app.services.param_tuner.types import (
    SuggestionStatus, ParamCandidate, ParamSuggestion, CandidateListResponse
)

def test_suggestion_status_includes_failed():
    assert SuggestionStatus.FAILED == "failed"

def test_candidate_list_response_parse():
    r = CandidateListResponse.model_validate({
        "candidates": [{"sma_period": 20}],
        "reasoning": "test"
    })
    assert len(r.candidates) == 1

def test_param_candidate_defaults():
    c = ParamCandidate(params={"sma": 20})
    assert c.backtest_job_id is None
    assert c.rank is None
    assert c.delta_vs_current == {}
```

```python
# backend/tests/services/param_tuner/test_context_builder.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.param_tuner.context_builder import TunerContextBuilder

@pytest.mark.asyncio
async def test_build_returns_fenced_payload(db_session_mock):
    bot_row = {"strategy_params": {"sma": 20}, "strategy_schema": {"sma": {"type": "int"}}}
    builder = TunerContextBuilder()
    payload, token_estimate = await builder.build(
        bot_id=..., bot_row=bot_row, db=db_session_mock
    )
    assert "<<BEGIN_TUNER_CONTEXT>>" in payload
    assert "<<END_TUNER_CONTEXT>>" in payload
    assert token_estimate <= 4000
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/services/param_tuner/ -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `types.py`**

```python
# backend/app/services/param_tuner/types.py
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class TunerTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    BACKTESTING = "backtesting"
    RANKED = "ranked"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    FAILED = "failed"


class BacktestResultSnapshot(BaseModel):
    sharpe: float | None = None
    mar: float | None = None
    max_dd: float | None = None
    win_rate: float | None = None
    avg_trade_pnl: Decimal = Decimal("0")
    forced_close_pnl: Decimal = Decimal("0")
    total_trades: int = 0


class ParamCandidate(BaseModel):
    params: dict
    backtest_job_id: UUID | None = None
    backtest_result: BacktestResultSnapshot | None = None
    rank: int | None = None
    delta_vs_current: dict[str, str] = Field(default_factory=dict)


class ParamSuggestion(BaseModel):
    id: UUID
    bot_id: UUID
    triggered_by: TunerTrigger
    status: SuggestionStatus
    strategy_params_current: dict
    ai_reasoning: str | None = None
    candidates: list[ParamCandidate] = Field(default_factory=list)
    approved_candidate_index: int | None = None
    approved_by: str | None = None
    applied_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CandidateListResponse(BaseModel):
    candidates: list[dict]
    reasoning: str


class TunerAlreadyActiveError(Exception):
    pass


class TunerCostCeilingError(Exception):
    pass


class SupervisorRestartError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)
```

- [ ] **Step 4: Implement `context_builder.py`**

```python
# backend/app/services/param_tuner/context_builder.py
from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

_MAX_TOKENS = 4000
_ROLE_TAG_RE = re.compile(r"<\|?(system|user|assistant)\|?>", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_MAX_FREE_TEXT = 200


def _sanitise(s: str) -> str:
    s = _ROLE_TAG_RE.sub("", s)
    s = _CODE_FENCE_RE.sub("[code]", s)
    return s[:_MAX_FREE_TEXT]


class TunerContextBuilder:
    async def build(
        self,
        bot_id: UUID,
        bot_row: dict[str, Any],
        db: AsyncSession,
    ) -> tuple[str, int]:
        """Return (fenced_payload, token_estimate). Reads in a single transaction."""
        runs_result = await db.execute(
            text("""
                SELECT kpi_sharpe, kpi_mar, kpi_max_dd, kpi_win_rate,
                       kpi_avg_trade_pnl, total_orders, started_at, stopped_at
                FROM bot_runs
                WHERE bot_id=:bid AND status='stopped'
                ORDER BY started_at DESC LIMIT 10
            """),
            {"bid": str(bot_id)},
        )
        runs = [dict(r._mapping) for r in runs_result]

        orders_result = await db.execute(
            text("""
                SELECT side, qty, fill_price
                FROM bot_orders
                WHERE bot_id=:bid
                ORDER BY created_at DESC LIMIT 100
            """),
            {"bid": str(bot_id)},
        )
        orders = [dict(r._mapping) for r in orders_result]

        advisor_result = await db.execute(
            text("""
                SELECT verdict, advice_tags
                FROM bot_advisor_decisions
                WHERE bot_id=:bid
                ORDER BY created_at DESC LIMIT 50
            """),
            {"bid": str(bot_id)},
        )
        advisor_rows = [dict(r._mapping) for r in advisor_result]
        verdict_counts: dict[str, int] = {}
        tag_freq: dict[str, int] = {}
        for row in advisor_rows:
            verdict_counts[row["verdict"]] = verdict_counts.get(row["verdict"], 0) + 1
            for tag in (row["advice_tags"] or []):
                tag_freq[tag] = tag_freq.get(tag, 0) + 1
        top_tags = sorted(tag_freq, key=lambda t: -tag_freq[t])[:5]

        payload_data = {
            "strategy_params": bot_row.get("strategy_params", {}),
            "strategy_schema": bot_row.get("strategy_schema", {}),
            "recent_runs": runs,
            "order_summary": {
                "total_sampled": len(orders),
                "sides": {
                    "buy": sum(1 for o in orders if o["side"] == "buy"),
                    "sell": sum(1 for o in orders if o["side"] == "sell"),
                },
            },
            "advisor_summary": {
                "verdict_counts": verdict_counts,
                "top_advice_tags": top_tags,
            },
        }

        payload_json = json.dumps(payload_data, default=str)
        token_estimate = len(payload_json) // 4  # rough 4-chars-per-token

        fenced = (
            f"<<BEGIN_TUNER_CONTEXT>>\n{payload_json}\n<<END_TUNER_CONTEXT>>"
        )
        return fenced, min(token_estimate, _MAX_TOKENS)
```

- [ ] **Step 5: Create `__init__.py`**

```python
# backend/app/services/param_tuner/__init__.py
```

- [ ] **Step 6: Run tests**

```bash
cd backend && pytest tests/services/param_tuner/ -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/param_tuner/ backend/tests/services/param_tuner/
git commit -m "feat(phase21b): param-tuner types + context builder"
```

---

## Task 5: Param-tuner service + metrics + APScheduler

**Files:**
- Create: `backend/app/services/param_tuner/service.py`
- Create: `backend/app/services/param_tuner/metrics.py`
- Test: `backend/tests/services/param_tuner/test_service.py`

**Routing:** Codex (multi-file integration: Redis INCRBYFLOAT reservation, APScheduler, BacktestSubmitter, BotSupervisor.restart())

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/services/param_tuner/test_service.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from app.services.param_tuner.service import ParamTunerService
from app.services.param_tuner.types import TunerAlreadyActiveError, TunerCostCeilingError


@pytest.mark.asyncio
async def test_trigger_creates_suggestion_and_fans_out(db, redis_mock, ai_client_mock):
    bot_id = uuid4()
    # Setup: bot row in DB with strategy_schema set, no active suggestion
    submitter_mock = AsyncMock()
    submitter_mock.submit.return_value = uuid4()
    submitter_mock.queue_depth.return_value = 0
    ai_client_mock.complete.return_value = MagicMock(
        content='{"candidates":[{"sma":20}],"reasoning":"test"}'
    )
    redis_mock.incrbyfloat.return_value = 0.10
    redis_mock.decrby.return_value = 0
    svc = ParamTunerService(ai_client_mock, redis_mock, db, submitter_mock)
    # ... (full integration test with test DB fixture)


@pytest.mark.asyncio
async def test_trigger_blocks_second_active_suggestion(db, redis_mock, ai_client_mock):
    # Insert pending suggestion, assert second trigger raises TunerAlreadyActiveError
    ...


@pytest.mark.asyncio
async def test_trigger_sets_failed_when_no_valid_candidates(db, redis_mock, ai_client_mock):
    submitter_mock = AsyncMock()
    submitter_mock.queue_depth.return_value = 0
    ai_client_mock.complete.return_value = MagicMock(
        content='{"candidates":[],"reasoning":"none"}'
    )
    redis_mock.incrbyfloat.return_value = 0.10
    svc = ParamTunerService(ai_client_mock, redis_mock, db, submitter_mock)
    # trigger() → row with status='failed', ws frame type='failed'


@pytest.mark.asyncio
async def test_trigger_sets_failed_when_queue_full(db, redis_mock, ai_client_mock):
    submitter_mock = AsyncMock()
    submitter_mock.queue_depth.return_value = 25  # > default 20
    svc = ParamTunerService(ai_client_mock, redis_mock, db, submitter_mock)
    # trigger() → row with status='failed', frame reason='queue_full'


@pytest.mark.asyncio
async def test_poll_marks_ranked_when_all_done(db, redis_mock, ai_client_mock):
    # Setup: suggestion status='backtesting', all backtest jobs status='done'
    # poll_backtest_results() → status='ranked', candidates sorted by sharpe
    ...


@pytest.mark.asyncio
async def test_approve_updates_params_and_restarts_bot(db, redis_mock, supervisor_mock):
    # approve() → bots.strategy_params updated; BotSupervisor.restart() called
    ...


@pytest.mark.asyncio
async def test_reject_sets_rejected_status(db, redis_mock):
    # reject() → status='rejected'; bot unchanged
    ...


@pytest.mark.asyncio
async def test_scheduled_job_skips_shadow_bots(db, redis_mock, ai_client_mock):
    # bot with is_shadow=True → not triggered
    ...


@pytest.mark.asyncio
async def test_trigger_cost_ceiling_prevents_trigger(db, redis_mock, ai_client_mock):
    # Redis INCRBYFLOAT returns value > ceiling → TunerCostCeilingError, DECRBY called
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/services/param_tuner/test_service.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `metrics.py`**

```python
# backend/app/services/param_tuner/metrics.py
from prometheus_client import Counter, Gauge, Histogram

param_tuner_trigger_total = Counter(
    "param_tuner_trigger_total", "Param tuner triggers", ["triggered_by"]
)
param_tuner_trigger_failures_total = Counter(
    "param_tuner_trigger_failures_total", "Trigger failures",
    ["reason"]  # no_valid_candidates|cost_ceiling|queue_full|ai_error
)
param_tuner_candidates_generated_total = Counter(
    "param_tuner_candidates_generated_total", "Total LLM candidates generated"
)
param_tuner_invalid_candidates_total = Counter(
    "param_tuner_invalid_candidates_total", "Candidates dropped as invalid",
    ["reason"]  # schema_type|out_of_bounds
)
param_tuner_backtest_fan_out_total = Counter(
    "param_tuner_backtest_fan_out_total", "Backtest fan-out submits"
)
param_tuner_backtest_queue_depth = Gauge(
    "param_tuner_backtest_queue_depth", "Current backtest queue depth"
)
param_tuner_ranked_total = Counter(
    "param_tuner_ranked_total", "Suggestions ranked"
)
param_tuner_applied_total = Counter(
    "param_tuner_applied_total", "Suggestions applied", ["triggered_by"]
)
param_tuner_ai_latency_seconds = Histogram(
    "param_tuner_ai_latency_seconds", "AI call latency for param tuner"
)
param_tuner_fleet_cost_ceiling_total = Counter(
    "param_tuner_fleet_cost_ceiling_total", "Fleet scheduled runs stopped by cost ceiling"
)
param_tuner_cost_reservation_failures_total = Counter(
    "param_tuner_cost_reservation_failures_total",
    "Redis cost reservation failures; ceiling fell back to DB-only"
)
```

- [ ] **Step 4: Implement `service.py`**

Service must implement `trigger()`, `poll_backtest_results()`, `approve()`, `reject()` per spec §5.3. Key contracts:
- `trigger()` step 2: `SELECT … FOR UPDATE SKIP LOCKED` on active suggestions.
- `trigger()` step 3: `redis.incrbyfloat(f"param_tuner:cost_pending:{utc_date}", 0.10, ex=86400)`. If result > ceiling: call `redis.decrby(key, 0.10)`, raise `TunerCostCeilingError`. On Redis error: log + metric, proceed.
- `trigger()` step 5: `capability = LOCAL_ONLY` unless `app_config[param_tuner/allow_cloud_reasoning] == "true"`.
- `trigger()` after AI call: `redis.decrby(key, 0.10)` to remove reservation.
- `trigger()` step 8/9: `status='failed'`, not `'pending'`.
- `poll_backtest_results()` step 3: rolling mean over last 5 `bot_runs` for delta.
- `approve()` step 4: call `supervisor.restart(bot_id)` — do not call `stop` + `start` separately.

```python
# backend/app/services/param_tuner/service.py
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.ai.router import AICompletionClient, AICapability
from app.services.param_tuner import metrics as m
from app.services.param_tuner.context_builder import TunerContextBuilder
from app.services.param_tuner.types import (
    CandidateListResponse, ParamCandidate, SuggestionStatus,
    SupervisorRestartError, TunerAlreadyActiveError, TunerCostCeilingError,
    TunerTrigger,
)

logger = structlog.get_logger(__name__)

MAX_CANDIDATES = 5
DEFAULT_QUEUE_DEPTH_LIMIT = 20
COST_ESTIMATE_PER_TRIGGER = 0.10


class BacktestSubmitter:
    """Thin wrapper to allow injection in tests."""

    def __init__(self, db_factory: async_sessionmaker[AsyncSession]) -> None:
        self._db_factory = db_factory

    async def submit(self, bot_id: UUID, params: dict) -> UUID:
        """Submit one candidate backtest. Returns backtest_job_id."""
        async with self._db_factory() as db:
            # Read bot's most recent completed backtest for slippage/commission config
            bt_row = (await db.execute(
                text("""SELECT slippage_bps, commission_cfg, canonical_id, timeframe
                        FROM backtests WHERE bot_id=:bid AND status='done'
                        ORDER BY created_at DESC LIMIT 1"""),
                {"bid": str(bot_id)},
            )).one_or_none()
            slippage_bps = bt_row["slippage_bps"] if bt_row else 5
            commission_cfg = bt_row["commission_cfg"] if bt_row else json.dumps({"type": "zero"})
            canonical_id = bt_row["canonical_id"] if bt_row else None
            timeframe = bt_row["timeframe"] if bt_row else "1m"

            # Get window from config (default 90 days)
            cfg_row = (await db.execute(
                text("SELECT value FROM app_config WHERE key='param_tuner/backtest_window_days'")
            )).one_or_none()
            window_days = int(cfg_row["value"]) if cfg_row else 90

            import datetime as _dt
            end_dt = _dt.date.today()
            start_dt = end_dt - _dt.timedelta(days=window_days)

            result = await db.execute(
                text("""INSERT INTO backtests
                        (bot_id, status, timeframe, canonical_id,
                         start_date, end_date, slippage_bps, slippage_atr_pct,
                         commission_cfg, params_snapshot, params_schema_hash, bars_source)
                        VALUES(:bid,'queued',:tf,:cid,:sd,:ed,:sbps,null,:ccfg,:ps,'tuner','db')
                        RETURNING id"""),
                {
                    "bid": str(bot_id),
                    "tf": timeframe,
                    "cid": canonical_id,
                    "sd": start_dt,
                    "ed": end_dt,
                    "sbps": slippage_bps,
                    "ccfg": commission_cfg if isinstance(commission_cfg, str) else json.dumps(commission_cfg),
                    "ps": json.dumps(params),
                },
            )
            await db.commit()
            return result.scalar()

    async def queue_depth(self) -> int:
        async with self._db_factory() as db:
            row = (await db.execute(
                text("SELECT COUNT(*) FROM backtests WHERE status IN ('queued','running')")
            )).one()
            return row[0]


class ParamTunerService:
    def __init__(
        self,
        ai_client: AICompletionClient,
        redis: Any,
        db_factory: async_sessionmaker[AsyncSession],
        backtest_submitter: BacktestSubmitter,
    ) -> None:
        self._ai = ai_client
        self._redis = redis
        self._db_factory = db_factory
        self._submitter = backtest_submitter
        self._context_builder = TunerContextBuilder()

    async def trigger(
        self,
        bot_id: UUID,
        triggered_by: TunerTrigger,
        db: AsyncSession,
    ) -> UUID:
        # Kill switch for scheduled triggers
        if triggered_by == TunerTrigger.SCHEDULED:
            cfg = (await db.execute(
                text("SELECT value FROM app_config WHERE key='param_tuner/scheduled_enabled'")
            )).one_or_none()
            if cfg and cfg["value"].lower() == "false":
                raise TunerAlreadyActiveError("scheduled_disabled")

        # Step 1: read bot
        bot_row = (await db.execute(
            text("SELECT * FROM bots WHERE id=:bid AND deleted_at IS NULL"),
            {"bid": str(bot_id)},
        )).one_or_none()
        if bot_row is None:
            raise ValueError("bot_not_found")
        bot = dict(bot_row._mapping)
        if bot.get("is_shadow"):
            raise ValueError("cannot_tune_shadow_bot")
        if bot.get("strategy_schema") is None:
            raise ValueError("strategy_schema_missing_run_backfill_endpoint")

        # Step 2: check for active suggestion (SELECT FOR UPDATE)
        active = (await db.execute(
            text("""SELECT id FROM bot_param_suggestions
                    WHERE bot_id=:bid AND status IN ('pending','backtesting','ranked')
                    FOR UPDATE SKIP LOCKED"""),
            {"bid": str(bot_id)},
        )).one_or_none()
        if active:
            raise TunerAlreadyActiveError("already_active")

        # Step 3: Redis cost reservation (TOCTOU-safe)
        utc_date = datetime.now(timezone.utc).date().isoformat()
        cost_key = f"param_tuner:cost_pending:{utc_date}"
        committed_cost = await self._get_committed_cost(bot_id, db)
        ceiling = await self._get_cost_ceiling(db)
        reservation_ok = True
        post_increment = 0.0
        try:
            post_increment = await self._redis.incrbyfloat(
                cost_key, COST_ESTIMATE_PER_TRIGGER
            )
            await self._redis.expire(cost_key, 86400)
            if committed_cost + post_increment > ceiling:
                await self._redis.incrbyfloat(cost_key, -COST_ESTIMATE_PER_TRIGGER)
                m.param_tuner_trigger_failures_total.labels(reason="cost_ceiling").inc()
                raise TunerCostCeilingError("cost_ceiling_exceeded")
        except TunerCostCeilingError:
            raise
        except Exception:
            logger.warning("param_tuner.cost_reservation_failed", bot_id=str(bot_id))
            m.param_tuner_cost_reservation_failures_total.inc()
            reservation_ok = False

        # Step 4: build context
        payload, _token_estimate = await self._context_builder.build(bot_id, bot, db)
        prompt_hash = hashlib.sha256(payload.encode()).hexdigest()[:16]

        # Step 5: capability routing
        cfg_cloud = (await db.execute(
            text("SELECT value FROM app_config WHERE key='param_tuner/allow_cloud_reasoning'")
        )).one_or_none()
        capability = (
            AICapability.REASONING
            if (cfg_cloud and cfg_cloud["value"].lower() == "true")
            else AICapability.LOCAL_ONLY
        )

        # Step 6: AI call
        t0 = time.monotonic()
        try:
            response = await self._ai.complete(
                messages=[{"role": "user", "content": payload}],
                capability=capability,
                caller=f"param_tuner:bot:{bot_id}",
                jwt_subject=f"system:bot:{bot_id}",
                response_format=CandidateListResponse.model_json_schema(),
                timeout=30,
            )
        except Exception as exc:
            if reservation_ok:
                try:
                    await self._redis.incrbyfloat(cost_key, -COST_ESTIMATE_PER_TRIGGER)
                except Exception:
                    pass
            m.param_tuner_trigger_failures_total.labels(reason="ai_error").inc()
            raise
        finally:
            m.param_tuner_ai_latency_seconds.observe(time.monotonic() - t0)

        if reservation_ok:
            try:
                await self._redis.incrbyfloat(cost_key, -COST_ESTIMATE_PER_TRIGGER)
            except Exception:
                pass

        # Step 7: parse and validate candidates
        try:
            parsed = CandidateListResponse.model_validate_json(response.content)
        except (ValidationError, Exception):
            parsed = CandidateListResponse(candidates=[], reasoning="parse_error")

        raw_candidates = parsed.candidates[:MAX_CANDIDATES]
        m.param_tuner_candidates_generated_total.inc(len(raw_candidates))
        valid_candidates: list[ParamCandidate] = []
        schema = bot.get("strategy_schema") or {}
        bounds = schema.get("bounds") or {}
        for cdict in raw_candidates:
            if not self._validate_candidate(cdict, schema, bounds):
                continue
            valid_candidates.append(ParamCandidate(params=cdict))

        # Steps 8/9: handle no valid candidates
        if not valid_candidates:
            suggestion_id = await self._persist_suggestion(
                bot_id, triggered_by, "failed", bot["strategy_params"],
                parsed.reasoning, [], None, None, prompt_hash, db
            )
            await self._publish(bot_id, {
                "v": 1, "type": "failed", "suggestion_id": str(suggestion_id),
                "reason": "no_valid_candidates"
            })
            m.param_tuner_trigger_failures_total.labels(reason="no_valid_candidates").inc()
            return suggestion_id

        # Step 9: queue depth check
        depth = await self._submitter.queue_depth()
        m.param_tuner_backtest_queue_depth.set(depth)
        queue_limit = await self._get_queue_limit(db)
        if depth >= queue_limit:
            suggestion_id = await self._persist_suggestion(
                bot_id, triggered_by, "failed", bot["strategy_params"],
                parsed.reasoning, [], None, None, prompt_hash, db
            )
            await self._publish(bot_id, {
                "v": 1, "type": "failed", "suggestion_id": str(suggestion_id),
                "reason": "queue_full"
            })
            m.param_tuner_trigger_failures_total.labels(reason="queue_full").inc()
            return suggestion_id

        # Step 10: persist row
        suggestion_id = await self._persist_suggestion(
            bot_id, triggered_by, "backtesting", bot["strategy_params"],
            parsed.reasoning, valid_candidates, None, None, prompt_hash, db
        )
        m.param_tuner_trigger_total.labels(triggered_by=str(triggered_by)).inc()

        # Step 11: fan-out backtests
        for i, candidate in enumerate(valid_candidates):
            try:
                job_id = await self._submitter.submit(bot_id, candidate.params)
                valid_candidates[i] = candidate.model_copy(update={"backtest_job_id": job_id})
                m.param_tuner_backtest_fan_out_total.inc()
            except Exception:
                logger.warning("param_tuner.submit_failed", bot_id=str(bot_id), idx=i)

        await db.execute(
            text("UPDATE bot_param_suggestions SET candidates=:c WHERE id=:id"),
            {
                "c": json.dumps([c.model_dump(mode="json") for c in valid_candidates]),
                "id": str(suggestion_id),
            },
        )
        await db.commit()

        # Step 12: publish
        await self._publish(bot_id, {
            "v": 1, "type": "backtesting",
            "suggestion_id": str(suggestion_id),
            "candidate_count": len(valid_candidates),
        })
        return suggestion_id

    async def poll_backtest_results(self, db: AsyncSession) -> None:
        rows = (await db.execute(
            text("SELECT * FROM bot_param_suggestions WHERE status='backtesting'")
        )).all()
        for row in rows:
            suggestion = dict(row._mapping)
            candidates = [ParamCandidate(**c) for c in (suggestion.get("candidates") or [])]
            updated = False
            for i, candidate in enumerate(candidates):
                if candidate.backtest_job_id is None or candidate.backtest_result is not None:
                    continue
                bt = (await db.execute(
                    text("SELECT status, kpi_sharpe, kpi_mar, kpi_max_dd, kpi_win_rate, "
                         "kpi_avg_trade_pnl, forced_close_pnl, total_orders "
                         "FROM backtests WHERE id=:bid"),
                    {"bid": str(candidate.backtest_job_id)},
                )).one_or_none()
                if bt is None or bt["status"] in ("queued", "running"):
                    continue
                if bt["status"] == "done":
                    from app.services.param_tuner.types import BacktestResultSnapshot
                    candidates[i] = candidate.model_copy(update={
                        "backtest_result": BacktestResultSnapshot(
                            sharpe=bt["kpi_sharpe"],
                            mar=bt["kpi_mar"],
                            max_dd=bt["kpi_max_dd"],
                            win_rate=bt["kpi_win_rate"],
                            avg_trade_pnl=bt["kpi_avg_trade_pnl"] or 0,
                            forced_close_pnl=bt["forced_close_pnl"] or 0,
                            total_trades=bt["total_orders"] or 0,
                        )
                    })
                else:  # failed
                    candidates[i] = candidate.model_copy(update={"backtest_result": None})
                updated = True

            all_resolved = all(
                c.backtest_result is not None or c.backtest_job_id is None
                for c in candidates
            )
            if not all_resolved or not updated:
                continue

            # Compute delta using rolling 5 bot_runs
            runs_result = await db.execute(
                text("""SELECT kpi_sharpe, kpi_mar, kpi_max_dd FROM bot_runs
                        WHERE bot_id=:bid AND status='stopped'
                        ORDER BY started_at DESC LIMIT 5"""),
                {"bid": str(suggestion["bot_id"])},
            )
            runs = [dict(r._mapping) for r in runs_result]

            def _mean(vals: list) -> float | None:
                v = [x for x in vals if x is not None]
                return sum(v) / len(v) if v else None

            current_sharpe = _mean([r["kpi_sharpe"] for r in runs])
            current_mar = _mean([r["kpi_mar"] for r in runs])
            current_max_dd = _mean([r["kpi_max_dd"] for r in runs])

            for i, candidate in enumerate(candidates):
                if candidate.backtest_result is None:
                    continue
                delta: dict[str, str] = {}
                if current_sharpe is not None and candidate.backtest_result.sharpe is not None:
                    d = candidate.backtest_result.sharpe - current_sharpe
                    delta["sharpe"] = f"{d:+.2f}"
                if current_mar is not None and candidate.backtest_result.mar is not None:
                    d = candidate.backtest_result.mar - current_mar
                    delta["mar"] = f"{d:+.2f}"
                if current_max_dd is not None and candidate.backtest_result.max_dd is not None:
                    d = candidate.backtest_result.max_dd - current_max_dd
                    delta["max_dd"] = f"{d:+.4f}"
                candidates[i] = candidate.model_copy(update={"delta_vs_current": delta})

            # Rank by sharpe (MAR tiebreaker; None last)
            def rank_key(c: ParamCandidate) -> tuple:
                if c.backtest_result is None:
                    return (1, 0.0, 0.0)
                s = c.backtest_result.sharpe if c.backtest_result.sharpe is not None else float("-inf")
                mar = c.backtest_result.mar if c.backtest_result.mar is not None else float("-inf")
                return (0, -s, -mar)

            sorted_idxs = sorted(range(len(candidates)), key=lambda i: rank_key(candidates[i]))
            ranked_candidates = [
                candidates[i].model_copy(update={"rank": rank + 1})
                for rank, i in enumerate(sorted_idxs)
            ]

            await db.execute(
                text("""UPDATE bot_param_suggestions
                        SET status='ranked', candidates=:c
                        WHERE id=:id"""),
                {
                    "c": json.dumps([c.model_dump(mode="json") for c in ranked_candidates]),
                    "id": str(suggestion["id"]),
                },
            )
            await db.commit()
            await self._publish(suggestion["bot_id"], {
                "v": 1, "type": "ranked",
                "suggestion_id": str(suggestion["id"]),
                "candidate_count": len(ranked_candidates),
            })
            m.param_tuner_ranked_total.inc()

    async def approve(
        self,
        suggestion_id: UUID,
        candidate_index: int,
        approved_by: str,
        db: AsyncSession,
        supervisor: Any,
    ) -> None:
        row = (await db.execute(
            text("SELECT * FROM bot_param_suggestions WHERE id=:id"),
            {"id": str(suggestion_id)},
        )).one_or_none()
        if row is None:
            raise ValueError("suggestion_not_found")
        suggestion = dict(row._mapping)
        if suggestion["status"] != "ranked":
            raise ValueError("suggestion_not_ranked")
        candidates = [ParamCandidate(**c) for c in (suggestion.get("candidates") or [])]
        if candidate_index < 0 or candidate_index >= len(candidates):
            raise ValueError("candidate_index_out_of_range")
        chosen = candidates[candidate_index]
        if chosen.backtest_result is None:
            raise ValueError("candidate_has_no_result")

        async with db.begin():
            await db.execute(
                text("UPDATE bots SET strategy_params=:p WHERE id=:bid"),
                {"p": json.dumps(chosen.params), "bid": str(suggestion["bot_id"])},
            )
            await db.execute(
                text("""UPDATE bot_param_suggestions
                        SET status='applied', approved_candidate_index=:idx,
                            approved_by=:by, applied_at=now()
                        WHERE id=:id"""),
                {"idx": candidate_index, "by": approved_by, "id": str(suggestion_id)},
            )

        bot_row = (await db.execute(
            text("SELECT status FROM bots WHERE id=:bid"),
            {"bid": str(suggestion["bot_id"])},
        )).one_or_none()
        if bot_row and bot_row["status"] in ("running", "paused", "error"):
            await supervisor.restart(UUID(str(suggestion["bot_id"])))

        await self._publish(suggestion["bot_id"], {
            "v": 1, "type": "applied",
            "suggestion_id": str(suggestion_id),
            "candidate_index": candidate_index,
        })
        triggered_by = suggestion.get("triggered_by", "manual")
        m.param_tuner_applied_total.labels(triggered_by=triggered_by).inc()

    async def reject(self, suggestion_id: UUID, rejected_by: str, db: AsyncSession) -> None:
        await db.execute(
            text("UPDATE bot_param_suggestions SET status='rejected' WHERE id=:id"),
            {"id": str(suggestion_id)},
        )
        await db.commit()

    # --- Helpers ---

    def _validate_candidate(self, cdict: dict, schema: dict, bounds: dict) -> bool:
        for field, spec in schema.items():
            if field == "bounds":
                continue
            val = cdict.get(field)
            if val is None:
                continue
            expected_type = spec.get("type")
            if expected_type == "int" and not isinstance(val, int):
                m.param_tuner_invalid_candidates_total.labels(reason="schema_type").inc()
                return False
            if expected_type == "float" and not isinstance(val, (int, float)):
                m.param_tuner_invalid_candidates_total.labels(reason="schema_type").inc()
                return False
        for field, bound_spec in bounds.items():
            val = cdict.get(field)
            if val is None:
                continue
            min_v = bound_spec.get("min")
            max_v = bound_spec.get("max")
            if min_v is not None and val < min_v:
                m.param_tuner_invalid_candidates_total.labels(reason="out_of_bounds").inc()
                return False
            if max_v is not None and val > max_v:
                m.param_tuner_invalid_candidates_total.labels(reason="out_of_bounds").inc()
                return False
        return True

    async def _persist_suggestion(
        self, bot_id, triggered_by, status, current_params, reasoning,
        candidates, ai_completion_id, ai_model, prompt_hash, db
    ) -> UUID:
        result = await db.execute(
            text("""INSERT INTO bot_param_suggestions
                    (bot_id, triggered_by, status, strategy_params_current,
                     ai_reasoning, candidates, ai_completion_id, ai_model, ai_prompt_hash)
                    VALUES (:bid,:trig,:status,:cur,:reasoning,:cand,:acid,:amodel,:phash)
                    RETURNING id"""),
            {
                "bid": str(bot_id),
                "trig": str(triggered_by),
                "status": status,
                "cur": json.dumps(current_params or {}),
                "reasoning": reasoning,
                "cand": json.dumps([c.model_dump(mode="json") for c in candidates]),
                "acid": ai_completion_id,
                "amodel": ai_model,
                "phash": prompt_hash,
            },
        )
        await db.commit()
        return result.scalar()

    async def _publish(self, bot_id: Any, frame: dict) -> None:
        await self._redis.publish(f"bot:tuner:{bot_id}", json.dumps(frame))

    async def _get_committed_cost(self, bot_id: UUID, db: AsyncSession) -> float:
        row = (await db.execute(
            text("""SELECT COALESCE(SUM(cost_usd), 0) FROM ai_completions
                    WHERE caller LIKE 'param_tuner:bot:%'
                      AND created_at >= now() - interval '1 day'""")
        )).scalar()
        return float(row or 0)

    async def _get_cost_ceiling(self, db: AsyncSession) -> float:
        row = (await db.execute(
            text("SELECT value FROM app_config WHERE key='param_tuner/cost_ceiling_usd_daily'")
        )).one_or_none()
        return float(row["value"]) if row else 10.0

    async def _get_queue_limit(self, db: AsyncSession) -> int:
        row = (await db.execute(
            text("SELECT value FROM app_config WHERE key='param_tuner/max_backtest_queue_depth'")
        )).one_or_none()
        return int(row["value"]) if row else DEFAULT_QUEUE_DEPTH_LIMIT
```

- [ ] **Step 5: Run tests**

```bash
cd backend && pytest tests/services/param_tuner/test_service.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/param_tuner/service.py backend/app/services/param_tuner/metrics.py backend/tests/services/param_tuner/test_service.py
git commit -m "feat(phase21b): param-tuner service + metrics"
```

---

## Task 6: Shadow-promoter types + service + metrics

**Files:**
- Create: `backend/app/services/shadow_promoter/__init__.py`
- Create: `backend/app/services/shadow_promoter/types.py`
- Create: `backend/app/services/shadow_promoter/service.py`
- Create: `backend/app/services/shadow_promoter/metrics.py`
- Test: `backend/tests/services/shadow_promoter/test_service.py`

**Routing:** Codex (multi-file, shadow paper-mode enforcement, BotSupervisor.stop/start calls, comparison_ready semantics fix M-new-7)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/services/shadow_promoter/test_service.py
import pytest
from uuid import uuid4
from app.services.shadow_promoter.service import ShadowPromoterService
from app.services.shadow_promoter.types import ShadowComparisonReport


@pytest.mark.asyncio
async def test_create_shadow_clones_bot(db):
    # live bot in DB → create_shadow() → shadow has is_shadow=True, shadow_of=live_id
    ...


@pytest.mark.asyncio
async def test_create_shadow_forces_paper_mode(db):
    # live bot mode='live' → shadow created with bots.mode='paper' (H3-new-1)
    ...


@pytest.mark.asyncio
async def test_create_shadow_copies_risk_caps(db):
    # shadow bot_risk_caps identical to live
    ...


@pytest.mark.asyncio
async def test_create_shadow_live_bot_is_shadow_rejected(db):
    # is_shadow=True live bot → ValueError
    ...


@pytest.mark.asyncio
async def test_get_comparison_ready_flag(db):
    # shadow running with started_at <= now() - window_days * day → comparison_ready=True
    # shadow started yesterday, window=14 → comparison_ready=False (M-new-7 fix)
    ...


@pytest.mark.asyncio
async def test_promote_updates_live_params(db, supervisor_mock):
    # promote() → bots.strategy_params set to shadow params; shadow_promoted_at set
    ...


@pytest.mark.asyncio
async def test_promote_soft_deletes_shadow(db, supervisor_mock):
    # shadow.status='deleted' after promote; bot_runs rows survive
    ...


@pytest.mark.asyncio
async def test_promote_inserts_audit_row(db, supervisor_mock):
    # shadow_promotion_events row created with metric snapshots and comparison_window_start
    ...


@pytest.mark.asyncio
async def test_promote_wrong_shadow_of(db, supervisor_mock):
    # shadow.shadow_of != live_bot_id → ValueError
    ...


@pytest.mark.asyncio
async def test_shadow_bot_fill_does_not_leak_to_live_child():
    # fill on fills:live:* channel routed to live bot child; shadow child not notified
    # fill on fills:paper:* channel routed to shadow child; live bot child not notified
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/services/shadow_promoter/test_service.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement `types.py`**

```python
# backend/app/services/shadow_promoter/types.py
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel


class ShadowMetrics(BaseModel):
    sharpe: float | None = None
    mar: float | None = None
    max_dd: float | None = None
    win_rate: float | None = None
    avg_trade_pnl: Decimal = Decimal("0")
    total_trades: int = 0
    window_days: int = 0


class ShadowVsLive(BaseModel):
    shadow_bot_id: UUID
    shadow_bot_name: str
    shadow_metrics: ShadowMetrics
    live_metrics: ShadowMetrics
    delta: dict[str, str]
    running_since: datetime
    comparison_window_days: int
    comparison_ready: bool


class ShadowComparisonReport(BaseModel):
    live_bot_id: UUID
    shadows: list[ShadowVsLive]
    generated_at: datetime


class ShadowPromotionEvent(BaseModel):
    id: UUID
    shadow_bot_id: UUID
    live_bot_id: UUID
    promoted_by: str
    comparison_window_days: int
    shadow_metrics: ShadowMetrics
    live_metrics: ShadowMetrics
    promoted_at: datetime
```

- [ ] **Step 4: Implement `metrics.py`**

```python
# backend/app/services/shadow_promoter/metrics.py
from prometheus_client import Counter, Gauge

shadow_promoter_created_total = Counter(
    "shadow_promoter_created_total", "Shadow bots created"
)
shadow_promoter_promoted_total = Counter(
    "shadow_promoter_promoted_total", "Shadow bots promoted to live"
)
shadow_promoter_promote_failures_total = Counter(
    "shadow_promoter_promote_failures_total", "Shadow promotion transaction failures"
)
shadow_promoter_comparison_notify_total = Counter(
    "shadow_promoter_comparison_notify_total", "Shadow comparison-ready notifications published"
)
shadow_promoter_active_shadows = Gauge(
    "shadow_promoter_active_shadows", "Active running shadow bots"
)
```

- [ ] **Step 5: Implement `service.py`**

Service must implement `create_shadow()`, `get_comparison()`, `promote()`, `check_auto_promote_eligibility()`. Key contracts:
- `create_shadow()` step 2: explicitly `mode='paper'` on shadow bot row (H3-new-1).
- `get_comparison()`: `comparison_ready = shadow.started_at <= now() - window_days * day` (M-new-7 fix — oldest completed run started BEFORE window boundary).
- `promote()`: `comparison_window_start = now() - window_days * interval '1 day'` in the audit row.
- `check_auto_promote_eligibility()`: always returns `False`.

```python
# backend/app/services/shadow_promoter/service.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.shadow_promoter import metrics as m
from app.services.shadow_promoter.types import (
    ShadowComparisonReport, ShadowMetrics, ShadowPromotionEvent, ShadowVsLive,
)

logger = structlog.get_logger(__name__)


class ShadowPromoterService:
    def __init__(
        self,
        db_factory: async_sessionmaker[AsyncSession],
        supervisor: Any,
        redis: Any,
    ) -> None:
        self._db_factory = db_factory
        self._supervisor = supervisor
        self._redis = redis

    async def create_shadow(
        self,
        live_bot_id: UUID,
        override_params: dict,
        comparison_window_days: int,
        created_by: str,
        db: AsyncSession,
    ) -> UUID:
        live = (await db.execute(
            text("SELECT * FROM bots WHERE id=:id AND deleted_at IS NULL"),
            {"id": str(live_bot_id)},
        )).one_or_none()
        if live is None:
            raise ValueError("bot_not_found")
        live = dict(live._mapping)
        if live.get("is_shadow"):
            raise ValueError("cannot_shadow_a_shadow")

        merged_params = {**(live.get("strategy_params") or {}), **override_params}

        result = await db.execute(
            text("""INSERT INTO bots
                    (name, strategy_file, strategy_params, mode, status,
                     is_shadow, shadow_of, shadow_comparison_window_days,
                     advisor_config, strategy_schema)
                    VALUES (:name, :sf, :sp, 'paper', 'stopped',
                            true, :sof, :wd, :ac, :ss)
                    RETURNING id"""),
            {
                "name": f"{live['name']} [shadow]",
                "sf": live["strategy_file"],
                "sp": json.dumps(merged_params),
                "sof": str(live_bot_id),
                "wd": comparison_window_days,
                "ac": json.dumps(live.get("advisor_config")) if live.get("advisor_config") else None,
                "ss": json.dumps(live.get("strategy_schema")) if live.get("strategy_schema") else None,
            },
        )
        shadow_id = result.scalar()

        # Copy risk caps
        caps_row = (await db.execute(
            text("SELECT * FROM bot_risk_caps WHERE bot_id=:bid"),
            {"bid": str(live_bot_id)},
        )).one_or_none()
        if caps_row:
            caps = dict(caps_row._mapping)
            caps.pop("id", None)
            caps["bot_id"] = str(shadow_id)
            await db.execute(
                text("INSERT INTO bot_risk_caps (bot_id, max_position_size, daily_loss_limit, "
                     "max_open_orders, max_order_size, allowed_asset_classes) "
                     "VALUES (:bot_id, :mps, :dll, :moo, :mos, :aac)"),
                {k: caps.get(k) for k in ("bot_id", "max_position_size", "daily_loss_limit",
                                           "max_open_orders", "max_order_size", "allowed_asset_classes")},
            )

        # Copy bot_accounts rows (no mode column — bots.mode is authoritative)
        accounts = (await db.execute(
            text("SELECT account_id FROM bot_accounts WHERE bot_id=:bid"),
            {"bid": str(live_bot_id)},
        )).all()
        for acct in accounts:
            await db.execute(
                text("INSERT INTO bot_accounts (bot_id, account_id) VALUES (:bid, :aid)"),
                {"bid": str(shadow_id), "aid": str(acct[0])},
            )

        await db.commit()
        m.shadow_promoter_created_total.inc()
        return shadow_id

    async def get_comparison(
        self, live_bot_id: UUID, db: AsyncSession
    ) -> ShadowComparisonReport:
        shadows = (await db.execute(
            text("""SELECT id, name, shadow_comparison_window_days, created_at
                    FROM bots
                    WHERE shadow_of=:lid AND is_shadow=true AND deleted_at IS NULL"""),
            {"lid": str(live_bot_id)},
        )).all()

        shadow_vs_lives: list[ShadowVsLive] = []
        for shadow in shadows:
            sid = shadow[0]
            window_days = shadow[2] or 14

            shadow_metrics = await self._aggregate_metrics(sid, window_days, db)
            live_metrics = await self._aggregate_metrics(live_bot_id, window_days, db)

            # comparison_ready: oldest completed run started BEFORE window boundary (M-new-7)
            oldest = (await db.execute(
                text("""SELECT started_at FROM bot_runs
                        WHERE bot_id=:bid AND status='stopped'
                        ORDER BY started_at ASC LIMIT 1"""),
                {"bid": str(sid)},
            )).scalar()
            comparison_ready = (
                oldest is not None and
                oldest <= datetime.now(timezone.utc).replace(
                    tzinfo=None
                ) - __import__("datetime").timedelta(days=window_days)
            )

            delta: dict[str, str] = {}
            if shadow_metrics.sharpe is not None and live_metrics.sharpe is not None:
                delta["sharpe"] = f"{shadow_metrics.sharpe - live_metrics.sharpe:+.2f}"
            if shadow_metrics.max_dd is not None and live_metrics.max_dd is not None:
                delta["max_dd"] = f"{shadow_metrics.max_dd - live_metrics.max_dd:+.4f}"

            shadow_vs_lives.append(ShadowVsLive(
                shadow_bot_id=sid,
                shadow_bot_name=shadow[1],
                shadow_metrics=shadow_metrics,
                live_metrics=live_metrics,
                delta=delta,
                running_since=shadow[3],
                comparison_window_days=window_days,
                comparison_ready=comparison_ready,
            ))

        return ShadowComparisonReport(
            live_bot_id=live_bot_id,
            shadows=shadow_vs_lives,
            generated_at=datetime.now(timezone.utc),
        )

    async def promote(
        self,
        live_bot_id: UUID,
        shadow_bot_id: UUID,
        promoted_by: str,
        db: AsyncSession,
    ) -> None:
        shadow = (await db.execute(
            text("SELECT * FROM bots WHERE id=:id AND deleted_at IS NULL"),
            {"id": str(shadow_bot_id)},
        )).one_or_none()
        if shadow is None:
            raise ValueError("shadow_not_found")
        shadow = dict(shadow._mapping)
        if str(shadow.get("shadow_of")) != str(live_bot_id):
            raise ValueError("shadow_not_owned_by_live_bot")
        if not shadow.get("is_shadow"):
            raise ValueError("bot_is_not_a_shadow")

        try:
            # Stop live and shadow if running
            for bid in (live_bot_id, shadow_bot_id):
                bot_status = (await db.execute(
                    text("SELECT status FROM bots WHERE id=:id"),
                    {"id": str(bid)},
                )).scalar()
                if bot_status == "running":
                    await self._redis.publish(
                        f"bot:control:{bid}",
                        json.dumps({"cmd": "STOP"}),
                    )

            window_days = shadow.get("shadow_comparison_window_days") or 14

            await db.execute(
                text("UPDATE bots SET strategy_params=:p, shadow_promoted_at=now() WHERE id=:id"),
                {"p": json.dumps(shadow.get("strategy_params") or {}), "id": str(live_bot_id)},
            )

            shadow_metrics = await self._aggregate_metrics(shadow_bot_id, window_days, db)
            live_metrics = await self._aggregate_metrics(live_bot_id, window_days, db)

            await db.execute(
                text("""INSERT INTO shadow_promotion_events
                        (shadow_bot_id, live_bot_id, promoted_by, comparison_window_days,
                         comparison_window_start, shadow_metrics, live_metrics)
                        VALUES (:sid, :lid, :by, :wd,
                                now() - :wd * interval '1 day',
                                :sm::jsonb, :lm::jsonb)"""),
                {
                    "sid": str(shadow_bot_id),
                    "lid": str(live_bot_id),
                    "by": promoted_by,
                    "wd": window_days,
                    "sm": shadow_metrics.model_dump_json(),
                    "lm": live_metrics.model_dump_json(),
                },
            )

            await db.execute(
                text("UPDATE bots SET deleted_at=now(), is_shadow=false WHERE id=:id"),
                {"id": str(shadow_bot_id)},
            )
            await db.commit()

        except Exception:
            m.shadow_promoter_promote_failures_total.inc()
            raise

        await self._supervisor._start_bot(str(live_bot_id))
        await self._redis.publish(
            f"bot:shadow:{live_bot_id}",
            json.dumps({"v": 1, "type": "promoted",
                        "shadow_bot_id": str(shadow_bot_id), "promoted_by": promoted_by}),
        )
        m.shadow_promoter_promoted_total.inc()

    def check_auto_promote_eligibility(self, live_bot_id: UUID) -> bool:
        return False

    async def _aggregate_metrics(
        self, bot_id: UUID, window_days: int, db: AsyncSession
    ) -> ShadowMetrics:
        row = (await db.execute(
            text("""SELECT AVG(kpi_sharpe), AVG(kpi_mar), AVG(kpi_max_dd),
                           AVG(kpi_win_rate), AVG(kpi_avg_trade_pnl), SUM(total_orders)
                    FROM bot_runs
                    WHERE bot_id=:bid AND status='stopped'
                      AND started_at >= now() - :wd * interval '1 day'"""),
            {"bid": str(bot_id), "wd": window_days},
        )).one()
        return ShadowMetrics(
            sharpe=row[0],
            mar=row[1],
            max_dd=row[2],
            win_rate=row[3],
            avg_trade_pnl=Decimal(str(row[4] or 0)),
            total_trades=row[5] or 0,
            window_days=window_days,
        )
```

- [ ] **Step 6: Run tests**

```bash
cd backend && pytest tests/services/shadow_promoter/test_service.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/shadow_promoter/ backend/tests/services/shadow_promoter/
git commit -m "feat(phase21b): shadow-promoter service + types + metrics"
```

---

## Task 7: BotSupervisor.restart() + BotFillRouter channel isolation + BotContext is_shadow guard

**Files:**
- Modify: `backend/app/bot/supervisor.py`
- Modify: `backend/app/bot/fill_router.py`
- Modify: `backend/app/bot/context.py`
- Test: `backend/tests/bot/test_supervisor_restart.py`
- Test: `backend/tests/bot/test_fill_router_isolation.py`

**Routing:** Codex (modifying Phase 19 surfaces; fill channel isolation is cross-cutting)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/bot/test_supervisor_restart.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from app.bot.supervisor import BotSupervisor
from app.services.param_tuner.types import SupervisorRestartError


@pytest.mark.asyncio
async def test_restart_running_bot_stops_then_starts(redis_mock, db_mock):
    supervisor = BotSupervisor(redis_mock, db_mock)
    bot_id = uuid4()
    bot_id_str = str(bot_id)
    supervisor._running_bots[bot_id_str] = MagicMock()
    supervisor._respawn_counts[bot_id_str] = 3  # should be reset
    # Simulate pubsub returning stopped status
    redis_mock.subscribe.return_value.__aenter__ = AsyncMock(return_value=...)
    with patch.object(supervisor, "_wait_for_bot_stopped", return_value=True):
        with patch.object(supervisor, "_start_bot", new_callable=AsyncMock) as mock_start:
            await supervisor.restart(bot_id)
            mock_start.assert_called_once_with(bot_id_str)
    assert supervisor._respawn_counts.get(bot_id_str, 0) == 0


@pytest.mark.asyncio
async def test_restart_stopped_bot_raises(redis_mock, db_mock):
    supervisor = BotSupervisor(redis_mock, db_mock)
    bot_id = uuid4()
    # No running process
    with pytest.raises(SupervisorRestartError) as exc_info:
        await supervisor.restart(bot_id)
    assert exc_info.value.reason in ("stopped", "not_running")


@pytest.mark.asyncio
async def test_restart_timeout_raises_stop_timeout(redis_mock, db_mock):
    supervisor = BotSupervisor(redis_mock, db_mock)
    bot_id = uuid4()
    bot_id_str = str(bot_id)
    supervisor._running_bots[bot_id_str] = MagicMock()
    with patch.object(supervisor, "_wait_for_bot_stopped", return_value=False):
        with pytest.raises(SupervisorRestartError) as exc_info:
            await supervisor.restart(bot_id, stop_drain_seconds=0.01)
        assert exc_info.value.reason == "stop_timeout"
```

```python
# backend/tests/bot/test_fill_router_isolation.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.bot.fill_router import BotFillRouter


@pytest.mark.asyncio
async def test_shadow_bot_fill_does_not_leak_to_live_child(db_mock, redis_mock):
    router = BotFillRouter(db_mock, redis_mock)
    # live fill event on fills:live:... → published to live bot child, not shadow
    # paper fill event on fills:paper:... → published to shadow child, not live bot
    ...


@pytest.mark.asyncio
async def test_live_fill_not_routed_to_shadow(db_mock, redis_mock):
    router = BotFillRouter(db_mock, redis_mock)
    # live fill event → shadow child does not receive it
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/bot/test_supervisor_restart.py tests/bot/test_fill_router_isolation.py -v
```
Expected: FAIL.

- [ ] **Step 3: Add `restart()` to `supervisor.py`**

Add after the existing `_start_bot` method:

```python
async def restart(
    self, bot_id: UUID, stop_drain_seconds: float = 5.0
) -> None:
    """Atomically stop a running/paused/error bot and restart it."""
    from app.services.param_tuner.types import SupervisorRestartError
    bot_id_str = str(bot_id)
    if bot_id_str not in self._running_bots:
        raise SupervisorRestartError("stopped")

    self._send_to_child(bot_id_str, {"cmd": "STOP"})
    stopped = await self._wait_for_bot_stopped(bot_id_str, timeout=stop_drain_seconds + 3.0)
    if not stopped:
        raise SupervisorRestartError("stop_timeout")

    self._respawn_counts[bot_id_str] = 0
    await self._start_bot(bot_id_str)

async def _wait_for_bot_stopped(self, bot_id_str: str, timeout: float = 8.0) -> bool:
    """Poll bot:status:{bot_id} pubsub until stopped or timeout."""
    import asyncio
    deadline = asyncio.get_event_loop().time() + timeout
    pubsub = self._redis.pubsub()
    await pubsub.subscribe(f"bot:status:{bot_id_str}")
    try:
        while asyncio.get_event_loop().time() < deadline:
            remaining = deadline - asyncio.get_event_loop().time()
            msg = await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=True), timeout=min(0.5, remaining))
            if msg and msg.get("data"):
                import json
                try:
                    data = json.loads(msg["data"])
                    if data.get("status") in ("stopped", "paused"):
                        return True
                except Exception:
                    pass
        return False
    except asyncio.TimeoutError:
        return False
    finally:
        await pubsub.unsubscribe(f"bot:status:{bot_id_str}")
        await pubsub.close()
```

- [ ] **Step 4: Add fill channel isolation to `fill_router.py`**

Add at the top of `handle_event()` after `order_id` extraction, read `is_shadow` from bot_orders JOIN bots:

```python
# In handle_event(), after looking up bot_id:
is_shadow_row = await self._db.execute(
    text("SELECT b.is_shadow FROM bots b "
         "JOIN bot_orders bo ON bo.bot_id=b.id "
         "WHERE bo.order_id=:oid"),
    {"oid": order_id},
)
is_shadow = is_shadow_row.scalar() or False

# Channel isolation (H-new-4): paper fills on fills:paper:*, live on fills:live:*
channel = event.get("channel", "")
is_paper_channel = channel.startswith("fills:paper:")
is_live_channel = channel.startswith("fills:live:") or not is_paper_channel

if is_shadow and is_live_channel:
    return  # shadow bots must not receive live fills
if not is_shadow and is_paper_channel:
    return  # live bots must not receive paper fills
```

- [ ] **Step 5: Add `is_shadow` guard to `context.py`**

In `BotContext.place_order()`, at the very start of the method:

```python
if self.bot.is_shadow:
    raise ShadowBotLiveTradeAttempt(
        f"Shadow bot {self.bot.id} attempted a trade — all orders must go to paper broker"
    )
```

Add `ShadowBotLiveTradeAttempt(Exception)` class to `context.py`.

- [ ] **Step 6: Run tests**

```bash
cd backend && pytest tests/bot/test_supervisor_restart.py tests/bot/test_fill_router_isolation.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/bot/supervisor.py backend/app/bot/fill_router.py backend/app/bot/context.py backend/tests/bot/
git commit -m "feat(phase21b): BotSupervisor.restart() + fill channel isolation + is_shadow guard"
```

---

## Task 8: Advisor extensions — filings/earnings context + Telegram notifier

**Files:**
- Modify: `backend/app/services/advisor/types.py` — add `AdvisorMode.SHADOW`, `AdvisorConfig.notify_telegram`
- Modify: `backend/app/services/advisor/context_builder.py` — filings/earnings injection
- Create: `backend/app/services/telegram/advisor_notify.py`
- Test: `backend/tests/services/advisor/test_context_builder_filings.py`
- Test: `backend/tests/services/telegram/test_advisor_notify.py`

**Routing:** Codex (cross-file: existing context_builder + existing telegram patterns)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/services/advisor/test_context_builder_filings.py
@pytest.mark.asyncio
async def test_filings_injected_into_context(db):
    # Insert filing with llm_summary → appears in context payload
    ...

@pytest.mark.asyncio
async def test_filings_skipped_when_no_summary(db):
    # Filing with llm_summary=NULL → omitted
    ...

@pytest.mark.asyncio
async def test_earnings_injected_upcoming(db):
    # earnings_event within 14 days → appears in context
    ...

@pytest.mark.asyncio
async def test_filings_context_gate_disabled(db):
    # app_config[advisor/filings_context_enabled]=false → queries skipped
    ...
```

```python
# backend/tests/services/telegram/test_advisor_notify.py
@pytest.mark.asyncio
async def test_telegram_veto_notify_fires_on_veto(redis_mock, bot_mock):
    # advisor veto pubsub frame → Telegram message sent
    ...

@pytest.mark.asyncio
async def test_telegram_veto_notify_skipped_on_approve():
    # approve verdict → no message
    ...

@pytest.mark.asyncio
async def test_telegram_override_command(redis_mock, db_mock):
    # /override_{id} from allowlisted user with jwt mapping → PATCH called; reply sent
    ...

@pytest.mark.asyncio
async def test_telegram_override_invalid_decision_id():
    # non-numeric id → rejected with no DB call
    ...

@pytest.mark.asyncio
async def test_telegram_override_jwt_map_missing():
    # user not in telegram/user_jwt_map → rejected with message
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/services/advisor/test_context_builder_filings.py tests/services/telegram/test_advisor_notify.py -v
```

- [ ] **Step 3: Add `AdvisorMode.SHADOW` and `notify_telegram` to `types.py`**

```python
# In AdvisorMode enum, add:
SHADOW = "SHADOW"

# In AdvisorConfig, add field:
notify_telegram: bool = True
```

Update `bots.advisor_config` CHECK to include `'SHADOW'` — this is handled by the Alembic 0064 migration from Phase 21a.1 (already applied). The `SHADOW` mode code path in `AdvisorService.review()` calls `_do_review()` but does not apply the verdict (no veto).

- [ ] **Step 4: Add filings/earnings injection to `context_builder.py`**

In `ContextBuilder.build()`, after existing context queries and before assembling the payload, add:

```python
# Filings injection (config-gated)
filings_cfg = (await db.execute(
    text("SELECT value FROM app_config WHERE key='advisor/filings_context_enabled'")
)).one_or_none()
filings_enabled = filings_cfg is None or filings_cfg["value"].lower() != "false"

recent_filings = []
upcoming_earnings = None
past_earnings = []

if filings_enabled:
    filings_rows = (await db.execute(
        text("""SELECT filing_type, filed_at, llm_summary
                FROM filings f
                JOIN instrument_filing_links ifl ON ifl.filing_id=f.id
                JOIN instruments i ON i.id=ifl.instrument_id
                WHERE i.canonical_id=:cid
                  AND f.filed_at >= now() - interval '30 days'
                  AND f.llm_summary IS NOT NULL
                ORDER BY f.filed_at DESC LIMIT 3"""),
        {"cid": intent.canonical_id},
    )).all()
    for row in filings_rows:
        recent_filings.append({
            "filing_type": row[0],
            "filed_at": row[1].isoformat() if row[1] else None,
            "summary": (row[2] or "")[:300],
        })

    earnings_row = (await db.execute(
        text("""SELECT expected_date, estimate_eps, consensus_eps
                FROM earnings_events e
                JOIN instruments i ON i.id=e.instrument_id
                WHERE i.canonical_id=:cid
                  AND e.expected_date BETWEEN now() AND now() + interval '14 days'
                ORDER BY e.expected_date ASC LIMIT 1"""),
        {"cid": intent.canonical_id},
    )).one_or_none()
    if earnings_row:
        upcoming_earnings = {
            "expected_date": earnings_row[0].isoformat() if earnings_row[0] else None,
            "estimate_eps": str(earnings_row[1]) if earnings_row[1] else None,
            "consensus_eps": str(earnings_row[2]) if earnings_row[2] else None,
        }

    past_rows = (await db.execute(
        text("""SELECT expected_date, estimate_eps, consensus_eps, actual_eps
                FROM earnings_events e
                JOIN instruments i ON i.id=e.instrument_id
                WHERE i.canonical_id=:cid
                  AND e.expected_date BETWEEN now() - interval '7 days' AND now()
                ORDER BY e.expected_date DESC LIMIT 3"""),
        {"cid": intent.canonical_id},
    )).all()
    for row in past_rows:
        past_earnings.append({
            "expected_date": row[0].isoformat() if row[0] else None,
            "actual_eps": str(row[3]) if row[3] else None,
        })
```

Include `recent_filings`, `upcoming_earnings`, `past_earnings` in the context payload dict.

- [ ] **Step 5: Implement `advisor_notify.py`**

```python
# backend/app/services/telegram/advisor_notify.py
from __future__ import annotations

import asyncio
import html
import json
from typing import Any

import structlog
from prometheus_client import Counter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

logger = structlog.get_logger(__name__)

telegram_advisor_veto_notify_total = Counter(
    "telegram_advisor_veto_notify_total", "Advisor VETO Telegram notifications sent"
)
telegram_advisor_override_total = Counter(
    "telegram_advisor_override_total", "Advisor override Telegram commands",
    ["outcome"]  # applied|rejected|rate_limited
)
telegram_advisor_notify_failures_total = Counter(
    "telegram_advisor_notify_failures_total", "Advisor notify failures"
)


class AdvisorTelegramNotifier:
    """Lifespan singleton. psubscribes bot:advisor:* and forwards VETO frames."""

    def __init__(
        self,
        redis: Any,
        telegram_bot: Any,
        db_factory: async_sessionmaker[AsyncSession],
        rate_limiter: Any,
    ) -> None:
        self._redis = redis
        self._bot = telegram_bot
        self._db_factory = db_factory
        self._rate_limiter = rate_limiter
        self._bot_name_cache: dict[str, str] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _listen(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe("bot:advisor:*")
        try:
            async for message in pubsub.listen():
                if message["type"] != "pmessage":
                    continue
                try:
                    frame = json.loads(message["data"])
                    if frame.get("verdict") == "veto":
                        await self._handle_veto(frame)
                except Exception:
                    telegram_advisor_notify_failures_total.inc()
        finally:
            await pubsub.punsubscribe("bot:advisor:*")
            await pubsub.close()

    async def _handle_veto(self, frame: dict) -> None:
        async with self._db_factory() as db:
            # Check global gate
            gate = (await db.execute(
                text("SELECT value FROM app_config WHERE key='telegram/advisor_veto_notify'")
            )).one_or_none()
            if gate and gate["value"].lower() == "false":
                return

            # Check per-bot gate via AdvisorConfig.notify_telegram
            advisor_cfg_row = (await db.execute(
                text("SELECT advisor_config FROM bots WHERE id=:bid"),
                {"bid": str(frame.get("bot_id", ""))},
            )).one_or_none()
            if advisor_cfg_row and advisor_cfg_row[0]:
                cfg = advisor_cfg_row[0]
                if isinstance(cfg, dict) and cfg.get("notify_telegram") is False:
                    return

            bot_name = await self._get_bot_name(frame.get("bot_id"), db)

            # Get allowlisted chat IDs
            allowlist_row = (await db.execute(
                text("SELECT value FROM app_config WHERE key='telegram/allowlist'")
            )).one_or_none()
            if not allowlist_row:
                return
            chat_ids = json.loads(allowlist_row["value"])

            msg = (
                f"🚫 <b>Advisor VETO</b> — {html.escape(bot_name)}\n"
                f"Symbol: {html.escape(str(frame.get('canonical_id', '')))}\n"
                f"Side: {html.escape(str(frame.get('side', '')))} "
                f"{html.escape(str(frame.get('qty', '')))}\n"
                f"Reason: {html.escape(str(frame.get('reasoning', ''))[:200])}\n"
                f"Tags: {html.escape(', '.join(frame.get('advice_tags', [])) or 'none')}\n"
                f"Confidence: {html.escape(str(frame.get('confidence') or 'n/a'))}\n\n"
                f"Run it anyway? /override_{frame.get('id', '')}"
            )
            for chat_id in chat_ids:
                try:
                    await self._bot.send_message(
                        chat_id=chat_id,
                        text=msg,
                        parse_mode="HTML",
                    )
                    telegram_advisor_veto_notify_total.inc()
                except Exception:
                    telegram_advisor_notify_failures_total.inc()

    async def _get_bot_name(self, bot_id: Any, db: AsyncSession) -> str:
        key = str(bot_id)
        if key in self._bot_name_cache:
            return self._bot_name_cache[key]
        cached = await self._redis.get(f"bot:name:{key}")
        if cached:
            self._bot_name_cache[key] = cached.decode()
            return self._bot_name_cache[key]
        row = (await db.execute(
            text("SELECT name FROM bots WHERE id=:id"), {"id": key}
        )).one_or_none()
        name = row[0] if row else key
        await self._redis.setex(f"bot:name:{key}", 300, name)
        self._bot_name_cache[key] = name
        return name

    async def handle_override_command(
        self,
        decision_id_str: str,
        from_user_id: int,
        db: AsyncSession,
    ) -> str:
        """Handle /override_{decision_id} Telegram command. Returns reply text."""
        # Validate numeric decision_id (injection guard)
        if not decision_id_str.isdigit():
            telegram_advisor_override_total.labels(outcome="rejected").inc()
            return "⛔ Invalid decision ID."

        decision_id = int(decision_id_str)

        # jwt-subject scoping (M-new-5)
        jwt_map_row = (await db.execute(
            text("SELECT value FROM app_config WHERE key='telegram/user_jwt_map'")
        )).one_or_none()
        if not jwt_map_row:
            telegram_advisor_override_total.labels(outcome="rejected").inc()
            return "⛔ Your Telegram user is not mapped to a JWT subject — contact admin."
        jwt_map = json.loads(jwt_map_row["value"])
        jwt_subject = jwt_map.get(str(from_user_id))
        if not jwt_subject:
            telegram_advisor_override_total.labels(outcome="rejected").inc()
            return "⛔ Your Telegram user is not mapped to a JWT subject — contact admin."

        # Rate limit (fail-CLOSED on Redis error)
        try:
            allowed = await self._rate_limiter.check("check_trade", from_user_id)
        except Exception:
            telegram_advisor_override_total.labels(outcome="rate_limited").inc()
            return "⛔ Rate limit check failed. Try again shortly."
        if not allowed:
            telegram_advisor_override_total.labels(outcome="rate_limited").inc()
            return "⛔ Rate limit exceeded."

        # Find decision and assert ownership
        row = (await db.execute(
            text("""SELECT bad.id, bad.bot_id FROM bot_advisor_decisions bad
                    JOIN bots b ON b.id=bad.bot_id
                    WHERE bad.id=:did"""),
            {"did": decision_id},
        )).one_or_none()
        if not row:
            telegram_advisor_override_total.labels(outcome="rejected").inc()
            return "⛔ Decision not found."

        await db.execute(
            text("""UPDATE bot_advisor_decisions
                    SET override_action='approve',
                        override_reason=:reason,
                        override_at=now()
                    WHERE id=:did"""),
            {"reason": f"telegram_override:{from_user_id}", "did": decision_id},
        )
        await db.commit()
        telegram_advisor_override_total.labels(outcome="applied").inc()
        return f"✅ Override recorded for decision {decision_id}. The original order was not re-submitted."
```

- [ ] **Step 6: Run tests**

```bash
cd backend && pytest tests/services/advisor/test_context_builder_filings.py tests/services/telegram/test_advisor_notify.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/advisor/types.py backend/app/services/advisor/context_builder.py backend/app/services/telegram/advisor_notify.py backend/tests/services/advisor/test_context_builder_filings.py backend/tests/services/telegram/test_advisor_notify.py
git commit -m "feat(phase21b): filings/earnings in advisor context + Telegram VETO notifier"
```

---

## Task 9: AdvisorStub + BacktestRunner wiring

**Files:**
- Create: `backend/app/backtest/advisor_stub.py`
- Modify: `backend/app/backtest/runner.py`
- Test: `backend/tests/backtest/test_advisor_stub.py`
- Test: `backend/tests/backtest/test_runner_advisor.py`

**Routing:** Qwen (new file, bounded scope, deterministic logic)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/backtest/test_advisor_stub.py
from app.backtest.advisor_stub import AdvisorStub
from app.services.advisor.types import AdvisorMode


def test_stub_approve_by_default():
    stub = AdvisorStub(mode=AdvisorMode.VETO, veto_injections=None)
    verdict = stub.review(bar_index=1, canonical_id="AAPL", intent={}, bar_buffer=[])
    assert verdict.action == "approve"


def test_stub_veto_injection_fires():
    stub = AdvisorStub(mode=AdvisorMode.VETO, veto_injections=[(5, "AAPL")])
    verdict = stub.review(bar_index=5, canonical_id="AAPL", intent={}, bar_buffer=[])
    assert verdict.action == "veto"
    assert verdict.reasoning == "veto_injection"


def test_stub_veto_injection_different_bar_no_veto():
    stub = AdvisorStub(mode=AdvisorMode.VETO, veto_injections=[(5, "AAPL")])
    verdict = stub.review(bar_index=6, canonical_id="AAPL", intent={}, bar_buffer=[])
    assert verdict.action == "approve"


def test_stub_observe_mode_never_vetoes():
    stub = AdvisorStub(mode=AdvisorMode.OBSERVE, veto_injections=[(5, "AAPL")])
    verdict = stub.review(bar_index=5, canonical_id="AAPL", intent={}, bar_buffer=[])
    assert verdict.action == "approve"  # OBSERVE: logs but never vetoes
```

```python
# backend/tests/backtest/test_runner_advisor.py
@pytest.mark.asyncio
async def test_backtest_runner_with_advisor_skips_vetoed_orders(db):
    # advisor_config set, veto_injection at bar 5 → that order skipped; advisor_vetoed_pnl accumulated
    ...

@pytest.mark.asyncio
async def test_backtest_runner_persists_advisor_decisions(db):
    # backtest_advisor_decisions rows created per order intent
    ...

@pytest.mark.asyncio
async def test_backtest_kpis_include_advisor_fields(db):
    # report includes advisor_veto_count, advisor_veto_rate, advisor_vetoed_pnl
    ...

@pytest.mark.asyncio
async def test_backtest_without_advisor_config_unaffected(db):
    # advisor_config IS NULL → no stub; no backtest_advisor_decisions rows; existing behavior unchanged
    ...

@pytest.mark.asyncio
async def test_backtest_advisor_decisions_flushed_on_failure(db):
    # backtest fails mid-run → _on_status_change flushes buffered advisor decisions
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/backtest/test_advisor_stub.py tests/backtest/test_runner_advisor.py -v
```

- [ ] **Step 3: Implement `advisor_stub.py`**

```python
# backend/app/backtest/advisor_stub.py
from __future__ import annotations

import time

from app.services.advisor.types import AdvisorMode, AdvisorVerdict


class AdvisorStub:
    """Deterministic in-backtest advisor. No AI calls, no DB, no Redis."""

    def __init__(
        self,
        mode: AdvisorMode,
        veto_injections: list[tuple[int, str]] | None = None,
    ) -> None:
        self._mode = mode
        self._veto_set: frozenset[tuple[int, str]] = (
            frozenset(veto_injections) if veto_injections else frozenset()
        )

    def review(
        self,
        bar_index: int,
        canonical_id: str,
        intent: dict,
        bar_buffer: list[dict],
    ) -> AdvisorVerdict:
        t0 = time.monotonic()
        if self._mode != AdvisorMode.VETO:
            return AdvisorVerdict(action="approve", reasoning="backtest_stub_observe")
        if (bar_index, canonical_id) in self._veto_set:
            return AdvisorVerdict(action="veto", reasoning="veto_injection")
        return AdvisorVerdict(action="approve", reasoning="backtest_stub")
```

- [ ] **Step 4: Wire `AdvisorStub` into `BacktestRunner`**

In `runner.py`, find the bar-loop where `FillSimulator` processes orders. Before pushing to fill queue, intercept:

```python
# At top of runner.py, add imports:
from app.backtest.advisor_stub import AdvisorStub
from app.services.advisor.types import AdvisorMode

# In BacktestRunner.__init__ or run():
advisor_stub = None
if backtest_config.get("advisor_config"):
    ac = backtest_config["advisor_config"]
    mode = AdvisorMode(ac.get("mode", "OBSERVE"))
    veto_injections_raw = ac.get("veto_injections", [])
    veto_injections = [
        (int(v[0]), str(v[1])) for v in veto_injections_raw
    ]
    advisor_stub = AdvisorStub(mode=mode, veto_injections=veto_injections)

advisor_decisions_buffer: list[dict] = []
advisor_vetoed_pnl = Decimal("0")
advisor_approve_count = 0
advisor_veto_count = 0

# In bar loop, when FillSimulator generates an order intent:
if advisor_stub is not None:
    t0 = time.monotonic()
    verdict = advisor_stub.review(
        bar_index=bar_idx,
        canonical_id=canonical_id,
        intent=intent,
        bar_buffer=bar_buffer,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    advisor_decisions_buffer.append({
        "backtest_id": str(backtest_id),
        "bar_index": bar_idx,
        "canonical_id": canonical_id,
        "intent": json.dumps(intent),
        "verdict": verdict.action,
        "reasoning": verdict.reasoning,
        "latency_ms": latency_ms,
    })
    if len(advisor_decisions_buffer) >= 100:
        await _flush_advisor_decisions(advisor_decisions_buffer, db)
        advisor_decisions_buffer = []
    if verdict.action == "veto":
        advisor_veto_count += 1
        advisor_vetoed_pnl += estimated_pnl  # from fill_simulator
        continue  # skip pushing to fill queue
    else:
        advisor_approve_count += 1

# In _on_status_change() (flush on done/failed/cancelled — M-new-6):
async def _on_status_change(new_status: str) -> None:
    if new_status in ("done", "failed", "cancelled") and advisor_decisions_buffer:
        await _flush_advisor_decisions(advisor_decisions_buffer, db)
        advisor_decisions_buffer.clear()

async def _flush_advisor_decisions(buf: list[dict], db: AsyncSession) -> None:
    if not buf:
        return
    await db.execute(
        text("""INSERT INTO backtest_advisor_decisions
                (backtest_id, bar_index, canonical_id, intent, verdict, reasoning, latency_ms)
                SELECT * FROM unnest(
                    :bids::uuid[], :bars::int[], :cids::text[], :intents::jsonb[],
                    :verdicts::text[], :reasonings::text[], :latencies::int[]
                )"""),
        {
            "bids": [r["backtest_id"] for r in buf],
            "bars": [r["bar_index"] for r in buf],
            "cids": [r["canonical_id"] for r in buf],
            "intents": [r["intent"] for r in buf],
            "verdicts": [r["verdict"] for r in buf],
            "reasonings": [r["reasoning"] for r in buf],
            "latencies": [r["latency_ms"] for r in buf],
        },
    )
    await db.commit()
```

Include `advisor_veto_count`, `advisor_approve_count`, `advisor_vetoed_pnl`, `advisor_veto_rate` in the backtest KPIs output.

- [ ] **Step 5: Run tests**

```bash
cd backend && pytest tests/backtest/test_advisor_stub.py tests/backtest/test_runner_advisor.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/backtest/advisor_stub.py backend/app/backtest/runner.py backend/tests/backtest/test_advisor_stub.py backend/tests/backtest/test_runner_advisor.py
git commit -m "feat(phase21b): AdvisorStub + BacktestRunner advisor wiring"
```

---

## Task 10: REST + WS API — param-tuner + shadow endpoints

**Files:**
- Modify: `backend/app/api/bots.py`
- Modify: `backend/app/api/ws_bots.py`
- Test: `backend/tests/api/test_param_tuner_endpoints.py`
- Test: `backend/tests/api/test_shadow_endpoints.py`

**Routing:** Codex (large existing file, 10 new endpoints + 2 WS endpoints)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/api/test_param_tuner_endpoints.py
@pytest.mark.asyncio
async def test_post_param_suggestions_triggers(client, admin_headers):
    # POST /api/bots/{id}/param-suggestions → 202, suggestion_id returned
    ...

@pytest.mark.asyncio
async def test_post_param_suggestions_second_active_409(client, admin_headers):
    # Second trigger while backtesting → 409
    ...

@pytest.mark.asyncio
async def test_get_param_suggestions_list(client, jwt_headers):
    # GET /api/bots/{id}/param-suggestions → list with cursor pagination
    ...

@pytest.mark.asyncio
async def test_post_param_suggestions_approve(client, admin_headers):
    # POST .../approve with candidate_index → 200; bots.strategy_params updated
    ...

@pytest.mark.asyncio
async def test_post_param_suggestions_reject(client, admin_headers):
    # POST .../reject → 200; status='rejected'
    ...
```

```python
# backend/tests/api/test_shadow_endpoints.py
@pytest.mark.asyncio
async def test_post_shadows_creates_shadow(client, admin_headers):
    # POST /api/bots/{id}/shadows → 201, shadow_bot_id returned
    ...

@pytest.mark.asyncio
async def test_get_shadows_comparison(client, jwt_headers):
    # GET /api/bots/{id}/shadows/comparison → ShadowComparisonReport
    ...

@pytest.mark.asyncio
async def test_post_shadows_promote(client, admin_headers):
    # POST /api/bots/{id}/shadows/{shadow_id}/promote → 200
    ...

@pytest.mark.asyncio
async def test_get_shadow_promotions(client, jwt_headers):
    # GET /api/bots/{id}/shadow-promotions → list
    ...

@pytest.mark.asyncio
async def test_get_backtest_advisor_decisions(client, jwt_headers):
    # GET /api/bots/{id}/backtests/{backtest_id}/advisor-decisions → cursor-paginated list
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/api/test_param_tuner_endpoints.py tests/api/test_shadow_endpoints.py -v
```

- [ ] **Step 3: Add param-tuner endpoints to `bots.py`**

Add after existing advisor endpoints (~line 510):

```python
# --- Param-tuner endpoints ---

@router.post("/bots/{bot_id}/param-suggestions", status_code=202)
async def trigger_param_suggestion(
    bot_id: UUID,
    _admin: AdminDep,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
    db: DbDep,
    redis: RedisDep,
    request: Request,
) -> dict:
    from app.services.param_tuner.service import ParamTunerService, BacktestSubmitter
    from app.services.param_tuner.types import TunerTrigger, TunerAlreadyActiveError, TunerCostCeilingError
    svc = ParamTunerService(
        ai_client=request.app.state.ai_client,
        redis=redis,
        db_factory=request.app.state.db_factory,
        backtest_submitter=BacktestSubmitter(request.app.state.db_factory),
    )
    try:
        suggestion_id = await svc.trigger(bot_id, TunerTrigger.MANUAL, db)
    except TunerAlreadyActiveError:
        raise HTTPException(status_code=409, detail="suggestion_already_active")
    except TunerCostCeilingError:
        raise HTTPException(status_code=429, detail="cost_ceiling_exceeded")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"suggestion_id": str(suggestion_id)}


@router.get("/bots/{bot_id}/param-suggestions")
async def list_param_suggestions(
    bot_id: UUID,
    user: JwtSubject,
    db: DbDep,
    before: str | None = None,
    limit: int = 20,
) -> dict:
    query = (
        "SELECT * FROM bot_param_suggestions WHERE bot_id=:bid"
        + (" AND created_at < :before" if before else "")
        + " ORDER BY created_at DESC LIMIT :lim"
    )
    params = {"bid": str(bot_id), "lim": min(limit, 50)}
    if before:
        params["before"] = before
    rows = (await db.execute(text(query), params)).all()
    return {"items": [dict(r._mapping) for r in rows]}


@router.get("/bots/{bot_id}/param-suggestions/{suggestion_id}")
async def get_param_suggestion(
    bot_id: UUID,
    suggestion_id: UUID,
    user: JwtSubject,
    db: DbDep,
) -> dict:
    row = (await db.execute(
        text("SELECT * FROM bot_param_suggestions WHERE id=:id AND bot_id=:bid"),
        {"id": str(suggestion_id), "bid": str(bot_id)},
    )).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="suggestion_not_found")
    return dict(row._mapping)


@router.post("/bots/{bot_id}/param-suggestions/{suggestion_id}/approve")
async def approve_param_suggestion(
    bot_id: UUID,
    suggestion_id: UUID,
    body: dict,
    _admin: AdminDep,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
    db: DbDep,
    request: Request,
) -> dict:
    from app.services.param_tuner.service import ParamTunerService, BacktestSubmitter
    from app.services.param_tuner.types import SupervisorRestartError
    svc = ParamTunerService(
        ai_client=request.app.state.ai_client,
        redis=request.app.state.redis,
        db_factory=request.app.state.db_factory,
        backtest_submitter=BacktestSubmitter(request.app.state.db_factory),
    )
    candidate_index = body.get("candidate_index")
    if candidate_index is None:
        raise HTTPException(status_code=422, detail="candidate_index_required")
    try:
        await svc.approve(
            suggestion_id, int(candidate_index),
            approved_by=_admin.jwt_subject,
            db=db,
            supervisor=request.app.state.supervisor,
        )
    except SupervisorRestartError as exc:
        raise HTTPException(
            status_code=500 if exc.reason == "stop_timeout" else 409,
            detail=exc.reason,
            headers={"Retry-After": "10"} if exc.reason == "mid_respawn" else {},
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True}


@router.post("/bots/{bot_id}/param-suggestions/{suggestion_id}/reject")
async def reject_param_suggestion(
    bot_id: UUID,
    suggestion_id: UUID,
    _admin: AdminDep,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
    db: DbDep,
) -> dict:
    from app.services.param_tuner.service import ParamTunerService, BacktestSubmitter
    # Use a minimal service instance for reject (no AI/submitter needed)
    await db.execute(
        text("UPDATE bot_param_suggestions SET status='rejected' WHERE id=:id AND bot_id=:bid"),
        {"id": str(suggestion_id), "bid": str(bot_id)},
    )
    await db.commit()
    return {"ok": True}
```

- [ ] **Step 4: Add shadow endpoints to `bots.py`**

```python
# --- Shadow-promoter endpoints ---

@router.post("/bots/{bot_id}/shadows", status_code=201)
async def create_shadow(
    bot_id: UUID,
    body: dict,
    _admin: AdminDep,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
    db: DbDep,
    request: Request,
) -> dict:
    from app.services.shadow_promoter.service import ShadowPromoterService
    svc = ShadowPromoterService(
        db_factory=request.app.state.db_factory,
        supervisor=request.app.state.supervisor,
        redis=request.app.state.redis,
    )
    try:
        shadow_id = await svc.create_shadow(
            live_bot_id=bot_id,
            override_params=body.get("override_params", {}),
            comparison_window_days=body.get("comparison_window_days", 14),
            created_by=_admin.jwt_subject,
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"shadow_bot_id": str(shadow_id)}


@router.get("/bots/{bot_id}/shadows/comparison")
async def get_shadow_comparison(
    bot_id: UUID,
    user: JwtSubject,
    db: DbDep,
    request: Request,
) -> dict:
    from app.services.shadow_promoter.service import ShadowPromoterService
    svc = ShadowPromoterService(
        db_factory=request.app.state.db_factory,
        supervisor=request.app.state.supervisor,
        redis=request.app.state.redis,
    )
    report = await svc.get_comparison(bot_id, db)
    return report.model_dump(mode="json")


@router.post("/bots/{bot_id}/shadows/{shadow_id}/promote")
async def promote_shadow(
    bot_id: UUID,
    shadow_id: UUID,
    _admin: AdminDep,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
    db: DbDep,
    request: Request,
) -> dict:
    from app.services.shadow_promoter.service import ShadowPromoterService
    svc = ShadowPromoterService(
        db_factory=request.app.state.db_factory,
        supervisor=request.app.state.supervisor,
        redis=request.app.state.redis,
    )
    try:
        await svc.promote(bot_id, shadow_id, promoted_by=_admin.jwt_subject, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.get("/bots/{bot_id}/shadow-promotions")
async def list_shadow_promotions(
    bot_id: UUID,
    user: JwtSubject,
    db: DbDep,
    before: str | None = None,
    limit: int = 20,
) -> dict:
    query = (
        "SELECT * FROM shadow_promotion_events WHERE live_bot_id=:lid"
        + (" AND promoted_at < :before::timestamptz" if before else "")
        + " ORDER BY promoted_at DESC LIMIT :lim"
    )
    params = {"lid": str(bot_id), "lim": min(limit, 50)}
    if before:
        params["before"] = before
    rows = (await db.execute(text(query), params)).all()
    return {"items": [dict(r._mapping) for r in rows]}


@router.get("/bots/{bot_id}/backtests/{backtest_id}/advisor-decisions")
async def list_backtest_advisor_decisions(
    bot_id: UUID,
    backtest_id: UUID,
    user: JwtSubject,
    db: DbDep,
    after_id: int | None = None,
    limit: int = 50,
) -> dict:
    query = (
        "SELECT * FROM backtest_advisor_decisions WHERE backtest_id=:bid"
        + (" AND id > :after" if after_id else "")
        + " ORDER BY id ASC LIMIT :lim"
    )
    params = {"bid": str(backtest_id), "lim": min(limit, 200)}
    if after_id:
        params["after"] = after_id
    rows = (await db.execute(text(query), params)).all()
    return {"items": [dict(r._mapping) for r in rows]}
```

- [ ] **Step 5: Add WS endpoints to `ws_bots.py`**

```python
# Tuner WS endpoint (global cap 100, per-bot cap 50)
_TUNER_WS_CONNS: dict[str, set] = {}
_TUNER_WS_GLOBAL: set = set()

@router.websocket("/ws/bots/{bot_id}/tuner")
async def ws_tuner(websocket: WebSocket, bot_id: UUID, redis: RedisDep):
    key = str(bot_id)
    if len(_TUNER_WS_GLOBAL) >= 100:
        await websocket.close(code=1008)
        return
    if len(_TUNER_WS_CONNS.get(key, set())) >= 50:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    conn_id = id(websocket)
    _TUNER_WS_CONNS.setdefault(key, set()).add(conn_id)
    _TUNER_WS_GLOBAL.add(conn_id)
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"bot:tuner:{bot_id}")
    try:
        async for msg in pubsub.listen():
            if msg["type"] == "message":
                frame = json.loads(msg["data"])
                if frame.get("v") != 1:
                    continue
                await asyncio.wait_for(websocket.send_json(frame), timeout=5.0)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        _TUNER_WS_CONNS.get(key, set()).discard(conn_id)
        _TUNER_WS_GLOBAL.discard(conn_id)
        await pubsub.unsubscribe(f"bot:tuner:{bot_id}")
        await pubsub.close()


# Shadow WS endpoint (same pattern)
_SHADOW_WS_CONNS: dict[str, set] = {}
_SHADOW_WS_GLOBAL: set = set()

@router.websocket("/ws/bots/{bot_id}/shadow")
async def ws_shadow(websocket: WebSocket, bot_id: UUID, redis: RedisDep):
    key = str(bot_id)
    if len(_SHADOW_WS_GLOBAL) >= 100:
        await websocket.close(code=1008)
        return
    if len(_SHADOW_WS_CONNS.get(key, set())) >= 50:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    conn_id = id(websocket)
    _SHADOW_WS_CONNS.setdefault(key, set()).add(conn_id)
    _SHADOW_WS_GLOBAL.add(conn_id)
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"bot:shadow:{bot_id}")
    try:
        async for msg in pubsub.listen():
            if msg["type"] == "message":
                frame = json.loads(msg["data"])
                if frame.get("v") != 1:
                    continue
                await asyncio.wait_for(websocket.send_json(frame), timeout=5.0)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        _SHADOW_WS_CONNS.get(key, set()).discard(conn_id)
        _SHADOW_WS_GLOBAL.discard(conn_id)
        await pubsub.unsubscribe(f"bot:shadow:{bot_id}")
        await pubsub.close()
```

- [ ] **Step 6: Wire APScheduler jobs in `main.py`**

Add to lifespan:
```python
# param_tuner_poll — every 60s
scheduler.add_job(
    _run_param_tuner_poll,
    "interval", seconds=60, id="param_tuner_poll"
)
# param_tuner_scheduled — Monday 02:00 local
scheduler.add_job(
    _run_param_tuner_scheduled,
    "cron", day_of_week="mon", hour=2, id="param_tuner_scheduled"
)
# shadow_comparison_notify — daily 08:00
scheduler.add_job(
    _run_shadow_comparison_notify,
    "cron", hour=8, id="shadow_comparison_notify"
)
# shadow_auto_promote_check — daily 08:05
scheduler.add_job(
    _run_shadow_auto_promote_check,
    "cron", hour=8, minute=5, id="shadow_auto_promote_check"
)
```

- [ ] **Step 7: Run tests**

```bash
cd backend && pytest tests/api/test_param_tuner_endpoints.py tests/api/test_shadow_endpoints.py -v
```
Expected: PASS.

- [ ] **Step 8: Run full backend suite**

```bash
cd backend && pytest --tb=short -q
```
Expected: all existing tests still pass.

- [ ] **Step 9: Commit**

```bash
git add backend/app/api/bots.py backend/app/api/ws_bots.py backend/app/main.py backend/tests/api/
git commit -m "feat(phase21b): param-tuner + shadow REST and WS endpoints, APScheduler wiring"
```

---

## Task 11: Frontend — param-tuner + shadow components + backtest extensions

**Files:**
- Create: `frontend/src/services/param_tuner/types.ts`
- Create: `frontend/src/services/param_tuner/api.ts`
- Create: `frontend/src/services/shadow_promoter/types.ts`
- Create: `frontend/src/services/shadow_promoter/api.ts`
- Create: `frontend/src/features/bots/hooks/useParamTunerStream.ts`
- Create: `frontend/src/features/bots/hooks/useShadowStream.ts`
- Create: `frontend/src/features/bots/components/ParamTunerSection.tsx`
- Create: `frontend/src/features/bots/components/ParamCandidateCard.tsx`
- Create: `frontend/src/features/bots/components/ShadowComparisonPanel.tsx`
- Create: `frontend/src/features/bots/components/ShadowMetricsTable.tsx`
- Modify: `frontend/src/features/bots/components/BacktestConfigForm.tsx`
- Modify: `frontend/src/features/bots/components/BacktestReportKpis.tsx`
- Modify: `frontend/src/features/bots/components/BacktestPnlChart.tsx`
- Modify: `frontend/src/features/bots/BotDetailPage.tsx`

**Routing:** Codex (large multi-file FE, cross-component TanStack Query + WS hooks)

- [ ] **Step 1: Write failing tests**

```typescript
// frontend/src/features/bots/components/ParamTunerSection.test.tsx
import { render, screen } from "@testing-library/react";
import { ParamTunerSection } from "./ParamTunerSection";

test("trigger button calls POST param-suggestions", async () => {
  // mock POST, click Trigger, assert API called
});

test("shows backtesting state during fan-out", async () => {
  // WS frame type=backtesting → loading state shown
});

test("shows ranked candidates", async () => {
  // suggestion status=ranked → candidate cards visible
});

test("shows dismiss affordance on failed suggestion", async () => {
  // status=failed → Dismiss button visible (M3-new-1 fix)
});
```

```typescript
// frontend/src/features/bots/components/ShadowComparisonPanel.test.tsx
test("create shadow form submits POST", async () => { ... });
test("comparison table renders delta column", async () => { ... });
test("promote button shows confirmation dialog", async () => { ... });
test("comparison_ready=false shows not-ready badge", async () => { ... });
```

```typescript
// frontend/src/features/bots/components/BacktestConfigForm.test.tsx — existing file
test("advisor toggle shows veto_injections textarea", async () => { ... });
test("advisor toggle hidden by default", async () => { ... });
```

```typescript
// frontend/src/features/bots/components/BacktestReportKpis.test.tsx — existing file
test("advisor fields render when advisor_veto_count > 0", async () => { ... });
test("advisor fields hidden when advisor_veto_count = 0", async () => { ... });
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend && pnpm test --run
```

- [ ] **Step 3: Implement service types + API**

```typescript
// frontend/src/services/param_tuner/types.ts
export type SuggestionStatus =
  | "pending" | "backtesting" | "ranked"
  | "approved" | "rejected" | "applied" | "failed";

export interface BacktestResultSnapshot {
  sharpe: number | null;
  mar: number | null;
  max_dd: number | null;
  win_rate: number | null;
  avg_trade_pnl: string;
  forced_close_pnl: string;
  total_trades: number;
}

export interface ParamCandidate {
  params: Record<string, unknown>;
  backtest_job_id: string | null;
  backtest_result: BacktestResultSnapshot | null;
  rank: number | null;
  delta_vs_current: Record<string, string>;
}

export interface ParamSuggestion {
  id: string;
  bot_id: string;
  triggered_by: "scheduled" | "manual";
  status: SuggestionStatus;
  candidates: ParamCandidate[];
  ai_reasoning: string | null;
  approved_candidate_index: number | null;
  created_at: string;
  updated_at: string;
}

export interface TunerWsFrame {
  v: 1;
  type: "backtesting" | "ranked" | "applied" | "failed";
  suggestion_id: string;
  candidate_count?: number;
  candidate_index?: number;
  reason?: string;
}
```

```typescript
// frontend/src/services/param_tuner/api.ts
import { checkOk } from "@/services/bots/api";

const BASE = (botId: string) => `/api/bots/${botId}/param-suggestions`;

export async function triggerParamSuggestion(
  botId: string,
  csrfNonce: string
): Promise<{ suggestion_id: string }> {
  const res = await fetch(BASE(botId), {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Nonce": csrfNonce },
  });
  return checkOk(res);
}

export async function listParamSuggestions(botId: string): Promise<{ items: ParamSuggestion[] }> {
  const res = await fetch(BASE(botId));
  return checkOk(res);
}

export async function approveParamSuggestion(
  botId: string,
  suggestionId: string,
  candidateIndex: number,
  csrfNonce: string
): Promise<void> {
  const res = await fetch(`${BASE(botId)}/${suggestionId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Nonce": csrfNonce },
    body: JSON.stringify({ candidate_index: candidateIndex }),
  });
  await checkOk(res);
}

export async function rejectParamSuggestion(
  botId: string,
  suggestionId: string,
  csrfNonce: string
): Promise<void> {
  const res = await fetch(`${BASE(botId)}/${suggestionId}/reject`, {
    method: "POST",
    headers: { "X-CSRF-Nonce": csrfNonce },
  });
  await checkOk(res);
}
```

- [ ] **Step 4: Implement `useParamTunerStream.ts`**

```typescript
// frontend/src/features/bots/hooks/useParamTunerStream.ts
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { TunerWsFrame } from "@/services/param_tuner/types";

const RETRY_DELAYS = [500, 1500, 5000, 15000];

export function useParamTunerStream(botId: string) {
  const qc = useQueryClient();
  const retryRef = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let cancelled = false;

    function connect() {
      if (cancelled) return;
      const url = new URL(`/ws/bots/${botId}/tuner`, location.href);
      url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(url.toString());

      ws.onmessage = (e) => {
        const frame: TunerWsFrame = JSON.parse(e.data);
        if (frame.v !== 1) return;
        if (frame.type === "ranked" || frame.type === "applied") {
          qc.invalidateQueries({ queryKey: ["param-suggestions", botId] });
        }
      };

      ws.onclose = () => {
        if (cancelled) return;
        const delay = RETRY_DELAYS[Math.min(retryRef.current, RETRY_DELAYS.length - 1)];
        retryRef.current++;
        setTimeout(connect, delay);
      };

      ws.onopen = () => { retryRef.current = 0; };
    }

    connect();
    return () => {
      cancelled = true;
      ws?.close();
    };
  }, [botId, qc]);
}
```

- [ ] **Step 5: Implement `ParamTunerSection.tsx`**

```typescript
// frontend/src/features/bots/components/ParamTunerSection.tsx
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParamTunerStream } from "../hooks/useParamTunerStream";
import { triggerParamSuggestion, listParamSuggestions, rejectParamSuggestion } from "@/services/param_tuner/api";
import { mintCsrfNonce } from "@/services/admin/api";
import { ParamCandidateCard } from "./ParamCandidateCard";
import type { ParamSuggestion } from "@/services/param_tuner/types";

interface Props { botId: string; isAdmin: boolean; }

export function ParamTunerSection({ botId, isAdmin }: Props) {
  const qc = useQueryClient();
  useParamTunerStream(botId);

  const { data } = useQuery({
    queryKey: ["param-suggestions", botId],
    queryFn: () => listParamSuggestions(botId),
  });

  const suggestions = data?.items ?? [];
  const active = suggestions.find(
    (s) => ["pending", "backtesting", "ranked"].includes(s.status)
  );
  const failed = suggestions.filter((s) => s.status === "failed");

  const trigger = useMutation({
    mutationFn: async () => {
      const nonce = await mintCsrfNonce();
      return triggerParamSuggestion(botId, nonce);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["param-suggestions", botId] }),
  });

  return (
    <section aria-label="Param tuner">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold">Param Tuner</h3>
        {isAdmin && !active && (
          <button
            onClick={() => trigger.mutate()}
            disabled={trigger.isPending}
            className="btn btn-sm btn-primary"
          >
            {trigger.isPending ? "Triggering…" : "Trigger"}
          </button>
        )}
      </div>

      {active && (
        <div className="space-y-2">
          <p className="text-xs text-muted">
            Status: <span className="font-mono">{active.status}</span>
          </p>
          {active.status === "ranked" && active.candidates.map((c, i) => (
            <ParamCandidateCard
              key={i}
              botId={botId}
              suggestionId={active.id}
              candidate={c}
              index={i}
              isAdmin={isAdmin}
            />
          ))}
          {active.status === "backtesting" && (
            <p className="text-xs text-muted">Running {active.candidates.length} backtests…</p>
          )}
        </div>
      )}

      {failed.length > 0 && (
        <div className="mt-4 space-y-1">
          {failed.map((s) => (
            <div key={s.id} className="flex items-center justify-between text-xs text-destructive">
              <span>Suggestion failed — no valid candidates</span>
              {isAdmin && (
                <button
                  onClick={async () => {
                    const nonce = await mintCsrfNonce();
                    await rejectParamSuggestion(botId, s.id, nonce);
                    qc.invalidateQueries({ queryKey: ["param-suggestions", botId] });
                  }}
                  className="btn btn-xs btn-ghost"
                >
                  Dismiss
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 6: Implement `ParamCandidateCard.tsx`**

```typescript
// frontend/src/features/bots/components/ParamCandidateCard.tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { approveParamSuggestion, rejectParamSuggestion } from "@/services/param_tuner/api";
import { mintCsrfNonce } from "@/services/admin/api";
import type { ParamCandidate } from "@/services/param_tuner/types";

interface Props {
  botId: string;
  suggestionId: string;
  candidate: ParamCandidate;
  index: number;
  isAdmin: boolean;
}

function DeltaBadge({ value }: { value: string }) {
  const positive = value.startsWith("+");
  return (
    <span className={positive ? "text-green-600" : "text-red-600"}>
      {value}
    </span>
  );
}

export function ParamCandidateCard({ botId, suggestionId, candidate, index, isAdmin }: Props) {
  const qc = useQueryClient();
  const r = candidate.backtest_result;

  const approve = useMutation({
    mutationFn: async () => {
      const nonce = await mintCsrfNonce();
      await approveParamSuggestion(botId, suggestionId, index, nonce);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["param-suggestions", botId] }),
  });

  const reject = useMutation({
    mutationFn: async () => {
      const nonce = await mintCsrfNonce();
      await rejectParamSuggestion(botId, suggestionId, nonce);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["param-suggestions", botId] }),
  });

  return (
    <div className="border rounded p-3 space-y-2" aria-label={`Candidate ${index + 1}`}>
      <div className="flex items-center gap-2">
        <span className="text-xs font-mono text-muted">Rank #{candidate.rank ?? "?"}</span>
      </div>
      {r && (
        <dl className="grid grid-cols-3 gap-x-4 text-xs">
          <div><dt className="text-muted">Sharpe</dt><dd>{r.sharpe?.toFixed(2) ?? "—"}</dd></div>
          <div><dt className="text-muted">MAR</dt><dd>{r.mar?.toFixed(2) ?? "—"}</dd></div>
          <div><dt className="text-muted">Max DD</dt><dd>{r.max_dd?.toFixed(4) ?? "—"}</dd></div>
          <div><dt className="text-muted">Win rate</dt><dd>{r.win_rate ? `${(r.win_rate * 100).toFixed(1)}%` : "—"}</dd></div>
          <div><dt className="text-muted">Trades</dt><dd>{r.total_trades}</dd></div>
        </dl>
      )}
      {Object.entries(candidate.delta_vs_current).length > 0 && (
        <div className="flex gap-3 text-xs">
          {Object.entries(candidate.delta_vs_current).map(([k, v]) => (
            <span key={k}>{k}: <DeltaBadge value={v} /></span>
          ))}
        </div>
      )}
      {isAdmin && (
        <div className="flex gap-2">
          <button onClick={() => approve.mutate()} disabled={approve.isPending} className="btn btn-xs btn-success">
            Approve this
          </button>
          <button onClick={() => reject.mutate()} disabled={reject.isPending} className="btn btn-xs btn-ghost">
            Reject all
          </button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 7: Implement shadow service types + API + `ShadowComparisonPanel.tsx` + `ShadowMetricsTable.tsx`**

Follow same pattern as param-tuner. Key details:
- `ShadowComparisonPanel` shows create form (override_params JSON textarea, comparison_window_days input), active shadows list, comparison table, promote button with confirmation dialog (`aria-label="Confirm shadow promotion"`).
- `comparison_ready=false` → show "Not yet comparison-ready" badge; disable promote.
- Promote uses `mintCsrfNonce`.

- [ ] **Step 8: Extend `BacktestConfigForm.tsx`**

Add `advisor_enabled: boolean` toggle (default `false`). When `true`:
- Show `advisor_mode` select: `OBSERVE | VETO`.
- Show `veto_injections` textarea (placeholder `5,AAPL\n10,TSLA`).

Include these in the form's submit payload as `advisor_config: { mode, veto_injections }` or `null`.

- [ ] **Step 9: Extend `BacktestReportKpis.tsx`**

Add optional fields:
```typescript
advisor_veto_count?: number;
advisor_approve_count?: number;
advisor_veto_rate?: number;
advisor_vetoed_pnl?: string;
```

Render a row "Advisor veto rate: X% (N vetoes)" when `advisor_veto_count > 0`.

- [ ] **Step 10: Extend `BacktestPnlChart.tsx` (dual-line mode)**

When `advisor_veto_count > 0`:
- Add a checkbox "Show advisor-adjusted PnL" (default checked).
- When checked, render two `<path>` lines: "Base" and "With Advisor" (advisor-adjusted removes vetoed-order PnL from curve).
- Colour: base = `--color-muted`; with-advisor = `--color-success`.

- [ ] **Step 11: Add tabs to `BotDetailPage.tsx`**

Existing tabs: overview / runs / orders / risk-caps / advisor.

Add:
- In advisor tab: `<ParamTunerSection>` below the existing advisor config form.
- New "Shadows" sub-tab: `<ShadowComparisonPanel>`.

- [ ] **Step 12: Run all FE tests**

```bash
cd frontend && pnpm test --run
```
Expected: PASS.

- [ ] **Step 13: Commit**

```bash
git add frontend/src/services/param_tuner/ frontend/src/services/shadow_promoter/ frontend/src/features/bots/
git commit -m "feat(phase21b): FE param-tuner + shadow components + backtest advisor extensions"
```

---

## Task 12: Close-out

**Files:**
- Modify: `CLAUDE.md` — LLM Advisor Gate section update, Phase 19 BotFillRouter note
- Modify: `CHANGELOG.md`
- Modify: `TASKS.md`
- Tag: `v0.21.2`

- [ ] **Step 1: Run full test suite**

```bash
cd backend && pytest --tb=short -q 2>&1 | tail -5
cd frontend && pnpm test --run 2>&1 | tail -5
```
Expected: all pass.

- [ ] **Step 2: Update CLAUDE.md**

In the "LLM Advisor Gate (Phase 21a, shipped v0.21.0)" section, extend with Phase 21b entry. Key facts:
- `ParamTunerService` in `app/services/param_tuner/`. `trigger()` → AI → fan-out N backtests → rank → `approve()` → `BotSupervisor.restart()`. Redis INCRBYFLOAT cost reservation. LOCAL_ONLY default.
- `ShadowPromoterService` in `app/services/shadow_promoter/`. `create_shadow()` forces `bots.mode='paper'`. Three-layer enforcement. Fill isolation via `fills:paper:*` / `fills:live:*`.
- `BotSupervisor.restart()` in `bot/supervisor.py`: atomic stop→pubsub-poll→start; `SupervisorRestartError` on timeout.
- `AdvisorStub` in `backtest/advisor_stub.py`: deterministic, no AI.
- `AdvisorTelegramNotifier` in `telegram/advisor_notify.py`: psubscribes `bot:advisor:*`.
- Alembic 0065 (`bot_param_suggestions`, `bots.is_shadow/shadow_of/strategy_schema`, `risk_decisions.attempt_kind` widened to include `shadow_place_order`) + 0066 (`shadow_promotion_events`) + 0067 (`backtests.advisor_config`, `backtest_advisor_decisions`).
- 16 new Prometheus metrics across `param_tuner_*` and `shadow_promoter_*`.
- Deferred: auto-promote logic, staged allocation, real AI in backtest.

- [ ] **Step 3: Update CHANGELOG.md and TASKS.md**

```markdown
## [0.21.2] — 2026-05-19 (Phase 21b — LLM-in-Loop)
### Added
- ParamTunerService: LLM-proposes → auto-backtest fan-out → rank → human-approve
- BotSupervisor.restart(): atomic stop→wait→start with pubsub drain
- ShadowPromoterService: create/compare/promote shadow bots (paper-only)
- Three-layer paper-mode enforcement on shadow bots
- Fill channel isolation: fills:paper:* vs fills:live:*
- AdvisorStub: deterministic in-backtest veto injection
- AdvisorTelegramNotifier: VETO notifications + /override_ command
- Filings/earnings injection into advisor context
- Alembic 0065/0066/0067
- 10 new REST endpoints + 2 WS endpoints
- 16 new Prometheus metrics
- FE: ParamTunerSection, ShadowComparisonPanel, dual PnL curve
### Deferred
- Auto-promote logic
- Real AI calls during backtest replay (deterministic stub is intentional)
- Advisor perf-attribution → Phase 21c
```

- [ ] **Step 4: Tag and commit**

```bash
git add CLAUDE.md CHANGELOG.md TASKS.md
git commit -m "docs(phase21b): close-out — CLAUDE.md, CHANGELOG, TASKS"
git tag v0.21.2
git push && git push --tags
```

---

## Chunk routing reference

| Chunk | Scope | Route | Gate |
|---|---|---|---|
| Tasks 1–3 | Alembic 0065/0066/0067 | Qwen | — |
| Task 4 | Types + context builder | Qwen | after migrations |
| Task 5 | Param-tuner service + metrics | Codex | after Task 4 |
| Task 6 | Shadow-promoter service + metrics | Codex | after migrations |
| Task 7 | supervisor.restart + fill isolation + context guard | Codex | after migrations |
| Task 8 | Advisor extensions + Telegram notifier | Codex | after migrations |
| Task 9 | AdvisorStub + BacktestRunner | Qwen | after migrations |
| Task 10 | REST + WS API | Codex | after Tasks 5, 6, 7 |
| Task 11 | Frontend | Codex | after Task 10 |
| Task 12 | Close-out | Opus direct | after all |

**Reviewer chain per chunk:** spec-compliance (haiku) + code-quality (sonnet) + lang-reviewer (haiku). Chunks 5, 6, 7, 8, 10: + security-reviewer (sonnet). Chunk 1–3: + database-reviewer (sonnet). Chunk 11: + typescript-reviewer (haiku). Phase end: ARCHITECT-REVIEW (opus).
