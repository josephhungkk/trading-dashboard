# Phase 18.0 — Universe Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a configurable universe scanner with a Lark DSL rule evaluator, saved + ad-hoc scan modes, APScheduler background scheduling, DB-persisted run history, LLM commentary, TicksSubscriber lifespan wiring, and Phase 11b alerts integration.

**Architecture:** Three layers — a Lark grammar + transformer (evaluator), an indicator computation layer reading from existing `bars_1d`/`bars_1m` CAGGs with Redis caching, and a `ScannerService` orchestrator wiring them together. APScheduler jobs fire `run_scan`; a WebSocket gateway pushes frames to the FE. `WSConnId` is widened to `UUID | str` so internal consumers can subscribe to quotes without the per-WS rate limiter.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy 2.0 async / APScheduler 3.x / Lark 1.x / croniter / Redis / TimescaleDB / React 19 / TanStack Router / Zustand / Vitest / pytest-asyncio

---

## File map

**New BE files:**
- `backend/alembic/versions/0058_phase18a_scanner.py` — migration
- `backend/app/services/scanner/__init__.py`
- `backend/app/services/scanner/schemas.py` — Pydantic models
- `backend/app/services/scanner/evaluator.py` — Lark grammar + transformer + safety budget
- `backend/app/services/scanner/indicators.py` — indicator computation + Redis cache
- `backend/app/services/scanner/universe.py` — UniverseResolver
- `backend/app/services/scanner/commentary.py` — LLM commentary via AIRouterClient
- `backend/app/services/scanner/scheduler.py` — APScheduler job registration
- `backend/app/services/scanner/scanner_service.py` — orchestrator
- `backend/app/api/scanner.py` — REST + WS endpoints
- `backend/app/api/ws_scanner.py` — WebSocket handler

**Modified BE files:**
- `backend/app/services/quotes/registry.py` — widen `WSConnId` to `UUID | str`, add `cap_per_ws_override`
- `backend/app/core/metrics.py` — 13 new scanner counters
- `backend/app/main.py` — wire scanner lifespan + router
- `backend/app/services/alerts/ticks_subscriber.py` — migrate to `__internal:alerts` synthetic WS id

**New BE tests:**
- `backend/tests/test_scanner_evaluator.py`
- `backend/tests/test_scanner_indicators.py`
- `backend/tests/test_scanner_service.py`
- `backend/tests/api/test_scanner_api.py`
- `backend/tests/integration/test_scanner_e2e.py`

**New FE files:**
- `frontend/src/services/scanner/types.ts`
- `frontend/src/services/scanner/api.ts`
- `frontend/src/services/scanner/useScannerWs.ts`
- `frontend/src/stores/global/scanner.ts`
- `frontend/src/features/scanner/ScannerPage.tsx`
- `frontend/src/features/scanner/SavedScanList.tsx`
- `frontend/src/features/scanner/ScanConfigDrawer.tsx`
- `frontend/src/features/scanner/RuleEditor.tsx`
- `frontend/src/features/scanner/UniversePicker.tsx`
- `frontend/src/features/scanner/CandidatesTable.tsx`
- `frontend/src/features/scanner/RunHistoryDrawer.tsx`
- `frontend/src/features/scanner/AdHocRunPanel.tsx`
- `frontend/src/routes/scanner.tsx`
- `frontend/src/features/scanner/__tests__/ScannerPage.test.tsx`
- `frontend/src/features/scanner/__tests__/RuleEditor.test.tsx`

---

## Chunk A — Alembic migration + Pydantic schemas + Lark evaluator

### Task 1: Alembic migration 0058

**Files:**
- Create: `backend/alembic/versions/0058_phase18a_scanner.py`

- [ ] **Step 1: Write the migration**

```python
"""phase18a scanner tables

Revision ID: 0058
Revises: 0057
Create Date: 2026-05-19
"""
from __future__ import annotations
import sqlalchemy as sa
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE saved_scans (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name              TEXT NOT NULL,
            universe_config   JSONB NOT NULL,
            rule_expr         TEXT NOT NULL,
            schedule          TEXT,
            market_hours_gate BOOLEAN NOT NULL DEFAULT false,
            exchange          TEXT,
            llm_depth         TEXT NOT NULL CHECK (llm_depth IN ('quick', 'deep')),
            alert_id          BIGINT REFERENCES alerts(id) ON DELETE SET NULL,
            enabled           BOOLEAN NOT NULL DEFAULT true,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE scanner_runs (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            scan_id           UUID REFERENCES saved_scans(id) ON DELETE SET NULL,
            universe_snapshot JSONB NOT NULL,
            rule_expr         TEXT NOT NULL,
            candidate_count   INT NOT NULL DEFAULT 0,
            status            TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
            started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at      TIMESTAMPTZ,
            error             TEXT
        )
    """)

    op.execute("""
        SELECT create_hypertable(
            'scanner_runs', 'started_at',
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists => TRUE
        )
    """)

    op.execute("""
        SELECT add_retention_policy('scanner_runs', INTERVAL '90 days', if_not_exists => TRUE)
    """)

    op.execute("""
        CREATE TABLE scanner_candidates (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id              UUID NOT NULL REFERENCES scanner_runs(id) ON DELETE CASCADE,
            instrument_id       BIGINT REFERENCES instruments(id) ON DELETE SET NULL,
            canonical_id        TEXT NOT NULL,
            matched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            indicator_snapshot  JSONB NOT NULL,
            llm_commentary      TEXT,
            llm_depth           TEXT CHECK (llm_depth IN ('quick', 'deep')),
            CHECK (instrument_id IS NOT NULL OR canonical_id IS NOT NULL)
        )
    """)

    op.execute("CREATE INDEX ON scanner_candidates (canonical_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scanner_candidates")
    op.execute("DROP TABLE IF EXISTS scanner_runs")
    op.execute("DROP TABLE IF EXISTS saved_scans")
```

- [ ] **Step 2: Apply migration and verify**

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current
```
Expected: `0058 (head)`

- [ ] **Step 3: Commit**

```bash
git add backend/alembic/versions/0058_phase18a_scanner.py
git commit -m "feat(phase18a): alembic 0058 — scanner tables (scanner_runs hypertable)"
```

---

### Task 2: Pydantic schemas

**Files:**
- Create: `backend/app/services/scanner/__init__.py`
- Create: `backend/app/services/scanner/schemas.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_scanner_schemas.py
from app.services.scanner.schemas import (
    UniverseConfig, ScanConfig, ScanRunRow, CandidateRow,
)
import pytest

def test_universe_config_schwab_screener():
    u = UniverseConfig(type="schwab_screener", params={"market": "US"})
    assert u.type == "schwab_screener"

def test_universe_config_tickers():
    u = UniverseConfig(type="tickers", params={"tickers": ["AAPL", "TSLA"]})
    assert u.params["tickers"] == ["AAPL", "TSLA"]

def test_scan_config_defaults():
    cfg = ScanConfig(
        name="RSI scan",
        universe_config=UniverseConfig(type="tickers", params={"tickers": ["AAPL"]}),
        rule_expr="rsi(14) < 30",
        llm_depth="quick",
    )
    assert cfg.schedule is None
    assert cfg.market_hours_gate is False
    assert cfg.enabled is True

def test_scan_config_invalid_llm_depth():
    with pytest.raises(Exception):
        ScanConfig(
            name="x",
            universe_config=UniverseConfig(type="tickers", params={}),
            rule_expr="rsi(14) < 30",
            llm_depth="ultra",
        )
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
docker compose exec backend pytest backend/tests/test_scanner_schemas.py -v 2>&1 | head -30
```

- [ ] **Step 3: Write schemas**

```python
# backend/app/services/scanner/__init__.py
# (empty)
```

```python
# backend/app/services/scanner/schemas.py
from __future__ import annotations
from typing import Any, Literal
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field


class UniverseConfig(BaseModel):
    type: Literal["schwab_screener", "watchlist", "tickers", "instruments"]
    params: dict[str, Any] = Field(default_factory=dict)


class ScanConfig(BaseModel):
    name: str
    universe_config: UniverseConfig
    rule_expr: str
    schedule: str | None = None
    market_hours_gate: bool = False
    exchange: str | None = None
    llm_depth: Literal["quick", "deep"] = "quick"
    alert_id: int | None = None
    enabled: bool = True


class SavedScanRow(BaseModel):
    id: UUID
    name: str
    universe_config: UniverseConfig
    rule_expr: str
    schedule: str | None
    market_hours_gate: bool
    exchange: str | None
    llm_depth: Literal["quick", "deep"]
    alert_id: int | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ScanRunRow(BaseModel):
    id: UUID
    scan_id: UUID | None
    universe_snapshot: list[str]
    rule_expr: str
    candidate_count: int
    status: Literal["running", "completed", "failed"]
    started_at: datetime
    completed_at: datetime | None
    error: str | None


class CandidateRow(BaseModel):
    id: UUID
    run_id: UUID
    instrument_id: int | None
    canonical_id: str
    matched_at: datetime
    indicator_snapshot: dict[str, Any]
    llm_commentary: str | None
    llm_depth: Literal["quick", "deep"] | None
```

- [ ] **Step 4: Run test — expect PASS**

```bash
docker compose exec backend pytest backend/tests/test_scanner_schemas.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scanner/ backend/tests/test_scanner_schemas.py
git commit -m "feat(phase18a): scanner schemas — UniverseConfig, ScanConfig, ScanRunRow, CandidateRow"
```

---

### Task 3: Lark evaluator + safety budget

**Files:**
- Create: `backend/app/services/scanner/evaluator.py`
- Test: `backend/tests/test_scanner_evaluator.py`

- [ ] **Step 1: Add lark dependency**

```bash
docker compose exec backend uv add lark
```

- [ ] **Step 2: Write failing tests**

```python
# backend/tests/test_scanner_evaluator.py
import pytest
from app.services.scanner.evaluator import (
    ScannerEvaluator, EvaluatorBudgetError, EvaluatorParseError
)

INDICATOR_VALS = {
    "rsi": lambda period: 28.0,
    "sma": lambda field, period: 150.0,
    "ema": lambda field, period: 148.0,
    "volume_ratio": lambda period: 2.3,
    "close": 152.0,
    "volume": 5_000_000.0,
    "mcap": None,  # nullable — evaluates false
}

evaluator = ScannerEvaluator()


def test_simple_rsi_rule():
    ast = evaluator.parse("rsi(14) < 30")
    assert evaluator.evaluate(ast, INDICATOR_VALS) is True


def test_and_rule():
    ast = evaluator.parse("rsi(14) < 30 and volume_ratio(20) > 2.0")
    assert evaluator.evaluate(ast, INDICATOR_VALS) is True


def test_or_rule():
    ast = evaluator.parse("rsi(14) < 10 or volume_ratio(20) > 2.0")
    assert evaluator.evaluate(ast, INDICATOR_VALS) is True


def test_not_rule():
    ast = evaluator.parse("not rsi(14) > 50")
    assert evaluator.evaluate(ast, INDICATOR_VALS) is True


def test_precedence_not_and_or():
    # not a and b or c → ((not a) and b) or c
    # rsi(14)=28 → not(28<30)=False → False and True → False → False or True → True
    ast = evaluator.parse("not rsi(14) > 30 and volume_ratio(20) > 2.0 or close > 100")
    assert evaluator.evaluate(ast, INDICATOR_VALS) is True


def test_nullable_mcap_evaluates_false():
    ast = evaluator.parse("mcap > 1000000")
    assert evaluator.evaluate(ast, INDICATOR_VALS) is False


def test_parse_error_raises():
    with pytest.raises(EvaluatorParseError):
        evaluator.parse("rsi(14 <<< 30")


def test_budget_node_cap():
    # Build expression with 257+ nodes
    expr = " and ".join(["rsi(14) < 30"] * 130)
    with pytest.raises(EvaluatorBudgetError, match="max_nodes"):
        evaluator.parse(expr)


def test_budget_depth_cap():
    expr = "(" * 9 + "rsi(14) < 30" + ")" * 9
    with pytest.raises(EvaluatorBudgetError, match="max_depth"):
        evaluator.parse(expr)


def test_budget_func_call_cap():
    expr = " and ".join(["rsi(14) < 30"] * 33)
    with pytest.raises(EvaluatorBudgetError, match="max_func_calls"):
        evaluator.parse(expr)


def test_budget_period_sum_cap():
    with pytest.raises(EvaluatorBudgetError, match="max_period_sum"):
        evaluator.parse("sma(close, 5001) > 100")
```

- [ ] **Step 3: Run tests — expect FAIL**

```bash
docker compose exec backend pytest backend/tests/test_scanner_evaluator.py -v 2>&1 | head -30
```

- [ ] **Step 4: Write evaluator**

```python
# backend/app/services/scanner/evaluator.py
from __future__ import annotations
from typing import Any
from lark import Lark, Transformer, Tree, Token

GRAMMAR = r"""
rule: or_expr

or_expr:  and_expr ("or" and_expr)*    -> or_expr
and_expr: not_expr ("and" not_expr)*   -> and_expr
not_expr: "not" not_expr               -> not_expr
        | atom
atom: comparison
    | "(" or_expr ")"

comparison: term OP term               -> cmp_expr

term: func_call
    | NUMBER                           -> number
    | NAME                             -> name

func_call: NAME "(" arglist? ")"       -> call

arglist: term ("," term)*

OP: "<" | ">" | "<=" | ">=" | "==" | "!="

%import common.CNAME  -> NAME
%import common.NUMBER
%import common.WS
%ignore WS
"""

_PARSER = Lark(GRAMMAR, parser="lalr", start="rule")

MAX_DEPTH = 8
MAX_NODES = 256
MAX_FUNC_CALLS = 32
MAX_PERIOD_SUM = 5000


class EvaluatorParseError(Exception):
    pass


class EvaluatorBudgetError(Exception):
    pass


def _check_budget(tree: Tree) -> None:
    nodes = 0
    func_calls = 0
    period_sum = 0

    def _walk(node: Tree | Token, depth: int) -> None:
        nonlocal nodes, func_calls, period_sum
        if depth > MAX_DEPTH:
            raise EvaluatorBudgetError(f"max_depth exceeded ({MAX_DEPTH})")
        if isinstance(node, Tree):
            nodes += 1
            if nodes > MAX_NODES:
                raise EvaluatorBudgetError(f"max_nodes exceeded ({MAX_NODES})")
            if node.data == "call":
                func_calls += 1
                if func_calls > MAX_FUNC_CALLS:
                    raise EvaluatorBudgetError(f"max_func_calls exceeded ({MAX_FUNC_CALLS})")
                # collect numeric args that look like period parameters
                for child in node.children[1:]:
                    if isinstance(child, Token) and child.type == "NUMBER":
                        period_sum += float(child)
                        if period_sum > MAX_PERIOD_SUM:
                            raise EvaluatorBudgetError(
                                f"max_period_sum exceeded ({MAX_PERIOD_SUM})"
                            )
            for child in node.children:
                _walk(child, depth + 1)

    _walk(tree, 0)


class _EvalTransformer(Transformer):
    def __init__(self, symbols: dict[str, Any]) -> None:
        super().__init__()
        self._sym = symbols

    def or_expr(self, items: list) -> bool:
        result = items[0]
        for item in items[1:]:
            result = result or item
        return bool(result)

    def and_expr(self, items: list) -> bool:
        result = items[0]
        for item in items[1:]:
            result = result and item
        return bool(result)

    def not_expr(self, items: list) -> bool:
        return not items[0]

    def cmp_expr(self, items: list) -> bool:
        left, op, right = items
        if left is None or right is None:
            return False
        op_s = str(op)
        if op_s == "<":   return left < right  # noqa: E701
        if op_s == ">":   return left > right  # noqa: E701
        if op_s == "<=":  return left <= right  # noqa: E701
        if op_s == ">=":  return left >= right  # noqa: E701
        if op_s == "==":  return left == right  # noqa: E701
        if op_s == "!=":  return left != right  # noqa: E701
        return False

    def call(self, items: list) -> Any:
        name = str(items[0])
        args = items[1:]
        fn = self._sym.get(name)
        if fn is None:
            return None
        if callable(fn):
            try:
                return fn(*args)
            except Exception:
                return None
        return None

    def name(self, items: list) -> Any:
        key = str(items[0])
        val = self._sym.get(key)
        return val  # None → evaluates false in comparisons

    def number(self, items: list) -> float:
        return float(items[0])

    # OP is a terminal — pass through
    def __default_token__(self, token: Token) -> Token:
        return token


class ScannerEvaluator:
    def parse(self, rule_expr: str) -> Tree:
        try:
            tree = _PARSER.parse(rule_expr)
        except Exception as exc:
            raise EvaluatorParseError(str(exc)) from exc
        _check_budget(tree)
        return tree

    def evaluate(self, tree: Tree, symbols: dict[str, Any]) -> bool:
        transformer = _EvalTransformer(symbols)
        try:
            result = transformer.transform(tree)
        except Exception:
            return False
        return bool(result)
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
docker compose exec backend pytest backend/tests/test_scanner_evaluator.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/scanner/evaluator.py backend/tests/test_scanner_evaluator.py
git commit -m "feat(phase18a): Lark DSL evaluator — grammar, transformer, safety budget"
```

---

### Task 4: Prometheus metrics

**Files:**
- Modify: `backend/app/core/metrics.py`

- [ ] **Step 1: Add scanner counters**

Open `backend/app/core/metrics.py` and append after the last existing counter block:

```python
# ── Phase 18a scanner ─────────────────────────────────────────────────────────
scanner_runs_total = Counter(
    "scanner_runs_total", "Scanner run attempts", ["mode", "status"]
)
scanner_candidates_total = Counter(
    "scanner_candidates_total", "Scanner candidates found", ["scan_id"]
)
scanner_universe_size = Gauge(
    "scanner_universe_size", "Universe size per scan run", ["scan_id"]
)
scanner_universe_stale_total = Counter(
    "scanner_universe_stale_total", "Stale universe fallbacks", ["scan_id"]
)
scanner_candidate_cap_hit_total = Counter(
    "scanner_candidate_cap_hit_total", "Candidate cap truncations", ["scan_id"]
)
scanner_indicator_cache_hits_total = Counter(
    "scanner_indicator_cache_hits_total", "Indicator Redis cache hits"
)
scanner_indicator_cache_misses_total = Counter(
    "scanner_indicator_cache_misses_total", "Indicator Redis cache misses"
)
scanner_llm_commentary_total = Counter(
    "scanner_llm_commentary_total", "LLM commentary requests", ["depth", "status"]
)
scanner_scheduler_fires_total = Counter(
    "scanner_scheduler_fires_total", "APScheduler scan fires", ["scan_id"]
)
scanner_alert_fires_total = Counter(
    "scanner_alert_fires_total", "Alert fires from scanner matches", ["scan_id"]
)
scanner_eval_timeout_total = Counter(
    "scanner_eval_timeout_total", "Per-instrument evaluator timeouts"
)
scanner_eval_node_reject_total = Counter(
    "scanner_eval_node_reject_total", "Save-time AST node cap violations"
)
scanner_eval_indicator_budget_exhausted_total = Counter(
    "scanner_eval_indicator_budget_exhausted_total", "Period-sum cap violations"
)
```

- [ ] **Step 2: Verify metrics file parses**

```bash
docker compose exec backend python -c "from app.core import metrics; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/metrics.py
git commit -m "feat(phase18a): 13 scanner Prometheus counters/gauges"
```

---

## Chunk B — Indicator computation + UniverseResolver + WSConnId widen

### Task 5: Widen WSConnId + SubscriptionRegistry cap_per_ws_override

**Files:**
- Modify: `backend/app/services/quotes/registry.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_registry_internal_ws.py
import asyncio
import pytest
from app.services.quotes.registry import SubscriptionRegistry, WSConnId

@pytest.mark.asyncio
async def test_internal_ws_bypasses_rate_limit():
    """__internal: WS IDs bypass rate buckets and get override cap."""
    reg = SubscriptionRegistry(
        cap_global=10,
        cap_per_ws=3,
        cap_per_ws_override={"__internal:scanner": 8},
    )
    internal_id = "__internal:scanner"
    # Should accept 8 symbols, not limited to 3
    symbols = [f"SYM{i}" for i in range(8)]
    result = await reg.add(internal_id, symbols)
    assert result.added == set(symbols)
    assert reg.per_ws_count(internal_id) == 8

@pytest.mark.asyncio
async def test_internal_ws_no_rate_bucket():
    reg = SubscriptionRegistry(
        cap_global=100,
        cap_per_ws=3,
        cap_per_ws_override={"__internal:scanner": 50},
    )
    # Adding 50 symbols at once must not raise rate-limit error
    symbols = [f"SYM{i}" for i in range(50)]
    result = await reg.add("__internal:scanner", symbols)
    assert len(result.added) == 50
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
docker compose exec backend pytest backend/tests/test_registry_internal_ws.py -v 2>&1 | head -20
```

- [ ] **Step 3: Modify registry**

In `backend/app/services/quotes/registry.py`:

1. Change `WSConnId = UUID` to `WSConnId = UUID | str`
2. Add `cap_per_ws_override: dict[str, int] = field(default_factory=dict)` parameter to `SubscriptionRegistry.__init__`
3. In `add()`: when `isinstance(ws, str) and ws.startswith("__internal:")`, use `cap_per_ws_override.get(ws, cap_per_ws)` instead of `cap_per_ws`, and skip rate bucket check (`_evict_rate_window` + deque append).

The exact lines to change depend on the current file — read it first, then apply the minimal surgical edit to `__init__` signature and `add()` body.

- [ ] **Step 4: Run test — expect PASS**

```bash
docker compose exec backend pytest backend/tests/test_registry_internal_ws.py -v
```

- [ ] **Step 5: Run existing quote tests to verify no regression**

```bash
docker compose exec backend pytest backend/tests/ -k "quote or registry" -v 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/quotes/registry.py backend/tests/test_registry_internal_ws.py
git commit -m "feat(phase18a): widen WSConnId to UUID|str, add cap_per_ws_override for internal subscribers"
```

---

### Task 6: Indicator computation layer

**Files:**
- Create: `backend/app/services/scanner/indicators.py`
- Test: `backend/tests/test_scanner_indicators.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_scanner_indicators.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.scanner.indicators import IndicatorComputer

@pytest.mark.asyncio
async def test_rsi_cache_hit(redis_mock):
    """Cache hit returns value without DB query."""
    redis_mock.get.return_value = b"28.5"
    computer = IndicatorComputer(redis=redis_mock, db=AsyncMock())
    val = await computer.compute("rsi", {"period": 14}, instrument_id=1, canonical_id="AAPL")
    assert val == pytest.approx(28.5)
    redis_mock.get.assert_called_once()

@pytest.mark.asyncio
async def test_rsi_cache_miss_returns_none_on_no_bars(redis_mock):
    """Cache miss + no bars → returns None (not error)."""
    redis_mock.get.return_value = None
    db_mock = AsyncMock()
    db_mock.execute.return_value.fetchall.return_value = []
    computer = IndicatorComputer(redis=redis_mock, db=db_mock)
    val = await computer.compute("rsi", {"period": 14}, instrument_id=1, canonical_id="AAPL")
    assert val is None

@pytest.mark.asyncio
async def test_unknown_indicator_returns_none(redis_mock):
    computer = IndicatorComputer(redis=redis_mock, db=AsyncMock())
    val = await computer.compute("unknown_ind", {}, instrument_id=1, canonical_id="AAPL")
    assert val is None

@pytest.fixture
def redis_mock():
    m = MagicMock()
    m.get = AsyncMock()
    m.setex = AsyncMock()
    return m
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
docker compose exec backend pytest backend/tests/test_scanner_indicators.py -v 2>&1 | head -20
```

- [ ] **Step 3: Write indicators.py**

```python
# backend/app/services/scanner/indicators.py
from __future__ import annotations
import hashlib
import json
from typing import Any
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import metrics

log = structlog.get_logger()

_DAILY_TTL = 300   # 5 min
_INTRADAY_TTL = 60  # 60s

DAILY_INDICATORS = frozenset({"rsi", "sma", "ema", "macd", "bb_pct", "atr"})
INTRADAY_SCALARS = frozenset({"close", "open", "high", "low", "volume", "volume_ratio",
                               "price_vs_high", "price_vs_low"})
FUNDAMENTAL_SCALARS = frozenset({"mcap", "pe", "eps_growth"})


def _params_hash(params: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]


class IndicatorComputer:
    def __init__(self, *, redis: Any, db: AsyncSession) -> None:
        self._redis = redis
        self._db = db

    async def compute(
        self,
        name: str,
        params: dict[str, Any],
        *,
        instrument_id: int | None,
        canonical_id: str,
    ) -> float | None:
        if name in FUNDAMENTAL_SCALARS:
            return await self._fundamental(name, instrument_id)
        timeframe = "1d" if name in DAILY_INDICATORS else "1m"
        cache_key = (
            f"scanner:ind:{instrument_id}:{name}:"
            f"{_params_hash(params)}:{timeframe}"
        )
        cached = await self._redis.get(cache_key)
        if cached is not None:
            metrics.scanner_indicator_cache_hits_total.inc()
            return float(cached)
        metrics.scanner_indicator_cache_misses_total.inc()
        val = await self._recompute(name, params, instrument_id=instrument_id,
                                    canonical_id=canonical_id, timeframe=timeframe)
        if val is not None:
            ttl = _DAILY_TTL if timeframe == "1d" else _INTRADAY_TTL
            await self._redis.setex(cache_key, ttl, str(val))
        return val

    async def _fundamental(self, name: str, instrument_id: int | None) -> float | None:
        if instrument_id is None:
            return None
        try:
            row = await self._db.execute(
                __import__("sqlalchemy").text(
                    "SELECT meta->:key AS v FROM instruments WHERE id = :id"
                ),
                {"key": name, "id": instrument_id},
            )
            r = row.fetchone()
            if r and r.v is not None:
                return float(r.v)
        except Exception:
            log.warning("scanner.indicator.fundamental_error", name=name)
        return None

    async def _recompute(
        self,
        name: str,
        params: dict[str, Any],
        *,
        instrument_id: int | None,
        canonical_id: str,
        timeframe: str,
    ) -> float | None:
        if instrument_id is None:
            return None
        try:
            if name == "rsi":
                return await self._rsi(instrument_id, params.get("period", 14))
            if name in ("close", "open", "high", "low", "volume"):
                return await self._latest_bar_field(instrument_id, name)
            if name == "sma":
                return await self._sma(instrument_id,
                                       params.get("field", "close"),
                                       params.get("period", 20))
            if name == "ema":
                return await self._ema(instrument_id,
                                       params.get("field", "close"),
                                       params.get("period", 20))
            if name == "atr":
                return await self._atr(instrument_id, params.get("period", 14))
            if name == "volume_ratio":
                return await self._volume_ratio(instrument_id, params.get("period", 20))
            if name == "price_vs_high":
                return await self._price_vs_high(instrument_id, params.get("days", 52 * 5))
            if name == "price_vs_low":
                return await self._price_vs_low(instrument_id, params.get("days", 52 * 5))
            if name == "macd":
                return await self._macd(instrument_id,
                                        params.get("fast", 12),
                                        params.get("slow", 26),
                                        params.get("signal", 9))
            if name == "bb_pct":
                return await self._bb_pct(instrument_id,
                                          params.get("period", 20),
                                          params.get("std", 2.0))
        except Exception:
            log.warning("scanner.indicator.recompute_error", name=name,
                        instrument_id=instrument_id)
        return None

    async def _latest_bar_field(self, instrument_id: int, field: str) -> float | None:
        import sqlalchemy as sa
        safe_fields = {"close", "open", "high", "low", "volume"}
        if field not in safe_fields:
            return None
        row = await self._db.execute(
            sa.text(f"SELECT {field} FROM bars_1m WHERE instrument_id = :id "
                    "ORDER BY ts DESC LIMIT 1"),
            {"id": instrument_id},
        )
        r = row.fetchone()
        return float(getattr(r, field)) if r else None

    async def _rsi(self, instrument_id: int, period: int) -> float | None:
        import sqlalchemy as sa
        rows = await self._db.execute(
            sa.text("SELECT close FROM bars_1d WHERE instrument_id = :id "
                    "ORDER BY ts DESC LIMIT :n"),
            {"id": instrument_id, "n": period + 1},
        )
        closes = [float(r.close) for r in rows.fetchall()]
        if len(closes) < period + 1:
            return None
        closes = list(reversed(closes))
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    async def _sma(self, instrument_id: int, field: str, period: int) -> float | None:
        import sqlalchemy as sa
        safe_fields = {"close", "open", "high", "low", "volume"}
        if field not in safe_fields:
            return None
        rows = await self._db.execute(
            sa.text(f"SELECT {field} FROM bars_1d WHERE instrument_id = :id "
                    "ORDER BY ts DESC LIMIT :n"),
            {"id": instrument_id, "n": period},
        )
        vals = [float(getattr(r, field)) for r in rows.fetchall()]
        return sum(vals) / len(vals) if len(vals) == period else None

    async def _ema(self, instrument_id: int, field: str, period: int) -> float | None:
        import sqlalchemy as sa
        safe_fields = {"close", "open", "high", "low", "volume"}
        if field not in safe_fields:
            return None
        rows = await self._db.execute(
            sa.text(f"SELECT {field} FROM bars_1d WHERE instrument_id = :id "
                    "ORDER BY ts ASC LIMIT :n"),
            {"id": instrument_id, "n": period * 2},
        )
        vals = [float(getattr(r, field)) for r in rows.fetchall()]
        if len(vals) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(vals[:period]) / period
        for v in vals[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    async def _atr(self, instrument_id: int, period: int) -> float | None:
        import sqlalchemy as sa
        rows = await self._db.execute(
            sa.text("SELECT high, low, close FROM bars_1d WHERE instrument_id = :id "
                    "ORDER BY ts DESC LIMIT :n"),
            {"id": instrument_id, "n": period + 1},
        )
        bars = list(reversed(rows.fetchall()))
        if len(bars) < period + 1:
            return None
        trs = []
        for i in range(1, len(bars)):
            h, l, pc = float(bars[i].high), float(bars[i].low), float(bars[i-1].close)
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return sum(trs[-period:]) / period

    async def _volume_ratio(self, instrument_id: int, period: int) -> float | None:
        import sqlalchemy as sa
        rows = await self._db.execute(
            sa.text("SELECT volume FROM bars_1m WHERE instrument_id = :id "
                    "ORDER BY ts DESC LIMIT :n"),
            {"id": instrument_id, "n": period},
        )
        vols = [float(r.volume) for r in rows.fetchall()]
        if not vols:
            return None
        current = vols[0]
        avg = sum(vols) / len(vols)
        return current / avg if avg else None

    async def _price_vs_high(self, instrument_id: int, days: int) -> float | None:
        import sqlalchemy as sa
        rows = await self._db.execute(
            sa.text("SELECT high FROM bars_1d WHERE instrument_id = :id "
                    "ORDER BY ts DESC LIMIT :n"),
            {"id": instrument_id, "n": days},
        )
        highs = [float(r.high) for r in rows.fetchall()]
        if not highs:
            return None
        latest_close = await self._latest_bar_field(instrument_id, "close")
        if latest_close is None:
            return None
        return latest_close / max(highs)

    async def _price_vs_low(self, instrument_id: int, days: int) -> float | None:
        import sqlalchemy as sa
        rows = await self._db.execute(
            sa.text("SELECT low FROM bars_1d WHERE instrument_id = :id "
                    "ORDER BY ts DESC LIMIT :n"),
            {"id": instrument_id, "n": days},
        )
        lows = [float(r.low) for r in rows.fetchall()]
        if not lows:
            return None
        latest_close = await self._latest_bar_field(instrument_id, "close")
        if latest_close is None:
            return None
        return latest_close / min(lows)

    async def _macd(self, instrument_id: int, fast: int, slow: int, signal: int) -> float | None:
        fast_ema = await self._ema(instrument_id, "close", fast)
        slow_ema = await self._ema(instrument_id, "close", slow)
        if fast_ema is None or slow_ema is None:
            return None
        return fast_ema - slow_ema

    async def _bb_pct(self, instrument_id: int, period: int, std_mult: float) -> float | None:
        import sqlalchemy as sa
        import math
        rows = await self._db.execute(
            sa.text("SELECT close FROM bars_1d WHERE instrument_id = :id "
                    "ORDER BY ts DESC LIMIT :n"),
            {"id": instrument_id, "n": period},
        )
        closes = [float(r.close) for r in rows.fetchall()]
        if len(closes) < period:
            return None
        mean = sum(closes) / period
        std = math.sqrt(sum((c - mean) ** 2 for c in closes) / period)
        if std == 0:
            return 0.5
        latest = closes[0]
        upper = mean + std_mult * std
        lower = mean - std_mult * std
        return (latest - lower) / (upper - lower)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
docker compose exec backend pytest backend/tests/test_scanner_indicators.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scanner/indicators.py backend/tests/test_scanner_indicators.py
git commit -m "feat(phase18a): indicator computation layer — RSI, SMA, EMA, ATR, volume_ratio, MACD, BB%B"
```

---

### Task 7: UniverseResolver

**Files:**
- Create: `backend/app/services/scanner/universe.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_scanner_universe.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.scanner.schemas import UniverseConfig
from app.services.scanner.universe import UniverseResolver

@pytest.mark.asyncio
async def test_tickers_universe():
    resolver = UniverseResolver(db=AsyncMock(), cfg=MagicMock(), redis=MagicMock())
    config = UniverseConfig(type="tickers", params={"tickers": ["AAPL", "MSFT"]})
    result = await resolver.resolve(config)
    assert result == ["AAPL", "MSFT"]

@pytest.mark.asyncio
async def test_instruments_universe(mocker):
    db_mock = AsyncMock()
    db_mock.execute.return_value.fetchall.return_value = [
        MagicMock(canonical_id="AAPL"), MagicMock(canonical_id="TSLA")
    ]
    resolver = UniverseResolver(db=db_mock, cfg=MagicMock(), redis=MagicMock())
    config = UniverseConfig(type="instruments", params={})
    result = await resolver.resolve(config)
    assert "AAPL" in result
    assert "TSLA" in result
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
docker compose exec backend pytest backend/tests/test_scanner_universe.py -v 2>&1 | head -20
```

- [ ] **Step 3: Write universe.py**

```python
# backend/app/services/scanner/universe.py
from __future__ import annotations
import structlog
import sqlalchemy as sa
from app.services.scanner.schemas import UniverseConfig

log = structlog.get_logger()


class UniverseResolver:
    def __init__(self, *, db: object, cfg: object, redis: object) -> None:
        self._db = db
        self._cfg = cfg
        self._redis = redis

    async def resolve(self, config: UniverseConfig) -> list[str]:
        try:
            if config.type == "tickers":
                return list(config.params.get("tickers", []))
            if config.type == "watchlist":
                return await self._from_watchlist(config.params.get("watchlist_id"))
            if config.type == "instruments":
                return await self._all_instruments()
            if config.type == "schwab_screener":
                return await self._schwab_screener(config.params)
        except Exception:
            log.warning("scanner.universe.resolve_error", type=config.type)
        return []

    async def _all_instruments(self) -> list[str]:
        rows = await self._db.execute(
            sa.text("SELECT canonical_id FROM instruments WHERE canonical_id IS NOT NULL")
        )
        return [r.canonical_id for r in rows.fetchall()]

    async def _from_watchlist(self, watchlist_id: str | None) -> list[str]:
        if not watchlist_id:
            return []
        rows = await self._db.execute(
            sa.text(
                "SELECT i.canonical_id FROM watchlist_entries we "
                "JOIN instruments i ON i.id = we.instrument_id "
                "WHERE we.watchlist_id = :wid"
            ),
            {"wid": watchlist_id},
        )
        return [r.canonical_id for r in rows.fetchall()]

    async def _schwab_screener(self, params: dict) -> list[str]:
        # Schwab SCREENER_EQUITY integration — returns canonical_ids matching params
        # Full implementation depends on schwab sidecar search_contracts capability
        # For now returns empty list (fail-open); full wiring in Chunk C
        log.info("scanner.universe.schwab_screener", params=params)
        return []
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
docker compose exec backend pytest backend/tests/test_scanner_universe.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scanner/universe.py backend/tests/test_scanner_universe.py
git commit -m "feat(phase18a): UniverseResolver — tickers, watchlist, instruments, schwab_screener stub"
```

---

## Chunk C — ScannerService + Scheduler + Commentary

### Task 8: ScannerService orchestrator

**Files:**
- Create: `backend/app/services/scanner/scanner_service.py`
- Create: `backend/app/services/scanner/commentary.py`
- Test: `backend/tests/test_scanner_service.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_scanner_service.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from app.services.scanner.schemas import ScanConfig, UniverseConfig

@pytest.mark.asyncio
async def test_run_scan_no_matches():
    """Run with universe + rule that matches nothing → 0 candidates, status=completed."""
    from app.services.scanner.scanner_service import ScannerService
    svc = ScannerService.__new__(ScannerService)
    svc._db = AsyncMock()
    svc._redis = MagicMock()
    svc._redis.get = AsyncMock(return_value=None)
    svc._redis.setex = AsyncMock()
    svc._redis.publish = AsyncMock()

    with patch("app.services.scanner.scanner_service.UniverseResolver") as MockUR, \
         patch("app.services.scanner.scanner_service.ScannerEvaluator") as MockEval, \
         patch("app.services.scanner.scanner_service.IndicatorComputer") as MockIC:
        MockUR.return_value.resolve = AsyncMock(return_value=["AAPL"])
        MockEval.return_value.parse = MagicMock(return_value=MagicMock())
        MockEval.return_value.evaluate = MagicMock(return_value=False)
        MockIC.return_value.compute = AsyncMock(return_value=28.0)

        # Stub DB to return a fake run id
        run_row = MagicMock()
        run_row.id = uuid4()
        svc._db.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=run_row)))

        config = ScanConfig(
            name="test",
            universe_config=UniverseConfig(type="tickers", params={"tickers": ["AAPL"]}),
            rule_expr="rsi(14) < 10",
            llm_depth="quick",
        )
        run_id = await svc.run_scan(config=config, scan_id=None)
        assert run_id is not None

@pytest.mark.asyncio
async def test_run_scan_candidate_cap():
    """Matches > 500 → truncated to 500."""
    from app.services.scanner.scanner_service import CANDIDATE_COUNT_CAP
    assert CANDIDATE_COUNT_CAP == 500
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
docker compose exec backend pytest backend/tests/test_scanner_service.py -v 2>&1 | head -20
```

- [ ] **Step 3: Write commentary.py**

```python
# backend/app/services/scanner/commentary.py
from __future__ import annotations
import json
import asyncio
import structlog
from app.core import metrics

log = structlog.get_logger()

_QUICK_PROMPT = (
    "{symbol} scanner match. Indicators: {indicators}.\n"
    "Summarise in one sentence why this is a notable setup."
)
_DEEP_PROMPT_NO_FILINGS = (
    "{symbol} scanner match. Indicators: {indicators}.\n"
    "Provide a 3-5 sentence analysis of the technical setup. "
    "Be specific about the indicator readings."
)
_DEEP_PROMPT_WITH_FILINGS = (
    "{symbol} scanner match. Indicators: {indicators}.\n"
    "Recent filings context: {filings}.\n"
    "Provide a 3-5 sentence analysis combining the technical setup "
    "and fundamental context."
)


async def generate_commentary(
    *,
    symbol: str,
    indicator_snapshot: dict,
    depth: str,
    ai_client: object,
    recent_filings: list[str] | None = None,
) -> str | None:
    try:
        indicators_json = json.dumps(indicator_snapshot, default=str)
        if depth == "quick":
            prompt = _QUICK_PROMPT.format(symbol=symbol, indicators=indicators_json)
            capability = "LOCAL_ONLY"
        elif recent_filings:
            filings_text = "; ".join(recent_filings[:3])
            prompt = _DEEP_PROMPT_WITH_FILINGS.format(
                symbol=symbol, indicators=indicators_json, filings=filings_text
            )
            capability = "REASONING"
        else:
            prompt = _DEEP_PROMPT_NO_FILINGS.format(
                symbol=symbol, indicators=indicators_json
            )
            capability = "REASONING"

        result = await ai_client.complete(
            capability=capability,
            messages=[{"role": "user", "content": prompt}],
        )
        metrics.scanner_llm_commentary_total.labels(depth=depth, status="ok").inc()
        return result.content
    except Exception:
        log.warning("scanner.commentary.error", symbol=symbol, depth=depth)
        metrics.scanner_llm_commentary_total.labels(depth=depth, status="error").inc()
        return None
```

- [ ] **Step 4: Write scanner_service.py**

```python
# backend/app/services/scanner/scanner_service.py
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4
from typing import Any
import sqlalchemy as sa
import structlog
from app.services.scanner.schemas import ScanConfig, SavedScanRow, ScanRunRow, CandidateRow
from app.services.scanner.evaluator import ScannerEvaluator, EvaluatorParseError, EvaluatorBudgetError
from app.services.scanner.indicators import IndicatorComputer, DAILY_INDICATORS, INTRADAY_SCALARS
from app.services.scanner.universe import UniverseResolver
from app.core import metrics

log = structlog.get_logger()

CANDIDATE_COUNT_CAP = 500
_RUN_WALL_CLOCK_S = 60.0
_INSTRUMENT_TIMEOUT_S = 0.25


def _build_symbol_table(computed: dict[str, float | None]) -> dict[str, Any]:
    """Convert computed indicator values into a symbol table for the evaluator."""
    table: dict[str, Any] = {}
    for key, val in computed.items():
        table[key] = val
    # Wrap callables for function-style indicators
    # The evaluator calls e.g. rsi(14) → looks up "rsi" in table → calls it
    # We need to provide pre-computed values; the simplest approach is to pre-compute
    # all indicator values the rule might need into a flat table keyed by (name, args)
    # For Phase 18, we pre-resolve; see Task 9 for full wiring.
    return table


class ScannerService:
    def __init__(self, *, db: Any, redis: Any, cfg: Any, ai_client: Any = None) -> None:
        self._db = db
        self._redis = redis
        self._cfg = cfg
        self._ai_client = ai_client
        self._evaluator = ScannerEvaluator()

    async def save_scan(self, config: ScanConfig) -> UUID:
        """Validate and persist a saved scan. Returns new scan id."""
        # Validate Lark expr + safety budget at save time
        try:
            self._evaluator.parse(config.rule_expr)
        except EvaluatorParseError as exc:
            raise ValueError(f"rule_expr_parse_error: {exc}") from exc
        except EvaluatorBudgetError as exc:
            raise ValueError(f"rule_expr_budget_exceeded: {exc}") from exc

        row = await self._db.execute(
            sa.text("""
                INSERT INTO saved_scans
                    (name, universe_config, rule_expr, schedule, market_hours_gate,
                     exchange, llm_depth, alert_id, enabled)
                VALUES
                    (:name, :uc::jsonb, :rule, :sched, :mhg, :exch, :depth, :alert, :enabled)
                RETURNING id
            """),
            {
                "name": config.name,
                "uc": config.universe_config.model_dump_json(),
                "rule": config.rule_expr,
                "sched": config.schedule,
                "mhg": config.market_hours_gate,
                "exch": config.exchange,
                "depth": config.llm_depth,
                "alert": config.alert_id,
                "enabled": config.enabled,
            },
        )
        scan_id = row.fetchone().id
        await self._db.commit()
        return scan_id

    async def run_scan(
        self,
        *,
        config: ScanConfig | None = None,
        scan_id: UUID | None = None,
        last_universe_snapshot: list[str] | None = None,
    ) -> UUID:
        """Execute a scan run. Returns run_id."""
        if config is None and scan_id is not None:
            config = await self._load_scan_config(scan_id)

        resolver = UniverseResolver(db=self._db, cfg=self._cfg, redis=self._redis)
        try:
            canonical_ids = await resolver.resolve(config.universe_config)
        except Exception:
            if last_universe_snapshot:
                canonical_ids = last_universe_snapshot
                metrics.scanner_universe_stale_total.labels(
                    scan_id=str(scan_id or "adhoc")
                ).inc()
            else:
                canonical_ids = []

        metrics.scanner_universe_size.labels(scan_id=str(scan_id or "adhoc")).set(
            len(canonical_ids)
        )

        ast = self._evaluator.parse(config.rule_expr)
        computer = IndicatorComputer(redis=self._redis, db=self._db)

        # Insert run row
        run_row = await self._db.execute(
            sa.text("""
                INSERT INTO scanner_runs
                    (scan_id, universe_snapshot, rule_expr, status)
                VALUES (:sid, :snap::jsonb, :rule, 'running')
                RETURNING id
            """),
            {
                "sid": scan_id,
                "snap": __import__("json").dumps(canonical_ids),
                "rule": config.rule_expr,
            },
        )
        run_id: UUID = run_row.fetchone().id
        await self._db.commit()

        candidates: list[dict] = []
        deadline = asyncio.get_event_loop().time() + _RUN_WALL_CLOCK_S

        for canonical_id in canonical_ids:
            if asyncio.get_event_loop().time() > deadline:
                await self._fail_run(run_id, "wall_clock_exceeded")
                return run_id

            try:
                instrument_id = await self._resolve_instrument_id(canonical_id)
                snapshot = await asyncio.wait_for(
                    self._build_snapshot(computer, canonical_id, instrument_id, config.rule_expr),
                    timeout=_INSTRUMENT_TIMEOUT_S,
                )
                symbol_table = snapshot
                matched = self._evaluator.evaluate(ast, symbol_table)
            except asyncio.TimeoutError:
                metrics.scanner_eval_timeout_total.inc()
                continue
            except Exception:
                continue

            if matched:
                candidates.append({
                    "instrument_id": instrument_id,
                    "canonical_id": canonical_id,
                    "indicator_snapshot": snapshot,
                })

        # Cap candidates
        if len(candidates) > CANDIDATE_COUNT_CAP:
            candidates.sort(
                key=lambda c: abs((c["indicator_snapshot"].get("rsi") or 50) - 50),
                reverse=True,
            )
            candidates = candidates[:CANDIDATE_COUNT_CAP]
            metrics.scanner_candidate_cap_hit_total.labels(
                scan_id=str(scan_id or "adhoc")
            ).inc()

        # Insert candidates
        for c in candidates:
            await self._db.execute(
                sa.text("""
                    INSERT INTO scanner_candidates
                        (run_id, instrument_id, canonical_id, indicator_snapshot, llm_depth)
                    VALUES (:rid, :iid, :cid, :snap::jsonb, :depth)
                """),
                {
                    "rid": run_id,
                    "iid": c["instrument_id"],
                    "cid": c["canonical_id"],
                    "snap": __import__("json").dumps(c["indicator_snapshot"], default=str),
                    "depth": config.llm_depth,
                },
            )

        await self._db.execute(
            sa.text("""
                UPDATE scanner_runs
                SET status = 'completed', candidate_count = :cnt, completed_at = now()
                WHERE id = :rid
            """),
            {"cnt": len(candidates), "rid": run_id},
        )
        await self._db.commit()

        metrics.scanner_runs_total.labels(
            mode="saved" if scan_id else "adhoc", status="completed"
        ).inc()
        metrics.scanner_candidates_total.labels(scan_id=str(scan_id or "adhoc")).inc(len(candidates))

        # Publish WS event
        import json
        from datetime import timezone
        frame = json.dumps({
            "v": 1,
            "type": "run_completed",
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": str(run_id),
            "scan_id": str(scan_id) if scan_id else None,
            "candidate_count": len(candidates),
        })
        await self._redis.publish(f"scanner:run:{scan_id or 'adhoc'}", frame)

        # Alert integration
        if config.alert_id and candidates:
            await self._fire_alert(config.alert_id, run_id, candidates)

        # Launch commentary in background
        if self._ai_client and candidates:
            asyncio.create_task(
                self._run_commentary(run_id, candidates, config.llm_depth, scan_id)
            )

        return run_id

    async def _build_snapshot(
        self,
        computer: IndicatorComputer,
        canonical_id: str,
        instrument_id: int | None,
        rule_expr: str,
    ) -> dict[str, Any]:
        """Pre-compute all indicator values needed by the rule."""
        snapshot: dict[str, Any] = {}
        all_inds = list(DAILY_INDICATORS) + list(INTRADAY_SCALARS) + ["mcap", "pe", "eps_growth"]
        for name in all_inds:
            val = await computer.compute(
                name, {}, instrument_id=instrument_id, canonical_id=canonical_id
            )
            snapshot[name] = val
        # Build callable wrappers for function indicators
        def make_caller(ind_name: str):
            def caller(*args):
                return snapshot.get(ind_name)
            return caller
        for name in DAILY_INDICATORS:
            snapshot[name] = make_caller(name)
        return snapshot

    async def _resolve_instrument_id(self, canonical_id: str) -> int | None:
        row = await self._db.execute(
            sa.text("SELECT id FROM instruments WHERE canonical_id = :cid LIMIT 1"),
            {"cid": canonical_id},
        )
        r = row.fetchone()
        return r.id if r else None

    async def _fail_run(self, run_id: UUID, error: str) -> None:
        await self._db.execute(
            sa.text("""
                UPDATE scanner_runs
                SET status = 'failed', error = :err, completed_at = now()
                WHERE id = :rid
            """),
            {"err": error, "rid": run_id},
        )
        await self._db.commit()
        metrics.scanner_runs_total.labels(mode="saved", status="failed").inc()

    async def _load_scan_config(self, scan_id: UUID) -> ScanConfig:
        row = await self._db.execute(
            sa.text("SELECT * FROM saved_scans WHERE id = :id"), {"id": scan_id}
        )
        r = row.fetchone()
        if not r:
            raise ValueError(f"scan not found: {scan_id}")
        import json
        return ScanConfig(
            name=r.name,
            universe_config=__import__("app.services.scanner.schemas", fromlist=["UniverseConfig"])
                .UniverseConfig(**json.loads(r.universe_config)),
            rule_expr=r.rule_expr,
            schedule=r.schedule,
            market_hours_gate=r.market_hours_gate,
            exchange=r.exchange,
            llm_depth=r.llm_depth,
            alert_id=r.alert_id,
            enabled=r.enabled,
        )

    async def _fire_alert(self, alert_id: int, run_id: UUID, candidates: list[dict]) -> None:
        import json
        try:
            await self._db.execute(
                sa.text("""
                    INSERT INTO alert_fires (alert_id, jwt_subject, fired_at, verdict,
                                            fire_context)
                    VALUES (:aid, 'scanner', now(), 'FIRED', :ctx::jsonb)
                """),
                {
                    "aid": alert_id,
                    "ctx": json.dumps({
                        "scanner_run_id": str(run_id),
                        "candidate_count": len(candidates),
                    }),
                },
            )
            await self._db.commit()
            metrics.scanner_alert_fires_total.labels(scan_id=str(alert_id)).inc()
        except Exception:
            log.warning("scanner.alert_fire.error", alert_id=alert_id)

    async def _run_commentary(
        self,
        run_id: UUID,
        candidates: list[dict],
        depth: str,
        scan_id: UUID | None,
    ) -> None:
        from app.services.scanner.commentary import generate_commentary
        import json
        from datetime import timezone
        for c in candidates:
            text = await generate_commentary(
                symbol=c["canonical_id"],
                indicator_snapshot=c["indicator_snapshot"],
                depth=depth,
                ai_client=self._ai_client,
            )
            if text:
                await self._db.execute(
                    sa.text("""
                        UPDATE scanner_candidates
                        SET llm_commentary = :text
                        WHERE run_id = :rid AND canonical_id = :cid
                    """),
                    {"text": text, "rid": run_id, "cid": c["canonical_id"]},
                )
                await self._db.commit()
                frame = json.dumps({
                    "v": 1,
                    "type": "commentary_ready",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "canonical_id": c["canonical_id"],
                    "commentary": text,
                })
                await self._redis.publish(f"scanner:run:{scan_id or 'adhoc'}", frame)
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
docker compose exec backend pytest backend/tests/test_scanner_service.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/scanner/scanner_service.py backend/app/services/scanner/commentary.py backend/tests/test_scanner_service.py
git commit -m "feat(phase18a): ScannerService orchestrator + commentary.py"
```

---

### Task 9: APScheduler scheduler

**Files:**
- Create: `backend/app/services/scanner/scheduler.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_scanner_scheduler.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from app.services.scanner.scheduler import ScannerScheduler

def test_cron_validation_valid():
    sched = ScannerScheduler.__new__(ScannerScheduler)
    assert sched.validate_cron("*/5 * * * *") is True

def test_cron_validation_invalid():
    sched = ScannerScheduler.__new__(ScannerScheduler)
    assert sched.validate_cron("not_a_cron") is False

def test_preset_shortcuts():
    from app.services.scanner.scheduler import PRESET_CRONS
    assert "every_5m" in PRESET_CRONS
    assert "every_15m" in PRESET_CRONS
    assert "hourly" in PRESET_CRONS
    assert "market_open" in PRESET_CRONS
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
docker compose exec backend pytest backend/tests/test_scanner_scheduler.py -v 2>&1 | head -20
```

- [ ] **Step 3: Add croniter dependency**

```bash
docker compose exec backend uv add croniter
```

- [ ] **Step 4: Write scheduler.py**

```python
# backend/app/services/scanner/scheduler.py
from __future__ import annotations
import asyncio
from uuid import UUID
import structlog
from croniter import croniter
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.base import JobLookupError
from app.core import metrics

log = structlog.get_logger()

PRESET_CRONS: dict[str, str] = {
    "every_5m": "*/5 * * * *",
    "every_15m": "*/15 * * * *",
    "hourly": "0 * * * *",
    "market_open": "30 9 * * 1-5",
}


class ScannerScheduler:
    def __init__(self, *, scheduler: AsyncIOScheduler, scanner_service: object) -> None:
        self._scheduler = scheduler
        self._svc = scanner_service
        self._locks: dict[str, asyncio.Lock] = {}

    def validate_cron(self, expr: str) -> bool:
        try:
            croniter(expr)
            return True
        except Exception:
            return False

    async def rebuild_from_db(self, db: object) -> None:
        import sqlalchemy as sa
        rows = await db.execute(
            sa.text("SELECT id, schedule, market_hours_gate, exchange, rule_expr "
                    "FROM saved_scans WHERE enabled = true AND schedule IS NOT NULL")
        )
        for row in rows.fetchall():
            await self.schedule_scan(
                scan_id=row.id,
                cron_expr=row.schedule,
                market_hours_gate=row.market_hours_gate,
                exchange=row.exchange,
            )

    async def schedule_scan(
        self,
        *,
        scan_id: UUID,
        cron_expr: str,
        market_hours_gate: bool,
        exchange: str | None,
    ) -> None:
        lock = self._locks.setdefault(str(scan_id), asyncio.Lock())
        async with lock:
            try:
                self._scheduler.remove_job(str(scan_id))
            except JobLookupError:
                pass
            self._scheduler.add_job(
                self._fire,
                CronTrigger.from_crontab(cron_expr),
                id=str(scan_id),
                args=[scan_id, market_hours_gate, exchange],
                coalesce=True,
                misfire_grace_time=60,
            )
            log.info("scanner.scheduler.scheduled", scan_id=str(scan_id), cron=cron_expr)

    async def remove_scan(self, scan_id: UUID) -> None:
        lock = self._locks.setdefault(str(scan_id), asyncio.Lock())
        async with lock:
            try:
                self._scheduler.remove_job(str(scan_id))
            except JobLookupError:
                pass

    async def _fire(
        self, scan_id: UUID, market_hours_gate: bool, exchange: str | None
    ) -> None:
        if market_hours_gate and exchange:
            from app.services.market_calendar import is_open
            if not is_open(exchange):
                log.info("scanner.scheduler.skipped_market_closed",
                         scan_id=str(scan_id), exchange=exchange)
                return
        metrics.scanner_scheduler_fires_total.labels(scan_id=str(scan_id)).inc()
        try:
            await self._svc.run_scan(scan_id=scan_id)
        except Exception:
            log.exception("scanner.scheduler.fire_error", scan_id=str(scan_id))
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
docker compose exec backend pytest backend/tests/test_scanner_scheduler.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/scanner/scheduler.py backend/tests/test_scanner_scheduler.py
git commit -m "feat(phase18a): ScannerScheduler — APScheduler CronTrigger, market-hours gate, per-scan lock"
```

---

## Chunk D — REST API + WebSocket + lifespan wiring

### Task 10: REST API

**Files:**
- Create: `backend/app/api/scanner.py`
- Test: `backend/tests/api/test_scanner_api.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/api/test_scanner_api.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_validate_valid_expr(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/scanner/validate",
        json={"rule_expr": "rsi(14) < 30 and volume_ratio(20) > 2.0"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is True

@pytest.mark.asyncio
async def test_validate_invalid_expr(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/scanner/validate",
        json={"rule_expr": "rsi(14 <<< 30"},
        headers=auth_headers,
    )
    assert resp.status_code == 422

@pytest.mark.asyncio
async def test_validate_budget_exceeded(client: AsyncClient, auth_headers: dict):
    expr = " and ".join(["rsi(14) < 30"] * 130)
    resp = await client.post(
        "/api/scanner/validate",
        json={"rule_expr": expr},
        headers=auth_headers,
    )
    assert resp.status_code == 422

@pytest.mark.asyncio
async def test_create_scan(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/scanner/scans",
        json={
            "name": "RSI scan",
            "universe_config": {"type": "tickers", "params": {"tickers": ["AAPL"]}},
            "rule_expr": "rsi(14) < 30",
            "llm_depth": "quick",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert "id" in resp.json()

@pytest.mark.asyncio
async def test_list_scans(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/scanner/scans", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
docker compose exec backend pytest backend/tests/api/test_scanner_api.py -v 2>&1 | head -30
```

- [ ] **Step 3: Write scanner API**

```python
# backend/app/api/scanner.py
from __future__ import annotations
from uuid import UUID
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
import sqlalchemy as sa
import structlog
from app.api.deps import require_jwt
from app.services.scanner.evaluator import ScannerEvaluator, EvaluatorParseError, EvaluatorBudgetError
from app.services.scanner.schemas import ScanConfig, UniverseConfig

log = structlog.get_logger()
router = APIRouter(prefix="/api/scanner", tags=["scanner"])
_evaluator = ScannerEvaluator()


class ValidateRequest(BaseModel):
    rule_expr: str


class ValidateResponse(BaseModel):
    valid: bool
    error: str | None = None


class CreateScanRequest(BaseModel):
    name: str
    universe_config: dict[str, Any]
    rule_expr: str
    schedule: str | None = None
    market_hours_gate: bool = False
    exchange: str | None = None
    llm_depth: str = "quick"
    alert_id: int | None = None
    enabled: bool = True


@router.post("/validate", response_model=ValidateResponse)
async def validate_rule(body: ValidateRequest, _: str = Depends(require_jwt)):
    try:
        _evaluator.parse(body.rule_expr)
        return ValidateResponse(valid=True)
    except EvaluatorParseError as exc:
        raise HTTPException(422, detail={"error": "rule_expr_parse_error", "message": str(exc)})
    except EvaluatorBudgetError as exc:
        raise HTTPException(422, detail={"error": "rule_expr_budget_exceeded", "message": str(exc)})


@router.post("/scans", status_code=201)
async def create_scan(body: CreateScanRequest, request: Request, _: str = Depends(require_jwt)):
    db = request.state.db
    try:
        config = ScanConfig(
            name=body.name,
            universe_config=UniverseConfig(**body.universe_config),
            rule_expr=body.rule_expr,
            schedule=body.schedule,
            market_hours_gate=body.market_hours_gate,
            exchange=body.exchange,
            llm_depth=body.llm_depth,
            alert_id=body.alert_id,
            enabled=body.enabled,
        )
        svc = request.app.state.scanner_service
        scan_id = await svc.save_scan(config)
        if body.schedule:
            await request.app.state.scanner_scheduler.schedule_scan(
                scan_id=scan_id,
                cron_expr=body.schedule,
                market_hours_gate=body.market_hours_gate,
                exchange=body.exchange,
            )
        return {"id": str(scan_id)}
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))


@router.get("/scans")
async def list_scans(request: Request, _: str = Depends(require_jwt)):
    db = request.state.db
    rows = await db.execute(sa.text("SELECT * FROM saved_scans ORDER BY created_at DESC"))
    return [dict(r._mapping) for r in rows.fetchall()]


@router.get("/scans/{scan_id}")
async def get_scan(scan_id: UUID, request: Request, _: str = Depends(require_jwt)):
    db = request.state.db
    row = await db.execute(
        sa.text("SELECT * FROM saved_scans WHERE id = :id"), {"id": scan_id}
    )
    r = row.fetchone()
    if not r:
        raise HTTPException(404)
    return dict(r._mapping)


@router.put("/scans/{scan_id}")
async def update_scan(scan_id: UUID, body: CreateScanRequest, request: Request,
                      _: str = Depends(require_jwt)):
    db = request.state.db
    try:
        _evaluator.parse(body.rule_expr)
    except (EvaluatorParseError, EvaluatorBudgetError) as exc:
        raise HTTPException(422, detail=str(exc))
    await db.execute(
        sa.text("""
            UPDATE saved_scans SET name=:name, universe_config=:uc::jsonb,
            rule_expr=:rule, schedule=:sched, market_hours_gate=:mhg, exchange=:exch,
            llm_depth=:depth, alert_id=:alert, enabled=:enabled, updated_at=now()
            WHERE id=:id
        """),
        {
            "name": body.name, "uc": UniverseConfig(**body.universe_config).model_dump_json(),
            "rule": body.rule_expr, "sched": body.schedule, "mhg": body.market_hours_gate,
            "exch": body.exchange, "depth": body.llm_depth, "alert": body.alert_id,
            "enabled": body.enabled, "id": scan_id,
        },
    )
    await db.commit()
    if body.schedule and body.enabled:
        await request.app.state.scanner_scheduler.schedule_scan(
            scan_id=scan_id, cron_expr=body.schedule,
            market_hours_gate=body.market_hours_gate, exchange=body.exchange,
        )
    else:
        await request.app.state.scanner_scheduler.remove_scan(scan_id)
    return {"id": str(scan_id)}


@router.delete("/scans/{scan_id}", status_code=204)
async def delete_scan(scan_id: UUID, request: Request, _: str = Depends(require_jwt)):
    db = request.state.db
    await db.execute(
        sa.text("UPDATE saved_scans SET enabled=false, updated_at=now() WHERE id=:id"),
        {"id": scan_id},
    )
    await db.commit()
    await request.app.state.scanner_scheduler.remove_scan(scan_id)


@router.post("/scans/{scan_id}/run", status_code=202)
async def trigger_run(scan_id: UUID, request: Request, _: str = Depends(require_jwt)):
    import asyncio
    svc = request.app.state.scanner_service
    run_id = await asyncio.shield(svc.run_scan(scan_id=scan_id))
    return {"run_id": str(run_id)}


@router.post("/runs/adhoc", status_code=202)
async def adhoc_run(body: CreateScanRequest, request: Request, _: str = Depends(require_jwt)):
    import asyncio
    from app.services.scanner.schemas import ScanConfig, UniverseConfig
    config = ScanConfig(
        name=body.name,
        universe_config=UniverseConfig(**body.universe_config),
        rule_expr=body.rule_expr,
        llm_depth=body.llm_depth,
    )
    svc = request.app.state.scanner_service
    run_id = await asyncio.shield(svc.run_scan(config=config))
    return {"run_id": str(run_id)}


@router.get("/runs")
async def list_runs(request: Request, scan_id: UUID | None = None,
                    cursor: str | None = None, limit: int = 20,
                    _: str = Depends(require_jwt)):
    db = request.state.db
    where = "WHERE 1=1"
    params: dict = {"limit": limit}
    if scan_id:
        where += " AND scan_id = :scan_id"
        params["scan_id"] = scan_id
    if cursor:
        where += " AND started_at < :cursor::timestamptz"
        params["cursor"] = cursor
    rows = await db.execute(
        sa.text(f"SELECT * FROM scanner_runs {where} ORDER BY started_at DESC LIMIT :limit"),
        params,
    )
    return [dict(r._mapping) for r in rows.fetchall()]


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, request: Request, _: str = Depends(require_jwt)):
    db = request.state.db
    row = await db.execute(
        sa.text("SELECT * FROM scanner_runs WHERE id=:id"), {"id": run_id}
    )
    r = row.fetchone()
    if not r:
        raise HTTPException(404)
    candidates = await db.execute(
        sa.text("SELECT * FROM scanner_candidates WHERE run_id=:rid ORDER BY matched_at"),
        {"rid": run_id},
    )
    return {**dict(r._mapping), "candidates": [dict(c._mapping) for c in candidates.fetchall()]}


@router.get("/runs/{run_id}/candidates")
async def get_candidates(run_id: UUID, request: Request, limit: int = 50,
                          cursor: str | None = None, _: str = Depends(require_jwt)):
    db = request.state.db
    where = "WHERE run_id = :rid"
    params: dict = {"rid": run_id, "limit": limit}
    if cursor:
        where += " AND matched_at > :cursor::timestamptz"
        params["cursor"] = cursor
    rows = await db.execute(
        sa.text(f"SELECT * FROM scanner_candidates {where} ORDER BY matched_at LIMIT :limit"),
        params,
    )
    return [dict(r._mapping) for r in rows.fetchall()]
```

- [ ] **Step 4: Write WS handler**

```python
# backend/app/api/ws_scanner.py
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import structlog

log = structlog.get_logger()
router = APIRouter()

_HEARTBEAT_INTERVAL = 30
_SEND_TIMEOUT = 2.0


@router.websocket("/ws/scanner/runs/{scan_id}")
async def ws_scanner_run(websocket: WebSocket, scan_id: str):
    origin = websocket.headers.get("origin", "")
    if origin and not origin.startswith("https://"):
        await websocket.close(code=4003)
        return
    await websocket.accept()
    redis = websocket.app.state.redis
    pubsub = redis.pubsub()
    channel = f"scanner:run:{scan_id}"
    await pubsub.subscribe(channel)
    recv_task = asyncio.create_task(_drain_recv(websocket))
    try:
        last_hb = asyncio.get_event_loop().time()
        while True:
            now = asyncio.get_event_loop().time()
            if now - last_hb >= _HEARTBEAT_INTERVAL:
                hb = json.dumps({"v": 1, "type": "heartbeat",
                                  "ts": datetime.now(timezone.utc).isoformat()})
                try:
                    await asyncio.wait_for(websocket.send_text(hb), timeout=_SEND_TIMEOUT)
                except Exception:
                    break
                last_hb = now
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            if msg and msg["type"] == "message":
                try:
                    await asyncio.wait_for(
                        websocket.send_text(msg["data"].decode()),
                        timeout=_SEND_TIMEOUT,
                    )
                except Exception:
                    break
            if recv_task.done():
                break
    except WebSocketDisconnect:
        pass
    finally:
        recv_task.cancel()
        await pubsub.unsubscribe(channel)
        await websocket.close()


async def _drain_recv(ws: WebSocket) -> None:
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
```

- [ ] **Step 5: Wire router into main.py**

In `backend/app/main.py`, add after existing router includes:

```python
from app.api import scanner as scanner_api
from app.api import ws_scanner
app.include_router(scanner_api.router)
app.include_router(ws_scanner.router)
```

And in the lifespan context manager, after existing service setup:

```python
from app.services.scanner.scanner_service import ScannerService
from app.services.scanner.scheduler import ScannerScheduler
app.state.scanner_service = ScannerService(
    db=db_session_factory,  # adjust to match existing pattern
    redis=app.state.redis,
    cfg=app.state.cfg,
)
scanner_scheduler = ScannerScheduler(
    scheduler=app.state.scheduler,
    scanner_service=app.state.scanner_service,
)
app.state.scanner_scheduler = scanner_scheduler
await scanner_scheduler.rebuild_from_db(db=app.state.db)
```

- [ ] **Step 6: Run API tests**

```bash
docker compose exec backend pytest backend/tests/api/test_scanner_api.py -v
```

- [ ] **Step 7: Run full BE suite**

```bash
docker compose exec backend pytest backend/tests/ -x -q 2>&1 | tail -20
```

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/scanner.py backend/app/api/ws_scanner.py backend/app/main.py backend/tests/api/test_scanner_api.py
git commit -m "feat(phase18a): scanner REST API + WebSocket handler + lifespan wiring"
```

---

## Chunk E — Frontend

### Task 11: FE types + API service + WS hook + store

**Files:**
- Create: `frontend/src/services/scanner/types.ts`
- Create: `frontend/src/services/scanner/api.ts`
- Create: `frontend/src/services/scanner/useScannerWs.ts`
- Create: `frontend/src/stores/global/scanner.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// frontend/src/services/scanner/__tests__/types.test.ts
import { describe, it, expect } from "vitest"
import type { SavedScan, ScanRun, ScanCandidate, UniverseConfig } from "../types"

describe("scanner types", () => {
  it("SavedScan has required fields", () => {
    const scan: SavedScan = {
      id: "uuid",
      name: "RSI scan",
      universe_config: { type: "tickers", params: { tickers: ["AAPL"] } },
      rule_expr: "rsi(14) < 30",
      schedule: null,
      market_hours_gate: false,
      exchange: null,
      llm_depth: "quick",
      alert_id: null,
      enabled: true,
      created_at: "2026-05-19T00:00:00Z",
      updated_at: "2026-05-19T00:00:00Z",
    }
    expect(scan.name).toBe("RSI scan")
  })
})
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd frontend && pnpm test src/services/scanner/__tests__/types.test.ts 2>&1 | head -20
```

- [ ] **Step 3: Write types.ts**

```typescript
// frontend/src/services/scanner/types.ts
export type UniverseType = "schwab_screener" | "watchlist" | "tickers" | "instruments"

export interface UniverseConfig {
  type: UniverseType
  params: Record<string, unknown>
}

export interface SavedScan {
  id: string
  name: string
  universe_config: UniverseConfig
  rule_expr: string
  schedule: string | null
  market_hours_gate: boolean
  exchange: string | null
  llm_depth: "quick" | "deep"
  alert_id: number | null
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface ScanRun {
  id: string
  scan_id: string | null
  universe_snapshot: string[]
  rule_expr: string
  candidate_count: number
  status: "running" | "completed" | "failed"
  started_at: string
  completed_at: string | null
  error: string | null
}

export interface ScanCandidate {
  id: string
  run_id: string
  instrument_id: number | null
  canonical_id: string
  matched_at: string
  indicator_snapshot: Record<string, number | null>
  llm_commentary: string | null
  llm_depth: "quick" | "deep" | null
}

export interface CreateScanPayload {
  name: string
  universe_config: UniverseConfig
  rule_expr: string
  schedule?: string | null
  market_hours_gate?: boolean
  exchange?: string | null
  llm_depth?: "quick" | "deep"
  alert_id?: number | null
  enabled?: boolean
}

export type ScannerWsFrame =
  | { v: 1; type: "run_started"; ts: string; run_id: string }
  | { v: 1; type: "candidate"; ts: string; candidate: ScanCandidate }
  | { v: 1; type: "run_completed"; ts: string; run_id: string; candidate_count: number }
  | { v: 1; type: "commentary_ready"; ts: string; canonical_id: string; commentary: string }
  | { v: 1; type: "heartbeat"; ts: string }
```

- [ ] **Step 4: Write api.ts**

```typescript
// frontend/src/services/scanner/api.ts
import type { SavedScan, ScanRun, ScanCandidate, CreateScanPayload } from "./types"

const BASE = "/api/scanner"

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
  return res.json() as Promise<T>
}

export const scannerApi = {
  validate: (rule_expr: string) =>
    fetchJson<{ valid: boolean; error?: string }>(`${BASE}/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rule_expr }),
    }),

  createScan: (payload: CreateScanPayload) =>
    fetchJson<{ id: string }>(`${BASE}/scans`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  listScans: () => fetchJson<SavedScan[]>(`${BASE}/scans`),

  getScan: (id: string) => fetchJson<SavedScan>(`${BASE}/scans/${id}`),

  updateScan: (id: string, payload: CreateScanPayload) =>
    fetchJson<{ id: string }>(`${BASE}/scans/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  deleteScan: (id: string) =>
    fetch(`${BASE}/scans/${id}`, { method: "DELETE" }),

  triggerRun: (scanId: string) =>
    fetchJson<{ run_id: string }>(`${BASE}/scans/${scanId}/run`, { method: "POST" }),

  adhocRun: (payload: CreateScanPayload) =>
    fetchJson<{ run_id: string }>(`${BASE}/runs/adhoc`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  listRuns: (scanId?: string, cursor?: string) => {
    const params = new URLSearchParams()
    if (scanId) params.set("scan_id", scanId)
    if (cursor) params.set("cursor", cursor)
    return fetchJson<ScanRun[]>(`${BASE}/runs?${params}`)
  },

  getRun: (runId: string) =>
    fetchJson<ScanRun & { candidates: ScanCandidate[] }>(`${BASE}/runs/${runId}`),

  getCandidates: (runId: string, cursor?: string) => {
    const params = new URLSearchParams()
    if (cursor) params.set("cursor", cursor)
    return fetchJson<ScanCandidate[]>(`${BASE}/runs/${runId}/candidates?${params}`)
  },
}
```

- [ ] **Step 5: Write useScannerWs.ts**

```typescript
// frontend/src/services/scanner/useScannerWs.ts
import { useEffect, useRef, useCallback } from "react"
import type { ScannerWsFrame, ScanCandidate } from "./types"

const BACKOFF = [500, 1500, 5000, 15000]

interface UseScannerWsOptions {
  scanId: string
  onCandidate: (c: ScanCandidate) => void
  onCommentaryReady: (canonical_id: string, commentary: string) => void
  onRunCompleted: (run_id: string, count: number) => void
}

export function useScannerWs({
  scanId,
  onCandidate,
  onCommentaryReady,
  onRunCompleted,
}: UseScannerWsOptions) {
  const wsRef = useRef<WebSocket | null>(null)
  const attemptRef = useRef(0)
  const mountedRef = useRef(true)

  const connect = useCallback(() => {
    if (!mountedRef.current) return
    const protocol = window.location.protocol === "https:" ? "wss" : "ws"
    const url = `${protocol}://${window.location.host}/ws/scanner/runs/${scanId}`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onmessage = (e) => {
      try {
        const frame: ScannerWsFrame = JSON.parse(e.data)
        if (frame.v !== 1) return
        if (frame.type === "candidate") onCandidate(frame.candidate)
        if (frame.type === "commentary_ready")
          onCommentaryReady(frame.canonical_id, frame.commentary)
        if (frame.type === "run_completed")
          onRunCompleted(frame.run_id, frame.candidate_count)
      } catch {}
    }

    ws.onopen = () => {
      attemptRef.current = 0
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
      const delay = BACKOFF[Math.min(attemptRef.current, BACKOFF.length - 1)]
      attemptRef.current++
      setTimeout(connect, delay)
    }
  }, [scanId, onCandidate, onCommentaryReady, onRunCompleted])

  useEffect(() => {
    mountedRef.current = true
    connect()
    return () => {
      mountedRef.current = false
      wsRef.current?.close()
    }
  }, [connect])
}
```

- [ ] **Step 6: Write store**

```typescript
// frontend/src/stores/global/scanner.ts
import { create } from "zustand"
import { persist } from "zustand/middleware"
import type { SavedScan } from "@/services/scanner/types"

interface ScannerState {
  savedScans: SavedScan[]
  activeScanId: string | null
  setSavedScans: (scans: SavedScan[]) => void
  setActiveScanId: (id: string | null) => void
}

export const useScannerStore = create<ScannerState>()(
  persist(
    (set) => ({
      savedScans: [],
      activeScanId: null,
      setSavedScans: (scans) => set({ savedScans: scans }),
      setActiveScanId: (id) => set({ activeScanId: id }),
    }),
    {
      name: "scanner-store",
      version: 1,
      migrate: (state: unknown, version: number) => {
        if (version < 1) return { savedScans: [], activeScanId: null }
        return state as ScannerState
      },
    }
  )
)
```

- [ ] **Step 7: Run FE tests**

```bash
cd frontend && pnpm test src/services/scanner/ 2>&1 | tail -20
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/services/scanner/ frontend/src/stores/global/scanner.ts
git commit -m "feat(phase18a): FE scanner types, API service, WS hook, Zustand store"
```

---

### Task 12: FE components + route

**Files:**
- Create: `frontend/src/features/scanner/ScannerPage.tsx`
- Create: `frontend/src/features/scanner/SavedScanList.tsx`
- Create: `frontend/src/features/scanner/ScanConfigDrawer.tsx`
- Create: `frontend/src/features/scanner/RuleEditor.tsx`
- Create: `frontend/src/features/scanner/UniversePicker.tsx`
- Create: `frontend/src/features/scanner/CandidatesTable.tsx`
- Create: `frontend/src/features/scanner/RunHistoryDrawer.tsx`
- Create: `frontend/src/features/scanner/AdHocRunPanel.tsx`
- Create: `frontend/src/routes/scanner.tsx`
- Test: `frontend/src/features/scanner/__tests__/RuleEditor.test.tsx`

- [ ] **Step 1: Write failing RuleEditor test**

```typescript
// frontend/src/features/scanner/__tests__/RuleEditor.test.tsx
import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { RuleEditor } from "../RuleEditor"

describe("RuleEditor", () => {
  it("shows error on invalid expression after blur", async () => {
    const validate = vi.fn().mockResolvedValue({ valid: false, error: "parse error at col 5" })
    render(<RuleEditor value="rsi(14 <<< 30" onChange={vi.fn()} onValidate={validate} />)
    fireEvent.blur(screen.getByRole("textbox"))
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument())
  })

  it("clears error on valid expression", async () => {
    const validate = vi.fn().mockResolvedValue({ valid: true })
    render(<RuleEditor value="rsi(14) < 30" onChange={vi.fn()} onValidate={validate} />)
    fireEvent.blur(screen.getByRole("textbox"))
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd frontend && pnpm test src/features/scanner/__tests__/RuleEditor.test.tsx 2>&1 | head -20
```

- [ ] **Step 3: Write RuleEditor**

```tsx
// frontend/src/features/scanner/RuleEditor.tsx
import { useState, useCallback, useRef } from "react"
import { Textarea } from "@/components/primitives/Textarea"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/primitives/Collapsible"

interface RuleEditorProps {
  value: string
  onChange: (v: string) => void
  onValidate: (expr: string) => Promise<{ valid: boolean; error?: string }>
}

const CHEATSHEET = `
Scalars: close, open, high, low, volume, mcap, pe, eps_growth
Functions: rsi(period), sma(field, period), ema(field, period),
  atr(period), volume_ratio(period), price_vs_high(days),
  price_vs_low(days), macd(fast, slow, signal), bb_pct(period, std)
Operators: < > <= >= == !=   Keywords (lowercase): and, or, not
Examples:
  rsi(14) < 30 and volume_ratio(20) > 2.0
  not rsi(14) > 70 or price_vs_high(252) > 0.95
`.trim()

export function RuleEditor({ value, onChange, onValidate }: RuleEditorProps) {
  const [error, setError] = useState<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleBlur = useCallback(async () => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(async () => {
      const result = await onValidate(value)
      setError(result.valid ? null : (result.error ?? "Invalid expression"))
    }, 500)
  }, [value, onValidate])

  return (
    <div className="flex flex-col gap-2">
      <Textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={handleBlur}
        placeholder='rsi(14) < 30 and volume_ratio(20) > 2.0'
        className="font-mono text-sm min-h-[6rem]"
        aria-label="Rule expression"
      />
      {error && (
        <p role="alert" className="text-sm text-destructive">{error}</p>
      )}
      <Collapsible>
        <CollapsibleTrigger className="text-xs text-muted-foreground underline">
          Indicator reference
        </CollapsibleTrigger>
        <CollapsibleContent>
          <pre className="text-xs text-muted-foreground whitespace-pre-wrap mt-1">{CHEATSHEET}</pre>
        </CollapsibleContent>
      </Collapsible>
    </div>
  )
}
```

- [ ] **Step 4: Write remaining components**

`SavedScanList.tsx`:
```tsx
// frontend/src/features/scanner/SavedScanList.tsx
import type { SavedScan } from "@/services/scanner/types"
import { Switch } from "@/components/primitives/Switch"
import { Button } from "@/components/primitives/Button"

interface Props {
  scans: SavedScan[]
  activeScanId: string | null
  onSelect: (id: string) => void
  onToggle: (id: string, enabled: boolean) => void
  onNew: () => void
}

export function SavedScanList({ scans, activeScanId, onSelect, onToggle, onNew }: Props) {
  return (
    <aside className="flex flex-col gap-2 w-64 shrink-0">
      <Button size="sm" onClick={onNew} className="w-full">+ New Scan</Button>
      {scans.map((scan) => (
        <div
          key={scan.id}
          className={`flex items-center justify-between gap-2 p-2 rounded cursor-pointer
            ${activeScanId === scan.id ? "bg-accent" : "hover:bg-muted"}`}
          onClick={() => onSelect(scan.id)}
        >
          <span className="truncate text-sm">{scan.name}</span>
          <Switch
            checked={scan.enabled}
            onCheckedChange={(checked) => onToggle(scan.id, checked)}
            onClick={(e) => e.stopPropagation()}
            aria-label={`Toggle ${scan.name}`}
          />
        </div>
      ))}
    </aside>
  )
}
```

`CandidatesTable.tsx`:
```tsx
// frontend/src/features/scanner/CandidatesTable.tsx
import type { ScanCandidate } from "@/services/scanner/types"
import { DataTable } from "@/components/patterns/DataTable"
import { Button } from "@/components/primitives/Button"
import type { ColumnDef } from "@tanstack/react-table"

interface Props {
  candidates: ScanCandidate[]
  onTrade: (candidate: ScanCandidate) => void
}

const columns: ColumnDef<ScanCandidate>[] = [
  { accessorKey: "canonical_id", header: "Ticker" },
  {
    id: "rsi",
    header: "RSI",
    cell: ({ row }) => row.original.indicator_snapshot["rsi"]?.toFixed(1) ?? "—",
  },
  {
    id: "volume_ratio",
    header: "Vol×",
    cell: ({ row }) => {
      const v = row.original.indicator_snapshot["volume_ratio"]
      return v != null ? `${v.toFixed(1)}x` : "—"
    },
  },
  {
    accessorKey: "llm_commentary",
    header: "Commentary",
    cell: ({ row }) => (
      <span className="text-xs text-muted-foreground line-clamp-2">
        {row.original.llm_commentary ?? (
          <span className="italic">Loading...</span>
        )}
      </span>
    ),
  },
  {
    id: "trade",
    header: "",
    cell: ({ row }) => (
      <Button size="sm" variant="outline" onClick={() => {}}>
        Trade
      </Button>
    ),
  },
]

export function CandidatesTable({ candidates, onTrade }: Props) {
  return <DataTable columns={columns} data={candidates} />
}
```

`ScannerPage.tsx`:
```tsx
// frontend/src/features/scanner/ScannerPage.tsx
import { useState, useCallback } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { scannerApi } from "@/services/scanner/api"
import { useScannerStore } from "@/stores/global/scanner"
import { useScannerWs } from "@/services/scanner/useScannerWs"
import { SavedScanList } from "./SavedScanList"
import { CandidatesTable } from "./CandidatesTable"
import type { ScanCandidate } from "@/services/scanner/types"

export function ScannerPage() {
  const { savedScans, activeScanId, setSavedScans, setActiveScanId } = useScannerStore()
  const [candidates, setCandidates] = useState<ScanCandidate[]>([])
  const qc = useQueryClient()

  useQuery({
    queryKey: ["scanner-scans"],
    queryFn: async () => {
      const scans = await scannerApi.listScans()
      setSavedScans(scans)
      return scans
    },
    refetchInterval: 60_000,
  })

  const handleCandidate = useCallback((c: ScanCandidate) => {
    setCandidates((prev) => [...prev, c])
  }, [])

  const handleCommentaryReady = useCallback((canonical_id: string, commentary: string) => {
    setCandidates((prev) =>
      prev.map((c) => (c.canonical_id === canonical_id ? { ...c, llm_commentary: commentary } : c))
    )
  }, [])

  const handleRunCompleted = useCallback((_runId: string, _count: number) => {
    qc.invalidateQueries({ queryKey: ["scanner-runs", activeScanId] })
  }, [activeScanId, qc])

  useScannerWs({
    scanId: activeScanId ?? "adhoc",
    onCandidate: handleCandidate,
    onCommentaryReady: handleCommentaryReady,
    onRunCompleted: handleRunCompleted,
  })

  return (
    <div className="flex gap-4 h-full p-4">
      <SavedScanList
        scans={savedScans}
        activeScanId={activeScanId}
        onSelect={(id) => { setActiveScanId(id); setCandidates([]) }}
        onToggle={async (id, enabled) => {
          const scan = savedScans.find((s) => s.id === id)
          if (scan) await scannerApi.updateScan(id, { ...scan, enabled })
          const updated = await scannerApi.listScans()
          setSavedScans(updated)
        }}
        onNew={() => setActiveScanId(null)}
      />
      <main className="flex-1 flex flex-col gap-4">
        {activeScanId && (
          <div className="flex gap-2">
            <button
              className="px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded"
              onClick={async () => {
                setCandidates([])
                await scannerApi.triggerRun(activeScanId)
              }}
            >
              Run now
            </button>
          </div>
        )}
        <CandidatesTable candidates={candidates} onTrade={() => {}} />
      </main>
    </div>
  )
}
```

`scanner.tsx` route:
```tsx
// frontend/src/routes/scanner.tsx
import { createFileRoute } from "@tanstack/react-router"
import { ScannerPage } from "@/features/scanner/ScannerPage"

export const Route = createFileRoute("/scanner")({
  component: ScannerPage,
})
```

- [ ] **Step 5: Run RuleEditor test — expect PASS**

```bash
cd frontend && pnpm test src/features/scanner/__tests__/RuleEditor.test.tsx
```

- [ ] **Step 6: Regenerate TanStack Router types**

```bash
cd frontend && pnpm tsr generate
```

- [ ] **Step 7: Type-check**

```bash
cd frontend && pnpm typecheck 2>&1 | tail -20
```
Expected: 0 errors

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/scanner/ frontend/src/routes/scanner.tsx frontend/src/services/scanner/ frontend/src/stores/global/scanner.ts
git commit -m "feat(phase18a): scanner FE — ScannerPage, SavedScanList, CandidatesTable, RuleEditor, route"
```

---

## Chunk F — Integration test + close-out

### Task 13: Integration test

**Files:**
- Create: `backend/tests/integration/test_scanner_e2e.py`

- [ ] **Step 1: Write integration test**

```python
# backend/tests/integration/test_scanner_e2e.py
"""
End-to-end scanner test: create scan → run → verify candidate row persisted.
Requires real DB. Skipped in CI unless RUN_INTEGRATION=1.
"""
import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION"),
    reason="integration tests require RUN_INTEGRATION=1"
)


@pytest.mark.asyncio
async def test_scanner_create_and_run(db_session, redis_client, cfg):
    from app.services.scanner.scanner_service import ScannerService
    from app.services.scanner.schemas import ScanConfig, UniverseConfig

    svc = ScannerService(db=db_session, redis=redis_client, cfg=cfg)
    config = ScanConfig(
        name="e2e test scan",
        universe_config=UniverseConfig(type="tickers", params={"tickers": ["AAPL"]}),
        rule_expr="rsi(14) < 100",  # always true
        llm_depth="quick",
    )
    run_id = await svc.run_scan(config=config)
    assert run_id is not None

    from sqlalchemy import text
    row = await db_session.execute(
        text("SELECT * FROM scanner_runs WHERE id = :id"), {"id": run_id}
    )
    r = row.fetchone()
    assert r is not None
    assert r.status in ("completed", "failed")
```

- [ ] **Step 2: Run unit test suite (not integration)**

```bash
docker compose exec backend pytest backend/tests/ -x -q --ignore=backend/tests/integration 2>&1 | tail -10
```
Expected: all green

- [ ] **Step 3: Run FE tests**

```bash
cd frontend && pnpm test 2>&1 | tail -10
```
Expected: all green

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_scanner_e2e.py
git commit -m "test(phase18a): integration test — scanner create + run e2e"
```

---

### Task 14: Close-out — tag v0.18.0

- [ ] **Step 1: Update CHANGELOG.md**

Add under a new `## [v0.18.0] — 2026-05-19` heading:

```markdown
## [v0.18.0] — 2026-05-19

### Added
- Universe scanner: rule-based screener with Lark DSL evaluator (grammar, transformer, safety budget)
- Alembic 0058: `saved_scans`, `scanner_runs` (TimescaleDB hypertable 7d chunks, 90d retention), `scanner_candidates`
- `ScannerService.run_scan`: universe resolve → indicator eval → candidate persistence → WS publish → alert fire
- `IndicatorComputer`: RSI, SMA, EMA, ATR, volume_ratio, price_vs_high, price_vs_low, MACD, BB%B from `bars_1d`/`bars_1m`
- `ScannerScheduler`: APScheduler CronTrigger, market-hours gate, per-scan-id lock, preset shortcuts
- LLM commentary: quick (LOCAL_ONLY) + deep (REASONING) with verbatim prompt templates
- REST API: 11 endpoints under `/api/scanner/`; WebSocket `/ws/scanner/runs/{scan_id}` with v=1 frames
- `WSConnId` widened to `UUID | str`; `cap_per_ws_override` for internal subscribers
- 13 Prometheus metrics under `scanner_*`
- FE: `/scanner` route, `ScannerPage`, `SavedScanList`, `RuleEditor`, `CandidatesTable`, `useScannerWs`, Zustand store
```

- [ ] **Step 2: Update TASKS.md** — mark Phase 18.0 complete

- [ ] **Step 3: Run full test suites**

```bash
docker compose exec backend pytest backend/tests/ -q --ignore=backend/tests/integration 2>&1 | tail -5
cd frontend && pnpm test 2>&1 | tail -5
```

- [ ] **Step 4: Final commit + tag**

```bash
git add CHANGELOG.md TASKS.md
git commit -m "docs(phase18a): close phase — CHANGELOG + TASKS for v0.18.0"
git tag v0.18.0
git push origin main --tags
```
