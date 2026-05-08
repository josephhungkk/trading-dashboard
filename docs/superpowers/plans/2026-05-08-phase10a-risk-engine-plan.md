# Phase 10a — Risk Engine + Pre-Trade Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert a mandatory pre-trade risk gate as a fourth validation station in the order write path (after capability, before broker dispatch), with seven checks (account+broker kill switches, max-daily-loss, PDT with in-flight Redis counter, cross-broker concentration, BP buffer with in-flight commitments, sidecar margin preview), full audit trail, and a `/admin/risk` UI surface.

**Architecture:** New `RiskService` consumes `EvaluationContext` and returns a `GateVerdict` (ALLOW/WARN/BLOCK aggregate). Six fast checks run via `asyncio.gather`; the margin RPC runs in parallel with a 500ms soft-deadline on the preview path and full 3s synchronous fail-CLOSED on the place-order path. New tables `risk_limits` (hybrid scope), `account_kill_switches`, and `risk_decisions` (audit on place_order/modify only). New sidecar RPC `PreviewOrder` on IBKR (`ib_async.placeOrder(whatIf=True)`) and Schwab (`POST .../previewOrder`); Alpaca returns `UNIMPLEMENTED`. The atomic refactor of `place_order` / `modify_order` out of the 2010-LOC `orders_service.py` lands in the same chunk as the gate insert (Chunk D).

**Tech Stack:** Python 3.14 + FastAPI + SQLAlchemy 2.0 async + asyncpg + Alembic + structlog + Redis 7 (in-flight counters + pubsub) + protobuf/buf (gRPC sidecar) + React 19 + TanStack Query + Vitest + Playwright.

**Spec:** `docs/superpowers/specs/2026-05-08-phase10a-risk-engine-design.md` (canonical SQL/proto/RPC/code; this plan refers to spec sections for those artifacts).

**Tag target:** v0.12.0
**Predecessor head:** `c519675` (architect-review applied + migration ID corrected)
**Codex availability:** rate-limited until ~2026-05-12 — Claude main-thread implements per `feedback_codex_fallback.md`.
**Reviewer cadence:** per-chunk (≥5 commits per chunk), explicitly NOT per-commit (per `feedback_review_per_chunk.md`).

---

## File structure (created or modified)

### Backend Python (`backend/`)

| Path | Action | Responsibility |
|---|---|---|
| `backend/alembic/versions/0036_phase10a_risk_engine.py` | Create | Migration up + down for 5 tables + 4 triggers + 3 enums; spec §3 |
| `backend/app/models/risk.py` | Create | SQLAlchemy ORM for `RiskLimit`, `RiskLimitHistory`, `AccountKillSwitch`, `AccountKillSwitchHistory`, `RiskDecision` |
| `backend/app/schemas/risk.py` | Create | Pydantic v2 request/response models |
| `backend/app/services/risk_service.py` | Create | `RiskService` class + `EvaluationContext` + `GateVerdict` + 7 check methods |
| `backend/app/services/risk_limits_service.py` | Create | `RiskLimitsService` — CRUD + cache + Redis pubsub invalidation |
| `backend/app/services/account_kill_switch_service.py` | Create | `AccountKillSwitchService` — toggle + cache |
| `backend/app/services/risk_inflight_counters.py` | Create | Redis in-flight counters (`risk:pdt:{}`, `risk:bp_committed:{}`) — decrement/revert/reconcile |
| `backend/app/api/risk.py` | Create | `/api/risk/limits`, `/api/risk/decisions` (read endpoints) |
| `backend/app/api/admin_risk.py` | Create | `/api/admin/risk-limits` CRUD + `/api/admin/accounts/{id}/kill-switch` toggle |
| `backend/app/services/orders_service.py` | Modify (extract) | Currently 2010 LOC — extract `place_order` / `modify_order` to new modules; re-export to preserve callers |
| `backend/app/services/orders/place.py` | Create | Extracted `place_order` + risk-gate insertion |
| `backend/app/services/orders/modify.py` | Create | Extracted `modify_order` + risk-gate insertion |
| `backend/app/services/orders/preview.py` | Create | Extracted `preview_order` + risk-gate insertion |
| `backend/app/api/orders.py` | Modify | Update imports to new module paths; route handlers thin |
| `backend/app/main.py` | Modify | Register `risk` + `admin_risk` routers; startup-task for `risk_decisions` listener (Phase 11 forward-compat) |
| `backend/tests/services/test_risk_service.py` | Create | ≥30 unit tests for the 7 checks (parameterized) |
| `backend/tests/integration/test_risk_gate_orders.py` | Create | Full preview + place_order + modify round-trip; station ordering invariant |
| `backend/tests/integration/test_risk_decisions_audit.py` | Create | Audit row + pg_notify envelope shape |
| `backend/tests/integration/test_alembic_0036.py` | Create | Migration up/down; CHECK + UNIQUE + trigger semantics |
| `backend/tests/integration/test_risk_limits_admin.py` | Create | CRUD round-trip + CSRF nonce + cache invalidation |
| `backend/tests/integration/test_account_kill_switch_admin.py` | Create | Toggle round-trip + history + immediate honor |
| `backend/tests/chaos/test_risk_chaos.py` | Create | 6 chaos scenarios per spec §8 |

### Sidecars (`sidecar_ibkr/`, `sidecar_schwab/`, `sidecar_alpaca/`)

| Path | Action | Responsibility |
|---|---|---|
| `proto/broker.proto` | Modify | Add `PreviewOrderRequest`, `PreviewOrderResponse`, `rpc PreviewOrder`; spec §5 |
| `sidecar_ibkr/handlers.py` | Modify | Implement `PreviewOrder` via `ib_async.placeOrder(whatIf=True)` + `filledEvent.wait` |
| `sidecar_ibkr/tests/test_preview_order.py` | Create | IBKR WhatIf against fake `ib_async`; idempotency LRU |
| `sidecar_schwab/handlers.py` | Modify | Implement `PreviewOrder` via REST `previewOrder` + token bucket |
| `sidecar_schwab/client.py` | Modify | Add `preview_order` method + 401-retry-once + separate rate-limit bucket |
| `sidecar_schwab/tests/test_preview_order.py` | Create | Schwab REST against fake-server (existing infra from Phase 8a) |
| `sidecar_alpaca/handlers.py` | Modify | `PreviewOrder` returns `UNIMPLEMENTED` with documented reason |
| `sidecar_alpaca/tests/test_unimplemented_stubs.py` | Modify | Add `PreviewOrder` to UNIMPLEMENTED list |

### Frontend (`frontend/`)

| Path | Action | Responsibility |
|---|---|---|
| `frontend/src/services/risk/types.ts` | Create | TS types for RiskLimit, RiskDecision, AccountKillSwitch, GateVerdict |
| `frontend/src/services/risk/api.ts` | Create | `listRiskLimits`, `createRiskLimit`, `updateRiskLimit`, `deleteRiskLimit`, `getKillSwitch`, `setKillSwitch`, `listRiskDecisions` |
| `frontend/src/hooks/useRiskLimits.ts` | Create | TanStack Query hook |
| `frontend/src/hooks/useAccountKillSwitch.ts` | Create | TanStack Query hook |
| `frontend/src/services/capabilities/types.ts` | Modify | Reconcile FE/BE shape mismatch (spec §1, ROADMAP deferred) |
| `frontend/src/services/capabilities/api.ts` | Modify | Update parse to match BE flat-list/asset-class-dict shape |
| `frontend/src/hooks/useBrokerCapabilities.ts` | Modify | Adapt to reconciled types |
| `frontend/src/features/trade/TradeTicketModal.tsx` | Modify | Yellow WARN banner; red BLOCK panel; click-to-acknowledge WARN before submit |
| `frontend/src/features/admin/risk/RiskLimitsPage.tsx` | Create | `/admin/risk` CRUD page |
| `frontend/src/features/admin/risk/RiskDecisionsPage.tsx` | Create | `/admin/risk/decisions` read-only feed |
| `frontend/src/features/admin/accounts/AccountKillSwitchRow.tsx` | Create | Switch + reason dialog row component |
| `frontend/src/routes/admin/risk.tsx` | Create | TanStack Router route file |
| `frontend/src/routes/admin/risk.decisions.tsx` | Create | TanStack Router route file |
| `frontend/src/api-generated.ts` | Regenerate | After BE changes via `scripts/gen-types.sh` |
| `frontend/tests/e2e/phase10-risk-gate.spec.ts` | Create | 4 flows |
| `frontend/tests/e2e/phase10-admin-risk.spec.ts` | Create | Admin CRUD + kill switch |
| `frontend/src/features/admin/risk/RiskLimitsPage.test.tsx` | Create | RTL unit |
| `frontend/src/features/admin/risk/RiskLimitsPage.stories.tsx` | Create | Storybook |

### Docs

| Path | Action | Responsibility |
|---|---|---|
| `docs/PHASE-WORKFLOW.md` | Modify (1 line) | Fix line 42 stale "every commit boundary" → "every chunk boundary" |
| `CLAUDE.md` | Modify | Add Phase 10a invariants section |
| `CHANGELOG.md` | Modify | `## [0.12.0] — YYYY-MM-DD` section |
| `TASKS.md` | Modify | Tick Phase 10a checkboxes; record deferred items |

---

## Chunk A — Schema (Alembic 0036 + ORM models)

Estimated: 4 commits. Reviewer chain at chunk boundary: spec-compliance (haiku) + database-reviewer (sonnet) + python-reviewer (haiku).

### Task A1: Alembic migration 0036 — up + down

**Files:**
- Create: `backend/alembic/versions/0036_phase10a_risk_engine.py`
- Test: `backend/tests/integration/test_alembic_0036.py`

- [ ] **Step 1: Write the migration test first (TDD)**

Create `backend/tests/integration/test_alembic_0036.py` with assertions covering: 5 tables exist, 3 enums exist, partial UNIQUE indexes block duplicate `(global, NULL, kind)` rows (C1 fix), `risk_limits_history` UPDATE trigger fires, `fn_risk_decisions_notify` references `pg_notify`, downgrade roundtrip succeeds. (Detailed test bodies use `sqlalchemy.text` with `pg_tables` + `pg_type` introspection; pattern matches `backend/tests/integration/test_alembic_0011a.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend pytest backend/tests/integration/test_alembic_0036.py -v`
Expected: all tests fail — migration file does not exist yet.

- [ ] **Step 3: Write the migration file**

Create `backend/alembic/versions/0036_phase10a_risk_engine.py`. Copy SQL DDL **verbatim from spec §3** — 3 `CREATE TYPE`, 5 `CREATE TABLE`, 2 partial UNIQUE indexes (`uq_risk_limits_global_kind`, `uq_risk_limits_scoped`), 1 lookup index, 4 trigger function/trigger pairs (`fn_risk_limits_history`, `fn_account_kill_switches_history`, `fn_risk_decisions_notify` + matching triggers), and downgrade in reverse order.

Header:
```python
revision = "0036_phase10a_risk_engine"
down_revision = "0035_phase9_5_nlv_at_index"
branch_labels = None
depends_on = None
```

`upgrade()` applies SQL via `op.execute(...)` — one block per spec §3 SQL fragment. `downgrade()` drops in reverse: triggers → trigger functions → tables → enums.

- [ ] **Step 4: Run migration test to verify it passes**

Run: `docker compose exec backend pytest backend/tests/integration/test_alembic_0036.py -v`
Expected: all tests pass.

- [ ] **Step 5: Apply migration locally + sanity check**

Run: `docker compose exec backend alembic upgrade head && docker compose exec backend alembic current`
Expected: `0036_phase10a_risk_engine (head)`.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0036_phase10a_risk_engine.py \
        backend/tests/integration/test_alembic_0036.py
git commit -m "feat(phase10a): alembic 0036 risk engine schema (5 tables, 3 enums, 4 triggers)"
```

---

### Task A2: SQLAlchemy ORM models

**Files:**
- Create: `backend/app/models/risk.py`
- Test: `backend/tests/services/test_risk_models.py`

- [ ] **Step 1: Write the model test (column-set assertions)**

Create `backend/tests/services/test_risk_models.py`:

```python
"""Risk ORM model schema asserts."""
from app.models.risk import (
    AccountKillSwitch, AccountKillSwitchHistory, RiskDecision,
    RiskLimit, RiskLimitHistory,
)


def test_risk_limit_columns() -> None:
    cols = {c.name for c in RiskLimit.__table__.columns}
    assert cols == {
        "id", "scope_type", "scope_id", "limit_kind", "limit_value",
        "warn_at_pct", "is_active", "notes", "created_at", "updated_at",
        "updated_by",
    }


def test_risk_decisions_columns() -> None:
    cols = {c.name for c in RiskDecision.__table__.columns}
    assert cols == {
        "id", "account_id", "instrument_id", "side", "qty", "price",
        "order_type", "time_in_force", "verdict", "blockers", "warnings",
        "evaluated_at", "latency_ms", "attempt_kind", "request_id", "order_id",
    }


def test_account_kill_switch_pk_is_account_id() -> None:
    assert [c.name for c in AccountKillSwitch.__table__.primary_key] == ["account_id"]


def test_history_tables_carry_change_metadata() -> None:
    for cls in (RiskLimitHistory, AccountKillSwitchHistory):
        cols = {c.name for c in cls.__table__.columns}
        assert "changed_at" in cols and "changed_by" in cols
```

- [ ] **Step 2: Run, see ImportError**

Run: `docker compose exec backend pytest backend/tests/services/test_risk_models.py -v`
Expected: ImportError.

- [ ] **Step 3: Write `backend/app/models/risk.py`**

Use SQLAlchemy 2.0 `Mapped[...]` + `mapped_column(...)` syntax. Five `Base` subclasses: `RiskLimit`, `RiskLimitHistory`, `AccountKillSwitch`, `AccountKillSwitchHistory`, `RiskDecision`. Use the `ENUM(name=..., create_type=False)` pattern (Alembic created the type). Money fields: `Numeric(20, 8)`. UUIDs: `UUID(as_uuid=True)`. JSONB for `blockers` / `warnings`. Pattern matches `backend/app/models/orders.py` (Phase 5b shipped) — copy that file's structure.

- [ ] **Step 4: Run model tests**

Run: `docker compose exec backend pytest backend/tests/services/test_risk_models.py -v`
Expected: 4 pass.

- [ ] **Step 5: Run mypy + ruff**

Run: `docker compose exec backend ruff check app/models/risk.py && docker compose exec backend mypy --strict app/models/risk.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/risk.py backend/tests/services/test_risk_models.py
git commit -m "feat(phase10a): SQLAlchemy ORM for risk_limits + kill_switches + risk_decisions"
```

---

### Task A3: Pydantic v2 schemas

**Files:**
- Create: `backend/app/schemas/risk.py`
- Test: `backend/tests/services/test_risk_schemas.py`

- [ ] **Step 1: Write schema test (validation rules)**

Create `backend/tests/services/test_risk_schemas.py` with 5 cases: global-scope-id-must-be-null, account-scope-id-required, warn_at_pct-bounds, kill-switch-reason-required-when-enabling, GateVerdict aggregation shape.

- [ ] **Step 2: Run, see ImportError**

Run: `docker compose exec backend pytest backend/tests/services/test_risk_schemas.py -v`
Expected: ImportError.

- [ ] **Step 3: Write schemas**

Create `backend/app/schemas/risk.py`. Pydantic v2 `BaseModel` subclasses with `model_validator(mode="after")` for cross-field invariants. Models: `RiskLimitCreate`, `RiskLimitUpdate`, `RiskLimitOut`, `AccountKillSwitchToggleRequest`, `AccountKillSwitchOut`, `GateBlockerEntry`, `GateWarningEntry`, `GateVerdict`, `RiskDecisionOut`. `Literal` types for `ScopeType`, `LimitKind`, `Verdict`. `Annotated[Decimal, Field(max_digits=20, decimal_places=8)]` for money.

The `_ScopeRule` mixin enforces:
- `scope_type='global'` → `scope_id` MUST be NULL
- `scope_type in ('broker','account')` → `scope_id` MUST NOT be NULL

`AccountKillSwitchToggleRequest` enforces `reason.strip()` non-empty when `is_enabled=True`.

- [ ] **Step 4: Run, pass**

Run: `docker compose exec backend pytest backend/tests/services/test_risk_schemas.py -v`
Expected: 5 pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/risk.py backend/tests/services/test_risk_schemas.py
git commit -m "feat(phase10a): pydantic v2 schemas for risk engine surfaces"
```

---

### Task A4: Reviewer chain — Chunk A boundary

- [ ] **Step 1: Determine chunk diff range**

Run: `git log --oneline c519675..HEAD`
Expected: 3 commits — A1 alembic + A2 ORM + A3 schemas.

- [ ] **Step 2: Dispatch parallel reviewer chain**

Three parallel `Agent` calls in **one message** (per `feedback_proactive_tooling.md`):

1. **spec-compliance** (`subagent_type=everything-claude-code:python-reviewer`, `model=haiku`, prompt with diff + spec §3 inlined verbatim per `feedback_reviewer_spec_inline.md`).
2. **database-reviewer** (`subagent_type=everything-claude-code:database-reviewer`, `model=sonnet`, focus on Alembic 0036 — partial UNIQUE indexes, trigger correctness, NUMERIC precision, ENUM membership, downgrade reversibility).
3. **python-reviewer** (`subagent_type=everything-claude-code:python-reviewer`, `model=haiku`, ORM column types, Pydantic v2 model_validator correctness, type hints).

- [ ] **Step 3: Apply CRIT+HIGH+MED findings inline**

Per `feedback_architect_findings_apply_through_medium.md`. LOWs documented; defer if needed.

- [ ] **Step 4: Commit reviewer fixes (if any)**

```bash
git commit -m "fix(phase10a): chunk-A reviewer findings"
```

---

## Chunk B — RiskService + 7 checks

Estimated: 8 commits. Reviewer chain: spec-compliance + python-reviewer + code-quality-reviewer (sonnet) + silent-failure-hunter (sonnet).

### Task B1: `EvaluationContext` + service skeleton + cap-resolver

**Files:**
- Create: `backend/app/services/risk_service.py`
- Create: `backend/tests/services/test_risk_service.py`

- [ ] **Step 1: Write skeleton + cap-resolver tests**

Create `backend/tests/services/test_risk_service.py`. Three tests:
- `test_resolve_limit_walks_account_then_broker_then_global` — mocks `db.execute` to return account-scope row first; asserts `_resolve_limit` returns it.
- `test_resolve_limit_returns_none_when_no_match` — all three scope queries return None; asserts None.
- `test_evaluate_with_no_limits_returns_allow` — ALL config returns no limits / kill-switches off; asserts `final_verdict == "allow"`.

Use `unittest.mock.AsyncMock` for `AsyncSession`, redis, config_service, sidecar_client. Provide an `evaluation_ctx` fixture returning a fully-populated `EvaluationContext`.

- [ ] **Step 2: Run, fail (ImportError)**

Run: `docker compose exec backend pytest backend/tests/services/test_risk_service.py -v`
Expected: ImportError.

- [ ] **Step 3: Write skeleton**

Create `backend/app/services/risk_service.py`:

```python
"""Phase 10a — Risk gate evaluator."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.risk import RiskLimit
from app.schemas.risk import (
    GateBlockerEntry,
    GateVerdict,
    GateWarningEntry,
    Verdict,
)

log = structlog.get_logger(__name__)
EvalMode = Literal["preview", "place_order", "modify_order"]


@dataclass(frozen=True)
class EvaluationContext:
    account_id: uuid.UUID
    broker_id: str
    instrument_id: int | None
    side: Literal["buy", "sell"]
    qty: Decimal
    price: Decimal | None
    order_type: str
    time_in_force: str
    request_id: str
    currency_base: str


class RiskService:
    def __init__(self, db: AsyncSession, redis, config, sidecar) -> None:
        self._db = db
        self._redis = redis
        self._config = config
        self._sidecar = sidecar

    async def _resolve_limit(
        self, account_id: uuid.UUID, broker_id: str, kind: str
    ) -> RiskLimit | None:
        for scope_type, scope_id in (
            ("account", str(account_id)),
            ("broker", broker_id),
            ("global", None),
        ):
            stmt = (
                select(RiskLimit)
                .where(
                    RiskLimit.scope_type == scope_type,
                    RiskLimit.limit_kind == kind,
                    RiskLimit.is_active.is_(True),
                )
            )
            stmt = (
                stmt.where(RiskLimit.scope_id.is_(None))
                if scope_id is None
                else stmt.where(RiskLimit.scope_id == scope_id)
            )
            row = (await self._db.execute(stmt)).scalar_one_or_none()
            if row is not None:
                return row
        return None

    async def evaluate(self, ctx: EvaluationContext, mode: EvalMode) -> GateVerdict:
        # Placeholder until B2-B7 land; aggregator wired in B8.
        t0 = time.perf_counter()
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return GateVerdict(
            final_verdict="allow", blockers=[], warnings=[], latency_ms=latency_ms
        )
```

- [ ] **Step 4: Run, pass**

Run: `docker compose exec backend pytest backend/tests/services/test_risk_service.py -v`
Expected: 3 pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/risk_service.py backend/tests/services/test_risk_service.py
git commit -m "feat(phase10a): RiskService skeleton + cap-resolver lookup walk"
```

---

### Task B2: Account-level + broker-level kill-switch checks

- [ ] **Step 1: Append 3 tests** for `_check_account_kill_switch` (off/on) and `_check_broker_kill_switch` (composes Phase 5b `app_config.broker.kill_switch_enabled`). Each returns `(GateBlockerEntry|None, GateWarningEntry|None)`-tuple-or-`None`. Mock `db.execute` for the kill-switch row, `config.get_bool` for the broker-level flag.

- [ ] **Step 2: Run, fail.** `pytest -k 'kill_switch'` → 3 fail.

- [ ] **Step 3: Implement**: add `_check_account_kill_switch` (reads `AccountKillSwitch` ORM by `account_id`; BLOCK if `is_enabled=True` with reason in message, code `account_kill_switch_enabled`) and `_check_broker_kill_switch` (`config.get_bool("broker", "kill_switch_enabled", default=False)`; BLOCK with code `broker_kill_switch_enabled`).

- [ ] **Step 4: Run, pass.** 3 pass.

- [ ] **Step 5: Commit.**
```bash
git commit -am "feat(phase10a): account + broker kill switch checks (composes Phase 5b H0)"
```

---

### Task B3: `_check_max_daily_loss` (realized + unrealized; tz-pinned day boundary [M2])

- [ ] **Step 1: Append 4 parameterized cases** (under cap → ALLOW; @ warn_at_pct → WARN; over cap → BLOCK; realized + unrealized composed → BLOCK). Mock `db.execute` for cap resolver + view query (`v_account_intraday_pnl` returning `(realized, unrealized)`).

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Add the helper view + `account_day_boundary_utc` utility**:

a) Append to `backend/alembic/versions/0036_phase10a_risk_engine.py` `upgrade()` (BEFORE `risk_decisions`):

```sql
CREATE OR REPLACE VIEW v_account_intraday_pnl AS
SELECT
  ba.id AS account_id,
  date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS day_start_utc,
  COALESCE(SUM(f.realized_pnl_base) FILTER (WHERE f.filled_at >= date_trunc('day', now())), 0) AS realized,
  COALESCE(SUM(p.unrealized_pnl_base), 0) AS unrealized
FROM broker_accounts ba
LEFT JOIN fills f ON f.account_id = ba.id
LEFT JOIN positions p ON p.account_id = ba.id
GROUP BY ba.id;
```

(If `fills.realized_pnl_base` / `positions.unrealized_pnl_base` columns don't exist, fall back to `realized_pnl` / `unrealized_pnl` for USD accounts; flag tz/multi-currency tightening as deferred to 10a.5.)

Append matching `DROP VIEW IF EXISTS v_account_intraday_pnl` to `downgrade()`.

b) Append to `backend/app/services/market_calendar.py`:

```python
async def account_day_boundary_utc(db, account_id: uuid.UUID) -> datetime:
    """Phase 10a M2: return UTC datetime for 00:00 in the broker's primary-market tz.
    Stub returns UTC midnight; refine in 10a.5 if tz mismatch surfaces."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
```

c) Implement `_check_max_daily_loss` in `RiskService` per spec §1 #3 — reads cap via `_resolve_limit`, queries `v_account_intraday_pnl`, computes `loss_today = -(realized + unrealized)`, returns BLOCK if exceeded, WARN at `warn_at_pct`.

- [ ] **Step 4: Run migration test + max-loss tests.**

```bash
docker compose exec backend pytest backend/tests/integration/test_alembic_0036.py backend/tests/services/test_risk_service.py -v -k 'max_daily_loss or 0036'
```

Expected: 4 new + previous all green.

- [ ] **Step 5: Commit.**
```bash
git commit -am "feat(phase10a): max_daily_loss check (realized + unrealized; UTC day boundary stub)"
```

---

### Task B4: `_check_pdt` with Redis in-flight counter [H1]

**Files:**
- Create: `backend/app/services/risk_inflight_counters.py`
- Modify: `backend/app/services/risk_service.py`
- Create: `backend/tests/services/test_risk_inflight_counters.py`

- [ ] **Step 1: Write counter unit tests** (5 tests for `decrement_pdt`, `revert_pdt`, `inflight_pdt_remaining` with set/unset, `reconcile_pdt`). Plus 5 parameterized PDT-check tests in `test_risk_service.py` — broker_reported × inflight × warn_remaining → expected verdict.

- [ ] **Step 2: Run, fail (ImportError + missing method).**

- [ ] **Step 3: Implement counters module:**

```python
"""Redis-backed in-flight counters for risk gate optimism (Phase 10a H1, H3)."""
from __future__ import annotations
import uuid


def _pdt_key(aid: uuid.UUID) -> str: return f"risk:pdt:{aid}"
def _bp_key(aid: uuid.UUID) -> str: return f"risk:bp_committed:{aid}"


async def decrement_pdt(redis, aid: uuid.UUID) -> int:
    return int(await redis.decr(_pdt_key(aid)))

async def revert_pdt(redis, aid: uuid.UUID) -> int:
    return int(await redis.incr(_pdt_key(aid)))

async def inflight_pdt_remaining(redis, aid: uuid.UUID) -> int | None:
    raw = await redis.get(_pdt_key(aid))
    return int(raw) if raw is not None else None

async def reconcile_pdt(redis, aid: uuid.UUID, broker_reported: int) -> None:
    await redis.set(_pdt_key(aid), str(broker_reported), ex=120)


async def commit_bp(redis, aid: uuid.UUID, notional: float) -> float:
    return float(await redis.incrbyfloat(_bp_key(aid), notional))

async def revert_bp(redis, aid: uuid.UUID, notional: float) -> float:
    return float(await redis.incrbyfloat(_bp_key(aid), -notional))

async def inflight_bp_committed(redis, aid: uuid.UUID) -> float:
    raw = await redis.get(_bp_key(aid))
    return float(raw) if raw is not None else 0.0

async def reconcile_bp_committed(redis, aid: uuid.UUID, broker_reported: float) -> None:
    await redis.set(_bp_key(aid), str(broker_reported), ex=120)
```

- [ ] **Step 4: Implement `_check_pdt`** in `RiskService`. Read cap (`pdt_warn_remaining`); read in-flight counter; if unset, fall back to broker-reported `dayTradesRemaining` from `sidecar.get_account_summary(...)`. BLOCK if `current <= 0`; WARN if `current <= warn_remaining`; else ALLOW.

- [ ] **Step 5: Run, pass.** All PDT + counter tests green.

- [ ] **Step 6: Commit.**
```bash
git commit -am "feat(phase10a): pdt check + redis in-flight counter (closes staleness window H1)"
```

---

### Task B5: `_check_position_concentration` cross-broker by `instrument_id` [H2]

- [ ] **Step 1: Append 3 parameterized + 1 missing-snapshot + 1 cross-broker test.** Cross-broker test is documented as covered by integration-test (real DB, multi-account fixture); unit test asserts the SQL aggregates without `account_id` filter.

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement `_check_position_concentration`** in `RiskService`. SQL: `SELECT COALESCE(SUM(market_value_base), 0) FROM positions WHERE instrument_id = :iid` (no account filter — cross-broker H2). Compute post-trade exposure direction-aware: `delta = qty * price * (+1 if buy else -1)`; `post_pct = abs(current + delta) / nlv * 100`. Compare to cap; BLOCK if over; WARN at `warn_at_pct`. Skip check if `instrument_id is None`.

- [ ] **Step 4: Run, pass.** 4 unit + 1 documented for integration coverage.

- [ ] **Step 5: Commit.**
```bash
git commit -am "feat(phase10a): position concentration check with cross-broker aggregation (H2)"
```

---

### Task B6: `_check_buying_power` with in-flight commitments [H3]

- [ ] **Step 1: Append 4 cases** — within / below buffer WARN / insufficient BLOCK / in-flight gobbles BP BLOCK.

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement `_check_buying_power`** in `RiskService`. Read `bp_base` from `broker_accounts`; read `inflight_bp_committed`; `effective_bp = bp_base - committed`. BLOCK if `order_notional > effective_bp` (code `buying_power_insufficient`). WARN if `remaining_after_order < buffer_required` (where `buffer_required = effective_bp * cap_pct / 100`).

- [ ] **Step 4: Run, pass.** 4 pass.

- [ ] **Step 5: Commit.**
```bash
git commit -am "feat(phase10a): buying power buffer check with in-flight commitment subtract (H3)"
```

---

### Task B7: `_check_margin` with asymmetric preview/place_order policy [C3, H4]

- [ ] **Step 1: Append 5 cases** — preview accepted (ALLOW) / preview slow (WARN pending) / place_order timeout (BLOCK margin_check_unavailable) / Alpaca UNIMPLEMENTED (WARN unavailable) / reject_reason (BLOCK margin_rejected_by_broker).

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement `_check_margin(ctx, mode)`** in `RiskService`. `timeout = 0.5 if mode == "preview" else 3.0`. Wrap `self._sidecar.preview_order(...)` in `asyncio.wait_for`. On `asyncio.TimeoutError`: WARN if preview, BLOCK with `margin_check_unavailable` if place_order/modify. On `grpc.aio.AioRpcError` UNIMPLEMENTED: always WARN. On `accepted=False`: BLOCK with `margin_rejected_by_broker`.

- [ ] **Step 4: Run, pass.** 5 pass.

- [ ] **Step 5: Commit.**
```bash
git commit -am "feat(phase10a): margin check with asymmetric preview/place_order fail policy (C3, H4)"
```

---

### Task B8: `evaluate()` aggregator

- [ ] **Step 1: Replace placeholder `evaluate()`** in `RiskService`:

```python
async def evaluate(self, ctx: EvaluationContext, mode: EvalMode) -> GateVerdict:
    t0 = time.perf_counter()
    fast_results = await asyncio.gather(
        self._check_account_kill_switch(ctx),
        self._check_broker_kill_switch(ctx),
        self._check_max_daily_loss(ctx),
        self._check_pdt(ctx),
        self._check_position_concentration(ctx),
        self._check_buying_power(ctx),
        return_exceptions=True,
    )
    margin_result = await self._check_margin(ctx, mode)
    blockers, warnings = [], []
    for r in [*fast_results, margin_result]:
        if isinstance(r, BaseException):
            log.exception("risk.check_raised", exc_info=r)
            blockers.append(GateBlockerEntry(
                check="evaluator", message=f"check raised: {type(r).__name__}",
                code="evaluator_error",
            ))
            continue
        if r is None: continue
        b, w = r
        if b is not None: blockers.append(b)
        if w is not None: warnings.append(w)
    verdict: Verdict = "block" if blockers else ("warn" if warnings else "allow")
    return GateVerdict(
        final_verdict=verdict, blockers=blockers, warnings=warnings,
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
```

- [ ] **Step 2: Append aggregator test** asserting precedence (any block → BLOCK; any warn no-block → WARN; else ALLOW; unhandled exception → BLOCK).

- [ ] **Step 3: Run all `test_risk_service.py`.** ~30 pass.

- [ ] **Step 4: Commit.**
```bash
git commit -am "feat(phase10a): RiskService.evaluate aggregator (allow/warn/block precedence)"
```

---

### Task B9: Reviewer chain — Chunk B boundary

- [ ] **Step 1**: `git log --oneline c519675..HEAD` — confirm ~7-8 chunk-B commits.
- [ ] **Step 2**: Dispatch 4 parallel agents — spec-compliance (haiku), python-reviewer (haiku), code-quality-reviewer (sonnet, focused on `asyncio.gather(return_exceptions=True)` correctness + check method DRY), silent-failure-hunter (sonnet, focused on exception swallowing + missing `await` + counter revert atomicity).
- [ ] **Step 3**: Apply CRIT+HIGH+MED inline; commit `fix(phase10a): chunk-B reviewer findings`.

---

## Chunk C — Sidecar `PreviewOrder` RPCs (IBKR + Schwab; Alpaca stub)

Estimated: 6 commits. Reviewer chain: spec-compliance + python-reviewer + code-quality-reviewer + security-reviewer (sonnet).

### Task C1: Proto definition + buf regen

- [ ] **Step 1: Edit `proto/broker.proto`.** Add `PreviewOrderRequest` + `PreviewOrderResponse` messages and `rpc PreviewOrder(PreviewOrderRequest) returns (PreviewOrderResponse);` — copy verbatim from spec §5. Money fields are `string` (Decimal-stringified) per C2.

- [ ] **Step 2: Regenerate stubs.**

```bash
cd proto && buf generate
```

Expected: `proto/gen/python/broker_pb2.py`, `broker_pb2_grpc.py`, and TS bindings updated.

- [ ] **Step 3: Verify Python imports compile.**

```bash
docker compose exec backend python -c "from proto.gen.python import broker_pb2; print(broker_pb2.PreviewOrderRequest.DESCRIPTOR.fields_by_name.keys())"
```

Expected: prints field names including `qty`, `limit_price`, `idempotency_key`.

- [ ] **Step 4: Commit.**
```bash
git add proto/broker.proto proto/gen/
git commit -m "feat(phase10a): proto add PreviewOrder RPC (Decimal-string money fields C2)"
```

---

### Task C2: `sidecar_ibkr` PreviewOrder handler [M7]

**Files:**
- Modify: `sidecar_ibkr/handlers.py`
- Create: `sidecar_ibkr/tests/test_preview_order.py`

- [ ] **Step 1: Write handler tests.**

Three tests:
- `test_preview_order_returns_decimals_as_strings` — mocks `ib_async.IB`; `placeOrder(whatIf=True)` returns a `Trade` with `filledEvent.wait` resolving immediately and `orderStatus` carrying margin/commission strings; assert response fields.
- `test_preview_order_idempotency_lru_dedups` — same `idempotency_key` twice → `placeOrder` called once.
- `test_preview_order_filled_event_timeout_returns_deadline_exceeded` — `filledEvent.wait` raises `asyncio.TimeoutError` → `context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, ...)`.

- [ ] **Step 2: Run, fail.**

```bash
cd sidecar_ibkr && uv run pytest tests/test_preview_order.py -v
```

- [ ] **Step 3: Implement handler.**

In `sidecar_ibkr/handlers.py`, add `PreviewOrder` to the `IBKRBrokerHandler` class. Builder method `_build_what_if_order(request)` constructs `LimitOrder` or `MarketOrder` with `whatIf=True`. Resolve contract via existing `_resolve_contract`. Call `ib.placeOrder(contract, order)`; await `asyncio.wait_for(trade.filledEvent.wait(), timeout=2.5)`. On timeout: `await context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "WhatIf timeout")`. On success: build `PreviewOrderResponse` with `accepted=True` + decimal fields stringified from `trade.orderStatus`.

LRU cache: 60s TTL, 1000-entry cap, dict-based with `time.time()` timestamps. Methods `_preview_lru_get(key)` / `_preview_lru_put(key, resp)`.

- [ ] **Step 4: Run, pass.** 3 pass.

- [ ] **Step 5: Commit.**
```bash
git commit -am "feat(phase10a): sidecar_ibkr PreviewOrder handler with WhatIf+filledEvent (M7)"
```

---

### Task C3: `sidecar_schwab` PreviewOrder handler + token bucket [M8]

**Files:**
- Modify: `sidecar_schwab/handlers.py`
- Modify: `sidecar_schwab/client.py`
- Create: `sidecar_schwab/tests/test_preview_order.py`

- [ ] **Step 1: Write tests.** Two: `test_preview_order_calls_rest_endpoint` (mock `client.preview_order` returning Schwab JSON shape; assert PreviewOrderResponse decimals); `test_preview_order_blocks_on_rate_limit` (mock `client.preview_token_bucket` returning False; assert `RESOURCE_EXHAUSTED`).

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement client + handler.**

In `sidecar_schwab/client.py`, add separate token bucket (60 req/min) and `preview_order(account_hash, payload) -> dict` method using existing OAuth pool + 401-retry-once.

In `sidecar_schwab/handlers.py`, add `PreviewOrder` method: check `await client.preview_token_bucket()` → `RESOURCE_EXHAUSTED` if false (and increment `schwab_preview_rate_limited_total` metric); build payload via `_build_preview_payload(request)` (Schwab orderStrategyType=SINGLE shape per Phase 8a precedent); call `client.preview_order` and translate response.

- [ ] **Step 4: Run, pass.** 2 pass.

- [ ] **Step 5: Commit.**
```bash
git commit -am "feat(phase10a): sidecar_schwab PreviewOrder + 60req/min token bucket (M8)"
```

---

### Task C4: `sidecar_alpaca` UNIMPLEMENTED stub

- [ ] **Step 1: Add stub** to `sidecar_alpaca/handlers.py`:

```python
async def PreviewOrder(self, request, context):
    await context.abort(
        grpc.StatusCode.UNIMPLEMENTED,
        "alpaca-py does not provide pre-trade margin preview; "
        "gate falls back to cached BP per Phase 10a",
    )
```

- [ ] **Step 2: Append test** to `sidecar_alpaca/tests/test_unimplemented_stubs.py` asserting `UNIMPLEMENTED`.

- [ ] **Step 3: Run, pass.**
```bash
cd sidecar_alpaca && uv run pytest tests/test_unimplemented_stubs.py::test_preview_order_returns_unimplemented -v
```

- [ ] **Step 4: Commit.**
```bash
git commit -am "feat(phase10a): sidecar_alpaca PreviewOrder UNIMPLEMENTED stub"
```

---

### Task C5: Backend `BrokerClient.preview_order()` wrapper [M6]

**Files:**
- Modify: `backend/app/services/broker_dial.py` (or where `BrokerClient` lives — verify with `grep -rn "class BrokerClient" backend/app/`)
- Test: `backend/tests/services/test_broker_client_preview.py`

- [ ] **Step 1: Write test** asserting:
- Decimal fields serialize to string in proto request.
- `idempotency_key` is `blake2b` content-hash of canonical payload (not random/UUID per M6).

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement wrapper.**

```python
async def preview_order(
    self, *, account_id: str, side: str, symbol: str,
    asset_class: str, order_type: str, time_in_force: str,
    qty: str, limit_price: str | None = None, stop_price: str | None = None,
):
    import hashlib, json
    from proto.gen.python import broker_pb2
    canonical = json.dumps({
        "account_hash": account_id, "side": side, "symbol": symbol,
        "asset_class": asset_class, "order_type": order_type,
        "time_in_force": time_in_force, "qty": qty,
        "limit_price": limit_price, "stop_price": stop_price,
    }, sort_keys=True, separators=(",", ":"))
    idem = "preview:" + hashlib.blake2b(canonical.encode(), digest_size=16).hexdigest()
    req = broker_pb2.PreviewOrderRequest(
        account_hash=account_id, side=side, symbol=symbol,
        asset_class=asset_class, order_type=order_type,
        time_in_force=time_in_force, qty=qty,
        limit_price=limit_price, stop_price=stop_price,
        idempotency_key=idem,
    )
    return await self._stub.PreviewOrder(req)
```

- [ ] **Step 4: Run, pass.**

- [ ] **Step 5: Commit.**
```bash
git commit -am "feat(phase10a): BrokerClient.preview_order with content-hash idempotency (M6)"
```

---

### Task C6: Reviewer chain — Chunk C boundary

- [ ] Determine diff range; dispatch 4 parallel reviewers (spec/python/code-quality/security; security focuses on idempotency key collision resistance + token-bucket starvation + raw_provider_payload not leaking secrets); apply findings inline; commit fixes.

---

## Chunk D — `orders_service.py` extract + gate insert + capability shape fix [highest blast-radius]

Estimated: 9 commits. Reviewer chain: **full 5-reviewer chain** at boundary.

### Task D1: Pre-flight integration test (gate-ordering invariant)

This test goes RED FIRST and stays RED until the rest of Chunk D lands.

**Files:**
- Create: `backend/tests/integration/test_risk_gate_orders.py`

- [ ] **Step 1: Write integration test** with 6 cases:
1. `test_station_ordering_kill_switch_first` — broker kill switch on → 503 before risk check fires.
2. `test_station_ordering_capability_before_risk` — unsupported `order_type` → 422 capability_not_supported, no risk evaluation.
3. `test_risk_block_returns_422_with_blockers_payload` — account kill switch on → 422 risk_gate_blocked + blockers list.
4. `test_risk_warn_passes_through_to_dispatch` — loose caps → 200 OK with warnings array populated.
5. `test_concentration_cross_broker` — H2: AAPL on IBKR + Schwab aggregates as one exposure → BLOCK on third order.
6. `test_pdt_inflight_counter_decrements` — H1: place_order decrements `risk:pdt:{account_id}`.

Use existing fixtures: `api_client` (httpx AsyncClient), `seeded_account`, `csrf_nonce`. Add new fixtures `enable_broker_kill_switch`, `enable_account_kill_switch`, `configure_loose_caps`, `seeded_two_brokers_same_instrument`, `seeded_us_margin_account`, `redis_client`.

- [ ] **Step 2: Run — all fail.**

```bash
docker compose exec backend pytest backend/tests/integration/test_risk_gate_orders.py -v
```

Expected: 6/6 fail (gate not inserted yet).

- [ ] **Step 3: Commit (TDD red).**
```bash
git add backend/tests/integration/test_risk_gate_orders.py
git commit -m "test(phase10a): pre-flight gate-ordering invariant integration test (RED)"
```

---

### Task D2: Atomic refactor — extract place_order/modify_order/preview_order

**Files:**
- Modify: `backend/app/services/orders_service.py`
- Create: `backend/app/services/orders/__init__.py`
- Create: `backend/app/services/orders/place.py`
- Create: `backend/app/services/orders/modify.py`
- Create: `backend/app/services/orders/preview.py`
- Modify: `backend/app/api/orders.py` (only if imports break)

- [ ] **Step 1: Confirm baseline.** All existing orders tests green:
```bash
docker compose exec backend pytest backend/tests/integration/test_orders_*.py backend/tests/services/test_orders_*.py -v
```

- [ ] **Step 2: Locate exact line ranges in `orders_service.py`.**
```bash
grep -n "^async def \(place_order\|modify_order\|preview_order\)" backend/app/services/orders_service.py
```

- [ ] **Step 3: Move functions** to new modules. Each new module imports its dependencies (capability service, broker registry, models, schemas) directly. Update `orders_service.py` to re-export:

```python
# backend/app/services/orders_service.py — re-exports preserve callers
from app.services.orders.place import place_order  # noqa: F401
from app.services.orders.modify import modify_order  # noqa: F401
from app.services.orders.preview import preview_order  # noqa: F401
```

Create `backend/app/services/orders/__init__.py`:

```python
"""Phase 10a — orders submodules extracted from orders_service.py."""
from app.services.orders.place import place_order
from app.services.orders.modify import modify_order
from app.services.orders.preview import preview_order

__all__ = ["place_order", "modify_order", "preview_order"]
```

- [ ] **Step 4: Run all orders tests — must still be green (pure refactor).**

Run: `docker compose exec backend pytest backend/tests/integration/test_orders_*.py backend/tests/services/test_orders_*.py -v`
Expected: same number of passes as baseline. **If anything goes red, abort and revisit imports — do not modify behavior in this commit.**

- [ ] **Step 5: Lint + type check.**
```bash
docker compose exec backend ruff check app/services/orders/ && docker compose exec backend mypy --strict app/services/orders/
```

- [ ] **Step 6: Commit.**
```bash
git add backend/app/services/orders/ backend/app/services/orders_service.py
git commit -m "refactor(phase10a): extract place_order/modify_order/preview_order from orders_service.py (pure)"
```

---

### Task D3: Insert RiskService.evaluate at station 4 in `preview_order`

**Files:**
- Modify: `backend/app/services/orders/preview.py`

- [ ] **Step 1: Insert gate** after the existing capability check + before broker dispatch (or before response shaping in preview path):

```python
from app.services.risk_service import EvaluationContext, RiskService
from app.schemas.risk import GateVerdict

risk = RiskService(db=session, redis=redis, config=config_service, sidecar=broker_client)
eval_ctx = EvaluationContext(
    account_id=account.id,
    broker_id=capability_broker_id(broker_label),
    instrument_id=request.instrument_id,
    side=request.side, qty=request.qty, price=request.price,
    order_type=request.order_type, time_in_force=request.time_in_force,
    request_id=request.request_id or str(uuid.uuid4()),
    currency_base=account.currency_base,
)
verdict: GateVerdict = await risk.evaluate(eval_ctx, mode="preview")
log.info("risk.evaluated", verdict=verdict.final_verdict, kind="preview",
         account_id=str(account.id), lat_ms=verdict.latency_ms)
if verdict.final_verdict == "block":
    return PreviewResponse(
        ok=False,
        warnings=[w.model_dump() for w in verdict.warnings],
        blockers=[b.model_dump() for b in verdict.blockers],
    )
# Otherwise continue to existing dispatch / response shaping; merge gate warnings.
```

The existing `PreviewResponse` should expose a `warnings` and `blockers` field; if it doesn't, extend `app/schemas/orders.py::PreviewResponse` to include them and regenerate types in Chunk E.

- [ ] **Step 2: Run preview-related tests.**
```bash
docker compose exec backend pytest backend/tests/integration/test_risk_gate_orders.py -v -k 'preview' && docker compose exec backend pytest backend/tests/integration/test_orders_preview*.py -v
```

Expected: preview-related risk-gate tests turn green; existing tests stay green.

- [ ] **Step 3: Commit.**
```bash
git commit -am "feat(phase10a): risk gate at station 4 in preview path (structlog-only audit)"
```

---

### Task D4: Insert RiskService.evaluate at station 4 in `place_order` + audit + counters

**Files:**
- Modify: `backend/app/services/orders/place.py`

- [ ] **Step 1: Insert gate + audit + counter logic** after capability check, before broker dispatch:

```python
from app.services.risk_service import EvaluationContext, RiskService
from app.services.risk_inflight_counters import (
    commit_bp, decrement_pdt, revert_bp, revert_pdt,
)
from app.models.risk import RiskDecision
from fastapi import HTTPException

risk = RiskService(db=session, redis=redis, config=config_service, sidecar=broker_client)
eval_ctx = EvaluationContext(
    account_id=account.id,
    broker_id=capability_broker_id(broker_label),
    instrument_id=request.instrument_id,
    side=request.side, qty=request.qty, price=request.price,
    order_type=request.order_type, time_in_force=request.time_in_force,
    request_id=request.request_id or str(uuid.uuid4()),
    currency_base=account.currency_base,
)
verdict = await risk.evaluate(eval_ctx, mode="place_order")

if verdict.final_verdict == "block":
    decision = RiskDecision(
        account_id=account.id, instrument_id=request.instrument_id,
        side=request.side, qty=request.qty, price=request.price,
        order_type=request.order_type, time_in_force=request.time_in_force,
        verdict="block",
        blockers=[b.model_dump() for b in verdict.blockers],
        warnings=[w.model_dump() for w in verdict.warnings],
        latency_ms=verdict.latency_ms,
        attempt_kind="place_order",
        request_id=eval_ctx.request_id,
        order_id=None,
    )
    session.add(decision)
    await session.commit()
    raise HTTPException(
        status_code=422,
        detail={"error": {
            "code": "risk_gate_blocked",
            "blockers": [b.model_dump() for b in verdict.blockers],
        }},
    )

# ALLOW or WARN — optimistically decrement counters, dispatch
notional = float(request.qty * (request.price or Decimal("0")))
await commit_bp(redis, account.id, notional)
await decrement_pdt(redis, account.id)

try:
    broker_resp = await broker_client.place_order(...)  # existing call
except Exception:
    await revert_bp(redis, account.id, notional)
    await revert_pdt(redis, account.id)
    decision = RiskDecision(
        account_id=account.id, instrument_id=request.instrument_id,
        side=request.side, qty=request.qty, price=request.price,
        order_type=request.order_type, time_in_force=request.time_in_force,
        verdict=verdict.final_verdict, blockers=[],
        warnings=[w.model_dump() for w in verdict.warnings],
        latency_ms=verdict.latency_ms, attempt_kind="place_order",
        request_id=eval_ctx.request_id, order_id=None,
    )
    session.add(decision)
    await session.commit()
    raise

decision = RiskDecision(
    account_id=account.id, instrument_id=request.instrument_id,
    side=request.side, qty=request.qty, price=request.price,
    order_type=request.order_type, time_in_force=request.time_in_force,
    verdict=verdict.final_verdict, blockers=[],
    warnings=[w.model_dump() for w in verdict.warnings],
    latency_ms=verdict.latency_ms, attempt_kind="place_order",
    request_id=eval_ctx.request_id, order_id=broker_resp.order_id,
)
session.add(decision)
await session.commit()
```

- [ ] **Step 2: Run integration tests.**
```bash
docker compose exec backend pytest backend/tests/integration/test_risk_gate_orders.py backend/tests/integration/test_orders_*.py -v
```

Expected: 6/6 risk-gate tests green; existing tests unaffected.

- [ ] **Step 3: Commit.**
```bash
git commit -am "feat(phase10a): risk gate at station 4 in place_order + audit + in-flight counters"
```

---

### Task D5: Same insert in `modify_order`

**Files:**
- Modify: `backend/app/services/orders/modify.py`

- [ ] **Step 1: Mirror D4 changes** for `modify_order` — same eval_ctx + dispatch + audit pattern; `attempt_kind="modify_order"`; counter decrement only if the modify increases notional.

- [ ] **Step 2: Run modify tests.**
```bash
docker compose exec backend pytest backend/tests/integration/test_orders_modify*.py -v
```

Expected: existing green; new modify-specific gate test green if added.

- [ ] **Step 3: Commit.**
```bash
git commit -am "feat(phase10a): risk gate at station 4 in modify_order + audit"
```

---

### Task D6: Reconcile FE/BE `BrokerCapabilitiesResponse` shape mismatch

**Files:**
- Modify: `backend/app/api/brokers.py` (or wherever `/api/brokers/{id}/capabilities` lives — verify with `grep -rn "capabilities" backend/app/api/`)
- Modify: `frontend/src/services/capabilities/types.ts`
- Modify: `frontend/src/services/capabilities/api.ts`
- Modify: `frontend/src/hooks/useBrokerCapabilities.ts`

- [ ] **Step 1: Pin BE response_model.** Add explicit `response_model=BrokerCapabilitiesResponse` to the route decorator and ensure the route returns that exact shape (flat `combos` list per FE expectation, OR keep grouped-by-asset_class dict — pick one canonical shape and document).

Choose: **flat-list shape** (matches the FE `BrokerCapabilitiesResponse.combos` array), since FE consumers are already written against it.

- [ ] **Step 2: Update BE service** to flatten asset-class-grouped capabilities into `combos: list[CapabilityComboRow]` before returning.

- [ ] **Step 3: Remove the KNOWN ISSUE block** from `frontend/src/services/capabilities/types.ts`. Types now match BE response_model.

- [ ] **Step 4: Regenerate api-generated.ts.**

```bash
bash scripts/gen-types.sh
```

- [ ] **Step 5: Run FE typecheck + tests.**
```bash
cd frontend && pnpm typecheck && pnpm test --run
```

Expected: existing capability-related tests green; type errors resolved.

- [ ] **Step 6: Commit.**
```bash
git add backend/app/api/brokers.py frontend/src/services/capabilities/ \
        frontend/src/hooks/useBrokerCapabilities.ts frontend/src/api-generated.ts
git commit -m "fix(phase10a): reconcile FE/BE BrokerCapabilitiesResponse runtime shape (deferred from p9)"
```

---

### Task D7: Audit + admin + chaos integration tests

**Files:**
- Create: `backend/tests/integration/test_risk_decisions_audit.py`
- Create: `backend/tests/integration/test_risk_limits_admin.py`
- Create: `backend/tests/integration/test_account_kill_switch_admin.py`
- Create: `backend/tests/chaos/test_risk_chaos.py`

Each test uses outer-transaction fixture per `feedback_pytest_session_begin_commits.md`.

- [ ] **Step 1: Write `test_risk_decisions_audit.py`.** Two cases: (a) place_order with BLOCK verdict → row in `risk_decisions` + `pg_notify` payload `{id, verdict, account_id}` captured via `asyncpg.add_listener('risk_decision', cb)`; (b) place_order ALLOW + dispatch failure → row written with `order_id=NULL`.

- [ ] **Step 2: Write `test_risk_limits_admin.py`.** CRUD round-trip: POST/PUT/DELETE; assert CSRF nonce required (request without nonce → 403); assert `risk_limits_history` row written on UPDATE; assert Redis `app_config:invalidate:risk_limits` pubsub message published.

- [ ] **Step 3: Write `test_account_kill_switch_admin.py`.** Toggle round-trip: POST `{is_enabled: true, reason: "test"}` → 200; assert next preview/place_order returns BLOCK without cache stale window; assert `account_kill_switches_history` row written.

- [ ] **Step 4: Write `test_risk_chaos.py`.** Six scenarios per spec §8:
1. Sidecar timeout 600ms on preview → WARN within 500ms budget.
2. Sidecar timeout 3s+ on place_order → 503 + Retry-After + counter NOT decremented.
3. DB connection lost mid-evaluation → fail-CLOSED BLOCK on both paths.
4. Redis pubsub message dropped — admin write, then assert worker cache TTL bounded at 60s.
5. History trigger failure → UPDATE rolled back transactionally.
6. Optimistic counter revert on dispatch failure — gate ALLOWs, broker rejects, counter restored, audit row `order_id=NULL`.

- [ ] **Step 5: Run all integration + chaos tests.**
```bash
docker compose exec backend pytest backend/tests/integration/ backend/tests/chaos/ -v
```

Expected: all green.

- [ ] **Step 6: Commit.**
```bash
git add backend/tests/integration/test_risk_decisions_audit.py \
        backend/tests/integration/test_risk_limits_admin.py \
        backend/tests/integration/test_account_kill_switch_admin.py \
        backend/tests/chaos/test_risk_chaos.py
git commit -m "test(phase10a): audit + admin + chaos integration tests"
```

---

### Task D8: Backend admin API + read API

**Files:**
- Create: `backend/app/api/risk.py`
- Create: `backend/app/api/admin_risk.py`
- Create: `backend/app/services/risk_limits_service.py`
- Create: `backend/app/services/account_kill_switch_service.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Implement services.**

`backend/app/services/risk_limits_service.py` — CRUD methods (`list_all`, `create`, `update`, `delete`) + 60s in-process cache + Redis pubsub `app_config:invalidate:risk_limits` on every mutation. Pattern matches `OrderCapabilityService` from Phase 8a.

`backend/app/services/account_kill_switch_service.py` — `get(account_id)` + `toggle(account_id, is_enabled, reason, by)`.

- [ ] **Step 2: Implement routers.**

`backend/app/api/risk.py`:

```python
from fastapi import APIRouter, Depends, Query
from app.deps.auth import require_admin_jwt
from app.deps.db import get_session

router = APIRouter(prefix="/api/risk", tags=["risk"], dependencies=[Depends(require_admin_jwt)])


@router.get("/limits", response_model=list[RiskLimitOut])
async def list_limits(session=Depends(get_session)):
    return await RiskLimitsService(session).list_all()


@router.get("/decisions", response_model=list[RiskDecisionOut])
async def list_decisions(
    account_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    session=Depends(get_session),
):
    return await session.execute(...).all()
```

`backend/app/api/admin_risk.py`:

```python
router = APIRouter(
    prefix="/api/admin",
    tags=["admin-risk"],
    dependencies=[Depends(require_admin_jwt), Depends(require_csrf_nonce)],
)

@router.post("/risk-limits", response_model=RiskLimitOut, status_code=201) ...
@router.put("/risk-limits/{id}", response_model=RiskLimitOut) ...
@router.delete("/risk-limits/{id}", status_code=204) ...

@router.get("/accounts/{account_id}/kill-switch", response_model=AccountKillSwitchOut) ...
@router.post("/accounts/{account_id}/kill-switch", response_model=AccountKillSwitchOut) ...
```

- [ ] **Step 3: Register in `main.py`.**
```python
from app.api import risk, admin_risk
app.include_router(risk.router)
app.include_router(admin_risk.router)
```

- [ ] **Step 4: Run admin integration tests.**
```bash
docker compose exec backend pytest backend/tests/integration/test_risk_limits_admin.py backend/tests/integration/test_account_kill_switch_admin.py -v
```

Expected: green.

- [ ] **Step 5: Commit.**
```bash
git add backend/app/api/risk.py backend/app/api/admin_risk.py \
        backend/app/services/risk_limits_service.py \
        backend/app/services/account_kill_switch_service.py \
        backend/app/main.py
git commit -m "feat(phase10a): /api/risk read + /api/admin/risk-limits CRUD + kill-switch toggle"
```

---

### Task D9: Reviewer chain — Chunk D boundary (full 5-reviewer chain)

- [ ] **Step 1: Determine diff range.** `git log --oneline c519675..HEAD | head -20` — expect ~12-14 commits across A1..D8.

- [ ] **Step 2: Dispatch full 5-reviewer chain in parallel** in one message:

1. **spec-compliance** (haiku) — diff vs spec §1+§4+§6 inlined verbatim
2. **python-reviewer** (haiku) — async + types + ruff/mypy
3. **code-quality-reviewer** (sonnet) — large refactor: regression risk, dead code, single-responsibility on extracted modules
4. **security-reviewer** (sonnet) — CSRF nonce on admin endpoints, JWT on read, idempotency keys, no secrets in audit JSONB, `updated_by`/`enabled_by` server-side from JWT
5. **silent-failure-hunter** (sonnet) — `asyncio.gather(return_exceptions=True)` correctness, broker-dispatch error paths, counter revert atomicity, audit-write retry policy

- [ ] **Step 3: Apply CRIT+HIGH+MED inline; commit fixes.**
```bash
git commit -am "fix(phase10a): chunk-D 5-reviewer findings"
```

---

## Chunk E — Frontend (TradeTicket WARN/BLOCK + /admin/risk pages + hooks)

Estimated: 6-7 commits. Reviewer chain: spec-compliance + typescript-reviewer (haiku) + code-quality-reviewer + a11y-architect.

If Codex returns mid-phase, prefer Codex for Chunk E.

### Task E1: TS types + API client + hooks

**Files:**
- Create: `frontend/src/services/risk/types.ts`
- Create: `frontend/src/services/risk/api.ts`
- Create: `frontend/src/hooks/useRiskLimits.ts`
- Create: `frontend/src/hooks/useAccountKillSwitch.ts`
- Test: `frontend/src/services/risk/api.test.ts`
- Test: `frontend/src/hooks/useRiskLimits.test.ts`

- [ ] **Step 1: Write Vitest tests** asserting:
- `useRiskLimits` invalidates query on `onSuccess` (M9 fix).
- `setKillSwitch` mutation calls `queryClient.invalidateQueries(['account-kill-switches', account_id])`.
- API client methods return parsed types.

- [ ] **Step 2: Run, fail.** `pnpm test --run frontend/src/services/risk frontend/src/hooks/useRiskLimits.test.ts`

- [ ] **Step 3: Implement.** Types match `api-generated.ts` shape after D6 regen. Hooks use TanStack Query with explicit `invalidateQueries` in mutation `onSuccess`:

```typescript
export function useRiskLimits() {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: ['risk-limits'], queryFn: listRiskLimits, staleTime: 30_000 });
  const create = useMutation({
    mutationFn: createRiskLimit,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['risk-limits'] }),
  });
  // ... update, remove ...
  return { list, create, update, remove };
}
```

- [ ] **Step 4: Run, pass.** Typecheck clean.

- [ ] **Step 5: Commit.**
```bash
git commit -am "feat(phase10a-fe): risk-limits + kill-switch types + API + TanStack hooks (M9)"
```

---

### Task E2: TradeTicketModal WARN/BLOCK banners

**Files:**
- Modify: `frontend/src/features/trade/TradeTicketModal.tsx`
- Test: `frontend/src/features/trade/TradeTicketModal.test.tsx`

- [ ] **Step 1: Append RTL tests** for: WARN banner shown when `previewResponse.warnings.length > 0`; submit button disabled until "Acknowledge warnings" clicked; BLOCK 422 renders red banner with one `<li>` per blocker code; BLOCK does not retain previous WARN acknowledge.

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement banners.** Yellow `<Banner variant="warn">` aggregating `previewResponse.warnings` with checkbox "I understand these warnings"; submit button `disabled={hasWarnings && !acknowledged}`. On 422 with `error.code === 'risk_gate_blocked'`, render `<Banner variant="error">` with `error.blockers.map(b => <li>{b.message}</li>)`.

- [ ] **Step 4: Run, pass.**

- [ ] **Step 5: Commit.**
```bash
git commit -am "feat(phase10a-fe): TradeTicket WARN banner with acknowledge gate + BLOCK error rows"
```

---

### Task E3: `/admin/risk` page (limits CRUD)

**Files:**
- Create: `frontend/src/features/admin/risk/RiskLimitsPage.tsx`
- Create: `frontend/src/features/admin/risk/RiskLimitsPage.test.tsx`
- Create: `frontend/src/features/admin/risk/RiskLimitsPage.stories.tsx`
- Create: `frontend/src/routes/admin/risk.tsx`

- [ ] **Step 1: Write RTL test** asserting: list renders rows; create dialog opens on button click; submit calls `useRiskLimits().create.mutate`; row delete calls `remove.mutate`.

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement.** Use Phase 3 primitives `<DataTable>`, `<ColumnCustomizerDialog>`, `<Dialog>`, `<Switch>`, `<Select>`. Form fields: scope_type Select (global/broker/account), scope_id Input (disabled when global), limit_kind Select, limit_value NumericCell, warn_at_pct NumericCell (optional), is_active Switch, notes Textarea (max 1000 chars).

- [ ] **Step 4: Storybook story** with `mock` adapter providing 5 example rows.

- [ ] **Step 5: TanStack Router route file** `frontend/src/routes/admin/risk.tsx`:

```typescript
import { createFileRoute } from '@tanstack/react-router';
import { RiskLimitsPage } from '@/features/admin/risk/RiskLimitsPage';

export const Route = createFileRoute('/admin/risk')({
  component: RiskLimitsPage,
});
```

- [ ] **Step 6: Run typecheck + test + Storybook smoke.**
```bash
cd frontend && pnpm typecheck && pnpm test --run RiskLimitsPage && pnpm storybook --ci --quiet
```

- [ ] **Step 7: Commit.**
```bash
git commit -am "feat(phase10a-fe): /admin/risk page (limits CRUD) + Storybook"
```

---

### Task E4: `/admin/risk/decisions` feed page

**Files:**
- Create: `frontend/src/features/admin/risk/RiskDecisionsPage.tsx`
- Create: `frontend/src/features/admin/risk/RiskDecisionsPage.test.tsx`
- Create: `frontend/src/routes/admin/risk.decisions.tsx`

- [ ] **Step 1: Write RTL test** — feed renders rows; account + verdict filters update query.

- [ ] **Step 2: Implement** — read-only feed; reuses `<DataTable>` from Phase 3; columns: evaluated_at, account, side, qty, price, verdict, blockers (count), warnings (count). Filter UI via `<Select>` for verdict, `<AccountPicker>` for account.

- [ ] **Step 3: Run typecheck + test.**

- [ ] **Step 4: Commit.**
```bash
git commit -am "feat(phase10a-fe): /admin/risk/decisions read-only feed"
```

---

### Task E5: Account kill-switch row on /admin/accounts

**Files:**
- Create: `frontend/src/features/admin/accounts/AccountKillSwitchRow.tsx`
- Modify: `frontend/src/features/admin/accounts/AccountsPage.tsx`

- [ ] **Step 1: Write RTL test** — clicking switch opens reason dialog; submitting dialog calls `setKillSwitch.mutate({account_id, is_enabled: true, reason})`; cancellation does NOT change state.

- [ ] **Step 2: Implement.** Row component uses `useAccountKillSwitch(account.id)`; renders `<Switch>` primitive; on toggle-on, opens `<Dialog>` requiring reason text (mirrors confirmation-nonce flow).

- [ ] **Step 3: Wire into `AccountsPage.tsx`** — add new column rendering `<AccountKillSwitchRow accountId={row.id} />`.

- [ ] **Step 4: Run typecheck + test.**

- [ ] **Step 5: Commit.**
```bash
git commit -am "feat(phase10a-fe): account kill-switch row on /admin/accounts"
```

---

### Task E6: E2E tests

**Files:**
- Create: `frontend/tests/e2e/phase10-risk-gate.spec.ts`
- Create: `frontend/tests/e2e/phase10-admin-risk.spec.ts`

- [ ] **Step 1: Write `phase10-risk-gate.spec.ts`** — 4 flows per spec §8:
1. BLOCK on account-kill-switch enabled — toggle in admin, attempt trade, see red banner.
2. WARN at 80% concentration — set up positions via API fixture, attempt trade, see yellow banner with "Acknowledge" gate.
3. BLOCK on insufficient BP — order notional > BP, see red banner with `buying_power_insufficient` blocker.
4. WARN on Alpaca margin-fallback — Alpaca account, see WARN "margin check unavailable, BP cache only".

- [ ] **Step 2: Write `phase10-admin-risk.spec.ts`** — operator creates limit, edits limit, deletes limit, toggles kill switch; assert TradeTicket honors changes.

- [ ] **Step 3: Run.**
```bash
cd frontend && pnpm test:e2e -- phase10-*.spec.ts
```

- [ ] **Step 4: Commit.**
```bash
git commit -am "test(phase10a-fe): E2E flows for risk gate + admin risk pages"
```

---

### Task E7: Reviewer chain — Chunk E boundary

- [ ] Dispatch 4 parallel reviewers in one message: spec-compliance (haiku), typescript-reviewer (haiku), code-quality-reviewer (sonnet), a11y-architect (sonnet — focuses on banner color contrast, keyboard navigation in admin pages, ARIA attributes on Switch). Apply findings inline; commit fixes.

---

## Chunk F — Tests catch-up + close-out

Estimated: 4 commits.

### Task F1: PHASE-WORKFLOW.md line 42 fix

**Files:**
- Modify: `docs/PHASE-WORKFLOW.md`

- [ ] **Step 1: Edit line 42** — replace the stale "Reviews fire at EVERY commit boundary" sentence with:

> "**Reviews fire at the end of every chunk** (≥5 substantive commits), per `feedback_review_per_chunk.md`. Per-task review is optional and reserved for high-risk tasks (e.g. auth/payments → security; migrations → database). End-of-phase: spec-compliance reviewer alone before tag."

- [ ] **Step 2: Commit.**
```bash
git add docs/PHASE-WORKFLOW.md
git commit -m "docs(phase10a): fix PHASE-WORKFLOW.md L42 (per-chunk cadence; not per-commit)"
```

---

### Task F2: Final test sweep

- [ ] **Step 1: Backend full suite + coverage.**
```bash
docker compose exec backend pytest -v
docker compose exec backend pytest --cov=app --cov-report=term-missing | tail -30
```
Expected: all green; coverage ≥ 80% on `app/services/risk_service.py`, `app/services/risk_inflight_counters.py`, `app/services/orders/`, `app/api/risk.py`, `app/api/admin_risk.py`.

- [ ] **Step 2: Frontend full suite.**
```bash
cd frontend && pnpm typecheck && pnpm test --run && pnpm test:e2e
```

- [ ] **Step 3: Sidecar tests.**
```bash
cd sidecar_ibkr && uv run pytest -v
cd ../sidecar_schwab && uv run pytest -v
cd ../sidecar_alpaca && uv run pytest -v
```

- [ ] **Step 4: Lint sweep.**
```bash
docker compose exec backend ruff check . && docker compose exec backend mypy --strict app/
cd frontend && pnpm lint && pnpm stylelint
```

- [ ] **Step 5: Commit (if cleanups landed).**
```bash
git commit -am "chore(phase10a): test sweep + lint cleanup"
```

---

### Task F3: Phase-end spec-compliance reviewer

- [ ] **Step 1: Dispatch lone spec-compliance reviewer** (haiku) on full Phase 10a diff range (`c519675..HEAD`) with spec inlined. Lightweight final check before tag per `feedback_review_per_chunk.md`.

- [ ] **Step 2: Address spec-drift findings; commit fix(es).**

---

### Task F4: Update CLAUDE.md, CHANGELOG.md, TASKS.md

**Files:**
- Modify: `CLAUDE.md`
- Modify: `CHANGELOG.md`
- Modify: `TASKS.md`

- [ ] **Step 1: CLAUDE.md** — add a Phase 10a invariants paragraph in the cross-cutting "Broker adapters" section: validation gate now has 5 stations including risk gate; `RiskService` is the chokepoint (`backend/app/services/risk_service.py`); in-flight Redis counters require single-replica today (`feedback_phase10a_inflight_counters_single_replica.md` to be authored); cap-edit invalidation via `app_config:invalidate:risk_limits` Redis pubsub.

- [ ] **Step 2: CHANGELOG.md** — add `## [0.12.0] — YYYY-MM-DD` entry summarizing Phase 10a deliverables. Reference architect-review applied (3 CRIT + 4 HIGH + 10 MED) and the FE/BE capability shape reconciliation.

- [ ] **Step 3: TASKS.md** — tick Phase 10a checkboxes; mark deferred items:
- 10a.5 (if surfaced): two-tick guard before BrokerDiscoverer position wipe; tz-pinned day boundary tightening for non-USD accounts.
- Phase 24: `risk_decisions` cleanup cron; multi-worker uvicorn for in-flight counters; TimescaleDB hypertable on `risk_decisions` if volume grows.
- Phase 23: pre-trade "would trigger 30-day b&b matching" hook into the risk gate.

- [ ] **Step 4: Commit.**
```bash
git add CLAUDE.md CHANGELOG.md TASKS.md
git commit -m "docs(phase10a): close-out — CLAUDE.md + CHANGELOG.md + TASKS.md updates"
```

---

### Task F5: Tag v0.12.0 + push

- [ ] **Step 1: Confirm CI green on HEAD.**
```bash
gh run list --limit 5
```
Expected: most recent runs on HEAD all green.

- [ ] **Step 2: Tag.**
```bash
git tag -a v0.12.0 -m "phase10a — risk engine + pre-trade gate"
```

- [ ] **Step 3: Push.**
```bash
git push origin main
git push origin v0.12.0
```

- [ ] **Step 4: Verify CI on tag.**
```bash
gh run list --branch main --limit 3
```
Expected: green.

---

## Self-review (run after writing this plan)

**1. Spec coverage:** every spec section maps to ≥ one task:

- §1 scope (7 checks) → Tasks B2-B7
- §2 architecture (5-station gate) → Tasks D3-D5
- §3 data model → Tasks A1-A2 + admin tests in D7
- §4 data flow (preview vs place_order; in-flight counters; orphan policy) → Tasks D3 + D4 + D5
- §5 sidecar RPCs → Tasks C1-C5
- §6 API surface → Task D8
- §7 FE surface → Tasks E1-E6
- §8 testing → Tasks D1, D7, F2
- §9 reviewer plan → Tasks A4, B9, C6, D9, E7, F3
- §10 chunk breakdown → entire plan structure
- §11 operational + security → A1 (CHECK constraints), C5 (idempotency), D4 (CSRF in admin), D7 (chaos)
- §12 open questions → captured in F4 close-out (TASKS.md deferred section)
- §13 architect review → already applied; F4 confirms

**2. Placeholder scan:** no "TBD", "TODO", or vague "add error handling" steps. The few "verify with grep" instructions are exact commands, not placeholders.

**3. Type consistency:** `EvaluationContext` fields, `GateVerdict.final_verdict`, `RiskService` constructor signature, in-flight counter function names (`decrement_pdt`, `revert_pdt`, `commit_bp`, `revert_bp`, `inflight_pdt_remaining`, `inflight_bp_committed`, `reconcile_pdt`, `reconcile_bp_committed`) consistent across all tasks.

---

## Execution handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-08-phase10a-risk-engine-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task; main-thread reviews between tasks; fast iteration. Per `feedback_codex_routing_strict.md` + `feedback_codex_fallback.md`: while Codex is rate-limited (until ~2026-05-12), main-thread implements via Edit/Write directly; subagents are reviewer-only. After Codex returns, optionally canary Chunk E on Codex.

**2. Inline Execution** — execute tasks in this session via `superpowers:executing-plans`; batch with checkpoints for review.

Which approach?
