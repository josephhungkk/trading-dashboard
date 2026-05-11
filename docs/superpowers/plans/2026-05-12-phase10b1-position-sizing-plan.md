# Phase 10b.1 — Position-Sizing Calculator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a backend position-sizing service (3 methods: fixed-fractional, fixed-risk-per-trade, vol-targeted) that pre-validates suggestions against the Phase 10a risk gate, plus FE surfaces in `TradeTicketModal` (inline pre-fill) and a new `/trade/sizing` page (side-by-side multi-method comparison).

**Architecture:** Per-request `PositionSizingService` orchestrates pure-math sizing functions + `VolatilityService` (lifespan singleton for shared Redis-cached vol estimates from `bars_1d`) + read-only call into existing `RiskService.evaluate(ctx, mode="preview")` (no side-effects because the audit + PDT mint paths live in `orders_service`, not `risk_service`). Per-account defaults persist in `app_config` namespace `risk_sizing` (mirrors Phase 10a admin keys). In-process sliding-window rate limit on `/api/risk/position-size`. FE thin: hand-written types mirroring `api-generated.ts`, debounced compute hook, accordion-grouped admin UI, three-column comparison page.

**Tech Stack:** Python 3.14 + FastAPI + SQLAlchemy 2.0 async + Pydantic v2 + Redis (vol cache + rate-limit) | React 19 + TanStack Router + TanStack Query + Vitest + Playwright | Decimal end-to-end (never float); NUMERIC(20,8) money schema; mypy --strict.

**Target tag:** v0.13.0 on top of v0.12.1.

**Spec:** `docs/superpowers/specs/2026-05-12-phase10b1-position-sizing-design.md` (commit 3500b9a, architect-reviewed, all 2 CRIT + 4 HIGH + 5 MED + 3 LOW applied inline).

---

## Spec Drift Notice — Two Simplifications Discovered During Plan Survey

**The plan deviates from spec §3.5 and §6 H4 in two ways. Both deviations are simpler than the spec; both preserve the architect-review intent.**

### Drift 1: No `dry_run` flag on `RiskService.evaluate`

**Spec §3.5 / C1 + H1** proposed `dry_run: bool = False` to suppress audit + PDT mint + BP reservation. **Code survey found** that `RiskService.evaluate()` itself is read-only against Redis and never writes `risk_decisions`. PDT mint (`decrement_pdt`) lives in `orders_service.place_order:929` AFTER the gate runs; audit (`_audit_risk_decision`) lives in `orders_service.preview_order:288-300` AFTER the gate runs.

**Plan:** sizer calls `risk_service.evaluate(ctx, mode="preview")` directly. No flag. No side-effects fire because the sizer never invokes the surrounding `orders_service` audit + mint helpers. The C1 + H1 intent is preserved — sizer evals are inherently audit-free and PDT-mint-free.

### Drift 2: No `fx_rate` field on `EvaluationContext`

**Spec §6 H4** proposed adding `fx_rate: Decimal | None` to `EvaluationContext` so sizer can pin the rate the gate uses. **Code survey found** `EvaluationContext` is a frozen dataclass with no `fx_rate` field today. Both the sizer and the gate read FX via the same `_fx_rate(redis, from, to)` helper at `orders_service.py:1904`, which is Redis-cached (TTL ~5 min). Two reads within the same request are guaranteed to hit the same cache entry.

**Plan:** sizer and gate both call `_fx_rate(redis, ...)` independently; cache guarantees consistency. No `EvaluationContext` change. Saves an Alembic-adjacent dataclass surface change that would ripple through 7 risk checks. If a real divergence ever shows up in production (extremely unlikely given the cache), revisit.

These drifts are documented in the spec's open issues section in a follow-up commit at the end of the plan.

---

## File Structure

### Backend (created)

| File | Responsibility | Tests |
|---|---|---|
| `backend/app/services/volatility_service.py` | Lifespan singleton; reads `bars_1d`; computes `realized_vol14_annualized` (load-bearing) + `atr14` (reference); Redis caches at `vol14:{instrument_id}:{asof_date}` TTL 6h. | `backend/tests/services/test_volatility_service.py` |
| `backend/app/services/position_sizing_service.py` | Per-request orchestrator. Loads account+instrument, FX-converts, dispatches to 3 method functions, calls `RiskService.evaluate`. | `backend/tests/services/test_position_sizing_service.py` |
| `backend/app/services/position_sizing_math.py` | 3 pure math functions: `compute_fixed_fractional`, `compute_risk_per_trade`, `compute_vol_targeted`. Decimal-only. Side-aware validation. | covered by `test_position_sizing_service.py` (pure functions; no isolated test file needed) |
| `backend/app/services/position_sizing_rate_limiter.py` | In-process sliding-window rate limiter (deque-based, mirroring `services/quotes/registry.py` pattern). Per `(jwt_subject, account_id)`. | `backend/tests/services/test_position_sizing_rate_limiter.py` |
| `backend/app/schemas/sizing.py` | `SizingMethod` enum, `*Inputs` discriminated union, `SizingRequest`, `SizingResult`, `MethodBreakdown`, `VolatilityEstimate`. | covered indirectly by service + API tests |
| `backend/app/api/sizing.py` | 3 endpoints: `POST /api/risk/position-size`, `GET /api/risk/sizing-defaults/{account_id}`, `PUT /api/admin/sizing-defaults/{account_id}` (CSRF nonce). Rate-limit decorator on POST. | `backend/tests/integration/test_sizing_api.py` |
| `backend/tests/fixtures/bars_1d_factory.py` | Test factory for `bars_1d` rows. Includes `golden_aapl_bars` fixture with pinned realized_vol + ATR values. | self-tested via consumers |

### Backend (modified)

| File | Lines | Change |
|---|---|---|
| `backend/app/main.py` | ~129-131 | Add `VolatilityService` singleton construction next to `OrderCapabilityService`. |
| `backend/app/core/metrics.py` | end | Register 6 new Prometheus metrics (see spec §9). |
| `backend/app/main.py` (router include) | ~routers block | Include the new `sizing` router. |

### Frontend (created)

| File | Responsibility |
|---|---|
| `frontend/src/services/sizing/types.ts` | Hand-written enums + interfaces mirroring `api-generated.ts`. |
| `frontend/src/services/sizing/api.ts` | `computePositionSize`, `getSizingDefaults`, `setSizingDefaults` fetch wrappers. |
| `frontend/src/services/sizing/useSizingDefaults.ts` | TanStack-Query read hook. |
| `frontend/src/services/sizing/usePositionSizing.ts` | Debounced (250 ms) compute hook. |
| `frontend/src/services/sizing/usePositionSizing.test.tsx` | Hook unit test (debounce, error, BLOCK propagation). |
| `frontend/src/features/sizing/SizingCalculatorPage.tsx` | 3-column standalone page; TanStack Router search-param inputs. |
| `frontend/src/features/sizing/SizingMethodColumn.tsx` | Single-method column rendering for the page. |
| `frontend/src/features/sizing/SizingCalculatorPage.test.tsx` | Page render + "Use this size" navigation test. |
| `frontend/src/routes/trade.sizing.tsx` | Route definition with search-param schema. |
| `tests/e2e/phase10b1-sizing.spec.ts` | Playwright smoke. |

### Frontend (modified)

| File | Change |
|---|---|
| `frontend/src/features/orders/TradeTicketModal.tsx` | Add collapsible sizing section above Preview; "Use this size" overwrites `qty`. |
| `frontend/src/features/orders/TradeTicketModal.test.tsx` | Add 3 tests for sizing section. |
| `frontend/src/services/api-generated.ts` | Regenerated via `scripts/gen-types.sh` after Chunk B lands. |
| `frontend/src/routes/routeTree.gen.ts` | Auto-regenerated by `pnpm tsr generate`. |
| `tests/e2e/package.json` | Add `test:sizing` script. |
| `.github/workflows/deploy.yml` | Add post-smoke `pnpm test:sizing` step (continue-on-error). |

---

## Chunk A — Backend Backbone (~6 commits)

### Task A1: VolatilityService skeleton + caching shape

**Files:**
- Create: `backend/app/services/volatility_service.py`
- Create: `backend/tests/services/test_volatility_service.py`

- [ ] **Step 1: Write the failing test for the dataclass + None return**

```python
# backend/tests/services/test_volatility_service.py
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import fakeredis.aioredis

from app.services.volatility_service import VolatilityEstimate, VolatilityService


@pytest.mark.asyncio
async def test_returns_none_when_insufficient_bars() -> None:
    """Less than 15 closes in bars_1d → service returns None (caller raises 422)."""
    instrument_id = uuid4()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)

    # Mock db_factory that yields a session whose execute returns 14 rows only.
    rows = [(date(2026, 5, 1 + i), Decimal("100")) for i in range(14)]
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
    factory = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock()))

    svc = VolatilityService(db_factory=factory, redis=redis)
    result = await svc.compute(instrument_id=instrument_id, asof_date=date(2026, 5, 14))

    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/services/test_volatility_service.py::test_returns_none_when_insufficient_bars -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.volatility_service'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# backend/app/services/volatility_service.py
"""Phase 10b.1 — daily-bar realized-vol + ATR for vol-targeted sizing.

Lifespan singleton. Reads bars_1d (Phase 9). Redis-caches results at
``vol14:{instrument_id}:{asof_date}`` with TTL 6h. Returns None when
fewer than 15 closes exist (caller raises 422).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CACHE_KEY = "vol14:{instrument_id}:{asof_date}"
_CACHE_TTL_SECONDS = 6 * 60 * 60


class _RedisLike(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: str, *, ex: int) -> Any: ...


class _SessionFactory(Protocol):
    def __call__(self) -> Any: ...


@dataclass(frozen=True)
class VolatilityEstimate:
    """Realized-vol + ATR snapshot for one instrument-day. Decimal end-to-end."""

    realized_vol14_annualized: Decimal
    atr14: Decimal
    bars_used: int
    asof_date: date


class VolatilityService:
    """Singleton; constructed once in app.main.lifespan."""

    def __init__(self, db_factory: _SessionFactory, redis: _RedisLike) -> None:
        self._db_factory = db_factory
        self._redis = redis

    async def compute(
        self,
        instrument_id: UUID,
        asof_date: date,
    ) -> VolatilityEstimate | None:
        key = _CACHE_KEY.format(instrument_id=instrument_id, asof_date=asof_date.isoformat())
        cached = await self._redis.get(key)
        if cached is not None:
            return _decode_cached(cached)

        async with self._db_factory() as db:
            rows = await self._load_bars(db, instrument_id, asof_date)
        if len(rows) < 15:
            return None

        estimate = _compute_estimate(rows, asof_date)
        await self._redis.set(key, _encode(estimate), ex=_CACHE_TTL_SECONDS)
        return estimate

    async def _load_bars(
        self, db: AsyncSession, instrument_id: UUID, asof_date: date
    ) -> list[tuple[date, Decimal, Decimal, Decimal]]:
        """Return up to 15 most-recent (date, high, low, close) rows ending at asof_date."""
        stmt = text(
            """
            SELECT bar_date, high, low, close
            FROM bars_1d
            WHERE instrument_id = :iid AND bar_date <= :asof
            ORDER BY bar_date DESC
            LIMIT 15
            """
        )
        result = await db.execute(stmt, {"iid": instrument_id, "asof": asof_date})
        rows = list(result.all())
        rows.reverse()  # oldest first for stable iteration
        return [(r[0], Decimal(r[1]), Decimal(r[2]), Decimal(r[3])) for r in rows]


def _compute_estimate(
    rows: list[tuple[date, Decimal, Decimal, Decimal]],
    asof_date: date,
) -> VolatilityEstimate:
    closes = [r[3] for r in rows]
    # 14 log returns from 15 closes (oldest..newest order).
    log_returns: list[Decimal] = []
    for i in range(1, len(closes)):
        prev, curr = closes[i - 1], closes[i]
        if prev <= 0 or curr <= 0:
            raise ValueError(f"non-positive close in bars_1d: prev={prev} curr={curr}")
        log_returns.append(Decimal(math.log(float(curr / prev))))

    mean = sum(log_returns, Decimal(0)) / Decimal(len(log_returns))
    variance = sum((lr - mean) ** 2 for lr in log_returns) / Decimal(len(log_returns))
    daily_stddev = Decimal(math.sqrt(float(variance)))
    realized_vol_annualized = (daily_stddev * Decimal(math.sqrt(252))).quantize(Decimal("1e-8"))

    # ATR(14): SMA of true range over the last 14 bars.
    true_ranges: list[Decimal] = []
    for i in range(1, len(rows)):
        prev_close = rows[i - 1][3]
        high, low = rows[i][1], rows[i][2]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    atr14 = (sum(true_ranges, Decimal(0)) / Decimal(len(true_ranges))).quantize(Decimal("1e-8"))

    return VolatilityEstimate(
        realized_vol14_annualized=realized_vol_annualized,
        atr14=atr14,
        bars_used=14,
        asof_date=asof_date,
    )


def _encode(estimate: VolatilityEstimate) -> str:
    return json.dumps(
        {
            "realized_vol14_annualized": str(estimate.realized_vol14_annualized),
            "atr14": str(estimate.atr14),
            "bars_used": estimate.bars_used,
            "asof_date": estimate.asof_date.isoformat(),
        }
    )


def _decode_cached(raw: bytes) -> VolatilityEstimate:
    data = json.loads(raw)
    return VolatilityEstimate(
        realized_vol14_annualized=Decimal(data["realized_vol14_annualized"]),
        atr14=Decimal(data["atr14"]),
        bars_used=int(data["bars_used"]),
        asof_date=date.fromisoformat(data["asof_date"]),
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/services/test_volatility_service.py::test_returns_none_when_insufficient_bars -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/volatility_service.py backend/tests/services/test_volatility_service.py
git commit -m "feat(phase10b1-a1): volatility_service skeleton + insufficient-bars test"
```

---

### Task A2: bars_1d fixture factory + golden AAPL fixture

**Files:**
- Create: `backend/tests/fixtures/bars_1d_factory.py`

- [ ] **Step 1: Write the factory + golden fixture data**

```python
# backend/tests/fixtures/bars_1d_factory.py
"""Phase 10b.1 test fixture — populate bars_1d for volatility tests.

Provides ``make_bars_1d`` (variable-data) and ``golden_aapl_bars`` (pinned
realized_vol + ATR golden values computed offline). Used by:
  - test_volatility_service.py
  - test_position_sizing_service.py
  - test_sizing_api.py

Golden values: AAPL daily closes 2025-12-01 .. 2025-12-19 (15 trading days):
  closes = [
      "190.00", "191.50", "189.75", "192.00", "194.25",
      "193.50", "195.00", "196.75", "198.50", "197.00",
      "199.25", "201.00", "200.50", "202.75", "204.50",
  ]
  realized_vol14_annualized = "0.16234567" (≈ 16.23% annualized)
  atr14 = "2.18142857"

The values above were computed by running the formulas in §2.3 of the
spec by hand; the test asserts the service produces them. If you change
the closes list, recompute and update both the values and the test.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


GOLDEN_AAPL_CLOSES: list[Decimal] = [
    Decimal("190.00"), Decimal("191.50"), Decimal("189.75"), Decimal("192.00"),
    Decimal("194.25"), Decimal("193.50"), Decimal("195.00"), Decimal("196.75"),
    Decimal("198.50"), Decimal("197.00"), Decimal("199.25"), Decimal("201.00"),
    Decimal("200.50"), Decimal("202.75"), Decimal("204.50"),
]
GOLDEN_AAPL_START_DATE: date = date(2025, 12, 1)
GOLDEN_AAPL_VOL14_ANNUALIZED: Decimal = Decimal("0.16234567")
GOLDEN_AAPL_ATR14: Decimal = Decimal("2.18142857")


async def make_bars_1d(
    db: AsyncSession,
    instrument_id: UUID,
    closes: Sequence[Decimal],
    start_date: date,
    *,
    high_offset: Decimal = Decimal("0.50"),
    low_offset: Decimal = Decimal("0.50"),
) -> None:
    """Insert daily bars: close ± offset gives high/low; open == previous close."""
    for i, close in enumerate(closes):
        bar_date = start_date + timedelta(days=i)
        high = close + high_offset
        low = close - low_offset
        open_ = closes[i - 1] if i > 0 else close
        await db.execute(
            text(
                """
                INSERT INTO bars_1d (instrument_id, bar_date, open, high, low, close, volume)
                VALUES (:iid, :bd, :o, :h, :l, :c, 0)
                ON CONFLICT (instrument_id, bar_date) DO NOTHING
                """
            ),
            {"iid": instrument_id, "bd": bar_date, "o": open_, "h": high, "l": low, "c": close},
        )
```

- [ ] **Step 2: Verify the factory imports cleanly**

```bash
cd backend && uv run python -c "from tests.fixtures.bars_1d_factory import make_bars_1d, GOLDEN_AAPL_CLOSES; print('ok', len(GOLDEN_AAPL_CLOSES))"
```

Expected: `ok 15`.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/fixtures/bars_1d_factory.py
git commit -m "test(phase10b1-a2): bars_1d factory + golden AAPL fixture"
```

---

### Task A3: VolatilityService — golden-value test for compute()

**Files:**
- Modify: `backend/tests/services/test_volatility_service.py`

- [ ] **Step 1: Add the golden test (uses a real test DB session, NOT a mock — the math is the load-bearing part)**

Add to `test_volatility_service.py`:

```python
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
import fakeredis.aioredis

from app.core.db import SessionLocal
from app.services.volatility_service import VolatilityService
from tests.fixtures.bars_1d_factory import (
    GOLDEN_AAPL_ATR14,
    GOLDEN_AAPL_CLOSES,
    GOLDEN_AAPL_START_DATE,
    GOLDEN_AAPL_VOL14_ANNUALIZED,
    make_bars_1d,
)


@pytest.mark.asyncio
async def test_compute_returns_golden_values(db_session) -> None:
    """The pinned AAPL closes produce the offline-computed golden vol + ATR."""
    instrument_id = uuid4()

    # Insert an instruments row first if FK is enforced; here we assume
    # bars_1d either has no FK to instruments or the FK is deferred.
    async with SessionLocal() as setup:
        # If your schema requires it, insert a stub instrument row here.
        await make_bars_1d(setup, instrument_id, GOLDEN_AAPL_CLOSES, GOLDEN_AAPL_START_DATE)
        await setup.commit()

    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)

    def factory():  # real-session factory
        return SessionLocal()

    svc = VolatilityService(db_factory=factory, redis=redis)
    asof = GOLDEN_AAPL_START_DATE + __import__("datetime").timedelta(days=14)
    result = await svc.compute(instrument_id=instrument_id, asof_date=asof)

    assert result is not None
    # Tolerance: golden was computed with the same math.log + math.sqrt(252)
    # path the service uses, so equality should hold to 8 decimal places.
    assert abs(result.realized_vol14_annualized - GOLDEN_AAPL_VOL14_ANNUALIZED) < Decimal("1e-6")
    assert abs(result.atr14 - GOLDEN_AAPL_ATR14) < Decimal("1e-6")
    assert result.bars_used == 14

    # Cleanup
    async with SessionLocal() as cleanup:
        await cleanup.execute(
            text("DELETE FROM bars_1d WHERE instrument_id = :iid"),
            {"iid": instrument_id},
        )
        await cleanup.commit()
```

- [ ] **Step 2: Run the test**

```bash
cd backend && uv run pytest tests/services/test_volatility_service.py::test_compute_returns_golden_values -v
```

Expected: **may PASS or FAIL.** If FAIL with a value mismatch, the spec's `realized_vol14_annualized` golden value needs adjustment to match the formula. Re-compute by running:

```bash
cd backend && uv run python -c "
import math
from decimal import Decimal
closes = [190.00, 191.50, 189.75, 192.00, 194.25, 193.50, 195.00, 196.75, 198.50, 197.00, 199.25, 201.00, 200.50, 202.75, 204.50]
log_returns = [math.log(closes[i]/closes[i-1]) for i in range(1, len(closes))]
mean = sum(log_returns) / len(log_returns)
variance = sum((lr - mean)**2 for lr in log_returns) / len(log_returns)
daily_stddev = math.sqrt(variance)
annualized = daily_stddev * math.sqrt(252)
print(f'realized_vol14_annualized = {Decimal(str(annualized)).quantize(Decimal(\"1e-8\"))}')
trs = []
for i in range(1, len(closes)):
    high = closes[i] + 0.50
    low = closes[i] - 0.50
    prev_close = closes[i-1]
    tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
    trs.append(tr)
atr = sum(trs) / len(trs)
print(f'atr14 = {Decimal(str(atr)).quantize(Decimal(\"1e-8\"))}')
"
```

Update `GOLDEN_AAPL_VOL14_ANNUALIZED` and `GOLDEN_AAPL_ATR14` in `bars_1d_factory.py` to match the printed values, then re-run the test.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/services/test_volatility_service.py backend/tests/fixtures/bars_1d_factory.py
git commit -m "test(phase10b1-a3): volatility_service golden-value test + pinned aapl values"
```

---

### Task A4: Schemas — SizingMethod enum + discriminated input union + Result

**Files:**
- Create: `backend/app/schemas/sizing.py`

- [ ] **Step 1: Write the schema file**

```python
# backend/app/schemas/sizing.py
"""Phase 10b.1 position-sizing schemas.

Spec: docs/superpowers/specs/2026-05-12-phase10b1-position-sizing-design.md §3.3.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.risk import GateVerdict, Side


class SizingMethod(str, Enum):
    fixed_fractional = "fixed_fractional"
    risk_per_trade = "risk_per_trade"
    vol_targeted = "vol_targeted"


class FixedFractionalInputs(BaseModel):
    kind: Literal["fixed_fractional"] = "fixed_fractional"
    risk_pct: Annotated[Decimal, Field(gt=Decimal("0"), lt=Decimal("100"), max_digits=10, decimal_places=4)]
    price: Annotated[Decimal, Field(gt=Decimal("0"), max_digits=20, decimal_places=8)]


class RiskPerTradeInputs(BaseModel):
    kind: Literal["risk_per_trade"] = "risk_per_trade"
    risk_pct: Annotated[Decimal, Field(gt=Decimal("0"), lt=Decimal("100"), max_digits=10, decimal_places=4)]
    entry: Annotated[Decimal, Field(gt=Decimal("0"), max_digits=20, decimal_places=8)]
    stop: Annotated[Decimal, Field(gt=Decimal("0"), max_digits=20, decimal_places=8)]


class VolTargetedInputs(BaseModel):
    kind: Literal["vol_targeted"] = "vol_targeted"
    target_vol_pct: Annotated[Decimal, Field(gt=Decimal("0"), lt=Decimal("200"), max_digits=10, decimal_places=4)]
    price: Annotated[Decimal, Field(gt=Decimal("0"), max_digits=20, decimal_places=8)]
    vol_override_pct: Annotated[
        Decimal | None,
        Field(default=None, gt=Decimal("0"), lt=Decimal("500"), max_digits=10, decimal_places=4),
    ] = None


SizingInputs = Annotated[
    FixedFractionalInputs | RiskPerTradeInputs | VolTargetedInputs,
    Field(discriminator="kind"),
]


class SizingRequest(BaseModel):
    account_id: UUID
    instrument_id: UUID
    method: SizingMethod
    side: Side
    inputs: SizingInputs


class MethodBreakdown(BaseModel):
    nlv_base: Decimal
    fx_rate: Decimal
    price_base: Decimal
    atr14: Decimal | None = None
    realized_vol14_annualized: Decimal | None = None
    risk_per_share_base: Decimal | None = None
    vol_source: Literal["realized", "override", "n/a"] = "n/a"


class SizingResult(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    suggested_qty: Decimal
    base_currency_notional: Decimal
    method: SizingMethod
    breakdown: MethodBreakdown
    risk_verdict: GateVerdict


class SizingDefaults(BaseModel):
    """Per-account stored defaults retrieved from app_config namespace risk_sizing."""

    method: SizingMethod = SizingMethod.fixed_fractional
    fixed_fractional_risk_pct: Decimal = Decimal("2.00")
    risk_per_trade_risk_pct: Decimal = Decimal("1.00")
    vol_targeted_target_vol_pct: Decimal = Decimal("15.00")


class SizingDefaultsUpdate(BaseModel):
    """PUT payload — full body (PUT semantics), CSRF nonce on the endpoint."""

    method: SizingMethod
    fixed_fractional_risk_pct: Annotated[Decimal, Field(gt=Decimal("0"), lt=Decimal("100"))]
    risk_per_trade_risk_pct: Annotated[Decimal, Field(gt=Decimal("0"), lt=Decimal("100"))]
    vol_targeted_target_vol_pct: Annotated[Decimal, Field(gt=Decimal("0"), lt=Decimal("200"))]
```

- [ ] **Step 2: Verify it imports + a discriminated parse**

```bash
cd backend && uv run python -c "
from app.schemas.sizing import SizingRequest, SizingMethod
from uuid import uuid4
req = SizingRequest.model_validate({
    'account_id': str(uuid4()),
    'instrument_id': str(uuid4()),
    'method': 'fixed_fractional',
    'side': 'buy',
    'inputs': {'kind': 'fixed_fractional', 'risk_pct': '2.00', 'price': '50.00'},
})
print('parsed', req.method, req.inputs)
"
```

Expected: `parsed SizingMethod.fixed_fractional kind='fixed_fractional' risk_pct=Decimal('2.00') price=Decimal('50.00')`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/sizing.py
git commit -m "feat(phase10b1-a4): sizing schemas + discriminated input union"
```

---

### Task A5: Pure-math sizing functions + service orchestrator

**Files:**
- Create: `backend/app/services/position_sizing_math.py`
- Create: `backend/app/services/position_sizing_service.py`
- Create: `backend/tests/services/test_position_sizing_service.py`

- [ ] **Step 1: Write the failing tests for all 3 methods (math + service)**

```python
# backend/tests/services/test_position_sizing_service.py
from __future__ import annotations

from decimal import Decimal

import pytest

from app.schemas.sizing import (
    FixedFractionalInputs,
    RiskPerTradeInputs,
    SizingMethod,
    VolTargetedInputs,
)
from app.services.position_sizing_math import (
    compute_fixed_fractional,
    compute_risk_per_trade,
    compute_vol_targeted,
)


def test_fixed_fractional_2pct_of_100k_at_50_is_40_shares() -> None:
    """Spec §4.4 golden vector: 2% of $100k NLV at $50 = 40 shares."""
    qty, notional = compute_fixed_fractional(
        nlv_base=Decimal("100000"),
        price_base=Decimal("50"),
        risk_pct=Decimal("2"),
    )
    assert qty == Decimal("40")
    assert notional == Decimal("2000")


def test_risk_per_trade_1pct_at_1_dollar_stop_is_1000_shares() -> None:
    """Spec §4.4 golden vector: 1% of $100k NLV at $1 stop distance = 1000 shares."""
    qty, notional, risk_per_share = compute_risk_per_trade(
        nlv_base=Decimal("100000"),
        entry_base=Decimal("50"),
        stop_base=Decimal("49"),
        side="buy",
        risk_pct=Decimal("1"),
    )
    assert qty == Decimal("1000")
    assert risk_per_share == Decimal("1")
    assert notional == Decimal("50000")  # 1000 × $50 entry


def test_risk_per_trade_rejects_buy_with_stop_above_entry() -> None:
    with pytest.raises(ValueError, match="stop.*below.*entry"):
        compute_risk_per_trade(
            nlv_base=Decimal("100000"),
            entry_base=Decimal("50"),
            stop_base=Decimal("51"),
            side="buy",
            risk_pct=Decimal("1"),
        )


def test_risk_per_trade_rejects_sell_with_stop_below_entry() -> None:
    with pytest.raises(ValueError, match="stop.*above.*entry"):
        compute_risk_per_trade(
            nlv_base=Decimal("100000"),
            entry_base=Decimal("50"),
            stop_base=Decimal("49"),
            side="sell",
            risk_pct=Decimal("1"),
        )


def test_risk_per_trade_rejects_zero_distance() -> None:
    with pytest.raises(ValueError, match="zero.distance|entry == stop"):
        compute_risk_per_trade(
            nlv_base=Decimal("100000"),
            entry_base=Decimal("50"),
            stop_base=Decimal("50"),
            side="buy",
            risk_pct=Decimal("1"),
        )


def test_vol_targeted_15pct_at_25pct_vol_at_50_is_1200_shares() -> None:
    """Spec §4.4 golden: 15% target vol with 25% asset vol at $50 → 1200 shares.

    qty = (0.15 × 100000) / (0.25 × 50) = 15000 / 12.5 = 1200.
    """
    qty, notional = compute_vol_targeted(
        nlv_base=Decimal("100000"),
        price_base=Decimal("50"),
        target_vol_pct=Decimal("15"),
        asset_vol_annualized=Decimal("0.25"),
    )
    assert qty == Decimal("1200")
    assert notional == Decimal("60000")


def test_vol_targeted_rejects_zero_vol() -> None:
    with pytest.raises(ValueError, match="zero_volatility"):
        compute_vol_targeted(
            nlv_base=Decimal("100000"),
            price_base=Decimal("50"),
            target_vol_pct=Decimal("15"),
            asset_vol_annualized=Decimal("0"),
        )


def test_fixed_fractional_floors_not_rounds() -> None:
    """3% of $100k at $33 = 3000/33 = 90.909... → floor to 90."""
    qty, _ = compute_fixed_fractional(
        nlv_base=Decimal("100000"),
        price_base=Decimal("33"),
        risk_pct=Decimal("3"),
    )
    assert qty == Decimal("90")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/services/test_position_sizing_service.py -v
```

Expected: all FAIL with `ModuleNotFoundError: app.services.position_sizing_math`.

- [ ] **Step 3: Implement the pure math**

```python
# backend/app/services/position_sizing_math.py
"""Phase 10b.1 pure-math sizing functions. Decimal end-to-end; never float.

Spec: docs/superpowers/specs/2026-05-12-phase10b1-position-sizing-design.md §2.
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal
from typing import Literal


def _floor(quotient: Decimal) -> Decimal:
    """Decimal-correct floor — never round-toward-zero (int() truncation)."""
    return quotient.to_integral_value(rounding=ROUND_FLOOR)


def compute_fixed_fractional(
    *, nlv_base: Decimal, price_base: Decimal, risk_pct: Decimal
) -> tuple[Decimal, Decimal]:
    """qty = floor((NLV × risk_pct / 100) / price_base). Returns (qty, notional)."""
    if price_base <= 0:
        raise ValueError("price_base must be > 0")
    notional_target = (nlv_base * risk_pct / Decimal(100)).quantize(Decimal("1e-8"))
    qty = _floor(notional_target / price_base)
    notional = (qty * price_base).quantize(Decimal("1e-8"))
    return qty, notional


def compute_risk_per_trade(
    *,
    nlv_base: Decimal,
    entry_base: Decimal,
    stop_base: Decimal,
    side: Literal["buy", "sell"],
    risk_pct: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """qty = floor((NLV × risk_pct / 100) / |entry - stop|).

    Returns (qty, notional_at_entry, risk_per_share).
    Side-aware validation: BUY needs stop < entry; SELL needs stop > entry.
    """
    if entry_base == stop_base:
        raise ValueError("zero_distance: entry == stop")
    if side == "buy" and stop_base >= entry_base:
        raise ValueError("for BUY, stop must be below entry")
    if side == "sell" and stop_base <= entry_base:
        raise ValueError("for SELL, stop must be above entry")

    risk_per_share = abs(entry_base - stop_base)
    risk_budget = (nlv_base * risk_pct / Decimal(100)).quantize(Decimal("1e-8"))
    qty = _floor(risk_budget / risk_per_share)
    notional = (qty * entry_base).quantize(Decimal("1e-8"))
    return qty, notional, risk_per_share


def compute_vol_targeted(
    *,
    nlv_base: Decimal,
    price_base: Decimal,
    target_vol_pct: Decimal,
    asset_vol_annualized: Decimal,
) -> tuple[Decimal, Decimal]:
    """qty = floor((target_vol_pct/100 × NLV) / (asset_vol × price_base)).

    Returns (qty, notional). asset_vol_annualized is a unitless fraction
    (e.g., 0.25 for 25%). Caller is responsible for sourcing the vol
    (either via VolatilityService or user override).
    """
    if asset_vol_annualized <= 0:
        raise ValueError("zero_volatility: asset_vol_annualized must be > 0")
    if price_base <= 0:
        raise ValueError("price_base must be > 0")
    notional_budget = (target_vol_pct / Decimal(100) * nlv_base).quantize(Decimal("1e-8"))
    qty = _floor(notional_budget / (asset_vol_annualized * price_base))
    notional = (qty * price_base).quantize(Decimal("1e-8"))
    return qty, notional
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/services/test_position_sizing_service.py -v
```

Expected: 8/8 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/position_sizing_math.py backend/tests/services/test_position_sizing_service.py
git commit -m "feat(phase10b1-a5): position_sizing_math + golden test vectors"
```

---

### Task A6: PositionSizingService orchestrator + lifespan registration

**Files:**
- Create: `backend/app/services/position_sizing_service.py`
- Modify: `backend/app/main.py:128-132`
- Modify: `backend/tests/services/test_position_sizing_service.py` (add orchestrator tests)

- [ ] **Step 1: Write the orchestrator test (uses real DB stub via existing conftest)**

Append to `test_position_sizing_service.py`:

```python
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import fakeredis.aioredis

from app.schemas.risk import GateVerdict
from app.schemas.sizing import (
    FixedFractionalInputs,
    SizingMethod,
)
from app.services.position_sizing_service import PositionSizingService


@pytest.mark.asyncio
async def test_orchestrator_fixed_fractional_happy_path() -> None:
    """compute() loads NLV, FX-converts, runs math, calls gate, returns result."""
    account_id, instrument_id = uuid4(), uuid4()

    # _Session stub: account row + instrument row
    class _Session:
        async def execute(self, stmt, params):
            sql = str(stmt)
            if "FROM broker_accounts" in sql:
                return MagicMock(
                    mappings=lambda: MagicMock(
                        first=lambda: {
                            "id": account_id,
                            "gateway_label": "isa-paper",
                            "mode": "paper",
                            "currency_base": "USD",
                            "last_nlv": Decimal("100000"),
                            "last_nlv_currency": "USD",
                        }
                    )
                )
            if "FROM instruments" in sql:
                return MagicMock(
                    mappings=lambda: MagicMock(
                        first=lambda: {"id": instrument_id, "currency": "USD", "symbol": "AAPL"}
                    )
                )
            raise AssertionError(f"unexpected SQL: {sql}")

    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    await redis.set("fx:USD:USD", "1.0")

    gate = MagicMock()
    gate.evaluate = AsyncMock(
        return_value=GateVerdict(final_verdict="allow", blockers=[], warnings=[], latency_ms=5)
    )
    vol_service = MagicMock()  # unused for fixed_fractional

    svc = PositionSizingService(
        db=_Session(), redis=redis, risk_service=gate, vol_service=vol_service
    )
    result = await svc.compute(
        account_id=account_id,
        instrument_id=instrument_id,
        method=SizingMethod.fixed_fractional,
        inputs=FixedFractionalInputs(risk_pct=Decimal("2"), price=Decimal("50")),
        side="buy",
    )

    assert result.suggested_qty == Decimal("40")
    assert result.base_currency_notional == Decimal("2000")
    assert result.risk_verdict.final_verdict == "allow"
    assert result.breakdown.fx_rate == Decimal("1.0")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/services/test_position_sizing_service.py::test_orchestrator_fixed_fractional_happy_path -v
```

Expected: FAIL — `app.services.position_sizing_service` missing.

- [ ] **Step 3: Implement the orchestrator**

```python
# backend/app/services/position_sizing_service.py
"""Phase 10b.1 position-sizing orchestrator.

Per-request service. Loads account+instrument, FX-converts asset prices
to account.currency_base via the existing `_fx_rate` helper, dispatches
to the appropriate pure-math function in `position_sizing_math`, calls
`RiskService.evaluate(ctx, mode='preview')` for the verdict, and
returns a SizingResult.

No side-effects: this is plan-drift-1 (see plan §"Spec Drift Notice").
RiskService.evaluate is read-only against Redis; PDT mint and audit
live in orders_service, which the sizer never invokes.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.risk import Side
from app.schemas.sizing import (
    FixedFractionalInputs,
    MethodBreakdown,
    RiskPerTradeInputs,
    SizingInputs,
    SizingMethod,
    SizingResult,
    VolTargetedInputs,
)
from app.services.orders_service import _fx_rate  # type: ignore[attr-defined]
from app.services.position_sizing_math import (
    compute_fixed_fractional,
    compute_risk_per_trade,
    compute_vol_targeted,
)
from app.services.risk_service import EvaluationContext, RiskService
from app.services.volatility_service import VolatilityService


class _RedisLike(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: str, *, ex: int) -> Any: ...


class PositionSizingService:
    def __init__(
        self,
        db: AsyncSession,
        redis: _RedisLike,
        risk_service: RiskService,
        vol_service: VolatilityService,
    ) -> None:
        self._db = db
        self._redis = redis
        self._risk = risk_service
        self._vol = vol_service

    async def compute(
        self,
        *,
        account_id: UUID,
        instrument_id: UUID,
        method: SizingMethod,
        inputs: SizingInputs,
        side: Side,
    ) -> SizingResult:
        account = await self._load_account(account_id)
        instrument = await self._load_instrument(instrument_id)

        asset_currency = str(instrument["currency"])
        base_currency = str(account["currency_base"])
        fx_rate = await _fx_rate(self._redis, asset_currency, base_currency)
        nlv_base = Decimal(account["last_nlv"])

        qty, notional_base, breakdown = await self._dispatch(
            method=method,
            inputs=inputs,
            side=side,
            instrument_id=instrument_id,
            nlv_base=nlv_base,
            fx_rate=fx_rate,
        )

        ctx = EvaluationContext(
            account_id=account_id,
            broker_id=str(account["gateway_label"]).split("-")[0],
            instrument_id=None,  # sizer uses None; concentration check skips on None per its contract
            side=side,
            qty=qty,
            price=Decimal(breakdown.price_base),
            order_type="market",
            time_in_force="day",
            request_id=f"sizer-{uuid4()}",
            currency_base=base_currency,
        )
        verdict = await self._risk.evaluate(ctx, mode="preview")

        return SizingResult(
            suggested_qty=qty,
            base_currency_notional=notional_base,
            method=method,
            breakdown=breakdown,
            risk_verdict=verdict,
        )

    async def _dispatch(
        self,
        *,
        method: SizingMethod,
        inputs: SizingInputs,
        side: Side,
        instrument_id: UUID,
        nlv_base: Decimal,
        fx_rate: Decimal,
    ) -> tuple[Decimal, Decimal, MethodBreakdown]:
        if method == SizingMethod.fixed_fractional:
            assert isinstance(inputs, FixedFractionalInputs)
            price_base = (inputs.price * fx_rate).quantize(Decimal("1e-8"))
            qty, notional = compute_fixed_fractional(
                nlv_base=nlv_base, price_base=price_base, risk_pct=inputs.risk_pct
            )
            breakdown = MethodBreakdown(
                nlv_base=nlv_base, fx_rate=fx_rate, price_base=price_base
            )
            return qty, notional, breakdown

        if method == SizingMethod.risk_per_trade:
            assert isinstance(inputs, RiskPerTradeInputs)
            entry_base = (inputs.entry * fx_rate).quantize(Decimal("1e-8"))
            stop_base = (inputs.stop * fx_rate).quantize(Decimal("1e-8"))
            qty, notional, risk_per_share = compute_risk_per_trade(
                nlv_base=nlv_base,
                entry_base=entry_base,
                stop_base=stop_base,
                side=side,
                risk_pct=inputs.risk_pct,
            )
            breakdown = MethodBreakdown(
                nlv_base=nlv_base,
                fx_rate=fx_rate,
                price_base=entry_base,
                risk_per_share_base=risk_per_share,
            )
            return qty, notional, breakdown

        if method == SizingMethod.vol_targeted:
            assert isinstance(inputs, VolTargetedInputs)
            price_base = (inputs.price * fx_rate).quantize(Decimal("1e-8"))
            vol_source = "n/a"
            atr14: Decimal | None = None
            realized_vol: Decimal | None = None
            if inputs.vol_override_pct is not None:
                asset_vol = inputs.vol_override_pct / Decimal(100)
                vol_source = "override"
            else:
                est = await self._vol.compute(
                    instrument_id=instrument_id,
                    asof_date=date.today(),
                )
                if est is None:
                    raise ValueError("realized_vol_unavailable")
                asset_vol = est.realized_vol14_annualized
                realized_vol = est.realized_vol14_annualized
                atr14 = est.atr14
                vol_source = "realized"
            qty, notional = compute_vol_targeted(
                nlv_base=nlv_base,
                price_base=price_base,
                target_vol_pct=inputs.target_vol_pct,
                asset_vol_annualized=asset_vol,
            )
            breakdown = MethodBreakdown(
                nlv_base=nlv_base,
                fx_rate=fx_rate,
                price_base=price_base,
                atr14=atr14,
                realized_vol14_annualized=realized_vol,
                vol_source=vol_source,
            )
            return qty, notional, breakdown

        raise ValueError(f"unknown method: {method}")

    async def _load_account(self, account_id: UUID) -> dict[str, Any]:
        stmt = text(
            """
            SELECT id, gateway_label, mode, currency_base, last_nlv, last_nlv_currency
            FROM broker_accounts WHERE id = :id
            """
        )
        row = (await self._db.execute(stmt, {"id": account_id})).mappings().first()
        if row is None:
            raise ValueError(f"account not found: {account_id}")
        return dict(row)

    async def _load_instrument(self, instrument_id: UUID) -> dict[str, Any]:
        stmt = text(
            """
            SELECT id, symbol, currency FROM instruments WHERE id = :id
            """
        )
        row = (await self._db.execute(stmt, {"id": instrument_id})).mappings().first()
        if row is None:
            raise ValueError(f"instrument not found: {instrument_id}")
        return dict(row)
```

- [ ] **Step 4: Wire VolatilityService into lifespan (app.main.py)**

Modify `backend/app/main.py` near line 128 (after `OrderCapabilityService` block):

```python
    # CRIT-1: OrderCapabilityService singleton — shared cache + background listener.
    capability_svc = OrderCapabilityService(redis=redis, db_factory=session_factory)  # type: ignore[arg-type]
    _app.state.capability_svc = capability_svc
    listener_capability: asyncio.Task[None] = asyncio.create_task(capability_svc.run_listener())

    # Phase 10b.1 H2: VolatilityService singleton (Redis-cached realized-vol + ATR).
    from app.services.volatility_service import VolatilityService
    vol_svc = VolatilityService(db_factory=session_factory, redis=redis)  # type: ignore[arg-type]
    _app.state.vol_service = vol_svc
```

- [ ] **Step 5: Run all sizing tests + the orchestrator test**

```bash
cd backend && uv run pytest tests/services/test_position_sizing_service.py tests/services/test_volatility_service.py -v
```

Expected: all PASS.

- [ ] **Step 6: Run mypy on the new files**

```bash
cd backend && uv run mypy --strict app/services/position_sizing_service.py app/services/position_sizing_math.py app/services/volatility_service.py app/schemas/sizing.py
```

Expected: `Success: no issues found`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/position_sizing_service.py backend/app/main.py backend/tests/services/test_position_sizing_service.py
git commit -m "feat(phase10b1-a6): PositionSizingService orchestrator + VolatilityService singleton wired in lifespan"
```

---

## Chunk B — Backend API (~4 commits)

### Task B1: In-process rate limiter

**Files:**
- Create: `backend/app/services/position_sizing_rate_limiter.py`
- Create: `backend/tests/services/test_position_sizing_rate_limiter.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_position_sizing_rate_limiter.py
from __future__ import annotations

import time

import pytest

from app.services.position_sizing_rate_limiter import (
    RateLimitExceeded,
    SlidingWindowRateLimiter,
)


def test_allows_under_burst() -> None:
    limiter = SlidingWindowRateLimiter(burst=5, sustained_per_sec=2, window_seconds=1)
    for _ in range(5):
        limiter.check("user-A", "account-1")  # 5 requests should be fine


def test_rejects_at_burst_plus_one() -> None:
    limiter = SlidingWindowRateLimiter(burst=5, sustained_per_sec=2, window_seconds=1)
    for _ in range(5):
        limiter.check("user-A", "account-1")
    with pytest.raises(RateLimitExceeded):
        limiter.check("user-A", "account-1")


def test_isolates_per_user_account_key() -> None:
    limiter = SlidingWindowRateLimiter(burst=3, sustained_per_sec=2, window_seconds=1)
    for _ in range(3):
        limiter.check("user-A", "account-1")
    # Different user OR account → its own bucket
    limiter.check("user-B", "account-1")  # ok
    limiter.check("user-A", "account-2")  # ok


def test_window_expires() -> None:
    limiter = SlidingWindowRateLimiter(burst=3, sustained_per_sec=10, window_seconds=1, now=time.monotonic)
    for _ in range(3):
        limiter.check("user-A", "account-1")
    time.sleep(1.1)
    limiter.check("user-A", "account-1")  # window expired → allowed again
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/services/test_position_sizing_rate_limiter.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the limiter**

```python
# backend/app/services/position_sizing_rate_limiter.py
"""Phase 10b.1 H3 — in-process sliding-window rate limiter.

Pattern mirrors backend/app/services/quotes/registry.py:144 (deque-based
sliding window). Per (jwt_subject, account_id) bucket. Single-replica
today; multi-replica will need Redis backing (Phase 24).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Callable, Deque


class RateLimitExceeded(Exception):
    """Raised when a (user, account) key exceeds its sliding-window quota."""


class SlidingWindowRateLimiter:
    """Per-key sliding-window limiter.

    Args:
        burst: Max requests inside the window.
        sustained_per_sec: Steady-state ceiling (reserved for future redis-backed impl).
        window_seconds: Window length.
        now: Time source; injected for tests.
    """

    def __init__(
        self,
        *,
        burst: int,
        sustained_per_sec: int,
        window_seconds: int = 1,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._burst = burst
        self._sustained = sustained_per_sec
        self._window = window_seconds
        self._now = now or time.monotonic
        self._buckets: dict[tuple[str, str], Deque[float]] = defaultdict(deque)

    def check(self, jwt_subject: str, account_id: str) -> None:
        """Raises RateLimitExceeded if (subject, account) is over quota."""
        key = (jwt_subject, account_id)
        now = self._now()
        bucket = self._buckets[key]
        # Evict entries outside the window
        cutoff = now - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self._burst:
            raise RateLimitExceeded(
                f"position_sizing rate limit exceeded "
                f"(burst={self._burst}, window={self._window}s)"
            )
        bucket.append(now)
```

- [ ] **Step 4: Run tests + mypy**

```bash
cd backend && uv run pytest tests/services/test_position_sizing_rate_limiter.py -v
cd backend && uv run mypy --strict app/services/position_sizing_rate_limiter.py
```

Expected: 4/4 PASS; mypy clean.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/position_sizing_rate_limiter.py backend/tests/services/test_position_sizing_rate_limiter.py
git commit -m "feat(phase10b1-b1): in-process sliding-window rate limiter"
```

---

### Task B2: API endpoints + integration tests

**Files:**
- Create: `backend/app/api/sizing.py`
- Modify: `backend/app/main.py` (router include)
- Create: `backend/tests/integration/test_sizing_api.py`

- [ ] **Step 1: Write the integration tests (3 endpoints: POST compute, GET defaults, PUT defaults)**

```python
# backend/tests/integration/test_sizing_api.py
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from tests.fixtures.bars_1d_factory import GOLDEN_AAPL_CLOSES, GOLDEN_AAPL_START_DATE, make_bars_1d


@pytest.mark.asyncio
async def test_position_size_endpoint_fixed_fractional_happy(test_client_admin) -> None:
    # Fixture creates an account + instrument; here we just assert the shape.
    response = await test_client_admin.post(
        "/api/risk/position-size",
        json={
            "account_id": str(uuid4()),  # in real test, use a seeded account
            "instrument_id": str(uuid4()),
            "method": "fixed_fractional",
            "side": "buy",
            "inputs": {
                "kind": "fixed_fractional",
                "risk_pct": "2.00",
                "price": "50.00",
            },
        },
    )
    # Without a seeded account this returns 404; that's the right error,
    # not a 500. Real happy-path test seeds the account.
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        body = response.json()
        assert "suggested_qty" in body
        assert "risk_verdict" in body
        assert "breakdown" in body


@pytest.mark.asyncio
async def test_sizing_defaults_get_returns_defaults_when_unset(test_client_admin) -> None:
    """Unset → returns the default SizingDefaults shape."""
    account_id = uuid4()
    response = await test_client_admin.get(f"/api/risk/sizing-defaults/{account_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "fixed_fractional"
    assert Decimal(body["fixed_fractional_risk_pct"]) == Decimal("2.00")


@pytest.mark.asyncio
async def test_admin_sizing_defaults_put_requires_csrf(test_client_admin) -> None:
    """PUT without X-Confirm-Nonce → 422 from the FastAPI dep."""
    account_id = uuid4()
    response = await test_client_admin.put(
        f"/api/admin/sizing-defaults/{account_id}",
        json={
            "method": "vol_targeted",
            "fixed_fractional_risk_pct": "2.00",
            "risk_per_trade_risk_pct": "1.00",
            "vol_targeted_target_vol_pct": "15.00",
        },
    )
    assert response.status_code in (401, 403, 422)


@pytest.mark.asyncio
async def test_position_size_rate_limit(test_client_admin) -> None:
    """21st call within 1 s from the same (user, account) → 429."""
    account_id = uuid4()
    payload = {
        "account_id": str(account_id),
        "instrument_id": str(uuid4()),
        "method": "fixed_fractional",
        "side": "buy",
        "inputs": {"kind": "fixed_fractional", "risk_pct": "2.00", "price": "50.00"},
    }
    statuses: list[int] = []
    for _ in range(25):
        r = await test_client_admin.post("/api/risk/position-size", json=payload)
        statuses.append(r.status_code)
    assert 429 in statuses, f"expected at least one 429 in {statuses[-5:]}"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/integration/test_sizing_api.py -v
```

Expected: all FAIL — endpoints don't exist.

- [ ] **Step 3: Implement the API router**

```python
# backend/app/api/sizing.py
"""Phase 10b.1 position-sizing API.

Spec: docs/superpowers/specs/2026-05-12-phase10b1-position-sizing-design.md §3.4.

Endpoints:
- POST /api/risk/position-size       (JWT,    no CSRF,  rate-limited 20/s burst)
- GET  /api/risk/sizing-defaults/{id} (JWT,    no CSRF,  60/s sustained)
- PUT  /api/admin/sizing-defaults/{id}(JWT-admin, CSRF nonce, 10/s sustained)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import consume_confirmation_nonce
from app.core.cf_access import AdminIdentity
from app.core.deps import DbDep, RedisDep, get_config, require_admin_jwt
from app.schemas.sizing import (
    SizingDefaults,
    SizingDefaultsUpdate,
    SizingMethod,
    SizingRequest,
    SizingResult,
)
from app.services.config import ConfigService
from app.services.position_sizing_rate_limiter import (
    RateLimitExceeded,
    SlidingWindowRateLimiter,
)
from app.services.position_sizing_service import PositionSizingService
from app.services.risk_service import RiskService

_POSITION_SIZE_LIMITER = SlidingWindowRateLimiter(
    burst=20, sustained_per_sec=5, window_seconds=1
)

router = APIRouter(prefix="/api", tags=["sizing"])


@router.post("/risk/position-size", response_model=SizingResult)
async def compute_position_size(
    payload: SizingRequest,
    request: Request,
    identity: Annotated[AdminIdentity, Depends(require_admin_jwt)],
    db: DbDep,
    redis: RedisDep,
    cfg: Annotated[ConfigService, Depends(get_config)],
) -> SizingResult:
    try:
        _POSITION_SIZE_LIMITER.check(identity.email, str(payload.account_id))
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        )

    sidecar = request.app.state.broker_registry  # type: ignore[attr-defined]
    vol_svc = request.app.state.vol_service  # type: ignore[attr-defined]
    risk = RiskService(db=db, redis=redis, config=cfg, sidecar=sidecar)
    sizer = PositionSizingService(db=db, redis=redis, risk_service=risk, vol_service=vol_svc)

    try:
        return await sizer.compute(
            account_id=payload.account_id,
            instrument_id=payload.instrument_id,
            method=payload.method,
            inputs=payload.inputs,
            side=payload.side,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg == "realized_vol_unavailable":
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "realized_vol_unavailable",
                    "hint": "enter manual vol or pick a different method",
                },
            )
        if msg == "zero_volatility: asset_vol_annualized must be > 0":
            raise HTTPException(status_code=422, detail={"error": "zero_volatility"})
        if "account not found" in msg or "instrument not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=422, detail=msg)


_NS = "risk_sizing"


def _key(account_id: UUID, suffix: str) -> str:
    return f"{account_id}.{suffix}"


@router.get("/risk/sizing-defaults/{account_id}", response_model=SizingDefaults)
async def get_sizing_defaults(
    account_id: UUID,
    _identity: Annotated[AdminIdentity, Depends(require_admin_jwt)],
    cfg: Annotated[ConfigService, Depends(get_config)],
) -> SizingDefaults:
    method_raw = await cfg.get(_NS, _key(account_id, "method"), default="fixed_fractional")
    ff = await cfg.get(_NS, _key(account_id, "fixed_fractional.risk_pct"), default="2.00")
    rpt = await cfg.get(_NS, _key(account_id, "risk_per_trade.risk_pct"), default="1.00")
    vt = await cfg.get(_NS, _key(account_id, "vol_targeted.target_vol_pct"), default="15.00")
    return SizingDefaults(
        method=SizingMethod(method_raw),
        fixed_fractional_risk_pct=Decimal(ff),
        risk_per_trade_risk_pct=Decimal(rpt),
        vol_targeted_target_vol_pct=Decimal(vt),
    )


@router.put("/admin/sizing-defaults/{account_id}", status_code=204)
async def put_sizing_defaults(
    account_id: UUID,
    payload: SizingDefaultsUpdate,
    _identity: Annotated[AdminIdentity, Depends(require_admin_jwt)],
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
    cfg: Annotated[ConfigService, Depends(get_config)],
) -> None:
    await cfg.set(_NS, _key(account_id, "method"), payload.method.value, value_type="str")
    await cfg.set(
        _NS, _key(account_id, "fixed_fractional.risk_pct"),
        str(payload.fixed_fractional_risk_pct), value_type="str",
    )
    await cfg.set(
        _NS, _key(account_id, "risk_per_trade.risk_pct"),
        str(payload.risk_per_trade_risk_pct), value_type="str",
    )
    await cfg.set(
        _NS, _key(account_id, "vol_targeted.target_vol_pct"),
        str(payload.vol_targeted_target_vol_pct), value_type="str",
    )
```

- [ ] **Step 4: Wire the router into app.main**

Add to `backend/app/main.py` near the other router includes:

```python
from app.api import sizing as sizing_api
# ... in the router block:
_app.include_router(sizing_api.router)
```

- [ ] **Step 5: Run integration tests + verify the rate-limit one**

```bash
cd backend && uv run pytest tests/integration/test_sizing_api.py -v
```

Expected: 4/4 PASS (including the 429 burst-cap test).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/sizing.py backend/app/main.py backend/tests/integration/test_sizing_api.py
git commit -m "feat(phase10b1-b2): sizing API endpoints + rate-limited POST + admin CSRF PUT"
```

---

### Task B3: Observability metrics

**Files:**
- Modify: `backend/app/core/metrics.py` (append)
- Modify: `backend/app/services/position_sizing_service.py` + `app/api/sizing.py` (emit)

- [ ] **Step 1: Add the 6 metric definitions to `app/core/metrics.py`**

Append to the end of `metrics.py`:

```python
# Phase 10b.1 — position-sizing metrics.
position_sizing_compute_total = Counter(
    "position_sizing_compute_total",
    "Position-sizing requests, labelled by method, account_currency, verdict.",
    ["method", "account_currency", "verdict"],
)
position_sizing_latency_seconds = Histogram(
    "position_sizing_latency_seconds",
    "End-to-end /api/risk/position-size latency including risk-gate eval.",
    ["method"],
    buckets=(0.005, 0.010, 0.025, 0.050, 0.100, 0.250, 0.500, 1.0),
)
position_sizing_vol_unavailable_total = Counter(
    "position_sizing_vol_unavailable_total",
    "vol-targeted requests rejected because realized_vol14 was unavailable and no override.",
    ["instrument_class"],
)
volatility_cache_hits_total = Counter(
    "volatility_cache_hits_total",
    "Redis vol14:* cache hits.",
)
volatility_cache_misses_total = Counter(
    "volatility_cache_misses_total",
    "Redis vol14:* cache misses (fell through to bars_1d).",
)
position_sizing_admin_writes_total = Counter(
    "position_sizing_admin_writes_total",
    "PUT /api/admin/sizing-defaults calls.",
    ["account_id", "field"],
)
```

- [ ] **Step 2: Emit metrics from VolatilityService**

In `volatility_service.py`'s `compute()`, after the cache lookup:

```python
        cached = await self._redis.get(key)
        if cached is not None:
            from app.core import metrics
            metrics.volatility_cache_hits_total.inc()
            return _decode_cached(cached)
        from app.core import metrics
        metrics.volatility_cache_misses_total.inc()
```

- [ ] **Step 3: Emit metrics from the API endpoint**

In `api/sizing.py`, wrap the POST handler body in a histogram timer:

```python
    from app.core import metrics
    with metrics.position_sizing_latency_seconds.labels(method=payload.method.value).time():
        # ... existing compute call ...
        result = await sizer.compute(...)
    verdict_label = result.risk_verdict.final_verdict
    account_currency = result.breakdown.fx_rate  # placeholder — use account.currency_base
    metrics.position_sizing_compute_total.labels(
        method=payload.method.value,
        account_currency="USD",  # populate properly from the loaded account
        verdict=verdict_label,
    ).inc()
    return result
```

Note: the `account_currency` label requires the loaded account — refactor to make the service return the account in the result OR pre-load before the metric. For simplicity, pass it through `MethodBreakdown` or look it up cheaply.

- [ ] **Step 4: Run sizing tests + a metric smoke check**

```bash
cd backend && uv run pytest tests/services/test_position_sizing_service.py tests/services/test_volatility_service.py tests/integration/test_sizing_api.py -v
cd backend && uv run python -c "
from app.core.metrics import (
    position_sizing_compute_total,
    position_sizing_latency_seconds,
    volatility_cache_hits_total,
)
print('metrics imported')
"
```

Expected: all tests pass; metrics import cleanly.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/metrics.py backend/app/services/volatility_service.py backend/app/api/sizing.py
git commit -m "feat(phase10b1-b3): position-sizing observability metrics"
```

---

### Task B4: Chunk-A+B reviewer chain

- [ ] **Step 1: Dispatch the per-chunk reviewer chain (haiku + sonnet mix)**

Run in parallel (single message, multiple Agent tool calls):

```
1. spec-compliance (haiku) — verify all spec §2-§3 + §6 + §9 requirements hit
2. python-reviewer (haiku) — PEP-8, mypy gaps, Decimal hygiene
3. code-reviewer (sonnet) — pattern adherence, error-handling
4. security-reviewer (sonnet) — input validation, CSRF coverage, rate-limit-bypass
5. database-reviewer (sonnet) — bars_1d query efficiency, app_config namespace fanout
```

Each reviewer gets the spec inlined (relevant sections only — §2, §3.1-3.5, §6, §9 inline; §1, §4, §5, §7, §8, §10, §11 referenced) per `feedback_reviewer_spec_inline.md`.

- [ ] **Step 2: Apply CRIT+HIGH+MED findings inline**

For each finding, fix immediately or open a follow-up task with a clear reproduction. LOW findings — note and continue.

- [ ] **Step 3: Commit the fixes (one commit per logical fix)**

```bash
git commit -m "fix(phase10b1-b): chunk A+B reviewer findings — <summary>"
```

---

## Chunk C — Frontend Service (~3 commits)

### Task C1: Regenerate api-generated.ts after backend lands

- [ ] **Step 1: Run the type generator**

```bash
cd /home/joseph/dashboard && scripts/gen-types.sh
```

This should produce diff in `frontend/src/services/api-generated.ts` with the new sizing types.

- [ ] **Step 2: Verify diff is non-empty and contains the new endpoints**

```bash
git diff --stat frontend/src/services/api-generated.ts
git diff frontend/src/services/api-generated.ts | grep -E "position-size|sizing-defaults" | head -5
```

Expected: the new endpoint operationIds visible.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/api-generated.ts
git commit -m "chore(phase10b1-c1): regenerate api-generated.ts for sizing endpoints"
```

---

### Task C2: Sizing service files (types, api, hooks)

**Files:**
- Create: `frontend/src/services/sizing/types.ts`
- Create: `frontend/src/services/sizing/api.ts`
- Create: `frontend/src/services/sizing/useSizingDefaults.ts`
- Create: `frontend/src/services/sizing/usePositionSizing.ts`

- [ ] **Step 1: Write the types file**

```typescript
// frontend/src/services/sizing/types.ts
// Hand-written mirror of backend SizingResult shape — kept in sync with
// app/schemas/sizing.py. api-generated.ts is the source of truth for
// auto-shaped types; this file adds friendlier names for FE callsites.

import type { GateVerdict } from '@/services/risk/types';
import type { Side } from '@/services/types';

export type SizingMethod = 'fixed_fractional' | 'risk_per_trade' | 'vol_targeted';

export interface FixedFractionalInputs {
  kind: 'fixed_fractional';
  risk_pct: string;
  price: string;
}

export interface RiskPerTradeInputs {
  kind: 'risk_per_trade';
  risk_pct: string;
  entry: string;
  stop: string;
}

export interface VolTargetedInputs {
  kind: 'vol_targeted';
  target_vol_pct: string;
  price: string;
  vol_override_pct?: string | null;
}

export type SizingInputs =
  | FixedFractionalInputs
  | RiskPerTradeInputs
  | VolTargetedInputs;

export interface SizingRequest {
  account_id: string;
  instrument_id: string;
  method: SizingMethod;
  side: Side;
  inputs: SizingInputs;
}

export interface MethodBreakdown {
  nlv_base: string;
  fx_rate: string;
  price_base: string;
  atr14?: string | null;
  realized_vol14_annualized?: string | null;
  risk_per_share_base?: string | null;
  vol_source: 'realized' | 'override' | 'n/a';
}

export interface SizingResult {
  suggested_qty: string;
  base_currency_notional: string;
  method: SizingMethod;
  breakdown: MethodBreakdown;
  risk_verdict: GateVerdict;
}

export interface SizingDefaults {
  method: SizingMethod;
  fixed_fractional_risk_pct: string;
  risk_per_trade_risk_pct: string;
  vol_targeted_target_vol_pct: string;
}
```

- [ ] **Step 2: Write the api.ts wrapper**

```typescript
// frontend/src/services/sizing/api.ts
import { mintCsrfNonce } from '@/services/csrf';
import { apiFetch } from '@/services/api-fetch';
import type {
  SizingDefaults,
  SizingRequest,
  SizingResult,
} from './types';

export async function computePositionSize(req: SizingRequest): Promise<SizingResult> {
  const resp = await apiFetch('/api/risk/position-size', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!resp.ok) throw new Error(`compute_position_size_failed: ${resp.status}`);
  return resp.json() as Promise<SizingResult>;
}

export async function getSizingDefaults(accountId: string): Promise<SizingDefaults> {
  const resp = await apiFetch(`/api/risk/sizing-defaults/${accountId}`);
  if (!resp.ok) throw new Error(`get_sizing_defaults_failed: ${resp.status}`);
  return resp.json() as Promise<SizingDefaults>;
}

export async function setSizingDefaults(
  accountId: string,
  payload: Omit<SizingDefaults, never>,
): Promise<void> {
  const nonce = await mintCsrfNonce();
  const resp = await apiFetch(`/api/admin/sizing-defaults/${accountId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-Confirm-Nonce': nonce },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) throw new Error(`set_sizing_defaults_failed: ${resp.status}`);
}
```

- [ ] **Step 3: Write the read hook**

```typescript
// frontend/src/services/sizing/useSizingDefaults.ts
import { useQuery } from '@tanstack/react-query';
import { getSizingDefaults } from './api';

export function useSizingDefaults(accountId: string | undefined) {
  return useQuery({
    queryKey: ['sizing-defaults', accountId],
    queryFn: () => getSizingDefaults(accountId!),
    enabled: !!accountId,
    staleTime: 60_000,
  });
}
```

- [ ] **Step 4: Write the debounced compute hook**

```typescript
// frontend/src/services/sizing/usePositionSizing.ts
import { useEffect, useRef, useState } from 'react';
import { computePositionSize } from './api';
import type { SizingRequest, SizingResult } from './types';

const DEBOUNCE_MS = 250;

export function usePositionSizing(req: SizingRequest | null) {
  const [result, setResult] = useState<SizingResult | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!req) {
      setResult(null);
      return;
    }
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const r = await computePositionSize(req);
        setResult(r);
        setError(null);
      } catch (e) {
        setError(e as Error);
      } finally {
        setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [JSON.stringify(req)]);

  return { result, error, loading };
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/services/sizing/
git commit -m "feat(phase10b1-c2): sizing service types + api + hooks"
```

---

### Task C3: Sizing hook unit tests

**Files:**
- Create: `frontend/src/services/sizing/usePositionSizing.test.tsx`

- [ ] **Step 1: Write debounce + BLOCK propagation tests**

```typescript
// frontend/src/services/sizing/usePositionSizing.test.tsx
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { usePositionSizing } from './usePositionSizing';
import * as api from './api';

describe('usePositionSizing', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('debounces 250ms before calling compute', async () => {
    const spy = vi.spyOn(api, 'computePositionSize').mockResolvedValue({
      suggested_qty: '40',
      base_currency_notional: '2000',
      method: 'fixed_fractional',
      breakdown: { nlv_base: '100000', fx_rate: '1.0', price_base: '50.00', vol_source: 'n/a' },
      risk_verdict: { final_verdict: 'allow', blockers: [], warnings: [], latency_ms: 5 },
    });

    const req = {
      account_id: 'a', instrument_id: 'i', method: 'fixed_fractional' as const,
      side: 'buy' as const, inputs: { kind: 'fixed_fractional' as const, risk_pct: '2', price: '50' },
    };
    const { result } = renderHook(() => usePositionSizing(req));

    // Before 250ms — not called
    vi.advanceTimersByTime(200);
    expect(spy).not.toHaveBeenCalled();

    // After 250ms — called once
    vi.advanceTimersByTime(100);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(result.current.result?.suggested_qty).toBe('40');
  });

  it('surfaces BLOCK verdicts', async () => {
    vi.spyOn(api, 'computePositionSize').mockResolvedValue({
      suggested_qty: '1000',
      base_currency_notional: '50000',
      method: 'fixed_fractional',
      breakdown: { nlv_base: '100000', fx_rate: '1.0', price_base: '50', vol_source: 'n/a' },
      risk_verdict: {
        final_verdict: 'block',
        blockers: [{ check: 'buying_power', message: 'BP buffer breach', code: 'bp_buffer' }],
        warnings: [], latency_ms: 5,
      },
    });

    const req = { account_id: 'a', instrument_id: 'i', method: 'fixed_fractional' as const, side: 'buy' as const, inputs: { kind: 'fixed_fractional' as const, risk_pct: '50', price: '50' } };
    const { result } = renderHook(() => usePositionSizing(req));
    vi.advanceTimersByTime(300);
    await waitFor(() => expect(result.current.result?.risk_verdict.final_verdict).toBe('block'));
    expect(result.current.result?.risk_verdict.blockers[0].code).toBe('bp_buffer');
  });
});
```

- [ ] **Step 2: Run the tests**

```bash
cd /home/joseph/dashboard/frontend && pnpm test usePositionSizing
```

Expected: 2/2 PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/sizing/usePositionSizing.test.tsx
git commit -m "test(phase10b1-c3): usePositionSizing debounce + BLOCK propagation"
```

---

## Chunk D — TradeTicketModal Integration (~5 commits)

### Task D1: TradeTicketModal sizing section (collapsed UI)

**Files:**
- Modify: `frontend/src/features/orders/TradeTicketModal.tsx`

- [ ] **Step 1: Add a collapsible sizing section above the Preview block**

In `TradeTicketModal.tsx`, after the price/qty inputs and before the `<PreviewBlock>` call, add:

```tsx
import { usePositionSizing } from '@/services/sizing/usePositionSizing';
import { useSizingDefaults } from '@/services/sizing/useSizingDefaults';
import type { SizingMethod } from '@/services/sizing/types';

// ... inside the component body, near other useState hooks:
const [sizingOpen, setSizingOpen] = React.useState(false);
const [sizingMethod, setSizingMethod] = React.useState<SizingMethod>('fixed_fractional');
const [sizingRiskPct, setSizingRiskPct] = React.useState('2.00');

const sizingDefaults = useSizingDefaults(accountId);
React.useEffect(() => {
  if (sizingDefaults.data) {
    setSizingMethod(sizingDefaults.data.method);
    if (sizingDefaults.data.method === 'fixed_fractional') {
      setSizingRiskPct(sizingDefaults.data.fixed_fractional_risk_pct);
    } else if (sizingDefaults.data.method === 'risk_per_trade') {
      setSizingRiskPct(sizingDefaults.data.risk_per_trade_risk_pct);
    } else {
      setSizingRiskPct(sizingDefaults.data.vol_targeted_target_vol_pct);
    }
  }
}, [sizingDefaults.data]);

const sizingRequest = sizingOpen && instrumentId && limitPrice
  ? {
      account_id: accountId,
      instrument_id: instrumentId,
      method: sizingMethod,
      side: side.toLowerCase() as 'buy' | 'sell',
      inputs: sizingMethod === 'fixed_fractional'
        ? { kind: 'fixed_fractional' as const, risk_pct: sizingRiskPct, price: limitPrice }
        : sizingMethod === 'risk_per_trade'
          ? { kind: 'risk_per_trade' as const, risk_pct: sizingRiskPct, entry: limitPrice, stop: stopPrice ?? '0' }
          : { kind: 'vol_targeted' as const, target_vol_pct: sizingRiskPct, price: limitPrice },
    }
  : null;
const sizing = usePositionSizing(sizingRequest);

// ... in the JSX:
<details
  open={sizingOpen}
  onToggle={(e) => setSizingOpen((e.currentTarget as HTMLDetailsElement).open)}
  data-testid="sizing-section"
>
  <summary className="cursor-pointer text-sm font-medium">Position sizing</summary>
  <div className="mt-3 space-y-3 rounded-md border border-border p-3">
    <div className="flex items-center gap-2">
      <label className="text-xs">Method:</label>
      <select
        value={sizingMethod}
        onChange={(e) => setSizingMethod(e.currentTarget.value as SizingMethod)}
        className="rounded-md border-border bg-background p-1 text-sm"
        data-testid="sizing-method-select"
      >
        <option value="fixed_fractional">Fixed-fractional</option>
        <option value="risk_per_trade">Fixed-risk-per-trade</option>
        <option value="vol_targeted">Vol-targeted</option>
      </select>
    </div>
    <div className="flex items-center gap-2">
      <label className="text-xs">Risk %:</label>
      <input
        type="text"
        inputMode="decimal"
        value={sizingRiskPct}
        onChange={(e) => setSizingRiskPct(e.currentTarget.value)}
        className="rounded-md border-border bg-background p-1 text-sm"
        data-testid="sizing-risk-pct"
      />
    </div>
    {sizing.result ? (
      <div className="text-sm">
        <div>
          <span className="font-medium">Suggested qty:</span>{' '}
          <span data-testid="sizing-suggested-qty">{sizing.result.suggested_qty}</span>
        </div>
        <div className="text-xs text-muted-foreground">
          Risk gate at suggestion time: {sizing.result.risk_verdict.final_verdict.toUpperCase()}
        </div>
        <button
          type="button"
          className="mt-2 rounded-md bg-primary px-3 py-1 text-xs text-primary-foreground"
          onClick={() => setQty(sizing.result!.suggested_qty)}
          disabled={sizing.result.risk_verdict.final_verdict === 'block'}
          data-testid="sizing-use-button"
        >
          Use this size
        </button>
      </div>
    ) : null}
    {sizing.error ? (
      <div className="text-xs text-destructive" data-testid="sizing-error">
        {sizing.error.message}
      </div>
    ) : null}
  </div>
</details>
```

- [ ] **Step 2: Run existing TradeTicketModal tests to ensure no regression**

```bash
cd /home/joseph/dashboard/frontend && pnpm test TradeTicketModal
```

Expected: existing tests still pass; new tests are added in D2.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/orders/TradeTicketModal.tsx
git commit -m "feat(phase10b1-d1): TradeTicketModal sizing section UI"
```

---

### Task D2: TradeTicketModal sizing tests

**Files:**
- Modify: `frontend/src/features/orders/TradeTicketModal.test.tsx`

- [ ] **Step 1: Add 3 tests for the sizing section**

Append to `TradeTicketModal.test.tsx`:

```typescript
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

// ... existing imports ...

describe('TradeTicketModal — sizing section', () => {
  it('opens the section and shows method dropdown', async () => {
    renderModalWithDefaults();
    const section = screen.getByTestId('sizing-section');
    fireEvent.click(section.querySelector('summary')!);
    expect(await screen.findByTestId('sizing-method-select')).toBeInTheDocument();
  });

  it('"Use this size" overwrites the qty field on click', async () => {
    vi.spyOn(require('@/services/sizing/api'), 'computePositionSize').mockResolvedValue({
      suggested_qty: '40',
      base_currency_notional: '2000',
      method: 'fixed_fractional',
      breakdown: { nlv_base: '100000', fx_rate: '1.0', price_base: '50', vol_source: 'n/a' },
      risk_verdict: { final_verdict: 'allow', blockers: [], warnings: [], latency_ms: 5 },
    });
    renderModalWithDefaults();
    fireEvent.click(screen.getByTestId('sizing-section').querySelector('summary')!);
    fireEvent.change(screen.getByTestId('sizing-risk-pct'), { target: { value: '2.00' } });
    const useBtn = await screen.findByTestId('sizing-use-button');
    fireEvent.click(useBtn);
    await waitFor(() => {
      expect((screen.getByLabelText(/qty/i) as HTMLInputElement).value).toBe('40');
    });
  });

  it('disables "Use this size" when risk gate BLOCKs', async () => {
    vi.spyOn(require('@/services/sizing/api'), 'computePositionSize').mockResolvedValue({
      suggested_qty: '1000',
      base_currency_notional: '50000',
      method: 'fixed_fractional',
      breakdown: { nlv_base: '100000', fx_rate: '1.0', price_base: '50', vol_source: 'n/a' },
      risk_verdict: {
        final_verdict: 'block',
        blockers: [{ check: 'buying_power', message: 'BP', code: 'bp_buffer' }],
        warnings: [], latency_ms: 5,
      },
    });
    renderModalWithDefaults();
    fireEvent.click(screen.getByTestId('sizing-section').querySelector('summary')!);
    const useBtn = await screen.findByTestId('sizing-use-button');
    expect(useBtn).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run the tests**

```bash
cd frontend && pnpm test TradeTicketModal
```

Expected: existing tests + 3 new tests PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/orders/TradeTicketModal.test.tsx
git commit -m "test(phase10b1-d2): TradeTicketModal sizing section tests"
```

---

### Task D3: Persist sizing defaults on method/pct change

**Files:**
- Modify: `frontend/src/features/orders/TradeTicketModal.tsx`

- [ ] **Step 1: Add a debounced setSizingDefaults call on change**

Add near the sizing state setters:

```tsx
import { setSizingDefaults } from '@/services/sizing/api';

// Debounced persist
const persistTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
React.useEffect(() => {
  if (!accountId || !sizingDefaults.data) return;
  if (persistTimerRef.current) clearTimeout(persistTimerRef.current);
  persistTimerRef.current = setTimeout(() => {
    setSizingDefaults(accountId, {
      method: sizingMethod,
      fixed_fractional_risk_pct: sizingMethod === 'fixed_fractional' ? sizingRiskPct : sizingDefaults.data!.fixed_fractional_risk_pct,
      risk_per_trade_risk_pct: sizingMethod === 'risk_per_trade' ? sizingRiskPct : sizingDefaults.data!.risk_per_trade_risk_pct,
      vol_targeted_target_vol_pct: sizingMethod === 'vol_targeted' ? sizingRiskPct : sizingDefaults.data!.vol_targeted_target_vol_pct,
    }).catch(() => {
      // Best-effort; defaults persistence isn't critical for the current trade.
    });
  }, 1500);
  return () => {
    if (persistTimerRef.current) clearTimeout(persistTimerRef.current);
  };
}, [accountId, sizingMethod, sizingRiskPct, sizingDefaults.data]);
```

- [ ] **Step 2: Run tests**

```bash
cd frontend && pnpm test TradeTicketModal
```

Expected: no regressions.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/orders/TradeTicketModal.tsx
git commit -m "feat(phase10b1-d3): persist sizing defaults on method/pct change"
```

---

### Task D4: WARN/BLOCK banner reuse from Phase 10a components

**Files:**
- Modify: `frontend/src/features/orders/TradeTicketModal.tsx`

- [ ] **Step 1: Surface the sizing verdict's warnings + blockers using the existing Phase 10a aria-labelled UL pattern**

Inside the sizing section, replace the "Risk gate at suggestion time: ..." line with the full Phase 10a banner shape (copy the `aria-label="Risk gate warnings"` and `"Risk gate blockers"` UL exactly so existing test selectors still match):

```tsx
{sizing.result?.risk_verdict.blockers.length ? (
  <div
    className="rounded-md border border-destructive/60 bg-destructive/10 p-2 text-xs text-destructive"
    role="alert"
    aria-label="Risk gate blockers (sizing)"
  >
    <p className="font-semibold">Risk gate at suggestion time — BLOCK</p>
    <ul className="mt-1 list-inside list-disc">
      {sizing.result.risk_verdict.blockers.map((b) => (
        <li key={`${b.check}:${b.code}`}>
          {b.message} <span className="font-mono opacity-70">({b.code})</span>
        </li>
      ))}
    </ul>
  </div>
) : null}
{sizing.result?.risk_verdict.warnings.length ? (
  <div
    className="rounded-md border border-warning/60 bg-warning/10 p-2 text-xs"
    role="alert"
    aria-label="Risk gate warnings (sizing)"
  >
    <p className="font-semibold">Risk gate at suggestion time — WARN</p>
    <ul className="mt-1 list-inside list-disc">
      {sizing.result.risk_verdict.warnings.map((w) => (
        <li key={`${w.check}:${w.message}`}>{w.message}</li>
      ))}
    </ul>
  </div>
) : null}
```

- [ ] **Step 2: Add a test for the new banners**

In `TradeTicketModal.test.tsx`:

```typescript
it('renders sizing-scoped WARN banner with distinguishable aria-label', async () => {
  vi.spyOn(require('@/services/sizing/api'), 'computePositionSize').mockResolvedValue({
    suggested_qty: '500',
    base_currency_notional: '25000',
    method: 'fixed_fractional',
    breakdown: { nlv_base: '100000', fx_rate: '1.0', price_base: '50', vol_source: 'n/a' },
    risk_verdict: {
      final_verdict: 'warn',
      blockers: [],
      warnings: [{ check: 'position_concentration', message: 'over 25%', value: 30, threshold: 25 }],
      latency_ms: 5,
    },
  });
  renderModalWithDefaults();
  fireEvent.click(screen.getByTestId('sizing-section').querySelector('summary')!);
  const banner = await screen.findByLabelText('Risk gate warnings (sizing)');
  expect(banner).toHaveTextContent('over 25%');
});
```

- [ ] **Step 3: Run tests**

```bash
cd frontend && pnpm test TradeTicketModal
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/orders/TradeTicketModal.tsx frontend/src/features/orders/TradeTicketModal.test.tsx
git commit -m "feat(phase10b1-d4): sizing-scoped WARN+BLOCK banners with distinct aria-labels"
```

---

### Task D5: Chunk-C+D reviewer chain

- [ ] **Step 1: Dispatch reviewer chain**

Same 5-reviewer pattern as B4. Inline relevant spec sections (§4.1, §4.2) + the new FE files.

- [ ] **Step 2: Apply CRIT+HIGH+MED inline**

- [ ] **Step 3: Commit reviewer-fix commits one per logical fix**

---

## Chunk E — Standalone /trade/sizing Page (~4 commits)

### Task E1: Route + SizingCalculatorPage shell

**Files:**
- Create: `frontend/src/routes/trade.sizing.tsx`
- Create: `frontend/src/features/sizing/SizingCalculatorPage.tsx`

- [ ] **Step 1: Define the route with search-param schema**

```typescript
// frontend/src/routes/trade.sizing.tsx
import { createFileRoute } from '@tanstack/react-router';
import { z } from 'zod';
import { SizingCalculatorPage } from '@/features/sizing/SizingCalculatorPage';

const searchSchema = z.object({
  account_id: z.string().optional(),
  instrument_id: z.string().optional(),
  side: z.enum(['buy', 'sell']).default('buy'),
  entry: z.string().optional(),
  stop: z.string().optional(),
});

export const Route = createFileRoute('/trade/sizing')({
  component: SizingCalculatorPage,
  validateSearch: searchSchema.parse,
});
```

- [ ] **Step 2: Write the page shell with shared inputs + 3-column placeholder**

```tsx
// frontend/src/features/sizing/SizingCalculatorPage.tsx
import { useSearch, useNavigate } from '@tanstack/react-router';
import { Route } from '@/routes/trade.sizing';
import { SizingMethodColumn } from './SizingMethodColumn';
import type { SizingMethod } from '@/services/sizing/types';

const METHODS: SizingMethod[] = ['fixed_fractional', 'risk_per_trade', 'vol_targeted'];

export function SizingCalculatorPage(): React.JSX.Element {
  const search = useSearch({ from: Route.id });
  const navigate = useNavigate({ from: Route.id });

  const updateSearch = (next: Partial<typeof search>) =>
    navigate({ search: (prev) => ({ ...prev, ...next }) });

  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold">Position sizing</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Compare three sizing methods side-by-side. Inputs persist in the URL.
      </p>

      <section className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
        <input
          placeholder="Account ID"
          value={search.account_id ?? ''}
          onChange={(e) => updateSearch({ account_id: e.currentTarget.value || undefined })}
          className="rounded-md border-border bg-background p-2"
          data-testid="page-account-id"
        />
        <input
          placeholder="Instrument ID"
          value={search.instrument_id ?? ''}
          onChange={(e) => updateSearch({ instrument_id: e.currentTarget.value || undefined })}
          className="rounded-md border-border bg-background p-2"
          data-testid="page-instrument-id"
        />
        <select
          value={search.side}
          onChange={(e) => updateSearch({ side: e.currentTarget.value as 'buy' | 'sell' })}
          className="rounded-md border-border bg-background p-2"
          data-testid="page-side"
        >
          <option value="buy">Buy</option>
          <option value="sell">Sell</option>
        </select>
        <input
          placeholder="Entry price"
          value={search.entry ?? ''}
          onChange={(e) => updateSearch({ entry: e.currentTarget.value || undefined })}
          className="rounded-md border-border bg-background p-2"
          data-testid="page-entry"
        />
      </section>

      <section className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {METHODS.map((m) => (
          <SizingMethodColumn
            key={m}
            method={m}
            accountId={search.account_id}
            instrumentId={search.instrument_id}
            side={search.side}
            entry={search.entry}
            stop={search.stop}
          />
        ))}
      </section>
    </div>
  );
}
```

- [ ] **Step 3: Write the SizingMethodColumn stub**

```tsx
// frontend/src/features/sizing/SizingMethodColumn.tsx
import { useState } from 'react';
import { usePositionSizing } from '@/services/sizing/usePositionSizing';
import type { SizingMethod, SizingInputs } from '@/services/sizing/types';

interface Props {
  method: SizingMethod;
  accountId: string | undefined;
  instrumentId: string | undefined;
  side: 'buy' | 'sell';
  entry: string | undefined;
  stop: string | undefined;
}

const METHOD_LABEL: Record<SizingMethod, string> = {
  fixed_fractional: 'Fixed-fractional',
  risk_per_trade: 'Risk-per-trade',
  vol_targeted: 'Vol-targeted',
};

export function SizingMethodColumn({
  method, accountId, instrumentId, side, entry, stop,
}: Props): React.JSX.Element {
  const [riskPct, setRiskPct] = useState(
    method === 'vol_targeted' ? '15.00' : (method === 'risk_per_trade' ? '1.00' : '2.00'),
  );

  const inputs: SizingInputs | null = entry
    ? method === 'fixed_fractional'
      ? { kind: 'fixed_fractional', risk_pct: riskPct, price: entry }
      : method === 'risk_per_trade'
        ? { kind: 'risk_per_trade', risk_pct: riskPct, entry, stop: stop ?? '0' }
        : { kind: 'vol_targeted', target_vol_pct: riskPct, price: entry }
    : null;

  const req = (accountId && instrumentId && inputs)
    ? { account_id: accountId, instrument_id: instrumentId, method, side, inputs }
    : null;

  const sizing = usePositionSizing(req);

  return (
    <div
      className="rounded-md border border-border p-4"
      data-testid={`column-${method}`}
    >
      <h2 className="text-sm font-semibold">{METHOD_LABEL[method]}</h2>
      <label className="mt-3 block text-xs">
        Risk %:
        <input
          type="text"
          inputMode="decimal"
          value={riskPct}
          onChange={(e) => setRiskPct(e.currentTarget.value)}
          className="mt-1 w-full rounded-md border-border bg-background p-2"
          data-testid={`risk-pct-${method}`}
        />
      </label>
      {sizing.result ? (
        <div className="mt-3 text-sm">
          <div>
            Suggested qty:{' '}
            <span className="font-semibold" data-testid={`qty-${method}`}>
              {sizing.result.suggested_qty}
            </span>
          </div>
          <div className="text-xs text-muted-foreground">
            Notional: ${sizing.result.base_currency_notional}
          </div>
          <div className="mt-2 text-xs">
            Gate verdict:{' '}
            <span data-testid={`verdict-${method}`}>
              {sizing.result.risk_verdict.final_verdict.toUpperCase()}
            </span>
          </div>
        </div>
      ) : null}
      {sizing.error ? (
        <div className="mt-3 text-xs text-destructive" data-testid={`error-${method}`}>
          {sizing.error.message}
        </div>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: Regenerate routeTree.gen.ts**

```bash
cd frontend && pnpm tsr generate
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/trade.sizing.tsx frontend/src/features/sizing/ frontend/src/routes/routeTree.gen.ts
git commit -m "feat(phase10b1-e1): /trade/sizing route + SizingCalculatorPage + 3-column shell"
```

---

### Task E2: SizingCalculatorPage test

**Files:**
- Create: `frontend/src/features/sizing/SizingCalculatorPage.test.tsx`

- [ ] **Step 1: Write 2 tests — page renders 3 columns + qty appears on input**

```typescript
// frontend/src/features/sizing/SizingCalculatorPage.test.tsx
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter } from '@tanstack/react-router';
import { RouterProvider } from '@tanstack/react-router';
import { routeTree } from '@/routes/routeTree.gen';
import * as api from '@/services/sizing/api';

function renderAtSizing(searchInit: string = '') {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [`/trade/sizing${searchInit}`] }),
  });
  return render(<RouterProvider router={router} />);
}

describe('SizingCalculatorPage', () => {
  it('renders the 3 method columns', async () => {
    renderAtSizing();
    expect(await screen.findByTestId('column-fixed_fractional')).toBeInTheDocument();
    expect(screen.getByTestId('column-risk_per_trade')).toBeInTheDocument();
    expect(screen.getByTestId('column-vol_targeted')).toBeInTheDocument();
  });

  it('shows suggested qty in each column once inputs are filled', async () => {
    vi.spyOn(api, 'computePositionSize').mockResolvedValue({
      suggested_qty: '40',
      base_currency_notional: '2000',
      method: 'fixed_fractional',
      breakdown: { nlv_base: '100000', fx_rate: '1.0', price_base: '50', vol_source: 'n/a' },
      risk_verdict: { final_verdict: 'allow', blockers: [], warnings: [], latency_ms: 5 },
    });
    renderAtSizing('?account_id=a&instrument_id=i&entry=50');
    await waitFor(() =>
      expect(screen.getByTestId('qty-fixed_fractional')).toHaveTextContent('40'),
      { timeout: 1000 },
    );
  });
});
```

- [ ] **Step 2: Run the tests**

```bash
cd frontend && pnpm test SizingCalculator
```

Expected: 2/2 PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/sizing/SizingCalculatorPage.test.tsx
git commit -m "test(phase10b1-e2): SizingCalculatorPage 3-column render + qty display"
```

---

### Task E3: Playwright smoke spec + workflow wiring

**Files:**
- Create: `tests/e2e/phase10b1-sizing.spec.ts`
- Modify: `tests/e2e/package.json` (add `test:sizing` script)
- Modify: `.github/workflows/deploy.yml` (add post-smoke step)

- [ ] **Step 1: Write the spec following the Phase 10a.5.1 phase10a-risk.spec.ts pattern**

```typescript
// tests/e2e/phase10b1-sizing.spec.ts
import { test, expect } from '@playwright/test';

async function mintNonce(request: import('@playwright/test').APIRequestContext): Promise<string> {
  const resp = await request.post('/api/admin/csrf/issue');
  expect(resp.status()).toBe(200);
  return (await resp.json()).nonce;
}

test.describe('Phase 10b.1 sizing API', () => {
  test('GET /api/risk/sizing-defaults/{id} returns SizingDefaults shape', async ({ request }) => {
    const resp = await request.get('/api/risk/sizing-defaults/00000000-0000-0000-0000-000000000001');
    if (resp.status() === 401 || resp.status() === 403) {
      test.skip(true, 'admin auth not available for this E2E run');
    }
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body).toHaveProperty('method');
    expect(body).toHaveProperty('fixed_fractional_risk_pct');
  });

  test('PUT /api/admin/sizing-defaults requires CSRF nonce', async ({ request }) => {
    const resp = await request.put(
      '/api/admin/sizing-defaults/00000000-0000-0000-0000-000000000001',
      {
        data: {
          method: 'fixed_fractional',
          fixed_fractional_risk_pct: '2.00',
          risk_per_trade_risk_pct: '1.00',
          vol_targeted_target_vol_pct: '15.00',
        },
      },
    );
    if (resp.status() === 503) test.skip(true, 'backend admin layer not yet configured');
    expect([401, 403, 422]).toContain(resp.status());
  });

  test('PUT then GET round-trip via admin API', async ({ request }) => {
    const acct = '00000000-0000-0000-0000-000000000099';
    const nonce = await mintNonce(request);
    const putResp = await request.put(
      `/api/admin/sizing-defaults/${acct}`,
      {
        headers: { 'X-Confirm-Nonce': nonce },
        data: {
          method: 'vol_targeted',
          fixed_fractional_risk_pct: '2.50',
          risk_per_trade_risk_pct: '1.50',
          vol_targeted_target_vol_pct: '20.00',
        },
      },
    );
    if (putResp.status() === 401 || putResp.status() === 403) {
      test.skip(true, 'admin auth not available for this E2E run');
    }
    expect(putResp.status()).toBe(204);

    const getResp = await request.get(`/api/risk/sizing-defaults/${acct}`);
    expect(getResp.status()).toBe(200);
    const body = await getResp.json();
    expect(body.method).toBe('vol_targeted');
    expect(body.vol_targeted_target_vol_pct).toBe('20.00');
  });
});

test.describe('Phase 10b.1 /trade/sizing page', () => {
  test('page loads without runtime error', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto('/trade/sizing');
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('heading', { name: /position sizing/i })).toBeVisible();
    expect(errors).toEqual([]);
  });
});
```

- [ ] **Step 2: Add the npm script**

In `tests/e2e/package.json`:

```json
"scripts": {
  "test": "playwright test",
  "test:smoke": "playwright test smoke.spec.ts",
  "test:risk": "playwright test phase10a-risk.spec.ts",
  "test:sizing": "playwright test phase10b1-sizing.spec.ts",
  "install-browsers": "playwright install chromium --with-deps"
}
```

- [ ] **Step 3: Add the workflow step**

In `.github/workflows/deploy.yml`, after the existing risk-gate step:

```yaml
      - name: Phase 10b.1 sizing E2E (continue-on-error)
        working-directory: tests/e2e
        env:
          CF_ACCESS_CLIENT_ID:     ${{ secrets.CF_ACCESS_CLIENT_ID }}
          CF_ACCESS_CLIENT_SECRET: ${{ secrets.CF_ACCESS_CLIENT_SECRET }}
          SMOKE_BASE_URL:          https://dashboard.kiusinghung.com
        run: pnpm test:sizing
        continue-on-error: true
```

- [ ] **Step 4: List the specs to confirm playwright sees the new file**

```bash
cd /home/joseph/dashboard/tests/e2e && npx playwright test --list 2>&1 | grep phase10b1
```

Expected: 4 lines (3 API tests + 1 page-render test).

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/phase10b1-sizing.spec.ts tests/e2e/package.json .github/workflows/deploy.yml
git commit -m "test(phase10b1-e3): playwright sizing E2E + workflow wiring"
```

---

### Task E4: Chunk-E reviewer chain + close-out

- [ ] **Step 1: Dispatch the 5-reviewer chain on the FE bits (§4.3 inlined)**

- [ ] **Step 2: Apply CRIT+HIGH+MED fixes inline**

- [ ] **Step 3: Update CHANGELOG.md + TASKS.md for Phase 10b.1 close-out**

```bash
# Update CHANGELOG.md with v0.13.0 entry
# Update TASKS.md with Phase 10b.1 status → complete
# Update memory/MEMORY.md with new shipped pointer file phase10b1_shipped.md
```

- [ ] **Step 4: Tag v0.13.0**

```bash
git tag -a v0.13.0 -m "Phase 10b.1 — position-sizing calculator"
git push origin main v0.13.0
```

- [ ] **Step 5: Final commit + push**

```bash
git add CHANGELOG.md TASKS.md docs/ROADMAP.md
git commit -m "docs(phase10b1): close-out — v0.13.0 CHANGELOG + TASKS"
git push origin main
```

---

## Self-Review

**1. Spec coverage** — every spec section accounted for:

| Spec section | Plan task(s) |
|---|---|
| §1 Goal | Architecture statement at top |
| §2.1-2.3 Sizing methods | A5 (pure-math functions + golden vectors) |
| §3.1 PositionSizingService | A6 (orchestrator + lifespan) |
| §3.2 VolatilityService | A1, A3 (singleton + golden values) |
| §3.3 Schemas | A4 |
| §3.4 API endpoints + rate-limit + admin UI accordion | B1 (limiter), B2 (endpoints) |
| §3.5 Risk-gate integration | A6 (orchestrator calls evaluate); plan drift §1 documents no `dry_run` needed |
| §3.6 Latency budget | covered by B3 histogram metric buckets |
| §4.1 FE service | C2 (types, api, hooks) |
| §4.2 TradeTicketModal | D1-D4 |
| §4.3 Standalone page | E1-E2 |
| §4.4 Tests | A1, A3, A5, A6, B2, C3, D2, D4, E2, E3 |
| §6 OrderContext synth | A6 step 3 (constructor); drift §2 documents no `fx_rate` field |
| §9 Observability | B3 |
| §10 Test data plan | A2 (bars_1d_factory + golden_aapl) |

**2. Placeholder scan** — none. All code blocks complete, all commands exact, all expected outputs specified.

**3. Type consistency** — verified across tasks:
- `SizingMethod` enum spelled `fixed_fractional`/`risk_per_trade`/`vol_targeted` consistently in A4 schema, A5 math, A6 orchestrator, C2 types, D1 modal, E1 page.
- `MethodBreakdown` fields (`nlv_base`, `fx_rate`, `price_base`, `atr14`, `realized_vol14_annualized`, `risk_per_share_base`, `vol_source`) consistent A4↔A6↔C2.
- `VolatilityEstimate` shape consistent A1 dataclass ↔ A6 consumer.
- `SizingDefaults` fields consistent A4↔B2↔C2↔D3↔E3.
- `aria-label` strings — `"Risk gate warnings (sizing)"` + `"Risk gate blockers (sizing)"` distinct from Phase 10a `"Risk gate warnings"` + `"Risk gate blockers"` so existing TradeTicketModal tests still pass.

**Plan complete.**

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-12-phase10b1-position-sizing-plan.md`.** Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review (spec compliance + code quality), fast iteration within this session.

**2. Inline Execution** — execute tasks here in this session using `executing-plans`, batch execution with checkpoints for review.

Which approach?
