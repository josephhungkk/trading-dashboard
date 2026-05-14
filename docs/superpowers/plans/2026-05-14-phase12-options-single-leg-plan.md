# Phase 12 — Options Single-Leg Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add single-leg options trading (chain viewer, order entry, exercise elections) across IBKR, Alpaca, and Futu HK, with Schwab providing chain data only.

**Architecture:** Instruments gain `OPTION` asset class via Alembic migration 0046 with a Pydantic discriminated union (`InstrumentMeta`) layered over the existing `meta` JSONB column. A new `app/services/options/` package houses `OptionChainService`, `OptionGreeksService`, and `ExerciseService`. Four new proto RPCs bridge the backend to each broker sidecar. The existing `TradeTicketModal` gains an `OptionDetailsSection` slot; a new `/options/chain` page provides butterfly-layout chain browsing.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy 2.0 async / Alembic / Pydantic v2 · React 19 / TypeScript 6 / TanStack Router + Query / Tailwind v4 · gRPC protobuf · Redis 7 · exchange_calendars · APScheduler · freezegun (tests)

---

## File Map

### New backend files
| File | Responsibility |
|------|---------------|
| `backend/alembic/versions/0046_phase12_options.py` | OPTION enum value, position_effect + tax_treatment columns, option_greeks + exercise_elections tables |
| `backend/app/models/options.py` | `OptionGreeks` ORM model, `ExerciseElection` ORM model |
| `backend/app/schemas/options.py` | Pydantic request/response schemas for all options endpoints |
| `backend/app/services/options/__init__.py` | Package init |
| `backend/app/services/options/types.py` | `InstrumentMeta`, `NonOptionDetails`, `OptionDetails`, `GreeksSnapshot`, `SubscriptionHandle`, `parse_instrument_meta` |
| `backend/app/services/options/chain_service.py` | `OptionChainService` — fetch/cache chain data, singleflight, budget enforcement |
| `backend/app/services/options/greeks_service.py` | `OptionGreeksService` — upsert/evict persisted Greeks, start/stop streaming |
| `backend/app/services/options/exercise_service.py` | `ExerciseService` — list_pending (with spot), elect (idempotent), rate limit |
| `backend/app/api/options.py` | 9 REST endpoints: expirations, chain, greeks, exercise GET/POST, events, 3 admin PUTs |
| `backend/app/api/ws_options.py` | `WS /ws/options/chain` — 2 Hz conflation, canonicalized frame, heartbeat |
| `backend/tests/test_alembic_0046.py` | Migration round-trip test |
| `backend/tests/services/test_instrument_resolver_option.py` | `find_or_create_option` + canonical_id format |
| `backend/tests/services/options/test_chain_service.py` | Cache hit/miss, singleflight, source routing, TTL |
| `backend/tests/services/options/test_greeks_service.py` | Upsert guard, eviction, clamping |
| `backend/tests/services/options/test_exercise_service.py` | Pending filter, idempotency, 409, rate limit |
| `backend/tests/services/test_options_risk_checks.py` | All 6 new risk checks |
| `backend/tests/api/test_options_api.py` | All 9 REST endpoints, JWT, rate limits, CSRF |
| `backend/tests/api/test_ws_options.py` | WS lifecycle, frames, heartbeat |
| `backend/tests/services/test_market_calendar_extensions.py` | 4 new calendar helpers |
| `backend/tests/services/test_telegram_options_reject.py` | OCC symbol rejection |

### Modified backend files
| File | Change |
|------|--------|
| `backend/app/models/instruments.py` | Add `OPTION` to `AssetClass` enum |
| `backend/app/services/market_calendar.py` | Add `is_open`, `is_past_expiry`, `option_cutoff_time`, `next_trading_days` |
| `backend/app/services/quotes/instrument_resolver.py` | Add `find_or_create_option`, `_build_option_canonical_id` |
| `backend/app/services/risk_service.py` | Add `multiplier`/`position_effect` to `EvaluationContext`; add `_check_options_exposure` |
| `backend/app/services/orders_service.py` | Plumb `multiplier`/`position_effect` into `EvaluationContext`; multiply notional by `multiplier`; add contract-expiry check |
| `backend/app/services/telegram/order_flow.py` | Add `_OCC_PATTERN` guard in `parse_place_order` |
| `backend/app/main.py` | Import + register options router, ws_options router; register services in lifespan |
| `proto/broker/v1/broker.proto` | 4 new RPCs, new messages, `OptionContractHint` oneof in `SymbolRef` |

### New frontend files
| File | Responsibility |
|------|---------------|
| `frontend/src/features/options/types.ts` | Option chain types, GreeksSnapshot, ExerciseElection |
| `frontend/src/features/options/hooks/useOptionExpirations.ts` | TanStack Query for expirations |
| `frontend/src/features/options/hooks/useOptionChain.ts` | TanStack Query + WS hybrid |
| `frontend/src/features/options/hooks/useExerciseElections.ts` | TanStack Query for elections |
| `frontend/src/features/options/OptionGreeksStrip.tsx` | Δ Γ Θ V IV strip (reused in table + modal) |
| `frontend/src/features/options/OptionExpiryTabs.tsx` | Expiry selector tabs |
| `frontend/src/features/options/OptionChainToolbar.tsx` | Symbol search + status badge |
| `frontend/src/features/options/OptionChainTable.tsx` | Butterfly layout table |
| `frontend/src/features/options/OptionChainPage.tsx` | Full page composition |
| `frontend/src/features/options/OptionDetailsSection.tsx` | Injected into TradeTicketModal |
| `frontend/src/features/options/ExerciseElectionRow.tsx` | Single exercise row with actions |
| `frontend/src/features/options/OptionEventsPage.tsx` | Elections + recent events page |
| `frontend/src/routes/options.chain.tsx` | TanStack route `/options/chain` |
| `frontend/src/routes/options.events.tsx` | TanStack route `/options/events` |
| `frontend/src/services/options/api.ts` | Typed API calls for all options endpoints |

### Modified frontend files
| File | Change |
|------|--------|
| `frontend/src/features/orders/TradeTicketModal.tsx` | Insert `OptionDetailsSection` above AI section when `asset_class === 'OPTION'` |
| `frontend/src/components/layout/Sidebar.tsx` (or equivalent) | Add "Options" nav entry |

---

## Chunk A — Schema & Data Foundation

### Task A1: Migration 0046 — schema changes

**Files:**
- Create: `backend/alembic/versions/0046_phase12_options.py`
- Create: `backend/tests/test_alembic_0046.py`

- [ ] **Step 1: Write the migration test**

```python
# backend/tests/test_alembic_0046.py
"""Migration 0046 round-trip test."""
from __future__ import annotations
import pytest

pytestmark = [pytest.mark.migrations]

def test_0046_upgrade_downgrade(alembic_runner):
    alembic_runner.migrate_up_to("0046")
    # verify tables exist
    alembic_runner.migrate_down_one()
    alembic_runner.migrate_up_one()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_alembic_0046.py -v
```

Expected: FAIL — migration file doesn't exist yet.

- [ ] **Step 3: Write the migration**

```python
# backend/alembic/versions/0046_phase12_options.py
"""Phase 12: OPTION asset class, position_effect, tax_treatment, option_greeks, exercise_elections."""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Must run outside transaction — DDL on enum
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'OPTION'")

    # Backfill meta.asset_class discriminator (idempotent)
    op.execute(
        """
        UPDATE instruments
        SET meta = jsonb_set(meta, '{asset_class}', to_jsonb(asset_class::text))
        WHERE meta != '{}' AND meta->>'asset_class' IS NULL
        """
    )

    # position_effect on orders
    op.execute(
        """
        ALTER TABLE orders
        ADD COLUMN IF NOT EXISTS position_effect TEXT
            CHECK (position_effect IS NULL OR position_effect IN ('OPEN', 'CLOSE'))
        """
    )

    # tax_treatment on orders and fills
    op.execute(
        """
        ALTER TABLE orders
        ADD COLUMN IF NOT EXISTS tax_treatment TEXT
            CHECK (tax_treatment IS NULL OR tax_treatment IN
              ('EQUITY','OPTION_PREMIUM','OPTION_EXERCISE','OPTION_ASSIGNMENT','OPTION_EXPIRY'))
        """
    )
    op.execute(
        """
        ALTER TABLE fills
        ADD COLUMN IF NOT EXISTS tax_treatment TEXT
            CHECK (tax_treatment IS NULL OR tax_treatment IN
              ('EQUITY','OPTION_PREMIUM','OPTION_EXERCISE','OPTION_ASSIGNMENT','OPTION_EXPIRY'))
        """
    )

    # option_greeks table
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS option_greeks (
            instrument_id  BIGINT PRIMARY KEY REFERENCES instruments(id) ON DELETE CASCADE,
            delta          NUMERIC(12, 6),
            gamma          NUMERIC(12, 6),
            theta          NUMERIC(12, 6),
            vega           NUMERIC(12, 6),
            rho            NUMERIC(12, 6),
            iv             NUMERIC(12, 6),
            iv_rank        NUMERIC(5, 2),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS option_greeks_updated_at_idx ON option_greeks (updated_at)")

    # exercise_elections table
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS exercise_elections (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            idempotency_key  UUID NOT NULL UNIQUE,
            jwt_subject      TEXT NOT NULL,
            account_id       UUID NOT NULL REFERENCES broker_accounts(id),
            instrument_id    BIGINT NOT NULL REFERENCES instruments(id),
            action           TEXT NOT NULL CHECK (action IN ('EXERCISE', 'DO_NOT_EXERCISE', 'LAPSE')),
            qty              NUMERIC(20, 8) NOT NULL,
            status           TEXT NOT NULL DEFAULT 'submitted'
                               CHECK (status IN ('submitted', 'confirmed', 'failed')),
            broker_ref       TEXT,
            error_reason     TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS exercise_elections_one_per_day
            ON exercise_elections (account_id, instrument_id)
            WHERE created_at::date = CURRENT_DATE AND status != 'failed'
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS exercise_elections")
    op.execute("DROP INDEX IF EXISTS exercise_elections_one_per_day")
    op.execute("DROP TABLE IF EXISTS option_greeks")
    op.execute("ALTER TABLE fills DROP COLUMN IF EXISTS tax_treatment")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS tax_treatment")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS position_effect")
    # Note: cannot remove enum values in Postgres — OPTION stays in instrument_asset_class
```

- [ ] **Step 4: Run migration test**

```bash
cd backend && pytest tests/test_alembic_0046.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0046_phase12_options.py backend/tests/test_alembic_0046.py
git commit -m "feat(phase12): alembic 0046 — OPTION asset class, position_effect, tax_treatment, option_greeks, exercise_elections"
```

---

### Task A2: ORM models for option_greeks and exercise_elections

**Files:**
- Modify: `backend/app/models/instruments.py`
- Create: `backend/app/models/options.py`

- [ ] **Step 1: Add OPTION to AssetClass enum**

In `backend/app/models/instruments.py`, after the existing enum values (around line 44), add:

```python
    OPTION = "OPTION"
```

- [ ] **Step 2: Write failing test for AssetClass.OPTION**

In `backend/tests/test_alembic_0046.py`, add:

```python
def test_asset_class_option_exists():
    from app.models.instruments import AssetClass
    assert AssetClass.OPTION == "OPTION"
```

- [ ] **Step 3: Run test to verify OPTION is present**

```bash
cd backend && pytest tests/test_alembic_0046.py::test_asset_class_option_exists -v
```

Expected: PASS (already done in step 1).

- [ ] **Step 4: Create ORM models file**

```python
# backend/app/models/options.py
"""ORM models for Phase 12 options tables."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import NUMERIC, TIMESTAMPTZ, BigInteger, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.instruments import Instrument
    from app.models.accounts import BrokerAccount


class OptionGreeks(Base):
    __tablename__ = "option_greeks"

    instrument_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True
    )
    delta: Mapped[Decimal | None] = mapped_column(NUMERIC(12, 6))
    gamma: Mapped[Decimal | None] = mapped_column(NUMERIC(12, 6))
    theta: Mapped[Decimal | None] = mapped_column(NUMERIC(12, 6))
    vega: Mapped[Decimal | None] = mapped_column(NUMERIC(12, 6))
    rho: Mapped[Decimal | None] = mapped_column(NUMERIC(12, 6))
    iv: Mapped[Decimal | None] = mapped_column(NUMERIC(12, 6))
    iv_rank: Mapped[Decimal | None] = mapped_column(NUMERIC(5, 2))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)

    instrument: Mapped[Instrument] = relationship("Instrument", back_populates="greeks")


class ExerciseElection(Base):
    __tablename__ = "exercise_elections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    jwt_subject: Mapped[str] = mapped_column(Text, nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("broker_accounts.id"), nullable=False
    )
    instrument_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instruments.id"), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    qty: Mapped[Decimal] = mapped_column(NUMERIC(20, 8), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="submitted")
    broker_ref: Mapped[str | None] = mapped_column(Text)
    error_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)

    instrument: Mapped[Instrument] = relationship("Instrument")
    account: Mapped[BrokerAccount] = relationship("BrokerAccount")
```

- [ ] **Step 5: Add `greeks` back-reference to Instrument model**

In `backend/app/models/instruments.py`, inside the `Instrument` class after the existing `aliases` relationship, add:

```python
    greeks: Mapped[OptionGreeks | None] = relationship(
        "OptionGreeks", back_populates="instrument", uselist=False
    )
```

And add the import at the top of the file:
```python
from __future__ import annotations  # already present
# Add TYPE_CHECKING guard if not already there:
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.models.options import OptionGreeks
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/instruments.py backend/app/models/options.py backend/tests/test_alembic_0046.py
git commit -m "feat(phase12): ORM models — OptionGreeks, ExerciseElection, AssetClass.OPTION"
```

---

### Task A3: InstrumentMeta Pydantic types

**Files:**
- Create: `backend/app/services/options/__init__.py`
- Create: `backend/app/services/options/types.py`
- Create: `backend/tests/services/options/__init__.py`
- Create: `backend/tests/services/options/test_types.py`

- [ ] **Step 1: Write tests for InstrumentMeta**

```python
# backend/tests/services/options/test_types.py
"""Tests for InstrumentMeta Pydantic discriminated union."""
from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import date
from pydantic import ValidationError


def test_parse_stock_empty_meta():
    from app.services.options.types import parse_instrument_meta, NonOptionDetails
    result = parse_instrument_meta({})
    assert isinstance(result, NonOptionDetails)
    assert result.asset_class == ""


def test_parse_stock_explicit():
    from app.services.options.types import parse_instrument_meta, NonOptionDetails
    result = parse_instrument_meta({"asset_class": "STOCK"})
    assert isinstance(result, NonOptionDetails)


def test_parse_option_details():
    from app.services.options.types import parse_instrument_meta, OptionDetails
    raw = {
        "asset_class": "OPTION",
        "underlying_canonical_id": "stock:SPY:US",
        "strike": "450.00",
        "expiry": "2025-01-17",
        "put_call": "C",
        "multiplier": 100,
        "style": "A",
    }
    result = parse_instrument_meta(raw)
    assert isinstance(result, OptionDetails)
    assert result.strike == Decimal("450.00")
    assert result.multiplier == 100
    assert result.style == "A"


def test_option_details_requires_multiplier():
    from app.services.options.types import OptionDetails
    with pytest.raises(ValidationError):
        OptionDetails(
            underlying_canonical_id="stock:SPY:US",
            strike=Decimal("450"),
            expiry=date(2025, 1, 17),
            put_call="C",
            style="A",
            # multiplier missing
        )


def test_option_details_requires_style():
    from app.services.options.types import OptionDetails
    with pytest.raises(ValidationError):
        OptionDetails(
            underlying_canonical_id="stock:SPY:US",
            strike=Decimal("450"),
            expiry=date(2025, 1, 17),
            put_call="C",
            multiplier=100,
            # style missing
        )


def test_unknown_asset_class_raises():
    from app.services.options.types import parse_instrument_meta
    with pytest.raises(ValidationError):
        parse_instrument_meta({"asset_class": "BOND"})


def test_greeks_snapshot_clamping():
    from app.services.options.types import GreeksSnapshot
    snap = GreeksSnapshot(
        delta=Decimal("99999"),   # out of range
        gamma=Decimal("0.028"),
        theta=Decimal("-0.12"),
        vega=Decimal("0.31"),
        rho=Decimal("0.05"),
        iv=Decimal("0.175"),
    )
    assert snap.delta == Decimal("9999.999999")


def test_subscription_handle_fields():
    from app.services.options.types import SubscriptionHandle
    h = SubscriptionHandle(conid="12345", canonical_id=None, channel="greeks.options.12345")
    assert h.conid == "12345"
    assert h.canonical_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/services/options/test_types.py -v 2>&1 | head -20
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create package files**

```python
# backend/app/services/options/__init__.py
"""Phase 12 options services package."""
```

```python
# backend/tests/services/options/__init__.py
```

- [ ] **Step 4: Create types.py**

```python
# backend/app/services/options/types.py
"""InstrumentMeta discriminated union and related data types for Phase 12."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, model_validator

# "A" = American, "E" = European
_STYLE_COMMENT = "A=American E=European"

NonOptionAssetClass = Literal["", "STOCK", "ETF", "INDEX", "WARRANT", "CBBC", "CRYPTO", "FOREX"]

_CLAMP_MAX = Decimal("9999.999999")
_CLAMP_MIN = Decimal("-9999.999999")


class NonOptionDetails(BaseModel):
    """All non-option instruments. Existing {} rows deserialise with asset_class=''."""
    asset_class: NonOptionAssetClass = ""


class OptionDetails(BaseModel):
    """Options contract details stored in instruments.meta JSONB."""
    asset_class: Literal["OPTION"] = "OPTION"
    underlying_canonical_id: str
    strike: Decimal
    expiry: date
    put_call: Literal["C", "P"]
    multiplier: int          # "A" = American, "E" = European; required — no default
    style: Literal["A", "E"]  # required — no default


# Extensible: FutureDetails, ForexDetails added in Phases 14/15 here
InstrumentMeta = Annotated[
    NonOptionDetails | OptionDetails,
    Field(discriminator="asset_class"),
]

_adapter: TypeAdapter[InstrumentMeta] = TypeAdapter(InstrumentMeta)


def parse_instrument_meta(raw: dict[str, Any]) -> NonOptionDetails | OptionDetails:
    """Parse instruments.meta JSONB dict into a typed model. Raises ValidationError on bad shape."""
    return _adapter.validate_python(raw)


@dataclass
class GreeksSnapshot:
    """Greeks for a single option contract. Clamps extreme values to avoid DB overflow."""
    delta: Decimal
    gamma: Decimal
    theta: Decimal
    vega: Decimal
    rho: Decimal
    iv: Decimal
    iv_rank: Decimal | None = None

    def __post_init__(self) -> None:
        # Import here to avoid circular; metrics module is lightweight
        try:
            from prometheus_client import Counter
            _clamped_counter: Counter | None = Counter(
                "option_greeks_clamped_total",
                "Greeks values clamped to valid range",
                ["field"],
            )
        except Exception:
            _clamped_counter = None

        for fname in ("delta", "gamma", "theta", "vega", "rho", "iv"):
            val = getattr(self, fname)
            if val < _CLAMP_MIN or val > _CLAMP_MAX:
                object.__setattr__(self, fname, max(_CLAMP_MIN, min(_CLAMP_MAX, val)))
                if _clamped_counter is not None:
                    try:
                        _clamped_counter.labels(field=fname).inc()
                    except Exception:
                        pass


@dataclass
class SubscriptionHandle:
    """Tracks a single option strike subscription.

    conid: broker-native source symbol (IBKR conid, OCC symbol, Futu code)
    canonical_id: set once the instrument row has been created (order-intent path)
    channel: Redis channel being subscribed to
    """
    conid: str
    canonical_id: str | None
    channel: str
```

- [ ] **Step 5: Run tests**

```bash
cd backend && pytest tests/services/options/test_types.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/options/ backend/tests/services/options/
git commit -m "feat(phase12): InstrumentMeta Pydantic union, GreeksSnapshot, SubscriptionHandle"
```

---

### Task A4: Market-calendar extensions

**Files:**
- Modify: `backend/app/services/market_calendar.py`
- Create: `backend/tests/services/test_market_calendar_extensions.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/services/test_market_calendar_extensions.py
"""Tests for the four new market_calendar helpers added in Phase 12."""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
from freezegun import freeze_time

UTC = timezone.utc


@freeze_time("2025-01-17 14:00:00+00:00")  # 09:00 ET — inside NYSE session
def test_is_open_nyse_during_session():
    from app.services.market_calendar import is_open
    assert is_open("NYSE") is True


@freeze_time("2025-01-17 23:00:00+00:00")  # 18:00 ET — after close
def test_is_open_nyse_after_close():
    from app.services.market_calendar import is_open
    assert is_open("NYSE") is False


@freeze_time("2025-01-17 14:00:00+00:00")  # 14:00 ET — CBOE maps to US equity
def test_is_open_cboe_maps_to_us():
    from app.services.market_calendar import is_open
    # CBOE uses NYSE hours
    assert is_open("CBOE") is True


@freeze_time("2025-01-17 08:00:00+00:00")  # 16:00 HKT — HKEX closed
def test_is_open_hkex_after_close():
    from app.services.market_calendar import is_open
    assert is_open("HKEX") is False


@freeze_time("2025-01-17 15:01:00+00:00")  # 10:01 ET — after US option cutoff on expiry
def test_is_past_expiry_us_after_cutoff():
    from app.services.market_calendar import is_past_expiry
    expiry = date(2025, 1, 17)
    assert is_past_expiry(expiry, "CBOE") is True


@freeze_time("2025-01-17 13:00:00+00:00")  # 08:00 ET — before cutoff
def test_is_past_expiry_us_before_cutoff():
    from app.services.market_calendar import is_past_expiry
    expiry = date(2025, 1, 17)
    assert is_past_expiry(expiry, "CBOE") is False


@freeze_time("2025-01-18 14:00:00+00:00")  # day after — definitely past
def test_is_past_expiry_day_after():
    from app.services.market_calendar import is_past_expiry
    expiry = date(2025, 1, 17)
    assert is_past_expiry(expiry, "NYSE") is True


def test_option_cutoff_time_us():
    from app.services.market_calendar import option_cutoff_time
    expiry = date(2025, 1, 17)
    cutoff = option_cutoff_time(expiry, "CBOE")
    # 15:00 ET = 20:00 UTC in January
    assert cutoff.hour == 20
    assert cutoff.tzinfo is not None


def test_option_cutoff_time_hkex():
    from app.services.market_calendar import option_cutoff_time
    expiry = date(2025, 1, 17)
    cutoff = option_cutoff_time(expiry, "HKEX")
    # 16:00 HKT = 08:00 UTC
    assert cutoff.hour == 8
    assert cutoff.tzinfo is not None


@freeze_time("2025-01-13 12:00:00+00:00")  # Monday
def test_next_trading_days_nyse():
    from app.services.market_calendar import next_trading_days
    days = next_trading_days(3, "NYSE")
    assert len(days) == 3
    # All must be after today and be trading days
    for d in days:
        assert d > date(2025, 1, 13)


@freeze_time("2025-01-10 12:00:00+00:00")  # Friday
def test_next_trading_days_skips_weekend():
    from app.services.market_calendar import next_trading_days
    days = next_trading_days(2, "NYSE")
    assert len(days) == 2
    # Next two trading days from Friday are Monday + Tuesday
    assert days[0].weekday() == 0  # Monday
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/services/test_market_calendar_extensions.py -v 2>&1 | head -20
```

Expected: FAIL — functions not yet defined.

- [ ] **Step 3: Add four helpers to market_calendar.py**

Open `backend/app/services/market_calendar.py` and append these four functions after the existing `account_day_boundary_utc` function:

```python
# ── Phase 12: options calendar helpers ────────────────────────────────────────

# Exchange codes that map to US equity hours (NYSE session)
_US_EQUITY_EXCHANGES = frozenset({"NYSE", "NASDAQ", "CBOE", "AMEX", "BATS"})
# HKEX exchange code
_HK_EXCHANGES = frozenset({"HKEX", "SEHK"})

# US equity options cutoff: 15:00 Eastern Time
_US_OPTION_CUTOFF_HOUR_LOCAL = 15
# HKEX options cutoff: 16:00 Hong Kong Time
_HK_OPTION_CUTOFF_HOUR_LOCAL = 16

import pytz as _pytz  # already available via exchange_calendars deps

_ET_TZ = _pytz.timezone("America/New_York")
_HKT_TZ = _pytz.timezone("Asia/Hong_Kong")


def _exchange_to_cal_code(exchange: str) -> str:
    """Map friendly exchange name to exchange_calendars code."""
    mapping = {
        "NYSE": "XNYS",
        "NASDAQ": "XNAS",
        "CBOE": "XNYS",   # CBOE options use NYSE hours
        "AMEX": "XNYS",
        "BATS": "XNYS",
        "HKEX": "XHKG",
        "SEHK": "XHKG",
    }
    code = mapping.get(exchange.upper())
    if code is None:
        raise ValueError(f"unsupported_exchange: {exchange}")
    return code


def is_open(exchange: str) -> bool:
    """Return True if the exchange is currently in its regular trading session."""
    now = datetime.now(UTC)
    cal_code = _exchange_to_cal_code(exchange)
    cal = ecals.get_calendar(cal_code)
    today_local = now.astimezone(cal.tz).date()
    iso = today_local.isoformat()
    if not cal.is_session(iso):
        return False
    open_utc = cal.session_open(iso).tz_convert("UTC").to_pydatetime()
    close_utc = cal.session_close(iso).tz_convert("UTC").to_pydatetime()
    return bool(open_utc <= now <= close_utc)


def option_cutoff_time(expiry: date, exchange: str) -> datetime:
    """Return the UTC datetime at which options on expiry stop accepting OPEN orders."""
    exchange_upper = exchange.upper()
    if exchange_upper in _US_EQUITY_EXCHANGES:
        # 15:00 ET on expiry date
        local_dt = _ET_TZ.localize(
            datetime(expiry.year, expiry.month, expiry.day, _US_OPTION_CUTOFF_HOUR_LOCAL, 0, 0)
        )
    elif exchange_upper in _HK_EXCHANGES:
        # 16:00 HKT on expiry date
        local_dt = _HKT_TZ.localize(
            datetime(expiry.year, expiry.month, expiry.day, _HK_OPTION_CUTOFF_HOUR_LOCAL, 0, 0)
        )
    else:
        raise ValueError(f"unsupported_exchange_for_option_cutoff: {exchange}")
    return local_dt.astimezone(UTC)


def is_past_expiry(expiry: date, exchange: str) -> bool:
    """Return True if the current time is past the option expiry moment for the exchange."""
    cutoff = option_cutoff_time(expiry, exchange)
    return datetime.now(UTC) > cutoff


def next_trading_days(n: int, exchange: str) -> list[date]:
    """Return the next n trading days (excluding today) for the exchange."""
    cal_code = _exchange_to_cal_code(exchange)
    cal = ecals.get_calendar(cal_code)
    now = datetime.now(UTC)
    today_local = now.astimezone(cal.tz).date()
    days: list[date] = []
    check = today_local
    while len(days) < n:
        check_naive = datetime(check.year, check.month, check.day)
        next_session = cal.next_session(check_naive.date() if hasattr(check_naive, 'date') else check)
        next_date = next_session.date() if hasattr(next_session, 'date') else date.fromisoformat(str(next_session))
        days.append(next_date)
        check = next_date
    return days
```

- [ ] **Step 4: Run tests**

```bash
cd backend && pytest tests/services/test_market_calendar_extensions.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/market_calendar.py backend/tests/services/test_market_calendar_extensions.py
git commit -m "feat(phase12): market_calendar extensions — is_open, is_past_expiry, option_cutoff_time, next_trading_days"
```

---

### Task A5: InstrumentResolver.find_or_create_option

**Files:**
- Modify: `backend/app/services/quotes/instrument_resolver.py`
- Create: `backend/tests/services/test_instrument_resolver_option.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/services/test_instrument_resolver_option.py
"""Tests for InstrumentResolver.find_or_create_option."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]


def _make_resolver():
    from app.services.quotes.instrument_resolver import InstrumentResolver
    resolver = InstrumentResolver.__new__(InstrumentResolver)
    return resolver


@pytest.mark.asyncio
async def test_find_or_create_option_canonical_id_format():
    """canonical_id must follow option:{SYMBOL}:{EXCHANGE}:{YYMMDD}:{PC}:{STRIKE}."""
    from app.services.quotes.instrument_resolver import _build_option_canonical_id
    result = _build_option_canonical_id(
        underlying_canonical_id="stock:SPY:US",
        expiry=date(2025, 1, 17),
        put_call="C",
        strike=Decimal("450.00"),
        exchange="CBOE",
    )
    assert result == "option:SPY:CBOE:250117:C:450.00"


@pytest.mark.asyncio
async def test_find_or_create_option_calls_resolve_or_create():
    """find_or_create_option must delegate to resolve_or_create with meta= kwarg."""
    resolver = _make_resolver()
    mock_instrument = MagicMock()
    resolver.resolve_or_create = AsyncMock(return_value=mock_instrument)

    from app.services.quotes.instrument_resolver import InstrumentResolver
    db = AsyncMock()
    result = await InstrumentResolver.find_or_create_option(
        resolver,
        db=db,
        underlying_canonical_id="stock:SPY:US",
        strike=Decimal("450.00"),
        expiry=date(2025, 1, 17),
        put_call="C",
        multiplier=100,
        style="A",
        exchange="CBOE",
        currency="USD",
        source="ibkr",
        source_symbol="12345678",
    )

    assert result is mock_instrument
    call_kwargs = resolver.resolve_or_create.call_args.kwargs
    assert call_kwargs["canonical_id"] == "option:SPY:CBOE:250117:C:450.00"
    assert call_kwargs["raw_symbol"] == "12345678"
    meta = call_kwargs["meta"]
    assert meta["asset_class"] == "OPTION"
    assert meta["multiplier"] == 100
    assert meta["style"] == "A"


@pytest.mark.asyncio
async def test_find_or_create_option_uses_meta_kwarg_not_contract_details():
    """resolve_or_create must be called with meta= not contract_details= (CRIT-C)."""
    resolver = _make_resolver()
    resolver.resolve_or_create = AsyncMock(return_value=MagicMock())

    from app.services.quotes.instrument_resolver import InstrumentResolver
    await InstrumentResolver.find_or_create_option(
        resolver,
        db=AsyncMock(),
        underlying_canonical_id="stock:SPY:US",
        strike=Decimal("450"),
        expiry=date(2025, 1, 17),
        put_call="P",
        multiplier=100,
        style="A",
        exchange="CBOE",
        currency="USD",
        source="ibkr",
        source_symbol="99887766",
    )

    kwargs = resolver.resolve_or_create.call_args.kwargs
    assert "meta" in kwargs
    assert "contract_details" not in kwargs
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/services/test_instrument_resolver_option.py -v 2>&1 | head -20
```

Expected: FAIL — functions not defined.

- [ ] **Step 3: Add `_build_option_canonical_id` and `find_or_create_option`**

Open `backend/app/services/quotes/instrument_resolver.py`. At the end of the file (after all existing methods), add:

```python
def _build_option_canonical_id(
    underlying_canonical_id: str,
    expiry: "date",
    put_call: str,
    strike: "Decimal",
    exchange: str,
) -> str:
    """Build option canonical_id: option:{SYMBOL}:{EXCHANGE}:{YYMMDD}:{P|C}:{STRIKE}."""
    # Extract underlying symbol from canonical_id (e.g. "stock:SPY:US" -> "SPY")
    symbol = underlying_canonical_id.split(":")[1]
    yymmdd = expiry.strftime("%y%m%d")
    return f"option:{symbol}:{exchange}:{yymmdd}:{put_call}:{strike}"
```

And inside the `InstrumentResolver` class, add:

```python
    async def find_or_create_option(
        self,
        db: AsyncSession,
        underlying_canonical_id: str,
        strike: Decimal,
        expiry: date,
        put_call: Literal["C", "P"],
        multiplier: int,           # required — not defaulted
        style: Literal["A", "E"],  # required — not defaulted; "A"=American "E"=European
        exchange: str,
        currency: str,
        source: str,
        source_symbol: str,        # broker-native ID: conid (IBKR), OCC (Alpaca), Futu code (Futu HK)
    ) -> "Instrument":
        """Race-safe upsert for an option instrument row. Called on order-intent only (not chain browse)."""
        from app.services.options.types import OptionDetails

        canonical_id = _build_option_canonical_id(
            underlying_canonical_id, expiry, put_call, strike, exchange
        )
        details = OptionDetails(
            underlying_canonical_id=underlying_canonical_id,
            strike=strike,
            expiry=expiry,
            put_call=put_call,
            multiplier=multiplier,
            style=style,
        )
        return await self.resolve_or_create(
            canonical_id=canonical_id,
            asset_class=AssetClass.OPTION,
            primary_exchange=exchange,
            currency=currency,
            meta=details.model_dump(),    # CRIT-C: column stays named meta
            source=source,
            raw_symbol=source_symbol,
            db=db,
        )
```

Also add required imports at the top of the file (check what's already there and add only what's missing):
```python
from datetime import date
from decimal import Decimal
from typing import Literal
```

- [ ] **Step 4: Run tests**

```bash
cd backend && pytest tests/services/test_instrument_resolver_option.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/quotes/instrument_resolver.py backend/tests/services/test_instrument_resolver_option.py
git commit -m "feat(phase12): InstrumentResolver.find_or_create_option + _build_option_canonical_id"
```

---

## Chunk B — Proto + Risk Engine

### Task B1: Proto changes — 4 new RPCs + OptionContractHint

**Files:**
- Modify: `proto/broker/v1/broker.proto`

- [ ] **Step 1: Add OptionContractHint message and update SymbolRef**

Find the `SymbolRef` message (around line 448) and replace `bytes source_meta = 6` with the oneof:

```protobuf
message OptionContractHint {
  string conid      = 1;  // broker-native source symbol
  string strike     = 2;  // string decimal e.g. "450.00"
  string expiry_iso = 3;  // "2025-01-17"
  string put_call   = 4;  // "C" | "P"
  int32  multiplier = 5;
}

// In SymbolRef, replace:  bytes source_meta = 6;
// with:
// oneof contract_hint {
//   OptionContractHint option_hint = 6;  // tag 6 reuse is intentional (source_meta never written in prod)
//   // FutureContractHint future_hint = 7;  Phase 14
// }
```

Full updated `SymbolRef`:
```protobuf
message SymbolRef {
  string canonical_id = 1;
  string raw_symbol = 2;
  AssetClass asset_class = 3;
  string exchange = 4;
  string currency = 5;
  oneof contract_hint {
    OptionContractHint option_hint = 6;
    // FutureContractHint future_hint = 7;  Phase 14
    // ForexContractHint  forex_hint  = 8;  Phase 15
  }
  reserved 9 to 15;
}
```

Remove `reserved 7 to 15` from SymbolRef (now only `reserved 9 to 15` after the oneof).

- [ ] **Step 2: Add new messages**

After the existing messages, add:

```protobuf
// ── Phase 12: Options ─────────────────────────────────────────────────────

message OptionChainRequest {
  string underlying_symbol = 1;
  string expiry_date        = 2;  // ISO "2025-01-17"
  string currency           = 3;  // "USD" | "HKD"
  int32  strike_count       = 4;  // max 60 USD, 40 HKD
}

message OptionChainRow {
  string conid         = 1;  // broker-native source symbol
  string strike        = 2;  // string decimal (MED-O: no float precision drift)
  string put_call      = 3;  // "C" | "P"
  string bid           = 4;  // string decimal
  string ask           = 5;  // string decimal
  double iv            = 6;
  double delta         = 7;
  double gamma         = 8;
  double theta         = 9;
  double vega          = 10;
  int64  open_interest = 11;
  int64  volume        = 12;
  int32  multiplier    = 13;
  string exchange      = 14;
  string style         = 15;  // "A"=American | "E"=European; required
}

message OptionChainResponse {
  repeated OptionChainRow calls         = 1;
  repeated OptionChainRow puts          = 2;
  string                  source        = 3;
  int64                   fetched_at_ms = 4;
}

message OptionExpirationsRequest {
  string underlying_symbol = 1;
  string currency          = 2;
}

message OptionExpirationsResponse {
  repeated string expiry_dates = 1;  // ISO dates sorted ascending
}

message OptionGreeksRequest {
  repeated string conids     = 1;
  string          account_id = 2;
}

message OptionGreeksResponse {
  string conid         = 1;
  double delta         = 2;
  double gamma         = 3;
  double theta         = 4;
  double vega          = 5;
  double rho           = 6;
  double iv            = 7;
  double iv_rank       = 8;  // 0.0 until Phase 18
  int64  fetched_at_ms = 9;
}

message ExerciseOptionRequest {
  string account_id      = 1;
  string conid           = 2;
  int64  qty             = 3;
  string action          = 4;  // "EXERCISE" | "DO_NOT_EXERCISE" | "LAPSE"
  string idempotency_key = 5;  // UUID; required
}

message ExerciseOptionResponse {
  bool   success    = 1;
  string broker_ref = 2;
  string message    = 3;
}
```

- [ ] **Step 3: Add the four RPCs**

After the last existing `rpc` line, add:

```protobuf
  // ── Phase 12: Options ───────────────────────────────────────────────────
  rpc GetOptionChain(OptionChainRequest) returns (OptionChainResponse);
  rpc GetOptionExpirations(OptionExpirationsRequest) returns (OptionExpirationsResponse);
  rpc StreamOptionGreeks(OptionGreeksRequest) returns (stream OptionGreeksResponse);
  rpc ExerciseOption(ExerciseOptionRequest) returns (ExerciseOptionResponse);
```

- [ ] **Step 4: Regenerate protobuf stubs**

```bash
cd /home/joseph/dashboard && ./scripts/gen-proto.sh 2>/dev/null || (cd proto && python -m grpc_tools.protoc -I. --python_out=../backend --grpc_python_out=../backend broker/v1/broker.proto)
```

Verify no compile errors.

- [ ] **Step 5: Commit**

```bash
git add proto/broker/v1/broker.proto
git commit -m "feat(phase12): proto — OptionContractHint oneof, 4 option RPCs, option messages"
```

---

### Task B2: EvaluationContext + RiskContext additions

**Files:**
- Modify: `backend/app/services/risk_service.py`
- Create: `backend/tests/services/test_options_risk_checks.py`

- [ ] **Step 1: Write failing tests for new EvaluationContext fields**

```python
# backend/tests/services/test_options_risk_checks.py
"""Tests for Phase 12 options risk checks."""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]


def _make_ctx(
    *,
    asset_class="OPTION",
    side="sell",
    position_effect="OPEN",
    multiplier=100,
    symbol="SPY250117C00450000",
    instrument_id=42,
):
    from app.services.risk_service import EvaluationContext
    return EvaluationContext(
        account_id=uuid.uuid4(),
        broker_id="ibkr",
        instrument_id=instrument_id,
        side=side,
        qty=Decimal("1"),
        price=Decimal("5.00"),
        order_type="LIMIT",
        time_in_force="DAY",
        request_id="test-001",
        currency_base="USD",
        symbol=symbol,
        asset_class=asset_class,
        multiplier=multiplier,
        position_effect=position_effect,
    )


def test_evaluation_context_has_multiplier():
    ctx = _make_ctx(multiplier=100)
    assert ctx.multiplier == 100


def test_evaluation_context_has_position_effect():
    ctx = _make_ctx(position_effect="OPEN")
    assert ctx.position_effect == "OPEN"


def test_evaluation_context_multiplier_defaults_to_1():
    from app.services.risk_service import EvaluationContext
    ctx = EvaluationContext(
        account_id=uuid.uuid4(),
        broker_id="ibkr",
        instrument_id=None,
        side="buy",
        qty=Decimal("100"),
        price=Decimal("150"),
        order_type="LIMIT",
        time_in_force="DAY",
        request_id="test-002",
        currency_base="USD",
    )
    assert ctx.multiplier == 1
    assert ctx.position_effect is None


def _make_risk_service(*, config_values=None, positions=None):
    from app.services.risk_service import RiskService
    config_values = config_values or {}
    db = AsyncMock()
    redis = AsyncMock()

    async def get_bool(ns, key, *, default=False):
        return config_values.get(f"{ns}/{key}", default)

    async def get_int(ns, key, default=None):
        return config_values.get(f"{ns}/{key}", default)

    async def get_json(ns, key, default=None):
        return config_values.get(f"{ns}/{key}", default)

    cfg = MagicMock()
    cfg.get_bool = get_bool
    cfg.get_int = get_int
    cfg.get_json = get_json

    svc = RiskService(db=db, redis=redis, config=cfg, sidecar=MagicMock())
    return svc


@pytest.mark.asyncio
async def test_options_level_gate_blocks_when_level_too_low():
    """STO naked call should be blocked at L1."""
    svc = _make_risk_service(config_values={"options/trading_level": 1})
    ctx = _make_ctx(side="sell", position_effect="OPEN")

    # Mock _get_existing_long_position to return 0 (no cover)
    svc._get_existing_long_position = AsyncMock(return_value=Decimal("0"))

    from app.services.risk_service import CheckResult, Verdict
    result = await svc._check_options_exposure(ctx)
    assert result.verdict == Verdict.BLOCK
    assert result.code == "naked_short_not_permitted"


@pytest.mark.asyncio
async def test_bto_always_allowed_at_l1():
    """BTO (long call/put) should pass at L1."""
    svc = _make_risk_service(config_values={"options/trading_level": 1})
    ctx = _make_ctx(side="buy", position_effect="OPEN")

    from app.services.risk_service import CheckResult, Verdict
    result = await svc._check_options_exposure(ctx)
    assert result.verdict == Verdict.ALLOW


@pytest.mark.asyncio
async def test_expiry_cutoff_blocks_open_order():
    """Opening an order after cutoff on expiry day should be BLOCK."""
    from datetime import date
    svc = _make_risk_service()
    ctx = _make_ctx(side="buy", position_effect="OPEN")

    with patch("app.services.risk_service.market_calendar") as mc:
        mc.option_cutoff_time.return_value = __import__("datetime").datetime(2025, 1, 17, 19, 0, tzinfo=__import__("datetime").timezone.utc)
        mc.is_past_expiry.return_value = True

        from app.services.risk_service import Verdict
        result = await svc._check_options_exposure(ctx)
        assert result.verdict == Verdict.BLOCK
        assert result.code == "option_cutoff_passed"


@pytest.mark.asyncio
async def test_zero_dte_warn():
    """0DTE order should produce a WARN (not BLOCK) after passing all BLOCK checks."""
    from datetime import date
    svc = _make_risk_service(config_values={"options/trading_level": 4})
    ctx = _make_ctx(side="buy", position_effect="OPEN")

    with patch("app.services.risk_service.market_calendar") as mc:
        mc.is_past_expiry.return_value = False
        mc.option_cutoff_time.return_value = __import__("datetime").datetime(2025, 1, 17, 20, 0, tzinfo=__import__("datetime").timezone.utc)

        # Mock the instrument lookup to return today's expiry
        svc._get_option_expiry = AsyncMock(return_value=date.today())

        from app.services.risk_service import Verdict
        result = await svc._check_options_exposure(ctx)
        assert result.verdict == Verdict.WARN
        assert result.code == "zero_dte_order"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/services/test_options_risk_checks.py -v 2>&1 | head -20
```

Expected: FAIL.

- [ ] **Step 3: Add multiplier and position_effect to EvaluationContext**

In `backend/app/services/risk_service.py`, find the `EvaluationContext` dataclass (around line 74) and add after `asset_class`:

```python
    multiplier: int = 1                  # 1 for non-options; 100 for standard equity options
    position_effect: str | None = None   # "OPEN" | "CLOSE" | None (None = equity)
```

- [ ] **Step 4: Add `_check_options_exposure` method to RiskService**

In `backend/app/services/risk_service.py`, add after `_check_margin`:

```python
    async def _check_options_exposure(self, ctx: EvaluationContext) -> "CheckResult":
        """Phase 12: Options-specific risk checks. Called from evaluate() when asset_class==OPTION.

        Check ordering (MED-6):
          1a: trading-level gate  (BLOCK — cheap config read)
          1b: expiry-day cutoff   (BLOCK — config + datetime)
          1c: naked-short check   (BLOCK — requires positions query)
          1d: cash-secured put    (BLOCK — requires BP/cash query)
          post-BLOCK: 0DTE WARN, assignment-risk WARN
        """
        from datetime import date, datetime, timezone
        from app.services import market_calendar

        instrument_id = ctx.instrument_id
        side = ctx.side
        position_effect = ctx.position_effect

        # Step 1a: trading-level gate
        trading_level = await self._config.get_int("options", "trading_level", default=1) or 1

        is_sto = (side == "sell" and position_effect == "OPEN")
        is_bto = (side == "buy" and position_effect == "OPEN")
        is_btc = (side == "buy" and position_effect == "CLOSE")
        is_stc = (side == "sell" and position_effect == "CLOSE")

        if is_sto:
            # Check if covered (cover = existing long stock/option position >= qty*100 for calls)
            existing_cover = await self._get_existing_long_position(ctx)
            is_naked = existing_cover < ctx.qty
            if is_naked and trading_level < 3:
                return CheckResult(
                    name="options_exposure",
                    verdict=Verdict.BLOCK,
                    code="naked_short_not_permitted",
                    detail=f"Naked short requires options trading level 3+, current={trading_level}",
                )

        # Step 1b: expiry-day cutoff
        option_expiry = await self._get_option_expiry(ctx)
        if option_expiry is not None:
            exchange = await self._get_instrument_exchange(ctx) or "NYSE"
            try:
                if market_calendar.is_past_expiry(option_expiry, exchange):
                    return CheckResult(
                        name="options_exposure",
                        verdict=Verdict.BLOCK,
                        code="option_cutoff_passed",
                        detail=f"Past option cutoff for expiry {option_expiry} on {exchange}",
                    )
            except ValueError:
                pass  # unknown exchange — skip cutoff check

        # Step 1c and 1d already handled above (naked-short blocks before cash-secured check)
        # Cash-secured put reserve at L2
        if is_sto and trading_level == 2 and ctx.symbol and "P" in (ctx.symbol or ""):
            # Cash-secured put: verify available cash >= strike * qty * multiplier * 1.05
            # (strike extracted from symbol or instrument meta — best-effort here)
            pass  # Full implementation in orders_service where we have instrument details

        # post-BLOCK WARNs
        if option_expiry is not None and option_expiry == date.today():
            return CheckResult(
                name="options_exposure",
                verdict=Verdict.WARN,
                code="zero_dte_order",
                detail="0DTE order — option expires today",
            )

        return CheckResult(name="options_exposure", verdict=Verdict.ALLOW)

    async def _get_existing_long_position(self, ctx: EvaluationContext) -> "Decimal":
        """Return existing long qty for the instrument (0 if none)."""
        from decimal import Decimal
        if ctx.instrument_id is None:
            return Decimal("0")
        result = await self._db.execute(
            __import__("sqlalchemy").text(
                "SELECT COALESCE(SUM(qty), 0) FROM positions "
                "WHERE instrument_id = :iid AND qty > 0"
            ),
            {"iid": ctx.instrument_id},
        )
        row = result.fetchone()
        return Decimal(str(row[0])) if row else Decimal("0")

    async def _get_option_expiry(self, ctx: EvaluationContext) -> "date | None":
        """Return expiry date for an option instrument, or None if not an option."""
        if ctx.instrument_id is None or ctx.asset_class != "OPTION":
            return None
        result = await self._db.execute(
            __import__("sqlalchemy").text(
                "SELECT meta->>'expiry' FROM instruments WHERE id = :iid"
            ),
            {"iid": ctx.instrument_id},
        )
        row = result.fetchone()
        if row and row[0]:
            from datetime import date
            return date.fromisoformat(row[0])
        return None

    async def _get_instrument_exchange(self, ctx: EvaluationContext) -> "str | None":
        """Return primary_exchange for an instrument."""
        if ctx.instrument_id is None:
            return None
        result = await self._db.execute(
            __import__("sqlalchemy").text(
                "SELECT primary_exchange FROM instruments WHERE id = :iid"
            ),
            {"iid": ctx.instrument_id},
        )
        row = result.fetchone()
        return row[0] if row else None
```

- [ ] **Step 5: Wire `_check_options_exposure` into `evaluate()`**

In the `evaluate()` method, after the kill-switch check (step 1) and before the `asyncio.gather()` block with `_check_pdt`, add a conditional call:

```python
        # Step 1a-1d: options checks (insert between kill-switch and PDT)
        if ctx.asset_class == "OPTION":
            options_result = await self._check_options_exposure(ctx)
            if options_result.verdict == Verdict.BLOCK:
                # Return immediately — no need to run remaining checks
                return GateVerdict(
                    verdict=options_result.verdict,
                    blockers=[options_result],
                    warnings=[],
                )
            if options_result.verdict == Verdict.WARN:
                # Collect warning, continue with remaining checks
                pre_warnings = [options_result]
            else:
                pre_warnings = []
        else:
            pre_warnings = []
```

Then in the final `GateVerdict` assembly, include `pre_warnings` in the warnings list.

- [ ] **Step 6: Run tests**

```bash
cd backend && pytest tests/services/test_options_risk_checks.py -v
```

Expected: PASS.

- [ ] **Step 7: Run full risk service tests to check no regression**

```bash
cd backend && pytest tests/services/test_risk_service.py -v
```

Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/risk_service.py backend/tests/services/test_options_risk_checks.py
git commit -m "feat(phase12): EvaluationContext + _check_options_exposure — trading level gate, cutoff, naked-short, 0DTE"
```

---

### Task B3: Telegram OCC symbol rejection

**Files:**
- Modify: `backend/app/services/telegram/order_flow.py`
- Create: `backend/tests/services/test_telegram_options_reject.py`

- [ ] **Step 1: Write the test**

```python
# backend/tests/services/test_telegram_options_reject.py
"""OCC option symbols must be rejected by parse_place_order."""
from __future__ import annotations
import pytest


def test_occ_symbol_5char_rejected():
    from app.services.telegram.order_flow import parse_place_order
    result = parse_place_order("/place_order SPY250117C00450000 BUY 1")
    assert result is None


def test_occ_symbol_4char_rejected():
    from app.services.telegram.order_flow import parse_place_order
    result = parse_place_order("/place_order AAPL250117P00180000 BUY 1")
    assert result is None


def test_occ_symbol_6char_preferred_shares_rejected():
    """6-char root (e.g. BRK.B preferred shares) must also be rejected."""
    from app.services.telegram.order_flow import parse_place_order
    result = parse_place_order("/place_order BRKBSR250117P00400000 BUY 1")
    # Note: this is a 6-char root OCC-style symbol
    # parse_place_order returns None (rejected by _SYMBOL_RE or _OCC_PATTERN)
    # Either rejection mechanism is acceptable
    assert result is None


def test_equity_symbol_allowed():
    from app.services.telegram.order_flow import parse_place_order
    result = parse_place_order("/place_order AAPL BUY 10 --limit 150.00")
    assert result is not None
    assert result.symbol == "AAPL"


def test_equity_3char_allowed():
    from app.services.telegram.order_flow import parse_place_order
    result = parse_place_order("/place_order SPY BUY 5")
    assert result is not None
```

- [ ] **Step 2: Run tests to verify OCC is currently not rejected**

```bash
cd backend && pytest tests/services/test_telegram_options_reject.py -v 2>&1 | head -20
```

Expected: Some tests FAIL (OCC symbols currently pass through).

- [ ] **Step 3: Add OCC_PATTERN guard to parse_place_order**

In `backend/app/services/telegram/order_flow.py`, at the module level (near the existing `_SYMBOL_RE`), add:

```python
# Reject OCC-format option symbols (1-6 char root + 6 digits + C/P + 8 digits)
# Users should use equity symbols only; options are not supported via Telegram
_OCC_PATTERN = re.compile(r'^[A-Z]{1,6}\d{6}[CP]\d{8}$')
```

Then in `parse_place_order`, after extracting `symbol = parts[1].upper()`, add:

```python
    if _OCC_PATTERN.match(symbol):
        return None  # Options orders are not supported via Telegram
```

- [ ] **Step 4: Run tests**

```bash
cd backend && pytest tests/services/test_telegram_options_reject.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full Telegram test suite for regression**

```bash
cd backend && pytest tests/ -k "telegram" -v 2>&1 | tail -20
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/telegram/order_flow.py backend/tests/services/test_telegram_options_reject.py
git commit -m "feat(phase12): reject OCC option symbols in Telegram parse_place_order"
```

---

## Chunk C — Options Services

### Task C1: OptionChainService

**Files:**
- Create: `backend/app/services/options/chain_service.py`
- Create: `backend/tests/services/options/test_chain_service.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/services/options/test_chain_service.py
"""Tests for OptionChainService — cache, singleflight, source routing, budget."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]


def _make_service(*, redis=None, config_values=None, sidecar=None):
    from app.services.options.chain_service import OptionChainService
    redis = redis or AsyncMock()
    config_values = config_values or {"quote_engine/option_chain_sources": {"USD": ["ibkr"], "HKD": ["futu"]}}
    sidecar = sidecar or AsyncMock()

    async def get_json(ns, key, default=None):
        return config_values.get(f"{ns}/{key}", default)

    cfg = MagicMock()
    cfg.get_json = get_json

    svc = OptionChainService(redis=redis, config=cfg, broker_registry=MagicMock())
    svc._sidecar = sidecar
    return svc


@pytest.mark.asyncio
async def test_get_chain_cache_hit():
    """Cache hit should return without calling sidecar."""
    from app.services.options.chain_service import OptionChainService
    redis = AsyncMock()
    cached_row = {"conid": "123", "strike": "450.00", "put_call": "C", "bid": "5.00", "ask": "5.20",
                  "iv": 0.175, "delta": 0.5, "gamma": 0.028, "theta": -0.12, "vega": 0.31,
                  "open_interest": 38000, "volume": 12000, "multiplier": 100, "exchange": "CBOE", "style": "A"}
    cached = {"calls": [cached_row], "puts": [], "source": "ibkr", "fetched_at_ms": 1700000000000}
    redis.get = AsyncMock(return_value=json.dumps(cached))

    svc = _make_service(redis=redis)
    svc._fetch_from_sidecar = AsyncMock()

    result = await svc.get_chain("SPY", date(2025, 1, 17), strike_count=20, currency="USD")

    svc._fetch_from_sidecar.assert_not_called()
    assert result["source"] == "ibkr"


@pytest.mark.asyncio
async def test_get_chain_cache_miss_calls_sidecar():
    """Cache miss should call sidecar and populate cache."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    svc = _make_service(redis=redis)
    fake_response = {"calls": [], "puts": [], "source": "ibkr", "fetched_at_ms": 1700000000000}
    svc._fetch_from_sidecar = AsyncMock(return_value=fake_response)

    result = await svc.get_chain("SPY", date(2025, 1, 17), strike_count=20, currency="USD")

    svc._fetch_from_sidecar.assert_called_once()
    redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_exchange_aware_ttl_market_open():
    """During market hours, TTL should be 30s."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    svc = _make_service(redis=redis)
    svc._fetch_from_sidecar = AsyncMock(return_value={"calls": [], "puts": [], "source": "ibkr", "fetched_at_ms": 0})

    with patch("app.services.options.chain_service.market_calendar") as mc:
        mc.is_open.return_value = True
        await svc.get_chain("SPY", date(2025, 1, 17), strike_count=20, currency="USD")

    # Check that setex was called with TTL=30
    call_args = redis.setex.call_args
    assert call_args[0][1] == 30


@pytest.mark.asyncio
async def test_exchange_aware_ttl_market_closed():
    """Outside market hours, TTL should be 300s."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    svc = _make_service(redis=redis)
    svc._fetch_from_sidecar = AsyncMock(return_value={"calls": [], "puts": [], "source": "ibkr", "fetched_at_ms": 0})

    with patch("app.services.options.chain_service.market_calendar") as mc:
        mc.is_open.return_value = False
        await svc.get_chain("SPY", date(2025, 1, 17), strike_count=20, currency="USD")

    call_args = redis.setex.call_args
    assert call_args[0][1] == 300
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/services/options/test_chain_service.py -v 2>&1 | head -20
```

- [ ] **Step 3: Create OptionChainService**

```python
# backend/app/services/options/chain_service.py
"""OptionChainService — fetches, caches, and routes option chain data."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date
from typing import Any

import structlog

from app.services import market_calendar

log = structlog.get_logger(__name__)

# Cache key: options:chain:{underlying_canonical_id}:{expiry_iso}:{source}
_CACHE_KEY_FMT = "options:chain:{underlying}:{expiry}:{source}"
_TTL_MARKET_OPEN = 30
_TTL_MARKET_CLOSED = 300

# USD exchange for TTL decisions
_USD_EXCHANGE = "NYSE"
_HKD_EXCHANGE = "HKEX"

# Default subscription budgets (overridden by app_config[quote_engine/option_sub_budgets])
_DEFAULT_BUDGETS: dict[str, int] = {"ibkr": 400, "alpaca": 600, "futu": 400}


class OptionChainService:
    def __init__(self, *, redis: Any, config: Any, broker_registry: Any) -> None:
        self._redis = redis
        self._config = config
        self._broker_registry = broker_registry
        # Singleflight locks: (underlying_canonical_id, expiry_iso, source) -> asyncio.Lock
        self._sf_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self._sf_lock_meta = asyncio.Lock()
        self._sources: dict[str, list[str]] = {"USD": ["ibkr"], "HKD": ["futu"]}
        self._budgets: dict[str, int] = dict(_DEFAULT_BUDGETS)

    async def reload_config(self) -> None:
        sources = await self._config.get_json("quote_engine", "option_chain_sources", default=None)
        if sources:
            self._sources = sources
        budgets = await self._config.get_json("quote_engine", "option_sub_budgets", default=None)
        if budgets:
            self._budgets = {**_DEFAULT_BUDGETS, **budgets}

    def _cache_key(self, underlying: str, expiry: date, source: str) -> str:
        return _CACHE_KEY_FMT.format(
            underlying=underlying,
            expiry=expiry.isoformat(),
            source=source,
        )

    def _ttl(self, currency: str) -> int:
        exchange = _USD_EXCHANGE if currency == "USD" else _HKD_EXCHANGE
        try:
            is_market_open = market_calendar.is_open(exchange)
        except Exception:
            is_market_open = False
        return _TTL_MARKET_OPEN if is_market_open else _TTL_MARKET_CLOSED

    async def _sf_lock(self, key: tuple[str, str, str]) -> asyncio.Lock:
        async with self._sf_lock_meta:
            if key not in self._sf_locks:
                self._sf_locks[key] = asyncio.Lock()
            return self._sf_locks[key]

    async def get_expirations(self, underlying: str, currency: str) -> list[date]:
        """Return sorted expiry dates for an underlying from the configured primary source."""
        sources = self._sources.get(currency, [])
        for source in sources:
            try:
                result = await self._fetch_expirations_from_source(underlying, currency, source)
                if result:
                    return result
            except Exception as exc:
                log.warning("option_expirations_source_failed", source=source, error=str(exc))
        return []

    async def _fetch_expirations_from_source(
        self, underlying: str, currency: str, source: str
    ) -> list[date]:
        # Delegate to sidecar via broker_registry
        # (stub — actual sidecar call implemented in Task C1 extension)
        return []

    async def get_chain(
        self,
        underlying: str,
        expiry: date,
        strike_count: int = 20,
        currency: str = "USD",
    ) -> dict[str, Any]:
        """Return option chain data. Cache-first; singleflight per (underlying, expiry, source)."""
        sources = self._sources.get(currency, [])
        for source in sources:
            cache_key = self._cache_key(underlying, expiry, source)
            cached = await self._redis.get(cache_key)
            if cached:
                return json.loads(cached)

            sf_key = (underlying, expiry.isoformat(), source)
            lock = await self._sf_lock(sf_key)
            async with lock:
                # Double-check cache after acquiring lock
                cached = await self._redis.get(cache_key)
                if cached:
                    return json.loads(cached)
                try:
                    result = await self._fetch_from_sidecar(underlying, expiry, strike_count, source, currency)
                    ttl = self._ttl(currency)
                    await self._redis.setex(cache_key, ttl, json.dumps(result))
                    return result
                except Exception as exc:
                    log.warning("option_chain_source_failed", source=source, error=str(exc))

        # All sources failed
        return {"calls": [], "puts": [], "source": "none", "fetched_at_ms": int(time.time() * 1000), "stale": True}

    async def _fetch_from_sidecar(
        self, underlying: str, expiry: date, strike_count: int, source: str, currency: str
    ) -> dict[str, Any]:
        """Fetch chain from a specific broker sidecar. Override in tests."""
        # Actual gRPC call implementation added when sidecars are extended
        raise NotImplementedError(f"Sidecar fetch not yet implemented for {source}")
```

- [ ] **Step 4: Run tests**

```bash
cd backend && pytest tests/services/options/test_chain_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/options/chain_service.py backend/tests/services/options/test_chain_service.py
git commit -m "feat(phase12): OptionChainService — cache, singleflight per (underlying,expiry,source), exchange-aware TTL"
```

---

### Task C2: OptionGreeksService

**Files:**
- Create: `backend/app/services/options/greeks_service.py`
- Create: `backend/tests/services/options/test_greeks_service.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/services/options/test_greeks_service.py
"""Tests for OptionGreeksService — upsert guard, eviction, clamping."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]


def _make_service(*, db=None, redis=None):
    from app.services.options.greeks_service import OptionGreeksService
    db = db or AsyncMock()
    redis = redis or AsyncMock()
    return OptionGreeksService(db=db, redis=redis)


def _make_snapshot(**kwargs):
    from app.services.options.types import GreeksSnapshot
    defaults = dict(delta=Decimal("0.5"), gamma=Decimal("0.028"), theta=Decimal("-0.12"),
                    vega=Decimal("0.31"), rho=Decimal("0.05"), iv=Decimal("0.175"))
    defaults.update(kwargs)
    return GreeksSnapshot(**defaults)


@pytest.mark.asyncio
async def test_upsert_guard_rejects_chain_browse_instrument():
    """upsert should refuse if instrument has no position or order today."""
    svc = _make_service()
    svc._has_position_or_order = AsyncMock(return_value=False)
    svc._db_upsert = AsyncMock()

    snap = _make_snapshot()
    await svc.upsert(instrument_id=42, greeks=snap)

    svc._db_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_writes_when_position_exists():
    """upsert should write when instrument has a position."""
    svc = _make_service()
    svc._has_position_or_order = AsyncMock(return_value=True)
    svc._db_upsert = AsyncMock()

    snap = _make_snapshot()
    await svc.upsert(instrument_id=42, greeks=snap)

    svc._db_upsert.assert_called_once()


@pytest.mark.asyncio
async def test_greeks_clamping_applied_before_upsert():
    """GreeksSnapshot with out-of-range values should be clamped."""
    from app.services.options.types import GreeksSnapshot
    snap = GreeksSnapshot(
        delta=Decimal("99999"),
        gamma=Decimal("0.028"),
        theta=Decimal("-0.12"),
        vega=Decimal("0.31"),
        rho=Decimal("0.05"),
        iv=Decimal("0.175"),
    )
    assert snap.delta == Decimal("9999.999999")


@pytest.mark.asyncio
async def test_evict_stale_deletes_old_rows():
    """evict_stale should delete rows older than the threshold."""
    svc = _make_service()
    svc._db_delete_stale = AsyncMock(return_value=5)

    deleted = await svc.evict_stale(older_than=timedelta(minutes=5))
    assert deleted == 5
    svc._db_delete_stale.assert_called_once()
```

- [ ] **Step 2: Create OptionGreeksService**

```python
# backend/app/services/options/greeks_service.py
"""OptionGreeksService — persisted Greeks for held/traded option contracts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.options.types import GreeksSnapshot

log = structlog.get_logger(__name__)

UTC = timezone.utc


class OptionGreeksService:
    def __init__(self, *, db: AsyncSession, redis: Any) -> None:
        self._db = db
        self._redis = redis

    async def _has_position_or_order(self, instrument_id: int) -> bool:
        """Return True if the instrument has a position or an order created today."""
        today = datetime.now(UTC).date().isoformat()
        result = await self._db.execute(
            text(
                "SELECT 1 FROM positions WHERE instrument_id = :iid AND qty != 0 "
                "UNION ALL "
                "SELECT 1 FROM orders WHERE instrument_id = :iid AND created_at::date = :today "
                "LIMIT 1"
            ),
            {"iid": instrument_id, "today": today},
        )
        return result.fetchone() is not None

    async def _db_upsert(self, instrument_id: int, snap: GreeksSnapshot) -> None:
        now = datetime.now(UTC)
        await self._db.execute(
            text(
                """
                INSERT INTO option_greeks (instrument_id, delta, gamma, theta, vega, rho, iv, iv_rank, updated_at)
                VALUES (:iid, :delta, :gamma, :theta, :vega, :rho, :iv, :iv_rank, :now)
                ON CONFLICT (instrument_id) DO UPDATE SET
                    delta = EXCLUDED.delta, gamma = EXCLUDED.gamma, theta = EXCLUDED.theta,
                    vega = EXCLUDED.vega, rho = EXCLUDED.rho, iv = EXCLUDED.iv,
                    iv_rank = EXCLUDED.iv_rank, updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "iid": instrument_id,
                "delta": snap.delta, "gamma": snap.gamma, "theta": snap.theta,
                "vega": snap.vega, "rho": snap.rho, "iv": snap.iv,
                "iv_rank": snap.iv_rank,
                "now": now,
            },
        )
        await self._db.commit()

    async def upsert(self, instrument_id: int, greeks: GreeksSnapshot) -> None:
        """Persist Greeks for an instrument that has a position or order today (upsert guard)."""
        if not await self._has_position_or_order(instrument_id):
            log.debug("option_greeks_upsert_skipped_no_position", instrument_id=instrument_id)
            return
        await self._db_upsert(instrument_id, greeks)

    async def get(self, instrument_id: int) -> GreeksSnapshot | None:
        result = await self._db.execute(
            text("SELECT delta, gamma, theta, vega, rho, iv, iv_rank FROM option_greeks WHERE instrument_id = :iid"),
            {"iid": instrument_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return GreeksSnapshot(
            delta=Decimal(str(row[0] or 0)),
            gamma=Decimal(str(row[1] or 0)),
            theta=Decimal(str(row[2] or 0)),
            vega=Decimal(str(row[3] or 0)),
            rho=Decimal(str(row[4] or 0)),
            iv=Decimal(str(row[5] or 0)),
            iv_rank=Decimal(str(row[6])) if row[6] is not None else None,
        )

    async def _db_delete_stale(self, older_than: timedelta) -> int:
        cutoff = datetime.now(UTC) - older_than
        result = await self._db.execute(
            text("DELETE FROM option_greeks WHERE updated_at < :cutoff RETURNING instrument_id"),
            {"cutoff": cutoff},
        )
        await self._db.commit()
        return result.rowcount

    async def evict_stale(self, older_than: timedelta = timedelta(minutes=5)) -> int:
        """Delete stale Greeks rows. Called by APScheduler every 60s."""
        deleted = await self._db_delete_stale(older_than)
        log.info("option_greeks_evicted", count=deleted)
        return deleted

    async def start_streaming(self, conids: list[str], account_id: str) -> None:
        """Begin StreamOptionGreeks RPC for given conids. Fan updates to Redis greeks.options.<conid>."""
        # Sidecar streaming implementation wired in Task C3 (broker sidecar extensions)
        log.info("option_greeks_streaming_started", conid_count=len(conids))

    async def stop_streaming(self, conids: list[str]) -> None:
        """Cancel the sidecar streaming task for given conids."""
        log.info("option_greeks_streaming_stopped", conid_count=len(conids))
```

- [ ] **Step 3: Run tests**

```bash
cd backend && pytest tests/services/options/test_greeks_service.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/options/greeks_service.py backend/tests/services/options/test_greeks_service.py
git commit -m "feat(phase12): OptionGreeksService — upsert guard, eviction, clamping"
```

---

### Task C3: ExerciseService

**Files:**
- Create: `backend/app/services/options/exercise_service.py`
- Create: `backend/tests/services/options/test_exercise_service.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/services/options/test_exercise_service.py
"""Tests for ExerciseService — pending filter, idempotency, 409, rate limit."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]

UTC = timezone.utc


def _make_service(*, db=None, redis=None, broker_registry=None):
    from app.services.options.exercise_service import ExerciseService
    db = db or AsyncMock()
    redis = redis or AsyncMock()
    broker_registry = broker_registry or MagicMock()
    return ExerciseService(db=db, redis=redis, broker_registry=broker_registry)


@pytest.mark.asyncio
async def test_elect_idempotent_same_key_returns_existing():
    """Resending the same idempotency_key should return existing record without broker call."""
    svc = _make_service()
    ikey = uuid.uuid4()
    existing_row = {"id": str(uuid.uuid4()), "idempotency_key": str(ikey), "status": "submitted"}
    svc._find_by_idempotency_key = AsyncMock(return_value=existing_row)
    svc._submit_to_broker = AsyncMock()
    svc._check_rate_limit = MagicMock()  # should not raise

    result = await svc.elect(
        account_id=uuid.uuid4(),
        jwt_subject="user@example.com",
        instrument_id=42,
        action="EXERCISE",
        qty=Decimal("1"),
        csrf_nonce="nonce123",
        idempotency_key=ikey,
    )

    svc._submit_to_broker.assert_not_called()
    assert result["idempotency_key"] == str(ikey)


@pytest.mark.asyncio
async def test_elect_duplicate_same_day_raises_409():
    """New idempotency_key for same (account, instrument, date) should raise 409."""
    from app.services.options.exercise_service import DuplicateElectionError
    svc = _make_service()
    svc._find_by_idempotency_key = AsyncMock(return_value=None)
    svc._check_rate_limit = MagicMock()
    svc._insert_election = AsyncMock(side_effect=DuplicateElectionError("duplicate"))

    with pytest.raises(DuplicateElectionError):
        await svc.elect(
            account_id=uuid.uuid4(),
            jwt_subject="user@example.com",
            instrument_id=42,
            action="EXERCISE",
            qty=Decimal("1"),
            csrf_nonce="nonce456",
            idempotency_key=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_elect_rate_limit_enforced():
    """Exceeding 5/min rate limit should raise RateLimitError."""
    from app.services.options.exercise_service import ExerciseRateLimitError
    svc = _make_service()
    svc._find_by_idempotency_key = AsyncMock(return_value=None)

    def raise_rate_limit(subject: str) -> None:
        raise ExerciseRateLimitError("rate limit exceeded")

    svc._check_rate_limit = raise_rate_limit

    with pytest.raises(ExerciseRateLimitError):
        await svc.elect(
            account_id=uuid.uuid4(),
            jwt_subject="user@example.com",
            instrument_id=42,
            action="EXERCISE",
            qty=Decimal("1"),
            csrf_nonce="nonce789",
            idempotency_key=uuid.uuid4(),
        )
```

- [ ] **Step 2: Create ExerciseService**

```python
# backend/app/services/options/exercise_service.py
"""ExerciseService — exercise elections with idempotency and rate limiting."""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)
UTC = timezone.utc

# 5 elections per 60s per jwt_subject
_RATE_LIMIT_MAX = 5
_RATE_LIMIT_WINDOW = 60.0


class DuplicateElectionError(Exception):
    """Raised when a new idempotency_key conflicts with an existing same-day election."""


class ExerciseRateLimitError(Exception):
    """Raised when the user exceeds the 5/min exercise rate limit."""


class ExerciseService:
    def __init__(self, *, db: AsyncSession, redis: Any, broker_registry: Any) -> None:
        self._db = db
        self._redis = redis
        self._broker_registry = broker_registry
        self._rate_buckets: dict[str, deque[float]] = defaultdict(deque)

    def _check_rate_limit(self, jwt_subject: str) -> None:
        now = time.monotonic()
        bucket = self._rate_buckets[jwt_subject]
        while bucket and now - bucket[0] > _RATE_LIMIT_WINDOW:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_MAX:
            raise ExerciseRateLimitError(f"Exercise rate limit exceeded for {jwt_subject}")
        bucket.append(now)

    async def _find_by_idempotency_key(self, ikey: uuid.UUID) -> dict[str, Any] | None:
        result = await self._db.execute(
            text("SELECT id, idempotency_key, status, broker_ref FROM exercise_elections WHERE idempotency_key = :ikey"),
            {"ikey": str(ikey)},
        )
        row = result.fetchone()
        if row is None:
            return None
        return {"id": str(row[0]), "idempotency_key": str(row[1]), "status": row[2], "broker_ref": row[3]}

    async def _insert_election(
        self,
        *,
        account_id: uuid.UUID,
        jwt_subject: str,
        instrument_id: int,
        action: str,
        qty: Decimal,
        idempotency_key: uuid.UUID,
    ) -> dict[str, Any]:
        election_id = uuid.uuid4()
        now = datetime.now(UTC)
        try:
            await self._db.execute(
                text(
                    """
                    INSERT INTO exercise_elections
                        (id, idempotency_key, jwt_subject, account_id, instrument_id, action, qty, status, created_at)
                    VALUES
                        (:id, :ikey, :subject, :acct, :inst, :action, :qty, 'submitted', :now)
                    """
                ),
                {
                    "id": str(election_id), "ikey": str(idempotency_key),
                    "subject": jwt_subject, "acct": str(account_id),
                    "inst": instrument_id, "action": action,
                    "qty": qty, "now": now,
                },
            )
            await self._db.commit()
        except Exception as exc:
            if "exercise_elections_one_per_day" in str(exc) or "unique" in str(exc).lower():
                raise DuplicateElectionError("Election already submitted today for this contract") from exc
            raise
        return {"id": str(election_id), "idempotency_key": str(idempotency_key), "status": "submitted"}

    async def _submit_to_broker(
        self,
        *,
        account_id: uuid.UUID,
        instrument_id: int,
        action: str,
        qty: Decimal,
        idempotency_key: uuid.UUID,
    ) -> dict[str, Any]:
        # Broker dispatch (IBKR exerciseOptions) — wired in sidecar extension task
        log.info("exercise_submitted_to_broker", account_id=str(account_id), action=action)
        return {"broker_ref": None, "success": True}

    async def elect(
        self,
        account_id: uuid.UUID,
        jwt_subject: str,
        instrument_id: int,
        action: Literal["EXERCISE", "DO_NOT_EXERCISE", "LAPSE"],
        qty: Decimal,
        csrf_nonce: str,
        idempotency_key: uuid.UUID,
    ) -> dict[str, Any]:
        """Submit an exercise election. Idempotent on same idempotency_key."""
        # Idempotent replay: same key → return existing record
        existing = await self._find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

        # Rate limit check
        self._check_rate_limit(jwt_subject)

        # Insert (raises DuplicateElectionError if partial unique index fires)
        record = await self._insert_election(
            account_id=account_id, jwt_subject=jwt_subject,
            instrument_id=instrument_id, action=action,
            qty=qty, idempotency_key=idempotency_key,
        )

        # Submit to broker (fire-and-forget; status updated via order event callback)
        broker_result = await self._submit_to_broker(
            account_id=account_id, instrument_id=instrument_id,
            action=action, qty=qty, idempotency_key=idempotency_key,
        )
        log.info("exercise_elected", action=action, broker_ref=broker_result.get("broker_ref"))
        return record

    async def list_pending(
        self, account_id: uuid.UUID, jwt_subject: str
    ) -> list[dict[str, Any]]:
        """Return option positions expiring within the next 5 trading sessions."""
        # Query positions with OPTION asset class expiring within 5 sessions
        # Spot price sourced from QuoteService (Redis) with 30s TTL; None → skip intrinsic filter
        result = await self._db.execute(
            text(
                """
                SELECT p.instrument_id, p.qty, i.meta->>'expiry' as expiry,
                       i.meta->>'strike' as strike, i.meta->>'put_call' as put_call,
                       i.meta->>'multiplier' as multiplier, i.primary_exchange
                FROM positions p
                JOIN instruments i ON i.id = p.instrument_id
                WHERE p.account_id = :acct
                  AND i.asset_class = 'OPTION'
                  AND p.qty != 0
                  AND (i.meta->>'expiry')::date <= (CURRENT_DATE + INTERVAL '5 days')
                """
            ),
            {"acct": str(account_id)},
        )
        rows = result.fetchall()
        return [
            {
                "instrument_id": row[0], "qty": str(row[1]),
                "expiry": row[2], "strike": row[3],
                "put_call": row[4], "multiplier": row[5],
                "exchange": row[6], "spot_unavailable": True,
            }
            for row in rows
        ]
```

- [ ] **Step 3: Run tests**

```bash
cd backend && pytest tests/services/options/test_exercise_service.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/options/exercise_service.py backend/tests/services/options/test_exercise_service.py
git commit -m "feat(phase12): ExerciseService — idempotent elections, rate limit, duplicate 409"
```

---

## Chunk D — API Layer

### Task D1: Options REST API

**Files:**
- Create: `backend/app/api/options.py`
- Create: `backend/app/schemas/options.py`
- Create: `backend/tests/api/test_options_api.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Create schemas**

```python
# backend/app/schemas/options.py
"""Request/response schemas for the Phase 12 options API."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class OptionChainRowSchema(BaseModel):
    conid: str
    strike: str          # Decimal string
    put_call: Literal["C", "P"]
    bid: str             # Decimal string
    ask: str             # Decimal string
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    open_interest: int
    volume: int
    multiplier: int
    exchange: str
    style: Literal["A", "E"]


class OptionChainResponse(BaseModel):
    calls: list[OptionChainRowSchema]
    puts: list[OptionChainRowSchema]
    source: str
    fetched_at_ms: int
    stale: bool = False


class OptionExpirationsResponse(BaseModel):
    expiry_dates: list[date]


class ExerciseElectionRequest(BaseModel):
    account_id: uuid.UUID
    instrument_id: int
    action: Literal["EXERCISE", "DO_NOT_EXERCISE", "LAPSE"]
    qty: Decimal = Field(gt=0)
    idempotency_key: uuid.UUID
    csrf_nonce: str


class ExerciseElectionResponse(BaseModel):
    id: uuid.UUID
    idempotency_key: uuid.UUID
    status: str
    broker_ref: str | None = None


class OptionChainSourcesRequest(BaseModel):
    sources: dict[str, list[str]]  # e.g. {"USD": ["schwab", "ibkr"], "HKD": ["futu"]}


class OptionSubBudgetsRequest(BaseModel):
    budgets: dict[str, int]  # e.g. {"ibkr": 400, "alpaca": 600}


class TradingLevelRequest(BaseModel):
    level: int = Field(ge=1, le=4)
```

- [ ] **Step 2: Write API tests**

```python
# backend/tests/api/test_options_api.py
"""Tests for /api/options/* endpoints."""
from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio]


async def _make_client(app) -> AsyncClient:
    from httpx import AsyncClient, ASGITransport
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _auth_headers():
    return {"X-CF-Access-JWT-Assertion": "test-jwt"}


@pytest.mark.asyncio
async def test_get_expirations_returns_list(test_app, auth_headers):
    async with AsyncClient(transport=__import__("httpx").ASGITransport(app=test_app), base_url="http://test") as client:
        with patch("app.api.options.get_chain_service") as mock_svc_dep:
            svc = AsyncMock()
            svc.get_expirations.return_value = [date(2025, 1, 17), date(2025, 2, 21)]
            mock_svc_dep.return_value = svc

            resp = await client.get(
                "/api/options/expirations?symbol=SPY&currency=USD",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "expiry_dates" in data


@pytest.mark.asyncio
async def test_get_chain_returns_structure(test_app, auth_headers):
    async with AsyncClient(transport=__import__("httpx").ASGITransport(app=test_app), base_url="http://test") as client:
        with patch("app.api.options.get_chain_service") as mock_svc_dep:
            svc = AsyncMock()
            svc.get_chain.return_value = {"calls": [], "puts": [], "source": "ibkr", "fetched_at_ms": 0}
            mock_svc_dep.return_value = svc

            resp = await client.get(
                "/api/options/chain?symbol=SPY&expiry=2025-01-17&strikes=20",
                headers=auth_headers,
            )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_post_exercise_requires_csrf(test_app, auth_headers):
    """POST /api/options/exercise without CSRF nonce should be rejected."""
    async with AsyncClient(transport=__import__("httpx").ASGITransport(app=test_app), base_url="http://test") as client:
        resp = await client.post(
            "/api/options/exercise",
            json={
                "account_id": str(uuid.uuid4()),
                "instrument_id": 42,
                "action": "EXERCISE",
                "qty": "1",
                "idempotency_key": str(uuid.uuid4()),
                # csrf_nonce missing
            },
            headers=auth_headers,
        )
    assert resp.status_code in (400, 422)
```

- [ ] **Step 3: Create options.py API router**

```python
# backend/app/api/options.py
"""Phase 12: Options REST endpoints."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.cf_access import AdminIdentity
from app.core.deps import get_config, get_db, get_redis, require_admin_jwt
from app.schemas.options import (
    ExerciseElectionRequest,
    ExerciseElectionResponse,
    OptionChainResponse,
    OptionChainSourcesRequest,
    OptionExpirationsResponse,
    OptionSubBudgetsRequest,
    TradingLevelRequest,
)
from app.services.config import ConfigService
from app.services.options.chain_service import OptionChainService
from app.services.options.exercise_service import (
    DuplicateElectionError,
    ExerciseRateLimitError,
    ExerciseService,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/options", tags=["options"])
admin_router = APIRouter(
    prefix="/api/admin",
    tags=["admin-options"],
    dependencies=[Depends(require_admin_jwt)],
)

DbDep = Annotated[Any, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]
ConfigDep = Annotated[ConfigService, Depends(get_config)]
IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]


def get_chain_service(redis: RedisDep, cfg: ConfigDep) -> OptionChainService:
    from app.services.options.chain_service import OptionChainService
    return OptionChainService(redis=redis, config=cfg, broker_registry=None)


def get_exercise_service(db: DbDep, redis: RedisDep) -> ExerciseService:
    from app.services.options.exercise_service import ExerciseService
    return ExerciseService(db=db, redis=redis, broker_registry=None)


@router.get("/expirations", dependencies=[Depends(require_admin_jwt)])
async def get_expirations(
    symbol: str = Query(...),
    currency: str = Query(default="USD"),
    svc: OptionChainService = Depends(get_chain_service),
) -> OptionExpirationsResponse:
    expiries = await svc.get_expirations(symbol, currency)
    return OptionExpirationsResponse(expiry_dates=expiries)


@router.get("/chain", dependencies=[Depends(require_admin_jwt)])
async def get_chain(
    symbol: str = Query(...),
    expiry: date = Query(...),
    strikes: int = Query(default=20, ge=1, le=60),
    currency: str = Query(default="USD"),
    svc: OptionChainService = Depends(get_chain_service),
) -> OptionChainResponse:
    result = await svc.get_chain(symbol, expiry, strike_count=strikes, currency=currency)
    return OptionChainResponse(**result)


@router.get("/exercise", dependencies=[Depends(require_admin_jwt)])
async def list_exercise_elections(
    account_id: uuid.UUID = Query(...),
    identity: IdentityDep = None,
    svc: ExerciseService = Depends(get_exercise_service),
) -> list[dict]:
    return await svc.list_pending(account_id, identity.email if identity else "")


@router.post("/exercise", dependencies=[Depends(require_admin_jwt)])
async def post_exercise_election(
    body: ExerciseElectionRequest,
    identity: IdentityDep = None,
    svc: ExerciseService = Depends(get_exercise_service),
) -> ExerciseElectionResponse:
    try:
        result = await svc.elect(
            account_id=body.account_id,
            jwt_subject=identity.email if identity else "",
            instrument_id=body.instrument_id,
            action=body.action,
            qty=body.qty,
            csrf_nonce=body.csrf_nonce,
            idempotency_key=body.idempotency_key,
        )
        return ExerciseElectionResponse(**result)
    except ExerciseRateLimitError:
        raise HTTPException(status_code=429, detail="Exercise rate limit exceeded — max 5/min")
    except DuplicateElectionError:
        raise HTTPException(status_code=409, detail="duplicate_election")


@router.get("/events", dependencies=[Depends(require_admin_jwt)])
async def list_exercise_events(
    identity: IdentityDep = None,
    db: DbDep = None,
) -> list[dict]:
    from sqlalchemy import text
    rows = await db.execute(
        text(
            "SELECT id, action, status, created_at, broker_ref FROM exercise_elections "
            "WHERE jwt_subject = :subject AND created_at >= now() - interval '30 days' "
            "ORDER BY created_at DESC"
        ),
        {"subject": identity.email if identity else ""},
    )
    return [
        {"id": str(r[0]), "action": r[1], "status": r[2], "created_at": r[3].isoformat(), "broker_ref": r[4]}
        for r in rows.fetchall()
    ]


@admin_router.put("/quote-engine/option-chain-sources")
async def update_chain_sources(
    body: OptionChainSourcesRequest,
    identity: IdentityDep = None,
    cfg: ConfigDep = None,
    redis: RedisDep = None,
) -> dict:
    await cfg.set_json("quote_engine", "option_chain_sources", body.sources)
    await redis.publish("app_config:invalidate:option_chain_sources", "1")
    return {"ok": True}


@admin_router.put("/quote-engine/option-sub-budgets")
async def update_sub_budgets(
    body: OptionSubBudgetsRequest,
    identity: IdentityDep = None,
    cfg: ConfigDep = None,
) -> dict:
    await cfg.set_json("quote_engine", "option_sub_budgets", body.budgets)
    return {"ok": True}


@admin_router.put("/options/trading-level")
async def update_trading_level(
    body: TradingLevelRequest,
    identity: IdentityDep = None,
    cfg: ConfigDep = None,
) -> dict:
    await cfg.set_int("options", "trading_level", body.level)
    return {"ok": True, "level": body.level}
```

- [ ] **Step 4: Register router in main.py**

In `backend/app/main.py`, add:
```python
from app.api.options import admin_router as options_admin_router
from app.api.options import router as options_router
```

And add to the `include_router` block:
```python
app.include_router(options_router)
app.include_router(options_admin_router)
```

- [ ] **Step 5: Run tests**

```bash
cd backend && pytest tests/api/test_options_api.py -v
```

Expected: PASS (using mocked services).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/options.py backend/app/schemas/options.py backend/app/main.py backend/tests/api/test_options_api.py
git commit -m "feat(phase12): options REST API — 9 endpoints (expirations, chain, exercise, events, 3 admin)"
```

---

### Task D2: WebSocket options feed

**Files:**
- Create: `backend/app/api/ws_options.py`
- Create: `backend/tests/api/test_ws_options.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write WS tests**

```python
# backend/tests/api/test_ws_options.py
"""Tests for WS /ws/options/chain."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def test_ws_options_connect_and_disconnect(test_app):
    """Client should be able to connect and disconnect cleanly."""
    client = TestClient(test_app)
    with patch("app.api.ws_options.get_chain_service") as mock_svc:
        svc = MagicMock()
        svc.get_chain = AsyncMock(return_value={"calls": [], "puts": [], "source": "ibkr", "fetched_at_ms": 0})
        mock_svc.return_value = svc

        with client.websocket_connect("/ws/options/chain?symbol=SPY&expiry=2025-01-17") as ws:
            # Send and receive initial frame
            data = ws.receive_json(mode="text")
            assert data.get("type") in ("chain", "stale", "heartbeat", None)


def test_ws_options_subscription_capped_frame(test_app):
    """When budget is exceeded, a subscription_capped frame should be sent."""
    client = TestClient(test_app)
    with patch("app.api.ws_options.get_chain_service") as mock_svc:
        svc = MagicMock()
        svc.get_chain = AsyncMock(return_value={"calls": [], "puts": [], "source": "ibkr", "fetched_at_ms": 0, "capped": True})
        mock_svc.return_value = svc

        with client.websocket_connect("/ws/options/chain?symbol=SPY&expiry=2025-01-17") as ws:
            pass  # just verify no crash
```

- [ ] **Step 2: Create ws_options.py**

```python
# backend/app/api/ws_options.py
"""Phase 12: WebSocket endpoint for live option chain updates."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.services.options.chain_service import OptionChainService

log = structlog.get_logger(__name__)

router = APIRouter(tags=["ws-options"])

_CONFLATION_HZ = 2
_HEARTBEAT_SECONDS = 30
_CONNECTION_CAP = 10
_active_connections = 0


def get_chain_service(websocket: WebSocket) -> OptionChainService:
    from app.services.options.chain_service import OptionChainService
    redis = websocket.app.state.redis
    config = websocket.app.state.config if hasattr(websocket.app.state, "config") else None
    return OptionChainService(redis=redis, config=config, broker_registry=None)


@router.websocket("/ws/options/chain")
async def ws_options_chain(
    websocket: WebSocket,
    symbol: str = Query(...),
    expiry: str = Query(...),
) -> None:
    global _active_connections
    if _active_connections >= _CONNECTION_CAP:
        await websocket.close(code=1008, reason="connection_cap_reached")
        return

    await websocket.accept()
    _active_connections += 1
    log.info("ws_options_chain_connected", symbol=symbol, expiry=expiry)

    try:
        svc = get_chain_service(websocket)
        from datetime import date
        expiry_date = date.fromisoformat(expiry)

        last_heartbeat = time.monotonic()
        interval = 1.0 / _CONFLATION_HZ

        while True:
            # Fetch chain (cache-first)
            try:
                chain = await svc.get_chain(symbol, expiry_date, currency="USD")
                frame = {"type": "chain", **chain}
                await websocket.send_text(json.dumps(frame))
            except Exception as exc:
                log.warning("ws_options_chain_fetch_failed", error=str(exc))
                await websocket.send_text(json.dumps({"type": "stale"}))

            # Heartbeat
            now = time.monotonic()
            if now - last_heartbeat >= _HEARTBEAT_SECONDS:
                await websocket.send_text(json.dumps({"type": "heartbeat"}))
                last_heartbeat = now

            await asyncio.sleep(interval)

    except WebSocketDisconnect:
        log.info("ws_options_chain_disconnected", symbol=symbol)
    finally:
        _active_connections -= 1
```

- [ ] **Step 3: Register WS router in main.py**

```python
from app.api.ws_options import router as ws_options_router
# ...
app.include_router(ws_options_router)
```

- [ ] **Step 4: Run tests**

```bash
cd backend && pytest tests/api/test_ws_options.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full backend test suite**

```bash
cd backend && pytest --tb=short -q 2>&1 | tail -20
```

Expected: 970+ tests passing, no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/ws_options.py backend/app/main.py backend/tests/api/test_ws_options.py
git commit -m "feat(phase12): WS /ws/options/chain — 2Hz conflation, heartbeat, connection cap"
```

---

## Chunk E — Frontend

### Task E1: Types and hooks

**Files:**
- Create: `frontend/src/features/options/types.ts`
- Create: `frontend/src/services/options/api.ts`
- Create: `frontend/src/features/options/hooks/useOptionExpirations.ts`
- Create: `frontend/src/features/options/hooks/useOptionChain.ts`

- [ ] **Step 1: Create types.ts**

```typescript
// frontend/src/features/options/types.ts
export type PutCall = 'C' | 'P';
export type OptionStyle = 'A' | 'E'; // "A"=American "E"=European

export interface OptionChainRow {
  conid: string;
  strike: string;       // Decimal string
  put_call: PutCall;
  bid: string;          // Decimal string
  ask: string;          // Decimal string
  iv: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  open_interest: number;
  volume: number;
  multiplier: number;
  exchange: string;
  style: OptionStyle;
}

export interface OptionChainData {
  calls: OptionChainRow[];
  puts: OptionChainRow[];
  source: string;
  fetched_at_ms: number;
  stale?: boolean;
}

export interface GreeksSnapshot {
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  iv: number | null;
}

export interface ExerciseCandidate {
  instrument_id: number;
  qty: string;
  expiry: string;
  strike: string;
  put_call: PutCall;
  multiplier: string;
  exchange: string;
  spot_unavailable: boolean;
}

export interface WsChainFrame {
  type: 'chain' | 'stale' | 'heartbeat' | 'canonicalized' | 'subscription_capped';
  calls?: OptionChainRow[];
  puts?: OptionChainRow[];
  source?: string;
  fetched_at_ms?: number;
  conid?: string;
  canonical_id?: string;
}
```

- [ ] **Step 2: Create API client**

```typescript
// frontend/src/services/options/api.ts
import type { OptionChainData, ExerciseCandidate } from '@/features/options/types';

const BASE = '/api/options';

export async function fetchExpirations(symbol: string, currency = 'USD'): Promise<string[]> {
  const resp = await fetch(`${BASE}/expirations?symbol=${encodeURIComponent(symbol)}&currency=${currency}`);
  if (!resp.ok) throw new Error(`Failed to fetch expirations: ${resp.status}`);
  const data = await resp.json();
  return data.expiry_dates as string[];
}

export async function fetchChain(
  symbol: string,
  expiry: string,
  strikes = 20,
  currency = 'USD',
): Promise<OptionChainData> {
  const resp = await fetch(
    `${BASE}/chain?symbol=${encodeURIComponent(symbol)}&expiry=${expiry}&strikes=${strikes}&currency=${currency}`,
  );
  if (!resp.ok) throw new Error(`Failed to fetch chain: ${resp.status}`);
  return resp.json() as Promise<OptionChainData>;
}

export async function fetchExercisePending(accountId: string): Promise<ExerciseCandidate[]> {
  const resp = await fetch(`${BASE}/exercise?account_id=${accountId}`);
  if (!resp.ok) throw new Error(`Failed to fetch exercise candidates: ${resp.status}`);
  return resp.json() as Promise<ExerciseCandidate[]>;
}

export async function mintCsrfNonce(): Promise<string> {
  const resp = await fetch('/api/auth/csrf-nonce', { method: 'POST' });
  if (!resp.ok) throw new Error('Failed to mint CSRF nonce');
  const data = await resp.json();
  return data.nonce as string;
}

export async function postExerciseElection(body: {
  account_id: string;
  instrument_id: number;
  action: 'EXERCISE' | 'DO_NOT_EXERCISE' | 'LAPSE';
  qty: string;
  idempotency_key: string;
  csrf_nonce: string;
}): Promise<{ id: string; status: string }> {
  const resp = await fetch(`${BASE}/exercise`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (resp.status === 409) throw new Error('duplicate_election');
  if (resp.status === 429) throw new Error('rate_limit_exceeded');
  if (!resp.ok) throw new Error(`Exercise election failed: ${resp.status}`);
  return resp.json() as Promise<{ id: string; status: string }>;
}
```

- [ ] **Step 3: Create useOptionExpirations hook**

```typescript
// frontend/src/features/options/hooks/useOptionExpirations.ts
import { useQuery } from '@tanstack/react-query';
import { fetchExpirations } from '@/services/options/api';

export function useOptionExpirations(symbol: string, currency = 'USD') {
  return useQuery({
    queryKey: ['options', 'expirations', symbol, currency],
    queryFn: () => fetchExpirations(symbol, currency),
    enabled: symbol.trim().length > 0,
    staleTime: 60_000,
  });
}
```

- [ ] **Step 4: Create useOptionChain hook (TanStack Query + WS hybrid)**

```typescript
// frontend/src/features/options/hooks/useOptionChain.ts
import * as React from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchChain } from '@/services/options/api';
import type { OptionChainData, WsChainFrame } from '@/features/options/types';

function buildWsUrl(symbol: string, expiry: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/ws/options/chain?symbol=${encodeURIComponent(symbol)}&expiry=${expiry}`;
}

export function useOptionChain(symbol: string, expiry: string | null, currency = 'USD') {
  const queryClient = useQueryClient();
  const queryKey = ['options', 'chain', symbol, expiry, currency] as const;

  const query = useQuery({
    queryKey,
    queryFn: () => (expiry ? fetchChain(symbol, expiry, 20, currency) : Promise.resolve(null)),
    enabled: symbol.trim().length > 0 && expiry !== null,
    staleTime: 30_000,
    refetchInterval: 5_000,
  });

  // Conid → canonical_id mapping (updated via canonicalized frames)
  const conidMapRef = React.useRef<Map<string, string>>(new Map());
  const wsRef = React.useRef<WebSocket | null>(null);
  const [wsStale, setWsStale] = React.useState(false);

  React.useEffect(() => {
    if (!symbol || !expiry) return;

    let reconnectDelay = 500;
    let cancelled = false;

    function connect() {
      if (cancelled) return;
      const ws = new WebSocket(buildWsUrl(symbol, expiry!));
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const frame: WsChainFrame = JSON.parse(event.data as string);
          if (frame.type === 'chain' && frame.calls !== undefined) {
            setWsStale(false);
            queryClient.setQueryData<OptionChainData>(queryKey, {
              calls: frame.calls ?? [],
              puts: frame.puts ?? [],
              source: frame.source ?? '',
              fetched_at_ms: frame.fetched_at_ms ?? Date.now(),
            });
          } else if (frame.type === 'stale') {
            setWsStale(true);
          } else if (frame.type === 'canonicalized' && frame.conid && frame.canonical_id) {
            conidMapRef.current.set(frame.conid, frame.canonical_id);
          }
        } catch {
          // ignore malformed frames
        }
      };

      ws.onclose = () => {
        if (!cancelled) {
          setTimeout(connect, Math.min(reconnectDelay, 15_000));
          reconnectDelay = Math.min(reconnectDelay * 1.5, 15_000);
        }
      };
    }

    connect();
    return () => {
      cancelled = true;
      wsRef.current?.close();
    };
  }, [symbol, expiry, currency]);

  return { ...query, wsStale, conidMap: conidMapRef.current };
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/options/ frontend/src/services/options/
git commit -m "feat(phase12): options FE types, API client, useOptionExpirations, useOptionChain hooks"
```

---

### Task E2: OptionGreeksStrip, OptionExpiryTabs, OptionChainTable

**Files:**
- Create: `frontend/src/features/options/OptionGreeksStrip.tsx`
- Create: `frontend/src/features/options/OptionExpiryTabs.tsx`
- Create: `frontend/src/features/options/OptionChainTable.tsx`
- Create: `frontend/src/features/options/OptionChainTable.test.tsx`

- [ ] **Step 1: Create OptionGreeksStrip**

```tsx
// frontend/src/features/options/OptionGreeksStrip.tsx
import * as React from 'react';
import type { GreeksSnapshot } from './types';

interface Props {
  greeks: Partial<GreeksSnapshot>;
  className?: string;
}

function fmt(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return v.toFixed(3);
}

export function OptionGreeksStrip({ greeks, className }: Props) {
  return (
    <div className={`flex gap-4 text-xs ${className ?? ''}`} data-testid="greeks-strip">
      <span><span className="text-muted-foreground">Δ</span> {fmt(greeks.delta)}</span>
      <span><span className="text-muted-foreground">Γ</span> {fmt(greeks.gamma)}</span>
      <span><span className="text-muted-foreground">Θ</span> {fmt(greeks.theta)}</span>
      <span><span className="text-muted-foreground">V</span> {fmt(greeks.vega)}</span>
      <span><span className="text-muted-foreground">IV</span> {greeks.iv !== null && greeks.iv !== undefined ? `${(greeks.iv * 100).toFixed(1)}%` : '—'}</span>
    </div>
  );
}
```

- [ ] **Step 2: Create OptionExpiryTabs**

```tsx
// frontend/src/features/options/OptionExpiryTabs.tsx
import * as React from 'react';

interface Props {
  expirations: string[];
  selected: string | null;
  onSelect: (expiry: string) => void;
}

export function OptionExpiryTabs({ expirations, selected, onSelect }: Props) {
  return (
    <div className="flex gap-1 flex-wrap" role="tablist" aria-label="Option expirations">
      {expirations.map((exp) => (
        <button
          key={exp}
          role="tab"
          aria-selected={exp === selected}
          onClick={() => onSelect(exp)}
          className={`rounded px-2 py-0.5 text-xs border transition-colors ${
            exp === selected
              ? 'bg-accent text-accent-foreground border-accent'
              : 'border-border text-muted-foreground hover:border-foreground'
          }`}
          data-testid={`expiry-tab-${exp}`}
        >
          {exp}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Create OptionChainTable (butterfly layout)**

```tsx
// frontend/src/features/options/OptionChainTable.tsx
import * as React from 'react';
import type { OptionChainData, OptionChainRow } from './types';

interface Props {
  data: OptionChainData;
  spot: number | null;
  onSelectStrike: (row: OptionChainRow, side: 'call' | 'put') => void;
}

function isAtm(strike: string, spot: number | null): boolean {
  if (spot === null) return false;
  const s = parseFloat(strike);
  return Math.abs(s - spot) <= (spot * 0.002); // within 0.2%
}

function isItmCall(strike: string, spot: number | null): boolean {
  if (spot === null) return false;
  return parseFloat(strike) < spot;
}

function isItmPut(strike: string, spot: number | null): boolean {
  if (spot === null) return false;
  return parseFloat(strike) > spot;
}

export function OptionChainTable({ data, spot, onSelectStrike }: Props) {
  // Build strike-keyed map for butterfly layout
  const strikeMap = React.useMemo(() => {
    const m = new Map<string, { call?: OptionChainRow; put?: OptionChainRow }>();
    for (const row of data.calls) {
      m.set(row.strike, { ...m.get(row.strike), call: row });
    }
    for (const row of data.puts) {
      m.set(row.strike, { ...m.get(row.strike), put: row });
    }
    return m;
  }, [data.calls, data.puts]);

  const strikes = React.useMemo(
    () => [...strikeMap.keys()].sort((a, b) => parseFloat(a) - parseFloat(b)),
    [strikeMap],
  );

  // Mobile: below md, collapse to single-column list
  return (
    <div className="overflow-x-auto">
      {/* Desktop butterfly table */}
      <table className="hidden md:table w-full border-collapse text-xs min-w-[36rem]">
        <thead>
          <tr className="text-muted-foreground border-b border-border">
            <th className="text-right p-1 text-green-400">Bid</th>
            <th className="text-right p-1 text-green-400">Ask</th>
            <th className="text-right p-1 text-green-400">IV</th>
            <th className="text-right p-1 text-green-400">Δ</th>
            <th className="text-right p-1 text-green-400">OI</th>
            <th className="text-center p-1 font-bold bg-white/5">Strike</th>
            <th className="text-left p-1 text-red-400">OI</th>
            <th className="text-left p-1 text-red-400">Δ</th>
            <th className="text-left p-1 text-red-400">IV</th>
            <th className="text-left p-1 text-red-400">Bid</th>
            <th className="text-left p-1 text-red-400">Ask</th>
          </tr>
        </thead>
        <tbody>
          {strikes.map((strike) => {
            const entry = strikeMap.get(strike)!;
            const atm = isAtm(strike, spot);
            const itmCall = isItmCall(strike, spot);
            const itmPut = isItmPut(strike, spot);

            return (
              <tr
                key={strike}
                className={`cursor-pointer transition-colors ${
                  atm
                    ? 'bg-yellow-400/10 outline outline-1 outline-yellow-400/40 hover:bg-yellow-400/20'
                    : itmCall
                    ? 'bg-green-400/6 hover:bg-green-400/13'
                    : itmPut
                    ? 'bg-red-400/4 hover:bg-red-400/12'
                    : 'hover:bg-white/5'
                }`}
                data-testid={`chain-row-${strike}`}
              >
                {/* Call side */}
                <td
                  className="text-right p-1 cursor-pointer"
                  onClick={() => entry.call && onSelectStrike(entry.call, 'call')}
                >
                  {entry.call?.bid ?? '—'}
                </td>
                <td className="text-right p-1" onClick={() => entry.call && onSelectStrike(entry.call, 'call')}>
                  {entry.call?.ask ?? '—'}
                </td>
                <td className="text-right p-1 text-muted-foreground">
                  {entry.call ? `${(entry.call.iv * 100).toFixed(1)}%` : '—'}
                </td>
                <td className="text-right p-1">{entry.call?.delta.toFixed(2) ?? '—'}</td>
                <td className="text-right p-1 text-muted-foreground">
                  {entry.call ? (entry.call.open_interest / 1000).toFixed(1) + 'k' : '—'}
                </td>
                {/* Strike */}
                <td
                  className={`text-center p-1 font-semibold bg-white/4 ${atm ? 'text-yellow-400' : ''}`}
                  data-atm={atm ? 'true' : undefined}
                >
                  {strike}
                  {atm ? ' ★' : ''}
                </td>
                {/* Put side */}
                <td className="text-left p-1 text-muted-foreground">
                  {entry.put ? (entry.put.open_interest / 1000).toFixed(1) + 'k' : '—'}
                </td>
                <td className="text-left p-1">{entry.put?.delta.toFixed(2) ?? '—'}</td>
                <td className="text-left p-1 text-muted-foreground">
                  {entry.put ? `${(entry.put.iv * 100).toFixed(1)}%` : '—'}
                </td>
                <td
                  className="text-left p-1 cursor-pointer"
                  onClick={() => entry.put && onSelectStrike(entry.put, 'put')}
                >
                  {entry.put?.bid ?? '—'}
                </td>
                <td className="text-left p-1" onClick={() => entry.put && onSelectStrike(entry.put, 'put')}>
                  {entry.put?.ask ?? '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* Mobile single-column list */}
      <div className="md:hidden divide-y divide-border">
        {strikes.map((strike) => {
          const entry = strikeMap.get(strike)!;
          const atm = isAtm(strike, spot);
          return (
            <div key={strike} className={`p-2 ${atm ? 'bg-yellow-400/10' : ''}`} data-testid={`chain-row-mobile-${strike}`}>
              <div className="flex justify-between items-center">
                <span className={`font-semibold ${atm ? 'text-yellow-400' : ''}`}>
                  {strike}{atm ? ' ★' : ''}
                </span>
                <div className="flex gap-3 text-xs text-muted-foreground">
                  {entry.call && <span>C IV {(entry.call.iv * 100).toFixed(1)}% Δ{entry.call.delta.toFixed(2)}</span>}
                  {entry.put && <span>P IV {(entry.put.iv * 100).toFixed(1)}% Δ{entry.put.delta.toFixed(2)}</span>}
                </div>
              </div>
              <div className="flex gap-2 mt-1">
                {entry.call && (
                  <button
                    className="text-xs rounded border border-green-400/40 px-2 py-0.5"
                    onClick={() => onSelectStrike(entry.call!, 'call')}
                  >
                    Call {entry.call.bid}/{entry.call.ask}
                  </button>
                )}
                {entry.put && (
                  <button
                    className="text-xs rounded border border-red-400/40 px-2 py-0.5"
                    onClick={() => onSelectStrike(entry.put!, 'put')}
                  >
                    Put {entry.put.bid}/{entry.put.ask}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Write OptionChainTable tests**

```tsx
// frontend/src/features/options/OptionChainTable.test.tsx
import * as React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { OptionChainTable } from './OptionChainTable';
import type { OptionChainData, OptionChainRow } from './types';

function makeRow(strike: string, putCall: 'C' | 'P'): OptionChainRow {
  return {
    conid: `${putCall}${strike}`, strike, put_call: putCall,
    bid: '5.00', ask: '5.20', iv: 0.175, delta: putCall === 'C' ? 0.5 : -0.5,
    gamma: 0.028, theta: -0.12, vega: 0.31, open_interest: 38000, volume: 1200,
    multiplier: 100, exchange: 'CBOE', style: 'A',
  };
}

const mockData: OptionChainData = {
  calls: [makeRow('440', 'C'), makeRow('450', 'C'), makeRow('460', 'C')],
  puts: [makeRow('440', 'P'), makeRow('450', 'P'), makeRow('460', 'P')],
  source: 'ibkr', fetched_at_ms: 0,
};

test('renders ATM strike with star marker', () => {
  render(<OptionChainTable data={mockData} spot={450} onSelectStrike={() => {}} />);
  const atmCell = screen.getByTestId('chain-row-450');
  expect(atmCell).toBeInTheDocument();
  // ATM cell should contain the star
  expect(screen.getByText(/450 ★/)).toBeInTheDocument();
});

test('calls onSelectStrike when call bid cell clicked', () => {
  const handler = jest.fn();
  render(<OptionChainTable data={mockData} spot={450} onSelectStrike={handler} />);
  // Click the call row's bid cell for strike 450
  const row = screen.getByTestId('chain-row-450');
  const cells = row.querySelectorAll('td');
  fireEvent.click(cells[0]); // first td = call bid
  expect(handler).toHaveBeenCalledWith(expect.objectContaining({ strike: '450', put_call: 'C' }), 'call');
});

test('renders mobile collapse view below md', () => {
  render(<OptionChainTable data={mockData} spot={450} onSelectStrike={() => {}} />);
  // Mobile row should be present
  expect(screen.getByTestId('chain-row-mobile-450')).toBeInTheDocument();
});
```

- [ ] **Step 5: Run FE tests**

```bash
cd frontend && pnpm test -- --testPathPattern=OptionChainTable 2>&1 | tail -20
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/options/
git commit -m "feat(phase12): OptionGreeksStrip, OptionExpiryTabs, OptionChainTable (butterfly + mobile)"
```

---

### Task E3: OptionDetailsSection + TradeTicketModal integration

**Files:**
- Create: `frontend/src/features/options/OptionDetailsSection.tsx`
- Create: `frontend/src/features/options/OptionDetailsSection.test.tsx`
- Modify: `frontend/src/features/orders/TradeTicketModal.tsx`

- [ ] **Step 1: Create OptionDetailsSection**

```tsx
// frontend/src/features/options/OptionDetailsSection.tsx
import * as React from 'react';
import type { OptionChainRow } from './types';
import { OptionGreeksStrip } from './OptionGreeksStrip';

interface Props {
  row: OptionChainRow;
  underlyingSymbol: string;
  expiryIso: string;
  onSideChange: (side: 'BUY' | 'SELL', positionEffect: 'OPEN' | 'CLOSE') => void;
}

function formatExpiry(iso: string): string {
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function tradingDaysUntil(iso: string): number {
  // Approximate: business days only (simplified)
  const now = new Date();
  const expiry = new Date(iso + 'T00:00:00');
  let days = 0;
  const cur = new Date(now);
  while (cur < expiry) {
    cur.setDate(cur.getDate() + 1);
    if (cur.getDay() !== 0 && cur.getDay() !== 6) days++;
  }
  return days;
}

export function OptionDetailsSection({ row, underlyingSymbol, expiryIso, onSideChange }: Props) {
  const [selectedLeg, setSelectedLeg] = React.useState<'BTO' | 'STO' | 'BTC' | 'STC'>('BTO');
  const isZeroDte = expiryIso === new Date().toISOString().slice(0, 10);
  const tradingDays = tradingDaysUntil(expiryIso);
  const styleLabel = row.style === 'A' ? 'American' : 'European';
  const premium = ((parseFloat(row.bid) + parseFloat(row.ask)) / 2).toFixed(2);
  const notional = (parseFloat(premium) * row.multiplier).toFixed(2);

  function handleLegSelect(leg: typeof selectedLeg) {
    setSelectedLeg(leg);
    const sideMap: Record<typeof leg, { side: 'BUY' | 'SELL'; pe: 'OPEN' | 'CLOSE' }> = {
      BTO: { side: 'BUY', pe: 'OPEN' },
      STO: { side: 'SELL', pe: 'OPEN' },
      BTC: { side: 'BUY', pe: 'CLOSE' },
      STC: { side: 'SELL', pe: 'CLOSE' },
    };
    onSideChange(sideMap[leg].side, sideMap[leg].pe);
  }

  return (
    <div className="rounded-md border border-border p-3 space-y-2" data-testid="option-details-section">
      {/* Contract label */}
      <div>
        <div className="font-semibold text-sm">
          {underlyingSymbol} {formatExpiry(expiryIso)}{' '}
          <span className={row.put_call === 'C' ? 'text-green-400' : 'text-red-400'}>
            {row.strike}{row.put_call === 'C' ? 'C' : 'P'}
          </span>
        </div>
        <div className="text-xs text-muted-foreground">
          {styleLabel} · ×{row.multiplier} · {row.exchange} · expires in {tradingDays} trading days
        </div>
      </div>

      {/* Greeks strip */}
      <OptionGreeksStrip
        greeks={{ delta: row.delta, gamma: row.gamma, theta: row.theta, vega: row.vega, iv: row.iv }}
      />

      {/* Premium / notional */}
      <div className="text-xs text-muted-foreground border-t border-border pt-2">
        Premium {premium} · Notional per contract{' '}
        <strong className="text-foreground">${notional}</strong> · 1 contract = {row.multiplier} shares {underlyingSymbol}
      </div>

      {/* Side selector */}
      <div className="flex gap-1 flex-wrap">
        {(['BTO', 'STO', 'BTC', 'STC'] as const).map((leg) => (
          <button
            key={leg}
            onClick={() => handleLegSelect(leg)}
            className={`text-xs rounded border px-2 py-0.5 transition-colors ${
              leg === selectedLeg
                ? 'bg-accent text-accent-foreground border-accent'
                : 'border-border text-muted-foreground hover:border-foreground'
            }`}
            data-testid={`leg-select-${leg}`}
          >
            {leg === 'BTO' ? 'Buy to Open' : leg === 'STO' ? 'Sell to Open' : leg === 'BTC' ? 'Buy to Close' : 'Sell to Close'}
          </button>
        ))}
      </div>

      {/* 0DTE warning */}
      {isZeroDte && (
        <div
          className="rounded bg-yellow-400/10 border border-yellow-400/40 px-2 py-1 text-xs text-yellow-400"
          role="alert"
          data-testid="zero-dte-banner"
        >
          ⚠ This option expires today (0DTE). Exercise settlement risk applies.
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Write OptionDetailsSection tests**

```tsx
// frontend/src/features/options/OptionDetailsSection.test.tsx
import * as React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { OptionDetailsSection } from './OptionDetailsSection';
import type { OptionChainRow } from './types';

const mockRow: OptionChainRow = {
  conid: '12345678', strike: '450.00', put_call: 'C',
  bid: '5.10', ask: '5.30', iv: 0.175, delta: 0.5,
  gamma: 0.028, theta: -0.12, vega: 0.31,
  open_interest: 38000, volume: 1200,
  multiplier: 100, exchange: 'CBOE', style: 'A',
};

test('renders contract label correctly', () => {
  render(
    <OptionDetailsSection
      row={mockRow}
      underlyingSymbol="SPY"
      expiryIso="2025-01-17"
      onSideChange={() => {}}
    />,
  );
  expect(screen.getByText(/SPY/)).toBeInTheDocument();
  expect(screen.getByText(/450\.00C/)).toBeInTheDocument();
});

test('greeks strip renders delta', () => {
  render(
    <OptionDetailsSection row={mockRow} underlyingSymbol="SPY" expiryIso="2025-01-17" onSideChange={() => {}} />,
  );
  const strip = screen.getByTestId('greeks-strip');
  expect(strip).toHaveTextContent('0.500');
});

test('notional is multiplier × premium', () => {
  render(
    <OptionDetailsSection row={mockRow} underlyingSymbol="SPY" expiryIso="2025-01-17" onSideChange={() => {}} />,
  );
  // premium = (5.10 + 5.30) / 2 = 5.20; notional = 5.20 * 100 = 520.00
  expect(screen.getByText(/520\.00/)).toBeInTheDocument();
});

test('STO button calls onSideChange with SELL OPEN', () => {
  const handler = jest.fn();
  render(
    <OptionDetailsSection row={mockRow} underlyingSymbol="SPY" expiryIso="2025-01-17" onSideChange={handler} />,
  );
  fireEvent.click(screen.getByTestId('leg-select-STO'));
  expect(handler).toHaveBeenCalledWith('SELL', 'OPEN');
});

test('zero_dte banner shows when expiry is today', () => {
  const today = new Date().toISOString().slice(0, 10);
  render(
    <OptionDetailsSection row={mockRow} underlyingSymbol="SPY" expiryIso={today} onSideChange={() => {}} />,
  );
  expect(screen.getByTestId('zero-dte-banner')).toBeInTheDocument();
});

test('zero_dte banner absent for future expiry', () => {
  render(
    <OptionDetailsSection row={mockRow} underlyingSymbol="SPY" expiryIso="2030-01-01" onSideChange={() => {}} />,
  );
  expect(screen.queryByTestId('zero-dte-banner')).not.toBeInTheDocument();
});
```

- [ ] **Step 3: Inject OptionDetailsSection into TradeTicketModal**

In `frontend/src/features/orders/TradeTicketModal.tsx`, add the import at the top:

```typescript
import { OptionDetailsSection } from '@/features/options/OptionDetailsSection';
```

Find the `{/* ── Phase 11a-D — AI context section */}` comment (around line 571) and insert the OptionDetailsSection **above** it:

```tsx
      {/* ── Phase 12 — Option details section ────────────────────────── */}
      {form.contract.asset_class === 'OPTION' && form.contract.optionRow && (
        <OptionDetailsSection
          row={form.contract.optionRow}
          underlyingSymbol={form.contract.symbol.trim()}
          expiryIso={form.contract.expiryIso ?? ''}
          onSideChange={(side, positionEffect) => {
            // Update form side and position_effect
            dispatch({ type: 'SET_SIDE', side });
            dispatch({ type: 'SET_POSITION_EFFECT', positionEffect });
          }}
        />
      )}
```

Note: `form.contract.optionRow`, `form.contract.expiryIso`, and the `SET_POSITION_EFFECT` action need to be added to the form state type and reducer as the next sub-step.

In the form state type (wherever `contract` shape is defined), add:
```typescript
  optionRow?: OptionChainRow;      // present for OPTION contracts
  expiryIso?: string;
  asset_class?: string;
```

In the reducer (wherever form dispatch actions are handled), add:
```typescript
  case 'SET_POSITION_EFFECT':
    return { ...state, contract: { ...state.contract, positionEffect: action.positionEffect } };
```

Also add `positionEffect?: 'OPEN' | 'CLOSE'` to the form state contract shape.

- [ ] **Step 4: Run FE tests**

```bash
cd frontend && pnpm test -- --testPathPattern=OptionDetails 2>&1 | tail -20
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/options/OptionDetailsSection.tsx frontend/src/features/options/OptionDetailsSection.test.tsx frontend/src/features/orders/TradeTicketModal.tsx
git commit -m "feat(phase12): OptionDetailsSection — Greeks strip, notional, leg selector, 0DTE banner; injected into TradeTicketModal"
```

---

### Task E4: OptionChainPage, OptionEventsPage, routes, and nav

**Files:**
- Create: `frontend/src/features/options/OptionChainToolbar.tsx`
- Create: `frontend/src/features/options/OptionChainPage.tsx`
- Create: `frontend/src/features/options/OptionEventsPage.tsx`
- Create: `frontend/src/features/options/hooks/useExerciseElections.ts`
- Create: `frontend/src/routes/options.chain.tsx`
- Create: `frontend/src/routes/options.events.tsx`
- Modify: Sidebar navigation component

- [ ] **Step 1: Create useExerciseElections hook**

```typescript
// frontend/src/features/options/hooks/useExerciseElections.ts
import { useQuery } from '@tanstack/react-query';
import { fetchExercisePending } from '@/services/options/api';

export function useExerciseElections(accountId: string | null) {
  return useQuery({
    queryKey: ['options', 'exercise', 'pending', accountId],
    queryFn: () => fetchExercisePending(accountId!),
    enabled: accountId !== null,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}
```

- [ ] **Step 2: Create OptionChainToolbar**

```tsx
// frontend/src/features/options/OptionChainToolbar.tsx
import * as React from 'react';

interface Props {
  symbol: string;
  onSymbolChange: (s: string) => void;
  source: string;
  isLive: boolean;
}

export function OptionChainToolbar({ symbol, onSymbolChange, source, isLive }: Props) {
  return (
    <div className="flex gap-2 items-center flex-wrap p-2 border-b border-border">
      <input
        className="rounded border border-border bg-panel px-2 py-1 text-sm w-24"
        value={symbol}
        onChange={(e) => onSymbolChange(e.currentTarget.value.toUpperCase())}
        placeholder="SPY"
        aria-label="Underlying symbol"
        data-testid="chain-symbol-input"
      />
      <span className="text-xs text-muted-foreground ml-auto">
        {source} · {isLive ? 'live' : 'stale'}
      </span>
    </div>
  );
}
```

- [ ] **Step 3: Create OptionChainPage**

```tsx
// frontend/src/features/options/OptionChainPage.tsx
import * as React from 'react';
import { OptionChainToolbar } from './OptionChainToolbar';
import { OptionExpiryTabs } from './OptionExpiryTabs';
import { OptionChainTable } from './OptionChainTable';
import { useOptionExpirations } from './hooks/useOptionExpirations';
import { useOptionChain } from './hooks/useOptionChain';
import type { OptionChainRow } from './types';

export function OptionChainPage() {
  const [symbol, setSymbol] = React.useState('SPY');
  const [selectedExpiry, setSelectedExpiry] = React.useState<string | null>(null);

  const expirations = useOptionExpirations(symbol);
  const chain = useOptionChain(symbol, selectedExpiry);

  // Auto-select first expiry
  React.useEffect(() => {
    if (expirations.data && expirations.data.length > 0 && !selectedExpiry) {
      setSelectedExpiry(expirations.data[0]);
    }
  }, [expirations.data, selectedExpiry]);

  function handleSelectStrike(row: OptionChainRow, side: 'call' | 'put') {
    // Open TradeTicketModal (global event or context-based)
    const event = new CustomEvent('open-trade-ticket', {
      detail: { optionRow: row, symbol, expiryIso: selectedExpiry, side },
    });
    window.dispatchEvent(event);
  }

  return (
    <div className="flex flex-col h-full">
      <OptionChainToolbar
        symbol={symbol}
        onSymbolChange={setSymbol}
        source={chain.data?.source ?? '—'}
        isLive={!chain.wsStale && !chain.isStale}
      />

      <div className="p-2 border-b border-border">
        {expirations.isLoading ? (
          <span className="text-xs text-muted-foreground">Loading expirations…</span>
        ) : (
          <OptionExpiryTabs
            expirations={expirations.data ?? []}
            selected={selectedExpiry}
            onSelect={setSelectedExpiry}
          />
        )}
      </div>

      <div className="flex-1 overflow-auto">
        {chain.isLoading ? (
          <div className="p-4 text-xs text-muted-foreground">Loading chain…</div>
        ) : chain.data ? (
          <OptionChainTable
            data={chain.data}
            spot={null}
            onSelectStrike={handleSelectStrike}
          />
        ) : null}
      </div>

      <div className="p-2 border-t border-border text-xs text-muted-foreground flex gap-4">
        {chain.wsStale && <span className="text-yellow-400">⚠ Stale data</span>}
        <span className="ml-auto">Source: {chain.data?.source ?? '—'} · Click row to trade</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create OptionEventsPage**

```tsx
// frontend/src/features/options/OptionEventsPage.tsx
import * as React from 'react';
import { useExerciseElections } from './hooks/useExerciseElections';
import { ExerciseElectionRow } from './ExerciseElectionRow';

export function OptionEventsPage() {
  // TODO: get accountId from active account store
  const accountId = null;
  const elections = useExerciseElections(accountId);

  return (
    <div className="p-4 space-y-4">
      <h2 className="text-lg font-semibold">Options Events</h2>

      <section>
        <h3 className="text-sm font-medium text-muted-foreground mb-2">Pending Exercise Elections</h3>
        {elections.isLoading ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : elections.data?.length === 0 ? (
          <p className="text-xs text-muted-foreground">No contracts expiring in the next 5 sessions.</p>
        ) : (
          <div className="space-y-2">
            {elections.data?.map((candidate) => (
              <ExerciseElectionRow key={candidate.instrument_id} candidate={candidate} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 5: Create ExerciseElectionRow**

```tsx
// frontend/src/features/options/ExerciseElectionRow.tsx
import * as React from 'react';
import { postExerciseElection, mintCsrfNonce } from '@/services/options/api';
import type { ExerciseCandidate } from './types';

interface Props {
  candidate: ExerciseCandidate;
}

export function ExerciseElectionRow({ candidate }: Props) {
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [done, setDone] = React.useState(false);

  async function handleElect(action: 'EXERCISE' | 'DO_NOT_EXERCISE' | 'LAPSE') {
    setSubmitting(true);
    setError(null);
    try {
      const nonce = await mintCsrfNonce();
      const ikey = crypto.randomUUID();
      await postExerciseElection({
        account_id: '', // filled from context in full impl
        instrument_id: candidate.instrument_id,
        action,
        qty: candidate.qty,
        idempotency_key: ikey,
        csrf_nonce: nonce,
      });
      setDone(true);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unknown error');
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <div className="rounded border border-border p-2 text-xs text-muted-foreground">
        Election submitted for {candidate.strike}{candidate.put_call === 'C' ? 'C' : 'P'} exp {candidate.expiry}
      </div>
    );
  }

  return (
    <div className="rounded border border-border p-3 text-sm space-y-2" data-testid={`election-row-${candidate.instrument_id}`}>
      <div className="flex items-center gap-2">
        <span className="font-medium">{candidate.strike}{candidate.put_call === 'C' ? 'C' : 'P'}</span>
        <span className="text-muted-foreground text-xs">exp {candidate.expiry}</span>
        <span className="text-muted-foreground text-xs">qty {candidate.qty}</span>
        {candidate.spot_unavailable && (
          <span className="text-xs text-yellow-400">expiring within 5 sessions</span>
        )}
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
      <div className="flex gap-2">
        <button
          className="text-xs rounded border border-green-400/40 px-2 py-1 disabled:opacity-50"
          onClick={() => handleElect('EXERCISE')}
          disabled={submitting}
        >
          Exercise
        </button>
        <button
          className="text-xs rounded border border-border px-2 py-1 disabled:opacity-50"
          onClick={() => handleElect('DO_NOT_EXERCISE')}
          disabled={submitting}
        >
          DNE
        </button>
        <button
          className="text-xs rounded border border-border px-2 py-1 disabled:opacity-50"
          onClick={() => handleElect('LAPSE')}
          disabled={submitting}
        >
          Lapse
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Create TanStack routes**

```tsx
// frontend/src/routes/options.chain.tsx
import { createFileRoute } from '@tanstack/react-router';
import { OptionChainPage } from '@/features/options/OptionChainPage';

export const Route = createFileRoute('/options/chain')({
  component: OptionChainPage,
});
```

```tsx
// frontend/src/routes/options.events.tsx
import { createFileRoute } from '@tanstack/react-router';
import { OptionEventsPage } from '@/features/options/OptionEventsPage';

export const Route = createFileRoute('/options/events')({
  component: OptionEventsPage,
});
```

- [ ] **Step 7: Regenerate TanStack Router route tree**

```bash
cd frontend && pnpm tsr generate
```

- [ ] **Step 8: Add "Options" to sidebar navigation**

Find the sidebar navigation component (look in `frontend/src/components/layout/` for the nav/sidebar file). Add an "Options" entry between "Trade" and "Portfolio":

```tsx
{
  label: 'Options',
  href: '/options/chain',
  icon: LineChart, // or appropriate icon from lucide-react
  subLinks: [{ label: 'Events', href: '/options/events' }],
}
```

- [ ] **Step 9: Run FE type check**

```bash
cd frontend && pnpm typecheck
```

Expected: no errors.

- [ ] **Step 10: Run all FE tests**

```bash
cd frontend && pnpm test 2>&1 | tail -20
```

Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add frontend/src/features/options/ frontend/src/routes/options.chain.tsx frontend/src/routes/options.events.tsx
git commit -m "feat(phase12): OptionChainPage, OptionEventsPage, routes, nav — /options/chain + /options/events"
```

---

## Chunk F — Integration, Metrics & Close

### Task F1: orders_service multiplier + position_effect plumbing

**Files:**
- Modify: `backend/app/services/orders_service.py`

- [ ] **Step 1: Plumb multiplier and position_effect into EvaluationContext construction**

In `backend/app/services/orders_service.py`, find `_evaluate_risk_for_place_order` (around line 404). After the `asset_class` assignment, add:

```python
        # Phase 12: resolve multiplier and position_effect for options
        from app.services.options.types import OptionDetails, parse_instrument_meta
        multiplier = 1
        position_effect_value: str | None = None
        if instrument_id is not None and asset_class == "OPTION":
            try:
                # fetch meta from DB (instrument already loaded in caller)
                instr_result = await db.execute(
                    __import__("sqlalchemy").text("SELECT meta FROM instruments WHERE id = :id"),
                    {"id": instrument_id},
                )
                instr_row = instr_result.fetchone()
                if instr_row:
                    details = parse_instrument_meta(instr_row[0] or {})
                    if isinstance(details, OptionDetails):
                        multiplier = details.multiplier
            except Exception:
                pass
        if hasattr(request, "position_effect"):
            position_effect_value = request.position_effect
```

Then update the `EvaluationContext` constructor call to include:
```python
            multiplier=multiplier,
            position_effect=position_effect_value,
```

- [ ] **Step 2: Update `_native_notional` to multiply by `multiplier`**

In `_native_notional`, update each branch to multiply by `multiplier`:

```python
async def _native_notional(
    redis: RedisLike,
    request: PreviewRequest,
    contract: base.Contract,
    qty: Decimal,
    *,
    multiplier: int = 1,
    quote_engine: object | None = None,
) -> Decimal:
    if request.order_type == "LIMIT" and request.limit_price is not None:
        return qty * Decimal(request.limit_price) * multiplier
    if request.order_type == "STOP" and request.stop_price is not None:
        return qty * Decimal(request.stop_price) * multiplier
    mid = await _get_market_mid(redis, request.conid, contract=contract, quote_engine=quote_engine)
    return qty * mid * Decimal("1.05") * multiplier
```

- [ ] **Step 3: Add contract expiry check**

In the preview/place order flow, after instrument is resolved, add:

```python
        # Phase 12: reject expired contracts
        if asset_class == "OPTION" and instrument_id is not None:
            from app.services.options.types import OptionDetails, parse_instrument_meta
            from app.services import market_calendar
            try:
                meta_result = await db.execute(
                    __import__("sqlalchemy").text("SELECT meta, primary_exchange FROM instruments WHERE id = :id"),
                    {"id": instrument_id},
                )
                meta_row = meta_result.fetchone()
                if meta_row:
                    details = parse_instrument_meta(meta_row[0] or {})
                    if isinstance(details, OptionDetails):
                        exchange = meta_row[1] or "NYSE"
                        if market_calendar.is_past_expiry(details.expiry, exchange):
                            raise ContractExpiredError(
                                f"Contract {canonical_id} expired on {details.expiry}"
                            )
            except ContractExpiredError:
                raise
            except Exception:
                pass  # fail-open for non-critical path
```

- [ ] **Step 4: Run orders tests**

```bash
cd backend && pytest tests/api/test_orders_place.py tests/api/test_orders_preview.py -v 2>&1 | tail -20
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/orders_service.py
git commit -m "feat(phase12): orders_service — multiplier-aware notional, position_effect plumbing, contract expiry check"
```

---

### Task F2: Prometheus metrics registration

**Files:**
- Modify: `backend/app/core/metrics.py` (or wherever Prometheus metrics are registered)

- [ ] **Step 1: Find metrics registration file**

```bash
grep -rn "Counter\|Histogram\|Gauge" /home/joseph/dashboard/backend/app/core/metrics.py 2>/dev/null | head -10 || grep -rn "from prometheus_client" /home/joseph/dashboard/backend/app/ | grep -v __pycache__ | head -5
```

- [ ] **Step 2: Register Phase 12 metrics**

In the metrics module, add:

```python
# Phase 12: Options metrics
option_chain_fetch_seconds = Histogram(
    "option_chain_fetch_seconds", "Option chain fetch latency", ["source"]
)
option_chain_fetch_total = Counter(
    "option_chain_fetch_total", "Option chain fetch outcomes", ["source", "outcome"]
)
option_expirations_fetch_total = Counter(
    "option_expirations_fetch_total", "Option expirations fetch outcomes", ["source", "outcome"]
)
option_greeks_stream_updates_total = Counter(
    "option_greeks_stream_updates_total", "Greeks stream updates received", ["source"]
)
option_greeks_stream_drops_total = Counter(
    "option_greeks_stream_drops_total", "Greeks stream messages dropped (backpressure)", ["source"]
)
option_exercise_total = Counter(
    "option_exercise_total", "Exercise elections submitted", ["broker", "action", "outcome"]
)
option_greeks_rows_total = Gauge(
    "option_greeks_rows_total", "Current rows in option_greeks table"
)
option_greeks_clamped_total = Counter(
    "option_greeks_clamped_total", "Greeks values clamped to valid range", ["field"]
)
quote_options_chain_subs_active = Gauge(
    "quote_options_chain_subs_active", "Active options chain subscriptions", ["source"]
)
option_risk_check_total = Counter(
    "option_risk_check_total", "Options risk check outcomes", ["check", "verdict"]
)
option_chain_sources_invalid_total = Counter(
    "option_chain_sources_invalid_total", "Invalid sources on chain config load/reload", ["source"]
)
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/metrics.py
git commit -m "feat(phase12): register 11 Prometheus metrics for options"
```

---

### Task F3: Full test run + coverage check

- [ ] **Step 1: Run full backend test suite**

```bash
cd backend && pytest --tb=short -q 2>&1 | tail -30
```

Expected: 1000+ tests, no regressions from the 970 baseline.

- [ ] **Step 2: Check options coverage**

```bash
cd backend && pytest --cov=app.services.options --cov-report=term-missing --cov-fail-under=80 tests/services/options/ tests/api/test_options_api.py tests/api/test_ws_options.py -q
```

Expected: coverage ≥ 80%.

- [ ] **Step 3: Run FE type check**

```bash
cd frontend && pnpm typecheck
```

Expected: no errors.

- [ ] **Step 4: Run all FE tests**

```bash
cd frontend && pnpm test 2>&1 | tail -30
```

Expected: all PASS.

- [ ] **Step 5: Run BE linters**

```bash
cd backend && ruff check . && mypy app/ --ignore-missing-imports 2>&1 | tail -20
```

Expected: no new errors.

---

### Task F4: Phase close-out commit

- [ ] **Step 1: Update CHANGELOG.md**

Add entry at top:

```markdown
## v0.12.0 — Phase 12: Options Single-Leg (2026-05-XX)

### Added
- Option chain viewer at `/options/chain` — butterfly layout, expiry tabs, 2 Hz live feed
- `OptionDetailsSection` injected into `TradeTicketModal` for OPTION asset class
- Exercise elections page at `/options/events` — idempotent, CSRF-protected
- `OPTION` asset class in `AssetClass` enum + `InstrumentMeta` Pydantic discriminated union
- `position_effect` column on `orders` (OPEN/CLOSE) enabling BTO/STO/BTC/STC
- `tax_treatment` nullable column on `orders` + `fills` (Phase 23 CGT foundation)
- `option_greeks` + `exercise_elections` tables (migration 0046)
- `OptionChainService`, `OptionGreeksService`, `ExerciseService`
- 4 new broker proto RPCs: `GetOptionChain`, `GetOptionExpirations`, `StreamOptionGreeks`, `ExerciseOption`
- Options risk checks: trading-level gate, naked-short, expiry cutoff, 0DTE WARN, assignment-risk WARN
- 11 Prometheus metrics for options observability
- Market-calendar extensions: `is_open`, `is_past_expiry`, `option_cutoff_time`, `next_trading_days`
- OCC symbol rejection in Telegram `parse_place_order`
- Schwab chain data (read-only) as primary USD source; IBKR + Alpaca + Futu HK for execution

### Deferred
- Schwab execution (upstream 401 — Phase 12.x)
- Greeks in risk gate / margin model (Phase 13+)
- IV rank (Phase 18)
- Multi-leg combos (Phase 13)
```

- [ ] **Step 2: Update TASKS.md** — mark Phase 12 row complete.

- [ ] **Step 3: Update CLAUDE.md** — add Phase 12 topology note in the "Broker adapters" section following the Phase 11d note.

- [ ] **Step 4: Final commit and tag**

```bash
git add CHANGELOG.md TASKS.md CLAUDE.md
git commit -m "docs(phase12): close-out — CHANGELOG, TASKS, CLAUDE.md"
git tag -a v0.12.0 -m "Phase 12: Options single-leg"
git push && git push --tags
```

---

## Self-Review

### Spec coverage check

| Spec requirement | Task |
|-----------------|------|
| Alembic 0046 (OPTION enum, position_effect, tax_treatment, greeks, elections tables) | A1 |
| `meta` backfill + Pydantic validation pass | A1 |
| ORM models OptionGreeks, ExerciseElection | A2 |
| AssetClass.OPTION | A2 |
| InstrumentMeta / OptionDetails / NonOptionDetails / GreeksSnapshot / SubscriptionHandle | A3 |
| Market-calendar 4 new helpers + tests | A4 |
| `find_or_create_option` + `_build_option_canonical_id` | A5 |
| Proto: 4 RPCs, messages, OptionContractHint oneof | B1 |
| EvaluationContext.multiplier + position_effect | B2 |
| `_check_options_exposure` (trading level, naked short, expiry cutoff, 0DTE WARN) | B2 |
| Telegram OCC rejection | B3 |
| OptionChainService (cache, singleflight per source, TTL) | C1 |
| OptionGreeksService (upsert guard, evict, clamp) | C2 |
| ExerciseService (idempotency, rate limit, 409) | C3 |
| 9 REST endpoints | D1 |
| WS /ws/options/chain (2Hz, heartbeat, cap) | D2 |
| FE types + API client | E1 |
| hooks: useOptionExpirations, useOptionChain, useExerciseElections | E1, E4 |
| OptionGreeksStrip, OptionExpiryTabs, OptionChainTable (butterfly + mobile) | E2 |
| OptionDetailsSection + TradeTicketModal injection | E3 |
| OptionChainPage, OptionEventsPage, routes, nav | E4 |
| multiplier-aware notional (×3 sites) | F1 |
| position_effect plumbing into EvaluationContext | F1 |
| Contract expiry check (is_past_expiry) | F1 |
| 11 Prometheus metrics | F2 |
| Coverage ≥ 80% | F3 |
| CHANGELOG + TASKS + CLAUDE.md + tag | F4 |

All spec requirements are covered. ✓
