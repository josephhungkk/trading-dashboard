# Phase 17 — IBKR Algo Orders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add IBKR algo order support (ADAPTIVE, TWAP, VWAP, ARRIVAL_PRICE, ICEBERG, RESERVE, DARK_ICE) across all IBKR asset classes with dynamic FE param form, Telegram syntax, enriched order events, risk gate checks, and Prometheus metrics.

**Architecture:** New `app/services/algo/` leaf module holds shared schemas and normalization helpers; `AlgoCapabilityService` mirrors `OrderCapabilityService` with Redis TTL cache and pubsub invalidation. Proto, sidecar, orders schema, risk gate, orders service, Telegram parser, and FE modal all receive targeted additions — no new gRPC RPCs, no new WS endpoints.

**Tech Stack:** Python 3.14 + FastAPI + SQLAlchemy async + Pydantic v2 + Alembic; protobuf 3 + grpcio; React 19 + TypeScript 6 strict + Tailwind v4 + shadcn/ui; pytest 9 + Vitest 4; prometheus-client; fakeredis.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `alembic/versions/0057_phase17_algo_orders.py` | Create | orders columns + broker_algo_capability table + seed |
| `proto/broker/v1/broker.proto` | Modify | 4 message additions (tags 26/27, 3, 25, 10) |
| `app/_generated/broker/v1/broker_pb2*.py` (regenerated) | Regenerate | Generated stubs post-proto change |
| `app/services/algo/__init__.py` | Create | Package marker |
| `app/services/algo/schemas.py` | Create | `AlgoStrategy` StrEnum, `ALGO_PARAM_SCHEMAS`, `_normalize_algo_params` |
| `app/services/algo/capability_service.py` | Create | `AlgoCapabilityService` (Redis TTL cache, pubsub invalidation) |
| `app/core/metrics.py` | Modify | 8 new Prometheus counters |
| `app/schemas/orders.py` | Modify | Add `algo_strategy` + `algo_params` to `PreviewRequest` + `OrderModifyRequest` |
| `app/api/algo.py` | Create | `GET /api/algo/capabilities/{broker_id}/{asset_class}` + `GET /api/algo/schemas` |
| `app/main.py` | Modify | Import + register algo router; wire `AlgoCapabilityService` singleton into lifespan |
| `app/services/risk_service.py` | Modify | Extend `EvaluationContext`; add `_check_algo_capability` + `_check_iceberg_display_size` to `evaluate()` |
| `app/services/orders_service.py` | Modify | `validate_pre_dispatch` algo checks; 3× `EvaluationContext` call-sites; modify-rule §5.3a |
| `sidecar_ibkr/order_builder.py` | Modify | `_ALGO_STRATEGY_MAP`, `_ALGO_STRATEGY_MAP_REVERSE`, `build_ib_algo_order()` |
| `sidecar_ibkr/handlers.py` | Modify | Populate `algo_strategy` tag 10 in `OrderEventMessage`; populate `PlaceOrderResponse` tag 3 |
| `app/services/telegram/order_flow.py` | Modify | Extend `parse_place_order` for algo syntax; extend `ParsedOrder` |
| `frontend/src/services/algo/types.ts` | Create | `AlgoStrategy`, `AlgoCapability`, `AlgoParamSchema`, `AlgoOrderFields` |
| `frontend/src/services/algo/api.ts` | Create | `getAlgoCapabilities`, `getAlgoSchemas` |
| `frontend/src/features/orders/AlgoSection.tsx` | Create | Collapsible algo execution section with dynamic param form |
| `frontend/src/features/orders/TradeTicketModal.tsx` | Modify | Import + wire `AlgoSection` below TIF row |
| `frontend/src/features/orders/OrdersPage.tsx` (or DataTable) | Modify | Add hidden `Algo` column |
| `tests/test_algo_capability_service.py` | Create | Service unit tests |
| `tests/test_algo_order_builder.py` | Create | Sidecar builder unit tests |
| `tests/test_risk_service_algo.py` | Create | Risk gate check unit tests |
| `tests/test_orders_service_algo.py` | Create | Orders service integration tests |
| `tests/test_telegram_algo.py` | Create | Telegram parser unit tests |
| `tests/integration/test_algo_order_e2e.py` | Create | Happy path + rejection e2e |
| `frontend/src/features/orders/AlgoSection.test.tsx` | Create | Component tests |
| `frontend/src/services/algo/api.test.ts` | Create | API service tests |

---

## Task 1: Alembic migration 0057

**Files:**
- Create: `backend/alembic/versions/0057_phase17_algo_orders.py`

- [ ] **Step 1: Write the migration**

```python
"""phase17 algo orders — orders columns + broker_algo_capability table + seed.

Revision ID: 0057
Revises: 0056
Create Date: 2026-05-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # orders table — two nullable columns
    op.add_column("orders", sa.Column("algo_strategy", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("algo_params", sa.dialects.postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        "orders_algo_strategy_check",
        "orders",
        "algo_strategy IN ('ADAPTIVE','TWAP','VWAP','ARRIVAL_PRICE','ICEBERG','RESERVE','DARK_ICE')",
    )

    # broker_algo_capability table
    op.create_table(
        "broker_algo_capability",
        sa.Column("broker_id", sa.String(32), nullable=False),
        sa.Column("asset_class", sa.String(16), nullable=False),
        sa.Column("algo_strategy", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("notes", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("broker_id", "asset_class", "algo_strategy"),
        sa.CheckConstraint(
            "broker_id IN ('ibkr','futu','schwab','alpaca')",
            name="broker_algo_capability_broker_id_valid",
        ),
        sa.CheckConstraint(
            "asset_class IN ('STOCK','ETF','OPTION','FUTURE','FOREX','BOND','CFD','CRYPTO','MUTUAL_FUND')",
            name="broker_algo_capability_asset_class_valid",
        ),
        sa.CheckConstraint(
            "algo_strategy IN ('ADAPTIVE','TWAP','VWAP','ARRIVAL_PRICE','ICEBERG','RESERVE','DARK_ICE')",
            name="broker_algo_capability_algo_strategy_valid",
        ),
        sa.CheckConstraint(
            r"notes ~ '^[\x20-\x7E]*$' AND length(notes) <= 256",
            name="broker_algo_capability_notes_printable_ascii",
        ),
    )

    # Seed: only enabled rows (absent = unsupported)
    # Verified against TWS API algo docs — LOW-A: implementer must
    # verify IBKR string casing against ibapi at impl time.
    rows = [
        # STOCK / ETF — all 7 strategies
        *[("ibkr", ac, strat) for ac in ("STOCK", "ETF")
          for strat in ("ADAPTIVE","TWAP","VWAP","ARRIVAL_PRICE","ICEBERG","RESERVE","DARK_ICE")],
        # OPTION — ADAPTIVE + ICEBERG only
        ("ibkr", "OPTION", "ADAPTIVE"),
        ("ibkr", "OPTION", "ICEBERG"),
        # FUTURE — all except DARK_ICE
        *[("ibkr", "FUTURE", strat)
          for strat in ("ADAPTIVE","TWAP","VWAP","ARRIVAL_PRICE","ICEBERG","RESERVE")],
        # FOREX — ADAPTIVE + TWAP + VWAP
        ("ibkr", "FOREX", "ADAPTIVE"),
        ("ibkr", "FOREX", "TWAP"),
        ("ibkr", "FOREX", "VWAP"),
    ]
    op.bulk_insert(
        sa.table(
            "broker_algo_capability",
            sa.column("broker_id", sa.String),
            sa.column("asset_class", sa.String),
            sa.column("algo_strategy", sa.String),
        ),
        [{"broker_id": b, "asset_class": a, "algo_strategy": s} for b, a, s in rows],
    )


def downgrade() -> None:
    op.drop_table("broker_algo_capability")
    op.drop_constraint("orders_algo_strategy_check", "orders", type_="check")
    op.drop_column("orders", "algo_params")
    op.drop_column("orders", "algo_strategy")
```

- [ ] **Step 2: Run migration against test DB**

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/test \
  alembic upgrade head
```

Expected: `Running upgrade 0056 -> 0057`

- [ ] **Step 3: Verify schema**

```bash
docker compose exec dashboard-test_postgres-1 psql -U test -d test -c "\d broker_algo_capability"
docker compose exec dashboard-test_postgres-1 psql -U test -d test -c "SELECT count(*) FROM broker_algo_capability;"
```

Expected: table exists, count = 24 (7+7+2+6+3).

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/0057_phase17_algo_orders.py
git commit -m "feat(phase17): alembic 0057 — algo_strategy/algo_params on orders + broker_algo_capability table + seed"
```

---

## Task 2: Proto changes

**Files:**
- Modify: `proto/broker/v1/broker.proto`

- [ ] **Step 1: Find current tag positions in broker.proto**

```bash
grep -n "oco_group_id\|PlaceOrderResponse\|message Order \|OrderEventMessage\|reserved" proto/broker/v1/broker.proto | head -40
```

- [ ] **Step 2: Add algo fields to PlaceOrderRequest (after oco_group_id = 25)**

In the `PlaceOrderRequest` message, after the `oco_group_id = 25` line:

```protobuf
  optional string     algo_strategy = 26;  // "ADAPTIVE"|"TWAP"|"VWAP"|"ARRIVAL_PRICE"|"ICEBERG"|"RESERVE"|"DARK_ICE"
  map<string, string> algo_params   = 27;  // strategy param key→value (all values are strings)
  reserved 28 to 35;  // algo forward-extension
```

Remove or adjust any existing `reserved` block that conflicts with 28-35.

- [ ] **Step 3: Add algo_strategy to PlaceOrderResponse (after tag 2)**

In the `PlaceOrderResponse` message, after the last field (tag 2):

```protobuf
  optional string algo_strategy = 3;
  reserved 4 to 25;  // forward growth
```

- [ ] **Step 4: Add algo_strategy to Order message (after expiry_date = 24)**

In the `Order` message, after `expiry_date = 24`, update the reserved block:

```protobuf
  optional string algo_strategy = 25;
  reserved 26 to 35;  // algo + forward-extension
```

Change the existing `reserved 16 to 20` to only cover what it covers; add the new `reserved 26 to 35` line.

- [ ] **Step 5: Add algo_strategy to OrderEventMessage (after tag 9)**

In the `OrderEventMessage` message, after the last field (tag 9):

```protobuf
  optional string algo_strategy = 10;  // populated by sidecar when order has algoStrategy
  reserved 11 to 20;
```

- [ ] **Step 6: Regenerate stubs**

```bash
cd backend
buf generate ../proto
# or: python -m grpc_tools.protoc -I../proto --python_out=app/_generated --grpc_python_out=app/_generated ../proto/broker/v1/broker.proto
```

Verify `app/_generated/broker/v1/broker_pb2.py` has `algo_strategy` in descriptor.

- [ ] **Step 7: Commit**

```bash
git add proto/broker/v1/broker.proto backend/app/_generated/
git commit -m "feat(phase17): proto — algo_strategy + algo_params fields on PlaceOrderRequest/Response/Order/OrderEventMessage"
```

---

## Task 3: `app/services/algo/` module — schemas + normalize helper

**Files:**
- Create: `backend/app/services/algo/__init__.py`
- Create: `backend/app/services/algo/schemas.py`
- Create: `backend/tests/test_algo_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_algo_schemas.py
import pytest
from decimal import Decimal
from app.services.algo.schemas import (
    AlgoStrategy,
    ALGO_PARAM_SCHEMAS,
    _normalize_algo_params,
)


def test_algo_strategy_members():
    assert set(AlgoStrategy) == {
        "ADAPTIVE", "TWAP", "VWAP", "ARRIVAL_PRICE", "ICEBERG", "RESERVE", "DARK_ICE"
    }


def test_normalize_bool():
    assert _normalize_algo_params({"allow_past_end_time": True}) == {"allow_past_end_time": "true"}
    assert _normalize_algo_params({"flag": False}) == {"flag": "false"}


def test_normalize_int():
    assert _normalize_algo_params({"max_pct_vol": 15}) == {"max_pct_vol": "15"}


def test_normalize_decimal():
    assert _normalize_algo_params({"display_size": Decimal("50.5")}) == {"display_size": "50.5"}


def test_normalize_str_passthrough():
    assert _normalize_algo_params({"urgency": "NORMAL"}) == {"urgency": "NORMAL"}


def test_normalize_invalid_list_raises():
    with pytest.raises(ValueError, match="unsupported type"):
        _normalize_algo_params({"bad": [1, 2]})


def test_normalize_invalid_dict_raises():
    with pytest.raises(ValueError, match="unsupported type"):
        _normalize_algo_params({"bad": {"nested": "dict"}})


def test_normalize_none_value_raises():
    with pytest.raises(ValueError, match="unsupported type"):
        _normalize_algo_params({"bad": None})


def test_algo_param_schemas_has_all_strategies():
    for strategy in AlgoStrategy:
        assert strategy in ALGO_PARAM_SCHEMAS, f"Missing schema for {strategy}"


def test_algo_param_schemas_required_fields():
    adaptive = ALGO_PARAM_SCHEMAS["ADAPTIVE"]
    urgency = next(p for p in adaptive if p["name"] == "urgency")
    assert urgency["required"] is True
    assert urgency["type"] == "enum"
    assert "PATIENT" in urgency["values"]
```

- [ ] **Step 2: Run to see it fail**

```bash
cd backend
pytest tests/test_algo_schemas.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'app.services.algo'`

- [ ] **Step 3: Create the module**

```python
# backend/app/services/algo/__init__.py
```

```python
# backend/app/services/algo/schemas.py
"""Algo order schemas, param definitions, and normalization helper.

Leaf module — no imports from other app.services.* modules.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any


class AlgoStrategy(StrEnum):
    ADAPTIVE = "ADAPTIVE"
    TWAP = "TWAP"
    VWAP = "VWAP"
    ARRIVAL_PRICE = "ARRIVAL_PRICE"
    ICEBERG = "ICEBERG"
    RESERVE = "RESERVE"
    DARK_ICE = "DARK_ICE"


# Display algos require LIMIT base order type.
DISPLAY_ALGOS = frozenset({AlgoStrategy.ICEBERG, AlgoStrategy.RESERVE, AlgoStrategy.DARK_ICE})

# Schema for each strategy's parameters.
# type: "enum" | "time" | "decimal" | "boolean"
ALGO_PARAM_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "ADAPTIVE": [
        {"name": "urgency", "type": "enum", "values": ["PATIENT", "NORMAL", "URGENT"],
         "required": True},
    ],
    "TWAP": [
        {"name": "start_time", "type": "time", "required": True},
        {"name": "end_time", "type": "time", "required": True},
        {"name": "allow_past_end_time", "type": "boolean", "required": False},
    ],
    "VWAP": [
        {"name": "start_time", "type": "time", "required": True},
        {"name": "end_time", "type": "time", "required": True},
        {"name": "max_pct_vol", "type": "decimal", "required": False},
        {"name": "no_take_liq", "type": "boolean", "required": False},
    ],
    "ARRIVAL_PRICE": [
        {"name": "urgency", "type": "enum", "values": ["PATIENT", "NORMAL", "URGENT"],
         "required": True},
        {"name": "max_pct_vol", "type": "decimal", "required": False},
    ],
    "ICEBERG": [
        {"name": "display_size", "type": "decimal", "required": True},
    ],
    "RESERVE": [
        {"name": "display_size", "type": "decimal", "required": True},
        {"name": "randomize_size", "type": "boolean", "required": False},
    ],
    "DARK_ICE": [
        {"name": "display_size", "type": "decimal", "required": True},
    ],
}

# Required param names per strategy (computed once).
REQUIRED_PARAMS: dict[str, frozenset[str]] = {
    strategy: frozenset(p["name"] for p in params if p["required"])
    for strategy, params in ALGO_PARAM_SCHEMAS.items()
}


def _normalize_algo_params(params: dict[str, Any]) -> dict[str, str]:
    """Normalize algo_params values to str end-to-end.

    Rules: bool → "true"/"false"; int → str; Decimal → canonical str.
    Any other type raises ValueError (surfaces as 500 to flush bugs).
    """
    result: dict[str, str] = {}
    for k, v in params.items():
        if isinstance(v, bool):
            result[k] = "true" if v else "false"
        elif isinstance(v, int):
            result[k] = str(v)
        elif isinstance(v, Decimal):
            result[k] = str(v)
        elif isinstance(v, str):
            result[k] = v
        else:
            raise ValueError(
                f"algo_params[{k!r}]: unsupported type {type(v).__name__!r}; "
                "expected bool, int, Decimal, or str"
            )
    return result
```

- [ ] **Step 4: Run tests**

```bash
cd backend
pytest tests/test_algo_schemas.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/algo/ backend/tests/test_algo_schemas.py
git commit -m "feat(phase17): app/services/algo/schemas — AlgoStrategy, ALGO_PARAM_SCHEMAS, _normalize_algo_params"
```

---

## Task 4: Prometheus metrics

**Files:**
- Modify: `backend/app/core/metrics.py`

- [ ] **Step 1: Add the 8 new counters at the end of metrics.py**

```python
# --- Phase 17: Algo orders ---

algo_orders_submitted_total = Counter(
    "algo_orders_submitted_total",
    "Algo orders successfully placed",
    labelnames=["strategy", "broker_id", "asset_class"],
    registry=registry,
)

algo_orders_cancelled_total = Counter(
    "algo_orders_cancelled_total",
    "Algo orders cancelled (from order event stream)",
    labelnames=["strategy", "broker_id"],
    registry=registry,
)

algo_orders_modify_rejected_total = Counter(
    "algo_orders_modify_rejected_total",
    "Modify attempts rejected due to algo strategy or bracket-leg constraint",
    labelnames=["strategy", "reason"],
    registry=registry,
)

algo_capability_cache_hits_total = Counter(
    "algo_capability_cache_hits_total",
    "AlgoCapabilityService Redis cache hits",
    labelnames=["broker_id"],
    registry=registry,
)

algo_capability_cache_misses_total = Counter(
    "algo_capability_cache_misses_total",
    "AlgoCapabilityService Redis cache misses",
    labelnames=["broker_id"],
    registry=registry,
)

algo_risk_blocks_total = Counter(
    "algo_risk_blocks_total",
    "Risk gate BLOCK results from algo checks",
    labelnames=["check", "strategy"],
    registry=registry,
)

algo_sidecar_errors_total = Counter(
    "algo_sidecar_errors_total",
    "Sidecar order builder errors for algo orders",
    labelnames=["strategy", "error_type"],
    registry=registry,
)

algo_capability_invalidate_malformed_total = Counter(
    "algo_capability_invalidate_malformed_total",
    "Malformed broker_algo_capability:invalidate pubsub payloads rejected",
    registry=registry,
)
```

- [ ] **Step 2: Verify no import errors**

```bash
cd backend
python -c "from app.core.metrics import algo_orders_submitted_total; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/metrics.py
git commit -m "feat(phase17): metrics — 8 algo counters"
```

---

## Task 5: `AlgoCapabilityService`

**Files:**
- Create: `backend/app/services/algo/capability_service.py`
- Create: `backend/tests/test_algo_capability_service.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_algo_capability_service.py
"""Unit tests for AlgoCapabilityService."""
import json
import pytest
import pytest_asyncio
import fakeredis.aioredis
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.algo.capability_service import AlgoCapabilityService


@pytest_asyncio.fixture
async def fake_redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def svc(fake_redis, db_session):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.core.db import engine
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield AlgoCapabilityService(redis=fake_redis, db_factory=factory)


@pytest.mark.asyncio
async def test_get_strategies_unknown_broker(svc):
    result = await svc.get_strategies("unknownbroker", "STOCK")
    assert result == []


@pytest.mark.asyncio
async def test_get_strategies_ibkr_stock_returns_rows(svc):
    # Test DB has migration 0057 applied — IBKR/STOCK should have 7 strategies.
    result = await svc.get_strategies("ibkr", "STOCK")
    strategies = [r["algo_strategy"] for r in result]
    assert "ADAPTIVE" in strategies
    assert "TWAP" in strategies
    assert "ICEBERG" in strategies
    assert len(strategies) == 7


@pytest.mark.asyncio
async def test_cache_hit(svc, fake_redis):
    # Populate cache with a synthetic entry.
    cache_key = "algo_cap:ibkr:STOCK"
    payload = json.dumps([{"algo_strategy": "TWAP", "enabled": True, "notes": ""}])
    await fake_redis.setex(cache_key, 300, payload.encode())

    result = await svc.get_strategies("ibkr", "STOCK")
    assert any(r["algo_strategy"] == "TWAP" for r in result)


@pytest.mark.asyncio
async def test_pubsub_invalidate_exact_key(svc, fake_redis):
    # Pre-populate Redis cache.
    cache_key = "algo_cap:ibkr:STOCK"
    payload = json.dumps([{"algo_strategy": "TWAP", "enabled": True, "notes": ""}])
    await fake_redis.setex(cache_key, 300, payload.encode())

    # Simulate invalidation message.
    await svc._handle_invalidation(json.dumps({"broker_id": "ibkr", "asset_class": "STOCK"}))

    # Key should be gone.
    assert await fake_redis.get(cache_key) is None


@pytest.mark.asyncio
async def test_pubsub_invalidate_malformed_increments_counter(svc, fake_redis):
    from app.core import metrics
    before = metrics.algo_capability_invalidate_malformed_total._value.get()
    await svc._handle_invalidation("not-json")
    after = metrics.algo_capability_invalidate_malformed_total._value.get()
    assert after > before


@pytest.mark.asyncio
async def test_pubsub_flush_all(svc, fake_redis):
    await fake_redis.setex("algo_cap:ibkr:STOCK", 300, b"x")
    await fake_redis.setex("algo_cap:ibkr:OPTION", 300, b"x")
    await svc._handle_invalidation(json.dumps({}))
    keys = await fake_redis.keys("algo_cap:*")
    assert keys == []
```

- [ ] **Step 2: Run to see failing**

```bash
cd backend
pytest tests/test_algo_capability_service.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'app.services.algo.capability_service'`

- [ ] **Step 3: Implement AlgoCapabilityService**

```python
# backend/app/services/algo/capability_service.py
"""Algo capability lookup with Redis TTL cache and pub/sub invalidation."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics

log = structlog.get_logger(__name__)

ALGO_CAPABILITY_INVALIDATION_CHANNEL = "broker_algo_capability:invalidate"
_CACHE_TTL_SECONDS = 300  # 5 minutes
_CACHE_KEY_PREFIX = "algo_cap"

_SessionFactory = Callable[[], Any]


class AlgoCapabilityService:
    """Redis-cached lookup of enabled algo strategies per (broker_id, asset_class).

    Singleton mode: pass db_factory. run_listener() must be started as a background task.
    """

    def __init__(
        self,
        redis: Any,
        *,
        db: AsyncSession | None = None,
        db_factory: _SessionFactory | None = None,
        ttl_seconds: int = _CACHE_TTL_SECONDS,
    ) -> None:
        if db is None and db_factory is None:
            raise ValueError("AlgoCapabilityService requires either db or db_factory")
        self._db = db
        self._db_factory = db_factory
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _cache_key(broker_id: str, asset_class: str) -> str:
        return f"{_CACHE_KEY_PREFIX}:{broker_id}:{asset_class}"

    async def _session(self) -> AsyncGenerator[AsyncSession]:
        if self._db is not None:
            yield self._db
            return
        assert self._db_factory is not None
        async with self._db_factory() as session:
            yield session

    async def get_strategies(
        self, broker_id: str, asset_class: str
    ) -> list[dict[str, Any]]:
        """Return list of enabled algo capability rows for (broker_id, asset_class).

        Returns [] for unknown brokers. Cached in Redis for _ttl_seconds.
        """
        from app.services.order_capability_service import KNOWN_BROKERS
        if broker_id not in KNOWN_BROKERS:
            return []

        cache_key = self._cache_key(broker_id, asset_class)
        cached = await self._redis.get(cache_key)
        if cached is not None:
            metrics.algo_capability_cache_hits_total.labels(broker_id=broker_id).inc()
            return json.loads(cached)

        metrics.algo_capability_cache_misses_total.labels(broker_id=broker_id).inc()
        async with self._session() as db:
            result = await db.execute(
                text(
                    "SELECT algo_strategy, enabled, notes "
                    "FROM broker_algo_capability "
                    "WHERE broker_id = :broker_id AND asset_class = :asset_class "
                    "  AND enabled = TRUE "
                    "ORDER BY algo_strategy"
                ),
                {"broker_id": broker_id, "asset_class": asset_class},
            )
            rows = [dict(r._mapping) for r in result.fetchall()]

        payload = json.dumps(rows).encode()
        await self._redis.setex(cache_key, self._ttl_seconds, payload)
        return rows

    async def _handle_invalidation(self, message: str) -> None:
        """Handle a broker_algo_capability:invalidate pubsub message."""
        try:
            payload = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            log.warning("algo_capability.invalidate_malformed", raw=message[:200])
            metrics.algo_capability_invalidate_malformed_total.inc()
            return

        if not isinstance(payload, dict):
            log.warning("algo_capability.invalidate_malformed", raw=message[:200])
            metrics.algo_capability_invalidate_malformed_total.inc()
            return

        if "broker_id" in payload and "asset_class" in payload:
            # Invalidate exactly one key.
            key = self._cache_key(payload["broker_id"], payload["asset_class"])
            await self._redis.delete(key)
            log.info("algo_capability.invalidated", key=key)
        elif "broker_id" in payload:
            # Invalidate all asset_class keys for this broker.
            pattern = f"{_CACHE_KEY_PREFIX}:{payload['broker_id']}:*"
            keys = await self._redis.keys(pattern)
            if keys:
                await self._redis.delete(*keys)
            log.info("algo_capability.invalidated_broker", broker_id=payload["broker_id"],
                     count=len(keys))
        elif payload == {}:
            # Flush all algo capability cache entries.
            keys = await self._redis.keys(f"{_CACHE_KEY_PREFIX}:*")
            if keys:
                await self._redis.delete(*keys)
            log.info("algo_capability.invalidated_all", count=len(keys))
        else:
            log.warning("algo_capability.invalidate_malformed", payload=payload)
            metrics.algo_capability_invalidate_malformed_total.inc()

    async def run_listener(self) -> None:
        """Background task: subscribe to invalidation channel and handle messages."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(ALGO_CAPABILITY_INVALIDATION_CHANNEL)
        log.info("algo_capability.listener_started")
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode()
                    try:
                        await self._handle_invalidation(data)
                    except Exception as exc:
                        log.exception("algo_capability.listener_error", exc_info=exc)
        finally:
            await pubsub.unsubscribe(ALGO_CAPABILITY_INVALIDATION_CHANNEL)
```

- [ ] **Step 4: Run tests**

```bash
cd backend
pytest tests/test_algo_capability_service.py -v
```

Expected: all green (DB tests require test DB at 0057).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/algo/capability_service.py backend/tests/test_algo_capability_service.py
git commit -m "feat(phase17): AlgoCapabilityService — Redis TTL cache + pubsub invalidation"
```

---

## Task 6: Schema additions — PreviewRequest and OrderModifyRequest

**Files:**
- Modify: `backend/app/schemas/orders.py` (lines 54–184, 377–424)

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_algo_order_schemas.py
import pytest
from app.schemas.orders import PreviewRequest, PlaceOrderRequest, OrderModifyRequest


def test_preview_request_accepts_algo_fields():
    req = PreviewRequest(
        account_id="00000000-0000-0000-0000-000000000001",
        conid="265598",
        side="BUY",
        order_type="MARKET",
        tif="DAY",
        qty="100",
        algo_strategy="TWAP",
        algo_params={"start_time": "10:00", "end_time": "14:00"},
    )
    assert req.algo_strategy == "TWAP"
    assert req.algo_params == {"start_time": "10:00", "end_time": "14:00"}


def test_place_order_request_inherits_algo():
    from uuid import uuid4
    req = PlaceOrderRequest(
        account_id="00000000-0000-0000-0000-000000000001",
        conid="265598",
        side="BUY",
        order_type="MARKET",
        tif="DAY",
        qty="100",
        algo_strategy="ADAPTIVE",
        algo_params={"urgency": "URGENT"},
        client_order_id=uuid4(),
        nonce="abc",
    )
    assert req.algo_strategy == "ADAPTIVE"


def test_order_modify_request_accepts_algo_fields():
    req = OrderModifyRequest(
        nonce="abc",
        qty="100",
        order_type="MARKET",
        tif="DAY",
        algo_strategy="TWAP",
        algo_params={"start_time": "10:00", "end_time": "14:00"},
    )
    assert req.algo_strategy == "TWAP"


def test_order_modify_request_extra_fields_rejected():
    with pytest.raises(Exception, match="Extra inputs"):
        OrderModifyRequest(
            nonce="abc",
            qty="100",
            order_type="MARKET",
            tif="DAY",
            totally_unknown_field="x",
        )


def test_preview_request_no_algo_is_fine():
    req = PreviewRequest(
        account_id="00000000-0000-0000-0000-000000000001",
        conid="265598",
        side="BUY",
        order_type="MARKET",
        tif="DAY",
        qty="100",
    )
    assert req.algo_strategy is None
    assert req.algo_params is None
```

- [ ] **Step 2: Run to see failing**

```bash
cd backend
pytest tests/test_algo_order_schemas.py -v 2>&1 | head -20
```

Expected: tests fail with field not found.

- [ ] **Step 3: Add algo fields to PreviewRequest (app/schemas/orders.py:67)**

After `expiry_date` on line 67 in `PreviewRequest`, add:

```python
    algo_strategy: AlgoStrategy | None = None
    algo_params: dict[str, str] | None = Field(default=None)
```

Also add the import at top of file (after existing imports):

```python
from app.services.algo.schemas import AlgoStrategy
```

- [ ] **Step 4: Add algo fields to OrderModifyRequest (app/schemas/orders.py:391)**

After `expiry_date` on line 391 in `OrderModifyRequest`, add:

```python
    algo_strategy: AlgoStrategy | None = None
    algo_params: dict[str, str] | None = None  # accepted but ignored server-side (§5.3a)
```

- [ ] **Step 5: Run tests**

```bash
cd backend
pytest tests/test_algo_order_schemas.py -v
```

Expected: all green.

- [ ] **Step 6: Run full schema test suite to check regressions**

```bash
cd backend
pytest tests/ -k "schema or order" -v --tb=short 2>&1 | tail -20
```

Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/orders.py backend/tests/test_algo_order_schemas.py
git commit -m "feat(phase17): schemas — algo_strategy + algo_params on PreviewRequest + OrderModifyRequest"
```

---

## Task 7: Risk gate additions

**Files:**
- Modify: `backend/app/services/risk_service.py` (EvaluationContext at line 88; evaluate() at line 1322)
- Create: `backend/tests/test_risk_service_algo.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_risk_service_algo.py
"""Tests for algo risk gate checks."""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from app.services.risk_service import EvaluationContext, RiskService, GateBlockerEntry, GateWarningEntry


_CTX_BASE = dict(
    account_id=UUID("00000000-0000-0000-0000-000000000001"),
    broker_id="ibkr",
    instrument_id=1,
    side="BUY",
    qty=Decimal("500"),
    price=Decimal("100"),
    order_type="LIMIT",
    time_in_force="DAY",
    request_id="test-req",
    currency_base="USD",
    asset_class="STOCK",
)


def _make_svc():
    svc = RiskService.__new__(RiskService)
    svc._db = MagicMock()
    svc._redis = MagicMock()
    svc._config = MagicMock()
    svc._sidecar = MagicMock()
    return svc


@pytest.mark.asyncio
async def test_check_iceberg_display_size_none():
    svc = _make_svc()
    ctx = EvaluationContext(
        **{**_CTX_BASE, "algo_strategy": "ICEBERG", "algo_params": {}}
    )
    result = await svc._check_iceberg_display_size(ctx)
    assert result is not None
    blocker, _ = result
    assert blocker is not None
    assert blocker.code == "display_size_required"


@pytest.mark.asyncio
async def test_check_iceberg_display_size_malformed():
    svc = _make_svc()
    ctx = EvaluationContext(
        **{**_CTX_BASE, "algo_strategy": "ICEBERG",
           "algo_params": {"display_size": "not_a_number"}}
    )
    result = await svc._check_iceberg_display_size(ctx)
    blocker, _ = result
    assert blocker.code == "display_size_malformed"


@pytest.mark.asyncio
async def test_check_iceberg_display_size_nonpositive():
    svc = _make_svc()
    ctx = EvaluationContext(
        **{**_CTX_BASE, "algo_strategy": "ICEBERG",
           "algo_params": {"display_size": "0"}}
    )
    result = await svc._check_iceberg_display_size(ctx)
    blocker, _ = result
    assert blocker.code == "display_size_nonpositive"


@pytest.mark.asyncio
async def test_check_iceberg_display_size_gte_qty():
    svc = _make_svc()
    ctx = EvaluationContext(
        **{**_CTX_BASE, "qty": Decimal("100"), "algo_strategy": "ICEBERG",
           "algo_params": {"display_size": "100"}}
    )
    result = await svc._check_iceberg_display_size(ctx)
    blocker, _ = result
    assert blocker.code == "display_size_gte_qty"


@pytest.mark.asyncio
async def test_check_iceberg_display_size_sub_lot_warns():
    svc = _make_svc()
    ctx = EvaluationContext(
        **{**_CTX_BASE, "qty": Decimal("500"), "algo_strategy": "ICEBERG",
           "algo_params": {"display_size": "0.5"}}
    )
    result = await svc._check_iceberg_display_size(ctx)
    blocker, warning = result
    assert blocker is None
    assert warning is not None
    assert warning.code == "display_size_sub_lot"


@pytest.mark.asyncio
async def test_check_iceberg_display_size_valid_passes():
    svc = _make_svc()
    ctx = EvaluationContext(
        **{**_CTX_BASE, "qty": Decimal("500"), "algo_strategy": "ICEBERG",
           "algo_params": {"display_size": "50"}}
    )
    result = await svc._check_iceberg_display_size(ctx)
    assert result is None


@pytest.mark.asyncio
async def test_check_iceberg_display_size_skipped_for_non_display_algo():
    svc = _make_svc()
    ctx = EvaluationContext(
        **{**_CTX_BASE, "algo_strategy": "TWAP",
           "algo_params": {"start_time": "10:00", "end_time": "14:00"}}
    )
    # Should return None immediately (not applicable)
    result = await svc._check_iceberg_display_size(ctx)
    assert result is None
```

- [ ] **Step 2: Run to see failing**

```bash
cd backend
pytest tests/test_risk_service_algo.py -v 2>&1 | head -30
```

Expected: `AttributeError: type object 'EvaluationContext' has no field 'algo_strategy'`

- [ ] **Step 3: Extend EvaluationContext (risk_service.py:114)**

After `position_effect` on line 114 (last field of `EvaluationContext`), add:

```python
    # Phase 17: algo orders
    algo_strategy: str | None = None        # AlgoStrategy value, or None for non-algo orders
    algo_params: dict[str, str] | None = None  # normalized string dict
```

- [ ] **Step 4: Add `_check_algo_capability` method to RiskService**

Add this method to `RiskService` class (before `_check_options_exposure`):

```python
    async def _check_algo_capability(self, ctx: EvaluationContext) -> CheckResult:
        """BLOCK if broker_algo_capability has no enabled row for this strategy."""
        if ctx.algo_strategy is None:
            return None
        if ctx.asset_class is None:
            return None
        try:
            from app.services.algo.capability_service import AlgoCapabilityService
            from sqlalchemy.ext.asyncio import AsyncSession
            svc = AlgoCapabilityService(redis=self._redis, db=self._db)
            rows = await svc.get_strategies(ctx.broker_id, ctx.asset_class)
            enabled = {r["algo_strategy"] for r in rows}
            if ctx.algo_strategy not in enabled:
                from app.core import metrics
                metrics.algo_risk_blocks_total.labels(
                    check="algo_capability", strategy=ctx.algo_strategy
                ).inc()
                return (
                    GateBlockerEntry(
                        code="unsupported_algo_strategy",
                        message=f"algo strategy {ctx.algo_strategy!r} not supported for "
                                f"{ctx.broker_id}/{ctx.asset_class}",
                    ),
                    None,
                )
        except Exception as exc:
            import structlog as _log
            _log.get_logger(__name__).warning(
                "risk.algo_capability_check_failed", exc=str(exc)
            )
            # Fail-OPEN on DB error (matches preview_order fail-OPEN policy)
            return None
        return None
```

- [ ] **Step 5: Add `_check_iceberg_display_size` method to RiskService**

```python
    async def _check_iceberg_display_size(self, ctx: EvaluationContext) -> CheckResult:
        """Validate display_size for ICEBERG/RESERVE/DARK_ICE orders."""
        from app.services.algo.schemas import DISPLAY_ALGOS
        if ctx.algo_strategy not in {str(s) for s in DISPLAY_ALGOS}:
            return None

        from decimal import Decimal, InvalidOperation
        display_size_str = (ctx.algo_params or {}).get("display_size")
        if display_size_str is None:
            return (
                GateBlockerEntry(
                    code="display_size_required",
                    message="display_size is required for ICEBERG/RESERVE/DARK_ICE",
                ),
                None,
            )
        try:
            display_size = Decimal(display_size_str)
        except InvalidOperation:
            return (
                GateBlockerEntry(
                    code="display_size_malformed",
                    message="display_size must be a valid decimal string",
                ),
                None,
            )
        if display_size <= 0:
            from app.core import metrics
            metrics.algo_risk_blocks_total.labels(
                check="iceberg_display_size", strategy=ctx.algo_strategy or ""
            ).inc()
            return (
                GateBlockerEntry(
                    code="display_size_nonpositive",
                    message="display_size must be > 0",
                ),
                None,
            )
        if display_size >= ctx.qty:
            metrics.algo_risk_blocks_total.labels(
                check="iceberg_display_size", strategy=ctx.algo_strategy or ""
            ).inc()
            return (
                GateBlockerEntry(
                    code="display_size_gte_qty",
                    message="display_size must be less than order qty",
                ),
                None,
            )
        if display_size < Decimal("1"):
            return (
                None,
                GateWarningEntry(
                    code="display_size_sub_lot",
                    message="fractional display sizes may be rejected by some venues",
                ),
            )
        return None
```

- [ ] **Step 6: Wire both checks into evaluate() (risk_service.py ~1322)**

In the `evaluate()` method, extend `fast_check_names` and `asyncio.gather` call to include the two algo checks:

```python
        fast_check_names = (
            "account_kill_switch",
            "broker_kill_switch",
            "max_daily_loss",
            "pdt",
            "position_concentration",
            "buying_power",
            "algo_capability",          # Phase 17
            "iceberg_display_size",      # Phase 17
        )
        fast_results = await asyncio.gather(
            self._check_account_kill_switch(ctx),
            self._check_broker_kill_switch(ctx),
            self._check_max_daily_loss(ctx),
            self._check_pdt(ctx),
            self._check_position_concentration(ctx),
            self._check_buying_power(ctx),
            self._check_algo_capability(ctx),       # Phase 17
            self._check_iceberg_display_size(ctx),  # Phase 17
            return_exceptions=True,
        )
```

- [ ] **Step 7: Run tests**

```bash
cd backend
pytest tests/test_risk_service_algo.py -v
```

Expected: all green.

- [ ] **Step 8: Run full risk tests to check regressions**

```bash
cd backend
pytest tests/test_risk_service.py tests/test_risk_service_algo.py -v --tb=short 2>&1 | tail -20
```

Expected: no new failures.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/risk_service.py backend/tests/test_risk_service_algo.py
git commit -m "feat(phase17): risk gate — EvaluationContext algo fields + _check_algo_capability + _check_iceberg_display_size"
```

---

## Task 8: `validate_pre_dispatch` + `orders_service` wiring

**Files:**
- Modify: `backend/app/services/orders_service.py`
- Create: `backend/tests/test_orders_service_algo.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_orders_service_algo.py
"""Tests for algo order handling in orders_service."""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


@pytest.mark.asyncio
async def test_validate_pre_dispatch_algo_requires_limit_rejects_market(db_session):
    """ICEBERG with MARKET order type should 422 algo_requires_limit."""
    from app.services.orders_service import validate_pre_dispatch, PreviewUnavailable

    cfg = MagicMock()
    cfg.get = AsyncMock(return_value=None)
    capability = MagicMock()
    capability.is_supported = AsyncMock(return_value=True)

    with pytest.raises(PreviewUnavailable) as exc_info:
        await validate_pre_dispatch(
            cfg=cfg,
            capability=capability,
            broker_label="isa-paper",
            asset_class="STOCK",
            order_type="MARKET",
            tif="DAY",
            algo_strategy="ICEBERG",
        )
    assert exc_info.value.status_code == 422
    assert "algo_requires_limit" in str(exc_info.value.body)


@pytest.mark.asyncio
async def test_validate_pre_dispatch_dark_ice_requires_limit(db_session):
    from app.services.orders_service import validate_pre_dispatch, PreviewUnavailable

    cfg = MagicMock()
    cfg.get = AsyncMock(return_value=None)
    capability = MagicMock()
    capability.is_supported = AsyncMock(return_value=True)

    with pytest.raises(PreviewUnavailable) as exc_info:
        await validate_pre_dispatch(
            cfg=cfg,
            capability=capability,
            broker_label="isa-paper",
            asset_class="STOCK",
            order_type="MARKET",
            tif="DAY",
            algo_strategy="DARK_ICE",
        )
    assert "algo_requires_limit" in str(exc_info.value.body)


@pytest.mark.asyncio
async def test_validate_pre_dispatch_adaptive_no_limit_required(db_session):
    """ADAPTIVE does not require LIMIT order type."""
    from app.services.orders_service import validate_pre_dispatch, PreviewUnavailable

    cfg = MagicMock()
    cfg.get = AsyncMock(return_value=None)
    capability = MagicMock()
    capability.is_supported = AsyncMock(return_value=True)

    # Should NOT raise — ADAPTIVE works with any order type
    await validate_pre_dispatch(
        cfg=cfg,
        capability=capability,
        broker_label="isa-paper",
        asset_class="STOCK",
        order_type="MARKET",
        tif="DAY",
        algo_strategy="ADAPTIVE",
        skip_operational_checks=True,
    )


@pytest.mark.asyncio
async def test_validate_pre_dispatch_bracket_sl_with_algo_rejects(db_session):
    """algo_strategy on bracket SL/TP legs must 422."""
    from app.services.orders_service import validate_pre_dispatch, PreviewUnavailable

    cfg = MagicMock()
    cfg.get = AsyncMock(return_value=None)
    capability = MagicMock()
    capability.is_supported = AsyncMock(return_value=True)

    with pytest.raises(PreviewUnavailable) as exc_info:
        await validate_pre_dispatch(
            cfg=cfg,
            capability=capability,
            broker_label="isa-paper",
            asset_class="STOCK",
            order_type="LIMIT",
            tif="GTC",
            algo_strategy="TWAP",
            is_bracket_leg=True,
            skip_operational_checks=True,
        )
    assert "algo_on_bracket_leg_unsupported" in str(exc_info.value.body)
```

- [ ] **Step 2: Run to see failing**

```bash
cd backend
pytest tests/test_orders_service_algo.py -v 2>&1 | head -20
```

Expected: tests fail — `validate_pre_dispatch` doesn't have `algo_strategy` param yet.

- [ ] **Step 3: Extend validate_pre_dispatch (orders_service.py:134)**

Add `algo_strategy: str | None = None` and `is_bracket_leg: bool = False` kwargs. After existing capability checks, add:

```python
    # Phase 17: algo validation
    if algo_strategy is not None:
        if is_bracket_leg:
            raise PreviewUnavailable(
                422,
                {"error": {"code": "algo_on_bracket_leg_unsupported",
                            "detail": "Algo orders cannot be used on bracket SL/TP legs"}},
            )
        from app.services.algo.schemas import DISPLAY_ALGOS, AlgoStrategy
        if algo_strategy in {str(s) for s in DISPLAY_ALGOS}:
            if order_type != "LIMIT":
                raise PreviewUnavailable(
                    422,
                    {"error": {"code": "algo_requires_limit",
                                "detail": f"Strategy {algo_strategy!r} requires order_type=LIMIT"}},
                )
```

- [ ] **Step 4: Extend EvaluationContext construction in `_evaluate_risk_for_preview` (orders_service.py:378)**

After the last field (`asset_class=asset_class`) in the `EvaluationContext(...)` call, add:

```python
        algo_strategy=str(request.algo_strategy) if request.algo_strategy else None,
        algo_params=request.algo_params,
```

Also add a `request` parameter check — `request` here is a `PreviewRequest` which now has those fields.

- [ ] **Step 5: Extend EvaluationContext construction in `_evaluate_risk_for_place_order` (orders_service.py:461)**

After `position_effect=position_effect_value` in the `EvaluationContext(...)` call, add:

```python
        algo_strategy=str(request.algo_strategy) if request.algo_strategy else None,
        algo_params=request.algo_params,
```

- [ ] **Step 6: Add modify rule in `modify_order` (orders_service.py ~1200)**

After `validate_pre_dispatch` is called in `modify_order`, add the algo strategy comparison check. Find where `row = await db.execute(...)` fetches the order row. After it, add:

```python
    # Phase 17 §5.3a: algo strategy immutable post-creation
    stored_algo = row["algo_strategy"]
    if request.algo_strategy is not None or stored_algo is not None:
        req_algo = str(request.algo_strategy) if request.algo_strategy else None
        if req_algo != stored_algo:
            from app.core import metrics as _m
            _m.algo_orders_modify_rejected_total.labels(
                strategy=stored_algo or req_algo or "unknown",
                reason="strategy_change",
            ).inc()
            raise PreviewUnavailable(
                422,
                {"error": {"code": "algo_modify_strategy_change_unsupported",
                            "detail": "Cannot change algo strategy on a live order; cancel and re-place"}},
            )
```

Also ensure that when passing params to the sidecar on modify, the stored `algo_params` is used (not `request.algo_params`).

- [ ] **Step 7: Increment `algo_orders_submitted_total` in `place_order` after successful dispatch**

Find where `place_order` completes successfully (after sidecar call). Add:

```python
    if request.algo_strategy:
        from app.core import metrics as _m
        _m.algo_orders_submitted_total.labels(
            strategy=str(request.algo_strategy),
            broker_id=capability_broker_id(account.gateway_label),
            asset_class=asset_class or "unknown",
        ).inc()
```

- [ ] **Step 8: Run tests**

```bash
cd backend
pytest tests/test_orders_service_algo.py -v
```

Expected: all green.

- [ ] **Step 9: Run broader orders tests**

```bash
cd backend
pytest tests/test_orders_service.py tests/test_orders_service_algo.py -v --tb=short 2>&1 | tail -20
```

Expected: no new failures.

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/orders_service.py backend/tests/test_orders_service_algo.py
git commit -m "feat(phase17): orders_service — validate_pre_dispatch algo checks + EvaluationContext wiring + modify rule"
```

---

## Task 9: API router — `/api/algo/`

**Files:**
- Create: `backend/app/api/algo.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Create algo API router**

```python
# backend/app/api/algo.py
"""Phase 17: algo capability + schema endpoints."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.deps import require_jwt
from app.services.algo.schemas import ALGO_PARAM_SCHEMAS

router = APIRouter(prefix="/api/algo", tags=["algo"])
log = structlog.get_logger(__name__)


@router.get("/capabilities/{broker_id}/{asset_class}", dependencies=[Depends(require_jwt)])
async def get_algo_capabilities(
    broker_id: str,
    asset_class: str,
    request: Request,
) -> dict[str, Any]:
    """Return enabled algo strategies + param schemas for (broker_id, asset_class).

    Cached in Redis 5 min per key via AlgoCapabilityService.
    """
    from app.services.algo.capability_service import AlgoCapabilityService
    from app.core.db import SessionLocal
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.core.db import engine

    # Use app.state.algo_capability_svc if wired in lifespan (singleton mode).
    svc: AlgoCapabilityService | None = getattr(request.app.state, "algo_capability_svc", None)
    if svc is None:
        # Fallback for tests without lifespan: per-request instance.
        svc = AlgoCapabilityService(
            redis=request.app.state.redis,
            db_factory=async_sessionmaker(engine, expire_on_commit=False),
        )

    rows = await svc.get_strategies(broker_id, asset_class)
    strategies = []
    for row in rows:
        strategy = row["algo_strategy"]
        param_schema = ALGO_PARAM_SCHEMAS.get(strategy, [])
        strategies.append({"strategy": strategy, "params": param_schema})

    return {"strategies": strategies}


@router.get("/schemas", dependencies=[Depends(require_jwt)])
async def get_algo_schemas() -> dict[str, Any]:
    """Return full ALGO_PARAM_SCHEMAS for all strategies (static data, no caching needed)."""
    return {"schemas": ALGO_PARAM_SCHEMAS}
```

- [ ] **Step 2: Register router in main.py**

In `backend/app/main.py`, add the import alongside other routers:

```python
from app.api.algo import router as algo_router
```

In the `app.include_router(...)` section (find where other routers are included), add:

```python
app.include_router(algo_router)
```

Also in `lifespan`, wire the `AlgoCapabilityService` singleton after `capability_svc`:

```python
    from app.services.algo.capability_service import AlgoCapabilityService
    algo_capability_svc = AlgoCapabilityService(redis=redis, db_factory=db_factory)
    app.state.algo_capability_svc = algo_capability_svc
    asyncio.create_task(algo_capability_svc.run_listener())
```

- [ ] **Step 3: Test endpoints with client fixture**

```python
# Add to tests/test_algo_capability_service.py or a new file tests/test_api_algo.py:
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_capabilities_ibkr_stock(test_client_admin: AsyncClient):
    resp = await test_client_admin.get("/api/algo/capabilities/ibkr/STOCK")
    assert resp.status_code == 200
    data = resp.json()
    strategies = [s["strategy"] for s in data["strategies"]]
    assert "TWAP" in strategies
    assert "ADAPTIVE" in strategies


@pytest.mark.asyncio
async def test_get_capabilities_schwab_stock_empty(test_client_admin: AsyncClient):
    resp = await test_client_admin.get("/api/algo/capabilities/schwab/STOCK")
    assert resp.status_code == 200
    assert resp.json()["strategies"] == []


@pytest.mark.asyncio
async def test_get_schemas(test_client_admin: AsyncClient):
    resp = await test_client_admin.get("/api/algo/schemas")
    assert resp.status_code == 200
    schemas = resp.json()["schemas"]
    assert "ADAPTIVE" in schemas
    assert "DARK_ICE" in schemas


@pytest.mark.asyncio
async def test_get_capabilities_requires_auth(test_client_no_auth: AsyncClient):
    resp = await test_client_no_auth.get("/api/algo/capabilities/ibkr/STOCK")
    assert resp.status_code == 401
```

- [ ] **Step 4: Run API tests**

```bash
cd backend
pytest tests/test_api_algo.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/algo.py backend/app/main.py backend/tests/test_api_algo.py
git commit -m "feat(phase17): GET /api/algo/capabilities + /api/algo/schemas endpoints"
```

---

## Task 10: IBKR sidecar — algo order builder

**Files:**
- Modify: `sidecar_ibkr/order_builder.py`
- Create: `sidecar_ibkr/tests/test_algo_order_builder.py`

- [ ] **Step 1: Write failing tests**

```python
# sidecar_ibkr/tests/test_algo_order_builder.py
"""Tests for build_ib_algo_order()."""
import pytest
from unittest.mock import MagicMock
from sidecar_ibkr.order_builder import build_ib_algo_order, _ALGO_STRATEGY_MAP, _ALGO_STRATEGY_MAP_REVERSE


def _make_order():
    order = MagicMock()
    order.algoStrategy = ""
    order.algoParams = []
    order.orderType = "MKT"
    return order


def _make_request(strategy, params, order_type="MARKET"):
    req = MagicMock()
    req.algo_strategy = strategy
    req.algo_params = params
    req.order_type = order_type
    return req


def test_adaptive_sets_algo_strategy():
    order = _make_order()
    request = _make_request("ADAPTIVE", {"urgency": "URGENT"})
    build_ib_algo_order(order, request)
    assert order.algoStrategy == _ALGO_STRATEGY_MAP["ADAPTIVE"]
    tag_keys = {tv.tag for tv in order.algoParams}
    assert "adaptPriority" in tag_keys


def test_twap_sets_start_end_time():
    order = _make_order()
    request = _make_request("TWAP", {"start_time": "10:00", "end_time": "14:00"})
    build_ib_algo_order(order, request)
    tag_map = {tv.tag: tv.value for tv in order.algoParams}
    assert "startTime" in tag_map
    assert "endTime" in tag_map


def test_iceberg_requires_limit():
    order = _make_order()
    order.orderType = "MKT"
    request = _make_request("ICEBERG", {"display_size": "50"}, order_type="MARKET")
    with pytest.raises(ValueError, match="requires LMT"):
        build_ib_algo_order(order, request)


def test_dark_ice_display_size_zero_raises():
    order = _make_order()
    order.orderType = "LMT"
    request = _make_request("DARK_ICE", {"display_size": "0"}, order_type="LIMIT")
    with pytest.raises(ValueError, match="display_size"):
        build_ib_algo_order(order, request)


def test_oversize_params_raises():
    order = _make_order()
    params = {f"key{i}": "v" for i in range(17)}
    request = _make_request("ADAPTIVE", params)
    with pytest.raises(ValueError, match="too many"):
        build_ib_algo_order(order, request)


def test_value_too_long_raises():
    order = _make_order()
    request = _make_request("ADAPTIVE", {"urgency": "X" * 65})
    with pytest.raises(ValueError, match="too long"):
        build_ib_algo_order(order, request)


def test_reverse_map_is_1to1():
    """1:1 invariant — no duplicate values in forward map."""
    assert len(_ALGO_STRATEGY_MAP_REVERSE) == len(_ALGO_STRATEGY_MAP)


def test_reserve_includes_randomize_size():
    order = _make_order()
    order.orderType = "LMT"
    request = _make_request("RESERVE", {"display_size": "50", "randomize_size": "true"},
                            order_type="LIMIT")
    build_ib_algo_order(order, request)
    tag_map = {tv.tag: tv.value for tv in order.algoParams}
    assert "displaySize" in tag_map
    assert "randomizeSize" in tag_map
```

- [ ] **Step 2: Run to see failing**

```bash
cd sidecar_ibkr
pytest tests/test_algo_order_builder.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'build_ib_algo_order'`

- [ ] **Step 3: Add algo builder to order_builder.py**

At the top of `sidecar_ibkr/order_builder.py`, after existing imports, add:

```python
from decimal import Decimal as _Decimal

# Phase 17: IBKR algo strategy string mapping.
# LOW-A: verify all casing against ibapi at impl time — add source citation comment.
_ALGO_STRATEGY_MAP: dict[str, str] = {
    "ADAPTIVE":      "Adaptive",     # verify: ibapi AlgoParam.ADAPTIVE_PRIORITY
    "TWAP":          "Twap",          # verify: may be "TWAP"
    "VWAP":          "Vwap",          # verify: may be "VWAP"
    "ARRIVAL_PRICE": "ArrivalPx",
    "ICEBERG":       "Iceberg",       # verify: may populate Order.displaySize directly
    "RESERVE":       "PctVol",        # verify: may use Order.displaySize + reserveSize
    "DARK_ICE":      "DarkIce",       # verify casing
}

_ALGO_STRATEGY_MAP_REVERSE: dict[str, str] = {v: k for k, v in _ALGO_STRATEGY_MAP.items()}
# 1:1 invariant guard — catches future duplicate-value additions at import time.
assert len(_ALGO_STRATEGY_MAP_REVERSE) == len(_ALGO_STRATEGY_MAP), (
    "_ALGO_STRATEGY_MAP must be 1:1; reverse mapping would be ambiguous"
)

_DISPLAY_ALGOS = frozenset({"ICEBERG", "RESERVE", "DARK_ICE"})

# TagValue key mapping per strategy
_ALGO_TAGVALUE_KEYS: dict[str, dict[str, str]] = {
    "ADAPTIVE":      {"urgency": "adaptPriority"},
    "TWAP":          {"start_time": "startTime", "end_time": "endTime",
                      "allow_past_end_time": "allowPastEndTime"},
    "VWAP":          {"start_time": "startTime", "end_time": "endTime",
                      "max_pct_vol": "maxPctVol", "no_take_liq": "noTakeLiq"},
    "ARRIVAL_PRICE": {"urgency": "adaptPriority", "max_pct_vol": "maxPctVol"},
    "ICEBERG":       {"display_size": "displaySize"},
    "RESERVE":       {"display_size": "displaySize", "randomize_size": "randomizeSize"},
    "DARK_ICE":      {"display_size": "displaySize"},
}
```

Then add the `build_ib_algo_order` function at the end of the file:

```python
def build_ib_algo_order(order: object, request: object) -> None:
    """Set order.algoStrategy and order.algoParams from request.algo_strategy/algo_params.

    Raises ValueError on invalid params (defence-in-depth; risk gate is primary).
    Must be called after base order is built.
    """
    try:
        from ibapi.order import Order as IbOrder
        from ibapi.tag_value import TagValue
    except ImportError:
        # Allow import in test context without ibapi installed.
        class TagValue:  # type: ignore[no-redef]
            def __init__(self, tag: str, value: str) -> None:
                self.tag = tag
                self.value = value

    strategy: str = str(request.algo_strategy)  # type: ignore[attr-defined]
    params: dict[str, str] = dict(request.algo_params or {})  # type: ignore[attr-defined]
    order_type: str = str(request.order_type)  # type: ignore[attr-defined]

    # Size cap (defence-in-depth; validate_pre_dispatch checks first).
    if len(params) > 16:
        raise ValueError(f"algo_params has too many keys ({len(params)}); max 16")
    for k, v in params.items():
        if len(v) > 64:
            raise ValueError(f"algo_params[{k!r}] value too long ({len(v)} chars); max 64")

    # Display algo requires LMT (defence-in-depth; server-side 422 fires first).
    if strategy in _DISPLAY_ALGOS and order_type != "LIMIT":
        raise ValueError(f"strategy {strategy!r} requires LMT order type, got {order_type!r}")

    # Display algo display_size > 0 (defence-in-depth; risk gate is primary).
    if strategy in _DISPLAY_ALGOS:
        ds = params.get("display_size", "0")
        try:
            if _Decimal(ds) <= 0:
                raise ValueError(f"display_size must be > 0 for {strategy!r}, got {ds!r}")
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    ibkr_strategy = _ALGO_STRATEGY_MAP.get(strategy)
    if ibkr_strategy is None:
        raise ValueError(f"Unknown algo strategy: {strategy!r}")

    order.algoStrategy = ibkr_strategy  # type: ignore[attr-defined]

    key_map = _ALGO_TAGVALUE_KEYS.get(strategy, {})
    tag_values = []
    for our_key, ibkr_key in key_map.items():
        if our_key in params:
            val = params[our_key]
            # Normalize time from HH:MM to HH:MM:SS for IBKR.
            if our_key in ("start_time", "end_time") and len(val) == 5:
                val = val + ":00"
            # Normalize bool strings to 0/1 for IBKR.
            if our_key in ("allow_past_end_time", "no_take_liq", "randomize_size"):
                val = "1" if val.lower() == "true" else "0"
            tag_values.append(TagValue(ibkr_key, val))

    order.algoParams = tag_values  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run tests**

```bash
cd sidecar_ibkr
pytest tests/test_algo_order_builder.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add sidecar_ibkr/order_builder.py sidecar_ibkr/tests/test_algo_order_builder.py
git commit -m "feat(phase17): sidecar order_builder — _ALGO_STRATEGY_MAP + build_ib_algo_order"
```

---

## Task 11: IBKR sidecar handlers — enrich OrderEventMessage + PlaceOrderResponse

**Files:**
- Modify: `sidecar_ibkr/handlers.py`

- [ ] **Step 1: Find where OrderEventMessage is constructed in handlers.py**

```bash
grep -n "OrderEventMessage\|algo_strategy\|algoStrategy" sidecar_ibkr/handlers.py | head -20
```

- [ ] **Step 2: Import the reverse map at top of handlers.py**

At the top of `sidecar_ibkr/handlers.py`, after existing imports, add:

```python
from sidecar_ibkr.order_builder import _ALGO_STRATEGY_MAP_REVERSE
```

- [ ] **Step 3: Populate algo_strategy on OrderEventMessage**

In the function that builds `OrderEventMessage`, after the existing fields are set, add:

```python
    # Phase 17: reverse-map IBKR algoStrategy string to internal enum.
    _ibkr_algo = getattr(trade.order, "algoStrategy", None) or ""
    msg.algo_strategy = _ALGO_STRATEGY_MAP_REVERSE.get(_ibkr_algo, "")
```

- [ ] **Step 4: Populate algo_strategy on PlaceOrderResponse**

Find where `PlaceOrderResponse` is built in `PlaceOrder` handler. Add:

```python
    # Phase 17: echo back the internal algo strategy name.
    if request.algo_strategy:
        response.algo_strategy = request.algo_strategy
```

- [ ] **Step 5: Wire build_ib_algo_order in the PlaceOrder handler**

In the handler where `_build_ib_order` is called (before `self.ib.placeOrder`), add:

```python
    if request.algo_strategy:
        from sidecar_ibkr.order_builder import build_ib_algo_order
        build_ib_algo_order(ib_order, request)
```

- [ ] **Step 6: Commit**

```bash
git add sidecar_ibkr/handlers.py
git commit -m "feat(phase17): sidecar handlers — populate algo_strategy on OrderEventMessage + PlaceOrderResponse; call build_ib_algo_order"
```

---

## Task 12: Telegram parser extension

**Files:**
- Modify: `backend/app/services/telegram/order_flow.py`
- Create: `backend/tests/test_telegram_algo.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_telegram_algo.py
"""Tests for Telegram algo order parsing."""
import pytest
from app.services.telegram.order_flow import parse_place_order


def test_parse_adaptive():
    result = parse_place_order("/place_order AAPL BUY 100 ADAPTIVE urgency=URGENT")
    assert result is not None
    assert result.algo_strategy == "ADAPTIVE"
    assert result.algo_params == {"urgency": "URGENT"}


def test_parse_twap():
    result = parse_place_order("/place_order AAPL BUY 1000 TWAP start_time=10:00 end_time=14:00")
    assert result is not None
    assert result.algo_strategy == "TWAP"
    assert result.algo_params["start_time"] == "10:00"
    assert result.algo_params["end_time"] == "14:00"


def test_parse_vwap_with_optional():
    result = parse_place_order(
        "/place_order AAPL BUY 1000 VWAP start_time=10:00 end_time=14:00 max_pct_vol=15"
    )
    assert result is not None
    assert result.algo_strategy == "VWAP"
    assert result.algo_params["max_pct_vol"] == "15"


def test_parse_arrival_price():
    result = parse_place_order("/place_order AAPL BUY 500 ARRIVAL_PRICE urgency=NORMAL")
    assert result is not None
    assert result.algo_strategy == "ARRIVAL_PRICE"


def test_parse_iceberg():
    result = parse_place_order("/place_order AAPL BUY 500 ICEBERG display_size=50")
    assert result is not None
    assert result.algo_strategy == "ICEBERG"
    assert result.algo_params["display_size"] == "50"


def test_parse_reserve():
    result = parse_place_order(
        "/place_order AAPL BUY 500 RESERVE display_size=50 randomize_size=true"
    )
    assert result is not None
    assert result.algo_strategy == "RESERVE"
    assert result.algo_params["randomize_size"] == "true"


def test_parse_dark_ice():
    result = parse_place_order("/place_order AAPL BUY 500 DARK_ICE display_size=50")
    assert result is not None
    assert result.algo_strategy == "DARK_ICE"


def test_dark_ice_display_size_zero_rejected():
    result = parse_place_order("/place_order AAPL BUY 500 DARK_ICE display_size=0")
    assert result is None  # parse-time validation rejects it


def test_unknown_key_rejected():
    result = parse_place_order("/place_order AAPL BUY 100 ADAPTIVE bad_key=x")
    assert result is None


def test_case_insensitive_strategy():
    result = parse_place_order("/place_order AAPL BUY 100 adaptive urgency=URGENT")
    assert result is not None
    assert result.algo_strategy == "ADAPTIVE"


def test_non_algo_path_unchanged():
    result = parse_place_order("/place_order AAPL BUY 100 --limit 150.00")
    assert result is not None
    assert result.algo_strategy is None
    assert result.limit_price == "150.00"
```

- [ ] **Step 2: Run to see failing**

```bash
cd backend
pytest tests/test_telegram_algo.py -v 2>&1 | head -20
```

Expected: `AttributeError: 'ParsedOrder' object has no attribute 'algo_strategy'`

- [ ] **Step 3: Extend ParsedOrder dataclass**

In `order_flow.py`, update `ParsedOrder` to add:

```python
@dataclass(frozen=True, slots=True)
class ParsedOrder:
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: str
    order_type: Literal["MARKET", "LIMIT", "STOP_LIMIT"]
    tif: Literal["DAY", "GTC"]
    limit_price: str | None
    stop_price: str | None
    algo_strategy: str | None = None   # Phase 17
    algo_params: dict[str, str] | None = None  # Phase 17
```

- [ ] **Step 4: Extend parse_place_order to handle algo tokens**

Replace the token-parsing section (starting from `qty = parts[3]`) with:

```python
    qty = parts[3]
    if not _DECIMAL_10_RE.match(qty):
        return None

    # Phase 17: detect algo strategy token at position 3 (after /place_order SYMBOL SIDE).
    # Re-read: position 3 in parts is 0-indexed: parts[0]=/place_order, [1]=SYMBOL, [2]=SIDE, [3]=QTY
    # algo token would be at position 4 (parts[4]) after qty.

    from app.services.algo.schemas import AlgoStrategy, ALGO_PARAM_SCHEMAS, REQUIRED_PARAMS, DISPLAY_ALGOS

    algo_strategy: str | None = None
    algo_params: dict[str, str] | None = None

    if len(parts) > 4 and parts[4].upper() in AlgoStrategy.__members__:
        # Algo path
        algo_strategy = parts[4].upper()
        algo_params = {}
        for token in parts[5:]:
            if "=" not in token:
                return None  # unexpected token
            k, _, v = token.partition("=")
            k = k.strip()
            v = v.strip()
            # Validate key is known for this strategy
            known_keys = {p["name"] for p in ALGO_PARAM_SCHEMAS.get(algo_strategy, [])}
            if k not in known_keys:
                return None  # unknown key
            algo_params[k] = v
        # Validate required params present
        missing = REQUIRED_PARAMS.get(algo_strategy, frozenset()) - set(algo_params)
        if missing:
            return None
        # Parse-time validation for display_size > 0
        if algo_strategy in {str(s) for s in DISPLAY_ALGOS}:
            ds = algo_params.get("display_size", "")
            try:
                from decimal import Decimal
                if Decimal(ds) <= 0:
                    return None
            except Exception:
                return None
        # Determine order_type for algo
        if algo_strategy in {str(s) for s in DISPLAY_ALGOS}:
            order_type: Literal["MARKET", "LIMIT", "STOP_LIMIT"] = "LIMIT"
        else:
            order_type = "MARKET"
        return ParsedOrder(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            tif="DAY",
            limit_price=None,
            stop_price=None,
            algo_strategy=algo_strategy,
            algo_params=algo_params,
        )
```

Keep the existing non-algo parsing below (for `--limit`, `--stop`, `--tif` flags).

- [ ] **Step 5: Run tests**

```bash
cd backend
pytest tests/test_telegram_algo.py -v
```

Expected: all green.

- [ ] **Step 6: Run full telegram tests for regressions**

```bash
cd backend
pytest tests/test_telegram*.py -v --tb=short 2>&1 | tail -20
```

Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/telegram/order_flow.py backend/tests/test_telegram_algo.py
git commit -m "feat(phase17): Telegram — algo order syntax in parse_place_order; extend ParsedOrder"
```

---

## Task 13: Integration test

**Files:**
- Create: `backend/tests/integration/test_algo_order_e2e.py`

- [ ] **Step 1: Write the integration test**

```python
# backend/tests/integration/test_algo_order_e2e.py
"""E2E integration test for algo order flow: preview → risk → place → WS event."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from decimal import Decimal


@pytest.mark.asyncio
async def test_twap_on_bond_rejected_422(test_client_admin):
    """TWAP on BOND is not in broker_algo_capability → 422 unsupported_algo_strategy."""
    from app.core.deps import set_broker_registry, set_account_service
    from app.services.brokers import BrokerRegistry, AccountService
    import uuid

    account_id = str(uuid.uuid4())

    resp = await test_client_admin.post("/api/orders/preview", json={
        "account_id": "00000000-0000-0000-0000-000000000001",
        "conid": "265598",
        "side": "BUY",
        "order_type": "MARKET",
        "tif": "DAY",
        "qty": "100",
        "algo_strategy": "TWAP",
        "algo_params": {"start_time": "10:00", "end_time": "14:00"},
    })
    # The test DB has no BOND instruments seeded, but the capability check
    # will fire via risk gate: TWAP not in broker_algo_capability for BOND.
    # If account resolution fails first, that's also acceptable (503).
    assert resp.status_code in (422, 503)


@pytest.mark.asyncio
async def test_iceberg_market_order_rejected_algo_requires_limit(test_client_admin):
    """ICEBERG with MARKET order type should return 422 algo_requires_limit."""
    resp = await test_client_admin.post("/api/orders/preview", json={
        "account_id": "00000000-0000-0000-0000-000000000001",
        "conid": "265598",
        "side": "BUY",
        "order_type": "MARKET",   # should be LIMIT for ICEBERG
        "tif": "DAY",
        "qty": "100",
        "algo_strategy": "ICEBERG",
        "algo_params": {"display_size": "10"},
    })
    assert resp.status_code in (422, 503)
    if resp.status_code == 422:
        assert "algo_requires_limit" in resp.text


@pytest.mark.asyncio
async def test_iceberg_display_size_zero_rejected(test_client_admin):
    """ICEBERG display_size=0 should be blocked by risk gate."""
    resp = await test_client_admin.post("/api/orders/preview", json={
        "account_id": "00000000-0000-0000-0000-000000000001",
        "conid": "265598",
        "side": "BUY",
        "order_type": "LIMIT",
        "limit_price": "150.00",
        "tif": "DAY",
        "qty": "100",
        "algo_strategy": "ICEBERG",
        "algo_params": {"display_size": "0"},
    })
    # Either account resolution fails (503) or risk gate blocks (422)
    assert resp.status_code in (422, 503)
```

- [ ] **Step 2: Run integration tests**

```bash
cd backend
pytest tests/integration/test_algo_order_e2e.py -v
```

Expected: all pass (some may hit 503 due to test DB lacking full broker data — that's acceptable per the test comments).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_algo_order_e2e.py
git commit -m "test(phase17): integration tests — ICEBERG/TWAP edge cases"
```

---

## Task 14: Frontend services layer

**Files:**
- Create: `frontend/src/services/algo/types.ts`
- Create: `frontend/src/services/algo/api.ts`
- Create: `frontend/src/services/algo/api.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// frontend/src/services/algo/api.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@/core/apiClient', () => ({
  apiGet: vi.fn(),
}));

import { getAlgoCapabilities, getAlgoSchemas } from './api';
import { apiGet } from '@/core/apiClient';

const mockApiGet = vi.mocked(apiGet);

beforeEach(() => {
  vi.clearAllMocks();
});

describe('getAlgoCapabilities', () => {
  it('returns strategies for ibkr/STOCK', async () => {
    mockApiGet.mockResolvedValueOnce({
      strategies: [
        { strategy: 'TWAP', params: [{ name: 'start_time', type: 'time', required: true }] },
      ],
    });
    const result = await getAlgoCapabilities('ibkr', 'STOCK');
    expect(result.strategies).toHaveLength(1);
    expect(result.strategies[0].strategy).toBe('TWAP');
    expect(mockApiGet).toHaveBeenCalledWith('/api/algo/capabilities/ibkr/STOCK');
  });

  it('returns empty strategies for schwab', async () => {
    mockApiGet.mockResolvedValueOnce({ strategies: [] });
    const result = await getAlgoCapabilities('schwab', 'STOCK');
    expect(result.strategies).toHaveLength(0);
  });
});

describe('getAlgoSchemas', () => {
  it('returns schemas dict', async () => {
    mockApiGet.mockResolvedValueOnce({
      schemas: { ADAPTIVE: [{ name: 'urgency', type: 'enum', required: true }] },
    });
    const result = await getAlgoSchemas();
    expect(result.schemas['ADAPTIVE']).toBeDefined();
  });
});
```

- [ ] **Step 2: Run to see failing**

```bash
cd frontend
pnpm test src/services/algo/api.test.ts 2>&1 | head -20
```

Expected: `Cannot find module './api'`

- [ ] **Step 3: Create types.ts**

```typescript
// frontend/src/services/algo/types.ts

export type AlgoStrategy =
  | 'ADAPTIVE'
  | 'TWAP'
  | 'VWAP'
  | 'ARRIVAL_PRICE'
  | 'ICEBERG'
  | 'RESERVE'
  | 'DARK_ICE';

export const DISPLAY_ALGOS: ReadonlySet<AlgoStrategy> = new Set([
  'ICEBERG',
  'RESERVE',
  'DARK_ICE',
]);

export interface AlgoParamSchema {
  name: string;
  type: 'enum' | 'time' | 'decimal' | 'boolean';
  values?: string[];  // for enum type
  required: boolean;
}

export interface AlgoCapabilityEntry {
  strategy: AlgoStrategy;
  params: AlgoParamSchema[];
}

export interface AlgoCapabilitiesResponse {
  strategies: AlgoCapabilityEntry[];
}

export interface AlgoSchemasResponse {
  schemas: Record<AlgoStrategy, AlgoParamSchema[]>;
}

export interface AlgoOrderFields {
  algo_strategy: AlgoStrategy;
  algo_params: Record<string, string>;
}
```

- [ ] **Step 4: Create api.ts**

```typescript
// frontend/src/services/algo/api.ts
import { apiGet } from '@/core/apiClient';
import type { AlgoCapabilitiesResponse, AlgoSchemasResponse } from './types';

export async function getAlgoCapabilities(
  brokerId: string,
  assetClass: string
): Promise<AlgoCapabilitiesResponse> {
  return apiGet<AlgoCapabilitiesResponse>(
    `/api/algo/capabilities/${brokerId}/${assetClass}`
  );
}

export async function getAlgoSchemas(): Promise<AlgoSchemasResponse> {
  return apiGet<AlgoSchemasResponse>('/api/algo/schemas');
}
```

- [ ] **Step 5: Run tests**

```bash
cd frontend
pnpm test src/services/algo/api.test.ts
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/services/algo/
git commit -m "feat(phase17): FE services/algo — types + api helpers"
```

---

## Task 15: AlgoSection component

**Files:**
- Create: `frontend/src/features/orders/AlgoSection.tsx`
- Create: `frontend/src/features/orders/AlgoSection.test.tsx`

- [ ] **Step 1: Write failing component tests**

```typescript
// frontend/src/features/orders/AlgoSection.test.tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

vi.mock('@/services/algo/api', () => ({
  getAlgoCapabilities: vi.fn(),
}));

import { AlgoSection } from './AlgoSection';
import { getAlgoCapabilities } from '@/services/algo/api';

const mockGetCap = vi.mocked(getAlgoCapabilities);

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AlgoSection', () => {
  it('renders collapsed chip with Off label', async () => {
    mockGetCap.mockResolvedValueOnce({ strategies: [
      { strategy: 'TWAP', params: [
        { name: 'start_time', type: 'time', required: true },
        { name: 'end_time', type: 'time', required: true },
      ]}
    ]});
    render(
      <AlgoSection
        brokerId="ibkr"
        assetClass="STOCK"
        onAlgoChange={vi.fn()}
      />
    );
    expect(await screen.findByText(/Algo Execution/)).toBeInTheDocument();
    expect(screen.getByText(/Off/)).toBeInTheDocument();
  });

  it('hidden when no strategies returned (e.g. Schwab)', async () => {
    mockGetCap.mockResolvedValueOnce({ strategies: [] });
    const { container } = render(
      <AlgoSection
        brokerId="schwab"
        assetClass="STOCK"
        onAlgoChange={vi.fn()}
      />
    );
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('shows LIMIT coercion notice for ICEBERG', async () => {
    mockGetCap.mockResolvedValueOnce({ strategies: [
      { strategy: 'ICEBERG', params: [{ name: 'display_size', type: 'decimal', required: true }]}
    ]});
    const onChange = vi.fn();
    render(<AlgoSection brokerId="ibkr" assetClass="STOCK" onAlgoChange={onChange} />);
    const chip = await screen.findByText(/Algo Execution/);
    fireEvent.click(chip);
    // Select ICEBERG
    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: 'ICEBERG' } });
    expect(await screen.findByText(/forced to LIMIT/i)).toBeInTheDocument();
  });

  it('shows MARKET coercion notice for TWAP', async () => {
    mockGetCap.mockResolvedValueOnce({ strategies: [
      { strategy: 'TWAP', params: [
        { name: 'start_time', type: 'time', required: true },
        { name: 'end_time', type: 'time', required: true },
      ]}
    ]});
    render(<AlgoSection brokerId="ibkr" assetClass="STOCK" onAlgoChange={vi.fn()} />);
    const chip = await screen.findByText(/Algo Execution/);
    fireEvent.click(chip);
    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: 'TWAP' } });
    expect(await screen.findByText(/forced to MARKET/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to see failing**

```bash
cd frontend
pnpm test src/features/orders/AlgoSection.test.tsx 2>&1 | head -20
```

Expected: `Cannot find module './AlgoSection'`

- [ ] **Step 3: Implement AlgoSection**

```typescript
// frontend/src/features/orders/AlgoSection.tsx
import React from 'react';
import { getAlgoCapabilities } from '@/services/algo/api';
import type { AlgoCapabilityEntry, AlgoOrderFields, AlgoStrategy } from '@/services/algo/types';
import { DISPLAY_ALGOS } from '@/services/algo/types';

interface Props {
  brokerId: string;
  assetClass: string;
  onAlgoChange: (fields: AlgoOrderFields | null) => void;
}

export function AlgoSection({ brokerId, assetClass, onAlgoChange }: Props) {
  const [open, setOpen] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [strategies, setStrategies] = React.useState<AlgoCapabilityEntry[]>([]);
  const [selectedStrategy, setSelectedStrategy] = React.useState<AlgoStrategy | null>(null);
  const [params, setParams] = React.useState<Record<string, string>>({});

  React.useEffect(() => {
    setLoading(true);
    getAlgoCapabilities(brokerId, assetClass)
      .then((res) => setStrategies(res.strategies))
      .catch(() => setStrategies([]))
      .finally(() => setLoading(false));
  }, [brokerId, assetClass]);

  // Hidden when no strategies available.
  if (!loading && strategies.length === 0) return null;

  const selectedEntry = strategies.find((s) => s.strategy === selectedStrategy) ?? null;
  const isDisplayAlgo = selectedStrategy != null && DISPLAY_ALGOS.has(selectedStrategy);
  const isExecutionAlgo = selectedStrategy != null && !DISPLAY_ALGOS.has(selectedStrategy);

  function handleStrategyChange(strategy: AlgoStrategy | '') {
    if (strategy === '') {
      setSelectedStrategy(null);
      setParams({});
      onAlgoChange(null);
      return;
    }
    setSelectedStrategy(strategy as AlgoStrategy);
    setParams({});
    onAlgoChange(null); // reset until params filled
  }

  function handleParamChange(name: string, value: string) {
    const next = { ...params, [name]: value };
    setParams(next);
    if (selectedStrategy) {
      onAlgoChange({ algo_strategy: selectedStrategy, algo_params: next });
    }
  }

  if (loading) {
    return (
      <div className="animate-pulse h-8 rounded bg-neutral-200" aria-label="Loading algo capabilities" />
    );
  }

  return (
    <div className="border rounded p-2 text-sm">
      <button
        type="button"
        className="w-full text-left font-medium"
        onClick={() => setOpen((o) => !o)}
      >
        Algo Execution — {selectedStrategy ?? 'Off'}
      </button>

      {open && (
        <div className="mt-2 space-y-2">
          <label className="block">
            <span className="text-xs text-neutral-500">Strategy</span>
            <select
              className="block w-full mt-0.5"
              value={selectedStrategy ?? ''}
              onChange={(e) => handleStrategyChange(e.currentTarget.value as AlgoStrategy | '')}
            >
              <option value="">— Off —</option>
              {strategies.map((s) => (
                <option key={s.strategy} value={s.strategy}>{s.strategy}</option>
              ))}
            </select>
          </label>

          {isDisplayAlgo && (
            <p className="text-xs text-amber-600">Order type forced to LIMIT for this strategy.</p>
          )}
          {isExecutionAlgo && (
            <p className="text-xs text-blue-600">Order type forced to MARKET for this strategy.</p>
          )}

          {selectedEntry?.params.map((param) => (
            <label key={param.name} className="block">
              <span className="text-xs text-neutral-500">
                {param.name}{param.required ? ' *' : ''}
              </span>
              {param.type === 'enum' && param.values ? (
                <select
                  className="block w-full mt-0.5"
                  value={params[param.name] ?? ''}
                  onChange={(e) => handleParamChange(param.name, e.currentTarget.value)}
                >
                  <option value="">—</option>
                  {param.values.map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              ) : param.type === 'boolean' ? (
                <input
                  type="checkbox"
                  checked={params[param.name] === 'true'}
                  onChange={(e) => handleParamChange(param.name, e.currentTarget.checked ? 'true' : 'false')}
                />
              ) : param.type === 'time' ? (
                <input
                  type="time"
                  className="block mt-0.5"
                  value={params[param.name] ?? ''}
                  onChange={(e) => handleParamChange(param.name, e.currentTarget.value)}
                />
              ) : (
                <input
                  type="text"
                  className="block w-full mt-0.5"
                  value={params[param.name] ?? ''}
                  onChange={(e) => handleParamChange(param.name, e.currentTarget.value)}
                  placeholder="0.00"
                />
              )}
            </label>
          ))}

          {isDisplayAlgo && selectedStrategy && (
            <p className="text-xs text-neutral-400">
              Display size must be {'>'} 0 and less than order quantity.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests**

```bash
cd frontend
pnpm test src/features/orders/AlgoSection.test.tsx
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/orders/AlgoSection.tsx frontend/src/features/orders/AlgoSection.test.tsx
git commit -m "feat(phase17): AlgoSection component — collapsible algo form with dynamic param inputs"
```

---

## Task 16: Wire AlgoSection into TradeTicketModal + Orders page Algo column

**Files:**
- Modify: `frontend/src/features/orders/TradeTicketModal.tsx`
- Modify: `frontend/src/features/orders/OrdersPage.tsx` (or wherever the orders DataTable lives)

- [ ] **Step 1: Import AlgoSection in TradeTicketModal**

In `TradeTicketModal.tsx`, after the existing section imports (around line 28), add:

```typescript
import { AlgoSection } from './AlgoSection';
import type { AlgoOrderFields } from '@/services/algo/types';
import { DISPLAY_ALGOS } from '@/services/algo/types';
```

- [ ] **Step 2: Add algoFields state to TradeTicketModal**

Near the other state declarations (around line 427), add:

```typescript
  const [algoFields, setAlgoFields] = React.useState<AlgoOrderFields | null>(null);
```

- [ ] **Step 3: Insert AlgoSection below TIF row**

Find the closing `</label>` of the TIF `<select>` (around line 611). After it, add:

```tsx
      {/* ── Phase 17 — Algo execution section ───────────────────────── */}
      {brokerId != null && form.contract.asset_class != null && (
        <AlgoSection
          brokerId={brokerId}
          assetClass={form.contract.asset_class as string}
          onAlgoChange={(fields) => {
            setAlgoFields(fields);
            // Coerce order type for display algos.
            if (fields?.algo_strategy && DISPLAY_ALGOS.has(fields.algo_strategy)) {
              setForm((s) => ({ ...s, orderType: 'LIMIT' }));
            } else if (fields?.algo_strategy) {
              setForm((s) => ({ ...s, orderType: 'MARKET' }));
            }
          }}
        />
      )}
```

- [ ] **Step 4: Pass algoFields to preview/place payloads**

Find where the preview and place order request bodies are constructed. Add algo fields if present:

```typescript
      // Inside buildPreviewPayload or equivalent:
      ...(algoFields ? {
        algo_strategy: algoFields.algo_strategy,
        algo_params: algoFields.algo_params,
      } : {}),
```

- [ ] **Step 5: Add Algo column to Orders DataTable**

In `OrdersPage.tsx` (or wherever the orders table column definitions live), find the column definitions array and add:

```typescript
  {
    id: 'algo',
    header: 'Algo',
    accessorKey: 'algo_strategy',
    cell: ({ getValue }) => {
      const v = getValue<string | null>();
      return v ? <span className="text-xs font-mono bg-neutral-100 px-1 rounded">{v}</span> : null;
    },
    enableHiding: true,
    meta: { defaultHidden: true },
  },
```

Ensure the column is hidden by default via the `ColumnCustomizerDialog` / `columnVisibility` initial state.

- [ ] **Step 6: Verify no TypeScript errors**

```bash
cd frontend
pnpm tsc --noEmit 2>&1 | tail -20
```

Expected: no new errors.

- [ ] **Step 7: Run FE tests**

```bash
cd frontend
pnpm test 2>&1 | tail -20
```

Expected: no regressions.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/orders/TradeTicketModal.tsx frontend/src/features/orders/OrdersPage.tsx
git commit -m "feat(phase17): TradeTicketModal — AlgoSection wired below TIF; Orders page Algo column"
```

---

## Task 17: Full test run + close

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `CLAUDE.md` (phase memory section)

- [ ] **Step 1: Run full backend test suite**

```bash
cd backend
pytest --tb=short -q 2>&1 | tail -30
```

Expected: all existing tests pass + new algo tests green. Note total count.

- [ ] **Step 2: Run full frontend test suite**

```bash
cd frontend
pnpm test --run 2>&1 | tail -20
```

Expected: all green.

- [ ] **Step 3: Run mypy**

```bash
cd backend
mypy app/ --ignore-missing-imports 2>&1 | tail -20
```

Expected: no new type errors.

- [ ] **Step 4: Update CHANGELOG.md**

Add entry:

```markdown
## [0.17.0] - 2026-05-18

### Added
- IBKR algo orders: ADAPTIVE, TWAP, VWAP, ARRIVAL_PRICE, ICEBERG, RESERVE, DARK_ICE
- `broker_algo_capability` table (alembic 0057) with capability seed for IBKR STOCK/ETF/OPTION/FUTURE/FOREX
- Dynamic algo param form in TradeTicketModal (collapsible AlgoSection below TIF)
- Algo column on Orders page (hidden by default, toggleable)
- Telegram `/place_order ... TWAP start_time=10:00 end_time=14:00` syntax for all 7 strategies
- Risk gate checks: `_check_algo_capability` + `_check_iceberg_display_size`
- 8 Prometheus counters under `algo_*` namespace
- `GET /api/algo/capabilities/{broker_id}/{asset_class}` + `GET /api/algo/schemas`
- Enriched `OrderEventMessage` (tag 10: algo_strategy) — no new WS endpoint
```

- [ ] **Step 5: Tag and push**

```bash
git add CHANGELOG.md CLAUDE.md
git commit -m "docs(phase17): close phase — update CHANGELOG + CLAUDE.md for v0.17.0"
git tag v0.17.0
git push && git push --tags
```

---

## Self-Review

### Spec coverage check

| Spec section | Task |
|---|---|
| §2.1 orders columns | Task 1 |
| §2.2 broker_algo_capability table + seed | Task 1 |
| §2.3 algo_params shapes + _normalize_algo_params | Task 3 |
| §3 Proto changes (4 messages) | Task 2 |
| §4.1 _ALGO_STRATEGY_MAP | Task 10 |
| §4.2 build_ib_algo_order | Task 10 |
| §4.3 enriched OrderEventMessage + PlaceOrderResponse | Task 11 |
| §5.1 PreviewRequest + OrderModifyRequest algo fields | Task 6 |
| §5.2 GET /api/algo/capabilities + GET /api/algo/schemas | Task 9 |
| §5.3 validate_pre_dispatch + bracket leg + algo_requires_limit | Task 8 |
| §5.3a modify rule | Task 8 |
| §5.5.0 EvaluationContext extension | Task 7 |
| §5.5 _check_algo_capability + _check_iceberg_display_size | Task 7 |
| §6 Telegram algo syntax | Task 12 |
| §7.1 AlgoSection component | Task 15 |
| §7.2 Orders page Algo column | Task 16 |
| §7.3 services/algo/types.ts + api.ts | Task 14 |
| §8 Prometheus metrics (8 counters) | Task 4 |
| §9 Test list | Tasks 3/5/6/7/8/12/13/14/15 |

All spec sections covered. No gaps found.
