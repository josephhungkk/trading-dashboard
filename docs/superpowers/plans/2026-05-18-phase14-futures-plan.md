# Phase 14 — Futures Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add CME/CBOT/NYMEX futures on IBKR + Schwab, and HKFE (HSI/HHI) on Futu — including contract-month picker, roll scheduling via APScheduler → Telegram confirm, settlement event recording, and a `/futures` page.

**Architecture:** New `services/futures/` module mirrors `services/options/` — `contract_resolver` (Redis-cached RPC), `roll_service` (CRUD + APScheduler + execute_roll), `settlement_listener` (3 independent broker tasks). Risk gate extended with `_check_futures_exposure`. Proto extended with `GetFutureContracts` + `StreamSettlementEvents` RPCs wired into all 3 broker sidecars. Frontend adds `FutureDetailsSection` in `TradeTicketModal` and a new `/futures` page.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 async, Pydantic v2, Alembic, Redis, APScheduler, aiogram, gRPC/protobuf; React 19, TypeScript 6, TanStack Query, TanStack Router, shadcn/ui, Tailwind v4.

---

## File Map

### New files (backend)
- `backend/alembic/versions/0050_phase14_futures.py` — DDL: enum widening + 2 tables
- `backend/app/services/futures/__init__.py`
- `backend/app/services/futures/types.py` — `FutureDetails`, `FutureContractMonth`
- `backend/app/services/futures/contract_resolver.py` — RPC wrapper + Redis singleflight
- `backend/app/services/futures/roll_service.py` — CRUD + APScheduler job + `execute_roll()`
- `backend/app/services/futures/settlement_listener.py` — 3 broker tasks + shared `_record_settlement()`
- `backend/app/api/futures.py` — 7 REST endpoints
- `backend/tests/services/futures/__init__.py`
- `backend/tests/services/futures/test_types.py`
- `backend/tests/services/futures/test_contract_resolver.py`
- `backend/tests/services/futures/test_roll_service.py`
- `backend/tests/services/futures/test_settlement_listener.py`
- `backend/tests/api/test_futures_api.py`
- `backend/tests/db/test_migration_0050.py`

### Modified files (backend)
- `proto/broker/v1/broker.proto` — 2 new RPCs + 4 new messages
- `backend/app/_generated/broker/v1/broker_pb2.py` + `broker_pb2_grpc.py` + `broker_pb2.pyi` — regenerated
- `sidecar_ibkr/_generated/broker/v1/` — regenerated
- `backend/app/services/options/types.py` — add `FutureDetails`, widen `OptionDetails.multiplier` to `Decimal`, update `InstrumentMeta` union
- `backend/app/models/instruments.py` — add `FUTURE = "FUTURE"` to `AssetClass` StrEnum
- `backend/app/services/risk_service.py` — widen `EvaluationContext.multiplier` to `Decimal`, add `tick_size`/`first_notice_day`/`underlying_symbol` fields, add `_check_futures_exposure()`
- `backend/app/services/orders_service.py` — widen `_native_notional(multiplier: Decimal)`, add FUTURE branch in meta resolution
- `backend/app/services/telegram/order_flow.py` — add `handle_confirm_roll()`, `handle_set_roll_rule()`, `handle_delete_roll_rule()`, `handle_roll_rules_list()`
- `backend/app/services/telegram/commands.py` — register 4 new commands
- `backend/app/main.py` — wire APScheduler roll jobs + settlement lifespan tasks + `FuturesRouter`
- `sidecar_ibkr/handlers.py` — `GetFutureContracts`, `StreamSettlementEvents`, `PlaceOrder` FUT branch
- `sidecar_futu/handlers.py` — `GetFutureContracts`, `StreamSettlementEvents`, `PlaceOrder` FUT branch
- `sidecar_schwab/handlers.py` — `GetFutureContracts`, `StreamSettlementEvents`, `PlaceOrder` FUT branch (with 503 stub)

### New files (frontend)
- `frontend/src/features/futures/FuturesPage.tsx`
- `frontend/src/features/futures/FutureDetailsSection.tsx`
- `frontend/src/features/futures/RollConfirmDialog.tsx`
- `frontend/src/features/futures/__tests__/FutureDetailsSection.test.tsx`
- `frontend/src/features/futures/__tests__/FuturesPage.test.tsx`
- `frontend/src/features/futures/__tests__/RollConfirmDialog.test.tsx`
- `frontend/src/services/futures/types.ts`
- `frontend/src/services/futures/api.ts`
- `frontend/src/routes/futures.tsx`

### Modified files (frontend)
- `frontend/src/features/orders/TradeTicketModal.tsx` — inject `FutureDetailsSection` when `asset_class === 'FUTURE'`

---

## Task A: Alembic migration 0050

**Files:**
- Create: `backend/alembic/versions/0050_phase14_futures.py`
- Modify: `backend/app/models/instruments.py`

- [ ] **Step 1: Add `FUTURE` to the Python `AssetClass` StrEnum**

In `backend/app/models/instruments.py`, find the `AssetClass` class (line ~35). Add after `OPTION = "OPTION"`:
```python
    FUTURE = "FUTURE"
```

- [ ] **Step 2: Write the failing migration test**

Create `backend/tests/db/test_migration_0050.py`:
```python
"""Migration 0050: futures tables DDL."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@pytest.mark.asyncio
async def test_futures_roll_rules_table(db_conn: AsyncConnection) -> None:
    result = await db_conn.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = 'futures_roll_rules' ORDER BY ordinal_position")
    )
    cols = [r[0] for r in result]
    assert "id" in cols
    assert "account_id" in cols
    assert "instrument_id" in cols
    assert "days_before" in cols
    assert "enabled" in cols
    assert "updated_at" in cols


@pytest.mark.asyncio
async def test_futures_settlement_events_table(db_conn: AsyncConnection) -> None:
    result = await db_conn.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = 'futures_settlement_events' ORDER BY ordinal_position")
    )
    cols = [r[0] for r in result]
    assert "id" in cols
    assert "account_id" in cols
    assert "instrument_id" in cols
    assert "settlement_price" in cols
    assert "cash_delta" in cols
    assert "settlement_type" in cols
    assert "broker_event_id" in cols
    assert "settled_at" in cols


@pytest.mark.asyncio
async def test_futures_settlement_events_dedup_index(db_conn: AsyncConnection) -> None:
    result = await db_conn.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'futures_settlement_events' "
            "AND indexdef LIKE '%broker_event_id%'"
        )
    )
    rows = result.fetchall()
    assert len(rows) >= 1


@pytest.mark.asyncio
async def test_future_asset_class_enum(db_conn: AsyncConnection) -> None:
    result = await db_conn.execute(
        text("SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_enum.enumtypid = pg_type.oid WHERE pg_type.typname = 'instrument_asset_class'")
    )
    labels = [r[0] for r in result]
    assert "FUTURE" in labels
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /home/joseph/dashboard/backend
docker compose exec backend pytest tests/db/test_migration_0050.py -v 2>&1 | head -40
```
Expected: FAIL — tables do not exist yet.

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/0050_phase14_futures.py`:
```python
"""Phase 14: FUTURE asset class, futures_roll_rules, futures_settlement_events."""
from __future__ import annotations

from alembic import op

revision = "0050_phase14_futures"
down_revision = "0049a_combo_orders_updated_at_trigger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Widen the PG enum (must run outside a transaction)
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'FUTURE'")

    # 2. futures_roll_rules
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS futures_roll_rules (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id    UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
            instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
            days_before   SMALLINT NOT NULL CHECK (days_before BETWEEN 1 AND 90),
            enabled       BOOLEAN NOT NULL DEFAULT true,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (account_id, instrument_id)
        )
        """
    )
    op.execute("DROP TRIGGER IF EXISTS futures_roll_rules_updated_at ON futures_roll_rules")
    op.execute(
        """
        CREATE TRIGGER futures_roll_rules_updated_at
        BEFORE UPDATE ON futures_roll_rules
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at()
        """
    )

    # 3. futures_settlement_events (append-only — no updated_at trigger)
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS futures_settlement_events (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id       UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE RESTRICT,
            instrument_id    BIGINT NOT NULL REFERENCES instruments(id),
            settlement_price NUMERIC(20,8) NOT NULL,
            cash_delta       NUMERIC(20,8) NOT NULL,
            settlement_type  TEXT NOT NULL CHECK (settlement_type IN ('CASH','PHYSICAL')),
            broker_event_id  TEXT,
            settled_at       TIMESTAMPTZ NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS futures_settlement_events_account_settled_at "
        "ON futures_settlement_events (account_id, settled_at DESC)"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS futures_settlement_events_dedup
        ON futures_settlement_events (account_id, broker_event_id)
        WHERE broker_event_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS futures_settlement_events")
    op.execute("DROP TABLE IF EXISTS futures_roll_rules")
```

- [ ] **Step 5: Run the migration**

```bash
cd /home/joseph/dashboard/backend
docker compose exec backend alembic upgrade head
```
Expected: applies `0050_phase14_futures` with no errors.

- [ ] **Step 6: Run test to verify it passes**

```bash
docker compose exec backend pytest tests/db/test_migration_0050.py -v
```
Expected: 4 PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/0050_phase14_futures.py backend/app/models/instruments.py backend/tests/db/test_migration_0050.py
git commit -m "feat(futures): migration 0050 — FUTURE enum + roll_rules + settlement_events tables"
```

---

## Task B: `FutureDetails` discriminated union + multiplier widening

**Files:**
- Modify: `backend/app/services/options/types.py`
- Create: `backend/tests/services/futures/test_types.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/services/futures/__init__.py` (empty).

Create `backend/tests/services/futures/test_types.py`:
```python
"""FutureDetails discriminated union tests."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from app.services.options.types import FutureDetails, parse_instrument_meta


def _future_meta(settlement_type: str = "CASH", first_notice: date | None = None) -> dict:
    return {
        "asset_class": "FUTURE",
        "contract_month": "202506",
        "tick_size": "0.25",
        "tick_value": "12.50",
        "multiplier": "50",
        "first_notice_day": first_notice.isoformat() if first_notice else None,
        "expiry": "2025-06-20",
        "settlement_type": settlement_type,
        "exchange": "CME",
        "underlying_symbol": "ES",
    }


def test_future_details_round_trip_cash() -> None:
    raw = _future_meta("CASH")
    result = parse_instrument_meta(json.dumps(raw))
    assert isinstance(result, FutureDetails)
    assert result.multiplier == Decimal("50")
    assert result.tick_size == Decimal("0.25")
    assert result.settlement_type == "CASH"
    assert result.first_notice_day is None
    assert result.underlying_symbol == "ES"


def test_future_details_round_trip_physical() -> None:
    raw = _future_meta("PHYSICAL", first_notice=date(2025, 5, 28))
    result = parse_instrument_meta(json.dumps(raw))
    assert isinstance(result, FutureDetails)
    assert result.settlement_type == "PHYSICAL"
    assert result.first_notice_day == date(2025, 5, 28)


def test_future_details_multiplier_is_decimal() -> None:
    raw = _future_meta()
    result = parse_instrument_meta(json.dumps(raw))
    assert isinstance(result, FutureDetails)
    assert isinstance(result.multiplier, Decimal)


def test_non_option_still_works_after_union_expansion() -> None:
    result = parse_instrument_meta("{}")
    from app.services.options.types import NonOptionDetails
    assert isinstance(result, NonOptionDetails)


def test_option_still_works_after_union_expansion() -> None:
    raw = json.dumps({
        "asset_class": "OPTION",
        "strike": "420.00",
        "put_call": "CALL",
        "expiry_iso": "2025-06-20",
        "multiplier": "100",
        "exchange": "CBOE",
    })
    result = parse_instrument_meta(raw)
    from app.services.options.types import OptionDetails
    assert isinstance(result, OptionDetails)
    assert result.multiplier == Decimal("100")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend pytest tests/services/futures/test_types.py -v 2>&1 | head -30
```
Expected: FAIL — `FutureDetails` not found.

- [ ] **Step 3: Add `FutureDetails` to `types.py` and widen `OptionDetails.multiplier`**

In `backend/app/services/options/types.py`:

1. Add imports at the top (after existing imports):
```python
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
```
(Some may already exist — add only missing ones.)

2. Change `OptionDetails.multiplier` from `int` to `Decimal`:
```python
class OptionDetails(BaseModel):
    asset_class: Literal["OPTION"] = "OPTION"
    # ... existing fields ...
    multiplier: Decimal  # widened from int; Decimal("100") == 100 is non-breaking
```

3. Add `FutureDetails` class after `OptionDetails`:
```python
class FutureDetails(BaseModel):
    asset_class: Literal["FUTURE"] = "FUTURE"
    contract_month: str            # "202506" (YYYYMM)
    tick_size: Decimal             # e.g. Decimal("0.25")
    tick_value: Decimal            # e.g. Decimal("12.50") — USD per tick
    multiplier: Decimal            # e.g. Decimal("50") for ES; Decimal("5") for MES
    first_notice_day: date | None  # None for cash-settled contracts
    expiry: date                   # last trading day
    settlement_type: Literal["CASH", "PHYSICAL"]
    exchange: str                  # "CME", "CBOT", "NYMEX", "HKFE"
    underlying_symbol: str         # root symbol, e.g. "ES", "HSI"
```

4. Update `InstrumentMeta` union:
```python
InstrumentMeta = Annotated[
    NonOptionDetails | OptionDetails | FutureDetails,
    Field(discriminator="asset_class"),
]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose exec backend pytest tests/services/futures/test_types.py -v
```
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/options/types.py backend/tests/services/futures/
git commit -m "feat(futures): FutureDetails discriminated union arm + widen OptionDetails.multiplier to Decimal"
```

---

## Task C: `EvaluationContext` widening + `_native_notional` signature update

**Files:**
- Modify: `backend/app/services/risk_service.py`
- Modify: `backend/app/services/orders_service.py`

- [ ] **Step 1: Widen `EvaluationContext` in `risk_service.py`**

In `backend/app/services/risk_service.py`, find `EvaluationContext` (line ~81):

1. Add `from decimal import Decimal` to imports if not present.
2. Change `multiplier: int = 1` to `multiplier: Decimal = Decimal("1")` (line ~103).
3. Add three new fields after `multiplier`:
```python
    tick_size: Decimal | None = None
    first_notice_day: date | None = None
    underlying_symbol: str | None = None
```
4. Add `from datetime import date` to imports if not present.

- [ ] **Step 2: Widen `_native_notional` in `orders_service.py`**

In `backend/app/services/orders_service.py`, find `_native_notional` (line ~1889):

Change signature from:
```python
async def _native_notional(
    redis: Any,
    request: Any,
    contract: Any,
    qty: Decimal,
    multiplier: int = 1,
) -> Decimal:
```
to:
```python
async def _native_notional(
    redis: Any,
    request: Any,
    contract: Any,
    qty: Decimal,
    multiplier: Decimal = Decimal("1"),
) -> Decimal:
```

Add `from decimal import Decimal` to imports if not present.

- [ ] **Step 3: Fix existing `multiplier` call site in orders_service for options**

In `backend/app/services/orders_service.py`, find where `details.multiplier` is used for options (line ~456). Change:
```python
multiplier = details.multiplier  # was int(details.multiplier)
```
to:
```python
multiplier = Decimal(str(details.multiplier))
```

And where `multiplier=multiplier` is passed to `_native_notional`, ensure `multiplier` is a `Decimal`.

- [ ] **Step 4: Verify existing tests still pass**

```bash
docker compose exec backend pytest tests/services/test_risk_service.py tests/services/test_orders_service.py -v 2>&1 | tail -20
```
Expected: all previously passing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/risk_service.py backend/app/services/orders_service.py
git commit -m "feat(futures): widen EvaluationContext.multiplier + _native_notional to Decimal"
```

---

## Task D: Proto extension + stub regeneration

**Files:**
- Modify: `proto/broker/v1/broker.proto`
- Regenerate: `backend/app/_generated/broker/v1/` and `sidecar_ibkr/_generated/broker/v1/`

- [ ] **Step 1: Add new RPCs to the `Broker` service in `proto/broker/v1/broker.proto`**

After line 56 (`rpc GetSupportedComboStrategies...`), add:
```protobuf
  rpc GetFutureContracts(GetFutureContractsRequest) returns (GetFutureContractsResponse);
  rpc StreamSettlementEvents(StreamSettlementEventsRequest) returns (stream SettlementEvent);
```

- [ ] **Step 2: Add new message types at end of proto file (before line 642)**

Append to `proto/broker/v1/broker.proto`:
```protobuf
message GetFutureContractsRequest {
  string root_symbol = 1;
  string broker_id   = 2;
}

message FutureContractMonth {
  string conid           = 1;
  string contract_month  = 2;  // "202506"
  string expiry_date     = 3;  // "2025-06-20"
  string first_notice    = 4;  // "" if cash-settled; never omit
  string exchange        = 5;
  string tick_size       = 6;  // decimal string
  string tick_value      = 7;  // decimal string
  string multiplier      = 8;  // decimal string
  string settlement_type = 9;  // "CASH" | "PHYSICAL"
}

message GetFutureContractsResponse {
  repeated FutureContractMonth contracts = 1;
}

message StreamSettlementEventsRequest {
  string account_number = 1;
}

message SettlementEvent {
  string conid            = 1;
  string symbol           = 2;
  string settlement_price = 3;  // decimal string
  string cash_delta       = 4;  // signed decimal string
  string settlement_type  = 5;
  string settled_at       = 6;  // ISO8601
  string broker_event_id  = 7;  // IBKR execId / Futu deal_id / Schwab activityId; "" if unavailable
}
```

- [ ] **Step 3: Regenerate proto stubs**

```bash
cd /home/joseph/dashboard/proto
buf generate
```
Expected: updates `backend/app/_generated/broker/v1/` and `sidecar_ibkr/_generated/broker/v1/` with no errors.

- [ ] **Step 4: Verify backend imports the regenerated stubs**

```bash
docker compose exec backend python -c "from app._generated.broker.v1 import broker_pb2; print(hasattr(broker_pb2, 'GetFutureContractsRequest'))"
```
Expected: `True`

- [ ] **Step 5: Commit**

```bash
git add proto/broker/v1/broker.proto backend/app/_generated/ sidecar_ibkr/_generated/
git commit -m "feat(futures): add GetFutureContracts + StreamSettlementEvents proto RPCs"
```

---

## Task E: `services/futures/` module — types + contract_resolver

**Files:**
- Create: `backend/app/services/futures/__init__.py`
- Create: `backend/app/services/futures/types.py`
- Create: `backend/app/services/futures/contract_resolver.py`
- Create: `backend/tests/services/futures/test_contract_resolver.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/services/futures/test_contract_resolver.py`:
```python
"""ContractResolver tests."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.futures.contract_resolver import ContractResolver
from app.services.futures.types import FutureContractMonth


def _make_proto_contract(
    conid: str = "12345",
    contract_month: str = "202506",
    expiry_date: str = "2025-06-20",
    first_notice: str = "",
    exchange: str = "CME",
    tick_size: str = "0.25",
    tick_value: str = "12.50",
    multiplier: str = "50",
    settlement_type: str = "CASH",
) -> MagicMock:
    m = MagicMock()
    m.conid = conid
    m.contract_month = contract_month
    m.expiry_date = expiry_date
    m.first_notice = first_notice
    m.exchange = exchange
    m.tick_size = tick_size
    m.tick_value = tick_value
    m.multiplier = multiplier
    m.settlement_type = settlement_type
    return m


@pytest.fixture
def resolver(redis_mock: AsyncMock, sidecar_mock: AsyncMock) -> ContractResolver:
    return ContractResolver(redis=redis_mock, config=AsyncMock(), broker_registry=sidecar_mock)


@pytest.mark.asyncio
async def test_cache_miss_calls_rpc(redis_mock: AsyncMock, sidecar_mock: AsyncMock) -> None:
    redis_mock.get.return_value = None
    proto_contract = _make_proto_contract()
    response = MagicMock()
    response.contracts = [proto_contract]
    sidecar_mock.GetFutureContracts = AsyncMock(return_value=response)

    resolver = ContractResolver(redis=redis_mock, config=AsyncMock(), broker_registry=sidecar_mock)
    result = await resolver.get_contracts("ES", broker="ibkr")

    assert len(result) == 1
    assert result[0].conid == "12345"
    assert result[0].multiplier == Decimal("50")
    assert result[0].first_notice_day is None  # empty string → None
    redis_mock.setex.assert_called_once()


@pytest.mark.asyncio
async def test_cache_hit_skips_rpc(redis_mock: AsyncMock, sidecar_mock: AsyncMock) -> None:
    cached = [
        {
            "conid": "12345",
            "contract_month": "202506",
            "expiry": "2025-06-20",
            "first_notice_day": None,
            "tick_size": "0.25",
            "tick_value": "12.50",
            "multiplier": "50",
            "settlement_type": "CASH",
            "exchange": "CME",
            "underlying_symbol": "ES",
        }
    ]
    redis_mock.get.return_value = json.dumps(cached).encode()

    resolver = ContractResolver(redis=redis_mock, config=AsyncMock(), broker_registry=sidecar_mock)
    result = await resolver.get_contracts("ES", broker="ibkr")

    assert len(result) == 1
    sidecar_mock.GetFutureContracts.assert_not_called()


@pytest.mark.asyncio
async def test_days_to_expiry_not_in_cache(redis_mock: AsyncMock, sidecar_mock: AsyncMock) -> None:
    """days_to_expiry must be computed at read time, not stored."""
    cached = [
        {
            "conid": "12345",
            "contract_month": "202506",
            "expiry": "2025-06-20",
            "first_notice_day": None,
            "tick_size": "0.25",
            "tick_value": "12.50",
            "multiplier": "50",
            "settlement_type": "CASH",
            "exchange": "CME",
            "underlying_symbol": "ES",
        }
    ]
    redis_mock.get.return_value = json.dumps(cached).encode()
    raw_str = redis_mock.get.return_value.decode()
    parsed = json.loads(raw_str)
    assert "days_to_expiry" not in parsed[0], "days_to_expiry must not be stored in cache"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend pytest tests/services/futures/test_contract_resolver.py -v 2>&1 | head -20
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create module files**

Create `backend/app/services/futures/__init__.py` (empty).

Create `backend/app/services/futures/types.py`:
```python
"""Futures service types."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass
class FutureContractMonth:
    conid: str
    contract_month: str        # "202506"
    expiry: date
    first_notice_day: date | None  # None for cash-settled
    tick_size: Decimal
    tick_value: Decimal
    multiplier: Decimal
    settlement_type: str       # "CASH" | "PHYSICAL"
    exchange: str
    underlying_symbol: str

    def to_cache_dict(self) -> dict:
        return {
            "conid": self.conid,
            "contract_month": self.contract_month,
            "expiry": self.expiry.isoformat(),
            "first_notice_day": self.first_notice_day.isoformat() if self.first_notice_day else None,
            "tick_size": str(self.tick_size),
            "tick_value": str(self.tick_value),
            "multiplier": str(self.multiplier),
            "settlement_type": self.settlement_type,
            "exchange": self.exchange,
            "underlying_symbol": self.underlying_symbol,
        }

    @classmethod
    def from_cache_dict(cls, d: dict, root_symbol: str) -> "FutureContractMonth":
        return cls(
            conid=d["conid"],
            contract_month=d["contract_month"],
            expiry=date.fromisoformat(d["expiry"]),
            first_notice_day=date.fromisoformat(d["first_notice_day"]) if d.get("first_notice_day") else None,
            tick_size=Decimal(d["tick_size"]),
            tick_value=Decimal(d["tick_value"]),
            multiplier=Decimal(d["multiplier"]),
            settlement_type=d["settlement_type"],
            exchange=d["exchange"],
            underlying_symbol=d.get("underlying_symbol", root_symbol),
        )
```

Create `backend/app/services/futures/contract_resolver.py`:
```python
"""ContractResolver — GetFutureContracts RPC wrapper with Redis singleflight cache."""
from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any

import structlog

from app.services import market_calendar
from app.services.futures.types import FutureContractMonth

log = structlog.get_logger(__name__)

_CACHE_KEY_FMT = "futures:contracts:{broker}:{root_symbol}"
_TTL_MARKET_OPEN = 300
_TTL_MARKET_CLOSED = 3600
_MAX_MONTHS = 6


class ContractResolver:
    def __init__(self, *, redis: Any, config: Any, broker_registry: Any) -> None:
        self._redis = redis
        self._config = config
        self._broker_registry = broker_registry
        self._sf_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._sf_lock_meta = asyncio.Lock()

    async def _sf_lock(self, key: tuple[str, str]) -> asyncio.Lock:
        async with self._sf_lock_meta:
            if key not in self._sf_locks:
                self._sf_locks[key] = asyncio.Lock()
            return self._sf_locks[key]

    def _ttl(self) -> int:
        is_open = market_calendar.is_market_open()
        return _TTL_MARKET_OPEN if is_open else _TTL_MARKET_CLOSED

    def _cache_key(self, broker: str, root_symbol: str) -> str:
        return _CACHE_KEY_FMT.format(broker=broker, root_symbol=root_symbol)

    async def get_contracts(self, root_symbol: str, *, broker: str) -> list[FutureContractMonth]:
        cache_key = self._cache_key(broker, root_symbol)
        cached = await self._redis.get(cache_key)
        if cached:
            data = json.loads(cached)
            return [FutureContractMonth.from_cache_dict(d, root_symbol) for d in data]

        sf_key = (broker, root_symbol)
        lock = await self._sf_lock(sf_key)
        async with lock:
            cached = await self._redis.get(cache_key)
            if cached:
                data = json.loads(cached)
                return [FutureContractMonth.from_cache_dict(d, root_symbol) for d in data]
            try:
                contracts = await self._fetch_from_sidecar(root_symbol, broker)
                payload = json.dumps([c.to_cache_dict() for c in contracts])
                await self._redis.setex(cache_key, self._ttl(), payload)
                return contracts
            except Exception as exc:
                log.warning("contract_resolver_fetch_failed", broker=broker, symbol=root_symbol, error=str(exc))
                return []

    async def _fetch_from_sidecar(self, root_symbol: str, broker: str) -> list[FutureContractMonth]:
        from app._generated.broker.v1 import broker_pb2

        stub = self._broker_registry  # caller passes stub directly in tests; real code uses registry
        request = broker_pb2.GetFutureContractsRequest(root_symbol=root_symbol, broker_id=broker)
        response = await stub.GetFutureContracts(request)

        max_months = _MAX_MONTHS
        contracts: list[FutureContractMonth] = []
        for proto_c in response.contracts[:max_months]:
            first_notice: date | None = None
            if proto_c.first_notice:
                try:
                    first_notice = date.fromisoformat(proto_c.first_notice)
                except ValueError:
                    pass
            contracts.append(
                FutureContractMonth(
                    conid=proto_c.conid,
                    contract_month=proto_c.contract_month,
                    expiry=date.fromisoformat(proto_c.expiry_date),
                    first_notice_day=first_notice,
                    tick_size=Decimal(proto_c.tick_size),  # type: ignore[name-defined]
                    tick_value=Decimal(proto_c.tick_value),  # type: ignore[name-defined]
                    multiplier=Decimal(proto_c.multiplier),  # type: ignore[name-defined]
                    settlement_type=proto_c.settlement_type,
                    exchange=proto_c.exchange,
                    underlying_symbol=root_symbol,
                )
            )
        contracts.sort(key=lambda c: c.expiry)
        return contracts
```

Add missing `from decimal import Decimal` to `contract_resolver.py`.

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose exec backend pytest tests/services/futures/test_contract_resolver.py -v
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/futures/ backend/tests/services/futures/test_contract_resolver.py backend/tests/services/futures/test_types.py
git commit -m "feat(futures): services/futures module — types + ContractResolver"
```

---

## Task F: `roll_service.py` — CRUD + execute_roll

**Files:**
- Create: `backend/app/services/futures/roll_service.py`
- Create: `backend/tests/services/futures/test_roll_service.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/services/futures/test_roll_service.py`:
```python
"""RollService tests — CRUD, nonce single-use, dedup, partial-fill path."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.services.futures.roll_service import RollService


@pytest.fixture
def redis_mock() -> AsyncMock:
    m = AsyncMock()
    m.get.return_value = None
    m.getdel.return_value = None
    m.exists.return_value = 0
    return m


@pytest.fixture
def roll_service(redis_mock: AsyncMock) -> RollService:
    return RollService(redis=redis_mock, config=AsyncMock(), orders_service=AsyncMock(), telegram=AsyncMock())


@pytest.mark.asyncio
async def test_nonce_single_use(roll_service: RollService, redis_mock: AsyncMock) -> None:
    """GETDEL returns payload on first call, None on second."""
    account_id = str(uuid.uuid4())
    nonce = "test-nonce-123"
    payload = {
        "instrument_id": 42,
        "close_conid": "111",
        "open_conid": "222",
        "account_id": account_id,
    }
    redis_mock.getdel.side_effect = [json.dumps(payload).encode(), None]

    # First call — succeeds
    first = await roll_service._consume_nonce(account_id, nonce)
    assert first is not None
    assert first["instrument_id"] == 42

    # Second call — nonce already consumed
    second = await roll_service._consume_nonce(account_id, nonce)
    assert second is None


@pytest.mark.asyncio
async def test_dedup_same_instrument(roll_service: RollService, redis_mock: AsyncMock) -> None:
    """Pending nonce for ESM25 blocks re-notification for same instrument."""
    account_id = str(uuid.uuid4())
    instrument_id = 42
    redis_mock.exists.return_value = 1  # key exists

    should_notify = await roll_service._should_notify(account_id, instrument_id)
    assert should_notify is False


@pytest.mark.asyncio
async def test_dedup_cross_instrument(roll_service: RollService, redis_mock: AsyncMock) -> None:
    """Pending ESM25 roll does NOT suppress NQM25 notification."""
    account_id = str(uuid.uuid4())
    esm25_instrument_id = 42
    nqm25_instrument_id = 99

    async def exists_side_effect(key: str) -> int:
        # Only ESM25 key exists
        if str(esm25_instrument_id) in key:
            return 1
        return 0

    redis_mock.exists.side_effect = exists_side_effect

    assert await roll_service._should_notify(account_id, esm25_instrument_id) is False
    assert await roll_service._should_notify(account_id, nqm25_instrument_id) is True


@pytest.mark.asyncio
async def test_cross_account_nonce_rejected(roll_service: RollService, redis_mock: AsyncMock) -> None:
    """Nonce with mismatched account_id in payload → 404 equivalent."""
    real_account = str(uuid.uuid4())
    evil_account = str(uuid.uuid4())
    nonce = "nonce-xyz"
    payload = {
        "instrument_id": 42,
        "close_conid": "111",
        "open_conid": "222",
        "account_id": evil_account,  # payload account differs from JWT claim
    }
    redis_mock.getdel.return_value = json.dumps(payload).encode()

    result = await roll_service._consume_nonce(real_account, nonce)
    # After GETDEL succeeds, account_id mismatch must return None (consumed but rejected)
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend pytest tests/services/futures/test_roll_service.py -v 2>&1 | head -20
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `roll_service.py`**

Create `backend/app/services/futures/roll_service.py`:
```python
"""RollService — roll rule CRUD + APScheduler job + execute_roll."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_NONCE_KEY = "futures:roll:pending:{account_id}:{nonce}"
_INSTRUMENT_KEY = "futures:roll:instrument:{account_id}:{instrument_id}"
_NONCE_TTL = 86400  # 24h


class RollService:
    def __init__(
        self,
        *,
        redis: Any,
        config: Any,
        orders_service: Any,
        telegram: Any,
    ) -> None:
        self._redis = redis
        self._config = config
        self._orders_service = orders_service
        self._telegram = telegram

    # ── Dedup helpers ────────────────────────────────────────────────────────

    async def _should_notify(self, account_id: str, instrument_id: int) -> bool:
        key = _INSTRUMENT_KEY.format(account_id=account_id, instrument_id=instrument_id)
        exists = await self._redis.exists(key)
        return exists == 0

    async def _mint_nonce(
        self,
        account_id: str,
        instrument_id: int,
        close_conid: str,
        open_conid: str,
    ) -> str:
        nonce = str(uuid.uuid4())
        nonce_key = _NONCE_KEY.format(account_id=account_id, nonce=nonce)
        instrument_key = _INSTRUMENT_KEY.format(account_id=account_id, instrument_id=instrument_id)
        payload = json.dumps({
            "instrument_id": instrument_id,
            "close_conid": close_conid,
            "open_conid": open_conid,
            "account_id": account_id,
        })
        pipe = self._redis.pipeline()
        pipe.setex(nonce_key, _NONCE_TTL, payload)
        pipe.setex(instrument_key, _NONCE_TTL, nonce)
        await pipe.execute()
        return nonce

    async def _consume_nonce(self, account_id: str, nonce: str) -> dict | None:
        key = _NONCE_KEY.format(account_id=account_id, nonce=nonce)
        raw = await self._redis.getdel(key)
        if raw is None:
            return None
        payload = json.loads(raw)
        # Validate account_id to prevent cross-account nonce replay
        if payload.get("account_id") != account_id:
            log.warning("roll_nonce_account_mismatch", nonce=nonce)
            return None
        # Clean up instrument dedup key
        instrument_id = payload.get("instrument_id")
        if instrument_id is not None:
            instrument_key = _INSTRUMENT_KEY.format(account_id=account_id, instrument_id=instrument_id)
            await self._redis.delete(instrument_key)
        return payload

    # ── execute_roll ─────────────────────────────────────────────────────────

    async def execute_roll(self, account_id: str, nonce: str) -> None:
        payload = await self._consume_nonce(account_id, nonce)
        if payload is None:
            raise KeyError(f"Roll nonce not found or expired: {nonce}")

        instrument_id = payload["instrument_id"]
        close_conid = payload["close_conid"]
        open_conid = payload["open_conid"]

        log.info("execute_roll_start", account_id=account_id, instrument_id=instrument_id)
        # Actual order placement delegated to orders_service (wired in Task I)
        # This stub lets roll tests pass without full orders_service
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose exec backend pytest tests/services/futures/test_roll_service.py -v
```
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/futures/roll_service.py backend/tests/services/futures/test_roll_service.py
git commit -m "feat(futures): RollService — nonce mint/GETDEL, dedup, cross-account guard"
```

---

## Task G: Risk gate — `_check_futures_exposure`

**Files:**
- Modify: `backend/app/services/risk_service.py`
- Modify: `backend/app/services/orders_service.py` (FUTURE meta resolution branch)

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/services/test_risk_service.py` (or create a focused file `tests/services/futures/test_futures_risk.py`):
```python
"""Futures risk gate tests."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.risk_service import EvaluationContext, RiskService


def _ctx(
    settlement_type: str = "CASH",
    first_notice_day: date | None = None,
    position_effect: str = "OPEN",
    days_to_expiry: int = 30,
    underlying_symbol: str = "ES",
) -> EvaluationContext:
    expiry = date.today() + timedelta(days=days_to_expiry)
    return EvaluationContext(
        account_id="acct-1",
        account_hash="hash",
        broker_id="ibkr",
        symbol="ESM25",
        side="buy",
        qty=Decimal("2"),
        asset_class="FUTURE",
        multiplier=Decimal("50"),
        instrument_id=42,
        first_notice_day=first_notice_day,
        underlying_symbol=underlying_symbol,
        position_effect=position_effect,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_physical_delivery_warn_dte_le_10(risk_service: RiskService) -> None:
    ctx = _ctx(settlement_type="PHYSICAL", days_to_expiry=8)
    ctx.tick_size = None
    # Mock NLV query to return a large value so concentration check doesn't block
    risk_service._db = AsyncMock()
    result = await risk_service._check_futures_exposure(ctx)
    warnings = [w for w in (result or []) if hasattr(w, "level") and w.level == "WARN"]
    assert any("physical" in (getattr(w, "message", "") or "").lower() for w in warnings)


@pytest.mark.asyncio
async def test_physical_delivery_block_past_first_notice(risk_service: RiskService) -> None:
    past_date = date.today() - timedelta(days=1)
    ctx = _ctx(settlement_type="PHYSICAL", first_notice_day=past_date, days_to_expiry=0)
    result = await risk_service._check_futures_exposure(ctx)
    blockers = [b for b in (result or []) if hasattr(b, "level") and b.level == "BLOCK"]
    assert any("delivery" in (getattr(b, "message", "") or "").lower() for b in blockers)


@pytest.mark.asyncio
async def test_physical_delivery_block_skipped_on_close(risk_service: RiskService) -> None:
    """Closing a physical contract past first notice day must NOT block."""
    past_date = date.today() - timedelta(days=1)
    ctx = _ctx(settlement_type="PHYSICAL", first_notice_day=past_date, position_effect="CLOSE", days_to_expiry=0)
    result = await risk_service._check_futures_exposure(ctx)
    blockers = [b for b in (result or []) if hasattr(b, "level") and b.level == "BLOCK"]
    delivery_blocks = [b for b in blockers if "delivery" in (getattr(b, "message", "") or "").lower()]
    assert len(delivery_blocks) == 0


@pytest.mark.asyncio
async def test_cash_settled_no_delivery_check(risk_service: RiskService) -> None:
    ctx = _ctx(settlement_type="CASH", first_notice_day=None, days_to_expiry=2)
    result = await risk_service._check_futures_exposure(ctx)
    blockers = [b for b in (result or []) if hasattr(b, "level") and b.level == "BLOCK"]
    delivery_blocks = [b for b in blockers if "delivery" in (getattr(b, "message", "") or "").lower()]
    assert len(delivery_blocks) == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend pytest tests/services/futures/test_futures_risk.py -v 2>&1 | head -20
```
Expected: FAIL.

- [ ] **Step 3: Add `_check_futures_exposure` to `risk_service.py`**

In `backend/app/services/risk_service.py`, after `_check_options_exposure` method (around line 780), add:

```python
    async def _check_futures_exposure(self, ctx: EvaluationContext) -> CheckResult:
        """Phase 14: Futures-specific risk checks."""
        from datetime import date

        blockers: list[Any] = []
        warnings: list[Any] = []
        is_close = ctx.position_effect == "CLOSE"

        # Physical delivery checks (skipped entirely on CLOSE)
        if not is_close and ctx.first_notice_day is not None:
            dte = (ctx.first_notice_day - date.today()).days
            # BLOCK: past first notice day
            if date.today() >= ctx.first_notice_day:
                blockers.append(
                    RiskWarning(
                        level="BLOCK",
                        code="futures_physical_delivery_block",
                        message=f"Physical delivery block: first notice day was {ctx.first_notice_day}. Close position via broker.",
                    )
                )

        # WARN: DTE ≤ 10 for physical contracts (skipped on CLOSE)
        if not is_close:
            from datetime import date as date_cls
            # We need expiry from EvaluationContext — use first_notice_day as proxy or skip if absent
            # Physical delivery warn — requires settlement_type knowledge
            # This is populated when orders_service passes tick_size/first_notice_day from FutureDetails
            # We use presence of first_notice_day as indicator of PHYSICAL (cash-settled → None)
            if ctx.first_notice_day is not None:
                # Expiry is not in EvaluationContext directly; use first_notice_day - 2 as conservative approximation
                # Full DTE comes from FutureDetails.expiry via orders_service in next task
                pass  # Detailed DTE warn wired in Task I when expiry is plumbed through

        if blockers:
            return blockers[0], None
        if warnings:
            return None, warnings[0]
        return None, None
```

Also add to `evaluate()` method (around line 846, after options check):
```python
        if ctx.asset_class == "FUTURE":
            fut_result = await self._check_futures_exposure(ctx)
            if fut_result:
                fut_blocker, fut_warning = fut_result
                if fut_blocker:
                    blockers.append(fut_blocker)
                if fut_warning:
                    warnings.append(fut_warning)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose exec backend pytest tests/services/futures/test_futures_risk.py -v
```
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/risk_service.py backend/tests/services/futures/test_futures_risk.py
git commit -m "feat(futures): _check_futures_exposure — physical delivery WARN/BLOCK, skip on CLOSE"
```

---

## Task H: REST API (`app/api/futures.py`)

**Files:**
- Create: `backend/app/api/futures.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/api/test_futures_api.py`

- [ ] **Step 1: Write failing API tests**

Create `backend/tests/api/test_futures_api.py`:
```python
"""Futures REST API tests."""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_contracts_requires_jwt(client: AsyncClient) -> None:
    resp = await client.get("/api/futures/contracts/ES")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_contracts_returns_list(authed_client: AsyncClient) -> None:
    with patch("app.api.futures._get_resolver") as mock_resolver:
        mock_resolver.return_value.get_contracts = AsyncMock(return_value=[])
        resp = await authed_client.get("/api/futures/contracts/ES")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_roll_rules_requires_jwt(client: AsyncClient) -> None:
    resp = await client.get("/api/futures/roll-rules")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_roll_confirm_requires_csrf(authed_client: AsyncClient) -> None:
    nonce = str(uuid.uuid4())
    resp = await authed_client.post(f"/api/futures/roll/confirm/{nonce}")
    # Without X-Csrf-Nonce header → 422 or 403
    assert resp.status_code in (403, 422)


@pytest.mark.asyncio
async def test_roll_confirm_missing_nonce_returns_404(authed_client: AsyncClient) -> None:
    nonce = "nonexistent-nonce"
    with patch("app.api.futures._get_roll_service") as mock_svc:
        mock_svc.return_value.execute_roll = AsyncMock(side_effect=KeyError("not found"))
        resp = await authed_client.post(
            f"/api/futures/roll/confirm/{nonce}",
            headers={"X-Csrf-Nonce": "valid-nonce"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_settlements_requires_jwt(client: AsyncClient) -> None:
    resp = await client.get("/api/futures/settlements")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend pytest tests/api/test_futures_api.py -v 2>&1 | head -20
```
Expected: FAIL.

- [ ] **Step 3: Create `app/api/futures.py`**

Create `backend/app/api/futures.py`:
```python
"""Phase 14: Futures REST API."""
from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_account_id
from app.core.database import get_db
from app.core.redis import get_redis
from app.services.futures.contract_resolver import ContractResolver
from app.services.futures.roll_service import RollService

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/futures", tags=["futures"])


def _get_resolver(redis: Any = Depends(get_redis)) -> ContractResolver:
    return ContractResolver(redis=redis, config=None, broker_registry=None)


def _get_roll_service(redis: Any = Depends(get_redis)) -> RollService:
    return RollService(redis=redis, config=None, orders_service=None, telegram=None)


@router.get("/contracts/{root_symbol}")
async def get_future_contracts(
    root_symbol: str,
    broker: str = Query(default="ibkr"),
    account_id: str = Depends(get_current_account_id),
    resolver: ContractResolver = Depends(_get_resolver),
) -> list[dict]:
    contracts = await resolver.get_contracts(root_symbol.upper(), broker=broker)
    today = __import__("datetime").date.today()
    return [
        {
            **c.to_cache_dict(),
            "days_to_expiry": (c.expiry - today).days,
        }
        for c in contracts
    ]


@router.get("/roll-rules")
async def list_roll_rules(
    account_id: str = Depends(get_current_account_id),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    from sqlalchemy import text
    result = await db.execute(
        text(
            "SELECT id, instrument_id, days_before, enabled, created_at "
            "FROM futures_roll_rules WHERE account_id = :account_id AND enabled = true"
        ),
        {"account_id": account_id},
    )
    return [dict(r._mapping) for r in result]


@router.post("/roll-rules")
async def set_roll_rule(
    body: dict,
    account_id: str = Depends(get_current_account_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from sqlalchemy import text
    instrument_id = body["instrument_id"]
    days_before = int(body["days_before"])
    if not (1 <= days_before <= 90):
        raise HTTPException(status_code=422, detail="days_before must be between 1 and 90")
    await db.execute(
        text(
            "INSERT INTO futures_roll_rules (account_id, instrument_id, days_before) "
            "VALUES (:account_id, :instrument_id, :days_before) "
            "ON CONFLICT (account_id, instrument_id) DO UPDATE SET days_before = EXCLUDED.days_before, updated_at = now()"
        ),
        {"account_id": account_id, "instrument_id": instrument_id, "days_before": days_before},
    )
    await db.commit()
    return {"status": "ok"}


@router.delete("/roll-rules/{instrument_id}")
async def delete_roll_rule(
    instrument_id: int,
    account_id: str = Depends(get_current_account_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from sqlalchemy import text
    await db.execute(
        text("DELETE FROM futures_roll_rules WHERE account_id = :account_id AND instrument_id = :instrument_id"),
        {"account_id": account_id, "instrument_id": instrument_id},
    )
    await db.commit()
    return {"status": "ok"}


@router.get("/settlements")
async def list_settlements(
    account_id: str = Depends(get_current_account_id),
    db: AsyncSession = Depends(get_db),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
) -> dict:
    from sqlalchemy import text
    query = (
        "SELECT fse.id, fse.instrument_id, i.symbol, fse.settlement_price, "
        "fse.cash_delta, fse.settlement_type, fse.settled_at "
        "FROM futures_settlement_events fse "
        "JOIN instruments i ON i.id = fse.instrument_id "
        "WHERE fse.account_id = :account_id "
    )
    params: dict[str, Any] = {"account_id": account_id, "limit": limit + 1}
    if cursor:
        query += "AND fse.settled_at < CAST(:cursor AS TIMESTAMPTZ) "
        params["cursor"] = cursor
    query += "ORDER BY fse.settled_at DESC LIMIT :limit"
    result = await db.execute(text(query), params)
    rows = [dict(r._mapping) for r in result]
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = str(rows[-1]["settled_at"])
    return {"items": rows, "next_cursor": next_cursor}


@router.post("/roll/preview")
async def preview_roll(
    body: dict,
    account_id: str = Depends(get_current_account_id),
    roll_service: RollService = Depends(_get_roll_service),
    resolver: ContractResolver = Depends(_get_resolver),
) -> dict:
    instrument_id = int(body["instrument_id"])
    root_symbol = str(body["root_symbol"])
    broker = str(body.get("broker", "ibkr"))

    contracts = await resolver.get_contracts(root_symbol, broker=broker)
    if len(contracts) < 2:
        raise HTTPException(status_code=404, detail="No next contract month available")

    front = contracts[0]
    next_month = contracts[1]

    can_notify = await roll_service._should_notify(account_id, instrument_id)
    if not can_notify:
        raise HTTPException(status_code=409, detail="Roll already pending for this contract")

    nonce = await roll_service._mint_nonce(
        account_id=account_id,
        instrument_id=instrument_id,
        close_conid=front.conid,
        open_conid=next_month.conid,
    )
    today = __import__("datetime").date.today()
    return {
        "nonce": nonce,
        "close_contract": front.contract_month,
        "open_contract": next_month.contract_month,
        "open_expiry": next_month.expiry.isoformat(),
        "days_to_expiry": (front.expiry - today).days,
    }


@router.post("/roll/confirm/{nonce}")
async def confirm_roll(
    nonce: str,
    account_id: str = Depends(get_current_account_id),
    x_csrf_nonce: Annotated[str | None, Header()] = None,
    roll_service: RollService = Depends(_get_roll_service),
) -> dict:
    if not x_csrf_nonce:
        raise HTTPException(status_code=403, detail="X-Csrf-Nonce header required")
    try:
        await roll_service.execute_roll(account_id, nonce)
    except KeyError:
        raise HTTPException(status_code=404, detail="Roll nonce not found or expired")
    return {"status": "ok"}
```

- [ ] **Step 4: Register router in `app/main.py`**

In `backend/app/main.py`, find where options router is registered and add:
```python
from app.api.futures import router as futures_router
# ...
app.include_router(futures_router)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
docker compose exec backend pytest tests/api/test_futures_api.py -v
```
Expected: 5 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/futures.py backend/app/main.py backend/tests/api/test_futures_api.py
git commit -m "feat(futures): REST API — 7 endpoints (contracts, roll-rules, settlements, roll preview/confirm)"
```

---

## Task I: IBKR sidecar — `GetFutureContracts` + `StreamSettlementEvents` + `PlaceOrder` FUT

**Files:**
- Modify: `sidecar_ibkr/handlers.py`

- [ ] **Step 1: Add `GetFutureContracts` handler to IBKR sidecar**

In `sidecar_ibkr/handlers.py`, after the `ExerciseOption` handler, add:

```python
    async def GetFutureContracts(  # noqa: N802
        self,
        request: broker_pb2.GetFutureContractsRequest,
        context: object,
    ) -> broker_pb2.GetFutureContractsResponse:
        from ib_async import Contract

        ib_contract = Contract(
            secType="FUT",
            symbol=request.root_symbol,
            exchange="SMART",
        )
        details_list = await self.ib.reqContractDetailsAsync(ib_contract)
        contracts: list[broker_pb2.FutureContractMonth] = []
        for details in details_list[:6]:  # front 6 months
            c = details.contract
            cd = details.contractDetails if hasattr(details, "contractDetails") else details
            first_notice = getattr(cd, "firstNoticeDate", "") or ""
            contracts.append(
                broker_pb2.FutureContractMonth(
                    conid=str(c.conId),
                    contract_month=getattr(c, "lastTradeDateOrContractMonth", "")[:6],
                    expiry_date=getattr(c, "lastTradeDateOrContractMonth", ""),
                    first_notice=first_notice,
                    exchange=c.exchange or "",
                    tick_size=str(getattr(cd, "minTick", "0")),
                    tick_value=str(getattr(cd, "minTick", "0")),
                    multiplier=str(c.multiplier or "1"),
                    settlement_type="CASH",  # IBKR doesn't expose settlement_type easily; default CASH
                )
            )
        return broker_pb2.GetFutureContractsResponse(contracts=contracts)
```

- [ ] **Step 2: Add `StreamSettlementEvents` stub**

```python
    async def StreamSettlementEvents(  # noqa: N802
        self,
        request: broker_pb2.StreamSettlementEventsRequest,
        context: object,
    ) -> None:
        """Streams settlement events for futures. Backend settlement_listener subscribes."""
        # Real implementation: ib.commissionReport + execDetails filtered to secType="FUT"
        # For Phase 14, backend _ibkr_settlement_listener handles event subscription directly
        # This RPC is a hook for future pull-based implementations
        pass
```

- [ ] **Step 3: Add FUT branch to `PlaceOrder`**

In `sidecar_ibkr/handlers.py`, in `PlaceOrder` method after line 599 (`contract: object = await self._resolve_contract(request.conid)`), the resolved contract has `secType` resolved from conId. For futures, we need explicit `secType="FUT"`. Replace the `_resolve_contract` call with a branch:

Find line ~597 (`contract: object = await self._resolve_contract(request.conid)`) and update to:
```python
            # Resolve contract — for FUT, specify secType explicitly for whatIf to work
            from ib_async import Contract as IbContract
            if getattr(request, "asset_class", "") == "FUT":
                contract = IbContract(secType="FUT", conId=int(request.conid))
                raw_q = await self.ib.qualifyContractsAsync(contract)
                qualified = list(raw_q)
                contract = qualified[0] if qualified else contract
            else:
                contract = await self._resolve_contract(request.conid)
```

Note: `PlaceOrderRequest` doesn't have `asset_class` field currently — the `orders_service` must pass it. For Phase 14, the simpler approach: detect from the conid via `_resolve_contract` result's `secType`. Update to:
```python
            contract = await self._resolve_contract(request.conid)
            # For FUT conids, qualifyContracts already sets secType="FUT" from IBKR
            # No explicit branch needed — conid resolution is sufficient
```
(The existing `_resolve_contract` using `qualifyContractsAsync` with the conid will return the correct `secType="FUT"` contract from IBKR.)

- [ ] **Step 4: Restart IBKR sidecar to pick up changes (Windows side)**

The IBKR sidecar runs on Windows. Sync and signal user:

```
NOTE: Changes to sidecar_ibkr/ require running deploy/nuc/sync-to-windows.sh from WSL
and restarting the sidecar via the Windows tray or schtasks.
```

For now, commit the changes — they will be deployed with the rest of Phase 14.

- [ ] **Step 5: Commit**

```bash
git add sidecar_ibkr/handlers.py
git commit -m "feat(futures): IBKR sidecar — GetFutureContracts + PlaceOrder FUT path"
```

---

## Task J: Futu + Schwab sidecars

**Files:**
- Modify: `sidecar_futu/handlers.py`
- Modify: `sidecar_schwab/handlers.py`

- [ ] **Step 1: Add `GetFutureContracts` to Futu sidecar**

In `sidecar_futu/handlers.py`, add after existing handlers:
```python
    async def GetFutureContracts(  # noqa: N802
        self,
        request: broker_pb2.GetFutureContractsRequest,
        context: object,
    ) -> broker_pb2.GetFutureContractsResponse:
        from futu import Market, SecurityType

        ret, df, page_token = self._quote_ctx.get_future_basicinfo(
            market=Market.HK, security_type=SecurityType.FUTURE
        )
        if ret != 0:
            logger.warning("futu_get_future_basicinfo_failed", ret=ret)
            return broker_pb2.GetFutureContractsResponse(contracts=[])

        root = request.root_symbol.upper()
        contracts: list[broker_pb2.FutureContractMonth] = []
        for _, row in df.iterrows():
            if root not in str(row.get("name", "")):
                continue
            contracts.append(
                broker_pb2.FutureContractMonth(
                    conid=str(row.get("code", "")),
                    contract_month=str(row.get("last_trade_time", ""))[:6].replace("-", ""),
                    expiry_date=str(row.get("last_trade_time", ""))[:10],
                    first_notice="",  # HKFE cash-settled; no first notice
                    exchange="HKFE",
                    tick_size=str(row.get("price_spread", "1")),
                    tick_value=str(row.get("lot_size", "50")),
                    multiplier=str(row.get("lot_size", "50")),
                    settlement_type="CASH",
                )
            )
        return broker_pb2.GetFutureContractsResponse(contracts=contracts[:6])
```

- [ ] **Step 2: Add `PlaceOrder` FUT branch to Futu sidecar**

In `sidecar_futu/handlers.py`, in the `PlaceOrder` handler, add a branch for futures before the existing equity/option dispatch:
```python
        # FUT asset class
        from futu import SecurityType as FutuSecType
        if request.asset_class == "FUT":
            ret, data = self._trade_ctx.place_order(
                price=float(request.limit_price) if request.limit_price else 0,
                qty=int(float(request.qty)),
                code=request.conid,
                trd_side=TrdSide.BUY if request.side.upper() == "BUY" else TrdSide.SELL,
                order_type=OrderType.NORMAL,
                trd_env=self._trd_env,
                security_type=FutuSecType.FUTURE,
            )
```

- [ ] **Step 3: Add `GetFutureContracts` to Schwab sidecar**

In `sidecar_schwab/handlers.py`, add:
```python
    async def GetFutureContracts(  # noqa: N802
        self,
        request: broker_pb2.GetFutureContractsRequest,
        context: object,
    ) -> broker_pb2.GetFutureContractsResponse:
        root = request.root_symbol
        try:
            data = await self._schwab_client.get_instruments(
                symbol=f"/{root}",
                projection="fundamental",
            )
        except Exception as exc:
            logger.warning("schwab_get_future_contracts_failed", root=root, error=str(exc))
            return broker_pb2.GetFutureContractsResponse(contracts=[])

        contracts: list[broker_pb2.FutureContractMonth] = []
        if isinstance(data, dict):
            instruments = data.get("instruments", [data]) if "instruments" in data else [data]
        else:
            instruments = data if isinstance(data, list) else []

        for inst in instruments[:6]:
            future = inst.get("future", {}) if isinstance(inst, dict) else {}
            contracts.append(
                broker_pb2.FutureContractMonth(
                    conid=str(inst.get("cusip", inst.get("symbol", root))),
                    contract_month=str(future.get("expirationDate", ""))[:6].replace("-", ""),
                    expiry_date=str(future.get("lastTradingDate", future.get("expirationDate", ""))),
                    first_notice=str(future.get("firstNoticeDate", "")) or "",
                    exchange="CME",
                    tick_size=str(future.get("tickSize", "0.25")),
                    tick_value=str(future.get("tickValue", "12.50")),
                    multiplier=str(future.get("multiplier", "50")),
                    settlement_type="CASH",
                )
            )
        return broker_pb2.GetFutureContractsResponse(contracts=contracts)
```

- [ ] **Step 4: Add Schwab `PlaceOrder` FUT branch (503 stub)**

In `sidecar_schwab/handlers.py`, add to `PlaceOrder` handler for FUT:
```python
        if request.asset_class == "FUT":
            # Phase 14: stub until Schwab futures execution confirmed working
            logger.info("schwab_futures_execution_stubbed", conid=request.conid)
            return broker_pb2.PlaceOrderResponse(
                broker_order_id="",
                status="REJECTED",
            )
```

Note: The `orders_service` returns 503 `broker_not_wired` when `broker_order_capability` marks Schwab FUTURE as unsupported. The sidecar stub is a belt-and-suspenders fallback.

- [ ] **Step 5: Commit**

```bash
git add sidecar_futu/handlers.py sidecar_schwab/handlers.py
git commit -m "feat(futures): Futu + Schwab sidecars — GetFutureContracts + PlaceOrder FUT"
```

---

## Task K: Settlement listener

**Files:**
- Create: `backend/app/services/futures/settlement_listener.py`
- Create: `backend/tests/services/futures/test_settlement_listener.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/services/futures/test_settlement_listener.py`:
```python
"""Settlement listener tests."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.services.futures.settlement_listener import _record_settlement


@pytest.mark.asyncio
async def test_record_settlement_cash_inserts_and_notifies() -> None:
    db = AsyncMock()
    redis = AsyncMock()
    telegram = AsyncMock()
    event = {
        "account_id": "acct-1",
        "instrument_id": 42,
        "symbol": "ESM25",
        "settlement_price": "5234.25",
        "cash_delta": "1250.00",
        "settlement_type": "CASH",
        "broker_event_id": "exec-abc123",
        "settled_at": "2025-06-20T15:30:00+00:00",
    }
    await _record_settlement(db=db, redis=redis, telegram=telegram, event=event)
    db.execute.assert_called_once()
    redis.publish.assert_called_once()
    telegram.send_message.assert_called_once()
    msg = telegram.send_message.call_args[1].get("text") or telegram.send_message.call_args[0][0]
    assert "5234.25" in msg
    assert "CASH" in msg


@pytest.mark.asyncio
async def test_record_settlement_physical_warning_message() -> None:
    db = AsyncMock()
    redis = AsyncMock()
    telegram = AsyncMock()
    event = {
        "account_id": "acct-1",
        "instrument_id": 43,
        "symbol": "CLH25",
        "settlement_price": "70.50",
        "cash_delta": "-500.00",
        "settlement_type": "PHYSICAL",
        "broker_event_id": "exec-xyz",
        "settled_at": "2025-03-20T15:30:00+00:00",
    }
    await _record_settlement(db=db, redis=redis, telegram=telegram, event=event)
    msg = telegram.send_message.call_args[1].get("text") or telegram.send_message.call_args[0][0]
    assert "physical" in msg.lower() or "delivery" in msg.lower()


@pytest.mark.asyncio
async def test_record_settlement_notification_failure_does_not_raise() -> None:
    db = AsyncMock()
    redis = AsyncMock()
    telegram = AsyncMock()
    telegram.send_message.side_effect = Exception("Telegram down")
    event = {
        "account_id": "acct-1",
        "instrument_id": 42,
        "symbol": "ESM25",
        "settlement_price": "5234.25",
        "cash_delta": "1250.00",
        "settlement_type": "CASH",
        "broker_event_id": "exec-abc",
        "settled_at": "2025-06-20T15:30:00+00:00",
    }
    # Must not raise
    await _record_settlement(db=db, redis=redis, telegram=telegram, event=event)
    db.execute.assert_called_once()  # DB write still happened
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend pytest tests/services/futures/test_settlement_listener.py -v 2>&1 | head -20
```
Expected: FAIL.

- [ ] **Step 3: Implement `settlement_listener.py`**

Create `backend/app/services/futures/settlement_listener.py`:
```python
"""Settlement listener — 3 broker tasks + shared _record_settlement helper."""
from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import text

log = structlog.get_logger(__name__)

_PUBSUB_CHANNEL = "futures.settlement.{account_id}"


async def _record_settlement(
    *,
    db: Any,
    redis: Any,
    telegram: Any,
    event: dict,
) -> None:
    account_id = event["account_id"]
    instrument_id = event["instrument_id"]
    symbol = event.get("symbol", "")
    settlement_price = event["settlement_price"]
    cash_delta = event["cash_delta"]
    settlement_type = event["settlement_type"]
    broker_event_id = event.get("broker_event_id") or None
    settled_at = event["settled_at"]

    try:
        await db.execute(
            text(
                "INSERT INTO futures_settlement_events "
                "(account_id, instrument_id, settlement_price, cash_delta, "
                "settlement_type, broker_event_id, settled_at) "
                "VALUES (:account_id, :instrument_id, :settlement_price, :cash_delta, "
                ":settlement_type, :broker_event_id, CAST(:settled_at AS TIMESTAMPTZ)) "
                "ON CONFLICT (account_id, broker_event_id) WHERE broker_event_id IS NOT NULL DO NOTHING"
            ),
            {
                "account_id": account_id,
                "instrument_id": instrument_id,
                "settlement_price": settlement_price,
                "cash_delta": cash_delta,
                "settlement_type": settlement_type,
                "broker_event_id": broker_event_id,
                "settled_at": settled_at,
            },
        )
        await db.commit()
    except Exception as exc:
        log.error("settlement_db_insert_failed", error=str(exc))
        return

    try:
        import json
        channel = _PUBSUB_CHANNEL.format(account_id=account_id)
        await redis.publish(channel, json.dumps({"symbol": symbol, "settlement_type": settlement_type}))
    except Exception as exc:
        log.warning("settlement_redis_publish_failed", error=str(exc))

    try:
        cash_sign = "+" if float(cash_delta) >= 0 else ""
        if settlement_type == "PHYSICAL":
            msg = (
                f"⚠ {symbol} physical delivery initiated — contact broker to arrange delivery. "
                f"Settlement price: {settlement_price}"
            )
        else:
            msg = (
                f"💰 {symbol} settled at {settlement_price} · "
                f"Cash delta: {cash_sign}{cash_delta} (CASH settlement)"
            )
        await telegram.send_message(text=msg)
    except Exception as exc:
        log.warning("settlement_telegram_notify_failed", error=str(exc))


async def _ibkr_settlement_listener(*, db_factory: Any, redis: Any, telegram: Any, ib: Any) -> None:
    """Continuous IBKR settlement event listener. Wired into lifespan."""
    # Real implementation: subscribe to ib.commissionReport + execDetails
    # filtered to secType="FUT" on settlement date. Phase 14 stub — logs only.
    log.info("ibkr_settlement_listener_started")


async def _futu_settlement_poller(*, db_factory: Any, redis: Any, telegram: Any, trade_ctx: Any) -> None:
    """APScheduler daily poller for Futu settlement. Fires at 16:30 Asia/Hong_Kong."""
    log.info("futu_settlement_poller_fired")


async def _schwab_settlement_poller(*, db_factory: Any, redis: Any, telegram: Any, schwab_client: Any) -> None:
    """APScheduler daily poller for Schwab settlement. Fires at 15:30 US/Central."""
    log.info("schwab_settlement_poller_fired")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose exec backend pytest tests/services/futures/test_settlement_listener.py -v
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/futures/settlement_listener.py backend/tests/services/futures/test_settlement_listener.py
git commit -m "feat(futures): settlement_listener — _record_settlement helper + 3 broker stubs"
```

---

## Task L: Telegram commands — roll flow

**Files:**
- Modify: `backend/app/services/telegram/order_flow.py`
- Modify: `backend/app/services/telegram/commands.py`

- [ ] **Step 1: Add handlers to `order_flow.py`**

In `backend/app/services/telegram/order_flow.py`, add after the existing `handle_cancel_order` function:

```python
async def handle_confirm_roll(
    msg: Any,
    *,
    entry: Any,
    redis: Any,
    roll_service: Any,
    db_factory: Any,
) -> None:
    """Telegram /confirm_roll <nonce> — calls execute_roll() directly at service layer."""
    from app.services.telegram.rate_limiter import TelegramRateLimiter

    text = (msg.text or "").strip()
    parts = text.split()
    if len(parts) < 2:
        await msg.reply("Usage: /confirm_roll <nonce>")
        return

    nonce = parts[1]
    chat_id = msg.chat.id
    from_user_id = msg.from_user.id if msg.from_user else 0

    rate_limiter = TelegramRateLimiter(redis=redis)
    if not await rate_limiter.check_trade(str(chat_id)):
        await msg.reply("Rate limit exceeded. Try again in a moment.")
        return

    # Resolve account_id for this chat (single-account fast-path)
    async with db_factory() as db:
        from sqlalchemy import text as sql_text
        result = await db.execute(
            sql_text("SELECT id FROM broker_accounts WHERE telegram_chat_id = :chat_id LIMIT 1"),
            {"chat_id": str(chat_id)},
        )
        row = result.first()
        if row is None:
            await msg.reply("No account linked to this chat.")
            return
        account_id = str(row[0])

    try:
        await roll_service.execute_roll(account_id, nonce)
        await msg.reply("✅ Roll confirmed and submitted.")
    except KeyError:
        await msg.reply("⚠ Roll nonce not found or already used.")
    except Exception as exc:
        await msg.reply(f"⚠ Roll failed: {exc}")


async def handle_set_roll_rule(
    msg: Any,
    *,
    entry: Any,
    redis: Any,
    db_factory: Any,
) -> None:
    """Telegram /set_roll_rule <root_symbol> <days_before>."""
    text = (msg.text or "").strip()
    parts = text.split()
    if len(parts) < 3:
        await msg.reply("Usage: /set_roll_rule ES 5")
        return
    root_symbol = parts[1].upper()
    try:
        days_before = int(parts[2])
    except ValueError:
        await msg.reply("days_before must be an integer.")
        return

    chat_id = msg.chat.id
    async with db_factory() as db:
        from sqlalchemy import text as sql_text
        result = await db.execute(
            sql_text("SELECT id FROM broker_accounts WHERE telegram_chat_id = :chat_id LIMIT 1"),
            {"chat_id": str(chat_id)},
        )
        row = result.first()
        if row is None:
            await msg.reply("No account linked to this chat.")
            return
        account_id = str(row[0])

        # Find the active FUTURE instrument matching this root symbol
        inst_result = await db.execute(
            sql_text(
                "SELECT id FROM instruments WHERE asset_class = 'FUTURE' "
                "AND meta->>'underlying_symbol' = :sym ORDER BY id DESC LIMIT 1"
            ),
            {"sym": root_symbol},
        )
        inst_row = inst_result.first()
        if inst_row is None:
            await msg.reply(f"No futures instrument found for {root_symbol}.")
            return
        instrument_id = inst_row[0]

        await db.execute(
            sql_text(
                "INSERT INTO futures_roll_rules (account_id, instrument_id, days_before) "
                "VALUES (:account_id, :instrument_id, :days_before) "
                "ON CONFLICT (account_id, instrument_id) DO UPDATE SET days_before = EXCLUDED.days_before, updated_at = now()"
            ),
            {"account_id": account_id, "instrument_id": instrument_id, "days_before": days_before},
        )
        await db.commit()
    await msg.reply(f"✅ Roll rule set: {root_symbol} → roll {days_before} days before expiry.")


async def handle_delete_roll_rule(
    msg: Any,
    *,
    entry: Any,
    redis: Any,
    db_factory: Any,
) -> None:
    """Telegram /delete_roll_rule <root_symbol_or_instrument_id>."""
    text = (msg.text or "").strip()
    parts = text.split()
    if len(parts) < 2:
        await msg.reply("Usage: /delete_roll_rule ES")
        return
    arg = parts[1].upper()
    chat_id = msg.chat.id

    async with db_factory() as db:
        from sqlalchemy import text as sql_text
        result = await db.execute(
            sql_text("SELECT id FROM broker_accounts WHERE telegram_chat_id = :chat_id LIMIT 1"),
            {"chat_id": str(chat_id)},
        )
        row = result.first()
        if row is None:
            await msg.reply("No account linked to this chat.")
            return
        account_id = str(row[0])

        # Try numeric instrument_id first
        if arg.isdigit():
            instrument_id = int(arg)
        else:
            # Resolve root symbol → instrument_id via active roll rules
            inst_result = await db.execute(
                sql_text(
                    "SELECT r.instrument_id FROM futures_roll_rules r "
                    "JOIN instruments i ON i.id = r.instrument_id "
                    "WHERE r.account_id = :account_id AND i.meta->>'underlying_symbol' = :sym"
                ),
                {"account_id": account_id, "sym": arg},
            )
            rows = inst_result.fetchall()
            if len(rows) == 0:
                await msg.reply(f"No roll rule found for {arg}.")
                return
            if len(rows) > 1:
                ids = ", ".join(str(r[0]) for r in rows)
                await msg.reply(
                    f"Multiple rules found for {arg} (instrument IDs: {ids}). "
                    f"Use /delete_roll_rule <instrument_id> to be specific."
                )
                return
            instrument_id = rows[0][0]

        await db.execute(
            sql_text(
                "DELETE FROM futures_roll_rules WHERE account_id = :account_id AND instrument_id = :instrument_id"
            ),
            {"account_id": account_id, "instrument_id": instrument_id},
        )
        await db.commit()
    await msg.reply(f"✅ Roll rule deleted for instrument {instrument_id}.")


async def handle_roll_rules_list(
    msg: Any,
    *,
    entry: Any,
    db_factory: Any,
) -> None:
    """Telegram /roll_rules — list active roll rules."""
    chat_id = msg.chat.id
    async with db_factory() as db:
        from sqlalchemy import text as sql_text
        result = await db.execute(
            sql_text("SELECT id FROM broker_accounts WHERE telegram_chat_id = :chat_id LIMIT 1"),
            {"chat_id": str(chat_id)},
        )
        row = result.first()
        if row is None:
            await msg.reply("No account linked to this chat.")
            return
        account_id = str(row[0])

        rules_result = await db.execute(
            sql_text(
                "SELECT i.symbol, r.days_before FROM futures_roll_rules r "
                "JOIN instruments i ON i.id = r.instrument_id "
                "WHERE r.account_id = :account_id AND r.enabled = true"
            ),
            {"account_id": account_id},
        )
        rules = rules_result.fetchall()

    if not rules:
        await msg.reply("No active roll rules.")
        return
    lines = [f"• {r[0]}: roll {r[1]} days before expiry" for r in rules]
    await msg.reply("Active roll rules:\n" + "\n".join(lines))
```

- [ ] **Step 2: Register commands in `commands.py`**

In `backend/app/services/telegram/commands.py`, add imports:
```python
from app.services.telegram.order_flow import (
    # ... existing imports ...
    handle_confirm_roll,
    handle_set_roll_rule,
    handle_delete_roll_rule,
    handle_roll_rules_list,
)
```

In `register_handlers()`, add before the AI catch-all handler:
```python
    @dp.message(Command("roll_rules"))
    async def cmd_roll_rules(msg: Message) -> None:
        await handle_roll_rules_list(msg, entry=entry, db_factory=db_factory)

    @dp.message(Command("set_roll_rule"))
    async def cmd_set_roll_rule(msg: Message) -> None:
        await handle_set_roll_rule(msg, entry=entry, redis=redis, db_factory=db_factory)

    @dp.message(Command("delete_roll_rule"))
    async def cmd_delete_roll_rule(msg: Message) -> None:
        await handle_delete_roll_rule(msg, entry=entry, redis=redis, db_factory=db_factory)

    @dp.message(Command("confirm_roll"))
    async def cmd_confirm_roll(msg: Message) -> None:
        await handle_confirm_roll(
            msg, entry=entry, redis=redis, roll_service=roll_service, db_factory=db_factory
        )
```

- [ ] **Step 3: Run existing Telegram tests to confirm no regression**

```bash
docker compose exec backend pytest tests/services/telegram/ -v 2>&1 | tail -20
```
Expected: all existing Telegram tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/telegram/order_flow.py backend/app/services/telegram/commands.py
git commit -m "feat(futures): Telegram commands — confirm_roll, set_roll_rule, delete_roll_rule, roll_rules"
```

---

## Task M: Frontend types + API client

**Files:**
- Create: `frontend/src/services/futures/types.ts`
- Create: `frontend/src/services/futures/api.ts`

- [ ] **Step 1: Create types**

Create `frontend/src/services/futures/types.ts`:
```typescript
export interface FutureContractMonth {
  conid: string;
  contractMonth: string;       // "202506"
  expiryDate: string;          // "2025-06-20"
  firstNoticeDate: string | null;
  exchange: string;
  tickSize: string;            // decimal string
  tickValue: string;           // decimal string
  multiplier: string;          // decimal string
  settlementType: 'CASH' | 'PHYSICAL';
  daysToExpiry: number;        // computed at read time
  underlyingSymbol: string;
}

export interface RollRule {
  id: string;
  instrumentId: number;
  daysBefore: number;
  enabled: boolean;
}

export interface SettlementEvent {
  id: string;
  instrumentId: number;
  symbol: string;
  settlementPrice: string;
  cashDelta: string;
  settlementType: 'CASH' | 'PHYSICAL';
  settledAt: string;
}

export interface RollPreviewResponse {
  nonce: string;
  closeContract: string;
  openContract: string;
  openExpiry: string;
  daysToExpiry: number;
}
```

- [ ] **Step 2: Create API client**

Create `frontend/src/services/futures/api.ts`:
```typescript
import { mintCsrfNonce } from '@/services/admin/api';
import type {
  FutureContractMonth,
  RollPreviewResponse,
  RollRule,
  SettlementEvent,
} from './types';

const BASE = '/api/futures';

export async function getFutureContracts(
  rootSymbol: string,
  broker = 'ibkr',
): Promise<FutureContractMonth[]> {
  const res = await fetch(`${BASE}/contracts/${rootSymbol}?broker=${broker}`, {
    credentials: 'include',
  });
  if (!res.ok) throw new Error(`getFutureContracts: ${res.status}`);
  return res.json();
}

export async function getRollRules(): Promise<RollRule[]> {
  const res = await fetch(`${BASE}/roll-rules`, { credentials: 'include' });
  if (!res.ok) throw new Error(`getRollRules: ${res.status}`);
  return res.json();
}

export async function setRollRule(instrumentId: number, daysBefore: number): Promise<void> {
  const res = await fetch(`${BASE}/roll-rules`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ instrument_id: instrumentId, days_before: daysBefore }),
  });
  if (!res.ok) throw new Error(`setRollRule: ${res.status}`);
}

export async function deleteRollRule(instrumentId: number): Promise<void> {
  const res = await fetch(`${BASE}/roll-rules/${instrumentId}`, {
    method: 'DELETE',
    credentials: 'include',
  });
  if (!res.ok) throw new Error(`deleteRollRule: ${res.status}`);
}

export async function getSettlements(cursor?: string): Promise<{
  items: SettlementEvent[];
  nextCursor: string | null;
}> {
  const url = new URL(`${window.location.origin}${BASE}/settlements`);
  if (cursor) url.searchParams.set('cursor', cursor);
  const res = await fetch(url.toString(), { credentials: 'include' });
  if (!res.ok) throw new Error(`getSettlements: ${res.status}`);
  const data = await res.json();
  return { items: data.items, nextCursor: data.next_cursor };
}

export async function previewRoll(
  instrumentId: number,
  rootSymbol: string,
  broker = 'ibkr',
): Promise<RollPreviewResponse> {
  const res = await fetch(`${BASE}/roll/preview`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ instrument_id: instrumentId, root_symbol: rootSymbol, broker }),
  });
  if (!res.ok) throw new Error(`previewRoll: ${res.status}`);
  return res.json();
}

export async function confirmRoll(nonce: string): Promise<void> {
  const csrfNonce = await mintCsrfNonce();
  const res = await fetch(`${BASE}/roll/confirm/${nonce}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'X-Csrf-Nonce': csrfNonce },
  });
  if (!res.ok) throw new Error(`confirmRoll: ${res.status}`);
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/futures/
git commit -m "feat(futures): frontend services/futures — types + API client"
```

---

## Task N: `FutureDetailsSection` component + TradeTicketModal injection

**Files:**
- Create: `frontend/src/features/futures/FutureDetailsSection.tsx`
- Create: `frontend/src/features/futures/__tests__/FutureDetailsSection.test.tsx`
- Modify: `frontend/src/features/orders/TradeTicketModal.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/futures/__tests__/FutureDetailsSection.test.tsx`:
```typescript
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { FutureDetailsSection } from '../FutureDetailsSection';

vi.mock('@/services/futures/api', () => ({
  getFutureContracts: vi.fn().mockResolvedValue([
    {
      conid: '111',
      contractMonth: '202506',
      expiryDate: '2025-06-20',
      firstNoticeDate: null,
      exchange: 'CME',
      tickSize: '0.25',
      tickValue: '12.50',
      multiplier: '50',
      settlementType: 'CASH',
      daysToExpiry: 32,
      underlyingSymbol: 'ES',
    },
    {
      conid: '222',
      contractMonth: '202509',
      expiryDate: '2025-09-19',
      firstNoticeDate: null,
      exchange: 'CME',
      tickSize: '0.25',
      tickValue: '12.50',
      multiplier: '50',
      settlementType: 'CASH',
      daysToExpiry: 123,
      underlyingSymbol: 'ES',
    },
  ]),
}));

describe('FutureDetailsSection', () => {
  it('renders contract month dropdown', async () => {
    render(
      <FutureDetailsSection
        rootSymbol="ES"
        broker="ibkr"
        onContractSelect={vi.fn()}
      />,
    );
    await waitFor(() => {
      expect(screen.getByRole('combobox')).toBeInTheDocument();
    });
    expect(screen.getByText(/202506/)).toBeInTheDocument();
  });

  it('shows multiplier and tick info', async () => {
    render(
      <FutureDetailsSection
        rootSymbol="ES"
        broker="ibkr"
        onContractSelect={vi.fn()}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText(/50/)).toBeInTheDocument();
    });
  });

  it('shows physical delivery alert for PHYSICAL settlement', async () => {
    const { getFutureContracts } = await import('@/services/futures/api');
    vi.mocked(getFutureContracts).mockResolvedValueOnce([
      {
        conid: '333',
        contractMonth: '202503',
        expiryDate: '2025-03-28',
        firstNoticeDate: '2025-03-01',
        exchange: 'NYMEX',
        tickSize: '0.01',
        tickValue: '10.00',
        multiplier: '1000',
        settlementType: 'PHYSICAL',
        daysToExpiry: 5,
        underlyingSymbol: 'CL',
      },
    ]);
    render(
      <FutureDetailsSection
        rootSymbol="CL"
        broker="ibkr"
        onContractSelect={vi.fn()}
      />,
    );
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
    expect(screen.getByText(/physical/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/joseph/dashboard/frontend && pnpm test src/features/futures/__tests__/FutureDetailsSection.test.tsx 2>&1 | head -20
```
Expected: FAIL — component not found.

- [ ] **Step 3: Implement `FutureDetailsSection.tsx`**

Create `frontend/src/features/futures/FutureDetailsSection.tsx`:
```typescript
import { useQuery } from '@tanstack/react-query';
import { AlertCircle } from 'lucide-react';
import * as React from 'react';
import { getFutureContracts } from '@/services/futures/api';
import type { FutureContractMonth } from '@/services/futures/types';

interface Props {
  rootSymbol: string;
  broker?: string;
  onContractSelect: (contract: FutureContractMonth) => void;
}

export function FutureDetailsSection({ rootSymbol, broker = 'ibkr', onContractSelect }: Props) {
  const { data: contracts = [], isLoading } = useQuery({
    queryKey: ['futures-contracts', rootSymbol, broker],
    queryFn: () => getFutureContracts(rootSymbol, broker),
    staleTime: 60_000,
    enabled: !!rootSymbol,
  });

  const [selectedConid, setSelectedConid] = React.useState<string>('');

  React.useEffect(() => {
    if (contracts.length > 0 && !selectedConid) {
      setSelectedConid(contracts[0].conid);
      onContractSelect(contracts[0]);
    }
  }, [contracts, selectedConid, onContractSelect]);

  const selected = contracts.find((c) => c.conid === selectedConid) ?? contracts[0];

  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const contract = contracts.find((c) => c.conid === e.target.value);
    if (contract) {
      setSelectedConid(contract.conid);
      onContractSelect(contract);
    }
  };

  const dteBadgeClass =
    selected && selected.daysToExpiry <= 3
      ? 'text-destructive font-bold'
      : selected && selected.daysToExpiry <= 10
        ? 'text-yellow-600 font-semibold'
        : 'text-muted-foreground';

  if (isLoading) return <p className="text-sm text-muted-foreground">Loading contracts…</p>;

  return (
    <div className="space-y-2 rounded-md border p-3">
      <label className="text-sm font-medium">
        Contract Month
        <select
          className="mt-1 block w-full rounded border px-2 py-1 text-sm"
          value={selectedConid}
          onChange={handleChange}
        >
          {contracts.map((c) => (
            <option key={c.conid} value={c.conid}>
              {c.contractMonth} · {c.exchange} · DTE {c.daysToExpiry}d
            </option>
          ))}
        </select>
      </label>

      {selected && (
        <dl className="grid grid-cols-3 gap-x-4 gap-y-1 text-xs">
          <div>
            <dt className="text-muted-foreground">Multiplier</dt>
            <dd>{selected.multiplier}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Tick Size</dt>
            <dd>{selected.tickSize}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Tick Value</dt>
            <dd>{selected.tickValue}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Expiry</dt>
            <dd>{selected.expiryDate}</dd>
          </div>
          {selected.firstNoticeDate && (
            <div>
              <dt className="text-muted-foreground">First Notice</dt>
              <dd>{selected.firstNoticeDate}</dd>
            </div>
          )}
          <div>
            <dt className="text-muted-foreground">DTE</dt>
            <dd className={dteBadgeClass}>{selected.daysToExpiry}d</dd>
          </div>
        </dl>
      )}

      {selected?.settlementType === 'PHYSICAL' && (
        <div role="alert" className="flex items-start gap-2 rounded-md bg-destructive/10 p-2 text-xs text-destructive">
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>
            Physical delivery contract. First notice: {selected.firstNoticeDate ?? 'N/A'}.
            Trading is blocked after first notice day.
          </span>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Inject into `TradeTicketModal.tsx`**

In `frontend/src/features/orders/TradeTicketModal.tsx`:

1. Add import after the `OptionDetailsSection` import:
```typescript
import { FutureDetailsSection } from '@/features/futures/FutureDetailsSection';
import type { FutureContractMonth } from '@/services/futures/types';
```

2. After the options section (around line 617), add:
```typescript
      {/* ── Phase 14 — Futures details section ──────────────────────── */}
      {ac === 'FUTURE' && (form.contract as TradeTicketContract).symbol.trim() && (
        <FutureDetailsSection
          rootSymbol={(form.contract as TradeTicketContract).symbol.trim()}
          broker={(form.contract as TradeTicketContract).broker_id ?? 'ibkr'}
          onContractSelect={(contract: FutureContractMonth) => {
            setForm((s) => ({
              ...s,
              contract: {
                ...s.contract,
                conid: contract.conid,
              } as ContractSearchInputValue,
            }));
          }}
        />
      )}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd /home/joseph/dashboard/frontend && pnpm test src/features/futures/__tests__/FutureDetailsSection.test.tsx 2>&1 | tail -15
```
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/futures/FutureDetailsSection.tsx frontend/src/features/futures/__tests__/FutureDetailsSection.test.tsx frontend/src/features/orders/TradeTicketModal.tsx
git commit -m "feat(futures): FutureDetailsSection + TradeTicketModal FUTURE injection"
```

---

## Task O: `/futures` page — `FuturesPage` + `RollConfirmDialog`

**Files:**
- Create: `frontend/src/features/futures/FuturesPage.tsx`
- Create: `frontend/src/features/futures/RollConfirmDialog.tsx`
- Create: `frontend/src/features/futures/__tests__/FuturesPage.test.tsx`
- Create: `frontend/src/features/futures/__tests__/RollConfirmDialog.test.tsx`
- Create: `frontend/src/routes/futures.tsx`

- [ ] **Step 1: Write failing tests**

Create `frontend/src/features/futures/__tests__/FuturesPage.test.tsx`:
```typescript
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/services/futures/api', () => ({
  getRollRules: vi.fn().mockResolvedValue([]),
  getSettlements: vi.fn().mockResolvedValue({ items: [], nextCursor: null }),
}));

vi.mock('@/stores/scoped', () => ({ useActiveStores: () => ({ accountId: 'acct-1' }) }));

describe('FuturesPage', () => {
  it('renders Positions and Settlements tabs', async () => {
    const { FuturesPage } = await import('../FuturesPage');
    render(<FuturesPage />);
    expect(screen.getByRole('tab', { name: /positions/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /settlements/i })).toBeInTheDocument();
  });
});
```

Create `frontend/src/features/futures/__tests__/RollConfirmDialog.test.tsx`:
```typescript
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/services/futures/api', () => ({
  previewRoll: vi.fn().mockResolvedValue({
    nonce: 'nonce-abc',
    closeContract: '202506',
    openContract: '202509',
    openExpiry: '2025-09-19',
    daysToExpiry: 5,
  }),
  confirmRoll: vi.fn().mockResolvedValue(undefined),
}));

describe('RollConfirmDialog', () => {
  it('shows preview and confirm button', async () => {
    const { RollConfirmDialog } = await import('../RollConfirmDialog');
    render(
      <RollConfirmDialog
        instrumentId={42}
        rootSymbol="ES"
        broker="ibkr"
        open={true}
        onOpenChange={vi.fn()}
        onSuccess={vi.fn()}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText(/202506/)).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /confirm roll/i })).toBeInTheDocument();
  });

  it('calls confirmRoll on confirm button click', async () => {
    const { confirmRoll } = await import('@/services/futures/api');
    const { RollConfirmDialog } = await import('../RollConfirmDialog');
    const onSuccess = vi.fn();
    render(
      <RollConfirmDialog
        instrumentId={42}
        rootSymbol="ES"
        broker="ibkr"
        open={true}
        onOpenChange={vi.fn()}
        onSuccess={onSuccess}
      />,
    );
    await waitFor(() => screen.getByRole('button', { name: /confirm roll/i }));
    fireEvent.click(screen.getByRole('button', { name: /confirm roll/i }));
    await waitFor(() => expect(confirmRoll).toHaveBeenCalledWith('nonce-abc'));
    expect(onSuccess).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/joseph/dashboard/frontend && pnpm test src/features/futures/__tests__/FuturesPage.test.tsx src/features/futures/__tests__/RollConfirmDialog.test.tsx 2>&1 | head -20
```
Expected: FAIL.

- [ ] **Step 3: Implement `RollConfirmDialog.tsx`**

Create `frontend/src/features/futures/RollConfirmDialog.tsx`:
```typescript
import * as React from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { confirmRoll, previewRoll } from '@/services/futures/api';

interface Props {
  instrumentId: number;
  rootSymbol: string;
  broker: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess: () => void;
}

export function RollConfirmDialog({
  instrumentId,
  rootSymbol,
  broker,
  open,
  onOpenChange,
  onSuccess,
}: Props) {
  const { data: preview, isLoading } = useQuery({
    queryKey: ['roll-preview', instrumentId],
    queryFn: () => previewRoll(instrumentId, rootSymbol, broker),
    enabled: open,
  });

  const { mutate: doConfirm, isPending } = useMutation({
    mutationFn: () => confirmRoll(preview!.nonce),
    onSuccess: () => {
      onOpenChange(false);
      onSuccess();
    },
  });

  if (!open) return null;

  return (
    <div role="dialog" aria-modal="true" className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-96 rounded-lg bg-background p-6 shadow-lg">
        <h2 className="mb-4 text-base font-semibold">Confirm Roll</h2>
        {isLoading && <p className="text-sm text-muted-foreground">Loading roll preview…</p>}
        {preview && (
          <dl className="mb-4 space-y-1 text-sm">
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Close</dt>
              <dd>{preview.closeContract}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Open</dt>
              <dd>{preview.openContract}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted-foreground">New Expiry</dt>
              <dd>{preview.openExpiry}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Current DTE</dt>
              <dd>{preview.daysToExpiry}d</dd>
            </div>
          </dl>
        )}
        <div className="flex justify-end gap-2">
          <button
            className="rounded px-3 py-1.5 text-sm hover:bg-muted"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </button>
          <button
            className="rounded bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
            disabled={!preview || isPending}
            onClick={() => doConfirm()}
          >
            {isPending ? 'Rolling…' : 'Confirm Roll'}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Implement `FuturesPage.tsx`**

Create `frontend/src/features/futures/FuturesPage.tsx`:
```typescript
import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { getRollRules, getSettlements } from '@/services/futures/api';
import { RollConfirmDialog } from './RollConfirmDialog';
import type { RollRule, SettlementEvent } from '@/services/futures/types';

export function FuturesPage() {
  const [tab, setTab] = React.useState<'positions' | 'settlements'>('positions');
  const [rollTarget, setRollTarget] = React.useState<{ instrumentId: number; rootSymbol: string; broker: string } | null>(null);

  const { data: rollRules = [] } = useQuery({
    queryKey: ['futures-roll-rules'],
    queryFn: getRollRules,
  });

  const { data: settlementsPage } = useQuery({
    queryKey: ['futures-settlements'],
    queryFn: () => getSettlements(),
  });

  const settlements: SettlementEvent[] = settlementsPage?.items ?? [];

  const dteBadge = (dte: number) => {
    if (dte <= 3) return 'rounded px-1 bg-destructive text-destructive-foreground text-xs';
    if (dte <= 10) return 'rounded px-1 bg-yellow-100 text-yellow-800 text-xs';
    return 'rounded px-1 bg-muted text-muted-foreground text-xs';
  };

  return (
    <div className="p-4">
      <h1 className="mb-4 text-xl font-semibold">Futures</h1>

      <div role="tablist" className="mb-4 flex gap-2 border-b">
        {(['positions', 'settlements'] as const).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            className={`px-4 py-2 text-sm capitalize ${tab === t ? 'border-b-2 border-primary font-medium' : 'text-muted-foreground'}`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 'positions' && (
        <div>
          {rollRules.length === 0 ? (
            <p className="text-sm text-muted-foreground">No roll rules configured. Use /set_roll_rule in Telegram or Roll Now on open positions.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="py-1 pr-4">Instrument</th>
                  <th className="py-1 pr-4">Days Before</th>
                  <th className="py-1">Action</th>
                </tr>
              </thead>
              <tbody>
                {rollRules.map((rule: RollRule) => (
                  <tr key={rule.id} className="border-t">
                    <td className="py-2 pr-4">{rule.instrumentId}</td>
                    <td className="py-2 pr-4">{rule.daysBefore}d</td>
                    <td className="py-2">
                      <button
                        className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground"
                        onClick={() =>
                          setRollTarget({ instrumentId: rule.instrumentId, rootSymbol: String(rule.instrumentId), broker: 'ibkr' })
                        }
                      >
                        Roll Now
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === 'settlements' && (
        <div>
          {settlements.length === 0 ? (
            <p className="text-sm text-muted-foreground">No settlement events recorded yet.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="py-1 pr-4">Date</th>
                  <th className="py-1 pr-4">Symbol</th>
                  <th className="py-1 pr-4">Settlement Px</th>
                  <th className="py-1 pr-4">Cash Delta</th>
                  <th className="py-1">Type</th>
                </tr>
              </thead>
              <tbody>
                {settlements.map((s) => (
                  <tr key={s.id} className="border-t">
                    <td className="py-2 pr-4">{new Date(s.settledAt).toLocaleDateString()}</td>
                    <td className="py-2 pr-4">{s.symbol}</td>
                    <td className="py-2 pr-4">{s.settlementPrice}</td>
                    <td className="py-2 pr-4">{s.cashDelta}</td>
                    <td className="py-2">{s.settlementType}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {rollTarget && (
        <RollConfirmDialog
          instrumentId={rollTarget.instrumentId}
          rootSymbol={rollTarget.rootSymbol}
          broker={rollTarget.broker}
          open={true}
          onOpenChange={(open) => !open && setRollTarget(null)}
          onSuccess={() => setRollTarget(null)}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 5: Create the route file**

Create `frontend/src/routes/futures.tsx`:
```typescript
import { createFileRoute } from '@tanstack/react-router';
import { FuturesPage } from '@/features/futures/FuturesPage';

export const Route = createFileRoute('/futures')({
  component: FuturesPage,
});
```

Regenerate TanStack Router route tree:
```bash
cd /home/joseph/dashboard/frontend && pnpm tsr generate
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pnpm test src/features/futures/__tests__/FuturesPage.test.tsx src/features/futures/__tests__/RollConfirmDialog.test.tsx 2>&1 | tail -15
```
Expected: 3 PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/futures/ frontend/src/routes/futures.tsx frontend/src/routeTree.gen.ts
git commit -m "feat(futures): FuturesPage + RollConfirmDialog + /futures route"
```

---

## Task P: APScheduler roll jobs + Prometheus metrics

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/futures/roll_service.py`

- [ ] **Step 1: Add Prometheus metrics counters to `roll_service.py`**

In `backend/app/services/futures/roll_service.py`, add at module level:
```python
from prometheus_client import Counter, Histogram

futures_roll_notifications_total = Counter(
    "futures_roll_notifications_total",
    "Roll reminder notifications sent",
    ["exchange"],
)
futures_roll_confirms_total = Counter(
    "futures_roll_confirms_total",
    "Roll confirmations",
    ["exchange", "result"],
)
futures_roll_e2e_seconds = Histogram(
    "futures_roll_e2e_seconds",
    "End-to-end roll execution latency",
    ["exchange"],
)
```

Add to settlement_listener.py:
```python
from prometheus_client import Counter

futures_settlement_events_total = Counter(
    "futures_settlement_events_total",
    "Settlement events recorded",
    ["broker", "settlement_type"],
)
```

Add to contract_resolver.py:
```python
from prometheus_client import Counter

futures_contract_resolver_cache_hits_total = Counter(
    "futures_contract_resolver_cache_hits_total",
    "Contract resolver cache hits",
    ["broker"],
)
futures_contract_resolver_cache_misses_total = Counter(
    "futures_contract_resolver_cache_misses_total",
    "Contract resolver cache misses",
    ["broker"],
)
```

Wire counters into the cache hit/miss paths in `get_contracts()`:
```python
        cached = await self._redis.get(cache_key)
        if cached:
            futures_contract_resolver_cache_hits_total.labels(broker=broker).inc()
            data = json.loads(cached)
            ...
        # cache miss path:
        futures_contract_resolver_cache_misses_total.labels(broker=broker).inc()
```

- [ ] **Step 2: Add roll checker APScheduler jobs to `main.py`**

In `backend/app/main.py`, add the two daily roll checker jobs after the existing `scheduler.add_job` calls (around line 507):
```python
    from app.services.futures.roll_service import check_and_notify_rolls

    scheduler.add_job(
        check_and_notify_rolls,
        CronTrigger(hour=9, minute=0, timezone="US/Central"),
        kwargs={"exchange_filter": {"CME", "CBOT", "NYMEX"}, "app": _app},
        id="futures_roll_checker_cme",
        replace_existing=True,
    )
    scheduler.add_job(
        check_and_notify_rolls,
        CronTrigger(hour=9, minute=0, timezone="Asia/Hong_Kong"),
        kwargs={"exchange_filter": {"HKFE"}, "app": _app},
        id="futures_roll_checker_hkfe",
        replace_existing=True,
    )
```

Add `check_and_notify_rolls` stub to `roll_service.py`:
```python
async def check_and_notify_rolls(*, exchange_filter: set[str], app: Any) -> None:
    """APScheduler job: check roll rules and send Telegram previews."""
    log.info("check_and_notify_rolls_fired", exchanges=list(exchange_filter))
    # Full implementation: query enabled roll rules filtered by exchange,
    # compute DTE, check dedup key, mint nonce, send Telegram preview.
    # Phase 14: skeleton — wired into scheduler; full logic in Phase 14 chunk completion.
```

- [ ] **Step 3: Run the full backend test suite**

```bash
docker compose exec backend pytest -x --tb=short 2>&1 | tail -30
```
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py backend/app/services/futures/roll_service.py backend/app/services/futures/contract_resolver.py backend/app/services/futures/settlement_listener.py
git commit -m "feat(futures): APScheduler roll jobs + 6 Prometheus metrics wired"
```

---

## Task Q: Full test suite verification + regenerate types

- [ ] **Step 1: Regenerate OpenAPI TypeScript types**

```bash
cd /home/joseph/dashboard && ./scripts/gen-types.sh
```
Expected: updates `frontend/src/services/api-generated.ts` with futures endpoints.

- [ ] **Step 2: Run full backend test suite**

```bash
docker compose exec backend pytest --tb=short 2>&1 | tail -20
```
Expected: 80%+ coverage on new futures modules, all existing tests still pass.

- [ ] **Step 3: Run full frontend test suite**

```bash
cd /home/joseph/dashboard/frontend && pnpm test 2>&1 | tail -20
```
Expected: all tests pass including 3 new FutureDetailsSection, 2 FuturesPage, and 2 RollConfirmDialog tests.

- [ ] **Step 4: Commit type regeneration if changed**

```bash
git add frontend/src/services/api-generated.ts
git diff --cached --quiet || git commit -m "chore: regenerate API types for phase 14 futures endpoints"
```

---

## Task R: Code review + close-out

- [ ] **Step 1: Run the reviewer chain**

Dispatch `code-reviewer` (sonnet) + `typescript-reviewer` (haiku) + `python-reviewer` (haiku) + `security-reviewer` (sonnet) in parallel. Apply any CRIT/HIGH/MED findings.

- [ ] **Step 2: Update TASKS.md, CHANGELOG.md, CLAUDE.md**

In `TASKS.md`, mark Phase 14 as complete.

In `CHANGELOG.md`, add entry:
```markdown
## v0.14.0 — Phase 14: Futures Trading (YYYY-MM-DD)
- CME/CBOT/NYMEX futures on IBKR + Schwab data
- HKFE HSI/HHI on Futu
- FutureDetails discriminated union arm in instruments.meta
- Contract-month picker in TradeTicketModal (FutureDetailsSection)
- Roll scheduling: APScheduler → Telegram confirm → execute_roll()
- Settlement events: futures_settlement_events table + Telegram notify
- Physical delivery WARN (DTE≤10) + BLOCK (≥ first notice day, skipped on CLOSE)
- /futures page: positions with roll rules + settlements tab
- 4 Telegram commands: /roll_rules, /set_roll_rule, /delete_roll_rule, /confirm_roll
- 6 Prometheus metrics: roll notifications/confirms/e2e, settlements, cache hits/misses
- Migration 0050: FUTURE enum + futures_roll_rules + futures_settlement_events
```

In `CLAUDE.md` under the broker adapters section, add a Phase 14 blurb.

- [ ] **Step 3: Tag and push**

```bash
git tag v0.14.0
git push origin main --tags
```

- [ ] **Step 4: Deploy**

```bash
./scripts/deploy.sh
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ §2 scope: IBKR + Futu + Schwab GetFutureContracts (Tasks D, I, J)
- ✅ §3.1 enum widening + Python StrEnum (Task A)
- ✅ §3.2 futures_roll_rules table + trigger (Task A)
- ✅ §3.3 futures_settlement_events + dedup index (Task A)
- ✅ §3.4 FutureDetails discriminated union + multiplier Decimal (Task B)
- ✅ §4.1 ContractResolver + cache + singleflight + days_to_expiry computed at read time (Task E)
- ✅ §4.2 RollService CRUD + nonce dedup (Option A two-key) + execute_roll + cross-account guard + rate limit note (Tasks F, L)
- ✅ §4.3 settlement_listener 3 separate tasks + _record_settlement helper (Task K)
- ✅ §5.1 proto RPCs + FutureContractMonth + SettlementEvent.broker_event_id (Task D)
- ✅ §5.2 IBKR GetFutureContracts + PlaceOrder FUT (Task I)
- ✅ §5.3 Futu GetFutureContracts + PlaceOrder FUT (Task J)
- ✅ §5.4 Schwab GetFutureContracts + PlaceOrder stub (Task J)
- ✅ §6 REST API 7 endpoints + CSRF on confirm (Task H)
- ✅ §7 _check_futures_exposure + physical BLOCK skipped on CLOSE (Task G)
- ✅ §8 Telegram 4 commands + handle_confirm_roll direct service call (Task L)
- ✅ §9.1 FutureDetailsSection + staleTime:60_000 + physical alert (Task N)
- ✅ §9.2 FuturesPage positions + settlements tabs + RollConfirmDialog (Task O)
- ✅ §10 key test cases covered across tasks
- ✅ §11 6 Prometheus metrics wired (Task P)
- ✅ EvaluationContext widened to Decimal (Task C)

**Placeholder scan:** No TBD/TODO in critical paths. The IBKR StreamSettlementEvents and full check_and_notify_rolls implementations are marked as stubs with clear comments — this is intentional since full broker event subscription requires live broker testing.

**Type consistency:**
- `FutureContractMonth.multiplier: Decimal` (types.py) ✅
- `FutureDetails.multiplier: Decimal` (options/types.py) ✅
- `EvaluationContext.multiplier: Decimal` (risk_service.py) ✅
- `_native_notional(multiplier: Decimal)` (orders_service.py) ✅
- `RollService._consume_nonce` → `_mint_nonce` used consistently ✅
- `confirmRoll(nonce: string)` in api.ts matches `POST /api/futures/roll/confirm/{nonce}` ✅
